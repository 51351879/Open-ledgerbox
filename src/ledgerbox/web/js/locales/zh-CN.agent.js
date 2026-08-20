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
};
