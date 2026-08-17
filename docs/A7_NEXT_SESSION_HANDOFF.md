# A7 下一 Session 权威交接

> 更新时间：2026-08-11
>
> 当前入口：**A7.6/A7.7 已完成：真实首跑取证、runner 证据保留、轮次链与手动重跑、整批诚实
> 报告与进度界面、学习回路（learned_rule）全部落地；下一步是真实账本复跑验证学习回路效果，
> 以及剩余发布门**
>
> 本文压缩当前事实、产品决定、已完成项、未完成项和下一提交的 Definition of Done。若旧文档与
> 本文对“现在做到哪里”的描述冲突，以本文、`STATUS.md` 最新小节和
> `A7_AUTOMATIC_CLASSIFICATION_PLAN.md` 为准；历史结果仍以各自冻结报告为准。

## 1. 当前已验证基线

| 项 | 当前事实 |
|---|---|
| 仓库 | `D:\AI\ledgerbox`，`main`，无远端 |
| 最新功能基线 | A7.2-A7.5 全部先前项，加 A7.6（runner 证据保留/超时不洗白、轮次链、classify-now、整批报告、进度界面）与 A7.7（learned_rule 学习回路），均已独立提交 |
| 数据库 | schema 18；迁移只向前，0001-0018 |
| 分类知识 | 24 类；official-classification-v1 模块化 Skill；账本级 learned_rule 按描述符模板复利 |
| Python | 1095 passed / 100 skipped |
| Node | 70 / 70 |
| 数据库 schema | 19（0019 standing prefix rules；learned_rule.match_kind） |
| 静态与隐私检查 | ruff、mypy strict、repo-data、diff check 全绿 |
| 当前运行时 | v1 永久待审；v2 原子应用；启用策略后，成功导入会排队并自动调度所选本地客户端 |

当前有效 Truth 在上次隔离核对时已全部分类。不要在 Truth 上重跑 proposal，也不要把 Truth 复制后
删除 override/audit 来制造测试基线。C4 的 Base、Truth 和两个 clone 都在仓库外；它们只保留为历史
证据。Claude clone 后来经过真实人工接受，已经不再是最初的 pending-only 冻结现场，不能作为 A7
开发 fixture。A7 使用仓库内纯合成数据库和反例，不读取、不修改这些现场。

## 2. 已拍板的产品方向

1. 同时支持用户自己的 **Codex** 与 **Claude Code**。产品负责人偏好 Claude，但产品不指定赢家；
   用户选择自己已安装的客户端。
2. Ledgerbox 不内置远程模型、不持有模型密钥。连接的是用户电脑上的客户端，经严格 CLI/STDIO MCP
   使用同一份官方 Classification Skill。
3. 未连接 Agent 时保持规则分类与人工分类，不静默启动任何客户端。用户明确连接并启用后，
   `Auto classify new imports` 默认开启，也可以切回 `Review suggestions first`。
4. ordinary 与 transfer proposal 都允许自动应用。transfer 不再永久强制人工审核，但没有充分事实时
   仍应遗漏，不能仅凭支付渠道词猜测。
5. 自动应用必须有 Agent provenance、run audit、原子失败和整轮撤回；后续人工修改永远优先并保留。
6. 财务 Agent 的权限仍然很窄：不读 PDF/任意文件、不执行任意 SQL、不修改项目源码。帮助用户改代码
   是另一条明确授权的 coding-agent 工作流，不能借分类连接自动获得。

## 3. 81.9% 未分类与空 proposal 面板的真实原因

最近观察到的那轮共有 270 个候选。Claude 提交 123 个 proposal，用户已接受全部 123 个，其中包含
48 个 transfer；所以 proposal 面板显示 0 pending。Agent 同时遗漏了 147 个候选，其中 81 个是支出行。

- 81 个遗漏支出占支出笔数 **27.5%**；
- 它们占净支出金额 **81.9%**，因为少数大额资金流显著放大金额口径；
- proposal 面板只保存“Agent 实际提交的建议”，不会为 omission 自动生成待审项；
- 因此 `0 pending` 与 `81.9% unclassified amount` 可以同时成立；
- 主要原因不是 transfer 仍需人工审核：该轮 48 个 transfer proposal 已被提交并接受。

