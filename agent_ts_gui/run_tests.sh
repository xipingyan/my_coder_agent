#!/usr/bin/env bash
# Regression suite for agent_ts_gui: backend pytest + GUI CDP smoke tests.
#
# Usage:
#   ./run_tests.sh            # everything (remote test skipped: needs a live server)
#   ./run_tests.sh backend    # backend pytest only
#   ./run_tests.sh gui        # all GUI CDP tests
#   ./run_tests.sh files      # one GUI test by name (files, theme, split, proj, ctxmenu, ...)
#   ./run_tests.sh remote     # remote test (start a standalone server on :8931 first)
#
# Exit code 0 = all green. Run it after every feature before calling it done.
set -u
cd "$(dirname "$0")"

target="${1:-all}"
failed=()

run_backend() {
  echo "=== backend pytest ==="
  if ! backend/.venv/bin/python -m pytest backend/tests/ -q; then
    failed+=("backend")
  fi
}

run_gui() { # $1 = cdp test file name (without .cjs)
  local name="$1"
  echo "=== gui: ${name} ==="
  if ! (cd gui && node "cdp_${name}_test.cjs"); then
    failed+=("${name}")
  fi
}

case "$target" in
  backend) run_backend ;;
  gui)
    for t in boot theme split proj proj_switch ctxmenu files local_add localpick; do
      run_gui "$t"
    done
    ;;
  remote)
    echo "(remote test needs a standalone server on 127.0.0.1:8931 - start it first)"
    run_gui remote
    ;;
  all)
    run_backend
    for t in boot theme split proj proj_switch ctxmenu files local_add localpick; do
      run_gui "$t"
    done
    ;;
  cdp_*_test.cjs|*.cjs) run_gui "$(basename "$target" .cjs | sed 's/^cdp_//')" ;;
  *)
    run_gui "$target"
    ;;
esac

echo
if [ "${#failed[@]}" -eq 0 ]; then
  echo "ALL TESTS PASSED"
else
  echo "FAILED: ${failed[*]}"
  exit 1
fi
