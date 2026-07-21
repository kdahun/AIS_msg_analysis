"""수집 장소(rx_sites) 모듈 — load_ais_raw.py 가 쓴다.

장소 정보의 원본은 AIS_실해역_데이터/sites.yaml 이고, DB 의 rx_sites 는 그 사본이다.
좌표를 정정할 일이 생기면 yaml 만 고치고 ensure_rx_sites() 를 다시 부르면
메시지 행은 건드리지 않고 좌표만 갱신된다.
"""
import os
from pathlib import Path

import psycopg2
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "AIS_실해역_데이터"
SITES_YAML = DATA_DIR / "sites.yaml"

RAW_TABLE = "ais_messages"

# 접속정보는 환경변수 우선(대시보드 core/db.py 와 같은 키). 없으면 개발용 기본값.
DB = dict(
    host=os.getenv("AIS_DB_HOST", "localhost"),
    port=int(os.getenv("AIS_DB_PORT", "5432")),
    user=os.getenv("AIS_DB_USER", "sim_user"),
    password=os.getenv("AIS_DB_PASSWORD", "all4land1!"),
    dbname=os.getenv("AIS_DB_NAME", "ais_analysis_db"),
)

_DDL = """
CREATE TABLE IF NOT EXISTS rx_sites (
    id          SMALLSERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,      -- 폴더명과 동일
    name        TEXT NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    antenna_h_m REAL,
    note        TEXT
)
"""

_UPSERT = """
INSERT INTO rx_sites (code, name, lat, lon, antenna_h_m, note)
VALUES (%(code)s, %(name)s, %(lat)s, %(lon)s, %(antenna_h_m)s, %(note)s)
ON CONFLICT (code) DO UPDATE
   SET name = EXCLUDED.name, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
       antenna_h_m = EXCLUDED.antenna_h_m, note = EXCLUDED.note
"""


def connect():
    return psycopg2.connect(**DB)


def load_sites_yaml() -> list[dict]:
    """sites.yaml 파싱 + 폴더 존재/코드 일치 검증."""
    with open(SITES_YAML, encoding="utf-8") as f:
        sites = yaml.safe_load(f)["sites"]

    codes = [s["code"] for s in sites]
    if len(set(codes)) != len(codes):
        raise ValueError(f"sites.yaml 에 code 중복: {codes}")

    for s in sites:
        s.setdefault("antenna_h_m", None)
        s.setdefault("note", None)
        if not (DATA_DIR / s["code"]).is_dir():
            raise FileNotFoundError(f"'{s['code']}' 폴더가 없습니다: {DATA_DIR / s['code']}")

    # yaml 에 선언되지 않은 폴더가 있으면 그 데이터는 통째로 누락되므로 막는다.
    on_disk = {p.name for p in DATA_DIR.iterdir() if p.is_dir()}
    if orphan := on_disk - set(codes):
        raise ValueError(f"sites.yaml 에 없는 폴더: {sorted(orphan)} — 항목을 추가하세요")

    return sites


def ensure_rx_sites(conn) -> dict[str, int]:
    """rx_sites 생성 + yaml 내용 upsert. 반환: {code: id}"""
    sites = load_sites_yaml()
    with conn.cursor() as cur:
        cur.execute(_DDL)
        for s in sites:
            cur.execute(_UPSERT, s)
        cur.execute("SELECT code, id FROM rx_sites")
        return dict(cur.fetchall())


def site_files(code: str) -> list[Path]:
    """한 장소 폴더의 원문 txt 목록(파일명 정렬 = 시간순)."""
    return sorted((DATA_DIR / code).glob("*.txt"))
