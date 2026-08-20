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
};
