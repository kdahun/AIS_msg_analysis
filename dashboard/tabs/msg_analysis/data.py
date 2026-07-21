"""메시지 분석 데이터 계층 — 시작 시 전체 프리컴퓨트, 이후 조회는 즉시.

2단계 캐시:
  ① 디스크(parquet, dashboard/data_cache/): DB 지문(행수+max recv_time)+LOGIC_VERSION
     이 같으면 SQL/enrich 를 건너뛰고 수 초 내 로드. 데이터가 바뀌면 자동 재계산.
  ② 메모리(st.cache_resource): 로드된 DataFrame + 프레임별 행 인덱스(groupby.indices)
     → 슬롯맵 프레임 이동이 풀스캔 없이 O(1).

classify(슬라이더 반응)는 파라미터 조합별로 st.cache_resource 에 메모 —
페이지들은 반환 DataFrame 을 **수정하지 말 것**(공유 객체).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import text

from core.db import get_engine
from . import logic

CACHE_DIR = Path(__file__).resolve().parents[2] / "data_cache"

# channel(A/B)은 타입별 테이블에 없어 원문(ais_messages.ais_raw)의 5번째 필드에서 추출한다.
_LOAD_SQL = """
SELECT t.mmsi, t.msg_type,
       COALESCE(date(t.recv_time - interval '9 hours')
         + make_interval(hours=>t.vsi_hour, mins=>t.vsi_minute, secs=>t.vsi_second)
         + interval '9 hours', t.recv_time) AS vsi_time,
       t.vsi_slot, t.slot_timeout, t.sub_message, t.speed, t.status, t.heading, t.course,
       t.vsi_rssi, t.vsi_snr, t.lon, t.lat,
       m.site_id,
       split_part(m.ais_raw, ',', 5) AS channel
FROM (
  SELECT source_id, mmsi, 1 AS msg_type, recv_time, vsi_hour, vsi_minute, vsi_second,
         vsi_slot, slot_timeout, sub_message, speed, status, heading, course,
         vsi_rssi, vsi_snr, lon, lat
  FROM ais_msg_1
  UNION ALL
  SELECT source_id, mmsi, 3, recv_time, vsi_hour, vsi_minute, vsi_second,
         vsi_slot, NULL, NULL, speed, status, heading, course,
         vsi_rssi, vsi_snr, lon, lat
  FROM ais_msg_3
) t
JOIN ais_messages m ON m.id = t.source_id
ORDER BY t.mmsi, vsi_time
"""

# 데이터가 바뀌면 캐시 키가 달라지도록 하는 지문. ais_fsr 도 잡음층에 쓰이므로 포함한다.
_FINGERPRINT_SQL = """
SELECT (SELECT count(*) FROM ais_msg_1) + (SELECT count(*) FROM ais_msg_3)
         + (SELECT count(*) FROM ais_fsr) AS n,
       greatest((SELECT max(recv_time) FROM ais_msg_1),
                (SELECT max(recv_time) FROM ais_msg_3)) AS t
"""

# 수집 장소 카탈로그. 거리 계산의 기준점이 장소마다 다르므로 행별로 붙여 쓴다.
_SITES_SQL = "SELECT id AS site_id, code, name, lat, lon FROM rx_sites ORDER BY id"

# 수신기가 분·채널마다 남긴 프레임 통계($AIFSR).
# frame 은 생성 컬럼(report_time − 1분)이라 아래 값들이 설명하는 구간과 정확히 맞는다.
_FSR_SQL = """
SELECT site_id, trim(channel) AS channel, frame,
       noise_dbm, rx_slots, crc_fail, strong_slots, ext_res
