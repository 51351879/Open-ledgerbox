# ledgerbox — 执行方案

> 2026-08-07 补充：本文件保留立项方案，不更新日常进度。P2 之后新增的 BYOA 分类方向、
> 遗留项重排与具体执行门槛见 `AGENT_CLASSIFICATION_PLAN.md`（维护者本地笔记，不在仓库内，
> 见 [`STATUS.md`](STATUS.md) 文件头）；
> 当前事实仍以 [`STATUS.md`](STATUS.md) 为准。

> 本地优先的个人财务账本。把银行对账单丢进去，得到一个**经过对账验证**的长期账本。
>
> 状态：方案定稿，待实施
> 最后更新：2026-08-02

---

## 0. 项目定位

### 0.1 一句话

一个跑在你自己电脑上的本地服务：网页上传对账单 → 确定性解析 → **强制对账** → 存入本地 SQLite 账本 → 分析看板。数据永不离开本机。

### 0.2 核心设计原则

**对账是产品，解析器不是。**

任何解析器最终都会错，区别在于你多久发现。前身项目的教训：收入被虚高 **4.57 倍**（$268,391 vs 真实 $58,725），储蓄率显示 78% 而真实约 0%，而这个错误在 CSV → JS → HTML → 决策的整条链路上没有任何一个环节能拦下它。

Chase 每张账单都印着期初余额、期末余额、存入合计、取款合计。**一个 15 行的断言就能拦住这次事故。**

因此本项目的设计目标是：**错了就崩溃，而不是错了看不出来。**

推论（贯穿全文的三条铁律）：

1. **确定性优先，LLM 兜底，不能反过来。** 正则错了是响亮的；LLM 错了是安静且看起来合理的——一个位数转置能完美通过肉眼检查。钱的事情要的失败模式是「崩溃」。
2. **永远不用 LLM 自报的 confidence 当闸门。** 闸门是确定性对账失败。
3. **未知即拒绝。** 未知布局、未知格式、对不上的账 → 进审核队列，绝不猜。

### 0.3 Non-goals（明确不做，写进 README）

| 不做 | 理由 |
|---|---|
| 多用户 / 权限系统 | 单人工具。加了就要做认证、隔离、审计，永远做不完 |
| 云同步 / 托管服务 | 与"数据不离开本机"直接冲突 |
| 自动抓取银行数据 | 见 §11，只提供实现思路文档 |
| 预算包络（YNAB 式） | Actual Budget 做得更好，别重复造 |
| 报税 / 洗售规则 | 整个生态都没人做对，不要碰 |
| 实时行情 | 日终价格足够 |
| 移动 App | 本地服务 + 手机浏览器访问局域网即可（且默认不开） |
| 记账凭证 / 发票管理 | 范围爆炸 |

---

## 1. 技术选型

### 1.1 选型表

| 层 | 选择 | 版本下限 | 理由 |
|---|---|---|---|
| 语言 | Python | 3.11+ | 生态、PDF 库、贡献者门槛 |
| Web 框架 | FastAPI + uvicorn | — | ASGI，绑 `127.0.0.1`。文件上传、校验、OpenAPI 开箱即用 |
| 数据库 | **SQLite**（stdlib `sqlite3`） | 3.37+ | `STRICT` 表需要 3.37。文件格式自 2004 年稳定，**美国国会图书馆推荐的长期保存格式** |
| ORM | **不用** | — | schema 就是产品。ORM 会隐藏「整数分」纪律，且迁移必须显式可审计 |
| PDF 抽取 | **pdfplumber** | 0.11+ | **MIT**。提供逐词 `(x0, top, x1, bottom)` 坐标，这是正确绑定金额/余额列的前提 |
| 前端 | **原生 ES modules，无构建步骤** | — | 见 §1.3 |
| 图表 | ~~Chart.js，**本地 vendor**~~ → **手写 SVG，不用库** | — | ⚠️ **已在 P2 规划时改掉，见 `STATUS.md` §6。** 理由：`tests/test_api.py` 的 `innerHTML` 守卫逐行扫 `web/` 下**所有** `.js`、没有排除项，一个压缩 bundle 绊上去的唯一出路是给守卫加豁免，而那把钝器钝正是重点。13 个月度点不需要图表库 |
| 测试 | pytest + pytest-regressions | — | `dataframe_regression` 对交易表做逐行逐列 diff |
| 打包 | uv / uvx + pyproject | — | `uvx ledgerbox` 自带 Python 下载 |
| 校验预言机 | `bean-check` **子进程** | 可选 | 见 §1.2 |

**运行时依赖总共 5 个**：`fastapi`、`uvicorn`、`pdfplumber`、`platformdirs`、`python-multipart`。刻意保持极少——20 年后还要能装上。

### 1.2 被否决的选项及理由

| 否决 | 理由 |
|---|---|
| **PyMuPDF** | **AGPL-3.0，会传染整个项目。** 这是前身项目里一个未被发现的开源阻断项。换 pdfplumber（MIT） |
| `import beancount` | GPL-2.0-**only**，同样传染。改为：借用它的**数据模型**和**文件格式**，通过 **subprocess** 调 `bean-check` 做独立对账预言机。这样还白得一个 Fava（MIT）当免费 Web UI |
| camelot / tabula 表格抽取 | 银行对账单的"表格"是**无框线的定位文本**：lattice 找不到线，stream 遇到折行描述就崩。**逐词坐标 + 列 x 区间**才是对的方法。（唯一维护良好的 Chase 解析器 `monopoly` 也是这么做的） |
| DuckDB 作为真相 | 前向兼容只是 "best effort"，存储格式几乎每个小版本都动。做分析引擎正确，**做 20 年档案不合格**。你的数据量只有几十 MB，速度差异无关紧要。需要窗口函数时从 DuckDB `ATTACH` 这个 SQLite 文件即可 |
| React / Vue + 构建链 | 见 §1.3 |
| 浮点存金额 | Ghostfolio 用 `Float` 存钱是真实缺陷。SQLite 官方明说：只有 0.00/0.25/0.50/0.75 能精确表示 |
| CRDT / 多端同步（抄 Actual） | 那套复杂度买的是无冲突多端合并。作者自己的复盘：schema 迁移变得「极其困难」，批量操作变得不可能。**你只有一个用户，不要付这个账单** |
| 完整双时态（bitemporal） | `posted_date` + `ingested_at` + `superseded_by` 三个列拿到 95% 收益，5% 复杂度 |

### 1.3 为什么前端不用构建链

这是一个**有意的、会被质疑的**决定，理由如下：

1. **单用户本地应用，性能不是约束。**（实测：5 万行交易的分类计算 535ms，分页表只渲染 50 行。真正的瓶颈是图表点数，靠聚合解决，不靠框架）
2. **无构建链 = 20 年后仍能运行。** 这与"长期复用"是同一件事。一个 2026 年的 Vite 配置在 2036 年大概率装不上。
3. **贡献门槛。** 开源后，别人想加一条分类规则不该先装 node_modules。
4. **打包路径。** 后端直接 serve 静态文件，桌面 app 化只是"启动进程 + 开窗口"。

**代价与对策**：原生 JS 容易长成一个 5000 行的怪物（前身项目正是如此）。对策是**强制模块化**——每个 `.js` 单一职责、单一导出面，纯计算逻辑必须可被 Node 直接 import 做单测，且**任何文件超过 400 行视为需要拆分的信号**。

