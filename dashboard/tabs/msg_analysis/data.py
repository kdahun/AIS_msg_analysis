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

_FINGERPRINT_SQL = """
SELECT (SELECT count(*) FROM ais_msg_1) + (SELECT count(*) FROM ais_msg_3) AS n,
       greatest((SELECT max(recv_time) FROM ais_msg_1),
                (SELECT max(recv_time) FROM ais_msg_3)) AS t
"""

# 수집 장소 카탈로그. 거리 계산의 기준점이 장소마다 다르므로 행별로 붙여 쓴다.
_SITES_SQL = "SELECT id AS site_id, code, name, lat, lon FROM rx_sites ORDER BY id"

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


def _precompute(key: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """SQL 로드 → enrich → 잡음층 → 침범 탐지. parquet 저장 후 반환."""
    eng = get_engine()
    with eng.connect() as conn:
        df = pd.read_sql(text(_LOAD_SQL), conn)
        sites = pd.read_sql(text(_SITES_SQL), conn)
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
    # 잡음층 추정 = 그 프레임에 수신된 메시지들의 (RSSI − SNR) 중앙값.
    # 장소·채널까지 나눈다 — 수신국이 다르면 전파환경이 다르고, FSR 실측상
    # 채널 A 와 B 의 잡음도 서로 다르다. 섞으면 두 값의 중간이 나온다.
    noise = ((enriched["vsi_rssi"] - enriched["vsi_snr"])
             .groupby([enriched["site_id"], enriched["channel"], enriched["frame"]])
             .median().rename("noise_dbm").reset_index())
    intrusions = logic.detect_intrusions(enriched)
    losses = logic.build_loss_layer(enriched)

    CACHE_DIR.mkdir(exist_ok=True)
    for old in CACHE_DIR.glob("*.parquet"):      # 이전 키 캐시 정리
        old.unlink(missing_ok=True)
    enriched.to_parquet(CACHE_DIR / f"enriched_{key}.parquet")
    noise.to_parquet(CACHE_DIR / f"noise_{key}.parquet")
    intrusions.to_parquet(CACHE_DIR / f"intrusions_{key}.parquet")
    losses.to_parquet(CACHE_DIR / f"losses_{key}.parquet")
    return enriched, noise, intrusions, losses


_BUNDLE_PARTS = ("enriched", "noise", "intrusions", "losses")


@st.cache_resource(show_spinner="메시지 분석 데이터 준비 중... (데이터 변경 시에만 오래 걸립니다)")
def get_bundle() -> dict:
    """전체 프리컴퓨트 번들. 반환 dict 의 DataFrame 은 수정 금지(공유).

    keys: enriched, noise, intrusions, losses(슬롯 특정 유실 레이어),
          frames(정렬된 프레임 배열), frame_idx(frame→행위치 ndarray)
    """
    key = _cache_key()
    paths = {n: CACHE_DIR / f"{n}_{key}.parquet" for n in _BUNDLE_PARTS}
    if all(p.exists() for p in paths.values()):
        enriched, noise, intrusions, losses = (pd.read_parquet(paths[n])
                                               for n in _BUNDLE_PARTS)
    else:
        enriched, noise, intrusions, losses = _precompute(key)

    frame_idx = enriched.groupby("frame").indices          # {Timestamp: ndarray}
    frames = np.array(sorted(frame_idx.keys()))
    return dict(enriched=enriched, noise=noise, intrusions=intrusions,
                losses=losses, frames=frames, frame_idx=frame_idx)


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
