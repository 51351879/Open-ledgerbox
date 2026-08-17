# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the frontend's ``node --test`` suite from pytest.

**Why there is a bridge at all.** Until now everything guarding ``web/`` was a
grep: no ``innerHTML``, nothing off-origin, no file past 400 lines, an SPDX
header on each. Those check that a file is not a certain *shape*; not one of
them can say whether a line of it computes the right answer. ``docs/STATUS.md``
§6 named ``node:test`` as the path for the pure functions and nothing had taken
it, so the first defect that lived in that gap was found by an acceptance round
constructing a clock rather than by the suite: on 31 May, "Last month" selected
*this* month.

A second entry point nobody runs is not coverage. This exists so that
``python -m pytest`` — the one command this project's docs tell you to run — is
also the command that runs the JavaScript.

**A missing ``node`` is a skip and never a failure**, in the shape
``LEDGERBOX_BEAN_CHECK`` established: an external oracle that is absent has not
disagreed with anything. The ``web`` job in ``.github/workflows/ci.yml`` is
where its absence is turned into an error, because that is the machine where it
is a promise rather than a preference.

That job was added after this sentence claimed it. The docstring named "the CI
job" while nothing in the workflow mentioned node at all — and because GitHub's
runners happen to ship one, the tests really were running and the claim was
still describing something that did not exist. It would have become true the
day an image stopped shipping node, in the form ``docs/STATUS.md`` §5.31 is
about: a green CI over twenty tests nobody ran.

**The suite is run three times, in three timezones.** The module under test
builds dates from the local calendar precisely so that it cannot repeat the
predecessor's UTC bug, and the assertion that separates a local-calendar
implementation from a ``toISOString()`` one is vacuous in UTC itself — no
instant has two different dates there. One run east of Greenwich and one west
of it are what make that test able to fail.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where the JavaScript tests live.
JS_TESTS = REPO_ROOT / "tests" / "js"

#: What is handed to ``node --test``: a glob, relative to the repository root,
#: expanded by node rather than by a shell. A **directory** is what the runner
#: took in Node 18-20 and it is now resolved as a module instead — on Node 25
#: ``node --test tests/js`` fails with ``Cannot find module``. A pattern is
#: accepted by both, and it keeps the property that mattered about the
#: directory: a new file is picked up by being written, not by being registered.
JS_TEST_GLOB = "tests/js/*.test.js"

#: Long enough for a cold start on a loaded machine, short enough that a hang is
#: a failed test rather than a stuck run.
TIMEOUT_SECONDS = 120

#: The machine's own zone, plus one either side of Greenwich. The two named ones
#: are what give the local-calendar assertions something to distinguish.
TIMEZONES = [None, "America/Los_Angeles", "Asia/Tokyo"]


def _node() -> str | None:
    """The interpreter to run, or ``None`` if this machine has none.

    ``LEDGERBOX_NODE`` first, for the same reason ``LEDGERBOX_BEAN_CHECK``
    exists: an oracle that is installed somewhere unusual should be usable
    without changing ``PATH`` for the whole session.
    """
    override = os.environ.get("LEDGERBOX_NODE")
    if override:
        return override if Path(override).exists() else None
    return shutil.which("node")


@pytest.mark.parametrize("timezone", TIMEZONES, ids=lambda tz: tz or "machine-local")
def test_the_web_modules_pass_their_own_suite(timezone: str | None) -> None:
    """``node --test tests/js`` exits zero, in three timezones."""
    node = _node()
    if node is None:
        pytest.skip(
            "no node interpreter found: set LEDGERBOX_NODE or put node on PATH. "
            "The frontend's behavioural tests did not run."
        )
    if not JS_TESTS.is_dir():  # pragma: no cover - the directory is tracked
        pytest.fail(f"{JS_TESTS} is missing; the JavaScript suite cannot have run.")

    env = dict(os.environ)
    if timezone is None:
        env.pop("TZ", None)
    else:
        env["TZ"] = timezone

    result = subprocess.run(  # noqa: S603 - the executable is ours or the operator's
        [node, "--test", "--test-reporter=tap", JS_TEST_GLOB],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    report = _tap_counts(result.stdout)

    if result.returncode != 0:
        # The runner's own report, verbatim. A summary invented here would be a
        # second description of a failure that already describes itself.
        pytest.fail(
            f"node --test exited {result.returncode} with TZ={timezone or '(machine)'}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )

    # **A zero exit code is not evidence that anything ran.** `node --test` over
    # a pattern that matches nothing exits 0, so a renamed directory or a
    # tightened glob would retire this whole suite silently and leave a green
    # line where the coverage used to be. That is the failure mode the
    # `beancount` CI job was rewritten to close by parsing its own junit XML
    # instead of trusting the exit code (`docs/STATUS.md` §5.31), and it is the
    # same one here. So the count is read out of the report and asserted.
    assert report.get("pass", 0) > 0, (
        f"node exited 0 but reported no passing tests with TZ={timezone or '(machine)'}; "
        f"{JS_TEST_GLOB} probably matched nothing.\n{result.stdout}"
    )
    assert report.get("fail", 0) == 0, f"node exited 0 with failures reported\n{result.stdout}"


def _tap_counts(stdout: str) -> dict[str, int]:
    """The ``# pass`` / ``# fail`` / ``# tests`` tail of node's TAP output.

    TAP is asked for explicitly rather than taken as the default, because the
    default depends on whether stdout is a terminal and this has to parse the
    same shape whether a person or CI is watching.
    """
    counts: dict[str, int] = {}
    for line in stdout.splitlines():
        parts = line.strip().split()
        if len(parts) == 3 and parts[0] == "#" and parts[2].isdigit():
            counts[parts[1]] = int(parts[2])
    return counts


def test_the_javascript_suite_is_not_empty() -> None:
    """The files exist, independently of whether the runner found them.

    Paired with the count assertion above: this one fails if the suite is
    deleted, that one fails if it is deleted *or* if node stops matching it.
    Two ways to notice, because the thing being guarded against is a green line
    standing in for coverage that is no longer there.
    """
    assert JS_TESTS.is_dir(), f"{JS_TESTS} is missing"
    files = sorted(path.name for path in JS_TESTS.glob("*.test.js"))
    assert files, f"no *.test.js under {JS_TESTS}"
