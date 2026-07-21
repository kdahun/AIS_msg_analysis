"""보고주기 준수 검증 핵심 로직 (streamlit 비의존, 순수 pandas/numpy).

성능/UX 를 위해 2단계로 분리한다.
  enrich_*  : 무거운 계산(오차범위·배율과 무관). 한 번만 하고 캐싱한다.
  classify  : 사용자 조절 임계값(허용오차/배율)을 싸게 적용해 위반 사유를 만든다.
              → 슬라이더를 움직여도 즉시 반영.

검증 대상
  1. 보고주기: ITU-R M.1371-6 Table 1 (Class A). SOG/항해상태/변침여부 → 기대 간격.
  2. Type 1 SOTDMA 슬롯 체인: 같은 슬롯 반복(timeout-1) / 슬롯 교체 예고(timeout=0).
  * num_slots(ITDMA) 검증은 이번 버전 제외.
  * 시각은 vsi_time(정밀 UTC 기반) 사용.
"""
import numpy as np
import pandas as pd

# enrich 결과(디스크 캐시)의 스키마/의미가 바뀔 때마다 올린다 → 캐시 자동 무효화
#   4: 구간(segment) 도입 — 장소·수집중단을 넘어 시계열을 잇지 않는다.
#      site_id/segment_id 컬럼 추가, 거리를 장소별 좌표 기준으로 계산.
#   5: 잡음층을 FSR 실측 우선으로. 추정치는 3~4dB 낮게 치우쳐 있었다.
LOGIC_VERSION = 5

# sentinel
HEADING_NA = 511
COURSE_NA = 360.0
SPEED_NA = 102.3
ANCHORED_MOORED = (1, 5)          # status: 1=at anchor, 5=moored

COURSE_CHANGE_DEG = 5.0
COURSE_AVG_WINDOW = "30s"
SLOTS_PER_FRAME = 2250

# 사유 코드 → 한글 (여러 곳(표/차트 hover)에서 공유)
# 위반 사유: 원그래프·MMSI 표에서 '위반'으로 집계된다.
REASON_LABELS_KO = {
    "RI_TOO_FAST": "과도한 보고(기대보다 빠름)",
    "RI_INTERVAL_BAD": "보고주기 부적합(어느 정수배와도 안 맞음)",
    "RI_UNDER_REPORT": "과소 보고(규정보다 느린 주기 — 유실 아님)",
    "SLOT_NUM_MISMATCH": "슬롯번호 불일치(보고 슬롯 ≠ 관측 슬롯)",
    "SLOT_REPEAT_MISSING": "슬롯 반복 누락(다음 프레임에 그 슬롯 미점유)",
    "SLOT_SWITCH_MISSING": "슬롯 교체 예고 불일치(예고 슬롯 미점유)",
    "TIMEOUT_NOT_DECREMENTED": "timeout 미감소(1씩 안 줄어듦)",
    "TIMEOUT_REINIT_OUT_OF_RANGE": "timeout 재초기화 범위밖([3,7] 벗어남)",
}

# 검증 보류(위반 아님): 수신 유실 등으로 선박 탓이라 단정할 수 없는 경우.
# 잡음층 대비 신호여유로 '환경성 유실 추정'과 '원인 미상'을 구분한다.
#  · RI_LOST_*  : 보고 간격이 기대의 정수배(≥2)인데, 그 선박은 원래 규정 주기를
#                 달성하므로 '이번 간격만 유실'로 본다(과소 보고와 반대).
#  · SLOT_UNVERIF_* : 다음 프레임에 그 선박이 미수신돼 슬롯 체인 확인 불가.
HOLD_LABELS_KO = {
    "RI_LOST_NOISE": "보고 유실 추정(수신한계 근접 — 환경성)",
    "RI_LOST_PENDING": "보고 유실(원인 미상 — 보류)",
    "SLOT_UNVERIF_NOISE": "검증보류(수신한계 근접 — 환경성 유실 추정)",
    "SLOT_UNVERIF_PENDING": "검증보류(다음 프레임 미수신 — 원인 미상)",
}

