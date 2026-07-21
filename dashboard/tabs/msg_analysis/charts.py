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
GRID_COLS, GRID_ROWS = 75, 30      # 75×30 = 2250

# A/B 통합 슬롯맵 색: 채널 A=파랑, 채널 B=청록, 위반=빨강
_CH_COLOR = {"A": "#4D9FEC", "B": "#33C4B3"}
_VIOL_COLOR = "#F2453C"

_PIE_COLORS = {"정상": "#3BA776", "보고주기 위반": "#E8A33D",
               "슬롯 위반": "#D64550", "둘 다 위반": "#8E44AD", "검증 보류": "#7A8290"}
_HOLD_COLOR = "#7A8290"

# 0=빈슬롯(어두움+은은한 격자), 1=정상(파랑), 2=위반(주황) — 참조 이미지 팔레트
_COLORSCALE = [[0.0, "#161B26"], [0.33, "#161B26"],
               [0.34, "#4D9FEC"], [0.66, "#4D9FEC"],
               [0.67, "#F2A33C"], [1.0, "#F2A33C"]]


def category_series(df):
    """메시지별 카테고리 Series (정상/보고주기 위반/슬롯 위반/둘 다 위반/검증 보류).

    '검증 보류'는 위반이 아니라 수신 유실 등으로 선박 탓이라 단정 못하는 경우
    (보고 유실 RI_LOST_* / 슬롯 미수신 SLOT_UNVERIF_*). 위반이 하나라도 있으면
    확정 위반으로 분류한다.
    """
    ri_code = df["ri_reason"].values.astype(str)
    slot_code = df["slot_reason"].values.astype(str)
    ri_viol = np.array([r in logic.RI_VIOLATION_CODES for r in ri_code])
    ri_hold = np.array([r in logic.RI_HOLD_CODES for r in ri_code])
    slot_viol = np.array([s in logic.SLOT_VIOLATION_CODES for s in slot_code])
    slot_hold = np.array([s in logic.SLOT_HOLD_CODES for s in slot_code])
    cat = np.where(ri_viol & slot_viol, "둘 다 위반",
          np.where(ri_viol, "보고주기 위반",
          np.where(slot_viol, "슬롯 위반",
          np.where(ri_hold | slot_hold, "검증 보류", "정상"))))
    return pd.Series(cat, index=df.index)


def compliance_pie(cat_counts: dict):
    """정상/위반 카테고리 원그래프. cat_counts = {카테고리: 건수}."""
    labels = [k for k in ["정상", "보고주기 위반", "슬롯 위반", "둘 다 위반", "검증 보류"]
              if cat_counts.get(k, 0) > 0]
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


# 통합 슬롯맵 격자: 50×45 = 2250 (정사각에 가까워 셀이 크게 보임)
MAP_COLS, MAP_ROWS = 50, 45


