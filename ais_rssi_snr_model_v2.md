# AIS RSSI/SNR 신호 유효성 판단 ML 모델 — 2차(V2) 설계

> 본 문서는 2차(고도화) 모델 설계만 다룬다. ML 모델의 질문 정의, 입력 데이터 전제,
> event_time 설계, 위치 부여 설계, RSSI/SNR baseline 설계, 1차 모델 설계는
> `ais_rssi_snr_model_v1.md`를 전제로 한다.

## 1. 문서 목적

2차 모델은 1차 모델(`ais_rssi_snr_model_v1.md`) 결과를 기반으로, 실제 성능 개선이 확인되는 feature를 단계적으로 확장하는 고도화 모델이다.

```text
2차 모델:
- 1차 모델 결과를 기반으로 feature를 확장한 개선 모델
- ablation test, SHAP/feature importance 분석을 통해 실제 성능 개선이 확인된 feature만 추가
```

1차 모델과 2차 모델은 **동시에 운영할 서로 다른 모델 2개**가 아니라, 개발 단계별 모델 버전이다. 최종 운영 시에는 1차 모델과 2차 모델 중 성능과 안정성이 더 좋은 버전을 선택하거나, 1차 모델을 기준선으로 유지하고 2차 모델을 고도화 버전으로 운영한다.

---

## 2. 2차 모델 설계

### 2.1 2차 모델 목표

```text
1차 모델의 오탐/미탐 원인 분석
feature group별 ablation test
중요도 낮은 feature 제거
성능 개선이 확인된 feature만 추가
운영 가능한 feature set 확정
```

### 2.2 2차 모델 확장 후보 feature

#### 2.2.1 메시지 세부 feature

| Feature | 설명 |
|---|---|
| `message_type` | 개별 AIS 메시지 번호 |
| `has_destination_mmsi` | 목적지 MMSI 필드 존재 여부 |
| `weekday` | 요일 패턴 |

추가 조건:

```text
데이터 수가 충분하고 message_type별 신호 패턴 차이가 관찰될 때만 추가
```

#### 2.2.2 위치/보간 세부 feature

| Feature | 설명 |
|---|---|
| `interpolation_method` | 현재는 `NONE/LINEAR` |
| `interpolation_gap_sec` | 보간 위치일 때 전후 정상 위치 사이 시간 간격. 1차에서는 `position_confidence`로 대체 |
| `prev_dynamic_gap_sec` | 직전 정상 위치까지 시간 |
| `next_dynamic_gap_sec` | 다음 정상 위치까지 시간 |
| `interpolation_ratio` | 보간 구간 내 상대 위치 |
| `relative_east_m` | 수신국 기준 동쪽 상대거리 |
| `relative_north_m` | 수신국 기준 북쪽 상대거리 |

추가 조건:

```text
보간 위치 row가 충분히 많고,
position_source=INTERPOLATED_DYNAMIC에서 오탐이 많을 경우 추가
```

#### 2.2.3 baseline 세부 feature

| Feature | 설명 |
|---|---|
| `rssi_residual` | 관측 RSSI - 기대 RSSI |
| `snr_residual` | 관측 SNR - 기대 SNR |
| `baseline_sample_count` | baseline cell 표본 수 |
| `baseline_fallback_level` | fallback 단계 |

추가 조건:

```text
zscore만으로 이상 정도 해석이 부족하거나,
baseline confidence만으로 baseline 품질을 충분히 설명하지 못할 때 추가
```

#### 2.2.4 동일 MMSI rolling feature

| Feature | 설명 |
|---|---|
| `rssi_roll_mean` | 최근 RSSI 평균 |
| `rssi_roll_std` | 최근 RSSI 표준편차 |
| `snr_roll_mean` | 최근 SNR 평균 |
| `snr_roll_std` | 최근 SNR 표준편차 |
| `rssi_residual_delta_from_prev` | 직전 대비 RSSI residual 변화 |
| `snr_residual_delta_from_prev` | 직전 대비 SNR residual 변화 |

추가 조건:

```text
step change 계열 오탐/미탐 분석 후,
직전 delta만으로 부족할 때 추가
```

#### 2.2.5 시간창/채널 context 확장

| Feature | 설명 |
|---|---|
| `msg_count_60s` | 최근 60초 전체 메시지 수 |
| `message_type_count_60s` | 최근 60초 동일 메시지 타입 수 |
| `rx_observed_slot_occupancy_60s` | 관측 slot 점유 추정값 |
| `avg_rssi_60s` | 최근 60초 평균 RSSI |
| `rssi_drop_ratio_60s` | 최근 60초 RSSI 급락 비율 |

추가 조건:

