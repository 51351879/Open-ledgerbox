# BYOA 分类：产品、架构与执行计划

> 状态：**G0–A6、A6.5 C0–C5、真实人工审核、官方模块化 Classification Skill v1、S2 合成
> eval 与 C4 自动化比较已完成；C5 已批准 A7，A7.0 进行中**
>
> 决策日期：2026-08-07
>
> 适用版本：schema 10、P2 M1–M6、G0–G2 与 A1–A6 已交付之后
>
> 进度的唯一事实来源仍是 [`STATUS.md`](STATUS.md)；本文件定义新方向的范围、顺序与验收。
> 2026-08-09 之后的 Skill 产品化、Agent UX 与开源发布顺序以
> [`AGENT_NATIVE_OPEN_SOURCE_PLAN.md`](AGENT_NATIVE_OPEN_SOURCE_PLAN.md) 为准；本文保留已实现 BYOA
> 契约、A1–A6/A6.5 历史与 A7 安全边界。
> 2026-08-10 的 A7 当前权威任务书是
> [`A7_AUTOMATIC_CLASSIFICATION_PLAN.md`](A7_AUTOMATIC_CLASSIFICATION_PLAN.md)；其 C5 决策取代
> 本文历史段落中的“transfer 永久人工审批”目标，但不改变仍在运行的 proposal schema v1 行为。

---

## 0. 执行摘要

Ledgerbox 不内置模型、不代用户持有 OpenAI / Anthropic 凭据，也不在后台调用远程 API。
它提供一条可选的 **BYOA（Bring Your Own Agent，用户自带 Agent）** 路径：

- 没有 Codex / Claude Code 的用户继续在本地网页手工分类；
- 有本地 Codex / Claude Code 的用户，可以让自己的 Agent 通过窄的本地接口读取未分类交易、
  分组并提交分类**提案**；
- V1 的每个提案都由人批量接受、修改或拒绝；
- 先用本地记录量出提案质量，再由产品负责人决定是否实现普通类别自动写入；
- A7 经用户明确连接并启用后，普通类别与 `kind='transfer'` 都可自动应用，但必须有 Agent 来源、
  原子 audit、遗漏可见与整轮撤回；
- Codex Cloud 可以修改公开源码、运行合成测试和提交 PR，但不得接触真实账单或本地账本。

这不是把 LLM 放进账本关键路径。PDF 解析、金额、日期、账户、两条 posting、余额断言与对账闸门
保持完全确定性；Agent 只触及成功入账之后的分析层。

### 总顺序

```text
G0 现有交付物第三轮验收
  ↓
G1 doctor / verify 假绿修复
  ↓
G2 前端拆分 + aria-live 基线修整
  ↓
A1 提案契约与本地审计数据模型
  ↓
A2 Agent 中立 CLI
  ↓
A3 提案审核页面
  ↓
A4 本地 STDIO MCP 适配器
  ↓
A5 Codex / Claude Code Skills
  ↓
A6 两轮独立验收 + 真实质量观察
  ↓
A6.5 剩余覆盖闭环
  ├─ 同时显示按笔数与按金额的分类覆盖
  ├─ 剩余项分流：疑似资金流转 / 分类表缺口 / 确实不确定
  ├─ 资金流转只由人确认，Agent 不代判
  └─ 固定分类表与候选基线后重跑质量观察
  ↓
产品负责人自动化决策
  └─ C5 已批准：另立 A7，连接 Agent 后默认自动应用 ordinary 与 transfer proposal
```

公开分发门槛 `R1`（`SECURITY.md`、真实 CI runner、合成/脱敏贡献工具、PyPI）与 A1–A6
有依赖但不是同一件事，见 §11。

---

## 1. 为什么现在做，以及实测改变了什么

### 1.1 产品问题是真的

完整 13 张 Chase 账单共 415 笔。现有通用规则：

- 按笔数认领 130 / 415（31%）；
- 按支出金额认领 8.4%；
- 未认领部分占支出金额 91.6%；
- 转账规则 9 条，在真实语料上命中 0 笔。

今天让分类图有意义的唯一有效路径是人逐笔或批量设置 `category_override`。

### 1.2 「精确描述串记忆」不是首要解

2026-08-07 用 13 张账单在隔离临时账本重建后，只输出计数、不输出描述文本，实测：

| 指标 | 数值 |
|---|---:|
| 未认领交易 | 285 |
| 不同的 `normalize_descriptor()` 结果 | 260 |
| 单例描述串 | 251 |
| 重复描述组 | 9 组 / 34 笔 |
| 一次决定覆盖同串后能少做的决定 | 25（约 8.8%） |
| 含 Zelle 的行 | 56 行 / 56 个不同串 |
| `ZELLE PAYMENT TO...` | 23 行 / 23 个不同串 |
| `ZELLE PAYMENT FROM...` | 33 行 / 33 个不同串 |
| 含 Venmo 的行 | 5 行 / 5 个不同串 |

所以：精确串记忆可以以后做，但它不能解决当前痛点；Zelle / Venmo 需要的是**模式或语义分组，
再由人按组决定**。Agent 的第一价值是聚类与商户知识，不是猜测一笔 Zelle 是否转给自己。

### 1.3 现有能力已经给了正确落点

- `v_txn_category` 已把规则答案与人的覆盖合成一个有效类别；
- `POST /api/transactions/category` 已支持显式 ID 列表、单事务、整批设置或撤回；
- 分类不改金额、账户或任何一条腿；
- `transfer_excluded_*` 已把转账从数字里拿走的金额显式报出；
- 所有写入都能停留在非关键路径。

新功能不应另造一套分类写入，而应把 Agent 的输出收敛成现有批量能力能消费的提案。

---

## 2. 已拍板的产品决策

| # | 决策 | 约束 |
|---|---|---|
| D1 | **BYOA，不内置模型** | Ledgerbox 不持有模型密钥、不选择模型、不后台外呼 |
| D2 | **V1 全部 proposal-only** | Agent 只能提交提案，不能改变有效分类 |
| D3 | **人批量接受后才写入** | 接受、改类、拒绝都必须可见且本地记录 |
| D4 | **先量质量，再讨论自动写入** | 不用模型自报 confidence 当升级闸门 |
| D5 | **自动模式若实施，只覆盖普通类别** | `kind='transfer'` 始终人工审批 |
| D6 | **用户保留模式选择权** | 手动 / 全审批 / 普通类别自动，默认全审批 |
| D7 | **Codex 与 Claude Code 共用一个能力契约** | MCP/CLI 是能力层，Skill 只是薄工作流层 |
| D8 | **Codex Cloud 只碰公开代码** | 真实 PDF、`ledger.db`、描述串和本地质量记录不得上传 |
| D9 | **提案历史只在本地** | 零遥测；质量报告不上传 |
| D10 | **话不许比证据强** | 页面报告“用户一致率”，不把它叫客观“准确率” |

### 2.1 三种最终使用模式

| 模式 | 可用条件 | 行为 |
|---|---|---|
| 手动 | 所有人 | 不连接 Agent，沿用现有逐笔/批量 UI |
| 全审批（V1 默认） | 已连接本地 Agent | 所有普通类别与转账都先生成提案 |
| 普通类别自动（未来） | 用户主动开启 | income / expense 可自动；transfer 仍进审批队列 |

