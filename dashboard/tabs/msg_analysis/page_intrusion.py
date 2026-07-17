"""페이지: 슬롯 침범 — 살아있는 예약 슬롯을 다른 선박이 차지한 이벤트.

이벤트 목록에서 하나를 고르면:
  · 그 프레임의 침범 슬롯맵 (침범 슬롯 빨강, 일반 수신 흐림)
  · 그 (채널·슬롯)의 RSSI 점유 이력 타임라인 — 피해자(약한 신호)가 지키던
    슬롯에 침범자(강한 신호)가 등장하는 순간을 눈으로 확인
"""
import pandas as pd
import streamlit as st

from . import charts, data

TITLE = "슬롯 침범"

_WIN_MIN = 6   # 타임라인 창 (침범 프레임 ± 분)


def render():
    st.subheader("슬롯 침범 (예약 슬롯 점유 이벤트)")
    st.caption(
        "**정의**: 직전 프레임에 선박 F 가 slot_timeout ≥ 1 로 송신한 슬롯(= 이번 프레임에도 "
        "F 가 써야 하는 살아있는 예약)에, 이번 프레임 **다른 선박 G 가 수신되고 F 는 없는** 경우. "
        "F 가 그 순간 실제로 송신했는지(물리적 비트충돌)는 수신 데이터로 확정할 수 없으므로 "
        "위반 집계와는 분리해 이벤트로만 보여줍니다. **'복귀 확증'** = F 가 바로 다음 프레임에 "
        "같은 슬롯으로 돌아옴(예약이 계속 살아있었다는 확증 — 가장 신뢰도 높음)."
    )

    intr = data.get_bundle()["intrusions"]
    if intr.empty:
        st.info("침범 이벤트가 없습니다.")
        return

    # ── 요약 ─────────────────────────────────────────────────
    stronger = (intr["intruder_rssi"] > intr["victim_rssi"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("침범 이벤트", f"{len(intr):,}")
    c2.metric("복귀 확증(브라킷)", f"{int(intr['f_returns'].sum()):,}")
    c3.metric("침범자가 더 강한 신호", f"{stronger.mean()*100:.0f}%")
    c4.metric("가해/피해 선박 수",
              f"{intr['intruder'].nunique()} / {intr['victim'].nunique()}")

    r1, r2 = st.columns(2)
    with r1:
        st.caption("상습 침범자 TOP 10 (가해 횟수)")
        top_g = (intr.groupby("intruder")
                     .agg(횟수=("intruder", "size"),
                          평균RSSI=("intruder_rssi", "mean"),
                          피해선박수=("victim", "nunique"))
                     .sort_values("횟수", ascending=False).head(10)
                     .reset_index().rename(columns={"intruder": "가해 MMSI"})
                     .round({"평균RSSI": 0}))
        st.dataframe(top_g, use_container_width=True, hide_index=True)
    with r2:
        st.caption("최다 피해자 TOP 10 (빼앗긴 횟수)")
        top_f = (intr.groupby("victim")
                     .agg(횟수=("victim", "size"),
                          평균RSSI=("victim_rssi", "mean"),
                          가해선박수=("intruder", "nunique"))
                     .sort_values("횟수", ascending=False).head(10)
                     .reset_index().rename(columns={"victim": "피해 MMSI"})
                     .round({"평균RSSI": 0}))
        st.dataframe(top_f, use_container_width=True, hide_index=True)

    st.divider()

    # ── 필터 + 이벤트 목록 ────────────────────────────────────
    f1, f2, f3 = st.columns([1, 1, 2])
    only_bracket = f1.toggle("복귀 확증만", value=True, key="in_bracket",
                             help="피해자가 다음 프레임에 같은 슬롯으로 복귀한, "
                                  "예약 지속이 확증된 이벤트만")
    ch_pick = f2.radio("채널", ["전체", "A", "B"], horizontal=True, key="in_ch")
    mmsi_pool = sorted(set(intr["intruder"]) | set(intr["victim"]))
    mmsi_pick = f3.selectbox("MMSI 필터 (가해 또는 피해)", [None] + mmsi_pool,
                             format_func=lambda m: "(전체)" if m is None else f"MMSI {m}",
                             key="in_mmsi")

    view = intr
    if only_bracket:
        view = view[view["f_returns"]]
    if ch_pick != "전체":
        view = view[view["channel"] == ch_pick]
    if mmsi_pick is not None:
        view = view[(view["intruder"] == mmsi_pick) | (view["victim"] == mmsi_pick)]
    if view.empty:
        st.info("조건에 맞는 이벤트가 없습니다.")
        return

    show = view.copy()
    show["ΔRSSI(dB)"] = (show["intruder_rssi"] - show["victim_rssi"]).round(0)
    show = show.rename(columns={
        "frame": "프레임", "channel": "채널", "slot": "슬롯",
        "victim": "피해 MMSI", "victim_rssi": "피해 RSSI",
        "intruder": "가해 MMSI", "intruder_rssi": "가해 RSSI",
        "f_returns": "복귀 확증"})
    cols = ["프레임", "채널", "슬롯", "피해 MMSI", "피해 RSSI",
            "가해 MMSI", "가해 RSSI", "ΔRSSI(dB)", "복귀 확증"]
    st.caption(f"침범 이벤트 {len(show):,}건 — 행을 클릭하면 아래에 상세가 표시됩니다 "
               "(ΔRSSI>0 이면 침범자가 더 강함)")
    ev = st.dataframe(show[cols], use_container_width=True, hide_index=True,
                      height=280, on_select="rerun", selection_mode="single-row",
                      key="in_events")

    sel_rows = (ev.get("selection") or {}).get("rows", []) if ev else []
    if sel_rows:
        e = view.iloc[sel_rows[0]]
    else:
        e = view.iloc[0]          # 기본: 첫 이벤트 (행 클릭 시 교체)
        st.caption("→ 아래는 **첫 이벤트** 상세입니다. 표에서 행을 클릭하면 바뀝니다.")

    # ── 선택 이벤트 상세 ──────────────────────────────────────
    st.divider()
    st.markdown(f"#### {pd.Timestamp(e['frame']):%Y-%m-%d %H:%M} · 채널 {e['channel']} · "
                f"슬롯 {int(e['slot'])} — 피해 {e['victim']} ← 가해 {e['intruder']}")

    d1, d2, d3 = st.columns(3)
    d1.metric(f"피해자 {e['victim']} RSSI", f"{e['victim_rssi']:.0f} dBm",
              help="직전 프레임(예약 시점)의 수신 세기")
    d2.metric(f"침범자 {e['intruder']} RSSI", f"{e['intruder_rssi']:.0f} dBm",
              delta=f"{e['intruder_rssi']-e['victim_rssi']:+.0f} dB vs 피해자")
    back = (f"{e['victim_rssi_after']:.0f} dBm" if pd.notna(e["victim_rssi_after"])
            else "미복귀")
    d3.metric("피해자 복귀 RSSI (다음 프레임)", back,
              help="복귀했다면 예약이 계속 살아있었다는 확증(브라킷)")

    # RSSI 타임라인: 그 (채널,슬롯)의 ±_WIN_MIN 분 점유 이력
    enriched = data.get_bundle()["enriched"]
    lo = pd.Timestamp(e["frame"]) - pd.Timedelta(minutes=_WIN_MIN)
    hi = pd.Timestamp(e["frame"]) + pd.Timedelta(minutes=_WIN_MIN + 1)
    hist = enriched[(enriched["channel"] == e["channel"])
                    & (enriched["vsi_slot"] == e["slot"])
                    & (enriched["vsi_time"].between(lo, hi))]
    st.plotly_chart(
        charts.intrusion_rssi_timeline(hist, e["victim"], e["intruder"],
                                       pd.Timestamp(e["frame"]),
                                       int(e["slot"]), e["channel"]),
        use_container_width=True, key="in_timeline")

    # 그 프레임의 침범 슬롯맵
    fdf = data.frame_slice(enriched, e["frame"])
    fev = intr[intr["frame"] == e["frame"]]
    st.plotly_chart(
        charts.intrusion_slot_map(fdf, fev, sel_slot=int(e["slot"]),
                                  sel_channel=e["channel"]),
        use_container_width=True, key="in_map")
