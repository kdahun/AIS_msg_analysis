"""원문 적재에 쓰는 SQL 문장 모음 (ais_messages / ais_fsr).

load_ais_raw.py 는 '무엇을 언제 실행할지'만 담당하고, '어떤 SQL 인지'는 여기 모아둔다.
스키마를 확인하거나 바꿀 때 이 파일만 보면 된다.

타입별 테이블(ais_msg_*) 의 DDL 은 여기 없다. 그쪽은 TYPE_SCHEMAS 정의로부터
문자열을 조립해 만들기 때문에 parse_by_type.py 안에 있다.
"""
import rx_sites as S

T = S.RAW_TABLE          # "ais_messages" — 아래 SQL 에서 반복되므로 짧게 받는다


# ── ais_messages: 원문 한 줄(또는 VDM+VSI 한 쌍) = 한 행 ──────────────
CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {T} (
    -- 대리 키. ais_msg_* 20개가 source_id 로 이 값을 참조한다.
    -- 재적재하면 새로 발급되므로 타입별 테이블도 함께 다시 만들어야 한다.
    id        BIGSERIAL PRIMARY KEY,

    -- 원문 줄 맨 앞의 수신 시각. KST naive(시간대 정보 없음).
    -- TIMESTAMP(6) = 마이크로초까지 보존. 초당 28건이 들어와 초 단위로는 구분이 안 된다.
    recv_time TIMESTAMP(6),

    -- AIS 메시지 번호(1,3,5,18...). payload 첫 글자를 6bit 로 풀어 얻는다.
    -- 본격적인 필드는 ais_msg_* 에 있고, 여기 값은 빠른 필터링용이다.
    msg_type  SMALLINT,

    -- AIVDM 원문. 멀티파트는 파트들을 '|' 로 이어 붙여 한 행에 담는다.
    ais_raw   TEXT,

    -- 짝지은 AIVSI 원문(수신 품질). 짝을 못 찾으면 NULL.
    vsi_raw   TEXT,

    -- 수집 장소. 장소명이 아니라 rx_sites 의 정수 id 를 넣는다.
    -- NOT NULL 이라 장소를 선언하지 않으면 적재 자체가 실패한다(누락 방지).
    site_id   SMALLINT NOT NULL REFERENCES rx_sites(id),

    -- 출처 파일 경로('kmou/ais_2026-06-10_10.txt').
    -- 이 값으로 '어디까지 적재했는지'를 판단하므로 파일 단위 재실행이 안전해진다.
    src_file  TEXT NOT NULL,

    -- 둘 다 비어 있는 행은 의미가 없다. 파싱 버그를 DB 단에서 막는다.
    CONSTRAINT chk_not_both_null CHECK (ais_raw IS NOT NULL OR vsi_raw IS NOT NULL)
);

-- 시간 구간으로 자르는 조회용(대시보드 기간 필터).
CREATE INDEX IF NOT EXISTS idx_{T}_recv_time ON {T} (recv_time);

-- 특정 메시지 타입만 뽑을 때.
CREATE INDEX IF NOT EXISTS idx_{T}_msg_type  ON {T} (msg_type);

-- '이 장소의 이 기간' 조회용 복합 인덱스. 장소별 분석의 주된 접근 경로다.
CREATE INDEX IF NOT EXISTS idx_{T}_site_time ON {T} (site_id, recv_time);

