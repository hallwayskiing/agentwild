"""The squad ladder (owner, 02:3x): at 19:30 (UTC+8) every hero is taken into the main character's squad and the squad is
signed up (the server wants 5 to 10 heroes walking with the main character); the tournament is fought at 20:00 (UTC+8) with
everyone as they are then. Afterwards the heroes go back to their squads. Every evening."""
import datetime
import time

from . import abyss, api, gear, rules

AT = datetime.time(11, 30)                          # 19:30 UTC+8, every evening
FIGHT_AFTER = datetime.timedelta(minutes=30)         # the tournament is fought at 20:00
RELEASE_AFTER = datetime.timedelta(minutes=36)       # the heroes go back to their squads
MIN_OTHERS = 4


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _next_start():
    """The coming 19:30 (UTC+8); a start less than 15 minutes old still counts (a restart during the evening)."""
    t = datetime.datetime.combine(_now().date(), AT, tzinfo=datetime.timezone.utc)
    return t if _now() <= t + datetime.timedelta(minutes=RELEASE_AFTER.total_seconds() // 60 - 1) else t + datetime.timedelta(days=1)


def _sleep_until(t):
    while _now() < t:
        time.sleep(min(30, max(1, (t - _now()).total_seconds())))


def watch():
    """Job, every evening: at 19:30 sign the main character up for the solo ladder, assemble everybody, sign the squad up,
    hold them until the tournament is fought, then send them back."""
    while True:
        start = _next_start()
        _sleep_until(start)
        evening(start)
        time.sleep(60)


def _together():
    """Everybody stands at the camp beside the main character, still on their own: one pool for the gear (the best pieces to
    the best heroes) and the coins above 50 to the main character, then they join."""
    try:
        gear.balance([list(rules.HERO_CONFIGS.keys())], everything=True)
        gear.collect_coins()
    except Exception as e:
        api.error("[ladder] gear/coins error %r" % e)


def evening(start):
    code, r = api.act(None, {"action": "ladder", "do": "join", "mode": "solo"})
    api.debug("[ladder] solo signup: %s %s" % (code, str(api.clean(r))[:200]))
    abyss.PAUSE.set()
    joined = []
    try:
        if _now() < start + FIGHT_AFTER:
            joined = abyss.assemble(before_join=_together)
            api.debug("[ladder] %d heroes walk with the main character: %s" % (len(joined), joined))
            if len(joined) < MIN_OTHERS:
                api.warning("[ladder] too few heroes (need %d): squad not signed up" % MIN_OTHERS)
            else:
                code, r = api.act(None, {"action": "ladder", "do": "join", "mode": "squad"})
                api.debug("[ladder] squad signup: %s %s" % (code, str(api.clean(r))[:300]))
        _sleep_until(start + RELEASE_AFTER)
        code, r = api.call("GET", "/world/ladder")
        api.info("[ladder] after the tournament: %s %s" % (code, str(r)[:600]))
    finally:
        for u in joined:
            api.act(None, {"action": "detach", "hero": u})   # the heroes go back to their squads
        abyss.PAUSE.clear()
