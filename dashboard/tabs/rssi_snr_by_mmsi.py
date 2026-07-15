"""탭: MMSI별 RSSI/SNR 분석."""
import streamlit as st

from core import queries
from components import filters, charts

TITLE = "MMSI별"


def render():
    st.subheader("MMSI별 RSSI / SNR")
    st.caption("선박(MMSI)을 선택하면 RSSI/SNR 통계와 값 분포를 비교합니다.")

    mmsis = filters.mmsi_multiselect("bymmsi", max_default=3)
    if not mmsis:
        st.info("위에서 MMSI를 1개 이상 선택하세요.")
        return

    stats = queries.stats_by_mmsi(mmsis)
    st.markdown("#### 통계 요약")
    st.dataframe(stats, use_container_width=True, hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### RSSI 분포")
        st.plotly_chart(charts.distribution_bars(
            queries.dist_by_mmsi(mmsis, "vsi_rssi"), "RSSI"),
            use_container_width=True)
    with c2:
        st.markdown("#### SNR 분포")
        st.plotly_chart(charts.distribution_bars(
            queries.dist_by_mmsi(mmsis, "vsi_snr"), "SNR"),
            use_container_width=True)