FROM ais_fsr
"""

# 프레임별로 우리가 실제로 받은 슬롯 수. FSR 의 rx_slots 와 대조하기 위한 것이다.
#
#  · 단위가 '슬롯' 이므로 메시지 건수가 아니라 **VDM 파트 수**를 세야 한다.
#    Type 5 처럼 2파트 메시지는 슬롯을 2개 먹는다. 멀티파트는 적재 시 '|' 로
#    이어 붙여 한 행에 담기므로, 자른 개수가 곧 점유 슬롯 수다.
#  · Type 1/3 만 보는 _LOAD_SQL 과 달리 **모든 타입**을 세야 rx_slots 와 맞는다.
#  · 프레임 기준은 _LOAD_SQL 의 vsi_time 과 같은 방식(날짜는 recv_time, 시:분은 VSI).
_FRAME_SLOTS_SQL = """
SELECT m.site_id,
       split_part(m.ais_raw, ',', 5) AS channel,
       date(m.recv_time - interval '9 hours')
         + make_interval(hours => v.vsi_hour, mins => v.vsi_minute)
         + interval '9 hours'                            AS frame,
       count(*)                                          AS msgs,
       sum(array_length(string_to_array(m.ais_raw, '|'), 1)) AS used_slots
FROM v_vsi v
JOIN ais_messages m ON m.id = v.source_id
WHERE m.ais_raw IS NOT NULL AND v.vsi_hour IS NOT NULL
GROUP BY 1, 2, 3
"""

# 구간(segment) 경계: 전 선박이 이만큼 조용하면 수신이 멈춘 것으로 본다.
# 근거 — 정상 상태에서 무수신이 2초를 넘은 적이 한 번도 없고(전체 100만 간격 중 0건),
# 실제 중단은 최소 17초였다. 그 사이가 비어 있어 5초면 양쪽으로 넉넉하다.
SEGMENT_GAP_SEC = 5.0


def _haversine_km(lat1, lon1, lat2, lon2):
    """두 좌표 사이 거리(km). 배열을 받아 벡터 연산한다."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(a))


def assign_segments(df: pd.DataFrame, gap_sec: float = SEGMENT_GAP_SEC) -> pd.Series:
    """'연속 수신 구간' 번호를 매긴다. 시계열 계산을 끊는 단위다.

    새 구간이 시작되는 조건
      · 수집 장소가 바뀜        → 이동 중 간격은 선박 잘못이 아니다
      · 전 선박 무수신 gap_sec 이상 → 장비가 꺼진 구간

    보고 간격·슬롯 체인을 이 경계 너머로 이으면 위반으로 오탐된다.
    날짜는 경계로 쓰지 않는다 — 자정에는 아무 일도 일어나지 않으므로,
    거기서 끊으면 자정을 넘는 정상 간격까지 버리게 된다.
    """
    d = df[["site_id", "vsi_time"]].sort_values("vsi_time", kind="mergesort")
    new = ((d["site_id"] != d["site_id"].shift(1))
           | (d["vsi_time"].diff().dt.total_seconds() >= gap_sec))
    new.iloc[0] = True
    return new.cumsum().astype("int32")      # 인덱스가 같아 원래 행 순서로 정렬된다


def segment_summary(df: pd.DataFrame) -> pd.DataFrame:
    """구간별 시작·끝·메시지수. 화면 표시와 검증에 쓴다."""
    g = df.groupby("segment_id")
    return pd.DataFrame({
        "site_id": g["site_id"].first(),
        "start": g["vsi_time"].min(),
        "end": g["vsi_time"].max(),
        "n_msg": g.size(),
    }).reset_index()


def _cache_key() -> str:
    eng = get_engine()
    with eng.connect() as conn:
        n, t = conn.execute(text(_FINGERPRINT_SQL)).one()
    return f"v{logic.LOGIC_VERSION}_{n}_{pd.Timestamp(t):%Y%m%d%H%M%S}"