未来可以增加“按类别授权”，例如 groceries 自动、income 审批；这不是 A1–A6 的范围。

---

## 3. 绝不能跨过的边界

### 3.1 Agent 不进入摄入关键路径

允许：

- 为未知银行分析版式；
- 生成或修改一个确定性 parser；
- 写合成用例并运行真实账单验收；
- 给已经成功入账的交易提分类建议；
- 生成本地分析、标签或整理提案。

禁止：

- 把 Agent 从 PDF 猜出的金额直接写进 `raw_record` / `txn` / `posting`；
- 绕过 `opening + inflow + outflow = closing`；
- 把 Agent 答案写进 `posting.category_id`；
- 用 Agent confidence 决定是否放过对账；
- 让 Codex Cloud、issue、PR 或测试 fixture 携带真实财务数据。

正确路径始终是：

```text
Agent 辅助开发 parser → 确定性解析 → 强制对账 → 成功入账或拒绝
                                            ↓
                                      分类提案层
```

### 3.2 MCP 不等于隐私边界

本地 STDIO MCP server 自己不联网，但 Codex / Claude Code 如何处理工具结果由用户的 Agent、账号、
供应商与配置决定。文档必须同时说清两件事：

1. Ledgerbox 不发起出站请求，也不持有 Agent 凭据；
2. 用户显式连接 Agent 后，返回给 Agent 的数据受用户所选 Agent 的数据政策约束。

不得写成“装了 MCP，所以数据仍绝对不离机”。

### 3.3 不开放任意 SQL

否决 `query(sql)`。MCP/CLI 只暴露产品语义明确的窄能力：

- 不能枚举任意表；
- 不能读 archive 路径或原始 PDF；
- 不能执行 DDL / PRAGMA 写操作；
- 不能绕过 repository / service 层直接写 `category_override`；
- 不能一次请求隐式选择“当前筛选的所有行”。

---

## 4. 过去未完成事项盘点与新排序

### 4.1 必须在 BYOA 之前完成

| 遗留项 | 现状 | 为什么现在成为前置条件 | 放置 |
|---|---|---|---|
| §5j / §5k 第三轮独立验收 | **✅ G0 已完成；四条修复后两路复验 PASS** | 批量端点是 proposal review 最终写入面；结果与证据见 STATUS §5l | G0 ✅ |
| `doctor` 与 `verify` 覆盖不一致 | **✅ G1 已完成；doctor 直接消费九条结果** | 三类旧假绿与 doctor 独有职责均有反例，见 STATUS §5m | G1 ✅ |
| `index.html` 拆分信号 | **✅ G2 已完成；396 → 334 行** | controls markup 已归拥有行为的模块，proposal panel 可落在独立壳中 | G2 ✅ |
| `.txn-totals` 过大的 `aria-live` | **✅ G2 已完成；长统计退出 live region** | 只保留 44–59 字的独立原子状态；证据见 STATUS §5n | G2 ✅ |
| 跨进程读写未构造 | **✅ A4 已构造；5 秒 busy 后零写，锁释放可整批重试** | 第二个真实 OS 进程持有 `BEGIN IMMEDIATE`，MCP session 未死亡、无半批 | A4 ✅ |
| `connect_read_only` 误用陷阱未封死 | **✅ A1 已封死；with 退出会真正 close** | 新 subclass 只改变只读句柄生命周期，写连接事务语义不变；见 STATUS §5o | A1 ✅ |

### 4.2 必须在“公开分发 BYOA”之前完成，但不阻塞本地 V1 开发

| P5 遗留项 | 现状 | 新排序理由 | 放置 |
|---|---|---|---|
| `SECURITY.md` | 未写 | MCP、Agent 数据边界和漏洞报告需要正式入口 | R1，A5 发布前 |
| CI 在真实 runner 上执行 | workflow 已写、从未跑过 | Skill/MCP 跨平台承诺不能只靠本机 | R1，首次发布前 |
| `tools/gen_synthetic.py` | 未写 | Codex Cloud 和外部贡献者需要不含真实数据的完整财务人生 | R2 |
| span fixtures + 故意损坏输入 | 未写 | 外部 parser PR 不能依赖产品负责人的 PDF | R2 |
| `tools/sanitize.py` | 未写 | 用户若贡献新银行，不能手工“脱敏”后上传 | R2，新银行贡献指南开放前 |
| PyPI / `uvx ledgerbox` | 未发布 | 最终 Agent 安装体验依赖稳定包入口 | R3 |

**第一次 push 前仍必须再次询问产品负责人。** 历史里的两笔泄漏当前仍因“没有远端”而保留
重写历史的选择；本计划不替产品负责人关闭这个选择。

### 4.3 不阻塞 BYOA V1，保持延期

| 项 | 决定 |
|---|---|
| P3 通用 CSV + 映射向导 | A6 质量观察期间再排；不是 A1–A6 依赖 |
| 第二家银行 | 继续由真实样本驱动；不得拿 Chase 语料猜 |
| 订阅检测 | 继续延期 |
| i18n | 继续延期 |
| 投资账户 P4 | 继续跳过，直到有真实样本 |
| 精确描述串记忆 | 当前只节省约 8.8%，不排在 Agent 提案前 |
| 非 ISO `txn.date` CHECK | 当前摄入路径不可达、SQLite 代价高，知情敞开 |
| 多腿交易类别语义 | 当前 builder 只产两腿，等真实需求 |
| 多币种聚合 | 当前仅 USD，等真实需求 |
| 宽日期窗口 5 万行性能 | 记录基线，A6 用合成数据复测；当前 415 行不阻塞 |
| Firefox / WebKit / 真实屏幕阅读器 | 公开发布前补一轮，不阻塞本地设计实现 |

---

## 5. 目标架构

```mermaid
flowchart LR
    subgraph PC["用户电脑"]
        DB["ledger.db"]
        CORE["Ledgerbox service / repo"]
        WEB["本地网页"]
        CLI["ledgerbox agent CLI"]
        MCP["本地 STDIO MCP"]
        AGENT["Codex / Claude Code"]
        PROP["提案审计表"]
        OVR["category_override"]
    end

    DB <--> CORE
    CORE <--> WEB
    CORE <--> CLI
    CLI <--> MCP
    MCP <--> AGENT
    AGENT -->|"显式 IDs + category"| PROP
    PROP -->|"用户接受/修改"| WEB
    WEB -->|"同一事务"| OVR

    REPO["公开 GitHub 仓库"] --> CLOUD["Codex Cloud"]
    CLOUD --> PR["diff / PR"]
```

### 5.1 一份能力定义，三个调用面

核心 service 层只实现一次：

1. Web API 调它；
2. Agent CLI 调它；
3. MCP adapter 调 Agent CLI 或同一 service。

Skill 不读 SQLite、不拼 SQL、不直接调用 repo 私有函数。它只描述工作流、风险和调用顺序。

### 5.2 为什么 CLI 在 MCP 前

Agent 中立 CLI 提供三个好处：

