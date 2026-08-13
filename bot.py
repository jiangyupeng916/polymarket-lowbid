# bot.py — 挂单策略主循环（买入 + 卖出 + CSV 状态对比 + 全局限流）
#
# 用法：
#   python bot.py --dry-run            # 默认：只计算打印，不真实下单/撤单
#   python bot.py --live               # 真实下单/撤单
#   python bot.py --live --interval 3600   # 循环间隔秒（默认 3600）
#   python bot.py --live --once        # 只跑一轮
#
# ── 买入循环（每轮，从筛选重新开始）─────────────────────────────
#   1) 扫描 Gamma /markets/keyset：结算>20天 且 bestBid<0.05 → 本轮候选
#   2) 读 data/buy_state.csv（上轮候选+挂单状态）
#   3) 查 open_orders 当前挂单，/prices 取最新 bestBid
#   4) 对比：
#      - 候选新增（上轮无）→ 挂单 @ bestBid
#      - 候选仍在但 bestBid 变 → 撤旧挂新
#      - 上轮有、本轮不符合（结算<20天 或 bestBid>=0.05）→ 撤单清理
#   5) 执行（单笔 place_limit_order + 全局限流），写回 CSV
#
# ── 卖出循环（每轮）─────────────────────────────────────────────
#   1) 查持仓 list_positions（size>0）
#   2) /prices side=SELL 取最新 bestAsk
#   3) 对比：有持仓未挂卖单 → 挂卖单 @ bestAsk（post-only，被动）；已挂价变 → 撤旧挂新
#
# 真实下单为真金白银，务必先 --dry-run 确认。
import argparse
import csv
import datetime
import json
import logging
import os
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

import requests
from dotenv import load_dotenv

from polymarket import RelayerApiKey, SecureClient

warnings.filterwarnings("ignore")

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
BUY_STATE = os.path.join("data", "buy_state.csv")

SHARES = 10              # 每 token 挂 10 份（买入，用户实测可挂）
PRICE_CEIL = 0.05        # bestBid < 0.05（下单/清理阈值，用 CLOB /prices 实时价）
SCAN_CEIL = 0.1          # Gamma 粗筛停止阈值（不信任 Gamma bestBid，放宽到 0.1 留余量）
DAYS_AHEAD = 20          # 结算 > 20 天
EXEC_INTERVAL = 0.05     # 两次写操作最小间隔（秒）→ 20 单/秒
PLACE_WORKERS = 8        # 下单线程池大小（写操作被全局限流串行化）
CANCEL_BATCH = 100       # 批量撤单每批上限（≤ burst 120，超了整批被限流拒）
PRICES_CHUNK = 150       # POST /prices 每批 token 数
MAX_WORKERS = 6          # 取价并发线程
PREFETCH_WORKERS = 50    # 预取市场信息并发线程

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")
SESSION = requests.Session()

_rate_lock = threading.Lock()
_last_exec = 0.0


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rate_wait():
    """全局限流：保证两次写操作间隔 EXEC_INTERVAL 秒。"""
    global _last_exec
    with _rate_lock:
        gap = EXEC_INTERVAL - (time.time() - _last_exec)
        if gap > 0:
            time.sleep(gap)
        _last_exec = time.time()


def load_client():
    load_dotenv(".env.bot1")
    return SecureClient.create(
        private_key=os.environ["SIGNER_PRIVATE_KEY"],
        wallet=os.environ["POLYMARKET_WALLET_ADDRESS"],
        api_key=RelayerApiKey(
            key=os.environ["POLYMARKET_RELAYER_API_KEY"],
            address=os.environ["POLYMARKET_RELAYER_API_KEY_ADDRESS"],
        ),
    )


