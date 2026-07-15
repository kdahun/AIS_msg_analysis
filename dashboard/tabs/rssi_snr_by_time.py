"""탭: 시간별 RSSI/SNR 추이."""
import streamlit as st

from core import queries
from components import filters, charts

TITLE = "시간별"


def render():
    st.subheader("시간별 RSSI / SNR 추이")
    st.caption("시간 버킷 단위로 평균 RSSI/SNR을 집계해 시계열로 봅니다. (집계는 DB에서 수행)")

    c1, c2 = st.columns([1, 3])
    with c1:
        bucket = st.radio("집계 단위", ["hour", "minute"],
                          format_func=lambda x: {"hour": "시간", "minute": "분"}[x],
                          key="bytime_bucket")
    with c2:
        msg_types = filters.msg_type_multiselect("bytime")

    mmsis = filters.mmsi_multiselect("bytime", label="MMSI 필터 (선택 안 하면 전체)")
    start, end = filters.time_range("bytime")

    df = queries.timeseries(bucket, start, end, mmsis or None, msg_types or None)
    if df.empty:
        st.warning("조건에 맞는 데이터가 없습니다.")
        return

    st.plotly_chart(charts.timeseries_dual(df), use_container_width=True)

    total = int(df["n"].sum())
    st.caption(f"버킷 {len(df):,}개 · 총 {total:,}건 집계")
    with st.expander("집계 원본 테이블 보기"):
        st.dataframe(df, use_container_width=True, hide_index=True)
