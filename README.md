# Polymarket 低价长周期挂单策略

在 Polymarket 上，对**结算时间远、价格极低**的市场（小概率事件）挂被动买单赚取成交机会，成交后自动挂卖单出场。

核心思路：远离结算的市场上，`bestBid < 0.05` 的 token 风险很小（最坏亏掉每份几分钱），被动挂在 `bestBid` 等成交；一旦成交形成持仓，再对标 `bestAsk` 挂被动卖单出场。

---

## 目录

1. [策略逻辑](#策略逻辑)
2. [文件结构](#文件结构)
3. [环境准备](#环境准备)
4. [配置凭据](#配置凭据)
5. [使用方法](#使用方法)
6. [参数配置](#参数配置)
7. [数据文件](#数据文件)
8. [关键实测经验](#关键实测经验)
9. [服务器部署](#服务器部署)

---

## 策略逻辑

### 买入侧（每轮循环）

1. **粗筛（Gamma）**：扫描 `gamma-api.polymarket.com/markets/keyset`，取 `结算时间 > 20 天` 且 `bestBid < 0.1` 的市场，拿到 token 列表 + `endDate`。这里 Gamma 的 `bestBid` 只做粗筛定位（放宽到 0.1 留余量），**不作为最终价格依据**（Gamma bestBid 实测不准确）。
2. **精筛（CLOB）**：用 `POST /prices` 批量取这些 token 的实时 `bestBid`，筛出 `< 0.05` 的。
3. **对比挂单**：
   - 候选里有、未挂单 → **挂买单** `10 份 @ bestBid`（post-only，被动，不追价）
   - 候选里有、已挂但 bestBid 变了 → **撤旧挂新**
   - 候选里有、已挂且价格没变 → 保持不动
   - 已挂但不再符合（bestBid ≥ 0.05 或 结算 < 20 天）→ **撤单清理**
4. **写回 CSV**：把本轮候选 + 挂单状态存入 `data/buy_state.csv`，供下轮对比。

### 卖出侧（每轮循环）

1. 查持仓 `list_positions`（份额 > 0）。
2. 对**份额 > 5** 的持仓，用 `POST /prices side=SELL` 取实时 `bestAsk`。
3. 对标 `bestAsk` 挂被动卖单（post-only），全部持仓卖出。已挂卖单价没变则保持，变了则撤旧挂新。

> 卖出最小份额限制：**份额 > 5 才能挂卖单**（低于 5 份会被 CLOB 拒绝，价格无限制）。持仓 ≤ 5 份时跳过不卖。

### 下单方式（关键）

- **单笔 `place_limit_order` + 全局限流**，而不是批量 `post_orders`。实测批量下单（15 单/请求）会触发 CLOB 匹配引擎「order timed out」超时，单笔 + 限流 20 单/秒稳定（成功率 99.9%）。
- 所有写操作（下单/撤单）过一把**全局限流锁**，间隔 `EXEC_INTERVAL` 秒。

---

## 文件结构

```
├── bot.py                  # ★ 主循环（买入 + 卖出 + CSV 状态 + 全局限流）
├── execution.py            # 用户已有的下单/撤单参考实现（线程池 + 全局限流）
├── place_test_orders.py    # 测试脚本：随机挑 N 个候选下单验证
├── scan_candidates.py      # 旧版冷扫描（已被 bot.py 内置扫描取代，保留参考）
├── poller.py               # 旧版热轮询（已被 bot.py 取代，保留参考）
├── probes/                 # 诊断探针（取价语义、限流、预取验证等，只读）
├── data/
│   └── buy_state.csv       # 运行时生成：买入状态（gitignored）
├── .env.bot1               # 凭据（gitignored，绝不提交）
├── .gitignore / .gitattributes
├── .mcp.json               # Polymarket 文档 MCP 配置
└── 新项目开发流程模板.md     # 项目搭建/部署流程模板
```

---

## 环境准备

本机用 Anaconda 全局环境即可（无需 venv），依赖：

```powershell
pip install polymarket-client python-dotenv websocket-client requests
```

> 实际开发用 `polymarket-client 0.5.0`，若 SDK 升级注意 API 可能变化。

---

## 配置凭据

在项目根目录创建 `.env.bot1`（已 gitignored，**绝不提交到 git**）：

```
SIGNER_PRIVATE_KEY=0x...                  # 签名者私钥
POLYMARKET_WALLET_ADDRESS=0x...           # 账户钱包地址
POLYMARKET_RELAYER_API_KEY=...            # Relayer API key（免 gas 交易）
POLYMARKET_RELAYER_API_KEY_ADDRESS=...    # Relayer API key 地址
```

Relayer API key 在 polymarket.com → Settings → API Keys → Relayer API Keys 创建。

---

## 使用方法

> ⚠️ 真实下单是真金白银。**务必先 `--dry-run` 确认无误，再 `--live`。**

### 基础命令

```powershell
# 1. 试跑（只计算打印，不真实下单/撤单）
python bot.py --dry-run --once

# 2. 真实跑一轮（下单/撤单/卖出，跑完退出）
python bot.py --live --once

# 3. 正式循环运行（每 1 小时自动一轮）
python bot.py --live --loop --interval 3600

# 4. 只处理前 N 个挂单（测试用，限制规模）
python bot.py --live --once --max 20
```

### 命令行参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `--live` | 真实下单/撤单 | 关闭 |
| `--dry-run` | 只计算打印，不真实操作 | 开启（默认） |
| `--interval N` | 循环间隔秒（配合 `--loop`） | `3600` |
| `--once` | 只跑一轮退出 | 关闭 |
| `--max N` | 每轮最多挂 N 单（测试用） | 无限制 |

> `--live` 与 `--dry-run` 互斥；不加 `--live` 就是 dry-run。

---

## 参数配置

所有参数在 `bot.py` 顶部的常量区，按需修改：

| 参数 | 值 | 说明 |
|---|---|---|
| `SHARES` | `10` | 每个 token 挂的买入份数 |
| `PRICE_CEIL` | `0.05` | 买入阈值：bestBid < 0.05（CLOB 实时价） |
| `SCAN_CEIL` | `0.1` | Gamma 粗筛停止阈值（放宽留余量） |
| `DAYS_AHEAD` | `20` | 只选结算 > 20 天的市场 |
| `EXEC_INTERVAL` | `0.05` | 两次写操作最小间隔秒（= 20 单/秒） |
| `PLACE_WORKERS` | `8` | 下单线程池大小 |
| `CANCEL_BATCH` | `100` | 批量撤单每批上限（≤ burst 120） |
| `PRICES_CHUNK` | `150` | `POST /prices` 每批 token 数 |
| `MAX_WORKERS` | `6` | 取价并发线程 |
| `PREFETCH_WORKERS` | `50` | 预取市场信息并发线程 |

### 常用调整

- **想更保守/更激进的价格**：改 `PRICE_CEIL`（如 `0.03` 更保守，`0.08` 更激进）。
- **想挂更多/更少份数**：改 `SHARES`。
- **结算时间更远**：改 `DAYS_AHEAD`（如 `30`）。
- **下单太快被限流**：调大 `EXEC_INTERVAL`（如 `0.1` = 10 单/秒）。
- **循环频率**：命令行 `--interval`（秒）。

---

## 数据文件

`data/buy_state.csv`（每轮写入，gitignored），字段：

| 字段 | 说明 |
|---|---|
| `token_id` | 市场的 YES 侧 token ID |
| `condition_id` | 市场条件 ID |
| `slug` | 市场 slug（问题描述） |
| `end_date` | 结算时间 |
| `bid` | 当前 bestBid（CLOB 实时价） |
| `order_id` | 当前挂单 ID（未挂则为空） |
| `order_price` | 当前挂单价 |

用途：持久化每轮状态，避免重复挂单、支持程序重启后对比、便于审计。

---

## 关键实测经验

> 这些是本项目开发中踩坑得出的结论（详见 `probes/` 探针）。

1. **bestBid 用 CLOB，不用 Gamma**：Gamma 的 `bestBid` 不准确（用户实测 + 本仓库验证），筛选/下单/清理一律用 CLOB `/prices` 实时价。
2. **取价**：`GET /price?side=BUY` = bestBid，`side=SELL` = bestAsk；批量用 `POST /prices`（body 是纯数组 `[{token_id, side}]`）。
3. **下单用单笔不用批量**：批量 `post_orders`（15 单/请求）触发「order timed out」，单笔 `place_limit_order` + 全局限流稳定。
4. **撤单每批 ≤ 100**：超过 burst 120 会被限流整批拒绝。
5. **卖出最小份额 > 5**：价格无限制，但份额必须 > 5。
6. **挂单不占用钱包余额**：实测 $2572 挂单总额，钱包余额不变；成交时才真正扣款。
7. **`/book` 在薄市场不可靠**，取价别用 `/book`，用 `/price`。

---

## 服务器部署

按 `新项目开发流程模板.md` 第 5 节（screen 后台托管）：

```bash
# 服务器首次
git clone <repo> && cd <repo>
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 传凭据
scp .env.bot1 root@服务器:/root/<repo>/

# 后台跑
screen -S bot1
cd /root/<repo> && source venv/bin/activate && python bot.py --live --loop --interval 3600
# Ctrl+A 再按 D 挂后台
```

---

## 安全检查清单

每次提交前：
- [ ] `git status` 看不到 `.env`、`.env.*`、`data/`、`*.log`
- [ ] 代码里没有硬编码私钥 / API key
- [ ] 先 `--dry-run --once` 确认无误再 `--live`
