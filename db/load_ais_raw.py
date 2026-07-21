"""원문 txt → ais_messages(원문) + ais_fsr(수신기 통계) + ais_msg_*(타입별 파싱).

적재부터 파싱까지 한 번에, 하나의 트랜잭션으로 끝낸다. 모드는 두 가지뿐이다.

  ① 업데이트(기본)   python db/load_ais_raw.py
     아직 안 들어간 파일만 INSERT 하고, 그 파일에서 온 행만 파싱한다.
     기존 행의 id 는 그대로. 새 장소를 추가하거나 다음 수집분을 붙일 때 쓴다.

  ② 전체 재적재      python db/load_ais_raw.py --rebuild
     전부 비우고 처음부터 다시 넣고 전량 재파싱한다. 스키마가 바뀌었을 때 쓴다.

  보조:  --site <코드>   업데이트 모드에서 특정 장소만 (--rebuild 와 함께 못 쓴다)
         --no-parse      적재만 하고 타입별 파싱은 건너뛴다

새 데이터를 넣는 절차
  1) AIS_실해역_데이터/<장소코드>/ 폴더를 만들고 원문 txt 를 넣는다
  2) AIS_실해역_데이터/sites.yaml 에 장소(code/name/lat/lon)를 추가한다
  3) python db/load_ais_raw.py

파싱을 여기 묶은 이유
  ais_msg_* 는 ais_messages(id) 를 FK 로 참조한다. 원문을 다시 넣으면 id 가 새로
  발급되므로 재파싱이 강제되는데, 이게 별도 노트북으로 갈라져 있으면 한쪽만 돌린 채
  분석에 들어가는 사고가 난다. 그래서 같은 명령·같은 트랜잭션에 묶었다.
"""
import argparse
import datetime
import time
from collections import defaultdict, deque, Counter

from psycopg2.extras import execute_values

import parse_by_type as P
import rx_sites as S

CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {S.RAW_TABLE} (
    id        BIGSERIAL PRIMARY KEY,
    recv_time TIMESTAMP(6),
    msg_type  SMALLINT,
    ais_raw   TEXT,
    vsi_raw   TEXT,
    site_id   SMALLINT NOT NULL REFERENCES rx_sites(id),
    src_file  TEXT NOT NULL,
    CONSTRAINT chk_not_both_null CHECK (ais_raw IS NOT NULL OR vsi_raw IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_{S.RAW_TABLE}_recv_time ON {S.RAW_TABLE} (recv_time);
CREATE INDEX IF NOT EXISTS idx_{S.RAW_TABLE}_msg_type  ON {S.RAW_TABLE} (msg_type);
CREATE INDEX IF NOT EXISTS idx_{S.RAW_TABLE}_site_time ON {S.RAW_TABLE} (site_id, recv_time);
CREATE INDEX IF NOT EXISTS idx_{S.RAW_TABLE}_src_file  ON {S.RAW_TABLE} (src_file);
"""

INSERT = (f"INSERT INTO {S.RAW_TABLE} "
          f"(recv_time, msg_type, ais_raw, vsi_raw, site_id, src_file) VALUES %s")

# $AIFSR — 선박 메시지가 아니라 '수신기 자신의 프레임 통계'라 별도 테이블에 둔다.
# ais_messages 에 섞으면 CHECK 제약에 걸리고 '총 메시지 수' 류의 집계가 전부 오염된다.
#
# 시각 규칙 (분석 시 반드시 이해할 것)
#   report_time : 문장이 조립된 시각 = '지금 시작하는' 프레임의 시작점
#   frame       : report_time - 1분. 아래 '이전 프레임' 값들이 설명하는 구간이며
#                 대시보드의 frame(vsi_time 을 분으로 내림) 과 같은 값이다.
#                 → 메시지와의 조인은 항상 frame 으로 한다.
# 두 시각 모두 KST(naive). ais_messages.recv_time 과 축을 맞춘다(원문은 UTC).
CREATE_FSR = """
CREATE TABLE IF NOT EXISTS ais_fsr (
    id           BIGSERIAL PRIMARY KEY,
    site_id      SMALLINT NOT NULL REFERENCES rx_sites(id),
    src_file     TEXT NOT NULL,
    recv_time    TIMESTAMP(6) NOT NULL,     -- 원문 라인 타임스탬프(KST)
    report_time  TIMESTAMP(6) NOT NULL,     -- 문장 시각(KST) = 현재 프레임 시작
    frame        TIMESTAMP(6) GENERATED ALWAYS AS
                     (report_time - interval '1 minute') STORED,
    channel      CHAR(1)  NOT NULL,
    -- ↓ 이전 프레임(= frame) 을 설명하는 값들
    rx_slots     SMALLINT,                  -- 수신 메시지가 점유한 슬롯 수(자국 송신 제외)
    tx_slots     SMALLINT,                  -- 자국 송신이 점유한 슬롯 수(수신전용국이면 0)
    crc_fail     SMALLINT,                  -- CRC 실패 건수(신호는 잡혔으나 디코딩 실패)
    noise_dbm    SMALLINT,                  -- 평균 잡음(dBm, 항상 음수. 측정불가 시 NULL)
    strong_slots SMALLINT,                  -- 잡음 대비 10dB 이상 수신된 슬롯 수
    -- ↓ 현재 프레임(= report_time) 을 설명하는 값들
    ext_res      SMALLINT,                  -- 외부 슬롯 예약(FATDMA 포함, 자국 제외)
    own_res      SMALLINT,                  -- 자국 슬롯 예약
    fsr_raw      TEXT NOT NULL,
    -- 분·채널당 1건이 규칙. 중복 적재 방지이자 그 규칙의 강제.
    UNIQUE (site_id, channel, report_time)
);
CREATE INDEX IF NOT EXISTS idx_ais_fsr_join ON ais_fsr (site_id, channel, frame);
"""

# ON CONFLICT DO NOTHING: 같은 (장소,채널,시각) 이 이미 있으면 조용히 건너뛴다.
# 정상적인 분당 2건(A/B)은 channel 이 달라 충돌하지 않는다. 걸리는 건 같은 내용이
# 두 번 들어오는 경우뿐이며(파일명만 다른 사본, 시간대가 겹치는 파일 등), 그대로 두면
# LEFT JOIN 시 메시지 행이 배로 불어난다. 적재를 실패시키는 대신 무시하고 건수만 보고한다.
INSERT_FSR = """
INSERT INTO ais_fsr (site_id, src_file, recv_time, report_time, channel,
                     rx_slots, tx_slots, crc_fail, ext_res, own_res,
                     noise_dbm, strong_slots, fsr_raw) VALUES %s
ON CONFLICT (site_id, channel, report_time) DO NOTHING
"""

# 재적재 시: 구 스키마(site_id/src_file 없음) 테이블을 비운 뒤 현재 스키마로 맞춘다.
# 테이블을 DROP 하지 않으므로 ais_msg_* 의 FK 제약이 그대로 살아남는다.
UPGRADE_SCHEMA = f"""
ALTER TABLE {S.RAW_TABLE} ADD COLUMN IF NOT EXISTS site_id  SMALLINT REFERENCES rx_sites(id);
ALTER TABLE {S.RAW_TABLE} ADD COLUMN IF NOT EXISTS src_file TEXT;
ALTER TABLE {S.RAW_TABLE} ALTER COLUMN site_id  SET NOT NULL;
ALTER TABLE {S.RAW_TABLE} ALTER COLUMN src_file SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_{S.RAW_TABLE}_site_time ON {S.RAW_TABLE} (site_id, recv_time);
CREATE INDEX IF NOT EXISTS idx_{S.RAW_TABLE}_src_file  ON {S.RAW_TABLE} (src_file);
"""


# ── 파싱·페어링 (ais_to_db.ipynb 와 동일 로직) ────────────────────────
def decode_msg_type(payload):
    """AIVDM payload 첫 글자 → AIS 메시지 번호(6bit)."""
    try:
        v = ord(payload[0]) - 48
        if v > 39:
            v -= 8
        return v
    except Exception:
        return None


def reformat_ts(s):
    """'20260610 11:00:02.0030' -> '2026-06-10 11:00:02.0030' (Postgres 캐스팅용)."""
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[9:]}"


def _int(s):
    """FSR 수치 필드 → int. 스펙상 측정 불가 시 빈 칸으로 온다."""
    s = s.strip()
    return int(s) if s else None


def parse_fsr(ts, msg):
    """'$AIFSR,0,085700.00,A,556,0,69,572,0,-102,707*3D' → INSERT_FSR 용 튜플 뒷부분.

    반환: (recv_time, report_time, channel, rx, tx, crc, ext, own, noise, strong, raw)
          형식이 어긋나면 None.

    FSR 은 hhmmss.ss(UTC)만 있고 날짜가 없다. 날짜는 라인 타임스탬프(KST)에서 가져오되,
    UTC 자정(=KST 09:00)을 넘는 순간 하루가 어긋나므로 12시간 이상 벌어지면 ±1일 보정한다.
    (실측 오차는 +0~1초. 현재 데이터에는 이 경계가 장소 이동 갭 안에 들어가 사례가 없어
     검증되지 않은 경로다 — 그래서 더더욱 코드로 막아둔다.)
    """
    body = msg.split("*")[0]
    p = body.split(",")
    if len(p) != 11 or not p[2] or p[3] not in ("A", "B"):
        return None
    try:
        utc_tod = int(p[2][0:2]) * 3600 + int(p[2][2:4]) * 60 + float(p[2][4:] or 0)
    except ValueError:
        return None

    kst_tod = (utc_tod + 9 * 3600) % 86400          # 원문은 UTC, DB 축은 KST
    report = (datetime.datetime.strptime(ts[:8], "%Y%m%d")
              + datetime.timedelta(seconds=kst_tod))
    line = datetime.datetime.strptime(ts[:17], "%Y%m%d %H:%M:%S")
    drift = (report - line).total_seconds()
    if drift > 43200:
        report -= datetime.timedelta(days=1)
    elif drift < -43200:
        report += datetime.timedelta(days=1)

    return (reformat_ts(ts), report, p[3],
            _int(p[4]), _int(p[5]), _int(p[6]),      # rx_slots, tx_slots, crc_fail
            _int(p[7]), _int(p[8]),                  # ext_res, own_res
            _int(p[9]), _int(p[10]),                 # noise_dbm, strong_slots
            msg)


def pair_file(path):
    """한 파일을 seq_id FIFO 로 페어링하고, 같은 패스에서 $AIFSR 도 뽑는다.

    AIVDM 과 AIVSI 는 타임스탬프가 아니라 seq_id 로 묶는다. 파일에 따라 VDM→VSI 순서도
    VSI→VDM 역순도 있고, 멀티파트는 part1→VSI→part2 처럼 끼기도 해서 타임스탬프
    매칭은 깨지기 때문이다.

    반환: (records, fsr, stats)
      records: [(recv_time_str, msg_type, ais_raw|None, vsi_raw|None), ...]
      fsr    : parse_fsr() 튜플 리스트
    """
    frag     = defaultdict(list)    # seq -> 조립 중인 멀티파트 파트들
    pend_vdm = defaultdict(deque)   # seq -> VSI 를 기다리는 조립완료 VDM
    pend_vsi = defaultdict(deque)   # seq -> VDM 을 기다리는 VSI
    records  = []
    fsr      = []
    stats = dict(vdm_msgs=0, vsi=0, pairs=0, ignored=0, fsr=0, fsr_bad=0)

    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.rstrip("\n")
            if "\t" not in ln:
                continue
            ts, msg = ln.split("\t", 1)
            head = msg.split(",", 1)[0]

            if head == "!AIVDM":
                p = msg.split(",")
                try:
                    total, pnum, seq = int(p[1]), int(p[2]), p[3]
                except (ValueError, IndexError):
                    continue
                frag[seq].append((ts, msg))
                if pnum == total:                      # 멀티파트 조립 완료
                    buf = frag.pop(seq)
                    combined = "|".join(m for _, m in buf)
                    first_ts = buf[0][0]
                    mt = decode_msg_type(buf[0][1].split(",")[5])
                    stats["vdm_msgs"] += 1
                    if pend_vsi[seq]:                   # 먼저 온 VSI 와 매칭
                        _, vmsg = pend_vsi[seq].popleft()
                        records.append((reformat_ts(first_ts), mt, combined, vmsg))
                        stats["pairs"] += 1
                    else:
                        pend_vdm[seq].append((first_ts, mt, combined))

            elif head == "$AIVSI":
                stats["vsi"] += 1
                p = msg.split(",")
                seq = p[2] if len(p) > 2 else ""
                if pend_vdm[seq]:                       # 먼저 온 VDM 과 매칭
                    dts, dmt, dcomb = pend_vdm[seq].popleft()
                    records.append((reformat_ts(dts), dmt, dcomb, msg))
                    stats["pairs"] += 1
                else:
                    pend_vsi[seq].append((ts, msg))

            elif head == "$AIFSR":
                if (rec := parse_fsr(ts, msg)) is None:
                    stats["fsr_bad"] += 1              # 형식 이상 — 조용히 넘기지 않는다
                else:
                    fsr.append(rec)
                    stats["fsr"] += 1

            else:
                stats["ignored"] += 1                  # $PSTT/$AIALR/$AIADS 등 무시

    # 짝을 못 찾은 잔여분도 누락 없이 저장
    vdm_only = [(reformat_ts(t), mt, c, None) for d in pend_vdm.values() for (t, mt, c) in d]
    vsi_only = [(reformat_ts(t), None, None, m) for d in pend_vsi.values() for (t, m) in d]
    records.extend(vdm_only)
    records.extend(vsi_only)
    stats["vdm_only"]   = len(vdm_only)
    stats["vsi_only"]   = len(vsi_only)
    stats["incomplete"] = sum(len(v) for v in frag.values())   # 미완성 멀티파트
    return records, fsr, stats


# ── 적재 ────────────────────────────────────────────────────────────
def _dependents(cur) -> list[str]:
    """ais_messages(id) 를 FK 로 참조하는 테이블 목록 — TRUNCATE CASCADE 가 함께 비운다."""
    cur.execute("""
        SELECT DISTINCT c.conrelid::regclass::text
          FROM pg_constraint c
         WHERE c.contype = 'f' AND c.confrelid = %s::regclass
         ORDER BY 1""", (S.RAW_TABLE,))
    return [r[0] for r in cur.fetchall()]


def _prepare(cur, rebuild: bool) -> None:
    """스키마를 현재 형태로 맞추고, 재적재면 기존 데이터를 비운다."""
    cur.execute("SELECT to_regclass(%s)", (S.RAW_TABLE,))
    exists = cur.fetchone()[0] is not None
    if not exists:
        cur.execute(CREATE_TABLE)
        cur.execute(CREATE_FSR)
        return

    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s", (S.RAW_TABLE,))
    cols = {r[0] for r in cur.fetchall()}
    legacy = bool({"site_id", "src_file"} - cols)

    if rebuild:
        # 타입 테이블을 먼저 DROP 해 FK 를 걷어낸다. 스키마 정의가 바뀌었을 때도
        # 그대로 반영되므로 TRUNCATE 로 비우는 것보다 안전하다.
        dep = _dependents(cur)
        P.drop_tables(cur)
        cur.execute(f"TRUNCATE {S.RAW_TABLE} CASCADE")
        print(f"비움: {S.RAW_TABLE}" + (f" + 파싱 테이블 {len(dep)}개" if dep else ""))
        if legacy:
            cur.execute(UPGRADE_SCHEMA)          # 빈 테이블이라 NOT NULL 부여가 안전
            print("스키마 갱신: site_id / src_file 열 추가")
        # ais_fsr 은 ais_messages 를 참조하지 않으므로 CASCADE 로 안 비워진다. 직접 비운다.
        cur.execute(CREATE_FSR)
        cur.execute("TRUNCATE ais_fsr")
        return

    # 업데이트 모드: 구 스키마 위에서는 돌 수 없다.
    # 파일별 멱등성이 DELETE WHERE src_file=... 에 기대는데, src_file 이 없는 기존 행은
    # 그 DELETE 로 안 지워져 전체가 중복 적재된다.
    if legacy:
        raise SystemExit(
            f"'{S.RAW_TABLE}' 가 구 스키마입니다(site_id/src_file 없음).\n"
            f"이 상태에서 업데이트하면 기존 행과 중복됩니다.\n"
            f"→ `python db/load_ais_raw.py --rebuild` 로 전체 재적재하세요.")
    cur.execute(f"SELECT count(*) FROM {S.RAW_TABLE} WHERE src_file IS NULL")
    if (n := cur.fetchone()[0]):
        raise SystemExit(f"src_file 이 비어 있는 행 {n:,}개가 있습니다 — 중복 위험.\n"
                         f"→ `python db/load_ais_raw.py --rebuild` 로 전체 재적재하세요.")


def load(sites: list[str] | None = None, rebuild: bool = False,
         no_parse: bool = False) -> None:
    conn = S.connect()
    conn.autocommit = False
    try:
        site_id = S.ensure_rx_sites(conn)
        if sites:
            if unknown := set(sites) - set(site_id):
                raise ValueError(f"sites.yaml 에 없는 장소: {sorted(unknown)}")
            site_id = {c: i for c, i in site_id.items() if c in sites}

        loaded = []                              # 이번에 적재한 src_file — 파싱 범위가 된다
        with conn.cursor() as cur:
            _prepare(cur, rebuild)
            cur.execute(CREATE_FSR)              # 업데이트 모드에서 처음 만들어지는 경우
            P.create_tables(cur)
            cur.execute(f"SELECT DISTINCT src_file FROM {S.RAW_TABLE}")
            already = {r[0] for r in cur.fetchall()}

            agg, t0 = Counter(), time.time()
            for code in sorted(site_id):
                for fp in S.site_files(code):
                    rel = f"{code}/{fp.name}"
                    if rel in already:
                        print(f"{rel:42s} 건너뜀 (이미 적재됨)")
                        continue

                    records, fsr, stats = pair_file(fp)
                    rows = [(t, mt, a, v, site_id[code], rel) for t, mt, a, v in records]
                    execute_values(cur, INSERT, rows, page_size=10000)
                    dup = 0
                    if fsr:
                        # FSR 은 파일당 최대 120건이라 한 페이지에 다 들어간다
                        # → rowcount 가 실제 INSERT 된 행수와 일치한다.
                        execute_values(cur, INSERT_FSR,
                                       [(site_id[code], rel, *r) for r in fsr],
                                       page_size=5000)
                        dup = len(fsr) - cur.rowcount

                    loaded.append(rel)
                    agg["rows"] += len(rows)
                    agg["files"] += 1
                    agg["fsr_dup"] += dup
                    for k in ("vdm_msgs", "vsi", "pairs", "vdm_only", "vsi_only",
                              "incomplete", "fsr", "fsr_bad"):
                        agg[k] += stats[k]
                    print(f"{rel:42s} 적재 {len(rows):7,d}"
                          f"  pairs={stats['pairs']:,} vdm_only={stats['vdm_only']}"
                          f" vsi_only={stats['vsi_only']} incomplete={stats['incomplete']}"
                          f" fsr={stats['fsr']}"
                          + (f" fsr_이상={stats['fsr_bad']}" if stats["fsr_bad"] else "")
                          + (f" fsr_중복무시={dup}" if dup else ""))

        print(f"\n파일 {agg['files']}개 / {agg['rows']:,}행 적재 ({time.time() - t0:.1f}s)")
        if agg["files"]:
            print(dict(agg))
        if agg["fsr_dup"]:
            print(f"⚠ FSR 중복 {agg['fsr_dup']}건 무시됨 — 같은 (장소,채널,시각)이 "
                  f"이미 있습니다. 내용이 겹치는 원문 파일이 없는지 확인하세요.")

        # 타입별 파싱. 재적재면 전량, 업데이트면 이번에 들어온 파일만.
        if no_parse:
            print("\n타입별 파싱 건너뜀(--no-parse) — ais_msg_* 가 원문과 어긋난 상태입니다.")
        elif rebuild or loaded:
            print(f"\n타입별 파싱 ({'전량' if rebuild else f'신규 {len(loaded)}개 파일'})")
            P.parse(conn, None if rebuild else loaded)
        else:
            print("\n새로 적재된 파일이 없어 파싱도 건너뜁니다.")

        conn.commit()
        summary(conn)
    except Exception:
        conn.rollback()
        print("\n실패 — 롤백했습니다. DB 는 실행 전 상태 그대로입니다.")
        raise
    finally:
        conn.close()


def summary(conn) -> None:
    """장소별 적재 현황(수집 시작/종료 시각은 여기서 유도된다)."""
    with conn.cursor() as cur:
        cur.execute(f"""SELECT s.code, s.name, count(*), count(DISTINCT m.src_file),
                               min(m.recv_time), max(m.recv_time)
                          FROM {S.RAW_TABLE} m JOIN rx_sites s ON s.id = m.site_id
                         GROUP BY s.code, s.name ORDER BY 5""")
        print("\n장소별 현황 (수집 시작/종료 시각은 여기서 유도된다)")
        for code, name, n, nf, t0, t1 in cur.fetchall():
            print(f"  {code:15s} {name:22s} {n:9,}행 / 파일 {nf:2d}개   {t0} ~ {t1}")

        # FSR 은 커버리지가 들쭉날쭉해서(장소·시간대별로 아예 없기도 함) 따로 보여준다.
        cur.execute("""SELECT s.code, count(*), count(DISTINCT f.frame),
                              min(f.frame), max(f.frame), round(avg(f.noise_dbm), 1)
                         FROM ais_fsr f JOIN rx_sites s ON s.id = f.site_id
                        GROUP BY s.code ORDER BY 4""")
        print("\nFSR 현황 (분당 채널별 1건)")
        for code, n, nmin, t0, t1, noise in cur.fetchall():
            print(f"  {code:15s} {n:6,}행 / {nmin:5,}분   {t0} ~ {t1}   평균잡음 {noise} dBm")

        # 파싱 테이블 합계 — 원문의 ais_raw 보유 행수와 맞아야 정상이다.
        union = " UNION ALL ".join(f"SELECT '{t}' t, count(*) n FROM {t}" for t in P.all_tables())
        cur.execute(f"SELECT t, n FROM ({union}) x WHERE n > 0 ORDER BY n DESC")
        rows = cur.fetchall()
        cur.execute(f"SELECT count(*) FROM {S.RAW_TABLE} WHERE ais_raw IS NOT NULL")
        n_raw = cur.fetchone()[0]
        total = sum(n for _, n in rows)
        print(f"\n타입별 파싱: {total:,}행 / 원문 디코딩 대상 {n_raw:,}행"
              f"{'  ✓ 일치' if total == n_raw else '  ⚠ 불일치(디코딩 실패분 확인)'}")
        print("  " + ", ".join(f"{t.replace('ais_msg_', 'type ')}={n:,}" for t, n in rows))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", action="append", dest="sites",
                    help="특정 장소 코드만 적재 (여러 번 지정 가능)")
    ap.add_argument("--rebuild", action="store_true",
                    help="전부 비우고 처음부터 재적재 + 전량 재파싱")
    ap.add_argument("--no-parse", action="store_true", dest="no_parse",
                    help="적재만 하고 타입별 파싱은 건너뛴다")
    a = ap.parse_args()
    if a.rebuild and a.sites:
        ap.error("--rebuild 는 전체 재적재 전용입니다. --site 와 함께 쓸 수 없습니다.")
    load(sites=a.sites, rebuild=a.rebuild, no_parse=a.no_parse)
