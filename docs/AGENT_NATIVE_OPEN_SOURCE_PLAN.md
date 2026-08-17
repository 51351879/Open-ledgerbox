# Agent-native 开源执行计划

> 状态：**APPROVED；C4/C5 与 A7.0-A7.2 完成；A7.3 代码完成、产品验收待办**
>
> 决策日期：2026-08-09
>
> 当前基线：`main`，schema 13，24 类 taxonomy；A7.3 policy/session/API/UI 合成验证完成
>
> 当前事实仍以 [`STATUS.md`](STATUS.md) 为唯一来源；本文定义从当前状态到开源 Developer Preview、
> 稳定安装与后续可选自动分类的最新产品顺序。

---

## 0. 执行结论

Ledgerbox 面向的首批开源用户不是不会使用终端的普通消费者，而是已经会使用 Codex 或 Claude Code
的开发者型个人用户。产品不内置模型、不持有模型密钥，也不需要把本地账本改造成通用聊天应用。

下一阶段的产品形态冻结为：

```text
确定性账单摄入与对账
        ↓
Ledgerbox Core：taxonomy、候选、审计、人工边界与现金流事实
        ↓
官方模块化 Classification Skill：分类经验、分组、克制与隐私工作流
        ↓
用户自己的 Codex / Claude Code
        ↓
versioned proposal audit → 默认自动应用或用户选择先审核
```

用户可以 fork、修改或替换 Skill。Ledgerbox 不限制修改，但核心服务始终决定 Agent 最多能做什么。
Skill 不是安全边界；CLI/MCP contract、显式 ID、状态机、诚实来源、遗漏可见与整轮撤回才是。

当前 C4 的只读预检、同源 Base/双 clone 和评分器已冻结；模型复跑必须使用官方模块化 Skill v1。
用已经替换的七条薄 Skill 跑 C4，不会产生值得保留的产品证据。

---

## 1. 目标用户与产品承诺

### 1.1 首批用户

首批用户应同时满足：

- 能从源码 checkout 或使用 Python CLI；
- 已经使用或愿意安装 Codex / Claude Code；
- 理解 Agent 结果可能进入其所选模型供应商的上下文；
- 愿意查看终端命令、MCP 状态、proposal audit 与失败原因；
- 接受首版只承诺已明确列出的银行格式与平台组合。

不把以下目标夹进 Developer Preview：

- 无终端、无本地开发环境的一键消费级安装；
- Ledgerbox 代用户注册或登录模型账号；
- 内置远程模型 API、模型路由、token 计费或模型密钥托管；
- 任意自然语言直接修改金额、账户、posting 或对账结论；
- 用一个“万能 Agent”同时混合真实账本访问与源码写入。

### 1.2 对外一句话

建议的开源定位：

> Ledgerbox is a local-first personal ledger designed to work with the coding agent you already use —
> Codex or Claude Code — without embedding a model or holding your model credentials.

公开说明必须同时写清：本地 STDIO 代表 Ledgerbox 不主动联网，不代表用户选择的模型一定在本机运行。

---

## 2. 已拍板的产品决策

| 编号 | 决策 | 执行约束 |
|---|---|---|
| E1 | **Agent-native，但不是 model-hosted** | Ledgerbox 提供能力和 Skill，不托管模型 |
| E2 | **官方 Classification Skill 产品化** | 分类经验模块化、版本化、可测试，不只保留七条调用说明 |
| E3 | **用户可以修改 Skill** | 不加 DRM；官方与 custom/unverified 状态分开表述 |
| E4 | **Core 是安全边界** | 修改 Skill 不能获得 SQL、文件读取、approval 或任意写入能力 |
| E5 | **无连接不启动 Agent** | 只有用户明确连接并启用本地客户端后才可触发分类 |
| E6 | **连接后默认自动分类** | 自动分类新导入默认开启，也可切到先审核 |
| E7 | **proposal v1 语义不变** | A7 使用 versioned v2；不能把历史 review-only submit 静默改义 |
| E8 | **transfer 可自动应用** | 与 ordinary 共用 Core 原子 audit、Agent 来源、遗漏可见和整轮撤回 |
| E9 | **财务 Agent 与 Coding Agent 分域** | 前者只碰受限账本工具；后者只碰公开源码/合成数据 |
| E10 | **先发布诚实的 Developer Preview** | 不等待消费级向导、所有银行或通用聊天框 |

