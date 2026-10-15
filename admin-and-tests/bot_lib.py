"""
Attacker library: timing generators shared by the API bots, the Playwright bot and the
synthetic-data generator. For testing YOUR OWN server only.

The attacker ladder, weakest to strongest:
  perfect   exact ideal timing, constant key hold
  gauss     i.i.d. Gaussian timing noise (the attack that beat the rule-based engine)
  fitted    i.i.d. draws from the pooled human error/dwell distributions (matches marginals,
            has no temporal structure)
  adaptive  knows our features: AR(1) correlation, linear drift, asymmetric noise, dwell
            coupled to error. Built from textbook assumptions, not from data.
  replay    replays the per-obstacle error/dwell sequence of a recorded human level
            (assumes the attacker obtained recorded human sessions)

fitted and replay need a pool of real human sessions (--pool sessions.jsonl).
"""
import json
import math
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gamecore import obstacle_window, pair_strokes, replay

KINDS = ["perfect", "gauss", "fitted", "adaptive", "replay"]
Offsets = List[Tuple[Optional[float], float]]   # per obstacle: (timing error ms | None = skip, dwell ms)


def load_human_pool(paths: List[str], min_attempts: int = 5) -> Dict[str, list]:
    """Per-level (err, dwell) sequences and pooled marginals from recorded human levels."""
    levels, errs, dwells = [], [], []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("label") != "human" or not rec.get("valid") or rec.get("practice"):
                continue
            strokes = pair_strokes([(e["k"], e["t"]) for e in rec["events"]])
            _, per = replay(rec["spawns_ms"], strokes)
            seq = [(o["err"], o["dwell"]) for o in per]
            if sum(1 for e, _ in seq if e is not None) < min_attempts:
                continue
            levels.append(seq)
            errs += [e for e, _ in seq if e is not None]
            dwells += [d for e, d in seq if e is not None and d is not None]
    if not levels:
        raise SystemExit("No usable human levels in the pool. Collect human data first.")
    return {"levels": levels, "errs": errs, "dwells": dwells}


def gen_offsets(kind: str, n: int, rng: random.Random, pool: Optional[dict] = None) -> Offsets:
    if kind == "perfect":
        return [(0.0, 90.0)] * n
    if kind == "gauss":
        return [(rng.gauss(0, 50), max(25.0, rng.gauss(90, 20))) for _ in range(n)]
    if kind == "adaptive":
        phi = rng.uniform(0.2, 0.6)
        slope = rng.uniform(-4, 4)
        sigma = rng.uniform(30, 60)
        mu = rng.uniform(-15, 15)
        x, out = 0.0, []
        for i in range(n):
            z = rng.gauss(0, 1)
            noise = sigma * (z if z < 0 else 1.4 * z) * math.sqrt(1 - phi ** 2)
            x = phi * x + noise
            out.append((mu + slope * (i - n / 2) + x, max(30.0, 95 + 0.2 * x + rng.gauss(0, 18))))
        return out
    if pool is None:
        raise ValueError(f"kind '{kind}' needs a human pool (--pool sessions.jsonl)")
    if kind == "fitted":
        return [(rng.choice(pool["errs"]) + rng.gauss(0, 4),
                 max(25.0, rng.choice(pool["dwells"]) + rng.gauss(0, 4))) for _ in range(n)]
    if kind == "replay":
        seq = rng.choice(pool["levels"])
        out = []
        for i in range(n):
            e, d = seq[i] if i < len(seq) else (None, None)
            if e is None:
                e = rng.choice(pool["errs"])
            if d is None:
                d = rng.choice(pool["dwells"])
            out.append((e + rng.gauss(0, 3), max(25.0, d + rng.gauss(0, 3))))
        return out
    raise ValueError(f"unknown kind {kind}")


def build_events(spawns: List[float], offsets: Offsets) -> List[dict]:
    """Key events for the given per-obstacle offsets, as the API expects them."""
    ev = []
    for spawn, (err, dwell) in zip(spawns, offsets):
        if err is None:
            continue
        down = obstacle_window(spawn)[2] + err
        dwell = min(dwell, 400.0)
        ev += [{"k": "d", "t": round(down, 1)}, {"k": "u", "t": round(down + dwell, 1)}]
    ev.sort(key=lambda e: e["t"])
    return ev


def fake_telemetry(rng: random.Random) -> dict:
    """What an attacker who read the API would send: a healthy-looking environment."""
    return {"n_frames": rng.randint(1100, 1200), "dt_median_ms": 16.7, "dt_p95_ms": round(rng.uniform(17, 22), 1),
            "dt_max_ms": round(rng.uniform(25, 60), 1), "n_over_50": rng.randint(0, 3), "n_over_100": 0,
            "timer_res_ms": 0.1, "dpr": 1.0, "inner_w": 1280, "outer_w": 1280, "canvas_css_w": 800.0,
            "blur_count": 0, "untrusted_count": 0, "repeat_count": 0, "ua": "bot-simulator"}
