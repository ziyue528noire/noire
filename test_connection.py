# -*- coding: utf-8 -*-
"""只读测试：确认能连上 Alpaca 模拟账户。不会下单，也不会打印密钥。"""
from keys import load_keys


def main():
    key, secret = load_keys()
    if not key or not secret:
        print("RESULT: NO_KEYS - 没找到密钥，请重新运行 set_keys.bat")
        return
    print(f"已读取密钥（Key 长度 {len(key)}，Secret 长度 {len(secret)}）")
    try:
        from alpaca.trading.client import TradingClient
        acct = TradingClient(key, secret, paper=True).get_account()
        print("RESULT: OK - 已连接 Alpaca 模拟账户")
        print(f"  账户状态: {acct.status}")
        print(f"  账户净值: ${float(acct.equity):,.2f}")
        print(f"  可用购买力: ${float(acct.buying_power):,.2f}")
        print(f"  是否被限制交易: {acct.trading_blocked}")
    except Exception as e:
        msg = str(e).replace(key, "***").replace(secret, "***")
        print(f"RESULT: FAILED - {type(e).__name__}: {msg[:300]}")
        print("常见原因：复制时多了空格、用了实盘(Live)密钥而不是模拟盘(Paper)密钥、Secret 已失效需重新生成")


if __name__ == "__main__":
    main()