- Codex 与 Claude Code 即使没有 MCP SDK，也都能调用本地命令；
- 协议输入输出可以先用普通 JSON 做确定性测试；
- MCP 只是适配层，不成为财务语义的第二个定义。

### 5.3 MCP 依赖策略

核心五个运行时依赖保持不变。首选顺序：

1. 用官方 SDK 做一次 Windows STDIO spike；
2. 若采用 SDK，放进可选 extra 或独立 Agent 插件，不进入核心依赖；
3. 没装 extra 时，Skill 回退到 Agent CLI；
4. 不为了省一个可选依赖手写一套未经协议一致性测试的 JSON-RPC 实现。

MCP 适配器只绑定 STDIO，不开监听端口，不持有凭据，不发网络请求。

---

## 6. 提案契约与数据模型

### 6.1 Agent 读取范围

V1 只读取：

- live statement transactions；
- `category_decided_by='none'` 的行；
- 用户显式选择的日期范围或全部未分类行；
- 最少字段：`txn_id`、date、direction、amount_minor、currency、raw_descriptor；
- 有效类别列表：id、kind、label；当前 taxonomy 没有独立展示名列，所以 A2 的稳定 `label=id`，
  不从另一份文件虚构第二套名称。

V1 默认不让 Agent 推翻规则答案或既有人工覆盖。这个范围先把“285 笔没人认领”解决，
不把“纠正已有决定”混进同一个里程碑。

### 6.2 提案 wire schema（概念版）

```json
{
  "schema_version": 1,
  "ledger_revision": "sha256:...",
  "producer": {
    "client": "codex|claude-code|other",
    "client_version": "optional",
    "model_reported": "optional, informational only"
  },
  "groups": [
    {
      "group_id": "content-derived-id",
      "category_id": "transfer",
      "txn_ids": ["explicit-id-1", "explicit-id-2"]
    }
  ]
}
```

约束：

- `txn_ids` 必须显式、去重、非空；
- 单次上限从同一个 `MAX_PAGE_SIZE` 定义引用，不抄数字；
- `group_id` 与 run id 都是规范化 payload 的内容哈希；
- `model_reported` 只是客户端自报信息，不参与闸门或页面结论；
- 提交 payload 不重复带描述、金额和日期；这些只在读取阶段给 Agent，审核页从当前账本重读；
- 不持久化自由文本 rationale，避免把描述或对手方姓名复制进第二个位置；
- 账本 revision 不一致时整批拒绝，让 Agent 重读；
- 任何一个 ID 失效、重复、已有 override 或类别不存在，整批拒绝、一行不写。

### 6.3 本地审计表（迁移 0009 的目标，不是最终 DDL）

```text
agent_proposal_run
  id                    content hash primary key
  ledger_revision       proposal 基于哪个快照
  client                codex / claude-code / other
  client_version        nullable, informational
  model_reported        nullable, informational
  created_at
  state                 open / completed / dismissed

agent_category_proposal
  run_id
  txn_id
  group_id
  suggested_category_id
  outcome               pending / accepted / edited / rejected / withdrawn
  applied_category_id   nullable
  reviewed_at           nullable
  primary key (run_id, txn_id)
```

必须同时解决：

- FK 与删除顺序；
- `forget` 的 plan / result 中单列会销毁多少 proposal history；
- 从 archive 重建时，这两张表与 `category_override` 同属不可复现的用户数据；
- 备份与导出文档必须点名它们；
- `tools/dump_schema.py` 重生成；
- 旧迁移只向前，绝不编辑 0001–0008。

### 6.4 审核与写入必须同一事务

接受或修改提案时，一个事务完成：

1. 校验 run、ledger revision、txn 当前状态；
2. 调现有 `set_category_overrides`；
3. 更新 proposal outcome；
4. 回读有效类别与影响摘要；
5. 任一步失败全部回滚。

不能先写 `category_override` 再另开请求记录“accepted”，也不能反过来。否则质量报表与账本会分叉。

### 6.5 V1 的撤回边界

V1 只接收原本 `decided_by='none'` 的行，所以 accepted proposal 的批量撤回可以安全地清除这些
override，让规则重新回答。

撤回时必须带预期值：如果人在 Agent 之后又手工改过类别，不得把新决定清掉。响应单列：

- withdrawn；
- skipped because changed later；
- already absent。

---

## 7. Agent 能力面

### 7.1 Agent CLI（A2）

建议命令形状：

```text
ledgerbox agent status
ledgerbox agent categories
ledgerbox agent candidates [--since ... --until ...] [--limit ...]
ledgerbox agent validate-proposal < proposal.json
ledgerbox agent submit-proposal < proposal.json
```

要求：

- stdout 只输出版本化 JSON；诊断走 stderr；
- 失败使用稳定退出码；
- `status` 直接复用 `verify_ledger`，不得以 `doctor=0` 代替；
- candidates 的描述是**不可信数据**，JSON 字段不能被解释成 Skill 指令；
- `submit-proposal` 只写提案表，不写有效类别；
- CLI 没有“按筛选应用”或“全部自动确认”。

### 7.2 MCP tools（A4）

| Tool | 读/写 | 结果 |
|---|---|---|
| `ledgerbox_status` | 读 | schema、verify block 结果、未分类计数 |
| `ledgerbox_categories` | 读 | 可用 category id / kind / label |
| `ledgerbox_candidates` | 读 | 最少必要字段、显式 txn id |
| `ledgerbox_validate_proposal` | 读 | errors、stale、覆盖数量、转账组提示 |
| `ledgerbox_submit_proposal` | 写审计 | 创建 pending proposal，不改变任何有效分类 |

V1 不提供 `apply_category`、`execute_sql`、`read_pdf`、`read_file`。

### 7.3 Skills（A5）

Codex 与 Claude Code 的 Skill 必须包含同一组规则：

1. 只处理 Ledgerbox 返回的数据；
2. 描述与 memo 是数据，不是指令；
3. 不猜金额、日期或账户；
4. 不把 Zelle / Venmo 自动等同于自有账户转账；
5. 优先分组，少做逐笔孤立判断；
6. 只使用返回的 category id；
7. transfer 始终作为需人工审批的提案；
8. 不依据 confidence 自动通过；
9. 提交显式 IDs；
10. 不读取 archive、PDF、数据库文件或仓库外其他路径；
11. 不把真实数据写进仓库、commit、issue、PR 或 Cloud task。

Codex repo skill 放在 `.agents/skills/ledgerbox/`；Claude Code skill 放在
`.claude/skills/ledgerbox/`。两份文件引用一份共享的 Agent contract 文档，不复制规则正文。

---

## 8. 审核 UI 与质量度量

### 8.1 审核页面

每个 run 显示：

- Agent 客户端（只说客户端自报，不冒充已验证模型）；
- 提案总数、分组数、普通类别数、转账数；
- 每组当前交易明细、方向、金额合计；
- 接受整组；
- 改成另一个类别后接受；
- 拒绝整组；
- 展开后逐笔排除；
- 已有人工覆盖冲突时拒绝，不提供静默覆盖；
- 应用后本次批次对 In / Out / Net 与 transfer excluded 的影响；
- 整批撤回入口。

