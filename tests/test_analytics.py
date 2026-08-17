# SPDX-License-Identifier: AGPL-3.0-or-later
"""P2 M5: what a category cost, and what the two charts are allowed to claim.

``v_category_spend`` — introduced by migration ``0007`` and rebuilt by ``0008``
as a projection of ``v_cashflow_line`` — answers "how much did this category
cost", and :func:`~ledgerbox.db.repo.category_spend` answers it a second time in
Python because that is the one the endpoint calls. Both are asserted against the
headline figure here and by ``verify``; checking only the first was a real hole
and ``tests/test_pipeline.py`` carries its negative case. This module exists
mainly to carry one sentence that the migration states and does not prove:

    the slices add up to the figure printed at the top of the page.

Everything a breakdown chart says rests on that. A pie whose wedges sum to
something merely *near* the headline Out is a fourth cashflow measurement, and
``docs/STATUS.md`` §5.45 records what the third one cost: a paragraph written
four times, refuted by construction three times, and a block-level check
written to end the argument. So it is asserted here rather than argued
anywhere.

The ledger-building helpers come from ``test_transactions`` rather than being
written again. They are careful about the thing that is easy to get wrong --
the rules' answer goes to ``posting.category_id``, a person's to
``category_override``, and nothing writes an effective value anywhere -- and a
second copy of that care is how two definitions of the same shape start
disagreeing (§5.29). ``tests/synth.py`` is imported the same way elsewhere in
this suite.

The second half of the file is about the two readers the page actually calls --
:func:`~ledgerbox.db.repo.category_spend` and
:func:`~ledgerbox.db.repo.monthly_cashflow` -- rather than about the SQL
underneath them. Those are separate questions: the view can be right while the
dataclass around it substitutes a placeholder for ``None``, re-sorts the rows,
or reports a total that is not the sum of what it returned. The last section
asks the same things of the operator's own 13 statements, and skips without
``LEDGERBOX_REAL_FIXTURES``.

Nothing here was copied out of a statement. The amounts are round, the
descriptors are invented, and no value in this file came from looking at real
data -- except the regression integers in
:func:`test_the_measured_category_distribution_by_amount_has_not_moved`, which
are measurements of the operator's ledger and are written as bare integers
inside assertions, never restated in prose. Eight leaks in this project's
history went into a sentence explaining something.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_pipeline import EXPECTED_CLAIMED, EXPECTED_MONTHS
from test_transactions import Line, book

from ledgerbox.config import DataPaths
from ledgerbox.db.connection import transaction
from ledgerbox.db.migrate import open_ledger
from ledgerbox.db.repo import (
    NO_CATEGORY,
    DateSpan,
    TransactionQuery,
    category_spend,
    clear_category_override,
    ledger_totals,
    list_transactions,
    monthly_cashflow,
    set_category_override,
)
from ledgerbox.ingest import pipeline

WHEN = "2026-03-04T00:00:00+00:00"


@pytest.fixture
def db(git_free_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = open_ledger(git_free_tmp / "ledger.db")
    try:
        yield conn
    finally:
        conn.close()


def _override(conn: sqlite3.Connection, txn_id: str, category_id: str | None) -> None:
    """Record or withdraw a person's decision, in its own transaction.

    Wrapped rather than called bare so every test here writes the way the
    application writes -- ``api.routes.transactions`` holds one handle across
    the lookup, the write and the read-back.
    """
    with transaction(conn):
        if category_id is None:
            clear_category_override(conn, txn_id=txn_id)
        else:
            set_category_override(conn, txn_id=txn_id, category_id=category_id, created_at=WHEN)


def _slices(conn: sqlite3.Connection) -> dict[str | None, int]:
    """``{effective category or None: spend_minor}`` straight from the view."""
    return {
        row["category_id"]: int(row["spend_minor"])
        for row in conn.execute("SELECT * FROM v_category_spend")
    }


def _counts(conn: sqlite3.Connection) -> dict[str | None, int]:
    return {
        row["category_id"]: int(row["txn_count"])
        for row in conn.execute("SELECT * FROM v_category_spend")
    }


#: One statement with every shape the breakdown has to handle: a line the rules
#: claimed, two they did not, a deposit (which has no expense leg at all), and a
#: line the rules flagged as a transfer.
LINES = (
    Line(amount_minor=-1_000, descriptor="lunch counter", rule_category="dining"),
    Line(amount_minor=-2_500, descriptor="nothing claims this one"),
    Line(amount_minor=-400, descriptor="nor this one"),
    Line(amount_minor=5_000, descriptor="money arriving"),
    Line(amount_minor=-700, descriptor="rule flagged this", rule_transfer=True),
)


def test_the_slices_add_up_to_the_headline_out(db: sqlite3.Connection) -> None:
    """The equality every M5 figure stands on, on the shape that has all five cases.

    Not "close to" and not "for the categories that matched": the whole of
    ``outflow_minor``, including the part no rule claimed, is accounted for by
    the rows of this view. If this ever fails, a chart drawn from the view is
    claiming to be a breakdown of a number it is not a breakdown of.
    """
    book(db, LINES)

    slices = _slices(db)
    assert sum(slices.values()) == ledger_totals(db)["outflow_minor"]
    # And the value itself, so a failure says which side moved rather than only
    # that the two moved together.
    assert sum(slices.values()) == -3_900


def test_the_lines_no_rule_claimed_are_a_group_and_not_a_gap(db: sqlite3.Connection) -> None:
    """NULL is a slice with an amount, because the alternative is the old bug.

    The predecessor's breakdown looked complete because its catch-all was also
    a wrong rule (§5.38), so "other" came to almost nothing. Here there is no
    catch-all to fall into and the unclaimed lines keep their own group -- which
    is what lets a chart give them area instead of quietly rounding coverage up
    to everything.
    """
    book(db, LINES)

    assert _slices(db)[None] == -2_900, "both unclaimed withdrawals, and nothing else"
    assert _counts(db)[None] == 2
    assert set(_slices(db)) == {None, "dining"}, "the deposit and the transfer are not spending"


def test_a_deposit_is_not_in_the_breakdown_at_all(db: sqlite3.Connection) -> None:
    """It has an income counter-leg, and this view reads expense legs.

    Worth pinning separately: a breakdown that counted deposits would still sum
    to *a* number, just not to the one on the page.
    """
    book(db, (Line(amount_minor=5_000, descriptor="money arriving"),))

    assert _slices(db) == {}
    assert ledger_totals(db)["outflow_minor"] == 0


def test_a_transfer_marked_by_hand_leaves_the_breakdown_and_comes_back(
    db: sqlite3.Connection,
) -> None:
    """EXECUTION_PLAN §7's "transfers do not appear in the spending pie", at last.

    That acceptance item has been unreachable for the whole project: the rules
    claim none of the author's 415 real lines (§5.52), so before M4 gave a
    person a way to mark one there was no way to reach the condition. It is
    reached here deliberately, in both directions, because "it left the chart"
    and "it can come back" are two facts and only one of them is about the
    exclusion working.
    """
    ids = book(db, LINES)
    before = _slices(db)
    assert before["dining"] == -1_000

    _override(db, ids[0], "transfer")

    after = _slices(db)
    assert "dining" not in after, "the line a person called a transfer is not spending"
    assert sum(after.values()) == -2_900, "and the total shrank by exactly that line"
    assert sum(after.values()) == ledger_totals(db)["outflow_minor"]

    _override(db, ids[0], None)

    assert _slices(db) == before, "withdrawing the decision restores it exactly"
    assert sum(_slices(db).values()) == ledger_totals(db)["outflow_minor"]


def test_an_override_moves_a_line_between_slices_without_moving_the_total(
    db: sqlite3.Connection,
) -> None:
    """Recategorising is a redistribution. The total is not a category's business."""
    ids = book(db, LINES)
    total = sum(_slices(db).values())

    _override(db, ids[1], "groceries")

    slices = _slices(db)
    assert slices["groceries"] == -2_500
    assert slices[None] == -400, "only the line that was overridden left the unclaimed group"
    assert sum(slices.values()) == total == ledger_totals(db)["outflow_minor"]


