# sell_bot.py — 卖出循环（独立运行）：定时轮询持仓 → 对标最新 bestAsk 调整卖单
#
# 与 bot.py 的买入循环解耦，可单独用更短的间隔跑卖出（如 15 分钟一轮）。
# 复用 bot.py 里已验证的取价/规划/执行逻辑（全局限流 + 单笔下单 + 撤旧挂新）。
#
# 用法：
#   python sell_bot.py --dry-run --once      # 试跑一轮（不真实操作）
#   python sell_bot.py --live --once         # 真实跑一轮退出
#   python sell_bot.py --live --interval 900 # 真实循环，每 15 分钟一轮（默认）
#
# 每轮逻辑：
#   1) 查持仓 list_positions（份额 > 5 才可卖）
#   2) POST /prices side=SELL 取每个持仓的实时 bestAsk
#   3) 对比当前卖单：
#      - 已挂 SELL 单但 token 无持仓 → 撤单清理
#      - 有持仓、未挂卖单 → 挂卖单 @ bestAsk（post-only，被动）
#      - 有持仓、已挂但 bestAsk 变了 → 撤旧挂新
#      - 价格没变 → 保持不动
import argparse
import logging
import sys
import time

import bot  # 复用 bot.py 的客户端、取价、规划、执行逻辑（bot.py import 无副作用）

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sellbot")

# 默认轮询间隔：卖出需要比买入更及时，默认 15 分钟
DEFAULT_INTERVAL = 900


def one_round(client, live):
    t0 = time.time()
    positions = bot.list_positions(client)
    open_orders = bot.list_open_orders(client)

    pos_ids = list(positions.keys())
    asks = bot.fetch_prices(pos_ids, "SELL") if pos_ids else {}
    sell_cancel, sell_place = bot.plan_sell(positions, open_orders, asks)
    log.info("持仓 %d，待撤卖单 %d，待挂卖单 %d（%.1fs）",
             len(positions), len(sell_cancel), len(sell_place), time.time() - t0)

    # 先撤，再挂
    bot.do_cancel(client, sell_cancel, live)
    token_to_cond = {t: positions[t]["cond"] for t in sell_place
                     if t in positions and positions[t].get("cond")}
    bot.do_place(client, sell_place, token_to_cond, live)
    log.info("本轮卖出完成 %.1fs", time.time() - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="真实下单/撤单")
    ap.add_argument("--dry-run", action="store_true", help="只计算打印（默认）")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="轮询间隔秒")
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    args = ap.parse_args()
    if args.live and args.dry_run:
        ap.error("--live 与 --dry-run 不能同时用")
    if args.dry_run:
        args.live = False

    log.info("== 卖出循环 %s 模式 ==", "LIVE" if args.live else "DRY-RUN")
    client = bot.load_client()
    while True:
        one_round(client, args.live)
        if args.once:
            log.info("单轮完成，退出")
            break
        log.info("%.0fs 后下一轮", args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("已停止")
        sys.exit(0)
