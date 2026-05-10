### 计划标题
实现“延迟结算链”重构：改用角色中立节点（无 A/B/C 固定字段）+ 有序待定队列

### 摘要
将当前“首个异向点即回退”的即时审判，升级为“先接受同向刷新、后延迟结算”的链式机制。  
数据结构从 `A/B/C` 命名改为**角色中立字段 + 引用关系**，避免语义混淆并支持多级依存推进。

### 实现变更（决策完成）
1. **新增待定链状态（SegmentState）**
   - `pending_judgements: deque[int]`：按时间顺序的待定节点 ID 队列。
   - `judgement_nodes: dict[int, PendingNode]`：节点索引。
   - `judgement_id_seed: int`：自增 ID。
   - `last_resolve_anchor_id: Optional[int]`：避免同一锚点重复结算。

2. **节点模型改为角色中立（替代 A/B/C 字段）**
   - `id`
   - `base_id`：刷新前端点（旧端点）
   - `candidate_id`：刷新后端点（新端点）
   - `resolve_anchor_id`：触发最终结算的真实锚点（原 C real）
   - `stage`：`wait_anchor_start | wait_anchor_real | ready_resolve | resolved | cancelled`
   - `resolution`：`keep_candidate | rollback_base`
   - `parent_id / child_ids`：依赖关系
   - `created_k_idx / created_dt / resolved_k_idx / resolved_dt`

3. **时序规则**
   - 同向刷新确立时：创建节点（`base_id -> candidate_id`）入队，`stage=wait_anchor_start`。
   - 首个异向确立时：仅推进到 `wait_anchor_real`，不结算。
   - 异向腿“封口”后（真实锚点出现）：写入 `resolve_anchor_id`，置 `ready_resolve`。
   - 结算器按队列顺序执行：
     - 比较 `candidate -> resolve_anchor` 与 `base -> resolve_anchor` 的实/虚质量；
     - 生成 `resolution` 并回写 turning 链；
     - 若回写改变基底，沿 `child_ids` 级联推进后续节点重评估。

4. **替换点**
   - 在 `MicroStructureEngine._confirm_candidate` 中移除“首异向即时回退”分支。
   - 追加两个内部步骤：
     - `enqueue_or_advance_pending_judgement(...)`
     - `resolve_ready_judgements(...)`
   - 保留现有 `refreshed_segments`（用于可视化“先刷新后结算”）。

5. **可观测性（测试可读）**
   - 新增内部调试接口/快照：返回节点 stage、resolution、anchor 与父子关系变化。
   - 不变更外部绘图 API。

### 测试计划
1. **主场景（300490）**
   - 断言 `2020-07-02` 后出现 `base=2020-04-28, candidate=2020-06-30` 节点并入队。
   - 断言 `2020-07-14` 时仅进入 `wait_anchor_real`，未结算。
   - 断言 `V29=2020-09-09` 作为真实锚点触发 `ready_resolve -> resolved`。
   - 断言最终 turning 序列稳定且与结算规则一致。
2. **链式依存**
   - 至少一个“父子节点”案例：父节点结算后触发子节点重评估。
3. **幂等**
   - 同一 `resolve_anchor_id` 不重复结算。
4. **回归保护**
   - `300371` 宏观吞噬场景保持通过。
   - `2019-02-20/02-21/02-25` 的 5K+离开K时间边界保持通过。

### 假设
- 继续使用在线数据源进行该阶段验证（后续可再补本地夹具版）。
- `resolve_anchor` 口径沿用你确认的“异向腿封口后的真实端点”定义。
