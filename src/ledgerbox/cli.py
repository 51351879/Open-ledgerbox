# SPDX-License-Identifier: AGPL-3.0-or-later
"""Command line: local ledger operations and the Agent-neutral JSON adapter.

Exit codes are part of the interface — this thing will end up in a cron job:

===  =========================================================
  0  everything imported (or was already imported) and verified
  1  at least one statement needs review; nothing was booked for it
  2  processing or Agent input error
  3  Agent candidates refused because ledger verification did not pass
  4  proposal conflicts with the current ledger state
===  =========================================================

``forget`` is the one command that reads that table backwards, and
:func:`cmd_forget` explains why: a plan is not a deletion, and a command named
``forget`` that deleted nothing must not report success.

Running with no subcommand starts the local server, because that is what
``uvx ledgerbox`` has to do for someone who has never read this help text. Every
other command still works exactly as before, and ``ledgerbox ingest`` is
unaffected by whether the optional web dependencies are installed — see
:func:`cmd_serve`, which is the only place in this module that imports them.

The first thing ``main`` does is force UTF-8 on stdout and stderr. The
predecessor died with ``UnicodeEncodeError`` on a cp1252 console before it had
read a single PDF, because one directory in the path was Chinese.
"""

from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .agent import (
    MAX_PROPOSAL_JSON_CHARS,
    AgentInputError,
    AgentLedgerNotReady,
    agent_candidates_to_wire,
    agent_categories_to_wire,
    agent_error_to_wire,
    agent_status_to_wire,
    parse_proposal_draft_json,
    parse_proposal_json,
    parse_triage_draft_json,
    parse_triage_json,
    proposal_submission_to_wire,
    proposal_validation_to_wire,
    read_agent_candidates,
    read_agent_categories,
    read_agent_status,
    triage_submission_to_wire,
    triage_validation_to_wire,
)
from .agent_jobs import enqueue_manual_job, read_job_log
from .agent_runner import drain_jobs
from .agent_skill_install import (
    OFFICIAL_SKILL_VERSION,
    SkillBundleInvalid,
    SkillInstallConflict,
    inspect_user_skill,
    install_user_skill,
)
from .config import DEFAULT_HOST, DEFAULT_PORT, DataPaths, configure_stdio
from .db.connection import integrity_check, read_transaction, transaction
from .db.migrate import discover, open_ledger, schema_version
from .db.repo import MAX_PAGE_SIZE, STATEMENT_ID_MIN_PREFIX, LedgerTotals
from .ingest.forget import ForgetPlan, ForgetRefused, ForgetResult, forget_statement, plan_forget
from .ingest.pipeline import (
    FAILED,
    NEEDS_REVIEW,
    ingest_paths,
    stranded_extractions,
    verify_ledger,
)
from .learning import add_prefix_rule, apply_learned_rules, list_prefix_rules, remove_prefix_rule
from .ledger.beancount_export import EXPORT_FILENAME, export_beancount
from .money import format_minor
from .proposals import (
    ProposalConflict,
    ProposalSubmission,
    submit_proposal,
    validate_proposal,
)
from .reconcile.checks import PASS, CheckResult
from .reconcile.report import render_report
from .triage import (
    TriageConflict,
    TriageDraft,
    TriageLedgerNotReady,
    TriageScopeIncomplete,
    TriageSubmission,
    submit_triage,
    validate_triage,
)

EXIT_OK = 0
EXIT_REVIEW = 1
EXIT_FAILED = 2
EXIT_AGENT_LEDGER_NOT_READY = 3
EXIT_AGENT_CONFLICT = 4


def _open(args: argparse.Namespace) -> tuple[DataPaths, sqlite3.Connection]:
    paths = DataPaths.resolve(args.data_dir)
    return paths, open_ledger(paths.db)


