# AIS_msg_analysis

부산 실해역에서 수집한 AIS(선박자동식별장치, Automatic Identification System) 원시 NMEA 로그를
엑셀(.xlsx)로 받아, 위치 보고 메시지(Type 1/3)만 필드 단위로 파싱해 분석 가능한 엑셀로
재가공하는 노트북 모음입니다.

## 노트북 구성

| 파일 | 역할 |
|---|---|
| [ais_analysis_by_sheet_final.ipynb](ais_analysis_by_sheet_final.ipynb) | **최종 버전.** 입력 파일의 모든 시트를 순회하며 시트별로 결과 파일을 각각 생성 |
| [ais_analysis.ipynb](ais_analysis.ipynb) | 초기 버전. 첫 번째 시트 하나만 처리, 결과를 서식 없이 단일 시트로 저장 |
| [ais_analysis_test.ipynb](ais_analysis_test.ipynb) | 입력 파일 구조(시트 목록, 행/열 개수) 확인용 테스트 노트북 |

아래 설명은 `ais_analysis_by_sheet_final.ipynb` 기준입니다.

## 실행 환경

```
pip install -r requirements.txt
```

`FILE_PATH` 변수에 입력 엑셀 파일명을 지정한 뒤 노트북을 순서대로 실행합니다.
원본 엑셀 파일과 `.venv`는 용량 문제로 `.gitignore`에 의해 저장소에 포함되지 않으므로,
분석 시 직접 같은 폴더에 배치해야 합니다.

---

## Input (입력)

**형식:** `.xlsx` (엑셀)
**예시 파일명:** `AIS_부산 실해역 데이터_26.06.09.xlsx`, `AIS_부산 실해역 테스트 데이터.xlsx` 등

### 구조

- 하나의 파일 안에 **여러 시트**가 들어 있으며, 시트 1개 = 특정 수집 시간대 구간
  (예: `26.06.09`, `26.06.10(~08 30)`, `26.06.10(10 33 ~ 15 06)`)
- 각 시트는 **헤더 없이(header=None)** 딱 2개 열로 구성:

