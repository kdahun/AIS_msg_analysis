"""AIS RSSI/SNR 분석 대시보드 — 엔트리포인트.

실행:  cd dashboard && streamlit run app.py

이 파일은 탭 등록/렌더링만 담당한다. 탭 추가는 tabs/ 폴더에서만 이뤄진다.
"""
import streamlit as st

st.set_page_config(page_title="AIS RSSI/SNR 분석", page_icon="📡", layout="wide")

from tabs import TABS  # noqa: E402  (set_page_config 이후 import)

st.title("📡 AIS RSSI / SNR 분석 대시보드")

tab_objs = st.tabs([mod.TITLE for mod in TABS])
for tab, mod in zip(tab_objs, TABS):
    with tab:
        mod.render()
