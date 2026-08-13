# 对照实验：单笔 post_order vs 批量 post_orders，定位 "order timed out" 原因
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

with open("data/candidates.json", encoding="utf-8") as f:
    cands = json.load(f)["candidates"]

# 取 10 个候选（从后段取，避开已经挂上的）
items = list(cands.items())[1000:1010]
om = client._ctx.order_metadata
sc = client._ctx.secure_clob

# 预取市场信息
for token_id, info in items:
    cond = info["cond"]
    try:
        data = sc.get_json(f"/clob-markets/{cond}")
        mi = MarketInfo(
            fee_info=PlatformFeeInfo(rate=Decimal(0), exponent=Decimal(0)),
            neg_risk=data.get("nr", False),
            tick_size=Decimal(str(data.get("mts"))),
            token_ids=frozenset(TokenId(t["t"]) for t in data.get("t", [])),
        )
        om._conditions.set(TokenId(token_id), CtfConditionId(cond))
        om._markets.set(CtfConditionId(cond), mi)
    except Exception as e:
        print("预取失败", token_id[:12], e)

print("=== 单笔 post_order（前 5 个）===")
for token_id, info in items[:5]:
    try:
        signed = client.create_limit_order(token_id=token_id, price=info["bid"], size=10, side="BUY", post_only=True)
        r = client.post_order(signed)
        if isinstance(r, RejectedOrder) or not r.ok:
            print(f"  {token_id[:12]} 拒: code={getattr(r,'code','?')} msg={getattr(r,'message','?')}")
        else:
            print(f"  {token_id[:12]} 成功: status={r.status}")
    except Exception as e:
        print(f"  {token_id[:12]} 异常: {str(e)[:120]}")
    time.sleep(0.4)

print("=== 批量 post_orders（后 5 个）===")
signed5 = []
for token_id, info in items[5:]:
    signed5.append(client.create_limit_order(token_id=token_id, price=info["bid"], size=10, side="BUY", post_only=True))
try:
    results = client.post_orders(signed_orders=signed5)
    for r in results:
        if isinstance(r, RejectedOrder) or not r.ok:
            print(f"  拒: code={getattr(r,'code','?')} msg={getattr(r,'message','?')}")
        else:
            print(f"  成功: status={r.status}")
except Exception as e:
    print("post_orders 异常:", str(e)[:200])
