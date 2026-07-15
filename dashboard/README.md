# AIS RSSI / SNR 분석 대시보드

`ais_analysis_db`(PostGIS 컨테이너)에 적재된 AIS 메시지의 **RSSI/SNR을 다각도로 분석**하는 Streamlit 대시보드입니다.
탭 단위로 기능이 분리되어 있어 **새 분석 탭을 파일 하나 추가하는 것만으로 확장**할 수 있습니다.

---

## 1. 사전 준비

### 1-1. 데이터베이스
아래 테이블이 `ais_analysis_db`에 있어야 합니다 (프로젝트 루트의 노트북으로 생성).
- `ais_messages` — 원문 테이블 (`ais_to_db.ipynb`)
- `ais_msg_1` ~ `ais_msg_21`, `ais_msg_24a`, `ais_msg_24b` — 타입별 파싱 테이블 (`ais_parse_by_type.ipynb`)

### 1-2. 통합 뷰 생성 (최초 1회)
모든 타입별 테이블의 공통 컬럼(수신시각·MMSI·RSSI·SNR 등)을 하나로 합친 **뷰 `v_vsi`**를 대시보드가 사용합니다.
뷰는 데이터를 저장하지 않고 조회 시점의 테이블 내용을 실시간으로 반영하므로, 한 번만 만들면 이후 데이터가 추가돼도 자동 반영됩니다.

```bash
# 컨테이너 안에서 실행
docker exec -i postgis-container psql -U sim_user -d ais_analysis_db < sql/create_vsi_view.sql
```
`CREATE OR REPLACE VIEW`라 여러 번 실행해도 안전합니다.

### 1-3. 접속정보 설정
`.streamlit/secrets.toml`에 DB 접속정보를 넣습니다 (이 파일은 `.gitignore`로 커밋에서 제외됨).
```toml
[postgres]
host = "localhost"
port = 5432
user = "sim_user"
password = "********"
dbname = "ais_analysis_db"
```

---

## 2. 설치 및 실행

```bash
cd dashboard
pip install -r requirements.txt
streamlit run app.py
```
기본적으로 http://localhost:8501 에서 열립니다.

---

## 3. 파일 구성

```
dashboard/
├── app.py                        # 엔트리포인트 — 탭 등록/렌더링만 담당 (로직 없음)
├── requirements.txt              # 의존성 (streamlit, pandas, plotly, sqlalchemy, psycopg2-binary)
├── README.md
│
├── .streamlit/
│   ├── config.toml               # 테마(dark)·서버 설정
│   └── secrets.toml              # DB 접속정보 (git 제외)
│
├── sql/
│   └── create_vsi_view.sql       # 통합 뷰 v_vsi 생성 스크립트 (20개 테이블 UNION ALL)
│
├── core/                         # 데이터 계층 (탭과 무관, UI 없음)
│   ├── db.py                     # SQLAlchemy 엔진(캐시) + run_query() (결과 캐싱)
│   ├── queries.py                # 재사용 SQL 함수 — 집계는 전부 DB에서 수행
│   └── constants.py              # 뷰 이름, 메시지 타입 이름 맵(MSG_NAMES)
│
├── components/                   # 여러 탭이 공유하는 UI 조각
│   ├── filters.py                # MMSI·메시지타입·시간범위 선택 위젯
│   └── charts.py                 # Plotly 차트 래퍼 (분포/시계열/박스플롯/산점도)
│
└── tabs/                         # ★ 탭 하나 = 파일 하나
    ├── __init__.py               #   탭 레지스트리 (TABS 목록)
    ├── rssi_snr_by_mmsi.py       #   [MMSI별] 탭
    ├── rssi_snr_by_time.py       #   [시간별] 탭
    └── rssi_snr_by_message.py    #   [메시지별] 탭
```

### 계층 구조
```
app.py  →  tabs/*  →  components/*  →  core/queries  →  core/db  →  PostgreSQL(v_vsi)
```
- **탭**은 UI 흐름만 담당하고, 데이터는 `core.queries`의 함수를 호출해서 가져옵니다.
- **집계·통계는 모두 SQL(Postgres)에서 계산**하고 브라우저로는 작은 결과만 전송해, 100만 행 규모에서도 각 탭이 빠르게 그려집니다.

---

## 4. 기능 (탭별 설명)

### 📊 MMSI별 (`rssi_snr_by_mmsi.py`)
선박(MMSI) 단위로 신호 품질을 비교합니다.
- **MMSI 다중 선택** (수신 건수 상위순 목록, 기본 상위 3개 자동 선택)
- **통계 요약 테이블**: 선택 MMSI별 RSSI/SNR의 건수·평균·최소·최대·표준편차
- **RSSI 분포 / SNR 분포**: 값별 건수 막대그래프 (MMSI 간 오버레이 비교)

### ⏱️ 시간별 (`rssi_snr_by_time.py`)
시간에 따른 신호 품질 추이를 봅니다.
- **집계 단위 선택**: 시간(hour) / 분(minute)
- **필터**: 메시지 타입, MMSI (선택 안 하면 전체)
- **이중 축 시계열**: 버킷별 RSSI 평균 + SNR 평균 라인 (좌/우 y축)
- 하단에 집계 원본 테이블(펼치기) 제공

### 📨 메시지별 (`rssi_snr_by_message.py`)
메시지 타입 비교 + **전체 메시지 원문 탐색**.
- **타입별 분포 비교**: 메시지 타입별 RSSI/SNR 박스플롯 (사분위수·최소/최대, DB에서 percentile 계산)
- **전체 메시지 탐색기**: 필터(타입·MMSI·시간범위) + 페이지네이션으로 **모든 메시지를 원문(AIS/VSI 포함)까지 조회**

---

## 5. 새 탭 추가 방법

1. `tabs/` 에 새 파일을 만들고 두 가지만 정의:
   ```python
   # tabs/my_new_tab.py
   TITLE = "새 분석"          # 탭에 표시될 이름
   def render():             # 탭 본문
       ...
   ```
2. `tabs/__init__.py` 의 import 와 `TABS` 리스트에 한 줄 추가:
   ```python
   from . import ..., my_new_tab
   TABS = [ ..., my_new_tab ]
   ```

→ `app.py` 는 `TABS` 목록만 보고 탭을 그리므로 **수정할 필요가 없습니다.**
재사용할 데이터 조회는 `core/queries.py`에, 공용 위젯·차트는 `components/`에 추가하면 다른 탭에서도 바로 씁니다.

---

## 6. 데이터 소스 참고: 통합 뷰 `v_vsi`

RSSI/SNR(`vsi_rssi`/`vsi_snr`)은 20개 타입별 테이블의 공통 컬럼에 들어 있어, 이를 하나로 합친 뷰를 사용합니다.

| 컬럼 | 설명 |
|---|---|
| `source_id` | 원문 테이블 `ais_messages.id` (조인 키) |
| `recv_time` | 수신 시각 (timestamp) |
| `mmsi` | 선박 식별번호 |
| `msg_type` | AIS 메시지 타입 (24a/24b는 24로 통일) |
| `vsi_rssi` | 신호 세기 (RSSI) |
| `vsi_snr` | 신호 대 잡음비 (SNR) |
| `vsi_hour`/`vsi_minute`/`vsi_second` | VSI TOA (수신기 기준 정밀 시각, UTC) |

> 성능이 더 필요해지면 이 뷰를 **MATERIALIZED VIEW**로 바꾸고 주기적으로 `REFRESH`하는 방식으로 전환할 수 있습니다(현재 규모에서는 일반 뷰로 충분).