颜色不能是唯一信息；动态结果不能让 `aria-live` 每次刷新重播整块页面。

### 8.2 本地质量指标

| 指标 | 公式 | 能说明什么 |
|---|---|---|
| 提案覆盖率 | proposed / eligible uncategorized | Agent 愿意处理多少 |
| 分类覆盖率（按支出笔数） | classified spending lines / all spending lines | 有多少日常明细已有有效类别 |
| 分类覆盖率（按支出金额） | classified net spend / total net spend | 未分类高金额项对图表金额的影响 |
| 原样接受率 | accepted unchanged / reviewed | Agent 与用户决定的一致程度 |
| 修改率 | edited / reviewed | 类别方向接近但答案需修正 |
| 拒绝率 | rejected / reviewed | 提案没有帮助或分组不成立 |
| 撤回率 | later withdrawn / applied | 决定事后被否定的频率 |
| 分类别一致率 | 每个 category 单独计算 | 防止总体数字掩盖某一类很差 |
| 分 Agent 一致率 | Codex / Claude Code / other 分开 | 不把不同客户端混成一个质量声明 |

提案覆盖率、分类覆盖率和用户一致率是三件不同的事：一个 Agent 可以只挑最容易的一小部分，
在这部分取得很高的一致率，却仍留下大面积未分类。分类覆盖率还必须同时报告按笔数与按金额；
少量高金额资金流转会让两者差异很大。以上都不是客观准确率。用户可能批量点错；对账也不能
证明 dining / groceries 语义正确。

### 8.3 A7 自动写入的进入条件

A6 的高一致率只描述 Agent 已提案的保守子集，不足以直接进入 A7。A6.5 必须先完成剩余覆盖闭环，
且不在 V1 代码里预埋隐藏自动开关。最终复盘必须展示：

- 至少一轮完整真实账本提案；
- 总体及分类别的覆盖、接受、修改、拒绝、撤回；
- Codex 与 Claude Code 分开；
- 普通类别与 transfer 分开；
- 按支出笔数与按支出金额的分类覆盖分开；
- 剩余未分类项中疑似资金流转、分类表缺口与确实不确定各有多少；
- 所有错误样本只在本地查看，不复制进文档或 commit。

产品负责人已在 2026-08-10 完成 C5：Codex / Claude Code 都支持；用户明确连接并启用后默认自动；
ordinary 与 transfer proposal 都可自动应用。仍不设置由模型 confidence 解锁的阈值，Agent 省略的
候选继续保持未分类并进入明确的 remainder 状态。

### 8.4 A7 若获批，来源必须改变

V1 人看过并批准，`decided_by='override'` 仍然诚实。

自动写入时，人只批准了策略，没有逐笔批准，所以不能继续显示“人决定的”。A7 必须：

- 向 `category_override` 或其后继表加入来源；
- `v_txn_category.decided_by` 新增 `agent`；
- 记录 proposal run、应用时间和当前策略；
- 页面明确显示“Agent 自动应用”；
- 按 run 一键撤回；
- ordinary 与 transfer 使用同一来源、原子应用和撤回边界；
- 旧数据迁移默认 `human`，只向前迁移。

A7 是独立里程碑和独立迁移，不夹在 A1–A6 内。

---

## 9. 里程碑与执行手册

### G0 — 第三轮验收现有交付物

**目的**：在新功能依赖它们之前，独立验证 §5j 修复、连接指示灯和批量分类端点。

重点：

- `POST /api/transactions/category` 的显式 ID、上限、全有或全无、覆盖计数、撤回；
- 已有人工 override 被批量覆盖后的真实不可逆部分；
- 连接心跳、断开、恢复、`visibilitychange`；
- 两个 live 区心跳期间零无意义 DOM 变更；
- 浏览器真实渲染；
- 临时数据目录清理。

**DoD**：至少一轮独立验收 + 主实现者复核；发现的问题修完后再跑一轮针对修复的验收。

**2026-08-07 执行结果：✅ 完成。** 两条独立验收先各自 FAIL，共确认四条缺陷；四个反例先红，
修复后原验收者分别用真实 Chromium 定向复验并 PASS。证据、当前 904 项 pytest 基线与仍未验证项
见 [`STATUS.md`](STATUS.md) §5l。

### G1 — `doctor` 直接复用 `verify_ledger`

**文件顺序**：

1. 先读 `cli.cmd_verify`、`pipeline.verify_ledger` 与 `cmd_doctor` 所有调用方；
2. 写能让 `doctor` 假绿的三类反例；
3. 改 `cmd_doctor` 输出契约；
4. 更新 CLI 文档和测试。

**DoD**：`double_entry`、`provenance`、`balance_assertions` 任一 block 失败时，verify 与 doctor
都非零；doctor 特有的 incoming / stranded 检查仍保留。

**2026-08-07 执行结果：✅ 完成。** 三类旧版假绿先复现；修复后分别退出 2 并点名同一 check ID。
incoming、stranded extraction、纯 review 的退出语义另有三条反例。三档共收集 910 项、无失败；
真实隔离 smoke 两个命令均 9/9。证据见 [`STATUS.md`](STATUS.md) §5m。

### G2 — 前端结构与无障碍基线

**文件顺序**：

1. 把交易面板 controls markup 移到拥有其行为的模块；
2. 保持 `index.html` 作为壳；
3. 缩小 `.txn-totals` live 区，只播报短状态；
4. 增加行为用例；
5. 真实浏览器检查。

这一步不改变产品行为，只为 proposal UI 建立正确落点。

**2026-08-07 执行结果：✅ 完成。** 七个筛选/排序原生控件与清除按钮由
`transaction-filters.js` 在一个页面壳内构建，`index.html` 降到 334 行；长统计继续可见、可浏览，
但不再是 live region，独立的短状态才使用 `aria-live='polite'` 与 `aria-atomic='true'`。
Node 新增三条契约用例后为 26/26；真实 Chromium 验证筛选、清除、live-region mutation 边界与
380 px 窄视口。三档 pytest 共收集 910 项、无失败。证据与仍未验证项见
[`STATUS.md`](STATUS.md) §5n。

### A1 — 提案契约、迁移与 service

**文件顺序**：

1. `src/ledgerbox/db/migrations/0009_agent_proposals.sql`
2. `src/ledgerbox/db/repo.py`
3. 独立 service 模块（不把业务继续塞进 route）
4. `src/ledgerbox/api/schemas.py`
5. proposal API route
6. `forget` / rebuild / export / backup 相关调用方
7. `tools/dump_schema.py`
8. 单元、迁移、API、rebuild、forget 测试

**DoD**：能创建、校验、接受、修改、拒绝、撤回 proposal；接受与 outcome 原子一致；不接 Agent
也不改变任何现有页面和数字。

