# scan_candidates.py — 冷启动扫描（低频，启动时/每几小时跑一次）
# 筛选：结算>20天、bestBid<0.05、有真实盘口 → 缓存到 data/candidates.json
# 特点：每页增量落盘 + 断点续扫（存 cursor），随时 Ctrl+C，重跑自动续扫。
# 慢（Gamma keyset 全量约 10 分钟）但只在启动/每几小时跑；15 分钟热循环另由 poller.py 走 /prices。
import datetime
import json
import logging
import os
import sys
import time
import warnings

import requests

warnings.filterwarnings("ignore")

GAMMA = "https://gamma-api.polymarket.com"
DAYS_AHEAD = 20
PRICE_CEIL = 0.05
DATA_FILE = os.path.join("data", "candidates.json")
HTTP_TIMEOUT = 45
RETRIES = 4

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scan")
SESSION = requests.Session()


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}


def _save(state):
    os.makedirs("data", exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def _get(params):
    for i in range(RETRIES):
        try:
            r = SESSION.get(f"{GAMMA}/markets/keyset", params=params, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            log.warning("第%d次 HTTP %s", i + 1, r.status_code)
        except requests.RequestException as e:
            log.warning("第%d次失败: %s", i + 1, e)
        time.sleep(1.5 * (i + 1))
    return None


def main():
    end_date_min = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=DAYS_AHEAD)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = _load()
    cands = state.get("candidates", {})
    cursor = state.get("cursor")
    log.info("断点续扫：已有候选 %d 个，cursor=%s", len(cands), "有" if cursor else "无")

    pages = 0
    while True:
        params = {"limit": 100, "order": "bestBid", "ascending": "true",
                  "closed": "false", "end_date_min": end_date_min}
        if cursor:
            params["after_cursor"] = cursor
        d = _get(params)
        if not isinstance(d, dict):
            log.error("该页连续失败，已停止（保留 %d 候选）", len(cands))
            break
        markets = d.get("markets") or []
        if not markets:
            log.info("无更多页，扫描完成")
            cursor = None
            break
        done = False
        for m in markets:
            b, a = _float(m.get("bestBid")), _float(m.get("bestAsk"))
            if b is not None and b >= PRICE_CEIL:
                done = True            # bestBid 升序，后面都 >=0.05
                break
            if b is None or b == 0 or a is None or a >= 0.5:
                continue               # 空盘口
            try:
                tok0 = json.loads(m["clobTokenIds"])[0]
            except Exception:
                continue
            cands[tok0] = {"cond": m.get("conditionId"), "slug": m.get("slug"),
                           "end_date": m.get("endDate"), "bid": b}
        pages += 1
        cursor = d.get("next_cursor")
        _save({"cursor": cursor, "end_date_min": end_date_min, "candidates": cands})
        if done or not cursor:
            break
        if pages % 10 == 0:
            log.info("已扫 %d 页，候选 %d 个", pages, len(cands))

    if not cursor and pages:
        log.info("扫描完成：候选 %d 个", len(cands))
    else:
        log.info("扫描未完成：候选 %d 个，可重跑续扫", len(cands))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("已停止，进度已落盘")
        sys.exit(0)
