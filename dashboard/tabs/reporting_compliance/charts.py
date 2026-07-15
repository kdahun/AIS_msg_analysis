"""보고주기 탭 전용 차트: 정상/위반 원그래프, 채널별 프레임 슬롯맵, 상황 선그래프.

슬롯맵은 참조 이미지 스타일: 숫자 없는 정사각 칸(파랑=정상 수신, 주황=위반, 어두움=빈슬롯),
채널 A/B 를 따로 그린다. 슬롯 번호·상세정보는 hover 로 확인.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from . import logic

_TEMPLATE = "plotly_dark"
SLOTS_PER_FRAME = 2250
GRID_COLS, GRID_ROWS = 75, 30      # 75×30 = 2250 (채널당 한 장)

_PIE_COLORS = {"정상": "#3BA776", "보고주기 위반": "#E8A33D",
               "슬롯 위반": "#D64550", "둘 다 위반": "#8E44AD"}

# 0=빈슬롯(어두움+은은한 격자), 1=정상(파랑), 2=위반(주황) — 참조 이미지 팔레트
_COLORSCALE = [[0.0, "#161B26"], [0.33, "#161B26"],
               [0.34, "#4D9FEC"], [0.66, "#4D9FEC"],
               [0.67, "#F2A33C"], [1.0, "#F2A33C"]]


def category_series(df):
    """메시지별 위반 카테고리 Series 반환 (정상/보고주기/슬롯/둘다)."""
    ri = df["ri_reason"].values != ""
    slot = df["slot_reason"].values != ""
    cat = np.where(ri & slot, "둘 다 위반",
          np.where(ri, "보고주기 위반",
          np.where(slot, "슬롯 위반", "정상")))
    return pd.Series(cat, index=df.index)


def compliance_pie(cat_counts: dict):
    """정상/위반 카테고리 원그래프. cat_counts = {카테고리: 건수}."""
    labels = [k for k in ["정상", "보고주기 위반", "슬롯 위반", "둘 다 위반"] if cat_counts.get(k, 0) > 0]
    values = [cat_counts[k] for k in labels]
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.45,
        marker=dict(colors=[_PIE_COLORS[k] for k in labels]),
        textinfo="label+percent", sort=False))
    fig.update_layout(template=_TEMPLATE, height=340, margin=dict(t=20, b=0, l=0, r=0),
                      showlegend=True, legend=dict(orientation="h", y=-0.05))
    return fig


def channel_slot_map(channel_df, channel: str):
    """한 프레임·한 채널(A/B)의 2,250슬롯 그리드(75×30, 숫자 없음).
    channel_df: 해당 프레임·채널의 행들 (columns=[vsi_slot, mmsi, msg_type,
    is_violation, ri_reason, slot_reason, vsi_rssi, vsi_snr, dist_km]).
    """
    state = np.zeros(SLOTS_PER_FRAME, dtype=int)
    info = np.full(SLOTS_PER_FRAME, "", dtype=object)
    for s in range(SLOTS_PER_FRAME):
        info[s] = f"슬롯 {s} · 빈 슬롯"

    agg = {}
    for r in channel_df.itertuples(index=False):
        s = int(r.vsi_slot)
        if 0 <= s < SLOTS_PER_FRAME:
            agg.setdefault(s, []).append(r)

    for s, rows in agg.items():
        any_viol = any(x.is_violation for x in rows)
        state[s] = 2 if any_viol else 1
        if len(rows) == 1:
            r = rows[0]
            reason = logic.combined_reason_ko(r.ri_reason, r.slot_reason)
            rssi = "-" if pd.isna(r.vsi_rssi) else f"{r.vsi_rssi:.0f}"
            snr = "-" if pd.isna(r.vsi_snr) else f"{r.vsi_snr:.0f}"
            dist = "-" if pd.isna(r.dist_km) else f"{r.dist_km:.2f}km"
            info[s] = (f"슬롯 {s} · MMSI {r.mmsi} · Type{r.msg_type}<br>"
                       f"사유: {reason}<br>RSSI {rssi} · SNR {snr} · 거리 {dist}")
        else:
            info[s] = (f"슬롯 {s} · {len(rows)}개 메시지(충돌 후보) · "
                       + ("위반 포함" if any_viol else "정상"))

    z = state.reshape(GRID_ROWS, GRID_COLS)
    custom = info.reshape(GRID_ROWS, GRID_COLS)

    fig = go.Figure(go.Heatmap(
        z=z, customdata=custom,
        hovertemplate="%{customdata}<extra></extra>",
        colorscale=_COLORSCALE, zmin=0, zmax=2, showscale=False,
        xgap=2, ygap=2,
    ))
    fig.update_yaxes(autorange="reversed", showticklabels=False, showgrid=False,
                     scaleanchor="x", scaleratio=1)   # 정사각 칸
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_layout(
        template=_TEMPLATE, height=520,
        margin=dict(t=30, b=6, l=6, r=6),
        title=dict(text=f"채널 {channel} · 슬롯 0 ~ 2249 (좌→우, 위→아래 순서)",
                   font=dict(size=13), x=0.01, y=0.99),
        plot_bgcolor="#0E1117",
    )
    return fig


def context_lines(vessel_df, frame_ts, color_by_mmsi=True):
    """선박(들)의 RSSI/SNR/거리 시간추이 선그래프 3단 (시간축 공유).
    현재 보고 있는 프레임 시점(frame_ts)을 세로 점선으로 표시한다.
    vessel_df: vsi_time 오름차순, columns=[vsi_time, mmsi, vsi_rssi, vsi_snr, dist_km]
    """
    from plotly.subplots import make_subplots
    import plotly.express as px

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=("RSSI", "SNR", "거리 (km, 한국해양대 기준)"))

    palette = px.colors.qualitative.Plotly
    mmsi_list = list(vessel_df["mmsi"].unique()) if color_by_mmsi else [None]
    for i, m in enumerate(mmsi_list):
        sub = vessel_df[vessel_df["mmsi"] == m] if color_by_mmsi else vessel_df
        color = palette[i % len(palette)]
        name = str(m) if color_by_mmsi else "선택 선박"
        common = dict(mode="lines+markers", legendgroup=name,
                      line=dict(color=color, width=1), marker=dict(size=3))
        fig.add_trace(go.Scattergl(x=sub["vsi_time"], y=sub["vsi_rssi"],
                                   name=name, **common), row=1, col=1)
        fig.add_trace(go.Scattergl(x=sub["vsi_time"], y=sub["vsi_snr"],
                                   name=name, showlegend=False, **common), row=2, col=1)
        fig.add_trace(go.Scattergl(x=sub["vsi_time"], y=sub["dist_km"],
                                   name=name, showlegend=False, **common), row=3, col=1)

    fig.add_vrect(x0=frame_ts, x1=frame_ts + pd.Timedelta(minutes=1),
                  fillcolor="#E8A33D", opacity=0.25, line_width=0)
    fig.add_vline(x=frame_ts, line=dict(color="#E8A33D", width=1.5, dash="dash"))

    fig.update_xaxes(title_text="수신시각 (VSI 기준)", row=3, col=1)
    fig.update_layout(template=_TEMPLATE, height=560,
                      margin=dict(t=40, b=0, l=0, r=0),
                      legend=dict(orientation="h", y=1.06),
                      legend_title_text="MMSI" if color_by_mmsi else None)
    return fig