WEB_EXTRA_HINT = (
    "the local server needs fastapi, uvicorn and python-multipart, which are\n"
    "normal dependencies of ledgerbox — this install is missing them:\n"
    "    pip install --upgrade ledgerbox\n"
    "Everything else — ingest, verify, export, doctor — works without them."
)


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the local server and, unless told not to, open a browser at it.

    Imports of :mod:`ledgerbox.api` happen *inside* this function on purpose.
    The web dependencies are optional, and a module-level import would make
    ``ledgerbox ingest`` fail to start on an install that never asked for a web
    server — turning an optional extra into a required one by accident. A
    missing dependency has to read as one sentence, not a traceback.
    """
    paths = DataPaths.resolve(args.data_dir)

    try:
        from .api.app import create_app
    except ImportError as exc:
        print(f"{WEB_EXTRA_HINT}\n({exc})", file=sys.stderr)
        return EXIT_FAILED
    import uvicorn

    port = DEFAULT_PORT if args.port is None else int(args.port)
    url = f"http://{DEFAULT_HOST}:{port}/"

    # Claim the port before claiming anything on stdout. uvicorn does not raise
    # when the bind fails: it logs and calls sys.exit(1) from inside its own
    # startup, which would leave this command exiting 1 — the code this CLI
    # documents as "a statement needs review". A cron job reading exit codes
    # would be told the wrong thing about its own ledger because a port was busy.
    #
    # And the banner has to come after this, not before. Printing "listening
    # http://…" and then failing to listen is a small lie of exactly the kind
    # this project is built to not tell.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((DEFAULT_HOST, port))
    except OSError as exc:
        print(f"cannot listen on {DEFAULT_HOST}:{port}: {exc}", file=sys.stderr)
        print("another ledgerbox may already be serving this ledger.", file=sys.stderr)
        return EXIT_FAILED
    finally:
        # Released immediately: this proves the port is free, it does not hold
        # it. The race with another process taking it in the microseconds after
        # is real and is not worth a lock — uvicorn still reports that one, and
        # the cost is a wrong exit code in a case nobody has hit.
        probe.close()

    app = create_app(paths)

    print(f"ledgerbox {__version__}")
    print(f"data dir  {paths.root}")
    print(f"listening {url}")
    print("loopback only, no authentication — do not expose this beyond this machine.")
    print("Ctrl-C to stop.")

    if not args.no_browser:
        _open_browser_shortly(url)

    uvicorn.run(app, host=DEFAULT_HOST, port=port, log_level="warning")
    return EXIT_OK


def _open_browser_shortly(url: str, *, delay: float = 0.8) -> None:
    """Open *url* just after the server has had time to bind.

    A timer rather than a startup hook because :func:`uvicorn.run` blocks this
    thread until shutdown, so there is nowhere else to put it. The cost of the
    guess being wrong is a browser tab that needs one refresh; a daemon thread
    means it can never keep the process alive if the server dies first.
    """
    import threading
    import webbrowser

    timer = threading.Timer(delay, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()


def cmd_ingest(args: argparse.Namespace) -> int:
    paths, conn = _open(args)
    try:
        outcomes = ingest_paths(conn, paths, args.paths)
    finally:
        conn.close()

    if not outcomes:
        print("no PDFs found in the given paths")
        return EXIT_OK

    for outcome in outcomes:
        print(outcome.summary_line())
        if outcome.report is not None and (outcome.report.failures or outcome.report.unverified):
            print(render_report(outcome.report))

    needs_review = [o for o in outcomes if o.status == NEEDS_REVIEW]
    failed = [o for o in outcomes if o.status == FAILED]
    booked = sum(o.counts.txns for o in outcomes if o.counts is not None)

    print()
    print(
        f"{len(outcomes)} file(s): {len(outcomes) - len(needs_review) - len(failed)} ok, "
        f"{len(needs_review)} need review, {len(failed)} failed; {booked} transaction(s) booked"
    )
    if any(outcome.agent_job_queued for outcome in outcomes):
        print("running enabled local Agent classification...")
        for job in drain_jobs(paths):
            if job.state == "failed":
                print(
                    "Agent classification failed; "
                    f"{job.omitted_count or 0} transaction(s) still need classification "
                    f"({job.error_code})."
                )
            else:
                print(
                    "Agent classification: "
                    f"{job.candidate_count} candidate(s), {job.submitted_count} submitted, "
                    f"{job.applied_count} applied, {job.omitted_count} need classification."
                )
    if failed:
        return EXIT_FAILED
    return EXIT_REVIEW if needs_review else EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    paths, conn = _open(args)
    try:
        results = verify_ledger(conn, paths)
        totals = _totals(conn)
    finally:
        conn.close()

    for result in results:
        mark = "ok  " if result.status == PASS else result.status.upper()
        print(f"{mark} [{result.severity:5}] {result.check_id}: {result.message}")
        if result.status != PASS:
            # "5 printed balances disagree" is a fact, not something anyone can
            # act on. The specifics are already computed; printing the summary
            # and throwing them away is how a report becomes decoration.
            for line in _detail_lines(result.detail):
                print(f"       {line}")

    print()
    print(
        f"ledger: {totals['txn_count']} transaction(s), "
        f"in {format_minor(totals['inflow_minor'])}, out {format_minor(totals['outflow_minor'])}, "
        f"net {format_minor(totals['net_minor'])}"
    )
    print(f"balance: {_format_balance(totals['balance_minor'])}")

    # Only when something was excluded, and with the amounts rather than only
    # the count. Marking a transfer takes money out of the two figures above;
    # a wrong flag therefore shrinks reported spending silently, and a count on
    # its own gives a reader nothing to compare against. Not a verdict either
    # way -- the exclusion may be entirely right. These are the numbers that
    # make it possible to notice when it is not.
    transfers = totals.get("transfer_count", 0)
    if transfers:
        print(
            f"excluded as transfers: {transfers} transaction(s), "
            f"{format_minor(totals.get('transfer_excluded_in_minor', 0))} from in, "
            f"{format_minor(totals.get('transfer_excluded_out_minor', 0))} from out"
        )
    return EXIT_OK if all(r.status == PASS for r in results) else EXIT_FAILED


def _render_entry(entry: Any) -> str:
    """One row of a check's detail, with money keys rendered as money.

    An operator reading ``diff_minor=-4321`` has to do arithmetic before they
    can act, which is exactly the friction that makes people stop reading
    reports.
    """
    if not isinstance(entry, dict):
        return str(entry)
    return ", ".join(
        f"{name}={format_minor(item) if name.endswith('_minor') else item}"
        for name, item in sorted(entry.items())
        if not isinstance(item, list)
    )


def _format_balance(minor: int | None) -> str:
    """A balance, or the fact that this ledger has none to report.

    ``None`` means no posting of an account the operator owns fell in the
    window — before the first statement, or after the last one has been
    forgotten. ``format_minor`` would render that as ``$0.00``, which asserts
    that an account held nothing on a day nothing was recorded about. The exit
    code is what a cron job reads here, but a person reads this line, and rule
    11 binds it the same way.
    """
    return "not known" if minor is None else format_minor(minor)


def _detail_lines(detail: dict[str, Any], *, limit: int = 10) -> list[str]:
    """Flatten a check's structured detail into something readable.

    Handles both shapes a check produces: a **list** of rows (which most of
    them emit) and a **mapping** of named rows, which ``cashflow_agreement``
    emits because its rows are named by the field that disagreed.

    The mapping arm was missing at first, so the one check whose whole reason
    for existing is to surface a number printed no number at all: the FAIL line
    named the fields and stopped, while ``doctor`` told the operator to "run
    `verify` for the numbers" that `verify` was silently dropping. The amounts
    were sitting in ``result.detail`` the whole time, reachable only by writing
    Python. ``cmd_verify``'s own comment calls that shape "a report becoming
    decoration".
    """
    lines: list[str] = []
    for key, value in sorted(detail.items()):
        if isinstance(value, dict):
            for name, entry in sorted(value.items())[:limit]:
                lines.append(f"{key}: {name}: {_render_entry(entry)}")
            if len(value) > limit:
                lines.append(f"{key}: ... and {len(value) - limit} more")
            continue
        if not isinstance(value, list) or not value:
            continue
        for entry in value[:limit]:
            lines.append(f"{key}: {_render_entry(entry)}")
        if len(value) > limit:
            lines.append(f"{key}: ... and {len(value) - limit} more")
    return lines


def _totals(conn: sqlite3.Connection) -> LedgerTotals:
    from .db import repo

    return repo.ledger_totals(conn)


def _print_checks(results: Sequence[CheckResult], *, indent: str = "  ") -> None:
    """The checks, failures first, in the same shape ``verify`` prints them.

    Not passing comes before passing, and everything that is not a pass gets its
    ``detail`` rendered through :func:`_detail_lines`. §5.45 is a whole section
    about the one check whose entire purpose was to put a number in front of a
    person and which printed no number at all, because the renderer walked the
    wrong shape; the amounts were in ``result.detail`` the whole time.
    """
    unhappy = [result for result in results if result.status != PASS]
    ordered = unhappy + [result for result in results if result.status == PASS]
    for result in ordered:
        mark = "ok  " if result.status == PASS else result.status.upper()
        print(f"{indent}{mark} [{result.severity:5}] {result.check_id}: {result.message}")
        if result.status != PASS:
            for line in _detail_lines(result.detail):
                print(f"{indent}       {line}")


def _print_plan_facts(plan: ForgetPlan) -> None:
    """Which statement, and what leaves with it. Printed on both paths.

    Counted from this database rather than estimated, and printed before the
    deletion on the run that performs one, so that the line above the act and
    the line below it are about the same rows.
    """
    facts = plan.facts
    period = (
        f"{facts.period_start} to {facts.period_end}"
        if facts.period_start and facts.period_end
        else "no period was ever read from it"
    )
    print(
        f"statement {plan.source_file_id[:12]}… — "
        f"{facts.statement_month or 'month unknown'} ({period})"
    )

    print()
    # "would", on every path. This is printed before a refusal as well as
    # before a deletion, and on the run that deletes it is still a forecast --
    # what actually went is reported afterwards, from counted rows.
    print("would leave the ledger:")
    print(f"  {facts.txns} transaction(s) with {facts.postings} posting(s)")
    print(
        f"  {facts.identities} identity row(s), {facts.raw_records} raw record(s), "
        f"{facts.review_items} review item(s)"
    )
    # Decisions get their own lines, because every other row above is a pure
    # function of the archived PDF and comes back if the file is ingested again.
    # These do not: archive/ holds documents, not what a person decided about
    # them (docs/STATUS.md §5.49, §5.65).
    decisions = []
    if facts.category_overrides:
        decisions.append(
            f"  {facts.category_overrides} category override(s) — a category somebody set by hand"
        )
    if facts.review_items_decided:
        decisions.append(
            f"  {facts.review_items_decided} review item(s) somebody had already resolved "
            f"or dismissed"
        )
    if facts.agent_proposals:
        decisions.append(
            f"  {facts.agent_proposals} Agent proposal outcome(s); "
            f"{facts.agent_proposal_runs} proposal run(s) become empty"
        )
    if facts.agent_triage_items:
        decisions.append(
            f"  {facts.agent_triage_items} Agent triage outcome(s); "
            f"{facts.agent_triage_runs} triage run(s) become empty"
        )
    if decisions:
        print("and destroys, with nothing that brings them back:")
        for line in decisions:
            print(line)
        print("    re-ingesting the archived PDF restores the transactions, not these.")
    else:
        # Said out loud rather than left as an absent line. The person reading
        # this is deciding whether to type --yes, which is the same moment the
        # 409 addresses, and it says so in both directions for the same reason:
        # somebody being asked to accept an irreversible loss is entitled to be
        # told there is not one. An absent line answers that only for a reader
        # who already knows the line exists.
        print(
            "destroys nothing irreversible: no hand-set category, Agent proposal, "
            "or decided review item."
        )

    kept = facts.balance_assertions_shared
    print(
        f"  {facts.balance_assertions} balance assertion(s): "
        f"{facts.balance_assertions - kept} removed, "
        f"{kept} kept with provenance moved to a statement that still prints that balance"
    )

    for label, path in (
        ("archived original", plan.archive_path),
        # Named even though it is rebuildable: it is the whole text layer of the
        # statement, which makes it the most disclosing file in the data
        # directory (docs/STATUS.md §5.31).
        ("extraction cache", plan.extracted_path),
    ):
        if path is not None:
            print(f"  {label}: {path}")


def _print_plan_forecast(plan: ForgetPlan) -> None:
    """The checks as they would be afterwards, and what was not simulated.

    Only printed on the run that deletes nothing. On the run that deletes, the
    real nine follow a second later, and printing a forecast immediately above
    the measurement of the same thing is noise in the place a person is reading
    hardest.
    """
    print()
    unhappy = [result for result in plan.checks_after if result.status != PASS]
    if unhappy:
        print(
            f"{len(unhappy)} of {len(plan.checks_after)} measured check(s) would "
            f"not pass afterwards:"
        )
    else:
        # "measured" is doing the work, and the note below says which three were
        # not. Rule 11: the line a person reads must not be stronger than what
        # was actually run.
        print(f"all {len(plan.checks_after)} measured check(s) would still pass:")
    _print_checks(plan.checks_after)

    # Verbatim, never paraphrased into something stronger: it names the three
    # checks this forecast did not simulate and why it could not.
    print(f"  ({plan.checks_note})")

    before, after = plan.totals_before, plan.totals_after
    if before and after:
        print()
        print(
            f"totals: in {format_minor(before['inflow_minor'])} → "
            f"{format_minor(after['inflow_minor'])}, "
            f"out {format_minor(before['outflow_minor'])} → "
            f"{format_minor(after['outflow_minor'])}, "
            f"balance {_format_balance(before['balance_minor'])} → "
            f"{_format_balance(after['balance_minor'])}"
        )


def _print_result(result: ForgetResult) -> None:
    counts = result.counts
    print(f"forgot {result.source_file_id[:12]}… ({result.statement_month or 'month unknown'})")
    print(
        f"  removed {counts.txns} transaction(s), {counts.postings} posting(s), "
        f"{counts.identities} identity row(s), {counts.raw_records} raw record(s), "
        f"{counts.review_items} review item(s)"
    )
    if counts.category_overrides:
        print(
            f"  destroyed {counts.category_overrides} category override(s) — "
            f"the hand-made decisions; nothing brings them back"
        )
    if counts.agent_proposals:
        print(
            f"  destroyed {counts.agent_proposals} Agent proposal outcome(s) and "
            f"{counts.agent_proposal_runs} run(s) that became empty; nothing rebuilds them"
        )
    if counts.agent_triage_items:
        print(
            f"  destroyed {counts.agent_triage_items} Agent triage outcome(s) and "
            f"{counts.agent_triage_runs} run(s) that became empty; nothing rebuilds them"
        )
    print(
        f"  balance assertions: {counts.balance_assertions_removed} removed, "
        f"{counts.balance_assertions_reassigned} kept with provenance moved"
    )
    if not counts.opening_txn_ids:
        print("  no opening entry left: no surviving statement asserts a balance")

    for path in result.removed_files:
        print(f"  deleted {path}")
    for path, problem in result.unremoved_files:
        # The ledger rows are already gone, so this is not cosmetic: these are
        # bytes on disk that nothing in the ledger accounts for any more.
        print(f"  COULD NOT DELETE {path}: {problem}", file=sys.stderr)
    if result.unremoved_files:
        # `doctor`, not `verify`. This sentence used to name
        # `archived_not_recorded`, which walks archive/ and nothing else — so it
        # was true of a leftover statement and **false** of a leftover extraction
        # cache, and the cache is the file that holds the whole text layer. A
        # person told to run `verify` saw nine green checks over a file still
        # sitting there. `doctor` reports both, and exits non-zero for both.
        print(
            f"  {len(result.unremoved_files)} file(s) are still on disk with no row behind "
            f"them. `ledgerbox doctor` reports them and exits non-zero until they are "
            f"deleted by hand.",
            file=sys.stderr,
        )

    print()
    unhappy = [check for check in result.checks_after if check.status != PASS]
    if unhappy:
        print(f"{len(unhappy)} of {len(result.checks_after)} check(s) do not pass:")
    else:
        print(f"all {len(result.checks_after)} check(s) pass.")
    _print_checks(result.checks_after)


def cmd_forget(args: argparse.Namespace) -> int:
    """Remove one statement — its rows, its archived PDF, its extraction cache.

    **Two commands, not a prompt.** Without ``--yes`` this measures and prints
    and touches nothing; with it, it deletes. There is no interactive question,
    because the answer to one is not evidence of anything a week later, and
    because a browser is one of the callers.

    **The plan-only run exits 2, deliberately.** This CLI documents its exit
    codes as an interface for cron, and 0 there would mean "everything imported
    and verified" — a command called ``forget`` that deleted nothing reporting
    success. So the run that changed nothing exits with the code that means "the
    thing you asked for did not happen", and the last line says what to add to
    make it happen. The same code covers a refusal and an unknown id, which are
    the other two ways this command can end without a deletion.

    A deletion that *did* happen exits 0 when every one of the nine checks
    passes afterwards **and** every file it meant to remove is gone; 2
    otherwise. The check half is the same rule :func:`cmd_verify` uses, so the
    two cannot disagree about one ledger. The file half is separate because the
    nine checks do not cover ``extracted/`` on purpose, and without it a
    deletion that left the entire text layer on disk would exit 0 with the
    warning printed underneath.
    """
    from .db import repo

    paths, conn = _open(args)
    try:
        try:
            statement = repo.find_statement(conn, args.statement)
        except (repo.StatementNotFound, repo.AmbiguousStatement) as exc:
            # One sentence, no traceback. `AmbiguousStatement` formats its own
            # candidates; both say what to do next rather than only what is wrong.
            print(str(exc), file=sys.stderr)
            return EXIT_FAILED

        source_file_id = str(statement["source_file_id"])
        plan = plan_forget(conn, paths, source_file_id)
        _print_plan_facts(plan)

        if not plan.allowed:
            print()
            print("this deletion is refused:")
            for reason in plan.refusals:
                print(f"  - {reason}")
            return EXIT_FAILED

        if not args.yes:
            _print_plan_forecast(plan)
            print()
            print("nothing has been deleted. To do it, add --yes:")
            # Quoted when it needs to be: a suggested command that does not work
            # when pasted is the same shape of unhelpfulness as a refusal that
            # leaves the next step to be invented.
            root = str(paths.root)
            shown = f'"{root}"' if " " in root else root
            print(f"    ledgerbox --data-dir {shown} forget {args.statement} --yes")
            return EXIT_FAILED

        try:
            result = forget_statement(conn, paths, source_file_id)
        except ForgetRefused as exc:
            # Re-checked between the plan and the act on purpose: the plan is a
            # measurement of a moment, and this command is not the only caller.
            print(str(exc), file=sys.stderr)
            return EXIT_FAILED

        print()
        _print_result(result)
        # A file left on disk is a failure of this command, whatever the nine
        # checks say. They deliberately do not look in `extracted/` (see
        # `pipeline.stranded_extractions`), so a stranded extraction cache -- the
        # whole text layer -- would otherwise leave `forget` exiting 0 with the
        # warning printed underneath it. This file's own comment on `doctor` says
        # a line printed under a zero exit code is a line nobody reads; that
        # applies here first.
        if result.unremoved_files:
            return EXIT_FAILED
        return EXIT_OK if all(c.status == PASS for c in result.checks_after) else EXIT_FAILED
    finally:
        conn.close()


def cmd_export(args: argparse.Namespace) -> int:
    paths, conn = _open(args)
    try:
        target = Path(args.output) if args.output else paths.export / EXPORT_FILENAME
        written = export_beancount(conn, target)
    finally:
        conn.close()

    print(f"wrote {written}")
    print(
        "plain text, and the point of it: if this software is ever wrong, this file and "
        "archive/ are what survive it. `bean-check` will read it without ledgerbox."
    )
    return EXIT_OK


def cmd_reapply_rules(args: argparse.Namespace) -> int:
    """Re-apply the rules file to every booked line: categories and transfers.

    Needed because both run once, at ingest, and are then stored. Editing a
    rule has to have a way of reaching the rows that were booked before the
    edit, and that way should be one command that says how many rows it moved —
    not a page that silently recomputes on every load, which is what the
    predecessor did 234 times per render while offering no way to correct a
    single transaction.

    It was called ``recategorize`` while it only wrote ``posting.category_id``.
    Transfer flags move money out of the headline totals, so a command that
    writes them under a name that says "categorise" would be telling the
    operator less than it does — the same objection this project raises to any
    other line that reads weaker than what it covers.

    Two things it does **not** touch. It never books, unbooks or re-reads a
    PDF. And it never writes ``category_override``: that table holds what a
    *person* decided, ``v_txn_transfer`` folds it over whatever the rules say,
    and re-running the rules must not be able to lose it.
    """
    from .analytics.categorize import RulesError, classify, default_rules, matches_transfer
    from .db import repo
    from .db.connection import transaction

    # Before the ledger is opened: a broken rules file is a broken install, not
    # a broken ledger, and the two deserve different messages. Verification
    # measured the previous behaviour and it was a 29-line traceback.
    try:
        rules = default_rules()
    except RulesError as error:
        print(f"the category rules are unusable: {error}", file=sys.stderr)
        return EXIT_FAILED

    _, conn = _open(args)
    try:
        rows = repo.categorized_rows(conn)
        if not rows:
            print("nothing is booked yet, so there is nothing to categorise")
            return EXIT_OK

        assignments: dict[str, str | None] = {
            str(row["posting_id"]): classify(
                str(row["raw_descriptor"]), int(row["amount_minor"]), rules=rules
            )
            for row in rows
        }
        flags: dict[str, bool] = {
            str(row["txn_id"]): matches_transfer(str(row["raw_descriptor"]), rules=rules)
            is not None
            for row in rows
        }
        # Both counts compare against the *rules'* own previous answer, never
        # the effective one. `rule_category_id` and `rule_is_transfer` are the
        # two raw columns `repo.categorized_rows` reaches past `v_txn_category`
        # and `v_txn_transfer` for, and this is the reason: an override is a
        # person disagreeing with a rule, so counting one as a row the rules
        # want to move makes the forecast below a forecast of something else.
        #
        # Exactly what goes wrong, because an earlier version of this comment
        # claimed more: nothing writes a person's decision into the rules'
        # columns. `assignments` and `flags` are pure functions of the
        # descriptor and read no stored value, so the rows written are identical
        # either way. What breaks is that `--dry-run` promises "N would change"
        # and the run that follows changes a different number — on exactly the
        # ledgers where somebody has corrected something.
        pending = sum(
            1 for row in rows if row["rule_category_id"] != assignments[str(row["posting_id"])]
        )
        pending_flags = sum(
            1 for row in rows if bool(row["rule_is_transfer"]) != flags[str(row["txn_id"])]
        )

        if args.dry_run:
            print(
                f"rules v{rules.version}: {pending} of {len(rows)} posting(s) and "
                f"{pending_flags} transfer flag(s) would change (nothing altered)"
            )
        else:
            with transaction(conn):
                repo.ensure_categories(conn, rows=list(rules.rows()))
                changed = repo.set_posting_categories(conn, assignments=assignments)
                changed_flags = repo.set_transfer_flags(conn, assignments=flags)
            print(
                f"rules v{rules.version}: {changed} of {len(rows)} posting(s) and "
                f"{changed_flags} transfer flag(s) changed"
            )

        tally: dict[str, int] = {}
        for value in assignments.values():
            name = value if value is not None else "(uncategorized)"
            tally[name] = tally.get(name, 0) + 1
        for name, count in sorted(tally.items(), key=lambda item: (-item[1], item[0])):
            print(f"  {count:>5}  {name}")

        # Said plainly because the number above looks like a score. It is not
        # one: no reconciliation check consults a category, and a line nothing
        # claimed is reported as unclaimed rather than swept into a bucket.
        print(
            "categories are a heuristic and gate nothing; unmatched lines stay uncategorised "
            "rather than falling into a catch-all"
        )
        return EXIT_OK
    finally:
        conn.close()


def cmd_doctor(args: argparse.Namespace) -> int:
    paths = DataPaths.resolve(args.data_dir)
    print(f"ledgerbox {__version__}")
    print(f"python   {sys.version.split()[0]}")
    print(f"sqlite   {sqlite3.sqlite_version}")
    print(f"data dir {paths.root}")
    print(f"database {paths.db} ({'present' if paths.db.exists() else 'not created yet'})")
    print(f"archive  {paths.archive}")

    if not paths.db.exists():
        print("\nnothing ingested yet")
        return EXIT_OK

    conn = open_ledger(paths.db)
    try:
        print(f"schema   version {schema_version(conn)} of {len(discover())}")
        problems = integrity_check(conn)
        print(f"integrity {problems if problems else 'ok'}")

        counts = {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in ("source_file", "txn", "posting", "account", "balance_assertion")
        }
        print("rows     " + ", ".join(f"{name}={value}" for name, value in counts.items()))

        pending = conn.execute(
            "SELECT severity, COUNT(*) FROM review_item WHERE status='open' GROUP BY severity"
        ).fetchall()
        if pending:
            print("review   " + ", ".join(f"{row[0]}={row[1]}" for row in pending))
        else:
            print("review   queue empty")

        months = conn.execute("SELECT COUNT(DISTINCT statement_month) FROM v_statement").fetchone()[
            0
        ]
        print(f"months   {months} distinct statement month(s)")

        # The nine measured ledger checks have one implementation.  `doctor`
        # used to re-ask a subset with private queries; three verifier failures
        # (`double_entry`, `provenance`, `balance_assertions`) therefore left a
        # green doctor exit.  Reusing the CheckResult objects closes the whole
        # class of drift and gives this summary the same check ids as `verify`.
        checks = verify_ledger(conn, paths)
        unhappy = [result for result in checks if result.status != PASS]
        if unhappy:
            print(f"checks   {len(unhappy)} of {len(checks)} measured check(s) do not pass")
            _print_checks(unhappy, indent="         ")
        else:
            print(f"checks   all {len(checks)} measured check(s) pass")

        # Archive temp debris is detail on the archive-integrity result rather
        # than a tenth check: it is reported but does not fail verification.
        archive_integrity = next(
            result for result in checks if result.check_id == "archive_integrity"
        )
        stale_temp = archive_integrity.detail.get("stale_temp", [])
        if stale_temp:
            print(
                f"debris   {len(stale_temp)} interrupted archive write(s) (removed after an hour)"
            )

        # The one data-directory condition deliberately outside `verify`.
        # `archive/` is rebuild input and belongs to the nine checks;
        # `extracted/` is disposable cache, but a stranded copy still contains
        # the whole text layer and doctor must keep making it a non-zero result.
        stranded = stranded_extractions(conn, paths)
        if stranded:
            print(
                f"stranded {len(stranded)} extraction cache(s) in {paths.extracted} "
                f"with no source_file row; nothing sweeps this directory, delete them"
            )

        # Not an error — an upload interrupted a second ago is legitimately
        # here. But nothing else in this tool ever mentions the directory, and
        # an abandoned spool is an unmanaged copy of a statement.
        spooled = [p for p in paths.incoming.glob("*") if p.is_file()]
        if spooled:
            print(f"incoming {len(spooled)} file(s) in {paths.incoming} (removed after an hour)")

        if problems:
            return EXIT_FAILED
        # A failure in any measured check except the review queue is a failed
        # ledger. This includes SKIP: "I could not check" is not a pass.
        non_review_failures = [result for result in unhappy if result.check_id != "review_queue"]
        if non_review_failures or stranded:
            return EXIT_FAILED

        # An open blocking review item keeps its established exit code 1: the
        # statement needs a decision, rather than a processing failure.  The
        # verifier result above is still the authority for whether that check
        # passed; this query only preserves the CLI's review/failure distinction.
        blocking = sum(row[1] for row in pending if row[0] == "block")
        return EXIT_REVIEW if blocking else EXIT_OK
    finally:
        conn.close()


def _emit_agent_json(payload: dict[str, Any], *, stream: Any = None) -> None:
    """Write one compact JSON document and no presentation text around it."""
    destination = sys.stdout if stream is None else stream
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=destination,
    )


def _emit_agent_error(
    code: str,
    message: str,
    *,
    failed_checks: Sequence[str] = (),
) -> None:
    _emit_agent_json(
        agent_error_to_wire(code, message, failed_checks=tuple(failed_checks)),
        stream=sys.stderr,
    )


def cmd_agent_status(args: argparse.Namespace) -> int:
    """Report proposal readiness using the actual nine ledger checks."""
    paths, conn = _open(args)
    try:
        with read_transaction(conn):
            status = read_agent_status(conn, paths)
    finally:
        conn.close()

    _emit_agent_json(agent_status_to_wire(status))
    # A status command successfully answered even when the answer is "not
    # ready".  Candidate generation is the operation that refuses with exit 3.
    return EXIT_OK


def cmd_agent_categories(args: argparse.Namespace) -> int:
    """Return the current stored taxonomy as versioned JSON."""
    _, conn = _open(args)
    try:
        with read_transaction(conn):
            catalog = read_agent_categories(conn)
    finally:
        conn.close()

    _emit_agent_json(agent_categories_to_wire(catalog))
    return EXIT_OK


def cmd_agent_candidates(args: argparse.Namespace) -> int:
    """Return only verified, unanswered rows and their minimum necessary facts."""
    paths, conn = _open(args)
    try:
        try:
            with read_transaction(conn):
                batch = read_agent_candidates(
                    conn,
                    paths,
                    since=args.since,
                    until=args.until,
                    limit=args.limit,
                )
        except AgentLedgerNotReady as error:
            _emit_agent_error(
                "ledger_not_ready",
                str(error),
                failed_checks=error.failed_checks,
            )
            return EXIT_AGENT_LEDGER_NOT_READY
        except ValueError as error:
            _emit_agent_error("invalid_request", str(error))
            return EXIT_FAILED
    finally:
        conn.close()

    _emit_agent_json(agent_candidates_to_wire(batch))
    return EXIT_OK


def _proposal_from_stdin(*, draft: bool = False) -> ProposalSubmission:
    text = sys.stdin.read(MAX_PROPOSAL_JSON_CHARS + 1)
    return parse_proposal_draft_json(text) if draft else parse_proposal_json(text)


def cmd_agent_validate_proposal(args: argparse.Namespace) -> int:
    """Validate stdin against the shared proposal contract without writing it."""
    try:
        submission = _proposal_from_stdin(draft=True)
    except AgentInputError as error:
        _emit_agent_error("invalid_proposal", str(error))
        return EXIT_FAILED

    _, conn = _open(args)
    try:
        try:
            with read_transaction(conn):
                result = validate_proposal(conn, submission)
        except ProposalConflict as error:
            _emit_agent_error("proposal_conflict", str(error))
            return EXIT_AGENT_CONFLICT
    finally:
        conn.close()

    _emit_agent_json(proposal_validation_to_wire(result, submission))
    return EXIT_OK


def cmd_agent_submit_proposal(args: argparse.Namespace) -> int:
    """Submit one strict versioned proposal to the shared Core state machine."""
    try:
        submission = _proposal_from_stdin()
    except AgentInputError as error:
        _emit_agent_error("invalid_proposal", str(error))
        return EXIT_FAILED

    _, conn = _open(args)
    try:
        try:
            result = submit_proposal(conn, submission)
        except ProposalConflict as error:
            _emit_agent_error("proposal_conflict", str(error))
            return EXIT_AGENT_CONFLICT
    finally:
        conn.close()

    _emit_agent_json(proposal_submission_to_wire(result))
    return EXIT_OK


def _triage_from_stdin(*, draft: bool) -> TriageDraft | TriageSubmission:
    text = sys.stdin.read(MAX_PROPOSAL_JSON_CHARS + 1)
    return parse_triage_draft_json(text) if draft else parse_triage_json(text)


def cmd_agent_validate_triage(args: argparse.Namespace) -> int:
    """Validate one exhaustive triage draft without storing audit rows."""
    try:
        draft = _triage_from_stdin(draft=True)
        assert isinstance(draft, TriageDraft)
    except AgentInputError as error:
        _emit_agent_error("invalid_triage", str(error))
        return EXIT_FAILED

    paths, conn = _open(args)
    try:
        try:
            with read_transaction(conn):
                result = validate_triage(conn, paths, draft)
        except TriageLedgerNotReady as error:
            _emit_agent_error(
                "ledger_not_ready",
                str(error),
                failed_checks=error.failed_checks,
            )
            return EXIT_AGENT_LEDGER_NOT_READY
        except TriageScopeIncomplete as error:
            _emit_agent_error("triage_scope_incomplete", str(error))
            return EXIT_AGENT_CONFLICT
        except TriageConflict as error:
            _emit_agent_error("triage_conflict", str(error))
            return EXIT_AGENT_CONFLICT
    finally:
        conn.close()

    _emit_agent_json(triage_validation_to_wire(result))
    return EXIT_OK


def cmd_agent_submit_triage(args: argparse.Namespace) -> int:
    """Store an exact validated triage as audit data, never as categories."""
    try:
        submission = _triage_from_stdin(draft=False)
        assert isinstance(submission, TriageSubmission)
    except AgentInputError as error:
        _emit_agent_error("invalid_triage", str(error))
        return EXIT_FAILED

    paths, conn = _open(args)
    try:
        try:
            result = submit_triage(conn, paths, submission)
        except TriageLedgerNotReady as error:
            _emit_agent_error(
                "ledger_not_ready",
                str(error),
                failed_checks=error.failed_checks,
            )
            return EXIT_AGENT_LEDGER_NOT_READY
        except TriageScopeIncomplete as error:
            _emit_agent_error("triage_scope_incomplete", str(error))
            return EXIT_AGENT_CONFLICT
        except TriageConflict as error:
            _emit_agent_error("triage_conflict", str(error))
            return EXIT_AGENT_CONFLICT
    finally:
        conn.close()

    _emit_agent_json(triage_submission_to_wire(result))
    return EXIT_OK


def cmd_agent_skill_doctor(args: argparse.Namespace) -> int:
    report = inspect_user_skill(args.client)
    version = report.installed_version or "none"
    print(f"{report.client} Skill: {report.state}")
    print(f"target: {report.target}")
    print(f"installed version: {version}")
    print(f"official version: {report.current_version}")
    if report.changed_files:
        print("changed files:")
        for name in report.changed_files:
            print(f"  {name}")
    return EXIT_OK if report.state == "current" else EXIT_REVIEW


def cmd_setup(args: argparse.Namespace) -> int:
    """Skill install, MCP registration and verification, in order, or stop."""
    import os

    from .first_run import FirstRunError, first_run

    if args.data_dir is None and not os.environ.get("LEDGERBOX_DATA_DIR"):
        # Everything else may fall back to the OS data directory; first-time
        # setup must not, because this choice is where someone's financial
        # records will live and a default chosen silently is not a choice.
        print(
            "setup needs --data-dir (or LEDGERBOX_DATA_DIR): the folder your statements "
            "and ledger should live in, outside any git repository",
            file=sys.stderr,
        )
        return EXIT_FAILED
    try:
        events = first_run(data_dir=args.data_dir, client=args.client)
    except FirstRunError as error:
        print(str(error), file=sys.stderr)
        return EXIT_FAILED
    for event in events:
        print(event)
    return EXIT_OK


def cmd_rules_add_prefix(args: argparse.Namespace) -> int:
    """Decree one standing prefix rule and claim what it answers right now."""
    from .db import repo

    paths = DataPaths.resolve(args.data_dir)
    if not paths.db.exists():
        print("nothing ingested yet", file=sys.stderr)
        return EXIT_REVIEW
    conn = open_ledger(paths.db)
    try:
        with transaction(conn):
            if not repo.category_exists(conn, args.category):
                print(f"no category {args.category!r}", file=sys.stderr)
                return EXIT_REVIEW
            try:
                add_prefix_rule(conn, prefix=args.prefix, category_id=args.category)
            except ValueError as error:
                print(str(error), file=sys.stderr)
                return EXIT_REVIEW
            claimed = apply_learned_rules(conn)
    finally:
        conn.close()
    print(
        f"standing rule saved: descriptors starting {args.prefix.strip()!r} "
        f"are {args.category}; {claimed} line(s) claimed now, and future imports "
        "apply it at booking"
    )
    return EXIT_OK


def cmd_rules_list(args: argparse.Namespace) -> int:
    paths = DataPaths.resolve(args.data_dir)
    if not paths.db.exists():
        print("nothing ingested yet", file=sys.stderr)
        return EXIT_REVIEW
    conn = open_ledger(paths.db)
    try:
        rows = list_prefix_rules(conn)
    finally:
        conn.close()
    if not rows:
        print("no standing prefix rules")
        return EXIT_OK
    for prefix, category_id, derived in rows:
        print(f"{prefix!r} -> {category_id} ({derived} line(s) currently answered by it)")
    return EXIT_OK


def cmd_rules_remove_prefix(args: argparse.Namespace) -> int:
    paths = DataPaths.resolve(args.data_dir)
    if not paths.db.exists():
        print("nothing ingested yet", file=sys.stderr)
        return EXIT_REVIEW
    conn = open_ledger(paths.db)
    try:
        with transaction(conn):
            removed, cleared = remove_prefix_rule(conn, prefix=args.prefix)
    finally:
        conn.close()
    if not removed:
        print("no standing rule with that exact prefix", file=sys.stderr)
        return EXIT_REVIEW
    print(f"standing rule removed; {cleared} derived answer(s) reverted to undecided")
    return EXIT_OK


def cmd_agent_classify_now(args: argparse.Namespace) -> int:
    """Ask the selected local client for another classification round.

    Until this existed the only way to queue a round was importing a statement,
    so transactions the Agent had left alone stayed that way.
    """
    paths = DataPaths.resolve(args.data_dir)
    if not paths.db.exists():
        print("nothing ingested yet", file=sys.stderr)
        return EXIT_REVIEW
    conn = open_ledger(paths.db)
    try:
        queued = enqueue_manual_job(conn)
    finally:
        conn.close()
    if queued is None:
        print(
            "no round was queued: enable a local Agent in Classification settings, "
            "and wait for any run already in flight to finish",
            file=sys.stderr,
        )
        return EXIT_REVIEW
    print(f"queued one {queued.job.client} classification round")
    rounds = drain_jobs(paths)
    if not rounds:
        print("the round is queued but nothing consumed it", file=sys.stderr)
        return EXIT_REVIEW
    for index, job in enumerate(rounds, start=1):
        print(
            f"round {index}: {job.state}, {job.candidate_count} candidates, "
            f"{job.submitted_count} submitted, {job.omitted_count} omitted"
            + (f", {job.error_code}" if job.error_code else "")
        )
    return EXIT_OK if rounds[-1].state != "failed" else EXIT_REVIEW


def cmd_agent_job_log(args: argparse.Namespace) -> int:
    """Print what the local client actually said during a classification run.

    This is the answer to "why did it leave those alone". It quotes the
    operator's own transactions, so it is printed here, to their own terminal,
    and is never part of an API response.
    """
    paths = DataPaths.resolve(args.data_dir)
    if not paths.db.exists():
        print("nothing ingested yet", file=sys.stderr)
        return EXIT_REVIEW
    conn = open_ledger(paths.db)
    try:
        row = conn.execute(
            "SELECT id, client, state, candidate_count, submitted_count, omitted_count, "
            "error_code, client_outcome, client_exit_code, started_at, finished_at "
            "FROM agent_classification_job WHERE finished_at IS NOT NULL "
            "ORDER BY finished_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print("no classification job has finished yet", file=sys.stderr)
            return EXIT_REVIEW
        exit_code = row["client_exit_code"]
        print(f"client:   {row['client']}")
        print(f"state:    {row['state']}")
        print(
            f"counts:   {row['candidate_count']} candidates, "
            f"{row['submitted_count']} submitted, {row['omitted_count']} omitted"
        )
        print(f"outcome:  {row['client_outcome'] or 'unrecorded'}", end="")
        print(f" (exit {exit_code})" if exit_code is not None else "")
        if row["error_code"]:
            print(f"error:    {row['error_code']}")
        print(f"ran:      {row['started_at']} to {row['finished_at']}")
        log = read_job_log(conn, str(row["id"]))
        if log is None:
            print("\nno client output was captured for this run")
            return EXIT_REVIEW
        print("\n--- client output ---")
        print(log, end="" if log.endswith("\n") else "\n")
    finally:
        conn.close()
    return EXIT_OK


def cmd_agent_install_skill(args: argparse.Namespace) -> int:
    def preview(files: tuple[str, ...]) -> None:
        print("custom Skill found; these files would be replaced:")
        for name in files:
            print(f"  {name}")

    def confirm() -> bool:
        if args.yes:
            return True
        print("Type REPLACE to overwrite this custom Skill: ", end="", flush=True)
        try:
            return input().strip() == "REPLACE"
        except EOFError:
            return False

    if args.yes and not args.force:
        print("--yes is only valid together with --force", file=sys.stderr)
        return EXIT_FAILED
    try:
        result = install_user_skill(
            args.client,
            force=args.force,
            preview=preview,
            confirm=confirm,
        )
    except (SkillBundleInvalid, SkillInstallConflict, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_FAILED
    print(f"{result.client} Skill: {result.action}")
    print(f"target: {result.target}")
    print(f"official version: {OFFICIAL_SKILL_VERSION}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledgerbox",
        description="A local-first personal ledger that refuses to give you "
        "numbers it cannot prove.",
    )
    parser.add_argument("--version", action="version", version=f"ledgerbox {__version__}")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="where the ledger lives (default: the OS data directory; "
        "refuses any path inside a git repository)",
    )
    # No subcommand means `serve`. `uvx ledgerbox` is the documented first
    # experience and it has to end with a page in a browser, not a usage error.
    # The defaults below are what `serve`'s own parser would have supplied, so
    # the bare form and the explicit form reach cmd_serve identically.
    parser.set_defaults(func=cmd_serve, port=None, no_browser=False)
    sub = parser.add_subparsers(dest="command", required=False)

    serve = sub.add_parser(
        "serve",
        help=f"start the local server on {DEFAULT_HOST}:{DEFAULT_PORT} (the default command)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"port to listen on (default: {DEFAULT_PORT})",
    )
    serve.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open a browser window",
    )
    # Deliberately no --host. There is no authentication anywhere in this
    # application, so the bind address is the access control; a flag would make
    # exposing a year of transaction history a typo away.
    serve.set_defaults(func=cmd_serve)

    ingest = sub.add_parser("ingest", help="import statement PDFs")
    ingest.add_argument("paths", nargs="+", type=Path, help="PDF files or directories")
    ingest.set_defaults(func=cmd_ingest)

    verify = sub.add_parser("verify", help="re-check the ledger without re-reading PDFs")
    verify.set_defaults(func=cmd_verify)

    forget = sub.add_parser(
        "forget",
        help="remove one statement: its rows, its archived PDF and its extraction cache",
    )
    forget.add_argument(
        "statement",
        help=f"the statement id, or at least its first {STATEMENT_ID_MIN_PREFIX} "
        f"hex characters. An ambiguous prefix is refused, never resolved",
    )
    forget.add_argument(
        "--yes",
        action="store_true",
        # Without it the command measures and prints and deletes nothing, and
        # exits 2 for saying so. See cmd_forget's docstring.
        help="actually delete. Without this the command only measures and reports",
    )
    forget.set_defaults(func=cmd_forget)

    export = sub.add_parser("export", help="write the plain-text escape hatch")
    export_kind = export.add_subparsers(dest="format", required=True)
    beancount = export_kind.add_parser("beancount", help="beancount, readable by bean-check/Fava")
    beancount.add_argument(
        "-o", "--output", type=Path, default=None, help="defaults to <data-dir>/export/"
    )
    beancount.set_defaults(func=cmd_export)

    reapply = sub.add_parser(
        "reapply-rules",
        help="re-apply the rules file (categories and transfer flags) to booked lines",
    )
    reapply.add_argument(
        "--dry-run",
        action="store_true",
        # Deliberately not "writes nothing": every command here opens the
        # ledger, and opening it creates the data directory and applies
        # migrations. The claim this flag can honestly make is narrower.
        help="report what would change without altering a category or a flag",
    )
    reapply.set_defaults(func=cmd_reapply_rules)

    agent = sub.add_parser(
        "agent",
        help="versioned local JSON interface for proposal-only classification",
    )
    agent_kind = agent.add_subparsers(dest="agent_command", required=True)

    agent_status = agent_kind.add_parser(
        "status",
        help="report ledger verification and proposal readiness as JSON",
    )
    agent_status.set_defaults(func=cmd_agent_status)

    agent_categories = agent_kind.add_parser(
        "categories",
        help="list the current stored category taxonomy as JSON",
    )
    agent_categories.set_defaults(func=cmd_agent_categories)

    agent_candidates = agent_kind.add_parser(
        "candidates",
        help="list verified transactions that no rule or person has categorized",
    )
    agent_candidates.add_argument(
        "--since",
        default=None,
        help="inclusive transaction date, YYYY-MM-DD",
    )
    agent_candidates.add_argument(
        "--until",
        default=None,
        help="inclusive transaction date, YYYY-MM-DD",
    )
    agent_candidates.add_argument(
        "--limit",
        type=int,
        default=MAX_PAGE_SIZE,
        help=f"maximum rows to return, 1..{MAX_PAGE_SIZE} (default: {MAX_PAGE_SIZE})",
    )
    agent_candidates.set_defaults(func=cmd_agent_candidates)

    agent_validate = agent_kind.add_parser(
        "validate-proposal",
        help="validate proposal JSON from stdin without storing it",
    )
    agent_validate.set_defaults(func=cmd_agent_validate_proposal)

    agent_submit = agent_kind.add_parser(
        "submit-proposal",
        help="store proposal JSON from stdin for later human review",
    )
    agent_submit.set_defaults(func=cmd_agent_submit_proposal)

    agent_validate_triage = agent_kind.add_parser(
        "validate-triage",
        help="validate an exhaustive remaining-coverage triage draft from stdin",
    )
    agent_validate_triage.set_defaults(func=cmd_agent_validate_triage)

    agent_submit_triage = agent_kind.add_parser(
        "submit-triage",
        help="store an exact validated triage for later human review",
    )
    agent_submit_triage.set_defaults(func=cmd_agent_submit_triage)

    agent_install_skill = agent_kind.add_parser(
        "install-skill",
        help="install or safely upgrade the official user-level classification Skill",
    )
    agent_install_skill.add_argument(
        "--client",
        required=True,
        choices=("codex", "claude", "claude-code"),
    )
    agent_install_skill.add_argument(
        "--force",
        action="store_true",
        help="preview and replace a custom Skill only after confirmation",
    )
    agent_install_skill.add_argument(
        "--yes",
        action="store_true",
        help="confirm the --force replacement after its preview",
    )
    agent_install_skill.set_defaults(func=cmd_agent_install_skill)

    agent_skill_doctor = agent_kind.add_parser(
        "doctor",
        help="report missing, current, outdated, or custom user-level Skill state",
    )
    agent_skill_doctor.add_argument(
        "--client",
        required=True,
        choices=("codex", "claude", "claude-code"),
    )
    agent_skill_doctor.set_defaults(func=cmd_agent_skill_doctor)

    agent_classify_now = agent_kind.add_parser(
        "classify-now",
        help="ask the selected local Agent for another classification round",
    )
    agent_classify_now.set_defaults(func=cmd_agent_classify_now)

    agent_job_log = agent_kind.add_parser(
        "job-log",
        help="print the local client's own output from the last finished classification run",
    )
    agent_job_log.set_defaults(func=cmd_agent_job_log)

    setup = sub.add_parser(
        "setup",
        help="one command from a fresh checkout to a connected local Agent",
    )
    setup.add_argument(
        "--client",
        required=True,
        choices=["codex", "claude", "claude-code"],
        help="which locally installed client to connect",
    )
    setup.set_defaults(func=cmd_setup)

    rules = sub.add_parser(
        "rules",
        help="standing classification rules the ledger's owner has decreed",
    )
    rules_kind = rules.add_subparsers(dest="rules_command", required=True)
    rules_add = rules_kind.add_parser(
        "add-prefix",
        help="decree: descriptors starting with this text get this category",
    )
    rules_add.add_argument("prefix", help="the exact descriptor beginning, at least 6 characters")
    rules_add.add_argument("--category", required=True, help="the category id to apply")
    rules_add.set_defaults(func=cmd_rules_add_prefix)
    rules_list = rules_kind.add_parser("list", help="show every standing prefix rule")
    rules_list.set_defaults(func=cmd_rules_list)
    rules_remove = rules_kind.add_parser(
        "remove-prefix",
        help="withdraw one standing rule and every answer it derived",
    )
    rules_remove.add_argument("prefix", help="the exact prefix the rule was decreed with")
    rules_remove.set_defaults(func=cmd_rules_remove_prefix)

    doctor = sub.add_parser("doctor", help="show paths, schema version and pending reviews")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
