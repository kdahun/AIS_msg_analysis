"""탭: 보고주기 준수 (Class A, Type 1/3).

- 전체 선박의 정상/위반 원그래프 + 정렬가능한 MMSI별 표
- MMSI 필터 (전체 화면에 적용)
- 프레임(1분)별 슬롯맵(75×5 테이블 6개) + 이전/다음 버튼 + 슬라이더
- 슬롯/위반 상세에 RSSI·SNR·거리·변침여부 표시

검증 로직은 logic.py, 데이터 로딩(+캐시)은 data.py, 차트는 charts.py.
"""
import pandas as pd
import streamlit as st

from . import data, charts, logic

TITLE = "보고주기 준수"


def render():
    st.subheader("보고주기 준수 검증 (Class A · Type 1/3)")
    st.caption(
        "① SOG·항해상태·**변침여부(HDG 30초 평균 대비 현재 HDG, 5도 초과 시 변침)**로 정한 "
        "기대 보고주기(ITU-R M.1371-6 Table 1)와 실제 간격 비교, "
        "② Type 1 SOTDMA 슬롯 체인(같은 슬롯 반복 / 슬롯 교체 예고)이 규정대로 이어지는지 검증합니다. "
        "(ITDMA num_slots 검증은 이번 버전 제외)"
    )

    with st.expander("판정 임계값 조절", expanded=False):
        c1, c2, c3 = st.columns(3)
        slow = c1.slider("보고 지연 배율 (기대×N 초과 시 위반)", 1.2, 5.0, 2.0, 0.1,
                         key="rc_slow", help="예: 기대 10초, 배율 2.0 → 20초 초과 시 '보고 지연'")
        fast = c2.slider("과도 보고 배율 (기대×N 미만 시 위반)", 0.1, 0.9, 0.5, 0.05,
                         key="rc_fast", help="예: 기대 10초, 배율 0.5 → 5초 미만 시 '과도한 보고'")
        tol = c3.slider("슬롯 시간 허용오차 (초)", 0.1, 10.0, 5.0, 0.1, key="rc_tol",
                        help="다음 프레임(60초 뒤) 예고/반복 슬롯을 찾을 때 허용할 시간 오차")

    enriched = data.get_enriched()
    df = logic.classify(enriched, slow_factor=slow, fast_factor=fast, time_tol_sec=tol)

    # ── MMSI 필터 (화면 전체에 적용) ─────────────────────────
    mmsi_opts = sorted(df["mmsi"].unique().tolist())
    picked = st.multiselect("MMSI 필터 (선택 안 하면 전체)", mmsi_opts, key="rc_mmsi")
    if picked:
        df = df[df["mmsi"].isin(picked)]
        if df.empty:
            st.warning("선택한 MMSI의 데이터가 없습니다.")
            return

    _render_overview(df)
    st.divider()
    _render_slot_map(df)


def _render_overview(df):
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


