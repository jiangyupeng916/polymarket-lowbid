# 对比 candidates 前段 vs 中段的 clob-markets 关键字段（neg_risk / itode 等）
import json
import os
from collections import Counter

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


def sample(idx_range, label):
    cnt = Counter()
    fields_seen = set()
    for token_id, info in cands[idx_range]:
        cond = info["cond"]
        try:
            d = sc.get_json(f"/clob-markets/{cond}")
            fields_seen.update(d.keys())
            cnt[f"neg_risk={d.get('nr')}"] += 1
            cnt[f"itode={d.get('itode')}"] += 1
            cnt[f"bid={info['bid']}"] += 1
        except Exception as e:
            cnt[f"ERR:{str(e)[:30]}"] += 1
    print(f"\n=== {label}（candidates[{idx_range.start}:{idx_range.stop}]）===")
    for k, v in cnt.most_common():
        print(f"  {k}: {v}")
    return fields_seen


f1 = sample(slice(0, 30), "前段 bestBid 最低")
f2 = sample(slice(1000, 1030), "中段")
print("\nclob-markets 顶层字段（前段）:", sorted(f1))
