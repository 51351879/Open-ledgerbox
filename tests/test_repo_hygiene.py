# SPDX-License-Identifier: AGPL-3.0-or-later
"""The repository must not contain real financial data. Ever.

This exists because it already happened. While building the parser, a real
15-digit account number and a real 20-digit statement barcode were copied out
of a debugging session and into two test files, where they sat looking like
ordinary fixture values. Counterparty names had been carefully replaced with
"Vendor" and "Someone"; the account number had not, because nobody was looking
for a number.

The repository has no commits yet, so removing them cost nothing. After a push
it would have cost everything -- which is the whole argument in
docs/PROJECT_SUMMARY.md §4 for starting a fresh repository rather than
rewriting history: keys can be rotated, an account number cannot.

The checks here are deliberately **shape-based**. A blacklist of the real
values would have to contain the real values.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from tools.check_repo_data import (
    ALLOWED_PREFIXES,
    is_data_file,
    offenders,
    tracked_files,
    unmeant_names,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
    "htmlcov",
}

#: Agent worktrees: a *second checkout of this same repository*, sitting inside
#: it. Its contents are whichever commit it is on, and this whole file is checked
#: out inside it and runs against it there. Scanning it from here reports one
#: finding once per live worktree, at a path where the fix does not live.
#:
#: Matched as a **path prefix**, not as a directory name. The first version put
#: `"worktrees"` in :data:`SKIP_DIRS`, which is compared against every component
#: of every path -- so `docs/worktrees/`, `src/ledgerbox/worktrees/` and a
#: top-level `worktrees/` were all exempt from every filesystem scan in this
#: file. An exemption argued for one directory has to be written as that one
#: directory.
SKIP_PREFIXES = (".claude/worktrees/",)

#: Everything is scanned except known binaries. An allowlist of text extensions
#: was the wrong shape: the predecessor's leak lived in `financial_dashboard.html`
#: and `dashboard_data.js`, neither of which would have been on it.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".gz",
    ".whl", ".so", ".pyd", ".dll", ".exe", ".db", ".sqlite", ".sqlite3", ".woff",
    ".woff2", ".ttf", ".otf",
}

#: What counts as a financial data file, imported rather than restated.
#:
#: There were two lists. They disagreed -- this one had `.ndjson`, the gate's did
#: not -- so the same question got two answers depending on which half of the
#: check you happened to hit. That is the shape of defect `docs/STATUS.md` §5.29
#: is about: the archive once had two definitions of "what is a shard", and the
#: difference was load-bearing.
FIXTURE_ROOTS = ALLOWED_PREFIXES

#: Eight, not twelve. Twelve caught a 15-digit account number and let an
#: 11-digit Zelle reference through -- a real one, copied verbatim out of a
#: debugging session into a row labelled "synthetic". The threshold has to sit
#: below the shortest identifier a statement actually prints, not below the
#: longest one somebody happened to leak.
LONG_DIGIT_RUN = re.compile(r"\d{8,}")

#: A digit run inside a full 64-hex-character token is part of a sha256 content
#: hash, not an account number: statements print identifiers as digits, never
#: embedded in letters-and-digits hex of exactly this length. The Skill install
#: catalogue pins every past official bundle by such hashes and grows with each
#: release, so without this the guard would demand a fresh exemption argument
#: for provably-public checksums every time. The boundary `(?![0-9a-f])` also
#: refuses 65-plus-character runs instead of exempting their first 64.
SHA256_HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def _digit_runs_outside_hashes(line: str) -> list[str]:
    return LONG_DIGIT_RUN.findall(SHA256_HEX.sub("", line))

#: Values proved fictional, each with the reason it is safe. Adding anything
#: here is an assertion that the value never appeared on a real statement, so
#: add only obviously-invented digits -- never something copied from a
#: debugging session, which is exactly how both leaks so far got in.
ALLOWED_SYNTHETIC = {
    "000000000001234",  # tests/synth.py: leading zeros, mask 1234
    "12345678901234567890",  # tests/test_parse_chase.py: sequential barcode
    "10000000001",  # tests/synth.py: invented Zelle-style reference
    "0123456789",  # a hex/digit charset literal, not an identifier
    "99999999999",  # tests/test_money.py: the largest amount the parser accepts
    "100000000",  # tests/test_beancount_export.py: a round synthetic amount
    "20250131",  # README: an example statement filename
    "20260801",  # tests/test_archive.py: an example ingest date
    "51351879",  # the GitHub account this repository lives under; public in every
    #             clone URL by definition, and never printed on any statement
}


def _candidate_files() -> list[Path]:
    found: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if _relative(path).startswith(SKIP_PREFIXES):
            continue
        if path.suffix.lower() not in BINARY_SUFFIXES:
            found.append(path)
    return found


#: Lines of this file that legitimately contain the allowlisted values. Skipping
#: the whole file was worse than it looks: it is the one file a developer edits
#: while debugging the guard, which makes it the likeliest place to paste a real
#: value and never hear about it.
#: The literal assignment, not the bare name: the name also appears in this
#: module's docstring and in the comparison inside the scan, and matching those
#: switched the exemption on for the rest of the file. A value pasted at the
#: bottom then went unseen -- the guard exempting itself, which a mutation test
#: caught and a reading would not have.
_ALLOWLIST_MARKER = "ALLOWED_SYNTHETIC = {"


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_the_security_policy_ships_and_names_no_mailbox() -> None:
    """SECURITY.md must exist for GitHub's reporting flow to point anywhere,
    and it must not contain an e-mail address: the reporting channel is the
    platform's private-advisory form, and no personal mailbox belongs in a
    public repository this project went to lengths to keep identity-free.
    """
    policy = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "vulnerability" in policy.lower()
    assert "THREAT_MODEL" in policy
    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", policy) is None, (
        "the security policy must route through the platform, not a mailbox"
    )


def test_the_changelog_leads_with_the_packaged_version() -> None:
    """One version, stated twice, compared: pyproject is the source of truth
    and the changelog's newest entry must be about that version -- the release
    gate in docs/RELEASE_PLAN.md §2 depends on this equality staying true.
    """
    import tomllib

    packaged = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first_heading = next(
        line for line in changelog.splitlines() if line.startswith("## ")
    )
    assert packaged in first_heading, (
        f"the changelog's newest entry {first_heading!r} is not about the "
        f"packaged version {packaged!r}"
    )


def test_the_hash_exemption_hides_hashes_and_nothing_else() -> None:
    """The check's own counterexamples: discipline rule 7 applies to guards too."""
    hash_line = (
        '    "SKILL.md": "e07e069879343f672d7b1ffeca140f14264f3e1cfb987ae7e3ec080b5cc07b4f",'
    )
    assert _digit_runs_outside_hashes(hash_line) == []
    assert _digit_runs_outside_hashes("account 4412345678") == ["4412345678"]
    beside = f"{'a1' * 32} then 4412345678"
    assert _digit_runs_outside_hashes(beside) == ["4412345678"], (
        "a hash on the line must not launder the digits sitting next to it"
    )
    too_long = "b" + "12345678" + "c" * 56
    assert len(too_long) == 65
    assert _digit_runs_outside_hashes(too_long) == ["12345678"], (
        "65 hexish characters are not a sha256; nothing in them is exempt"
    )


