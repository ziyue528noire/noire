# -*- coding: utf-8 -*-
"""只读测试：确认能连上 Alpaca 账户（模拟盘；实盘脚本里是实盘账户）。不会下单，也不会打印密钥。"""
import runmode
import broker
from keys import load_keys


def main():
    key, secret = load_keys()
    if not key or not secret:
        bat = "set_live_keys.bat" if runmode.IS_LIVE else "set_keys.bat"
        print(f"RESULT: NO_KEYS - 没找到密钥，请重新运行 {bat}")
        return
    print(f"已读取{runmode.LABEL}密钥（Key 长度 {len(key)}，Secret 长度 {len(secret)}）")
    try:
        acct = broker.client(key, secret, trading=False).get_account()
        print(f"RESULT: OK - 已连接 Alpaca {runmode.LABEL}账户（只读）")
        print(f"  账户状态: {acct.status}")
        print(f"  账户净值: ${float(acct.equity):,.2f}")
        print(f"  可用购买力: ${float(acct.buying_power):,.2f}")
        print(f"  是否被限制交易: {acct.trading_blocked}")
        if runmode.IS_LIVE:
            ok, why = broker.live_trading_ready()
            print(f"  自动真钱交易: {'已打开' if ok else '没有打开（' + why + '）'}")
    except Exception as e:
        msg = str(e).replace(key, "***").replace(secret, "***")
        print(f"RESULT: FAILED - {type(e).__name__}: {msg[:300]}")
        if runmode.IS_LIVE:
            print("常见原因：复制时多了空格、用了模拟盘(Paper)密钥而不是实盘(Live)密钥、实盘账户还没开通或还没入金")
        else:
            print("常见原因：复制时多了空格、用了实盘(Live)密钥而不是模拟盘(Paper)密钥、Secret 已失效需重新生成")


if __name__ == "__main__":
    main()