# ── 候选扫描（每轮重新筛选）─────────────────────────────────────────
def scan_candidates():
    """Gamma keyset 扫描 → {token_id: {cond, slug, end_date, bid}}，筛 >20天 & bestBid<0.05。"""
    end_date_min = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=DAYS_AHEAD)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, cursor = {}, None
    for _ in range(500):
        params = {"limit": 100, "order": "bestBid", "ascending": "true",
                  "closed": "false", "end_date_min": end_date_min}
        if cursor:
            params["after_cursor"] = cursor
        try:
            d = requests.get(f"{GAMMA}/markets/keyset", params=params, timeout=45).json()
        except Exception as e:
            log.warning("扫描页失败: %s", e)
            break
        if not isinstance(d, dict) or not d.get("markets"):
            break
        done = False
        for m in d["markets"]:
            b = _float(m.get("bestBid"))
            if b is not None and b >= SCAN_CEIL:
                done = True
                break
            if b is None or b <= 0:
                continue                       # 无买单（bestBid 空或 0）才跳过
            try:
                tok0 = json.loads(m["clobTokenIds"])[0]
            except Exception:
                continue
            # 只存市场元数据；bestBid 精筛交给 CLOB /prices（Gamma 的 bestBid 不信任）
            out[str(tok0)] = {"cond": m.get("conditionId"), "slug": m.get("slug"),
                              "end_date": m.get("endDate")}
        cursor = d.get("next_cursor")
        if done or not cursor:
            break
    return out


# ── CSV 状态读写 ────────────────────────────────────────────────────
CSV_FIELDS = ["token_id", "condition_id", "slug", "end_date", "bid", "order_id", "order_price"]


