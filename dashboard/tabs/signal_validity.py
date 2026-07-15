"""탭: 위치 기반 신호 유효성 — 선박 위치(동적 메시지)로 수신국까지 거리를 구하고,
그 거리에서 이 RSSI가 타당한지 검증한다 (ais_rssi_snr_model_v1.md 5~6절 1차 구현)."""
import streamlit as st

from core import queries
from core.constants import RX_LAT, RX_LON, UNIV_START
from core.signal_model import build_baseline, fspl_db, TX_POWER_DBM
from components import charts

TITLE = "신호 유효성"

SAMPLE_SIZE = 20_000    # 산점도에 표시할 표본 상한
TRAJ_MMSI_LIMIT = 10    # 궤적 그래프에서 한번에 색으로 구분해 보여줄 MMSI 상한


@st.cache_data(ttl=600, show_spinner=False)
def _load_and_score(mmsis_key: tuple[int, ...] | None):
    df_raw = queries.load_dynamic_positions(list(mmsis_key) if mmsis_key else None)
    if df_raw.empty:
        return df_raw, None, None
    return build_baseline(df_raw)


def render():
    st.subheader("위치 기반 신호 유효성 검증")
    st.caption(
        f"동적 위치보고(Type 1/3)의 선박 위치로 수신국까지 거리를 계산하고, 그 거리에서 "
        f"관측된 RSSI가 타당한 범위인지 확인합니다. 수신국: 한국해양대학교 아치캠퍼스 "
        f"({RX_LAT}, {RX_LON}) — 이 좌표가 확정된 **{UNIV_START} 이후** 구간만 대상입니다 "
        f"(그 이전은 수신국 좌표를 모르는 '모텔' 구간이라 제외)."
    )

    mmsis = st.multiselect(
        "MMSI 필터 (선택 안 하면 전체)",
        [m for m in queries.get_mmsi_options(limit=2000)["mmsi"].tolist()],
        key="sigval_mmsi",
    )
    mmsis_key = tuple(sorted(mmsis)) if mmsis else None

    df, baseline, reg = _load_and_score(mmsis_key)
    if df is None or df.empty:
        st.warning("조건에 맞는 Type 1/3 위치보고가 없습니다 (해양대 구간 기준).")
        return
    if baseline is None or baseline.empty:
        st.warning("거리구간별 표본이 부족해 baseline 을 만들 수 없습니다. MMSI 필터를 줄여보세요.")
        return

    valid = df.dropna(subset=["rssi_zscore"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("분석 건수", f"{len(df):,}")
    c2.metric("거리-RSSI 상관계수", f"{reg['corr']:.3f}")
    c3.metric("회귀 R²", f"{reg['r2']:.3f}")
    c4.metric("실측 감쇠율", f"{reg['slope']:.1f} dB/decade",
             help="자유공간 이론값은 -20 dB/decade 입니다")

    st.plotly_chart(
        charts.signal_validity_scatter(
            df.sample(min(SAMPLE_SIZE, len(df)), random_state=0)[["dist_km", "vsi_rssi"]],
            baseline, reg, fspl_db, TX_POWER_DBM,
        ),
        use_container_width=True,
    )
    st.caption(
        "FSPL(빨간 점선)은 안테나 이득·케이블 손실·다중경로가 반영되지 않은 이론값이라 "
        "절대 위치가 아니라 **기울기(감쇠 형태)** 비교용입니다."
    )

    st.divider()
    st.markdown("#### 이상치 후보 (거리 대비 비정상 RSSI)")
    threshold = st.slider("|zscore| 임계값", min_value=1.0, max_value=5.0, value=3.0, step=0.5,
                          key="sigval_threshold")
    outliers = valid[valid["rssi_zscore"].abs() >= threshold].copy()
    outliers = outliers.reindex(
        outliers["rssi_zscore"].abs().sort_values(ascending=False).index
    )

    pct = len(outliers) / len(valid) * 100 if len(valid) else 0
    stronger = int((outliers["rssi_zscore"] > 0).sum())
    weaker = int((outliers["rssi_zscore"] < 0).sum())
    st.caption(f"|zscore| ≥ {threshold}: {len(outliers):,}건 / {len(valid):,}건 ({pct:.2f}%) "
              f"— 예상보다 강함 {stronger:,}건, 예상보다 약함 {weaker:,}건")

    cols = ["source_id", "recv_time", "mmsi", "msg_type", "dist_km",
           "vsi_rssi", "rssi_median", "rssi_std", "rssi_zscore"]
    st.dataframe(outliers[cols].head(200).round(2), use_container_width=True, hide_index=True)

    with st.expander("거리구간별 baseline 테이블 보기"):
        st.dataframe(baseline.round(2), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### 선박 궤적 — 시간에 따른 거리 · RSSI 변화")
    if not mmsis:
        st.info(
            "위 'MMSI 필터'에서 선박을 1개 이상 선택하면, 그 배가 시간이 지나며 수신국에 "
            "가까워지거나 멀어질 때 거리와 RSSI가 같이 어떻게 움직이는지 볼 수 있습니다. "
            "(거리가 줄어들 때 RSSI도 세지면 정상, 거리와 상관없이 RSSI만 튀면 이상 신호 후보)"
        )
    elif df["mmsi"].nunique() > TRAJ_MMSI_LIMIT:
        st.warning(f"선택된 MMSI가 {df['mmsi'].nunique()}개라 궤적이 너무 복잡해집니다. "
                  f"{TRAJ_MMSI_LIMIT}개 이하로 선택해주세요.")
    else:
        traj = df.sort_values("vsi_time")
        color_by_mmsi = traj["mmsi"].nunique() > 1
        st.plotly_chart(charts.trajectory_time_series(traj, color_by_mmsi),
                        use_container_width=True)
        st.caption("위(거리)·아래(RSSI) 그래프는 같은 시간축을 공유합니다. 같은 시각(x좌표)에서 "
                  "두 그래프를 같이 보면서, 거리가 줄어들 때 RSSI도 세지는지(정상) 확인해보세요.")
