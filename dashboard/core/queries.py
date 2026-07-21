"""재사용 쿼리 함수 모음. 모두 통합 뷰 v_vsi(+ 원문 테이블)를 대상으로 하며,
집계는 최대한 DB(Postgres)에서 처리해 브라우저로는 작은 결과만 보낸다.
"""
import math

import pandas as pd

from core.db import run_query
from core.constants import VIEW, RAW_TABLE, RX_LAT, RX_LON, UNIV_START


# ── 장소 필터 (사이드바 전역 선택) ────────────────────────────
# 두 장소는 수신국 위치·전파환경이 달라 RSSI 절대값을 직접 비교할 수 없다.
# 선택이 없으면 전체를 본다.
def get_site_options() -> pd.DataFrame:
    """수집 장소 목록 + 각 장소의 수신 건수. columns=[site_id, code, name, n]"""
    return run_query(
        f"""SELECT s.id AS site_id, s.code, s.name, count(v.source_id) AS n
              FROM rx_sites s LEFT JOIN {VIEW} v ON v.site_id = s.id
             GROUP BY 1, 2, 3 ORDER BY 1""")


def selected_sites() -> list[int] | None:
    """사이드바에서 고른 장소 id 목록. 선택이 없으면 None(전체)."""
    try:
        import streamlit as st
        picked = st.session_state.get("global_sites")
    except Exception:
        picked = None
    return [int(x) for x in picked] if picked else None


def _view(alias: str = "") -> str:
    """FROM 절에 넣을 뷰 표현식. 장소가 선택돼 있으면 그 장소로 좁힌 서브쿼리를 준다.

    site id 는 DB 에서 온 정수만 들어오므로 문자열로 이어 붙여도 안전하다.
    (run_query 가 (sql, params) 단위로 캐싱하므로 값이 SQL 에 있어야 캐시가 갈린다)
    """
    sites = selected_sites()
    if not sites:
        return f"{VIEW} {alias}".strip()
    ids = ",".join(str(s) for s in sites)
    return f"(SELECT * FROM {VIEW} WHERE site_id IN ({ids})) {alias or VIEW}"


# ── 공통 조회 ────────────────────────────────────────────────
def get_time_bounds(mmsis: list[int] | None = None):
    """최소/최대 수신시각. mmsis 를 주면 그 MMSI(들)로 한정된 범위를 반환한다."""
    if mmsis:
        df = run_query(
            f"SELECT MIN(recv_time) AS lo, MAX(recv_time) AS hi FROM {_view()} "
            f"WHERE mmsi = ANY(:mmsis)",
            {"mmsis": list(mmsis)},
        )
    else:
        df = run_query(f"SELECT MIN(recv_time) AS lo, MAX(recv_time) AS hi FROM {_view()}")
    return df.iloc[0]["lo"], df.iloc[0]["hi"]


def get_mmsi_options(limit: int = 2000) -> pd.DataFrame:
    """수신 건수 많은 순으로 MMSI 목록. columns=[mmsi, n]"""
    return run_query(
        f"SELECT mmsi, COUNT(*) AS n FROM {_view()} "
        f"GROUP BY mmsi ORDER BY n DESC LIMIT :lim",
        {"lim": limit},
    )


def get_msg_type_counts() -> pd.DataFrame:
    """메시지 타입별 건수. columns=[msg_type, n]"""
    return run_query(
        f"SELECT msg_type, COUNT(*) AS n FROM {_view()} "
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
        FROM {_view()}
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
        FROM {_view()}
        WHERE mmsi = ANY(:mmsis)
        GROUP BY mmsi, {metric} ORDER BY mmsi, value
        """,
        {"mmsis": list(mmsis)},
    )


# ── 탭 2: 시간별 ─────────────────────────────────────────────
def _vsi_where(start, end, mmsis: list[int] | None = None,
               msg_types: list[int] | None = None):
    where = ["recv_time BETWEEN :start AND :end"]
    params = {"start": start, "end": end}
    if mmsis:
        where.append("mmsi = ANY(:mmsis)")
        params["mmsis"] = list(mmsis)
    if msg_types:
        where.append("msg_type = ANY(:mtypes)")
        params["mtypes"] = list(msg_types)
    return " AND ".join(where), params


def timeseries(bucket: str, start, end,
               mmsis: list[int] | None = None,
               msg_types: list[int] | None = None) -> pd.DataFrame:
    """시간 버킷(minute/hour)별 RSSI/SNR 평균 + 건수. (참고용 — 기본은 points() 개별값 표시)
    columns=[ts, n, rssi_avg, snr_avg]
    """
    assert bucket in ("minute", "hour")
    where_sql, params = _vsi_where(start, end, mmsis, msg_types)
    params["bucket"] = bucket
    return run_query(
        f"""
        SELECT date_trunc(:bucket, recv_time) AS ts,
               COUNT(*)                        AS n,
               ROUND(AVG(vsi_rssi)::numeric, 2) AS rssi_avg,
               ROUND(AVG(vsi_snr)::numeric, 2)  AS snr_avg
        FROM {_view()}
        WHERE {where_sql}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    )


def count_points(start, end, mmsis: list[int] | None = None,
                 msg_types: list[int] | None = None) -> int:
    """조건에 맞는 개별 메시지(행) 총 건수."""
    where_sql, params = _vsi_where(start, end, mmsis, msg_types)
    df = run_query(f"SELECT COUNT(*) AS n FROM {_view()} WHERE {where_sql}", params)
    return int(df.iloc[0]["n"])


