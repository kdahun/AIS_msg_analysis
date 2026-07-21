"""AIS 분석 대시보드 — 엔트리포인트.

실행:  cd dashboard && streamlit run app.py

사이드바 내비게이션(st.navigation)으로 대분류/중분류를 구성한다.
st.tabs 와 달리 **선택된 페이지만 실행**되므로 페이지 이동·조작이 가볍다.
페이지 추가: tabs/ 에 TITLE + render() 모듈을 만들고 아래 _PAGES 에 등록.
"""
import streamlit as st

st.set_page_config(page_title="AIS 분석 대시보드", page_icon="📡", layout="wide")

from tabs import (  # noqa: E402  (set_page_config 이후 import)
    rssi_snr_by_mmsi, rssi_snr_by_time, rssi_snr_by_message, signal_validity,
)
from tabs.msg_analysis import (  # noqa: E402
    page_reporting, page_slotmap, page_intrusion, page_loss, page_quality,
)

_PAGES = {
    "📡 AIS RSSI/SNR 분석": [
        (rssi_snr_by_mmsi, "rssi-mmsi"),
        (rssi_snr_by_time, "rssi-time"),
        (rssi_snr_by_message, "rssi-message"),
        (signal_validity, "signal-validity"),
    ],
    "📋 메시지 분석": [
        (page_reporting, "msg-reporting"),
        (page_slotmap, "msg-slotmap"),
        (page_intrusion, "msg-intrusion"),
        (page_loss, "msg-loss"),
        (page_quality, "msg-quality"),
    ],
}

def _global_filters():
    """모든 페이지에 공통으로 걸리는 수집 장소 선택.

    두 장소는 수신국 위치·안테나·주변 지형이 달라 RSSI 절대값을 직접 비교할 수 없다.
    선택하지 않으면 전체를 보되, 장소별로 나눠 보고 싶을 때 여기서 좁힌다.
    (선택값은 session_state["global_sites"] 에 들어가고 core.queries 가 읽어 간다)
    """
    from core import queries  # DB 접속이 필요하므로 사용할 때 import

    with st.sidebar:
        st.divider()
        try:
            opts = queries.get_site_options()
        except Exception:
            return                       # DB 미접속 등 — 필터 없이 전체
        if opts.empty:
            return
        label = {int(r.site_id): f"{r.code}  ({int(r.n):,}건)" for r in opts.itertuples()}
        st.multiselect("수집 장소 (미선택 = 전체)", list(label), key="global_sites",
                       format_func=lambda i: label[i])


nav = {
    section: [st.Page(mod.render, title=mod.TITLE, url_path=url) for mod, url in mods]
    for section, mods in _PAGES.items()
}
page = st.navigation(nav)
_global_filters()
page.run()