# '진짜 위반'으로 집계할 코드(보류 코드는 is_violation 에서 제외)
RI_VIOLATION_CODES = frozenset({"RI_TOO_FAST", "RI_INTERVAL_BAD", "RI_UNDER_REPORT"})
RI_HOLD_CODES = frozenset({"RI_LOST_NOISE", "RI_LOST_PENDING"})
SLOT_VIOLATION_CODES = frozenset({
    "SLOT_NUM_MISMATCH", "SLOT_REPEAT_MISSING", "TIMEOUT_NOT_DECREMENTED",
    "SLOT_SWITCH_MISSING", "TIMEOUT_REINIT_OUT_OF_RANGE"})
SLOT_HOLD_CODES = frozenset({"SLOT_UNVERIF_NOISE", "SLOT_UNVERIF_PENDING"})

# 사유 뒤에 '~N배 간격'을 붙일 코드(간격이 기대의 몇 배인지 표기)
_COUNT_CODES = RI_HOLD_CODES | {"RI_UNDER_REPORT"}
_ALL_LABELS = {**REASON_LABELS_KO, **HOLD_LABELS_KO}


def reason_ko(code: str, missed_count: int = 0) -> str:
    """빈 문자열/None 은 '정상'으로. 유실/과소 보고는 간격 배수를 붙인다."""
    if not code:
        return "정상"
    label = _ALL_LABELS.get(code, code)
    if code in _COUNT_CODES and missed_count:
        return f"{label} ~{missed_count + 1}배 간격"
    return label


def combined_reason_ko(ri_reason: str, slot_reason: str, missed_count: int = 0) -> str:
    parts = []
    if ri_reason:
        parts.append(reason_ko(ri_reason, missed_count))
    if slot_reason:
        parts.append(reason_ko(slot_reason))
    return " / ".join(parts) if parts else "정상"
FRAME_SEC = 60.0
TMO_MIN, TMO_MAX = 3, 7

# 보고주기 판정 파라미터
SLOW_INTERVAL_TOL_SEC = 3.0   # 기대주기>60초(정박 등)는 프레임 고정 → 절대 ±초로 엄격
UNDER_MIN_SAMPLES = 10        # 과소 보고 판정에 필요한 (같은 기대주기) 최소 간격 표본
UNDER_FLOOR_RATIO = 1.5       # 달성 최소간격 비율(p10)이 이 이상이면 과소 보고로 봄


# ── 보고주기: 기대 간격 ───────────────────────────────────────
def expected_interval_sec(speed, status, changing_course):
    """Table 1 (Class A) 기대 보고주기(초). speed=knots, SPEED_NA=미상."""
    spd_na = (speed == SPEED_NA) or pd.isna(speed)
    spd = np.inf if spd_na else speed
    if status in ANCHORED_MOORED:
        return 180.0 if (spd_na or spd <= 3) else 10.0
    if spd_na or spd <= 14:
        return 3.3333 if changing_course else 10.0
    if spd <= 23:
        return 2.0 if changing_course else 6.0
    return 2.0


def _circular_mean_rolling(series_deg, window):
    rad = np.radians(series_deg)
    s = pd.DataFrame({"sin": np.sin(rad), "cos": np.cos(rad)}, index=series_deg.index)
    sin_avg = s["sin"].rolling(window, closed="left").mean()
    cos_avg = s["cos"].rolling(window, closed="left").mean()
    out = np.degrees(np.arctan2(sin_avg.values, cos_avg.values)) % 360
    return pd.Series(np.where(np.isnan(sin_avg.values), np.nan, out), index=series_deg.index)


def _circular_diff(a, b):
    return np.abs((a - b + 180) % 360 - 180)


