"""재사용 쿼리 함수 모음. 모두 통합 뷰 v_vsi(+ 원문 테이블)를 대상으로 하며,
집계는 최대한 DB(Postgres)에서 처리해 브라우저로는 작은 결과만 보낸다.
"""
import pandas as pd

from core.db import run_query
from core.constants import VIEW, RAW_TABLE


# ── 공통 조회 ────────────────────────────────────────────────
def get_time_bounds():
    """전체 데이터의 최소/최대 수신시각."""
    df = run_query(f"SELECT MIN(recv_time) AS lo, MAX(recv_time) AS hi FROM {VIEW}")
    return df.iloc[0]["lo"], df.iloc[0]["hi"]


def get_mmsi_options(limit: int = 2000) -> pd.DataFrame:
    """수신 건수 많은 순으로 MMSI 목록. columns=[mmsi, n]"""
    return run_query(
        f"SELECT mmsi, COUNT(*) AS n FROM {VIEW} "
        f"GROUP BY mmsi ORDER BY n DESC LIMIT :lim",
        {"lim": limit},
    )


def get_msg_type_counts() -> pd.DataFrame:
    """메시지 타입별 건수. columns=[msg_type, n]"""
    return run_query(
        f"SELECT msg_type, COUNT(*) AS n FROM {VIEW} "
        f"GROUP BY msg_type ORDER BY msg_type"
    )


# ── 탭 1: MMSI별 ─────────────────────────────────────────────
def stats_by_mmsi(mmsis: list[int]) -> pd.DataFrame:
    """선택 MMSI들의 RSSI/SNR 통계."""
    if not mmsis:
        return pd.DataFrame()
    return run_query(
        f"""
        SELECT mmsi,
               COUNT(*)                       AS n,
               ROUND(AVG(vsi_rssi)::numeric, 2) AS rssi_avg,
               MIN(vsi_rssi)                  AS rssi_min,
               MAX(vsi_rssi)                  AS rssi_max,
               ROUND(STDDEV(vsi_rssi)::numeric, 2) AS rssi_std,
               ROUND(AVG(vsi_snr)::numeric, 2)  AS snr_avg,
               MIN(vsi_snr)                   AS snr_min,
               MAX(vsi_snr)                   AS snr_max,
               ROUND(STDDEV(vsi_snr)::numeric, 2)  AS snr_std
        FROM {VIEW}
        WHERE mmsi = ANY(:mmsis)
        GROUP BY mmsi ORDER BY mmsi
        """,
        {"mmsis": list(mmsis)},
    )


def dist_by_mmsi(mmsis: list[int], metric: str) -> pd.DataFrame:
    """선택 MMSI별 RSSI 또는 SNR 값 분포(값별 건수). columns=[mmsi, value, n]
    metric: 'vsi_rssi' | 'vsi_snr'
    """
    assert metric in ("vsi_rssi", "vsi_snr")
    if not mmsis:
        return pd.DataFrame()
    return run_query(
        f"""
        SELECT mmsi, {metric} AS value, COUNT(*) AS n
        FROM {VIEW}
        WHERE mmsi = ANY(:mmsis)
        GROUP BY mmsi, {metric} ORDER BY mmsi, value
        """,
        {"mmsis": list(mmsis)},
    )


