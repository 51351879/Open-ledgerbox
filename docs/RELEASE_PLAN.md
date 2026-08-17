# 发布前六项：设计与执行顺序

> 2026-08-16 由产品负责人要求逐项设计。每一项遵守既有纪律：失败反例先行、
> 完整闸门后独立提交、不许把没验证过的东西写成已支持。

## 执行顺序与理由

```
1. SECURITY.md 与私密漏洞流程     （纯文档，push 前必须存在）
2. 版本与变更日志                （定版本号方案，后续每项都要引用它）
3. 真实托管 CI                   （需要远端仓库；在 squash+push 后立即做）
4. 发布 smoke                    （依赖 CI 与版本号；产出可安装证据）
5. 候选 wire template/occurrences（独立功能增量，任何时候可做）
6. triage 自动接跑               （残池近零，价值最低，最后做或砍掉）
```

1-2 无依赖且是 push 的前置；3-4 依赖远端存在；5-6 是功能增量，与发布互不阻塞。

## 1. SECURITY.md 与私密漏洞流程

**设计**：仓库根放 `SECURITY.md`，内容四段——支持版本（只有最新 tag）；报告渠道
（GitHub Security Advisories 的 private vulnerability reporting，不设邮箱，因为
项目没有组织邮箱且个人邮箱不进仓库）；响应承诺（尽力而为的单人项目措辞，
不承诺 SLA——承诺守不住的时限就是又一个说得比证据多的句子）；范围声明
（本地单机软件，威胁模型见 `THREAT_MODEL.md`，账本数据不经网络，最有价值的
攻击面是 PDF 解析器和 MCP 边界）。

**执行**：写文档；在 GitHub 仓库设置里开启 private vulnerability reporting
（push 之后的一次性人工操作，记入 push 清单）；README 的 Privacy & security
节加一行链接。反例：`test_repo_hygiene` 断言 SECURITY.md 存在且不含邮箱地址。

## 2. 版本与变更日志

**设计**：`0.y.z` 语义——公开 API（CLI 参数、HTTP 路由、proposal schema、
DB schema）在 1.0 前允许破坏，但每次破坏必须进 CHANGELOG。单一事实源是
`pyproject.toml` 的 `version`；`ledgerbox --version`、`/api/health` 的
`version` 字段都已读它。`CHANGELOG.md` 用 Keep a Changelog 结构，从
`0.1.0-preview` 开始——**squash 之后历史只有一个提交，CHANGELOG 是唯一的
变更叙事，所以它从发布视角写（用户可见行为），不从提交视角写**。

**执行**：写 CHANGELOG.md 初版（0.1.0-preview：一段话概括当前能力 + 已知
边界，引用 README Scope）；定 tag 规范 `v0.1.0-preview`；反例：CI 里校验
tag 与 pyproject version 一致才允许 release job 跑。

## 3. 真实托管 CI

**设计**：`.github/workflows/ci.yml` 已写好但从未在托管 runner 上跑过——
它在本机的每一次绿都不构成"CI 会绿"的证据（Windows runner 的 PowerShell
版本、路径分隔符、无真实样本环境都不同）。矩阵只有 `windows-latest`
（Windows-only 声明的直接推论；加 ubuntu 只为跑 ruff/mypy 这类平台无关闸，
可选）。真实样本永不进 CI：`LEDGERBOX_REAL_FIXTURES` 不设，94+ 门控用例
按设计 skip，且 `beancount` job 已有"skip 数不得超过预期"的守卫防止
靠 skip 变绿。

**执行**：push 后看第一次真实运行；预期的现实问题——bean-check 在
runner 上的安装方式、npm/node 版本 pin、pip 缓存。每个红灯按"修因不修表"
处理并独立提交。CI 绿不写进 README 徽章，直到连续若干次运行稳定。

## 4. 发布 smoke

**设计**：回答"用户拿到的东西装得上、跑得起来"。两条路径：
(a) `python -m build` 出 wheel/sdist → 全新 venv 安装 `[mcp]` extra →
`ledgerbox --version`、`ledgerbox-mcp` 可执行、包内 Skill compatibility
检查通过（这条已在 A7.5 本机验证过一次，进 CI 变成每次都验）；
(b) PyPI 发布后 `uvx ledgerbox` 冷启动 smoke——**这条只能在真实发布后做**，
做完前 README 保持"Not published to PyPI"的诚实句。

**执行**：把 (a) 做成 CI 的 `package` job（build → install into clean venv →
三条命令断言）；(b) 作为首次 PyPI 发布的人工清单项。macOS/Linux smoke 明确
不做（范围声明已写），社区 PR 附带各自平台的 CI job 才算支持。

## 5. 候选 wire template/occurrences 字段

**设计**：`ledgerbox_candidates` 每个候选附加两个只读字段：
`descriptor_template`（本仓库 `descriptor_template()` 的输出）与
`occurrences`（当前候选集内同模板行数）。价值：Agent 不再自己猜哪些行是
同一商户，分组直接按模板对齐，一次提交覆盖整簇；与 learned_rule 形成同一
词汇。**边界**：字段是证据不是指令——Skill 文本明确"同模板仍需逐行核对
方向与语义，模板相同不豁免弃权规则"。加字段是响应的加法，v2 契约不需要
版本升级，但 `AGENT_CONTRACT.md` 的候选对象清单必须同步，出厂 Skill 指纹
随之滚动（目录里追加旧指纹，流程同 2026-08-12 那次）。

**执行**：反例先行——模板计算与 candidates 输出一致性、occurrences 计数、
旧 Skill 收到新字段不崩（宽进）、新 Skill 在旧 Core 上不假设字段存在
（严出）；然后 Core/CLI/MCP 三处输出 + 两端 Skill 文本 + 契约文档一次提交。

## 6. triage 自动接跑

**设计**：分类链收束（completed / examined-and-declined）且残留未分类 > 0
时，自动排一个 triage job，让每条残留带上 possible transfer / taxonomy gap /
uncertain 的理由进人工清单。复用 A7.4 的 job 队列：`trigger_kind='triage'`，
同样的单 job、绑定 session、证据终结、失败零写。**现状核对**：真实账本
残池已是 1 条，学习回路 + 前缀规则 + Large flows 把这个功能的原始动机
（"118 条白墙"）基本消化了。

**执行**：降级为 backlog——只有当真实使用中再次出现两位数以上的顽固残池
才实施。实施时的反例集：链后自动排队、残池为零不排、triage run 绑定 job、
撤回不影响 triage 历史。**不做也要诚实**：README 不提这个能力。

## push 前人工清单（squash 之后）

1. `git log` 只有一个提交，作者是中性身份，无真实邮箱；
2. `SECURITY.md`、`CHANGELOG.md` 在树里；
3. GitHub 仓库开 private vulnerability reporting；
4. 第一次 CI 运行全绿或逐红修复；
5. README 徽章、PyPI 声明与事实一致。
