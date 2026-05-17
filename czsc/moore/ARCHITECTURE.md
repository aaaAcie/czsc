# 摩尔缠论（Moore CZSC）代码架构说明

## 设计哲学

> 每一个时间级别的分析逻辑完全独立，不同级别之间通过**数据对象**（而非引擎）传递信息。  
> `MooreCZSC` 是对外的统一门面，随能力增长持续组合新的子分析器，对外接口始终稳定。

---

## 目录结构

```
czsc/moore/
│
├── ARCHITECTURE.md             # 本文件：架构总览
├── __init__.py                 # 统一对外导出
├── objects.py                  # 所有级别共用的数据对象定义
│
├── segment/                    # 【30分钟 线段级】完整独立分析模块
│   ├── 摩尔缠论核心定义-线段.md   # 线段级逻辑的完整定义与文档
│   ├── __init__.py             # 导出 SegmentAnalyzer
│   ├── analyzer.py             # SegmentAnalyzer：协调三个子引擎的主入口
│   ├── fractal.py              # 顶底识别引擎（FractalEngine）
│   ├── center.py               # 中枢识别引擎（CenterEngine）
│   └── trend.py                # 趋势穿透层（TrendEngine）
│
├── higher/                     # 【高级别结构】占位，待实现
│   └── __init__.py
│
└── analyze.py                  # MooreCZSC：顶层门面，组合所有子分析器
```

---

## 各层职责

### `objects.py` — 数据层
- 纯数据类（`@dataclass`），无任何业务逻辑，无状态
- 所有级别的分析器共用这里定义的数据类型
- 当前包含：`TurningK`、`MooreCenter`、`MooreSegment`
- 未来扩展：`HigherSegment`、`SegCenter` 等高级别对象

### `segment/` — 30分钟线段级分析模块
30分钟这一套逻辑（顶底引擎、中枢引擎、趋势穿透）**专属于该级别，不对其他级别复用**。

| 文件 | 类名 | 职责 |
|------|------|------|
| `analyzer.py` | `SegmentAnalyzer` | 协调三个引擎的主入口，管理 `update(bar)` 生命周期 |
| `fractal.py` | `FractalEngine` | 顶底触发、极值寻址、候选管理、四法则验真 |
| `center.py` | `CenterEngine` | 双轨中枢识别、K0 锚点、确认K、轨道定轨 |
| `trend.py` | `TrendEngine` | 趋势穿透判定、多层回溯吞噬、趋势状态翻转 |

三个引擎通过共享的 **`SegmentState`** 状态容器通信，互不直接调用。

### `analyze.py` — `MooreCZSC` 门面
- 对外唯一入口，`sz500_moore_plot.py` 等调用方只与此类交互
- 通过 `@property` 属性代理，将 `SegmentAnalyzer` 的输出透传到外部接口
- 未来加入 `HigherAnalyzer` 时，只在这里新增组合，**外部调用代码无需修改**

### `higher/` — 高级别结构（待实现）
- 以 `List[MooreSegment]` 为输入（而非原始 K 线），运行完全不同的识别逻辑
- 与 `segment/` 之间唯一的依赖是 `objects.py` 中的 `MooreSegment` 数据类型

---

## 数据流

```
原始K线 (RawBar)
    │
    ▼
SegmentAnalyzer          （30分钟专属逻辑）
    ├─ FractalEngine  →  turning_ks: List[TurningK]
    ├─ CenterEngine   →  all_centers: List[MooreCenter]
    └─ TrendEngine    →  trend_state, ghost_forks
    │
    │  (produces)
    ▼
List[MooreSegment]
    │
    ▼
HigherAnalyzer           （完全不同的逻辑，待实现）
    │  (produces)
    ▼
List[HigherSegment / SegCenter]
```

---

## 扩展规范

### 新增时间级别
1. 在对应目录（如 `higher/`）下创建独立子包
2. 新子包只依赖 `objects.py` 的数据类型，不 import 其他级别的引擎
3. 在 `MooreCZSC` 中新增属性组合新子分析器

### 新增数据对象
- 统一在 `objects.py` 中定义，保持各级别共用一个数据层

### 关于可视化
- `sz500_moore_plot.py` 当前保留在项目根目录，作为验证脚本
- 未来可迁移至 `moore/plot.py`，支持多级别叠加绘制
