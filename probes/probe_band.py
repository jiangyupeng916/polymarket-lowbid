# 只读：keyset 深度扫描结算>20天市场，按价格细分桶（可越过 offset 2100 上限）
import datetime

import requests

GAMMA = "https://gamma-api.polymarket.com"
now = datetime.datetime.now(datetime.timezone.utc)
end_date_min = (now + datetime.timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


BUCKETS = ["bid0/空", "<0.005", "0.005-0.01", "0.01-0.02", "0.02-0.03", "0.03-0.04", "0.04-0.05", ">=0.05"]


def bucket_of(b, a):
    if b is None or b == 0 or a is None or a >= 0.5:
        return "bid0/空"
    if b < 0.005:
        return "<0.005"
    if b < 0.01:
        return "0.005-0.01"
    if b < 0.02:
        return "0.01-0.02"
    if b < 0.03:
        return "0.02-0.03"
    if b < 0.04:
        return "0.03-0.04"
    if b < 0.05:
        return "0.04-0.05"
    return ">=0.05"


counts = {k: 0 for k in BUCKETS}
examples = {k: [] for k in BUCKETS}
total = 0
cursor = None
pages = 0

while pages < 500:  # 保险上限
    pages += 1
    params = {"limit": 100, "order": "bestBid", "ascending": "true",
              "closed": "false", "end_date_min": end_date_min}
    if cursor:
        params["after_cursor"] = cursor
    r = requests.get(f"{GAMMA}/markets/keyset", params=params, timeout=30).json()
    if not isinstance(r, dict):
        print("非 dict 响应:", str(r)[:200])
        break
    markets = r.get("markets", []) or []
    if not markets:
        print("没有更多市场")
        break
    hit_ge_05 = False
    for m in markets:
        total += 1
        b = f(m.get("bestBid"))
        a = f(m.get("bestAsk"))
        k = bucket_of(b, a)
        counts[k] += 1
        if len(examples[k]) < 3:
            examples[k].append((m.get("id"), str(m.get("slug"))[:44], m.get("bestBid"), m.get("bestAsk")))
        if k == ">=0.05":
            hit_ge_05 = True
    cursor = r.get("next_cursor")
    if hit_ge_05:
        print(f"扫到第 {total} 个进入 >=0.05 段，终止（<0.05 已全部统计）")
        break
    if not cursor:
        print(f"next_cursor 为空，共 {total} 个")
        break

print(f"\n总扫描: {total} 个市场（bestBid 升序，{pages} 页）")
for k in BUCKETS:
    print(f"  {k:<10} {counts[k]:>6}  例: {examples[k]}")
