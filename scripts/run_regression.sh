#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# Smart Regression Runner
# ────────────────────────────────────────────────────────────────
# Usage:
#   bash scripts/run_regression.sh --scope smoke       # after any quick fix
#   bash scripts/run_regression.sh --scope story       # after story/webhook change
#   bash scripts/run_regression.sh --scope prompt      # after bot.py prompt change
#   bash scripts/run_regression.sh --scope data        # after product/price change
#   bash scripts/run_regression.sh --scope all         # full suite
#
# Scope → What runs:
#   smoke   → smoke test only                                (~3 min)
#   story   → Day 3 + smoke                                  (~8 min)
#   prompt  → core + data + Day1 + Day2 + Day3 + smoke       (~25 min)
#   data    → data tests only                                 (~varies)
#   all     → core + data + e2e + Day1 + Day2 + Day3 + smoke (~35 min)
# ────────────────────────────────────────────────────────────────

set -euo pipefail
cd "$(dirname "$0")/.."

SCOPE="${2:-smoke}"
for arg in "$@"; do
  case $arg in
    --scope) shift; SCOPE="${1:-smoke}" ;;
    smoke|story|prompt|data|all) SCOPE="$arg" ;;
  esac
done

BOLD="\033[1m"; GRN="\033[32m"; YLW="\033[33m"; RED="\033[31m"; RST="\033[0m"

header() {
  echo ""
  echo -e "${BOLD}════════════════════════════════════════════════════════"
  echo -e "  Regression Runner  —  scope: $SCOPE"
  echo -e "  $(date '+%Y-%m-%d %H:%M:%S')"
  echo -e "════════════════════════════════════════════════════════${RST}"
}

run_py() {
  local label="$1"
  local script="$2"
  echo ""
  echo -e "${BOLD}▶ Running: $label${RST}"
  python3 "scripts/$script" && echo -e "${GRN}  ✅ $label passed${RST}" \
                            || echo -e "${RED}  ❌ $label had failures${RST}"
}

header

case "$SCOPE" in

  smoke)
    echo -e "${YLW}  → Smoke test only${RST}"
    run_py "Smoke Test"        "test_smoke.py"
    ;;

  story)
    echo -e "${YLW}  → Story scope: Day 3 + Smoke${RST}"
    run_py "Day 3 Story Tests" "test_bot_day3.py"
    run_py "Smoke Test"        "test_smoke.py"
    ;;

  prompt)
    echo -e "${YLW}  → Prompt scope: Core + Data + Day1 + Day2 + Day3 + Day4 + Smoke${RST}"
    run_py "Core Behavior"     "test_core.py"
    run_py "Data Tests"        "test_data.py"
    run_py "Day 1 Tests"       "test_bot_day1.py"
    run_py "Day 2 Tests"       "test_bot_day2.py"
    run_py "Day 3 Tests"       "test_bot_day3.py"
    run_py "Day 4 Tests"       "test_bot_day4.py"
    run_py "Smoke Test"        "test_smoke.py"
    ;;

  data)
    echo -e "${YLW}  → Data scope: DB-driven tests only${RST}"
    run_py "Data Tests"        "test_data.py"
    ;;

  all)
    echo -e "${YLW}  → Full suite${RST}"
    run_py "Core Behavior"     "test_core.py"
    run_py "Data Tests"        "test_data.py"
    run_py "E2E Flows"         "test_e2e_flows.py"
    run_py "Day 1 Tests"       "test_bot_day1.py"
    run_py "Day 2 Tests"       "test_bot_day2.py"
    run_py "Day 3 Tests"       "test_bot_day3.py"
    run_py "Day 4 Tests"       "test_bot_day4.py"
    run_py "Smoke Test"        "test_smoke.py"
    ;;

  *)
    echo -e "${RED}Unknown scope: $SCOPE${RST}"
    echo "Valid scopes: smoke | story | prompt | data | all"
    exit 1
    ;;

esac

echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════"
echo -e "  Done — scope: $SCOPE"
echo -e "════════════════════════════════════════════════════════${RST}"
echo ""