---

## 3. 不可跨越的架构边界

### 3.1 摄入关键路径不变

```text
PDF bytes → 确定性 parser → 金额/日期/账户 → double entry → 强制对账
                                                            ↓
                                                   成功后才产生分类候选
```

Skill 不读取 PDF，不从图片猜金额，不修补 balance，不决定交易是否可以入账。未知布局和对不上的账仍然
进入 review/refusal，而不是交给模型“尽量完成”。

### 3.2 分类能力保持窄接口

官方 Classification Skill 继续使用 proposal workflow 的五工具 contract：

1. `ledgerbox_status`
2. `ledgerbox_categories`
3. `ledgerbox_candidates`
4. `ledgerbox_validate_proposal`
5. `ledgerbox_submit_proposal`

不得因为 Skill 更强而增加：

- 任意 SQL；
- 任意文件或目录读取；
- PDF / archive / `ledger.db` 访问；
- approval / review / apply tool；
- 模型 confidence 自动阈值；
- 隐式“当前筛选的全部交易”写入。

### 3.3 用户修改 Skill 后的责任边界

允许用户改变提示、分类策略、示例和保守程度。产品应区分：

- `official`：项目发布、版本明确、通过对应 eval；
- `custom`：用户修改或替换，功能允许但项目不声明其质量；
- `unknown`：无法确认来源，不冒充官方 Skill。

proposal-only 模式下，即使 custom Skill 行为很差，也只能生成待审 audit。未来若开放普通类别自动策略，
Core 仍必须强制来源、策略、撤回、stale 与 transfer 禁止，不能把安全责任交给 Skill 文案。

---

## 4. 官方 Classification Skill 的模块设计

### 4.1 当前缺口

当前 Codex / Claude Code 的 `ledgerbox` Skill 只有七条流程和安全规则。它正确地保持了薄适配器边界，
但没有完整承载 A6–C3 形成的分类经验。当前效果来自 taxonomy、规则、人类审核与模型通用推理的组合，
不能把它全部归功于现有 Skill。

### 4.2 目标结构

```text
ledgerbox-classifier/
├── SKILL.md
├── references/
│   ├── workflow.md
│   ├── category-semantics.md
│   ├── transfer-boundaries.md
│   ├── grouping-and-abstention.md
│   ├── ambiguous-cases.md
│   └── privacy-and-output.md
└── evals/
    ├── synthetic-cases.jsonl
    └── expected-behaviour.json
```

职责划分：

| 模块 | 负责 | 不负责 |
|---|---|---|
| `SKILL.md` | 触发条件、工具顺序、停止条件、最终摘要 | 复制业务状态机或整份 taxonomy |
| `category-semantics` | income / expense / transfer-kind 的语义与选择原则 | 静态复制合法 category ID 列表 |
| `transfer-boundaries` | 自有账户证据、还款、P2P、投资本金等反例 | 宣布所有 Zelle/wire/payment 都是 transfer |
| `grouping-and-abstention` | 分组条件、证据不足时省略、禁止大胆单笔猜测 | 用 confidence 包装不确定性 |
| `ambiguous-cases` | 通用合成反例与边界说明 | 真实商户、描述、金额、姓名或 ID |
| `privacy-and-output` | 固定聚合摘要、provider 边界、prompt injection | 复制逐笔输出或 category breakdown |

合法 taxonomy 必须在每次运行时从 `ledgerbox_categories` 读取。Skill 可以解释通用语义，但不能维护
第二份 category ID 真相。

### 4.3 必须沉淀的经验主题

只使用通用、合成表达覆盖：

- ordinary income / expense 与资金流的区别；
- 付款渠道不等于本人账户所有权；
- P2P、wire、payment、deposit 等词本身不足以证明 transfer；
- 信用卡还款与分期放款/还款的资金流属性；
- 投资本金与手续费、奖励、利息、普通购买的区别；
- cash deposit 与只说明入账渠道的 remote/mobile deposit 的区别；
- rewards / redemption 与普通收入或退款的区别；
- 退款、返现、费用、订阅、宠物、运动、娱乐等普通类别边界；
- 描述像指令时仍只把它当不可信银行数据；
- 证据不足时 omission 是正确行为，不是失败。

