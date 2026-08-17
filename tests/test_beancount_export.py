# SPDX-License-Identifier: AGPL-3.0-or-later
"""The beancount export, checked against beancount itself — from the outside.

Two things this module is careful about.

**It never imports beancount.** beancount is GPL-2.0-only and ledgerbox is not;
:mod:`ledgerbox.ledger.beancount_export` explains why that matters. Validation
runs the ``bean-check`` *executable* through :mod:`subprocess`, and every number
read back out of an export is re-derived with a regular expression written here.
Checking a beancount exporter with beancount's own parser would also be the
weaker test: two copies of the same misunderstanding agree with each other.

**A missing ``bean-check`` skips, never fails.** CI has no beancount and is not
going to get one. Point ``LEDGERBOX_BEAN_CHECK`` at an executable, or put one on
``PATH``, to turn these on::

    python -m venv /somewhere/outside/the/repo/bcvenv
    /somewhere/outside/the/repo/bcvenv/bin/pip install beancount
    LEDGERBOX_BEAN_CHECK=/somewhere/outside/the/repo/bcvenv/bin/bean-check pytest

Installing beancount into the project's own ``.venv`` would defeat the point:
``import beancount`` has to stay impossible.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from ledgerbox.config import DataPaths
from ledgerbox.db import repo
from ledgerbox.db.connection import connect_read_only, transaction
from ledgerbox.db.migrate import open_ledger, schema_version
from ledgerbox.ingest import pipeline
from ledgerbox.ledger import beancount_export
from ledgerbox.ledger.beancount_export import (
    EPOCH_DATE,
    EXPORT_FILENAME,
    BeancountExportError,
    export_beancount,
    render_beancount,
)

ENV_BEAN_CHECK = "LEDGERBOX_BEAN_CHECK"

CHECKING_ID = "assets:chase:checking:1234"
CHECKING_NAME = "Assets:Chase:Checking:1234"
INCOME_ID = "income:uncategorized"
EXPENSE_ID = "expenses:uncategorized"

#: The P0 monetary acceptance numbers come from the untracked
#: expected-totals.json beside the real statements (the `real_expected`
#: fixture): hardcoded relative to the code under test, so a test that asks
#: the code what the answer should be is still not a thing -- but no longer
#: hardcoded into a public repository, because they are the owner's real
#: figures.

#: Rows that came off a statement. ``repo.sync_opening_entry`` adds one more
#: transaction and two more postings, which are the account's starting balance
#: rather than anything the bank listed — see the count test below.
REAL_STATEMENT_TXNS = 415
REAL_STATEMENT_POSTINGS = 830


# ---------------------------------------------------------------------------
# reading an export back without beancount
# ---------------------------------------------------------------------------

#: A posting leg: two-space indent, account, amount, currency.
POSTING_LINE_RE = re.compile(r"^ {2}([A-Z][A-Za-z0-9:\-]*) +(-?\d+\.\d{2}) USD$", re.MULTILINE)

#: A transaction header. Directives start in column 0; legs never do.
TXN_LINE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}) ([*!]) (".*)$', re.MULTILINE)

BALANCE_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) balance ([A-Z][A-Za-z0-9:\-]*) +(-?\d+\.\d{2}) (\w+)$", re.MULTILINE
)

OPEN_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) open (\S+) (\w+)$", re.MULTILINE)

PAD_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) pad (\S+) (\S+)$", re.MULTILINE)


def to_minor(text: str) -> int:
    """``'-12.44'`` → ``-1244``, by integer arithmetic only.

    Deliberately not ``int(Decimal(text) * 100)``: the whole export exists to
    prove the integer discipline held, and reading it back through a decimal
    type would hide a rounding bug in exactly the place it would appear.
    """
    negative = text.startswith("-")
    whole, _, frac = text.lstrip("-").partition(".")
    minor = int(whole) * 100 + int(frac)
    return -minor if negative else minor


def legs_from(text: str, prefix: str) -> list[int]:
    """Every posting amount on an account under *prefix*, in minor units."""
    return [
        to_minor(amount)
        for account, amount in POSTING_LINE_RE.findall(text)
        if account.startswith(prefix)
    ]


def totals_from(text: str) -> dict[str, int]:
    """Re-derive ``repo.ledger_totals`` from the file, the way a stranger would.

    Measured on the **income and expense legs**, matching ``_TOTALS_SQL``, and
    negated because double entry gives income a negative sign. Summing the bank
    leg instead would answer "how did the balance move", which is contaminated
    by transfers and by the opening entry — the predecessor summed the bank leg
    and reported 82.6% of its income as money moving between its owner's own
    accounts.

    ``account.kind`` maps onto the beancount root by construction: an
    ``income`` account is named ``Income:…``, an ``expense`` account
    ``Expenses:…``. That correspondence is what makes a file-side reconstruction
    possible at all.
    """
    income = legs_from(text, "Income:")
    expense = legs_from(text, "Expenses:")
    return {
        "inflow_minor": -sum(income),
        "outflow_minor": -sum(expense),
        "net_minor": -sum(income) - sum(expense),
        "balance_minor": sum(legs_from(text, "Assets:") + legs_from(text, "Liabilities:")),
        "posting_count": len(income) + len(expense),
    }


def transactions_from(text: str) -> list[tuple[str, str, list[tuple[str, int]]]]:
    """``[(date, rest-of-header, [(account, minor), …]), …]`` from the file.

    A hand-rolled block reader rather than beancount's parser, for the reason in
    the module docstring: a second copy of the same misunderstanding is not an
    independent check.
    """
    blocks: list[tuple[str, str, list[tuple[str, int]]]] = []
    for line in text.splitlines():
        header = TXN_LINE_RE.fullmatch(line)
        if header is not None:
            blocks.append((header.group(1), header.group(3), []))
            continue
        leg = POSTING_LINE_RE.fullmatch(line)
        if leg is not None:
            assert blocks, f"posting line with no transaction above it: {line!r}"
            blocks[-1][2].append((leg.group(1), to_minor(leg.group(2))))
    return blocks


# ---------------------------------------------------------------------------
# bean-check, out of process
# ---------------------------------------------------------------------------

BeanCheck = Callable[[Path], subprocess.CompletedProcess[str]]


def _bean_check_executable() -> str | None:
    override = os.environ.get(ENV_BEAN_CHECK, "").strip()
    if override:
        return override if Path(override).exists() else None
    return shutil.which("bean-check")


@pytest.fixture(scope="session")
def bean_check() -> BeanCheck:
    """Run ``bean-check`` on a file, or skip the test."""
    executable = _bean_check_executable()
    if executable is None:
        pytest.skip(f"no bean-check on PATH and ${ENV_BEAN_CHECK} is unset — see module docstring")

    def run(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [executable, str(path)], capture_output=True, text=True, timeout=300, check=False
        )

    return run


def assert_accepted(result: subprocess.CompletedProcess[str]) -> None:
    """bean-check is silent and returns 0 when it has nothing to complain about."""
    output = (result.stdout + result.stderr).strip()
    assert result.returncode == 0, f"bean-check rejected the export:\n{output}"
    assert output == "", f"bean-check reported problems:\n{output}"


# ---------------------------------------------------------------------------
# synthetic ledgers
# ---------------------------------------------------------------------------


def new_ledger(root: Path) -> tuple[sqlite3.Connection, DataPaths]:
    paths = DataPaths.resolve(root / "data")
    return open_ledger(paths.db), paths


def add_account(
    conn: sqlite3.Connection,
    *,
    account_id: str = CHECKING_ID,
    name: str = CHECKING_NAME,
    currency: str = "USD",
) -> None:
    conn.execute(
        "INSERT INTO account (id, name, kind, subtype, currency, institution, mask) "
        "VALUES (?, ?, 'asset', 'checking', ?, 'Chase', '1234')",
        (account_id, name, currency),
    )


def add_txn(
    conn: sqlite3.Connection,
    *,
    txn_id: str,
    day: str,
    narration: str | None,
    amount_minor: int,
    account_id: str = CHECKING_ID,
    payee: str | None = None,
    flag: str = "*",
    superseded_by: str | None = None,
    currency: str = "USD",
) -> None:
    """One balanced transaction: the bank's leg at seq 0, the counter at seq 1."""
    counter = INCOME_ID if amount_minor > 0 else EXPENSE_ID
    conn.execute(
        "INSERT INTO txn (id, date, payee, narration, flag, superseded_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, '2025-01-01T00:00:00+00:00')",
        (txn_id, day, payee, narration, flag, superseded_by),
    )
    for seq, (leg_account, minor) in enumerate(
        ((account_id, amount_minor), (counter, -amount_minor))
    ):
        conn.execute(
            "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"{txn_id}:{seq}", txn_id, seq, leg_account, minor, currency),
        )


