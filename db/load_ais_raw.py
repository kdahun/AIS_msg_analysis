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
import schema as Q

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
    """ais_messages(id) 를 FK 로 참조하는 테이블 목록. 무엇이 함께 비워지는지 알리는 용도."""
    cur.execute(Q.SELECT_DEPENDENTS, (S.RAW_TABLE,))
    return [r[0] for r in cur.fetchall()]      # [('ais_msg_1',), ...] → ['ais_msg_1', ...]


def _prepare(cur, rebuild: bool) -> None:
    """스키마를 현재 형태로 맞추고, 재적재면 기존 데이터를 비운다."""
    # 테이블이 아예 없으면(=빈 DB) 만들기만 하고 끝. 비울 것도 검사할 것도 없다.
    cur.execute(Q.SELECT_TABLE_EXISTS, (S.RAW_TABLE,))
    if cur.fetchone()[0] is None:              # to_regclass 는 없으면 NULL 을 준다
        cur.execute(Q.CREATE_TABLE)
        cur.execute(Q.CREATE_FSR)
        return

    # 현재 컬럼 목록을 읽어 '구 스키마'(site_id/src_file 이 없던 시절) 인지 판별한다.
    cur.execute(Q.SELECT_COLUMNS, (S.RAW_TABLE,))
    cols = {r[0] for r in cur.fetchall()}
    legacy = bool({"site_id", "src_file"} - cols)

    if rebuild:
        dep = _dependents(cur)                 # 로그에 몇 개가 함께 비워지는지 알리려고 먼저 조회
        print(f"비우는 중... (파싱 테이블 {len(dep)}개 + {S.RAW_TABLE} + ais_fsr)")

        # 타입 테이블을 DROP 해 FK 를 걷어낸다(v_vsi 뷰도 이 안에서 함께 내려간다).
        # TRUNCATE 가 아니라 DROP 인 이유: TYPE_SCHEMAS 정의가 바뀌어도 그대로 반영된다.
        P.drop_tables(cur)
        cur.execute(Q.TRUNCATE_RAW)
        print(f"비움: {S.RAW_TABLE}" + (f" + 파싱 테이블 {len(dep)}개" if dep else ""))

        # NOT NULL 부여는 행이 남아 있으면 실패한다 → 반드시 비운 뒤에 실행한다.
        if legacy:
            cur.execute(Q.UPGRADE_SCHEMA)
            print("스키마 갱신: site_id / src_file 열 추가")

        # ais_fsr 은 ais_messages 를 참조하지 않아 위 CASCADE 로 안 비워진다.
        # 없을 수도 있으므로 만든 뒤 비운다(순서가 반대면 없는 테이블을 TRUNCATE 하게 된다).
        cur.execute(Q.CREATE_FSR)
        cur.execute(Q.TRUNCATE_FSR)
        return

    # ── 여기부터는 업데이트 모드 ──
    # 파일 단위 멱등성이 src_file 대조에 기대는데, 그 값이 없는 기존 행은
    # '아직 안 넣은 파일'로 오판돼 통째로 중복 적재된다. 그래서 아예 막는다.
    if legacy:
        raise SystemExit(
            f"'{S.RAW_TABLE}' 가 구 스키마입니다(site_id/src_file 없음).\n"
            f"이 상태에서 업데이트하면 기존 행과 중복됩니다.\n"
            f"→ `python db/load_ais_raw.py --rebuild` 로 전체 재적재하세요.")

    # 열은 있는데 값이 비어 있는 행이 남은 경우도 같은 이유로 위험하다.
    cur.execute(Q.COUNT_NULL_SRC_FILE)
    if (n := cur.fetchone()[0]):
        raise SystemExit(f"src_file 이 비어 있는 행 {n:,}개가 있습니다 — 중복 위험.\n"
                         f"→ `python db/load_ais_raw.py --rebuild` 로 전체 재적재하세요.")


