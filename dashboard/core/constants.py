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
