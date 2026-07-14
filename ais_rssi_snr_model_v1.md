# AIS RSSI/SNR 신호 유효성 판단 ML 모델 — 1차(V1) 설계

> 본 문서는 1차(MVP) 모델 설계만 다룬다. 2차(고도화) 모델 설계는 별도 문서
> `ais_rssi_snr_model_v2.md`를 참고한다.

## 1. 문서 목적

본 문서는 HDFS에 저장된 AIS JSON 데이터를 기반으로, 선박 또는 해안국 송신 신호의 **RSSI/SNR이 수신국 기준 거리와 비교했을 때 유효한 신호세기인지 판단하는 ML 모델**의 1차(MVP) 버전을 개발하기 위한 설계를 정리한다.

```text
1차 모델:
- 핵심 feature만 사용한 MVP 모델
- RSSI/SNR + 거리 + 위치 신뢰도 + 최소 채널 context 중심
- 해석 가능성과 구현 안정성 우선
```

---

## 2. 1차 모델 개발 방향

### 2.1 ML 모델의 질문

이번 ML 모델은 아래 질문에만 답한다.

```text
이 AIS 메시지를 송신한 선박 또는 해안국의 위치를 기준으로 볼 때,
현재 수신된 RSSI/SNR이 물리적으로 타당한 범위인가?
```

즉, ML 모델은 **RSSI/SNR 신호 유효성 판단 전용 모델**이다.

### 2.2 이번 ML 모델에서 다루지 않는 항목

다음 항목은 ML 입력 feature로 사용하지 않고, 별도 Rule-base 검증 대상으로 분리한다.

```text
보고주기 검증
요청/응답 검증
ACK 검증
Message 6/7 요청-ACK 관계
Message 12/13 Safety Message-ACK 관계
Message 15 질의 후 응답 여부
Message 16/20/22/23 제어 메시지 권한 및 파라미터 검증
동일 MMSI 다위치 충돌 검증
위치 점프 검증
SOG/COG/ROT/heading 기반 운동학 검증
sensor sentinel 반복 검증
Message 20 reservationGroups 검증
```

### 2.3 중요 설계 원칙

```text
HDFS JSON 1건 = ML feature 생성 대상 1건

GW가 이미 AIS 원문을 디코딩하고 JSON으로 저장하므로,
ML feature 생성 단계에서는 멀티프래그먼트 재조립을 수행하지 않는다.

data.messageId = message_type
data.mmsi = source_mmsi
vsi.rssi = rssi
vsi.snr = snr
vsi.slotNum = slot_num

vsi.time이 존재하면 event_time의 1순위로 사용한다.
vsi.time이 없거나 파싱 실패하면 dataBucket을 fallback event_time으로 사용한다.

SOG/COG 기반 dead-reckoning 외삽은 사용하지 않는다.

raw MMSI, raw latitude/longitude, encodedData, weak_label, LF 결과, Rule-base 결과는 ML input X에 넣지 않는다.
```

---

## 3. 입력 데이터 전제

### 3.1 HDFS JSON 구조

HDFS에는 GW가 AIS 원문을 디코딩한 JSON이 저장되어 있다고 가정한다.

공통 envelope 예시:

```json
{
  "messageName": "!AIVDM",
  "totalMessage": 1,
  "messageNumber": 1,
  "encodedData": "D04<Sqh00C6D58Bfpm0k6D",
  "data": {
    "messageId": 20,
    "repeatIndicator": 0,
    "mmsi": 4400103
  },
  "channel": "A",
  "sequenceId": "",
  "vsi": {
    "time": "102633.717",
    "slotNum": "1264",
    "rssi": "-115.0",
    "snr": "6.3"
  },
  "dataBucket": "2026-07-01-21:17:19.461",
  "stationMmsi": "004403102"
}
```

### 3.2 JSON 정규화 결과

각 JSON에서 다음 값을 정규화한다.

