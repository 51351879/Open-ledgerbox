# SPDX-License-Identifier: AGPL-3.0-or-later
"""A translated front page has to make the same promises as the English one.

Translation is where a careful project quietly stops being careful. The
English README was written to refuse every word it could not prove -- "not yet
on PyPI", one bank, one platform, a red cross next to everything else -- and a
translated page is read by people who will never see it. Nothing about writing
in another language makes a claim true, so a second front page needs the same
gate the first one has, and it needs it from the first commit rather than from
the first complaint.

Every check here is over ``README.md`` and every ``README.<lang>.md`` beside
it. A language added later inherits all of them without anybody remembering
to -- which is the point, because the person adding Japanese in a year is not
going to read this file first.

What is deliberately *not* checked: whether the prose is a good translation.
No test can ask that. These ask the narrower question a test can settle --
whether the two pages agree about what is true.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGLISH = REPO_ROOT / "README.md"

#: ``README.zh-CN.md``, and whatever comes after it.
TRANSLATIONS = sorted(REPO_ROOT.glob("README.*.md"))

#: Identifiers and figures that survive translation unchanged, because a
#: reader cannot type a translated one. A path, a command, a filename and an
#: amount are not words; they are things the machine will be shown, and the
#: predecessor project's own history is a long argument about what happens
#: when a number gets restated instead of copied.
FROZEN = (
    "$1,000",
    "78%",
    "ledger.db",
    "expected-totals.json",
    "start-ledgerbox.cmd",
    "ledgerbox setup --client claude --data-dir",
    "python -m venv .venv",
    "AGPL-3.0-or-later",
    "SQLite",
    "MCP",
)

#: The three verdicts in the scope tables. Their totals are the honesty of the
#: page in one countable form: a translation that turns a cross into a tick has
#: promised support for something nobody validated, and that is the exact
#: sentence this project exists to refuse.
VERDICTS = ("✅", "🔜", "❌")

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _targets(text: str) -> set[str]:
    return {match.group(1) for match in _LINK.finditer(text)}


def _relative_targets(text: str) -> set[str]:
    return {
        target.split("#", 1)[0]
        for target in _targets(text)
        if not target.startswith(("http://", "https://", "mailto:", "#"))
    }


def test_there_is_at_least_one_translation() -> None:
    """Without this the whole file passes by having nothing to check -- the
    failure mode the beancount job in CI exists to remove, in miniature.
    """
    assert TRANSLATIONS, "no README.<lang>.md found; every check below is vacuous"


def test_every_frozen_token_is_in_the_english_page() -> None:
    """The list above is a claim about ``README.md`` and goes stale silently.

    A token that has left the English page makes its row below vacuous, and a
    vacuous check reads exactly like a passing one.
    """
    absent = [token for token in FROZEN if token not in ENGLISH.read_text(encoding="utf-8")]
    assert absent == [], (
        f"FROZEN names {absent}, which README.md no longer contains; either the "
        f"page changed or this list did not"
    )


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda path: path.name)
def test_the_pages_link_to_each_other(translation: Path) -> None:
    """A reader who lands on the wrong one has to be able to leave."""
    english = ENGLISH.read_text(encoding="utf-8")
    other = translation.read_text(encoding="utf-8")
    assert translation.name in _targets(english), (
        f"README.md offers no way to reach {translation.name}"
    )
    assert "README.md" in _targets(other), f"{translation.name} offers no way back to English"


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda path: path.name)
def test_identifiers_and_amounts_survive_translation(translation: Path) -> None:
    missing = [token for token in FROZEN if token not in translation.read_text(encoding="utf-8")]
    assert missing == [], (
        f"{translation.name} translated or dropped {missing}. These are things a "
        f"reader types or a machine reads, not words"
    )


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda path: path.name)
def test_the_scope_verdicts_are_the_same_count(translation: Path) -> None:
    """Same number of ticks, same number of crosses.

    Crude on purpose. It cannot tell whether the right row got the right mark,
    but it makes the one edit that matters -- promoting something to supported
    on the way through a translation -- impossible to make quietly.
    """
    english = ENGLISH.read_text(encoding="utf-8")
    other = translation.read_text(encoding="utf-8")
    counted = {verdict: (english.count(verdict), other.count(verdict)) for verdict in VERDICTS}
    disagree = {mark: pair for mark, pair in counted.items() if pair[0] != pair[1]}
    assert disagree == {}, (
        f"{translation.name} states a different set of verdicts than README.md "
        f"(mark: english, translated) {disagree}"
    )


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda path: path.name)
def test_no_document_the_english_page_points_at_is_dropped(translation: Path) -> None:
    """The security policy, the threat model, the licence, the scope tables'
    own references: the pages a reader is sent to for the uncomfortable parts.
    Dropping one in translation removes exactly the material a shorter page
    would want to lose.
    """
    english = _relative_targets(ENGLISH.read_text(encoding="utf-8"))
    other = _relative_targets(translation.read_text(encoding="utf-8"))
    dropped = sorted(english - other - {translation.name})
    assert dropped == [], f"{translation.name} does not point its reader at {dropped}"