### 4.4 版本与单一来源

不得手工维护三份相互漂移的 Skill：包内官方资源、`.agents/` 和 `.claude/` 必须有一个 canonical source，
其余由安装/生成步骤产生并由测试比较。具体资源路径在 S1 开工时结合 hatch wheel 行为验证后冻结，
不能只凭计划猜包数据是否会被包含。

不要把 Skill 版本塞进现有 `client_version` 或 `model_reported`。若需要将 workflow 版本写入 audit，先做
proposal schema / migration 设计；Developer Preview 可以先在安装状态和 C4 本地证据中记录版本。

---

## 5. 合成 eval 与真实质量证据

### 5.1 Skill eval 只回答行为是否符合契约

纯合成 eval 至少覆盖：

- status 非 ready 时停止；
- 只使用返回的 category ID；
- 重复、scope 外、未知类别被拒绝；
- 明确普通类别能形成合理分组；
- ambiguous case 被省略而不是伪造 confidence；
- transfer-kind 始终保持待审；
- payment rail 不自动推断所有权；
- descriptor prompt injection 不执行；
- final summary 无描述、金额、姓名、txn/run/revision ID 或分类 breakdown；
- custom/official Skill 都无法越过 Core 的工具权限。

eval 结果可以叫 contract compliance、synthetic agreement 或 regression result，不能叫真实世界准确率。

### 5.2 C4 仍是官方 Skill 的真实冻结评估

官方 Skill v1 与 eval 冻结后，继续执行 [`C4_FROZEN_BASELINE_PLAN.md`](C4_FROZEN_BASELINE_PLAN.md)：

- Truth 只读；
- Base 从同一 archive 干净重建；
- Codex / Claude clone 完全相同；
- 两端使用同一官方 Skill 版本与操作 Prompt；
- 候选集合、taxonomy、规则、行数先证明相同；
- proposal coverage、frozen-reference agreement、ordinary/transfer、omission、笔数/金额 correct reach
  在模型运行前固定；
- 错误样本只在本地查看；
- C4 只进入 C5，不直接创建 A7。

公开质量说明必须展示分母和限制，不能把冻结人类参照一致率称为客观准确率。

---

## 6. 上传后的触发与网页 Agent Center

### 6.1 Developer Preview：用户主动触发

Skill 不会因为网页上传了一张账单就自行获得一次 Agent turn。当前 MCP 方向是 Agent 启动本地 STDIO
child，再由 Agent 主动调用 Ledgerbox。Developer Preview 使用以下诚实流程：

```text
上传成功 → 确定性摄入/对账 → 页面显示新增候选
        → 用户复制固定 Prompt 或在自己的 Agent 中调用 Skill
        → proposal submit → 页面出现待审结果
```

这符合开发者用户定位，也不要求 Ledgerbox 启动或控制模型客户端。

### 6.2 Agent Center V1

网页新增独立 **Local Agent** 面板，但不把 Ledgerbox 后端的 `Connected` 指示灯冒充 Agent 状态。

V1 显示：

- Ledgerbox proposal readiness 与 9/9；
- Codex / Claude Code 的配置说明和固定命令；
- `configured`、`session active`、`last activity` 三种不同语义；
- 客户端实际报告的版本/模型（仅当存在）；
- 当前候选、最新 proposal、pending review 聚合；
- `Copy prompt`、`Test setup`、`Open review`；
- 数据会进入用户所选 Agent provider 上下文的明确披露。

如果当前架构只能证明配置存在，页面就只能显示 `Configured`，不能显示 `Connected now`。实时 session
状态需要 MCP child heartbeat 或等价的本地会话登记，必须另有失败、过期与清理测试。

### 6.3 可选 local runner 延后

未来可研究显式启用的：

```text
ledgerbox agent run --client codex --workflow classify
```

它调用用户自己安装和登录的 CLI，不持有模型密钥。但这会新增子进程、OAuth 过期、取消、超时、重复
run、客户端版本与隐私确认问题，不阻塞 Developer Preview，也不得藏在上传流程中默认开启。

---

## 7. Skill 分发与安装

### 7.1 当前事实

源码 checkout 中已有：

- `.agents/skills/ledgerbox/`
- `.claude/skills/ledgerbox/`

