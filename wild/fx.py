"""The coin exchange (https://agentwild.fun/exchange): trade game coins for BOLT on an order book.

Endpoints:
    GET  /world/fx/me          - Balance, open orders, recent trades
    GET  /world/fx             - Order book (bids, asks), last price, market trades
    POST /world/fx/order       - Place buy or sell order: {"side": "buy"|"sell", "qty": int, "price": str}
    POST /world/fx/cancel      - Cancel order: {"order": int}
    POST /world/fx/withdraw    - Request withdrawal voucher: {"amount": str}
    GET  /world/fx/voucher     - View active withdrawal voucher transaction
"""
import json
import sys
import time

from . import api


def me(amount=None):
    """GET /world/fx/me (optional ?amount=1000 for deposit instructions)."""
    path = "/world/fx/me" if amount is None else f"/world/fx/me?amount={amount}"
    code, r = api.call("GET", path)
    if code != 200:
        api.warning("[fx] failed to read fx/me: %s %s" % (code, r.get("error") if isinstance(r, dict) else r))
        return None
    return r


def book():
    """GET /world/fx: order book, last price and trades."""
    code, r = api.call("GET", "/world/fx")
    if code != 200:
        api.warning("[fx] failed to read fx order book: %s" % code)
        return None
    return r


def order(side, qty, price):
    """POST /world/fx/order: place buy or sell order.
    `side`: 'buy' or 'sell'
    `qty`: whole coins (int)
    `price`: BOLT per coin (str or float)
    """
    side = str(side).lower().strip()
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    qty = int(qty)
    if qty <= 0:
        raise ValueError("qty must be positive integer")
    body = {"side": side, "qty": qty, "price": str(price)}
    code, r = api.call("POST", "/world/fx/order", body)
    api.info("[fx] order %s %d coins @ %s BOLT: code %s %s" % (
        side, qty, price, code, "" if code == 200 else (r.get("error") if isinstance(r, dict) else r)))
    return code, r


def cancel(order_id):
    """POST /world/fx/cancel: cancel an open order."""
    order_id = int(order_id)
    code, r = api.call("POST", "/world/fx/cancel", {"order": order_id})
    api.info("[fx] cancel order %d: code %s %s" % (
        order_id, code, "" if code == 200 else (r.get("error") if isinstance(r, dict) else r)))
    return code, r


def withdraw(amount=None):
    """POST /world/fx/withdraw: request withdrawal voucher transaction.
    If amount is None or 'all', withdraws the entire available BOLT balance.
    """
    if amount is None or str(amount).lower() == "all":
        info = me()
        if not info:
            return 0, {"error": "cannot read fx/me to get balance"}
        amount = info.get("you", {}).get("bolt", "0")
    amount = str(amount).strip()
    code, r = api.call("POST", "/world/fx/withdraw", {"amount": amount})
    api.info("[fx] withdraw %s BOLT: code %s %s" % (
        amount, code, "" if code == 200 else (r.get("error") if isinstance(r, dict) else r)))
    return code, r


def voucher():
    """GET /world/fx/voucher: read current unexpired voucher transaction."""
    code, r = api.call("GET", "/world/fx/voucher")
    return code, r


def print_status():
    """Format and print full status for the CLI."""
    info = me()
    if not info:
        print("无法获取金币交易所账户信息。")
        return
    you = info.get("you") or {}
    print("=" * 60)
    print("【金币交易所 (Coin Exchange) 账户状态】")
    print("=" * 60)
    print("角色金币:       %s coins" % you.get("coins", 0))
    print("可用 BOLT 余额: %s BOLT" % you.get("bolt", "0"))
    print("挂单中 BOLT:    %s BOLT" % you.get("bolt_in_orders", "0"))
    print("历史已充值:     %s BOLT" % you.get("deposited", "0"))
    print("历史已提现:     %s BOLT" % you.get("withdrawn", "0"))
    print("-" * 60)

    orders = info.get("orders") or []
    print("当前活动挂单 (%d 个):" % len(orders))
    if orders:
        print("  %-8s %-6s %-10s %-12s %-12s" % ("订单ID", "方向", "数量(金币)", "单价(BOLT)", "总价(BOLT)"))
        for o in orders:
            print("  %-8s %-6s %-10s %-12s %-12s" % (
                o.get("id"), o.get("side"), o.get("qty"), o.get("price"), o.get("total", o.get("bolt", "-"))))
    else:
        print("  (当前暂无挂单)")
    print("-" * 60)

    trades = info.get("trades") or []
    print("最近成交记录 (%d 条):" % len(trades))
    if trades:
        print("  %-6s %-6s %-10s %-12s %-14s %-20s" % ("ID", "方向", "数量(金币)", "单价(BOLT)", "成交BOLT", "成交时间(UTC)"))
        for t in trades[:10]:
            print("  %-6s %-6s %-10s %-12s %-14s %-20s" % (
                t.get("id"), t.get("side"), t.get("qty"), t.get("price"), t.get("bolt"), t.get("at", "")[:19]))
    else:
        print("  (暂无成交记录)")
    print("=" * 60)