def test_an_income_category_on_a_withdrawal_stays_in_the_total(db: sqlite3.Connection) -> None:
    """A refund really is dining, and a person is allowed to say so.

    ``repo.list_categories`` deliberately offers every category whatever the
    sign of the line, so an income category can end up on a withdrawal. This
    view groups it under whatever category is effective and does **not** filter
    on the category's ``kind``: dropping the row would shrink somebody's
    spending silently, which is this project's own failure mode aimed at itself.
    """
    ids = book(db, LINES)
    total = sum(_slices(db).values())

    _override(db, ids[2], "salary")

    slices = _slices(db)
    assert slices["salary"] == -400, "grouped under what it was called, not discarded"
    assert sum(slices.values()) == total == ledger_totals(db)["outflow_minor"]


# ---------------------------------------------------------------------------
# repo.category_spend -- the same rows, through the layer a chart reads
#
# Everything above asks the view. A view can be right while the function that
# wraps it is not: `total_minor` is summed in Python and could have been asked
# of the database instead, `None` could arrive as a placeholder string, and the
# order the wedges are drawn in is decided by the repository's ORDER BY rather
# than by migration 0007. Those are three ways this layer can lose what the
# view guarantees, and they are three tests.
# ---------------------------------------------------------------------------


def test_the_breakdown_total_is_its_own_slices_and_the_headline_out(
    db: sqlite3.Connection,
) -> None:
    """One number reached three ways, on the shape that has all five cases.

    ``CategoryBreakdown.total_minor`` is summed from the rows it returns, so no
    caller can print a figure its own wedges contradict. That is only worth
    something if the figure is also the Out already at the top of the page,
    which is the equality migration 0007 was shaped around.
    """
    book(db, LINES)

    breakdown = category_spend(db)
    assert breakdown.total_minor == sum(part.spend_minor for part in breakdown.slices)
    assert breakdown.total_minor == ledger_totals(db)["outflow_minor"]
    assert breakdown.total_minor == -3_900


