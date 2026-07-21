"""원문(ais_messages) → 메시지 타입별 테이블(ais_msg_*) 파싱.

ais_parse_by_type.ipynb 의 로직을 그대로 옮긴 것으로, load_ais_raw.py 가 적재 직후
같은 트랜잭션 안에서 호출한다. 적재와 파싱이 갈라져 있으면 한쪽만 돌린 채로
분석에 들어가는 사고가 나기 때문이다.

- 대상 19종 (1,3,4,5,6,7,8,9,10,11,12,13,14,15,18,19,20,21 + 24는 Part A/B 로 분리)
- radio 필드: sync_state 는 공통 분해. SOTDMA 상세(slot_timeout/sub_message)는 type 1,
  ITDMA 상세(slot_increment/num_slots/keep_flag)는 type 3 에서만 분해한다.
- 각 테이블은 source_id 로 ais_messages.id 를 참조한다.
"""
import enum
import time
from collections import defaultdict

from psycopg2.extras import execute_values
from pyais import decode

COMMON_COLS = [
    ("source_id", "BIGINT"),
    ("recv_time", "TIMESTAMP(6)"),
    ("vsi_ui", "SMALLINT"),
    ("vsi_link", "SMALLINT"),
    ("vsi_hour", "SMALLINT"),
    ("vsi_minute", "SMALLINT"),
    ("vsi_second", "NUMERIC(9,6)"),
    ("vsi_slot", "INTEGER"),
    ("vsi_rssi", "SMALLINT"),
    ("vsi_snr", "SMALLINT"),
]

_POS_A = [   # type 1 / 3 공통 베이스 (Position Report Class A)
    ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("status", "SMALLINT"), ("turn", "REAL"),
    ("speed", "REAL"), ("accuracy", "BOOLEAN"), ("lon", "DOUBLE PRECISION"), ("lat", "DOUBLE PRECISION"),
    ("course", "REAL"), ("heading", "SMALLINT"), ("second", "SMALLINT"), ("maneuver", "SMALLINT"),
    ("raim", "BOOLEAN"), ("radio", "INTEGER"),
]
_BASE_TIME = [  # type 4 / 11 공통 (Base Station Report / UTC-date response)
    ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("year", "SMALLINT"), ("month", "SMALLINT"),
    ("day", "SMALLINT"), ("hour", "SMALLINT"), ("minute", "SMALLINT"), ("second", "SMALLINT"),
    ("accuracy", "BOOLEAN"), ("lon", "DOUBLE PRECISION"), ("lat", "DOUBLE PRECISION"),
    ("epfd", "SMALLINT"), ("raim", "BOOLEAN"), ("radio", "INTEGER"),
]
_ADDR_MSG = [   # type 6 / 12 공통 베이스 (addressed message)
    ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("seqno", "SMALLINT"), ("dest_mmsi", "INTEGER"),
    ("retransmit", "BOOLEAN"),
]
_MMSI4 = [  # type 7 / 13 공통 (binary/safety ack)
    ("repeat", "SMALLINT"), ("mmsi", "INTEGER"),
    ("mmsi1", "INTEGER"), ("mmsiseq1", "SMALLINT"), ("mmsi2", "INTEGER"), ("mmsiseq2", "SMALLINT"),
    ("mmsi3", "INTEGER"), ("mmsiseq3", "SMALLINT"), ("mmsi4", "INTEGER"), ("mmsiseq4", "SMALLINT"),
]

