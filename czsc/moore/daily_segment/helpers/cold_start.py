# -*- coding: utf-8 -*-
"""冷启动入口。"""
from __future__ import annotations

from typing import Optional, Sequence

from .commit import CommitDecision, find_delayed_commit_decision


def find_cold_start_decision(segments: Sequence, ma34, ma170) -> Optional[CommitDecision]:
    return find_delayed_commit_decision(
        segments,
        completed_segments=(),
        ma34=ma34,
        ma170=ma170,
        continuity_broken=False,
        allow_cold_start=True,
    )

