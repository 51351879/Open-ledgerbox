// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Simplified Chinese, the two sections that are neither the frame nor a
// table: the large-flows board and the planning notes.
//
// One of four files this language arrives in. `zh-CN.js` carries the design
// and the rules that hold for all of them; `locales/all.js` is the only place
// any of them is registered.
//
// **Nothing in the planning notes may be softened.** Every sentence there that
// says the panel is not advice, is not personalised, is not written by anyone
// licensed, and knows nothing about this ledger says exactly that in Chinese
// too. A translation that reads as encouragement is a defect, not a style.

export const zhCNPanels = {
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
  // The drop zone and its result cards. `imported`, `duplicate`, `needs_review`
  // and `failed` are wire values; only the four labels this page writes for
  // them are here, and a status the page does not know is shown as it arrived.
  'Release to upload': '松开即可上传',
  Uploading: '上传中',
  'Reconciling before anything is booked…': '正在对账，之后才会有内容入账…',
  Imported: '已导入',
  'Already imported': '此前已导入',
  'Needs review': '需要人工查看',
  'Could not read': '无法读取',
  Booked: '已入账',
  'not stated': '未说明',
  'Skipped as duplicates': '作为重复项跳过',
  // The verdict itself is a wire value and is never looked up.
  Verdict: '判定',
  ['The file is archived and nothing was booked. Every reason is below, and each one is '
    + 'waiting in the review queue.']:
    '文件已归档，没有任何内容入账。每一条原因都在下面，而且每一条都在待审队列里等着。',
  'No detail was returned with this refusal.': '这次拒收没有返回任何细节。',
  ['These exact bytes were already archived, so there was nothing to do. Re-uploading a '
    + 'statement is always safe.']:
    '这些字节此前已经归档过，因此没有任何事情要做。重复上传一份账单永远是安全的。',
  'The file could not be read at all.': '这个文件完全无法读取。',
  'No answer': '没有响应',
  Rejected: '被拒绝',
  'The upload was refused.': '这次上传被拒绝。',
  'Server status {status}.': '服务器状态 {status}。',

  // The review queue. Resolving books nothing, and the Chinese says so as
  // plainly as the English does.
  ['Everything here was archived and not booked. Resolving records that a person looked at '
    + 'it; it never books a transaction. The way a refused statement gets into the ledger is '
    + 'to fix the parser and re-ingest the kept bytes.']:
    '这里的每一项都已归档且未入账。“处理完毕”只是记录有人看过它；它永远不会让任何一笔交易入账。'
    + '一份被拒收的账单进入账本的方式，是修好解析器并重新导入保留下来的字节。',
  Blocking: '阻断',
  Warning: '警告',
  Warnings: '警告',
  'Blocking — nothing was booked': '阻断——没有任何内容入账',
  '{count} blocking': '{count} 条阻断',
  '{count} warning': '{count} 条警告',
  'unknown check': '未知检查项',
  'period unread': '账期未读出',
  'Queued {when}': '入队于 {when}',
  'Closed {when}': '关闭于 {when}',
  yes: '是',
  no: '否',
  Resolve: '处理完毕',
  Dismiss: '搁置',
  'Dismiss anyway': '仍然搁置',
  'Keep it open': '保持打开',
  ['The statement stays archived. Fixing the parser and re-ingesting the kept bytes is the '
    + 'only route into the ledger.']:
    '账单仍然归档保留。修好解析器并重新导入保留下来的字节，是进入账本的唯一路径。',
  'The item could not be updated.': '无法更新这一项。',
  'Nothing is waiting on you.': '没有任何事情在等你。',
  ['Every statement in the ledger passed its own printed totals. Anything that did not would '
    + 'be listed here, unbooked.']:
    '账本里的每一份账单都通过了它自己印出的合计。没有通过的会列在这里，且未入账。',
  'The queue could not be read.': '无法读取该队列。',

  // The statement archive. `ledgerbox doctor` is a command and stays.
  '{count} statement(s)': '{count} 份账单',
  '{count} not booked': '{count} 份未入账',
  'No statements yet.': '还没有任何账单。',
  ['Anything dropped above is listed here, archived either way — and booked only if it '
    + 'reconciles against the totals printed on it.']:
    '在上面拖入的任何文件都会列在这里，无论如何都会归档——'
    + '只有与它上面印出的合计对上账才会入账。',
  'No statement matches this search.': '没有账单符合这次检索。',
  ['Nothing has been deleted and nothing has changed — emptying the search box above brings '
    + 'the whole list back.']:
    '什么都没有被删除，什么都没有改变——清空上面的检索框就能让整个列表回来。',
  'month unread': '月份未读出',
  'Not booked': '未入账',
  'institution not stated': '未说明机构',
  '{count} blocking in the queue': '队列中有 {count} 条阻断',
  '{count} warning(s) in the queue': '队列中有 {count} 条警告',
  '{bytes} bytes': '{bytes} 字节',
  'ingested {when}': '导入于 {when}',
  ['In the archive, not in the ledger. None of its transactions were booked, so nothing on '
    + 'this page counts them. Fixing the parser and re-ingesting these same bytes is the way '
    + 'in; deleting is the way out.']:
    '在归档里，不在账本里。它的交易一笔都没有入账，所以本页没有任何数字在统计它们。'
    + '修好解析器并重新导入同样这些字节是进来的路；删除是出去的路。',
  'The statement was deleted.': '该账单已删除。',
  'The statement list could not be read.': '无法读取账单列表。',
  ['{count} file(s) could not be removed from disk. `ledgerbox doctor` reports them, and '
    + 'exits non-zero, until they are gone:']:
    '有 {count} 个文件无法从磁盘上删除。在它们消失之前，`ledgerbox doctor` 会报告它们，'
    + '并以非零码退出：',
  '{range} matched, {total} in all': '{range}匹配；全部 {total} 份',
};
