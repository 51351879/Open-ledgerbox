// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Simplified Chinese, the page around the panels: the masthead, the date
// window, the sidebar, the section names, the two pictures and the four
// figures above them, the connection light, the status strip and the footer.
//
// **The key is the English sentence exactly as the page says it.**
// `tests/js/locales.test.js` refuses a key that appears nowhere in the
// interface, so no file here can drift into translating sentences that do not
// exist.
//
// **This language arrives in four files** -- this one, `zh-CN.agent.js`,
// `zh-CN.panels.js` and `zh-CN.table.js`. A dictionary is the one file that
// grows with every sentence the page gains, and this one met the 400-line
// split signal that every module here answers to. It was split rather than
// exempted, along the seam the page already has: the frame, the Agent review
// panels, the other panels, the table. `registerLocale` merges rather than
// replaces, which is what makes a language in several files safe, and the
// counterexamples refuse a file nothing imports and a sentence answered twice.
//
// Each file exports a dictionary and registers nothing. `locales/all.js` is the
// one place registration happens, so a language cannot half-arrive by being
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

  // The date window. `This year` is not here and cannot be: the control shows
  // the year itself, so that label never reaches the page.
  'All time': '全部时间',
  'Last 7 days': '最近 7 天',
  'Last month': '最近一个月',
  'Last 3 months': '最近 3 个月',
  'Last 6 months': '最近 6 个月',
  'Last 12 months': '最近 12 个月',
  'Custom…': '自定义…',
  'the whole ledger': '整本账',
  '{since} to {until}': '{since} 到 {until}',
  '{since} onwards': '{since} 起',
  'everything up to {until}': '截至 {until} 的全部',
  ['The start of the range is after its end, so it selects nothing. Swap the two dates, or '
    + 'clear one of them. Nothing below has changed: the figures, both charts and the table '
    + 'are still showing {showing}.']:
    '范围的起点在终点之后，因此它什么都选不到。把两个日期对调，或者清掉其中一个。'
    + '下方没有任何东西发生变化：数字、两张图和表格显示的仍然是 {showing}。',

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

  // The two pictures and the four figures above them.
  'Where it went': '钱去了哪里',
  In: '流入',
  Out: '流出',
  Net: '净额',
  Balance: '余额',
  '{count} transaction(s)': '{count} 笔交易',
  '{count} transaction month(s)': '{count} 个交易月',
  '{count} bucket(s)': '{count} 个类别桶',
  'dated {since} to {until}': '日期从 {since} 到 {until}',
  'dated {since} onwards': '日期自 {since} 起',
  'dated up to {until}': '日期截至 {until}',
  // `In` and `Out` are substituted here, not written again; see analytics.js.
  ['{count} transfer(s) excluded: {inflow} from {in}, {outflow} from {out}']:
    '已排除 {count} 笔转账：{in} 减少 {inflow}，{out} 减少 {outflow}',
  ['Balance is not shown for this range: nothing in this ledger is dated on or before its '
    + 'end, so there is no evidence of what the account held then.']:
    '此范围不显示余额：本账本中没有任何一笔的日期在该范围结束当天或之前，'
    + '因此没有证据说明当时账户里有多少钱。',
  ['Two readings of the same booked lines, grouped by the database and not by this page. Both '
    + 'count booked lines only: a statement that failed a check printed on it is archived and '
    + 'never averaged in here, exactly as it is never counted in the four figures above. '
    + 'Marking a line as a transfer takes it out of both pictures.']:
    '对同一批已入账行的两种读法，由数据库分组，而不是由本页分组。'
    + '两者都只统计已入账的行：一份没有通过它自己印出的检查的账单会被归档，'
    + '永远不会在这里被计入，正如它永远不会被计入上方的四个数字。'
    + '把一行标记为转账，会把它从这两张图里都拿掉。',
  'The breakdown could not be read.': '无法读取该明细。',
  'Nothing is booked yet.': '还没有任何入账。',
  ['Totals appear once a statement has passed every check printed on it. A statement that '
    + 'fails one is archived and listed below, never averaged in.']:
    '一份账单通过它上面印出的每一项检查之后，合计才会出现。'
    + '没有通过其中某一项的账单会被归档并列在下方，永远不会被计入。',
  'Nothing is booked yet, so there is nothing to break down.':
    '还没有任何入账，因此没有可以拆解的东西。',
  ['These two pictures are drawn from booked lines only: a statement that failed a check '
    + 'printed on it is archived and never averaged in here, exactly as it is never counted in '
    + 'the four figures at the top of the page.']:
    '这两张图只画已入账的行：一份没有通过它自己印出的检查的账单会被归档，'
    + '永远不会在这里被计入，正如它永远不会被计入本页顶部的四个数字。',
  'No booked line falls in this date range.':
    '这个日期范围内没有任何已入账的行。',
  ['The ledger is not empty — widen the range, or set it back to All time, to see what is in '
    + 'it. The figures above describe this range too, which is why they are zero.']:
    '账本不是空的——把范围放宽，或者把它设回“全部时间”，就能看到里面有什么。'
    + '上方的数字描述的也是这个范围，这就是它们为零的原因。',

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
