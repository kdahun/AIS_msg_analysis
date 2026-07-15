"""탭: 시간별 RSSI/SNR — 개별 메시지 값의 시간에 따른 변화."""
import streamlit as st

from core import queries
from components import filters, charts

TITLE = "시간별"

RAW_POINT_LIMIT = 50_000       # 이 이상이면 시간순 균등 표본 추출
COLOR_MMSI_LIMIT = 12          # 이 이상 MMSI 가 섞이면 색 구분 대신 단색(가독성)


def render():
    st.subheader("시간별 RSSI / SNR 추이")
    st.caption("평균이 아니라 메시지 하나하나의 실제 RSSI/SNR 값을 시간축에 그대로 표시합니다.")

    mode = st.radio("표시 방식", ["개별 값 (원본)", "시간 평균(버킷)"],
                    horizontal=True, key="bytime_mode")

    msg_types = filters.msg_type_multiselect("bytime")
    mmsis = filters.mmsi_multiselect(
        "bytime", label="MMSI 필터 — 선택하면 아래 시간 범위가 해당 MMSI 기준으로 자동 조정됩니다")

    # mmsis 가 바뀌면 time_range() 내부에서 위젯 key 가 바뀌어 슬라이더가
    # 그 MMSI 의 실제 수신 시간 범위(시작~끝)로 새로 초기화된다.
    start, end = filters.time_range("bytime", mmsis=mmsis or None)

    if mode == "시간 평균(버킷)":
        _render_bucket_mode(start, end, mmsis, msg_types)
    else:
        _render_raw_mode(start, end, mmsis, msg_types)


def _render_raw_mode(start, end, mmsis, msg_types):
    df, total = queries.points(start, end, mmsis or None, msg_types or None,
                               limit=RAW_POINT_LIMIT)
    if total == 0:
        st.warning("조건에 맞는 데이터가 없습니다.")
        return

    if total > RAW_POINT_LIMIT:
        st.info(f"조건에 맞는 {total:,}건 중 시간순으로 고르게 표본 추출한 "
                f"{len(df):,}건을 표시합니다. 더 세밀하게 보려면 MMSI 를 좁혀보세요.")
    else:
        st.caption(f"총 {len(df):,}건 (표본 추출 없이 전체 표시)")

    color_by_mmsi = df["mmsi"].nunique() > 1 and df["mmsi"].nunique() <= COLOR_MMSI_LIMIT
    if df["mmsi"].nunique() > COLOR_MMSI_LIMIT:
        st.caption(f"MMSI {df['mmsi'].nunique()}개가 섞여 있어 색 구분 없이 표시합니다 "
                   f"(MMSI를 {COLOR_MMSI_LIMIT}개 이하로 선택하면 색으로 구분됩니다).")

    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(charts.scatter_over_time(df, "vsi_rssi", "RSSI", color_by_mmsi),
                        use_container_width=True)
    with c2:
        st.plotly_chart(charts.scatter_over_time(df, "vsi_snr", "SNR", color_by_mmsi),
                        use_container_width=True)


def _render_bucket_mode(start, end, mmsis, msg_types):
    bucket = st.radio("집계 단위", ["hour", "minute"],
                      format_func=lambda x: {"hour": "시간", "minute": "분"}[x],
                      key="bytime_bucket", horizontal=True)
    df = queries.timeseries(bucket, start, end, mmsis or None, msg_types or None)
    if df.empty:
        st.warning("조건에 맞는 데이터가 없습니다.")
        return

    st.plotly_chart(charts.timeseries_dual(df), use_container_width=True)

    total = int(df["n"].sum())
    st.caption(f"버킷 {len(df):,}개 · 총 {total:,}건 집계")
    with st.expander("집계 원본 테이블 보기"):
        st.dataframe(df, use_container_width=True, hide_index=True)