def test_no_long_digit_runs_outside_the_synthetic_allowlist() -> None:
    offenders: list[str] = []
    this_file = _relative(Path(__file__).resolve())
    for path in _candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        in_allowlist = False
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _relative(path) == this_file:
                # Only the allowlist block is exempt, not the whole file.
                if _ALLOWLIST_MARKER in line:
                    in_allowlist = True
                elif in_allowlist and line.startswith("}"):
                    in_allowlist = False
                if in_allowlist:
                    continue
            for run in _digit_runs_outside_hashes(line):
                if run not in ALLOWED_SYNTHETIC:
                    offenders.append(f"{_relative(path)}:{line_number} ({len(run)} digits)")
    assert offenders == [], (
        "long digit runs found; if these are account, card or barcode numbers from a real "
        f"statement they must be replaced, not committed: {offenders}"
    )


#: How often a short digit run has to recur before it counts as an identifier.
#:
#: Most four-digit numbers in a statement are incidental -- a store number, part
#: of a street address, an order reference that appears once. Blacklisting all of
#: them would flag every ``1000`` in the test suite. But a run that shows up on
#: row after row is not incidental: it is something printed on every line, and on
#: a card statement that is the card.
#:
#: Five is not a guess. Measured over the 415 real transactions: 237 distinct
#: runs of 4-7 digits, of which only 11 recur five times or more, none of them
#: resembling a year. The real card tail appears on 204 of them. The gap between
#: "recurs a handful of times" and "appears once" is wide enough that the
#: threshold can sit anywhere in it, and low enough to catch an identifier that
#: only appears on a few rows.
RECURRING_RUN_THRESHOLD = 5


#: Above this many rows, a run is an identifier whatever it looks like.
#:
#: The year exemption below exists so that ``2025`` in a docstring does not fail
#: the build. Left unconditional it would exempt a *card tail* that happened to
#: fall in 1990-2100 -- roughly one four-digit value in eighty -- and the class of
#: value it would wave through is the one that has already leaked three times.
#: A year appears in a description incidentally and rarely; measured on this
#: corpus, no year-like run recurs more than twice. Something printed on twenty
#: separate rows is not incidental, and at that point what it resembles stops
#: mattering.
IDENTIFIER_HIGH_WATER = 20