| 열 | 이름(코드상) | 설명 | 예시 값 |
|---|---|---|---|
| A | `timestamp` | 해당 메시지를 수신 장비가 기록한 시각 (문자열, `YYYYMMDD HH:MM:SS.ffff`) | `20260609 17:56:42.9433` |
| B | `raw_msg` | NMEA 0183 원시 문장 1줄 | `!AIVDM,1,1,3,B,36Sf4MPP@Ta>qd`D5PSri9eD21q0,0*52` |

`raw_msg`(B열)에는 두 종류의 문장이 섞여서 들어옵니다.

1. **`!AIVDM,...`** — 실제 AIS 선박 메시지(6bit ASCII-armored payload).
   한 메시지가 여러 줄(멀티파트)로 나뉠 수 있으며, 필드 구성은 다음과 같습니다.

   ```
   !AIVDM,<total>,<part_num>,<seq_id>,<channel>,<payload>,<fill_bits>*<checksum>
   ```
   - `total` : 이 메시지가 총 몇 조각으로 나뉘어 있는지
   - `part_num` : 현재 조각 번호
   - `seq_id` : 멀티파트 메시지를 묶는 시퀀스 ID
   - `channel` : 수신 채널 (`A`=161.975MHz, `B`=162.025MHz)
   - `payload` : 실제 AIS 데이터(6bit 인코딩), 멀티파트는 이 값들을 이어 붙여야 완전한 메시지가 됨

2. **`$AIVSI,...`** — 바로 직전 `!AIVDM` 문장에 대한 **수신 신호 품질 정보**(VSI: VHF Signal Info).

   ```
   $AIVSI,<ui>,<link>,<time_of_arrival>,<slot>,<strength>,<snr>*<checksum>
   ```
   - `ui` : 수신 유닛(수신국) ID
   - `link` : 링크 ID
   - `time_of_arrival` : 신호 도달 시각 (`HHMMSS.ffffff`, UTC)
   - `slot` : 수신 당시 TDMA 슬롯 번호
   - `strength` : 수신 신호 세기 (dBm, 음수)
   - `snr` : 신호 대 잡음비 (dB)

노트북 3단계("시트별 데이터 품질 검증")에서는 이 멀티파트 조각들이 순서대로 빠짐없이 모여
정상 조립 가능한지 사전 검증합니다.

---

## Output (출력)

입력 파일의 **시트 개수만큼** 아래 이름으로 결과 파일이 생성됩니다.

```
AIS_type13_<시트명>.xlsx
```
예: `AIS_type13_26.06.09.xlsx`, `AIS_type13_26.06.10(~08 30).xlsx`

각 출력 파일은 **2개 시트**로 구성됩니다.

### 1) `Summary` 시트 — 메시지 타입별 수신 통계

| 열 | 설명 |
|---|---|
| `msg_num` | AIS 메시지 타입 번호 |
| 메시지 이름 | 메시지 타입 이름 (아래 표 참고) |
| 수신 개수 | 해당 타입 메시지 총 수신 건수 |
| 비율(%) | 시트 전체 대비 비율 |
| VSI 없는 수 | `$AIVSI` 문장이 매칭되지 않은(=신호 품질 정보 없는) 건수 |
| VSI 없는 비율(%) | 해당 타입 내에서 VSI 없는 건의 비율 |

맨 아래에 시트별 소계와 전체 총합계 행이 추가됩니다. 메시지 이름 매핑:

| msg_num | 메시지 이름 |
|---|---|
| 1 | Position Report Class A (SOTDMA) |
| 2 | Position Report Class A (Assigned) |
| 3 | Position Report Class A (ITDMA) |
| 4 | Base Station Report |
| 5 | Static and Voyage Related Data |
| 6 | Addressed Binary Message |
| 7 | Binary Acknowledge |
| 8 | Binary Broadcast Message |
| 12 | Addressed Safety Message |
| 15 | Interrogation |
| 18 | Standard Class B CS Position Report |
| 19 | Extended Class B CS Position Report |
| 20 | Data Link Management |
| 21 | Aid-to-Navigation Report |
| 24 | Static Data Report |
| 27 | Long Range AIS Broadcast |

### 2) `Type1_3` 시트 — Type 1/3(위치 보고) 상세 파싱 데이터

전체 메시지 중 **Type 1(SOTDMA)과 Type 3(ITDMA), 즉 "Position Report"만 필터링**해서
`pyais` 라이브러리로 디코딩한 뒤 필드 단위로 펼친 표입니다. 컬럼은 5개 그룹으로 나뉘어
헤더에 색상이 다르게 표시됩니다.

#### VDM 그룹 (파란색 헤더)

| 필드 | 설명 |
|---|---|
| `timestamp` | 입력 파일의 원본 수신 시각 (A열 값 그대로) |
| `vdm_channel` | AIS 수신 채널 (`A` 또는 `B`) |

#### VSI 그룹 (녹색 헤더) — 신호 품질 정보

| 필드 | 설명 |
|---|---|
| `vsi_ui` | 수신 유닛 ID |
| `vsi_link` | 링크 ID |
| `vsi_hour` / `vsi_minute` / `vsi_second` | VSI 문장에 담긴 신호 도달 시각(UTC)을 시/분/초로 분해 |
| `vsi_slot` | 수신 당시 TDMA 슬롯 번호 |
| `vsi_strength` | 수신 신호 세기 (dBm) |
| `vsi_snr` | 신호 대 잡음비 (dB) |

#### AIS 그룹 (하늘색 헤더) — pyais로 디코딩된 표준 AIS 필드

| 필드 | 설명 |
|---|---|
| `msg_type` | AIS 메시지 타입 (1 또는 3) |
| `repeat` | Repeat indicator (재전송 횟수, 보통 0) |
| `mmsi` | 선박 식별 번호(MMSI, 9자리) |
| `status` | 항행 상태 코드 (0=기관 사용 중 항행, 1=묘박, 5=계류 등 ITU-R M.1371 표준 코드) |
| `turn` | 선회율(Rate of Turn), 인코딩된 원시값 |
| `speed` | 대지속력 SOG (knots, 0.1kn 단위) |
| `accuracy` | 위치 정확도 플래그 (1=고정밀 <10m, 0=저정밀) |
| `lon` / `lat` | 경도 / 위도 (십진도, WGS84) |
| `course` | 대지침로 COG (도) |
| `heading` | 실침로 True Heading (도, 511=미제공) |
| `second` | 메시지 생성 시점의 UTC 초 (0~59, 60=시각 미제공) |
| `maneuver` | 특수조종지표 (0=N/A, 1=일반 항행, 2=특수조종 중) |
| `raim` | RAIM(수신 무결성 자체 감시) 사용 여부 플래그 |
| `radio` | 통신 상태 원시 비트값 (아래 COMM 그룹으로 추가 해석됨) |

#### COMM 그룹 (청록색 헤더) — `radio` 필드를 msg_type에 따라 재해석

| 필드 | 적용 타입 | 설명 |
|---|---|---|
| `sync_state` | 1, 3 공통 | 동기화 상태 (0=UTC 직접, 1=UTC 간접, 2=기지국 동기화, 3=타 국 동기화) |
| `slot_timeout` | Type 1 (SOTDMA) | 다음 슬롯 재할당까지 남은 프레임 수 |
| `sub_message` | Type 1 (SOTDMA) | `slot_timeout` 값에 따라 해석이 달라지는 부가 정보(수신국 수/슬롯 번호/UTC 시분 등) |
| `slot_increment` | Type 3 (ITDMA) | 다음 전송까지의 슬롯 증분값 |
| `num_slots` | Type 3 (ITDMA) | 예약된 연속 슬롯 개수 |
| `keep_flag` | Type 3 (ITDMA) | 슬롯 유지 여부 플래그 |

#### RAW 그룹 (회색 헤더) — 원본 보존

| 필드 | 설명 |
|---|---|
| `raw_msg` | 원본 `!AIVDM` 문장(들). 멀티파트인 경우 `\|`로 이어붙인 원문 |
| `vsi` | 매칭된 원본 `$AIVSI` 문장 |

#### 행 색상 규칙

- `msg_type` 값에 따라 연노랑(Type 1) / 연보라(Type 3) 배경 적용
- 매칭되는 `$AIVSI`가 없는 행(`vsi` 값이 없음)은 **빨간색**으로 강조 표시

---

## 처리 흐름 요약

1. 입력 엑셀의 시트 목록 확인
2. 시트별로 `!AIVDM`(멀티파트 조립) + `$AIVSI`(직전 메시지에 매칭) 페어링 → `(timestamp, msg_num, raw_msg, vsi)` 중간 테이블 생성
3. 멀티파트 조립 무결성 검증 (조각 누락/순서 오류 여부)
4. 전체 메시지 타입 분포 및 VSI 누락 통계 집계 → `Summary` 시트
5. `msg_num`이 1 또는 3인 행만 필터링 → `pyais.decode()`로 필드 분해 → `Type1_3` 시트로 서식 적용 후 저장