A7.0 已修正文案：`0 pending` 只代表已提交建议都处理完，不代表所有交易已分类，并把用户指向
Transactions 的 `Nothing claimed this`。A7.4 已把 candidate / submitted / applied / omitted 分开计数，
并把最近一轮的 omission 明确交给 `Needs classification`；仍不得用 catch-all 或大胆猜测制造虚假 100%。

## 4. 已完成

- P0、P1、P2 M1-M6；G0-G2；A1-A6；A6.5 C0-C3 与真实人工审核。
- S1：Codex / Claude Code 共用的官方模块化 Classification Skill 与六个知识模块。
- S2：11 个 answer-blind 合成 case 与 deterministic scorer；两端均为 11/11。它是合成回归结果，
  不是现实准确率。
- C4：同一 Base、同一候选集、同一 Skill 的冻结比较与聚合评分；C5 产品决定已经形成。
- A7.0：proposal 页面诚实区分 submitted/pending 与 omitted/unclassified。
- A7.1：schema 11；`category_override.source = human | agent`；Agent 决定必须引用 originating run；
  view/API/UI 显示 `set by Agent` / `marked by Agent`；旧数据迁移后仍是 human。
- A7.2：schema 12；proposal v2 strict parser；`automatic` 的 audit、ordinary/transfer Agent override、
  outcome 与 run completion 同事务；v1 与 v2 `review_first` 保持待审；CLI/HTTP/MCP/Skill 版本协商
  fail closed；整轮撤回保留后来的人工或其他 run 决定。
- A7.3 代码：schema 13；严格 singleton 本地策略；aggregate-only MCP session/result 证据；Codex 与
  Claude Code 独立安装检测。正文 Agent Center 已按产品反馈移除，紧凑侧栏分开显示当前账本、
  Ledgerbox 服务与真实 MCP session，并提供页面目录、待处理数字、当前 data-dir 的注册命令和折叠教程。
  复制注册命令/固定提示词都不启动模型。
- A7.4：schema 14/15 持久化有界 job、导入事务 outbox、job/session/run 精确归因、受限本地客户端
  runner、HTTP/CLI 调度与四路结果 UI 已完成。最近一轮 omission 数显示在 Transactions 目录徽标和
  `Needs classification` 入口；点击会清除旧筛选并打开未分类交易，不与整本账的覆盖率混为一谈。

## 5. A7.2 完成证据

proposal schema v1 的 review-only 语义未改变。Versioned v2 由 Core 严格验证 `application_mode`：

- `review_first`：与当前行为相同，只产生 pending audit；
- `automatic`：在同一个数据库事务中创建 run/item audit、把每个 ordinary/transfer proposal 写成
  Agent-sourced override、记录 outcome 并完成 run；
- 任一步失败时，audit、override、outcome 都不应产生部分状态；
- withdrawal 按 run compare-and-clear，只撤回仍与该 run 答案一致的 Agent 决定，保留后来的人工修改；
- CLI、HTTP API、MCP 与两份官方 Skill 已完成版本协商；旧 Core/旧 Skill 和跨版本字段 fail closed。

### 已由红转绿的反例

1. v1 携带 v2 字段、v2 缺少/拼错 mode、extra field 或类型错误；
2. ordinary 与 transfer 的 automatic 成功路径；
3. `review_first` 仍全部 pending、没有有效类别变化；
4. stale revision、候选不存在、重复候选、同一候选跨组重复、未知类别；
5. 数据库 busy/lock；在 audit、override、outcome 各阶段注入异常；
6. 上述每个失败都证明 run/item/override 没有部分写入；
7. 整轮撤回、已不存在、后来被人工改过、后来被另一 run 改过的 compare-and-clear；
8. v1 历史用例保持原语义，API/CLI/MCP 严格 JSON 契约保持兼容。

### A7.2 Definition of Done

- [x] schema v2 strict parser 和 Core 原子状态机完成；
- [x] ordinary 与 transfer 自动应用均带正确 Agent source/run；
- [x] failure injection、lock、stale、duplicate、unknown-category 全部零部分写；
- [x] review-first 与 v1 不回归；withdrawal 保留后续人工答案；
- [x] CLI/MCP/Skill 只在 Core 宣告 v2 能力时使用 v2；
- [x] 完整 Python 939 passed / 100 skipped、Node 46 / 46、ruff、mypy、repo-data、diff check 全绿；
- [x] Core 与接口协商独立提交；未运行真实模型，未修改仓库外账本，未 push。

