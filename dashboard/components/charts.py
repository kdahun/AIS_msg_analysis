"""Plotly 차트 래퍼. 대량 산점도는 WebGL(Scattergl)을 사용해 성능을 확보한다.
집계/다운샘플은 호출 전에 SQL 단계에서 끝냈다고 가정한다.
"""
import plotly.express as px
import plotly.graph_objects as go

_TEMPLATE = "plotly_dark"


def distribution_bars(df, metric_label: str):
    """값별 건수(GROUP BY value) 막대 분포. df columns=[mmsi, value, n]"""
    fig = px.bar(df, x="value", y="n", color=df["mmsi"].astype(str),
                 barmode="overlay", opacity=0.7,
                 labels={"value": metric_label, "n": "건수", "color": "MMSI"},
                 template=_TEMPLATE)
    fig.update_layout(legend_title_text="MMSI", margin=dict(t=30, b=0, l=0, r=0))
    return fig


def timeseries_dual(df):
    """시간축 RSSI/SNR 평균 이중 y축 라인. df columns=[ts, n, rssi_avg, snr_avg]"""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["ts"], y=df["rssi_avg"], name="RSSI 평균",
                             mode="lines", line=dict(color="#4C9BE8")))
    fig.add_trace(go.Scatter(x=df["ts"], y=df["snr_avg"], name="SNR 평균",
                             mode="lines", line=dict(color="#E8A33D"), yaxis="y2"))
    fig.update_layout(
        template=_TEMPLATE, margin=dict(t=30, b=0, l=0, r=0),
        xaxis=dict(title="시각"),
        yaxis=dict(title="RSSI 평균", color="#4C9BE8"),
        yaxis2=dict(title="SNR 평균", overlaying="y", side="right", color="#E8A33D"),
        legend=dict(orientation="h", y=1.1),
    )
    return fig


def box_by_type(stats_df, metric: str):
    """메시지 타입별 RSSI 또는 SNR 박스플롯(사전 계산된 사분위수 사용).
    metric: 'rssi' | 'snr'
    """
    fig = go.Figure()
    for r in stats_df.itertuples():
        label = str(int(r.msg_type))
        fig.add_trace(go.Box(
            x=[label], name=label,
            q1=[getattr(r, f"{metric}_q1")], median=[getattr(r, f"{metric}_med")],
            q3=[getattr(r, f"{metric}_q3")],
            lowerfence=[getattr(r, f"{metric}_min")], upperfence=[getattr(r, f"{metric}_max")],
            mean=[getattr(r, f"{metric}_avg")],
        ))
    fig.update_xaxes(type="category")
    fig.update_layout(template=_TEMPLATE, showlegend=False,
                      xaxis_title="메시지 타입", yaxis_title=metric.upper(),
                      margin=dict(t=30, b=0, l=0, r=0))
    return fig


def scatter_over_time(df, y_col: str, y_label: str, color_by_mmsi: bool = True):
    """개별 메시지 값을 시간순으로 선으로 이은 그래프(WebGL, 집계 없이 원본 그대로).
    df 는 반드시 x축 컬럼(vsi_time) 기준 오름차순 정렬되어 있어야 선이 올바르게 이어진다
    (core.queries.points() 가 이미 정렬해서 반환함).
    df columns=[vsi_time, mmsi, y_col]
    color_by_mmsi: MMSI 여러 개 선택 시 색으로 구분 (너무 많으면 호출 측에서 False 로 끄는 걸 권장).
                  여러 MMSI 가 섞여도 px 가 MMSI 별로 트레이스를 나누므로 선은 같은 배(MMSI) 안에서만 이어진다.
    """
    fig = px.scatter(
        df, x="vsi_time", y=y_col,
        color=df["mmsi"].astype(str) if color_by_mmsi else None,
        labels={"vsi_time": "수신시각(VSI 기준)", y_col: y_label, "color": "MMSI"},
        template=_TEMPLATE, render_mode="webgl",
    )
    fig.update_traces(mode="lines+markers",
                      marker=dict(size=3, opacity=0.6),
                      line=dict(width=1))
    fig.update_layout(margin=dict(t=30, b=0, l=0, r=0),
                      legend_title_text="MMSI" if color_by_mmsi else None,
                      showlegend=color_by_mmsi)
    return fig


