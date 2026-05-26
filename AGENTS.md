# Agent Instructions

Start here before running commands or editing code in this repository.

## Required Reading

Read [docs/safe_debugging.md](docs/safe_debugging.md) before running Python debugging or analysis commands.

## Hard Rules

1. Do not run anonymous stdin Python code such as `uv run python -` or `python - <<'PY'`.
2. Put debugging logic in a named script under `scripts/`, for example `scripts/debug_moore_case.py`.
3. For Python debugging or heavy analysis, use `scripts/run_safe_python.sh`.
4. Do not start long-running Python jobs in the background unless the user explicitly asks for it.
5. If a Python task may run longer than 2 minutes, state the purpose, expected runtime, and stop method before running it.

## Preferred Command Pattern

```bash
scripts/run_safe_python.sh scripts/debug_moore_case.py
```

## Notes

- Detailed policy and examples live in [docs/safe_debugging.md](docs/safe_debugging.md).
- `README.md` contains the user-facing project overview; this file is the agent-facing operational policy.