def test_the_slice_counts_are_not_the_headlines_transaction_count(
    db: sqlite3.Connection,
) -> None:
    """Two counts of two different things, pinned apart so nobody unifies them.

    ``ledger_totals``' ``txn_count`` counts income *and* expense: it is how many
    transactions the four headline figures were computed from.
    ``CategoryBreakdown.txn_count`` counts the transactions behind the slices,
    which is spending only. On any ledger with a deposit in it the two differ,
    and they differ on purpose -- a breakdown of what was spent has nothing to
    say about a payday. Making one into the other would look like a fix.

    The difference is asked of the database rather than computed here. ``a + b
    == c`` where ``a`` and ``b`` were both asserted two lines above cannot fail
    whatever its message claims, and ``test_pipeline`` carries the record of
    that exact line shipping once.
    """
    book(db, LINES)

    breakdown = category_spend(db)
    assert breakdown.txn_count == sum(part.txn_count for part in breakdown.slices)
    assert breakdown.txn_count == 3

    totals = ledger_totals(db)
    assert totals["txn_count"] == 4
    assert breakdown.txn_count != totals["txn_count"]

    deposits = db.execute(
        "SELECT COUNT(*) FROM v_transaction WHERE amount_minor > 0 AND is_transfer = 0"
    ).fetchone()[0]
    assert deposits == 1
    assert totals["txn_count"] - breakdown.txn_count == deposits


#: Two categories with **equal** spend, booked in the opposite order to the one
#: they have to come back in: ``groceries`` is booked before ``dining`` and must
#: be returned after it.
TIED = (
    Line(amount_minor=-3_000, descriptor="nothing claims this one"),
    Line(amount_minor=-2_000, descriptor="nor this one"),
    Line(amount_minor=-2_000, descriptor="a grocery run", rule_category="groceries"),
    Line(amount_minor=-2_000, descriptor="lunch counter", rule_category="dining"),
)


def test_the_slices_are_largest_first_and_a_tie_is_broken_by_name(
    db: sqlite3.Connection,
) -> None:
    """Largest first, and the tied pair in a fixed place. The two halves differ.

    ``spend_minor`` is negative, so ascending is descending by magnitude. That
    half is load-bearing: reversing the ``ORDER BY`` turns this test and the
    real-corpus distribution below red.

    **The tiebreak is not, and saying so is the point.** Reordering the two
    equal slices requires deleting ``, category_id`` from the query, and doing
    that changes nothing observable -- not here, and not on 300 tied groups in
    a direct SQLite probe. ``GROUP BY category_id`` already emits its rows in
    ``category_id`` order and this engine's ``ORDER BY`` sorter happens to
    preserve it, so the clause is protecting against a sorter or a plan that
    does not, which is a thing SQL declines to promise and this build does not
    do. ``repo._CATEGORY_SPEND_SQL``'s own comment says the same in fewer
    words: the guarantee is what is missing, not the observed behaviour.

    So what these two lines pin is the order as measured, and a failure means
    the order moved -- not necessarily that the tiebreak did. Written down
    because a comment claiming this test proves the tiebreak works would be a
    claim stronger than its evidence, which is the defect this project keeps
    finding in its own prose.

    The second read is the same weak shape and is kept for the same honest
    reason: it would catch a reader that sorted from a set or a dict rather
    than from the query, and nothing more.
    """
    book(db, TIED)

    breakdown = category_spend(db)
    assert [part.category_id for part in breakdown.slices] == [None, "dining", "groceries"]
    assert [part.spend_minor for part in breakdown.slices] == [-5_000, -2_000, -2_000]

    assert category_spend(db).slices == breakdown.slices


def test_the_unclaimed_slice_arrives_as_none_and_not_as_a_stand_in(
    db: sqlite3.Connection,
) -> None:
    """``None`` survives the dataclass, because the alternative is unsayable.

    A placeholder here would make "no rule claimed this" and "somebody chose the
    category called ``(none)``" arrive looking identical, and on the operator's
    own ledger the first is most of the chart. ``NO_CATEGORY`` is named rather
    than guarded against in general because it is a real string in the same
    module, meaning this exact concept for the transaction filter, and so it is
    the substitution somebody would actually reach for.
    """
    book(db, LINES)

    ids = [part.category_id for part in category_spend(db).slices]
    assert ids.count(None) == 1
    assert NO_CATEGORY not in ids
    assert "" not in ids

    unclaimed = next(part for part in category_spend(db).slices if part.category_id is None)
    assert unclaimed.category_id is None
    assert unclaimed.spend_minor == -2_900


def test_an_empty_ledger_breaks_down_into_nothing(db: sqlite3.Connection) -> None:
    """No rows -- not one row of zero -- and the sums still exist.

    A chart handed a single empty slice draws a full circle labelled with
    nothing, which reads as "all of your spending is unclaimed" rather than as
    "there is no spending". The zero totals are asserted beside it because
    ``sum(())`` being ``0`` is what makes the dataclass safe to build here at
    all.
    """
    breakdown = category_spend(db)
    assert breakdown.slices == ()
    assert breakdown.total_minor == 0
    assert breakdown.txn_count == 0
    assert ledger_totals(db)["outflow_minor"] == 0


# ---------------------------------------------------------------------------
# repo.monthly_cashflow -- the bars, and the month they are filed under
# ---------------------------------------------------------------------------

#: One 64-character sha per statement. ``book`` derives the archived path from
#: the first eight characters, so two statements sharing one are one file.
SHAS = ("a" * 64, "b" * 64, "e" * 64)


