# 直接看 POST /orders 原始响应，理解 "order timed out" 完整上下文
import json
import os
from decimal import Decimal

from dotenv import load_dotenv

from polymarket import RelayerApiKey, SecureClient
from polymarket._internal.actions.orders import post as _post_actions
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

# 预取一个市场（用之前失败的 [0]）
token_id, info = cands[0]
cond = info["cond"]
d = sc.get_json(f"/clob-markets/{cond}")
om._conditions.set(TokenId(token_id), CtfConditionId(cond))
om._markets.set(CtfConditionId(cond), MarketInfo(
    fee_info=PlatformFeeInfo(rate=Decimal(0), exponent=Decimal(0)),
    neg_risk=d.get("nr", False),
    tick_size=Decimal(str(d.get("mts"))),
    token_ids=frozenset(TokenId(t["t"]) for t in d.get("t", [])),
))

signed = client.create_limit_order(token_id=token_id, price=info["bid"], size=10, side="BUY", post_only=True)
path, payload = _post_actions.build_post_orders_request([signed], owner_api_key=client._ctx.credentials.key)
print("path:", path)
print("payload type:", type(payload).__name__, "len:", len(payload))
raw = sc.post_json(path, json=payload)
print("\n=== 原始响应 ===")
print(json.dumps(raw, indent=1, default=str)[:2000])
