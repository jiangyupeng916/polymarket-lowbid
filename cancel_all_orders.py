# cancel_all_orders.py — 取消账户所有挂单（SDK cancel_all，一次搞定）
# 用法：python cancel_all_orders.py
import os

from dotenv import load_dotenv

from polymarket import RelayerApiKey, SecureClient


def main():
    load_dotenv(".env.bot1")
    client = SecureClient.create(
        private_key=os.environ["SIGNER_PRIVATE_KEY"],
        wallet=os.environ["POLYMARKET_WALLET_ADDRESS"],
        api_key=RelayerApiKey(
            key=os.environ["POLYMARKET_RELAYER_API_KEY"],
            address=os.environ["POLYMARKET_RELAYER_API_KEY_ADDRESS"],
        ),
    )

    orders = list(client.list_open_orders().iter_items())
    print(f"当前挂单 {len(orders)} 个")

    if not orders:
        print("没有挂单，无需取消")
        return

    # DELETE /cancel-all：先消耗 1 个 cancel token，执行后按取消数扣款（标准 tier 允许负余额），
    # 不是 all-or-nothing，可一次取消所有单
    res = client.cancel_all()
    canceled = list(res.canceled) if res.canceled else []
    not_canceled = dict(res.not_canceled) if res.not_canceled else {}
    print(f"取消 {len(canceled)} 个，失败 {len(not_canceled)} 个")
    if not_canceled:
        for oid, err in list(not_canceled.items())[:10]:
            print(f"  失败 {str(oid)[:16]}...: {err}")

    # 复查剩余
    left = list(client.list_open_orders().iter_items())
    print(f"取消后剩余挂单 {len(left)} 个")


if __name__ == "__main__":
    main()
