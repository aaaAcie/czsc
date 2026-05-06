# 日线级别线段模块重命名与一体化重构

## Summary
这次不再沿用过于概括的 `higher` 命名，直接把当前实现定位为“日线级别线段”模块来做，整体重命名为 `daily_segment`。

本次合并完成四件事：
- 模块解耦瘦身：对象、状态、纯算法、工具、调度总线拆开
- 安全锚点局部重建：主键 `k_index`，辅键 `dt` 双键校验
- B / D 点结构边界扫描：完全服从底层 `MooreSegment` 的结构定界
- 吞噬线段 VIP 放行：`is_macro_swallow=True` 的宏观线段直接自成日线级别线段并刷新锚点

命名策略：
- 当前 `czsc/moore/higher/` 直接重命名为 `czsc/moore/daily_segment/`
- 当前 `HigherAnalyzer / HigherCenter / HigherSegment / HigherState` 分别改为
  - `DailySegmentAnalyzer`
  - `DailySegmentCenter`
  - `DailySegment`
  - `DailySegmentState`

这样当前语义最准确；未来周线实现时，再按同样方式新增平级模块，而不是让 `higher` 继续承担模糊语义。

## Key Changes
### 1. 模块与类型统一改名
目录改名：
- `czsc/moore/higher/` -> `czsc/moore/daily_segment/`

核心类型改名：
- `HigherAnalyzer` -> `DailySegmentAnalyzer`
- `HigherCenter` -> `DailySegmentCenter`
- `HigherSegment` -> `DailySegment`
- `HigherState` -> `DailySegmentState`

文件结构改为：
- `czsc/moore/daily_segment/objects.py`
- `czsc/moore/daily_segment/state.py`
- `czsc/moore/daily_segment/center_algo.py`
- `czsc/moore/daily_segment/utils.py`
- `czsc/moore/daily_segment/analyzer.py`
- `czsc/moore/daily_segment/__init__.py`

说明：
- 本次不保留 `higher` 作为主实现目录
- 若需要兼容旧导入路径，可在 `czsc/moore/higher/__init__.py` 暂留一层转发壳；若你希望干净切换，也可以不保留兼容层
- 默认建议保留一层兼容导出，避免已有调用立刻失效

### 2. 模块解耦瘦身
职责拆分如下：

`objects.py`
- `DailySegmentCenter`
- `DailySegment`

`state.py`
- `DailySegmentState`
- 收纳全部运行态、锚点、缓存态

`center_algo.py`
- 四大 Gate
- BADC 上下轨纯算法
- 不持有 analyzer 状态

`utils.py`
- 均线计算
- segment 索引/价格 helper
- 锚点切片与快照 helper

`analyzer.py`
- 仅负责事件流转、规则判定、状态调度
- 不定义 dataclass
- 不内嵌大段纯算法
- 不承载公共工具实现

### 3. 安全锚点局部重建
`DailySegmentState` 中新增并收口以下字段：
- `anchor_k_index: Optional[int]`
- `anchor_dt: Optional[datetime]`
- `anchor_completed_segments: List[DailySegment]`

双键校验规则必须写死：
- 主键：`k_index`
- 辅键：`dt`
- 局部重建切片主条件：`seg.start_k.k_index >= anchor_k_index`
- 命中的首段必须满足：`seg.start_k.dt == anchor_dt`
- 若 `k_index` 命中但 `dt` 不一致，判定锚点失真，本次退回全量重建

局部重建路径：
1. 先全量重算 `ma34` / `ma170` 数组
2. 恢复 `anchor_completed_segments`
3. 清空运行态
4. 仅对锚点之后的危险区线段重放状态机

锚点推进时机：
- 普通日线级别线段归库成功后
- 吞噬线段 VIP 直通归库后

### 4. B / D 点扫描边界明确入算法层
这两条边界必须在 `center_algo.py` 中硬编码为结构规则，不允许退回启发式逻辑：

- B 点右扫死线 = `seg_34.end_k.k_index`
- D 点右扫死线 = `seg_56.end_k.k_index`

具体要求：
- B 点：
  - 先在 `seg_23` 内从右向左扫描
  - 若未找到，再向右扫描到 `seg_34.end_k.k_index`
  - 不允许任何固定根数对称扩展
- D 点：
  - 从 B 点之后向右扫描
  - 扫描终点严格为 `seg_56.end_k.k_index`
  - 不允许任何“多扫几根”补偿

抽出的纯函数建议为：
- `find_center(segments, ma34)`
- `find_b_point(seg_23, seg_34, ma34, sign)`
- `find_a_point(seg_12, b_idx, ma34, sign)`
- `find_d_point(b_idx, scan_end_index, ma34, sign)`
- `find_c_point(b_idx, d_idx, ma34, sign)`
- `find_local_extreme(...)`
- `check_ma34_overlap(...)`

### 5. 吞噬线段 VIP 放行
在 `DailySegmentAnalyzer._process_new_segment(new_seg)` 首行加入最高优先级分支：

