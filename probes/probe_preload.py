# 验证：并发预取 clob-markets 填 SDK 缓存后，create_limit_order 是否命中缓存（不再发 HTTP）
import json
import os
import time
from decimal import Decimal

from dotenv import load_dotenv

from polymarket import RelayerApiKey, SecureClient
from polymarket._internal.actions.orders.market_data import MarketInfo, PlatformFeeInfo
from polymarket.models.types import TokenId, CtfConditionId

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

# 取前 3 个 token 测试
items = list(cands.items())[:3]
for token_id, info in items:
    cond = info["cond"]
    data = client._ctx.secure_clob.get_json(f"/clob-markets/{cond}")
    mi = MarketInfo(
        fee_info=PlatformFeeInfo(rate=Decimal(0), exponent=Decimal(0)),
        neg_risk=data.get("nr", False),
        tick_size=Decimal(str(data.get("mts"))),
        token_ids=frozenset(TokenId(t["t"]) for t in data.get("t", [])),
    )
    om = client._ctx.order_metadata
    om._conditions.set(TokenId(token_id), CtfConditionId(cond))
    om._markets.set(CtfConditionId(cond), mi)
    print(f"预填 {token_id[:12]} cond={cond[:12]} tick={mi.tick_size} neg_risk={mi.neg_risk}")

print("\n开始 create_limit_order（应命中缓存，不再发 HTTP）...")
t0 = time.time()
for token_id, info in items:
    so = client.create_limit_order(
        token_id=token_id, price=info["bid"], size=10, side="BUY", post_only=True,
    )
    print(f"  {token_id[:12]} → SignedOrder(price={info['bid']}) ok")
print(f"3 个签名耗时 {time.time()-t0:.2f}s（应 <0.5s，若命中缓存）")