def combined_slot_map(frame_df, highlight_mmsi=None, losses=None):
    """채널 A/B 를 한 그리드(50×45)에 통합한 슬롯맵. 클릭 선택 지원(scattergl).

    - 채널 A = 파랑, 채널 B = 청록, 위반 = 빨강, 검증 보류 = 회색 (채널 무관)
    - A 마커는 셀 왼쪽(x-0.22), B 마커는 오른쪽(x+0.22)에 찍어 한 슬롯에 두 채널이
      같이 와도 겹치지 않게 구분
    - 격자선(그래프용지식) + 좌표축 라벨(행=슬롯 시작번호, 열=+0~+49)로 슬롯 위치를
      눈으로 바로 읽을 수 있게 함. 슬롯 = 행 라벨 + 열 번호. hover 로 정확한 슬롯 확인.
    - highlight_mmsi 지정 시 그 선박의 슬롯을 크게+흰 테두리로 강조
    - customdata=[mmsi, ...] 로 클릭 시 MMSI 를 회수
    - losses: 이 프레임의 슬롯 특정 유실 rows(columns: slot, channel, mmsi,
      est_rssi, is_env). 빈 사각(테두리만)으로 표시 — 주황=환경성, 회색=원인미상.
    frame_df columns=[vsi_slot, mmsi, msg_type, channel, is_violation,
                      ri_reason, slot_reason, ri_missed_count, vsi_rssi, vsi_snr, dist_km]
    """
    fig = go.Figure()

    def _add(sub, color, name, offset):
        if sub.empty:
            return
        slot = sub["vsi_slot"].astype(int).values
        x = (slot % MAP_COLS) + offset
        y = slot // MAP_COLS
        texts = []
        for r in sub.itertuples(index=False):
            reason = logic.combined_reason_ko(r.ri_reason, r.slot_reason,
                                              getattr(r, "ri_missed_count", 0))
            rssi = "-" if pd.isna(r.vsi_rssi) else f"{r.vsi_rssi:.0f}"
            snr = "-" if pd.isna(r.vsi_snr) else f"{r.vsi_snr:.0f}"
            dist = "-" if pd.isna(r.dist_km) else f"{r.dist_km:.2f}km"
            texts.append(f"슬롯 {int(r.vsi_slot)} · 채널 {r.channel} · MMSI {r.mmsi} "
                         f"· Type{r.msg_type}<br>사유: {reason}<br>"
                         f"RSSI {rssi} · SNR {snr} · 거리 {dist}")
        fig.add_trace(go.Scattergl(
            x=x, y=y, mode="markers", name=name,
            marker=dict(color=color, size=11, symbol="square",
                        line=dict(width=0.7, color="#0E1117")),
            customdata=sub["mmsi"].values, text=texts,
            hovertemplate="%{text}<extra></extra>"))

    hold = frame_df["slot_reason"].isin(list(logic.SLOT_HOLD_CODES)) & ~frame_df["is_violation"]
    viol = frame_df[frame_df["is_violation"]]
    held = frame_df[hold]
    ok = frame_df[~frame_df["is_violation"] & ~hold]
    _add(ok[ok["channel"] == "A"], _CH_COLOR["A"], "채널 A (정상)", -0.22)
    _add(ok[ok["channel"] == "B"], _CH_COLOR["B"], "채널 B (정상)", +0.22)
    # 검증 보류(다음 프레임 미수신)는 회색
    _add(held[held["channel"] == "A"], _HOLD_COLOR, "검증 보류", -0.22)
    _add(held[held["channel"] == "B"], _HOLD_COLOR, "검증 보류", +0.22)
    # 위반은 채널별 위치는 유지하되 색만 빨강
    va = viol[viol["channel"] == "A"]; vb = viol[viol["channel"] == "B"]
    _add(va, _VIOL_COLOR, "위반", -0.22)
    _add(vb, _VIOL_COLOR, "위반", +0.22)

    # 유실(수신 못한 예약 슬롯): 테두리만 있는 빈 사각형
    if losses is not None and len(losses):
        for env, color, name in [(True, "#F2A33C", "유실(환경성 추정)"),
                                 (False, "#9AA3B2", "유실(원인 미상)")]:
            sub = losses[losses["is_env"] == env]
            if sub.empty:
                continue
            slot = sub["slot"].astype(int).values
            offs = np.where(sub["channel"].values == "A", -0.22, 0.22)
            texts = [f"유실 · 슬롯 {int(r.slot)} · 채널 {r.channel} · MMSI {r.mmsi}"
                     f"<br>추정 RSSI {r.est_rssi:.0f} dBm (양옆 수신 평균)"
                     for r in sub.itertuples()]
            fig.add_trace(go.Scattergl(
                x=(slot % MAP_COLS) + offs, y=slot // MAP_COLS,
                mode="markers", name=name,
                marker=dict(color="rgba(0,0,0,0)", size=10, symbol="square",
                            line=dict(width=1.5, color=color)),
                customdata=sub["mmsi"].values, text=texts,
                hovertemplate="%{text}<extra></extra>"))

    if highlight_mmsi is not None:
        hs = frame_df[frame_df["mmsi"] == highlight_mmsi]
        if not hs.empty:
            slot = hs["vsi_slot"].astype(int).values
            offs = np.where(hs["channel"].values == "A", -0.22, 0.22)
            fig.add_trace(go.Scattergl(
                x=(slot % MAP_COLS) + offs, y=slot // MAP_COLS,
                mode="markers", name=f"선택: {highlight_mmsi}",
                marker=dict(color="rgba(0,0,0,0)", size=20, symbol="square",
                            line=dict(width=2.5, color="#FFFFFF")),
                hoverinfo="skip", showlegend=True))

    _apply_slotmap_axes(
        fig, "슬롯 = 왼쪽 행번호 + 상단 열번호 (예: 행 500 · 열 +7 → 슬롯 507) · "
             "셀 왼쪽=채널A, 오른쪽=채널B · 드래그로 확대, 슬롯 클릭 시 그 선박 강조")
    return fig


