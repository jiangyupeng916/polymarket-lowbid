# bot.py — 挂单策略主循环（买入侧，全挂 + 撤旧重挂 + 批量下单/撤单 + 控速）
#
# 用法：
#   python bot.py --dry-run            # 默认：只计算打印，不真实下单（先跑这个看效果）
#   python bot.py --live               # 真实下单/撤单
#   python bot.py --live --interval 3600   # 循环间隔秒（默认 3600，可长，因市场离结算远难成交）
#   python bot.py --dry-run --once     # 只跑一轮退出
#
# 每轮逻辑：
#   1) 读候选池 data/candidates.json（>20天 & bestBid<0.05）
#   2) list_open_orders 拿当前挂单（token_id -> 挂价/order_id）
#   3) POST /prices 批量取最新 bestBid
#   4) 撤：已挂但 bestBid 变了 / 涨到>=0.05 / 归零 → 批量 cancel_orders
#   5) 挂：bestBid 在 (0,0.05) 且（未挂 或 挂价!=bestBid）→ create_limit_order 批量签名 → post_orders 每 15 单一批
#   6) 控速：下单 40/s、撤单 80/s（标准 tier）；遇到 429 open_orders_limit 停本轮挂单
#
# 真实下单为真金白银，务必先 --dry-run 确认无误再 --live。
import argparse
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

from polymarket import RejectedOrder, RelayerApiKey, SecureClient

warnings.filterwarnings("ignore")

CLOB = "https://clob.polymarket.com"
DATA_FILE = os.path.join("data", "candidates.json")

SHARES = 10              # 每 token 挂 10 份（用户实测可挂）
PRICE_CEIL = 0.05        # bestBid < 0.05
BATCH = 1000             # 批量撤单每批上限（DELETE /orders ≤1000，execution.py 同款）
EXEC_INTERVAL = 0.05     # 两次写操作最小间隔（秒）→ 20 单/秒（execution.py 全局限流）
PLACE_WORKERS = 8        # 下单线程池大小（实际写操作被全局限流串行化）
CANCEL_PAUSE = 0.5       # 撤单批间 sleep
PRICES_CHUNK = 150       # POST /prices 每批 token 数
MAX_WORKERS = 6          # 取价并发线程
PREFETCH_WORKERS = 50    # 预取市场信息并发线程（clob 通用限流 9000/10s，足够）

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
    """全局限流：保证两次写操作间隔 EXEC_INTERVAL 秒（execution.py 同款）。"""
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


def load_candidates():
    if not os.path.exists(DATA_FILE):
        log.error("缺 %s，先跑 python scan_candidates.py", DATA_FILE)
        sys.exit(1)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)["candidates"]


def list_open_orders(client):
    """→ {token_id(str): {'order_id': str, 'price': str}}"""
    result = {}
    try:
        for o in client.list_open_orders().iter_items():
            result[str(o.token_id)] = {"order_id": str(o.id), "price": str(o.price)}
    except Exception as e:
        log.error("list_open_orders 失败: %s", e)
    return result


def _post_prices(chunk):
    try:
        p = SESSION.post(f"{CLOB}/prices",
                         json=[{"token_id": t, "side": "BUY"} for t in chunk], timeout=45)
        if p.status_code == 200:
            return {tid: v.get("BUY") for tid, v in p.json().items() if isinstance(v, dict)}
    except requests.RequestException as e:
        log.warning("/prices 失败: %s", e)
    return {}