wheel 现在从 checkout canonical source 显式映射 Codex/Claude 的 classification、triage、共享 references
与 contract 到包内只读 Agent workspace。A7.5 已实测从 sdist 构建 wheel，并在全新 Windows venv 中
验证两个入口和两端 Skill compatibility。它保证 Ledgerbox 自动 runner 可用，不会把 Skill 自动复制到
用户任意项目；源码可发现、包内 runner 可发现与用户级 Skill 安装仍是三个不同状态。

### 7.2 Developer Preview

源码 checkout 允许客户端发现项目 Skill；同时提供明确的 `AGENT_SETUP.md` 和 Agent Center 命令。

### 7.3 稳定安装

稳定发布前提供类似：

```text
ledgerbox agent install-skill --client codex
ledgerbox agent install-skill --client claude
ledgerbox agent doctor --client codex
ledgerbox agent doctor --client claude
```

要求：

- 安装目标明确，默认不覆盖用户修改；
- `--force` 前预览会替换什么；
- 官方版本、custom 状态和来源可检查；
- 不把真实 data-dir、凭据或本地 MCP 配置写进仓库；
- Codex / Claude 的薄适配差异不复制分类知识；
- wheel/sdist 安装后在 Windows、macOS、Linux 各做 smoke。

当前实现已完成前五条，并在 Windows 的全新 wheel/venv 与隔离 HOME 中验证 Codex
`missing → installed → current`、Claude Code `installed → current`。只有包内明确登记的历史官方文件
指纹可无提示升级；未知/伪造 manifest 与任何增删改文件都归为 custom。`--force` 仍先列替换文件并
要求明确确认，中途目录提升失败会恢复原目录。macOS/Linux 与真实发布包 smoke 仍待 CI/发布门。

仍有一层产品整合没有完成：Agent Center 的 `skill_compatible` 目前描述包内 canonical workspace，侧栏
复制的 setup command 只注册 MCP；它们尚未呈现个人 Skill 的 missing/current/outdated/custom，也没有
把安全的非 force 安装步骤接入复制流程。下个 Session 必须先分开这两个事实，不能让页面静默写用户
目录或把 custom 当成可自动升级。用户级 triage 安装、卸载与完整升级矩阵继续作为稳定包后续项。

---

## 8. 开源发布分级

### 8.1 Developer Preview

可以诚实发布为开发者预览的最低条件：

- [x] S1 官方模块化 Classification Skill 完成；
- [x] S2 合成 Skill eval 与隐私反例完成；
- [x] S3 C4 同基线真实复跑完成；结果只称 frozen-reference agreement，不称客观准确率；
- [ ] `SECURITY.md` 与 Agent threat model 完成；
- [ ] 不含真实数据的合成端到账本可供贡献者运行；
- [ ] CI 在真实远端 runner 上至少完整通过一轮；
- [ ] 第一次 push 前由产品负责人明确决定 Git 历史隐私处置；
- [ ] README 写清目标用户、支持范围、Agent/provider 边界和 Developer Preview 标签；
- [ ] repo-data、secret scanning、license 与构建产物检查通过；
- [ ] 没有远端模型密钥、真实账单、数据库、浏览器快照或本地 manifest 进入仓库。

Developer Preview 可以是源码 checkout 工作流，不要求 PyPI，也不要求消费级一键安装。

### 8.2 开放新银行贡献

在公开鼓励用户贡献 parser 前，还必须：

- [ ] `tools/sanitize.py`；
- [ ] span fixture 套件与故意损坏输入；
- [ ] 新银行合成 fixture 指南；
- [ ] 明确禁止上传真实 PDF / DB / 描述串；
- [ ] 未知布局继续 fail closed，Agent 不能绕过对账。

在此之前可以开放源码，但贡献指南应明确暂不接收携带真实账单材料的新银行 PR。

### 8.3 稳定 PyPI / `uvx` 发布

- [x] 官方 Skill 资源进入 wheel/sdist，并完成 Windows 全新安装 smoke；
- [x] Skill installer / doctor 完成；Windows 隔离 HOME wheel smoke 已通过；
- [ ] `uvx ledgerbox` 无 Agent extra 也能完整启动；
- [ ] `[mcp]` 保持显式可选；
- [ ] 三平台安装、升级、卸载和带空格路径 smoke；
- [ ] 版本、变更日志、回滚与发布流程固定；
- [ ] Agent Center 不对配置/连接/活动状态说过头；
- [ ] 真实 CI、security 与贡献安全门全部通过。