当 `new_seg.cache["is_macro_swallow"]` 为真时：
1. 若当前存在运行态 `current_segments`
   - 先结束旧时代
   - 旧时代仍按普通归库逻辑处理
2. `new_seg` 本身直接归库为一条单段 `DailySegment`
   - 不再经过顺势判定
   - 不再经过 MA34 / MA170 交叉判定
   - 不依赖 active center / pending break
3. 归库后清空运行态
4. 立即刷新安全锚点
5. 在结果 `cache` 中写入 `from_macro_swallow=True`

实现要求：
- 不直接复用原普通 `_commit_and_reset_system(break_seg)` 语义来处理吞噬线段
- 需要独立私有方法，例如：
  - `_commit_current_running_epoch_if_needed()`
  - `_commit_swallow_segment_directly(new_seg)`

### 6. 公共 helper 下沉到 `utils.py`
抽出纯工具函数：
- `seg_start_price`
- `seg_end_price`
- `seg_start_index`
- `seg_end_index`
- `collect_bars_by_index`
- `build_sma_array`
- `safe_ma_value`
- `slice_segments_from_anchor`
- `clone_completed_segments_snapshot`

要求：
- 不参与业务判定
- 不引用 analyzer 实例
- 能纯函数就纯函数

### 7. `analyze.py` 与顶层导出同步改名
更新 `czsc/moore/analyze.py`：
- `self.higher_analyzer` 改为 `self.daily_segment_analyzer`
- 对外属性同步改名为更准确的日线语义，例如：
  - `daily_segments`
  - `daily_current_segments`
  - `daily_active_center`
  - `daily_archived_centers`
  - `daily_candidates`

兼容策略：
- 若你希望平滑迁移，则保留旧属性 `higher_*` 作为兼容 alias，内部转发到新的 `daily_*`
- 默认建议保留 alias，一次重构不顺带打断上层使用方

更新 `czsc/moore/__init__.py`：
- 导出 `DailySegmentAnalyzer`
- 导出 `DailySegmentCenter`
- 导出 `DailySegment`

若保留兼容：
- 旧 `HigherAnalyzer / HigherCenter / HigherSegment` 可以暂时作为 alias 指向新类型
- 仅在注释中标明“旧名兼容，后续可清理”

## Public APIs / Types
新的主命名：
- `DailySegmentAnalyzer`
- `DailySegmentCenter`
- `DailySegment`
- `DailySegmentState`

新的对外属性建议：
- `MooreCZSC.daily_segments`
- `daily_current_segments`
- `daily_active_center`
- `daily_archived_centers`
- `daily_candidates`

兼容层建议保留：
- `HigherAnalyzer = DailySegmentAnalyzer`
- `HigherCenter = DailySegmentCenter`
- `HigherSegment = DailySegment`
- `MooreCZSC.higher_*` 属性转发到 `daily_*`

## Test Plan
测试统一使用 `uv` 环境。

建议命令：
- `uv run python -m py_compile ...`
- `uv run pytest ...`

测试场景：
1. 导入兼容
- `from czsc.moore.daily_segment import DailySegmentAnalyzer, DailySegmentCenter, DailySegment`
- 若保留兼容层，再验证 `from czsc.moore.higher import HigherAnalyzer`
- 顶层 `from czsc.moore import DailySegmentAnalyzer` 正常

2. 对象迁移一致性
- `DailySegmentCenter` / `DailySegment` 属性与旧行为一致

3. 双键锚点局部重建
- `anchor_k_index` 切片成功
- 首段 `dt` 一致时走局部重建
- 首段 `dt` 不一致时退回全量重建

4. B 点结构边界扫描
- 仅在 `seg_34.end_k.k_index` 边界内找到 B 点
- 不越界扫描

5. D 点结构边界扫描
- D 点扫描截止于 `seg_56.end_k.k_index`
- 不做固定根数扩展

6. 吞噬线段 VIP
- 单独吞噬线段
- 运行态中途吞噬线段
- 连续吞噬线段
- 都应直接归库并刷新锚点

7. 普通归库与局部重建回归
- 无锚点全量重建结果正确
- 有锚点后仅重算危险区
- 结果与从头全量重建一致

8. 兼容属性验证
- 若保留 alias：
  - `higher_segments` 与 `daily_segments` 指向一致
  - `higher_active_center` 与 `daily_active_center` 一致

## Assumptions
- 当前模块的真实业务语义就是“日线级别线段”，因此直接改名为 `daily_segment`
- 未来周线会作为新的平级模块实现，而不是继续塞进一个语义模糊的 `higher`
- 安全锚点双键校验是硬约束：
  - 主键 `k_index`
  - 辅键 `dt`
- B / D 点扫描结构边界是硬约束：
  - `B -> seg_34.end_k.k_index`
  - `D -> seg_56.end_k.k_index`
- 实施与测试统一使用 `uv`
