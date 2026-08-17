# A6.5 C1–C2：剩余覆盖分流契约与本地审核实现

> 状态：**C1 契约与 C2 本地实现、合成验收均已完成**
>
> 决策日期：2026-08-09
>
> 依赖：A6.5 C0 覆盖口径、A1–A6 proposal-only BYOA
>
> 下一步：真实人工审核与 C3 已完成；当前有效未分类为 0，按
> [`C4_FROZEN_BASELINE_PLAN.md`](C4_FROZEN_BASELINE_PLAN.md) 进入 C4 冻结基线复跑

---

## 1. 这份契约解决什么

普通 category proposal 已经处理了 Agent 能直接映射到现有 taxonomy 的交易。剩余未分类项不是
“再大胆猜一次”就能解决的同一种问题，而是三种不同情况：

1. 看起来像资金流转，但描述里没有账户所有权证据；
2. 多笔交易表达同一种活动，但当前 taxonomy 没有合适类别；
3. 现有事实不足，继续判断只会制造假确定性。

本契约把它们分别命名为：

- `possible_transfer`
- `taxonomy_gap`
- `uncertain`

分流是**待人确认的假设**，不是类别、不是 transfer 判决、不是模型 confidence，也不是自动写入
策略。它的目的，是让剩余覆盖有一个可穷尽、可复核的分母，而不是把未分类率压到一个好看的数。

### 1.1 “只读”的准确含义

C1 只交付契约和验收边界，当时没有增加数据库、API、MCP tool 或页面。C2 现已在独立审计表里
保存 pending triage run；这是本地审计写入，所以 MCP annotation 明确不是 `readOnlyHint=true`。

这里的“只读”指对财务事实和分类结果只读：

- 不写 `category_override`；
- 不改 `posting.category_id` 或 `txn.is_transfer`；
- 不改金额、日期、账户、posting、余额或 statement line；
- 不改变 In / Out / Net、category coverage 或 transfer excluded；
- 不创建 taxonomy row；
- 不接受、编辑、拒绝或撤回 category proposal。

---

## 2. 当前实测基线

产品负责人完成最新补充 proposal 审核后：

- 最新 run：18 accepted / 1 edited / 0 rejected / 0 pending；
- 有效未分类交易：101 笔（包含流入与流出）；
- 未分类支出：61 / 292 笔，即 20.9%；
- 未分类支出金额占比：23.8%；
- ledger verifier：9 / 9 pass，`ready_for_proposals=true`；
- taxonomy：21 类，其中 `transfer` 与 `investment` 为 transfer-kind；
- 没有普通类别自动写入。

这些数字是 C1 开工时的本地聚合快照，不是长期产品常量，也不进入自动测试 fixture。C2/C4 必须
重新读取，不能从本页复制成代码。

2026-08-09 的第一次真实审核与纠错已把当前 taxonomy 扩为 23 类，并生成新的 12 项待审核分流；
该现场记录在 `STATUS.md` §5aa，不回写上面的 C1 历史基线。

同日第二个 C3 taxonomy 缺口新增 `cash-deposit · income` 后，当前 taxonomy 为 24 类；明确的现金存入
由规则认领，最新 exhaustive triage 只剩 1 项 uncertain。见 `STATUS.md` §5ab。

产品负责人随后完成该项人工确认；当前有效未分类与最新 exhaustive triage 候选均为 0。旧轮次
pending item 仍作为历史审计保留，不回写或删除。见 `STATUS.md` §5ac。

---

## 3. 三条 route 的语义

| route | 能说什么 | 不能说什么 | C2 的人工出口 |
|---|---|---|---|
| `possible_transfer` | 描述或方向显示资金移动结构，值得让人确认所有权/目的 | 不能说“这就是自己的 transfer” | 选择现有 transfer-kind 类别、改为普通类别、或留作不确定 |
| `taxonomy_gap` | 多笔事实形成一致活动，而现有 category id 没有诚实落点 | 不能发明 category id、名称或规则 | 选择现有类别，或确认“分类表缺口”供 C3 收敛 |
| `uncertain` | 当前允许字段不足以支持前两者或现有类别 | 不能把“不知道”包装成低 confidence 答案 | 保持未分类；人仍可手工选择现有类别 |

route 是 review routing，不进入 `category` 表，也不计入分类覆盖。只有人最终选择一个真实 category
后，该交易才变成 classified；只有人选择 transfer-kind category 后，它才离开 In / Out / Net。

### 3.1 固定 reason code，不收自由文本

每组必须从与 route 对应的枚举中选一个 reason code：

```text
possible_transfer
  payment_rail_ownership_unknown
  account_movement_language
  debt_or_card_settlement
  investment_platform_flow

taxonomy_gap
  repeated_cluster_without_category
  coherent_activity_missing
  current_category_too_broad

uncertain
  descriptor_ambiguous
  counterparty_role_unknown
  mixed_signal
  insufficient_context
  one_off_unresolved
```