def _looks_like_a_year(run: str, *, seen: int) -> bool:
    """Years recur constantly and are in every changelog, docstring and test.

    Not an unconditional pass: see :data:`IDENTIFIER_HIGH_WATER`.
    """
    if seen >= IDENTIFIER_HIGH_WATER:
        return False
    return len(run) == 4 and 1990 <= int(run) <= 2100


def _identifying_values(real_parsed: list) -> set[str]:
    """Everything in the corpus that could identify the person or the account.

    Three sources, and the third is the one that was missing:

    * the account mask;
    * every run of eight digits or more in a description -- reference numbers,
      phone numbers, barcodes;
    * every **short** run that recurs across transactions, which is how a card
      tail is caught. See :data:`RECURRING_RUN_THRESHOLD`.

    The returned set is never written anywhere. It is derived from the operator's
    own files at test time and lives only in memory, because a blacklist of real
    values stored in the repository would be the leak it exists to prevent.
    """
    secrets: set[str] = set()
    short_runs: Counter[str] = Counter()

    for statement in real_parsed:
        if statement.account_mask:
            secrets.add(statement.account_mask)
        for txn in statement.transactions:
            secrets.update(re.findall(r"\d{8,}", txn.description))
            # set() per transaction: a value repeated twice on one row is still
            # one row, and what matters is how many rows carry it.
            for run in set(re.findall(r"\d{4,7}", txn.description)):
                short_runs[run] += 1

    secrets.update(
        run
        for run, seen in short_runs.items()
        if seen >= RECURRING_RUN_THRESHOLD and not _looks_like_a_year(run, seen=seen)
    )
    return secrets


def test_nothing_from_the_real_statements_appears_in_the_repository(
    real_parsed: list,
) -> None:
    """The check the shape rules cannot do, done from the data itself.

    A four-digit card fragment is invisible to a digit-run threshold: the real
    one looks exactly like an invented one. That is precisely the value that has
    now leaked three separate times. The only way to catch it is to ask the real
    statements what their identifying values are, compare, and never write the
    answer down.

    This docstring originally named the real fragment as an example. The check
    caught it on its first run -- in the file whose entire job is to stop that
    from happening. A guard that only its author reads is not a guard.

    **The fifth leak got through this check**, and the reason is recorded in
    :data:`RECURRING_RUN_THRESHOLD`. The earlier version collected only the
    account mask and runs of eight digits or more; a card tail is four digits
    and is not the account mask, so it fell straight between them. An independent
    reviewer found it in a paragraph of ``docs/STATUS.md`` that was explaining a
    *different* bug -- the fourth leak had been in a paragraph explaining how to
    prevent leaks. Prose about the real data is where this keeps happening,
    because that is where quoting a real value is most natural.

    Skips without ``LEDGERBOX_REAL_FIXTURES``, so CI never needs real data and
    the values never enter the repository even as a blacklist.
    """
    secrets = _identifying_values(real_parsed)
    assert secrets, "the corpus should yield identifying values to check against"

    offenders: list[str] = []
    for path in _candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for run in re.findall(r"\d{4,}", line):
                if run in secrets:
                    # Deliberately does not print the value.
                    offenders.append(f"{_relative(path)}:{line_number} ({len(run)} digits)")
    assert offenders == [], (
        f"{len(offenders)} literal(s) in the repository also appear in the real statements; "
        f"replace them with invented values: {offenders}"
    )