| 정규화 컬럼 | 추출 기준 | 설명 |
|---|---|---|
| `message_type` | `data.messageId` | AIS 메시지 번호 |
| `source_mmsi` | `data.mmsi` | 송신 MMSI |
| `destination_mmsi` | `data.destinationMmsi`, `data.destinationId` 등 | 목적지 MMSI가 있는 경우 |
| `channel` | `channel` | AIS 채널 A/B |
| `slot_num` | `vsi.slotNum` | 수신 slot 번호 |
| `rssi` | `vsi.rssi` | RSSI |
| `snr` | `vsi.snr` | SNR |
| `vsi_time_raw` | `vsi.time` | VSI 시각 원문 |
| `gateway_time` | `dataBucket` | GW parsing/storage 기준 시각 |
| `station_mmsi` | `stationMmsi` | 수신국/저장 metadata |

`stationMmsi`는 저장/추적 metadata로 보존하지만, 현재 하드코딩 가능성이 있으므로 수신국 위치 계산에는 직접 사용하지 않는다.
수신국 좌표는 별도 config에서 주입한다.

```text
receiver_lat
receiver_lon
```

---

## 4. event_time 설계

### 4.1 event_time 우선순위

`vsi.time` 필드가 추가되었으므로, event_time은 다음 우선순위로 생성한다.

```text
1순위: vsi.time + dataBucket 날짜
2순위: dataBucket
```

### 4.2 vsi.time 파싱

`vsi.time`은 `HHMMSS.sss` 형식이다.

예시:

```text
vsi.time = 102633.717
→ 10:26:33.717
```

날짜 정보가 없으므로 `dataBucket`의 날짜를 결합한다.

### 4.3 자정 경계 처리

자정 근처에서 날짜가 하루 밀리는 문제를 방지하기 위해 다음 3개 후보 timestamp를 만든다.

```text
dataBucket 날짜 - 1일 + vsi.time
dataBucket 날짜       + vsi.time
dataBucket 날짜 + 1일 + vsi.time
```

이 중 `dataBucket`과 시간 차이가 가장 작은 값을 최종 `event_time`으로 선택한다.

### 4.4 시간 관련 저장 컬럼

| 컬럼 | 설명 | ML 입력 여부 |
|---|---|---|
| `event_time` | 최종 메시지 기준 시각 | 직접 입력하지 않음 |
| `event_time_source` | `VSI_TIME` 또는 `DATA_BUCKET` | 2차부터 optional |
| `vsi_time_raw` | VSI time 원문 | 입력 제외 |
| `gateway_time` | dataBucket 원문 | 입력 제외 |
| `gateway_vsi_time_diff_ms` | gateway_time과 event_time 차이 | 품질 확인용, 1차 입력 제외 |
| `event_time_parse_status` | 파싱 성공/실패 상태 | 품질 확인용, 1차 입력 제외 |

### 4.5 event_time 사용 용도

`event_time`은 다음 feature 생성에 사용한다.

```text
메시지 시간 정렬
동일 MMSI rolling RSSI/SNR 계산
이전/다음 Type 1/2/3 위치 탐색
전후 위치 기반 선형 보간
시간창 기반 channel context 계산
hour_sin / hour_cos / weekday 생성
```

단, 다음 용도에는 사용하지 않는다.

```text
slot-level 정밀 검증
표준 기반 수신시각 검증
RF propagation delay 계산
```

---

## 5. 위치 부여 설계

### 5.1 위치 부여 우선순위

```text
1. 직접 선박 위치
2. 직접 기지국 위치
3. 등록 해안국 위치
4. 전후 Type 1/2/3 기반 선형 보간 위치
5. UNKNOWN
```

### 5.2 직접 선박 위치

대상:

```text
AIS Type 1, 2, 3
```

조건:

```text
data.latitude != 91
data.longitude != 181
```

처리:

```text
position_source = DIRECT_DYNAMIC
position_available = 1
position_confidence = 1.0
```

### 5.3 Type 1/2/3 위치 sentinel 처리

Type 1/2/3이라도 `latitude=91` 또는 `longitude=181`이면 위치 미상으로 본다.