### A7.3 当前证据

- [x] schema 13 policy/session Core 与 API/UI 分开实现；
- [x] disconnected、安装检测、活动中、完成/部分/失败与严格零写均有合成反例；
- [x] 官方 Skill 仅对启用且匹配的 MCP client 协商 automatic，其余 review-first；
- [x] 完整 Python 962 passed / 100 skipped、Node 50 / 50、ruff、mypy、repo-data、diff check 全绿；
- [x] 仓库外纯合成隔离账本的真实 Codex MCP automatic smoke：2 candidates、1 submitted/applied、
  1 omitted，run 为 partial；它证明连接与原子路径，不代表全覆盖；
- [x] 产品负责人在仓库外纯合成账本预览上完成新侧栏视觉/键盘体感验收；
- [x] Claude Code 2.1.207 对 canonical `ledgerbox` 的真实 STDIO MCP health smoke 返回 Connected；
  隔离账本记录 session 后正常结束为 seen-before，未调用业务工具、未运行分类模型，原私有注册已恢复。

## 6. A7.5 设置诚实性整合已完成；发布门仍开放

### A7.3 — Local Agent sidebar

代码与合成反例已经覆盖断开、单/双客户端安装、活动中、完成、部分、失败、严格启用与浏览器交互。
网页侧栏已经独立显示当前账本、Ledgerbox 服务、真实 session 与最后结果；安装或历史 session 不会
显示成“当前已连接”。目录锚点与 proposal/triage/review 待处理数字已有反例，连接设置与教程默认折叠。
产品负责人已签收新侧栏，Codex 与 Claude Code 的 canonical MCP 连接均有仓库外纯合成隔离证据；
A7.3 完整关闭。

### A7.4 — 导入触发与遗漏闭环

启用自动模式后，每次成功导入只排一个有界分类 job；只处理当前 eligible candidates；分别显示
submitted/applied/omitted；所有 omission 进入 `Needs classification`，不会因为 proposal pending 为 0
而消失。

已完成全部代码与自动化边界：schema 14 为每个导入源持久化唯一 job，入队时冻结 client/mode，FIFO 串行
领取，并以数据库约束保存 candidate/submitted/applied/omitted 或失败归并。关闭/退出策略零写入、
重复源幂等、未知源零写、非法计数不改变 running job、终态不可重复完成均有反例。账单入账与 job
outbox 已在同一事务提交；duplicate、needs-review 与失败均不留新 job，注入入队异常时账单和 job
数据库写入一起回滚。schema 15 让内部 `--job-id` MCP session 只能绑定匹配 client 的 running job，
并让 v2 proposal run 在原有原子提交事务内绑定同一 job；重复 run、错误 session、client/mode 不匹配
全部 fail closed。单-job runner Core 现在用隔离 MCP 配置启动所选客户端，丢弃客户端 stdout/stderr，
并只按绑定的数据库证据终结 job；缺客户端、超时、无结果均 fail closed，已提交 run 即使客户端随后
退出也不会被误记为全遗漏。HTTP upload 现在只在新 job 提交后安排响应后 background drain；CLI
ingest 只在新 job 存在时同步 drain；duplicate/refusal/策略关闭不启动。drain 遇到空/忙队列停止并有
100-job 硬上限。API 与侧栏现在显示 queued/running/completed/partial/failed，并分别报告 candidate、
submitted、applied 与 omitted；最近一轮 omitted 同步进入 Transactions 徽标和 `Needs classification`
入口，点击后精确筛选 `(none)`。A7.4 代码与合成自动化验收完成；本轮没有启动真实模型，真实 Codex /
Claude Code 导入 smoke 与视觉、键盘、屏幕阅读器体验归入 A7.5。

### A7.5 — 人工与发布门槛

Codex Windows 真实自动分类门已完成：产品负责人确认 MCP 连接/断开灯正确；仓库外纯合成导入产生
**16 candidates / 12 submitted / 12 applied / 4 omitted**，普通类别与 transfer 都显示 Agent 来源，
遗漏入口精确落到 4 条未分类。整轮撤回后当前 accepted 为 0、withdrawn 为 12，Transactions 的
`(none)` 回到 16；持久化 job 仍保留完成当时的 `16/12/12/4`，证明历史结果没有被当前状态覆盖。