### 8.4 不阻塞 Developer Preview 的项目

- 消费级无代码安装器；
- 所有银行与通用 CSV；
- i18n；
- 订阅检测；
- 完整投资会计；
- 自动启动 Agent runner；
- 普通类别自动写入 A7。

P3 不阻塞开发者预览，但如果目标从“会写 parser 的开发者”扩大到一般 Codex/Claude 用户，通用 CSV
应排在稳定版或 A7 默认自动之前。

---

## 9. 里程碑与执行顺序

| 里程碑 | 内容 | 状态 | 完成定义 |
|---|---|---|---|
| **S0** | 当前产品定位与执行计划 | ✅ 本提交 | 权威关系、范围、发布分级与下一项明确 |
| **S1** | 官方模块化 Classification Skill v1 | ✅ | 单一知识源、两个客户端薄适配、无真实数据、contract 不变 |
| **S2** | 合成 Skill eval | ✅ | 正反例先红后绿，输出只含合成/聚合证据 |
| **S3** | C4 官方 Skill 冻结复跑 | 🟡 自动化 ✅ / 人工待验 | 同候选双客户端评分，Truth 与有效分类零写 |
| **S4** | Skill 安装/版本/doctor | ⬜ | checkout 与安装包路径均可用，不覆盖 custom |
| **S5** | 网页 Agent Center V1 | ⬜ | readiness/config/session/activity 语义不混淆 |
| **R1** | 开源安全与真实 CI | ⬜ | SECURITY、threat model、历史决定、远端 CI 全部完成 |
| **R2** | Developer Preview | ⬜ | README、合成端到端、构建与发布标签诚实 |
| **R3** | 贡献安全与 PyPI 稳定版 | ⬜ | sanitize、span fixtures、三平台安装与 Skill 分发完成 |
| **A7** | 普通类别可选自动写入 | 条件项 | C4/C5 书面批准；来源、策略、撤回、transfer 禁止完整 |

依赖顺序：

```text
S1 → S2 → S3
 ↓     ↓
S4    R1
 ↓     ↓
S5 → R2 → R3

S3 → C5 ──明确批准──→ A7
```

R1 的 SECURITY、threat model、历史审查与 CI 准备可以和 S1–S3 并行，但任何第一次 push 仍必须等待
产品负责人明确批准。

---

## 10. S1 执行手册与完成记录

### 10.1 开工顺序

1. 完整阅读当前两个 proposal Skill、`AGENT_CONTRACT.md`、`AGENT_SETUP.md` 与 Skill validator；
2. 枚举 A6–C3 已确认的通用分类经验，只写抽象规则，不复制真实事实；
3. 冻结 canonical source 与 Codex/Claude 薄适配方式；
4. 先写会失败的结构、漂移、隐私与边界测试；
5. 编写 `workflow`、`category-semantics`、`transfer-boundaries`、`grouping-and-abstention`、
   `ambiguous-cases`、`privacy-and-output`；
6. 保持五工具 proposal contract，不把 triage 混入分类 Skill；
7. 运行 Skill validator、定向测试、完整回归与 repo-data gate；
8. 单独 commit，不创建 Base/clone、不运行真实模型、不 push。

### 10.2 S1 必须先红的反例

- Codex 与 Claude 复制了两份不同分类知识；
- Skill 静态写死 24 个 category ID；
- Skill 把 Zelle、wire、payment 或 deposit 一律认成 transfer/income；
- Skill 请求 confidence、SQL、文件读取、PDF 或 approval；
- Skill 把 triage route 当成 category proposal；
- Skill 最终摘要输出 category breakdown、描述、金额或 ID；
- Skill 在 MCP 缺失时退回读取仓库外文件；
- 文档示例包含真实路径、商户、姓名或账本测量；
- custom Skill 被页面/CLI 冒充 official；
- wheel 安装假定仓库根目录 Skill 会自动出现。

### 10.3 S1 Definition of Done

