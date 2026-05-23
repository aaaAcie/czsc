#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/run_safe_python.sh [--timeout SECONDS] [--grace SECONDS] script.py [args...]

Behavior:
  - runs `uv run python ...` in the foreground
  - applies a hard timeout
  - sends SIGTERM first, then SIGKILL after a short grace period

Environment:
  SAFE_PYTHON_TIMEOUT   default timeout in seconds, default: 600
  SAFE_PYTHON_GRACE     grace period before SIGKILL, default: 10
EOF
}

timeout_seconds="${SAFE_PYTHON_TIMEOUT:-600}"
grace_seconds="${SAFE_PYTHON_GRACE:-10}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --timeout)
            timeout_seconds="$2"
            shift 2
            ;;
        --grace)
            grace_seconds="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            break
            ;;
    esac
done

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

script_path="$1"
shift

if [[ ! -f "$script_path" ]]; then
    echo "Script not found: $script_path" >&2
    exit 2
fi

if [[ "$timeout_seconds" =~ [^0-9] ]] || [[ "$grace_seconds" =~ [^0-9] ]]; then
    echo "timeout and grace must be non-negative integers" >&2
    exit 2
fi

if [[ "$script_path" == "-" ]]; then
    echo "Refusing to run anonymous stdin code. Use a named script file instead." >&2
    exit 2
fi

echo "[safe-python] timeout=${timeout_seconds}s grace=${grace_seconds}s"
echo "[safe-python] exec: uv run python $script_path $*"

child_pid=""
watchdog_pid=""
timed_out_file="$(mktemp)"
cleanup() {
    rm -f "$timed_out_file"
    if [[ -n "${watchdog_pid}" ]]; then
        kill "$watchdog_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT

uv run python "$script_path" "$@" &
child_pid=$!

(
    sleep "$timeout_seconds"
    if kill -0 "$child_pid" 2>/dev/null; then
        echo "1" > "$timed_out_file"
        echo "[safe-python] timeout reached; sending SIGTERM to $child_pid" >&2
        kill -TERM "$child_pid" 2>/dev/null || true
        sleep "$grace_seconds"
        if kill -0 "$child_pid" 2>/dev/null; then
            echo "[safe-python] grace expired; sending SIGKILL to $child_pid" >&2
            kill -KILL "$child_pid" 2>/dev/null || true
        fi
    fi
) &
watchdog_pid=$!

set +e
wait "$child_pid"
exit_code=$?
set -e

kill "$watchdog_pid" 2>/dev/null || true

if [[ -s "$timed_out_file" ]]; then
    echo "[safe-python] command timed out" >&2
    exit 124
fi

exit "$exit_code"
