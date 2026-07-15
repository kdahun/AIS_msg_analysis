"""DB 접속 및 쿼리 실행 계층.

- get_engine(): SQLAlchemy 엔진 1개를 캐시(@st.cache_resource)해 재사용
- run_query(): SQL 실행 후 DataFrame 반환. 결과는 @st.cache_data 로 캐싱

탭 코드는 이 모듈을 직접 쓰기보다 core/queries.py 의 함수를 통해 접근한다.
"""
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text


@st.cache_resource
def get_engine():
    """DB 엔진(커넥션 풀). secrets.toml 의 [postgres] 섹션을 읽는다."""
    c = st.secrets["postgres"]
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
