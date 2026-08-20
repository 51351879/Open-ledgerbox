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
// transaction table, and the archive note under the statement list. Each is
// broken into several text nodes by an inline `<strong>`, so the sweep sees
// fragments rather than a sentence, and a fragment translated on its own only
// reads correctly if Chinese happens to put the emphasised words in the same
// place English does. Word order is not a property a dictionary can preserve.
// `missingKeys()` names the fragments in the console meanwhile.
//
// This note used to give a different reason -- that they quoted labels other
// modules still rendered in English. That rule is real and is why several
// panel notes waited for the controls they name; those labels are translated
// now and the notes went in with them. These three were never waiting on that.
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
  // What the page may claim about the ring under it. This is the module whose
  // sentence has been published as a falsehood twice; a translation that makes
  // any of it sound more complete than it is would be the third time.
  'Nothing claimed these': '没有任何规则认领这些',
  ['not a category — no rule claimed these lines and nobody overruled that. This is {share} of '
    + 'spending lines; the percentage above is the share of spending amount, not the share of '
    + 'lines.']:
    '不是一个类别——没有规则认领这些行，也没有人推翻这一点。这是支出行数的 {share}；'
    + '上面那个百分比是支出金额的占比，不是行数的占比。',
  ['The hatched slice is not a category. It is the lines no rule claimed and nobody overruled, '
    + 'drawn differently on purpose so it cannot be read as one more bucket beside the named '
    + 'ones. Each row’s main percentage is its share of spending amount — of the whole until a '
    + 'visual filter is active, then of visible spending; classification coverage by both '
    + 'amount and line count is stated below the chart.']:
    '斜纹那一块不是一个类别。它是没有规则认领、也没有人推翻的那些行，故意画成另一种样子，'
    + '好让它不会被读成挨着那些有名字的桶的又一个桶。每一行的主百分比是它在支出金额中的占比——'
    + '在没有启用可视筛选时是占整体，启用之后是占可见支出；'
    + '按金额与按行数两种口径的分类覆盖率都写在图表下方。',
  ['One colour per category, fixed by the category and not by its size, so a bucket keeps its '
    + 'colour when you change the dates. Colour is a second way to find a slice and never the '
    + 'only one: the rows below name every slice, including the ones too small to see.']:
    '一个类别一种颜色，由类别本身决定而不是由它的大小决定，所以改变日期时一个桶仍然保持它的颜色。'
    + '颜色是找到某一块的第二种方式，永远不是唯一的方式：下面的每一行都写出了每一块的名字，'
    + '包括小到看不见的那些。',
  ['Each row is a switch. Switching a bucket off removes its wedge and rebalances the remaining '
    + 'visible buckets into a complete ring representing all visible spending. The crossed-out '
    + 'row keeps its original whole-spend figures for reference.']:
    '每一行都是一个开关。关掉一个桶会移除它的扇形，并把其余可见的桶重新配平成一个完整的圆环，'
    + '代表全部可见支出。被划掉的那一行仍然保留它原本按整体支出算出的数字，供参考。',
  'Share of total spent': '占总支出的比例',
  'Share of visible spent': '占可见支出的比例',
  'Share of spending lines': '占支出行数的比例',
  'switched off': '已关闭',
  'share not computable': '占比无法计算',
  'Total spent': '总支出',
  'over {count} spending line(s).': '分布在 {count} 条支出行上。',
  // Quotes `Out`, the figure at the top of the page, in the same word the grid
  // above uses for it.
  ['The amount is the “Out” figure at the top of this page, broken down: the same measurement '
    + 'of the same money, not a second one.']:
    '这个金额就是本页顶部“流出”那个数字的拆解：同一笔钱的同一次计量，不是第二次计量。',
  'Showing {drawn} of {whole} spent': '在 {whole} 支出中显示 {drawn}',
  ['{count} bucket(s) are switched off in the list below. Their wedges are removed and the '
    + 'remaining visible buckets are rebalanced into a complete ring; the ring represents all '
    + 'visible spending. This visual filter does not change the whole {whole}, the ledger, or '
    + 'the classification coverage below.']:
    '下面的列表里有 {count} 个桶被关掉了。它们的扇形被移除，'
    + '其余可见的桶被重新配平成一个完整的圆环；这个圆环代表全部可见支出。'
    + '这个可视筛选不会改变整体的 {whole}、账本，或者下面的分类覆盖率。',
  ['Every bucket is switched off, so the ring is empty and there is no visible spending share '
    + 'to compute.']:
    '每一个桶都被关掉了，所以圆环是空的，也没有可见支出占比可以计算。',
  ['Nothing has been spent yet, so there is no total to divide and no share to compute.']:
    '还没有任何支出，所以没有可以拆分的总额，也没有可以计算的占比。',
  ['{count} bucket(s) are switched off in the list below. Turning them back on will not change '
    + 'the figures, because there are none to change.']:
    '下面的列表里有 {count} 个桶被关掉了。把它们重新打开不会改变这些数字，因为根本没有数字可改。',
  'No spending to break down yet.': '还没有可以拆解的支出。',
  'Show every bucket again': '重新显示每一个桶',
  ['Classification coverage: there are no spending lines in this view.']:
    '分类覆盖率：这个视图里没有任何支出行。',
  ['Classification coverage: {classified} of {total} spending line(s) ({classifiedShare}) are '
    + 'classified. The remaining {unclassified} line(s) ({unclassifiedShare}) are '
    + 'unclassified.']:
    '分类覆盖率：{total} 条支出行中有 {classified} 条（{classifiedShare}）已分类。'
    + '其余 {unclassified} 条（{unclassifiedShare}）未分类。',
  'Amount coverage is not computable because net spending is zero.':
    '金额口径的覆盖率无法计算，因为净支出为零。',
  ['By net spending amount, {classified} is classified and {unclassified} is unclassified. '
    + 'Line share and amount share answer different questions and neither is an Agent accuracy '
    + 'score.']:
    '按净支出金额算，{classified} 已分类，{unclassified} 未分类。'
    + '行数口径与金额口径回答的是不同的问题，两者都不是 Agent 的准确率分数。',
  ['Donut chart dividing {total} of spending into {buckets} bucket(s): {named} category(ies) '
    + 'and the lines nothing claimed. Every bucket is named, with its share and its amount, in '
    + 'the list beside the chart.']:
    '把 {total} 的支出分成 {buckets} 个桶的环形图：{named} 个类别，'
    + '加上没有任何规则认领的那些行。每一个桶都在图表旁边的列表里有名字，并附有它的占比与金额。',
  '{count} bucket(s) are switched off in that list and are not drawn,':
    '那个列表里有 {count} 个桶被关掉了，没有被画出来，',
  'so the ring is empty and there is no visible spending share to compute.':
    '所以圆环是空的，也没有可见支出占比可以计算。',
  ['so the remaining wedges form a complete ring showing {drawn}; their shares are recomputed '
    + 'against that visible spending.']:
    '所以其余的扇形组成一个完整的圆环，显示 {drawn}；它们的占比是按那部分可见支出重新算出来的。',
  'The whole {total} and classification coverage are unchanged.':
    '整体的 {total} 与分类覆盖率不变。',

  // The two sentences a page says about its own transport, outside any panel.
  'No answer from ledgerbox. Is the server still running?':
    'ledgerbox 没有响应。服务器还在运行吗？',
  'The local service did not answer.': '本地服务没有响应。',
};