def _book_three_statements(conn: sqlite3.Connection) -> None:
    """Three statements, booked newest first, each period ending in the next month.

    Two things are deliberate. The ingest order is not chronological, so the
    order the months come back in is the reader's doing rather than the
    insertion order's. And no period both starts and ends in the same month, so
    every ``statement_month`` differs from the month its own lines are dated in
    -- which is the only way "the period's end day" can be told apart from
    "either day would have done".
    """
    book(
        conn,
        (Line(amount_minor=3_000, descriptor="third money arriving", date="2025-07-20"),),
        sha256=SHAS[2],
        period_start="2025-07-06",
        period_end="2025-08-05",
    )
    book(
        conn,
        (
            Line(amount_minor=-1_000, descriptor="first lunch counter", rule_category="dining"),
            Line(amount_minor=5_000, descriptor="first money arriving"),
        ),
        sha256=SHAS[0],
        period_start="2025-05-04",
        period_end="2025-06-03",
    )
    book(
        conn,
        (
            Line(
                amount_minor=-2_000,
                descriptor="second grocery run",
                date="2025-06-10",
                rule_category="groceries",
            ),
            Line(amount_minor=-500, descriptor="second nothing claims this", date="2025-06-11"),
        ),
        sha256=SHAS[1],
        period_start="2025-06-04",
        period_end="2025-07-05",
    )


def test_the_months_come_back_oldest_first(db: sqlite3.Connection) -> None:
    """A time axis reads left to right, and this is what puts it that way.

    Asserted as the whole list. ``sorted(months) == months`` is a tautology over
    any list of one, and the fixture books the newest statement first precisely
    so that "already in order" is not the thing being observed.
    """
    _book_three_statements(db)

    assert [month.month for month in monthly_cashflow(db).months] == [
        "2025-05",
        "2025-06",
        "2025-07",
    ]


def test_the_bars_are_keyed_by_the_transaction_date_and_the_statement_month_survives(
    db: sqlite3.Connection,
) -> None:
    """Both date questions, on one ledger, giving different answers on purpose.

    This is the assertion standing where the predecessor's worst-labelled defect
    was. It had two month definitions -- its chart bucketed by the transaction
    date, its table by the statement month -- and 83 of its 415 rows fell in
    different buckets with nothing on screen saying which was which.

    This project keeps both, because they answer different questions:

    * the bars ask *when did this happen*, so P2 M6 keys them by ``txn.date``.
      It is also the only one of the two that can express "the last week", which
      is why the page's date range is on that column;
    * ``statement_month`` asks *which statement is this printed on*, and is
      still derived from the period's **end** day -- taking the start day is
      what deleted three months from the predecessor's output entirely.

    The line below is dated in May on a statement that ends in June, so the two
    answers cannot coincide by luck, and both are asserted. What is forbidden is
    not that they differ; it is either one silently standing in for the other.
    """
    book(
        db,
        (Line(amount_minor=-1_000, descriptor="lunch counter", date="2025-05-06"),),
        period_start="2025-05-04",
        period_end="2025-06-03",
    )

    assert [month.month for month in monthly_cashflow(db).months] == ["2025-05"], (
        "the bars are keyed by the date the transaction carries"
    )
    filed = [row[0] for row in db.execute("SELECT statement_month FROM v_statement")]
    assert filed == ["2025-06"], "and the statement is still filed under its period's end"


def test_each_month_balances_and_the_aggregates_are_the_months(db: sqlite3.Connection) -> None:
    """The bars, their own total, and the figures at the top of the page: one set.

    Three claims, and the third has a second guard behind it.

    ``inflow + outflow == net`` per month is the arithmetic a stacked bar draws
    directly. A month where it fails is a bar whose two halves do not meet the
    line labelled net.

    The four aggregate fields being the sums over ``.months`` is what stops a
    caller re-summing the rows itself, or asking the database a second time, and
    drawing bars that do not add up to the figure printed under them.

    Those aggregates equalling ``ledger_totals`` is what makes the bars a
    decomposition of the four figures rather than a second measurement of them.
    Since P2 M6 both are sums of ``v_cashflow_line`` under the same predicate,
    so this holds for any date window and not only for the unscoped one --
    which is the property that let the range control apply to the whole page.
    ``verify``'s ``cashflow_agreement`` pins the unscoped case against
    ``v_cashflow_monthly``, an independently written view, so the two are not
    redundant: that one can catch this pair drifting together.
    """
    _book_three_statements(db)

    cashflow = monthly_cashflow(db)
    for month in cashflow.months:
        assert month.inflow_minor + month.outflow_minor == month.net_minor, month.month

    assert cashflow.inflow_minor == sum(month.inflow_minor for month in cashflow.months)
    assert cashflow.outflow_minor == sum(month.outflow_minor for month in cashflow.months)
    assert cashflow.net_minor == sum(month.net_minor for month in cashflow.months)
    assert cashflow.txn_count == sum(month.txn_count for month in cashflow.months)

    totals = ledger_totals(db)
    assert cashflow.inflow_minor == totals["inflow_minor"]
    assert cashflow.outflow_minor == totals["outflow_minor"]
    assert cashflow.net_minor == totals["net_minor"]
    assert cashflow.txn_count == totals["txn_count"]

    # And the values, so a failure says which side moved rather than only that
    # the two moved together.
    assert cashflow.inflow_minor == 8_000
    assert cashflow.outflow_minor == -3_500
    assert cashflow.net_minor == 4_500
    assert [month.net_minor for month in cashflow.months] == [4_000, -2_500, 3_000]


