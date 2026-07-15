"""탭: 메시지별 — 타입별 RSSI/SNR 비교 + 전체 메시지 탐색기."""
import streamlit as st

from core import queries
from components import filters, charts

TITLE = "메시지별"

PAGE_SIZE = 100


def render():
    st.subheader("메시지별 RSSI / SNR")

    # ── 타입별 비교 (박스플롯) ─────────────────────────────
    st.markdown("#### 메시지 타입별 분포 비교")
    stats = queries.stats_by_msg_type()
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(charts.box_by_type(stats, "rssi"), use_container_width=True)
    with c2:
        st.plotly_chart(charts.box_by_type(stats, "snr"), use_container_width=True)
    with st.expander("타입별 통계 테이블 보기"):
        st.dataframe(stats, use_container_width=True, hide_index=True)

    st.divider()

    # ── 전체 메시지 탐색기 ─────────────────────────────────
    st.markdown("#### 전체 메시지 탐색기")
    st.caption("모든 메시지를 필터·페이지 단위로 조회합니다. (원문 AIS/VSI 포함)")

    msg_types = filters.msg_type_multiselect("bymsg")
    mmsis = filters.mmsi_multiselect("bymsg", label="MMSI 필터 (선택 안 하면 전체)")
    start, end = filters.time_range("bymsg")

    total = queries.count_messages(msg_types or None, mmsis or None, start, end)
    n_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    cc1, cc2 = st.columns([1, 3])
    page = cc1.number_input("페이지", min_value=1, max_value=n_pages, value=1, step=1,
                            key="bymsg_page")
    cc2.caption(f"조건에 맞는 총 {total:,}건 · {n_pages:,}페이지 (페이지당 {PAGE_SIZE}건)")

    rows = queries.list_messages(msg_types or None, mmsis or None, start, end,
                                 limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    st.dataframe(rows, use_container_width=True, hide_index=True)
