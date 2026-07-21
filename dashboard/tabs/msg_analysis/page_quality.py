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

from . import charts, data
from .logic import SPEED_NA, HEADING_NA, COURSE_NA

TITLE = "데이터 품질"

RARE_MAX = 10   # 이 미만 수신이면 '희귀 MMSI'


def _render_device_status(segments: pd.DataFrame, frame_slots: pd.DataFrame):
    """수집 이력 — 어디서 언제 받았고, 어디서 끊겼고, 어디가 반쪽만 켜져 있었나.

    장비 상태는 켜짐/꺼짐 두 가지가 아니라 셋이다.
      정상      메시지도 FSR 도 나옴
      반쪽 가동  메시지는 정상인데 상태 문장(FSR 등)만 끊김 — 재기동 직후로 보인다
      중단      둘 다 없음 — 장비가 꺼졌거나 장소 이동 중
    """
    st.markdown("#### 수집 이력 — 구간과 장비 상태")

    n_seg = len(segments)
    n_off = int((segments["gap_reason"] == "장비 중단").sum())
    n_move = int((segments["gap_reason"] == "장소 이동").sum())
    half = data.device_status_runs(frame_slots)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("연속 수신 구간", f"{n_seg}개")
    c2.metric("장비 중단", f"{n_off}회",
              help="전 선박 무수신이 5초 이상 이어진 지점 — 여기서 시계열을 끊습니다")
    c3.metric("장소 이동", f"{n_move}회")
    c4.metric("반쪽 가동 구간", f"{len(half)}개",
              help="메시지는 정상인데 FSR 등 상태 문장만 끊긴 구간")

    st.caption(
        "보고 간격과 슬롯 체인은 **구간 안에서만** 계산합니다. 장소 이동(수 시간)이나 "
        "장비 중단(수십~수백 초)을 하나의 보고 간격으로 이으면 선박이 보고를 빼먹은 "
        "것처럼 보이기 때문입니다. 날짜는 계산을 자르지 않습니다 — 자정에는 아무 일도 "
        "일어나지 않으므로, 거기서 끊으면 자정을 넘는 정상 간격까지 버리게 됩니다."
    )

    seg = segments.copy()
    seg["앞 공백"] = seg["gap_sec"].map(
        lambda s: "-" if pd.isna(s) else
        (f"{s/3600:.1f}시간" if s >= 3600 else f"{s/60:.1f}분" if s >= 60 else f"{s:.0f}초"))
    show = seg.rename(columns={
        "segment_id": "구간", "code": "장소", "start": "시작", "end": "끝",
        "duration_min": "길이(분)", "n_msg": "메시지 수", "gap_reason": "끊긴 이유",
    })[["구간", "장소", "시작", "끝", "길이(분)", "메시지 수", "앞 공백", "끊긴 이유"]]
    st.dataframe(show, use_container_width=True, hide_index=True)

    if half.empty:
        return
    st.markdown("##### 반쪽 가동 구간 (메시지는 정상, FSR 만 없음)")
    st.caption(
        "장비가 꺼진 게 아닙니다. 이 구간에서도 메시지는 분당 수백 건씩 정상 수신되고 "
        "RSSI·SNR·슬롯 분포가 정상 구간과 구분되지 않습니다. 재기동 직후 상태 출력 "
        "계통만 복구되지 않은 것으로 보입니다. **메시지 분석에는 그대로 쓰고**, "
        "FSR 기반 지표(잡음 실측·슬롯 대조)만 이 구간에서 비워 둡니다."
    )
    h = half.rename(columns={"channel": "채널", "start": "시작", "end": "끝",
                             "n_frames": "분", "n_msg": "메시지 수"})
    st.dataframe(h[["채널", "시작", "끝", "분", "메시지 수"]],
                 use_container_width=True, hide_index=True)


def _render_noise_check(noise: pd.DataFrame):
    """잡음층: 우리가 계산한 추정치와 수신기 실측(FSR)을 나란히 확인한다.

    추정치는 '수신에 성공한 메시지'만 가지고 재기 때문에 조용한 쪽으로 치우친다.
    잡음이 큰 순간의 메시지는 애초에 못 받아 표본에서 빠지기 때문이다.
    """
    st.markdown("#### 잡음층 — 추정 vs 수신기 실측(FSR)")
    both = noise[noise["noise_fsr"].notna()]
    if both.empty:
        st.info("FSR 실측이 있는 프레임이 없습니다. 잡음층은 추정치로만 계산됩니다.")
        return

    d = both["noise_est"] - both["noise_fsr"]
    n_fsr = int((noise["noise_src"] == "FSR").sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("실측을 쓴 프레임", f"{n_fsr:,} / {len(noise):,}",
              help="FSR 이 있으면 실측을 쓰고, 없는 프레임만 추정치로 메웁니다")
    c2.metric("추정이 더 조용하게 본 프레임", f"{int((d < 0).sum()):,}",
              help="추정 잡음층이 실측보다 낮게 나온 프레임 수")
    c3.metric("평균 차이", f"{d.mean():+.1f} dB",
              help="추정 − 실측. 음수면 추정이 더 조용하다고 본 것")
    c4.metric("5dB 넘게 낮게 본 프레임", f"{int((d < -5).sum()):,}",
              help="이 구간은 신호 여유를 5dB 이상 크게 본 셈이 됩니다")

    st.caption(
        "추정 잡음층이 실측보다 **낮게(조용하게)** 나오는 것이 정상적인 편향입니다. "
        "추정은 수신에 성공한 메시지의 RSSI−SNR 로 재는데, 잡음이 큰 순간의 메시지는 "
        "애초에 수신되지 않아 표본에서 빠지기 때문입니다. 이 편향을 그대로 두면 "
        "신호 여유를 그만큼 크게 보게 되어, 잡음에 묻혀 유실된 보고가 "
        "'원인 미상'으로 남습니다. 그래서 실측이 있으면 실측을 씁니다."
    )

    sub = both.copy()
    sub["차이(dB)"] = d.round(1)
    tbl = (sub.groupby(["site_id", "channel"])
              .agg(프레임=("frame", "size"),
                   추정평균=("noise_est", lambda s: round(s.mean(), 1)),
                   실측평균=("noise_fsr", lambda s: round(s.mean(), 1)),
                   평균차이=("차이(dB)", lambda s: round(s.mean(), 1)))
              .reset_index())
    st.dataframe(tbl, use_container_width=True, hide_index=True)

    fig = charts.noise_est_vs_fsr(both)
    st.plotly_chart(fig, use_container_width=True, key="quality_noise")


def render():
    st.subheader("데이터 품질 — 잡음층 검증 · 기본값(무정보) 장비 · 희귀 MMSI")
    b = data.get_bundle()
    df = b["enriched"]

    _render_device_status(b["segments"], b["frameslots"])
    st.divider()
    _render_noise_check(b["noise"])
    st.divider()

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