---

## 2. 目录结构

```
ledgerbox/                          # ← git 仓库，永远不含真实数据
├── src/ledgerbox/
│   ├── __main__.py                 # python -m ledgerbox → 起服务 + 开浏览器
│   ├── cli.py                      # ingest / verify / export / doctor
│   ├── config.py                   # 数据目录解析 + 运行时守卫
│   │
│   ├── db/
│   │   ├── schema.sql              # 权威 schema（见 §3）
│   │   ├── migrations/             # 0001_init.sql, 0002_*.sql … 只向前
│   │   ├── connection.py           # WAL、foreign_keys=ON、query_only 只读句柄
│   │   └── repo.py                 # 薄仓储层，显式 SQL
│   │
│   ├── ingest/
│   │   ├── pipeline.py             # 摄入→识别→抽取→对账→入账 编排
│   │   ├── archive.py              # SHA-256 内容寻址归档
│   │   ├── identify.py             # /Producer + 首页标记 → 布局配置
│   │   ├── registry.py             # 解析器插件注册表
│   │   └── parsers/
│   │       ├── base.py             # Parser 协议
│   │       ├── chase_checking.py   # P0：唯一有真实样本验证的
│   │       └── generic_csv.py      # P3：列映射向导驱动
│   │
│   ├── reconcile/
│   │   ├── checks.py               # 5 层断言（见 §4.3）
│   │   └── report.py               # 结构化失败报告 → 审核队列
│   │
│   ├── ledger/
│   │   ├── identity.py             # 幂等键（见 §3.3）
│   │   ├── posting.py              # 单边流水 → 复式 posting
│   │   ├── transfers.py            # 内部转账配对（见 §5.2）
│   │   └── beancount_export.py     # 纯文本逃生舱
│   │
│   ├── analytics/
│   │   ├── categorize.py           # 规则引擎 + 用户覆盖
│   │   ├── aggregate.py            # 纯函数，全部有单测
│   │   └── subscriptions.py        # 真·周期性检测（见 §5.3）
│   │
│   ├── api/
│   │   ├── app.py                  # FastAPI，绑 127.0.0.1
│   │   └── routes/                 # upload / review / transactions / analytics
│   │
│   └── web/                        # 静态前端（无构建）
│       ├── index.html
│       ├── css/
│       ├── js/
│       │   ├── main.js
│       │   ├── api.js
│       │   ├── analytics.js        # 纯函数，Node 可直接单测
│       │   ├── charts.js
│       │   ├── table.js
│       │   ├── review.js           # 审核队列 UI
│       │   ├── categories.js
│       │   └── i18n.js
│       ├── i18n/{zh,en}.json
│       ├── rules/categories.json   # 分类规则，数据不是代码
│       └── vendor/chart.umd.js     # 本地 vendor，锁版本
│
├── tests/
│   ├── fixtures/
│   │   ├── synthetic/              # 合成 PDF（生成器产出）
│   │   ├── spans/                  # ★ 文本层 JSON，不是 PDF（见 §8.2）
│   │   └── malformed/              # 故意损坏的输入
│   ├── test_parse_chase.py
│   ├── test_reconcile.py
│   ├── test_identity.py
│   ├── test_analytics.py
│   └── test_rebuild.py             # ★ 从 archive/ 完整重建 == 当前 db
│
├── tools/
│   ├── gen_synthetic.py            # 合成财务人生生成器（抄 bean-example）
│   └── sanitize.py                 # 真实 PDF → 可提交的脱敏 span JSON
│
├── docs/
│   ├── EXECUTION_PLAN.md           # 本文件
│   ├── ARCHITECTURE.md
│   ├── THREAT_MODEL.md
│   ├── ADDING_A_BANK.md
│   └── AUTOMATION.md               # ★ §11 的调研成果
│
├── .gitignore                      # 已就位
├── pyproject.toml
├── README.md
├── LICENSE
├── SECURITY.md
└── CONTRIBUTING.md
```

**用户数据目录（仓库之外）：**

```
%LOCALAPPDATA%\ledgerbox\           # Windows
~/.local/share/ledgerbox/           # Linux
~/Library/Application Support/ledgerbox/   # macOS
├── archive/2026/03/<sha256>.pdf    # 青铜层：原件，不可变，内容寻址
├── extracted/<sha256>.ndjson       # 抽取结果，可从 archive/ 完全重建
├── ledger.db                       # 白银+黄金层：系统真相
├── export/ledger.beancount         # 纯文本逃生舱
└── config.toml
```

**不变式（做成 CI 测试）：`ledger.db` 必须能从 `archive/` + `migrations/` 完全重建。**

---

## 3. 数据模型

### 3.1 核心决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 记账模型 | **复式** | 零和约束 = 永远在线的免费集成测试；**转账双重计数在结构上不可能发生**。存储 posting，渲染成单边 |
| 金额 | **整数最小单位（分）** | 见 §1.2 |
| 数量 vs 金额 | **分开两列** | GnuCash 的 `value` + `quantity`：一行同时表示 "150.00 USD" 和 "10 IBM"。**只存一个 `amount` 列是投资建模的经典入门错误** |
| 批次 | `lot` **独立表** | 成本基础必须是一等公民，事后加极痛 |
| 更正 | 追加式 + `superseded_by` | 抄 OFX 的 `CORRECTACTION ∈ {REPLACE, DELETE}` |
| 表模式 | `STRICT` + `foreign_keys=ON` + WAL | 类型强制 |

### 3.2 Schema

