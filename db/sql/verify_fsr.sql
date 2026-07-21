-- FSR 검증 쿼리 모음
--
-- 적재(python db/load_ais_raw.py --rebuild) 후, 아래 쿼리를 하나씩 골라 실행한다.
-- 각 쿼리는 자체 완결형이라 그대로 복사해 붙여넣으면 바로 돈다. 새로 만드는 뷰는 없다.
-- (앞서 v_msg_frame 뷰를 만들었다면 DROP VIEW IF EXISTS v_msg_frame; 로 지워도 된다.)
--
-- 전제: v_vsi 뷰에 vsi_slot 컬럼이 있어야 한다.
--       없으면 `python db/load_ais_raw.py` 를 한 번 실행해 뷰를 갱신한다.
--
-- 시각 규칙 (중요)
--   프레임의 '시:분' 은 오직 VSI/FSR 문장 안의 hhmmss(UTC) 에서만 가져온다.
--   recv_time(로거가 파일에 쓴 시각)은 뭉쳐 찍히거나 밀릴 수 있어 프레임 경계를
--   흐트러뜨리므로 분 단위 계산에는 쓰지 않는다. 날짜를 얻는 데만 쓰고,
--   VSI 시각과 12시간 이상 벌어지면 하루를 보정한다(UTC 자정 = KST 09:00 경계).
--
--   아래 쿼리마다 반복되는 raw/msg CTE 가 그 계산이다. 내용은 전부 동일하다.


-- ════════════════════════════════════════════════════════════════
-- [1] 프레임 목록 — 프레임마다 한 줄
--
--     FSR슬롯수    FSR: 그 1분에 정상 수신된 메시지가 점유한 슬롯 수
--     사용슬롯     우리 로그의 VDM 파트 수 합 (멀티파트는 슬롯을 여러 개 씀)
--     못받은슬롯   FSR슬롯수 - 사용슬롯
--     메시지수     그 1분의 메시지 건수 (멀티파트도 1건)
--     서로다른슬롯 관측된 슬롯 번호 종류 수
--     같은슬롯겹침 메시지수 - 서로다른슬롯. 0 이 아니면 한 슬롯에 둘 이상 = 슬롯 충돌
--     상태         수집 시작/종료 프레임, 장비 재기동 근처 표시
--
--     맨 아래 WHERE 주석을 풀면 특정 장소·채널·시간대만 볼 수 있다.
-- ════════════════════════════════════════════════════════════════
WITH raw AS (
    SELECT m.site_id,
           split_part(m.ais_raw, ',', 5)                    AS channel,
           m.recv_time - interval '9 hours'                 AS utc_recv,
           date(m.recv_time - interval '9 hours')
             + make_interval(hours => v.vsi_hour, mins => v.vsi_minute) AS cand,
           array_length(string_to_array(m.ais_raw, '|'), 1) AS parts,
           v.vsi_slot
      FROM v_vsi v JOIN ais_messages m ON m.id = v.source_id
     WHERE m.ais_raw IS NOT NULL AND v.vsi_hour IS NOT NULL
), msg AS (
    SELECT site_id, channel, parts, vsi_slot,
           CASE WHEN cand - utc_recv >  interval '12 hours' THEN cand - interval '1 day'
                WHEN cand - utc_recv < -interval '12 hours' THEN cand + interval '1 day'
                ELSE cand END + interval '9 hours' AS frame
      FROM raw
), agg AS (
    SELECT site_id, channel, frame,
           count(*)                 AS msgs,
           sum(parts)               AS used_slots,
           count(DISTINCT vsi_slot) AS distinct_slots
      FROM msg GROUP BY 1, 2, 3
), gap AS (                       -- 메시지는 있는데 FSR 이 없는 프레임 = 장비 이상
    SELECT a.site_id, a.channel, a.frame
      FROM agg a
      LEFT JOIN ais_fsr f ON f.site_id = a.site_id
                         AND f.channel::text = a.channel AND f.frame = a.frame
     WHERE f.id IS NULL
), bounds AS (
    SELECT site_id, channel, min(frame) AS f_first, max(frame) AS f_last
      FROM ais_fsr GROUP BY 1, 2
)
SELECT s.code AS 장소, f.channel AS 채널, f.frame AS 프레임,
       f.rx_slots                 AS "FSR슬롯수",
       a.used_slots               AS 사용슬롯,
       f.rx_slots - a.used_slots  AS 못받은슬롯,
       a.msgs                     AS 메시지수,
       a.distinct_slots           AS 서로다른슬롯,
       a.msgs - a.distinct_slots  AS 같은슬롯겹침,
       f.crc_fail                 AS "CRC실패",
       f.noise_dbm                AS 잡음,
       CASE WHEN f.frame = b.f_first THEN '수집 시작'
            WHEN f.frame = b.f_last  THEN '수집 종료'
            WHEN EXISTS (SELECT 1 FROM gap g
                          WHERE g.site_id = f.site_id AND g.channel = f.channel::text
                            AND g.frame BETWEEN f.frame - interval '3 min'
                                            AND f.frame + interval '3 min')
                 THEN '재기동 근처'
            ELSE '' END           AS 상태
  FROM ais_fsr f
  JOIN agg    a ON a.site_id = f.site_id AND a.channel = f.channel::text
               AND a.frame   = f.frame
  JOIN bounds b ON b.site_id = f.site_id AND b.channel = f.channel
  JOIN rx_sites s ON s.id = f.site_id
 -- WHERE s.code = 'kmou' AND f.channel = 'A'
 --   AND f.frame BETWEEN '2026-06-10 10:30' AND '2026-06-10 11:30'
 ORDER BY f.frame, f.channel;


