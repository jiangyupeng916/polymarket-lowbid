# probe_markets.py — 只读探针（不提交任何订单）
# 目标：实测低价长周期市场的真实数据，为挂单策略定参数
#   1) Gamma /markets: 结算>20天 + bestBid 从低到高排
#   2) 筛 bestBid<0.05 的候选
#   3) 对前几个候选，按 tokenId 拉 CLOB /book，核对每枚 token 的
#      bestBid/bestAsk/min_order_size/tick_size
import datetime
import json
import sys

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DAYS_AHEAD = 20
PRICE_CEIL = 0.05
LIMIT = 100


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    end_date_min = (now + datetime.timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[1] Gamma /markets: end_date_min={end_date_min} (结算>{DAYS_AHEAD}天) closed=false order=bestBid asc")

    r = requests.get(
        f"{GAMMA}/markets",
        params={
            "closed": "false",
            "end_date_min": end_date_min,
            "order": "bestBid",
            "ascending": "true",
            "limit": str(LIMIT),
        },
        timeout=30,
    )
    r.raise_for_status()
    markets = r.json()
    print(f"    返回 {len(markets)} 个市场")

    def _float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def bid(m):
        return _float(m.get("bestBid"))

    cheap = [m for m in markets if (b := bid(m)) is not None and b < PRICE_CEIL]
    print(f"    bestBid < {PRICE_CEIL} 的市场: {len(cheap)} 个\n")

    print("[2] 低价市场一览（前 15 个）")
    print(f"    {'id':<6}{'slug':<42}{'endDate':<20}{'bid':<7}{'ask':<7}{'outcomePrices':<18}clobTokenIds")
    for m in cheap[:15]:
        print(
            f"    {str(m.get('id')):<6}"
            f"{str(m.get('slug'))[:41]:<42}"
            f"{str(m.get('endDate'))[:19]:<20}"
            f"{str(m.get('bestBid')):<7}"
            f"{str(m.get('bestAsk')):<7}"
            f"{str(m.get('outcomePrices')):<18}"
            f"{m.get('clobTokenIds')}"
        )

    # 只看有真实盘口（ask 非空且 <0.5）的低价市场，跳过 bestBid=0/ask=1 的空簿
    liquid = [m for m in cheap if (a := _float(m.get("bestAsk"))) is not None and 0 < a < 0.5]
    print(f"    其中 ask<0.5（有真实盘口）的市场: {len(liquid)} 个\n")
    print("[3] 对有真实盘口的前 5 个市场，拉两枚 token 的 CLOB /book（只读）")
    for m in liquid[:5]:
        cond = m.get("conditionId")
        try:
            tokens = json.loads(m["clobTokenIds"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        print(f"\n    -- market id={m.get('id')} slug={m.get('slug')} conditionId={cond}")
        for t in tokens:
            try:
                b = requests.get(f"{CLOB}/book", params={"token_id": t}, timeout=30)
                b.raise_for_status()
                d = b.json()
                bids = d.get("bids", [])[:2]
                asks = d.get("asks", [])[:2]
                print(
                    f"      token={t[:12]}  bestBid={bids[0]['price'] if bids else '-'} "
                    f"bestAsk={asks[0]['price'] if asks else '-'} "
                    f"min_order_size={d.get('min_order_size')} tick_size={d.get('tick_size')}"
                )
            except Exception as e:
                print(f"      token={t[:12]}  出错: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