```sql
-- ===== 青铜层：只增不改 =====================================================
CREATE TABLE source_file (
  id           TEXT PRIMARY KEY,
  sha256       TEXT NOT NULL UNIQUE,   -- 内容寻址：重复上传天然是 no-op
  rel_path     TEXT NOT NULL,
  media_type   TEXT NOT NULL,
  byte_len     INTEGER NOT NULL,
  institution  TEXT,
  period_start TEXT,
  period_end   TEXT,
  ingested_at  TEXT NOT NULL,
  supersedes   TEXT REFERENCES source_file(id)   -- 更正版账单链
) STRICT;

CREATE TABLE raw_record (
  id             TEXT PRIMARY KEY,
  source_file_id TEXT NOT NULL REFERENCES source_file(id),
  record_index   INTEGER NOT NULL,     -- 溯源用，绝不当身份
  kind           TEXT NOT NULL,        -- stmttrn | invtran | invpos | balance
  payload        TEXT NOT NULL,        -- 逐字 JSON，含 page/bbox
  parser_id      TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  UNIQUE(source_file_id, record_index)
) STRICT;

-- ===== 白银层 ===============================================================
CREATE TABLE commodity (
  id     TEXT PRIMARY KEY,             -- 'USD' | 'VTSAX'
  kind   TEXT NOT NULL CHECK (kind IN
           ('currency','equity','fund','bond','option','crypto')),
  scale  INTEGER NOT NULL,             -- USD=2, 股票=8
  cusip  TEXT, isin TEXT,              -- 真正的键（ticker 会被复用）
  ticker TEXT
) STRICT;

CREATE TABLE account (
  id              TEXT PRIMARY KEY,
  parent_id       TEXT REFERENCES account(id),
  name            TEXT NOT NULL,       -- 'Assets:Chase:Checking'
  kind            TEXT NOT NULL CHECK (kind IN
                    ('asset','liability','equity','income','expense')),
  subtype         TEXT,                -- checking | credit_card | brokerage
  currency        TEXT NOT NULL REFERENCES commodity(id),
  booking_method  TEXT NOT NULL DEFAULT 'FIFO'
                    CHECK (booking_method IN
                      ('STRICT','FIFO','LIFO','AVERAGE','NONE')),
  is_own_account  INTEGER NOT NULL DEFAULT 1,  -- 内部转账识别的依据
  institution     TEXT, mask TEXT,
  opened_on TEXT, closed_on TEXT
) STRICT;
-- 不存 `sign` 列：正常余额方向由 kind 推导

CREATE TABLE txn (
  id            TEXT PRIMARY KEY,
  date          TEXT NOT NULL,
  payee         TEXT,
  narration     TEXT,
  flag          TEXT NOT NULL DEFAULT '*' CHECK (flag IN ('*','!')),
  is_transfer   INTEGER NOT NULL DEFAULT 0,   -- ★ 不计入收支
  superseded_by TEXT REFERENCES txn(id),
  created_at    TEXT NOT NULL
) STRICT;

CREATE TABLE posting (
  id          TEXT PRIMARY KEY,
  txn_id      TEXT NOT NULL REFERENCES txn(id),
  seq         INTEGER NOT NULL,
  account_id  TEXT NOT NULL REFERENCES account(id),
  date        TEXT,                    -- NULL⇒txn.date；两腿隔日结算时用

  amount_minor    INTEGER NOT NULL,    -- 有符号，单位 currency 的最小单位
  currency        TEXT NOT NULL REFERENCES commodity(id),
  quantity_scaled INTEGER,             -- ★ 与 amount 分离
  commodity_id    TEXT REFERENCES commodity(id),

  lot_id              TEXT REFERENCES lot(id),
  cost_per_unit_minor INTEGER,
  cost_currency       TEXT REFERENCES commodity(id),
  cost_date           TEXT,
  price_per_unit_minor INTEGER,

  category_id TEXT REFERENCES category(id),
  memo        TEXT,
  cleared     INTEGER NOT NULL DEFAULT 0,
  reconciled  INTEGER NOT NULL DEFAULT 0,
  UNIQUE(txn_id, seq)
) STRICT;
-- 不变式：SUM(amount_minor) GROUP BY txn_id, currency == 0   （见 §4.3 检查 0）

CREATE TABLE lot (
  id                  TEXT PRIMARY KEY,
  account_id          TEXT NOT NULL REFERENCES account(id),
  commodity_id        TEXT NOT NULL REFERENCES commodity(id),
  acquired_on         TEXT NOT NULL,
  cost_per_unit_minor INTEGER NOT NULL,
  cost_currency       TEXT NOT NULL REFERENCES commodity(id),
  label               TEXT,            -- 券商的批次号：是事实，不可推导
  opening_posting_id  TEXT REFERENCES posting(id),
  closed_on           TEXT
) STRICT;

-- ===== 身份层 ===============================================================
CREATE TABLE txn_identity (
  txn_id              TEXT NOT NULL REFERENCES txn(id),
  account_id          TEXT NOT NULL REFERENCES account(id),
  source_system       TEXT NOT NULL,   -- pdf | csv | ofx | simplefin
  source_id           TEXT,            -- FITID：可空、不可信
  natural_key         TEXT NOT NULL,
  natural_key_version INTEGER NOT NULL,
  occurrence_index    INTEGER NOT NULL DEFAULT 0,
  raw_descriptor      TEXT NOT NULL,   -- 逐字保留，绝不原地规范化
  raw_record_id       TEXT REFERENCES raw_record(id),
  UNIQUE(account_id, source_system, natural_key, natural_key_version)
) STRICT;

CREATE UNIQUE INDEX txn_identity_src
  ON txn_identity(account_id, source_system, source_id)
  WHERE source_id IS NOT NULL;

-- ===== 对账 / 审核 ==========================================================
CREATE TABLE balance_assertion (
  id             TEXT PRIMARY KEY,
  account_id     TEXT NOT NULL REFERENCES account(id),
  as_of          TEXT NOT NULL,
  commodity_id   TEXT NOT NULL REFERENCES commodity(id),
  amount_minor   INTEGER,
  quantity_scaled INTEGER,
  source_file_id TEXT REFERENCES source_file(id),
  UNIQUE(account_id, as_of, commodity_id)
) STRICT;

CREATE TABLE review_item (
  id             TEXT PRIMARY KEY,
  source_file_id TEXT NOT NULL REFERENCES source_file(id),
  status         TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open','resolved','dismissed')),
  severity       TEXT NOT NULL CHECK (severity IN ('block','warn')),
  check_id       TEXT NOT NULL,        -- 哪条断言失败了
  detail         TEXT NOT NULL,        -- 人类可读 + 结构化 JSON
  created_at     TEXT NOT NULL,
  resolved_at    TEXT
) STRICT;

CREATE TABLE category (
  id       TEXT PRIMARY KEY,           -- 稳定 ID，不是显示名
  parent_id TEXT REFERENCES category(id),
  kind     TEXT NOT NULL CHECK (kind IN ('income','expense','transfer'))
) STRICT;
-- 显示名走 i18n 表，不塞进 key（前身项目把中英文塞在 key 里用 split('/') 拆）

CREATE TABLE category_override (   -- 人、Agent 或已学规则的逐笔决定，必须能持久化并诚实标源
  txn_id          TEXT PRIMARY KEY REFERENCES txn(id),
  category_id     TEXT NOT NULL REFERENCES category(id),
  created_at      TEXT NOT NULL,
  source          TEXT NOT NULL DEFAULT 'human'
                       CHECK (source IN ('human', 'agent', 'learned')),
  agent_run_id    TEXT REFERENCES "agent_proposal_run"(id),
  learned_rule_id TEXT REFERENCES learned_rule(id),
  CHECK (
    (source = 'human' AND agent_run_id IS NULL AND learned_rule_id IS NULL)
    OR
    (source = 'agent' AND agent_run_id IS NOT NULL AND learned_rule_id IS NULL)
    OR
    (source = 'learned' AND agent_run_id IS NULL AND learned_rule_id IS NOT NULL)
  )
) STRICT;

CREATE TABLE price (
  commodity_id   TEXT NOT NULL,
  quote_currency TEXT NOT NULL,
  date           TEXT NOT NULL,
  price_minor    INTEGER NOT NULL,
  source         TEXT NOT NULL,        -- statement | manual | yahoo
  PRIMARY KEY (commodity_id, quote_currency, date, source)
) STRICT;

CREATE TABLE corporate_action (       -- 不建这张表，拆股会让持仓对账每次误报
  id           TEXT PRIMARY KEY,
  commodity_id TEXT NOT NULL,
  ex_date      TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN
                 ('split','reverse_split','dividend','drip',
                  'return_of_capital','spinoff','merger')),
  ratio_num INTEGER, ratio_den INTEGER,   -- 精确有理数
  cash_per_unit_minor INTEGER,
  resulting_commodity_id TEXT,
  applied_txn_id TEXT REFERENCES txn(id)
) STRICT;

-- 2026-08-08 A1 扩展：Agent 只写提案审计；人工审批与 override 同事务。
CREATE TABLE "agent_proposal_run" (
  id               TEXT PRIMARY KEY
                     CHECK (length(id) = 71
                            AND substr(id, 1, 7) = 'sha256:'
                            AND substr(id, 8) NOT GLOB '*[^0-9a-f]*'),
  ledger_revision  TEXT NOT NULL
                     CHECK (length(ledger_revision) = 71
                            AND substr(ledger_revision, 1, 7) = 'sha256:'
                            AND substr(ledger_revision, 8) NOT GLOB '*[^0-9a-f]*'),
  schema_version   INTEGER NOT NULL CHECK (schema_version IN (1, 2)),
  application_mode TEXT CHECK (
                     (schema_version = 1 AND application_mode IS NULL)
                     OR
                     (schema_version = 2
                      AND application_mode IN ('review_first','automatic'))
                   ),
  client           TEXT NOT NULL CHECK (client IN ('codex','claude-code','other')),
  client_version   TEXT CHECK (client_version IS NULL OR length(client_version) <= 200),
  model_reported   TEXT CHECK (model_reported IS NULL OR length(model_reported) <= 200),
  created_at       TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'open'
                     CHECK (state IN ('open','completed','dismissed'))
) STRICT;

CREATE TABLE "agent_category_proposal" (
  run_id                  TEXT NOT NULL
                            REFERENCES "agent_proposal_run"(id) ON DELETE CASCADE,
  txn_id                  TEXT NOT NULL REFERENCES txn(id),
  group_id                TEXT NOT NULL
                            CHECK (length(group_id) = 71
                                   AND substr(group_id, 1, 7) = 'sha256:'
                                   AND substr(group_id, 8) NOT GLOB '*[^0-9a-f]*'),
  suggested_category_id   TEXT NOT NULL REFERENCES category(id),
  outcome                 TEXT NOT NULL DEFAULT 'pending'
                            CHECK (outcome IN
                                   ('pending','accepted','edited','rejected','withdrawn')),
  applied_category_id     TEXT REFERENCES category(id),
  reviewed_at             TEXT,
  PRIMARY KEY (run_id, txn_id),
  CHECK (
    (outcome = 'pending' AND applied_category_id IS NULL AND reviewed_at IS NULL)
    OR
    (outcome = 'rejected' AND applied_category_id IS NULL AND reviewed_at IS NOT NULL)
    OR
    (outcome IN ('accepted','edited','withdrawn')
     AND applied_category_id IS NOT NULL AND reviewed_at IS NOT NULL)
  )
) STRICT;

CREATE INDEX agent_category_proposal_txn
  ON agent_category_proposal(txn_id);

CREATE INDEX agent_category_proposal_run_outcome
  ON agent_category_proposal(run_id, outcome);

-- 2026-08-09 A6.5 C2 扩展：剩余覆盖率分流是独立审计，不是分类提案。
CREATE TABLE agent_triage_run (
  id               TEXT PRIMARY KEY
                     CHECK (length(id) = 71
                            AND substr(id, 1, 7) = 'sha256:'
                            AND substr(id, 8) NOT GLOB '*[^0-9a-f]*'),
  ledger_revision  TEXT NOT NULL
                     CHECK (length(ledger_revision) = 71
                            AND substr(ledger_revision, 1, 7) = 'sha256:'
                            AND substr(ledger_revision, 8) NOT GLOB '*[^0-9a-f]*'),
  scope_revision   TEXT NOT NULL
                     CHECK (length(scope_revision) = 71
                            AND substr(scope_revision, 1, 7) = 'sha256:'
                            AND substr(scope_revision, 8) NOT GLOB '*[^0-9a-f]*'),
  schema_version   INTEGER NOT NULL CHECK (schema_version = 1),
  since            TEXT CHECK (since IS NULL OR length(since) = 10),
  until            TEXT CHECK (until IS NULL OR length(until) = 10),
  client           TEXT NOT NULL CHECK (client IN ('codex','claude-code','other')),
  client_version   TEXT CHECK (client_version IS NULL OR length(client_version) <= 200),
  model_reported   TEXT CHECK (model_reported IS NULL OR length(model_reported) <= 200),
  created_at       TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'open'
                     CHECK (state IN ('open','completed','dismissed'))
) STRICT;

CREATE TABLE agent_triage_item (
  run_id                  TEXT NOT NULL
                            REFERENCES agent_triage_run(id) ON DELETE CASCADE,
  txn_id                  TEXT NOT NULL REFERENCES txn(id),
  group_id                TEXT NOT NULL
                            CHECK (length(group_id) = 71
                                   AND substr(group_id, 1, 7) = 'sha256:'
                                   AND substr(group_id, 8) NOT GLOB '*[^0-9a-f]*'),
  route                   TEXT NOT NULL
                            CHECK (route IN
                                   ('possible_transfer','taxonomy_gap','uncertain')),
  reason_code             TEXT NOT NULL,
  outcome                 TEXT NOT NULL DEFAULT 'pending'
                            CHECK (outcome IN
                                   ('pending','confirmed_transfer','confirmed_taxonomy_gap',
                                    'left_uncertain','classified_existing','stale','withdrawn')),
  applied_category_id     TEXT REFERENCES category(id),
  reviewed_at             TEXT,
  PRIMARY KEY (run_id, txn_id),
  CHECK (
    (route = 'possible_transfer' AND reason_code IN
      ('payment_rail_ownership_unknown','account_movement_language',
       'debt_or_card_settlement','investment_platform_flow'))
    OR
    (route = 'taxonomy_gap' AND reason_code IN
      ('repeated_cluster_without_category','coherent_activity_missing',
       'current_category_too_broad'))
    OR
    (route = 'uncertain' AND reason_code IN
      ('descriptor_ambiguous','counterparty_role_unknown','mixed_signal',
       'insufficient_context','one_off_unresolved'))
  ),
  CHECK (
    (outcome = 'pending' AND applied_category_id IS NULL AND reviewed_at IS NULL)
    OR
    (outcome IN ('confirmed_taxonomy_gap','left_uncertain','stale')
     AND applied_category_id IS NULL AND reviewed_at IS NOT NULL)
    OR
    (outcome IN ('confirmed_transfer','classified_existing','withdrawn')
     AND applied_category_id IS NOT NULL AND reviewed_at IS NOT NULL)
  )
) STRICT;

CREATE INDEX agent_triage_item_txn
  ON agent_triage_item(txn_id);

CREATE INDEX agent_triage_item_run_outcome
  ON agent_triage_item(run_id, outcome);

CREATE INDEX agent_triage_item_run_route
  ON agent_triage_item(run_id, route);

-- 2026-08-10 A7.3：本地同意策略与 aggregate-only MCP session 证据。
CREATE TABLE agent_local_policy (
  id                         INTEGER PRIMARY KEY CHECK (id = 1),
  selected_client            TEXT CHECK (selected_client IN ('codex', 'claude-code')),
  application_mode           TEXT NOT NULL DEFAULT 'automatic'
                                   CHECK (application_mode IN ('review_first', 'automatic')),
  enabled                    INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
  auto_classify_new_imports  INTEGER NOT NULL DEFAULT 1 CHECK (auto_classify_new_imports IN (0, 1)),
  updated_at                 TEXT NOT NULL,
  CHECK (enabled = 0 OR selected_client IS NOT NULL)
) STRICT;

CREATE TABLE agent_local_session (
  id               TEXT PRIMARY KEY,
  client           TEXT NOT NULL CHECK (client IN ('codex', 'claude-code')),
  started_at       TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  ended_at         TEXT,
  result_state     TEXT NOT NULL DEFAULT 'none'
                        CHECK (result_state IN ('none', 'completed', 'partial', 'failed')),
  result_at        TEXT,
  candidate_count  INTEGER CHECK (candidate_count IS NULL OR candidate_count >= 0),
  submitted_count  INTEGER CHECK (submitted_count IS NULL OR submitted_count >= 0),
  error_code       TEXT,
  CHECK (
    (result_state = 'none'
      AND result_at IS NULL
      AND candidate_count IS NULL
      AND submitted_count IS NULL
      AND error_code IS NULL)
    OR
    (result_state = 'completed'
      AND result_at IS NOT NULL
      AND candidate_count IS NOT NULL
      AND submitted_count = candidate_count
      AND error_code IS NULL)
    OR
    (result_state = 'partial'
      AND result_at IS NOT NULL
      AND candidate_count IS NOT NULL
      AND submitted_count IS NOT NULL
      AND submitted_count < candidate_count
      AND error_code IS NULL)
    OR
    (result_state = 'failed'
      AND result_at IS NOT NULL
      AND candidate_count IS NULL
      AND submitted_count IS NULL
      AND error_code IS NOT NULL)
  )
) STRICT;

CREATE INDEX agent_local_session_client_seen
  ON agent_local_session(client, last_seen_at DESC);

CREATE INDEX agent_local_session_client_result
  ON agent_local_session(client, result_at DESC)
  WHERE result_state <> 'none';
```