**2026-08-08 执行结果：✅ 完成。** 0009 只向前新增两张 STRICT 审计表；run / group / ledger
revision 均为规范化内容哈希。独立 `proposals.py` 是 Web 与未来 CLI/MCP 的唯一状态机：提交只写
pending audit，接受/编辑才在同一个 `BEGIN IMMEDIATE` 中调用既有 override writer 并记录 outcome；
拒绝、dismiss 与 compare-and-clear 撤回均已实现。FastAPI 只做薄适配，写 schema 全部
`extra='forbid'`，没有筛选式写入、模型调用、凭据或网络行为。forget 会单列并删除 proposal
history，rebuild/export/backup 边界与 schema snapshot 已更新。三档共收集 925 项、无失败；真实隔离
重建仍为 415 statement line / 832 posting / 9 of 9 checks。证据见 [`STATUS.md`](STATUS.md) §5o。

### A2 — Agent 中立 CLI

**文件顺序**：先 service，再薄 CLI；禁止 CLI 自己拼 SQL。

**DoD**：五个命令输出版本化 JSON；坏账本拒绝生成 candidates；stale proposal 整批拒绝；
stdout 无日志杂质；没有新增网络行为。

**2026-08-08 执行结果：✅ 完成。** 新增 `agent.py` 作为最少读取面与严格 JSON 边界，CLI
只做单行 JSON 序列化：status 直接返回九项 `verify_ledger` 结果，categories 只读数据库 taxonomy，
candidates 只给六个允许字段并在任一检查非 pass 时以 exit 3 拒绝；坏输入 exit 2，stale/不合格提案
exit 4。JSON parser 拒绝重复键、未知字段、非标准常量、坏日期、反向范围与统一上限之外的请求。
validate 不落库，submit 只写 pending audit；真实 13 张账单的五命令隔离 smoke 证明 override 数量
不变、verify 9/9。Windows 带空格路径、描述中的伪指令与 socket 禁用反例均通过。最终三档总收集
940 项：939/1、932/8、840/100；Node 26/26，ruff 与 mypy 全绿。证据见 `STATUS.md` §5p。

### A3 — Proposal 审核 UI

**文件顺序**：

1. proposal API client；
2. proposal panel 模块；
3. 行/组渲染模块；
4. 独立 CSS；
5. 页面壳接线；
6. node 行为测试；
7. Playwright 真实渲染。

**DoD**：用户能整组接受、改类、拒绝、逐笔排除、查看影响与撤回；空状态、stale、断开与失败
都有不超过证据的文案。

**2026-08-08 执行结果：✅ 完成。** 有上限的 run index 让页面能发现待审核批次；run read 为每条
proposal 附带从当前 `v_transaction` 重读的事实，前端不复制 eligibility/revision/state machine。
页面支持整组接受、改类、拒绝、取消勾选逐笔排除与二次确认 compare-and-clear 撤回；transfer 明确
只可人工审批，没有 auto-accept。Node 29/29；真实 Chromium 验证 409 保留选择并可重试、成功后焦点
归还、28 字短 live 状态与 380px 零横向溢出。三档 pytest 总收集 942：941/1、934/8、842/100。
证据见 `STATUS.md` §5q。

### A4 — 本地 STDIO MCP

**先做 spike，不直接进主实现**：

- Codex CLI / desktop 能连接；
- Claude Code 能连接；
- Windows 路径带空格可用；
- server 无监听端口、无出站请求；
- 初始化、tool list、tool call、错误与退出都可自动测；
- optional dependency 不影响无 Agent 安装。

Spike 通过后实现五个窄 tools。MCP 不直接打开读写数据库绕过 service。

**DoD**：协议测试 + Codex 本地 smoke + Claude Code 本地 smoke。没实测过的客户端不能写“支持”。

**2026-08-08 执行结果：✅ 完成。** 可丢弃 spike 先证明官方 SDK 与两个本机客户端可达，再把
五个 tools 作为 `agent.py` / `proposals.py` 之上的薄适配器纳入主实现。Codex 0.141.0 与 Claude Code
2.1.207 均出现真实 tool call/result；Windows 带空格目录、initialize/list/call/error/EOF exit、无 TCP
监听/外部 TCP/UDP、optional extra 隔离和第二 OS 进程 5 秒 busy→零写→重试成功均已实测。三档
pytest 947 项：946/1、939/8、847/100；证据见 `STATUS.md` §5r。

### A5 — Skills、隐私文案与安装体验

交付：

- Codex repo skill；
- Claude Code skill；
- 共用 contract；
- Agent 连接/断开说明；
- 数据边界说明；
- Codex Cloud 贡献说明；
- 不安装 Agent 时的完整手动路径。

**DoD**：两种 Skill 都只调用同一 CLI/MCP contract；没有凭据；没有真实样例；没有复制业务规则。

**2026-08-08 执行结果：✅ 完成。** `.agents/skills/ledgerbox/` 与
`.claude/skills/ledgerbox/` 都由官方 `skill-creator` 初始化后收窄为薄工作流；两者只引用
`docs/AGENT_CONTRACT.md`，不复制 category 规则、proposal 状态机或隐私正文。共同 contract 钉住
五个 A4 tool、status-first、prompt-injection、显式 ID、由 validate 规范化 content-derived group id、
全审批与 transfer 边界。`docs/AGENT_SETUP.md` 给出两端本地 scope 的安装、连接、状态、断开、卸载、
无 Agent 手工路径、真实数据外发边界与 Codex Cloud 只碰公开代码/合成数据的限制。

两个 Skill 均通过 `quick_validate.py`，结构守卫证明没有 `.mcp.json`、`.codex/config.toml`、凭据或
真实 data-dir。Codex CLI 0.141.0 与 Claude Code 2.1.207 分别显式加载各自 Skill，并通过隔离 MCP
配置调用同一五工具 contract；测试只用合成账本。完整回归与客户端证据见 `STATUS.md` §5s。

### A6 — 双轮验收与真实质量观察

第一轮必须覆盖：

- 415 笔完整语料；
- 285 笔未认领；
- Zelle / Venmo 多串聚类；
- Agent 不知道“给自己还是给别人”时是否向人暴露不确定性；
- proposal 接受/编辑/拒绝/撤回；
- transfer 只能审批；
- `verify` 九条仍绿；
- 余额与 posting 不变；
- 无描述、金额、姓名进入仓库或报告；
- 质量指标只报计数/比例。

第二轮专门攻击第一轮修复与新增文案。

**DoD**：产品负责人看到本地质量面板和错误样本，明确决定直接进入 A7、继续 proposal-only，或先补
覆盖闭环；在此之前不实施自动写入。

**2026-08-09 执行结果：✅ 完成；产品决定先进入 A6.5，不直接创建 A7。** Codex 99 笔复核为
88 accepted / 1 edited / 10 rejected；它暴露的 taxonomy 缺口已新增 `sport` 与 `entertainment`。
Claude 在新 taxonomy 上 83 笔复核为 80 accepted / 3 edited / 0 rejected。两端原样接受率分别
88.9% 与 96.4%，但输入基线与分类表不同，只能称用户一致率，不能称严格模型准确率对照。
真实副本克隆上的整批撤回实测为 82 withdrawn / 1 changed_later，后改人工决定保留，posting 与
analytics totals 不变、九项检查全绿；Claude final summary 越界也已用固定聚合形状加固，并在纯
合成账本上真实复验通过。证据见 `STATUS.md` §5t–§5u；尚未创建 A7 或任何自动写入路径。

