# 一次性核对：Gamma 与 CLOB 的 bestAsk 差异（Everton 2026-27 EPL champion）
import json

import requests

CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
cond = "0x968e48b511e76c798aabbafc5c5de292dc15bf4ba8d4aa6d560767eeddd588ad"

g = requests.get(f"{GAMMA}/markets/2771331", timeout=20).json()
print("Gamma:", g.get("slug"))
print("  bestBid=", g.get("bestBid"), "bestAsk=", g.get("bestAsk"), "outcomePrices=", g.get("outcomePrices"))
print("  clobTokenIds=", g.get("clobTokenIds"))

c = requests.get(f"{CLOB}/clob-markets/{cond}", timeout=20).json()
print("CLOB /clob-markets: t=", json.dumps(c.get("t")), "mos=", c.get("mos"), "mts=", c.get("mts"))

tokens = json.loads(g["clobTokenIds"])
for t in tokens:
    b = requests.get(f"{CLOB}/book", params={"token_id": t}, timeout=20).json()
    bids = b.get("bids", [])[:3]
    asks = b.get("asks", [])[:3]
    print(f"  /book token={t[:12]}: 前3 bids={[x['price'] for x in bids]}  前3 asks={[x['price'] for x in asks]}")