이 경우 바로 학습 제외하지 않고, **위치 없는 선박 메시지와 동일하게 보간 후보로 처리한다.**

```text
Type 1/2/3 with latitude=91 or longitude=181
→ INTERPOLATED_DYNAMIC 후보
→ 전후 Type 1/2/3 정상 위치가 모두 있으면 선형 보간
→ 없으면 UNKNOWN
```

이 처리는 GPS 미상, 위치 센서 이상, 일시적 위치 누락 메시지를 완전히 버리지 않기 위한 것이다.

### 5.4 직접 기지국 위치

대상:

```text
AIS Type 4, 11
```

조건:

```text
data.latitude, data.longitude가 유효함
```

처리:

```text
position_source = DIRECT_BASE
position_available = 1
position_confidence = 1.0
```

### 5.5 등록 해안국 위치

해안국/기지국 MMSI와 위치 정보를 담는 `registered_base_station_table`을 사용한다.

필수 컬럼 예시:

| 컬럼 | 설명 |
|---|---|
| `base_mmsi` | 해안국 MMSI |
| `base_name` | 해안국명 |
| `latitude` | 등록 위도 |
| `longitude` | 등록 경도 |
| `authority_name` | 관할기관 |
| `station_type` | 기지국 유형 |
| `valid_from` | 등록 유효 시작 시각 |
| `valid_to` | 등록 유효 종료 시각 |
| `position_quality` | 위치 품질 |

조인 조건:

```text
source_mmsi = base_mmsi
AND event_time >= valid_from
AND event_time < valid_to
```

`valid_to`가 null이면 현재까지 유효한 것으로 처리한다.

처리:

```text
position_source = REGISTERED_BASE
position_available = 1
position_confidence = position_quality 기반
```

권장 confidence:

| position_quality | position_confidence |
|---|---:|
| HIGH | 1.0 |
| MEDIUM | 0.8 |
| LOW | 0.6 |
| 정보 없음 | 0.8 |

### 5.6 전후 Type 1/2/3 기반 선형 보간

대상:

```text
위치 필드가 없는 선박 송신 메시지
Type 1/2/3 중 latitude=91 또는 longitude=181인 메시지
Type 5
Type 6
Type 7
Type 10
Type 12
Type 13
Type 15
source_entity_type이 VESSEL로 추정되는 위치 없는 메시지
```

조건:

```text
같은 source_mmsi의 prev Type 1/2/3 정상 위치가 존재
같은 source_mmsi의 next Type 1/2/3 정상 위치가 존재
prev_dynamic_time < event_time < next_dynamic_time
interpolation_gap_sec <= max_interpolation_gap_sec
```

보간 공식:

```text
ratio = (event_time - prev_dynamic_time) / (next_dynamic_time - prev_dynamic_time)

interp_lat = prev_lat + ratio × (next_lat - prev_lat)
interp_lon = prev_lon + ratio × (next_lon - prev_lon)
```

생성 컬럼:

```text
position_source = INTERPOLATED_DYNAMIC
position_available = 1
position_confidence = gap 기준
interpolation_method = LINEAR
interpolation_gap_sec = next_dynamic_time - prev_dynamic_time
prev_dynamic_gap_sec = event_time - prev_dynamic_time
next_dynamic_gap_sec = next_dynamic_time - event_time
interpolation_ratio = ratio
```

권장 config:

```text
max_interpolation_gap_sec = 180
```

### 5.7 외삽 금지

이번 배치 학습 데이터 생성에서는 외삽을 사용하지 않는다.

```text
SOG/COG 기반 dead-reckoning 사용하지 않음
마지막 위치 고정 방식 사용하지 않음
prev 위치만 있으면 UNKNOWN
next 위치만 있으면 UNKNOWN
```

이유:

```text
외삽 위치는 실측 위치가 아니라 가정값이다.
가정값으로 distance_to_station_m을 만들면 rssi/snr residual이 오염될 수 있다.
위치가 끊긴 상황 자체는 Rule-base 또는 데이터 품질 검증에서 다뤄야 한다.
```