-- ════════════════════════════════════════════════════════════════
-- [2] "1분에 몇 개나 못 받았나" — 구간별 프레임 수
--     수집 시작/종료 프레임과 재기동 ±3분은 원래 반쪽만 받으므로 제외한다.
--
--     원문 직접 집계 기대값 (2,244개 프레임)
--       0개 336 / 1~2개 990 / 3~5개 521 / 6~10개 342 / 11개+ 51 / 더 받음 4
-- ════════════════════════════════════════════════════════════════
WITH raw AS (
    SELECT m.site_id,
           split_part(m.ais_raw, ',', 5)                    AS channel,
           m.recv_time - interval '9 hours'                 AS utc_recv,
           date(m.recv_time - interval '9 hours')
             + make_interval(hours => v.vsi_hour, mins => v.vsi_minute) AS cand,
           array_length(string_to_array(m.ais_raw, '|'), 1) AS parts
      FROM v_vsi v JOIN ais_messages m ON m.id = v.source_id
     WHERE m.ais_raw IS NOT NULL AND v.vsi_hour IS NOT NULL
), msg AS (
    SELECT site_id, channel, parts,
           CASE WHEN cand - utc_recv >  interval '12 hours' THEN cand - interval '1 day'
                WHEN cand - utc_recv < -interval '12 hours' THEN cand + interval '1 day'
                ELSE cand END + interval '9 hours' AS frame
      FROM raw
), agg AS (
    SELECT site_id, channel, frame, sum(parts) AS used_slots
      FROM msg GROUP BY 1, 2, 3
), gap AS (
    SELECT a.site_id, a.channel, a.frame
      FROM agg a
      LEFT JOIN ais_fsr f ON f.site_id = a.site_id
                         AND f.channel::text = a.channel AND f.frame = a.frame
     WHERE f.id IS NULL
), bounds AS (
    SELECT site_id, channel, min(frame) AS f_first, max(frame) AS f_last
      FROM ais_fsr GROUP BY 1, 2
), d AS (
    SELECT f.rx_slots - a.used_slots AS diff
      FROM ais_fsr f
      JOIN agg    a ON a.site_id = f.site_id AND a.channel = f.channel::text
                   AND a.frame = f.frame
      JOIN bounds b ON b.site_id = f.site_id AND b.channel = f.channel
     WHERE f.frame <> b.f_first AND f.frame <> b.f_last
       AND NOT EXISTS (SELECT 1 FROM gap g
                        WHERE g.site_id = f.site_id AND g.channel = f.channel::text
                          AND g.frame BETWEEN f.frame - interval '3 min'
                                          AND f.frame + interval '3 min')
)
SELECT CASE WHEN diff <  0 THEN '우리가 더 받음'
            WHEN diff =  0 THEN '0개 (하나도 안 놓침)'
            WHEN diff <= 2 THEN '1~2개'
            WHEN diff <= 5 THEN '3~5개'
            WHEN diff <= 10 THEN '6~10개'
            ELSE '11개 이상' END                       AS "못 받은 슬롯",
       count(*)                                       AS "프레임 수",
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS "비율(%)"
  FROM d GROUP BY 1 ORDER BY min(diff);