def _git(*args: str) -> str | None:
    """Run one git command in :data:`REPO_ROOT`. ``None`` when git cannot answer.

    ``OSError`` is caught, not only a non-zero exit. git missing from ``PATH``
    **raises** rather than returning a code, and this file has already paid for
    that once: the skip branch in
    :func:`test_a_real_span_dump_would_be_ignored_by_git` was unreachable for
    the same reason. Writing the lesson down twice and implementing it once is
    how ``docs/STATUS.md`` §5.22 describes three of its own defects.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _git_answers_for_this_checkout() -> bool:
    """Is the repository git is talking about actually this one?

    ``git -C <dir>`` walks **up**. A source tree with no ``.git`` of its own,
    unpacked inside some other repository, makes every command above report that
    other project's history — and a leak check would then scan a stranger's
    commits and pass without ever looking at ledgerbox's own. That is not
    hypothetical on this machine: ``C:\\Users\\admin\\.git`` exists, which is
    why the data directory guard exists at all, and an acceptance run
    demonstrated the guard going green while reading an unrelated repository.
    """
    top = _git("rev-parse", "--show-toplevel")
    if top is None or not top.strip():
        return False
    return Path(top.strip()).resolve() == REPO_ROOT.resolve()


def _git_message_texts() -> list[tuple[str, str]]:
    """Every message git will hand over, as ``(label, text)``.

    Commit messages reachable from any ref **or from the reflog**, and annotated
    tag messages. The reflog matters because ``--amend`` leaves the superseded
    commit in the object database, and a tag message is pushed and permanent
    just as a commit message is; verification found both invisible to the first
    version of this.

    The last entry from each source is the **raw stream**, unparsed. A record
    that does not split cleanly — a message containing the ``\\x1e`` separator,
    which verification planted — used to be skipped, and a leak guard that drops
    what it cannot parse fails open. Scanning the raw text as well means no byte
    reaches this function and leaves unread; the cost is that an offender found
    only there is reported without a position.
    """
    texts: list[tuple[str, str]] = []
    for source, label, args in (
        ("commit", "commit", ("log", "--format=%h%x00%B%x1e", "--all", "--reflog")),
        ("tag", "tag", ("for-each-ref", "--format=%(refname:short)%x00%(contents)%x1e",
                        "refs/tags")),
    ):
        raw = _git(*args) or ""
        for record in raw.split("\x1e"):
            name, separator, body = record.strip().partition("\x00")
            if separator:
                texts.append((f"{label} {name.strip()}", body))
        texts.append((f"the raw git {source} stream", raw))
    return texts


def test_nothing_from_the_real_statements_appears_in_a_commit_message(
    real_parsed: list,
) -> None:
    """The same blacklist, asked of the one surface nothing had ever scanned.

    Every other check in this file walks ``REPO_ROOT.rglob`` — the working tree.
    A commit message is not in the working tree and is every bit as permanent,
    so for the whole life of this project it has been an unwatched way in. The
    eighth leak arrived through it (``docs/STATUS.md`` §6.5): a real transaction
    amount copied out of a terminal into three commit messages and, from there,
    into the prose.

    **This check would not have caught that one**, and saying so is the point.
    The blacklist is built from ``txn.description``, account masks and recurring
    short digit runs; an *amount* is in none of those, and the reason it is not
    is argued in §6.5 — a single amount cannot be blacklisted without
    blacklisting arithmetic. What this closes is the surface, not that hole.

    Two things it still does not see, said here rather than left to be assumed:
    the body of a ``git note``, which lives in a tree rather than in any
    message; and a value split across a line break, which every scan in this
    file shares because they all read line by line.

    A failure here is worse than a failure in the working tree, because the fix
    is not an edit. The message says so.
    """
    secrets = _identifying_values(real_parsed)
    assert secrets, "the corpus should yield identifying values to check against"

    if not _git_answers_for_this_checkout():
        pytest.skip(
            "git cannot answer for this checkout — absent, or this tree has no history of "
            "its own and git resolved to an enclosing repository. Scanning that would be "
            "scanning somebody else's commits and reporting a pass."
        )

    texts = _git_message_texts()
    attributed = [(label, body) for label, body in texts if not label.startswith("the raw ")]
    assert attributed, "git answered for this checkout but produced no messages at all"

    offenders: list[str] = []
    for label, body in attributed:
        for line_number, line in enumerate(body.splitlines(), start=1):
            for run in re.findall(r"\d{4,}", line):
                if run in secrets:
                    # Deliberately does not print the value.
                    offenders.append(f"{label}:{line_number} ({len(run)} digits)")

    if not offenders:
        # The backstop: anything the record split dropped is still in here.
        for label, body in texts:
            if not label.startswith("the raw "):
                continue
            for run in re.findall(r"\d{4,}", body):
                if run in secrets:
                    offenders.append(f"{label} (no position, {len(run)} digits)")

    assert offenders == [], (
        f"{len(offenders)} value(s) in git messages also appear in the real statements. "
        f"A message cannot be edited in place: this needs the history decision in "
        f"docs/STATUS.md §6.5, not a patch. {offenders}"
    )


#: How money is written **for people**: ``48.00``, ``1,130.16``.
_MONEY_DECIMAL = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})*|\d+)\.(\d{2})(?![\d])")

#: How money is written **for the machine**, which is how this codebase writes
#: it nearly everywhere: integer minor units. The first version of this scan had
#: only the pattern above, so a keyword argument carrying an amount beside a
#: balance produced no tokens at all — and that is the form seven of the eight
#: surviving leaks were written in.
_MONEY_MINOR = re.compile(r"(?<![\d.\w])(\d{3,9})(?![\d.\w])")

#: How far apart two figures may sit and still be read as a pair. One line was
#: the first version's window, and Python's ordinary formatting defeated it: a
#: builder call puts the amount on one line and the balance on the next. Three
#: lines covers that and covers a third value written between them.
_PAIR_WINDOW_LINES = 3


def _money_tokens(text: str) -> list[tuple[int, int]]:
    """``(line number, minor units)`` for every money-shaped token in *text*."""
    found: list[tuple[int, int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in _MONEY_DECIMAL.finditer(line):
            whole = int(match.group(1).replace(",", ""))
            found.append((line_number, whole * 100 + int(match.group(2))))
        for match in _MONEY_MINOR.finditer(line):
            found.append((line_number, int(match.group(1))))
    return found


def _real_money_pairs(real_parsed: list) -> set[tuple[int, int]]:
    """Ordered pairs of figures that only a real statement puts next to each other.

    **Pairs, not values.** A single real amount is not evidence of anything:
    ``$5.00`` and ``$10.00`` are in the corpus and also in half the tests, and a
    blacklist of every real amount would fail the build on arithmetic. What does
    not happen by chance is one figure sitting beside another the statement
    itself put beside it:

    * an amount and the balance it produced;
    * two balances in the order they were printed;
    * the statement's opening or closing balance beside either — given a
      published opening and one real balance, the transaction between them is
      subtraction, which is how one of these leaks was reconstructable;
    * a printed subtotal beside either end of the statement it belongs to.

    Pairs of two equal values are dropped. The last transaction's balance *is*
    the closing balance, so ``(x, x)`` would otherwise be in the set and would
    flag every ``MINOR = 28871  # $288.71`` annotation — a published figure
    written twice in two notations.

    Derived from the operator's files at test time and never written down, for
    the same reason as :func:`_identifying_values`.
    """
    pairs: set[tuple[int, int]] = set()
    for statement in real_parsed:
        transactions = statement.transactions
        summary = statement.summary
        opening = summary.beginning_balance_minor
        closing = summary.ending_balance_minor
        pairs.add((opening, closing))
        for index, txn in enumerate(transactions):
            if txn.balance_minor is None:
                continue
            pairs.add((abs(txn.amount_minor), txn.balance_minor))
            pairs.add((opening, txn.balance_minor))
            pairs.add((txn.balance_minor, closing))
            previous = transactions[index - 1] if index else None
            if previous is not None and previous.balance_minor is not None:
                pairs.add((previous.balance_minor, txn.balance_minor))
        for value in summary.components.values():
            pairs.add((abs(value), abs(closing)))
            pairs.add((abs(opening), abs(value)))
    return {(first, second) for first, second in pairs if first != second}


def test_no_real_amount_sits_next_to_its_real_balance(real_parsed: list) -> None:
    """The seventh leak, and the one both existing layers were built to miss.

    ``tests/synth.py``'s default statement carried a real amount and the real
    balance it produced, on the real date, from the first commit — the operator's
    first January transaction. The synthetic statement's whole balance chain was
    anchored on that row, so several other files inherited the same figures.
    Found by an acceptance run comparing the repository against the corpus by
    hand.

    **The values are not quoted here**, and that sentence is the point: the first
    draft of this docstring named them, and this check went red on its own file
    on its first run. That is the fourth time in this project's history that a
    paragraph explaining a leak has contained one — ``docs/STATUS.md`` §6.5
    counts the others — and the second time a guard has caught its own author.

    Neither older layer could have seen it. The shape layer looks for runs of
    eight digits or more, and a balance under ten thousand dollars is five. The
    data layer takes its blacklist from ``txn.description`` only — **amounts and
    balances were never in the set it compares against**, so it was structurally
    incapable of catching this however the threshold was tuned.

    **Nor could the first version of this check**, which is the part worth
    reading. It matched only ``123.45``-shaped literals on a single line, and:

    * this codebase writes money as **integer minor units** almost everywhere,
      so a pair written that way produced no tokens at all;
    * ``StatementBuilder(...)`` calls put the amount on one line and the balance
      on the next, which one-line matching cannot pair.

    Seven of the eight surviving pairs were in one of those two forms. The check
    ran green over them and a commit message said the whole chain had been
    re-anchored. It had not. So this now reads minor units too and pairs across
    a window of lines — and the acceptance round that found them did it by
    writing its own scan rather than trusting this one, which is the only reason
    it worked.

    ``docs/STATUS.md`` §6.5 summarises the pattern as "counterparty names were
    replaced every time and numbers never were, because nobody was looking for
    numbers". The guards were then built to look for numbers — in descriptions,
    with a decimal point, on one line. Each round of that sentence has been true
    and narrower than somebody hoped.

    Skips without ``LEDGERBOX_REAL_FIXTURES``.
    """
    pairs = _real_money_pairs(real_parsed)
    assert pairs, "the corpus should yield money pairs to check against"

    offenders: list[str] = []
    for path in _candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        tokens = _money_tokens(text)
        for index, (line, value) in enumerate(tokens):
            for other_line, other_value in tokens[index + 1 :]:
                if other_line - line > _PAIR_WINDOW_LINES:
                    break
                if (value, other_value) in pairs:
                    # Deliberately prints neither value.
                    offenders.append(f"{_relative(path)}:{line}")
    assert offenders == [], (
        f"{len(offenders)} place(s) carry two figures a real statement put beside each "
        f"other — an amount and the balance it produced, two balances in printed order, "
        f"or either against a printed total. Re-anchor the whole chain onto invented "
        f"figures: changing one of them leaves the rest reconstructable, which is the "
        f"mistake this check exists to have caught: {sorted(set(offenders))}"
    )


def test_the_index_gate_agrees_the_repository_is_clean() -> None:
    """The positive case, run against the real index."""
    assert offenders(tracked_files()) == []


@pytest.mark.parametrize(
    "path",
    [
        "statements/january.pdf",
        "export/ledger.beancount",
        "data/ledger.db",
        "notes/Transactions.CSV",  # extension case is not evidence of anything
        "tests/fixtures/spans/chase.pdf",  # that directory holds text layers
        "tests/fixtures/real/statement.pdf",  # "fixtures" alone is not a licence
        # The extraction cache: the *whole* text layer, which is to say the
        # account number, the legal name, the street address and every
        # counterparty. The first version of this gate had no `.ndjson`.
        "data/extracted/abc123.ndjson",
        # What people produce immediately before changing something. `.suffix`
        # sees only `.bak`, so both of these walked straight through.
        "backup/ledger.db.bak",
        "old/statement.pdf.bak",
        "archive/january.csv.old",
        # A dotfile whose entire name is the extension: no suffixes at all.
        ".beancount",
    ],
)
def test_the_index_gate_rejects_a_tracked_data_file(path: str) -> None:
    """The negative case. A gate nobody has watched fail has not been tested.

    ``.gitignore`` does not apply to files that are already tracked, so a
    ``git add -f`` — or a file added before a rule existed — stays in the index
    with the ignore file saying nothing about it. This is the half that asks the
    index instead.
    """
    assert offenders([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "tests/fixtures/synthetic/one-month.pdf",
        "tests/fixtures/malformed/truncated.pdf",
        "src/ledgerbox/web/js/main.js",
        "src/ledgerbox/db/migrations/0004_views.sql",
        "docs/STATUS.md",
        "2025.08.release-notes.md",  # dotted names that are not data
    ],
)
def test_the_index_gate_allows_generated_fixtures_and_source(path: str) -> None:
    assert offenders([path]) == []


def test_only_the_agent_worktree_is_exempt_from_the_filesystem_scans() -> None:
    """The exemption is one directory, not every directory called ``worktrees``.

    Written as an assertion because the first version was a bare name in
    :data:`SKIP_DIRS`, compared against every component of every path — so
    ``docs/worktrees/`` and a top-level ``worktrees/`` were exempt from every
    scan in this file, silently. The comment argued for one directory and the
    code implemented a wildcard.
    """
    exempt = REPO_ROOT / ".claude" / "worktrees" / "somewhere" / "leak.py"
    assert _relative(exempt).startswith(SKIP_PREFIXES)

    for elsewhere in (
        REPO_ROOT / "worktrees" / "leak.py",
        REPO_ROOT / "docs" / "worktrees" / "leak.py",
        REPO_ROOT / "src" / "ledgerbox" / "worktrees" / "leak.py",
        REPO_ROOT / ".claude" / "settings.json",
    ):
        assert not _relative(elsewhere).startswith(SKIP_PREFIXES), _relative(elsewhere)
        assert not any(part in SKIP_DIRS for part in elsewhere.parts), _relative(elsewhere)


def test_no_tracked_name_is_one_nobody_typed() -> None:
    """The positive case, run against the real index.

    This exists because a zero-byte file called ``=ro`` — a shell redirect that
    landed one character off — was committed to the repository root and survived
    every other check here. Once a file is tracked it is invisible: ``git
    status`` is clean, ``.gitignore`` has no say, and the data gate passes it
    because it is not a statement. An acceptance run reading ``git ls-files`` by
    hand is what found it.
    """
    assert unmeant_names(tracked_files()) == []


@pytest.mark.parametrize(
    "path",
    [
        "=ro",  # the one that actually happened
        "2>&1",
        "src/ledgerbox/=ro",  # a component anywhere, not only at the root
        "~$notes.docx",
        "docs/'STATUS.md'",  # quotes that were meant for the shell
        "-rf",  # a name that is an argument
        " leading-space.md",
        "trailing-space.md ",
        "nul\tcharacter.md",
    ],
)
def test_the_index_gate_rejects_a_name_nobody_typed(path: str) -> None:
    """The negative case. Erring narrow on purpose — see the pattern's comment."""
    assert unmeant_names([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        ".gitignore",
        ".github/workflows/ci.yml",
        "src/ledgerbox/db/migrations/0001_init.sql",
        "tests/test_repo_hygiene.py",
        "docs/ADDING_A_BANK.md",
        "2025.08.release-notes.md",
        "_private.py",
        "some-file.tar.gz",
        # Dotfiles this repository does not have yet and would be entitled to.
        # The first version of the rule carried a four-name allowlist and would
        # have rejected all of these -- including `.gitmodules`, which
        # `tracked_files()` anticipates by passing `--recurse-submodules`, and
        # `.env.example`, which `.gitignore` goes out of its way to re-include.
        ".gitmodules",
        ".env.example",
        ".pre-commit-config.yaml",
        ".python-version",
        ".gitkeep",
    ],
)
def test_the_index_gate_allows_a_name_somebody_meant(path: str) -> None:
    assert unmeant_names([path]) == []


