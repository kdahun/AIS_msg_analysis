"""보고주기 탭 데이터 로더. 무거운 enrich 는 @st.cache_data 로 세션당 1회만 수행."""
import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import text

from core.db import get_engine
from core.constants import RX_LAT, RX_LON
from . import logic

# channel(A/B)은 타입별 테이블에 없어 원문(ais_messages.ais_raw)의 5번째 필드에서 추출한다.
_LOAD_SQL = """
SELECT t.mmsi, t.msg_type,
       COALESCE(date(t.recv_time - interval '9 hours')
         + make_interval(hours=>t.vsi_hour, mins=>t.vsi_minute, secs=>t.vsi_second)
         + interval '9 hours', t.recv_time) AS vsi_time,
       t.vsi_slot, t.slot_timeout, t.sub_message, t.speed, t.status, t.heading, t.course,
       t.vsi_rssi, t.vsi_snr, t.lon, t.lat,
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


def _haversine_km(lat, lon):
    """수신국(RX_LAT/RX_LON = 한국해양대) 기준 거리(km)."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [RX_LAT, RX_LON, lat, lon])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(a))


@st.cache_data(ttl=3600, show_spinner="보고주기 검증 데이터 준비 중... (최초 1회, 약 30초)")
def get_enriched() -> pd.DataFrame:
    """전체 Type1/3 을 로드해 enrich(오차/배율 무관한 무거운 계산)까지 마친 DataFrame.
    거리(dist_km)는 전 구간을 한국해양대 좌표 기준으로 계산한다
    (모텔 구간은 실제 수신국이 달라 근사값임에 유의).
    """
    eng = get_engine()
    with eng.connect() as conn:
        df = pd.read_sql(text(_LOAD_SQL), conn)
    df["vsi_time"] = pd.to_datetime(df["vsi_time"])
    df["frame"] = df["vsi_time"].dt.floor("min")   # 1프레임 = 1분(UTC분 경계와 일치)

    valid_pos = (df["lon"].between(-180, 180)) & (df["lat"].between(-90, 90))
    df["dist_km"] = np.where(valid_pos, _haversine_km(df["lat"], df["lon"]), np.nan)

    return logic.enrich_all(df)


@st.cache_data(ttl=3600, show_spinner=False)
def get_noise_floor() -> pd.DataFrame:
    """분(프레임) 단위 잡음층 시계열. 전체 수신 메시지의 (RSSI − SNR) 중앙값.

    수신기는 SNR = RSSI − 잡음층 으로 계산하므로 RSSI − SNR 이 그 시점의
    주변 잡음층(dBm)이 된다. 중앙값을 써서 개별 측정 편차(±5dB)에 흔들리지
    않는 안정적 기준선을 만든다. columns=[frame, noise_dbm]
    """
    df = get_enriched()
    nf = (df["vsi_rssi"] - df["vsi_snr"]).groupby(df["frame"]).median()
    return nf.rename("noise_dbm").reset_index()