def _build_noise(enriched: pd.DataFrame, fsr: pd.DataFrame) -> pd.DataFrame:
    """(장소·채널·프레임) 잡음층. FSR 실측을 쓰고, 없는 프레임만 추정으로 메운다.

    columns=[site_id, channel, frame, noise_est, noise_fsr, noise_dbm, noise_src]
      noise_est  수신 메시지들의 median(RSSI − SNR) — 우리가 계산한 추정치
      noise_fsr  수신기가 직접 잰 값(ais_fsr.noise_dbm)
      noise_dbm  판정에 실제로 쓰는 값 = 실측 우선, 없으면 추정
      noise_src  그 값이 어디서 왔는지("FSR" / "추정")

    추정치는 실측보다 체계적으로 3~4dB 낮게(더 조용하게) 나온다. 수신에 성공한
    메시지만 가지고 재기 때문이다 — 잡음이 큰 순간의 메시지는 애초에 못 받으므로
    표본에서 빠지고, 결과적으로 조용한 쪽으로 치우친다. 그대로 두면 신호 여유를
    그만큼 크게 보게 되어 decode_margin 기준이 관대해진다.
    """
    est = ((enriched["vsi_rssi"] - enriched["vsi_snr"])
           .groupby([enriched["site_id"], enriched["channel"], enriched["frame"]])
           .median().rename("noise_est").reset_index())
    key = ["site_id", "channel", "frame"]
    out = est.merge(fsr[key + ["noise_dbm"]].rename(columns={"noise_dbm": "noise_fsr"}),
                    on=key, how="left")
    out["noise_dbm"] = out["noise_fsr"].fillna(out["noise_est"])
    out["noise_src"] = np.where(out["noise_fsr"].notna(), "FSR", "추정")
    return out


