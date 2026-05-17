### 计划标题
`micro_engine` Helper 化重构 + 双通道基线回归（保证前后逻辑/边界/细节不变）

### 摘要
你说得对，核心不是“多测一点”，而是**helper 化重构时严格保持行为等价**。  
这版计划把“helper 拆分”作为主线，把 baseline/audit 作为行为守护网，目标是：重构后细节、边界、时序、判定结果与重构前一致。

### Helper 化主线（重点）
1. **重构目标**
   - `micro_engine` 仅保留流程编排与依赖注入；
   - 规则实现下沉到 `helpers/micro_engine/*`；
   - 不改策略语义、不改判定口径、不改外部接口。

2. **模块拆分清单（按风险从低到高）**
   - 已有并保留：
     - `candidate_commit.py`（候选落盘/同向替换）
     - `delayed_judgement.py`（延迟结算链）
   - 继续拆分：
     - `trigger_gate.py`（触发门与同/反向触发判定）
     - `extreme_locator.py`（极值寻址与 rule1-local 回退）
     - `reversal_gate.py`（快照包络与异向准入门控）
     - `refresh_physics.py`（同向刷新物理比较）
     - `rule_validator.py`（四法则 + 独立两K）
     - `segment_builder.py`（锁定重建 + 线段重建）

3. **等价性约束（硬约束）**
   - 以下内容必须逐项不变：
     - 判定边界：区间开闭、`start_idx/end_idx`、触发/候选时序
     - 状态迁移：`candidate_tk` 生命周期、`waiting_special_rule` 回调路径
     - 端点写入：`micro_id` 分配、`turning_tk_store` 写入时机
     - 延迟链：节点 stage 迁移顺序与幂等行为
     - 线段与中枢挂载：`is_perfect` 重算口径、`swallow` 映射语义
   - 任何 helper 若需新增参数，只能是“显式注入”，不能改业务语义。

### 双通道回归（守住“前后不变”）
1. **审计通道（语义真源）**
   - 扩展 `tests/moore_audit/audit_engine.py`：支持 `--json` 结构化输出（保留原文本报告）。
   - 输出字段覆盖：turning、segments、centers、ghost、delayed-events、counts。

2. **断言通道（高价值门禁）**
   - 保留并增强 `tests/moore/test_delayed_judgement_chain.py`：
     - 关键微观点
     - 中枢命中
     - 延迟链关键事件序列
     - 关键 turning 日期与计数
   - 新增 `tests/moore/test_micro_engine_baseline_regression.py` 对比基线文件。

3. **基线资产（可复用）**
   - `tests/moore/baselines/micro_engine_refactor_v1.json`
   - 生成脚本：`tests/moore/gen_micro_engine_baseline.py`（调用 `audit_engine --json`）
   - 校验脚本/测试：读取 baseline 后重跑同场景并输出最小 diff。

4. **场景集（最小扩展）**
   - 核心：`300490`, `300371`, `300339`
   - 扩展：`002346`, `300137`
   - 覆盖你要求的断言维度：关键微观点、中枢命中、延迟链事件、关键 turning 计数。

### 执行顺序与验收
1. 先冻结 `v1` baseline（当前行为快照）。  
2. 按 helper 模块分批迁移（每次只动一个模块）。  
3. 每一批都必须通过：
   - `test_delayed_judgement_chain.py`
   - `test_micro_engine_baseline_regression.py`
4. 任一字段漂移即回滚该批重构，定位后再迁移。  
5. 全部分批完成后，`micro_engine` 主文件应显著瘦身，且回归结果与 `v1` 一致。

### 假设
- 在线数据源继续使用；
- 若未来有“有意策略变更”，必须升 baseline 版本（`v2`），不得覆盖 `v1`。
