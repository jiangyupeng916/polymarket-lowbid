# probe_account.py — 只读：查账户余额/授权、当前挂单数、挂单上限（open_orders_limit）
import json
import os

from dotenv import load_dotenv

from polymarket import RelayerApiKey, SecureClient

load_dotenv(".env.bot1")
client = SecureClient.create(
    private_key=os.environ["SIGNER_PRIVATE_KEY"],
    wallet=os.environ["POLYMARKET_WALLET_ADDRESS"],
    api_key=RelayerApiKey(
        key=os.environ["POLYMARKET_RELAYER_API_KEY"],
        address=os.environ["POLYMARKET_RELAYER_API_KEY_ADDRESS"],
    ),
)

print("=== 余额/授权 (COLLATERAL = USDC) ===")
try:
    ba = client.get_balance_allowance(asset_type="COLLATERAL")
    print(type(ba).__name__, "字段:", [f for f in ba.model_fields] if hasattr(ba, "model_fields") else dir(ba))
    print(json.dumps(ba.model_dump(), indent=1, default=str))
except Exception as e:
    print("查余额失败:", e)

print("\n=== 当前挂单数 ===")
try:
    orders = list(client.list_open_orders())
    print("open orders:", len(orders))
    for o in orders[:5]:
        print("  ", getattr(o, "token_id", "?")[:14], getattr(o, "price", "?"), getattr(o, "side", "?"))
except Exception as e:
    print("查挂单失败:", e)

print("\n=== 账户限制（尝试 /v1/account/limits）===")
try:
    creds = client.credentials
    # 尝试用 SDK 内部 http 客户端；拿不到就提示
    print("credentials 有 key/secret:", bool(getattr(creds, "key", None)), bool(getattr(creds, "secret", None)))
    # 直接看 client 有没有可用的 http 层
    print("client 属性:", [a for a in dir(client) if "http" in a.lower() or "session" in a.lower() or "api" in a.lower()])
except Exception as e:
    print("失败:", e)
