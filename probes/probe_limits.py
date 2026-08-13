# probe_limits.py — 只读：查账户 open_orders 上限 + 当前挂单数
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

sc = client._ctx.secure_clob
for path in ("/v1/account/limits", "/account/limits"):
    try:
        r = sc.get_json(path)
        print(f"{path} →")
        print(json.dumps(r, indent=1, default=str))
        break
    except Exception as e:
        print(f"{path} 失败: {str(e)[:160]}")
