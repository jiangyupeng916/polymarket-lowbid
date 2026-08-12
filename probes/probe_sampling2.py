# 只读：/sampling-markets 全量分页扫描 —— 总量、候选数、稳定性
import datetime
import time

import requests

CLOB = "https://clob.polymarket.com"

end_min = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=20)).isoformat() + "Z"
print("end_date_min(>20天)=", end_min)


def get_page(cursor):
    params = {}
    if cursor:
        params["next_cursor"] = cursor
    for i in range(3):
        try:
            r = requests.get(f"{CLOB}/sampling-markets", params=params, timeout=60)
            return r.json()
        except Exception as e:
            print(f"  页失败({i+1}): {e}")
            time.sleep(2)
    return None


total = 0
cheap = 0
empty = 0
cursor = None
t0 = time.time()
for page in range(40):
    d = get_page(cursor)
    if not d or not d.get("data"):
        print("没有更多或失败")
        break
    items = d["data"]
    total += len(items)
    for x in items:
        if not x.get("active"):
            continue
        if not x.get("end_date_iso") or x["end_date_iso"] < end_min:
            continue
        found = False
        for t in x.get("tokens", []):
            try:
                if float(t["price"]) < 0.05:
                    found = True
                    break
            except (TypeError, ValueError):
                pass
        if found:
            cheap += 1
    cursor = d.get("next_cursor")
    if not cursor:
        break

print(f"\n总市场(active): {total}  候选(>20天 & price<0.05): {cheap}  页数: {page+1}  耗时: {time.time()-t0:.1f}s")