这轮还发现并修复两个只在 Windows 真实链路出现的缺口：runner 现在先解析 npm client shim 再启动；
账单已新归档但数据库事务随后失败时，会删除本次新建且未登记的归档，避免 verifier 降为 8/9 并阻断
后续 Agent job；重试前已存在的归档不会被删除。当前完整基线已随设置整合增至 Python
**1026 passed / 100 skipped**、Node **57 / 57**，ruff、mypy strict、repo-data 与 diff check 全绿。

package-content 门也已完成：从 sdist 构建的 wheel 显式包含 checkout canonical Codex/Claude Skills、
references 与 contracts；全新 Windows venv 安装 `[mcp]` 后，`ledgerbox`、`ledgerbox-mcp` 和两端包内
Skill compatibility smoke 均通过。它不向用户任意项目复制或覆盖 Skill。

Claude Code Windows 真实 automatic 也已完成：首次运行因 Claude 可变参数吞掉 prompt 而在工具调用前
`client_exit`，且零 proposal/override；加入 `--` 分隔符后，第二份纯合成导入得到
**25 candidates / 19 submitted / 19 applied / 6 omitted**。schema v2 run 为 completed，19 条全部带
Agent 来源，其中 ordinary 12、transfer 7；Ledgerbox 9/9、pending review 0、当前未分类 6。Claude 未
报告 client version 或 model label，不猜。产品负责人确认页面计数后执行整轮撤回：accepted 0、
withdrawn 19、pending 0，当前未分类从 6 回到 25，历史 job 仍保持 `25/19/19/6`。

用户级 Classification Skill install/doctor 与安全升级也已完成：Codex/Claude 使用各自当前官方用户
目录；默认只安装 missing 或包内认识的旧官方指纹，未知 manifest 与任何改动都归为 custom、零覆盖；
force 先预览并确认，目录提升失败会恢复旧版。当前 wheel 已在全新 Windows venv 与隔离 HOME 验证
双客户端 installed/current；没有写入真实用户目录。triage 的用户级安装不在本轮范围。

Windows Narrator 真实验收也已完成：产品负责人确认断开状态不只靠颜色；历史 job 的
`25/19/19/6` 与当前 25 unclassified 能区分；accepted 0 / withdrawn 19 / pending 0 可读；目录、控件
名称和焦点正常。证据只覆盖当前 Windows Narrator A7 关键流，不代表 NVDA/JAWS/VoiceOver、其他浏览器
或平台。

### 已完成：个人 Skill 状态与复制设置流程说同一件事

- [x] 先写 API/JS 失败反例，证明 runner compatibility 与 personal installation state 是两个字段；
- [x] Agent Center schema v2 只返回 `runner_skill_compatible` 布尔值与
  `personal_skill_state = missing/current/outdated/custom` 枚举，不返回个人路径、hash、manifest、版本、
  改动文件名或内容；
- [x] 侧栏分别陈述 runner Skill 与 personal Skill，不把包内兼容冒充个人已安装；
- [x] `Copy safe setup steps` 先运行非 force `agent install-skill`，失败即停止，成功后才注册 MCP；
  页面读取/复制不写用户目录、不启动客户端、不调用模型；
- [x] 该守卫是**单条语句**：控制台逐行执行粘贴文本，所以守卫和注册不能分行。旧的三行
  `throw` 形式已由反例证伪（PS 5.1 与 pwsh 7 都在安装失败后仍注册），现在为
  `& '<ledgerbox>' agent install-skill --client <client>; if ($?) { <注册> } else { Write-Error ... }`；
  含换行、缺安装、守卫不在安装之后或含 `--force/--yes` 的 payload 在浏览器端 fail closed；
- [x] custom 停下并指向 CLI doctor/人工决定；UI 不展示、复制或执行 `--force --yes`；
- [x] Codex/Claude、checkout/package、四种个人状态、Clipboard 失败/缺失、旧 schema/字段与未知客户端
  均有反例；完整 Python **1028 passed / 100 skipped**、Node **58 / 58**、ruff、mypy、repo-data、
  diff check 全绿。

### A7.6/A7.7 已完成（2026-08-11，详见 STATUS §5bd-§5bg）

