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

  // The connection light and the sentence under it. One place on the page
  // explains a server that is not answering; six panels only say they are
  // waiting, so `Waiting for ledgerbox.` is short on purpose here too.
  'Ledgerbox online': 'Ledgerbox 在线',
  'Ledgerbox not answering': 'Ledgerbox 没有响应',
  'Checking Ledgerbox…': '正在检查 Ledgerbox…',
  'ledgerbox is running on this machine and answering.':
    'ledgerbox 正在这台机器上运行并响应。',
  ['The ledgerbox process on this machine is not answering. Start it again — the window that '
    + 'opened this page has the command — and this will go green by itself. Nothing has been '
    + 'lost: the ledger is a file on your disk.']:
    '这台机器上的 ledgerbox 进程没有响应。重新启动它——打开本页的那个窗口里有命令——'
    + '之后这里会自己变绿。什么都没丢：账本是你磁盘上的一个文件。',
  'Waiting for ledgerbox.': '正在等待 ledgerbox。',
  'Try again now': '立即重试',

  // The status strip: only ever says what is wrong.
  'Queue clear': '队列已清空',
  '{count} statement(s) refused and unbooked': '{count} 份账单被拒收，未入账',
  '{count} warning(s) to look at': '{count} 条警告待查看',
  'Database integrity check FAILED': '数据库完整性检查未通过',
  'Schema {version} of {latest}: migrations pending':
    'Schema {version} / {latest}：仍有迁移未执行',
  'No ledger file yet. It is created the first time a statement is booked.':
    '还没有账本文件。第一次有账单入账时才会创建它。',
  'Data directory': '数据目录',

  // Large flows: the board for money no person has confirmed. Category IDs and
  // amounts are substituted into these sentences, never looked up in them.
  ['Lines of at least $1,000 whose category no person has directly confirmed. Confirm keeps '
    + 'the shown category as your own decision; anything wrong, change it in Transactions '
    + 'instead.']:
    '金额不低于 $1,000、且没有任何人直接确认过其类别的行。确认会把当前显示的类别定为你自己的'
    + '决定；有问题就到 Transactions 里改，而不是在这里确认。',
  'set by Agent': '由 Agent 决定',
  'set by your earlier answer': '由你此前的回答决定',
  'set by a shipped rule': '由出厂规则决定',
  'nobody claimed this': '没有任何规则认领',
  Confirm: '确认',
  'Confirmed {category} for the {amount} line.': '已把 {amount} 那一行确认为 {category}。',
  'Confirm {category} for {amount} on {date}': '把 {date} 的 {amount} 确认为 {category}',
  'Could not confirm the category.': '无法确认该类别。',
  'Classify in Transactions': '到 Transactions 分类',
  '{count} large line(s) awaiting one look': '{count} 笔大额待看一眼',
  '(more beyond the first 200)': '（前 200 条之外还有）',
  'Every large line has a person-confirmed answer.': '每一笔大额都已有人确认过答案。',
  'Waiting for the local Ledgerbox service.': '正在等待本地 Ledgerbox 服务。',
  'Could not read large flows.': '无法读取大额流水。',

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
