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
};