-- 적재 시 'SELECT DISTINCT src_file' 과 파싱 시 'WHERE src_file = ANY(...)' 를 위한 것.
CREATE INDEX IF NOT EXISTS idx_{T}_src_file  ON {T} (src_file);
"""

# 원문 INSERT. VALUES %s 는 psycopg2 의 execute_values 가 실제 값 묶음으로 치환한다
# (행마다 INSERT 를 보내지 않고 한 번에 1만 행씩 전송 → 왕복 횟수를 줄인다).
# id 는 BIGSERIAL 이라 넣지 않는다.
INSERT = (f"INSERT INTO {T} "
          f"(recv_time, msg_type, ais_raw, vsi_raw, site_id, src_file) VALUES %s")


# ── ais_fsr: $AIFSR 한 줄 = 한 행 ────────────────────────────────────
# 선박 메시지가 아니라 '수신기 자신의 프레임 통계'라 ais_messages 와 섞지 않는다.
# 섞으면 위의 CHECK 제약에 걸리고 '총 메시지 수' 류의 집계가 전부 오염된다.
CREATE_FSR = """
CREATE TABLE IF NOT EXISTS ais_fsr (
    id           BIGSERIAL PRIMARY KEY,

    -- ais_messages 와 같은 방식. 어느 장소의 수신기가 낸 통계인지 구분한다.
    site_id      SMALLINT NOT NULL REFERENCES rx_sites(id),
    src_file     TEXT NOT NULL,

    -- 원문 줄 맨 앞의 시각(KST). 문장 자체의 시각(report_time)과 0~1초 차이가 난다.
    recv_time    TIMESTAMP(6) NOT NULL,

    -- 문장에 적힌 시각 = '지금 시작하는' 프레임의 시작점.
    -- 원문은 hhmmss.ss(UTC)뿐이라 날짜는 recv_time 에서 가져오고 +9시간 해 KST 로 맞춘다.
    report_time  TIMESTAMP(6) NOT NULL,

    -- report_time - 1분. FSR 의 주요 값들이 '이전 프레임'을 설명하므로,
    -- 메시지와 묶을 때 실제로 맞춰야 하는 시각은 이쪽이다.
    -- GENERATED ... STORED = DB 가 계산해 저장하는 컬럼(INSERT 목록에 넣지 않는다).
    -- 사람이 매번 1분을 빼는 실수를 원천 차단한다. PostgreSQL 12 이상 필요.
    frame        TIMESTAMP(6) GENERATED ALWAYS AS
                     (report_time - interval '1 minute') STORED,

    -- 보고 대상 채널. A/B 가 각각 나오므로 1분에 2행이 생긴다.
    channel      CHAR(1)  NOT NULL,

    -- ↓ 여기부터 5개는 '이전 프레임'(= frame 값의 1분간)을 설명한다
    rx_slots     SMALLINT,   -- 정상 수신된 메시지가 점유한 슬롯 수(자국 송신 제외)
    tx_slots     SMALLINT,   -- 자국 송신이 점유한 슬롯 수(수신 전용국이라 항상 0)
    crc_fail     SMALLINT,   -- 신호는 검출됐으나 CRC 검증에 실패한 건수
    noise_dbm    SMALLINT,   -- 평균 Noise Level(dBm, 음수). 측정 불가 시 NULL
    strong_slots SMALLINT,   -- 평균 잡음보다 10dB 이상 강한 신호가 잡힌 슬롯 수

    -- ↓ 이 2개만 '현재 프레임'(= report_time 값의 1분간)을 설명한다
    ext_res      SMALLINT,   -- 외부 무선국의 슬롯 예약 수(FATDMA 포함, 자국 제외)
    own_res      SMALLINT,   -- 자국이 예약한 슬롯 수(수신 전용국이라 항상 0)

    -- 파싱을 잘못했더라도 되짚을 수 있도록 원문 문장을 그대로 보관한다.
    fsr_raw      TEXT NOT NULL,

    -- 분·채널당 1건이 규칙이므로 이 셋의 조합이 자연키가 된다.
    -- channel 하나가 UNIQUE 인 게 아니라 셋이 '모두' 같을 때만 중복으로 본다.
    -- 정상적인 A/B 2행은 channel 이 달라 걸리지 않는다.
    UNIQUE (site_id, channel, report_time)
);

-- 메시지와 묶을 때 쓰는 조인 키 3개를 그대로 인덱스로 만든다.
CREATE INDEX IF NOT EXISTS idx_ais_fsr_join ON ais_fsr (site_id, channel, frame);
"""

# FSR INSERT. frame 은 생성 컬럼이라 컬럼 목록에서 빠진다.
#
# ON CONFLICT ... DO NOTHING: 위 UNIQUE 에 걸리면 에러 대신 그 행만 건너뛴다.
# 걸리는 경우는 같은 내용이 두 경로로 들어올 때뿐이다(파일명만 다른 사본,
# 시간대가 겹치는 파일 등). 그대로 두면 FSR 이 2행이 되어 LEFT JOIN 시
# 그 분의 메시지 행이 배로 불어난다 — 평균은 멀쩡한데 건수·합계만 틀려 발견이 어렵다.
# 적재를 통째로 실패시키는 대신 무시하고, 무시된 건수만 로그로 보고한다.
INSERT_FSR = """
INSERT INTO ais_fsr (site_id, src_file, recv_time, report_time, channel,
                     rx_slots, tx_slots, crc_fail, ext_res, own_res,
                     noise_dbm, strong_slots, fsr_raw) VALUES %s
