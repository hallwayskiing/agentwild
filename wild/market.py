"""The camp market (coins, TOTEM): buy goods from the cheapest listings. Buying works only in the camp and needs a hero."""
import time

from . import api


def _me(unit):
    """A read that retries (rate limits and hiccups return nothing)."""
    for _ in range(10):
        m, _ = api.me(unit)
        if m:
            return m
        time.sleep(5)
    raise RuntimeError("cannot read %s" % unit)


def listings(goods):
    code, r = api.call("GET", "/world/market?goods=%s&sort=price" % goods)
    return r.get("listings") or [] if code == 200 else []


def buy(unit, goods, qty, max_each=3):
    """Buy up to `qty` of `goods` from the cheapest listings (none dearer than max_each) with the coins at hand.
    The unit goes home first. Returns the amount bought."""
    m = _me(unit)
    for _ in range(6):  # `home` takes about 20 seconds: wait until we are in the camp
        if m["ring"] == "camp":
            break
        api.act(unit, {"action": "home"})
        time.sleep(22)
        m = _me(unit)
    bought = 0
    for l in listings(goods):
        if bought >= qty:
            break
        m = _me(unit)
        each = l.get("price_each", 1 << 30)
        take = min(l["qty"], qty - bought, (m["coins"] // each) if each else l["qty"])
        if each > max_each or take <= 0:
            continue
        code, r = api.act(unit, {"action": "buy", "listing": l["id"], "qty": take})
        api.info("[%s] buy %d %s at %s each from listing %s: %s %s" % (unit, take, goods, each, l["id"], code, "" if code == 200 else r.get("error")))
        if code == 200:
            bought += take
    return bought