真实首跑取证证明 13-job 15 分钟跑掉 152/270，"只分 2 条"是末 job 显示缺陷。已落地：schema 16 runner
证据（client_outcome/exit/有界日志，`agent job-log` 仅终端）、超时不洗成完成；schema 17 轮次链与
`classify-now`（空轮容忍 3 连、上限 25 轮、有提交的批不被末轮失败冒名）；整批诚实报告 + 4 秒轮询 +
进度条与上界式剩余时间；效率审核结论（证据耗尽而非模型偷懒，系统不学习是最大结构差距）；schema 18
学习回路——决定即教 `learned_rule`（模板粒度），导入/人工分类/automatic run 后即刻认领同模板行，
人工规则压过 agent、撤回与 forget 连带清除、来源写 `learned` 永不冒充人工。

A7.8 弃权协议也已完成：真实 job log 证明三次按钮全部是 Codex 审完 98 条候选、判定全部弃权、想提交
空提案被 wire 拒绝、只能以 client_no_result 收场；同时旧链失败跨链吃掉新链容忍额度，每按一次只跑
1 轮。现在 schema v2 接受空提案（v1 冻结），空 run 到即完成、runner 记 partial 0/N 无错误码、链条
干净收束、面板解释为"examined and declined"；容忍数只在当前链内计数；旧版官方 Skill 指纹已入
PREVIOUS_OFFICIAL_BUNDLES（未改动旧安装判 outdated 可非 force 升级）；卫生守卫豁免 64-hex 内容哈希
并带自身正反例。产品负责人的个人 Skill 需重跑一次安全安装以升级到含弃权协议的新文本。

A7.9 常驻前缀规则也已完成并在真实账本兑现：产品负责人拍板 Zelle/Venmo 类按现金收支计入 In/Out
（transfer 会被排除出统计，故放弃 transfer 方向）；schema 19 的 `learned_rule.match_kind='prefix'`
（human-only、≥6 字符、模板规则优先、最长前缀优先、永不覆写既有决定、撤销只回滚自身派生）；
`ledgerbox rules add-prefix/list/remove-prefix`。真实账本执行：12 条人工 transfer 标记改回
（6 cash / 6 cash-deposit），两条 ZELLE 前缀规则当场认领 39 条，未分类 67 → **14**（含 5 条 Venmo，
形状未知，待产品负责人用同 CLI 自行决定）。

A7.10 大额确认板块已完成：`GET /api/large-flows`（默认阈值 $1,000，参数化，≤200 行、biggest-first、
排除 `category_decided_by='override'`）+ `Large flows` 页面板块。Confirm 复用既有 PATCH 分类路径把
当前答案重定为人工决定——离板即"人已确认"，同时教学习规则；未分类大额行只给 Transactions 链接。
真实账本此时 270 → 仅 1 条未分类（学习回路 + 前缀规则 + 产品负责人教学合力），Venmo 未分类为 0，
无需 Venmo 规则。

已完成补记：产品负责人已验收 Large flows 正常；一句话装机已落地——`ledgerbox setup --client
codex|claude --data-dir <dir>` 按序执行目录守卫→非 force 个人 Skill 安装→仅在成功后注册 MCP→验证，
幂等、custom 即停指向 doctor、拒绝默认数据目录；两端 checked-in `ledgerbox-setup` Skill 让
"帮我 set up"一句话落到这条命令；AGENT_SETUP.md §0 记录该路径，出厂包指纹已按 CRLF 教训用工作树
字节入册。真实账本未分类 270 → 1，Venmo 已清零。

Windows-only 范围声明与开源前隐私审计也已完成（STATUS §5bh）：README §Scope 平台表、机器路径
清理、全量身份检索零命中；留给产品负责人的两项披露决定——提交作者 gmail 的首次 push 前处置，
以及 README 故事真实聚合数字是否改写为合成值。

开源发布已完成（2026-08-17）：真实数字合成化、机器路径清除、历史 squash 为单根提交（中性作者，
完整开发史只在本地 `archive/pre-squash-history`，**永不 push**）；SECURITY.md（无邮箱，走 GitHub
私密报告，仓库开关已开启）与 CHANGELOG（0.1.0a1，与 pyproject 同源，守卫锁死）；README 重写为
~120 行用户视角（SVG logo、徽章、亮点表、mermaid 流程、一句话装机优先，scope 表保留）；已 push 至
github.com/51351879/Open-ledgerbox，**第二次托管 CI 全绿**——首轮红灯修复：矩阵裁为 Windows-only
（与 scope 声明一致，非 Windows mypy 红灯恰证明该决定）、guard 路径测试 resolve 两侧（runner 的
DOS 8.3 短路径）、gitleaks 换 pinned 二进制全史扫描（wrapper action 在分支首推时无 base range）。

