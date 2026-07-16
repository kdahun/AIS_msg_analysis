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
        "② Type 1 SOTDMA 슬롯 체인을 **슬롯번호·프레임 정수 비교**로 검증합니다"
        "(timeout 2/4/6 의 보고 슬롯번호=관측 슬롯, timeout 0 의 offset 예고 슬롯 점유, "
        "timeout>0 의 같은 슬롯 유지·1감소 — 수신시각 허용오차 없음). "
        "다음 프레임에 그 선박이 미수신되면 위반이 아니라 '검증 보류'로 두고 잡음층으로 "
        "환경성/미상을 구분합니다. (ITDMA num_slots 검증은 이번 버전 제외)"
    )

    with st.expander("판정 임계값 조절", expanded=False):
        c1, c2, c3 = st.columns(3)
        grid_tol = c1.slider("보고주기 격자 허용오차", 0.0, 0.5, 0.2, 0.02, key="rc_grid",
                             help="실제간격/기대간격 비율이 정수배(격자)에서 이 값 이내면 "
                                  "'격자 위'로 봄. 0=엄격(정확한 정수배만 정상 → 지터까지 위반). "
                                  "슬라이더를 0까지 내릴 수 있음. 올릴수록 '보고주기 부적합'이 "
                                  "'보고 누락/정상'으로 완화됨")
        fast = c2.slider("과도 보고 배율 (기대×N 미만 시 위반)", 0.1, 0.9, 0.5, 0.05,
                         key="rc_fast", help="예: 기대 10초, 배율 0.5 → 5초 미만 시 '과도한 보고'")
        margin = c3.slider("수신한계 여유 (dB)", 3.0, 20.0, 10.0, 1.0, key="rc_margin",
                           help="선박 RSSI가 잡음층+이 값 미만이면 '수신한계 근접'으로 판정. "
                                "슬롯 검증에서 다음 프레임에 그 선박이 미수신됐을 때 이 기준으로 "
                                "'환경성 유실 추정'과 '원인 미상'을 나눔. AIS 복조에 통상 ~10dB SNR 필요. "
                                "※ 슬롯 검증은 슬롯번호·프레임 정수 비교라 시간 허용오차가 없음")

    enriched = data.get_enriched()
    noise_df = data.get_noise_floor()
    df = logic.classify(enriched, fast_factor=fast, grid_tol=grid_tol,
                        noise_df=noise_df, decode_margin=margin)

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
    _render_slot_map(df, noise_df, margin)


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


