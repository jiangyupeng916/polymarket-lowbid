# place_test_orders.py — 真实下单测试：随机挑 N 个候选，post-only 挂 BUY @ bestBid，10 份
# 用法：python place_test_orders.py [N]   （默认 5 个）
import json
import os
import random
import sys

import requests
from dotenv import load_dotenv

from polymarket import RelayerApiKey, RejectedOrder, SecureClient

CLOB = "https://clob.polymarket.com"
DATA_FILE = os.path.join("data", "candidates.json")
SHARES = 10          # 每单 10 份（用户实测可挂）
SIDE = "BUY"


def load_creds():
    load_dotenv(".env.bot1")
    return {
        "private_key": os.environ["SIGNER_PRIVATE_KEY"],
        "wallet": os.environ["POLYMARKET_WALLET_ADDRESS"],
        "relayer_key": os.environ["POLYMARKET_RELAYER_API_KEY"],
        "relayer_addr": os.environ["POLYMARKET_RELAYER_API_KEY_ADDRESS"],
    }


def pick_candidates(n):
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        cands = json.load(f)["candidates"]
    # 优先挑 bid 较高（0.01-0.05 更“真实”）的，随机取样
    pool = list(cands.items())
    chosen = random.sample(pool, min(n, len(pool)))
    return chosen


def best_bid(token_id):
    p = requests.get(f"{CLOB}/price", params={"token_id": token_id, "side": "BUY"}, timeout=20)
    p.raise_for_status()
    return p.json().get("price")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    creds = load_creds()
    client = SecureClient.create(
        private_key=creds["private_key"],
        wallet=creds["wallet"],
        api_key=RelayerApiKey(key=creds["relayer_key"], address=creds["relayer_addr"]),
    )
    print(f"钱包: {creds['wallet']}")
    print(f"随机挑 {n} 个候选，各挂 {SHARES} 份 @ 当前 bestBid（post-only，BUY）\n")

    placed = 0
    for token_id, info in pick_candidates(n):
        slug = info.get("slug", "?")[:50]
        try:
            bb = best_bid(token_id)
        except Exception as e:
            print(f"[跳过] {slug} 取价失败: {e}")
            continue
        if bb is None or float(bb) <= 0:
            print(f"[跳过] {slug} 无 bid（bestBid={bb}）")
            continue
        try:
            res = client.place_limit_order(
                token_id=token_id, price=bb, size=SHARES, side=SIDE, post_only=True,
            )
        except Exception as e:
            print(f"[异常] {slug} 下单抛出: {e}")
            continue
        if isinstance(res, RejectedOrder) or not res.ok:
            code = getattr(res, "code", "?"); msg = getattr(res, "message", "?")
            print(f"[拒绝] {slug}\n       @ {bb} x{SHARES}  → code={code} msg={msg}")
        else:
            placed += 1
            print(f"[成交挂单] {slug}\n       @ {bb} x{SHARES}  → order_id={res.order_id} status={res.status}")
    print(f"\n完成：成功挂单 {placed} 个")


if __name__ == "__main__":
    main()
