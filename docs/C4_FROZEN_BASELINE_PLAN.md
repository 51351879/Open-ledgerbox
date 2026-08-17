# C4 冻结基线复跑：执行与验收文档

> 状态：**C4.0-C4.4 自动化比较完成；等待只读人工语义/视觉复核与 C5 决策**
>
> 决策日期：2026-08-09
>
> 功能基线：`092cad7`（隐藏类别圆环重排修复完成）
>
> 前置状态：G0–A6、A6.5 C0–C3 与真实人工审核已完成；当前有效未分类为 0；A7 暂停
>
> 2026-08-09 后续决定：C4.0 预检与 C4.1 基线设计保持有效；C4.3 模型运行必须使用
> [`AGENT_NATIVE_OPEN_SOURCE_PLAN.md`](AGENT_NATIVE_OPEN_SOURCE_PLAN.md) 定义并经合成 eval 冻结的
> 官方模块化 Classification Skill v1，不复跑已经替换的七条薄 Skill。

---

## 0. 一句话

C4 不是再给当前账本分类，而是从同一批归档、同一套规则和同一份 24 类 taxonomy 建立两个没有
人工答案的隔离账本，让用户自己的 Codex 与 Claude Code 在完全相同的候选集上各跑一次，再用已经
完成的人类最终决定作为本地冻结参照，只输出聚合质量证据，供 C5 决定是否创建 A7。

---

## 1. 为什么不能直接在当前页面再点一次 Agent

当前有效未分类为 0。现有 proposal contract 只读取当前仍无人/规则认领、且没有 pending proposal
的候选，因此在已经完成审核的账本上运行只会得到 0 个候选。这能证明当前 coverage 已收敛，不能
比较两个 Agent。

此前 Codex 与 Claude 的真实质量数字也不能直接当作公平比赛：两轮使用的 taxonomy、候选集和审核
现场不同。产品负责人主观感受是 Claude 更准，但 C4 要回答的是“在同一输入与同一分类表上是否仍
如此”，而不是把历史不同条件下的数字重新排列。

---

## 2. C4 只回答什么

C4 回答：

1. 两端看到的候选集是否逐项相同；
2. 各自愿意提案多少，即 proposal coverage；
3. 提案与冻结的人类最终类别有多一致；
4. 普通类别和 transfer-kind 分开后，错误与省略分布怎样；
5. 若只计算与冻结参照一致的普通类别提案，按支出笔数与净支出金额能增加多少正确覆盖；
6. 这些证据是否足以进入 C5 产品决策。

C4 不回答：

- 模型的客观“准确率”；冻结参照仍是一个人的最终决定；
- 投资持仓、成本基础、税务处理或财务建议；
- transfer 是否可以自动写入——答案永远是不可以；
- 是否已经批准 A7——C4 完成也只允许进入 C5。

---

## 3. 四个本地角色，绝不能混用

| 角色 | 用途 | 可写吗 |
|---|---|---|
| **Truth** | 当前已完成审核的隔离账本；提供冻结的人类最终类别 | 只读 |
| **Base** | 从同一 archive 重建；只有确定性规则/taxonomy，没有人工 override 或 Agent audit | 建好后只读 |
| **Codex clone** | 从 Base 独立产生 Codex proposal audit | 只允许 proposal submit |
| **Claude clone** | 从 Base 独立产生 Claude proposal audit | 只允许 proposal submit |

当前 Truth 现场是 `D:\ledgerbox-data\A6 quality round1 claude`。它不是产品默认数据目录；开始 C4 前
必须重新从 `/api/health` 或 CLI 核对路径、schema、integrity 与 verifier，不能只信这行文字。

禁止把 Truth 直接复制后删除 `category_override` 充当 Base。那会遗留 proposal/triage audit、状态机
和 revision 现场，导致“看起来一样”的候选集不是一个可重建基线。Base 必须从同一 archive 向一个
全新、明确的隔离目录重新 ingest。

---

## 4. 冻结项

模型调用之前必须把以下项目写进本地 C4 运行记录；记录留在仓库外：

- 当前 Git HEAD，以及功能基线 `092cad7`；
- schema 版本；
- taxonomy 数量、id/kind 集合相等；
- 规则文件内容摘要相等；
- source / transaction / posting 等聚合行数相等；
- verifier 9 / 9；
- all-dates proposal candidate 数量相等；
- Codex clone 与 Claude clone 的候选 ID 集合相等。