def load_buy_state():
    rows = {}
    if not os.path.exists(BUY_STATE):
        return rows
    with open(BUY_STATE, "r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows[r["token_id"]] = r
    return rows


def save_buy_state(candidates, open_orders, old_state, bids):
    os.makedirs("data", exist_ok=True)
    all_tokens = set(candidates.keys()) | set(open_orders.keys())
    with open(BUY_STATE, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for token_id in all_tokens:
            c = candidates.get(token_id, {})
            old = old_state.get(token_id, {})
            buy_orders = [o for o in open_orders.get(token_id, []) if o["side"] == "BUY"]
            o = buy_orders[0] if buy_orders else None
            w.writerow({
                "token_id": token_id,
                "condition_id": c.get("cond", old.get("condition_id", "")),
                "slug": c.get("slug", old.get("slug", "")),
                "end_date": c.get("end_date", old.get("end_date", "")),
                "bid": bids.get(token_id, old.get("bid", "")),
                "order_id": o["order_id"] if o else "",
                "order_price": o["price"] if o else "",
            })


# ── 挂单/持仓查询 ────────────────────────────────────────────────────
def list_open_orders(client):
    """→ {token_id: [{order_id, price, side}, ...]}（一个 token 可能有多个单）"""
    result = {}
    try:
        for o in client.list_open_orders().iter_items():
            tid = str(o.token_id)
            result.setdefault(tid, []).append(
                {"order_id": str(o.id), "price": str(o.price), "side": str(o.side).upper()})
    except Exception as e:
        log.error("list_open_orders 失败: %s", e)
    return result


def list_positions(client):
    """→ {token_id: {size, outcome, cond}}（size>0 的持仓）"""
    result = {}
    try:
        for p in client.list_positions(size_threshold=0).iter_items():
            sz = _float(p.size)
            if sz is None or sz <= 0:
                continue
            result[str(p.token_id)] = {"size": str(p.size), "outcome": p.outcome,
                                       "cond": str(p.condition_id)}
    except Exception as e:
        log.error("list_positions 失败: %s", e)
    return result


def _post_prices(chunk, side):
    try:
        p = SESSION.post(f"{CLOB}/prices",
                         json=[{"token_id": t, "side": side} for t in chunk], timeout=45)
        if p.status_code == 200:
            return {tid: v.get(side) for tid, v in p.json().items() if isinstance(v, dict)}
    except requests.RequestException as e:
        log.warning("/prices 失败: %s", e)
    return {}


def fetch_prices(token_ids, side):
    """POST /prices 批量取价 → {token_id: price(str)}。side=BUY→bestBid, SELL→bestAsk。"""
    result = {}
    ids = list(token_ids)
    chunks = [ids[i:i + PRICES_CHUNK] for i in range(0, len(ids), PRICES_CHUNK)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for fut in as_completed([ex.submit(_post_prices, c, side) for c in chunks]):
            result.update(fut.result())
    return result


# ── 预取市场信息（填 SDK 缓存，避免逐单查网络）─────────────────────
def preload_market_info(client, token_cond):
    from polymarket._internal.actions.orders.market_data import MarketInfo, PlatformFeeInfo
    from polymarket.models.types import CtfConditionId, TokenId

    om = client._ctx.order_metadata
    sc = client._ctx.secure_clob

    def fetch(kv):
        token_id, cond = kv
        try:
            data = sc.get_json(f"/clob-markets/{cond}")
            om._conditions.set(TokenId(token_id), CtfConditionId(cond))
            om._markets.set(CtfConditionId(cond), MarketInfo(
                fee_info=PlatformFeeInfo(rate=Decimal(0), exponent=Decimal(0)),
                neg_risk=data.get("nr", False),
                tick_size=Decimal(str(data.get("mts"))),
                token_ids=frozenset(TokenId(t["t"]) for t in data.get("t", [])),
            ))
        except Exception as e:
            log.warning("预取失败 %s: %s", str(token_id)[:12], str(e)[:80])

    items = list(token_cond.items())
    if not items:
        return
    log.info("并发预取 %d 个市场信息...", len(items))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as ex:
        list(ex.map(fetch, items))
    log.info("预取完成 %.1fs", time.time() - t0)


# ── 执行（下单/撤单）─────────────────────────────────────────────────
def _place_one(client, token_id, price, size, side):
    """单笔下单（带重试）。返回 (ok, msg)。"""
    for attempt in range(3):
        rate_wait()
        try:
            resp = client.place_limit_order(
                token_id=token_id, price=price, size=size, side=side, post_only=True,
            )
        except Exception as e:
            if attempt == 2:
                return False, str(e)[:120]
            time.sleep(0.3)
            continue
        if resp.ok:
            return True, ""
        msg = getattr(resp, "message", "") or ""
        if "post_only" in msg.lower() or "crosses" in msg.lower():
            return False, msg
        if attempt == 2:
            return False, msg
        time.sleep(0.3)
    return False, "retries exhausted"


def do_cancel(client, order_ids, live):
    if not order_ids:
        return
    if not live:
        log.info("[dry-run] 将撤 %d 单", len(order_ids))
        return
    for i in range(0, len(order_ids), CANCEL_BATCH):
        chunk = order_ids[i:i + CANCEL_BATCH]
        try:
            res = client.cancel_orders(order_ids=chunk)
            log.info("撤单 %d/%d → canceled=%d", i + len(chunk), len(order_ids),
                     len(res.canceled) if res.canceled else 0)
        except Exception as e:
            log.error("撤单批失败: %s", str(e)[:120])
        time.sleep(1.5)   # 撤单桶 80/s、burst 120，100/批 + 1.5s 间隔留余量


def do_place(client, token_cond, token_to_cond, live):
    """批量挂单（token_cond: {token_id: (price, size, side)}；token_to_cond: {token_id: condition_id}）。"""
    if not token_cond:
        return
    if not live:
        log.info("[dry-run] 将挂 %d 单", len(token_cond))
        return
    preload_market_info(client, {t: token_to_cond[t] for t in token_cond if t in token_to_cond})
    placed = rejected = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=PLACE_WORKERS) as ex:
        futs = {ex.submit(_place_one, client, t, p, s, sd): t
                for t, (p, s, sd) in token_cond.items()}
        for i, fut in enumerate(as_completed(futs), 1):
            token_id = futs[fut]
            ok, msg = fut.result()
            if ok:
                placed += 1
            else:
                rejected += 1
                if rejected <= 10:
                    log.warning("  拒单 %s: %s", token_id[:12], msg[:100])
            if i % 200 == 0 or i == len(token_cond):
                log.info("下单进度 %d/%d（成功 %d 拒 %d，%.1fs）",
                         i, len(token_cond), placed, rejected, time.time() - t0)
    log.info("本轮下单完成：成功 %d，拒绝 %d，%.1fs", placed, rejected, time.time() - t0)


# ── 买入规划 ────────────────────────────────────────────────────────
def plan_buy(candidates, open_orders, bids):
    """对比本轮候选 vs 当前挂单。目标价 = CLOB bestBid（candidates 已筛>20天）。
    每个 token 只保留一个价格正确的 BUY 单，其余撤掉。返回 (to_cancel, to_place)。"""
    to_cancel, to_place = [], {}
    all_tokens = set(open_orders.keys()) | set(candidates.keys())

    for token_id in all_tokens:
        buy_orders = [o for o in open_orders.get(token_id, []) if o["side"] == "BUY"]
        # 目标价：候选里 CLOB bestBid<0.05
        target_price = None
        if token_id in candidates:
            bb = _float(bids.get(token_id))
            if bb is not None and 0 < bb < PRICE_CEIL:
                target_price = str(bb)

        if target_price is None:
            # 无目标（bestBid>=0.05 或结算<20天）→ 撤所有 BUY 单
            for o in buy_orders:
                to_cancel.append(o["order_id"])
        else:
            # 有目标：保留一个价格对的，撤其余；没有对的 → 挂新
            kept = False
            for o in buy_orders:
                if not kept and Decimal(o["price"]) == Decimal(target_price):
                    kept = True
                else:
                    to_cancel.append(o["order_id"])
            if not kept:
                to_place[token_id] = (target_price, SHARES, "BUY")
    return to_cancel, to_place


# ── 卖出规划 ────────────────────────────────────────────────────────
def plan_sell(positions, open_orders, asks):
    """有持仓 → 挂卖单 @ bestAsk。返回 (to_cancel, to_place)。"""
    to_cancel, to_place = [], {}
    # 已挂 SELL 单：token 无持仓 → 撤单清理
    for token_id, orders in open_orders.items():
        for o in orders:
            if o["side"] == "SELL" and token_id not in positions:
                to_cancel.append(o["order_id"])
    for token_id, p in positions.items():
        ask = asks.get(token_id)
        if ask is None or _float(ask) is None:
            continue
        sz = _float(p["size"])
        if sz is None or sz <= 0:
            continue
        # 卖出最小份额 > 5（低于 5 份会被拒，价格无限制）
        if sz <= 5:
            continue
        sell_orders = [o for o in open_orders.get(token_id, []) if o["side"] == "SELL"]
        kept = False
        for o in sell_orders:
            if not kept and Decimal(o["price"]) == Decimal(str(ask)):
                kept = True
            else:
                to_cancel.append(o["order_id"])
        if not kept:
            to_place[token_id] = (str(ask), p["size"], "SELL")
    return to_cancel, to_place


# ── 单轮 ────────────────────────────────────────────────────────────
def one_round(client, live, max_orders=None):
    t0 = time.time()

    # 买入：扫描 → 对比 → 执行
    candidates = scan_candidates()
    log.info("扫描候选 %d 个（%.1fs）", len(candidates), time.time() - t0)
    buy_state = load_buy_state()
    open_orders = list_open_orders(client)
    need_bids = set(candidates.keys()) | set(open_orders.keys())
    bids = fetch_prices(need_bids, "BUY")
    buy_cancel, buy_place = plan_buy(candidates, open_orders, bids)
    if max_orders is not None:
        buy_place = dict(list(buy_place.items())[:max_orders])
    log.info("买入：待撤 %d，待挂 %d", len(buy_cancel), len(buy_place))

    # 卖出：持仓 → 对比 → 执行
    positions = list_positions(client)
    pos_ids = list(positions.keys())
    asks = fetch_prices(pos_ids, "SELL") if pos_ids else {}
    sell_cancel, sell_place = plan_sell(positions, open_orders, asks)
    log.info("卖出：持仓 %d，待撤 %d，待挂 %d", len(positions), len(sell_cancel), len(sell_place))

    # 执行：先撤（买入清理 + 卖出调整），再挂（买入 + 卖出）
    do_cancel(client, buy_cancel + sell_cancel, live)
    all_place = {**buy_place, **sell_place}
    token_to_cond = {t: candidates[t]["cond"] for t in buy_place if t in candidates}
    for t in sell_place:
        if t in positions and positions[t].get("cond"):
            token_to_cond[t] = positions[t]["cond"]
    do_place(client, all_place, token_to_cond, live)

    if live:
        save_buy_state(candidates, open_orders, buy_state, bids)
    log.info("本轮完成 %.1fs", time.time() - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="真实下单/撤单")
    ap.add_argument("--dry-run", action="store_true", help="只计算打印（默认）")
    ap.add_argument("--interval", type=int, default=3600, help="循环间隔秒（默认 3600）")
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    ap.add_argument("--max", type=int, default=None, help="每轮最多挂 N 单（测试用）")
    args = ap.parse_args()
    if args.live and args.dry_run:
        ap.error("--live 与 --dry-run 不能同时用")
    if args.dry_run:
        args.live = False

    log.info("== %s 模式 ==", "LIVE" if args.live else "DRY-RUN")
    client = load_client()
    while True:
        one_round(client, args.live, args.max)
        if args.once:
            log.info("单轮完成，退出")
            break
        log.info("%.0fs 后下一轮", args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("已停止")
        sys.exit(0)