**黄金层 = 视图**：`v_cashflow_monthly`、`v_networth_daily`、`v_holdings_asof(date)`、`v_category_spend`。单人工具不需要 dbt。

### 3.3 幂等键

```python
NATURAL_KEY_VERSION = 1
SEP = "\x1f"          # ★ 不可打印分隔符

def natural_key(account_id, posted_date, amount_minor, description, occurrence_index):
    raw = SEP.join([
        account_id,                    # 我们自己的 ID，不是银行的
        posted_date,                   # ISO 8601
        str(amount_minor),             # 有符号整数
        normalize_descriptor(description),
        str(occurrence_index),         # 0,1,2… 同键内序号
    ])
    return hashlib.sha256(raw.encode()).hexdigest()
```

四个必须点：

1. **必须有分隔符** —— 没有的话 `("ABC","12")` 和 `("ABC1","2")` 会碰撞。`ofxstatement` 的 `sha1(date+memo+amount)` 正是这个缺陷。
2. **必须有 `occurrence_index`** —— 同一天两杯 $4.75 咖啡是两笔交易，不是重复。
3. **内容哈希，绝不用行号** —— 重新下载时行序会变。
4. **`natural_key` 与 `source_id` 并存，永不合并。**

**为什么 FITID 不可信**（结构性原因，非轶事）：规范只保证 `(FI, account)` 内唯一；其设计目的只是单次响应内去重；规范自带 `CORRECTFITID`/`CORRECTACTION`，等于承认 ID 会被取代；**pending → posted 会换 ID**（Plaid 有 `pending_transaction_id` 正是因为过账后的记录 ID 不同）。

