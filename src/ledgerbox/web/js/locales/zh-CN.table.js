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
};
