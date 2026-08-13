# 查 candidates 前段（持续 timed out）市场的 clob-markets 完整字段，找共同特征
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

with open("data/candidates.json", encoding="utf-8") as f:
    cands = list(json.load(f)["candidates"].items())

# 前 5 个候选的完整 clob-markets 响应
for token_id, info in cands[:5]:
    cond = info["cond"]
    d = sc.get_json(f"/clob-markets/{cond}")
    print(f"=== {token_id[:12]} bid={info['bid']} slug={info.get('slug','')[:40]}")
    print(json.dumps(d, indent=1)[:1200])
    print()
