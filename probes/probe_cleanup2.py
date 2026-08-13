# 诊断：plan_buy 的 to_cancel 构成（价变调整 vs 误撤 vs 正确清理）
import os
from collections import Counter

from dotenv import load_dotenv

from polymarket import RelayerApiKey, SecureClient
from bot import (scan_candidates, list_open_orders, fetch_prices, plan_buy,
                 load_buy_state, _float)

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
state = load_buy_state()
print(f"CSV 旧状态 {len(state)} 行")
orders = list_open_orders(client)
buy = {t: o for t, o in orders.items() if o["side"] == "BUY"}
print(f"open_orders BUY 单 {len(buy)} 个")

need = set(cands.keys()) | set(buy.keys())
bids = fetch_prices(need, "BUY")

to_cancel, to_place = plan_buy(cands, orders, bids, state)
print(f"\nto_cancel {len(to_cancel)} 个，to_place {len(to_place)} 个")

# 分析 to_cancel 的 token（用 order_id -> token 反查）
oid_to_tok = {o["order_id"]: t for t, o in buy.items()}
cancel_tokens = [oid_to_tok[oid] for oid in to_cancel if oid in oid_to_tok]

dist = Counter()
for t in cancel_tokens:
    if t in to_place:
        dist["价变(撤旧挂新)"] += 1
        continue
    bb = _float(bids.get(t))
    if bb is None:
        dist["bb=None(/prices无返回)"] += 1
    elif bb <= 0:
        dist["bestBid=0"] += 1
    elif bb >= 0.05:
        dist["bestBid>=0.05"] += 1
    else:
        dist[f"bestBid<0.05(bb={bb}) 误撤?"] += 1

print("\n撤单构成:")
for k, v in dist.most_common():
    print(f"  {k}: {v}")