def test_a_transfer_marked_by_hand_leaves_the_month_it_was_in(db: sqlite3.Connection) -> None:
    """The bars exclude it for the reason the pie does, and by the same route.

    ``v_transaction.is_transfer`` folds a person's ``category_override`` over the
    rules' flag, so marking one line moves the month's bar, the headline figures
    and the breakdown together. Asserted as a movement -- what the month was,
    what it became, by how much -- rather than as a final value, because a final
    value alone cannot tell "that line left" from "the month was rebuilt out of
    something else".
    """
    ids = book(db, LINES)

    before = monthly_cashflow(db)
    assert [month.month for month in before.months] == ["2025-05"]
    assert before.months[0].outflow_minor == -3_900
    assert before.months[0].txn_count == 4

    _override(db, ids[0], "transfer")

    after = monthly_cashflow(db)
    assert [month.month for month in after.months] == ["2025-05"], "the month stays"
    assert after.months[0].outflow_minor == before.months[0].outflow_minor + 1_000
    assert after.months[0].txn_count == before.months[0].txn_count - 1
    assert after.months[0].inflow_minor == before.months[0].inflow_minor, "a withdrawal left"

    totals = ledger_totals(db)
    assert after.outflow_minor == totals["outflow_minor"]
    assert after.inflow_minor == totals["inflow_minor"]
    assert after.txn_count == totals["txn_count"]

    _override(db, ids[0], None)
    assert monthly_cashflow(db) == before, "withdrawing the decision restores it exactly"


def test_an_empty_ledger_has_no_months(db: sqlite3.Connection) -> None:
    """No bars, and four zeros rather than four ``None``s for a caller to divide by."""
    cashflow = monthly_cashflow(db)
    assert cashflow.months == ()
    assert cashflow.inflow_minor == 0
    assert cashflow.outflow_minor == 0
    assert cashflow.net_minor == 0
    assert cashflow.txn_count == 0


# ---------------------------------------------------------------------------
# the operator's own 13 statements
#
# Every ledger above was built to have a shape. These have whatever shape they
# have, which is the only way to find out that a reader is correct on the four
# cases somebody thought of and wrong on the corpus. Skips without
# `LEDGERBOX_REAL_FIXTURES`, so CI never needs real data.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def real_ledger(
    git_free_tmp_root: Path, real_statements: list[Path]
) -> Iterator[sqlite3.Connection]:
    """All 13 real statements, ingested once for this module.

    Deliberately **not** ``test_pipeline``'s ``ingested_real``, and the reason is
    mechanical rather than stylistic. Importing a fixture function into a second
    module gives pytest a second ``FixtureDef``, so a session-scoped body runs
    once per importing module -- and that one resolves a fixed
    ``real-ledger`` directory, so the second run ingests into a database that
    already holds all 13. Every outcome comes back ``already imported`` and
    whichever module ran second fails, for a reason nowhere near what it is
    testing. Verified by doing it. The directory name is the only difference
    here; if these two ever need to be one fixture, it belongs in ``conftest``.
    """
    paths = DataPaths.resolve(git_free_tmp_root / "analytics-real-ledger")
    conn = open_ledger(paths.db)
    outcomes = pipeline.ingest_paths(conn, paths, real_statements)
    failures = [out.summary_line() for out in outcomes if out.status != pipeline.IMPORTED]
    assert failures == [], "this module's own ingest of the corpus must be a clean one"
    try:
        yield conn
    finally:
        conn.close()


def test_the_real_slices_add_up_to_the_real_headline_out(real_ledger: sqlite3.Connection) -> None:
    """The one equality M5 stands on, asked of the ledger it will be drawn from.

    The synthetic version of this is above and reaches five hand-built shapes.
    This one reaches whatever 13 statements of a real account contain, which is
    the difference between "the equality holds for the cases somebody imagined"
    and "the equality holds".
    """
    breakdown = category_spend(real_ledger)
    assert breakdown.total_minor == sum(part.spend_minor for part in breakdown.slices)
    assert breakdown.total_minor == ledger_totals(real_ledger)["outflow_minor"]


def test_no_real_month_is_lost_on_the_way_to_the_bars(real_ledger: sqlite3.Connection) -> None:
    """Every month the ledger has a transaction in gets a bar, in order.

    The predecessor drew this chart with three months missing. Counting the bars
    is not enough to catch that -- two of its statements collapsed onto
    neighbours and the count merely dropped -- so the months are compared as a
    **set** against the distinct months the database itself reports, asked
    through neither of the two views the chart is built on.

    Both date questions are pinned here, because the corpus is the one place
    they can be seen to differ on real data: the statements are filed under 13
    period-end months, and the bars are keyed by transaction date. Whether those
    two sets coincide on this ledger is measured, not assumed -- a statement
    period straddles a month boundary, so they need not.
    """
    filed = {row[0] for row in real_ledger.execute("SELECT statement_month FROM v_statement")}
    assert len(filed) == EXPECTED_MONTHS, "13 statements, filed under 13 period-end months"

    dated = {
        row[0]
        for row in real_ledger.execute(
            "SELECT DISTINCT substr(date, 1, 7) FROM txn WHERE superseded_by IS NULL"
        )
    }

    reported = [month.month for month in monthly_cashflow(real_ledger).months]
    assert set(reported) <= dated, "no bar for a month nothing is dated in"
    assert reported == sorted(reported), "oldest first"
    assert len(reported) == len(set(reported)), "one bar per month"
    assert None not in reported

    # The opening entry is dated before the first statement and books against
    # equity, so it is in `dated` and not in the bars: `v_cashflow_line` carries
    # income and expense legs only. Everything else must have a bar.
    assert dated - set(reported) <= {min(dated)}