# ── 탭 2: 시간별 ─────────────────────────────────────────────
def timeseries(bucket: str, start, end,
               mmsis: list[int] | None = None,
               msg_types: list[int] | None = None) -> pd.DataFrame:
    """시간 버킷(minute/hour)별 RSSI/SNR 평균 + 건수.
    columns=[ts, n, rssi_avg, snr_avg]
    """
    assert bucket in ("minute", "hour")
    where = ["recv_time BETWEEN :start AND :end"]
    params = {"start": start, "end": end, "bucket": bucket}
    if mmsis:
        where.append("mmsi = ANY(:mmsis)")
        params["mmsis"] = list(mmsis)
    if msg_types:
        where.append("msg_type = ANY(:mtypes)")
        params["mtypes"] = list(msg_types)
    where_sql = " AND ".join(where)
    return run_query(
        f"""
        SELECT date_trunc(:bucket, recv_time) AS ts,
               COUNT(*)                        AS n,
               ROUND(AVG(vsi_rssi)::numeric, 2) AS rssi_avg,
               ROUND(AVG(vsi_snr)::numeric, 2)  AS snr_avg
        FROM {VIEW}
        WHERE {where_sql}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    )


# ── 탭 3: 메시지별 (전체 메시지 탐색) ────────────────────────
def stats_by_msg_type() -> pd.DataFrame:
    """메시지 타입별 RSSI/SNR 통계(박스플롯/비교용)."""
    return run_query(
        f"""
        SELECT msg_type,
               COUNT(*)                       AS n,
               ROUND(AVG(vsi_rssi)::numeric, 2) AS rssi_avg,
               MIN(vsi_rssi) AS rssi_min, MAX(vsi_rssi) AS rssi_max,
               percentile_cont(0.25) WITHIN GROUP (ORDER BY vsi_rssi) AS rssi_q1,
               percentile_cont(0.50) WITHIN GROUP (ORDER BY vsi_rssi) AS rssi_med,
               percentile_cont(0.75) WITHIN GROUP (ORDER BY vsi_rssi) AS rssi_q3,
               ROUND(AVG(vsi_snr)::numeric, 2)  AS snr_avg,
               MIN(vsi_snr) AS snr_min, MAX(vsi_snr) AS snr_max,
               percentile_cont(0.25) WITHIN GROUP (ORDER BY vsi_snr) AS snr_q1,
               percentile_cont(0.50) WITHIN GROUP (ORDER BY vsi_snr) AS snr_med,
               percentile_cont(0.75) WITHIN GROUP (ORDER BY vsi_snr) AS snr_q3
        FROM {VIEW}
        GROUP BY msg_type ORDER BY msg_type
        """
    )


def count_messages(msg_types: list[int] | None, mmsis: list[int] | None,
                   start, end) -> int:
    """탐색기 필터 조건에 맞는 전체 건수(페이지네이션용)."""
    where, params = _explorer_where(msg_types, mmsis, start, end)
    df = run_query(f"SELECT COUNT(*) AS n FROM {VIEW} v WHERE {where}", params)
    return int(df.iloc[0]["n"])


def list_messages(msg_types: list[int] | None, mmsis: list[int] | None,
                  start, end, limit: int, offset: int,
                  with_raw: bool = True) -> pd.DataFrame:
    """전체 메시지 탐색기: 조건에 맞는 원문 행을 페이지 단위로 반환."""
    where, params = _explorer_where(msg_types, mmsis, start, end)
    params.update({"lim": limit, "off": offset})
    raw_cols = ", m.ais_raw, m.vsi_raw" if with_raw else ""
    join = f"JOIN {RAW_TABLE} m ON m.id = v.source_id" if with_raw else ""
    return run_query(
        f"""
        SELECT v.source_id, v.recv_time, v.mmsi, v.msg_type,
               v.vsi_rssi, v.vsi_snr,
               v.vsi_hour, v.vsi_minute, v.vsi_second {raw_cols}
        FROM {VIEW} v {join}
        WHERE {where}
        ORDER BY v.recv_time
        LIMIT :lim OFFSET :off
        """,
        params,
    )


def _explorer_where(msg_types, mmsis, start, end):
    where = ["v.recv_time BETWEEN :start AND :end"]
    params = {"start": start, "end": end}
    if msg_types:
        where.append("v.msg_type = ANY(:mtypes)")
        params["mtypes"] = list(msg_types)
    if mmsis:
        where.append("v.mmsi = ANY(:mmsis)")
        params["mmsis"] = list(mmsis)
    return " AND ".join(where), params
