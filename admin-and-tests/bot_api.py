"""
API-level attackers: fabricate key events and submit them through the real collection
endpoints, labeled as bots (needs the server's GOTCHA_ADMIN_KEY). Real-time bound applies,
so each level costs ~20 s of waiting; runs execute in parallel.

    python bot_api.py --kind gauss --runs 6 --levels 4 --admin-key $GOTCHA_ADMIN_KEY
    python bot_api.py --kind replay --runs 6 --pool sessions.jsonl --admin-key ...

One run = one bot "participant" (its own code, like a person). The first level of each
run is the practice level and is excluded from training, so --levels counts timed ones.
FOR YOUR OWN SERVER ONLY.
"""
import argparse
import os
import random
import threading
import time

import requests

from bot_lib import KINDS, build_events, fake_telemetry, gen_offsets, load_human_pool

lock = threading.Lock()
summary = {"valid": 0, "flagged": 0, "errors": 0}


def post(url, **kw):
    for _ in range(8):
        r = requests.post(url, timeout=15, **kw)
        if r.status_code != 429:
            return r
        time.sleep(5)
    return r


def run_bot(idx, args, pool):
    rng = random.Random(args.seed * 1000 + idx)
    time.sleep(idx * 0.4)  # stagger registrations
    r = post(f"{args.base}/collect/start", json={
        "consent": True, "adult": True, "consent_version": args.consent_version, "group": "sighted",
        "keyboard": "other", "aids": ["none"], "label": "bot", "bot_kind": args.kind, "admin_key": args.admin_key})
    if r.status_code != 200:
        print(f"[run {idx}] register failed: {r.status_code} {r.text}")
        with lock:
            summary["errors"] += 1
        return
    pid = r.json()["pid"]
    for _ in range(args.levels + 1):
        t_req = time.time()
        ch = requests.get(f"{args.base}/collect/challenge", params={"pid": pid}, timeout=15).json()
        offsets = gen_offsets(args.kind, len(ch["spawns_ms"]), rng, pool)
        events = build_events(ch["spawns_ms"], offsets)
        time.sleep(max(0, t_req + ch["level_end_ms"] / 1000 + 0.3 - time.time()))
        r = post(f"{args.base}/collect/submit", json={"pid": pid, "nonce": ch["nonce"], "events": events,
                                                       "telemetry": fake_telemetry(rng)})
        with lock:
            if r.status_code == 200 and r.json().get("valid"):
                summary["valid"] += 1
            else:
                summary["flagged"] += 1
    print(f"[run {idx}] {pid} done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=KINDS, required=True)
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--levels", type=int, default=4)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--admin-key", default=os.environ.get("GOTCHA_ADMIN_KEY", ""))
    ap.add_argument("--pool", nargs="*", default=[], help="human sessions.jsonl for fitted/replay")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--consent-version", default="2026-10-v5")
    args = ap.parse_args()

    pool = load_human_pool(args.pool) if args.pool else None
    threads = [threading.Thread(target=run_bot, args=(i, args, pool)) for i in range(args.runs)]
    print(f"{args.runs} {args.kind} bots x {args.levels + 1} levels (about 25 s each, in parallel)...")
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"done: {summary}")
