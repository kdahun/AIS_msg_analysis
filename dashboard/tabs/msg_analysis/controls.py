"""메시지 분석 페이지 공용 컨트롤 — 판정 임계값 슬라이더.

여러 페이지(보고주기/슬롯맵/침범/유실)가 같은 session_state 키를 공유하므로
어느 페이지에서 조절해도 값이 유지·일치된다.
"""
import streamlit as st

from . import data


def thresholds(expanded: bool = False):
    """판정 임계값 expander. (grid_tol, fast_factor, decode_margin) 반환."""
    with st.expander("판정 임계값 조절", expanded=expanded):
        c1, c2, c3 = st.columns(3)
        grid_tol = c1.slider("보고주기 격자 허용오차", 0.0, 0.5, 0.2, 0.02, key="rc_grid",
                             help="실제간격/기대간격 비율이 정수배(격자)에서 이 값 이내면 "
                                  "'격자 위'로 봄. 0=엄격(정확한 정수배만 정상 → 지터까지 위반). "
                                  "이동 선박만 적용(SOTDMA 선택구간 ±0.2·NI 근거), "
                                  "정박(주기>60초)은 ±3초 절대 허용오차로 고정")
        fast = c2.slider("과도 보고 배율 (기대×N 미만 시 위반)", 0.1, 0.9, 0.5, 0.05,
                         key="rc_fast", help="예: 기대 10초, 배율 0.5 → 5초 미만 시 '과도한 보고'")
        margin = c3.slider("수신한계 여유 (dB)", 3.0, 20.0, 10.0, 1.0, key="rc_margin",
                           help="선박 RSSI가 잡음층+이 값 미만이면 '수신한계 근접'으로 판정. "
                                "미수신·유실의 '환경성'과 '원인 미상'을 나누는 기준. "
                                "기본 10dB 근거: IEC 61993-2 동일채널 보호비 10dB(@20%PER) + "
                                "GMSK 복조 임계 ~8.6dB(≈10% 패킷실패)")
    return grid_tol, fast, margin


def classified_df():
    """현재 슬라이더 값으로 분류된 DataFrame + 사이드바 장소 필터 적용.

    판정 자체는 전체 데이터로 미리 끝나 있고, 여기서는 보여줄 범위만 좁힌다.
    (구간 경계를 넘는 계산을 막는 것은 프리컴퓨트 단계의 segment_id 가 담당한다)
    """
    g = st.session_state.get("rc_grid", 0.2)
    f = st.session_state.get("rc_fast", 0.5)
    m = st.session_state.get("rc_margin", 10.0)
    df = data.get_classified(g, f, m)
    return filter_sites(df), m


def filter_sites(df):
    """사이드바에서 고른 수집 장소로 행을 좁힌다. 선택이 없으면 그대로 돌려준다."""
    sites = st.session_state.get("global_sites")
    if not sites or "site_id" not in getattr(df, "columns", []):
        return df
    return df[df["site_id"].isin([int(s) for s in sites])]