TYPE_SCHEMAS = {
    1: _POS_A + [("sync_state", "SMALLINT"), ("slot_timeout", "SMALLINT"), ("sub_message", "INTEGER")],
    3: _POS_A + [("sync_state", "SMALLINT"), ("slot_increment", "INTEGER"), ("num_slots", "SMALLINT"), ("keep_flag", "BOOLEAN")],
    4: _BASE_TIME + [("sync_state", "SMALLINT")],
    5: [
        ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("ais_version", "SMALLINT"), ("imo", "INTEGER"),
        ("callsign", "TEXT"), ("shipname", "TEXT"), ("ship_type", "SMALLINT"), ("to_bow", "SMALLINT"),
        ("to_stern", "SMALLINT"), ("to_port", "SMALLINT"), ("to_starboard", "SMALLINT"), ("epfd", "SMALLINT"),
        ("month", "SMALLINT"), ("day", "SMALLINT"), ("hour", "SMALLINT"), ("minute", "SMALLINT"),
        ("draught", "REAL"), ("destination", "TEXT"), ("dte", "BOOLEAN"),
    ],
    6: _ADDR_MSG + [("dac", "INTEGER"), ("fid", "INTEGER"), ("data", "BYTEA")],
    7: _MMSI4,
    8: [("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("dac", "INTEGER"), ("fid", "INTEGER"), ("data", "BYTEA")],
    9: [
        ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("alt", "INTEGER"), ("speed", "REAL"),
        ("accuracy", "BOOLEAN"), ("lon", "DOUBLE PRECISION"), ("lat", "DOUBLE PRECISION"), ("course", "REAL"),
        ("second", "SMALLINT"), ("reserved_1", "INTEGER"), ("dte", "BOOLEAN"), ("assigned", "BOOLEAN"),
        ("raim", "BOOLEAN"), ("radio", "INTEGER"), ("sync_state", "SMALLINT"),
    ],
    10: [("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("dest_mmsi", "INTEGER")],
    11: _BASE_TIME + [("sync_state", "SMALLINT")],
    12: _ADDR_MSG + [("text", "TEXT")],
    13: _MMSI4,
    14: [("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("text", "TEXT")],
    15: [
        ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("mmsi1", "INTEGER"), ("type1_1", "SMALLINT"),
        ("offset1_1", "INTEGER"), ("type1_2", "SMALLINT"), ("offset1_2", "INTEGER"), ("mmsi2", "INTEGER"),
        ("type2_1", "SMALLINT"), ("offset2_1", "INTEGER"),
    ],
    18: [
        ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("reserved_1", "INTEGER"), ("speed", "REAL"),
        ("accuracy", "BOOLEAN"), ("lon", "DOUBLE PRECISION"), ("lat", "DOUBLE PRECISION"), ("course", "REAL"),
        ("heading", "SMALLINT"), ("second", "SMALLINT"), ("reserved_2", "INTEGER"), ("cs", "BOOLEAN"),
        ("display", "BOOLEAN"), ("dsc", "BOOLEAN"), ("band", "BOOLEAN"), ("msg22", "BOOLEAN"),
        ("assigned", "BOOLEAN"), ("raim", "BOOLEAN"), ("radio", "INTEGER"), ("sync_state", "SMALLINT"),
    ],
    19: [
        ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("reserved_1", "INTEGER"), ("speed", "REAL"),
        ("accuracy", "BOOLEAN"), ("lon", "DOUBLE PRECISION"), ("lat", "DOUBLE PRECISION"), ("course", "REAL"),
        ("heading", "SMALLINT"), ("second", "SMALLINT"), ("reserved_2", "INTEGER"), ("shipname", "TEXT"),
        ("ship_type", "SMALLINT"), ("to_bow", "SMALLINT"), ("to_stern", "SMALLINT"), ("to_port", "SMALLINT"),
        ("to_starboard", "SMALLINT"), ("epfd", "SMALLINT"), ("raim", "BOOLEAN"), ("dte", "BOOLEAN"),
        ("assigned", "BOOLEAN"),
    ],
    20: [
        ("repeat", "SMALLINT"), ("mmsi", "INTEGER"),
        ("offset1", "INTEGER"), ("number1", "INTEGER"), ("timeout1", "INTEGER"), ("increment1", "INTEGER"),
        ("offset2", "INTEGER"), ("number2", "INTEGER"), ("timeout2", "INTEGER"), ("increment2", "INTEGER"),
        ("offset3", "INTEGER"), ("number3", "INTEGER"), ("timeout3", "INTEGER"), ("increment3", "INTEGER"),
        ("offset4", "INTEGER"), ("number4", "INTEGER"), ("timeout4", "INTEGER"), ("increment4", "INTEGER"),
    ],
    21: [
        ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("aid_type", "SMALLINT"), ("name", "TEXT"),
        ("accuracy", "BOOLEAN"), ("lon", "DOUBLE PRECISION"), ("lat", "DOUBLE PRECISION"),
        ("to_bow", "SMALLINT"), ("to_stern", "SMALLINT"), ("to_port", "SMALLINT"), ("to_starboard", "SMALLINT"),
        ("epfd", "SMALLINT"), ("second", "SMALLINT"), ("off_position", "BOOLEAN"), ("reserved_1", "INTEGER"),
        ("raim", "BOOLEAN"), ("virtual_aid", "BOOLEAN"), ("assigned", "BOOLEAN"),
        ("name_ext", "TEXT"), ("full_name", "TEXT"),
    ],
    "24a": [("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("partno", "SMALLINT"), ("shipname", "TEXT")],
    "24b": [
        ("repeat", "SMALLINT"), ("mmsi", "INTEGER"), ("partno", "SMALLINT"), ("ship_type", "SMALLINT"),
        ("vendorid", "TEXT"), ("model", "SMALLINT"), ("serial", "INTEGER"), ("callsign", "TEXT"),
        ("to_bow", "SMALLINT"), ("to_stern", "SMALLINT"), ("to_port", "SMALLINT"), ("to_starboard", "SMALLINT"),
    ],
}

_VSI_KEYS = ["vsi_ui", "vsi_link", "vsi_hour", "vsi_minute", "vsi_second",
             "vsi_slot", "vsi_rssi", "vsi_snr"]


def table_name(key) -> str:
    return f"ais_msg_{key}"


def all_tables() -> list[str]:
    return [table_name(k) for k in TYPE_SCHEMAS]


# ── DDL ─────────────────────────────────────────────────────────────
def drop_tables(cur) -> None:
    """타입 테이블 전부 DROP. ais_messages 를 향한 FK 도 함께 사라진다."""
    for tbl in all_tables():
        cur.execute(f"DROP TABLE IF EXISTS {tbl}")


def create_tables(cur) -> None:
    for key, cols in TYPE_SCHEMAS.items():
        tbl = table_name(key)
        col_defs = ",\n    ".join(f"{n} {t}" for n, t in (COMMON_COLS + cols))
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {tbl} (
                id BIGSERIAL PRIMARY KEY,
                {col_defs},
                FOREIGN KEY (source_id) REFERENCES ais_messages(id)
            );
            CREATE INDEX IF NOT EXISTS idx_{tbl}_recv_time ON {tbl} (recv_time);
            CREATE INDEX IF NOT EXISTS idx_{tbl}_mmsi      ON {tbl} (mmsi);
        """)


# ── 파싱 ────────────────────────────────────────────────────────────
def parse_vsi_fields(vsi):
    try:
        parts = vsi.split(",")
        toa = parts[3]
        return {
            "vsi_ui": int(parts[1]),
            "vsi_link": int(parts[2]),
            "vsi_hour": int(toa[0:2]),
            "vsi_minute": int(toa[2:4]),
            "vsi_second": float(toa[4:]),
            "vsi_slot": int(parts[4]),
            "vsi_rssi": int(parts[5]),
            "vsi_snr": int(parts[6].split("*")[0]),
        }
    except Exception:
        return {k: None for k in _VSI_KEYS}


def parse_comm_state(radio, msg_type):
    r = int(radio)
    sync_state = (r >> 17) & 0x3
    if msg_type == 1:
        return {"sync_state": sync_state, "slot_timeout": (r >> 14) & 0x7, "sub_message": r & 0x3FFF}
    if msg_type == 3:
        return {"sync_state": sync_state, "slot_increment": (r >> 5) & 0x1FFF,
                "num_slots": (r >> 2) & 0x7, "keep_flag": bool(r & 0x1)}
    return {"sync_state": sync_state}


def normalize(v):
    """pyais Enum(IntEnum/FloatEnum) 값을 순수 파이썬 primitive 로 변환."""
    return v.value if isinstance(v, enum.Enum) else v


def parse_row(source_id, recv_time, ais_raw, vsi_raw):
    """한 행(ais_messages) → (table_key, row_dict) 또는 실패 시 (None, 에러메시지)."""
    try:
        parts = [p.encode() for p in ais_raw.split("|")]
        decoded = {k: normalize(v) for k, v in decode(*parts).asdict().items()}
    except Exception as e:
        return None, str(e)

    msg_type = decoded.get("msg_type")
    if msg_type == 24:
        table_key = "24a" if decoded.get("partno") == 0 else "24b"
    else:
        table_key = msg_type

    schema = TYPE_SCHEMAS.get(table_key)
    if schema is None:
        return None, f"미지원 msg_type={msg_type}"

    row = {"source_id": source_id, "recv_time": recv_time}
    row.update(parse_vsi_fields(vsi_raw) if vsi_raw else {k: None for k in _VSI_KEYS})
    for col, _ in schema:
        row[col] = decoded.get(col)
    if msg_type in (1, 3, 4, 9, 11, 18) and "radio" in decoded:
        row.update(parse_comm_state(decoded["radio"], msg_type))
    return table_key, row


def parse(conn, src_files: list[str] | None = None) -> dict:
    """ais_messages 를 읽어 타입별 테이블에 적재.

    src_files 를 주면 그 파일들에서 온 행만 파싱한다(업데이트 모드).
    None 이면 전체(재적재 모드). ais_raw 가 없는 행(VSI 단독)은 디코딩 대상이 아니라 건너뛴다.
    """
    where, params = "ais_raw IS NOT NULL", []
    if src_files is not None:
        if not src_files:
            return dict(processed=0, inserted=0, failed=0, counts={})
        where += " AND src_file = ANY(%s)"
        params = [list(src_files)]

    col_order = {k: [c for c, _ in (COMMON_COLS + cols)] for k, cols in TYPE_SCHEMAS.items()}
    buffers, counts, failures = defaultdict(list), defaultdict(int), []
    t0, n = time.time(), 0

    def flush(key):
        if not buffers[key]:
            return
        cols = col_order[key]
        values = [tuple(r.get(c) for c in cols) for r in buffers[key]]
        with conn.cursor() as wcur:
            execute_values(wcur, f"INSERT INTO {table_name(key)} ({', '.join(cols)}) VALUES %s",
                           values, page_size=5000)
        counts[key] += len(buffers[key])
        buffers[key].clear()

    # 서버사이드 커서로 스트리밍 — 93만 행을 한 번에 메모리에 올리지 않는다.
    with conn.cursor(name="ais_reader") as read_cur:
        read_cur.itersize = 20000
        read_cur.execute(
            f"SELECT id, recv_time, ais_raw, vsi_raw FROM ais_messages WHERE {where} ORDER BY id",
            params or None)
        for source_id, recv_time, ais_raw, vsi_raw in read_cur:
            key, result = parse_row(source_id, recv_time, ais_raw, vsi_raw)
            n += 1
            if key is None:
                failures.append((source_id, result))
            else:
                buffers[key].append(result)
                if len(buffers[key]) >= 5000:
                    flush(key)
            if n % 200000 == 0:
                print(f"    파싱 진행 {n:,}행 ({time.time() - t0:.0f}s)")

    for key in list(buffers):
        flush(key)

    print(f"  파싱 {n:,}행 → 적재 {sum(counts.values()):,}행, "
          f"실패 {len(failures)}건 ({time.time() - t0:.0f}s)")
    if failures:
        print("    실패 샘플:", failures[:3])
    return dict(processed=n, inserted=sum(counts.values()),
                failed=len(failures), counts=dict(counts))
