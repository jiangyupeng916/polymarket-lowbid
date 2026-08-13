# 验证：Gamma 市场级 bestBid vs CLOB /prices 的 token 级 bestBid（BUY）
import json
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cbid(token_id):
    try:
        p = requests.get(f"{CLOB}/price", params={"token_id": token_id, "side": "BUY"}, timeout=20).json()
        return f(p.get("price"))
    except Exception:
        return None


# 分页扫描，收集 20 个 bestBid>0 的市场
cursor = None
samples = []
for _ in range(30):
    params = {"limit": 100, "order": "bestBid", "ascending": "true", "closed": "false"}
    if cursor:
        params["after_cursor"] = cursor
    d = requests.get(f"{GAMMA}/markets/keyset", params=params, timeout=30).json()
    markets = d.get("markets", [])
    for m in markets:
        gb = f(m.get("bestBid"))
        if gb is None or gb <= 0:
            continue
        try:
            tok0 = json.loads(m["clobTokenIds"])[0]
        except Exception:
            continue
        samples.append((m.get("id"), m.get("slug"), gb, tok0))
        if len(samples) >= 20:
            break
    if len(samples) >= 20:
        break
    cursor = d.get("next_cursor")
    if not cursor:
        break

print(f"采集 {len(samples)} 个 bestBid>0 的市场\n")
print(f"{'id':<9}{'gamma_bestBid':<15}{'clob_bestBid(YES)':<18}{'差异'}")
mismatch = 0
for mid, slug, gb, tok0 in samples:
    cb = cbid(tok0)
    if cb is None:
        diff = "CLOB 无返回"
    elif abs(cb - gb) < 1e-9:
        diff = "✓ 一致"
    else:
        diff = f"✗ 差 {cb - gb:+.4f}"
        mismatch += 1
    print(f"{mid:<9}{gb:<15}{str(cb):<18}{diff}   {slug[:25]}")
print(f"\n不一致: {mismatch}/{len(samples)}")
