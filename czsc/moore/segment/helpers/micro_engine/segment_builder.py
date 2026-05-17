# -*- coding: utf-8 -*-
"""线段重建与中枢挂载 helper。"""

from czsc.py.enum import Direction

from ....objects import MooreSegment


class SegmentBuilderHelper:
    def __init__(self, state):
        self.s = state

    def reset_locks(self):
        s = self.s
        for tk in s.turning_ks:
            tk.is_locked = False
        if len(s.turning_ks) >= 2:
            s.turning_ks[-2].is_locked = True
        if len(s.turning_ks) >= 3:
            s.turning_ks[-3].is_locked = True

    def has_center_between(self, start_k_index: int, end_k_index: int) -> bool:
        s = self.s
        all_c = s.all_centers + s.potential_centers
        for c in all_c:
            if start_k_index <= c.start_k_index <= end_k_index and not getattr(c, "is_ghost", False):
                return True
        if s.center_state >= 2 and s.center_line_k:
            is_confirmed = (s.center_method_found is not None and s.center_black_k_pass)
            if is_confirmed and s.center_line_k_index <= end_k_index:
                if start_k_index <= s.center_start_k_index <= end_k_index:
                    return True
        return False

    def update_segments(self):
        s = self.s
        s.segments = []
        if len(s.turning_ks) < 2:
            return
        for i in range(len(s.turning_ks) - 1):
            tk1 = s.turning_ks[i]
            tk2 = s.turning_ks[i + 1]
            direction = Direction.Up if tk2.mark.name == "G" else Direction.Down
            seg = MooreSegment(symbol=tk1.symbol, start_k=tk1, end_k=tk2, direction=direction)
            seg.centers = []
            seg_key = (tk1.cache.get("micro_id"), tk2.cache.get("micro_id"))
            for c in s.micro_centers:
                if getattr(c, "is_ghost", False):
                    continue
                if c.owner_seg_key is not None:
                    if c.owner_seg_key == seg_key:
                        seg.centers.append(c)
                    continue
                c_confirm_dt = c.confirm_k.dt if c.confirm_k else c.start_dt
                if not c_confirm_dt:
                    continue
                if tk1.dt <= c_confirm_dt <= tk2.dt:
                    seg.centers.append(c)
            tk2.is_perfect = bool(seg.centers)
            tk2.maybe_is_fake = not tk2.is_perfect
            s.segments.append(seg)