def _render_slot_map(df):
    st.markdown("#### 프레임별 슬롯맵 — 채널 A / 채널 B (각 2,250 슬롯 / 1분)")

    frame_list = sorted(df["frame"].dropna().unique())
    if not frame_list:
        st.info("표시할 프레임이 없습니다.")
        return
    n = len(frame_list)

    if "rc_frame_idx" not in st.session_state:
        st.session_state.rc_frame_idx = 0
    st.session_state.rc_frame_idx = min(st.session_state.rc_frame_idx, n - 1)

    def _clamp(i):
        return max(0, min(n - 1, i))

    b1, b2, b3 = st.columns([1, 5, 1])
    with b1:
        if st.button("◀ 이전 프레임", key="rc_prev", use_container_width=True):
            st.session_state.rc_frame_idx = _clamp(st.session_state.rc_frame_idx - 1)
    with b3:
        if st.button("다음 프레임 ▶", key="rc_next", use_container_width=True):
            st.session_state.rc_frame_idx = _clamp(st.session_state.rc_frame_idx + 1)
    with b2:
        st.slider("프레임 인덱스 (드래그해서 이동)", 0, n - 1, key="rc_frame_idx",
                  format=f"%d / {n-1}")

    sel_ts = pd.Timestamp(frame_list[st.session_state.rc_frame_idx])
    fdf = df[df["frame"] == sel_ts]

    if fdf.empty:
        st.info(f"{sel_ts:%m-%d %H:%M} 프레임에는 수신된 메시지가 없습니다.")
        return

    occ = len(fdf)
    viol = int(fdf["is_violation"].sum())
    st.caption(f"**{sel_ts:%Y-%m-%d %H:%M}** · 수신 {occ:,}건 · 위반 {viol}건 · "
              "파랑=정상 수신, 주황=위반, 어두움=빈슬롯 (마우스 올리면 슬롯번호·상세정보)")

    # 채널 A/B 각각 2,250슬롯 그리드 (SOTDMA 프레임은 채널별로 독립)
    for ch in ("A", "B"):
        cdf = fdf[fdf["channel"] == ch]
        c_occ, c_viol = len(cdf), int(cdf["is_violation"].sum())
        st.markdown(f"##### 채널 {ch} — 점유 {c_occ:,} / 2,250 · 위반 {c_viol}건")
        st.plotly_chart(charts.channel_slot_map(cdf, ch),
                        use_container_width=True, key=f"rc_slotmap_{ch}")

    viol_rows = fdf[fdf["is_violation"]]
    if len(viol_rows):
        show = viol_rows.copy()
        show["사유"] = [logic.combined_reason_ko(r, s)
                       for r, s in zip(show["ri_reason"], show["slot_reason"])]
        show["변침"] = show["changing_course"].map({True: "예", False: "아니오"})
        cols_show = ["vsi_time", "mmsi", "msg_type", "channel", "vsi_slot", "사유",
                    "speed", "heading", "변침", "vsi_rssi", "vsi_snr", "dist_km"]
        show = show[cols_show].rename(columns={
            "vsi_time": "시각", "mmsi": "MMSI", "msg_type": "타입", "channel": "채널",
            "vsi_slot": "슬롯", "speed": "속력(kn)", "heading": "HDG",
            "vsi_rssi": "RSSI", "vsi_snr": "SNR", "dist_km": "거리(km)",
        })
        show = show.sort_values(["채널", "슬롯"]).round({"거리(km)": 2})
        st.caption(f"이 프레임의 위반 내역 ({len(show)}건) — RSSI/SNR/거리로 주변 환경 확인 가능 "
                  "(거리는 전 구간 한국해양대 좌표 기준, 모텔 구간은 근사값)")
        st.dataframe(show, use_container_width=True, hide_index=True)

    _render_context_lines(df, sel_ts)


CONTEXT_MMSI_LIMIT = 10   # 선그래프에 동시에 그릴 MMSI 상한


def _render_context_lines(df, sel_ts):
    """선택 MMSI(들)의 RSSI/SNR/거리 시간추이 선그래프 + 현재 프레임 시점 표시."""
    st.markdown("#### 시점별 상황 — RSSI · SNR · 거리 선그래프")
    n_mmsi = df["mmsi"].nunique()
    if n_mmsi > CONTEXT_MMSI_LIMIT:
        st.info(
            f"위 'MMSI 필터'에서 선박을 {CONTEXT_MMSI_LIMIT}개 이하로 선택하면, 그 선박의 "
            "RSSI/SNR/거리 시간추이 선그래프가 표시됩니다. 현재 보고 있는 프레임 시점이 "
            "주황 세로선으로 표시되어, 위반이 난 슬롯 시점에 신호·거리가 어떤 상황이었는지 "
            "함께 확인할 수 있습니다."
        )
        return

    traj = df.sort_values("vsi_time")
    st.caption(f"주황 세로선 = 현재 슬롯맵에서 보고 있는 프레임({sel_ts:%m-%d %H:%M}) 시점. "
              "거리는 전 구간 한국해양대 좌표 기준입니다 (모텔 구간(06-10 10:33 이전)은 "
              "실제 수신 위치가 달라 근사값).")
    st.plotly_chart(
        charts.context_lines(traj, sel_ts, color_by_mmsi=n_mmsi > 1),
        use_container_width=True, key="rc_context_lines")