def _apply_slotmap_axes(fig, title, height=680):
    """50×45 슬롯맵 공통 축(그래프용지 격자 + 행/열 라벨)·레이아웃."""
    _grid = dict(showgrid=True, gridcolor="rgba(255,255,255,0.12)", gridwidth=1,
                 minor=dict(dtick=1, showgrid=True,
                            gridcolor="rgba(255,255,255,0.05)", gridwidth=1))
    # y축(행): 라벨 = 그 행의 시작 슬롯 번호(행×50), 2행마다 → 0,100,...,2200
    fig.update_yaxes(
        range=[MAP_ROWS - 0.5, -0.5], scaleanchor="x", scaleratio=1, zeroline=False,
        tickmode="array", tickvals=list(range(0, MAP_ROWS, 2)),
        ticktext=[str(r * MAP_COLS) for r in range(0, MAP_ROWS, 2)],
        tickfont=dict(size=9), ticks="outside", ticklen=3, **_grid)
    # x축(열): +0 ~ +49, 10칸마다 라벨
    fig.update_xaxes(
        range=[-0.7, MAP_COLS - 0.3], zeroline=False,
        tickmode="array", tickvals=list(range(0, MAP_COLS, 10)),
        ticktext=[f"+{c}" for c in range(0, MAP_COLS, 10)],
        tickfont=dict(size=9), ticks="outside", ticklen=3, **_grid)
    fig.update_layout(
        template=_TEMPLATE, height=height, margin=dict(t=30, b=6, l=6, r=6),
        plot_bgcolor="#0E1117", clickmode="event+select",
        legend=dict(orientation="h", y=1.03),
        title=dict(text=title, font=dict(size=11), x=0.01, y=0.995))


