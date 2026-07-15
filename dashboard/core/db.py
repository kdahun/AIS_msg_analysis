"""DB 접속 및 쿼리 실행 계층.

- get_engine(): SQLAlchemy 엔진 1개를 캐시(@st.cache_resource)해 재사용
- run_query(): SQL 실행 후 DataFrame 반환. 결과는 @st.cache_data 로 캐싱

탭 코드는 이 모듈을 직접 쓰기보다 core/queries.py 의 함수를 통해 접근한다.
"""
import os

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text


def _conn_params() -> dict:
    """DB 접속정보를 secrets.toml → 환경변수 순으로 읽는다.

    1) .streamlit/secrets.toml 의 [postgres] 섹션 (권장)
    2) 없으면 환경변수 AIS_DB_HOST/PORT/USER/PASSWORD/NAME 폴백
    둘 다 없으면 설정 방법을 안내하는 에러를 던진다.
    """
    try:
        c = st.secrets["postgres"]
        return {k: c[k] for k in ("host", "port", "user", "password", "dbname")}
    except Exception:
        pass

    if os.getenv("AIS_DB_PASSWORD") is not None:
        return {
            "host": os.getenv("AIS_DB_HOST", "localhost"),
            "port": os.getenv("AIS_DB_PORT", "5432"),
            "user": os.getenv("AIS_DB_USER", "sim_user"),
            "password": os.getenv("AIS_DB_PASSWORD"),
            "dbname": os.getenv("AIS_DB_NAME", "ais_analysis_db"),
        }

    st.error(
        "DB 접속정보가 없습니다. 아래 중 하나로 설정하세요:\n\n"
        "① `dashboard/.streamlit/secrets.toml` 생성 "
        "(템플릿: `secrets.toml.example` 복사 후 값 입력)\n"
        "② 환경변수 `AIS_DB_HOST/PORT/USER/PASSWORD/NAME` 설정"
    )
    st.stop()


@st.cache_resource
def get_engine():
    """DB 엔진(커넥션 풀). 접속정보는 _conn_params() 로 로드."""
    c = _conn_params()
    url = (
        f"postgresql+psycopg2://{c['user']}:{c['password']}"
        f"@{c['host']}:{c['port']}/{c['dbname']}"
    )
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


@st.cache_data(ttl=600, show_spinner=False)
def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    """SQL 실행 → DataFrame. (sql, params) 조합 단위로 10분 캐싱."""
    engine = get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})