---

## 6. RSSI/SNR baseline 설계

### 6.1 baseline key

RSSI/SNR baseline은 메시지 타입이 아니라 물리적 송신 주체와 거리/채널/시간대 기준으로 구성한다.

`message_type_group`은 baseline key에 넣지 않는다.

대신 `source_entity_type`은 baseline key에 포함할 수 있다.
선박과 해안국/기지국은 송신 출력과 안테나 조건이 다를 수 있으므로, `source_entity_type`은 물리적으로 의미 있는 구분이다.

권장 baseline key 우선순위:

```text
1순위: source_entity_type + channel + distance_bin + hour_bin
2순위: source_entity_type + channel + distance_bin
3순위: channel + distance_bin + hour_bin
4순위: channel + distance_bin
5순위: distance_bin
6순위: global baseline
7순위: path-loss fallback
```

### 6.2 baseline feature

| 컬럼 | 설명 |
|---|---|
| `rssi_zscore` | 거리/채널/시간대 대비 RSSI 이상도 |
| `snr_zscore` | 거리/채널/시간대 대비 SNR 이상도 |
| `rssi_residual` | 관측 RSSI - 기대 RSSI |
| `snr_residual` | 관측 SNR - 기대 SNR |
| `baseline_sample_count` | baseline cell 표본 수 |
| `baseline_confidence` | baseline 신뢰도 |
| `baseline_fallback_level` | fallback 단계 |

1차 모델에서는 `rssi_zscore`, `snr_zscore`, `baseline_confidence`만 사용한다.
나머지는 저장하고 2차 모델에서 검토한다.

---

## 7. 1차 모델 설계

### 7.1 1차 모델 목표

1차 모델은 **핵심 feature만 사용하여 빠르게 검증 가능한 RSSI/SNR 신호 유효성 모델**을 만드는 것이 목적이다.

목표:

```text
feature 수 최소화
구조적 결측 최소화
해석 가능성 확보
baseline 모델 역할 수행
```

### 7.2 1차 모델 알고리즘 후보

```text
1순위: XGBoost
2순위: LightGBM
보조: IsolationForest 또는 One-Class 계열은 low-confidence/unlabeled 분석용으로만 검토
```

### 7.3 1차 모델 입력 feature

1차 모델은 23개 feature로 시작한다.

| 번호 | Feature | 타입 | 설명 |
|---:|---|---|---|
| 1 | `message_type_group` | categorical | AIS 메시지를 큰 그룹으로 묶은 값. 위치 부여 방식과 메시지 성격 context 제공 |
| 2 | `source_entity_type` | categorical | 송신 주체 유형. `VESSEL`, `BASE_STATION`, `UNKNOWN` |
| 3 | `is_addressed` | boolean | 특정 수신자를 지정한 메시지 여부 |
| 4 | `hour_sin` | float | 하루 중 시간의 sin 인코딩 |
| 5 | `hour_cos` | float | 하루 중 시간의 cos 인코딩 |
| 6 | `rssi` | float | 수신 신호 세기 |
| 7 | `snr` | float | 신호대잡음비 |
| 8 | `position_source` | categorical | 위치 출처. 직접/등록/보간/UNKNOWN |
| 9 | `position_confidence` | float | 위치 신뢰도 |
| 10 | `distance_to_station_m` | float | 수신국과 송신 주체 간 거리 |
| 11 | `bearing_from_station_sin` | float | 수신국 기준 방위각 sin |
| 12 | `bearing_from_station_cos` | float | 수신국 기준 방위각 cos |
| 13 | `channel` | categorical | AIS 채널 A/B |
| 14 | `slot_num` | integer | 수신 slot 번호 |
| 15 | `rssi_zscore` | float | baseline 대비 RSSI 이상도 |
| 16 | `snr_zscore` | float | baseline 대비 SNR 이상도 |
| 17 | `baseline_confidence` | categorical | baseline 신뢰도. `HIGH`, `MEDIUM`, `LOW` |
| 18 | `rssi_delta_from_prev` | float | 같은 MMSI 직전 메시지 대비 RSSI 변화량 |
| 19 | `snr_delta_from_prev` | float | 같은 MMSI 직전 메시지 대비 SNR 변화량 |
| 20 | `unique_mmsi_60s` | integer | 최근 60초 고유 MMSI 수 |
| 21 | `channel_msg_count_60s` | integer | 최근 60초 동일 채널 메시지 수 |
| 22 | `avg_snr_60s` | float | 최근 60초 평균 SNR |
| 23 | `snr_drop_ratio_60s` | float | 최근 60초 SNR 급락 비율 |