def test_the_index_gate_reads_a_non_ascii_filename_without_dying(git_free_tmp: Path) -> None:
    """git emits path *bytes*; decoding them with the locale is how this broke.

    ``subprocess.run(..., text=True)`` without an explicit encoding uses the
    locale's, and on a Windows console that is ``cp1252`` — which has no mapping
    for 0x81, 0x8D, 0x8F, 0x90 or 0x9D, every one of which occurs inside UTF-8
    CJK. One Chinese filename therefore took this gate down with a
    ``UnicodeDecodeError`` about a byte offset.

    It failed closed, so nothing got through. But a gate that reports a problem
    with no relation to the actual one is a gate somebody switches off, and this
    project has "a Chinese path does not crash it" among its P0 criteria.
    """
    repo = git_free_tmp / "repo"
    repo.mkdir()
    run = lambda *args: subprocess.run(  # noqa: E731 - four one-line invocations
        ["git", *args], cwd=repo, check=True, capture_output=True
    )
    run("init", "-q")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    (repo / "说明.md").write_text("hello", encoding="utf-8")
    run("add", "-A")

    names = tracked_files(repo)
    assert "说明.md" in names
    # And the name passes the shape rule only because it is genuinely non-ASCII,
    # which this repository's rule rejects on purpose. The point of the test is
    # that the answer is a *finding*, not a traceback.
    assert unmeant_names(names) == ["说明.md"]


