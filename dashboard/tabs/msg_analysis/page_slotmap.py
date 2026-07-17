"""페이지: 프레임 슬롯맵 — 1분(2,250슬롯) 단위 채널 A/B 통합 슬롯맵 탐색.

프레임 이동은 data.frame_slice(프레임 인덱스)로 O(1) 조회.
선박 클릭/선택 → 강조 + 상세표, 아래에 시점별 RSSI/SNR/거리 상황 선그래프.
"""
import pandas as pd
import streamlit as st

from . import charts, controls, data, logic

TITLE = "프레임 슬롯맵"


def render():
    st.subheader("프레임 슬롯맵 (채널 A/B 통합 · 2,250슬롯/1분)")
    controls.thresholds()
    df, margin = controls.classified_df()
    noise_df = data.get_noise_floor()

    frames = data.get_bundle()["frames"]
    n = len(frames)
    if n == 0:
        st.info("표시할 프레임이 없습니다.")
        return

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

    sel_ts = pd.Timestamp(frames[st.session_state.rc_frame_idx])
    fdf = data.frame_slice(df, sel_ts)

    if fdf.empty:
        st.info(f"{sel_ts:%m-%d %H:%M} 프레임에는 수신된 메시지가 없습니다.")
        return

    # 이 프레임의 슬롯 특정 유실(빈 사각) + 환경성 여부(신호여유 < margin)
    losses_all = data.get_bundle()["losses"]
    flo = losses_all[losses_all["frame"] == sel_ts]
    if len(flo):
        noise_now = noise_df.set_index("frame")["noise_dbm"].get(sel_ts)
        flo = flo.assign(is_env=(flo["est_rssi"] - noise_now < margin)
                         if noise_now is not None else False)

    occ = len(fdf)
    viol = int(fdf["is_violation"].sum())
    n_a = int((fdf["channel"] == "A").sum())
    n_b = int((fdf["channel"] == "B").sum())
    st.caption(f"**{sel_ts:%Y-%m-%d %H:%M}** · 수신 {occ:,}건(A {n_a}·B {n_b}) · 위반 {viol}건 · "
              f"**유실 {len(flo)}건**(수신 총계 미포함, 빈 사각형: 주황=환경성·회색=미상) · "
              "파랑=채널A, 청록=채널B, 빨강=위반, 회색=검증 보류 · "
              "슬롯을 클릭하면 그 선박이 강조되고 아래 표가 필터됩니다")

    # 선박 강조/필터: selectbox(확실) + 슬롯 클릭(보조) — 둘 다 같은 선택값을 씀
    vessels = sorted(fdf["mmsi"].unique().tolist())
    if "rc_pending_sel" in st.session_state:
        st.session_state["rc_sel_box"] = st.session_state.pop("rc_pending_sel")
    if st.session_state.get("rc_sel_box") not in ([None] + vessels):
        st.session_state["rc_sel_box"] = None
    sel_mmsi = st.selectbox(
        "강조·필터할 선박 (슬롯을 클릭해도 선택됨)", [None] + vessels,
        format_func=lambda m: "(선택 안 함)" if m is None else f"MMSI {m}",
        key="rc_sel_box")

    fig = charts.combined_slot_map(fdf, highlight_mmsi=sel_mmsi,
                                   losses=flo if len(flo) else None)
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
    """현재 프레임에서 송신한 선박 전체의 RSSI/SNR/거리 시간추이 + 잡음층."""
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