def scatter_rssi_snr(df):
    """RSSI vs SNR 산점도(WebGL). df columns=[vsi_rssi, vsi_snr, ...]"""
    fig = go.Figure(go.Scattergl(
        x=df["vsi_rssi"], y=df["vsi_snr"], mode="markers",
        marker=dict(size=4, opacity=0.4, color="#4C9BE8"),
    ))
    fig.update_layout(template=_TEMPLATE, xaxis_title="RSSI", yaxis_title="SNR",
                      margin=dict(t=30, b=0, l=0, r=0))
    return fig


def signal_validity_scatter(sample_df, baseline_df, reg_stats, fspl_fn, tx_power_dbm):
    """거리(log) vs RSSI: 실측 표본 + 경험적 baseline + FSPL 이론 + 실측회귀 비교.
    sample_df columns=[dist_km, vsi_rssi] (표시용 표본, 이미 다운샘플되어 있다고 가정)
    baseline_df columns=[bin_center_km, rssi_median]
    """
    import numpy as np

    d_min = min(sample_df["dist_km"].min(), baseline_df["bin_center_km"].min())
    d_max = max(sample_df["dist_km"].max(), baseline_df["bin_center_km"].max())
    d_line = np.linspace(d_min, d_max, 200)
    log_d_line = np.log10(d_line)

    fig = go.Figure()
    fig.add_trace(go.Scattergl(
        x=sample_df["dist_km"], y=sample_df["vsi_rssi"], mode="markers",
        marker=dict(size=3, opacity=0.25, color="#4C9BE8"), name="실측 RSSI(표본)",
    ))
    fig.add_trace(go.Scatter(
        x=baseline_df["bin_center_km"], y=baseline_df["rssi_median"],
        mode="lines+markers", name="경험적 baseline(구간 중앙값)",
        line=dict(color="#E8A33D", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=d_line, y=tx_power_dbm - fspl_fn(d_line),
        mode="lines", name="FSPL 이론(12.5W 가정)",
        line=dict(color="#FF6B6B", width=2, dash="dash"),
    ))
    fig.add_trace(go.Scatter(
        x=d_line, y=reg_stats["intercept"] + reg_stats["slope"] * log_d_line,
        mode="lines", name=f"실측 회귀({reg_stats['slope']:.1f}dB/decade)",
        line=dict(color="#5CD65C", width=2, dash="dot"),
    ))
    fig.update_xaxes(type="log", title="거리 (km, log scale)")
    fig.update_yaxes(title="RSSI")
    fig.update_layout(template=_TEMPLATE, height=550, margin=dict(t=30, b=0, l=0, r=0),
                      legend=dict(orientation="h", y=1.08))
    return fig


def trajectory_time_series(df, color_by_mmsi: bool = True):
    """시간(vsi_time) 축을 공유하는 2단 그래프: 위=거리(km), 아래=RSSI.
    같은 x좌표(시각)에서 위/아래를 같이 보면 "거리가 줄 때 신호가 세지는지"를 바로 비교할 수 있다.
    df 는 vsi_time 오름차순 정렬되어 있어야 하고, columns=[vsi_time, mmsi, dist_km, vsi_rssi] 필요.
    """
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        subplot_titles=("거리 (km)", "RSSI"))

    palette = px.colors.qualitative.Plotly
    mmsi_list = list(df["mmsi"].unique()) if color_by_mmsi else [None]
    for i, m in enumerate(mmsi_list):
        sub = df[df["mmsi"] == m] if color_by_mmsi else df
        color = palette[i % len(palette)]
        name = str(m) if color_by_mmsi else "선택 MMSI"
        fig.add_trace(go.Scattergl(
            x=sub["vsi_time"], y=sub["dist_km"], mode="lines+markers", name=name,
            legendgroup=name, line=dict(color=color, width=1), marker=dict(size=3),
        ), row=1, col=1)
        fig.add_trace(go.Scattergl(
            x=sub["vsi_time"], y=sub["vsi_rssi"], mode="lines+markers", name=name,
            legendgroup=name, showlegend=False,
            line=dict(color=color, width=1), marker=dict(size=3),
        ), row=2, col=1)

    fig.update_yaxes(title_text="거리 (km)", row=1, col=1)
    fig.update_yaxes(title_text="RSSI", row=2, col=1)
    fig.update_xaxes(title_text="수신시각 (VSI 기준)", row=2, col=1)
    fig.update_layout(template=_TEMPLATE, height=650, margin=dict(t=40, b=0, l=0, r=0),
                      legend=dict(orientation="h", y=1.06),
                      legend_title_text="MMSI" if color_by_mmsi else None)
    return fig
