# market_sell_all.py — 快速将所有持仓以市价卖出（FAK），卖不出的跳过
#
# 用法：python market_sell_all.py
#
# 说明：
# - 市价单（order_type=FAK）吃盘，能成交多少成交多少，卖不出去的部分/整单被拒绝
# - 所有写操作走全局限流（rate_wait），并发 4 线程
# - 注意：市价单是 taker，立即成交且有 taker 费（非挂单）
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from polymarket import RejectedOrder

import bot  # 复用 load_client / list_positions / rate_wait

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mkt_sell")

WORKERS = 4  # 并发线程（市价单吃盘，别太猛，避免打爆深度）


def market_sell_one(client, token_id, shares):
    """市价卖出单个持仓。返回 (ok, msg)。"""
    bot.rate_wait()
    try:
        resp = client.place_market_order(
            token_id=token_id, side="SELL", shares=shares, order_type="FAK",
        )
    except Exception as e:
        return False, f"异常: {str(e)[:100]}"
    if isinstance(resp, RejectedOrder) or not resp.ok:
        return False, (resp.message if hasattr(resp, "message") else str(resp))
    status = getattr(resp, "status", "?")
    n_trades = len(getattr(resp, "trade_ids", None) or [])
    return True, f"status={status} 成交{n_trades}单"


def main():
    client = bot.load_client()
    positions = bot.list_positions(client)
    print(f"当前持仓 {len(positions)} 个")

    if not positions:
        print("没有持仓，无需卖出")
        return

    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(market_sell_one, client, t, p["size"]): t
                for t, p in positions.items()}
        done = 0
        for fut in as_completed(futs):
            token_id = futs[fut]
            ok, msg = fut.result()
            results.append((token_id, ok, msg))
            done += 1
            if done % 50 == 0 or done == len(futs):
                print(f"进度 {done}/{len(futs)}")

    sold = sum(1 for _, ok, _ in results if ok)
    skipped = sum(1 for _, ok, _ in results if not ok)
    print(f"\n完成：市价成交 {sold}，跳过 {skipped}，耗时 {time.time() - t0:.1f}s")

    if skipped:
        print("\n跳过（卖不出）的前 20 个：")
        for tid, ok, msg in [r for r in results if not ok][:20]:
            print(f"  {tid[:16]}… {msg}")

    left = bot.list_positions(client)
    print(f"\n卖出后剩余持仓 {len(left)} 个")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("已停止")
        sys.exit(0)
