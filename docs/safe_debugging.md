# 安全调试约定

这份约定用于避免 `uv run python -`、长时间后台运行、匿名 heredoc 调试代码等情况再次把 CPU 持续跑满。

## 默认规则

1. 调试代码优先写成具名脚本，例如 `scripts/debug_moore_case.py`。
2. 不直接运行匿名标准输入代码，例如 `uv run python -` 或 `python - <<'PY'`。
3. 所有重计算调试任务默认前台运行，禁止无提示后台常驻。
4. 超过 30 秒的 Python 调试任务必须加超时。
5. 超过 2 分钟的任务应当先说明用途、预计耗时和停止方式。

## 推荐用法

先把临时调试逻辑写到具名文件：

```bash
mkdir -p scripts
```

```python
# scripts/debug_moore_case.py
from czsc.moore.analyze import MooreCZSC

print("debug here")
```

再通过统一入口运行：

```bash
scripts/run_safe_python.sh scripts/debug_moore_case.py
```

自定义超时：

```bash
scripts/run_safe_python.sh --timeout 1800 scripts/debug_moore_case.py
```

## 统一入口说明

`scripts/run_safe_python.sh` 会做这些事：

- 统一使用 `uv run python`
- 默认超时 `600` 秒
- 超时后先发 `SIGTERM`
- 宽限 `10` 秒后仍未退出，再发 `SIGKILL`
- 拒绝执行 `-` 形式的匿名 stdin 代码

环境变量：

```bash
export SAFE_PYTHON_TIMEOUT=900
export SAFE_PYTHON_GRACE=15
```

## 不推荐的方式

```bash
uv run python - <<'PY'
...
PY
```

```bash
uv run python some_heavy_debug.py >/tmp/debug.log 2>&1 &
```

这两种方式都容易留下不透明、难追踪的长任务。