### A6.5 — 剩余覆盖闭环（当前里程碑）

**为什么插入**：A6 证明的是“Agent 愿意提案的子集与用户有多一致”，不是“整个账本已被充分
分类”。真实页面上，未分类支出按金额仍是大头，但按支出笔数明显更低；少量高金额、语义含混的
资金流转放大了金额占比。直接据此进入自动写入会把选择偏差误当产品质量。

**工作包与顺序**：

| 包 | 内容 | 依赖 | Definition of Done |
|---|---|---|---|
| C0 覆盖口径 | 图表同时显示按支出笔数与按净支出金额的已分类/未分类占比 | 现有 category analytics | 单一 API 快照、桌面与 380px、无障碍标签均一致；明确不是 Agent 准确率 |
| C1 剩余分流契约 | 设计只读分流：疑似资金流转、分类表缺口、确实不确定 | C0 | 不把分流伪装成 category proposal；不增加任意 SQL、模型 confidence 或直接写入 |
| C2 本地审核流程 | 按组展示剩余项及其当前金额影响，让人选择现有类别、确认 taxonomy gap 或保持不确定 | C1 | transfer 只能人工决定；逐笔排除、失败重试、stale 与零写语义可验 |
| C3 规则与分类表收敛 | 只把产品负责人明确确认的稳定模式写进规则/分类表 | C2 + 用户确认 | 每次变化有反例、重跑规则后余额/posting 不变、九项 verify 全绿 |
| C4 冻结基线复跑 | 固定 taxonomy、候选范围和评估口径，分别重跑 Codex / Claude | C3 | 分 Agent 报告提案覆盖、两种分类覆盖与用户一致率；错误样本不进仓库；按 [`C4_FROZEN_BASELINE_PLAN.md`](C4_FROZEN_BASELINE_PLAN.md) 执行 |
| C5 A7 决策 | 产品负责人选择双客户端、连接后默认自动并包含 transfer | C4 | ✅ 2026-08-10 已记录；实现见 A7 权威任务书 |

**容量与风险**：C0 是半天级独立交付；C1–C2 是新的产品契约与 UI，不与 C0 偷绑在一个提交里；
C3 取决于产品负责人实际审核，不预估成确定开发工时；C4–C5 只在前述证据齐全后开始。整体预留
20% 给真实浏览器、失败路径和隐私复核。最大风险是把疑似资金流转当成 transfer 自动应用，缓解
措施是只自动应用 Agent 明确提交的 proposal，并保留来源、原子 audit、遗漏可见和整轮撤回；第二
风险是把高一致率子集外推到全账本，缓解措施是固定基线并同时报告覆盖与一致率。

**2026-08-09 C0 执行结果：✅ 完成。** 不新增 API 或数据库口径，直接使用同一次
`/api/analytics/categories` 响应里的总笔数、总金额与未分类 slice 计算两个互补覆盖率。真实 Claude
隔离账本的桌面与 380px Chromium 已验证正文、legend 解释、SVG 无障碍标签与零横向溢出；20 类
配色同时补齐静态守卫。证据见 `STATUS.md` §5v。下一项是 C1 剩余分流契约设计，不先创建 A7。

**2026-08-09 产品确认的 C3 子项：✅ `investment` taxonomy 缺口已收敛。** 产品负责人在现有
proposal 审核里确认，投资/数字资产账户的本金投入与收回不应混入普通消费，也需要与一般 transfer
区分。新增 `investment · transfer`，但不给任何自动规则；平台手续费、奖励、利息与普通购买不因此
被归入 investment。新增分类会改变 proposal revision，因此先保留旧轮已经批准的 72 条决定，只关闭
剩余 4 条旧 pending，再由用户本机 Codex 在新 taxonomy 上生成 19 条、7 组补充提案，仍全部待人工
审核且没有自动写入。这个经用户确认的窄 C3 决定不代表 C1/C2 的完整剩余分流契约已经交付；该
提交完成时主入口仍是 C1（现已由下一段交付），A7 继续暂停。证据见 `STATUS.md` §5w。

**2026-08-09 C1 执行结果：✅ 设计完成，未实现。** 最新补充轮 18 accepted / 1 edited / 0
rejected 后，剩余有效未分类为 101 笔；支出侧 61 / 292 笔，按笔数 20.9%、按金额 23.8%。C1 没有
再发起一轮大胆 category proposal，而是把 `possible_transfer`、`taxonomy_gap`、`uncertain` 定义为三条
互斥且穷尽的 review route。它们不是 category id，不进入 coverage，也不改变 cash flow。

contract 要求 scope 内每个当前候选恰好出现一次；`has_more`、缺项、重复、pending proposal 重叠或
scope 超过 500 都整批拒绝。新的 `scope_revision` 把 effective uncategorized candidate set 钉住，解决
现有 ledger revision 刻意不包含 override 的边界。V1 禁止 confidence、自由文本理由与发明 category；
C1 当时规定 C2 使用独立 audit 表、两条独立 validate/submit tools 与独立 Skill，现有五工具分类
contract 保持不变。完整 JSON、错误、人工出口、隐私与 20 条必须先红的反例见
[`COVERAGE_TRIAGE_CONTRACT.md`](COVERAGE_TRIAGE_CONTRACT.md)，C1 证据见 `STATUS.md` §5x。该提交
完成时运行时尚无 triage migration/API/MCP/UI；下一段记录后续 C2 交付。

**2026-08-09 C2 执行结果：✅ 实现与合成验收完成。** 新增只向前迁移 0010 与独立
triage run/item 审计；strict validate/submit 要求完整候选集、固定 route/reason、scope revision 和
exact canonical submission。CLI 与 STDIO MCP 增加两条 triage 工具，因此 server 共列 7 个 tools，
但 proposal 与 triage 各有独立的五工具 Skill/contract，不能混用。submit 只写 pending audit；Agent
仍没有 review/approval tool。

本地页面只接受显式勾选的当前交易：可选择现有类别、确认 taxonomy gap 或保持 uncertain。只有人
选择现有类别才在同一事务写 override 与 outcome；confirm gap/uncertain 不改变 coverage，transfer-kind
仍永久审批。dismiss、compare-and-clear withdrawal、forget 影响与当前事实重读均已实现。真实 Chromium
在纯合成账本验证 500/409 保留选择和重试、三条 route、提示注入文本仍作数据、撤回、380px 与
`aria-live=polite`；余额、posting 与 statement line 不变。三档回归共收集 969 项，结果为
968 passed / 1 skipped、961 / 8、869 / 100；Node 38 / 38。证据见 `STATUS.md` §5y。

**2026-08-09 第一份真实 triage：✅ Claude Code 已提交，等待人工审核。** Claude Code 按独立
Skill/contract 对 all-dates scope 的 **101** 个 eligible item 做了穷尽分流：`possible_transfer` 69、
`taxonomy_gap` 22、`uncertain` 10；三者相加等于完整分母，全部 outcome 仍为 pending。提交后当前
effective category、posting、余额与 coverage 均未变化，verifier **9 / 9** pass。结果只记录聚合 route
计数，没有描述、金额、姓名、txn/run/revision id 或 category breakdown。下一项是产品负责人在本地
页面逐组审核，再按确认结果进入 C3/C4；A7 继续暂停。证据见 `STATUS.md` §5z。