> **`interpolation_gap_sec`을 1차 core에서 제외한 이유**: 이 feature는 `position_source = INTERPOLATED_DYNAMIC`인 row에만 값이 채워지고, `DIRECT_DYNAMIC`/`DIRECT_BASE`/`REGISTERED_BASE` row에는 정의되지 않는 구조적 결측 feature다. 이는 아래 7.4절에서 `prev_dynamic_gap_sec`, `next_dynamic_gap_sec`, `interpolation_ratio`를 1차에서 제외한 이유("구조적 결측이 많음")와 정확히 같은 성격이므로, 같은 family로 묶어 2차로 이연한다(`ais_rssi_snr_model_v2.md` 참고). 또한 `interpolation_gap_sec`이 담는 정보(보간 구간이 얼마나 넓었는지)는 이미 5.6절의 `position_confidence` 산정식에 gap 크기 기준으로 반영되어 있으므로, 1차에서는 `position_confidence` 하나만으로 이 정보를 충분히 대체할 수 있다.

### 7.4 1차 모델에서 제외하는 feature

1차 모델에서는 다음 feature를 제외한다.

```text
message_type
has_destination_mmsi
weekday
vsi_missing
position_available
interpolation_method
interpolation_gap_sec
prev_dynamic_gap_sec
next_dynamic_gap_sec
interpolation_ratio
relative_east_m
relative_north_m
sync_state
slot_timeout
slot_increment
number_of_slot
rssi_residual
snr_residual
baseline_sample_count
baseline_fallback_level
rssi_roll_mean
rssi_roll_std
snr_roll_mean
snr_roll_std
rssi_residual_delta_from_prev
snr_residual_delta_from_prev
msg_count_60s
message_type_count_60s
rx_observed_slot_occupancy_60s
avg_rssi_60s
rssi_drop_ratio_60s
event_time_source
gateway_vsi_time_diff_ms
```

제외 이유:

```text
중복성이 높음
구조적 결측이 많음
Rule-base 성격이 강함
1차 모델의 해석성을 낮출 수 있음
품질 확인용으로는 저장하되 입력 X에는 넣지 않음
```

이 feature들은 모두 2차 모델의 확장 후보다 (`ais_rssi_snr_model_v2.md` 참고).

### 7.5 1차 모델 학습 데이터 필터

1차 supervised 학습에는 다음 조건을 만족하는 row만 사용한다.

```text
json_quality_status = VALID
vsi_missing = 0
position_available = 1
position_confidence >= 최소 기준
baseline_confidence in {HIGH, MEDIUM}
weak_label confidence >= 최소 기준
```

권장 제외 대상:

```text
POSITION_UNKNOWN
LOW_QUALITY
baseline_confidence = LOW
vsi_missing = 1
```

