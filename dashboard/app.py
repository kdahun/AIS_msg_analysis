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
from tabs.msg_analysis import page_reporting, page_slotmap  # noqa: E402

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
    ],
}

nav = {
    section: [st.Page(mod.render, title=mod.TITLE, url_path=url) for mod, url in mods]
    for section, mods in _PAGES.items()
}
st.navigation(nav).run()