-- ════════════════════════════════════════════════════════════════
-- [2-b] 같은 계산을 '메시지 건수'로 (틀린 단위임을 확인하는 대조군)
--       기대: 0개인 프레임이 하나도 없고 대부분 11개 이상 벌어진다.
-- ════════════════════════════════════════════════════════════════
WITH raw AS (
    SELECT m.site_id,
           split_part(m.ais_raw, ',', 5)    AS channel,
           m.recv_time - interval '9 hours' AS utc_recv,
           date(m.recv_time - interval '9 hours')
             + make_interval(hours => v.vsi_hour, mins => v.vsi_minute) AS cand
      FROM v_vsi v JOIN ais_messages m ON m.id = v.source_id
     WHERE m.ais_raw IS NOT NULL AND v.vsi_hour IS NOT NULL
), msg AS (
    SELECT site_id, channel,
           CASE WHEN cand - utc_recv >  interval '12 hours' THEN cand - interval '1 day'
                WHEN cand - utc_recv < -interval '12 hours' THEN cand + interval '1 day'
                ELSE cand END + interval '9 hours' AS frame
      FROM raw
), agg AS (
    SELECT site_id, channel, frame, count(*) AS msgs
      FROM msg GROUP BY 1, 2, 3
), bounds AS (
    SELECT site_id, channel, min(frame) AS f_first, max(frame) AS f_last
      FROM ais_fsr GROUP BY 1, 2
), d AS (
    SELECT f.rx_slots - a.msgs AS diff
      FROM ais_fsr f
      JOIN agg    a ON a.site_id = f.site_id AND a.channel = f.channel::text
                   AND a.frame = f.frame
      JOIN bounds b ON b.site_id = f.site_id AND b.channel = f.channel
     WHERE f.frame <> b.f_first AND f.frame <> b.f_last
)
SELECT CASE WHEN diff <= 0 THEN '0개 이하'
            WHEN diff <= 2 THEN '1~2개'
            WHEN diff <= 5 THEN '3~5개'
            WHEN diff <= 10 THEN '6~10개'
            ELSE '11개 이상' END AS "못 받은 슬롯",
       count(*) AS "프레임 수"
  FROM d GROUP BY 1 ORDER BY min(diff);


-- ════════════════════════════════════════════════════════════════
-- [3] 프레임 경계를 넘어가는 멀티파트 메시지
--     슬롯 2249 에서 2슬롯 메시지가 시작하면 뒷부분은 다음 프레임 0번 슬롯에 놓인다.
--     우리는 VSI 시각 기준으로 두 파트를 모두 이 프레임에 세지만 장비는 나눠 센다.
--     원문 기준 10건.
-- ════════════════════════════════════════════════════════════════
WITH raw AS (
    SELECT m.site_id,
           split_part(m.ais_raw, ',', 5)                    AS channel,
           m.recv_time - interval '9 hours'                 AS utc_recv,
           date(m.recv_time - interval '9 hours')
             + make_interval(hours => v.vsi_hour, mins => v.vsi_minute) AS cand,
           array_length(string_to_array(m.ais_raw, '|'), 1) AS parts,
           v.vsi_slot, v.msg_type, v.mmsi
      FROM v_vsi v JOIN ais_messages m ON m.id = v.source_id
     WHERE m.ais_raw IS NOT NULL AND v.vsi_hour IS NOT NULL
), msg AS (
    SELECT site_id, channel, parts, vsi_slot, msg_type, mmsi,
           CASE WHEN cand - utc_recv >  interval '12 hours' THEN cand - interval '1 day'
                WHEN cand - utc_recv < -interval '12 hours' THEN cand + interval '1 day'
                ELSE cand END + interval '9 hours' AS frame
      FROM raw
)
SELECT s.code AS 장소, msg.channel AS 채널, msg.frame AS 프레임,
       msg.vsi_slot AS 시작슬롯, msg.parts AS 파트수,
       msg.vsi_slot + msg.parts - 1 AS 마지막슬롯,
       msg.msg_type AS 타입, msg.mmsi
  FROM msg JOIN rx_sites s ON s.id = msg.site_id
 WHERE msg.vsi_slot + msg.parts - 1 >= 2250
 ORDER BY msg.frame;


