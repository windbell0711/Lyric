from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class MarkItem:
    id: int
    cx: float
    cy: float

@dataclass
class SplitRegion:
    id: int
    x: int
    y: int
    width: int
    height: int

@dataclass
class SplitItem:
    extract_id: int
    regions: List[SplitRegion] = field(default_factory=list)

@dataclass
class TimelineSplit:
    split_id: int
    time: Optional[int] = None  # 毫秒，尚未记录时为 None

@dataclass
class TimelineExtract:
    extract_id: int
    start_time: Optional[int] = None  # 毫秒
    splits: List[TimelineSplit] = field(default_factory=list)