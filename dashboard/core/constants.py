"""공용 상수: 통합 뷰 이름, 메시지 타입 이름 맵."""

# RSSI/SNR 분석용 통합 뷰 (sql/create_vsi_view.sql 로 생성)
VIEW = "v_vsi"

# 원문 테이블 (source_id 로 조인)
RAW_TABLE = "ais_messages"

# AIS 메시지 타입 → 사람이 읽는 이름
MSG_NAMES = {
    1:  "Position Report Class A (SOTDMA)",
    2:  "Position Report Class A (Assigned)",
    3:  "Position Report Class A (ITDMA)",
    4:  "Base Station Report",
    5:  "Static and Voyage Related Data",
    6:  "Addressed Binary Message",
    7:  "Binary Acknowledge",
    8:  "Binary Broadcast Message",
    9:  "Standard SAR Aircraft Position",
    10: "UTC/Date Inquiry",
    11: "UTC/Date Response",
    12: "Addressed Safety Message",
    13: "Safety Acknowledge",
    14: "Safety Broadcast Message",
    15: "Interrogation",
    18: "Standard Class B CS Position Report",
    19: "Extended Class B CS Position Report",
    20: "Data Link Management",
    21: "Aid-to-Navigation Report",
    24: "Static Data Report",
}


def msg_label(msg_type):
    """예: '1 - Position Report Class A (SOTDMA)'"""
    name = MSG_NAMES.get(msg_type, f"Type {msg_type}")
    return f"{msg_type} - {name}"


# ── 신호 유효성(위치 기반) 분석용 ── [폐기 예정] ────────────────
# 수신국 좌표는 이제 DB 의 rx_sites 테이블에 장소별로 들어 있고,
# ais_messages.site_id 로 행마다 어느 장소인지 알 수 있다.
# 메시지 분석(tabs/msg_analysis)은 이미 그쪽으로 옮겼다.
#
# 아래 상수는 core/queries.load_dynamic_positions 와 tabs/signal_validity 만
# 아직 쓰고 있다. 그 둘을 장소별 처리로 바꾸면서 함께 지운다.
#   · RX_LAT/RX_LON : 해양대 좌표만 있어 부산역 구간의 거리가 전부 틀리게 나온다
#   · UNIV_START    : 부산역 좌표를 몰라 제외하던 필터. 이제 아니까 불필요하다
#                     (지금은 이 필터 때문에 분석 대상이 전체의 1/4 로 줄어 있다)
RX_LAT = 35.0805
RX_LON = 129.0886
UNIV_START = "2026-06-10 10:33:23"