def add_balance(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    amount_minor: int | None,
    account_id: str = CHECKING_ID,
    commodity_id: str = "USD",
    quantity_scaled: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO balance_assertion "
        "(id, account_id, as_of, commodity_id, amount_minor, quantity_scaled) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            f"{account_id}@{as_of}@{commodity_id}",
            account_id,
            as_of,
            commodity_id,
            amount_minor,
            quantity_scaled,
        ),
    )


@pytest.fixture
def empty(git_free_tmp: Path) -> Iterator[tuple[sqlite3.Connection, DataPaths]]:
    """A migrated ledger with nothing in it but the seeded rows."""
    conn, paths = new_ledger(git_free_tmp)
    try:
        yield conn, paths
    finally:
        conn.close()


@pytest.fixture
def simple(empty: tuple[sqlite3.Connection, DataPaths]) -> tuple[sqlite3.Connection, DataPaths]:
    """One account, two transactions, an opening and a closing balance.

    The second transaction lands **on** the closing assertion's ``as_of``. That
    is not decoration: it is the only shape in which the balance-date shift can
    be told apart from getting it wrong.
    """
    conn, paths = empty
    add_account(conn)
    add_balance(conn, as_of="2024-12-31", amount_minor=82_015)
    add_txn(
        conn,
        txn_id="t1",
        day="2025-01-02",
        narration="Zelle Payment From A Name",
        amount_minor=3_711,
    )
    add_txn(
        conn,
        txn_id="t2",
        day="2025-01-31",
        narration="Card Purchase Some Merchant",
        amount_minor=-1244,
    )
    add_balance(conn, as_of="2025-01-31", amount_minor=84_482)
    return conn, paths


# ---------------------------------------------------------------------------
# the licence boundary
# ---------------------------------------------------------------------------


def test_the_exporter_never_imports_beancount() -> None:
    """The one constraint that cannot be argued about after the fact.

    beancount is GPL-2.0-only; ledgerbox is AGPL-3.0-or-later and intends to
    keep a relicensing option open. Reading the format is fine, linking against
    the implementation is not, so the source may not contain the import at all.
    """
    source = Path(beancount_export.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("#", ";"))
    )
    assert not re.search(r"^\s*(import|from)\s+beancount\b", code, re.MULTILINE)
    assert "importlib" not in code, "no dynamic import either"


