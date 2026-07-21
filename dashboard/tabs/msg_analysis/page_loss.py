"""페이지: 유실 분석 — 보냈지만 수신하지 못한 보고의 통계와 원인.

유실은 수신 메시지가 아니므로 전체 메시지 수·위반 집계에 포함하지 않는다.
두 관점을 함께 보여준다:
  · 시간 기반(보고 간격 정수배): 유실 '개수'를 세는 데 가장 견고
  · 슬롯 특정(timeout 카운트다운 브라킷): 유실의 '슬롯 위치'까지 아는 부분집합
    → 프레임 슬롯맵에 빈 사각형으로 표시됨
"""
import pandas as pd
import streamlit as st

from . import charts, controls, data, logic

TITLE = "유실 분석"


def render():
    st.subheader("유실 분석 (수신하지 못한 보고)")
    st.caption(
        "**유실** = 선박은 규정 주기로 송신했지만 우리 수신국이 받지 못한 보고. "
        "수신 데이터가 아니므로 **전체 메시지 수와 위반 집계에서 제외**하고 여기서 따로 셉니다. "
        "간격이 기대의 정수배(k≥2)로 벌어졌고 그 선박의 달성 최소간격이 규정을 만족할 때 "
        "'유실'로 판정합니다(과소 보고와 구분). 원인은 유실 순간의 추정 신호여유"
        "(양옆 수신 RSSI 보간 − 그 프레임 잡음층)로 나눕니다: 수신한계 여유 미만이면 "
        "**환경성**(멀거나 잡음이 높아 묻힘), 이상이면 **원인 미상**(충돌 등 — 수신 데이터로 확정 불가)."
    )

    controls.thresholds()
    df, margin = controls.classified_df()
    b = data.get_bundle()
    noise_df, losses = b["noise"], b["losses"]
    # 잡음층은 (장소·채널·프레임) 단위라 프레임당 여러 행이다.
    # 여기서는 프레임 하나에 값 하나가 필요하므로 표시용으로 줄여 쓴다.
    noise_map = data.noise_frame_series(noise_df)

    # ── 시간 기반 유실 (슬라이더 반영) ────────────────────────
    # 유실 신호 세기 추정: 유실 구간의 양옆(직전 행=이 메시지, 직후 행=같은 선박
    # 다음 수신)의 RSSI 평균. 선박이 한 주기(10~60초) 사이 거의 안 움직이므로
    # 유실된 보고의 실제 세기에 대한 타당한 근사가 된다.
    rssi_next = df.groupby("mmsi")["vsi_rssi"].shift(-1)
    est_rssi_all = (df["vsi_rssi"] + rssi_next) / 2

    lost_mask = df["ri_reason"].isin(list(logic.RI_HOLD_CODES))
    lost_rows = df[lost_mask].assign(est_rssi=est_rssi_all[lost_mask])
    lost_rows = lost_rows.assign(
        est_margin=lost_rows["est_rssi"] - lost_rows["frame"].map(noise_map))
    n_time = int(lost_rows["ri_missed_count"].sum())
    n_env = int(lost_rows.loc[lost_rows["ri_reason"] == "RI_LOST_NOISE",
                              "ri_missed_count"].sum())
    # ── 슬롯 특정 유실 (프리컴퓨트) ───────────────────────────
    l_margin = losses["est_rssi"] - losses["frame"].map(noise_map)
    n_slot = len(losses)
    n_slot_env = int((l_margin < margin).sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("유실 보고 (시간 기반)", f"{n_time:,}",
              help="보고 간격의 정수배 판정으로 센 총 유실 수 — 개수 집계의 기준")
    c2.metric("그중 환경성 추정", f"{n_env:,}",
              help=f"유실 직전 신호여유 < {margin:.0f}dB (잡음에 묻힘)")
    c3.metric("슬롯 특정 유실", f"{n_slot:,} (환경성 {n_slot_env:,})",
              help="timeout 카운트다운이 정확히 이어져 '어느 슬롯이 비었는지'까지 "
                   "아는 부분집합 — 프레임 슬롯맵에 빈 사각형으로 표시")
    c4.metric("슬롯 미상 (근사)", f"{max(n_time - n_slot, 0):,}",
              help="timeout=0(슬롯 교체) 메시지 자체가 유실되면 다음 슬롯을 알 수 "
                   "없음 → 개수만 집계 (시간 기반 − 슬롯 특정, 근사치)")

    st.divider()

    # ── 시간대별 추이 + 잡음층 + 유실 추정 RSSI ───────────────
    bucket = st.select_slider("추이 구간(분)", options=[5, 10, 20, 30, 60], value=10,
                              key="loss_bucket")
    li = lost_rows.set_index("frame")
    per = li["ri_missed_count"].resample(f"{bucket}min").sum()
    est_q = (li["est_rssi"].resample(f"{bucket}min")
             .agg(q25=lambda s: s.quantile(.25), q50="median",
                  q75=lambda s: s.quantile(.75)))
    st.plotly_chart(charts.loss_timeline(per, data.noise_frame_df(noise_df), bucket,
                                         est_rssi_q=est_q),
                    use_container_width=True, key="loss_tl")
    st.caption(
        "**유실 신호 추정 RSSI(파란 선)** = 유실 구간 양옆 수신 RSSI 의 보간 — 유실된 보고가 "
        "어느 세기로 왔을지에 대한 근사입니다. 파란 선이 잡음층(빨간 점선)에 붙는 구간은 "
        "신호가 잡음에 묻힌 **환경성** 유실, 잡음층보다 한참 위인데 유실이 많으면 "
        "혼잡/충돌 등 **다른 요인**을 의심할 수 있습니다.")

    # ── 유실 순간 추정 신호여유 분포 ──────────────────────────
    st.plotly_chart(charts.loss_margin_hist(lost_rows["est_margin"], margin),
                    use_container_width=True, key="loss_hist")
    st.caption("유실 '구간'(연속 유실 묶음) 단위 분포입니다. 수신한계 여유 슬라이더를 "
               "움직이면 한계선이 함께 이동합니다.")

    # ── MMSI별 유실 TOP ──────────────────────────────────────
    st.markdown("#### 선박별 유실 현황")
    g = lost_rows.groupby("mmsi")
    tbl = pd.DataFrame({
        "유실 보고 수": g["ri_missed_count"].sum(),
        "유실 구간 수": g.size(),
        "유실 추정 RSSI": g["est_rssi"].median().round(0),
        "추정 여유(dB)": g["est_margin"].median().round(0),
        "평균 거리(km)": g["dist_km"].mean().round(1),
    })
    total_by = df.groupby("mmsi").size()
    tbl["수신 메시지"] = total_by
    tbl["유실률(%)"] = (tbl["유실 보고 수"]
                     / (tbl["유실 보고 수"] + tbl["수신 메시지"]) * 100).round(1)
    tbl = (tbl.reset_index().sort_values("유실 보고 수", ascending=False)
           [["mmsi", "유실 보고 수", "유실 구간 수", "유실률(%)", "수신 메시지",
             "유실 추정 RSSI", "추정 여유(dB)", "평균 거리(km)"]])
    st.caption("유실이 많은 선박 순 — **유실 추정 RSSI**(유실 구간 양옆 보간 중앙값)와 "
               f"**추정 여유**(추정 RSSI−잡음층)가 낮을수록({margin:.0f}dB 미만) 환경성, "
               "여유가 큰데 유실이 많으면 다른 원인(혼잡/충돌 등)입니다")
    st.dataframe(tbl, use_container_width=True, hide_index=True, height=330)
