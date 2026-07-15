"""여러 탭이 공유하는 필터 위젯. 위젯 key 는 탭마다 고유해야 하므로 접두어(prefix)를 받는다."""
import streamlit as st

from core import queries
from core.constants import msg_label


def mmsi_multiselect(prefix: str, label: str = "MMSI 선택", max_default: int = 0):
    """수신 건수 상위 MMSI 목록에서 다중 선택. 반환: 선택된 mmsi 리스트[int]."""
    opts = queries.get_mmsi_options()
    mapping = {f"{row.mmsi}  ({row.n:,}건)": int(row.mmsi) for row in opts.itertuples()}
    labels = list(mapping.keys())
    default = labels[:max_default] if max_default else []
    picked = st.multiselect(label, labels, default=default, key=f"{prefix}_mmsi")
    return [mapping[p] for p in picked]


def msg_type_multiselect(prefix: str, label: str = "메시지 타입"):
    """존재하는 메시지 타입에서 다중 선택. 반환: 선택된 msg_type 리스트[int](빈 리스트=전체)."""
    counts = queries.get_msg_type_counts()
    mapping = {f"{msg_label(int(r.msg_type))}  ({r.n:,}건)": int(r.msg_type)
               for r in counts.itertuples()}
    picked = st.multiselect(label, list(mapping.keys()), key=f"{prefix}_mtype")
    return [mapping[p] for p in picked]


def time_range(prefix: str, mmsis: list[int] | None = None):
    """시작/끝 시각 선택. 반환: (start, end).

    mmsis 를 주면 그 MMSI(들)의 실제 수신 시간 범위로 슬라이더의 최소/최대/기본값이
    맞춰진다. MMSI 선택이 바뀌면(위젯 key 에 mmsis 를 포함시켜) 슬라이더가 새로
    초기화되어 새 범위의 시작~끝으로 자동 조정된다.
    """
    lo, hi = queries.get_time_bounds(mmsis)
    scope = "_".join(str(m) for m in sorted(mmsis)) if mmsis else "all"
    c1, c2 = st.columns(2)
    start = c1.slider("시작", min_value=lo.to_pydatetime(), max_value=hi.to_pydatetime(),
                      value=lo.to_pydatetime(), key=f"{prefix}_start_{scope}", format="MM-DD HH:mm")
    end = c2.slider("끝", min_value=lo.to_pydatetime(), max_value=hi.to_pydatetime(),
                    value=hi.to_pydatetime(), key=f"{prefix}_end_{scope}", format="MM-DD HH:mm")
    return start, end
