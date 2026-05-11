# -*- coding: utf-8 -*-
"""四法则 + 独立两K校验 helper。"""

from czsc.py.enum import Mark

from . import cold_start as cold_start_config


class RuleValidatorHelper:
    def __init__(self, state):
        self.s = state

    def validate_four_rules(self, tk, override_ref_tk=None) -> tuple:
        s = self.s
        bars = s.bars_raw
        ma5_val = bars[-1].cache.get("ma5", 0.0)
        if override_ref_tk is not None:
            ref_tk = override_ref_tk
        else:
            if s.turning_ks and s.turning_ks[-1].mark == tk.mark:
                ref_tk = s.turning_ks[-2] if len(s.turning_ks) >= 2 else None
            else:
                ref_tk = s.turning_ks[-1] if s.turning_ks else None
        start_idx = ref_tk.k_index if ref_tk else 0
        end_idx = tk.k_index
        rule1 = False
        p_idx = tk.k_index - 1
        n_idx = tk.k_index + 1
        prev_b = bars[p_idx] if p_idx >= 0 else None
        curr_b = bars[tk.k_index]
        next_b = bars[n_idx] if n_idx < len(bars) else bars[-1]
        if tk.mark == Mark.G:
            rule1 = (curr_b.high >= (prev_b.high if prev_b else -1)) and (curr_b.high >= next_b.high)
        else:
            rule1 = (curr_b.low <= (prev_b.low if prev_b else 999999)) and (curr_b.low <= next_b.low)
        curr_ma5 = curr_b.cache.get("ma5")
        body_top = max(curr_b.open, curr_b.close)
        body_bottom = min(curr_b.open, curr_b.close)
        if tk.mark == Mark.G:
            body_ma5_ok = curr_ma5 is not None and body_top > curr_ma5
        else:
            body_ma5_ok = curr_ma5 is not None and body_bottom < curr_ma5
        if rule1 and not body_ma5_ok:
            s.debug_body_filter += 1
        rule1 = rule1 and body_ma5_ok
        rule2 = False
        if not ref_tk:
            if tk.mark == Mark.G:
                rule2 = bars[-1].close < ma5_val
            else:
                rule2 = bars[-1].close > ma5_val
        else:
            if s.ma34_cross_expand_one_k:
                scan_left_idx = max(0, start_idx - 1)
                scan_right_idx = min(len(bars) - 1, end_idx + 1)
            else:
                # 关闭扩展：仅允许交叉发生在 [start_idx, end_idx] 闭区间内（允许落在边界K上）
                scan_left_idx = max(0, start_idx)
                scan_right_idx = min(len(bars) - 1, end_idx)
            for i in range(scan_left_idx + 1, scan_right_idx + 1):
                b_prev = bars[i - 1]
                b_curr = bars[i]
                m5_p, m34_p = b_prev.cache.get("ma5"), b_prev.cache.get("ma34")
                m5_c, m34_c = b_curr.cache.get("ma5"), b_curr.cache.get("ma34")
                if None in (m5_p, m34_p, m5_c, m34_c):
                    continue
                if (m5_p <= m34_p and m5_c > m34_c) or (m5_p >= m34_p and m5_c < m34_c):
                    rule2 = True
                    break
        rule3 = False
        has_v = False
        all_c = s.all_centers + s.potential_centers
        seg_centers = [c for c in all_c if start_idx <= c.start_k_index <= end_idx and not getattr(c, "is_ghost", False)]
        if seg_centers:
            rule3 = True
            if any(c.is_visible for c in seg_centers):
                has_v = True
        live_center_qualifies = False
        if not rule3 and s.center_state >= 2 and s.center_line_k:
            is_confirmed = (s.center_method_found is not None and s.center_black_k_pass)
            if is_confirmed and s.center_line_k_index <= end_idx:
                c_start = s.center_start_k_index
                if start_idx <= c_start <= end_idx:
                    rule3 = True
                    live_center_qualifies = True
        rule_double_k = True
        effective_centers = list(seg_centers)
        if live_center_qualifies:
            from ....objects import MooreCenter
            live_c = MooreCenter(
                type_name="INVISIBLE",
                direction=s.center_direction,
                upper_rail=s.center_upper_rail,
                lower_rail=s.center_lower_rail,
            )
            live_c.start_k_index = s.center_start_k_index
            effective_centers.append(live_c)
        if effective_centers:
            rule_double_k = self.check_double_k_escape(ref_tk, tk, effective_centers, bars)

        if cold_start_config.ENABLE_MICRO_COLD_START:
            # 启动期放宽：候选极值仍在“MA34 首次出现之前”时，允许无 MA5/MA34 交叉，先建立异向骨架。
            first_ma34_idx = s.cache.get("first_ma34_k_index")
            if first_ma34_idx is None:
                first_ma34_idx = -1
                for i, b in enumerate(bars):
                    if b.cache.get("ma34") is not None:
                        first_ma34_idx = i
                        break
                s.cache["first_ma34_k_index"] = first_ma34_idx
            pre_ma34_candidate = first_ma34_idx >= 0 and tk.k_index <= first_ma34_idx

            if (
                s.ma34_cross_as_valid_gate
                and (not rule2)
                and ref_tk is not None
                and s.turning_ks
                and s.turning_ks[-1].mark != tk.mark
                and pre_ma34_candidate
            ):
                rule2 = True

        if s.ma34_cross_as_valid_gate:
            is_valid = rule1 and rule2 and rule_double_k
            is_perfect = rule3
        else:
            is_valid = rule1 and rule_double_k
            is_perfect = rule3 and rule2
        return is_valid, is_perfect, has_v

    def check_double_k_escape(self, ref_tk, end_tk, centers: list, bars: list) -> bool:
        if not centers:
            return True
        seg_upper = max(c.upper_rail for c in centers)
        seg_lower = min(c.lower_rail for c in centers)

        def get_3k(tk):
            idx = tk.k_index
            result = []
            if idx - 1 >= 0:
                result.append(bars[idx - 1])
            result.append(bars[idx])
            if idx + 1 < len(bars):
                result.append(bars[idx + 1])
            return result

        def two_k_above_lower(three_ks):
            for j in range(len(three_ks) - 1):
                if three_ks[j].low >= seg_lower and three_ks[j + 1].low >= seg_lower:
                    return True
            return False

        def two_k_below_upper(three_ks):
            for j in range(len(three_ks) - 1):
                if three_ks[j].high <= seg_upper and three_ks[j + 1].high <= seg_upper:
                    return True
            return False

        end_3k = get_3k(end_tk)
        if end_tk.mark == Mark.G:
            if two_k_above_lower(end_3k):
                return True
        else:
            if two_k_below_upper(end_3k):
                return True
        if ref_tk is not None:
            start_3k = get_3k(ref_tk)
            if ref_tk.mark == Mark.G:
                if two_k_above_lower(start_3k):
                    return True
            else:
                if two_k_below_upper(start_3k):
                    return True
        return False
