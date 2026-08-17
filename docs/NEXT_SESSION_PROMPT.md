# 下一 Session 启动 Prompt

> 更新时间：2026-08-10
>
> 当前任务入口：**A7.5 进行中；Windows 双客户端 automatic/撤回、Narrator、用户级 Skill 安装与设置诚实性整合已完成；其余发布门仍待完成**。
>
> S1、S2、C4 与 C5 已完成；C5 已批准双客户端、连接后默认自动分类并包含 transfer。

复制下面整段即可：

```text
你正在继续 D:\AI\ledgerbox 项目。请用中文、专业、直接地执行，不要先给泛泛建议。

Ledgerbox 是面向开发者型用户的本地优先财务账本。Ledgerbox 不内置模型、不持有模型密钥；用户
自己的 Codex / Claude Code 经严格 CLI/STDIO MCP 使用官方 Classification Skill。Agent 不读 PDF/
文件、不执行任意 SQL。Proposal schema v1 永远只生成待审 audit；v2 Core 已能按显式 mode 对
ordinary 与 transfer proposal 做原子自动应用，并保留 Agent 来源、遗漏可见和整轮撤回。Schema 15
已经实现本地策略、aggregate-only MCP session、持久化分类 job、导入事务 outbox 与精确 run 归因；
官方 Skill 只对启用且匹配的当前客户端使用 `automatic`，其余情况 fail closed 到 `review_first`。

先做只读预检：
1. 确认 cwd=D:\AI\ledgerbox；读取 git status、git log -5、git remote -v；保留用户现有改动。
2. 按顺序完整阅读：
   - docs/A7_NEXT_SESSION_HANDOFF.md（当前压缩交接与事实入口）
   - docs/A7_AUTOMATIC_CLASSIFICATION_PLAN.md（当前 A7 权威任务书）
   - docs/STATUS.md 文件头、§5aq–§5bb、§6、§6.5、§7–§9
   - docs/AGENT_CONTRACT.md
   - .agents/skills/ledgerbox/SKILL.md
   - src/ledgerbox/agent_jobs.py、agent_runner.py、proposals.py、agent.py、agent_mcp.py
   - tests/test_agent_jobs.py、test_agent_proposals.py、test_agent_cli.py、test_agent_mcp.py
   - docs/ARCHITECTURE.md 中 rebuild、override、proposal 边界
3. 核对 main、schema 15、A7.2-A7.4 Core 基线、仓库当前没有远端。

当前已验证事实：
- G0–A6、A6.5 C0–C3 与真实人工审核已完成；当前有效未分类为 0。
- S1 已冻结 official-classification-v1 和两端共用的六个分类知识模块。
- S2 已冻结 11 个 answer-blind 合成 case 和 deterministic scorer。
- Codex / Claude 最终合成结果均为 11/11；这叫 synthetic regression result，不叫现实准确率。
- S2 的初始失败、修复、聚合结果与限制见 CLASSIFICATION_SKILL_EVAL.md。
- C4.0-C4.4 自动化比较已完成；聚合结果见 C4 result。
- 当前 Truth 已全部分类，不能直接在 Truth 上重跑 proposal，否则候选为 0。
- C4 的仓库外现场仅保留为历史证据；Claude clone 后来经过人工接受，已不再是原始 pending-only
  冻结现场。A7 不读取、不修改 Truth/Base/clones；没有新的明确授权不运行真实模型。
- C5 已批准同时支持 Codex / Claude Code；用户明确连接并启用后默认自动，transfer 不再永久人工审批。
- 最近的 81.9% 是遗漏支出的金额占比；对应 27.5% 的支出行。proposal 0 pending 只表示已提交
  建议处理完，不能说明 omission 为 0；transfer 审核门槛不是这次遗漏的主要原因。

当前没有被授权的唯一新实施项。第一安全步是做只读发布门审计：把 A7_NEXT_SESSION_HANDOFF.md
列出的开放项分成“本机可完成”“需要 macOS/Linux/托管 CI/真实发布包”“需要产品负责人新决定或
授权”，给出一个最小、可独立提交的推荐下一项；产品负责人选定前不要擅自扩大实现范围。已完成事实：
1. A7.0 已完成：proposal 页面已区分 submitted/pending 与 omitted/unclassified。
2. A7.1 已完成：schema 11、human/agent source、originating run、view/API/UI 来源均已落地。
3. A7.2 已完成：schema 12；v1 永久 review-only；v2 `review_first` 待审，显式 `automatic` 原子应用；
   ordinary/transfer、零部分写、compare-and-clear withdrawal 与 CLI/API/MCP/Skill 协商均已验证。
4. A7.3-A7.4 已完成：schema 13-15、本地策略/session、紧凑侧栏、持久化 job、导入触发、runner、
   四路计数与 omission handoff 均已落地。
5. Codex Windows 真实 automatic 已验收：16 candidates、12 submitted/applied、4 omitted；ordinary 与
   transfer 均带 Agent 来源；整轮撤回后当前未分类回到 16，历史 job 仍保留完成时计数。
6. Windows client shim 与失败导入留下 archive orphan 的两个真实链路缺口已修复。wheel/sdist 也已
   包含同源只读 Agent workspace，并通过 Windows 全新 `[mcp]` 安装与双入口 smoke。当前完整门禁为
   Python 1026 passed / 100 skipped、Node 57 / 57、ruff、mypy、repo-data 与 diff check 全绿。
7. Claude Code Windows 真实 automatic 已在明确授权的仓库外纯合成隔离账本通过：第一次调用因
   `--allowedTools` 吞掉 prompt 而在工具调用前失败且零 proposal/override；加入 `--` 分隔符后得到
   25 candidates、19 submitted/applied、6 omitted，19 条全部带 Agent 来源（ordinary 12、transfer 7）。
   产品负责人确认页面计数后执行整轮撤回：accepted 0、withdrawn 19，当前未分类从 6 回到 25，历史
   job 仍保持 `25/19/19/6`。不得复用或触碰 Truth/Base/C4 clones。
8. 用户级 Classification Skill install/doctor 与安全升级已完成：Codex/Claude 当前用户目录、
   missing/current/outdated/custom、默认零覆盖、package-catalogued 旧官方升级、force 预览确认与失败恢复
   均有反例；Windows 全新 wheel/venv + 隔离 HOME 双端 installed/current smoke 已通过，未写真实用户
   目录。Windows Narrator 也已由产品负责人验收：连接状态、历史 `25/19/19/6` 与当前 25 未分类、
   withdrawn audit、控件名称和焦点均正常；不得外推到其他 AT/浏览器/平台。
9. 设置诚实性整合已完成：Agent Center schema v2 分开返回 runner compatibility 与个人
   missing/current/outdated/custom 枚举；响应不含个人路径/hash/manifest/版本/文件信息。复制的安全步骤先
   非 force 安装，失败停止，成功后才注册 MCP；custom 指向 doctor/人工决定，UI 不提供 `--force --yes`；
   页面读取/复制不写 HOME、不启动客户端/模型。旧 schema/字段与未知客户端 fail closed。
10. 按交接中的发布清单选择下一项；macOS/Linux、真实 CI、push、发布或真实用户目录
    写入需要相应环境或明确授权，不得用本机推断冒充。

每完成可提交项，运行：
  .\.venv\Scripts\python.exe -m pytest
  node --test "tests/js/*.test.js"
  .\.venv\Scripts\ruff.exe check src tests tools
  .\.venv\Scripts\mypy.exe
  .\.venv\Scripts\python.exe tools\check_repo_data.py
  git diff --check

验证通过后独立 commit，不 push。commit message 和聊天总结不得包含真实数据、ID/hash、金额或本地
数据路径。需要产品负责人做新的实质决定时停止并给证据；否则在安全、明确、可逆时继续。
```

---

## 当前顺序

```text
S1 官方模块化 Classification Skill ✅
  ↓
S2 纯合成 Skill eval ✅
  ↓
S3 / C4 同基线 Codex/Claude 复跑 ✅
  ↓
C4.5 只读人工复核 + C5 产品决定 ✅
  ↓
A7.0 omission / zero-pending UX ✅
  ↓
A7.1 Agent provenance + schema 11 ✅
  ↓
A7.2 proposal v2 atomic auto apply, ordinary + transfer ✅
  ↓
A7.3 compact sidebar, Codex + Claude Code ✅
  ↓
A7.4 import trigger + submitted/applied/omitted remainder ✅
  ↓
A7.5 release gate（Windows real-client/Narrator/S4/setup truthfulness ✅；其余门待选择）
```

详细 A7 边界、顺序、失败条件与 DoD 见
[`A7_AUTOMATIC_CLASSIFICATION_PLAN.md`](A7_AUTOMATIC_CLASSIFICATION_PLAN.md)。
