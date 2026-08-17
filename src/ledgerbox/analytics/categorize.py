# SPDX-License-Identifier: AGPL-3.0-or-later
"""Descriptor to category, as a pure function of a versioned rules file.

The predecessor to this project had four defects in this exact place, and each
one is answered by something structural here rather than by care:

1. ``"chase"`` matched as a **substring** of ``"Purchase"``. Every Chase line
   says ``Card Purchase``, so 68 rows and $11,726 landed in "bank fees" against
   a real figure near $533. Patterns here are matched with word boundaries, and
   there is a test that feeds ``Card Purchase`` in and asserts it does not come
   out as ``fees``.
2. A keyword written bare as ``"76"`` (the fuel brand) matched any two adjacent
   digits and pulled 16 ACH and Zelle rows into "transport". A ``word`` pattern
   shorter than :data:`MIN_WORD_LENGTH` is refused **at load time**, so that
   rule cannot be written again.
3. Priority was the **accident of object-literal key order**, so adding one rule
   silently re-categorised unrelated transactions. ``priority`` is a declared
   integer field here, unique within a kind, and a duplicate is a load error.
4. One category became the silent catch-all, which is what made the pie chart
   look complete. There is **no catch-all**: an unmatched descriptor leaves
   ``posting.category_id`` NULL, and NULL is the only way to say "not
   classified" so the dashboard cannot be shown a fallback dressed as a fact.
   :data:`CANARIES` refuses, at load time, any pattern broad enough to become
   one.

Two further properties this module holds on to:

**It is pure.** No clock, no database, no filesystem beyond reading the rules
file once. That is what lets categorisation run inside the ingest transaction
without weakening the rebuild invariant: delete the database, re-ingest every
archived PDF, and the same descriptors produce the same category ids.

**Sign is not decided here.** Which side of the ledger an amount falls on is
already decided by :func:`ledgerbox.ledger.posting.counter_account_for`, and
:func:`side_for` reads that answer rather than re-deriving it. An expense rule
can therefore never claim a deposit: a refund from a grocery shop is income,
not negative groceries, and the classifier has no way to express otherwise.

Categories of kind ``transfer`` may declare patterns, and
:func:`matches_transfer` is what evaluates them.

That is a **deliberate reversal**. The loader used to refuse a ``transfer``
category that declared any pattern at all, on the ground that nothing read
those patterns and a rule no code evaluates reads as coverage that does not
exist (``docs/STATUS.md`` §5.39, last row). A reader exists now, so the ground
that refusal stood on is gone and the refusal went with it. **Nothing else was
relaxed**: length, canary, dead-pattern and priority-uniqueness refusals apply
to a transfer pattern exactly as they apply to a dining one, and each has a
case in ``tests/test_categorize.py`` that trips it on a transfer category
specifically.

Transfer matching is deliberately **sign-independent** and lives in its own
function rather than as a third branch of :func:`classify` -- see
:func:`matches_transfer` for why the sign gate above is not merely unnecessary
there but wrong.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from ..ledger.posting import (
    BANK_LEG_SEQ,
    EXPENSE_ACCOUNT_ID,
    INCOME_ACCOUNT_ID,
    counter_account_for,
)

__all__ = [
    "CANARIES",
    "MIN_WORD_LENGTH",
    "RULES_PATH",
    "TRANSFER_CATEGORY_ID",
    "CategoryRule",
    "Clause",
    "RuleSet",
    "RulesError",
    "assign_categories",
    "classify",
    "default_rules",
    "load_rules",
    "matches_transfer",
    "side_for",
]

#: The one rules file shipped with the package. Data, not code: adding a
#: category must not mean editing Python.
RULES_PATH: Final[Path] = Path(__file__).resolve().parent / "rules" / "categories.json"

#: ``income`` and ``expense`` are the two sides a signed amount can fall on.
#: ``transfer`` is not a third side: it is a claim about the *other end* of the
#: movement, reached by :func:`matches_transfer` or by a user's own mark, never
#: by :func:`classify` -- see the module docstring.
KINDS: Final[frozenset[str]] = frozenset({"income", "expense", "transfer"})

#: The id of the one ``transfer`` category the shipped rules file declares. It
#: spells the same word as the *kind* by coincidence of English; the kind is a
#: schema value and this is a row's primary key.
#:
#: **Nothing in ``src/`` imports it**, and that is the design rather than an
#: oversight: every production path asks a question that does not need the name.
#: ``pipeline.transfer_flags`` and ``cli.cmd_reapply_rules`` ask
#: :func:`matches_transfer` whether *any* transfer category claimed the line,
#: and ``v_txn_transfer`` matches on ``category.kind = 'transfer'`` — so a
#: hand-edited rules file may declare several transfer categories under any ids
#: and everything keeps working. Code that hardcoded one id would forbid that.
#:
#: It is exported for tests and for a caller that genuinely needs to *write*
#: this particular category (an override UI offering "mark as transfer" would),
#: and it is stated here as a constant so such a caller does not spell the
#: string. An earlier version of this comment claimed modules already import it;
#: they do not, and a comment describing a discipline nobody follows is worse
#: than no comment.
TRANSFER_CATEGORY_ID: Final[str] = "transfer"

#: Shortest permitted ``word`` pattern. Three characters is what makes the
#: predecessor's bare ``"76"`` unwritable rather than merely discouraged.
MIN_WORD_LENGTH: Final[int] = 3

#: No legitimate rule matches any of these. A pattern that does is broad enough
#: to become the silent fallback category, which is the shape that made the
#: predecessor's classification look complete while being wrong.
#:
#: The digit canary is punctuated rather than a plain run of zeros, and that is
#: deliberate: ``tests/test_repo_hygiene.py`` refuses any run of eight or more
#: digits anywhere in the repository, because that is what a leaked account or
#: barcode number looks like. A guard's own bait must not be shaped like the
#: thing the other guard is hunting -- the honest fix is to change the bait, not
#: to add an exemption. ``re.search`` still finds ``\d+`` inside it, which is
#: the digit-shaped catch-all this canary exists to refuse.
CANARIES: Final[tuple[str, ...]] = ("", "zqxjvk", "00-00-00")

_ID_RE: Final[re.Pattern[str]] = re.compile(r"\A[a-z][a-z0-9-]*\Z")

_SIDE_BY_COUNTER_ACCOUNT: Final[dict[str, str]] = {
    INCOME_ACCOUNT_ID: "income",
    EXPENSE_ACCOUNT_ID: "expense",
}


class RulesError(ValueError):
    """The rules file is not usable. Raised at load time, never at match time."""


@dataclass(frozen=True, slots=True)
class Clause:
    """One compiled matcher, and whether its source text is a sample of itself.

    ``literal`` is True for a ``word`` pattern, whose source is exactly a string
    it matches, and False for a ``regex``, whose source is source code. The
    distinction is only used by :func:`_refuse_dead_patterns`, and getting it
    wrong there refuses working rules -- which is why it is carried on the rule
    rather than recomputed: a reader checking the loader's promise has to be
    able to ask the same question the loader asked.
    """

    pattern: re.Pattern[str]
    source: str
    literal: bool


@dataclass(frozen=True, slots=True)
class CategoryRule:
    """One category and the compiled patterns that select it."""

    id: str
    kind: str
    priority: int
    #: Compiled matchers in file order, each with the text it was written as
    #: and whether that text is a sample of itself. Any one matching selects
    #: the category.
    clauses: tuple[Clause, ...]

    @property
    def patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(clause.pattern for clause in self.clauses)

    @property
    def sources(self) -> tuple[str, ...]:
        """The pattern strings as written, for error messages and documentation."""
        return tuple(clause.source for clause in self.clauses)


@dataclass(frozen=True, slots=True)
class RuleSet:
    """A validated rules file.

    ``categories`` is sorted by ``(kind, priority)``, so :func:`classify` can
    take the first match without sorting on every descriptor.
    """

    version: int
    categories: tuple[CategoryRule, ...]

    def ids(self) -> tuple[str, ...]:
        return tuple(rule.id for rule in self.categories)

    def rows(self) -> tuple[tuple[str, None, str], ...]:
        """``(id, parent_id, kind)`` for the ``category`` table.

        Flat for now: ``parent_id`` is always NULL. A hierarchy nobody displays
        is structure with no reader, and adding one later is a data change
        rather than a schema change.
        """
        return tuple((rule.id, None, rule.kind) for rule in self.categories)


def side_for(amount_minor: int) -> str:
    """``"income"`` or ``"expense"``, read off the posting layer's own answer.

    Not a second sign rule. :func:`ledgerbox.ledger.posting.counter_account_for`
    already decides which counter-account a signed amount books against, zero
    included, and this looks that decision up rather than restating it. Two
    statements of one rule is how they come to disagree.
    """
    return _SIDE_BY_COUNTER_ACCOUNT[counter_account_for(amount_minor)]


def classify(description: str, amount_minor: int, *, rules: RuleSet | None = None) -> str | None:
    """The category id for one statement line, or ``None`` if no rule claims it.

    ``None`` is a real answer and it is stored as SQL NULL. There is no
    "uncategorized" row to fall into: a category that catches everything left
    over is indistinguishable, in a chart, from a category that was matched on
    purpose.

    Only categories on the amount's own side are considered, so no expense rule
    can ever claim a deposit.

    :func:`side_for` answers ``income`` or ``expense`` and nothing else, so a
    ``transfer`` category is never returned from here however well its patterns
    fit -- :func:`matches_transfer` is the only reader of those. The two answers
    are kept apart because they are stored in different places and mean
    different things: this one becomes ``posting.category_id``, that one decides
    ``txn.is_transfer``.
    """
    ruleset = default_rules() if rules is None else rules
    side = side_for(amount_minor)
    text = description.casefold()
    for rule in ruleset.categories:
        if rule.kind != side:
            continue
        if any(pattern.search(text) for pattern in rule.patterns):
            return rule.id
    return None


def matches_transfer(description: str, *, rules: RuleSet | None = None) -> str | None:
    """The id of the ``transfer`` category claiming this descriptor, or ``None``.

    **This takes no amount, and the absence of that parameter is the point.**
    :func:`classify` gates on sign because ``income`` and ``expense`` are the
    two sides a signed amount can land on, and an expense rule claiming a
    deposit would be a category error. ``transfer`` is not a third side: it is
    a claim about *who owns the other end* of the movement, and the direction
    money travelled says nothing about that. A credit-card payment leaves the
    account; the transfer from savings that funded it arrives. Both are the
    same event. Were the sign gate applied here, every transfer rule would have
    to be written once per direction, and the first one somebody forgot would
    put transferred money back into the headline totals silently. So there is
    no sign to gate on, and no parameter through which a caller can supply one.

    **A false positive here is one step away from deleting money from the
    headline totals.** This function decides nothing on its own -- it answers a
    question -- but whatever acts on the answer sets ``txn.is_transfer``, and
    both cash-flow aggregations exclude transactions carrying it. So a wrong
    match does not misfile a row into the wrong slice of a pie: it takes the row
    out of spending altogether, which is the defect this project exists to fix
    wearing the other face (the predecessor counted transfers *as* spending;
    over-claiming here would hide real spending instead). The shipped patterns
    are therefore structural banking phrasing only, and they are written to miss
    rather than to over-claim.

    **``None`` means "no rule claimed it", never "not a transfer".** Two whole
    routes are out of scope by construction: pairing the two sides of a
    movement (``EXECUTION_PLAN.md`` §5.2 route 1) is unreachable while the
    ledger holds one own account, and a Zelle to yourself is not distinguishable
    from a Zelle to anybody else by its description -- so it is left for a
    person to mark (route 3) rather than guessed at.

    The id returned is whatever category matched, not :data:`TRANSFER_CATEGORY_ID`
    spelled out again: the shipped file declares one transfer category, a
    hand-edited file may declare several, and lowest ``priority`` wins among
    them exactly as it does for the other kinds.
    """
    ruleset = default_rules() if rules is None else rules
    text = description.casefold()
    for rule in ruleset.categories:
        if rule.kind != "transfer":
            continue
        if any(pattern.search(text) for pattern in rule.patterns):
            return rule.id
    return None


def assign_categories(
    entries: Iterable[Any], *, rules: RuleSet | None = None
) -> dict[str, str | None]:
    """``{bank-leg posting id: category id or None}`` for a batch of entries.

    The category is recorded on the **bank leg** -- the one posting whose
    account is the user's own and whose sign is what the statement printed.
    Two reasons, and both are about there being one answer rather than two:
    ``v_transaction`` is the single-entry rendering and it already joins that
    leg, so a category written anywhere else would not appear in the view every
    reader uses; and ``category_override`` is keyed by ``txn_id``, which says a
    category belongs to a transaction rather than to one of its legs.

    The text classified is ``txn_identity.raw_descriptor`` -- the bank's bytes,
    verbatim -- because that is the column ``v_transaction`` exposes and
    therefore the column ``ledgerbox recategorize`` will re-read later. Ingest
    and re-categorisation must not be reading two different strings.
    """
    assignments: dict[str, str | None] = {}
    for entry in entries:
        bank = next(posting for posting in entry.postings if posting.seq == BANK_LEG_SEQ)
        assignments[bank.id] = classify(
            entry.identity.raw_descriptor, bank.amount_minor, rules=rules
        )
    return assignments


def load_rules(path: Path | None = None) -> RuleSet:
    """Read and validate a rules file. Uncached: every call touches the disk."""
    target = RULES_PATH if path is None else path
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except OSError as error:  # pragma: no cover - depends on the filesystem
        raise RulesError(f"cannot read category rules at {target}: {error}") from error
    except json.JSONDecodeError as error:
        raise RulesError(f"{target} is not valid JSON: {error}") from error
    return _parse(raw, target)


@lru_cache(maxsize=1)
def default_rules() -> RuleSet:
    """The shipped rules, parsed once.

    Cached because :func:`classify` is called once per statement line and the
    file cannot change under a running process without a restart. Tests that
    supply their own file call :func:`load_rules` and pass the result in.
    """
    return load_rules()


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _parse(raw: Any, where: Path) -> RuleSet:
    if not isinstance(raw, dict):
        raise RulesError(f"{where}: top level must be an object")

    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise RulesError(f"{where}: 'version' must be an integer")

    note = raw.get("note", "")
    if not isinstance(note, str):
        raise RulesError(f"{where}: 'note' must be a string when present")

    entries = raw.get("categories")
    if not isinstance(entries, list) or not entries:
        raise RulesError(f"{where}: 'categories' must be a non-empty array")

    rules: list[CategoryRule] = []
    seen_ids: set[str] = set()
    seen_priorities: set[tuple[str, int]] = set()

    for entry in entries:
        rule = _parse_category(entry, where)
        if rule.id in seen_ids:
            raise RulesError(f"{where}: duplicate category id {rule.id!r}")
        seen_ids.add(rule.id)

        # Unique *within a kind*: an income rule and an expense rule never
        # compete, because sign has already chosen the side. Requiring global
        # uniqueness would force an ordering decision that has no meaning.
        slot = (rule.kind, rule.priority)
        if slot in seen_priorities:
            raise RulesError(
                f"{where}: priority {rule.priority} used twice for kind {rule.kind!r} "
                f"(at {rule.id!r}). Priority decides which rule wins; a tie would make "
                f"that depend on file order, which is the defect this field exists to remove."
            )
        seen_priorities.add(slot)
        rules.append(rule)

    rules.sort(key=lambda rule: (rule.kind, rule.priority))
    return RuleSet(version=version, categories=tuple(rules))


def _parse_category(entry: Any, where: Path) -> CategoryRule:
    if not isinstance(entry, dict):
        raise RulesError(f"{where}: every entry in 'categories' must be an object")

    identifier = entry.get("id")
    if not isinstance(identifier, str) or not _ID_RE.match(identifier):
        raise RulesError(
            f"{where}: category id {identifier!r} must be lowercase letters, digits and hyphens, "
            f"starting with a letter. It is a stable key, not a display name -- display names "
            f"live in the i18n files."
        )

    kind = entry.get("kind")
    if kind not in KINDS:
        raise RulesError(f"{where}: category {identifier!r} has kind {kind!r}, not one of {KINDS}")

    priority = entry.get("priority")
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise RulesError(f"{where}: category {identifier!r} needs an integer 'priority'")

    raw_rules = entry.get("rules")
    if not isinstance(raw_rules, list):
        raise RulesError(f"{where}: category {identifier!r} needs a 'rules' array")

    clauses: list[Clause] = []
    for clause in raw_rules:
        clauses.extend(_parse_clause(clause, identifier, where))
    patterns = [clause.pattern for clause in clauses]

    # The kind test is narrow on purpose. What M2.3 revoked is the *opposite*
    # refusal -- transfer categories that do declare patterns -- and demanding
    # that they declare some would be a second, independent change riding along
    # with the first. So a patternless transfer category stays legal exactly as
    # it was, and `matches_transfer` simply never returns one.
    if not patterns and kind != "transfer":
        raise RulesError(f"{where}: category {identifier!r} declares no patterns")

    for pattern in patterns:
        for canary in CANARIES:
            if pattern.search(canary):
                raise RulesError(
                    f"{where}: pattern {pattern.pattern!r} in {identifier!r} matches "
                    f"{canary!r}. A pattern that broad becomes the silent fallback that "
                    f"catches everything unclaimed, which is how a wrong breakdown comes "
                    f"to look complete."
                )

    _refuse_dead_patterns(clauses, identifier, where)

    return CategoryRule(id=identifier, kind=kind, priority=priority, clauses=tuple(clauses))


def _parse_clause(clause: Any, identifier: str, where: Path) -> list[Clause]:
    if not isinstance(clause, dict):
        raise RulesError(f"{where}: every rule in {identifier!r} must be an object")

    kind = clause.get("type")
    if kind == "word":
        return _parse_words(clause.get("patterns"), identifier, where)
    if kind == "regex":
        return _parse_regex(clause.get("pattern"), identifier, where)
    raise RulesError(f"{where}: rule type {kind!r} in {identifier!r} must be 'word' or 'regex'")


def _parse_words(raw: Any, identifier: str, where: Path) -> list[Clause]:
    if not isinstance(raw, list) or not raw:
        raise RulesError(f"{where}: 'word' rule in {identifier!r} needs a non-empty 'patterns'")

    clauses: list[Clause] = []
    for word in raw:
        if not isinstance(word, str):
            raise RulesError(f"{where}: word pattern {word!r} in {identifier!r} is not a string")
        text = word.strip().casefold()
        if len(text) < MIN_WORD_LENGTH:
            raise RulesError(
                f"{where}: word pattern {word!r} in {identifier!r} is shorter than "
                f"{MIN_WORD_LENGTH} characters. The predecessor's bare '76' matched any two "
                f"adjacent digits and pulled 16 unrelated rows into transport."
            )
        clauses.append(Clause(pattern=_word_pattern(text), source=text, literal=True))
    return clauses


def _parse_regex(raw: Any, identifier: str, where: Path) -> list[Clause]:
    if not isinstance(raw, str) or not raw:
        raise RulesError(f"{where}: 'regex' rule in {identifier!r} needs a non-empty 'pattern'")
    try:
        compiled = re.compile(raw, re.IGNORECASE)
    except re.error as error:
        raise RulesError(
            f"{where}: regex {raw!r} in {identifier!r} does not compile: {error}"
        ) from error
    return [Clause(pattern=compiled, source=raw, literal=False)]


def _refuse_dead_patterns(clauses: Sequence[Clause], identifier: str, where: Path) -> None:
    """Refuse a pattern that can never decide anything its neighbours would not.

    ``"service fee"`` matches every string ``"monthly service fee"`` matches, so
    within one category the longer one is dead: removing it changes no outcome,
    and leaving it in reads as coverage that is not there. Measured on a real
    corpus, two of the patterns first written for this file were exactly that.

    Two limits, both deliberate, both narrower than "this finds dead patterns":

    **One category at a time.** Patterns inside a category are combined with OR,
    so one matching the text of another makes the second redundant outright.
    Across categories the same overlap is the intended meaning of ``priority``
    -- dining claims ``uber eats`` before transport sees ``uber`` -- and
    refusing it would forbid the feature.

    **Only a literal can be the accused.** The test asks whether some sibling
    matches the accused pattern's own text, which is sound when that text is
    exactly what the accused matches -- true for a ``word`` pattern, false for a
    ``regex``, whose source is source code rather than a sample. Checking a
    regex that way accuses live rules: a sibling ``word: "abc"`` matches the
    *source* of ``regex: "[0-9]abc[0-9]"`` while matching none of the text that
    regex matches. So a genuinely dead ``regex`` clause is **not** caught here,
    and that miss is the honest trade for never killing a working one.
    """
    for index, accused in enumerate(clauses):
        for other_index, other in enumerate(clauses):
            if other_index == index:
                continue
            if other.source == accused.source:
                raise RulesError(
                    f"{where}: {identifier!r} lists the pattern {accused.source!r} twice"
                )
            if not accused.literal:
                continue
            if other.pattern.search(accused.source) is not None:
                raise RulesError(
                    f"{where}: pattern {accused.source!r} in {identifier!r} is already matched "
                    f"by {other.source!r} in the same category, so it can never change an "
                    f"outcome. Remove it, or narrow the other one."
                )


def _word_pattern(text: str) -> re.Pattern[str]:
    """Word-bounded, literal, case-insensitive.

    Lookarounds rather than ``\\b`` because ``\\b`` is defined relative to the
    character beside it: a pattern ending in ``+`` or ``/`` would have its
    trailing ``\\b`` demand a word character *after* the punctuation, which
    silently stops it ever matching. ``(?!\\w)`` means the same thing for a
    pattern that ends in a letter and the right thing for one that does not.

    The boundary is the whole point: ``chase`` inside ``Purchase`` is preceded
    by ``r``, so the lookbehind fails and the predecessor's 68 misfiled rows
    cannot happen here.
    """
    return re.compile(rf"(?<!\w){re.escape(text)}(?!\w)", re.IGNORECASE)