def _render_slot_map(df, noise_df, margin):
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
    n_a = int((fdf["channel"] == "A").sum())
    n_b = int((fdf["channel"] == "B").sum())
    st.caption(f"**{sel_ts:%Y-%m-%d %H:%M}** · 수신 {occ:,}건(A {n_a}·B {n_b}) · 위반 {viol}건 · "
              "파랑=채널A, 청록=채널B, 빨강=위반, 회색=검증 보류(다음 프레임 미수신) · "
              "슬롯을 클릭하면 그 선박이 강조되고 아래 표가 필터됩니다")

    # 선박 강조/필터: selectbox(확실) + 슬롯 클릭(보조) — 둘 다 같은 선택값을 씀
    vessels = sorted(fdf["mmsi"].unique().tolist())
    # 슬롯 클릭으로 대기중인 선택을 selectbox 값으로 반영
    if "rc_pending_sel" in st.session_state:
        st.session_state["rc_sel_box"] = st.session_state.pop("rc_pending_sel")
    # 프레임이 바뀌어 이전 선택이 이 프레임에 없으면 초기화(selectbox 오류 방지)
    if st.session_state.get("rc_sel_box") not in ([None] + vessels):
        st.session_state["rc_sel_box"] = None
    sel_mmsi = st.selectbox(
        "강조·필터할 선박 (슬롯을 클릭해도 선택됨)", [None] + vessels,
        format_func=lambda m: "(선택 안 함)" if m is None else f"MMSI {m}",
        key="rc_sel_box")

    fig = charts.combined_slot_map(fdf, highlight_mmsi=sel_mmsi)
    event = st.plotly_chart(fig, use_container_width=True,
                            key=f"rc_map_{st.session_state.rc_frame_idx}",
                            on_select="rerun", selection_mode="points")
    try:
        pts = (event.get("selection") or {}).get("points", []) if event else []
    except AttributeError:
        pts = []
    if pts:
        cd = pts[0].get("customdata")
        clicked = cd[0] if isinstance(cd, (list, tuple)) else cd
        if clicked is not None and int(clicked) != (sel_mmsi if sel_mmsi is not None else -1):
            st.session_state["rc_pending_sel"] = int(clicked)
            st.rerun()

    # ── 표: 선박 선택 시 그 선박 전체 / 아니면 전체 위반 ──────
    noise_map = noise_df.set_index("frame")["noise_dbm"]
    if sel_mmsi is not None:
        rows = fdf[fdf["mmsi"] == sel_mmsi]
        table_title = f"선택 선박 MMSI {sel_mmsi} 의 이 프레임 메시지 ({len(rows)}건)"
    else:
        rows = fdf[fdf["is_violation"]]
        table_title = None

    if len(rows):
        show = rows.copy()
        show["사유"] = [logic.combined_reason_ko(r, s, m)
                       for r, s, m in zip(show["ri_reason"], show["slot_reason"],
                                          show["ri_missed_count"])]
        show["변침"] = show["changing_course"].map({True: "예", False: "아니오"})
        show["잡음여유(dB)"] = show["vsi_rssi"] - show["frame"].map(noise_map)
        show["환경요인?"] = show["잡음여유(dB)"].lt(margin).map(
            {True: "예(수신한계 근접)", False: ""})
        cols_show = ["vsi_time", "mmsi", "msg_type", "channel", "vsi_slot", "사유",
                    "환경요인?", "잡음여유(dB)", "speed", "heading", "변침",
                    "vsi_rssi", "vsi_snr", "dist_km"]
        show = show[cols_show].rename(columns={
            "vsi_time": "시각", "mmsi": "MMSI", "msg_type": "타입", "channel": "채널",
            "vsi_slot": "슬롯", "speed": "속력(kn)", "heading": "HDG",
            "vsi_rssi": "RSSI", "vsi_snr": "SNR", "dist_km": "거리(km)",
        })
        show = show.sort_values(["채널", "슬롯"]).round({"거리(km)": 2, "잡음여유(dB)": 0})
        if table_title:
            st.caption(table_title + " — 슬롯맵 클릭으로 선택됨. '선택 해제'로 전체 위반 보기")
        else:
            env_n = int((show["환경요인?"] != "").sum())
            st.caption(f"이 프레임의 위반 내역 ({len(show)}건, 그중 수신한계 근접 {env_n}건) — "
                      f"잡음여유(RSSI−잡음층)가 {margin:.0f}dB 미만이면 신호가 잡음에 묻혀 "
                      "수신 유실됐을 가능성이 높은 환경성 위반입니다 "
                      "(거리는 전 구간 한국해양대 좌표 기준, 모텔 구간은 근사값)")
        st.dataframe(show, use_container_width=True, hide_index=True)
    elif sel_mmsi is not None:
        st.info(f"MMSI {sel_mmsi} 는 이 프레임에 메시지가 없습니다.")

    _render_context_lines(df, sel_ts, fdf, noise_df, margin)


def _render_context_lines(df, sel_ts, fdf, noise_df, margin):
    """현재 프레임에서 송신한 선박 전체의 RSSI/SNR/거리 시간추이 + 잡음층.
    정상 선박은 반투명 파란 선(주변 환경), 이 프레임 위반 선박은 주황/색 강조.
    RSSI 패널의 빨간 점선(잡음층)과 음영 밴드(수신한계) 아래로 선박 RSSI 가
    떨어지면 '멀어져서 신호가 잡음에 묻힌' 환경성 수신 유실로 해석할 수 있다.
    """
    st.markdown("#### 시점별 상황 — RSSI · SNR · 거리 선그래프")

    win_min = st.slider("시간 창 (현재 프레임 ± 분)", 2, 30, 10, key="rc_ctx_win")
    lo = sel_ts - pd.Timedelta(minutes=win_min)
    hi = sel_ts + pd.Timedelta(minutes=win_min + 1)

    vessels_in_frame = fdf["mmsi"].unique()
    window_df = df[df["mmsi"].isin(vessels_in_frame) & df["vsi_time"].between(lo, hi)]
    violators = set(fdf.loc[fdf["is_violation"], "mmsi"])
    noise_win = noise_df[noise_df["frame"].between(lo, hi)]

    st.caption(
        f"현재 프레임({sel_ts:%m-%d %H:%M})에서 송신한 **{len(vessels_in_frame)}척 전체**의 "
        f"±{win_min}분 추이. 파란 반투명 선=정상 선박(주변 환경), 색/주황 선=이 프레임 "
        f"위반 선박({len(violators)}척). **빨간 점선=잡음층**, 그 위 음영=수신한계 영역"
        f"(잡음층+{margin:.0f}dB) — 선박 RSSI 선이 이 영역에 들어가면 수신이 물리적으로 "
        "불안정합니다. 거리 패널과 같이 보면 '멀어짐→신호약화→수신유실'을 확인할 수 있습니다. "
        "선에 마우스를 올리면 MMSI 표시. 거리는 전 구간 한국해양대 좌표 기준."
    )
    st.plotly_chart(
        charts.context_lines_frame(window_df, sel_ts, violators,
                                   noise_df=noise_win, decode_margin=margin),
        use_container_width=True, key="rc_context_lines")
