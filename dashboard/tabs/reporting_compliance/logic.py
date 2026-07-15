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

# sentinel
HEADING_NA = 511
COURSE_NA = 360.0
SPEED_NA = 102.3
ANCHORED_MOORED = (1, 5)          # status: 1=at anchor, 5=moored

COURSE_CHANGE_DEG = 5.0
COURSE_AVG_WINDOW = "30s"
SLOTS_PER_FRAME = 2250

# 사유 코드 → 한글 (여러 곳(표/차트 hover)에서 공유)
REASON_LABELS_KO = {
    "RI_TOO_SLOW": "보고 지연(기대보다 늦음)",
    "RI_TOO_FAST": "과도한 보고(기대보다 빠름)",
    "SLOT_REPEAT_MISSING": "슬롯 반복 누락(1프레임 뒤 같은 슬롯 없음)",
    "SLOT_SWITCH_MISSING": "슬롯 교체 예고 불일치(예고슬롯 미등장)",
    "TIMEOUT_NOT_DECREMENTED": "timeout 미감소(1씩 안 줄어듦)",
    "TIMEOUT_REINIT_OUT_OF_RANGE": "timeout 재초기화 범위밖([3,7] 벗어남)",
}


def reason_ko(code: str) -> str:
    """빈 문자열/None 은 '정상'으로."""
    if not code:
        return "정상"
    return REASON_LABELS_KO.get(code, code)


def combined_reason_ko(ri_reason: str, slot_reason: str) -> str:
    parts = [reason_ko(r) for r in (ri_reason, slot_reason) if r]
    return " / ".join(parts) if parts else "정상"
FRAME_SEC = 60.0
TMO_MIN, TMO_MAX = 3, 7


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

    _enrich_slot_chain(df)
    return df


def _enrich_slot_chain(df: pd.DataFrame):
    """Type 1 슬롯 체인의 오차무관 지표를 채운다(제자리 수정)."""
    n = len(df)
    kind = np.full(n, "", dtype=object)
    gap_err = np.full(n, np.nan)           # 관련 슬롯의 '1프레임 뒤' 등장까지 오차(초)
    next_to = np.full(n, np.nan)           # 그 등장 시점의 slot_timeout
    exp_to = np.full(n, np.nan)            # 기대 timeout (repeat 케이스만; switch 는 [3,7])

    t1_mask = df["msg_type"].values == 1
    if not t1_mask.any():
        df["chain_kind"] = kind; df["chain_gap_err"] = gap_err
        df["chain_next_timeout"] = next_to; df["chain_expected_timeout"] = exp_to
        return

    # SOTDMA 통신상태는 "해당 채널의 그 슬롯"에만 적용된다(M.1371-6 A2-3.3.7.2.2).
    # 따라서 슬롯 체인 추적은 (채널, 슬롯) 단위로 한다. channel 컬럼이 없으면 슬롯만으로.
    has_ch = "channel" in df.columns
    t1 = df[t1_mask]
    if has_ch:
        slot_times = {k: g["vsi_time"].values for k, g in t1.groupby(["channel", "vsi_slot"])}
        slot_tos = {k: g["slot_timeout"].values for k, g in t1.groupby(["channel", "vsi_slot"])}
    else:
        slot_times = {(s,): g["vsi_time"].values for s, g in t1.groupby("vsi_slot")}
        slot_tos = {(s,): g["slot_timeout"].values for s, g in t1.groupby("vsi_slot")}

    def nearest_next_frame(key, t):
        arr = slot_times.get(key)
        if arr is None:
            return np.nan, np.nan
        target = t + np.timedelta64(int(FRAME_SEC * 1000), "ms")
        diffs = np.abs((arr - target) / np.timedelta64(1, "s"))
        j = int(np.argmin(diffs))
        return float(diffs[j]), float(slot_tos[key][j])

    ch_vals = df["channel"].values if has_ch else None
    for i in np.where(t1_mask)[0]:
        to = df["slot_timeout"].values[i]
        if to is None or pd.isna(to):
            continue
        to = int(to)
        slot = int(df["vsi_slot"].values[i])
        t = np.datetime64(df["vsi_time"].values[i])
        mk = (lambda s: (ch_vals[i], s)) if has_ch else (lambda s: (s,))
        if to > 0:
            ge, nt = nearest_next_frame(mk(slot), t)
            kind[i] = "repeat"; gap_err[i] = ge; next_to[i] = nt; exp_to[i] = to - 1
        else:
            sub = df["sub_message"].values[i]
            if sub is None or pd.isna(sub):
                continue
            pred = (slot + int(sub)) % SLOTS_PER_FRAME
            ge, nt = nearest_next_frame(mk(pred), t)
            kind[i] = "switch"; gap_err[i] = ge; next_to[i] = nt

    df["chain_kind"] = kind
    df["chain_gap_err"] = gap_err
    df["chain_next_timeout"] = next_to
    df["chain_expected_timeout"] = exp_to


def enrich_all(df: pd.DataFrame) -> pd.DataFrame:
    parts = [enrich_vessel(g) for _, g in df.groupby("mmsi", sort=False)]
    return pd.concat(parts, ignore_index=True)


# ── classify (싸다, 슬라이더 임계값 적용) ────────────────────
def classify(df: pd.DataFrame, slow_factor=2.0, fast_factor=0.5,
             time_tol_sec=5.0) -> pd.DataFrame:
    """enrich 된 df 에 사용자 임계값을 적용해 사유 컬럼 생성(벡터화, 즉시).
    추가/갱신: ri_reason, slot_reason, is_violation
    """
    df = df.copy()
    exp = df["expected_interval"].values
    act = df["actual_gap"].values
    ri = np.full(len(df), "", dtype=object)
    with np.errstate(invalid="ignore"):
        ri[act > exp * slow_factor] = "RI_TOO_SLOW"
        ri[(act < exp * fast_factor) & (ri == "")] = "RI_TOO_FAST"
    ri[np.isnan(act)] = ""

    kind = df["chain_kind"].values
    ge = df["chain_gap_err"].values
    nt = df["chain_next_timeout"].values
    et = df["chain_expected_timeout"].values
    slot = np.full(len(df), "", dtype=object)

    with np.errstate(invalid="ignore"):
        found = ge <= time_tol_sec       # 1프레임 뒤 해당 슬롯이 있었는가
        rep = kind == "repeat"
        sw = kind == "switch"
        slot[rep & ~found] = "SLOT_REPEAT_MISSING"
        slot[rep & found & (nt != et)] = "TIMEOUT_NOT_DECREMENTED"
        slot[sw & ~found] = "SLOT_SWITCH_MISSING"
        slot[sw & found & ~((nt >= TMO_MIN) & (nt <= TMO_MAX))] = "TIMEOUT_REINIT_OUT_OF_RANGE"

    df["ri_reason"] = ri
    df["slot_reason"] = slot
    df["is_violation"] = (ri != "") | (slot != "")
    return df
