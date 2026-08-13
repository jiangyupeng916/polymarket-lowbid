# 对比前段（timed out）vs 中段（成功）市场的 slug + 关键字段
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


def dump(idx_range, label):
    print(f"\n=== {label} ===")
    for token_id, info in cands[idx_range]:
        d = sc.get_json(f"/clob-markets/{info['cond']}")
        print(f"  {info.get('slug','')[:55]}")
        print(f"    bid={info['bid']} ao={d.get('ao')} nr={d.get('nr')} cbos={d.get('cbos')} "
              f"ibce={d.get('ibce')} mts={d.get('mts')} mos={d.get('mos')} aot={d.get('aot')}")


dump(slice(0, 8), "前段 [0:8]（持续 timed out）")
dump(slice(1000, 1008), "中段 [1000:1008]（之前成功）")
