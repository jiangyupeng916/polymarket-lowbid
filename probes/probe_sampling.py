# 只读：验证 /sampling-markets —— tokens[].price 语义、分页速度、总量
import time

import requests

CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"

t0 = time.time()
r = requests.get(f"{CLOB}/sampling-markets", timeout=45).json()
first = time.time() - t0
data = r.get("data", [])
print(f"首屏: count={r.get('count')} 本页 data={len(data)} next_cursor={str(r.get('next_cursor'))[:20]}… 耗时{first:.1f}s")

# 对比 tokens[].price 与 /price 的 BUY/SELL
m = next((x for x in data if x.get("tokens") and x.get("end_date_iso")), None)
print("\n示例市场:", m.get("market_slug"), "end_date_iso=", m.get("end_date_iso"),
      "mos=", m.get("minimum_order_size"), "mts=", m.get("minimum_tick_size"), "active=", m.get("active"))
for t in m["tokens"]:
    bid = requests.get(f"{CLOB}/price", params={"token_id": t["token_id"], "side": "BUY"}, timeout=20).json()
    ask = requests.get(f"{CLOB}/price", params={"token_id": t["token_id"], "side": "SELL"}, timeout=20).json()
    print(f"  outcome={t['outcome']:>3} sampling_price={t['price']}  /price BUY={bid.get('price')} SELL={ask.get('price')}")

# 数一下分页到 end_date>20天 且 price<0.05 需要多少页 / 有多少候选
import datetime
end_min = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=20)).isoformat() + "Z"
cheap = 0
cursor = None
t0 = time.time()
for page in range(60):
    params = {}
    if cursor:
        params["next_cursor"] = cursor
    d = requests.get(f"{CLOB}/sampling-markets", params=params, timeout=45).json()
    items = d.get("data", [])
    if not items:
        print("没有更多")
        break
    for x in items:
        if x.get("end_date_iso") and x["end_date_iso"] >= end_min and x.get("active"):
            for t in x.get("tokens", []):
                try:
                    if float(t["price"]) < 0.05:
                        cheap += 1
                except (TypeError, ValueError):
                    pass
    cursor = d.get("next_cursor")
    if not cursor:
        break
print(f"\n扫 {page+1} 页后：候选(>20天,active,price<0.05)={cheap}，耗时{time.time()-t0:.1f}s")
