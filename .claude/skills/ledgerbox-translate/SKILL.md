---
name: ledgerbox-translate
description: Translate this Ledgerbox checkout into a new language -- a UI locale dictionary and a translated README -- with the terms that must never be translated held fixed. Use when the user asks for Ledgerbox in another language, to add or extend a locale, or to translate the interface or the front page. Not for classifying transactions (use ledgerbox) or for changing behaviour.
---

# Translate Ledgerbox

You are adding a language to a project whose entire argument is that it refuses
to state what it cannot prove. **Translation is where that stops being true
without anyone noticing**, because the person reading your output cannot read
the original. Two rules govern everything below:

- **A sentence you cannot translate stays English.** English is the fallback
  and English is true. A guess is not.
- **Never soften a limit.** A scope table's ❌ stays ❌, "not yet on PyPI" stays
  false, "untested" does not become "should work". Promoting a claim while
  translating it is the one edit this project exists to refuse.

## Never translate these

They are things a reader types or a machine reads, not words:

- **Category IDs** exactly as stored: `transfer`, `cash`, `cash-deposit`,
  `groceries`, and every other id. A translated id matches nothing.
- **Wire values**: `review_first`, `automatic`, `proposal_schema_version`,
  `human`, `agent`, `learned`, `override`.
- **Amounts and their format**: `$1,000`, integer minor units, date formats.
- **Commands, paths, filenames, flags**: `ledgerbox setup --client claude
  --data-dir ...`, `start-ledgerbox.cmd`, `ledger.db`, `expected-totals.json`,
  `archive/`, `data-dir.txt`.
- **The product name** `ledgerbox`, and `MCP`, `SQLite`, `PDF`, `CSV`.
- Anything already inside backticks or a code block.

## Glossary — the words the honesty rests on

Translate these consistently, and keep the reservation each one carries. The
Chinese column is the decided rendering; a new language decides its own and
records it in its README the same way.

| English | 简体中文 | What must survive |
|---|---|---|
| reconcile / reconciled | 对账 / 已对账 | Matched against the statement's own printed totals, not "checked" |
| refused | 拒收 | Not "failed". Nothing was booked, not even partly |
| review queue | 待审队列 | It is waiting for a person, not discarded |
| abstain / abstention | 弃权 | The correct move on thin evidence. Omission is not a defect; guessing is |
| provenance | 来源 | Who decided: a rule, the Agent, an earlier answer, or the person |
| proposal | 提案 | A suggestion that has not taken effect |
| withdraw a run | 整轮撤回 | Undoes that run only, and keeps later human decisions |
| learned rule / standing rule | 学到的规则 / 常驻规则 | Learned from one answer; decreed by the owner |
| integer minor units | 整数最小单位 | Money is never a float here |
| BYOA | 自带 AI | The model and the key are the user's |

## Adding a UI locale

The mechanism is `src/ledgerbox/web/js/i18n.js`. **The English sentence is the
key and the default**, so an untranslated page is unchanged and a missing entry
falls back to English rather than to a blank.

1. Choose the BCP 47 tag, e.g. `ja`, `de`, `pt-BR`.
2. Read `src/ledgerbox/web/js/locales/zh-CN.js` first. Its header states what it
   deliberately leaves untranslated and why; the same reasoning applies to you.
3. Write `src/ledgerbox/web/js/locales/<tag>.js`: export one object, register
   nothing. Keys are English sentences copied **exactly** from
   `src/ledgerbox/web/index.html` or from a `t('...')` call in
   `src/ledgerbox/web/js/`. Do not invent a key; a key the interface does not
   contain is a translation that can never appear, and the test suite rejects it.
4. Add the import and one row to `src/ledgerbox/web/js/locales/all.js`.
5. Add one `<option>` to the `#locale` select in `index.html`, labelled in that
   language.
6. Placeholders like `{count}` must appear in your translation exactly as in the
   key -- same names, no more, no fewer. A sentence whose number was translated
   away still reads like a sentence, and is a page stating a fact with the fact
   removed. The layer refuses such an entry at runtime and the suite refuses it
   in the repository.
7. Skip, leaving English:
   - prose that quotes a label another module still renders in English;
   - a sentence split across inline markup whose fragments would not read
     correctly in your language once joined.

Run `node --test "tests/js/*.test.js"`. Then open the page, pick the language,
and read `missingKeys()` in the console for what is still English.

## Adding a translated README

Copy `README.md` to `README.<lang>.md` and translate it under the two rules at
the top. Then:

- Put a language switcher at the top of the new page, and add it to the row in
  `README.md`.
- Keep every ✅, 🔜 and ❌ on the row it was on. The suite counts them and a
  different total fails.
- Keep every link `README.md` makes, including the security policy and the
  threat model. A page that wants to be shorter loses the uncomfortable
  material first.
- Include a glossary section like the one in `README.zh-CN.md`, with the rows
  above plus anything your language had to decide.
- If `docs/` is still English only, say so on the page rather than implying
  otherwise.

Run `python -m pytest tests/test_readme_translations.py`.

## Before you finish

- Full gates: `python -m pytest`, `node --test "tests/js/*.test.js"`,
  `ruff check src tests tools`, `mypy`, `python tools/check_repo_data.py`.
- Never edit an existing English sentence to make it easier to translate. That
  changes the source of truth for every other language and for every test that
  pins it.
- Never read, copy, or translate the user's statements, descriptors, amounts or
  ledger contents. Translation touches the interface, never the data.