def intrusion_slot_map(frame_df, frame_events, sel_slot=None, sel_channel=None):
    """침범 전용 슬롯맵: 그 프레임의 일반 수신은 흐린 회색, 침범 슬롯만 강조.

    frame_events: 이 프레임의 침범 이벤트 rows
      (columns: channel, slot, victim, victim_rssi, intruder, intruder_rssi)
    sel_slot/sel_channel: 선택된 이벤트 슬롯 → 흰 테두리 강조
    """
    fig = go.Figure()

    # 배경: 이 프레임의 모든 수신(흐리게) — 침범이 어디서 일어났는지 맥락
    if len(frame_df):
        slot = frame_df["vsi_slot"].astype(int).values
        offs = np.where(frame_df["channel"].values == "A", -0.22, 0.22)
        fig.add_trace(go.Scattergl(
            x=(slot % MAP_COLS) + offs, y=slot // MAP_COLS,
            mode="markers", name="일반 수신",
            marker=dict(color="rgba(120,130,150,0.30)", size=8, symbol="square"),
            hoverinfo="skip"))

    # 침범 슬롯: 빨강 큰 마커, hover 에 피해자↔침범자 RSSI 비교
    if len(frame_events):
        slot = frame_events["slot"].astype(int).values
        offs = np.where(frame_events["channel"].values == "A", -0.22, 0.22)
        texts = [f"슬롯 {int(r.slot)} · 채널 {r.channel}<br>"
                 f"침범자 MMSI {r.intruder} ({r.intruder_rssi:.0f} dBm)<br>"
                 f"← 피해자 MMSI {r.victim} ({r.victim_rssi:.0f} dBm, "
                 f"직전 프레임 예약)"
                 for r in frame_events.itertuples()]
        fig.add_trace(go.Scattergl(
            x=(slot % MAP_COLS) + offs, y=slot // MAP_COLS,
            mode="markers", name="침범 슬롯",
            marker=dict(color=_VIOL_COLOR, size=13, symbol="square",
                        line=dict(width=1, color="#FFFFFF")),
            text=texts, hovertemplate="%{text}<extra></extra>"))

    if sel_slot is not None:
        off = -0.22 if sel_channel == "A" else 0.22
        fig.add_trace(go.Scattergl(
            x=[(int(sel_slot) % MAP_COLS) + off], y=[int(sel_slot) // MAP_COLS],
            mode="markers", name="선택 이벤트",
            marker=dict(color="rgba(0,0,0,0)", size=22, symbol="square",
                        line=dict(width=2.5, color="#FFFFFF")),
            hoverinfo="skip"))

    _apply_slotmap_axes(fig, "이 프레임의 침범 슬롯(빨강) · 회색=일반 수신 · "
                             "흰 테두리=선택한 이벤트", height=620)
    return fig


def intrusion_rssi_timeline(hist_df, victim, intruder, ev_frame, slot, channel):
    """한 (채널,슬롯)의 점유 이력에서 침범 순간을 보여주는 RSSI 타임라인.

    hist_df: 그 (채널,슬롯)의 ±수분 수신 rows (columns: vsi_time, mmsi, vsi_rssi,
             slot_timeout). 피해자=파란 선+점, 침범자=빨간 큰 점, 기타=회색 점.
    ev_frame: 침범 프레임(Timestamp) → 주황 세로 밴드.
    """
    fig = go.Figure()
    hist_df = hist_df.sort_values("vsi_time")

    others = hist_df[~hist_df["mmsi"].isin([victim, intruder])]
    if len(others):
        fig.add_trace(go.Scatter(
            x=others["vsi_time"], y=others["vsi_rssi"], mode="markers",
            name="기타 선박", marker=dict(color="#7A8290", size=7),
            customdata=others["mmsi"],
            hovertemplate="MMSI %{customdata}<br>%{y:.0f} dBm<extra></extra>"))

    v = hist_df[hist_df["mmsi"] == victim]
    if len(v):
        to_txt = ["" if pd.isna(t) else f" · timeout {int(t)}"
                  for t in v["slot_timeout"]]
        fig.add_trace(go.Scatter(
            x=v["vsi_time"], y=v["vsi_rssi"], mode="lines+markers",
            name=f"피해자 {victim}",
            line=dict(color="#4D9FEC", width=1.5),
            marker=dict(color="#4D9FEC", size=10),
            text=to_txt,
            hovertemplate=f"피해자 MMSI {victim}<br>%{{y:.0f}} dBm%{{text}}<extra></extra>"))

    g = hist_df[hist_df["mmsi"] == intruder]
    if len(g):
        fig.add_trace(go.Scatter(
            x=g["vsi_time"], y=g["vsi_rssi"], mode="markers",
            name=f"침범자 {intruder}",
            marker=dict(color=_VIOL_COLOR, size=16, symbol="diamond",
                        line=dict(width=1.5, color="#FFFFFF")),
            hovertemplate=f"침범자 MMSI {intruder}<br>%{{y:.0f}} dBm<extra></extra>"))

    fig.add_vrect(x0=ev_frame, x1=ev_frame + pd.Timedelta(minutes=1),
                  fillcolor="rgba(242,163,60,0.15)", line_width=0,
                  annotation_text="침범 프레임", annotation_position="top left",
                  annotation_font=dict(size=11, color="#F2A33C"))

    fig.update_layout(
        template=_TEMPLATE, height=380, margin=dict(t=40, b=10, l=10, r=10),
        plot_bgcolor="#0E1117", legend=dict(orientation="h", y=1.12),
        title=dict(text=f"채널 {channel} · 슬롯 {slot} 점유 이력 — 피해자(파랑)가 "
                        "지키던 슬롯에 침범자(빨강 다이아)가 더 강한 신호로 등장",
                   font=dict(size=12), x=0.01),
        yaxis_title="RSSI (dBm)", xaxis_title="수신 시각")
    return fig


def loss_timeline(loss_per_bucket: pd.Series, noise_df, bucket_min: int,
                  est_rssi_q=None):
    """시간대별 유실 추이(막대) + 잡음층·유실 추정 RSSI(보조축, dBm).

    유실 신호의 추정 RSSI(양옆 수신 보간)가 잡음층에 붙으면 '신호가 잡음에
    묻혀 유실'(환경성), 잡음층보다 훨씬 위면 신호 세기 문제가 아님을 뜻한다.

    loss_per_bucket: index=bucket 시각, value=유실 보고 수
    noise_df: columns=[frame, noise_dbm]
    est_rssi_q: DataFrame(index=bucket, columns=[q25, q50, q75]) — 유실 추정 RSSI 분위
    """
    from plotly.subplots import make_subplots
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        x=loss_per_bucket.index, y=loss_per_bucket.values, name="유실 보고 수",
        marker_color="#F2A33C", opacity=0.75,
        hovertemplate="%{x}<br>유실 %{y}건<extra></extra>"), secondary_y=False)

    # 유실 추정 RSSI: 중앙값 선 + 25~75% 밴드 (dBm 축)
    if est_rssi_q is not None and len(est_rssi_q):
        eq = est_rssi_q.dropna(subset=["q50"])
        fig.add_trace(go.Scatter(
            x=eq.index, y=eq["q75"], mode="lines", showlegend=False,
            line=dict(width=0), hoverinfo="skip"), secondary_y=True)
        fig.add_trace(go.Scatter(
            x=eq.index, y=eq["q25"], mode="lines", name="유실 추정 RSSI 25~75%",
            line=dict(width=0), fill="tonexty",
            fillcolor="rgba(77,159,236,0.18)", hoverinfo="skip"), secondary_y=True)
        fig.add_trace(go.Scatter(
            x=eq.index, y=eq["q50"], mode="lines",
            name="유실 신호 추정 RSSI(중앙값)",
            line=dict(color="#4D9FEC", width=1.8),
            hovertemplate="%{x}<br>유실 추정 RSSI %{y:.0f} dBm<extra></extra>"),
            secondary_y=True)

    if noise_df is not None and len(noise_df):
        nb = (noise_df.set_index("frame")["noise_dbm"]
              .resample(f"{bucket_min}min").median())
        fig.add_trace(go.Scatter(
            x=nb.index, y=nb.values, name="잡음층(중앙값)",
            line=dict(color="#FF5C5C", width=1.5, dash="dash"),
            hovertemplate="%{x}<br>잡음층 %{y:.0f} dBm<extra></extra>"),
            secondary_y=True)

    fig.update_layout(
        template=_TEMPLATE, height=360, margin=dict(t=30, b=10, l=10, r=10),
        plot_bgcolor="#0E1117", legend=dict(orientation="h", y=1.14),
        title=dict(text=f"{bucket_min}분 단위 유실 보고 수 · 오른쪽 축(dBm): "
                        "파란 선=유실 신호 추정 RSSI, 빨간 점선=잡음층 — "
                        "파란 선이 빨간 선에 붙을수록 환경성 유실",
                   font=dict(size=12), x=0.01))
    fig.update_yaxes(title_text="유실 보고 수", secondary_y=False)
    fig.update_yaxes(title_text="dBm (추정 RSSI · 잡음층)", secondary_y=True,
                     showgrid=False)
    return fig


