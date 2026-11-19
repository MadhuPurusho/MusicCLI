#!/usr/bin/env bash
# ==============================================================================
# Pulse CLI Preflight Verification Suite
# Automated pre-submission verification runner for contributors.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Identify target Python runtime
if [[ -f "${HOME}/.pulse-cli/venv/bin/python3" ]]; then
    PYTHON_BIN="${HOME}/.pulse-cli/venv/bin/python3"
elif [[ -f "${ROOT_DIR}/.venv/bin/python3" ]]; then
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python3"
else
    PYTHON_BIN="$(which python3)"
fi

echo "================================================================================"
echo "  PULSE CLI // PREFLIGHT SUBMISSION VERIFICATION"
echo "  Target Interpreter: ${PYTHON_BIN}"
echo "================================================================================"

echo ""
echo "--> STEP 1: Syntax & Bytecode Compilation Check..."
"${PYTHON_BIN}" -m py_compile "${ROOT_DIR}/pulse/__init__.py" \
                              "${ROOT_DIR}/pulse/__main__.py" \
                              "${ROOT_DIR}/pulse/app.py" \
                              "${ROOT_DIR}/pulse/audio.py" \
                              "${ROOT_DIR}/pulse/dsp.py" \
                              "${ROOT_DIR}/pulse/metadata.py" \
                              "${ROOT_DIR}/pulse/queue_manager.py"
echo "    [SUCCESS] All Python source files compiled with zero bytecode errors."

echo ""
echo "--> STEP 2: Executing Regression Test Suite..."
"${PYTHON_BIN}" -m unittest discover -s "${ROOT_DIR}/tests" -v
echo "    [SUCCESS] Full regression test suite passed."

echo ""
echo "--> STEP 3: Executing Unified Contributor Invariant Verification Harness..."
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/verify_invariants.py"

echo ""
echo "================================================================================"
echo "  PREFLIGHT VERIFICATION COMPLETE: Ready for branch staging and PR creation."
echo "================================================================================"
