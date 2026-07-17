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
    noise_map = noise_df.set_index("frame")["noise_dbm"]

    # ── 시간 기반 유실 (슬라이더 반영) ────────────────────────
    lost_rows = df[df["ri_reason"].isin(list(logic.RI_HOLD_CODES))]
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

    # ── 시간대별 추이 + 잡음층 ────────────────────────────────
    bucket = st.select_slider("추이 구간(분)", options=[5, 10, 20, 30, 60], value=10,
                              key="loss_bucket")
    per = (lost_rows.set_index("frame")["ri_missed_count"]
           .resample(f"{bucket}min").sum())
    st.plotly_chart(charts.loss_timeline(per, noise_df, bucket),
                    use_container_width=True, key="loss_tl")
    st.caption("잡음층(빨간 점선)이 올라가는 구간에서 유실이 같이 늘면 환경(잡음) 요인, "
               "잡음층이 낮은데도 유실이 많으면 혼잡/충돌 등 다른 요인을 의심할 수 있습니다.")

    # ── MMSI별 유실 TOP ──────────────────────────────────────
    st.markdown("#### 선박별 유실 현황")
    g = lost_rows.groupby("mmsi")
    tbl = pd.DataFrame({
        "유실 보고 수": g["ri_missed_count"].sum(),
        "유실 구간 수": g.size(),
        "평균 RSSI": g["vsi_rssi"].mean().round(0),
        "평균 거리(km)": g["dist_km"].mean().round(1),
    })
    total_by = df.groupby("mmsi").size()
    tbl["수신 메시지"] = total_by
    tbl["유실률(%)"] = (tbl["유실 보고 수"]
                     / (tbl["유실 보고 수"] + tbl["수신 메시지"]) * 100).round(1)
    tbl = (tbl.reset_index().sort_values("유실 보고 수", ascending=False)
           [["mmsi", "유실 보고 수", "유실 구간 수", "유실률(%)", "수신 메시지",
             "평균 RSSI", "평균 거리(km)"]])
    st.caption("유실이 많은 선박 순 — 평균 RSSI·거리가 낮고 멀수록 환경성일 확률이 높습니다")
    st.dataframe(tbl, use_container_width=True, hide_index=True, height=330)