def loss_margin_hist(margins: pd.Series, decode_margin: float):
    """유실 순간의 추정 신호여유(추정 RSSI − 잡음층) 분포 히스토그램.

    decode_margin 왼쪽(수신한계 미만) = 환경성 유실 — 잡음에 묻혀 못 받은 것.
    오른쪽으로 멀수록 신호는 충분했는데 유실된 것(혼잡/충돌 등 다른 원인).
    """
    m = margins.dropna()
    env = m[m < decode_margin]
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=m.values, xbins=dict(size=2), name="유실 구간",
        marker_color="#4D9FEC", opacity=0.85,
        hovertemplate="여유 %{x} dB<br>%{y}건<extra></extra>"))
    fig.add_vline(x=decode_margin, line=dict(color="#FF5C5C", width=2, dash="dash"),
                  annotation_text=f"수신한계 여유 {decode_margin:.0f}dB",
                  annotation_position="top right",
                  annotation_font=dict(color="#FF5C5C", size=11))
    fig.add_vrect(x0=float(m.min()) - 1 if len(m) else -1, x1=decode_margin,
                  fillcolor="rgba(255,92,92,0.08)", line_width=0)
    fig.update_layout(
        template=_TEMPLATE, height=300, margin=dict(t=34, b=10, l=10, r=10),
        plot_bgcolor="#0E1117", showlegend=False, bargap=0.05,
        title=dict(text=f"유실 순간 추정 신호여유 분포 — 한계선 왼쪽(붉은 음영) "
                        f"{len(env):,}건({100*len(env)/max(len(m),1):.0f}%)이 환경성(잡음에 묻힘)",
                   font=dict(size=12), x=0.01),
        xaxis_title="추정 신호여유 = 추정 RSSI − 잡음층 (dB)", yaxis_title="유실 구간 수")
    return fig


