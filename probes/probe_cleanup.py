# 诊断：open_orders 里 BUY 单不在本轮候选（要撤的）到底为什么
import json
import os
from collections import Counter

from dotenv import load_dotenv

from polymarket import RelayerApiKey, SecureClient
from bot import scan_candidates, list_open_orders, _float, fetch_prices

load_dotenv(".env.bot1")
client = SecureClient.create(
    private_key=os.environ["SIGNER_PRIVATE_KEY"],
    wallet=os.environ["POLYMARKET_WALLET_ADDRESS"],
    api_key=RelayerApiKey(
        key=os.environ["POLYMARKET_RELAYER_API_KEY"],
        address=os.environ["POLYMARKET_RELAYER_API_KEY_ADDRESS"],
    ),
)

print("扫描候选...")
cands = scan_candidates()
print(f"候选 {len(cands)} 个")

print("查 open_orders...")
orders = list_open_orders(client)
buy = {t: o for t, o in orders.items() if o["side"] == "BUY"}
print(f"open_orders 里 BUY 单 {len(buy)} 个")

not_in_cands = [t for t in buy if t not in cands]
print(f"BUY 单不在候选（要撤）: {len(not_in_cands)} 个")

if not_in_cands:
    # 查这些 token 的最新 bestBid
    bids = fetch_prices(not_in_cands[:500], "BUY")
    dist = Counter()
    for t in not_in_cands:
        bb = _float(bids.get(t))
        if bb is None:
            dist["无价(None)"] += 1
        elif bb <= 0:
            dist["bestBid=0"] += 1
        elif bb < 0.05:
            dist["bestBid<0.05(应候选)"] += 1
        else:
            dist["bestBid>=0.05"] += 1
    print("要撤单的 token 当前 bestBid 分布（前 500 个）:")
    for k, v in dist.most_common():
        print(f"  {k}: {v}")
    # 打印几个例子
    print("\n前 5 个要撤的 token:")
    for t in not_in_cands[:5]:
        print(f"  {t[:16]} bestBid={bids.get(t)} order_price={buy[t]['price']}")