```text
SNR cluster drop만으로 채널 환경 이상을 설명하기 부족할 때 추가
slot occupancy 계산 정확도가 검증된 후 추가
```

#### 2.2.6 시간 품질 feature

| Feature | 설명 |
|---|---|
| `event_time_source` | VSI_TIME 또는 DATA_BUCKET |
| `gateway_vsi_time_diff_ms` | gateway_time과 vsi_time 차이 |
| `event_time_parse_status` | event_time 파싱 상태 |

추가 조건:

```text
VSI time과 dataBucket의 차이가 실제 신호 feature 품질에 영향을 준다고 확인될 때만 추가
```

처음에는 품질 분석용으로만 저장하고, 2차 실험에서만 사용 여부를 검토한다.

### 2.3 2차 ablation test 계획

2차 모델은 feature를 한꺼번에 추가하지 않는다.
다음 순서로 그룹 단위 ablation test를 수행한다.

```text
기준 모델:
- V1 core 23개 feature

실험 A:
- V1 + 위치/보간 세부 feature

실험 B:
- V1 + baseline 세부 feature

실험 C:
- V1 + 동일 MMSI rolling feature

실험 D:
- V1 + 시간창/채널 context 확장 feature

실험 E:
- V1 + 메시지 세부 feature

실험 F:
- V1 + 시간 품질 feature
```

각 실험은 동일한 train/validation/test split으로 비교한다.

평가 결과가 V1보다 개선되지 않으면 해당 feature group은 제외한다.

### 2.4 2차 feature selection 기준

다음 기준으로 feature 유지 여부를 결정한다.

```text
시간 분리 검증 성능 개선
수동 검토 샘플에서 오탐 감소
주입 공격 또는 시뮬레이션 데이터에서 미탐 감소
SHAP/feature importance가 의미 있게 나타남
운영 중 계산 비용이 과도하지 않음
결측률이 과도하지 않음
```

제거 기준:

```text
중요도 거의 0
결측률 과다
특정 메시지 타입만 구분하는 shortcut으로 작동
라벨 누수 위험
Rule-base 항목을 우회적으로 학습
운영 계산 비용 대비 성능 개선 없음
```

### 2.5 2차 모델 출력

2차 모델도 1차 모델과 동일하게 신호 이상 점수만 출력한다.

```text
signal_anomaly_score
signal_anomaly_label
signal_anomaly_reason_codes
model_version
feature_version
```

Rule-base 결과는 여전히 별도이다.

```text
final_risk_score = combine(rule_risk_score, signal_anomaly_score)
```

단, 이 final risk aggregator는 별도 설계에서 다룬다.

### 2.6 2차 산출물

```text
ais_signal_features_extended.py
ais_training_dataset_v2.py
ablation_report_v2.md
feature_importance_report_v2.md
shap_analysis_v2.md
model_v2_xgboost.pkl 또는 model_v2_lightgbm.pkl
feature_dictionary_v2.md
evaluation_report_v2.md
```

---

## 3. 1차/2차 모델 비교

| 항목 | 1차 모델 | 2차 모델 |
|---|---|---|
| 목적 | 빠르게 검증 가능한 핵심 신호 모델 | 성능 개선 및 feature 확장 |
| feature 수 | 약 23개 | 23개 + 검증된 확장 feature |
| 장점 | 단순함, 해석 쉬움, 결측 적음 | 복잡한 패턴 반영 가능 |
| 단점 | 일부 상황 설명 부족 가능 | 과적합/해석성 저하 위험 |
| 사용 feature | RSSI/SNR, 거리, 위치 신뢰도, 최소 context | rolling, 세부 baseline, 위치 세부, 채널 context 확장 |
| 평가 방식 | baseline 성능 확인 | V1 대비 ablation 비교 |
| 운영 추천 | 초기 운영/검증용 | V1보다 명확히 개선될 때만 운영 반영 |

---

## 4. 2차 개발 순서

1차 개발 순서(`ais_rssi_snr_model_v1.md` 8절, 1~9단계) 완료 후 이어서 진행한다.

```text
10. 2차 확장 후보 feature 생성
11. feature group별 ablation test
12. 2차 모델 성능 비교
13. 운영 feature set 확정
```

---

## 5. 2차 최종 결론

2차 모델은 1차 모델 결과를 바탕으로 다음 feature group을 단계적으로 추가한다.

```text
message_type 세부 feature
위치/보간 세부 feature
baseline 세부 feature
동일 MMSI rolling feature
시간창/채널 context 확장 feature
event_time 품질 feature
```

2차 feature는 모두 ablation test를 거쳐 실제 성능 개선이 확인될 때만 최종 모델에 반영한다.