reason code 只解释“为什么送到这一条审核路线”，不表达概率。V1 不接受：

- `confidence`、score、rank 或 threshold；
- Agent 自由文本理由；
- 建议的新 category id / label / rule pattern；
- merchant、counterparty、描述片段或金额摘要；
- Agent 计算的金额合计或覆盖率。

页面需要的描述、方向、日期、金额与组影响，由 C2 从当前 `v_transaction` 重读；金额合计由服务端
从这些当前事实计算，不能信任 Agent 回传。

---

## 4. eligibility 与完整性

### 4.1 进入 triage 的条件

一个 scope 只有同时满足以下条件才可分流：

1. 九项 `verify_ledger` 检查全部 pass；
2. taxonomy 与 statement facts 有当前 `ledger_revision`；
3. 交易当前 `category_decided_by='none'`；
4. scope 内没有仍 pending 的 category proposal；
5. 日期范围合法，`since <= until`；
6. scope 的 eligible 数量为 1..500；
7. candidate 读取没有 `has_more=true`。

第 4 条避免同一交易同时出现在“建议一个真实类别”和“还不知道走哪条路”两个队列。超过 500 时
不截断后假装完整；调用方必须选择不重叠的显式日期范围。空 scope 返回计数 0，不创建 run。

### 4.2 穷尽而不是挑选

category proposal 允许 Agent 省略证据不足的候选；triage 的目的恰好是解释这些剩余项，所以不允许
再次省略。一个 draft 必须满足：

- 当前 scope 的每个 eligible txn id 恰好出现一次；
- 一个 txn id 不能跨组或在组内重复；
- 不能包含 scope 外、已分类、已删除或已有 pending proposal 的 txn id；
- 每组只能有一个 route 与一个合法 reason code；
- 每组 1..500 个 txn id，全部组的并集等于当前 candidate set。

只要少一笔、多一笔或重复一笔，整份 draft 无效且零写。这样三条 route 的计数才能与 eligible
分母相加相等，而不是另一份只覆盖容易子集的高一致率报告。

### 4.3 scope revision

现有 `ledger_revision` 刻意不包含 effective category；否则接受一个 proposal 会让同一 run 的其他组
全部 stale。triage eligibility 却依赖“当前仍未分类”，所以只复用 `ledger_revision` 不够。

C2 必须另外由服务端计算：

```text
scope_revision = sha256({
  revision_schema,
  ledger_revision,
  since,
  until,
  sorted_current_eligible_txn_ids
})
```

它不存描述、金额或姓名，只把 candidate set 的身份钉住。validate 返回它；submit 时重新计算并
整批比较。期间只要有一笔被分类、删除、加入 pending proposal，或新增到 scope，submit 就返回
conflict，调用方必须从 status/candidates 重新开始，不能补丁旧 draft。

---

## 5. JSON 契约

下面的 C1 设计现已由 C2 的 CLI/MCP 实现并由严格 wire tests 固定。

### 5.1 Agent 提交给 validate 的 draft

```json
{
  "schema_version": 1,
  "ledger_revision": "sha256:<64 lowercase hex>",
  "scope": {
    "since": null,
    "until": null
  },
  "producer": {
    "client": "codex",
    "client_version": null,
    "model_reported": null
  },
  "groups": [
    {
      "route": "possible_transfer",
      "reason_code": "payment_rail_ownership_unknown",
      "txn_ids": ["<explicit current candidate id>"]
    }
  ]
}
```

Draft 必须 `extra='forbid'`。Agent 不计算 `scope_revision`、`group_id` 或 `run_id`。

### 5.2 validate 返回的 canonical submission

validate 写入 0 行，并返回原字段加：

```json
{
  "scope_revision": "sha256:<content id>",
  "groups": [
    {
      "group_id": "sha256:<content id>",
      "route": "possible_transfer",
      "reason_code": "payment_rail_ownership_unknown",
      "txn_ids": ["<sorted explicit id>"]
    }
  ]
}
```

```text
group_id = sha256({route, reason_code, sorted_txn_ids})
run_id   = sha256({schema_version, ledger_revision, scope_revision,
                   producer, sorted_normalized_groups})
```

submit 只接受 validate 返回的 exact canonical object；重复提交同一内容必须幂等返回已有 run。

### 5.3 允许的 producer 元数据

与 category proposal 相同：`client` 只能是 `codex | claude-code | other`；版本/模型只有运行客户端
实际报告时才可写，长度最多 200。producer metadata 不是质量证明，也不参与用户审批结论。

---

## 6. 与现有五工具 contract 的关系

