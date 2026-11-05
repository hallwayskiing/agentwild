"""Regions of the map: which part of the world a hunting squad may take creatures from (rules.SQUADS[..]["region"]), so
that squads working side by side never chase the same creature."""
import math

from . import landwatch, rules

CAMP = rules.MAP_CAMP


def sector(x, y, parts=4):
    """Which of `parts` equal slices of the circle round the camp (128,128) the point lies in, counted from east (0 deg)
    towards south (y grows downward)."""
    angle = (math.degrees(math.atan2(y - CAMP[1], x - CAMP[0])) + 360) % 360
    return int(angle // (360 / parts))


def _land(x, y):
    ax, ay, reach = landwatch.AREA
    return max(abs(x - ax), abs(y - ay)) <= reach


REGIONS = {
    "all": lambda x, y: True,
    "west": lambda x, y: x < rules.REGION_SPLIT_X,
    "east": lambda x, y: x >= rules.REGION_SPLIT_X,
    "eastN": lambda x, y: x >= rules.REGION_SPLIT_X and y < CAMP[1],
    "westN": lambda x, y: x < rules.REGION_SPLIT_X and y < CAMP[1],
    "westS": lambda x, y: x < rules.REGION_SPLIT_X and y >= CAMP[1],
    "eastS": lambda x, y: x >= rules.REGION_SPLIT_X and y >= CAMP[1],
    "land": _land,                                      # the corrupted land, wherever landwatch last put it
    **{"sec%d" % i: (lambda x, y, i=i: sector(x, y) == i) for i in range(4)},   # quarters of the circle round the camp
}