def test_the_real_monthly_aggregates_are_the_real_headline_figures(
    real_ledger: sqlite3.Connection,
) -> None:
    """``cashflow_agreement`` on the corpus, from the reader's side.

    ``verify`` asserts this on the operator's machine over the same view. Doing
    it here as well is what makes a change to either SQL text fail in a suite
    somebody is watching, rather than on a laptop after the fact.
    """
    cashflow = monthly_cashflow(real_ledger)
    totals = ledger_totals(real_ledger)
    assert cashflow.inflow_minor == totals["inflow_minor"]
    assert cashflow.outflow_minor == totals["outflow_minor"]
    assert cashflow.net_minor == totals["net_minor"]
    assert cashflow.txn_count == totals["txn_count"]

    for month in cashflow.months:
        assert month.inflow_minor + month.outflow_minor == month.net_minor, month.month


def test_the_measured_category_distribution_by_amount_has_not_moved(
    real_ledger: sqlite3.Connection,
) -> None:
    """What the shipped rules claim on the corpus **by amount**, measured at last.

    P2 M1 pinned the distribution by *line count* -- ``EXPECTED_CLAIMED`` lines
    claimed, the rest stored ``NULL``. By amount it had never been measured, and
    the two are not the same distribution: a rule that claims many small lines
    and a rule that claims one large one are indistinguishable by count and are
    not remotely the same wedge. This is the measurement the chart draws.

    What the assertions say, in order: how many slices there are; that the group
    nothing claimed is present and is the largest of them; that the ordering is
    genuinely by amount; the whole ordering by name; and the counts.

    **No amount is pinned here, and the omission is the decision rather than an
    oversight.** A first version of this test pinned all nine as bare integers.
    One of those slices has a ``txn_count`` of 1 -- so its "aggregate" is a
    single real transaction's exact amount, printed beside the category it fell
    into, in a file bound for a public repository. ``docs/STATUS.md`` §6.5's
    seventh entry settled this exact question once already, for thirteen monthly
    subtotals that were nobody's single transaction: keep what is already public,
    derive the rest at runtime, and do not commit the figures.

    Shares are no better and were considered: the total these would be shares of
    is in the README, so a percentage and an amount are the same disclosure
    wearing different units.

    What that costs is real and is not talked around: a rules change that moved
    the amounts without moving any count or the ordering would not turn this
    red. The ordering *is* sensitive -- it is the ranking the chart draws, and it
    is not the ranking by count -- and the counts are pinned both here and in
    ``test_categorize``. That is the trade, taken knowingly.

    The claimed counts come to ``EXPECTED_CLAIMED``, which is
    ``test_pipeline``'s count of *postings* carrying a category, while this
    counts *spending transactions* carrying one. They are equal only because no
    income category claims anything on this corpus -- which
    ``test_no_income_category_claims_anything_yet`` pins separately, and which is
    the thing that would have to change for these two to come apart.
    """
    breakdown = category_spend(real_ledger)

    # Re-measured 2026-08-16, the first real-corpus run since A6.5 added pet,
    # rewards and cash-deposit: `pet` claims one spending line and lands by
    # amount between dining and transport.
    assert len(breakdown.slices) == 12
    assert breakdown.slices[0].category_id is None
    assert breakdown.slices[0].spend_minor == min(part.spend_minor for part in breakdown.slices)

    assert [part.category_id for part in breakdown.slices] == [
        None,
        "taxes",
        "shopping",
        "subscriptions",
        "fees",
        "insurance",
        "groceries",
        "dining",
        "pet",
        "transport",
        "entertainment",
        "sport",
    ]

    # Ordered by amount, strictly, and asked of the values themselves rather
    # than trusted from the name order below: a ranking that came back sorted by
    # anything else would still satisfy a list of names.
    amounts = [part.spend_minor for part in breakdown.slices]
    assert amounts == sorted(amounts), "largest spend first; spend_minor is negative"
    assert len(set(amounts)) == len(amounts), "no ties here, so the order is total"

    assert [part.txn_count for part in breakdown.slices] == [
        201,
        1,
        26,
        41,
        36,
        5,
        5,
        8,
        1,
        8,
        7,
        3,
    ]

    claimed = sum(part.txn_count for part in breakdown.slices if part.category_id is not None)
    # EXPECTED_CLAIMED counts postings of any kind; this breakdown counts
    # spending transactions only. They were equal while no income rule claimed
    # anything; since A6.5 four deposit rows are income-claimed (rewards 2,
    # cash-deposit 2 -- pinned in test_pipeline), so the two measures differ by
    # exactly those four.
    assert claimed == EXPECTED_CLAIMED - 4

    # The transactions the breakdown does not describe are the deposits, asked
    # of the database rather than subtracted from two numbers already asserted.
    deposits = real_ledger.execute(
        "SELECT COUNT(*) FROM v_transaction WHERE amount_minor > 0 AND is_transfer = 0"
    ).fetchone()[0]
    assert ledger_totals(real_ledger)["txn_count"] - breakdown.txn_count == deposits


