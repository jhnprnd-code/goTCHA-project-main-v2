"""
Does browser automation leave timing artifacts?
Compares what the Playwright bot asked the browser to do (intents file written by
bot_playwright.py --intent-out) with what the widget actually RECORDED in sessions.jsonl.
The difference is automation latency plus jitter: a signature that a human, who has no
"intended" schedule, does not have, and that API-level bots cannot show at all.

python analyze_artifacts.py --intents intents.jsonl --sessions sessions.jsonl [--out results]

Reports, per bot kind: mean and spread of (recorded - intended) for key-down and key-up
times, the dwell error, and how much jitter automation adds on top of the intended timing.
Caveats: headless Chromium on one machine; run it on your own machine (and headed, if you
can) before claiming anything about "browser automation" in general.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--intents", required=True)
ap.add_argument("--sessions", required=True)
ap.add_argument("--out", default="")
a = ap.parse_args()


def read(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


intents = defaultdict(dict)
for r in read(a.intents):
    intents[r["pid"]][r["order"]] = r["events"]

recs = defaultdict(list)
for r in read(a.sessions):
    if r.get("source") == "collect" and r.get("label") == "bot" and str(r.get("bot_kind", "")).startswith("pw_"):
        recs[r["pid"]].append(r)

by_kind = defaultdict(lambda: {"down": [], "up": [], "dwell": [], "levels": 0, "skipped": 0, "stall": 0})
for pid, rs in recs.items():
    rs.sort(key=lambda r: r["ts"])
    for order, rec in enumerate(rs):
        kind = rec["bot_kind"]
        k = by_kind[kind]
        want = intents.get(pid, {}).get(order)
        got = rec["events"]
        k["levels"] += 1
        if rec.get("flags") and any(f.startswith("frame:") for f in rec["flags"]):
            k["stall"] += 1
        if want is None or len(want) != len(got) or any(w["k"] != g["k"] for w, g in zip(want, got)):
            k["skipped"] += 1        # example: synthetic KeyboardEvents were ignored by the widget
            continue
        for w, g in zip(want, got):
            (k["down"] if w["k"] == "d" else k["up"]).append(g["t"] - w["t"])
        dw = [(w2["t"] - w1["t"], g2["t"] - g1["t"]) for (w1, w2), (g1, g2)
              in zip(zip(want[0::2], want[1::2]), zip(got[0::2], got[1::2]))]
        k["dwell"] += [g - w for w, g in dw]

lines = ["| bot kind | levels | usable | stalled | down: mean / sd / max |Δ| (ms) | up: mean / sd (ms) | dwell error: mean / sd (ms) |",
         "|---|---|---|---|---|---|---|"[:0] + "---|---|---|---|---|---|---|---|"[:0]]
lines = ["| bot kind | levels | usable | stalled | down: mean / sd / max abs (ms) | up: mean / sd (ms) | dwell error: mean / sd (ms) |",
         "|---|---|---|---|---|---|---|"]
for kind, k in sorted(by_kind.items()):
    d, u, w = map(np.array, (k["down"], k["up"], k["dwell"]))
    if not len(d):
        lines.append(f"| {kind} | {k['levels']} | 0 | {k['stall']} | no usable events (ignored by widget) | | |")
        continue
    lines.append(f"| {kind} | {k['levels']} | {k['levels'] - k['skipped']} | {k['stall']} | "
                 f"{d.mean():+.2f} / {d.std(ddof=1):.2f} / {np.abs(d).max():.1f} | "
                 f"{u.mean():+.2f} / {u.std(ddof=1):.2f} | {w.mean():+.2f} / {w.std(ddof=1):.2f} |")
report = "\n".join(lines)
print(report)
if a.out:
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "automation_artifacts.md").write_text(
        "# Automation timing artifacts (recorded minus intended)\n\n" + report +
        "\n\nA positive mean is automation latency. The spread is jitter the bot adds on top of its own timing model. "
        "The standard deviation (sd) indicates the variability of the timing errors.\n")