_METRICS = [("vsi_rssi", "RSSI"), ("vsi_snr", "SNR"), ("dist_km", "거리 (km, 수신 장소 기준)")]


def _merged_line(sub_df, col, color, name, showlegend):
    """여러 MMSI 의 궤적을 None 구분자로 이어붙인 단일 trace (수백 척도 가볍게 렌더).
    hover 에 MMSI 표시."""
    xs, ys, cds = [], [], []
    for m, g in sub_df.groupby("mmsi"):
        g = g.sort_values("vsi_time")
        xs.extend(g["vsi_time"].tolist()); xs.append(None)
        ys.extend(g[col].tolist()); ys.append(None)
        cds.extend([m] * len(g)); cds.append(None)
    return go.Scattergl(
        x=xs, y=ys, customdata=cds, mode="lines", name=name,
        legendgroup=name, showlegend=showlegend,
        line=dict(color=color, width=1),
        hovertemplate="MMSI %{customdata}<br>%{y}<extra></extra>")


def context_lines_frame(window_df, frame_ts, violator_mmsis, max_legend=12,
                        noise_df=None, decode_margin=10.0):
    """현재 프레임에서 송신한 선박 전체의 RSSI/SNR/거리 시간추이 (프레임 주변 시간창).

    - 일반 선박: 얇은 반투명 파란 선(배경 = 주변 신호 환경 전체 상황)
    - 이 프레임에서 위반한 선박: 주황 강조 (max_legend 이하면 개별 색+범례)
    - 잡음층(noise_df, 분 단위 RSSI−SNR 중앙값): RSSI 패널에 빨간 점선 +
      '수신한계(잡음층+decode_margin dB)' 음영 밴드. 선박 RSSI 가 이 밴드에
      들어오면 물리적으로 수신이 불안정한 상태(환경 요인 후보)
    - 현재 프레임 시점: 주황 세로선/영역
    window_df: columns=[vsi_time, mmsi, vsi_rssi, vsi_snr, dist_km]
    noise_df: columns=[frame, noise_dbm] (이미 시간창으로 잘려 있다고 가정)
    """
    from plotly.subplots import make_subplots
    import plotly.express as px

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=tuple(t for _, t in _METRICS))

    # 잡음층 + 수신한계 밴드를 먼저 깔아 배경으로 (RSSI 패널)
    if noise_df is not None and len(noise_df):
        nd = noise_df.sort_values("frame")
        fig.add_trace(go.Scatter(
            x=nd["frame"], y=nd["noise_dbm"], mode="lines",
            name="잡음층(RSSI−SNR 중앙값)",
            line=dict(color="#FF5C5C", width=1.5, dash="dash"),
            hovertemplate="잡음층 %{y:.0f} dBm<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=nd["frame"], y=nd["noise_dbm"] + decode_margin, mode="lines",
            name=f"수신한계(잡음층+{decode_margin:.0f}dB)",
            line=dict(color="rgba(255,92,92,0.5)", width=0.5),
            fill="tonexty", fillcolor="rgba(255,92,92,0.15)",
            hovertemplate="수신한계 %{y:.0f} dBm<extra></extra>"), row=1, col=1)
        # SNR 관점의 같은 기준선: SNR < decode_margin 이면 수신 불안정
        fig.add_hline(y=decode_margin, row=2, col=1,
                      line=dict(color="#FF5C5C", width=1, dash="dash"))

    normal = window_df[~window_df["mmsi"].isin(violator_mmsis)]
    viol = window_df[window_df["mmsi"].isin(violator_mmsis)]

    for row, (col, _title) in enumerate(_METRICS, start=1):
        if len(normal):
            fig.add_trace(_merged_line(normal, col, "rgba(77,159,236,0.35)",
                                       "정상 선박(주변 환경)", showlegend=(row == 1)),
                          row=row, col=1)

    v_list = sorted(viol["mmsi"].unique())
    if len(v_list) <= max_legend:
        palette = px.colors.qualitative.Plotly
        for i, m in enumerate(v_list):
            sub = viol[viol["mmsi"] == m].sort_values("vsi_time")
            color = palette[i % len(palette)]
            for row, (col, _t) in enumerate(_METRICS, start=1):
                fig.add_trace(go.Scattergl(
                    x=sub["vsi_time"], y=sub[col], mode="lines+markers",
                    name=f"위반: {m}", legendgroup=str(m), showlegend=(row == 1),
                    line=dict(color=color, width=2), marker=dict(size=4)),
                    row=row, col=1)
    elif len(v_list):
        for row, (col, _t) in enumerate(_METRICS, start=1):
            fig.add_trace(_merged_line(viol, col, "#F2A33C",
                                       f"위반 선박 {len(v_list)}척", showlegend=(row == 1)),
                          row=row, col=1)

    fig.add_vrect(x0=frame_ts, x1=frame_ts + pd.Timedelta(minutes=1),
                  fillcolor="#E8A33D", opacity=0.25, line_width=0)
    fig.add_vline(x=frame_ts, line=dict(color="#E8A33D", width=1.5, dash="dash"))

    fig.update_xaxes(title_text="수신시각 (VSI 기준)", row=3, col=1)
    fig.update_layout(template=_TEMPLATE, height=620,
                      margin=dict(t=40, b=0, l=0, r=0),
                      legend=dict(orientation="h", y=1.05))
    return fig


