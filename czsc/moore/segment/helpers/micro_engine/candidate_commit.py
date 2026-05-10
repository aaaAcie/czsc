# -*- coding: utf-8 -*-
"""候选确立落盘 helper（不含审判推进，只做状态提交）。"""

from czsc.py.enum import Direction, Mark

from ....objects import MooreSegment
from ...scope_utils import get_trigger_index


class CandidateCommitHelper:
    def __init__(self, state, compute_ma5_extreme):
        self.s = state
        self._compute_ma5_extreme = compute_ma5_extreme

    def commit_candidate(self, final_tk, perfect_struct: bool, has_visible: bool):
        """执行候选确立的核心落盘，返回被同向刷新替换的旧端点（若存在）。"""
        s = self.s

        final_tk.is_valid = True
        final_tk.is_perfect = perfect_struct
        final_tk.maybe_is_fake = not perfect_struct
        final_tk.has_visible_center = has_visible
        if final_tk.cache.get("micro_id") is None:
            s.micro_id_seed += 1
            final_tk.cache["micro_id"] = s.micro_id_seed
        s.turning_tk_store[final_tk.cache.get("micro_id")] = final_tk

        if s.turning_ks and s.turning_ks[-1].mark == final_tk.mark:
            ref_tk = s.turning_ks[-2] if len(s.turning_ks) >= 2 else None
        else:
            ref_tk = s.turning_ks[-1] if s.turning_ks else None
        start_idx = ref_tk.k_index if ref_tk else 0
        end_idx = get_trigger_index(final_tk)
        final_tk.cache["leg_ma5_extreme"] = self._compute_ma5_extreme(final_tk.mark, start_idx, end_idx)

        refreshed_old_tk = None
        if s.turning_ks and s.turning_ks[-1].mark == final_tk.mark:
            old_tk = s.turning_ks[-1]
            refreshed_old_tk = old_tk
            old_mid = old_tk.cache.get("micro_id")
            if old_mid is not None:
                s.turning_tk_store[old_mid] = old_tk
            s.refreshed_segments.append(
                MooreSegment(
                    symbol=old_tk.symbol,
                    start_k=old_tk,
                    end_k=final_tk,
                    direction=Direction.Up if final_tk.mark == Mark.G else Direction.Down,
                )
            )
            s.turning_ks.pop()

        s.turning_ks.append(final_tk)
        return refreshed_old_tk
