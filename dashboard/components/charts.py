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
    """개별 메시지 값의 시간별 산점도(WebGL, 집계 없이 원본 그대로).
    df columns=[recv_time, mmsi, y_col]
    color_by_mmsi: MMSI 여러 개 선택 시 색으로 구분 (너무 많으면 호출 측에서 False 로 끄는 걸 권장)
    """
    fig = px.scatter(
        df, x="recv_time", y=y_col,
        color=df["mmsi"].astype(str) if color_by_mmsi else None,
        labels={"recv_time": "수신시각", y_col: y_label, "color": "MMSI"},
        template=_TEMPLATE, render_mode="webgl",
    )
    fig.update_traces(marker=dict(size=4, opacity=0.55))
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
