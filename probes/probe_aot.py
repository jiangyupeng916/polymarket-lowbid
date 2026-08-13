# 验证：单笔 post_order 挂不同 aot 时间的市场，看是否与 "order timed out" 关联
import json
import os
import time
from decimal import Decimal

from dotenv import load_dotenv

from polymarket import RejectedOrder, RelayerApiKey, SecureClient
from polymarket._internal.actions.orders.market_data import MarketInfo, PlatformFeeInfo
from polymarket.models.types import CtfConditionId, TokenId

load_dotenv(".env.bot1")
client = SecureClient.create(
    private_key=os.environ["SIGNER_PRIVATE_KEY"],
    wallet=os.environ["POLYMARKET_WALLET_ADDRESS"],
    api_key=RelayerApiKey(
        key=os.environ["POLYMARKET_RELAYER_API_KEY"],
        address=os.environ["POLYMARKET_RELAYER_API_KEY_ADDRESS"],
    ),
)
sc = client._ctx.secure_clob
om = client._ctx.order_metadata

with open("data/candidates.json", encoding="utf-8") as f:
    cands = list(json.load(f)["candidates"].items())


def preload(token_id, info):
    cond = info["cond"]
    d = sc.get_json(f"/clob-markets/{cond}")
    om._conditions.set(TokenId(token_id), CtfConditionId(cond))
    om._markets.set(CtfConditionId(cond), MarketInfo(
        fee_info=PlatformFeeInfo(rate=Decimal(0), exponent=Decimal(0)),
        neg_risk=d.get("nr", False),
        tick_size=Decimal(str(d.get("mts"))),
        token_ids=frozenset(TokenId(t["t"]) for t in d.get("t", [])),
    ))
    return d.get("aot")


def single_place(token_id, info):
    try:
        signed = client.create_limit_order(token_id=token_id, price=info["bid"], size=10, side="BUY", post_only=True)
        r = client.post_order(signed)
        if isinstance(r, RejectedOrder) or not r.ok:
            return f"拒: {getattr(r,'message','?')}"
        return f"成功: {r.status}"
    except Exception as e:
        return f"异常: {str(e)[:80]}"


# 挑不同 aot 段的市场各 2 个测试
picks = [0, 1, 500, 1000, 2000, 3000]
for idx in picks:
    token_id, info = cands[idx]
    aot = preload(token_id, info)
    res = single_place(token_id, info)
    print(f"[{idx}] aot={aot} bid={info['bid']} {info.get('slug','')[:40]} → {res}")
    time.sleep(0.3)