# ── enrich (무거움, 오차/배율 무관) ──────────────────────────
def enrich_vessel(df: pd.DataFrame) -> pd.DataFrame:
    """한 MMSI(Type1+3, vsi_time 오름차순)에 오차/배율 무관한 계산 컬럼 추가.

    추가 컬럼:
      changing_course, expected_interval, actual_gap,     (보고주기)
      chain_kind('repeat'|'switch'|''), chain_gap_err,    (Type1 슬롯체인)
      chain_next_timeout, chain_expected_timeout
    """
    df = df.sort_values("vsi_time").reset_index(drop=True)
    idx = df.set_index("vsi_time")

    # 변침 판정 (HDG 우선, 없으면 SOG>2kn 일 때 COG)
    course_proxy = np.where(
        df["heading"].values != HEADING_NA, df["heading"].values,
        np.where((df["course"].values != COURSE_NA)
                 & (df["speed"].values > 2) & (df["speed"].values != SPEED_NA),
                 df["course"].values, np.nan))
    cp = pd.Series(course_proxy, index=idx.index)
    hdg_avg = _circular_mean_rolling(cp, COURSE_AVG_WINDOW)
    delta = _circular_diff(cp.values, hdg_avg.values)
    df["changing_course"] = np.where(np.isnan(delta), False, delta > COURSE_CHANGE_DEG)
    df["course_delta"] = delta

    df["expected_interval"] = [
        expected_interval_sec(s, st, cc)
        for s, st, cc in zip(df["speed"], df["status"], df["changing_course"])]
    df["actual_gap"] = df["vsi_time"].shift(-1).sub(df["vsi_time"]).dt.total_seconds()

    # achieved_ratio_floor 는 여기서 내지 않는다 — 선박 단위 통계라서
    # 구간별로 쪼개면 표본이 부족해져 값이 흔들린다. enrich_all 에서 한 번에 낸다.
    _enrich_slot_chain(df)
    return df