**去重算法**（抄 Actual 的三趟匹配）：

1. `source_id` 精确匹配
2. 金额相同 + 日期 ±7 天 + payee 相同
3. 金额相同 + 日期 ±7 天 + 任意未匹配行

按日期接近度排序候选。**已知 bug 必须从第一天就防**：两笔各自带**不同** `source_id` 的交易**永远不能模糊匹配**（Actual #2562）。

---

## 4. 摄入管线

### 4.1 流程

```
① 摄入   SHA-256 → 已存在则直接返回「已导入」（幂等，零副作用）
         原件复制进 archive/，只读权限
② 识别   PDF /Producer + 首页标记 → 带版本的布局配置
         ★ 未知布局 → 审核队列，绝不猜
③ 抽取   pdfplumber extract_words() + 列 x 区间锚定
         每个字段保留 (page, x0, top, x1, bottom)
④ 对账   §4.3 五层断言，硬失败
         全过 → 自动入账 | 任一 block 失败 → 审核队列
⑤ 入账   幂等键去重 → 单边转复式 → 写 txn/posting
⑥ 导出   重新生成 export/ledger.beancount（可选跑 bean-check）
```

### 4.2 Chase Checking 解析器的关键点

前身项目的致命 bug：用「块内第一个数字 = 金额，第二个 = 余额」的**文本顺序**启发式。Chase 存款行的金额列被 PDF 抽取甩到了另一个文本块，块内只剩余额 → 余额被当成金额。

**正确做法：按 x 坐标绑定列。**

```python
# 列区间从表头 "AMOUNT" / "BALANCE" 的 x 位置学习，不写死
cols = detect_columns(page)        # {'date':…, 'desc':…, 'amount':…, 'balance':…}
for word in page.extract_words():
    col = classify_by_x(word, cols)
```

**兜底**：当某行只解析到一个数字时，用 `amount = balance_n − balance_{n−1}` 反推——已在真实 13 张账单上验证，恢复出的存入总额 **$58,725.12 与 Chase 自报分毫不差**。

**其他必修点**：

- 账单月份取周期**结束**日（前身取起始日，导致 2025-06/09/12 三个月在输出里根本不存在）
- `SKIP_PATTERNS` 改**整行精确匹配**，不用子串（`"of"` 会吃掉 `House of Sushi`、`Coffee Shop`）
- 金额正则要求必须有 `\.\d{2}`（否则支票号会被当金额）
- 每个 PDF 独立 try/except，失败不影响其他文件
- `sys.stdout.reconfigure(encoding="utf-8")`
- 写文件用临时文件 + `os.replace` 原子替换

**已验证的前提**：Chase 对账单 `/Producer = OpenText Output Transformation Engine 23.4.25`，**有完整文本层**（首页 395 词）。**不需要 OCR。** OCR 分支写成"永远不该触发"，一旦触发就告警——那意味着格式变了。

### 4.3 对账断言（按强度排序）

| # | 检查 | 级别 | 说明 |
|---|---|---|---|
| 0 | `SUM(posting.amount_minor) GROUP BY txn == 0` | block | 复式零和，结构性 |
| 1 | **逐行余额链走查** `bal[n-1] + amt[n] == bal[n]` | block | **最强、免费——Chase 已经把余额印在账单上了** |
| 2 | `期初 + Σ金额 == 期末` | block | 必要但**不充分**：两个方向相反的等额错误会互相抵消 |
| 3 | 账单自报分项小计（存入/取款/费用） | block | 正是它把前身的 bug 精确定位到收入侧 |
| 4 | 笔数 vs 账单声明笔数 | warn | 不是所有账单都印 |
| 5 | 日期落在账期内；账期月月连续无缺口 | warn | 检测漏传的月份 |
| 6 | 页面连续性（"continued" 标记、页眉重复） | warn | 检测漏页 |

**投资账户补充（P4）：**

| # | 检查 | 说明 |
|---|---|---|
| 7 | `shares_end[t-1] == shares_begin[t]` | ⚠️ **拆股会破坏它**——必须查 `corporate_action` 豁免，否则每次拆股都误报 |
| 8 | `price × shares == 行市值`；`Σ行市值 == 声明总市值` | |
| 9 | `期初市值 + 净流入 + 收益 + 涨跌 == 期末市值` | |
| 10 | 成本基础完整性 | 券商自己都免责声明"仅供参考、不可用于报税"，不要当权威目标 |