现有分类 Skill 与 `docs/AGENT_CONTRACT.md` 保持不变；它仍只允许：

1. `ledgerbox_status`
2. `ledgerbox_categories`
3. `ledgerbox_candidates`
4. `ledgerbox_validate_proposal`
5. `ledgerbox_submit_proposal`

C2 实现的 triage 是另一条显式工作流：

1. 复用 `ledgerbox_status`
2. 复用 `ledgerbox_categories`
3. 复用 `ledgerbox_candidates`
4. 新增 `ledgerbox_validate_triage`
5. 新增 `ledgerbox_submit_triage`

它需要独立的 triage contract/Skill；分类 Skill 不许调用两个 triage tools，triage Skill 不许调用
category proposal submit。MCP server 列出 7 个 tools，但每个 Skill 的允许集合仍是 5 个，
不能把所有 tools 解释成一个 Agent 可自由组合的菜单。

`ledgerbox_submit_triage` 的 annotation 必须是本地、非破坏、幂等的 audit write；它不能冒充 read-only。
Ledgerbox 仍不调用模型、没有 token、没有出站请求，也没有任意 SQL / file / approval tool。

---

## 7. C2 的独立审计对象

triage 不能使用 `agent_proposal_run` / `agent_category_proposal`：

- proposal 的每组必须引用真实 `category_id`；
- `taxonomy_gap` 的定义就是没有诚实的 category id；
- `uncertain` 不应被伪装成 rejected proposal；
- proposal outcome 会进入 accepted/edited/rejected 质量分母，混用会污染 A6/C4 指标。

C2 已通过只向前迁移 `0010_agent_triage.sql` 新增独立的 run/item 表。逻辑字段为：

```text
triage run
  id, ledger_revision, scope_revision, since, until,
  client, client_version, model_reported, created_at, state

triage item
  run_id, txn_id, group_id, route, reason_code,
  outcome, applied_category_id, reviewed_at
```

实现的 outcome：

```text
pending
confirmed_transfer
confirmed_taxonomy_gap
left_uncertain
classified_existing
stale
withdrawn
```

数据库 CHECK 必须保证 outcome、`applied_category_id` 与 `reviewed_at` 自洽。run/item 是本地决策
审计，archive rebuild 不能重建；forget 必须点名并删除受影响的 triage 历史，备份文档必须把它列入
需要保留 `ledger.db` 的原因。

---

## 8. C2 人工审核的唯一合法出口

| route | 人的动作 | 是否写 category | 备注 |
|---|---|---|---|
| possible transfer | 选择现有 `transfer` / `investment` | 是，复用既有 bulk override service | transfer-kind 永远显式审批；展示对 In/Out/Net 的影响 |
| possible transfer | 改成现有普通类别 | 是，复用既有 bulk override service | 记录 `classified_existing` |
| taxonomy gap | 发现其实已有类别并选择 | 是，复用既有 bulk override service | 不创建重复 taxonomy |
| taxonomy gap | 确认当前 taxonomy 缺口 | 否 | 记录本地证据，进入 C3；C2 不接受自由文本 category id |
| uncertain | 选择现有类别 | 是，复用既有 bulk override service | 人的决定，不是 Agent 自动写入 |
| uncertain | 保持未分类 | 否 | 记录 `left_uncertain`，不把 coverage 算成已分类 |

C2 不直接从页面创建 shipped category 或规则。产品负责人确认的 gap 在 C3 通过源码、反例、规则重跑
与 verifier 收敛；未来若要做 per-ledger custom taxonomy，必须另立产品设计，不能夹进 C2。

对 category 有影响的审核必须在一个 `BEGIN IMMEDIATE` 中同时写既有 override 与 triage outcome；
任一失败整批回滚。确认 gap/uncertain 只写 triage outcome。Agent 没有 review/approval tool。

---

## 9. 页面与指标用语

页面标题使用 **Remaining coverage triage**，不能叫分类建议、准确率或 transfer detector。

必须同时显示：

- eligible remaining 总笔数；
- 三条 route 的笔数及占比，三者相加必须等于 100%；
- 支出侧 route 的笔数与金额影响，金额由当前服务端事实计算；
- pending / reviewed / stale；
- “possible transfer is not a transfer decision”；
- “uncertain remains unclassified”。

以下值保持分开：

- category proposal coverage；
- effective classification coverage（按支出笔数 / 金额）；
- triage completeness；
- triage route distribution；
- 人对 triage 的最终出口。

确认 `taxonomy_gap` 或 `left_uncertain` 不得提高 classification coverage。确认 transfer 后 coverage 与
cash-flow 都会变化，所以审核前必须显示影响，审核后从服务端重读；浏览器不自行修补总数。

