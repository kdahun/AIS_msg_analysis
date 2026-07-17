"""페이지: 보고주기 검증 — 전체 요약(원그래프)·MMSI별 위반 현황.

간격 위반(과도/과소/부적합)과 슬롯 위반의 전체 통계를 본다.
프레임 단위 탐색은 '프레임 슬롯맵' 페이지에서.
"""
import pandas as pd
import streamlit as st

from . import charts, controls, logic

TITLE = "보고주기 검증"


def render():
    st.subheader("보고주기 준수 검증 (Class A · Type 1/3)")
    st.caption(
        "① SOG·항해상태·**변침여부(HDG 30초 평균 대비 현재 HDG, 5도 초과 시 변침)**로 정한 "
        "기대 보고주기(ITU-R M.1371-6 Table 1)와 실제 간격 비교 — 과도(빠름)·부적합·"
        "**과소(느림)**를 판정하고, 긴 간격이 선박의 원래 주기(달성 최소간격)로 설명되면 "
        "위반이 아니라 '보고 유실(환경/보류)'로 분리. 정박(주기>60초)은 프레임 고정이라 "
        "±초 절대 허용오차로 엄격 적용, 이동은 SOTDMA 선택구간(±0.2·NI) 근거로 비율 허용오차. "
        "② Type 1 SOTDMA 슬롯 체인을 **슬롯번호·프레임 정수 비교**로 검증합니다"
        "(timeout 2/4/6 의 보고 슬롯번호=관측 슬롯, timeout 0 의 offset 예고 슬롯 점유, "
        "timeout>0 의 같은 슬롯 유지·1감소 — 수신시각 허용오차 없음). "
        "다음 프레임에 그 선박이 미수신되면 위반이 아니라 '검증 보류'로 두고 잡음층으로 "
        "환경성/미상을 구분합니다. (ITDMA num_slots 검증은 이번 버전 제외)"
    )

    controls.thresholds()
    df, _margin = controls.classified_df()

    # ── MMSI 필터 ────────────────────────────────────────────
    mmsi_opts = sorted(df["mmsi"].unique().tolist())
    picked = st.multiselect("MMSI 필터 (선택 안 하면 전체)", mmsi_opts, key="rc_mmsi")
    if picked:
        df = df[df["mmsi"].isin(picked)]
        if df.empty:
            st.warning("선택한 MMSI의 데이터가 없습니다.")
            return

    st.markdown("#### 전체 요약")
    cat = charts.category_series(df)
    counts = cat.value_counts().to_dict()
    total = len(df)
    viol = int((df["is_violation"]).sum())

    c1, c2 = st.columns([1, 1.4])
    with c1:
        st.plotly_chart(charts.compliance_pie(counts), use_container_width=True)
        st.metric("전체 메시지", f"{total:,}")
        st.metric("위반", f"{viol:,}  ({viol/total*100:.1f}%)" if total else "0")

    with c2:
        g = df.groupby("mmsi")
        tbl = pd.DataFrame({
            "전체": g.size(),
            "위반": g["is_violation"].sum(),
        })
        tbl["위반율(%)"] = (tbl["위반"] / tbl["전체"] * 100).round(1)
        for code, label in logic.REASON_LABELS_KO.items():
            col = "ri_reason" if code.startswith("RI_") else "slot_reason"
            tbl[label] = g[col].apply(lambda s, c=code: (s == c).sum())
        tbl = tbl.reset_index().sort_values("위반율(%)", ascending=False)
        st.caption("MMSI별 위반 현황 (헤더 클릭으로 정렬)")
        st.dataframe(tbl, use_container_width=True, hide_index=True, height=360)