ON CONFLICT (site_id, channel, report_time) DO NOTHING
"""


# ── 구 스키마 업그레이드 ─────────────────────────────────────────────
# site_id/src_file 이 없던 시절의 ais_messages 를 현재 형태로 맞춘다.
# 재적재로 테이블을 비운 직후에만 실행한다(행이 남아 있으면 NOT NULL 부여가 실패한다).
# DROP 후 재생성이 아니라 ALTER 라서 ais_msg_* 의 FK 제약이 그대로 살아남는다.
UPGRADE_SCHEMA = f"""
-- 열 추가. IF NOT EXISTS 라 이미 있으면 조용히 넘어간다(여러 번 실행해도 안전).
ALTER TABLE {T} ADD COLUMN IF NOT EXISTS site_id  SMALLINT REFERENCES rx_sites(id);
ALTER TABLE {T} ADD COLUMN IF NOT EXISTS src_file TEXT;

-- 앞으로 장소·출처 없는 행이 들어오지 못하게 막는다.
ALTER TABLE {T} ALTER COLUMN site_id  SET NOT NULL;
ALTER TABLE {T} ALTER COLUMN src_file SET NOT NULL;

-- 새 열에 대응하는 인덱스. CREATE_TABLE 쪽과 같은 이름이라 중복 생성되지 않는다.
CREATE INDEX IF NOT EXISTS idx_{T}_site_time ON {T} (site_id, recv_time);
CREATE INDEX IF NOT EXISTS idx_{T}_src_file  ON {T} (src_file);
"""


# ── 조회 ────────────────────────────────────────────────────────────
# 이미 적재된 파일 목록. 이 집합에 없는 파일만 새로 넣는다(파일 단위 멱등성).
SELECT_LOADED_FILES = f"SELECT DISTINCT src_file FROM {T}"

# 테이블 존재 여부. 없으면 NULL 을 돌려준다(예외가 아니라 값으로 받는다).
SELECT_TABLE_EXISTS = "SELECT to_regclass(%s)"

# 현재 컬럼 목록. site_id/src_file 유무로 구 스키마인지 판별한다.
SELECT_COLUMNS = ("SELECT column_name FROM information_schema.columns "
                  "WHERE table_name = %s")

# src_file 이 비어 있는 행 수. 0 이 아니면 구 스키마 잔재가 남은 것이라
# 파일 단위 판정이 어긋나 중복 적재된다 → 업데이트 모드를 중단시킨다.
COUNT_NULL_SRC_FILE = f"SELECT count(*) FROM {T} WHERE src_file IS NULL"

# ais_messages 를 FK 로 참조하는 테이블 목록.
# TRUNCATE 전에 무엇이 함께 비워지는지 확인해 로그에 알리려고 조회한다.
SELECT_DEPENDENTS = """
SELECT DISTINCT c.conrelid::regclass::text
  FROM pg_constraint c
 WHERE c.contype = 'f' AND c.confrelid = %s::regclass
 ORDER BY 1
"""

# DROP/TRUNCATE 는 ACCESS EXCLUSIVE 락이 필요하다. 대시보드 등이 테이블을 잡고 있으면
# 기본값(무제한)에서는 아무 메시지 없이 영원히 멈춘다. 30초 뒤 에러로 끝내게 한다.
SET_LOCK_TIMEOUT = "SET lock_timeout = '30s'"

# 원문 비우기. ais_msg_* 가 FK 로 물려 있어 CASCADE 없이는 거부된다.
# (호출 전에 타입 테이블을 DROP 하므로 실제로 CASCADE 가 지울 대상은 없다.)
TRUNCATE_RAW = f"TRUNCATE {T} CASCADE"

# ais_fsr 은 ais_messages 를 참조하지 않아 위 CASCADE 로 안 비워진다. 따로 비운다.
TRUNCATE_FSR = "TRUNCATE ais_fsr"


# ── 결과 요약 ───────────────────────────────────────────────────────
# 장소별 행수·파일수·수집 시작/종료 시각.
# 기간을 테이블에 저장하지 않고 여기서 min/max 로 유도한다(항상 실제 데이터와 일치).
SUMMARY_SITES = f"""
SELECT s.code, s.name, count(*), count(DISTINCT m.src_file),
       min(m.recv_time), max(m.recv_time)
  FROM {T} m JOIN rx_sites s ON s.id = m.site_id
 GROUP BY s.code, s.name
 ORDER BY 5
"""

# 장소별 FSR 현황. count(DISTINCT frame) 은 A/B 2행을 1분으로 세기 위한 것.
SUMMARY_FSR = """
SELECT s.code, count(*), count(DISTINCT f.frame),
       min(f.frame), max(f.frame), round(avg(f.noise_dbm), 1)
  FROM ais_fsr f JOIN rx_sites s ON s.id = f.site_id
 GROUP BY s.code
 ORDER BY 4
"""

# 파싱 정합성 검증용. 타입별 테이블 합계와 비교해 디코딩 누락을 잡는다.
COUNT_DECODABLE = f"SELECT count(*) FROM {T} WHERE ais_raw IS NOT NULL"
