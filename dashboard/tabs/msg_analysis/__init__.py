"""메시지 분석 패키지 (대분류: 메시지 분석).

페이지(중분류)
  page_reporting  : 보고주기 검증 — 전체 요약·MMSI별 위반 현황
  page_slotmap    : 프레임 슬롯맵 — 1분 단위 채널 A/B 슬롯맵 탐색
  (예정) page_intrusion : 슬롯 침범 — 예약 슬롯을 다른 선박이 차지한 이벤트
  (예정) page_loss      : 유실 분석 — 수신 못한 보고의 통계·원인
  (예정) page_quality   : 데이터 품질 — 유령/손상 디코드 내역

공용 모듈
  logic.py    : 검증 로직(streamlit 비의존) — 보고주기·슬롯체인 판정
  data.py     : 프리컴퓨트 + parquet 디스크 캐시 + 프레임 인덱스
  charts.py   : plotly 차트
  controls.py : 공용 임계값 슬라이더
"""
