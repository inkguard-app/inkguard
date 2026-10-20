"""Shared test setup.

Pseudonym mode needs a persistent root key and a code table (inkguard/onyx.py).
The suite ASSIGNS both — never "use one if already set" — so a developer's
real key or a practice's real table in the shell can never be read or written
by a test. The table is created fresh, once per session, in a temp folder.
"""
from __future__ import annotations

import os
import tempfile

os.environ["PHI_ROOT_KEY"] = "ab" * 32
_TABLE_DIR = tempfile.mkdtemp(prefix="code-table-tests-")
os.environ["PHI_CODE_TABLE"] = os.path.join(_TABLE_DIR, "code_table.json")

from inkguard import onyx  # noqa: E402

onyx.create_code_table(os.environ["PHI_CODE_TABLE"], onyx.pseudonym_key())


# ---------------------------------------------------------------------------
# Running the tests in a different ORDER is a test in its own right
# ---------------------------------------------------------------------------
#
# State one test leaves behind — a window never closed, a standing
# instruction never taken down, a file never deleted — shows up as a failure
# in a LATER test, and only a different order finds it. So the order is
# switchable, from tools/run_tests.sh:
#
#   PHI_TEST_ORDER=reverse    backwards
#   PHI_TEST_ORDER=shuffled   random, with PHI_TEST_SEED to repeat it exactly
#
# It is done here, as a collection hook, rather than by naming every test on
# the command line: a thousand test names do not fit on one.

def pytest_collection_modifyitems(session, config, items):
    import random
    order = os.environ.get("PHI_TEST_ORDER", "").strip().lower()
    if order == "reverse":
        items.reverse()
    elif order == "shuffled":
        seed = os.environ.get("PHI_TEST_SEED") or "0"
        random.Random(int(seed)).shuffle(items)
    if order in ("reverse", "shuffled"):
        print(f"\ntest order: {order}"
              + (f" (seed {os.environ.get('PHI_TEST_SEED', '0')})" if order == "shuffled" else ""))


def pytest_runtest_logreport(report):
    """Print a failure THE MOMENT it happens, not at the end of the run.

    If a later step hangs (a browser that stops answering, say), the end of
    the run never comes, and a failure kept back for the summary is lost
    with it. Whoever runs the suite then sees an F and nothing else."""
    if report.failed:
        import sys
        sys.stderr.write(f"\n--- {report.nodeid} failed during {report.when} ---\n")
        sys.stderr.write(str(report.longrepr)[-6000:] + "\n")
        sys.stderr.flush()