def _build_segments(enriched: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """구간 목록과 '왜 끊겼는지'. 화면에서 수집 이력을 훑는 용도.

    columns=[segment_id, site_id, code, name, start, end, duration_min, n_msg,
             gap_sec, gap_reason]
      gap_sec/gap_reason 은 **앞 구간과의 공백**이다.
        수집 시작 / 장소 이동 / 장비 중단
    """
    g = enriched.groupby("segment_id")
    seg = pd.DataFrame({
        "site_id": g["site_id"].first(),
        "start": g["vsi_time"].min(),
        "end": g["vsi_time"].max(),
        "n_msg": g.size(),
    }).reset_index().sort_values("start").reset_index(drop=True)
    seg = seg.merge(sites[["site_id", "code", "name"]], on="site_id", how="left")
    seg["duration_min"] = ((seg["end"] - seg["start"]).dt.total_seconds() / 60).round(1)

    prev_end, prev_site = seg["end"].shift(1), seg["site_id"].shift(1)
    seg["gap_sec"] = (seg["start"] - prev_end).dt.total_seconds().round(0)
    seg["gap_reason"] = np.where(
        prev_end.isna(), "수집 시작",
        np.where(seg["site_id"] != prev_site, "장소 이동", "장비 중단"))
    return seg


def _runs(frames: pd.Series) -> list[tuple]:
    """연속된 분(分)들을 (시작, 끝, 개수) 구간으로 묶는다."""
    f = pd.Series(sorted(frames.unique()))
    if f.empty:
        return []
    brk = (f.diff() != pd.Timedelta(minutes=1)).cumsum()
    return [(g.min(), g.max(), len(g)) for _, g in f.groupby(brk)]


def device_status_runs(frame_slots: pd.DataFrame) -> pd.DataFrame:
    """'반쪽 가동' 구간 — 메시지는 들어오는데 FSR 만 없는 시간대.

    장비가 꺼진 게 아니다. 실제로 그런 구간에서 메시지가 분당 수백 건씩 정상
    수신되고 RSSI·SNR·슬롯 분포도 정상 구간과 구분되지 않는다. 재기동 직후
    상태 출력 계통만 복구되지 않은 상태로 보인다.
    → 데이터는 그대로 쓰되, FSR 기반 지표(잡음 실측·rx_slots 대조)만 비워 둔다.
    """
    half = frame_slots[frame_slots["status"] == "FSR 없음"]
    rows = []
    for (site_id, ch), g in half.groupby(["site_id", "channel"]):
        for start, end, n in _runs(g["frame"]):
            rows.append((site_id, ch, start, end, n,
                         int(g[g["frame"].between(start, end)]["msgs"].sum())))
    return pd.DataFrame(rows, columns=["site_id", "channel", "start", "end",
                                       "n_frames", "n_msg"]).sort_values("start")


def _build_frame_slots(slots: pd.DataFrame, fsr: pd.DataFrame,
                       enriched: pd.DataFrame) -> pd.DataFrame:
    """프레임별 '장비가 받은 슬롯 수' vs '우리 로그에 남은 슬롯 수'.

    columns=[site_id, channel, frame, msgs, used_slots, rx_slots, missing_slots,
             crc_fail, strong_slots, noise_dbm, status]

    missing_slots(= rx_slots − used_slots)는 **전파상 유실이 아니다.**
    rx_slots 는 장비가 이미 디코딩에 성공한 슬롯이므로, 우리 로그에 없다는 건
    장비 출력과 파일 기록 사이에서 빠졌다는 뜻이다. 로그 무결성 지표로 쓴다.
    진짜 유실은 crc_fail(검출됐으나 디코딩 실패)과 아예 검출 못한 것이다.

    status 는 그 프레임을 그대로 믿어도 되는지 알려준다.
      구간 시작/종료 : 그 1분을 통째로 받지 못해 원래 많이 비어 보인다
      FSR 없음       : 장비가 수신은 하는데 상태 문장만 안 낸 구간(비교 불가)
      메시지 없음     : 그 분에 수신이 아예 없었다
    """
    key = ["site_id", "channel", "frame"]
    out = slots.merge(fsr[key + ["rx_slots", "crc_fail", "strong_slots", "noise_dbm"]],
                      on=key, how="outer")
    out["missing_slots"] = out["rx_slots"] - out["used_slots"]

    # 구간의 첫/마지막 프레임 — 반쪽만 수신되므로 비교에서 빼고 봐야 한다
    seg = enriched.groupby("segment_id")["frame"]
    edges = set(seg.min()) | set(seg.max())

    # 판정 순서가 중요하다. 구간의 첫/마지막 프레임은 그 1분을 통째로 받지 못해
    # FSR 도 안 나오는 게 정상이므로, 'FSR 없음'보다 먼저 걸러야 한다.
    # 그러지 않으면 수집이 분 중간에 끝난 것까지 '반쪽 가동'으로 잡힌다.
    out["status"] = np.select(
        [out["used_slots"].isna(), out["frame"].isin(edges), out["rx_slots"].isna()],
        ["메시지 없음", "구간 시작/종료", "FSR 없음"],
        default="")
    return out.sort_values(["frame", "site_id", "channel"]).reset_index(drop=True)


def _precompute(key: str) -> tuple[pd.DataFrame, ...]:
    """SQL 로드 → enrich → 잡음층 → 침범 탐지. parquet 저장 후 반환."""
    eng = get_engine()
    with eng.connect() as conn:
        df = pd.read_sql(text(_LOAD_SQL), conn)
        sites = pd.read_sql(text(_SITES_SQL), conn)
        fsr = pd.read_sql(text(_FSR_SQL), conn)
        slots = pd.read_sql(text(_FRAME_SLOTS_SQL), conn)
    df["vsi_time"] = pd.to_datetime(df["vsi_time"])
    df["frame"] = df["vsi_time"].dt.floor("min")   # 1프레임 = 1분(UTC분 경계와 일치)

    # 연속 수신 구간 — 아래 enrich/침범/유실이 이 경계를 넘지 않도록 한다.
    df["segment_id"] = assign_segments(df)

    # 거리는 그 메시지를 받은 장소의 좌표 기준이다. 장소마다 다르므로 행별로 붙인다.
    rx = df["site_id"].map(sites.set_index("site_id")["lat"]), \
         df["site_id"].map(sites.set_index("site_id")["lon"])
    valid_pos = (df["lon"].between(-180, 180) & df["lat"].between(-90, 90)
                 & rx[0].notna())
    df["dist_km"] = np.where(
        valid_pos, _haversine_km(rx[0], rx[1], df["lat"], df["lon"]), np.nan)

    enriched = logic.enrich_all(df)
    noise = _build_noise(enriched, fsr)
    frame_slots = _build_frame_slots(slots, fsr, enriched)
    segments = _build_segments(enriched, sites)
    intrusions = logic.detect_intrusions(enriched)
    losses = logic.build_loss_layer(enriched)

    CACHE_DIR.mkdir(exist_ok=True)
    for old in CACHE_DIR.glob("*.parquet"):      # 이전 키 캐시 정리
        old.unlink(missing_ok=True)
    enriched.to_parquet(CACHE_DIR / f"enriched_{key}.parquet")
    noise.to_parquet(CACHE_DIR / f"noise_{key}.parquet")
    intrusions.to_parquet(CACHE_DIR / f"intrusions_{key}.parquet")
    losses.to_parquet(CACHE_DIR / f"losses_{key}.parquet")
    fsr.to_parquet(CACHE_DIR / f"fsr_{key}.parquet")
    frame_slots.to_parquet(CACHE_DIR / f"frameslots_{key}.parquet")
    segments.to_parquet(CACHE_DIR / f"segments_{key}.parquet")
    return enriched, noise, intrusions, losses, fsr, frame_slots, segments


_BUNDLE_PARTS = ("enriched", "noise", "intrusions", "losses", "fsr",
                 "frameslots", "segments")


@st.cache_resource(show_spinner="메시지 분석 데이터 준비 중... (데이터 변경 시에만 오래 걸립니다)")
def get_bundle() -> dict:
    """전체 프리컴퓨트 번들. 반환 dict 의 DataFrame 은 수정 금지(공유).

    keys: enriched, noise, intrusions, losses(슬롯 특정 유실 레이어),
          fsr(수신기 프레임 통계), frameslots(프레임별 수신 슬롯 대조),
          segments(구간 목록),
          frames(정렬된 프레임 배열), frame_idx(frame→행위치 ndarray)
    """
    key = _cache_key()
    paths = {n: CACHE_DIR / f"{n}_{key}.parquet" for n in _BUNDLE_PARTS}
    if all(p.exists() for p in paths.values()):
        parts = [pd.read_parquet(paths[n]) for n in _BUNDLE_PARTS]
    else:
        parts = list(_precompute(key))
    enriched, noise, intrusions, losses, fsr, frameslots, segments = parts

    frame_idx = enriched.groupby("frame").indices          # {Timestamp: ndarray}
    frames = np.array(sorted(frame_idx.keys()))
    return dict(enriched=enriched, noise=noise, intrusions=intrusions,
                losses=losses, fsr=fsr, frameslots=frameslots,
                segments=segments, frames=frames, frame_idx=frame_idx)


@st.cache_resource(max_entries=4, show_spinner=False)
def get_classified(grid_tol: float, fast_factor: float, decode_margin: float) -> pd.DataFrame:
    """슬라이더 파라미터 조합별 classify 결과(행 순서 = enriched 와 동일). 수정 금지."""
    b = get_bundle()
    return logic.classify(b["enriched"], fast_factor=fast_factor, grid_tol=grid_tol,
                          noise_df=b["noise"], decode_margin=decode_margin)


def get_noise_floor() -> pd.DataFrame:
    """잡음층 시계열. columns=[site_id, channel, frame, noise_dbm]

    판정(classify)은 이 정밀한 값을 그대로 쓴다. 화면에 선 하나로 그릴 때는
    아래 noise_frame_* 로 프레임 단위 하나로 줄여서 쓴다.
    """
    return get_bundle()["noise"]


def noise_frame_series(noise_df: pd.DataFrame) -> pd.Series:
    """표시용 — 프레임 하나당 잡음층 하나(장소·채널을 가로질러 중앙값).

    frame 을 인덱스로 하는 Series 라 .map()/.get() 으로 바로 쓸 수 있다.
    """
    return noise_df.groupby("frame")["noise_dbm"].median()


def noise_frame_df(noise_df: pd.DataFrame) -> pd.DataFrame:
    """표시용 — columns=[frame, noise_dbm]. 차트 함수들이 기대하는 모양."""
    return noise_frame_series(noise_df).reset_index()


def frame_slice(df: pd.DataFrame, frame) -> pd.DataFrame:
    """프레임 인덱스로 해당 분의 행만 O(1) 조회 (classified/enriched 공용)."""
    idx = get_bundle()["frame_idx"].get(pd.Timestamp(frame))
    if idx is None:
        return df.iloc[0:0]
    return df.iloc[idx]