# ---------------------------------------------------------------------------
# P2 M6: the date range
#
# The range is on `txn.date` and the reason is on `repo.DateSpan`: it is the one
# column every money query already joins, so a bound adds no join and can
# therefore neither drop a row nor duplicate one. Which is what these tests are
# really about -- not that filtering works, but that **the two equalities the
# charts rest on survive a filter**. Before M6 they held for the whole ledger; a
# range that broke them would leave a page whose pictures no longer add up to
# the figures above them, and only for some windows.
# ---------------------------------------------------------------------------


#: One statement whose lines fall in three different months, so a window can cut
#: it in the middle and there is something on either side of the cut.
SPREAD = (
    Line(amount_minor=-1_000, descriptor="in may", date="2025-05-10", rule_category="dining"),
    Line(amount_minor=5_000, descriptor="also in may", date="2025-05-20"),
    Line(amount_minor=-2_000, descriptor="in june", date="2025-06-10", rule_category="groceries"),
    Line(amount_minor=-400, descriptor="in july", date="2025-07-04"),
)

#: Windows worth asking every equality about, including one that selects nothing.
WINDOWS = [
    DateSpan(),
    DateSpan(since="2025-06-01"),
    DateSpan(until="2025-06-30"),
    DateSpan(since="2025-05-15", until="2025-06-15"),
    DateSpan(since="2030-01-01", until="2030-12-31"),
]


@pytest.mark.parametrize(
    "since, until",
    [
        ("2025-13-01", None),
        ("not-a-date", None),
        (None, "2025-06-3"),
        ("2025-W23-1", None),
    ],
)
def test_a_date_that_is_not_a_date_is_refused_where_it_is_built(
    since: str | None, until: str | None
) -> None:
    """Validated where it is constructed, not where it reaches SQL.

    The same placement ``TransactionQuery`` uses: a caller gets a ``ValueError``
    on its own line rather than a string that travels into a query. HTTP refuses
    these earlier still, with a pattern, so this is the guard for every other
    caller.
    """
    with pytest.raises(ValueError):
        DateSpan(since=since, until=until)


def test_a_range_that_ends_before_it_starts_is_refused() -> None:
    """Not answered with an empty result, which would be true and useless.

    "No rows matched" is a fact about a window nobody meant to ask for. Nothing
    a person can type into a date control should quietly come back meaning
    nothing.
    """
    with pytest.raises(ValueError, match="after"):
        DateSpan(since="2025-07-01", until="2025-06-30")

    assert DateSpan(since="2025-06-30", until="2025-06-30").bounded, "one day is a range"


def test_both_ends_of_a_range_are_inclusive(db: sqlite3.Connection) -> None:
    """What a person means by "the 1st to the 31st" includes both of them.

    Pinned against the exact dates of two lines rather than a wide window, so an
    off-by-one at either end changes the answer instead of being absorbed.
    """
    book(db, SPREAD)

    assert ledger_totals(db, DateSpan(since="2025-05-10", until="2025-05-20"))["txn_count"] == 2
    assert ledger_totals(db, DateSpan(since="2025-05-11", until="2025-05-19"))["txn_count"] == 0


@pytest.mark.parametrize("span", WINDOWS)
def test_the_slices_add_up_to_the_out_under_every_window(
    db: sqlite3.Connection, span: DateSpan
) -> None:
    """The M5 equality, asked again for each window including the empty one.

    This is the assertion that made the date range affordable. Both sides are
    sums of ``v_cashflow_line`` under the same bound, so the equality is a
    property of the row set rather than of two queries agreeing -- which is what
    makes it hold for a window nobody has tried.
    """
    book(db, SPREAD)

    breakdown = category_spend(db, span)
    assert breakdown.total_minor == sum(part.spend_minor for part in breakdown.slices)
    assert breakdown.total_minor == ledger_totals(db, span)["outflow_minor"]


@pytest.mark.parametrize("span", WINDOWS)
def test_the_months_add_up_to_the_figures_under_every_window(
    db: sqlite3.Connection, span: DateSpan
) -> None:
    """The bars are the four figures decomposed, so they sum back to them.

    Includes a window the ledger has nothing in, which is exactly where a
    ``SUM`` that should have been a ``COALESCE`` shows itself.
    """
    book(db, SPREAD)

    months = monthly_cashflow(db, span)
    totals = ledger_totals(db, span)
    assert months.inflow_minor == totals["inflow_minor"]
    assert months.outflow_minor == totals["outflow_minor"]
    assert months.net_minor == totals["net_minor"]
    assert months.txn_count == totals["txn_count"]
    assert months.inflow_minor == sum(month.inflow_minor for month in months.months)