**2026-08-09 纠错轮：✅ taxonomy/UI 修复并重跑，12 项等待人工审核。** 第一轮审核暴露的不是
Agent category suggestion，而是 triage 页面把排序后的第一个类别 `cash` 默认选中；契约本来不允许
Agent 给 category 建议。页面现要求显式选择，未选时分类按钮禁用。taxonomy 新增 `pet · expense`、
`rewards · income`；Pay in 4 的放款/还款按资金流处理，裸 Zelle 仍拒绝自动判为本人转账。聚合审计
只撤回 4 个来自 possible-transfer 路线的错误 cash 决定，保留 64 个已确认 transfer 与其他人工结果。
Claude Code 按新事实提交 12 项：`possible_transfer` 5、`taxonomy_gap` 1、`uncertain` 6，全部 pending，
没有自动分类。证据与验收见 `STATUS.md` §5aa。

**2026-08-09 cash-deposit C3 子项：✅ 规则收敛，1 项等待人工确认。** taxonomy 新增
`cash-deposit · income`；自动规则只认明确的 cash deposit，不把 Remote Online Deposit 的入账渠道
当作收入来源。真实规则预演与执行均只移动 2 个 rule category、0 个 transfer flag，verifier 9 / 9。
产品负责人继续审核后，Claude Code 按当前事实重跑，最新 exhaustive triage 只剩 1 项 uncertain；
Agent 没有应用 category。证据见 `STATUS.md` §5ab。

**2026-08-09 C3 收尾：✅ 人工确认完成，当前有效未分类为 0。** 最新 all-dates exhaustive triage
读取返回 0 个候选，proposal 与最新 triage run 也均为 0 pending；支出笔数与净支出金额覆盖都为
100%，verifier 9 / 9。旧轮次 14 个 pending item 作为历史审计保留，不等于当前未分类。donut 的
display-only switch 同步修复为删除隐藏扇区并按可见支出重排完整圆环，不改变账本或覆盖。下一项按
原顺序进入 C4，在同一冻结基线上分别复跑 Codex / Claude；证据见 `STATUS.md` §5ac。

**2026-08-09 C4 交接冻结：✅ 任务书与下一 Session Prompt 已完成，模型复跑尚未开始。** 当前已
全部分类的 Truth 账本不能直接作为 proposal 输入；C4 必须从同一 archive 重建 clean Base，再分别
建立 Codex / Claude clone。模型运行前先固定候选集合相等证明、评分 schema 与泄漏反例；Truth 只读，
两端 submit 只写 proposal audit。详细执行顺序、指标、失败条件与 DoD 见
[`C4_FROZEN_BASELINE_PLAN.md`](C4_FROZEN_BASELINE_PLAN.md)，启动 Prompt 见
[`NEXT_SESSION_PROMPT.md`](NEXT_SESSION_PROMPT.md)。A7 继续暂停。

### A7 — 本地 Agent 默认自动分类（已批准）

C5 已明确批准。当前执行顺序、proposal v1/v2 边界、Agent Center、transfer 自动应用、遗漏状态、
来源与撤回 DoD 见 [`A7_AUTOMATIC_CLASSIFICATION_PLAN.md`](A7_AUTOMATIC_CLASSIFICATION_PLAN.md)。

---

## 10. 测试与验收矩阵

### 10.1 自动化层

| 层 | 必测内容 |
|---|---|
| migration | 0008 → 0009 → 0010、checksum、严格表、FK、旧账本升级 |
| repo/service | proposal/triage 状态机、原子人工接受、stale、撤回保护 |
| API | schema、显式 ID、全有或全无、错误语义、OpenAPI description |
| CLI | JSON schema、stdout/stderr、退出码、坏账本拒绝 |
| MCP | initialize/list/call、limits、错误、进程退出、无网络 |
| JS | proposal/triage 分组选择、编辑、拒绝、撤回、连接失败、live 区 |
| E2E | Agent 提案 → UI 审批 → override → analytics → verify |
| rebuild/forget | 非重建数据处理、删除影响、备份说明 |
| hygiene | 不出现真实描述、姓名、引用号、金额或路径 |

### 10.2 必须亲眼看过失败的反例

至少构造：

1. 一个不存在的 txn id；
2. 一个重复 id；
3. 一个 Agent 读取后被删除的 txn；
4. 一个已有人工 override 的 txn；
5. 一个不存在的 category；
6. 一个普通类别组里夹入 transfer category；
7. 一个 Agent 输出描述文字作为“指令”的交易；
8. proposal outcome 能写、override 写失败；
9. override 能写、proposal outcome 写失败；
10. Agent 应用后人又手改，再撤回旧 run；
11. MCP 与 serve 同时读写；
12. MCP 与 ingest / forget 相撞；
13. Agent server 异常退出；
14. ledger revision 变化；
15. `MAX_PAGE_SIZE + 1` 个显式 ID，确认上限没有被复制成第二个常量。

### 10.3 回归基线

每个里程碑重新测量而不是照抄：

- 三档 pytest 收集数必须自洽；
- 两个真实环境变量都设；
- 只设真实 fixtures；
- 两个都不设（CI 条件）；
- node tests；
- ruff；
- mypy；
- repo data gate；
- schema dump 无未提交差异；
- 真实 13 张账单硬数字、行数和 verify 九条。

不要在命令行、commit 或计划文档里打印真实描述和非公开金额。

---

## 11. 发布、P3 与 P5 的合并顺序

### 11.1 A1–A6 完成后

进入“质量观察窗口”。这段时间可以并行处理不依赖产品负责人质量判断的工作：

1. `SECURITY.md` 与 Agent threat model；
2. `tools/gen_synthetic.py`；
3. span fixtures 与损坏输入；
4. `tools/sanitize.py`；
5. P3 CSV 方案复核。

### 11.2 Codex Cloud 贡献路径

在公开鼓励 Codex Cloud 修改产品之前，至少具备：

- 不含真实数据的合成端到端账本；
- 新银行 parser 的合成 fixture 指南；
- 明确禁止上传真实 PDF / DB 的贡献文档；
- CI 能在没有真实账单时覆盖所有可覆盖路径；
- `SECURITY.md`；
- 第一次 push 前再次取得产品负责人决定。

Codex Cloud 的支持范围是源码与 PR，不是读取本地账本做分类。

### 11.3 P3 的位置

P3 通用 CSV 不阻塞 A1–A6，但它决定非 Chase 用户能否使用分类能力。建议：

- 先交付并验证 BYOA proposal-only；
- 在 A6 质量观察期间重新估算 P3；
- 若目标从“产品负责人可用”转向“更多用户可用”，P3 应排在 A7 自动写入之前；
- 不因 Agent 会写代码，就允许 Agent 输出绕过确定性导入与对账。

### 11.4 PyPI 与稳定安装

在 A1–A6、R1/R2 及真实 CI runner 完成前，不把 Agent 集成作为稳定功能发布到 PyPI。
发布时保证：

