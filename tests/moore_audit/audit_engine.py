import argparse
import collections
from datetime import datetime
from czsc.moore.analyze import MooreCZSC
from czsc.connectors import research
from czsc.py.enum import Direction, Mark
import czsc.moore.segment.center as mod_center
import czsc.moore.segment.fractal as mod_fractal
import czsc.moore.segment.trend as mod_trend

def unified_audit(symbol, sdt, edt, target_dates, flags):
    """
    摩尔缠论统一审计引擎入口
    支持按需开启：基础数据、中枢逻辑、顶底逻辑、趋势穿透审计。
    """
    print(f"\n{'='*80}")
    print(f" 🛡️  MOORE UNIFIED AUDIT ENGINE | {symbol} | {sdt} - {edt}")
    print(f" 🎯 Focus Dates: {target_dates}")
    print(f"{'='*80}\n")

    # 1. 保存原版指针 (用于猴子补丁恢复)
    orig_center = {
        "update": mod_center.CenterEngine.update,
        "formation": mod_center.CenterEngine._check_center_formation,
        "finalize": mod_center.CenterEngine._finalize_and_mount_center,
    }
    orig_fractal = {
        "update": mod_fractal.FractalEngine.update,
        "validate": mod_fractal.FractalEngine._validate_four_rules,
        "confirm": mod_fractal.FractalEngine._confirm_candidate,
    }

    # --- 辅助判断函数 ---
    def is_target(self):
        if not self.s.bars_raw: return False
        dt = str(self.s.bars_raw[-1].dt)
        return any(d in dt for d in target_dates)

    # 2. 定义【中枢引擎】审计补丁
    def center_audit_update(self, bar, k_index):
        curr_dt = str(bar.dt)
        hit = any(d in curr_dt for d in target_dates)
        if hit:
            ma5 = bar.cache.get('ma5', 0)
            state = self.s.center_state
            print(f"  [中枢👉 {curr_dt}] State:{state} | MA5:{ma5:.2f} | Rail:({self.s.center_lower_rail:.2f}-{self.s.center_upper_rail:.2f})")
            
            # 抢占逻辑审计
            if state == 2:
                formed = self._check_center_formation()
                new_k0 = self.s.latest_k0
                if new_k0:
                    print(f"    [🚩 抢占审计] 备选K0:{new_k0.dt} | Formed:{formed} | 是否允许刷新: {'❌' if formed else '✅'}")

        res = orig_center["update"](self, bar, k_index)
        if hit:
            print(f"  [中枢✅ {curr_dt}] NewState:{self.s.center_state} | Rail:({self.s.center_lower_rail:.2f}-{self.s.center_upper_rail:.2f})")
        return res

    # 3. 定义【顶底引擎】审计补丁
    def fractal_audit_validate(self, tk):
        res_valid, res_perfect = orig_fractal["validate"](self, tk)
        if is_target(self):
            print(f"  [顶底🕵️ 验真] {tk.dt} | {tk.mark.name} | 结果: Valid={res_valid}, Perfect={res_perfect}")
            # 如果不满足法则，可以打印具体失败原因（通过 state.debug_rule_fail 计数差值判断，此处简化）
        return res_valid, res_perfect

    def fractal_audit_confirm(self, final_tk, perfect):
        if is_target(self):
             print(f"  [顶底🔒 确立] {final_tk.dt} | {final_tk.mark.name} | Price:{final_tk.price} | Perfect:{perfect}")
        return orig_fractal["confirm"](self, final_tk, perfect)

    # 4. 应用补丁 (基于 Flags)
    if flags.get('audit_center'):
        mod_center.CenterEngine.update = center_audit_update
    if flags.get('audit_fractal'):
        mod_fractal.FractalEngine._validate_four_rules = fractal_audit_validate
        mod_fractal.FractalEngine._confirm_candidate = fractal_audit_confirm

    try:
        # 加载数据
        bars = research.get_raw_bars_origin(symbol, sdt=sdt, edt=edt)
        if not bars: bars = research.get_raw_bars_30m(symbol, sdt=sdt, edt=edt)
        if not bars: return print("❌ Error: No data found.")

        # A. 基础数据展示 (同 check_data_detailed.py)
        if flags.get('show_raw'):
            print(f"{'-'*30} [ RAW BARS INSPECTOR ] {'-'*30}")
            target_bars = [b for b in bars if any(d in str(b.dt) for d in target_dates)]
            print(f"{'Time':<20} | {'Open':<7} | {'Close':<7} | {'High':<7} | {'Low':<7} | {'Color':<5}")
            for b in target_bars:
                color = "🔴" if b.close < b.open else "🟢" if b.close > b.open else "⚪"
                print(f"{str(b.dt):<20} | {b.open:<7.2f} | {b.close:<7.2f} | {b.high:<7.2f} | {b.low:<7.2f} | {color:<5}")
            print(f"{'-'*85}\n")

        print(f"📊 Analyzing {len(bars)} bars...\n")
        engine = MooreCZSC(bars)

        # B. 最终审计报告
        print("\n" + "="*80)
        print(" 🔍 FINAL AUDIT SUMMARY")
        print("="*80)
        
        # 1. 线段与顶底报告
        print(f"\n[ 1. 顶底与线段 ]")
        for tk in engine.turning_ks:
            print(f"  {str(tk.dt):<20} | {tk.mark.name} | Price: {tk.price:<7.2f} | Perfect: {tk.is_perfect}")
        
        # 2. 趋势穿透与幽灵顶底 (Ghosts)
        if flags.get('show_ghosts'):
            print(f"\n[ 2. 趋势穿透与幽灵顶底 (Ghosts) ]")
            if not engine.ghost_forks:
                print("  None.")
            for fork_base, consumed_list in engine.ghost_forks:
                for ctk in consumed_list:
                    print(f"  👻 GHOST: {ctk.dt} | {ctk.mark.name} | Price: {ctk.price:.2f} (Swallowed by {fork_base.dt})")

        # 3. 中枢系统与归属审计
        print(f"\n[ 3. 中枢系统与线段归属审计 ]")
        for i, c in enumerate(engine.all_centers):
            anchor_dt = c.confirm_k.dt if c.confirm_k else c.start_dt
            # 寻找该中枢理论上属于哪根线段
            belong_seg = None
            reject_reason = "No matching segment"
            for s_idx, s in enumerate(engine.segments):
                time_match = (c.start_dt >= s.start_k.dt and c.end_dt <= s.end_k.dt)
                dir_match = (c.direction == s.direction)
                
                if time_match and dir_match:
                    belong_seg = f"线段#{s_idx}"
                    break
                elif time_match and not dir_match:
                    reject_reason = f"方向冲突(👻异类): 中枢{c.direction.name} vs 线段{s.direction.name}"
                elif not time_match and (s.start_k.dt <= anchor_dt <= s.end_k.dt):
                    reject_reason = f"生命周期跨越线段: 中枢从{c.start_dt}开始，而线段从{s.start_k.dt}才开始"

            status_str = f"✅ 属于 {belong_seg}" if belong_seg else f"⚠️ 游离 ({reject_reason})"
            print(f"  #{i:02d} | {c.start_dt} -> {c.end_dt} | {c.direction.name} | {status_str} | 轨:{c.lower_rail:.2f}-{c.upper_rail:.2f}")

        print("\n" + "="*80 + "\n")

    finally:
        # 恢复指针
        mod_center.CenterEngine.update = orig_center["update"]
        mod_fractal.FractalEngine._validate_four_rules = orig_fractal["validate"]
        mod_fractal.FractalEngine._confirm_candidate = orig_fractal["confirm"]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="摩尔缠论统一审计引擎")
    parser.add_argument('--symbol', default='sz002286', help='股票代码')
    parser.add_argument('--sdt', default='20200101', help='开始时间')
    parser.add_argument('--edt', default='20200828', help='结束时间')
    parser.add_argument('--target', nargs='+', default=['2020-03-20', '2020-06-05', '2020-06-10'], help='重点观测日期')
    
    # 开关
    parser.add_argument('--no-center', action='store_true', help='关闭中枢审计日志')
    parser.add_argument('--no-fractal', action='store_true', help='关闭顶底审计日志')
    parser.add_argument('--no-raw', action='store_true', help='关闭原始K线展示')
    parser.add_argument('--no-ghost', action='store_true', help='关闭幽灵顶底审计展示')

    args = parser.parse_args()
    
    config_flags = {
        'audit_center': not args.no_center,
        'audit_fractal': not args.no_fractal,
        'show_raw': not args.no_raw,
        'show_ghosts': not args.no_ghost
    }
    
    unified_audit(args.symbol, args.sdt, args.edt, args.target, config_flags)