def load(sites: list[str] | None = None, rebuild: bool = False,
         no_parse: bool = False) -> None:
    print(f"DB 접속: {S.DB['user']}@{S.DB['host']}:{S.DB['port']}/{S.DB['dbname']}")
    conn = S.connect()
    conn.autocommit = False                      # 전 과정을 한 트랜잭션으로 — 실패 시 통째로 롤백
    try:
        with conn.cursor() as c:
            c.execute(Q.SET_LOCK_TIMEOUT)        # 락 대기로 무한정 멈추는 것을 방지

        # sites.yaml → rx_sites 반영 후 {장소코드: id} 를 받는다. 이 id 가 모든 행에 박힌다.
        site_id = S.ensure_rx_sites(conn)
        print(f"장소 {len(site_id)}곳: {', '.join(sorted(site_id))}")

        # --site 가 주어졌으면 그 장소만 남긴다(오타는 여기서 걸러진다).
        if sites:
            if unknown := set(sites) - set(site_id):
                raise ValueError(f"sites.yaml 에 없는 장소: {sorted(unknown)}")
            site_id = {c: i for c, i in site_id.items() if c in sites}

        loaded = []                              # 이번에 적재한 src_file — 뒤에서 파싱 범위가 된다
        with conn.cursor() as cur:
            _prepare(cur, rebuild)               # 비우기/스키마 정리 (모드에 따라 갈림)
            cur.execute(Q.CREATE_FSR)            # 업데이트 모드에서 ais_fsr 이 처음 생기는 경우
            P.create_tables(cur)                 # 타입 테이블 20개 (있으면 그대로 둔다)
            P.create_view(cur)                   # 타입 테이블에 의존하는 v_vsi 되살리기

            # 이미 들어간 파일 목록. --rebuild 면 방금 비웠으니 빈 집합이 되고,
            # 결과적으로 아래 루프가 전체 파일을 적재하게 된다(별도 분기가 필요 없다).
            cur.execute(Q.SELECT_LOADED_FILES)
            already = {r[0] for r in cur.fetchall()}

            agg, t0 = Counter(), time.time()
            for code in sorted(site_id):                    # 장소 순회
                for fp in S.site_files(code):               # 그 장소 폴더의 txt 순회
                    rel = f"{code}/{fp.name}"               # DB 에 기록할 상대 경로
                    if rel in already:
                        print(f"{rel:42s} 건너뜀 (이미 적재됨)")
                        continue

                    # 파일 한 개를 파싱 — 원문 페어와 FSR 을 한 번에 얻는다.
                    records, fsr, stats = pair_file(fp)

                    # 파싱 결과에 장소·출처를 붙여 INSERT 할 튜플로 만든다.
                    rows = [(t, mt, a, v, site_id[code], rel) for t, mt, a, v in records]
                    execute_values(cur, Q.INSERT, rows, page_size=10000)

                    dup = 0
                    if fsr:
                        execute_values(cur, Q.INSERT_FSR,
                                       [(site_id[code], rel, *r) for r in fsr],
                                       page_size=5000)
                        # ON CONFLICT DO NOTHING 으로 걸러진 행수 = 보낸 수 - 실제 들어간 수.
                        # FSR 은 파일당 최대 120건이라 한 페이지에 다 들어가므로 rowcount 가 정확하다.
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

        # 타입별 파싱. None 을 넘기면 전량, 파일 목록을 넘기면 그 파일에서 온 행만.
        if no_parse:
            print("\n타입별 파싱 건너뜀(--no-parse) — ais_msg_* 가 원문과 어긋난 상태입니다.")
        elif rebuild or loaded:
            print(f"\n타입별 파싱 ({'전량' if rebuild else f'신규 {len(loaded)}개 파일'})")
            P.parse(conn, None if rebuild else loaded)
        else:
            print("\n새로 적재된 파일이 없어 파싱도 건너뜁니다.")

        conn.commit()                            # 여기까지 와야 실제로 DB 에 반영된다
        summary(conn)
    except Exception as e:
        conn.rollback()
        print("\n실패 — 롤백했습니다. DB 는 실행 전 상태 그대로입니다.")
        if "lock timeout" in str(e).lower() or "canceling statement" in str(e).lower():
            print("원인: 다른 연결이 테이블을 잡고 있습니다. Streamlit 대시보드나 "
                  "DB 클라이언트를 닫고 다시 실행하세요.\n"
                  "  확인: SELECT pid, state, left(query,60) FROM pg_stat_activity "
                  "WHERE datname = current_database() AND pid <> pg_backend_pid();")
        raise
    finally:
        conn.close()