def test_the_index_gate_does_not_claim_to_catch_a_plain_leftover() -> None:
    """The limit of a shape rule, asserted so the docstring cannot drift past it.

    ``cmd 2>1`` leaves a file called ``1``. It is indistinguishable from a
    deliberate one, and this gate says so rather than implying otherwise — the
    first draft of that docstring promised to catch "mangled redirects" and an
    acceptance run produced the counter-example in one line.
    """
    assert unmeant_names(["1", "core", "a.out", "nohup.out", "tmp"]) == []


def test_no_statement_or_ledger_files_are_in_the_repository() -> None:
    """The filesystem half. `tools/check_repo_data.py` is the index half.

    Both are needed and neither is redundant: a file can be on disk and not in
    the index (about to be added), or in the index and matching no rule anyone
    remembers writing (`git add -f`, or added before the rule existed --
    `.gitignore` does not apply retroactively).
    """
    found = [
        _relative(path)
        for path in REPO_ROOT.rglob("*")
        if path.is_file()
        and not any(part in SKIP_DIRS for part in path.parts)
        and is_data_file(_relative(path))
        and not _relative(path).startswith(FIXTURE_ROOTS)
    ]
    assert found == [], f"financial data file(s) inside the repository: {found}"


def test_a_real_span_dump_would_be_ignored_by_git() -> None:
    """`.gitignore` must exclude captured span JSON, not merely intend to.

    The first attempt at this rule was a no-op: it re-included
    `tests/fixtures/**/*.json` without ever excluding it, so a span dump -- full
    account number, legal name, street address, every counterparty -- would have
    gone in on `git add .`.
    """
    def ignored(relative: str) -> bool:
        """Ask git, not the file.

        Asserting that two strings appear in `.gitignore` proves nothing about
        what git does with them: one more `!` rule further down silently undoes
        both, and the assertion keeps passing. `git check-ignore` is the only
        thing that answers the question actually being asked.
        """
        try:
            result = subprocess.run(
                ["git", "check-ignore", "-q", relative],
                cwd=REPO_ROOT,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError:  # git is not on PATH at all
            # The skip branch below was unreachable without this: no git means
            # an exception, not a return code, so the test failed where it meant
            # to skip.
            pytest.skip("git is not installed here")
        if result.returncode not in (0, 1):
            pytest.skip(f"git unavailable here: {result.stderr.decode(errors='replace')}")
        return result.returncode == 0

    assert ignored("tests/fixtures/spans/statement.json"), "a captured span dump must be ignored"
    assert ignored("tests/fixtures/spans/nested/deep/real.json")
    assert ignored("tests/fixtures/anything.json")
    assert not ignored("tests/fixtures/spans/statement.redacted.json"), (
        "redacted fixtures need an explicit, narrow way back in"
    )
    assert not ignored("tests/fixtures/synthetic/generated.json")


def test_the_real_fixture_directory_is_referenced_only_through_the_environment() -> None:
    """No source or test file may hard-code where the real statements live.

    Scoped to code. Prose is allowed to say "set LEDGERBOX_REAL_FIXTURES to
    wherever your statements are" and give an example -- that is the setup
    instruction. Code containing the same string is a machine that only works
    on one person's laptop, and a leak of that person's directory layout.
    """
    offenders: list[str] = []
    for path in _candidate_files():
        relative = _relative(path)
        if relative == "tests/test_repo_hygiene.py":
            continue
        if not (relative.startswith("src/") or relative.startswith("tests/")):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if "bank statement" in lowered and "statement/" in lowered.replace("\\", "/"):
                offenders.append(f"{_relative(path)}:{line_number}")
    assert offenders == [], (
        f"the real statement directory must come from LEDGERBOX_REAL_FIXTURES only: {offenders}"
    )


# ---------------------------------------------------------------------------
# Links that point at nothing
#
# Five process documents -- a session handoff, a session prompt and three
# planning documents -- were maintainer working notes rather than anything a
# stranger reading a public repository could use, and they now live in an
# untracked directory outside the tree. Removing them left fifteen markdown
# links aimed at files that are no longer there.
#
# A dead link is the documentation form of the defect this whole project is
# about: a claim published without checking whether the thing it points at is
# real. So the guard is not "these five files are gone"; it is "every relative
# link in every tracked markdown file resolves", which the *next* document to
# leave the tree cannot slip past either.
# ---------------------------------------------------------------------------

#: ``[text](target)``, allowing the optional ``"title"`` markdown permits after
#: the target. Angle-bracket autolinks and reference-style definitions are not
#: matched: neither form occurs in this repository, and a pattern advertised to
#: catch a shape it has never seen is the kind of sentence `docs/STATUS.md`
#: §5.43 is about.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+?)(?:\s+\"[^\"]*\")?\s*\)")


def _relative_link_targets(text: str) -> list[str]:
    """Every link target in ``text`` that names a path inside this repository.

    Absolute URLs, ``mailto:`` and same-document anchors belong to somebody
    else; this check owns the tree and nothing beyond it. A fragment on a
    repository path (``FILE.md#5b``) is trimmed, because the file is what has
    to exist -- heading anchors move, and a check that fails when a section is
    renamed would be switched off within a week.
    """
    targets: list[str] = []
    for match in _MARKDOWN_LINK.finditer(text):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0]
        if path:
            targets.append(path)
    return targets


