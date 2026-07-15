"""거리 기반 RSSI 유효성 분석 로직 (ais_signal_validity.ipynb 의 방법론을 그대로 재사용).

- build_baseline(): 로그거리 구간별 실측 RSSI 중앙값/표준편차를 baseline 으로 삼아
  각 메시지의 rssi_zscore 를 계산한다 (경험적 방식).
- fspl_db(): 자유공간경로손실(FSPL) 이론값. 안테나 이득/케이블 손실 미반영이라
  절대값이 아니라 거리에 따른 감쇠 "형태(기울기)" 참고용이다.
"""
import numpy as np
import pandas as pd

AIS_FREQ_MHZ = 162.0                        # AIS Ch A/B(~161.975/162.025MHz) 평균
TX_POWER_DBM = 10 * np.log10(12.5 * 1000)   # Class A 표준 출력 12.5W -> dBm (~41dBm)


def fspl_db(dist_km, freq_mhz: float = AIS_FREQ_MHZ):
    """자유공간경로손실(dB) = 20*log10(d_km) + 20*log10(f_MHz) + 32.44"""
    return 20 * np.log10(dist_km) + 20 * np.log10(freq_mhz) + 32.44


def build_baseline(df: pd.DataFrame, n_bins: int = 25, min_bin_samples: int = 20):
    """df(최소 columns=[dist_m, vsi_rssi])에 로그거리 baseline 을 적용한다.

    반환: (df_scored, baseline_df, reg_stats)
      df_scored: 입력 df + [dist_km, log_dist, dist_bin, rssi_median, rssi_std,
                            rssi_residual, rssi_zscore]
      baseline_df: 거리구간별 [bin_center_km, rssi_median, rssi_std, n] (표본 부족 구간 제외)
      reg_stats: 실측 로그거리 선형회귀 {slope, intercept, r2, corr, resid_std}
    """
    df = df[df["dist_m"] > 0].copy()
    df["dist_km"] = df["dist_m"] / 1000
    df["log_dist"] = np.log10(df["dist_km"])

    edges = np.linspace(df["log_dist"].min(), df["log_dist"].max(), n_bins + 1)
    df["dist_bin"] = pd.cut(df["log_dist"], bins=edges, include_lowest=True)

    baseline = (
        df.groupby("dist_bin", observed=True)
          .agg(bin_center_km=("dist_km", "median"),
               rssi_median=("vsi_rssi", "median"),
               rssi_std=("vsi_rssi", "std"),
               n=("vsi_rssi", "size"))
          .reset_index()
    )
    baseline = baseline[baseline["n"] >= min_bin_samples].reset_index(drop=True)

    bin_map = baseline.set_index("dist_bin")[["rssi_median", "rssi_std"]]
    df = df.join(bin_map, on="dist_bin")
    df["rssi_residual"] = df["vsi_rssi"] - df["rssi_median"]
    df["rssi_zscore"] = df["rssi_residual"] / df["rssi_std"].replace(0, np.nan)

    x, y = df["log_dist"].values, df["vsi_rssi"].values
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - np.sum((y - pred) ** 2) / ss_tot if ss_tot > 0 else float("nan")
    corr = np.corrcoef(x, y)[0, 1] if len(x) > 1 else float("nan")

    reg_stats = dict(slope=float(slope), intercept=float(intercept),
                     r2=float(r2), corr=float(corr), resid_std=float((y - pred).std()))
    return df, baseline, reg_stats