def print_book():
    """Format and print order book."""
    b = book()
    if not b:
        print("无法获取交易所订单簿。")
        return
    print("=" * 60)
    print("【金币交易所 (Coin Exchange) 盘口订单簿】")
    print("最新成交价: %s BOLT / coin" % b.get("last_price", "N/A"))
    print("=" * 60)
    asks = b.get("asks") or []
    bids = b.get("bids") or []
    print("卖盘 (Asks - 卖出金币 / 换取 BOLT):")
    if asks:
        print("  %-12s %-12s %-12s" % ("单价(BOLT)", "数量(金币)", "总计(BOLT)"))
        for a in asks[:10]:
            print("  %-12s %-12s %-12s" % (a.get("price"), a.get("qty"), a.get("total", a.get("bolt", "-"))))
    else:
        print("  (暂无卖单)")
    print("-" * 60)
    print("买盘 (Bids - 买入金币 / 支付 BOLT):")
    if bids:
        print("  %-12s %-12s %-12s" % ("单价(BOLT)", "数量(金币)", "总计(BOLT)"))
        for bd in bids[:10]:
            print("  %-12s %-12s %-12s" % (bd.get("price"), bd.get("qty"), bd.get("total", bd.get("bolt", "-"))))
    else:
        print("  (暂无买单)")
    print("=" * 60)


def cli(action, args):
    """CLI dispatcher for wild.py fx."""
    if action == "status":
        print_status()
    elif action == "book":
        print_book()
    elif action == "sell":
        if len(args) < 2:
            print("用法: python wild.py fx sell <数量:金币整数> <单价:BOLT>")
            return
        code, r = order("sell", args[0], args[1])
        print("挂卖单结果 (%s): %s" % (code, json.dumps(r, ensure_ascii=False)))
    elif action == "buy":
        if len(args) < 2:
            print("用法: python wild.py fx buy <数量:金币整数> <单价:BOLT>")
            return
        code, r = order("buy", args[0], args[1])
        print("挂买单结果 (%s): %s" % (code, json.dumps(r, ensure_ascii=False)))
    elif action == "cancel":
        if not args:
            print("用法: python wild.py fx cancel <订单ID>")
            return
        code, r = cancel(args[0])
        print("撤单结果 (%s): %s" % (code, json.dumps(r, ensure_ascii=False)))
    elif action == "withdraw":
        amt = args[0] if args else "all"
        code, r = withdraw(amt)
        print("申请提现结果 (%s): %s" % (code, json.dumps(r, ensure_ascii=False, indent=2)))
        if code == 200 and isinstance(r, dict) and r.get("transaction"):
            tx = r["transaction"]
            print("\n" + "=" * 60)
            print("【提现交易凭证已生成】请在 1 小时内向金库合约发送以下交易提取 BOLT：")
            print("To (Vault):    %s" % tx.get("to"))
            print("From (Wallet): %s" % tx.get("from"))
            print("Value:         %s" % tx.get("value"))
            print("Data:          %s" % tx.get("data"))
            print("=" * 60)
    elif action == "voucher":
        code, r = voucher()
        print("当前提现凭证 (%s): %s" % (code, json.dumps(r, ensure_ascii=False, indent=2)))
    else:
        print("未知操作: %s" % action)
        print("可用命令: status, book, buy, sell, cancel, withdraw, voucher")
