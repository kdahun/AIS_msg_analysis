"""페이지: 데이터 품질 — 기본값(무정보) 송신 장비와 희귀 MMSI.

동일슬롯 충돌 조사에서 발견된 두 부류를 상시 감시한다:
  · 기본값 장비: 위치·속력·침로가 전부 sentinel(91/181, 102.3, 511/360)인
    메시지만 보내는 실제 송신기(GPS 미연결/고장 추정). 보고주기 기대값을
    속력으로 정할 수 없어 판정이 추정치가 됨 → 별도 표시.
  · 희귀 MMSI: 전체에서 10건 미만 수신된 MMSI — 비트 오류로 생긴 손상
    디코드(유령)이거나 잠깐 지나간 선박.
"""
import numpy as np
import pandas as pd
import streamlit as st

from . import data
from .logic import SPEED_NA, HEADING_NA, COURSE_NA

TITLE = "데이터 품질"

RARE_MAX = 10   # 이 미만 수신이면 '희귀 MMSI'


def render():
    st.subheader("데이터 품질 — 기본값(무정보) 장비 · 희귀 MMSI")
    df = data.get_bundle()["enriched"]

    sentinel = ((~df["lat"].between(-90, 90)) & (~df["lon"].between(-180, 180))
                & (df["speed"] == SPEED_NA) & (df["heading"] == HEADING_NA)
                & (df["course"] == COURSE_NA))
    tot = df["mmsi"].value_counts()
    rare_mask = df["mmsi"].map(tot) < RARE_MAX

    c1, c2, c3 = st.columns(3)
    c1.metric("기본값(무정보) 메시지", f"{int(sentinel.sum()):,} "
              f"({100*sentinel.mean():.2f}%)",
              help="위치(91/181)·속력(102.3)·HDG(511)·COG(360) 전부 기본값")
    c2.metric("기본값 송신 MMSI", f"{df.loc[sentinel, 'mmsi'].nunique()}")
    c3.metric(f"희귀 MMSI (<{RARE_MAX}건)",
              f"{df.loc[rare_mask, 'mmsi'].nunique()}척 · "
              f"{int(rare_mask.sum()):,}건")

    st.caption(
        "이 메시지들은 **수신 자체는 정상**이므로 분석에서 제외하지 않습니다. 다만 "
        "기본값 장비는 SOG 가 없어 기대 보고주기를 확정할 수 없고(현재: 정박이면 180초, "
        "아니면 10초로 추정 판정), 변침 판정도 불가하므로 보고주기 위반 해석 시 주의가 "
        "필요합니다. 슬롯 검증(슬롯번호·timeout)은 통신상태 필드만 쓰므로 그대로 유효합니다."
    )

    # ── 기본값 장비 목록 ─────────────────────────────────────
    st.markdown("#### 기본값(무정보) 송신 장비")
    sub = df[sentinel]
    if sub.empty:
        st.info("기본값 메시지가 없습니다.")
    else:
        g = sub.groupby("mmsi")
        tbl = pd.DataFrame({
            "기본값 메시지": g.size(),
            "평균 RSSI": g["vsi_rssi"].mean().round(0),
            "첫 수신": g["vsi_time"].min(),
            "마지막 수신": g["vsi_time"].max(),
        })
        tbl["전체 메시지"] = tot[tbl.index]
        tbl["기본값 비율(%)"] = (tbl["기본값 메시지"] / tbl["전체 메시지"] * 100).round(1)
        tbl = (tbl.reset_index().sort_values("기본값 메시지", ascending=False)
               [["mmsi", "기본값 메시지", "전체 메시지", "기본값 비율(%)",
                 "평균 RSSI", "첫 수신", "마지막 수신"]])
        st.caption("비율 100% = 항상 무정보(GPS 미연결·미입력 장비 추정). "
                   "간헐적이면 일시적 GPS 신호 상실일 수 있습니다")
        st.dataframe(tbl, use_container_width=True, hide_index=True, height=300)

    # ── 희귀 MMSI 목록 ───────────────────────────────────────
    st.markdown(f"#### 희귀 MMSI (전체 {RARE_MAX}건 미만 수신)")
    st.caption("수 건만 수신된 MMSI 는 ① 비트 오류로 MMSI 가 깨진 손상 디코드(유령), "
               "② 수신권을 잠깐 지나간 선박 중 하나입니다. MMSI 형식(9자리·MID)과 "
               "무정보 여부를 함께 보면 구분에 도움이 됩니다")
    rr = df[rare_mask]
    if rr.empty:
        st.info("희귀 MMSI 가 없습니다.")
        return
    g = rr.groupby("mmsi")
    rt = pd.DataFrame({
        "수신": g.size(),
        "무정보(sentinel)": g.apply(
            lambda s: int(((~s["lat"].between(-90, 90))
                           & (s["speed"] == SPEED_NA)).sum()),
            include_groups=False),
        "평균 RSSI": g["vsi_rssi"].mean().round(0),
        "첫 수신": g["vsi_time"].min(),
    }).reset_index()
    rt["MMSI 9자리"] = rt["mmsi"].astype(str).str.len().eq(9).map({True: "", False: "⚠ 형식 이상"})
    rt = rt.sort_values("수신")
    st.dataframe(rt, use_container_width=True, hide_index=True, height=300)