候选 ID、描述、日期、金额、姓名、账户尾号、run/revision hash 不得进入仓库文档、commit message 或
最终聊天摘要。集合相等只报告 true/false 与聚合数量；原始集合只在本地进程内比较。

---

## 5. 冻结的人类参照

Truth 的当前有效类别是本轮评估参照。因为交易 ID 是内容寻址的，Base/clone 与 Truth 可以在本地按
ID 对齐；对齐过程不得导出逐笔映射到仓库。

参照规则：

1. 只评分 Base 的固定 candidate denominator；规则已经认领的行不进入 Agent 分母；
2. Agent 省略的候选记为 abstained / omitted，不算正确也不算错误；
3. suggested category 与 Truth 当前 effective category 完全一致才算 exact reference agreement；
4. 普通类别与 transfer-kind 分开报告；
5. current Truth 的 100% coverage 是人工流程结果，不是 Agent 分数；
6. 不把历史 accepted/edited/rejected 直接复制成 C4 结果，因为历史输入条件不同；
7. 不向 Agent 暴露 Truth 标签。

如果任一 Base candidate 在 Truth 中没有最终类别，冻结失败，停止模型运行并先解释为什么当前
“有效未分类为 0”与对齐结果冲突。

---

## 6. 指标在运行前固定

每个 Agent 单独报告：

| 指标 | 分子 / 分母 | 解释 |
|---|---|---|
| Candidate denominator | 固定候选数 | 两端必须相同 |
| Proposal coverage | 被提案的不同候选 / denominator | Agent 愿意回答多少 |
| Frozen-reference agreement | 与 Truth exact match / 被提案候选 | 只叫一致率，不叫客观准确率 |
| Ordinary agreement | 普通类别 exact match / 普通类别提案 | A7 只可能讨论这一侧 |
| Transfer agreement | transfer-kind exact match / transfer-kind 提案 | 永远仍需人工审批 |
| Omission | 未提案候选 / denominator | 保守性，不伪装成正确 |
| Correct line reach | 规则已覆盖行 + exact ordinary proposal 行 | 假设性正确覆盖，按支出笔数 |
| Correct amount reach | 上述集合的净支出金额 / 全部净支出金额 | 与 line reach 分开 |
| Wrong-category count | proposed but not exact | 必须分普通 / transfer，不打印样本 |

可另外按 category 聚合，但小样本必须同时显示分母；不能只展示百分比。模型自报 confidence 不采集、
不展示，也不参与阈值。

---

## 7. 执行顺序

### C4.0 — 只读预检

1. 读 `STATUS.md` §5t–§5ac、`AGENT_CLASSIFICATION_PLAN.md` §8–§10 和本文；
2. 确认 worktree 干净、分支为 main、没有意外远端；
3. 对 Truth 运行 health / verify，只读确认当前 coverage 与 0 候选；
4. 选择仓库外、名称明确的 C4 根目录；不得使用 `C:\ledger-data\my`；
5. 记录测试与隐私基线。

### C4.1 — 建立 Base 与两个 clone

1. 从 Truth 的 archive 向全新 Base 数据目录 ingest；
2. Base verifier 必须 9 / 9；
3. 独立建立 Codex clone 与 Claude clone；
4. 比较 taxonomy、规则、行数、候选数量和候选集合；
5. 任一不相等即停止，不运行模型。

不要依赖手工删除数据库表来“清空答案”。优先使用现有 ingest/rebuild 不变式建立干净基线。

### C4.2 — 固定评分器与反例

在调用模型前先让评分逻辑通过合成测试：

- 两端候选少一项时必须 FAIL；
- Truth 缺标签时必须 FAIL；
- 重复 proposal 不能扩大 coverage；
- 错误 ordinary category 计入 wrong，不计入 correct reach；
- transfer exact match 也不能进入 auto-write eligible；
- 金额与笔数分母分开；
- 输出不含描述、金额、ID、hash 或姓名。

若实现通用本地评分命令，应走 repository/service 层、使用严格 schema，并有泄漏反例；不要留下一个
只在本次会话能跑的 scratch script。如果暂不实现产品命令，评分产物必须留在仓库外且清理清单明确。

