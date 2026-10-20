#!/usr/bin/env bash
# Run the test suite so that it CANNOT hang.
#
# Whoever supports this software has to be able to run the tests and get an
# answer, every time, without watching them. So this script is the one way
# in, and it carries four failsafes:
#
#   1. PER-TEST TIMEOUT — a test that sticks is killed and reported as a
#      failure with its own traceback, instead of stopping the run. Nothing
#      can wait for ever.
#   2. WHOLE-RUN TIMEOUT — the run itself is capped. If it is hit, the
#      script says so and exits non-zero rather than appearing to pass.
#   3. NO STALE RUNS — every "python3 -m pytest" process still running on
#      this machine is stopped first (kill -9), whichever project started
#      it. Two runs on one machine starve each other and look like a hang.
#   4. LIVE OUTPUT — unbuffered, so progress is visible while it works.
#
# Modes (ORDER IS A TEST IN ITSELF: state left behind by one test shows up
# as a failure in another, and only a different order finds it):
#   ./tools/run_tests.sh            every test, file order
#   ./tools/run_tests.sh reverse    every test, backwards
#   ./tools/run_tests.sh shuffled   every test, in a random order (seeded)
#   ./tools/run_tests.sh screens    the browser tests only
#   ./tools/run_tests.sh quick      everything except the browser tests
set -uo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-all}"
PER_TEST="${PER_TEST:-900}"          # seconds; a batch of notes is slow, not endless
WHOLE_RUN="${WHOLE_RUN:-5400}"       # seconds
export PYTHONUNBUFFERED=1

stale=$(pgrep -f "python3 -m pytest" | grep -v "^$$\$" || true)
if [ -n "${stale}" ]; then
  echo "stopping an earlier test run (${stale//$'\n'/ })"
  echo "${stale}" | xargs -r kill -9 2>/dev/null || true
  sleep 1
fi

ARGS=(-q -p no:cacheprovider "--timeout=${PER_TEST}" "--timeout-method=thread")

# PARALLEL WORKERS — one process on fewer than four cores; on four or more,
# half the cores, but only when the pytest-xdist plugin is installed;
# without it everything runs in one process.
#
# Each worker is its own process and loads its own copy of the language
# model (about 600 MB) before it runs a single test. On two cores that was
# measured as a loss: the browser tests took 3m45s in one process and 8m56s
# across two workers, with one test failing under the contention. Workers
# are never used for the browser tests, which want the machine to
# themselves. WORKERS=<n> overrides; WORKERS=1 turns workers off.
CORES=$(nproc 2>/dev/null || echo 2)
if [ -z "${WORKERS:-}" ]; then
  if [ "${CORES}" -ge 4 ] && [ "${MODE}" != "screens" ] \
     && python -c "import xdist" >/dev/null 2>&1; then WORKERS="$(( CORES / 2 ))"
  else WORKERS=1; fi
fi
if [ "${WORKERS}" != "1" ]; then
  echo "using ${WORKERS} workers (${CORES} cores)"
  ARGS+=("-n" "${WORKERS}" "--dist" "loadfile")   # whole files per worker: fixtures stay shared
fi

case "${MODE}" in
  all)      SELECT=() ;;
  screens)  SELECT=(tests/test_desktop_ui.py) ;;
  quick)    SELECT=(--ignore=tests/test_desktop_ui.py) ;;
  reverse)
    export PHI_TEST_ORDER=reverse
    SELECT=()
    ;;
  shuffled)
    export PHI_TEST_ORDER=shuffled
    export PHI_TEST_SEED="${SEED:-$RANDOM}"
    echo "random order, seed ${PHI_TEST_SEED} (pass SEED=${PHI_TEST_SEED} to repeat it exactly)"
    SELECT=()
    ;;
  *) echo "unknown mode: ${MODE}"; exit 2 ;;
esac

run_pytest() {                       # one pytest process, capped both ways
  timeout --signal=INT --kill-after=30 "${WHOLE_RUN}" \
    python3 -m pytest "${ARGS[@]}" "$@"
  local code=$?
  if [ "${code}" -eq 124 ] || [ "${code}" -eq 137 ]; then
    echo "THE RUN WAS CUT OFF by its own ${WHOLE_RUN}s limit."
    echo "Nothing is proven by a run that did not finish: raise WHOLE_RUN, or run"
    echo "the parts one at a time (quick, then screens)."
    return 1
  fi
  return "${code}"
}

# THE SCREENS, ONE PROCESS PER TEST, WITH ONE RETRY.
#
# The browser tests drive a real browser through Playwright. In roughly one
# full run in three, the browser driver has been seen to stop
# answering a single command, with the app idle and healthy (batch ready,
# no request in flight, every thread alive). That is the test robot
# failing, not the app, and it wedges the whole process. So each screen
# test runs in its own process with a short limit: a wedged robot costs one
# test a retry, never the run. A test that fails twice is a real failure.
# Retries are printed by name, so a test that keeps needing one is visible.
SCREEN_LIMIT="${SCREEN_LIMIT:-240}"
run_screens() {
  local ids failed=() retried=() id n=0 total
  mapfile -t ids < <(python3 -m pytest --collect-only -q -p no:cacheprovider \
                       tests/test_desktop_ui.py 2>/dev/null | grep "::")
  total=${#ids[@]}
  if [ "${total}" -eq 0 ]; then echo "no screen tests were found"; return 1; fi
  for id in "${ids[@]}"; do
    n=$((n + 1))
    if timeout --kill-after=10 "${SCREEN_LIMIT}" python3 -m pytest -q -p no:cacheprovider \
         "--timeout=$((SCREEN_LIMIT - 20))" --timeout-method=thread "${id}" > /tmp/screen-test.log 2>&1; then
      printf "."
    else
      printf "r"
      if timeout --kill-after=10 "${SCREEN_LIMIT}" python3 -m pytest -q -p no:cacheprovider \
           "--timeout=$((SCREEN_LIMIT - 20))" --timeout-method=thread "${id}" > /tmp/screen-test.log 2>&1; then
        retried+=("${id}")
      else
        failed+=("${id}")
        echo; echo "FAILED twice: ${id}"; tail -40 /tmp/screen-test.log
      fi
    fi
  done
  echo
  echo "screens: $((total - ${#failed[@]})) of ${total} passed"
  for id in "${retried[@]}"; do echo "  passed on its retry: ${id}"; done
  [ "${#failed[@]}" -eq 0 ]
}

start=$(date +%s)
case "${MODE}" in
  all|reverse|shuffled)
    # TWO PROCESSES, ON PURPOSE. The browser tests each drive a real batch
    # through the engine in a real browser; the whole suite and a browser in
    # ONE process is a lot of memory, and the first thing to suffer is the
    # browser, which then waits for a page that never settles. Run as two
    # processes they are both comfortable, and a leak in one cannot starve
    # the other (this showed up when the suite was run backwards).
    echo "— everything except the screens —"
    run_pytest --ignore=tests/test_desktop_ui.py
    quick_code=$?
    echo "— the screens —"
    run_screens
    screens_code=$?
    code=$(( quick_code > screens_code ? quick_code : screens_code ))
    ;;
  screens)
    run_screens
    code=$?
    ;;
  *)
    run_pytest "${SELECT[@]}"
    code=$?
    ;;
esac
took=$(( $(date +%s) - start ))
echo "finished in ${took}s (exit ${code})"
exit "${code}"
