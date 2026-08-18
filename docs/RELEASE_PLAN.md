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

**(a) 已完成（2026-08-17，见 `STATUS.md` §5bk）**：`package` job 在
`windows-latest` 上 `python -m build`（sdist 先出、wheel 由 sdist 构建）→
仓库外全新 venv 装 `<wheel>[mcp]` → 用该 venv 的解释器跑
`tools/package_smoke.py`。断言不写在 YAML 里而是写在那个脚本里，因为写进 YAML
的判断没有类型检查也没有反例能够到；`tests/test_package_smoke.py` 逐条证伪它。
脚本额外断言 `ledgerbox` 是从 venv 而不是 checkout 导入的、workspace 解析到包内
而不是树内——`agent_workspace_root()` 优先找 checkout，所以少了这两条，一个什么
Skill 都没带的 wheel 也会报绿。**(b) 仍未做**，README 的 "Not yet on PyPI" 保持事实。

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

以上五项已于 2026-08-16/17 全部完成。

---

## 首次 PyPI 发布清单（§4b，产品负责人执行）

发布管线已就绪，**缺的只有凭据，且凭据不该存在**：`ci.yml` 的 `publish` job 用
PyPI Trusted Publishing（OIDC），PyPI 校验这个 workflow 的身份后签发一次性短期
令牌，仓库里没有任何 API token 可被泄露或需要轮换——和"账本从不持有模型密钥"
是同一条理由。因此没有任何密钥需要交给谁代持。

**产品负责人的三步：**

1. **在 PyPI 配置 Trusted Publisher。** 登录 pypi.org →（首发用 pending publisher，
   因为项目名还不存在）Your projects → Publishing → Add a new pending publisher：
   - PyPI Project Name: `ledgerbox`
   - Owner: `51351879`，Repository name: `Open-ledgerbox`
   - Workflow name: `ci.yml`
   - Environment name: `pypi`

   2026-08-17 只读核对：`https://pypi.org/pypi/ledgerbox/json` 返回 404，名称当时未被占用；
   这不是预留，先到先得。若届时已被占用，先改 `pyproject.toml` 的 `name` 与 README 的安装
   说明，再回到这一步——**不要**用近似名硬发。

2.（可选但建议）**给 GitHub 环境 `pypi` 加保护规则**：Settings → Environments →
   `pypi` → Required reviewers 填自己。加了之后每次 tag 推送会停在等待批准，
   发布从"推一个 tag"变成"推一个 tag 并按一次确认"。

3. **打 tag 并推送：**

   ```
   git tag -a v0.1.0a1 -m "ledgerbox 0.1.0a1"
   git push origin v0.1.0a1
   ```

   预期：CI 在 tag 上跑完整矩阵 + `package` smoke + 新增的
   `the tag and the version are one number`；全绿后 `publish` 才拿
   **`package` job 上传的那个 wheel**（不是第二次构建）传到 PyPI。
   若 tag 与 `pyproject.toml` 版本不一致，`release-tag` job 先红，什么都不会上传。

**发布后（可以交回给下一个 session 执行）：**

4. `uvx ledgerbox --version` 冷启动 smoke——在一台没装过 ledgerbox 的 Windows 上跑，
   预期打印 `ledgerbox 0.1.0a1`；再 `uvx --from "ledgerbox[mcp]" ledgerbox-mcp --help`
   预期打印 usage 并退出 0。
5. 只有 4 通过之后，才把 README 的 `Not yet on PyPI — run from a checkout.` 改成事实
   （安装命令 + 仍需 `ledgerbox setup` 的说明），并在 CHANGELOG 记一行。
   **在此之前那句话必须留着**——它现在是真的。