def fetch_best_bids(token_ids):
    """→ {token_id(str): bestBid(float)}"""
    result = {}
    chunks = [token_ids[i:i + PRICES_CHUNK] for i in range(0, len(token_ids), PRICES_CHUNK)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for fut in as_completed([ex.submit(_post_prices, c) for c in chunks]):
            result.update(fut.result())
    return result


def plan_actions(candidates, open_orders, bids):
    """计算撤/挂。返回 (to_cancel=[order_id], to_place=[(token_id, price)])"""
    to_cancel, to_place = [], []
    for token_id in candidates:
        raw = bids.get(token_id)                             # /prices 返回的原始字符串
        bb = _float(raw)
        if bb is None or bb <= 0 or bb >= PRICE_CEIL:
            if token_id in open_orders:                      # 已挂但不再符合 → 撤
                to_cancel.append(open_orders[token_id]["order_id"])
            continue
        if token_id in open_orders:
            if Decimal(open_orders[token_id]["price"]) == Decimal(str(raw)):
                continue                                     # 价格没变 → 保持
            to_cancel.append(open_orders[token_id]["order_id"])   # 变了 → 撤旧
        to_place.append((token_id, str(raw)))                # 挂新（保留原始字符串精确）
    return to_cancel, to_place


def do_cancel(client, order_ids, live):
    if not order_ids:
        return
    if not live:
        log.info("[dry-run] 将撤 %d 单", len(order_ids))
        return
    for i in range(0, len(order_ids), BATCH):
        chunk = order_ids[i:i + BATCH]
        try:
            res = client.cancel_orders(order_ids=chunk)
            log.info("撤单 %d/%d → canceled=%s not_canceled=%s",
                     i + len(chunk), len(order_ids),
                     getattr(res, "canceled", "?"), getattr(res, "not_canceled", "?"))
        except Exception as e:
            log.error("撤单批失败: %s", e)
        time.sleep(CANCEL_PAUSE)


def preload_market_info(client, token_cond):
    """并发预取 /clob-markets，填 SDK 缓存，使 create_limit_order 不再逐单查网络。"""
    from polymarket._internal.actions.orders.market_data import MarketInfo, PlatformFeeInfo
    from polymarket.models.types import CtfConditionId, TokenId

    om = client._ctx.order_metadata
    sc = client._ctx.secure_clob

    def fetch(kv):
        token_id, cond = kv
        try:
            data = sc.get_json(f"/clob-markets/{cond}")
            mi = MarketInfo(
                fee_info=PlatformFeeInfo(rate=Decimal(0), exponent=Decimal(0)),
                neg_risk=data.get("nr", False),
                tick_size=Decimal(str(data.get("mts"))),
                token_ids=frozenset(TokenId(t["t"]) for t in data.get("t", [])),
            )
            om._conditions.set(TokenId(token_id), CtfConditionId(cond))
            om._markets.set(CtfConditionId(cond), mi)
        except Exception as e:
            log.warning("预取失败 %s: %s", str(token_id)[:12], str(e)[:100])

    items = list(token_cond.items())
    log.info("并发预取 %d 个市场信息...", len(items))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as ex:
        list(ex.map(fetch, items))
    log.info("预取完成 %.1fs", time.time() - t0)


def _place_one(client, token_id, price):
    """单笔下单（带重试）。每次 post 前全局限流。返回 (ok, msg)。"""
    for attempt in range(3):
        rate_wait()
        try:
            resp = client.place_limit_order(
                token_id=token_id, price=price, size=SHARES, side="BUY", post_only=True,
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
            return False, msg          # post-only 拒单（会 cross）不重试
        if attempt == 2:
            return False, msg
        time.sleep(0.3)
    return False, "retries exhausted"


def do_place(client, candidates, to_place, live):
    if not to_place:
        return
    if not live:
        total = sum(Decimal(p) * SHARES for _, p in to_place)
        log.info("[dry-run] 将挂 %d 单（共 ~$%.2f 名义）", len(to_place), float(total))
        return
    # 预取市场信息填缓存（避免 place_limit_order 每单查网络）
    token_cond = {t: candidates[t]["cond"] for t, _ in to_place if t in candidates and "cond" in candidates[t]}
    preload_market_info(client, token_cond)

    placed, rejected = 0, 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=PLACE_WORKERS) as ex:
        futs = [ex.submit(_place_one, client, t, p) for t, p in to_place]
        for i, fut in enumerate(as_completed(futs), 1):
            ok, msg = fut.result()
            if ok:
                placed += 1
            else:
                rejected += 1
                if rejected <= 10:
                    log.warning("  拒单: %s", msg[:120])
            if i % 200 == 0 or i == len(to_place):
                log.info("下单进度 %d/%d（成功 %d 拒 %d，%.1fs）",
                         i, len(to_place), placed, rejected, time.time() - t0)
    log.info("本轮下单完成：成功 %d，拒绝 %d，%.1fs", placed, rejected, time.time() - t0)


def one_round(client, live, max_orders=None):
    t0 = time.time()
    candidates = load_candidates()
    open_orders = list_open_orders(client)
    log.info("候选 %d，当前挂单 %d", len(candidates), len(open_orders))

    bids = fetch_best_bids(list(candidates.keys()))
    to_cancel, to_place = plan_actions(candidates, open_orders, bids)
    if max_orders is not None:
        to_place = to_place[:max_orders]
    log.info("待撤 %d，待挂 %d（取价 %.1fs）", len(to_cancel), len(to_place), time.time() - t0)

    do_cancel(client, to_cancel, live)
    do_place(client, candidates, to_place, live)
    log.info("本轮完成 %.1fs", time.time() - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="真实下单")
    ap.add_argument("--dry-run", action="store_true", help="只计算打印不真实下单（默认）")
    ap.add_argument("--interval", type=int, default=3600, help="循环间隔秒（默认 3600）")
    ap.add_argument("--once", action="store_true", help="只跑一轮退出")
    ap.add_argument("--max", type=int, default=None, help="每轮最多挂 N 单（测试用）")
    args = ap.parse_args()
    if args.live and args.dry_run:
        ap.error("--live 与 --dry-run 不能同时用")
    if args.dry_run:
        args.live = False

    if not args.live:
        log.info("== DRY-RUN 模式（不真实下单）==")
    else:
        log.info("== LIVE 模式（真实下单）==")

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
