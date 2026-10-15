"""
Game physics, level layout and stroke replay. Shared by the server, the feature
extractor, the bots, the synthetic-data generator and the tests, so they can never
disagree about what happened in a level.
"""
import math
from typing import Iterable, List, Tuple

GAME = {
    "world_w": 800, "world_h": 300, "ground_y": 250,
    "player_x": 100, "player_size": 48, "obstacle_size": 48,
    "speed_px_s": 300.0,
    "gravity_px_s2": 3000.0,
    "jump_v_px_s": 1000.0,
}
N_OBSTACLES = 8
LEAD_IN_MS = 2000
MIN_GAP_MS, MAX_GAP_MS = 1300, 2600

_V, _G = GAME["jump_v_px_s"], GAME["gravity_px_s2"]
AIRTIME_MS = 2 * _V / _G * 1000.0
_root = math.sqrt(_V ** 2 - 2 * _G * GAME["obstacle_size"])
# Time after jump start during which the player is higher than the obstacle.
CLEAR_FROM_MS = (_V - _root) / _G * 1000.0
CLEAR_UNTIL_MS = (_V + _root) / _G * 1000.0


def obstacle_window(spawn_ms: float) -> Tuple[float, float, float]:
    """(lo, hi, center): jump-start times (ms since level start) that clear this obstacle."""
    w, px, ps = GAME["world_w"], GAME["player_x"], GAME["player_size"]
    osz, speed = GAME["obstacle_size"], GAME["speed_px_s"]
    overlap_start = spawn_ms + (w - (px + ps)) / speed * 1000.0
    overlap_end = spawn_ms + (w - (px - osz)) / speed * 1000.0
    lo = overlap_end - CLEAR_UNTIL_MS
    hi = overlap_start - CLEAR_FROM_MS
    return lo, hi, (lo + hi) / 2.0


assert obstacle_window(0)[1] > obstacle_window(0)[0], "physics params make the level unwinnable"


def make_layout(rng) -> Tuple[List[int], float]:
    """Random obstacle spawn times (ms) and the level end time. rng needs .uniform()."""
    spawns, t = [], float(LEAD_IN_MS)
    for _ in range(N_OBSTACLES):
        spawns.append(round(t))
        t += rng.uniform(MIN_GAP_MS, MAX_GAP_MS)
    level_end = spawns[-1] + (GAME["world_w"] + GAME["obstacle_size"]) / GAME["speed_px_s"] * 1000.0
    return spawns, level_end


def pair_strokes(events: Iterable[Tuple[str, float]]) -> List[list]:
    """(kind, t) pairs -> [[down_ms, up_ms|None], ...]. Same pairing rule as the server."""
    strokes, pending = [], False
    for k, t in events:
        if k == "d" and not pending:
            strokes.append([t, None])
            pending = True
        elif k == "u" and pending:
            strokes[-1][1] = t
            pending = False
    return strokes


def replay(spawns: List[float], strokes: List[list], attribution_ms: float = 1000.0):
    """
    Replay key strokes against the level, exactly as the server physics sees it.

    Returns (accepted, per_obstacle)
      accepted:     [(down_ms, dwell_ms|None)] jumps that actually started (player not airborne)
      per_obstacle: one dict per obstacle, in order:
                    {"center", "cleared", "err" (jump_start - ideal, ms) | None, "dwell" | None}
    """
    accepted, last_start = [], -math.inf
    for down, up in strokes:
        if down >= last_start + AIRTIME_MS:
            accepted.append((down, (up - down) if up is not None else None))
            last_start = down

    used, per_obstacle = set(), []
    for spawn in spawns:
        lo, hi, center = obstacle_window(spawn)
        cleared = any(lo <= s <= hi for s, _ in accepted)
        cands = [(abs(s - center), i) for i, (s, _) in enumerate(accepted)
                 if i not in used and abs(s - center) <= attribution_ms]
        err = dwell = None
        if cands:
            _, i = min(cands)
            used.add(i)
            err = accepted[i][0] - center
            dwell = accepted[i][1]
        per_obstacle.append({"center": center, "cleared": cleared, "err": err, "dwell": dwell})
    return accepted, per_obstacle