def _dangling_links(root: Path, relatives: list[str]) -> list[str]:
    """``file -> target`` for every relative markdown link that resolves to nothing."""
    dangling: list[str] = []
    for relative in relatives:
        if not relative.endswith(".md"):
            continue
        source = root / relative
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        for target in _relative_link_targets(text):
            if not (source.parent / target).exists():
                dangling.append(f"{relative} -> {target}")
    return dangling


def test_every_relative_link_in_tracked_markdown_resolves() -> None:
    """No tracked document may point at a path that is not in the tree."""
    dangling = _dangling_links(REPO_ROOT, tracked_files())
    assert dangling == [], (
        f"{len(dangling)} markdown link(s) point at nothing. Either the target "
        f"belongs in the repository or the reference should stop pretending it "
        f"is one: {dangling}"
    )


def test_the_link_scan_reads_targets_and_leaves_the_rest_alone() -> None:
    """The check's own counterexamples: discipline rule 7 applies to guards too."""
    assert _relative_link_targets("[x](docs/X.md)") == ["docs/X.md"]
    assert _relative_link_targets('[x](docs/X.md "title")') == ["docs/X.md"]
    assert _relative_link_targets("[x](docs/X.md#5bd)") == ["docs/X.md"]
    assert _relative_link_targets("[`X.md`](X.md)") == ["X.md"]
    assert _relative_link_targets("[x](https://example.invalid/y)") == []
    assert _relative_link_targets("[x](mailto:someone@example.invalid)") == []
    assert _relative_link_targets("[x](#a-heading)") == []
    assert _relative_link_targets("a bare mention of `docs/X.md` is not a link") == []


def test_the_link_scan_catches_a_target_that_left_the_tree(tmp_path: Path) -> None:
    """The red half. Written against a tree built here rather than against the
    real one, so it keeps failing for its own reason after the repository is
    fixed -- the mistake `docs/STATUS.md` §5.29 records is a check that stops
    being able to fail.
    """
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "KEPT.md").write_text("still here\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[kept](docs/KEPT.md) and [moved](docs/MOVED.md)\n", encoding="utf-8"
    )
    assert _dangling_links(tmp_path, ["README.md", "docs/KEPT.md"]) == [
        "README.md -> docs/MOVED.md"
    ]