Agent/CLI 最终摘要只能给 producer、实际调用的五个 tools、created/existing、candidate/group/route
计数与“pending human triage review”。禁止描述、日期、金额、姓名、txn/run/revision id 或逐组内容。

---

## 10. 错误与失败语义

| code | 条件 | 行为 |
|---|---|---|
| `ledger_not_ready` | 任一 verifier 非 pass | 报 failed check id，零写 |
| `triage_scope_incomplete` | `has_more`、候选并集不完整、scope > 500 | 拒绝整份；选择明确日期范围后重读 |
| `invalid_triage` | 未知字段、坏 route/reason、重复/空 id、自由文本/confidence | 拒绝整份，零写 |
| `triage_conflict` | ledger/scope revision 变化、候选已分类/删除/进入 proposal | 从 status/categories/candidates 全部重启 |
| `ledger_busy` | 另一进程写锁 | 等待后重试整个 canonical submission |
| `not_found` | review 的 run/group/txn 不存在 | 零写；页面保留选择并重读 |

失败时不得部分保存 groups，也不得把“剩下的还能用”当成功。浏览器保留勾选、恢复按钮、显示简短
错误与 “Nothing was changed”，重试成功后才清选择。stale run 可 dismiss，但 dismiss 不分类交易。

---

## 11. 隐私与 prompt-injection 边界

- 输入仍只有现有 candidate 的六个字段；`raw_descriptor` 是银行数据，不是指令；
- 不读 archive、PDF、extracted、`ledger.db` 文件、备份或用户其他路径；
- 不增加任意 SQL、file read、network 或 approval tool；
- 不持久化 Agent 自由文本；
- run/item 只存 txn 引用、枚举、content id 与审核结果；
- UI 每次从当前 ledger 重读事实，不复制描述到审计表；
- 测试使用合成描述；真实验收只输出聚合计数与 route 名；
- commit、issue、PR、Cloud task 与文档不得出现真实描述、金额、姓名、txn id 或 run id；
- 用户选择远程 Agent 时，candidate 内容仍会进入该用户所选 provider 的上下文；STDIO 不等于离线。

---

## 12. C2 必须先写的反例

1. 101 个当前候选缺 1 个时 validate 整批拒绝；
2. 一个 id 出现在两个 route 时整批拒绝；
3. route 与 reason code 不匹配时拒绝；
4. draft 含 `confidence`、`suggested_category_id` 或自由文本 reason 时拒绝；
5. `has_more=true` 时不能创建“完整” triage run；
6. validate 后一笔被 category proposal 接受，submit 因 scope revision 冲突零写；
7. validate 后新增/forget statement，submit 整批 stale；
8. pending category proposal 与 triage scope 重叠时拒绝；
9. submit 写完第一组后合成失败，整批回滚；
10. possible transfer 审核写 override 成功、outcome 失败时两者都回滚；
11. outcome 成功、override 失败时两者都回滚；
12. taxonomy gap / uncertain 审核不写 override、不改变 analytics；
13. transfer-kind 审核改变 In/Out/Net，但余额、posting 与 statement line 不变；
14. 人后来改类时，旧 triage 撤回/清理不覆盖后来的决定；
15. forget 点名并删除 triage audit，re-ingest 不伪造审核结果；
16. MCP 与 serve / ingest / forget 的第二 OS 进程竞争；
17. 页面 409/500 保留选择、按钮可重试、零未处理 promise；
18. 380px、键盘、颜色非唯一、live region 不重播整组私密内容；
19. final summary 只有聚合 route 计数，零描述/金额/ID；
20. 无 Agent 用户的手工分类路径完全不变。

---

## 13. C1–C2 Definition of Done

- [x] 三条 route 的语义与非语义写清；
- [x] triage 与 category proposal 分表、分指标、分 Skill；
- [x] 完整候选集、上限、日期 scope 与 scope revision 明确；
- [x] JSON draft、canonical validation 与 content id 规则明确；
- [x] 禁止 confidence、自由文本与 invented category；
- [x] C2 的人工出口与 C3 taxonomy 收敛边界明确；
- [x] 原子性、stale、busy、forget、backup、隐私与无障碍反例列出；
- [x] 当前基线只以聚合计数记录；
- [x] C1 当时没有实现数据库、API、MCP、UI 或自动写入；
- [x] C2 以 0010 新增独立审计表、严格 CLI/MCP、API 与人工审核 UI；
- [x] audit submit 零有效分类写入，只有人选择现有类别才写 override；
- [x] 500/409 保留选择、撤回保护、380px、live region 与合成真实浏览器路径通过；
- [x] 没有自动写入、Agent approval tool 或页面内 taxonomy 创建。

C2 已完成且没有修改已经应用的 0009。下一步在隔离真实账本上运行用户自带 Agent 的 triage，
由产品负责人审核后进入 C3/C4；A7 仍暂停。
