# Moore 引擎近期改动纪要

本文汇总本轮围绕 `300137` / `300371` 场景的关键代码调整，重点覆盖：转折K语义统一、异向边界约束、宏观预备轮、异向 MA5 刷新门槛。

## 1) 转折K命名与语义统一

### 背景
- 原字段名使用 `trigger_k / trigger_k_index`，语义上实际承担“转折确认K”的角色。
- 为避免“触发K vs 转折K”混淆，新增正式命名并保留兼容。

### 改动
- `czsc/moore/objects.py`
  - 在 `TurningK` 上新增属性别名：
    - `turning_k` ↔ `trigger_k`
    - `turning_k_index` ↔ `trigger_k_index`
  - 旧字段保留，不破坏现有调用。
- `czsc/moore/segment/scope_utils.py`
  - `get_trigger_index` 优先读取 `turning_k_index`，其次回退到 `trigger_k_index`。
- 同步替换核心调用侧为新语义（兼容旧字段）：
  - `czsc/moore/segment/analyzer.py`
  - `czsc/moore/segment/center.py`
  - `czsc/moore/segment/micro_engine.py`
  - `sz500_moore_plot.py`
  - `sz500_moore_echarts_plot.py`

## 2) 异向转折的搜索左边界与 3K 不重叠

### 背景
- 讨论中确认：异向新候选的 3K 不应与“上一个转折K”重合。
- 仅使用 `turning_k_index + 1` 会导致新 3K 仍可包含上一转折K（在左邻位）。

### 最终规则（已落地）
- 异向候选 3K 的第一根 K 左边界统一为：

`first_k_min_idx = max(last.k_index + 2, last.turning_k_index + 1)`

- 由于候选索引 `ext_idx` 表示 3K 中间K，等价约束为：

`ext_idx >= first_k_min_idx + 1`

- 对应代码：`czsc/moore/segment/micro_engine.py`
  - `search_start` 直接使用 `first_k_min_idx + 1`（中间K左边界）
  - 二次保险同时校验：
    - `ext_idx >= first_k_min_idx + 1`
    - `ext_idx - 1 >= first_k_min_idx`（第一根K不越界）

### 说明
- 同向刷新逻辑未套用此边界（保持原行为）。

## 3) 宏观审计新增“预备轮”（Pre-Round）

### 目标
- 在正式轮次回溯前，先尝试一次“抢跑”连接，仅用法则1（生长法则）审判。

### 最终语义（按需求收敛）
- 仅尝试固定一笔：
  - `start = idx - 2`
  - `end = idx + 1`
- 仅使用法则1（价格刷新 + 实体边际）
- 若失败，完整回退到原正式轮次（`idx-1`, `idx-2`, ...）

### 改动
- `czsc/moore/segment/macro_engine.py`
  - `audit_and_replay` 前置 `Pre-Round`
  - 新增 `_check_leap_growth_only(...)`

## 4) 异向候选 MA5 + 价格 双基线门槛（含失败候选推进）

### 背景
- 旧逻辑在异向场景中依赖 old/new scope 对比，难以表达“从段起点到候选”的持续门槛。
- 需求要求：即使候选被丢弃，也要把这次候选对应区间的 MA5 与价格极值并入运行基线。

### 设计
- 运行态基线放在 `SegmentState`：
  - `reversal_ma5_gate_mark`
  - `reversal_ma5_gate_start_k_index`
  - `reversal_ma5_gate_extreme`
  - `reversal_price_gate_mark`
  - `reversal_price_gate_start_k_index`
  - `reversal_price_gate_extreme`
- 结果态快照放在 `TurningK.cache`：
  - `leg_ma5_extreme`

### 行为
- 每次异向候选都会计算当前段（`last.k_index -> trigger_index`）的 MA5 与价格极值。
- 与运行基线比较（任一门槛满足即可）：
  - MA5：找顶 `current_max_ma5 > baseline`；找底 `current_min_ma5 < baseline`
  - 价格：找顶 `current_max_price > baseline`；找底 `current_min_price < baseline`
- 无论是否通过，运行基线都会推进（满足“失败候选也更新”的要求）。

### 改动
- `czsc/moore/segment/analyzer.py`
  - `SegmentState` 新增 `reversal_ma5_gate_*` 与 `reversal_price_gate_*` 字段
- `czsc/moore/segment/micro_engine.py`
  - 新增 `_compute_ma5_extreme(...)`
  - 新增 `_compute_price_extreme(...)`
  - 升级 `_check_and_update_reversal_ma5_gate(...)` 为 MA5/价格并行基线判定（OR）
  - 在异向候选流程接入该门槛
  - 在 `_confirm_candidate` 落盘 `final_tk.cache['leg_ma5_extreme']`

## 5) 起手三式的时间左边界分流（中枢起算点）

### 背景
- 讨论中确认：起手三式的“时间左边界”并不统一，不能继续共用同一搜索起点。
- 价格轨道（`upper_rail/lower_rail`）取值逻辑保持不变，仅调整时间起算点。

### 最终规则（已落地）
- `5K重叠`：从“转折K及其后”起算（并受 `last_center_end_idx` 约束）。
- `反正两穿`：动作窗口仍从 `center_line_k`（确认K）起算，最终时间左边界不早于确认K。
- `三笔`：从“转折K之前的顶/底K”（即线段物理锚点 `center_anchor_idx`）起算。

### 改动
- `czsc/moore/segment/analyzer.py`
  - `SegmentState` 新增 `center_trigger_k_index`（记录当前观测中枢对应转折K索引）。
- `czsc/moore/segment/center.py`
  - State 0 入场时写入 `center_trigger_k_index`，`rollback` 时清空。
  - 新增方法级左边界计算：
    - `_get_5k_search_start`
    - `_get_sanbi_search_start`
    - `_resolve_method_start_idx`
  - `_check_5k_overlap_with_idx` 改为使用转折K起算。
  - `_check_san_bi` 改为使用锚点极值起算。
  - `_check_center_formation` 在名分成立后立即同步 `center_start_k_index/center_start_dt`。
  - `_finalize_and_mount_center` 按方法级左边界固化 `start_k_index/start_dt`。

## 6) 已确认的行为结果（回放）

- `300137`（`20190415~20201130`）
  - 预备轮失败时可回退到原正式审计路径。
- `300371`（`20181220~20201030`）
  - 异向相邻转折点 3K 不再包含上一个转折K。

## 7) 备注

- 本文只总结本轮讨论直接相关变更。
- Working tree 中若有其它历史改动（例如绘图样式、中枢细节）不在本文主线内。