def summary(conn) -> None:
    """적재 결과 요약. 기대값과 대조해 이상을 바로 알아채는 것이 목적이다."""
    with conn.cursor() as cur:
        # 장소별 행수·파일수·수집 구간. 시작/종료 시각을 테이블에 저장하지 않고
        # min/max 로 유도하므로 항상 실제 데이터와 일치한다.
        cur.execute(Q.SUMMARY_SITES)
        print("\n장소별 현황 (수집 시작/종료 시각은 여기서 유도된다)")
        for code, name, n, nf, t0, t1 in cur.fetchall():
            print(f"  {code:15s} {name:22s} {n:9,}행 / 파일 {nf:2d}개   {t0} ~ {t1}")

        # FSR 은 커버리지가 들쭉날쭉해서(장소·시간대별로 결측이 있음) 따로 보여준다.
        cur.execute(Q.SUMMARY_FSR)
        print("\nFSR 현황 (분당 채널별 1건)")
        for code, n, nmin, t0, t1, noise in cur.fetchall():
            print(f"  {code:15s} {n:6,}행 / {nmin:5,}분   {t0} ~ {t1}   평균잡음 {noise} dBm")

        # 타입별 테이블 20개의 행수를 UNION ALL 로 한 번에 센다.
        # 테이블 이름이 코드에서 오므로(사용자 입력 아님) f-string 조립이 안전하다.
        union = " UNION ALL ".join(f"SELECT '{t}' t, count(*) n FROM {t}" for t in P.all_tables())
        cur.execute(f"SELECT t, n FROM ({union}) x WHERE n > 0 ORDER BY n DESC")
        rows = cur.fetchall()

        # 위 합계는 원문의 '디코딩 대상 행수'와 같아야 한다. 다르면 디코딩 실패가 있었다는 뜻.
        cur.execute(Q.COUNT_DECODABLE)
        n_raw = cur.fetchone()[0]
        total = sum(n for _, n in rows)
        print(f"\n타입별 파싱: {total:,}행 / 원문 디코딩 대상 {n_raw:,}행"
              f"{'  ✓ 일치' if total == n_raw else '  ⚠ 불일치(디코딩 실패분 확인)'}")
        print("  " + ", ".join(f"{t.replace('ais_msg_', 'type ')}={n:,}" for t, n in rows))


# ── 명령줄 진입점 ───────────────────────────────────────────────────
# 'python db/load_ais_raw.py --rebuild' 처럼 직접 실행했을 때만 아래가 돌아간다.
# 다른 파일에서 import 하면 __name__ 이 "load_ais_raw" 가 되어 실행되지 않는다
# (그래서 load() 함수를 import 해서 쓰는 것도 가능하다).

# ap = argparse.ArgumentParser()      : 빈 규칙표를 가진 객체
# ap.add_argument("--rebuild", ...)   : 규칙 등록 ─┐
# ap.add_argument("--site", ...)      : 규칙 등록 ─┤ 여기서 "뭐가 뭔지" 결정
# ap.add_argument("--no-parse", ...)  : 규칙 등록 ─┘
# a = ap.parse_args()                 : 등록된 표대로 sys.argv 해석 + 검증 + 객체 생성

if __name__ == "__main__":
    # 명령줄 인자를 해석해주는 표준 라이브러리 도구. description 은 --help 맨 위에 뜨는 설명으로,
    # 이 파일 맨 위 docstring 의 첫 줄(__doc__.splitlines()[0])을 그대로 쓴다.
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])

    # action="append"  : 값을 리스트에 쌓는다 → --site a --site b 하면 ["a", "b"]
    # dest="sites"     : 결과를 담을 변수명. 지정 안 하면 "site" 가 되므로 복수형으로 바꿔둔 것
    # 아예 안 쓰면 None → load() 가 "전체 장소" 로 해석한다
    ap.add_argument("--site", action="append", dest="sites",
                    help="특정 장소 코드만 적재 (여러 번 지정 가능)")

    # action="store_true" : 값을 받지 않는 on/off 스위치.
    #   --rebuild 를 붙이면 True, 안 붙이면 False. (--rebuild=1 처럼 값을 주는 게 아니다)
    ap.add_argument("--rebuild", action="store_true",
                    help="전부 비우고 처음부터 재적재 + 전량 재파싱")

    # 하이픈이 든 --no-parse 는 파이썬 변수명이 될 수 없어 자동으로 no_parse 가 되지만,
    # 헷갈리지 않게 dest 로 명시했다.
    ap.add_argument("--no-parse", action="store_true", dest="no_parse",
                    help="적재만 하고 타입별 파싱은 건너뛴다")

    # 실제로 sys.argv(명령줄에 입력된 문자열들)를 읽어 해석하는 지점.
    # 결과 a 는 a.sites / a.rebuild / a.no_parse 를 가진 객체다.
    # 모르는 인자가 오거나 --help 가 붙으면 여기서 안내를 출력하고 프로그램이 끝난다.
    a = ap.parse_args()

    # 두 옵션의 조합은 막는다. --rebuild 는 전체를 비우므로 '특정 장소만' 과 뜻이 충돌하고,
    # 허용하면 지정하지 않은 장소의 데이터까지 지워진다.
    # ap.error() 는 사용법을 출력하고 종료 코드 2로 프로그램을 끝낸다.
    if a.rebuild and a.sites:
        ap.error("--rebuild 는 전체 재적재 전용입니다. --site 와 함께 쓸 수 없습니다.")

    # 해석한 값을 실제 작업 함수에 넘긴다. 여기부터가 본 작업.
    load(sites=a.sites, rebuild=a.rebuild, no_parse=a.no_parse)
