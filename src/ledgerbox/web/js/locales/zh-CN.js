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

  // Both Agent panels: what a failed write means for the action you just tried.
  // The service's own sentence arrives in English beside these and is quoted
  // rather than translated -- it is the local process reporting, not the page
  // speaking.
  'The local service reported an unexplained failure.': '本地服务报告了一个未说明的失败。',
  ['Reload current facts before retrying; this page cannot confirm whether the action '
    + 'finished.']:
    '重试前请重新读取当前事实；本页无法确认该操作是否已经完成。',
  'Reload current facts before retrying.': '重试前请重新读取当前事实。',
  'This refused action changed nothing.': '这次被拒绝的操作什么都没有改变。',
  ['The proposal or ledger changed. Reload current facts before reviewing; this refused '
    + 'action changed nothing.']:
    '提案或账本已发生变化。审阅前请重新读取当前事实；这次被拒绝的操作什么都没有改变。',
  ['The triage or ledger changed. Reload current facts before reviewing; this refused '
    + 'action changed nothing.']:
    '分流或账本已发生变化。审阅前请重新读取当前事实；这次被拒绝的操作什么都没有改变。',
  'Reload current facts': '重新读取当前事实',

  // Who produced a run. `Codex` and `Claude Code` are product names and stay.
  'Other local tool': '其他本地工具',
  'client {version}': '客户端 {version}',
  'model label {label} (self-reported)': '模型标签 {label}（自述）',
  '{count} pending': '{count} 条待审',
  '0 runs': '0 个轮次',
  '{pending} pending in {runs} recent run(s)': '最近 {runs} 个轮次中有 {pending} 条待审',
  'Confirm withdrawal': '确认撤回',
  ['Withdrew {withdrawn}; already absent {absent}; changed later and preserved {preserved}.']:
    '已撤回 {withdrawn}；原本就不存在 {absent}；此后被改过因而保留 {preserved}。',

  // Agent proposals. The panel's two long notes are deliberately absent: both
  // quote `Nothing claimed this`, the transaction filter's label, which is
  // still rendered in English. Prose in one language pointing at a control in
  // another sends the reader looking for something that is not on the page.
  'Proposal run': '提案轮次',
  ['No Agent proposal runs yet. You can keep classifying with the manual transaction '
    + 'controls, or submit a proposal with the local JSON command.']:
    '还没有任何 Agent 提案轮次。你可以继续用手工交易控件分类，或者用本地 JSON 命令提交一份提案。',
  'No Agent proposals to review.': '没有待审阅的 Agent 提案。',
  '{count} Agent proposal(s) pending.': '有 {count} 条 Agent 提案待审。',
  'Applied {count} proposal(s).': '已应用 {count} 条提案。',
  'Rejected {count} proposal(s).': '已拒绝 {count} 条提案。',
  'Proposal review failed. Current selection was kept.': '提案审阅失败。当前选择已保留。',
  'Withdraw applied decisions': '撤回已应用的决定',
  'Keep applied decisions': '保留已应用的决定',
  ['{count} applied decision(s) belong to this run. Withdrawal clears only categories that '
    + 'still match what this run applied; later manual edits are preserved.']:
    '有 {count} 条已应用的决定属于这一轮。撤回只会清除仍与这一轮所应用内容一致的类别；'
    + '此后的人工修改会被保留。',
  ['Proposal withdrawal could not be confirmed. Reload current facts.']:
    '无法确认提案撤回是否完成。请重新读取当前事实。',
  'Agent proposal review is waiting.': 'Agent 提案审阅正在等待。',
  'Agent proposal review could not load.': 'Agent 提案审阅无法加载。',

  // Remaining coverage triage. Its panel note is absent for the same reason as
  // the two above: it quotes `Possible transfer`, a route heading
  // `triage-groups.js` still renders in English.
  'Remaining coverage triage': '剩余覆盖率分流',
  'Triage run': '分流轮次',
  // The compact range beside a run, where a bound the run did not have is
  // written as one word. The sentence form is directly below it.
  start: '起点',
  end: '终点',
  'all dates': '全部日期',
  '{since} through {until}': '{since} 至 {until}',
  'All transaction dates': '全部交易日期',
  ['No remaining-coverage triage runs yet. Manual transaction classification and Agent '
    + 'proposal review remain available.']:
    '还没有任何剩余覆盖率分流轮次。手工交易分类与 Agent 提案审阅依然可用。',
  'No remaining coverage triage to review.': '没有待审阅的剩余覆盖率分流。',
  '{count} remaining coverage item(s) pending.': '有 {count} 条剩余覆盖率项待处理。',
  'This run has no pending triage items.': '这一轮没有待处理的分流项。',
  'Recorded {count} explicit triage decision(s).': '已记录 {count} 条明确的分流决定。',
  'Triage review failed. Current selection was kept.': '分流审阅失败。当前选择已保留。',
  'Dismiss remaining as uncertain': '把剩余的搁置为不确定',
  'Confirm leave unclassified': '确认保持未分类',
  'Keep reviewing': '继续审阅',
  ['{count} pending item(s) will remain unclassified. This changes no category or money '
    + 'figure.']:
    '将有 {count} 条待处理项保持未分类。这不会改变任何类别或金额数字。',
  'Left {count} remaining item(s) unclassified.': '已把剩余 {count} 条保持为未分类。',
  ['Triage dismissal could not be confirmed. Reload current facts.']:
    '无法确认分流搁置是否完成。请重新读取当前事实。',
  'Withdraw applied categories': '撤回已应用的类别',
  'Keep applied categories': '保留已应用的类别',
  ['{count} category decision(s) came from your review of this run. Withdrawal clears only '
    + 'values that still match; later changes are preserved.']:
    '有 {count} 条类别决定来自你对这一轮的审阅。撤回只会清除仍然一致的值；此后的修改会被保留。',
  'Triage withdrawal could not be confirmed. Reload current facts.':
    '无法确认分流撤回是否完成。请重新读取当前事实。',

  // Planning notes. **Not one limit in this section may be softened.** Every
  // sentence that says this panel is not advice, is not personalised, is not
  // written by anyone licensed, and knows nothing about this ledger says
  // exactly that in Chinese too. A translation that reads as encouragement is
  // a defect, not a style.
  ['General information, not advice, and not from anyone licensed to give it. Nothing here '
    + 'is computed from your transactions: the rules that sort spending into categories '
    + 'claim a small share of this ledger, so a panel that told you what you spend too much '
    + 'on would be reading a breakdown that does not cover enough to say it. Pick a range to '
    + 'see the ordinary rules of thumb for it, and check them against the figures at the top '
    + 'of this page yourself.']:
    '一般性信息，不是建议，也不出自任何有执业资质的人。这里没有任何内容是根据你的交易算出来的：'
    + '把支出归类的规则只认领了本账本的一小部分，所以一个告诉你在哪方面花得太多的面板，'
    + '读的会是一份覆盖不足以支撑这句话的明细。选一个区间，看该水平上的常见经验法则，'
    + '然后自己拿它们与本页顶部的数字对照。',
  ['General information only. Not advice, not personalised, and not written by anyone '
    + 'licensed to give it. Figures at the top of this page are measured; nothing in this '
    + 'section is.']:
    '仅为一般性信息。不是建议，不针对个人，也不出自任何有执业资质的人。'
    + '本页顶部的数字是量出来的；本节中的任何内容都不是。',
  ['This section cannot see what you spend it on. The category breakdown above covers only '
    + 'the part of your spending the shipped rules claim, and on most ledgers that is a small '
    + 'share — so no note here is derived from it.']:
    '本节看不到你把钱花在了什么上面。上方的类别明细只覆盖出厂规则认领的那部分支出，'
    + '在大多数账本上那只是一小部分——所以这里没有任何一条备注是从它推导出来的。',
  // The space before the amount is not part of this sentence; see advice.js.
  'Over the window selected at the top of this page, these statements net':
    '在本页顶部所选的时间窗口内，这些账单的净额为',
  '. That is what the documents say, and it is the only figure this section uses.':
    '。这是文件所说的，也是本节唯一使用的数字。',
  ['Once a statement is booked, this section will quote the net for the selected window — '
    + 'the one figure here that comes from your own documents.']:
    '一旦有账单入账，本节会引用所选窗口的净额——这是本节唯一一个来自你自己文件的数字。',
  'Annual income range': '年收入区间',

  // The five ranges. Their labels are amounts and are never looked up.
  'Cash buffer first': '先建立现金缓冲',
  ['The usual first target is a small emergency fund — often quoted as one month of '
    + 'essential costs to start with, then three — held somewhere boring and instant.']:
    '通常的第一个目标是一小笔应急金——常见说法是先攒够一个月的必要开支，再攒到三个月——'
    + '放在无聊而且随时可取的地方。',
  ['High-interest debt is normally paid down before anything is invested, because its rate '
    + 'is certain and an investment return is not.']:
    '高息债务通常在投资任何东西之前先还掉，因为它的利率是确定的，而投资回报不是。',
  ['Where an employer matches retirement contributions, the match is the part most guides '
    + 'say to capture before anything else.']:
    '如果雇主对退休金缴存有对等匹配，多数指南都说这部分匹配要先于其他一切拿到手。',
  'Buffer, then the tax-advantaged room': '先缓冲，再用税优额度',
  ['Three to six months of essential costs is the range most often quoted for an emergency '
    + 'fund once income is steady.']:
    '收入稳定之后，应急金最常被引用的区间是三到六个月的必要开支。',
  ['Tax-advantaged accounts have annual limits that do not carry over, which is why guides '
    + 'usually mention them before ordinary brokerage saving.']:
    '税优账户有不能结转到下一年的年度额度，这就是指南通常把它们排在普通券商储蓄之前的原因。',
  ['The 50/30/20 split — needs, wants, saving — is a starting frame, not a rule. It is worth '
    + 'checking against your own figures rather than adopting.']:
    '50/30/20 的划分——必需、想要、储蓄——是一个起步框架，不是规则。'
    + '它值得拿你自己的数字去核对，而不是直接采纳。',
  'Automate, then look at fees': '先自动化，再看费用',
  ['Automatic transfers on payday are the mechanism most commonly recommended, on the '
    + 'grounds that it removes a monthly decision rather than because it earns anything.']:
    '发薪日自动转账是最常被推荐的机制，理由是它省掉了一个每月都要做的决定，'
    + '而不是因为它能多赚什么。',
  ['Fund fees compound the same way returns do, in the other direction. Comparing expense '
    + 'ratios is one of the few levers with a known sign.']:
    '基金费用与收益以同样的方式复利，只是方向相反。比较费用率是少数几个方向已知的杠杆之一。',
  ['Insurance and estate basics — disability cover, beneficiaries — tend to be raised at '
    + 'this level because they are cheap to fix and expensive to have skipped.']:
    '保险与遗产方面的基本事项——伤残保障、受益人——常在这个水平上被提起，'
    + '因为补上它们很便宜，漏掉它们很贵。',
  'Tax treatment starts to dominate': '税务处理开始起主导作用',
  ['Which account a thing is held in starts to matter as much as what it is; asset location '
    + 'is the usual term.']:
    '一项资产放在哪个账户里，开始变得和它本身是什么一样重要；通常的说法叫资产配置位置。',
  ['Concentration risk is worth naming if a large share of pay arrives as one company’s '
    + 'equity.']:
    '如果收入中有很大一部分是以某一家公司的股权形式到手，集中度风险值得点名。',
  ['Marginal rates and phase-outs make general rules less reliable here. This is the level '
    + 'at which most guides stop generalising and say to ask somebody licensed.']:
    '边际税率与优惠递减使得一般规则在这里不那么可靠。多数指南正是在这个水平上停止泛泛而谈，'
    + '改口说去问一个有执业资质的人。',
  'General notes stop being useful': '一般性备注不再有用',
  ['Published rules of thumb are written for the middle of a distribution and get less '
    + 'applicable the further out you are.']:
    '公开发表的经验法则是为分布的中段写的，你离中段越远，它们越不适用。',
  ['The questions at this level — entity structure, concentrated positions, estate planning '
    + '— have answers that depend on details no dashboard has.']:
    '这个水平上的问题——实体架构、集中持仓、遗产规划——的答案取决于任何仪表盘都没有的细节。',
  ['This panel is general information. For anything in that list, the honest suggestion is '
    + 'a licensed professional rather than a page on your own machine.']:
    '本面板是一般性信息。对于上面列表中的任何一项，诚实的建议是找一个有执业资质的专业人士，'
    + '而不是你自己机器上的一个页面。',
};
