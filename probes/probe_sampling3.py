# 只读：对比 /sampling-markets 的 tokens[].price 与 /price 的真实 bestBid（几个已知便宜市场）
import time

import requests

CLOB = "https://clob.polymarket.com"
KEYWORDS = ["everton", "tim-walz", "mark-cuban", "xi-jinping-out", "ukraine-agree"]


def get_page(cursor):
    params = {}
    if cursor:
        params["next_cursor"] = cursor
    for i in range(3):
        try:
            return requests.get(f"{CLOB}/sampling-markets", params=params, timeout=60).json()
        except Exception as e:
            print(f"  页失败({i+1}): {e}")
            time.sleep(2)
    return None


cursor = None
seen = {}
for page in range(25):
    d = get_page(cursor)
    if not d or not d.get("data"):
        break
    for x in d["data"]:
        slug = x.get("market_slug") or ""
        if any(k in slug for k in KEYWORDS) and x.get("active"):
            seen.setdefault(slug, x)
    cursor = d.get("next_cursor")
    if not cursor:
        break

for slug, x in seen.items():
    print(f"\n{slug}")
    print(f"  end_date_iso={x.get('end_date_iso')} active={x.get('active')} mos={x.get('minimum_order_size')}")
    for t in x.get("tokens", []):
        p = requests.get(f"{CLOB}/price", params={"token_id": t["token_id"], "side": "BUY"}, timeout=20).json()
        s = requests.get(f"{CLOB}/price", params={"token_id": t["token_id"], "side": "SELL"}, timeout=20).json()
        print(f"  {t['outcome']:>3}: sampling_price={t['price']}  /price BUY(bestBid)={p.get('price')} SELL(bestAsk)={s.get('price')}")
