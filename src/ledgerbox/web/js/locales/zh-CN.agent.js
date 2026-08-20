// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Simplified Chinese, the two Agent review panels: proposals and remaining
// coverage triage, plus the sentences they share about a write that failed.
//
// One of four files this language arrives in. `zh-CN.js` carries the design
// and the rules that hold for all of them; `locales/all.js` is the only place
// any of them is registered.
//
// The panels' own long notes are here rather than in the module, and two of
// them were only translatable once the labels they quote were: prose in one
// language pointing at a control in another sends the reader looking for
// something that is not on the page. The triage note is still absent for
// exactly that reason -- it quotes `Possible transfer`, which
// `triage-groups.js` renders in English.

export const zhCNAgent = {
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

  // The two Agent-proposal notes that quote the filter label directly above.
  // They could not be translated until it was, and they move with it.
  ['This panel only lists suggestions the Agent submitted. A zero pending count does not mean '
    + 'every candidate was classified: suggestions the Agent omitted stay under Transactions '
    + 'with Category set to “Nothing claimed this”. Review-first runs wait here; automatic v2 '
    + 'runs are already applied atomically and remain inspectable and withdrawable here.']:
    '本面板只列出 Agent 实际提交的建议。待审数为零并不表示每一个候选都已分类：'
    + 'Agent 遗漏的建议仍留在“交易”里，“类别”为“没有任何规则认领”。'
    + '先审阅（review_first）的轮次在这里等待；自动应用（automatic）的 v2 轮次已经原子地应用完毕，'
    + '仍可在这里查看与撤回。',
  ['This run has no pending proposals. That only means every submitted suggestion was '
    + 'reviewed. Candidates the Agent omitted never appear in this run; find them under '
    + 'Transactions → Category → Nothing claimed this.']:
    '这一轮没有待审的提案。这只表示每一条已提交的建议都已审阅过。'
    + 'Agent 遗漏的候选从不会出现在这一轮里；'
    + '到“交易 → 类别 → 没有任何规则认领”下面找它们。',
  // The three triage routes and the controls inside them. Reason codes and
  // outcomes are wire values, shown with their underscores turned to spaces
  // and never looked up.
  'Possible transfer': '可能是转账',
  ['Possible transfer is not a transfer decision. Choose an existing category before anything '
    + 'changes.']:
    '“可能是转账”不是一个转账决定。在任何东西发生改变之前，先选择一个已有类别。',
  'Possible taxonomy gap': '可能的分类缺口',
  ['Confirming a gap records audit evidence only. It does not invent a category or increase '
    + 'coverage.']:
    '确认一个缺口只会记录审计证据。它不会凭空造出一个类别，也不会提高覆盖率。',
  Uncertain: '不确定',
  ['Leaving a row uncertain keeps it unclassified. No catch-all category is applied.']:
    '把一行留作不确定，就是让它保持未分类。不会套用任何兜底类别。',
  // The panel note above the three routes. It quotes the first heading, so it
  // could not be translated until that heading was, and the two move together.
  ['A tool you ran locally sorted every currently unanswered row into three review routes. '
    + 'Possible transfer is not a transfer decision. Confirmed gaps and uncertain rows stay '
    + 'unclassified; only choosing an existing category changes coverage.']:
    '你在本地运行的工具，把当前每一条尚无答案的行都归入了三条审阅路线。'
    + '“可能是转账”不是一个转账决定。已确认的缺口与不确定的行仍然未分类；'
    + '只有选择一个已有类别才会改变覆盖率。',

  // What a selection is about to do. `In` and `Out` name the two figures at the
  // top of the page and are worded here exactly as `analytics.js` names them.
  '1 selected transaction.': '已选择 1 笔交易。',
  '{count} selected transactions.': '已选择 {count} 笔交易。',
  'Choose a category before classifying.': '分类前请先选择一个类别。',
  ['Applying {category} removes those amounts from the In and Out figures, not from the '
    + 'ledger.']:
    '套用 {category} 会把这些金额从“流入”和“流出”两个数字里拿掉，而不是从账本里拿掉。',
  ['Classifying sets the current category to {category}. Balances and statement lines do not '
    + 'change.']:
    '分类会把当前类别设为 {category}。余额与账单行不会改变。',
  ['Transfer remains manual approval only; accepting removes those amounts from the In and '
    + 'Out figures, not from the ledger.']:
    '转账（transfer）仍然只允许人工批准；接受会把这些金额从“流入”和“流出”两个数字里拿掉，'
    + '而不是从账本里拿掉。',
  ['Accepting sets the current category to {category}. Balances and statement lines do not '
    + 'change.']:
    '接受会把当前类别设为 {category}。余额与账单行不会改变。',

  // The row list inside a group, and the picker over it.
  'Choose a category…': '选择一个类别…',
  'Category to apply to selected transactions': '要套用到所选交易的类别',
  'Include transaction {id}': '包含交易 {id}',
  'Include all in this group': '包含这一组里的全部',
  'Include all in this reason group': '包含这个原因组里的全部',
  'Current ledger row is unavailable.': '当前账本行不可用。',
  'Current ledger row unavailable': '当前账本行不可用',
  'Still unclassified.': '仍然未分类。',
  'Still unclassified': '仍然未分类',
  'No current category.': '当前没有类别。',
  'set by you': '由你决定',
  'set by a rule': '由规则决定',
  'Accept selected': '接受所选',
  'Reject selected': '拒绝所选',
  'Classify selected': '对所选分类',
  'Confirm gap': '确认缺口',
  'Leave uncertain': '留作不确定',
  'Select at least one transaction in this group.': '请在这一组里至少选择一笔交易。',
  'Select at least one transaction in this reason group.': '请在这个原因组里至少选择一笔交易。',
  'Choose a category before classifying selected transactions.':
    '对所选交易分类前，请先选择一个类别。',
  '{count} item(s) · current bank-line total {amount} (server-derived)':
    '{count} 项 · 当前银行行合计 {amount}（由服务端推导）',
  'Reviewed decisions ({count})': '已审阅的决定（{count}）',
  'Reviewed triage decisions ({count})': '已审阅的分流决定（{count}）',
  'Gap recorded; still unclassified': '已记录缺口；仍然未分类',
  'No category applied': '未套用任何类别',
  // The sidebar's own panel: which ledger this is, whether a client is on the
  // bridge, and the setup a person copies for their own client. `Codex` and
  // `Claude Code` are product names and are substituted, never looked up.
  pending: '待处理',
  'need classification': '需要分类',
  'Reading…': '正在读取…',
  'The data directory this page and copied commands use': '本页与复制出的命令所使用的数据目录',
  'Checking Agent MCP…': '正在检查 Agent MCP…',
  '{client} MCP connected': '{client} MCP 已连接',
  'No Agent MCP connected': '没有 Agent MCP 连接',
  'Live session observed by this ledger.': '本账本观察到一个活动中的会话。',
  'Last run submitted {submitted} of {candidates} candidates.':
    '上一轮在 {candidates} 个候选中提交了 {submitted} 个。',
  'Ledgerbox may still be online; no Agent bridge is active now.':
    'Ledgerbox 可能仍然在线；只是现在没有任何 Agent 桥接处于活动状态。',
  'Agent status unavailable': '无法获知 Agent 状态',
  'Could not read Agent status.': '无法读取 Agent 状态。',

  // Connect or change Agent. `ledgerbox agent doctor --client …` is a command
  // and is substituted; the sentence around it is this page's.
  'Connect or change Agent': '连接或更换 Agent',
  Client: '客户端',
  'Copy safe setup steps': '复制安全安装步骤',
  'Copy classification prompt': '复制分类提示词',
  ['1. Copy the safe setup step. 2. Paste that single line into PowerShell: it installs or '
    + 'safely upgrades the personal Skill first, then registers MCP only if that install '
    + 'succeeded. 3. Start or reopen the selected client. 4. Check its MCP list for '
    + '“ledgerbox”. The light above turns green only after that client actually opens the '
    + 'bridge.']:
    '1. 复制那一条安全安装步骤。2. 把这一行粘贴进 PowerShell：它会先安装或安全升级个人 Skill，'
    + '只有在安装成功之后才注册 MCP。3. 启动或重新打开所选客户端。4. 在它的 MCP 列表里找'
    + '“ledgerbox”。只有那个客户端真的把桥接打开之后，上面的灯才会变绿。',
  'Full human and Agent-readable guide:': '完整的、人与 Agent 都能读的指南：',
  'Runner Skill status unavailable.': '无法获知 Runner Skill 状态。',
  'Personal Skill status unavailable.': '无法获知个人 Skill 状态。',
  'Runner Skill compatible with this Ledgerbox protocol.':
    'Runner Skill 与本 Ledgerbox 协议兼容。',
  'Runner Skill incompatible or unavailable in this Ledgerbox installation.':
    '在这个 Ledgerbox 安装里，Runner Skill 不兼容或不可用。',
  'Personal Skill current.': '个人 Skill 已是最新。',
  'Personal Skill missing. Safe setup installs it before MCP registration.':
    '个人 Skill 缺失。安全安装会在注册 MCP 之前先装上它。',
  'Personal Skill outdated. Safe setup upgrades only a recognised official copy.':
    '个人 Skill 已过时。安全安装只升级它认得出的官方副本。',
  ['Personal Skill custom. Stop and run ledgerbox agent doctor --client {client}; decide '
    + 'manually.']:
    '个人 Skill 是自定义的。请停下来运行 ledgerbox agent doctor --client {client}；由你自己决定。',
  'Stop and run ledgerbox agent doctor --client {client}; decide manually.':
    '请停下来运行 ledgerbox agent doctor --client {client}；由你自己决定。',
  'Safe setup is unavailable for this Ledgerbox installation.':
    '这个 Ledgerbox 安装无法使用安全安装。',
  '{client} setup command copied. Paste the single line into PowerShell.':
    '{client} 的安装命令已复制。把这一行粘贴进 PowerShell。',
  'Could not copy the setup command.': '无法复制安装命令。',
  '{client} classification prompt copied.': '{client} 的分类提示词已复制。',
  'Could not copy the classification prompt.': '无法复制分类提示词。',
  'Clipboard access is unavailable in this browser.': '这个浏览器不提供剪贴板访问。',

  // Classification settings. `automatic` and `review_first` are wire values;
  // only the labels a person reads are here.
  'Classification settings': '分类设置',
  'Local client': '本地客户端',
  'Application mode': '应用模式',
  'Apply answers automatically': '自动应用答案',
  'Review suggestions first': '先审阅建议',
  'Auto classify new imports': '自动分类新导入',
  ['I understand returned transaction facts may be sent to this client’s model provider.']:
    '我明白返回的交易事实可能会被发送给这个客户端的模型提供方。',
  ['When enabled, a successful import starts one bounded classification run in the selected '
    + 'local client.']:
    '启用后，一次成功的导入会在所选本地客户端里启动一轮有界的分类运行。',
  'Save and enable': '保存并启用',
  Disable: '停用',
  'Confirm the provider data boundary before enabling classification.':
    '启用分类之前，请先确认提供方数据边界。',
  '{client} policy saved.': '{client} 策略已保存。',
  'Could not save Agent settings.': '无法保存 Agent 设置。',
  'Automatic Agent classification disabled. The MCP registration is unchanged.':
    '自动 Agent 分类已停用。MCP 注册未改变。',

  // What one stretch of classification work added up to.
  'Latest classification': '最近一次分类',
  'No automatic classification run yet.': '还没有任何自动分类运行。',
  'Classify now': '立即分类',
  'Classification round queued. This panel follows it.': '分类轮次已排队。本面板会跟着它。',
  'Could not start a classification round.': '无法启动一轮分类。',
  '{done} of {total} candidates classified': '{total} 个候选中已分类 {done} 个',
  '{count}s': '{count} 秒',
  '{count} min': '{count} 分钟',
  '1 round': '1 轮',
  '{count} rounds': '{count} 轮',
  'Classification queued · 1 round.': '分类已排队 · 1 轮。',
  'Classification queued · {count} rounds.': '分类已排队 · {count} 轮。',
  'Classifying now · round {round} · {submitted} submitted so far.':
    '正在分类 · 第 {round} 轮 · 目前已提交 {submitted} 个。',
  'Round {round} of at most {max}': '第 {round} 轮，最多 {max} 轮',
  'running {duration}': '已运行 {duration}',
  'up to {duration} left if it uses every round': '若用满每一轮，最多还需 {duration}',
  'This page is watching; it updates itself. You can leave it running.':
    '本页正在盯着它；它会自己更新。你可以让它继续跑。',
  'Classification failed.': '分类失败。',
  'Classification failed ({code}).': '分类失败（{code}）。',
  ['Finished · {candidates} candidates · {submitted} submitted · {applied} applied · '
    + '{omitted} omitted.']:
    '已结束 · {candidates} 个候选 · 已提交 {submitted} · 已应用 {applied} · 遗漏 {omitted}。',
  '{rounds} in {duration}': '{rounds}，用时 {duration}',
  '{took}, {count} of them returned nothing': '{took}，其中 {count} 轮什么都没返回',
  'Ended {when}.': '结束于 {when}。',
  unknown: '未知',
  ['The Agent examined every candidate and declined them all under its abstention rules. '
    + 'These need a person: classify a few in Transactions and each answer also claims its '
    + 'identical descriptors.']:
    'Agent 检查了每一个候选，并按它的弃权规则把它们全部拒绝了。这些需要人来处理：'
    + '在“交易”里分类几笔，每个答案也会同时认领与它描述符完全相同的行。',
  'The client ended early ({outcome}), so this is not a considered stopping point.':
    '客户端提前结束了（{outcome}），所以这不是一个经过考虑的停止点。',
  ['It stopped at the round limit while still finding work, so asking again may find more.']:
    '它在仍然找得到活干的时候撞上了轮次上限，所以再问一次可能会找到更多。',
  'Needs classification: {count}': '需要分类：{count}',
};
