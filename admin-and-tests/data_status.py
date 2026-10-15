"""
Progress report on collected data. Run any time during collection:

    python admin-and-tests/data_status.py [sessions.jsonl] [consent.jsonl]

Shows who has finished, how many valid levels each participant has, and how often the
frame gate (stutter) fired, so you can chase missing participants and calibrate the gate.
"""
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "backend"
sessions = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_DIR / "sessions.jsonl"
consent = Path(sys.argv[2]) if len(sys.argv) > 2 else DATA_DIR / "consent.jsonl"


def read(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


reg = {r["pid"]: r for r in read(consent)}
recs = [r for r in read(sessions) if r.get("source") == "collect"]

by = defaultdict(list)
for r in recs:
    by[r["pid"]].append(r)

print(f"{len(reg)} registered, {len(by)} with at least one level\n")
print(f"{'pid':8} {'label':6} {'group':18} {'valid timed':>11} {'stutter':>8} {'cleared (timed)':>16}")
done = Counter()
for pid, p in sorted(reg.items()):
    rs = by.get(pid, [])
    timed = [r for r in rs if r.get("valid") and not r.get("practice")]
    stutter = sum(1 for r in rs if any(f.startswith("frame:") for f in r.get("flags", [])))
    cl = [r["features"]["cleared"] for r in timed]
    note = "" if len(timed) >= 3 else "  <- incomplete"
    print(f"{pid:8} {p['label']:6} {p['group']:18} {len(timed):>11} {stutter:>8} "
          f"{(str(round(statistics.mean(cl), 1)) + '/8') if cl else '-':>16}{note}")
    if len(timed) >= 3:
        done[(p["label"], p["group"])] += 1

print("\ncomplete participants (>=3 valid timed levels):")
for (label, group), n in sorted(done.items()):
    print(f"  {label:6} {group:18} {n}")
if not done:
    print("  none yet")

tele = [r["telemetry"] for r in recs if r.get("telemetry")]
if tele:
    gaps = sorted(t["dt_max_ms"] for t in tele)
    res = Counter(t["timer_res_ms"] for t in tele)
    print(f"\ntelemetry over {len(tele)} levels")
    print(f"  worst frame gap: median {statistics.median(gaps):.0f} ms, max {gaps[-1]:.0f} ms")
    print(f"  timer resolution seen (ms -> levels): {dict(res.most_common(5))}")
    print(f"  levels flagged by frame gate: {sum(1 for r in recs if any(f.startswith('frame:') for f in r.get('flags', [])))}")