def test_a_window_selects_the_months_it_covers_and_no_others(db: sqlite3.Connection) -> None:
    book(db, SPREAD)

    assert [m.month for m in monthly_cashflow(db).months] == ["2025-05", "2025-06", "2025-07"]

    narrowed = DateSpan(since="2025-06-01", until="2025-06-30")
    assert [m.month for m in monthly_cashflow(db, narrowed).months] == ["2025-06"]
    assert monthly_cashflow(db, narrowed).outflow_minor == -2_000

    empty = DateSpan(since="2030-01-01")
    assert monthly_cashflow(db, empty).months == ()
    assert monthly_cashflow(db, empty).outflow_minor == 0


def test_a_window_moves_which_categories_have_a_slice(db: sqlite3.Connection) -> None:
    """A category with nothing in the window is absent, not zero.

    A zero-height slice is a claim that the category was looked for and found
    empty; an absent one says the truth, which is that nothing in this window
    belongs to it. The unclaimed group follows the same rule and is not special.
    """
    book(db, SPREAD)

    june = DateSpan(since="2025-06-01", until="2025-06-30")
    assert {part.category_id for part in category_spend(db, june).slices} == {"groceries"}

    july = DateSpan(since="2025-07-01", until="2025-07-31")
    assert {part.category_id for part in category_spend(db, july).slices} == {None}


def test_the_balance_takes_the_closing_bound_only(db: sqlite3.Connection) -> None:
    """A balance is a level, and a level does not have a start.

    ``since`` bounds the flows, because "what did I spend in June" is a question
    about June. It must not bound the balance, because "what did I have at the
    end of June" is not -- money already there arrived earlier by definition.
    Reporting the second under the first's window would put a figure labelled
    Balance on the page that actually meant the movement within the window.
    """
    book(db, SPREAD)

    whole = ledger_totals(db)["balance_minor"]
    assert whole == 1_600

    assert ledger_totals(db, DateSpan(until="2025-06-30"))["balance_minor"] == 2_000
    assert ledger_totals(db, DateSpan(since="2025-06-01"))["balance_minor"] == whole
    assert (
        ledger_totals(db, DateSpan(since="2025-06-01", until="2025-06-30"))["balance_minor"]
        == 2_000
    ), "the same closing balance the closing bound gives on its own"


def test_a_window_ending_before_the_ledger_begins_has_no_balance_to_report(
    db: sqlite3.Connection,
) -> None:
    """$0.00 there would be a claim about a day this ledger has never heard of.

    The date control accepts any date, so this is one typed date away on a page
    that also states, in ``routes/analytics.py``'s own words, that a balance is
    "a claim about money" and must not be printed for a ledger that has none.
    ``/api/health`` sends ``totals: null`` rather than a zeroed object for that
    reason; this query was answering ``0`` for the same question with a window
    on it.

    The three flows stay ``0`` and are right to: they are sums over a set the
    caller chose, and the set is empty. Only the balance is a position rather
    than a sum, and only it has nothing to stand on.
    """
    book(db, SPREAD)

    before = ledger_totals(db, DateSpan(until="2000-01-01"))
    assert before["balance_minor"] is None
    assert before["inflow_minor"] == 0
    assert before["outflow_minor"] == 0
    assert before["txn_count"] == 0

    # And the boundary is a real date, not a mood: the day the first posting
    # lands, there is a balance.
    first = min(row[0] for row in db.execute("SELECT date FROM txn").fetchall())
    assert ledger_totals(db, DateSpan(until=first))["balance_minor"] is not None


def test_a_balance_that_really_is_nothing_is_reported_as_nothing(
    db: sqlite3.Connection,
) -> None:
    """Zero and unknown are two answers, and the count is what tells them apart.

    Keyed on whether any own-account posting was selected rather than on whether
    the sum came out ``NULL``, because a ledger whose legs cancel exactly has a
    balance and it is $0.00. Getting this backwards would replace one wrong
    answer with another: "we cannot say" printed over an account that really is
    empty.
    """
    book(db, SPREAD)

    whole = ledger_totals(db)["balance_minor"]
    assert whole == 1_600

    # Send the balance to exactly zero by booking its negation, rather than by
    # emptying the ledger -- the point is a measured zero, not an absence.
    book(db, (Line(amount_minor=-whole, descriptor="spends the lot", date="2025-08-01"),))

    settled = ledger_totals(db)["balance_minor"]
    assert settled == 0
    assert settled is not None, "a measured zero is a balance, and it is $0.00"


def test_the_range_narrows_the_transaction_table_by_the_same_column(
    db: sqlite3.Connection,
) -> None:
    """The table and the charts show one window of the ledger, or neither can be read.

    The table keeps its own ``month`` control as well, and the two are different
    questions: the span asks when a line happened, ``month`` asks which
    statement it is printed on. Both are applied at once here, to pin that they
    combine rather than one overriding the other.
    """
    book(db, SPREAD, period_start="2025-05-04", period_end="2025-07-31")

    june = DateSpan(since="2025-06-01", until="2025-06-30")
    rows = list_transactions(db, TransactionQuery(span=june))
    assert [row["raw_descriptor"] for row in rows] == ["in june"]

    both = TransactionQuery(span=june, month="2025-07")
    assert len(list_transactions(db, both)) == 1, "its statement's month, and its own date"

    assert list_transactions(db, TransactionQuery(span=june, month="2025-01")) == []