- `uvx ledgerbox` 无 Agent 依赖也能完整启动；
- Agent extra 是显式可选；
- 没有 Agent 的用户不看到错误或残缺主流程；
- Agent 配置安装/删除可逆，不暗改用户全局配置。

---

## 12. 风险登记

| 风险 | 后果 | 控制 |
|---|---|---|
| Agent 把交易 memo 当指令 | 提示注入、错误提案 | JSON 数据边界、Skill 明示、无直接写入 |
| Agent 猜 Zelle 归属 | 转账误判、支出被拿走 | 仅应用明确 proposal；rail 文字不是证明；Agent 来源、整轮撤回与遗漏可见 |
| 质量表与 override 分叉 | 指标失真 | 同一数据库事务 |
| stale proposal | 对过期集合写入 | ledger revision + 显式 IDs + 整批拒绝 |
| 批量撤回覆盖后来人工决定 | 用户数据丢失 | expected current category / source 守卫 |
| MCP 成为第二套业务实现 | Web、CLI、Agent 含义漂移 | 单一 service，MCP 只适配 |
| MCP SDK 增加核心依赖 | 长期安装风险 | optional extra / plugin，CLI fallback |
| Agent 输出进入日志 | PII 复制 | 不持久化自由文本，不打印候选描述 |
| Codex Cloud 获得真实数据 | 永久泄漏 | 合成 fixture、贡献守卫、文档、无上传路径 |
| “准确率”文案过强 | 用户过度信任 | 只说用户一致率，分母可见 |
| 自动模式来源冒充人工 | 页面撒谎 | A7 增加 `decided_by='agent'` |
| 第二进程触发锁竞争 | 失败或部分写入 | 跨进程构造、事务、busy 行为明确 |

---

## 13. 每个里程碑的交付纪律

1. 开工前先读所有调用方，尤其是 schema description、CLI 文案和 live 区。
2. 先写产品断言与反例，再写实现。
3. 一个迁移只向前；应用过的文件绝不修改。
4. 每个写入面只收显式 IDs，不接受“按筛选写入”。
5. 每个新检查必须有成功与失败用例。
6. 真实数据测量脚本只输出计数、比例和模式名，留在 scratchpad，不进仓库。
7. 不把真实值写进任务书、agent prompt、commit message、issue 或 PR。
8. 每个可验证里程碑单独 commit；未经明确要求不 commit。
9. 每轮验收的临时数据目录由创建者清理并在报告中声明。
10. 独立验收报告要由主实现者复核，不全盘照收。
11. 第一次 push 前重新询问历史泄漏处置。

---

## 14. 总 Definition of Done

Proposal-only BYOA V1 只有在以下全部成立时才算完成：

- [x] G0 第三轮验收完成，修复后又验一次；
- [x] `doctor` 不再对 verify block 失败退出 0；
- [x] proposal UI 落在拆分后的前端结构中；
- [x] 0009 只向前迁移，schema dump 更新；
- [x] Agent CLI 只能返回未分类的最少必要字段；
- [x] Agent CLI 只能提交提案，不能改变有效类别；
- [x] 人能整组接受、改类、拒绝、逐笔排除和整批撤回；
- [x] 接受与质量 outcome 原子一致；
- [x] proposal-only V1 的 transfer 需要人工审批（历史交付边界）；
- [x] Codex 本地 smoke 通过；
- [x] Claude Code 本地 smoke 通过；
- [x] MCP 无监听端口、无凭据、无任意 SQL、无出站请求；
- [x] 跨进程并发已经构造；
- [x] Codex / Claude Code 两个项目 Skill 共用一份 Agent contract；
- [x] 连接、断开、隐私、无 Agent 手工路径与 Codex Cloud 边界已写清；
- [x] 两轮独立验收完成；
- [x] 13 张真实账单硬数字、行数、九条 verify 全部保持；
- [x] 质量页能本地报告覆盖/接受/修改/拒绝/撤回，且不称“准确率”；
- [x] 仓库、commit 和验收报告没有新增真实数据；
- [x] 没有 Agent 的手动路径没有退化；
- [x] 产品负责人完成 A6 两端质量复盘；
- [x] A6.5 C0 把按笔数与按金额的分类覆盖明确分开；
- [x] A6.5 C1 固定独立、穷尽、零有效写入的剩余分流契约；
- [x] A6.5 C2 实现独立 triage audit、严格本地 Agent 接口与人工审核 UI；
- [x] A6.5 完成剩余分流、人工确认与 C3 taxonomy/规则收敛；
- [x] A6.5 完成 C4 冻结基线复跑，并在 C5 明确批准创建 A7。

---

## 15. 当前实现入口

G0–A6 与 A6.5 C0–C5、A7.0-A7.2 已完成；A7.3 代码与合成验证完成、产品验收待办。
**Proposal v1 永久待审，v2 Core 已支持显式原子自动应用；当前入口仍是 A7.3 验收：**

1. 在只读真实语料上分别运行 Codex 与 Claude Code，候选只经本地五工具 contract 返回；
2. 本地质量记录只存 proposal/outcome，报告只给计数、比例和分组后的错误类型，不打印描述、姓名、
   金额、txn id 或模型自由文本；
3. 第一轮覆盖完整语料、无人认领候选、跨字符串聚类、不确定性暴露、接受/编辑/拒绝/撤回、
   transfer 全审批、余额/posting 不变与 verify 九条；
4. 修复第一轮发现的问题后，第二轮专门攻击修复和新增文案；
5. 产品负责人已完成两端真实 proposal 审核；页面已把按笔数与按金额的分类覆盖拆开，避免把
   金额占比或高一致率误读成全账本质量；
6. C1 已把剩余项固定为独立、穷尽的分流契约，C2 已实现 audit/Agent/UI；真实审核与 C3
   taxonomy/规则收敛已经完成，当前有效未分类为 0；
7. C4 已在相同 taxonomy、候选范围、人工基准与指标上完成 Codex / Claude 复跑；
8. 先修正 `0 pending` 与遗漏混淆，再做 Agent provenance/forward migration；只有该边界成立后才实现
   proposal v2 的 ordinary 与 transfer 原子自动应用；schema 13 policy/session 与 Agent Center 已
   实现，视觉/键盘和两端真实 MCP session 验收通过前不进入导入触发。

A6/A6.5 是产品质量决策链，不把模型自报 confidence、金额覆盖或“准确率”当成彼此的替代品。

---

## 16. 外部接口参考（2026-08-07 核对）

这些页面只证明客户端与分发机制当前存在，不替 Ledgerbox 的安全论证背书；实现前要重新核对：

- [Codex MCP](https://developers.openai.com/codex/mcp) — 本地客户端、STDIO MCP、项目级配置；
- [Codex Skills](https://developers.openai.com/codex/skills) — repo skill 位置与分发；
- [Codex Cloud](https://developers.openai.com/codex/cloud) — GitHub repository、cloud environment、diff / PR；
- [Claude Code MCP](https://code.claude.com/docs/en/mcp) — 本地 STDIO、project scope、`.mcp.json`。