def noise_est_vs_fsr(noise: pd.DataFrame):
    """잡음층 추정 vs 수신기 실측(FSR) — 위: 시계열, 아래: 차이 분포.

    noise: columns=[site_id, channel, frame, noise_est, noise_fsr, ...]
           실측이 있는 프레임만 넘긴다.
    두 선이 붙어 있으면 추정이 잘 맞는 것이고, 추정선이 아래로 벌어질수록
    잡음을 실제보다 조용하게 봐서 신호 여유를 크게 잡고 있다는 뜻이다.
    """
    from plotly.subplots import make_subplots
    d = noise.sort_values("frame")
    fig = make_subplots(rows=2, cols=1, row_heights=[0.62, 0.38], vertical_spacing=0.12,
                        subplot_titles=("프레임별 잡음층 (dBm)",
                                        "차이 분포 (추정 − 실측, dB)"))

    # 채널별로 선을 나눈다 — A/B 는 서로 다른 잡음 환경이다
    for ch, color in (("A", "#4C9BE8"), ("B", "#E8A33D")):
        g = d[d["channel"] == ch]
        if g.empty:
            continue
        fig.add_trace(go.Scatter(x=g["frame"], y=g["noise_fsr"], mode="lines",
                                 name=f"실측 {ch}", legendgroup=ch,
                                 line=dict(color=color, width=1.4)), row=1, col=1)
        fig.add_trace(go.Scatter(x=g["frame"], y=g["noise_est"], mode="lines",
                                 name=f"추정 {ch}", legendgroup=ch,
                                 line=dict(color=color, width=1.1, dash="dot"),
                                 opacity=0.75), row=1, col=1)

    diff = (d["noise_est"] - d["noise_fsr"]).dropna()
    fig.add_trace(go.Histogram(x=diff, nbinsx=60, name="차이",
                               marker_color="#7FB069", showlegend=False), row=2, col=1)
    fig.add_vline(x=0, row=2, col=1, line=dict(color="#AAAAAA", width=1, dash="dash"),
                  annotation_text="일치", annotation_position="top")
    if len(diff):
        fig.add_vline(x=float(diff.mean()), row=2, col=1,
                      line=dict(color="#FF5C5C", width=2),
                      annotation_text=f"평균 {diff.mean():+.1f}dB",
                      annotation_position="top left")

    fig.update_yaxes(title_text="dBm", row=1, col=1)
    fig.update_xaxes(title_text="추정 − 실측 (dB) · 음수 = 추정이 더 조용",
                     row=2, col=1)
    fig.update_yaxes(title_text="프레임 수", row=2, col=1)
    fig.update_layout(template=_TEMPLATE, height=560,
                      margin=dict(t=50, b=10, l=10, r=10),
                      legend=dict(orientation="h", y=1.06))
    return fig