仍未完成：发布 smoke 的 CI package job（RELEASE_PLAN §4a：build wheel → 全新 venv 装 [mcp] → 三条
命令断言）；PyPI 首发与 `uvx` 冷启动 smoke；候选 wire 附加 template/occurrences 字段（P2 增量，
牵动 AGENT_CONTRACT 措辞）；triage 自动接跑（残池近零，backlog）；用户级 triage 安装、installer
卸载/真实旧版本升级矩阵、合成端到端贡献 fixture。macOS/Linux 与其他读屏器/浏览器按 README scope
表长期归社区。没有 package smoke 与 PyPI 证据不得宣称"可安装的稳定发布"。

## 7. 距离开源还缺什么

Developer Preview 的核心账本、官方 Skill、合成 eval、C4/C5 已具备，但还不能宣称稳定开源发布：

- A7.5 尚未完成；Codex Windows 真实自动导入、遗漏、Agent ordinary/transfer 来源和整轮撤回已由
  产品负责人验收；Claude Code Windows 真实 automatic、页面计数、遗漏与整轮撤回，以及
  package-content、用户级 Classification Skill 安全安装、Windows 全新安装与 Narrator smoke 也已通过，
  侧栏 personal Skill 状态/复制设置整合也已完成；三平台真实发布 smoke 与其余 release 门仍待完成；
- 缺 `SECURITY.md` 与私密漏洞报告流程；
- CI 配置已写但尚未在真实托管 runner 跑通；
- 缺脱敏工具、合成财务人生生成器、可提交 span fixtures 与损坏输入；
- Skill 已验证进入 wheel/sdist、供包内 runner 使用，并可在用户级检查/安全安装；triage 用户级安装、
  三平台发布 smoke 与完整升级/卸载矩阵仍未完成；
- 尚未做三平台 PyPI/`uvx ledgerbox` 安装 smoke，当前也没有 PyPI 发布；
- 仓库无远端；第一次 push 前仍需产品负责人明确批准 Git 历史隐私处置；
- P3 通用 CSV/新银行插件化、i18n 等仍是后续范围，不阻塞源码 Developer Preview 的定义，但必须
  诚实标注未完成。

## 8. 下个 Session 最小阅读顺序

1. 本文全文；
2. `A7_AUTOMATIC_CLASSIFICATION_PLAN.md` 全文；
3. `STATUS.md` 文件头、§5aq-§5bb、§6、§6.5、§7-§9；
4. `AGENT_CONTRACT.md` 与 `.agents/skills/ledgerbox/SKILL.md`；
5. 设置整合的实现证据在 `src/ledgerbox/agent_skill_install.py`、`src/ledgerbox/api/routes/agent_center.py`、
   `src/ledgerbox/api/schemas.py`、`src/ledgerbox/web/js/agent-contract.js` 与
   `src/ledgerbox/web/js/agent-center.js`；
6. 对应反例在 `tests/test_agent_skill_install.py`、Agent Center API tests、`tests/js/agent-center.test.js` 与
   `tests/test_web_behaviour.py`；下一本地实施项需由产品负责人从开放发布门中明确选择；
7. 需要核对长期边界时再读 `ARCHITECTURE.md`、`THREAT_MODEL.md`、`AGENT_SETUP.md`、
   `AGENT_NATIVE_OPEN_SOURCE_PLAN.md` 与 C4 历史结果。

## 9. 仓库与隐私纪律

- 金额只用整数最小单位；迁移只向前；不执行任意 SQL 给 Agent。
- 不把模型 confidence 当产品阈值，不把 synthetic agreement 或 frozen-reference agreement 叫准确率。
- 不把真实描述、金额、姓名、账户尾号、txn/run/revision id、hash、截图、manifest 或仓库外数据路径
  写进仓库、commit message 或聊天总结。
- 保留用户现有改动；每个可提交项先验证再 commit；没有再次明确批准不得 push。
