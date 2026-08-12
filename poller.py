# poller.py — 入场循环·热轮询（15 分钟一循环，只读版，不提交订单）
# 读 data/candidates.json（由 scan_candidates.py 生成），POST /prices 批量并发取 bestBid，
# 筛 bestBid<0.05 → 记录“应挂 10 份 @ bestBid”。真实下单待 .env.bot1 凭证后接入。
import argparse
import datetime
import json
import logging
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

warnings.filterwarnings("ignore")

CLOB = "https://clob.polymarket.com"
DATA_FILE = os.path.join("data", "candidates.json")

PRICE_CEIL = 0.05        # bestBid < 0.05
SHARES = 10              # 每 token 挂 10 份（用户实测可挂）
POLL_INTERVAL = 900      # 循环周期（秒）
PRICES_CHUNK = 150       # 每次 POST /prices 的 token 数
MAX_WORKERS = 6          # 并发线程数（打太猛会被 CLOB 掐连接）
HTTP_TIMEOUT = 45
RETRIES = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("poller")
SESSION = requests.Session()


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _post_prices(chunk):
    for i in range(RETRIES):
        try:
            p = SESSION.post(f"{CLOB}/prices",
                             json=[{"token_id": t, "side": "BUY"} for t in chunk],
                             timeout=HTTP_TIMEOUT)
            if p.status_code == 200:
                data = p.json()
                return {tid: _float(v.get("BUY")) for tid, v in data.items() if isinstance(v, dict)}
            if p.status_code in (429, 503, 520, 521, 522):
                time.sleep(1.5 * (i + 1))
                continue
            log.warning("/prices %s: %s", p.status_code, p.text[:120])
            return {}
        except requests.RequestException as e:
            log.warning("/prices 第%d次失败: %s", i + 1, e)
            time.sleep(1.0 * (i + 1))
    return {}


def fetch_best_bids(token_ids):
    """POST /prices 批量并发取 BUY(bestBid) → {token_id: bestBid}"""
    result = {}
    chunks = [token_ids[i:i + PRICES_CHUNK] for i in range(0, len(token_ids), PRICES_CHUNK)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [ex.submit(_post_prices, c) for c in chunks]
        for fut in as_completed(futs):
            result.update(fut.result())
    return result


def load_candidates():
    if not os.path.exists(DATA_FILE):
        log.error("没有 %s —— 先运行 python scan_candidates.py 生成候选池", DATA_FILE)
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        st = json.load(f)
    cands = st.get("candidates", {})
    status = "扫描已完成" if not st.get("cursor") else "扫描未完成(续扫中)"
    log.info("候选池 %d 个（%s）", len(cands), status)
    return list(cands.keys())


def one_round():
    token_ids = load_candidates()
    if not token_ids:
        return []
    t0 = time.time()
    bids = fetch_best_bids(token_ids)
    live = sorted(((t, bb) for t, bb in bids.items() if bb is not None and bb < PRICE_CEIL),
                  key=lambda x: x[1])
    log.info("实测 bestBid<%.2f：%d 个（取价 %.1fs）", PRICE_CEIL, len(live), time.time() - t0)
    for t, bb in live[:10]:
        log.info("  应挂 %d 份 @ %s (token=%s…)", SHARES, bb, str(t)[:14])
    return live


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="循环运行（默认单轮退出）")
    args = ap.parse_args()
    log.info("== 入场循环·只读版 start ==")
    while True:
        one_round()
        if not args.loop:
            log.info("单轮完成，退出")
            break
        log.info("%.0fs 后下一轮", POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("已停止")
        sys.exit(0)
