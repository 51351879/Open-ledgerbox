// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Simplified Chinese, the transaction table and the controls above it.
//
// One of four files this language arrives in. `zh-CN.js` carries the design
// and the rules that hold for all of them; `locales/all.js` is the only place
// any of them is registered.

export const zhCNTable = {
  // The transaction filter bar. `(none)` is the wire sentinel behind the
  // `Nothing claimed this` option and is never translated.
  'Filter and sort the transactions': '筛选与排序交易',
  "Search the bank's line": '检索银行原始行',
  'part of a description': '描述中的一部分',
  Month: '月份',
  'Any month': '任意月份',
  Category: '类别',
  'Any category': '任意类别',
  'Nothing claimed this': '没有任何规则认领',
  Transfers: '转账',
  Included: '包含',
  'Only transfers': '仅转账',
  'Excluding transfers': '排除转账',
  Direction: '方向',
  'Either way': '两个方向',
  'Into the account': '转入账户',
  'Out of the account': '转出账户',
  'Sort by': '排序依据',
  Date: '日期',
  Amount: '金额',
  Description: '描述',
  'Statement month': '账单月份',
  Order: '顺序',
  Descending: '降序',
  Ascending: '升序',
  'Clear filters': '清除筛选',
  // The three kinds a category can have, as the option groups name them.
  Income: '收入',
  Expense: '支出',
  Transfer: '转账',
  'The month filter is empty as a result; every other control still works.':
    '因此月份筛选是空的；其他每一个控件仍然可用。',
  'No category can be chosen or filtered for until that succeeds.':
    '在这一步成功之前，无法选择类别，也无法按类别筛选。',
  // The table itself: its three bank-leg figures, its empty states, its pager,
  // and the one short sentence its live region announces.
  'Bank leg in': '银行腿流入',
  'Bank leg out': '银行腿流出',
  'Bank leg net': '银行腿净额',
  ['Measured on this account’s own leg: what the matched lines did to the balance, transfers '
    + 'included.']:
    '按本账户自己那一腿计量：匹配到的行对余额做了什么，含转账。',
  '{count} line(s) match': '{count} 行匹配',
  filtered: '已筛选',
  'Showing {first}–{last} of {matched}': '显示第 {first}–{last} 条，共 {matched} 条',
  'Showing none of {matched}': '{matched} 条中未显示任何一条',
  Previous: '上一页',
  Next: '下一页',
  'No transactions yet.': '还没有任何交易。',
  ['A statement is booked only if it reconciles against the totals printed on it; anything '
    + 'that did not is in the list below, archived and unbooked.']:
    '一份账单只有与它上面印出的合计对上账才会入账；没有对上的都在下方列表里，已归档但未入账。',
  'No transaction matches this filter.': '没有交易符合这个筛选条件。',
  ['Nothing has been deleted — changing or clearing a control above brings the rows back.']:
    '什么都没有被删除——改动或清除上方任意一个控件就能让这些行回来。',
  ['A statement that was refused has no transactions at all, so filtering to its month '
    + 'correctly shows none.']:
    '一份被拒收的账单根本没有任何交易，所以筛选到它所在的月份显示为空是正确的。',
  ['This page is past the end of the result. Previous goes back to rows that exist.']:
    '这一页已经超出结果的末尾。“上一页”会回到确实存在的行。',
  'The transactions could not be read.': '无法读取这些交易。',
  'Transaction results unavailable while ledgerbox is not answering.':
    'ledgerbox 没有响应期间，无法获得交易结果。',
  'Transaction results could not be updated.': '交易结果无法更新。',
  // One sentence per case rather than a noun swapped into one; see
  // transaction-status.js for why singular and plural are written out.
  'Transaction results updated: no lines match.': '交易结果已更新：没有行匹配。',
  'Transaction results updated: 1 line matches; this page shows none.':
    '交易结果已更新：1 行匹配；本页未显示任何一条。',
  'Transaction results updated: {count} lines match; this page shows none.':
    '交易结果已更新：{count} 行匹配；本页未显示任何一条。',
  'Transaction results updated: 1 line matches; showing {range}.':
    '交易结果已更新：1 行匹配；正在显示 {range}。',
  'Transaction results updated: {count} lines match; showing {range}.':
    '交易结果已更新：{count} 行匹配；正在显示 {range}。',
  // One row, and the one decision a person records about it. `Date`,
  // `Transfer` and `Category` are already above; a column name and a filter
  // label for the same thing must not become two words.
  'Description, as the bank printed it': '描述，银行印出来的原样',
  'Amount, bank leg': '金额，银行腿',
  'Change category': '更改类别',
  'Let the rules decide': '交给规则决定',
  'No category: nothing claimed this line.': '没有类别：没有任何规则认领这一行。',
  'marked by you': '由你标记',
  'marked by Agent': '由 Agent 标记',
  'marked by your earlier answer': '由你此前的回答标记',
  'The category could not be recorded.': '无法记录该类别。',
  'No categories to choose from.': '没有可以选择的类别。',
  ['This filter selects on category or transfer, so the count and the figures above the table '
    + 'were measured before this change.']:
    '这个筛选是按类别或转账来选的，所以上方的计数与数字是在这次改动之前量出来的。',
  'Re-read the table': '重新读取表格',
  'Select every line on this page': '选择本页的每一行',
  'Select the line dated {date} for {amount}': '选择 {date} 金额为 {amount} 的那一行',
  'Category for {date} {amount}': '{date} {amount} 的类别',

  // One decision said once about many rows.
  '{count} line(s) selected': '已选择 {count} 行',
  'Select all {count} matching': '选择全部 {count} 条匹配项',
  'Clear selection': '清除选择',
  'Say they are': '把它们说成',
  'Withdraw {count} decision(s)': '撤回 {count} 条决定',
  'Mark {count} line(s) as {what}': '把 {count} 行标记为 {what}',
  ['{count} of these carry a category you set by hand. Applying replaces it, and withdrawing '
    + 'afterwards hands the line to the rules rather than back to that category.']:
    '其中有 {count} 条带着你手工设置的类别。应用会替换掉它，而事后撤回会把这一行交给规则，'
    + '而不是交还给原来那个类别。',
  ['This filter matches {count} line(s), which is more than the {cap} one request may name. '
    + 'Narrow it — by month, by direction, or by searching — and the button will offer the '
    + 'rest.']:
    '这个筛选匹配到 {count} 行，超过了一次请求可以指名的 {cap} 行。'
    + '把它缩窄一些——按月份、按方向，或者用检索——按钮就会提供其余的。',
  'Nothing was changed.': '什么都没有改变。',
  'Working…': '处理中…',

  // The month-by-month chart and the table under it. `In`, `Out` and `Net` are
  // the same three words the four figures at the top of the page use.
  'Transaction month': '交易月',
  Lines: '行数',
  'All months': '全部月份',
  'No month has a booked line yet.': '还没有哪个月有已入账的行。',
  'no month': '无月份',
  ['“no month” is a booked line the server returned with no month on it. It is drawn as its '
    + 'own column at the end rather than dropped, because a column that is not there is a '
    + 'column nobody can question.']:
    '“无月份”是服务器返回的、没有月份的已入账行。它被画成末尾一根自己的柱子，而不是被丢掉，'
    + '因为一根不在那里的柱子是没有人能质疑的柱子。',
  ['Columns are transaction months — when the money moved. The Month filter on the table below '
    + 'is the statement month, which is the statement a line was printed on, and the two differ '
    + 'for a line near a period boundary.']:
    '每一列是交易月——钱是什么时候动的。下方表格上的“月份”筛选是账单月份，'
    + '也就是一行被印在哪一份账单上，两者在靠近账期边界的行上会不一样。',
  ['Only {shown} of {total} month labels are drawn, so they do not overlap. Every month is in '
    + 'the table below, labelled.']:
    '{total} 个月份标签只画了 {shown} 个，以免它们互相重叠。每一个月都在下方表格里，且都有标签。',
  ['Gridlines are money, the middle line is zero. In is drawn above it and out below, on one '
    + 'shared scale.']:
    '网格线是钱，中间那条线是零。流入画在它上方，流出画在下方，用同一个刻度。',
  '{month}: in {inflow}, out {outflow}, net {net}, {count} line(s).':
    '{month}：流入 {inflow}，流出 {outflow}，净额 {net}，{count} 行。',
  ['Bar chart of money in and out for {count} transaction month(s), {first} to {last}. In is '
    + 'drawn above a zero line and out below it, on one shared scale. Each column can be '
    + 'focused for its own figures, and every figure in it is in the table under the chart.']:
    '{count} 个交易月的资金进出柱状图，从 {first} 到 {last}。'
    + '流入画在零线上方，流出画在下方，用同一个刻度。'
    + '每一列都可以获得焦点以查看它自己的数字，而其中每一个数字也都在图表下方的表格里。',
};
