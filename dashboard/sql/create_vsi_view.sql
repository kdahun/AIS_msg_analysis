CREATE OR REPLACE VIEW v_vsi AS
  SELECT source_id, recv_time, mmsi, 1 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_1
  UNION ALL
  SELECT source_id, recv_time, mmsi, 3 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_3
  UNION ALL
  SELECT source_id, recv_time, mmsi, 4 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_4
  UNION ALL
  SELECT source_id, recv_time, mmsi, 5 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_5
  UNION ALL
  SELECT source_id, recv_time, mmsi, 6 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_6
  UNION ALL
  SELECT source_id, recv_time, mmsi, 7 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_7
  UNION ALL
  SELECT source_id, recv_time, mmsi, 8 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_8
  UNION ALL
  SELECT source_id, recv_time, mmsi, 9 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_9
  UNION ALL
  SELECT source_id, recv_time, mmsi, 10 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_10
  UNION ALL
  SELECT source_id, recv_time, mmsi, 11 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_11
  UNION ALL
  SELECT source_id, recv_time, mmsi, 12 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_12
  UNION ALL
  SELECT source_id, recv_time, mmsi, 13 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_13
  UNION ALL
  SELECT source_id, recv_time, mmsi, 14 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_14
  UNION ALL
  SELECT source_id, recv_time, mmsi, 15 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_15
  UNION ALL
  SELECT source_id, recv_time, mmsi, 18 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_18
  UNION ALL
  SELECT source_id, recv_time, mmsi, 19 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_19
  UNION ALL
  SELECT source_id, recv_time, mmsi, 20 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_20
  UNION ALL
  SELECT source_id, recv_time, mmsi, 21 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_21
  UNION ALL
  SELECT source_id, recv_time, mmsi, 24 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_24a
  UNION ALL
  SELECT source_id, recv_time, mmsi, 24 AS msg_type, vsi_rssi, vsi_snr, vsi_hour, vsi_minute, vsi_second FROM ais_msg_24b;