- [x] 官方分类经验按模块拆分，内容可由 Codex 与 Claude 共用；
- [x] 两个客户端只保留必须的触发/producer 差异；
- [x] taxonomy 仍只从 `ledgerbox_categories` 获取；
- [x] proposal 五工具顺序与固定摘要保持；
- [x] transfer、prompt injection、omission 与隐私边界有合成示例；
- [x] repo 中无真实描述、金额、姓名、账户尾号、txn/run/revision ID；
- [x] Skill validator 与结构/漂移/隐私测试通过；
- [x] 无 migration、API、UI、自动写入或模型运行；
- [x] 完整回归、Node、ruff、mypy、repo-data 与 diff check 全绿；
- [x] 一个独立 docs/Skill commit，未 push。

S1 将 `.agents/skills/ledgerbox/references/` 冻结为 checkout 内的 canonical knowledge source；Claude
adapter 指向同一目录，没有复制第二份分类知识。包内资源与安装生成仍属于 S4，不把 checkout 可用
误写成 wheel 已分发。S2 随后按 §5.1 建立并完成纯合成 contract/behavior eval，结果见 §10a。

## 10a. S2 完成记录

S2 冻结了 11 个 answer-blind 合成 case、独立 expected behavior、共享 Agent prompt、跨客户端 strict
JSON Schema 与 deterministic scorer。19 个定向反例覆盖 catalog/result strictness、not-ready 停止、
未知类别、重复/越界候选、payment rail、transfer pending、prompt injection、custom 权限和摘要泄漏。

Codex 首轮评分因两个多候选组把 pending 笔数写成 group 数而为 9 / 11；没有修改结果，而是澄清共享
contract/prompt 后两端从头重跑。最终 Codex 与 Claude 都为 11 / 11，五个维度全部通过。完整 schema、
错误码、命令、初始失败与聚合结果见
[`CLASSIFICATION_SKILL_EVAL.md`](CLASSIFICATION_SKILL_EVAL.md)。这叫 synthetic regression result，
不是现实准确率，也不解锁 A7。下一项是 S3 / C4。

## 10b. S3 C4.0-C4.2 完成记录

Truth 经只读边界重验为 schema 10、24 类、9 / 9 和 0 有效未分类。Base 从同一 13 份 archive 向
全新仓库外目录干净 ingest，双 clone 从 clean Base 独立建立；taxonomy、稳定行数与候选集合比较通过，
共同候选分母为 270。评分器的缺项、缺标签、重复、错误 ordinary、transfer、两种 reach 分母与隐私
反例已先红后绿。模型尚未运行，细节见
[`C4_FROZEN_BASELINE_EVAL.md`](C4_FROZEN_BASELINE_EVAL.md)。下一项是 C4.3。

C4.3-C4.4 随后完成双客户端 proposal-only audit 与统一评分。聚合结果、运行限制和 C5 人工入口见
[`C4_FROZEN_BASELINE_RESULT.md`](C4_FROZEN_BASELINE_RESULT.md)。2026-08-10 产品负责人完成视觉/语义
复核并批准 A7；当前入口改为
[`A7_AUTOMATIC_CLASSIFICATION_PLAN.md`](A7_AUTOMATIC_CLASSIFICATION_PLAN.md) 的 A7.0。

---

## 11. 测试与发布纪律

每个里程碑至少运行与风险相称的检查：

```powershell
python -m pytest
node --test "tests/js/*.test.js"
.\.venv\Scripts\ruff.exe check src tests tools
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe tools\check_repo_data.py
git diff --check
```

额外纪律：

1. Skill 例子只用合成事实；
2. 不在任务书、commit、issue、PR 或 Cloud task 中写真实描述、金额、姓名、路径或 ID；
3. 新检查同时有成功与失败用例；
4. 模型行为必须有真实 tool call/result 才算集成证据；
5. proposal coverage、用户一致率、分类覆盖与 synthetic eval 分开报告；
6. C4 前冻结 Skill 版本，C4 后改 Skill 必须视为新的评估对象；
7. 每个里程碑验证后单独 commit；
8. 未经产品负责人再次明确批准不 push；
9. 任何临时真实数据目录、MCP 注册、浏览器 profile 与 manifest 都由创建者清理或说明保留状态。

---

## 12. 风险登记

