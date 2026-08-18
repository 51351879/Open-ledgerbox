// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Simplified Chinese. The key is the English sentence exactly as the page says
// it; `tests/js/locales.test.js` refuses a key that appears nowhere in the
// interface, so this file cannot drift into translating sentences that do not
// exist.
//
// It exports the dictionary and registers nothing. `locales/all.js` is the one
// place registration happens, so a language cannot half-arrive by being
// imported somewhere unexpected.
//
// **What is deliberately not here.** Three explanatory paragraphs -- the
// hatched-slice note under the category chart, the bank-leg note under the
// transaction table, and the archive note under the statement list -- quote
// labels other modules still render in English (`(none)`, `0 transactions`).
// Prose that points at an English label describes something the reader cannot
// find, which is worse than a paragraph left in English. They stay untranslated
// until the modules drawing those labels take `t()`, and `missingKeys()` names
// them in the console meanwhile.
//
// The same rule keeps sentences split across inline markup out of this file
// unless every fragment still reads correctly in Chinese once joined. Word
// order is not a property a dictionary can preserve.
//
// The `<noscript>` sentence is not here either, and cannot be: it is shown
// exactly when JavaScript is off, and this dictionary is JavaScript. An entry
// for it was written, shipped, and found dead by opening the page -- see
// OPAQUE_TAGS in ../i18n.js.
//
// Untranslatable by rule, here as everywhere: category IDs (`transfer`,
// `cash`, `cash-deposit`), wire values (`review_first`, `automatic`), amounts,
// commands, filenames, and the product name.

export const zhCN = {
  // The masthead
  'A local ledger that refuses to show numbers it cannot prove.':
    '一个拒绝显示自己证明不了的数字的本地账本。',
  'Service status': '服务状态',
  // The visually-hidden name of the language control. The option labels beside
  // it stay in their own languages on purpose -- `English` and `简体中文` are
  // how a reader who cannot read the current page finds the way out.
  Language: '语言',
  'Add statements': '添加账单',
  'Clear results': '清除结果',
  'Drop statement PDFs anywhere on this page': '把账单 PDF 拖到本页任意位置',
  ['One request per file, in order. Each statement is reconciled against its own printed '
    + 'totals before a single transaction is booked.']:
    '每个文件一次请求，按顺序处理。每一份账单都要先与它自己印出的合计对上账，'
    + '才会有第一笔交易入账。',
  'Choose files': '选择文件',

  // The date window
  'Date range': '日期范围',
  From: '从',
  To: '到',
  Refresh: '刷新',

  // The sidebar
  'Ledger navigation and Agent connection': '账本导航与 Agent 连接',
  'On this page': '本页目录',
  'This ledger': '当前账本',
  'Reading Agent connection…': '正在读取 Agent 连接…',

  // Section names, in the sidebar and as headings
  Overview: '总览',
  Charts: '图表',
  Transactions: '交易',
  'Large flows': '大额流水',
  'Agent proposals': 'Agent 提案',
  'Coverage triage': '覆盖率分流',
  Statements: '账单',
  'Planning notes': '规划备注',
  'Review queue': '待审队列',
  'Reading the local ledger…': '正在读取本地账本…',

  // The two charts
  'Ledger totals': '账本合计',
  'Month by month': '逐月',
  'Money in and out for each transaction month.': '每个交易月的资金流入与流出。',
  'In, above the line': '流入，在线上方',
  'Out, below the line': '流出，在线下方',
  'Every month as figures, including the totals': '每个月的数字，含合计',
  'What was spent, by category': '按类别看支出去向',
  'Share of spending by category.': '各类别在支出中的占比。',

  // The statement list
  'Search these statements': '检索这些账单',
  'month, institution or id': '月份、机构或 id',

  // The bottom of the page
  'Diagnostics: table rows, schema version, where the data lives':
    '诊断信息：表行数、schema 版本、数据存放位置',
  ['Runs on this machine only. No account, no telemetry, no outbound request: your statements '
    + 'never leave this computer.']:
    '只在这台机器上运行。无账号、无遥测、无对外请求：你的账单不会离开这台电脑。',
};