# recv_time(컬럼0, KST)은 메시지가 몰리는 구간에서 여러 메시지가 같은 값으로 뭉쳐 찍힌다
# (로거가 버퍼를 읽어들이는 시점 기준이라 배치 단위로 갱신됨). 반면 VSI 의 시/분/초(TOA)는
# 수신기가 메시지마다 개별적으로 찍는 정밀 시각(UTC)이라 실제 도착 순서를 더 정확히 반영한다.
# VSI 는 시각(HH:MM:SS.ffffff)만 있고 날짜가 없으므로, recv_time 을 UTC로 환산한 날짜와
# 결합해 정밀 타임스탬프를 만들고 다시 KST 로 되돌려 recv_time 과 같은 기준으로 표시한다.
_VSI_TIME_EXPR = """
    COALESCE(
        date(recv_time - interval '9 hours')
          + make_interval(hours => vsi_hour, mins => vsi_minute, secs => vsi_second)
          + interval '9 hours',
        recv_time
    )
"""


def points(start, end, mmsis: list[int] | None = None,
          msg_types: list[int] | None = None,
          limit: int = 50_000) -> tuple[pd.DataFrame, int]:
    """개별 메시지의 RSSI/SNR 원본 값(집계 없음). VSI 시각(vsi_time) 기준으로 정렬한다.
    조건에 맞는 총 건수가 limit 을 넘으면 그 시간순으로 균등한 간격 표본을 추출한다
    (앞부분만 자르지 않고 전체 구간에 고르게 분포하도록 MOD 기반 표본 사용).

    반환: (DataFrame[vsi_time, recv_time, mmsi, msg_type, vsi_rssi, vsi_snr], 전체 건수)
    """
    where_sql, params = _vsi_where(start, end, mmsis, msg_types)
    total = count_points(start, end, mmsis, msg_types)
    if total == 0:
        cols = ["vsi_time", "recv_time", "mmsi", "msg_type", "vsi_rssi", "vsi_snr"]
        return pd.DataFrame(columns=cols), 0

    if total <= limit:
        df = run_query(
            f"""
            SELECT {_VSI_TIME_EXPR} AS vsi_time,
                   recv_time, mmsi, msg_type, vsi_rssi, vsi_snr
            FROM {_view()}
            WHERE {where_sql}
            ORDER BY vsi_time
            """,
            params,
        )
        return df, total

    # 올림 나눗셈: 내림(//)을 쓰면 stride 가 작게 잡혀 결과가 limit 을 넘어설 수 있다.
    stride = max(1, math.ceil(total / limit))
    params["stride"] = stride
    df = run_query(
        f"""
        WITH base AS (
            SELECT {_VSI_TIME_EXPR} AS vsi_time,
                   recv_time, mmsi, msg_type, vsi_rssi, vsi_snr
            FROM {_view()}
            WHERE {where_sql}
        )
        SELECT vsi_time, recv_time, mmsi, msg_type, vsi_rssi, vsi_snr FROM (
            SELECT *, ROW_NUMBER() OVER (ORDER BY vsi_time) AS rn FROM base
        ) t
        WHERE MOD(rn, :stride) = 0
        ORDER BY vsi_time
        """,
        params,
    )
    return df, total


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
        FROM {_view()}
        GROUP BY msg_type ORDER BY msg_type
        """
    )


def count_messages(msg_types: list[int] | None, mmsis: list[int] | None,
                   start, end) -> int:
    """탐색기 필터 조건에 맞는 전체 건수(페이지네이션용)."""
    where, params = _explorer_where(msg_types, mmsis, start, end)
    df = run_query(f"SELECT COUNT(*) AS n FROM {_view('v')} WHERE {where}", params)
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
        FROM {_view('v')} {join}
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


# ── 탭 4: 신호 유효성 (위치 기반) ─────────────────────────────
# 수신국(RX_LAT/RX_LON)이 확정된 구간(UNIV_START 이후)의 Type 1/3 동적 위치보고만 대상으로,
# Haversine 으로 수신국까지 거리(dist_m)를 계산해 반환한다. core.signal_model 이 이 결과를
# 받아 거리구간별 baseline/이상치를 계산한다.
def load_dynamic_positions(mmsis: list[int] | None = None) -> pd.DataFrame:
    """Type 1/3 동적 위치보고 + 수신국 기준 거리(dist_m). UNIV_START 이후만 대상.
    vsi_time 은 recv_time 대신 시간순 궤적을 그릴 때 쓰는 정밀 시각(points() 와 동일 방식).
    columns=[source_id, vsi_time, recv_time, mmsi, msg_type, lon, lat, vsi_rssi, vsi_snr, dist_m]
    """
    haversine = f"""
        2 * 6371000 * asin(sqrt(
            power(sin(radians(lat - {RX_LAT}) / 2), 2) +
            cos(radians({RX_LAT})) * cos(radians(lat)) *
            power(sin(radians(lon - {RX_LON}) / 2), 2)
        ))
    """
    where = ["recv_time >= :univ_start",
             "lon BETWEEN -180 AND 180", "lat BETWEEN -90 AND 90"]
    params = {"univ_start": UNIV_START}
    if mmsis:
        where.append("mmsi = ANY(:mmsis)")
        params["mmsis"] = list(mmsis)
    where_sql = " AND ".join(where)

    parts = [
        f"""SELECT source_id, {_VSI_TIME_EXPR} AS vsi_time, recv_time, mmsi,
                   {mt} AS msg_type, lon, lat, vsi_rssi, vsi_snr, {haversine} AS dist_m
            FROM {tbl} WHERE {where_sql}"""
        for tbl, mt in (("ais_msg_1", 1), ("ais_msg_3", 3))
    ]
    return run_query(" UNION ALL ".join(parts), params)