> **학습/서빙 필터 일관성**: `baseline_confidence`는 core feature(#17)로 모델 입력 X에도 포함되는데, 위 필터로 학습 데이터에서는 `baseline_confidence = LOW`인 row를 전부 제외한다. 따라서 실제 운영(추론) 시에도 이 필터를 동일하게 적용해, `LOW` confidence row는 이 모델에 넣지 않고 별도 경로(비지도 모델 보조 또는 관제사 검토)로 라우팅해야 한다. 이 라우팅을 서빙 단계에서 빠뜨리면, 모델은 학습 때 한 번도 보지 못한 `baseline_confidence = LOW` 조합을 운영 중 마주치게 되어 예측이 불안정해질 수 있다. `position_confidence`가 낮은 row, `vsi_missing = 1`인 row도 동일하게 학습/서빙 양쪽에서 같은 기준으로 걸러야 한다.

### 7.6 1차 weak labeling

1차 모델의 라벨은 신호 기반 Labeling Function만 사용한다.

사용 LF:

```text
LF_RSSI_TOO_STRONG_FOR_DISTANCE
LF_RSSI_TOO_WEAK_FOR_DISTANCE
LF_SNR_TOO_LOW_FOR_DISTANCE
LF_RSSI_STEP_CHANGE
LF_SNR_STEP_CHANGE
LF_SNR_CLUSTER_DROP
LF_LOW_POSITION_CONFIDENCE
LF_VSI_MISSING
```

주의:

```text
LF_LOW_POSITION_CONFIDENCE와 LF_VSI_MISSING은 ANOMALY 라벨을 만들기보다 LOW_QUALITY 또는 ABSTAIN으로 처리한다.
Rule-base 결과는 weak_label 생성에 직접 사용하지 않는다.
```

#### LF_RSSI_STEP_CHANGE 보완 조건

`LF_RSSI_STEP_CHANGE`는 현재 메시지와 직전 메시지 양쪽 모두 위치 신뢰도가 충분할 때만 판정한다.

```text
current.position_confidence >= threshold
previous.position_confidence >= threshold
```

위치 신뢰도가 낮으면 ABSTAIN 처리한다.

### 7.7 1차 평가 기준

1차 평가는 weak label만으로 최종 성능을 단정하지 않는다.

평가 기준:

```text
시간 분리 검증
수동 검토 샘플
주입 공격 또는 시뮬레이션 데이터가 있으면 별도 검증
feature importance 확인
오탐 사례 분석
```

확인할 항목:

```text
rssi_zscore, snr_zscore가 실제로 상위 중요 feature인지
distance_to_station_m이 정상적으로 영향력을 갖는지
position_confidence가 낮은 row에서 오탐이 늘어나는지
source_entity_type별 baseline이 분리되는지
```

### 7.8 1차 산출물

```text
ais_json_normalizer.py
ais_event_time.py
ais_position_enrichment.py
ais_signal_baseline.py
ais_signal_features_core.py
ais_signal_labeling_functions.py
ais_signal_training_dataset_v1.py
model_v1_xgboost.pkl 또는 model_v1_lightgbm.pkl
feature_dictionary_v1.md
evaluation_report_v1.md
```

---

## 8. 1차 개발 순서

```text
1. HDFS JSON 정규화 구현
2. vsi.time 기반 event_time 생성 구현
3. 위치 부여 로직 구현
4. RSSI/SNR baseline 생성
5. 1차 core feature 23개 생성
6. 신호 기반 weak label 생성
7. 1차 XGBoost/LightGBM 학습
8. 시간 분리 검증 및 수동 샘플 검토
9. feature importance 확인
```

9번(feature importance 확인) 결과는 2차 모델 설계의 입력으로 사용한다.

---

## 9. 1차 최종 결론

1차 모델은 **core 23개 feature**로 시작한다. (`interpolation_gap_sec`은 `prev/next_dynamic_gap_sec`, `interpolation_ratio`와 같은 구조적 결측 family로 묶어 2차로 이연했다 — `position_confidence`가 1차에서 그 역할을 대신한다.)

```text
message_type_group
source_entity_type
is_addressed
hour_sin
hour_cos
rssi
snr
position_source
position_confidence
distance_to_station_m
bearing_from_station_sin
bearing_from_station_cos
channel
slot_num
rssi_zscore
snr_zscore
baseline_confidence
rssi_delta_from_prev
snr_delta_from_prev
unique_mmsi_60s
channel_msg_count_60s
avg_snr_60s
snr_drop_ratio_60s
```

1차 모델 완료 후 성능/feature importance 결과를 바탕으로 2차 모델(`ais_rssi_snr_model_v2.md`)로 진행한다.