**2026-08-09 执行结果：✅ 完成。** Base 从同一 13 份 archive 向全新仓库外目录干净 ingest，两个
clone 从已通过 9/9 的 clean Base 独立建立。Truth/Base/双 clone 的 taxonomy 与 13 张稳定表聚合行数
相等；双 clone 候选集合相等为 `true`，共同分母为 270，且 Truth 对每个候选都有冻结标签。新增
[`C4_FROZEN_BASELINE_EVAL.md`](C4_FROZEN_BASELINE_EVAL.md) 和 aggregate-only scorer；缺项、缺标签、
重复、错误 ordinary、transfer、两种分母与泄漏反例均先红后绿。模型尚未运行。

### C4.3 — 两端独立 proposal run

1. Codex 只连 Codex clone，加载 `ledgerbox` Skill，严格使用五工具 proposal contract；
2. Claude Code 只连 Claude clone，加载同一项目的 `ledgerbox` Skill；
3. 两端使用同一 all-dates scope、同一分类表和同一操作 Prompt；
4. submit 只写 pending audit，不接受、不编辑、不应用类别；
5. 记录客户端/模型自报标签，但不把它当成质量保证。

不要调用 triage Skill 代替 category proposal。C4 的比较对象是两端在同一普通提案任务上的行为；
triage 是剩余覆盖审计，不会给普通类别建议。

### C4.4 — 本地聚合评分与复核

1. 对两个 run 用同一评分器与 Truth 评分；
2. 输出相同格式的两份聚合结果和一个并排比较；
3. 本地检查错误样本，但不复制进仓库；
4. verifier、余额、posting、statement line 与 Truth 保持不变；
5. clone 中只有 proposal audit 增加，没有 effective category 写入。

### C4.5 — 文档与 C5 交接

只把聚合证据写进 `STATUS.md` 和新的 C4 结果段落：候选数、coverage、agreement、ordinary/transfer
拆分、按笔数/金额的 correct reach、遗漏数与已知限制。然后由产品负责人在 C5 明确选择：

1. 保持全部审批；
2. 只对点名的普通类别自动；
3. 普通类别默认可选自动；
4. 某个 Agent 客户端暂不支持。

没有 C5 的明确书面决定，不创建 A7 migration/API/UI。

---

## 8. C4 Definition of Done

- [x] Base 从同一 archive 干净重建，不靠删除 Truth 的人工答案；
- [x] 两个 clone 的 taxonomy、规则、行数、候选数量与候选集合相同；
- [x] 两个 clone 与 Truth 均 verify 9 / 9；
- [x] 评分指标在模型运行前固定并有正反例；
- [x] Codex / Claude 各只产生一份 proposal-only audit；
- [x] 没有 Agent 或评分器写 effective category；
- [x] ordinary 与 transfer 分开；笔数与金额分开；省略单列；
- [ ] 本地错误样本已检查，但仓库/commit/聊天无私密逐笔事实；
- [x] 两端聚合结果可直接比较，限制写清；
- [ ] C5 产品负责人作出明确决定，或明确要求继续收集证据；
- [ ] 临时 MCP、浏览器快照、local manifest 与 clone 清理/保留状态逐项说明。

---

## 9. 失败即停止的条件

- Truth 不再是 0 有效未分类；
- Base 不能从 archive 重建到 9 / 9；
- 两个 clone 候选集不同；
- 模型运行前指标仍可临时改口径；
- Agent 请求任意 SQL、文件读取、confidence 或直接 apply；
- 输出准备写入逐笔描述、金额、姓名、ID 或 revision hash；
- 任何 transfer 自动应用路径出现；
- C4 需要修改现有 migration 才能继续。

这些情况不是“尽量继续”的 warning，而是冻结基线已经失效。

---

## 10. 与其他文档的关系

- 当前事实与完成证据：[`STATUS.md`](STATUS.md)
- BYOA 总路线、A7 边界与 C5 选项：[`AGENT_CLASSIFICATION_PLAN.md`](AGENT_CLASSIFICATION_PLAN.md)
- proposal 五工具契约：[`AGENT_CONTRACT.md`](AGENT_CONTRACT.md)
- 本地 Codex / Claude 安装连接：[`AGENT_SETUP.md`](AGENT_SETUP.md)
- triage 为什么不是 category proposal：[`COVERAGE_TRIAGE_CONTRACT.md`](COVERAGE_TRIAGE_CONTRACT.md)
- 架构与重建不变式：[`ARCHITECTURE.md`](ARCHITECTURE.md)
- 下一 Session 可直接复制的启动词：[`NEXT_SESSION_PROMPT.md`](NEXT_SESSION_PROMPT.md)
