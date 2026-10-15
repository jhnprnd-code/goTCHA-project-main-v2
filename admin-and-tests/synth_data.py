"""
SYNTHETIC session generator. Exists ONLY to test the training/evaluation pipeline before
real data arrives. Anything computed from this file is circular (we wrote the generator that
makes the "humans") and MUST NOT appear in the paper as a result.

    python synth_data.py --humans 15 --bots-per-kind 6 --out synthetic_sessions.jsonl

Records have source="synthetic" so train.py refuses them unless --allow-synthetic.
Synthetic humans have structure the 'adaptive' bot lacks (learning curve inside a level,
fatigue, lapses, heavier late tail), so the pipeline has something to find.
"""
import argparse
import json
import math
import random

from bot_lib import KINDS, build_events, fake_telemetry, gen_offsets
from gamecore import GAME, make_layout


def synth_human_offsets(n, rng, person):
    mu, sigma, phi, drift = person["mu"], person["sigma"], person["phi"], person["drift"]
    x, out = 0.0, []
    for i in range(n):
        s = sigma * (1 + 0.6 * math.exp(-i / 2.5) + 0.04 * max(0, i - 5))   # learning, then fatigue
        z = rng.gauss(0, 1)
        x = phi * x + s * (z if z < 0 else 1.5 * z) * math.sqrt(1 - phi ** 2)  # heavier late tail
        e = mu + drift * (i - n / 2) + x
        if rng.random() < person["lapse"]:
            e += rng.gauss(120, 40)                                            # hesitation
        if rng.random() < 0.03:
            out.append((None, 0.0))                                            # skipped jump
            continue
        d = max(30.0, rng.lognormvariate(math.log(person["dwell"]), 0.28) + 0.15 * (e - mu))
        out.append((e, d))
    return out


def new_person(rng, low_vision):
    p = {"mu": rng.gauss(-10, 25), "sigma": rng.lognormvariate(math.log(45), 0.35),
         "phi": rng.uniform(0.1, 0.5), "drift": rng.gauss(0, 3), "lapse": 0.06,
         "dwell": rng.uniform(70, 130)}
    if low_vision:
        p.update(mu=p["mu"] + 25, sigma=p["sigma"] * 1.4, lapse=0.10)
    return p


def record(rng, pid, label, kind, group, level_idx, offsets_fn):
    spawns, level_end = make_layout(rng)
    events = build_events(spawns, offsets_fn(len(spawns)))
    return {"schema": 2, "ts": 0, "source": "synthetic", "spawns_ms": spawns, "level_end_ms": level_end,
            "params": GAME, "elapsed_ms": round(level_end + 300), "events": events,
            "telemetry": fake_telemetry(rng), "pid": pid, "label": label, "bot_kind": kind, "group": group,
            "level_idx": level_idx, "practice": level_idx == 0, "valid": True, "flags": [],
            "features": {"cleared": 0, "n_obstacles": len(spawns)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--humans", type=int, default=15)
    ap.add_argument("--low-vision", type=int, default=4, help="how many of the humans are low vision")
    ap.add_argument("--levels", type=int, default=3)
    ap.add_argument("--bots-per-kind", type=int, default=6)
    ap.add_argument("--out", default="synthetic_sessions.jsonl")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    rows = []

    humans = []
    for h in range(a.humans):
        lv = h < a.low_vision
        pid = f"SH{h:03d}"
        person = new_person(rng, lv)
        humans.append(person)
        for lvl in range(a.levels + 1):
            rows.append(record(rng, pid, "human", "", "low_vision" if lv else "sighted", lvl,
                               lambda n: synth_human_offsets(n, rng, person)))

    # human pool for the data-driven attackers, built from the synthetic humans' levels
    from bot_lib import load_human_pool
    tmp = a.out + ".pool.tmp"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    pool = load_human_pool([tmp])
    import os
    os.remove(tmp)

    for kind in KINDS:
        for b in range(a.bots_per_kind):
            pid = f"SB{kind[:3]}{b:02d}"
            for lvl in range(a.levels + 1):
                rows.append(record(rng, pid, "bot", kind, "sighted", lvl,
                                   lambda n: gen_offsets(kind, n, rng, pool)))

    with open(a.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"wrote {len(rows)} synthetic records to {a.out} (NOT real data)")


if __name__ == "__main__":
    main()