def collection_timeline(segments: pd.DataFrame, half_runs: pd.DataFrame,
                        site_label: dict):
    """수집 이력을 한 줄짜리 간트로 — 언제 어디서 무슨 일이 있었는지.

    행 구성 (위→아래)
      장소       장소별 색 막대. 사이가 비어 있으면 그때 이동했거나 꺼져 있었다
      구간       연속 수신 구간. 막대 사이의 틈이 곧 장비 중단
      장비 상태   정상(초록) / 반쪽 가동(주황, 메시지는 있는데 FSR 만 없음)

    segments:  [segment_id, site_id, code, start, end, gap_sec, gap_reason]
    half_runs: [site_id, channel, start, end, n_frames, n_msg]
    """
    fig = go.Figure()
    ROWS = {"장소": 2, "구간": 1, "장비 상태": 0}
    site_colors = {}
    palette = ["#4D9FEC", "#33C4B3", "#E8A33D", "#B07FE8"]

    def bar(y, x0, x1, color, name, text, width=0.34, legend=True):
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[y, y], mode="lines",
            line=dict(color=color, width=18 * width * 2),
            name=name, legendgroup=name, showlegend=legend,
            hovertemplate=text + "<extra></extra>"))

    seen = set()
    for r in segments.itertuples():
        c = site_colors.setdefault(r.site_id, palette[len(site_colors) % len(palette)])
        label = site_label.get(r.site_id, f"site {r.site_id}")
        bar(ROWS["장소"], r.start, r.end, c, label,
            f"{label}<br>{r.start:%m-%d %H:%M} ~ {r.end:%m-%d %H:%M}",
            legend=label not in seen)
        seen.add(label)
        gap = "" if pd.isna(r.gap_sec) else f"<br>앞 공백 {r.gap_sec:,.0f}초 ({r.gap_reason})"
        bar(ROWS["구간"], r.start, r.end, "#7A8290", "구간",
            f"구간 {r.segment_id} · {r.n_msg:,}건<br>"
            f"{r.start:%m-%d %H:%M} ~ {r.end:%m-%d %H:%M}{gap}",
            legend="구간" not in seen)
        seen.add("구간")
        # 장비 상태: 기본은 정상
        bar(ROWS["장비 상태"], r.start, r.end, "#3BA776", "정상 가동",
            f"정상 가동<br>{r.start:%m-%d %H:%M} ~ {r.end:%m-%d %H:%M}",
            legend="정상 가동" not in seen)
        seen.add("정상 가동")

    # 반쪽 가동 구간을 장비 상태 행 위에 덧그린다 (채널 무관하게 합쳐서 표시)
    if len(half_runs):
        merged = (half_runs.groupby(["site_id"])
                  .apply(lambda g: g[["start", "end"]], include_groups=False)
                  .reset_index(drop=True))
        for r in merged.drop_duplicates().itertuples():
            bar(ROWS["장비 상태"], r.start, r.end + pd.Timedelta(minutes=1),
                "#F2A33C", "반쪽 가동(FSR 없음)",
                f"반쪽 가동 — 메시지는 정상, 상태 문장만 끊김<br>"
                f"{r.start:%m-%d %H:%M} ~ {r.end:%m-%d %H:%M}",
                legend="반쪽 가동(FSR 없음)" not in seen)
            seen.add("반쪽 가동(FSR 없음)")

    # 장소 이동·장비 중단 지점 표시
    for r in segments.itertuples():
        if pd.isna(r.gap_sec) or r.gap_sec <= 0:
            continue
        fig.add_vline(x=r.start, line=dict(
            color="#F2453C" if r.gap_reason == "장소 이동" else "#9AA3B2",
            width=1.5, dash="dot"))

    fig.update_yaxes(tickmode="array", tickvals=list(ROWS.values()),
                     ticktext=list(ROWS.keys()), range=[-0.6, 2.6])
    fig.update_xaxes(title_text="시각 (KST)")
    fig.update_layout(template=_TEMPLATE, height=300,
                      margin=dict(t=30, b=10, l=10, r=10),
                      legend=dict(orientation="h", y=1.18),
                      hovermode="closest")
    return fig