**失败时的产物**必须是结构化的、人能看懂的：

```json
{
  "check_id": "balance_chain",
  "severity": "block",
  "message": "2025-01 账单第 2 页：余额链断裂",
  "detail": {"row": 3, "expected": 857.26, "actual": 820.15, "diff": 37.11,
             "page": 2, "bbox": [412.0, 318.5, 461.2, 328.1]}
}
```

---

## 5. 分析层

### 5.1 分类引擎

前身的三个真 bug 及对策：

| 前身 bug | 影响 | 对策 |
|---|---|---|
| `"chase"` 是 `"Purchase"` 的子串 | 68 笔 / $11,726 错归"银行费用"（真实约 $533，虚高 23 倍），且成了静默兜底类 | **词边界匹配** `\bchase\b` |
| 裸写 `"76"`（加油站） | 16 笔 ACH/Zelle 被吸进"交通" | 同上 + 规则需声明匹配模式 |
| 规则埋在 5092 行 HTML 第 4373 行，对象键顺序隐式决定优先级 | 加一条规则会静默重分类无关交易 | 移到 `rules/categories.json`，**优先级显式声明**为字段 |
| 完全没有用户覆盖机制 | 想改一笔？不可能 | `category_override` 表 + UI |

规则文件形状：

```json
{
  "version": 1,
  "categories": [
    {"id": "dining", "kind": "expense", "priority": 50,
     "rules": [{"type": "word", "patterns": ["chipotle", "starbucks"]},
               {"type": "regex", "pattern": "\\bsushi\\b"}]}
  ]
}
```

**分类只算一次**，结果写进 `posting.category_id`；用户覆盖走 `category_override` 表并在读取时 LEFT JOIN 覆盖。不要每次页面加载都重算 234 次 `includes()`。

### 5.2 内部转账识别（最高价值的单点修复）

前身把转账计入收支，导致 **82.6% 的"收入"和 77.5% 的"支出"是转账**，饼图里最大的"消费类别"是"转账 $31,493"，储蓄率 78% 完全失真。

三种识别方式，按可靠度：

1. **两侧都有账本**：金额相反 + 日期 ±3 天 + 双方 `is_own_account=1` → 配成**一笔 txn 两条 posting**，结构上不可能双计
2. **只有一侧**（信用卡还款、Zelle 给自己）：规则匹配 → `txn.is_transfer = 1`
3. **人工标记**：UI 上一键标为转账

所有收支聚合一律 `WHERE txn.is_transfer = 0`。

### 5.3 真·订阅检测

前身的 `detectAndRenderSubscriptions(data)` **参数从未被使用**，返回硬编码 10 行表——上传任何数据都显示同样内容。

正确做法：按规范化商户名分组 → 找出**≥3 次、间隔约 28–31 天、金额相近（±10%）**的序列 → 年化用**实际计费月数**，不是 `×12`。

### 5.4 其他必修

- **年化不要写死 `/13`**（前身如此），用实际数据跨度天数
- **日期解析** `new Date(t.date + 'T00:00:00')` 或 `getUTCDay()`，否则太平洋时区星期分布整体错一天
- **月份口径统一**：图表用 `date.substring(0,7)`、表格用 `statement_month`，前身两者在 415 行里有 83 行（20%）不一致
- **余额曲线用真实 `balance`**，不是从 0 累加过滤后净额（前身解析出的 `balance` 字段全程没被读过一次）
- **图表先聚合再画**：月度点 13 个，不是逐笔 415 个（5 万行时这是唯一真正的性能杀手）
- **金额格式化**用 `Intl.NumberFormat({style:'currency'})`，前身产出 `$-12.44`
- **`innerHTML` 插值必须转义**：商户名/Zelle 备注是第三方可控文本

---

## 6. 后端 API

