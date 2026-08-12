# 只读：验证 /prices 返回的 BUY/SELL 语义，与 /book 的 bestBid/bestAsk 对比
import json

import requests

CLOB = "https://clob.polymarket.com"
cond = "0x968e48b511e76c798aabbafc5c5de292dc15bf4ba8d4aa6d560767eeddd588ad"
c = requests.get(f"{CLOB}/clob-markets/{cond}", timeout=20).json()
tokens = c["t"]
print("tokens:", [(t["o"], t["t"][:12]) for t in tokens])

# 单点：GET /price?side=BUY / SELL
for t in tokens:
    b = requests.get(f"{CLOB}/price", params={"token_id": t["t"], "side": "BUY"}, timeout=20).json()
    s = requests.get(f"{CLOB}/price", params={"token_id": t["t"], "side": "SELL"}, timeout=20).json()
    print(f"  GET /price {t['o']:>3}: BUY(bestBid)={b.get('price')}  SELL(bestAsk)={s.get('price')}")

# 批量：POST /prices，试几种 body 形态
for body in (
    {"items": [{"token_id": t["t"], "side": sd} for t in tokens for sd in ("BUY", "SELL")]},
    [{"token_id": t["t"], "side": sd} for t in tokens for sd in ("BUY", "SELL")],
    {"token_ids": [t["t"] for t in tokens], "sides": ["BUY", "SELL", "BUY", "SELL"]},
):
    p = requests.post(f"{CLOB}/prices", json=body, timeout=20)
    print(f"POST /prices body={type(body).__name__} → {p.status_code}: {str(p.json())[:200]}")

for t in tokens:
    b = requests.get(f"{CLOB}/book", params={"token_id": t["t"]}, timeout=20).json()
    bids = [x["price"] for x in b.get("bids", [])[:1]]
    asks = [x["price"] for x in b.get("asks", [])[:1]]
    print(f"  /book {t['o']:>3}: bestBid={bids[0] if bids else '-'}  bestAsk={asks[0] if asks else '-'}")