| 风险 | 后果 | 控制 |
|---|---|---|
| 把分类经验全部塞进一个长 Prompt | 不可维护、难测试 | references 模块化 + eval |
| Skill 复制 taxonomy | 分类表漂移 | 每次调用 categories；静态漂移测试 |
| custom Skill 被当官方质量 | 质量声明失真 | official/custom/unknown 分开 |
| “上传后自动”被误写成 Ledgerbox 调模型 | 产品边界改变 | Preview 由用户触发；runner 另立里程碑 |
| 网页把后端在线称为 Agent connected | 状态撒谎 | readiness/config/session/activity 分层 |
| 包内 Skill 映射漂移或用户级安装缺失 | runner 或手动客户端找不到 Skill | canonical force-include 反例 + 构建/安装 smoke；installer 仍单独验收 |
| 真实案例进入 Skill/eval | 永久泄漏 | 纯合成 fixture + repo-data/secret gate |
| 更强 Skill 诱导直接自动写 | 财务分类错误 | A7 使用诚实 Agent 来源、原子 audit、遗漏可见与整轮撤回 |
| 财务 Agent 与源码 Agent 混用 | 数据进入 PR/Cloud | 两个权限域与文档明确分开 |
| Developer Preview 被当稳定版 | 用户期待失真 | release label、支持矩阵、已知限制 |

---

## 13. 总 Definition of Done

Agent-native 开源路线只有在以下事实分别成立时才可升级措辞：

### Official Skill ready

- [ ] S1 与 S2 完成；
- [ ] 官方/自定义状态可区分；
- [ ] Skill 知识单一来源、两客户端无漂移；
- [ ] 合成 eval 和 contract compliance 有可重跑证据。

### Developer Preview ready

- [x] Official Skill ready；
- [x] C4 完成，且结果明确标为 frozen-reference agreement；
- [ ] SECURITY、threat model、合成端到端与真实 CI 完成；
- [ ] 第一次 push 的历史隐私决定完成；
- [ ] README、安装、连接、隐私和支持矩阵准确；
- [ ] 无真实数据或本地配置进入仓库。

### Stable package ready

- [ ] Developer Preview 的已知阻塞项关闭；
- [x] Skill 在 wheel/sdist 中可安装、检查和安全升级；
- [ ] sanitize、span fixtures 与新银行贡献路径完成；
- [ ] 三平台 PyPI/`uvx` smoke；
- [ ] Agent Center 的每个状态都有真实证据。

### Optional auto classification ready

- [x] C4/C5 明确批准 A7；
- [x] 普通类别来源诚实记录为 Agent；
- [x] 用户明确连接并启用 Agent 后默认自动；无 Agent 时不启动任何客户端；
- [x] 按 run 一键撤回并保护后续人工修改；
- [x] ordinary 与 transfer 都能按同一原子 audit/自动应用/撤回边界运行；
- [x] proposal omission 与 pending 分开显示，`0 pending` 不冒充全覆盖；
- [ ] custom Skill 不能绕过 Core 策略。

---

## 14. 与现有文档的关系

- 当前事实与历史证据：[`STATUS.md`](STATUS.md)
- 已实现 BYOA 契约与历史里程碑：[`AGENT_CLASSIFICATION_PLAN.md`](AGENT_CLASSIFICATION_PLAN.md)
- 当前官方 Skill 的五工具边界：[`AGENT_CONTRACT.md`](AGENT_CONTRACT.md)
- 本地客户端连接事实：[`AGENT_SETUP.md`](AGENT_SETUP.md)
- C4 公平复跑与评分：[`C4_FROZEN_BASELINE_PLAN.md`](C4_FROZEN_BASELINE_PLAN.md)
- triage 独立边界：[`COVERAGE_TRIAGE_CONTRACT.md`](COVERAGE_TRIAGE_CONTRACT.md)
- 整体架构与 rebuild 不变式：[`ARCHITECTURE.md`](ARCHITECTURE.md)
- 威胁模型：[`THREAT_MODEL.md`](THREAT_MODEL.md)
- 早期项目方案与立项审查：`EXECUTION_PLAN.md` / `PROJECT_SUMMARY.md`，保留历史，不作为当前顺序

本文不撤销已经完成的 G0–A6、A6.5 C0–C3，也不削弱现有 proposal/triage contract。它更新的是目标
用户、官方 Skill 产品化、Agent UX 和开源发布顺序。