-- ════════════════════════════════════════════════════════════════
-- [4] 장비 상태 — FSR 이 없는 프레임 (메시지는 들어오는데 FSR 만 없는 구간)
--     원문 기준 28개. kmou 10:50~10:59 는 수신은 정상인데 상태 출력만 멈춘 구간.
-- ════════════════════════════════════════════════════════════════
WITH raw AS (
    SELECT m.site_id,
           split_part(m.ais_raw, ',', 5)    AS channel,
           m.recv_time - interval '9 hours' AS utc_recv,
           date(m.recv_time - interval '9 hours')
             + make_interval(hours => v.vsi_hour, mins => v.vsi_minute) AS cand
      FROM v_vsi v JOIN ais_messages m ON m.id = v.source_id
     WHERE m.ais_raw IS NOT NULL AND v.vsi_hour IS NOT NULL
), msg AS (
    SELECT site_id, channel,
           CASE WHEN cand - utc_recv >  interval '12 hours' THEN cand - interval '1 day'
                WHEN cand - utc_recv < -interval '12 hours' THEN cand + interval '1 day'
                ELSE cand END + interval '9 hours' AS frame
      FROM raw
), agg AS (
    SELECT site_id, channel, frame, count(*) AS msgs
      FROM msg GROUP BY 1, 2, 3
)
SELECT s.code AS 장소, a.channel AS 채널, a.frame AS 프레임,
       a.msgs AS "그 분의 메시지 수"
  FROM agg a
  JOIN rx_sites s ON s.id = a.site_id
  LEFT JOIN ais_fsr f ON f.site_id = a.site_id
                     AND f.channel::text = a.channel AND f.frame = a.frame
 WHERE f.id IS NULL
 ORDER BY a.frame, a.channel;


-- ════════════════════════════════════════════════════════════════
-- [5] 잡음 비교 — 직접 계산 vs FSR 실측
--     직접 계산 = 그 프레임 수신 메시지들의 median(RSSI - SNR)
--     원문 기준: 우리 추정이 실측보다 낮게(= 더 조용하게) 나온다.
-- ════════════════════════════════════════════════════════════════
WITH raw AS (
    SELECT m.site_id,
           split_part(m.ais_raw, ',', 5)    AS channel,
           m.recv_time - interval '9 hours' AS utc_recv,
           date(m.recv_time - interval '9 hours')
             + make_interval(hours => v.vsi_hour, mins => v.vsi_minute) AS cand,
           v.vsi_rssi, v.vsi_snr
      FROM v_vsi v JOIN ais_messages m ON m.id = v.source_id
     WHERE m.ais_raw IS NOT NULL AND v.vsi_hour IS NOT NULL
       AND v.vsi_rssi IS NOT NULL AND v.vsi_snr IS NOT NULL
), msg AS (
    SELECT site_id, channel, vsi_rssi, vsi_snr,
           CASE WHEN cand - utc_recv >  interval '12 hours' THEN cand - interval '1 day'
                WHEN cand - utc_recv < -interval '12 hours' THEN cand + interval '1 day'
                ELSE cand END + interval '9 hours' AS frame
      FROM raw
), est AS (
    SELECT site_id, channel, frame,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY vsi_rssi - vsi_snr) AS noise_est
      FROM msg GROUP BY 1, 2, 3
)
SELECT s.code AS 장소, f.channel AS 채널, count(*) AS 프레임수,
       round(avg(f.noise_dbm), 1)          AS "FSR 실측 평균",
       round(avg(e.noise_est)::numeric, 1) AS "직접계산 평균",
       count(*) FILTER (WHERE e.noise_est <  f.noise_dbm) AS "직접계산이 더 낮음",
       count(*) FILTER (WHERE e.noise_est >  f.noise_dbm) AS "직접계산이 더 높음",
       count(*) FILTER (WHERE abs(e.noise_est - f.noise_dbm) <= 3) AS "3dB 이내"
  FROM ais_fsr f
  JOIN est e ON e.site_id = f.site_id AND e.channel = f.channel::text
            AND e.frame = f.frame
  JOIN rx_sites s ON s.id = f.site_id
 GROUP BY 1, 2 ORDER BY 1, 2;