绑 `127.0.0.1`，默认端口 `8787`，**不监听 0.0.0.0**。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/upload` | multipart。返回 `{status: imported\|duplicate\|needs_review, file_id, checks[]}` |
| GET | `/api/review` | 待审核队列 |
| POST | `/api/review/{id}/resolve` | 人工放行 / 修正 / 丢弃 |
| GET | `/api/transactions` | 分页、筛选、排序（**服务端做**，不是把全量塞进 JS） |
| PATCH | `/api/transactions/{id}` | 改分类 / 标记转账 |
| GET | `/api/analytics/summary` | KPI |
| GET | `/api/analytics/monthly` | 月度序列（**已聚合**） |
| GET | `/api/analytics/categories` | 分类占比 |
| GET | `/api/analytics/subscriptions` | 订阅检测结果 |
| GET | `/api/accounts` | |
| POST | `/api/export/beancount` | 生成纯文本导出 |
| GET | `/api/health` | 版本、schema 版本、数据目录、待审核数 |

**安全约束**：

- 只绑回环地址；文档明确警告不要暴露到局域网
- 上传大小上限（默认 50 MB）、扩展名与 magic bytes 双重校验
- 无遥测、无外呼、无 CDN
- 响应头 `Content-Security-Policy: default-src 'self'`
- 前端全部资源本地 vendor

---

## 7. 分阶段路线图

### P0 —— 地基与正确性（必须一次做对）✅ 2026-08-03 完成

范围：仓库骨架、配置与运行时守卫、schema + 迁移框架、Chase Checking 解析器、对账引擎、CLI 摄入。**无 Web、无前端。**

**验收标准（硬性、可自动化）—— 全部通过，由独立 agent 用三条互不相关的路径复核：**

- [x] 13 张真实 Chase 账单全部通过全部 block 级断言
- [x] 总入账 == **$58,725.12**（Chase 自报合计）
- [x] 总支出 == **−$58,937.52**
- [x] 净变动 == **−$212.40**
- [x] 重放后期末余额 == **$288.71**（2026-01 账单期末）
- [x] `statement_month` 出现 **13 个不同值**（含 2025-06/09/12）
- [x] 同一 PDF 摄入 3 次 → 数据库行数不变
- [x] 把同一批 PDF 从两个目录各摄入一次 → 无重复
- [x] 故意改坏一个金额 → 对账 block 失败并进审核队列
- [x] 中文路径不崩
- [x] `test_rebuild.py`：删库 → 从 archive/ 重建 → 与重建前逐行一致

> P0 完成时，你手上的数字才第一次是对的。**在此之前不要建任何 UI。**

实施期间超出原范围额外完成：beancount 导出（`ledgerbox export beancount`，经真实 `bean-check` 校验）、期初分录、仓库卫生守卫、以及若干本属 P5 的发布物。**实施期间新增的设计决策见 [`docs/STATUS.md`](STATUS.md) §5 —— 其中若干处偏离了本文件，都在那里给了理由。**

### P1 —— 本地服务 + 上传 + 审核队列

范围：FastAPI、上传端点、审核队列 API + UI（最小可用）、`python -m ledgerbox` 启动器。

**验收：** 浏览器拖入 PDF → 几秒内看到「已导入 47 笔 / 全部对账通过」或「需要审核：第 2 页余额链断裂，差 $37.11」。重复拖同一个文件 → 「已导入过」。

### P2 —— 分析与前端

范围：分类引擎、转账识别、订阅检测、analytics 纯函数 + 单测、Dashboard、交易明细表、i18n。视觉借鉴旧版，**逻辑全部重写**。

**验收：**
- [ ] 储蓄率、月度趋势、分类占比与手工核算逐项一致
- [ ] 转账不计入收支（"转账"不再出现在消费饼图里）
- [ ] 单笔改分类后刷新页面仍生效
- [ ] 断网可完整使用
- [ ] `analytics.js` 单测覆盖率 ≥ 90%

### P3 —— 通用 CSV 导入 + 插件化

范围：CSV 列映射向导（预览 + 记住映射模板）、parser 插件注册表、`docs/ADDING_A_BANK.md`。

**验收：** 一份从未见过的银行 CSV，通过 UI 映射后正确入账；映射模板可复用。

### P4 —— 投资账户（**已决定：暂不实施**）

范围：`lot` / 成本基础 / 持仓、券商 CSV 导入、持仓对账（含 `corporate_action` 豁免）、净值曲线。

**状态：2026-08-02 决定不做。** 理由：没有真实券商对账单样本，写出来的解析器等于幻觉。

**但 schema 保留全部投资建模能力**（`commodity` / `lot` / `posting.quantity_scaled` / `posting.cost_*` / `corporate_action`）。这些列现在就在库里，未来接投资账户不需要迁移数据——事后加这些列会极其痛苦，所以现在留着，代价只是几张空表。

**重新启动的前置条件：** 拿到真实券商对账单样本。

**届时的验收：** 持仓数量与市值和对账单一致；一次拆股不产生误报。

### P5 —— 开源发布

范围：

- [ ] 合成数据生成器（抄 `bean-example`：双周薪资含预扣与 401k、月租带波动、投资账户带价格序列、年度报税）——**不可能含真实数据，因为它从来没有过**
- [ ] 脱敏 CLI `tools/sanitize.py`（真实 PDF → 可提交的 span JSON）
- [ ] 文本层 fixture 套件 + 故意损坏的输入
- [ ] README（含 §11 自动化章节）
- [ ] `LICENSE`（AGPL-3.0-or-later）、`SECURITY.md` + 启用 Private Vulnerability Reporting、`CONTRIBUTING.md`（**含"永远不要在 issue/PR 里附真实对账单"及脱敏配方**）、`docs/THREAT_MODEL.md`
- [ ] CI：3 OS × 3 Python、ruff、mypy、pytest、gitleaks、TruffleHog、**数据文件硬检查**
- [ ] `uvx ledgerbox` 可跑

**发布前最后一道闸：**

```bash
git ls-files | grep -Ei '\.(pdf|csv|ofx|qfx|db|sqlite3?|xlsx?)$' | grep -v '^tests/fixtures/'
# 必须为空
gitleaks detect --no-git --source .
```

---

## 8. 测试策略

### 8.1 分层

| 层 | 对象 | 数据来源 |
|---|---|---|
| 单元 | `natural_key`、金额解析、日期、分类规则、聚合函数 | 内联 |
| 抽取 | `extract_spans(pdf)` | 少量**合成** PDF |
| 解析 | `spans_to_transactions(spans)` ← **所有银行逻辑在这** | 大量 **span JSON** |
| 对账 | 每条断言的正例与反例 | 构造 |
| 集成 | 完整管线 + 重建不变式 | 合成 |
| 前端 | `analytics.js` 纯函数 | Node + 内联 |

### 8.2 关键决策：提交文本层，不提交 PDF

把 `(text, x0, top, x1, bottom)` 序列化成 JSON 提交。

- diff 里**可读**，体积小
- **你能有把握地脱敏——PDF 你做不到**（内容流、内嵌字体、XMP 元数据、增量更新历史全都会漏）
- 贡献者提交一份 span JSON + 期望输出，评审者无需看到真实账单就能判断对错

黄金文件用 **pytest-regressions 的 `dataframe_regression`**：解析产物是一张交易表，它给逐列逐行的 diff；syrupy 的 `.ambr` 在 200 行表上没法看。

### 8.3 银行适配器插件化（抄 ofxstatement）

每家银行 = 独立插件包 + 自己的 fixture 仓库。**别人拿着自己的账单维护自己的 fixture，你永远不碰他们的数据。**

### 8.4 自带 fixture

`LEDGERBOX_REAL_FIXTURES` 环境变量指向**仓库之外**的目录，默认 skip，永不 fail。这样你本地能用 13 张真实账单跑回归，CI 上不需要它们。

---

## 9. 安全与隐私

### 9.1 数据位置（架构级控制，不是 `.gitignore`）

```python
from platformdirs import user_data_dir
from pathlib import Path

def resolve_data_dir(override: str | None = None) -> Path:
    d = Path(override) if override else Path(user_data_dir("ledgerbox"))
    # 运行时守卫：拒绝往 git 仓库里写用户数据
    for p in [d, *d.parents]:
        if (p / ".git").exists():
            raise SystemExit(
                f"拒绝写入 {d}：该路径位于 git 仓库内。\n"
                f"财务数据不应放在版本控制目录。请用 --data-dir 指定别处，"
                f"或阅读 docs/THREAT_MODEL.md 的 portable 模式说明。"
            )
    d.mkdir(parents=True, exist_ok=True)
    return d
