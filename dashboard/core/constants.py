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


# ── 신호 유효성(위치 기반) 분석용 ──────────────────────────────
# 국립한국해양대학교 아치캠퍼스 (수신국 좌표, 사용자 제공)
RX_LAT = 35.0805
RX_LON = 129.0886

# 위 좌표가 유효한 구간의 시작 시각. 이전 구간은 "바다 근처 모텔"에서 수집되어
# 수신국 좌표를 모르므로 위치 기반 신호 검증 대상에서 제외한다.
UNIV_START = "2026-06-10 10:33:23"