# ---------------------------------------------------------------------------
# shape of the file
# ---------------------------------------------------------------------------


def test_the_header_says_generated_and_names_the_schema_version(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = simple
    text = render_beancount(conn)
    head = text.splitlines()[0]
    assert head.startswith(";;")
    assert "ledgerbox" in head and "DO NOT EDIT" in head

    # Compared against the version this database is actually at, not against a
    # literal somebody bumps with each migration. The literal was here and it
    # bought nothing the contiguity check in `migrate.discover` does not already
    # buy: an exporter printing a constant would satisfy it, which is the one
    # failure this assertion exists to catch.
    assert f"schema version {schema_version(conn):04d}" in text


def test_the_options_declare_the_title_and_the_operating_currency(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = simple
    lines = render_beancount(conn).splitlines()
    assert 'option "title" "ledgerbox"' in lines
    assert 'option "operating_currency" "USD"' in lines


def test_every_commodity_is_declared_on_a_fixed_early_date(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = simple
    text = render_beancount(conn)
    declared = set(conn.execute("SELECT id FROM commodity"))
    for (code,) in declared:
        assert f"{EPOCH_DATE} commodity {code}" in text
    assert date.today().isoformat() not in text, "today's date would break determinism"


def test_every_account_gets_exactly_one_open_directive(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = simple
    opened = OPEN_LINE_RE.findall(render_beancount(conn))
    names = sorted(name for _day, name, _currency in opened)
    stored = sorted(str(row[0]) for row in conn.execute("SELECT name FROM account").fetchall())
    assert names == stored
    assert len(names) == len(set(names))


def test_an_account_opens_on_the_day_it_is_first_referenced(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """Not "the first transaction": the opening pad and balance come earlier.

    Dating the open at the first *transaction* would put it after the pad that
    seeds the account, and beancount rejects any reference to an account before
    it is open.
    """
    conn, _ = simple
    by_name = {name: day for day, name, _currency in OPEN_LINE_RE.findall(render_beancount(conn))}
    assert by_name[CHECKING_NAME] == "2024-12-31"  # the opening assertion, not 2025-01-02
    assert by_name["Equity:Opening-Balances"] == "2024-12-31"
    assert by_name["Income:Uncategorized"] == "2025-01-02"
    assert by_name["Expenses:Uncategorized"] == "2025-01-31"


def test_an_unreferenced_account_falls_back_to_the_fixed_date(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = empty
    days = {day for day, _name, _currency in OPEN_LINE_RE.findall(render_beancount(conn))}
    assert days == {EPOCH_DATE}


def test_a_transaction_renders_as_a_flag_a_narration_and_its_legs(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = simple
    text = render_beancount(conn)
    assert '2025-01-02 * "Zelle Payment From A Name"' in text
    block = text.split('2025-01-02 * "Zelle Payment From A Name"\n')[1].splitlines()[:2]
    assert [line.split() for line in block] == [
        ["Assets:Chase:Checking:1234", "37.11", "USD"],
        ["Income:Uncategorized", "-37.11", "USD"],
    ]


def test_postings_follow_seq_and_not_the_row_order(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """seq 0 is the bank's own leg. Two legs that balance still carry meaning
    in their order, and inserting them backwards must not change the file."""
    conn, _ = empty
    add_account(conn)
    conn.execute(
        "INSERT INTO txn (id, date, payee, narration, created_at) "
        "VALUES ('t1', '2025-03-01', NULL, 'Backwards', 'x')"
    )
    # Counter leg written first; seq says it is second.
    conn.execute(
        "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
        "VALUES ('t1:1', 't1', 1, ?, -3711, 'USD')",
        (INCOME_ID,),
    )
    conn.execute(
        "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
        "VALUES ('t1:0', 't1', 0, ?, 3711, 'USD')",
        (CHECKING_ID,),
    )
    accounts = [account for account, _amount in POSTING_LINE_RE.findall(render_beancount(conn))]
    assert accounts == [CHECKING_NAME, "Income:Uncategorized"]


def test_a_superseded_transaction_is_left_out_entirely(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = simple
    add_txn(conn, txn_id="t3", day="2025-01-05", narration="Correction target", amount_minor=1_000)
    add_txn(conn, txn_id="t4", day="2025-01-05", narration="Correction", amount_minor=1_000)
    conn.execute("UPDATE txn SET superseded_by = 't4' WHERE id = 't3'")

    text = render_beancount(conn)
    assert "Correction target" not in text
    assert "Correction" in text
    # ...and its legs left with it, or the totals would double-count.
    assert totals_from(text)["posting_count"] == 3


def test_a_missing_narration_still_renders_a_string(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-03-01", narration=None, amount_minor=100)
    assert '2025-03-01 * ""' in render_beancount(conn)


def test_a_payee_when_one_exists_is_written_as_the_first_string(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """P0 never sets ``payee``; P2 will, and the format changes when it does."""
    conn, _ = empty
    add_account(conn)
    add_txn(
        conn,
        txn_id="t1",
        day="2025-03-01",
        narration="raw line",
        payee="Merchant",
        amount_minor=100,
    )
    assert '2025-03-01 * "Merchant" "raw line"' in render_beancount(conn)


def test_an_internal_transfer_is_tagged(empty: tuple[sqlite3.Connection, DataPaths]) -> None:
    """The cashflow aggregations exclude transfers from income and expense.

    An export that dropped the flag could not reproduce the ledger's own
    headline numbers, so it becomes a tag.
    """
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-03-01", narration="moved", amount_minor=100)
    add_txn(conn, txn_id="t2", day="2025-03-02", narration="kept", amount_minor=100)
    conn.execute("UPDATE txn SET is_transfer = 1 WHERE id = 't1'")
    text = render_beancount(conn)
    assert '2025-03-01 * "moved" #transfer' in text
    assert '2025-03-02 * "kept"\n' in text


def test_the_tag_follows_a_person_overruling_the_rules(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """Both directions, because the export reads the *effective* answer.

    Reading ``txn.is_transfer`` directly would tag what the rules found and
    miss what a person marked — and the export would stop reproducing the
    ledger's own figures for exactly the rows a human had looked at. This is
    the third place the concept could have grown a second definition.
    """
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-03-01", narration="ruled", amount_minor=100)
    add_txn(conn, txn_id="t2", day="2025-03-02", narration="marked", amount_minor=100)
    with transaction(conn):
        conn.execute("UPDATE txn SET is_transfer = 1 WHERE id = 't1'")
        conn.execute(
            "INSERT INTO category (id, parent_id, kind) VALUES "
            "('probe-transfer', NULL, 'transfer'), ('probe-dining', NULL, 'expense')"
        )
        conn.execute(
            "INSERT INTO category_override (txn_id, category_id, created_at) VALUES "
            "('t1', 'probe-dining', '2026-08-04'), ('t2', 'probe-transfer', '2026-08-04')"
        )

    text = render_beancount(conn)
    assert '2025-03-01 * "ruled"\n' in text, "a person un-marked it; the tag must go"
    assert '2025-03-02 * "marked" #transfer' in text, "and appear on the one they marked"


def test_the_flag_comes_from_the_row(empty: tuple[sqlite3.Connection, DataPaths]) -> None:
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-03-01", narration="unsure", amount_minor=100, flag="!")
    assert '2025-03-01 ! "unsure"' in render_beancount(conn)


# ---------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------


def test_amounts_are_rendered_from_integers_without_a_float(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """The classic float casualties.

    ``.10``, ``.20`` and ``.29`` are the cent values binary floating point
    cannot hold, and ``2**53 + 1`` is the first integer it cannot hold either —
    it rounds down to ``2**53``. So a single ``/ 100`` anywhere on the path
    renders the last case one cent short, and the final assertion below is what
    notices.

    Expected strings are computed with ``divmod`` rather than written out:
    ``tests/test_repo_hygiene.py`` refuses any 12-digit run in the repository,
    on the grounds that it is usually somebody's account number.
    """
    conn, _ = empty
    add_account(conn)
    huge = 2**53 + 1
    cases = {"a": 10, "b": 20, "c": 29, "d": 1_00, "e": huge, "f": -1}
    for index, (txn_id, minor) in enumerate(sorted(cases.items())):
        add_txn(
            conn,
            txn_id=txn_id,
            day=f"2025-03-{index + 1:02d}",
            narration=txn_id,
            amount_minor=minor,
        )
    text = render_beancount(conn)
    rendered = {
        account_amount[1]
        for account_amount in POSTING_LINE_RE.findall(text)
        if account_amount[0] == CHECKING_NAME
    }

    def as_text(minor: int) -> str:
        whole, frac = divmod(abs(minor), 100)
        return f"{'-' if minor < 0 else ''}{whole}.{frac:02d}"

    assert rendered == {as_text(minor) for minor in cases.values()}
    assert as_text(huge) in rendered
    assert as_text(huge - 1) not in rendered, "a float would have rounded this away"
    assert totals_from(text)["net_minor"] == sum(cases.values())


# ---------------------------------------------------------------------------
# balance directives: the one-day shift
# ---------------------------------------------------------------------------


def test_every_balance_directive_is_dated_one_day_after_the_stored_assertion(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """``as_of`` is a closing balance; beancount's ``balance`` is an opening
    check. They describe the same instant only one day apart."""
    conn, _ = simple
    stored = [
        (str(row[0]), str(row[1]), int(row[2]))
        for row in conn.execute(
            "SELECT account_id, as_of, amount_minor FROM balance_assertion ORDER BY as_of"
        ).fetchall()
    ]
    written = BALANCE_LINE_RE.findall(render_beancount(conn))
    assert len(written) == len(stored)
    for (_account_id, as_of, amount_minor), (day, name, amount, currency) in zip(
        stored, written, strict=True
    ):
        assert day == (date.fromisoformat(as_of) + timedelta(days=1)).isoformat()
        assert name == CHECKING_NAME
        assert to_minor(amount) == amount_minor
        assert currency == "USD"


def test_a_ledger_without_an_opening_entry_falls_back_to_a_pad(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """``sync_opening_entry`` puts the opening balance in the ledger as a real
    transaction. A database written before that existed has only the assertion,
    and beancount would replay it from zero — so the pad states the same fact
    with no posting line, and no income figure moves."""
    conn, _ = simple
    text = render_beancount(conn)
    assert PAD_LINE_RE.findall(text) == [("2024-12-31", CHECKING_NAME, "Equity:Opening-Balances")]
    assert totals_from(text) == {
        "inflow_minor": 3_711,
        "outflow_minor": -1244,
        "net_minor": 3_711 - 1_244,
        "balance_minor": 3_711 - 1_244,
        "posting_count": 2,
    }
    stored = repo.ledger_totals(conn)
    assert totals_from(text)["net_minor"] == stored["net_minor"]


def test_an_opening_entry_in_the_ledger_replaces_the_pad(
    simple: tuple[sqlite3.Connection, DataPaths], bean_check: BeanCheck
) -> None:
    """The normal path once ``sync_opening_entry`` has run.

    The opening entry is a posting dated on the earliest assertion, so
    :func:`_pad_for`'s second condition suppresses the pad on its own — and the
    earliest balance directive becomes a real check of a real transaction
    instead of a tautology.
    """
    conn, paths = simple
    repo.sync_opening_entry(conn, account_id=CHECKING_ID, currency="USD")

    text = render_beancount(conn)
    assert PAD_LINE_RE.findall(text) == []
    assert '2024-12-31 * "Opening balance"' in text
    # Assets and Equity only: an opening entry must not touch income or expense,
    # or the headline numbers would move by the opening balance.
    assert totals_from(text)["inflow_minor"] == 3_711
    assert totals_from(text)["outflow_minor"] == -1244
    assert totals_from(text)["balance_minor"] == (3_711 - 1_244) + 82_015
    assert totals_from(text)["balance_minor"] == repo.ledger_totals(conn)["balance_minor"]
    assert legs_from(text, "Equity:") == [-82_015]

    assert_accepted(bean_check(export_beancount(conn, paths.export)))


def test_a_zero_opening_balance_gets_no_pad(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """beancount rejects a pad it did not have to use ("Unused Pad entry"), and
    an account with no earlier postings already reads zero."""
    conn, _ = empty
    add_account(conn)
    add_balance(conn, as_of="2024-12-31", amount_minor=0)
    add_txn(conn, txn_id="t1", day="2025-01-02", narration="first money", amount_minor=3_711)
    add_balance(conn, as_of="2025-01-31", amount_minor=3_711)
    assert PAD_LINE_RE.findall(render_beancount(conn)) == []


def test_no_pad_is_written_where_postings_already_predate_the_assertion(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """Then the assertion is a real check of booked rows, and padding it would
    absorb whatever discrepancy it was meant to catch."""
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-01-02", narration="before", amount_minor=3_711)
    add_balance(conn, as_of="2025-01-31", amount_minor=3_711)
    assert PAD_LINE_RE.findall(render_beancount(conn)) == []


def test_an_account_with_no_assertions_at_all_needs_no_pad(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-01-02", narration="only", amount_minor=3_711)
    assert PAD_LINE_RE.findall(render_beancount(conn)) == []


# ---------------------------------------------------------------------------
# escaping
# ---------------------------------------------------------------------------

NASTY_NARRATION = 'Card Purchase "QUOTED" \\ Store\\ 1 "" tail'


def test_quotes_and_backslashes_are_escaped(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-03-01", narration=NASTY_NARRATION, amount_minor=100)
    line = TXN_LINE_RE.findall(render_beancount(conn))[0][2]
    assert line == ('"Card Purchase \\"QUOTED\\" \\\\ Store\\\\ 1 \\"\\" tail"')
    # One string, opened and closed exactly once: an unescaped quote would end
    # it early and everything after would be parsed as syntax.
    assert line.startswith('"') and line.endswith('"')
    assert len(re.findall(r'(?<!\\)"', line)) == 2


def test_control_characters_are_escaped_rather_than_dropped(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """A descriptor is the bank's bytes. Reflowing them would make the export
    impossible to diff against ``raw_record``."""
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-03-01", narration="a\nb\tc\rd", amount_minor=100)
    text = render_beancount(conn)
    assert '"a\\nb\\tc\\rd"' in text
    # And the directive stayed on one line.
    assert len(TXN_LINE_RE.findall(text)) == 1


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_rendering_twice_gives_the_same_string(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = simple
    assert render_beancount(conn) == render_beancount(conn)


def test_exporting_twice_gives_the_same_bytes(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, paths = simple
    first = export_beancount(conn, paths.export).read_bytes()
    second = export_beancount(conn, paths.export).read_bytes()
    assert first == second
    assert b"\r\n" not in first, "line endings must not depend on the platform"


def test_two_ledgers_built_in_a_different_order_export_identically(
    git_free_tmp: Path,
) -> None:
    """Insertion order is not content. If it leaked into the file, the export
    would diff against itself after a rebuild from ``archive/``."""
    rows = [
        ("t1", "2025-01-02", "alpha", 3_711),
        ("t2", "2025-01-02", "beta", -1244),
        ("t3", "2025-01-03", "gamma", 1_250),
    ]
    rendered = []
    for index, order in enumerate((rows, list(reversed(rows)))):
        conn, _paths = new_ledger(git_free_tmp / f"order{index}")
        add_account(conn)
        for txn_id, day, narration, minor in order:
            add_txn(conn, txn_id=txn_id, day=day, narration=narration, amount_minor=minor)
        rendered.append(render_beancount(conn))
        conn.close()
    assert rendered[0] == rendered[1]


def test_rendering_needs_no_write_access(simple: tuple[sqlite3.Connection, DataPaths]) -> None:
    conn, paths = simple
    expected = render_beancount(conn)
    conn.close()
    read_only = connect_read_only(paths.db)
    try:
        assert render_beancount(read_only) == expected
    finally:
        read_only.close()


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def test_a_directory_target_gets_the_default_filename(
    simple: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, paths = simple
    written = export_beancount(conn, paths.export)
    assert written == paths.export / EXPORT_FILENAME
    assert written.read_text(encoding="utf-8") == render_beancount(conn)


def test_a_file_target_is_used_verbatim_and_its_parents_created(
    simple: tuple[sqlite3.Connection, DataPaths], git_free_tmp: Path
) -> None:
    conn, _ = simple
    target = git_free_tmp / "nested" / "deeper" / "mine.beancount"
    assert export_beancount(conn, target) == target
    assert target.read_text(encoding="utf-8").startswith(";; Generated by ledgerbox")


def test_an_export_replaces_the_previous_one_whole(
    simple: tuple[sqlite3.Connection, DataPaths], git_free_tmp: Path
) -> None:
    conn, _ = simple
    target = git_free_tmp / "out.beancount"
    target.write_text("stale content that must not survive", encoding="utf-8")
    export_beancount(conn, target)
    assert "stale content" not in target.read_text(encoding="utf-8")
    assert list(target.parent.glob("*.tmp")) == [], "no temp file left behind"


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_a_posting_carrying_a_commodity_quantity_is_refused(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """Shares are not dollars. Rendering only ``amount_minor`` would export a
    stock purchase as a bare cash movement with the position deleted."""
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-03-01", narration="buy", amount_minor=-10_000)
    conn.execute("INSERT INTO commodity (id, kind, scale) VALUES ('VTSAX', 'fund', 8)")
    conn.execute(
        "UPDATE posting SET quantity_scaled = 100000000, commodity_id = 'VTSAX' WHERE id = 't1:0'"
    )
    with pytest.raises(BeancountExportError, match="holdings"):
        render_beancount(conn)


def test_a_posting_that_settles_on_its_own_date_is_refused(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """beancount dates the transaction, not the leg; a differing per-leg date
    would silently move to the transaction's day."""
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-03-01", narration="split settlement", amount_minor=100)
    conn.execute("UPDATE posting SET date = '2025-03-03' WHERE id = 't1:1'")
    with pytest.raises(BeancountExportError, match="per-leg date"):
        render_beancount(conn)


def test_a_posting_date_equal_to_the_transaction_date_is_fine(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = empty
    add_account(conn)
    add_txn(conn, txn_id="t1", day="2025-03-01", narration="same day", amount_minor=100)
    conn.execute("UPDATE posting SET date = '2025-03-01'")
    assert '2025-03-01 * "same day"' in render_beancount(conn)


def test_a_balance_assertion_without_an_amount_is_refused(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = empty
    add_account(conn)
    add_balance(conn, as_of="2024-12-31", amount_minor=None)
    with pytest.raises(BeancountExportError, match="no .*amount"):
        render_beancount(conn)


def test_a_balance_assertion_on_a_quantity_is_refused(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    conn, _ = empty
    add_account(conn)
    conn.execute("INSERT INTO commodity (id, kind, scale) VALUES ('VTSAX', 'fund', 8)")
    add_balance(
        conn, as_of="2024-12-31", amount_minor=None, commodity_id="VTSAX", quantity_scaled=100
    )
    with pytest.raises(BeancountExportError, match="holdings"):
        render_beancount(conn)


def test_a_currency_that_is_not_two_decimal_is_refused(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """``decimal_str`` splits at 100. A three-decimal currency would be rendered
    ten times too small, in a file whose whole purpose is to be believed."""
    conn, _ = empty
    conn.execute("INSERT INTO commodity (id, kind, scale) VALUES ('XYZ', 'currency', 3)")
    add_account(conn, account_id="assets:other", name="Assets:Other", currency="XYZ")
    add_txn(
        conn,
        txn_id="t1",
        day="2025-03-01",
        narration="odd",
        amount_minor=1_000,
        account_id="assets:other",
        currency="XYZ",
    )
    with pytest.raises(BeancountExportError, match="scale 3"):
        render_beancount(conn)


@pytest.mark.parametrize(
    "name",
    [
        "Chase:Checking",  # no known root
        "assets:chase:checking",  # the account id, not the display name
        "Assets",  # a root on its own is not an account
        "Assets:chase:Checking",  # lower-case component
        "Assets:Chase Checking",  # a space ends the account token
        "Assets::Checking",  # empty component
        'Assets:Ch"ase',  # would close the directive early
    ],
)
def test_an_account_name_beancount_cannot_parse_is_refused(
    empty: tuple[sqlite3.Connection, DataPaths], name: str
) -> None:
    """``ensure_account`` never renames an account, so the name is the user's.

    That freedom reaches the export: a rename to something beancount's grammar
    cannot express must fail here, naming the account, rather than downstream in
    a parser message that points at a line number.
    """
    conn, _ = empty
    add_account(conn, account_id="assets:renamed", name=name)
    with pytest.raises(BeancountExportError, match="account name"):
        render_beancount(conn)


def test_a_liability_account_name_is_accepted(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """P1 brings credit cards. Nothing here reads ``account.kind`` — the name
    carries the type, exactly as beancount defines it."""
    conn, _ = empty
    conn.execute(
        "INSERT INTO account (id, name, kind, currency) "
        "VALUES ('liabilities:chase:card:1111', 'Liabilities:Chase:Card:1111', "
        "'liability', 'USD')"
    )
    assert "open Liabilities:Chase:Card:1111 USD" in render_beancount(conn)


def test_opening_balances_in_two_commodities_on_one_day_are_refused(
    empty: tuple[sqlite3.Connection, DataPaths],
) -> None:
    """One ``pad`` cannot open two commodities, and guessing which it means
    would be a coin flip over somebody's money."""
    conn, _ = empty
    conn.execute("INSERT INTO commodity (id, kind, scale) VALUES ('EUR', 'currency', 2)")
    add_account(conn)
    add_balance(conn, as_of="2024-12-31", amount_minor=1_000)
    add_balance(conn, as_of="2024-12-31", amount_minor=2_000, commodity_id="EUR")
    with pytest.raises(BeancountExportError, match="more than one commodity"):
        render_beancount(conn)


# ---------------------------------------------------------------------------
# bean-check, on synthetic ledgers
# ---------------------------------------------------------------------------


def test_bean_check_accepts_a_simple_export(
    simple: tuple[sqlite3.Connection, DataPaths], bean_check: BeanCheck
) -> None:
    conn, paths = simple
    assert_accepted(bean_check(export_beancount(conn, paths.export)))


def test_bean_check_accepts_an_empty_ledger(
    empty: tuple[sqlite3.Connection, DataPaths], bean_check: BeanCheck
) -> None:
    """Zero transactions must still produce a loadable file, not a crash and
    not a stub. The first export of a fresh install is this one."""
    conn, paths = empty
    written = export_beancount(conn, paths.export)
    assert TXN_LINE_RE.findall(written.read_text(encoding="utf-8")) == []
    assert_accepted(bean_check(written))


def test_bean_check_accepts_quotes_and_backslashes(
    empty: tuple[sqlite3.Connection, DataPaths], bean_check: BeanCheck
) -> None:
    conn, paths = empty
    add_account(conn)
    add_balance(conn, as_of="2024-12-31", amount_minor=10_000)
    add_txn(conn, txn_id="t1", day="2025-01-05", narration=NASTY_NARRATION, amount_minor=-2_500)
    add_txn(
        conn, txn_id="t2", day="2025-01-06", narration="ends with a backslash \\", amount_minor=500
    )
    add_txn(conn, txn_id="t3", day="2025-01-07", narration='"', amount_minor=1)
    add_balance(conn, as_of="2025-01-31", amount_minor=8_001)
    assert_accepted(bean_check(export_beancount(conn, paths.export)))


def test_bean_check_accepts_two_accounts_with_their_own_opening_balances(
    empty: tuple[sqlite3.Connection, DataPaths], bean_check: BeanCheck
) -> None:
    """P0 has one account; the pad and open logic is per-account regardless.

    Second account starts later and at a different balance, so a pad or an open
    date leaking across accounts shows up as a failed balance rather than as a
    file that happens to still load.
    """
    conn, paths = empty
    add_account(conn)
    conn.execute(
        "INSERT INTO account (id, name, kind, subtype, currency) "
        "VALUES ('assets:chase:savings:9911', 'Assets:Chase:Savings:9911', "
        "'asset', 'savings', 'USD')"
    )
    savings = "assets:chase:savings:9911"

    add_balance(conn, as_of="2024-12-31", amount_minor=82_015)
    add_txn(conn, txn_id="c1", day="2025-01-31", narration="checking out", amount_minor=-1244)
    add_balance(conn, as_of="2025-01-31", amount_minor=82_015 - 1_244)

    add_balance(conn, as_of="2025-03-31", amount_minor=1_000_00, account_id=savings)
    add_txn(
        conn,
        txn_id="s1",
        day="2025-04-30",
        narration="savings in",
        amount_minor=2_500,
        account_id=savings,
    )
    add_balance(conn, as_of="2025-04-30", amount_minor=1_025_00, account_id=savings)

    text = render_beancount(conn)
    assert sorted(PAD_LINE_RE.findall(text)) == [
        ("2024-12-31", CHECKING_NAME, "Equity:Opening-Balances"),
        ("2025-03-31", "Assets:Chase:Savings:9911", "Equity:Opening-Balances"),
    ]
    opened = {name: day for day, name, _currency in OPEN_LINE_RE.findall(text)}
    assert opened[CHECKING_NAME] == "2024-12-31"
    assert opened["Assets:Chase:Savings:9911"] == "2025-03-31"
    assert opened["Equity:Opening-Balances"] == "2024-12-31"  # the earlier of the two pads

    assert_accepted(bean_check(export_beancount(conn, paths.export)))


def test_bean_check_rejects_the_export_if_the_balance_shift_is_undone(
    simple: tuple[sqlite3.Connection, DataPaths], bean_check: BeanCheck, git_free_tmp: Path
) -> None:
    """The proof that the one-day shift is load-bearing rather than cosmetic.

    Move the closing ``balance`` back onto the stored ``as_of`` — the naive
    reading — and beancount evaluates it before that day's transaction, which is
    exactly the transaction the closing balance is supposed to include.
    """
    conn, paths = simple
    good = export_beancount(conn, paths.export)
    assert_accepted(bean_check(good))

    text = good.read_text(encoding="utf-8")
    matches = list(BALANCE_LINE_RE.finditer(text))
    last = matches[-1]
    shifted = (date.fromisoformat(last.group(1)) - timedelta(days=1)).isoformat()
    naive = git_free_tmp / "naive.beancount"
    naive.write_text(text[: last.start()] + shifted + text[last.start() + 10 :], encoding="utf-8")

    result = bean_check(naive)
    assert result.returncode != 0
    assert "Balance failed" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# the real corpus
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_export(
    git_free_tmp_root: Path, real_statements: list[Path], real_parsed: list
) -> Iterator[tuple[sqlite3.Connection, Path, list]]:
    """Thirteen real statements, ingested once and exported once.

    Module-scoped, so ``git_free_tmp`` (function-scoped) cannot be used; its
    session-scoped parent ``git_free_tmp_root`` gives the same guarantee — a
    writable directory with no ``.git`` above it — which is what the data-dir
    guard actually checks.
    """
    root = git_free_tmp_root / "beancount-export-real"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    conn, paths = new_ledger(root)
    try:
        outcomes = pipeline.ingest_paths(conn, paths, real_statements)
        assert [o.status for o in outcomes] == [pipeline.IMPORTED] * len(real_statements)
        yield conn, export_beancount(conn, paths.export), real_parsed
    finally:
        conn.close()
        shutil.rmtree(root, ignore_errors=True)


def test_bean_check_accepts_the_real_corpus(
    real_export: tuple[sqlite3.Connection, Path, list], bean_check: BeanCheck
) -> None:
    _conn, written, _parsed = real_export
    assert_accepted(bean_check(written))


def test_the_real_export_re_adds_to_the_acceptance_numbers(
    real_export: tuple[sqlite3.Connection, Path, list],
    real_expected: dict[str, int],
) -> None:
    """P0 acceptance item: the corpus's own in, out and net figures.

    Read back out of the *file*, by a regex written in this module, with no
    beancount and no ledgerbox query involved. If the export and the database
    ever disagree, this is where it shows.
    """
    conn, written, _parsed = real_export
    totals = totals_from(written.read_text(encoding="utf-8"))
    assert totals["inflow_minor"] == real_expected["deposits_minor"]
    assert totals["outflow_minor"] == real_expected["withdrawals_minor"]
    assert totals["net_minor"] == real_expected["net_minor"]
    assert totals["inflow_minor"] + totals["outflow_minor"] == totals["net_minor"]

    # The reconstruction above ignores `is_transfer`, which `ledger_totals`
    # filters on. P0 marked nothing as a transfer and this block once asserted
    # that; P2's conservative transfer rules now do mark lines on the corpus,
    # so the honest comparison is the whole against the filtered figures plus
    # exactly what the filter says it removed. This is also the first assertion
    # anywhere that ties `transfer_excluded_*` to an independently derived sum.
    stored = repo.ledger_totals(conn)
    assert stored["transfer_count"] > 0, "the P2 transfer rules do fire on this corpus"
    assert totals["inflow_minor"] == (
        stored["inflow_minor"] + stored["transfer_excluded_in_minor"]
    )
    assert totals["outflow_minor"] == (
        stored["outflow_minor"] + stored["transfer_excluded_out_minor"]
    )
    assert totals["net_minor"] == (
        stored["net_minor"]
        + stored["transfer_excluded_in_minor"]
        + stored["transfer_excluded_out_minor"]
    )


def test_the_real_export_adds_up_to_the_balance_the_bank_printed(
    real_export: tuple[sqlite3.Connection, Path, list],
    real_expected: dict[str, int],
) -> None:
    """The whole point of the opening entry, checked from the text.

    Sum every asset leg in the file — opening entry included — and the answer
    must be the closing balance of the newest statement. Without the opening
    entry this is the *net change* across every statement instead, which is the
    shape of error that looks entirely plausible for a year.
    """
    conn, written, _parsed = real_export
    text = written.read_text(encoding="utf-8")
    balance = totals_from(text)["balance_minor"]
    assert balance == repo.ledger_totals(conn)["balance_minor"]
    assert balance == to_minor(BALANCE_LINE_RE.findall(text)[-1][2])
    assert balance != real_expected["net_minor"], "summing assets must not be the net change"


def test_every_real_transaction_and_posting_reaches_the_file(
    real_export: tuple[sqlite3.Connection, Path, list],
) -> None:
    """415 statement rows and their 830 legs, plus the one opening entry.

    The opening entry is told apart structurally — it is the only transaction
    with an equity leg — rather than by its narration, which is a string a later
    feature could reuse.
    """
    conn, written, parsed = real_export
    blocks = transactions_from(written.read_text(encoding="utf-8"))
    opening = [b for b in blocks if any(a.startswith("Equity:") for a, _m in b[2])]
    statement_rows = [b for b in blocks if b not in opening]

    assert len(statement_rows) == REAL_STATEMENT_TXNS
    assert sum(len(b[2]) for b in statement_rows) == REAL_STATEMENT_POSTINGS
    assert len(opening) == 1 and len(opening[0][2]) == 2

    # The same counts derived from the parsed statements and from SQLite, so a
    # shortfall cannot hide behind one of the three agreeing with itself.
    assert sum(len(statement.transactions) for statement in parsed) == REAL_STATEMENT_TXNS
    assert conn.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == REAL_STATEMENT_TXNS + 1
    assert conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0] == REAL_STATEMENT_POSTINGS + 2
    assert len(blocks) == len(TXN_LINE_RE.findall(written.read_text(encoding="utf-8")))


def test_every_real_balance_assertion_reaches_the_file(
    real_export: tuple[sqlite3.Connection, Path, list],
) -> None:
    conn, written, _parsed = real_export
    stored = [
        (str(row[0]), int(row[1]))
        for row in conn.execute(
            "SELECT as_of, amount_minor FROM balance_assertion ORDER BY as_of"
        ).fetchall()
    ]
    written_rows = [
        (day, to_minor(amount))
        for day, _name, amount, _cur in BALANCE_LINE_RE.findall(written.read_text(encoding="utf-8"))
    ]
    assert len(written_rows) == len(stored)
    assert written_rows == [
        ((date.fromisoformat(as_of) + timedelta(days=1)).isoformat(), minor)
        for as_of, minor in stored
    ]


def test_the_real_export_is_byte_identical_on_a_second_run(
    real_export: tuple[sqlite3.Connection, Path, list],
) -> None:
    conn, written, _parsed = real_export
    before = written.read_bytes()
    assert export_beancount(conn, written).read_bytes() == before