```

五行代码，把一类事故变成一条错误信息。

### 9.2 威胁模型（`docs/THREAT_MODEL.md` 要诚实写明）

| 项 | 说明 |
|---|---|
| 存什么 | 交易明细、余额、商户名、对手方姓名、账单 PDF 原件（含账号、姓名、地址） |
| 静态加密 | **无**，除非你的磁盘卷本身加密（BitLocker / FileVault / LUKS）。明确说 |
| 网络 | 只绑 `127.0.0.1`。**零外呼、零遥测、零 CDN** |
| 认证 | 无。安全边界是"这台机器的本地用户" |
| 不在范围内 | 恶意本地用户、被入侵的操作系统、恶意浏览器扩展 |

> 一份简短诚实的威胁模型，在这个品类里比任何徽章都更能建立信任。

### 9.3 开源前的清理

**旧项目不能靠删文件开源**——数据在 6 处副本里，git 历史会永久保留。**新仓库、无历史**，理由：

1. **PII ≠ 凭据**：密钥可轮换，你的交易史/账号/收入/住址**永久有效**
2. 重写历史的失败模式很弱：改过名的路径、粘在 commit message 里的余额、**文件名本身泄露**（旧项目 26 个文件名都带账号后四位）、notebook 输出——`--invert-paths` 一个都抓不到
3. 未发布的个人项目，历史价值接近零
4. **唯一有干净证明的方案**：在 `.gitignore` 和数据隔离架构就位**之后**创建的、只有一个 commit 的新仓库，可以靠肉眼检查证明干净

**特别注意**：旧项目 `financial_dashboard.html:4385` 把卡号后四位写成了分类关键词，`4374/4380/4382` 硬编码约 60 个本地商户名——**PII 烤进了逻辑里，删数据也带不走**。移植分类规则时必须逐条清洗。

### 9.4 许可证

**AGPL-3.0-or-later + DCO。**

- 同类项目共识（Firefly III / Ghostfolio / Maybe 都是 AGPL），不需要辩护
- DCO 而非 CLA：单人项目付不起贡献摩擦
- **在第一个外部 PR 之前决定**——一旦以无 CLA 的 AGPL 接受外部贡献，你**永远**无法在没有每位贡献者同意的情况下提供商业许可
- 不用 BUSL/ELv2/SSPL：对一个卖点是"这是你的钱，你可以审计"的工具，source-available 直接摧毁价值主张
- 加 SPDX 头，全文放根 `LICENSE`

**交叉约束**：走 AGPL 就不必回避 PyMuPDF/beancount 的传染性，但**本方案仍选 pdfplumber(MIT) + beancount 子进程**，为的是保留未来改用宽松许可证的选项。

---

## 10. 从旧项目迁移

**不迁移代码，只迁移知识。**

| 迁移 | 不迁移 |
|---|---|
| Dashboard 视觉与交互设计 | 任何 JS 逻辑（14 个已确认 bug） |
| 分类关键词表（**逐条清洗 PII**） | `categoryRules` 对象本身 |
| 中英双语文案 | i18n 实现（翻译塞在 category key 里用 `split('/')` 拆） |
| 「余额链可反推金额」这一发现 | 解析器启发式（核心是错的） |
| 13 张真实 PDF → 本地 fixture（**仓库外**） | 任何 CSV / JS / XLSX 派生数据（6 份副本） |

**旧数据的处置**：用新管线重新摄入 13 张原始 PDF，不要导入旧 `transactions.csv`（收入侧全错）。原始 PDF 是唯一可信来源。

---

## 11. README 里的自动化章节（`docs/AUTOMATION.md`）

我们不实现自动化，但把调研结论完整交给用户，让他们能拿去让 Claude / Codex 自己实现。章节大纲：

### 11.1 官方渠道的现实（2026-08 核实）

- **Chase 没有面向消费者的 API。** `developer.chase.com` 整站锁在 JPMC 企业 SSO 后；JPM Payments 自助注册只给文档和 Mock，生产要走销售 4–6 周 KYC
- **OFX / Direct Connect 已死。** `ofx.chase.com` 的 CNAME 目标 DNS 返回 NXDOMAIN。Chase 已转 EWC+（银行托管 OAuth，token 存在 Quicken 的聚合伙伴处）——**你拿不到任何可交给第三方客户端的凭据**，GnuCash / ofxtools 无路可走
- **FDX 卖的是规范，不是数据。** 2025-01-08 获 CFPB 承认（有效期至 2030-01-08），个人会员 $99/年、Observer 免费——但 FDX **不运营任何数据接口**，且强制 FAPI 1.0 Advanced + mTLS + 动态客户端注册，**自然人不是可注册的 OAuth 客户端**
- **CFPB 1033 规则被法院禁止执行中**（2025-10-29，*Forcht Bank v. CFPB*）。CFPB 重新立法中，短期内不要指望

### 11.2 现实可行的路径

**银行侧：SimpleFIN Bridge，$15/年。** 唯一一个个人今天就能注册、不需要公司实体/KYB/销售/MSA 的选项。**协议只读**（无支付面，token 泄露动不了钱）、Access URL 存本机、可单方面吊销、覆盖 25 家机构。

代价必须写清楚：**Chase 凭据存在 MX（第三方聚合商）**——"local-first" 说的是数据落在哪，不是凭据存在哪；配额 24 次/天；单次最多跨 90 天；**首次同步历史深度未公布**，做好只有 90 天的准备——**更早的数据仍然要靠本项目的 PDF 管线**。

**券商侧：IBKR Flex Web Service 是唯一能无人值守跑 cron 的。** 自助开关、token 可设 6 小时–1 年、支持 IP 白名单、两个 GET 拿 XML、**含批次级成本基础**。Schwab 个人开发者虽开放，但 **refresh token 7 天硬过期不可续**，必须每周手动浏览器登录。E\*TRADE 更糟（每天美东午夜过期）。Fidelity / Vanguard / Merrill / Robinhood 股票**无官方 API**。

**劝退**：Plaid（免费额度后是未公开定价 + 销售，且 Plaid Portal **不能导出**你的数据）、MX / Akoya / Finicity / Yodlee（纯企业）、GoCardless（**已关闭新注册**且只覆盖欧盟）。

### 11.3 MCP 安全警告（必须醒目）

- **官方 Plaid MCP 硬编码 sandbox**，读不了真实账户（设计如此）
- 银行/券商官方 MCP **基本不存在**——`schwab`/`fidelity`/`InteractiveBrokers`/`SnapTrade`/`simplefin`/`ynab`/`actualbudget`/`beancount` 的 GitHub 组织 MCP 仓库搜索**全部返回 0**
- **最流行的两个社区服务器恰好最危险**：`code-rabi/interactive-brokers-mcp`（205★）索要 `IB_USERNAME` + `IB_PASSWORD_AUTH` + **`IB_TOTP_SECRET`**。**交出 TOTP 种子 = 2FA 变成装饰品。星标是流行度信号，不是安全信号**

**社区 MCP 服务器具体是什么**：一个无沙箱的本地进程，你的密钥在它环境变量里；每次工具调用都是外泄机会（合法调 `api.plaid.com` 和顺便 POST 到别处，在网络上长得一模一样）；**返回的数据是提示注入通道**（一条交易备注就能进你的模型上下文，而好几个这类服务器默认开写工具）；`npx -y` / `:latest` 每次启动重新解析版本。

**安全形状——本项目天然就是它：**

```
[取数器: SimpleFIN URL / IBKR token / PDF]  ← 持有全部密钥，cron 运行
        ↓
   ledger.db  (你的磁盘)
        ↑  只读连接，PRAGMA query_only=ON
[你自己写的 MCP server]  ← 零凭据，零网络出口
        ↑
      Claude
```

⚠️ **2026 年没有官方 SQLite MCP**——官方仓库 `src/` 只剩 7 个（`everything`/`fetch`/`filesystem`/`git`/`memory`/`sequentialthinking`/`time`），SQLite 那个已归档移到 `servers-archived`，冻结约 14 个月。**这 ~150 行得自己写**：包一个只读 sqlite3 连接、`PRAGMA query_only=ON`、暴露一个 `query` 工具。

考虑到替代方案是把券商密码交给陌生人，这个交易非常划算。**不要 `npx -y` 你的券商密码。**

### 11.4 给 Claude / Codex 的实现提示

提供一段可直接粘贴的 prompt 骨架：目标接口（`fetchers/` 目录、`Fetcher` 协议、输出必须走与 PDF 相同的对账管线）、密钥放系统钥匙串而非 `.env`、cron 错峰与窗口重叠 5 天、失败告警。**强调：抓来的数据同样必须过 §4.3 的全部断言，不能因为"来自 API"就跳过对账。**

---

## 12. 已定决策（2026-08-02）

| # | 问题 | 决定 |
|---|---|---|
| 1 | 项目名 | **`ledgerbox`** |
| 2 | 许可证 | **AGPL-3.0-or-later + DCO**。在第一个外部 PR 之前不可更改 |
| 3 | 数据目录 | **默认 XDG**（`%LOCALAPPDATA%\ledgerbox`），`--data-dir` 与 portable 模式作为文档化选项 |
| 4 | 投资账户（P4） | **暂不实施**。schema 保留能力，解析不做 |
| 5 | 支持范围 | **仅 Chase Checking PDF**（P0–P2）+ 通用 CSV 导入（P3）。**必须在 README 显著位置声明** |
| 6 | 自动化取数 | **不实施**，只在 `docs/AUTOMATION.md` 提供调研成果与实现思路 |

**实施顺序：P0 → P1 → P2 → P3 → P5。跳过 P4。**
