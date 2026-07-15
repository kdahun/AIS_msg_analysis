"""탭 레지스트리.

새 탭 추가 방법:
  1) tabs/ 에 파일을 만들고 `TITLE`(문자열)과 `render()`(함수)를 정의
  2) 아래 import 와 TABS 리스트에 한 줄 추가

app.py 는 이 목록만 보고 탭을 그리므로 수정할 필요가 없다.
"""
from . import rssi_snr_by_mmsi, rssi_snr_by_time, rssi_snr_by_message, signal_validity

TABS = [
    rssi_snr_by_mmsi,
    rssi_snr_by_time,
    rssi_snr_by_message,
    signal_validity,
]