def _achieved_ratio_floor(df: pd.DataFrame) -> np.ndarray:
    """같은 기대주기 상태에서 이 선박이 달성하는 '가장 짧은' 간격 비율(p10).

    수신 유실은 간격을 늘리기만 하므로, p10 이 여전히 크면(≈2배 이상) 선박이 원래
    규정보다 느리게 쏘는 것(과소 보고)이고, 1 근처면 규정 주기를 달성하는 것이다.

    **구간이 아니라 선박 단위로 낸다.** 이 값은 그 선박의 송신 습성이지 우리 수신
    구간의 성질이 아니다. 구간별로 나누면 표본이 쪼개져 p10 이 흔들리고, 실제로
    과소 보고 판정이 839건이나 널뛰었다.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        r = df["actual_gap"].values / df["expected_interval"].values
    tmp = pd.DataFrame({"mmsi": df["mmsi"].values,
                        "exp": df["expected_interval"].values, "r": r})
    floor = np.full(len(df), np.nan)
    for _, grp in tmp.groupby(["mmsi", "exp"], sort=False):
        rr = grp["r"].dropna()
        if len(rr) >= UNDER_MIN_SAMPLES:
            floor[grp.index.to_numpy()] = np.percentile(rr.values, 10)
    return floor


def _enrich_slot_chain(df: pd.DataFrame):
    """Type 1 슬롯 체인을 '프레임 인덱스 + 슬롯번호' 이산 검증으로 채운다(제자리 수정).

    수신시각 60초 근접이 아니라 frame(=UTC분) 단위로 '다음 프레임'을 찾으므로
    서브초 지터에 영향받지 않는다 → 슬롯 검증에 허용오차가 필요 없다.

    검증 근거(ITU-R M.1371 SOTDMA 통신상태 sub message):
      timeout ∈ {2,4,6} → sub_message = 사용 슬롯 번호      → vsi_slot 과 일치해야
      timeout == 0       → sub_message = 다음 프레임 슬롯 offset → (slot+offset)%2250 점유
      timeout  > 0       → 같은 슬롯을 다음 프레임에도 유지(timeout 1 감소)
      timeout ∈ {1,3,5,7}→ sub_message 가 슬롯이 아님(UTC/수신국수) → 번호검사 제외

    추가 컬럼:
      chain_kind             : 'repeat'(to>0) | 'switch'(to==0) | ''
      slot_num_bad           : to∈{2,4,6} 인데 sub_message≠vsi_slot (bool)
      chain_heard_next       : 다음 프레임에 이 선박이 (그 채널에서) 수신됐나
      chain_slot_matched     : 기대 슬롯이 다음 프레임에 실제 점유됐나
      chain_next_timeout     : 그 기대 슬롯의 다음 프레임 slot_timeout (없으면 nan)
      chain_expected_timeout : repeat=to-1, switch=nan
    """
    n = len(df)
    kind = np.full(n, "", dtype=object)
    num_bad = np.zeros(n, dtype=bool)
    heard_next = np.zeros(n, dtype=bool)
    slot_matched = np.zeros(n, dtype=bool)
    next_to = np.full(n, np.nan)
    exp_to = np.full(n, np.nan)

    t1_mask = df["msg_type"].values == 1
    if not t1_mask.any():
        df["chain_kind"] = kind; df["slot_num_bad"] = num_bad
        df["chain_heard_next"] = heard_next; df["chain_slot_matched"] = slot_matched
        df["chain_next_timeout"] = next_to; df["chain_expected_timeout"] = exp_to
        return

    # SOTDMA 통신상태는 "해당 채널의 그 슬롯"에만 적용된다(M.1371-6 A2-3.3.7.2.2).
    has_ch = "channel" in df.columns
    ch_all = df["channel"].values if has_ch else np.full(n, "-", dtype=object)
    frame = df["frame"].values                          # datetime64[ns] (분 바닥)
    one_min = np.timedelta64(60, "s")

    # (채널, 프레임) → 그 선박이 수신됐는지 (Type1/3 모두 = 물리적 수신 여부)
    present = set()
    for i in range(n):
        present.add((ch_all[i], frame[i]))
    # (채널, 프레임) → {슬롯: timeout}  (Type1 만; 슬롯 점유 확인용)
    t1_slots: dict = {}
    for i in np.where(t1_mask)[0]:
        t1_slots.setdefault((ch_all[i], frame[i]), {})[int(df["vsi_slot"].values[i])] = \
            df["slot_timeout"].values[i]

    slot_vals = df["vsi_slot"].values
    to_vals = df["slot_timeout"].values
    sub_vals = df["sub_message"].values
    for i in np.where(t1_mask)[0]:
        to = to_vals[i]
        if to is None or pd.isna(to):
            continue
        to = int(to)
        slot = int(slot_vals[i])
        sub = sub_vals[i]
        ch = ch_all[i]
        f_next = frame[i] + one_min

        # timeout ∈ {2,4,6}: 보고한 슬롯 번호(sub_message)와 관측 슬롯(vsi_slot) 일치검사
        if to in (2, 4, 6) and sub is not None and not pd.isna(sub):
            num_bad[i] = int(sub) != slot

        heard_next[i] = (ch, f_next) in present
        nxt = t1_slots.get((ch, f_next), {})

        if to > 0:                                       # repeat: 같은 슬롯 유지
            kind[i] = "repeat"; exp_to[i] = to - 1
            if slot in nxt:
                slot_matched[i] = True
                nto = nxt[slot]
                next_to[i] = float(nto) if nto is not None and not pd.isna(nto) else np.nan
        else:                                            # switch: offset 예고 슬롯 점유
            if sub is None or pd.isna(sub):
                continue
            kind[i] = "switch"
            pred = (slot + int(sub)) % SLOTS_PER_FRAME
            if pred in nxt:
                slot_matched[i] = True
                nto = nxt[pred]
                next_to[i] = float(nto) if nto is not None and not pd.isna(nto) else np.nan

    df["chain_kind"] = kind
    df["slot_num_bad"] = num_bad
    df["chain_heard_next"] = heard_next
    df["chain_slot_matched"] = slot_matched
    df["chain_next_timeout"] = next_to
    df["chain_expected_timeout"] = exp_to


def enrich_all(df: pd.DataFrame) -> pd.DataFrame:
    """선박별 시계열 계산. 반드시 '구간 안에서만' 묶는다.

    segment_id 를 빼고 mmsi 로만 묶으면 장소 이동(123분)이나 장비 중단(최대 145초)을
    하나의 보고 간격으로 잇게 되어 위반으로 오탐된다(실측 1,369건).
    구간 마지막 메시지는 '다음 보고 없음'으로 남아 검증 보류가 된다.
    """
    parts = [enrich_vessel(g)
             for _, g in df.groupby(["segment_id", "mmsi"], sort=False)]
    out = pd.concat(parts, ignore_index=True)
    # 이건 선박 단위 통계라 구간을 가로질러 한 번에 낸다(위 함수 주석 참고).
    out["achieved_ratio_floor"] = _achieved_ratio_floor(out)
    return out


# ── 슬롯 특정 유실 (오차/배율 무관 → 프리컴퓨트 대상) ─────────
def build_loss_layer(df: pd.DataFrame) -> pd.DataFrame:
    """timeout 카운트다운 런 안에서 '브라킷된 빈 프레임' = 슬롯 특정 유실.

    같은 (mmsi, 채널, 슬롯)의 연속 두 수신이 프레임 d칸 떨어져 있고
    timeout 이 정확히 d 만큼 감소했다면(t2 == t1 − d), 그 사이 d−1 개
    프레임에도 같은 슬롯으로 송신했음이 확실하다(예약 규칙) — 수신만 못한 것.
    그 빈 프레임들을 유실 행으로 만든다. 수신 메시지가 아니므로 별도 레이어.

    반환 columns: mmsi, channel, slot, frame, est_rssi(양옆 평균), gap_frames
    """
    cols = ["site_id", "mmsi", "channel", "slot", "frame", "est_rssi", "gap_frames"]
    t1 = df[(df["msg_type"] == 1) & df["slot_timeout"].notna()
            & df["channel"].isin(["A", "B"])]
    if t1.empty:
        return pd.DataFrame(columns=cols)

    # segment_id 를 키에 넣어, 장비가 꺼진 구간을 사이에 두고 이어붙이지 않게 한다.
    # (짧은 중단은 프레임 간격 d 가 2~3 이라 timeout 감소와 우연히 맞을 수 있다)
    s = (t1[["segment_id", "site_id", "mmsi", "channel", "vsi_slot", "frame",
             "slot_timeout", "vsi_rssi"]]
         .sort_values(["segment_id", "mmsi", "channel", "vsi_slot", "frame"]))
    grp = s.groupby(["segment_id", "mmsi", "channel", "vsi_slot"])
    prev_frame = grp["frame"].shift(1)
    prev_to = grp["slot_timeout"].shift(1)
    prev_rssi = grp["vsi_rssi"].shift(1)
    d = (s["frame"] - prev_frame).dt.total_seconds() / 60.0
    consistent = (d >= 2) & (prev_to - d == s["slot_timeout"])

    out = []
    sub = s[consistent].assign(gap_d=d[consistent], p_rssi=prev_rssi[consistent],
                               p_frame=prev_frame[consistent])
    for r in sub.itertuples(index=False):
        est = np.nanmean([r.p_rssi, r.vsi_rssi])
        for j in range(1, int(r.gap_d)):
            out.append((r.site_id, r.mmsi, r.channel, int(r.vsi_slot),
                        r.p_frame + pd.Timedelta(minutes=j), est, int(r.gap_d) - 1))
    return pd.DataFrame(out, columns=cols)


# ── 슬롯 침범 탐지 (오차/배율 무관 → 프리컴퓨트 대상) ─────────
def detect_intrusions(df: pd.DataFrame) -> pd.DataFrame:
    """'살아있는 예약 슬롯을 다른 선박이 차지한' 이벤트를 찾는다.

    정의(모두 관측된 positive 사실):
      · 피해자 F: 프레임 N−1 에서 (채널,슬롯 S)를 slot_timeout ≥ 1 로 송신
        → SOTDMA 규칙상 프레임 N 에도 S 를 써야 함(예약이 살아있음)
      · 침범자 G(≠F): 프레임 N 의 S 에서 수신됨, F 는 그 (채널,슬롯,프레임)에 없음
      · f_returns: F 가 프레임 N+1 에 S 로 복귀(예약 지속 확증 — 브라킷)
    F 가 실제로 N 에 송신했는지(물리적 비트충돌 여부)는 수신 데이터로 확정
    불가하므로, 이벤트는 '예약 침범'으로 명명하고 위반 집계와는 분리해 둔다.

    반환 columns: channel, slot, frame, victim, victim_timeout_prev,
      victim_rssi, victim_dist, intruder, intruder_rssi, intruder_dist,
      f_returns, victim_rssi_after
    """
    cols = ["site_id", "channel", "slot", "frame", "victim", "victim_timeout_prev",
            "victim_rssi", "victim_dist", "intruder", "intruder_rssi",
            "intruder_dist", "f_returns", "victim_rssi_after"]
    t1 = df[(df["msg_type"] == 1) & df["slot_timeout"].notna()
            & df["channel"].isin(["A", "B"])]
    if t1.empty:
        return pd.DataFrame(columns=cols)

    occ = pd.DataFrame({
        "site_id": t1["site_id"].values,
        "channel": t1["channel"].values, "slot": t1["vsi_slot"].astype(int).values,
        "frame": t1["frame"].values, "mmsi": t1["mmsi"].values,
        "timeout": t1["slot_timeout"].astype(int).values,
        "rssi": t1["vsi_rssi"].values, "dist": t1["dist_km"].values})

    # 예약자: timeout>=1 → 다음 프레임의 그 슬롯을 예약. 슬롯당 최강 RSSI 대표.
    res = occ[occ["timeout"] >= 1].copy()
    res["frame"] = res["frame"] + pd.Timedelta(minutes=1)
    res = (res.sort_values("rssi", ascending=False)
              .drop_duplicates(["site_id", "channel", "slot", "frame"])
              .rename(columns={"mmsi": "victim", "timeout": "victim_timeout_prev",
                               "rssi": "victim_rssi", "dist": "victim_dist"}))

    # 이번 프레임 점유자에 예약자 정보를 붙임.
    # site_id 를 키에 넣는 이유: 두 장소에서 동시에 수집하면 서로 다른 수신국이
    # 같은 (채널,슬롯,프레임)을 보게 되고, 무관한 선박끼리 침범으로 오탐된다.
    m = occ.merge(res, on=["site_id", "channel", "slot", "frame"], how="inner")
    if m.empty:
        return pd.DataFrame(columns=cols)

    # 그 (장소,채널,슬롯,프레임)에 피해자 본인이 있으면 침범 아님(정상 반복)
    key = ["site_id", "channel", "slot", "frame"]
    victim_present = (m[m["mmsi"] == m["victim"]][key].drop_duplicates()
                      .assign(_vp=True))
    m = m.merge(victim_present, on=key, how="left")
    cand = m[(m["_vp"].isna()) & (m["mmsi"] != m["victim"])]
    if cand.empty:
        return pd.DataFrame(columns=cols)

    # 슬롯·프레임당 최강 침범자 1명으로 요약
    ev = (cand.sort_values("rssi", ascending=False).drop_duplicates(key)
              .rename(columns={"mmsi": "intruder", "rssi": "intruder_rssi",
                               "dist": "intruder_dist"}))

    # 브라킷: 피해자가 다음 프레임에 그 슬롯으로 복귀했는가 (+복귀 RSSI)
    nxt = occ[["site_id", "channel", "slot", "frame", "mmsi", "rssi"]].copy()
    nxt["frame"] = nxt["frame"] - pd.Timedelta(minutes=1)
    nxt = (nxt.rename(columns={"mmsi": "victim", "rssi": "victim_rssi_after"})
              .sort_values("victim_rssi_after", ascending=False)
              .drop_duplicates(key + ["victim"]))
    ev = ev.merge(nxt, on=key + ["victim"], how="left")
    ev["f_returns"] = ev["victim_rssi_after"].notna()

    return ev[cols].sort_values("frame").reset_index(drop=True)


# ── classify (싸다, 슬라이더 임계값 적용) ────────────────────
def _signal_margin(df: pd.DataFrame, noise_df, offset_min: int = 0) -> np.ndarray:
    """각 행의 (RSSI − 잡음층)[dB]. offset_min 프레임 뒤의 잡음층 기준. 없으면 nan.

    그 시점 주변 잡음층(다른 선박들의 RSSI−SNR 중앙값) 대비 이 선박의 RSSI 여유.
    여유가 작으면 신호가 잡음에 묻혀 유실됐을 '환경성' 근거가 된다.
      offset_min=0 → 이 프레임(보고 유실 판정), 1 → 다음 프레임(슬롯 미수신 판정)
    """
    n = len(df)
    if noise_df is None or not len(noise_df):
        return np.full(n, np.nan)
    # 같은 (장소, 채널, 프레임)의 잡음층을 찾는다. 채널까지 맞추는 이유는
    # A/B 의 잡음이 서로 다르기 때문이다(FSR 실측 기준 수 dB 차이).
    idx = noise_df.set_index(["site_id", "channel", "frame"])["noise_dbm"]
    keys = pd.MultiIndex.from_arrays([
        df["site_id"].values, df["channel"].values,
        (df["frame"] + pd.Timedelta(minutes=offset_min)).values])
    noise = idx.reindex(keys).values
    return df["vsi_rssi"].values - noise


def classify(df: pd.DataFrame, fast_factor=0.5, grid_tol=0.2,
             noise_df=None, decode_margin=10.0) -> pd.DataFrame:
    """enrich 된 df 에 사용자 임계값을 적용해 사유 컬럼 생성(벡터화, 즉시).
    추가/갱신: ri_reason, ri_missed_count, slot_reason, is_violation

    보고주기(메시지마다 비율 = 실제간격/기대간격, k = round(비율)):
      - 비율 < fast_factor            → 과도한 보고 (RI_TOO_FAST)
      - |실제 − k·기대| ≤ 허용오차:
          허용오차 = grid_tol·기대  (기대 ≤ 60초, SOTDMA 선택구간 ±0.2·NI 근거)
                   = SLOW_INTERVAL_TOL_SEC (기대 > 60초, 정박 등 프레임 고정 → 절대 ±초로 엄격)
          k = 1 → 정상
          k ≥ 2 → 간격이 기대의 정수배. 이 선박의 달성 최소간격(achieved_ratio_floor)으로 구분:
              floor ≥ UNDER_FLOOR_RATIO → 과소 보고(RI_UNDER_REPORT, 위반: 원래 느리게 쏨)
              그 외 → 이번 간격만 유실(위반 아님). 신호여유(RSSI − 이 프레임 잡음층)로
                      < decode_margin → 환경성 유실(RI_LOST_NOISE) / 그 외 → 원인 미상(RI_LOST_PENDING)
      - 어느 정수배와도 안 맞음 (|실제 − k·기대| > 허용오차) → 보고주기 부적합 (RI_INTERVAL_BAD)

    슬롯 체인(이산 검증 → 허용오차 없음, enrich 에서 산출한 지표를 그대로 적용):
      - to∈{2,4,6} sub_message≠vsi_slot → 슬롯번호 불일치
      - 다음 프레임에 수신됨(heard):
          repeat: 그 슬롯 미점유→반복누락 / 점유했으나 timeout≠to-1→미감소
          switch: 예고슬롯 미점유→예고불일치 / 점유했으나 timeout∉[3,7]→재초기화범위밖
      - 다음 프레임 미수신(¬heard) → 위반이 아니라 '검증 보류':
          (RSSI − 다음프레임 잡음층) < decode_margin → 환경성 유실 추정 / 그 외 → 원인 미상
    """
    df = df.copy()
    # ── 보고주기 ──
    exp = df["expected_interval"].values
    act = df["actual_gap"].values
    floor = df["achieved_ratio_floor"].values
    ri = np.full(len(df), "", dtype=object)
    missed = np.zeros(len(df), dtype=int)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = act / exp
        k = np.round(ratio)
        dev_time = np.abs(act - k * exp)                    # 정수배로부터의 시간 편차(초)
        allowed = np.where(exp <= 60.0, grid_tol * exp, SLOW_INTERVAL_TOL_SEC)
        valid = ~np.isnan(act)
        too_fast = valid & (ratio < fast_factor)
        on_grid = valid & ~too_fast & (dev_time <= allowed)
        off_grid = valid & ~too_fast & (dev_time > allowed)
        long_gap = on_grid & (k >= 2)                       # 기대의 정수배(≥2) = 누락 후보
        under = long_gap & (floor >= UNDER_FLOOR_RATIO)     # floor nan → False → 유실로
        loss = long_gap & ~under
        ri[too_fast] = "RI_TOO_FAST"
        ri[off_grid] = "RI_INTERVAL_BAD"
        ri[under] = "RI_UNDER_REPORT"
        missed[long_gap] = (k[long_gap] - 1).astype(int)
    # 유실(선박은 규정 주기 달성하지만 이 간격만 김)을 환경/보류로 분리
    if loss.any():
        margin = _signal_margin(df, noise_df, offset_min=0)
        lost_noise = loss & (margin < decode_margin)        # margin nan → False → pending
        ri[loss & ~lost_noise] = "RI_LOST_PENDING"
        ri[lost_noise] = "RI_LOST_NOISE"
    df["ri_missed_count"] = missed

    # ── 슬롯 체인 (이산, 허용오차 없음) ──
    kind = df["chain_kind"].values
    heard = df["chain_heard_next"].values.astype(bool)
    matched = df["chain_slot_matched"].values.astype(bool)
    nt = df["chain_next_timeout"].values
    et = df["chain_expected_timeout"].values
    num_bad = df["slot_num_bad"].values.astype(bool)
    slot = np.full(len(df), "", dtype=object)

    rep = kind == "repeat"
    sw = kind == "switch"
    with np.errstate(invalid="ignore"):
        # 다음 프레임에서 수신됨 → 슬롯 체인 실제 위반 판정
        slot[rep & heard & ~matched] = "SLOT_REPEAT_MISSING"
        slot[rep & heard & matched & (nt != et)] = "TIMEOUT_NOT_DECREMENTED"
        slot[sw & heard & ~matched] = "SLOT_SWITCH_MISSING"
        slot[sw & heard & matched & ~((nt >= TMO_MIN) & (nt <= TMO_MAX))] = \
            "TIMEOUT_REINIT_OUT_OF_RANGE"

        # 다음 프레임 미수신 → 검증 보류(위반 아님): 잡음층으로 환경성/미상 구분
        unheard = (rep | sw) & ~heard
        if unheard.any():
            margin = _signal_margin(df, noise_df, offset_min=1)
            env = unheard & (margin < decode_margin)     # margin nan → False → pending
            slot[unheard & ~env] = "SLOT_UNVERIF_PENDING"
            slot[env] = "SLOT_UNVERIF_NOISE"

    # 슬롯번호 불일치(2/4/6)는 가장 근본적 → 최우선 표기
    slot[num_bad] = "SLOT_NUM_MISMATCH"

    df["ri_reason"] = ri
    df["slot_reason"] = slot
    ri_is_viol = np.array([r in RI_VIOLATION_CODES for r in ri])
    slot_is_viol = np.array([s in SLOT_VIOLATION_CODES for s in slot])
    df["is_violation"] = ri_is_viol | slot_is_viol
    return df
