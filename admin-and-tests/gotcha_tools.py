"""
Unified goTCHA operational toolkit.
Usage:
  python gotcha_tools.py bots --admin-key test-admin --human-data sessions.jsonl
  python gotcha_tools.py merge --sessions data1.jsonl data2.jsonl --consent consent.jsonl --consent-out final_consent.jsonl --out final_sessions.jsonl
  python gotcha_tools.py calibrate --sessions final_sessions.jsonl
"""
import argparse

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
import statistics

from bot_lib import KINDS

def run_bots(args):
    """Runs all bot kinds automatically."""
    print(f"Starting bot batch run ({args.runs} runs per kind)...")
    
    for kind in KINDS:
        print(f"\n--- Running {kind} bots ---")
        cmd = [
            sys.executable, "bot_playwright.py",
            "--kind", kind,
            "--runs", str(args.runs),
            "--levels", "3",
            "--concurrent", "1",
            "--admin-key", args.admin_key
        ]
        
        if kind in ("fitted", "replay"):
            if not args.human_data:
                print(f"Skipping {kind}: requires --human-data")
                continue
            cmd.extend(["--human-data", args.human_data])
            
        res = subprocess.run(cmd)
        if res.returncode:
            print(f"!! {kind} bots FAILED (exit {res.returncode})")
            
    print("\nBot batch complete.")

def run_merge(args):
    """Merges files, drops withdrawn users completely, and prints dataset stats."""
    withdrawn_pids = {p.strip().upper() for p in (args.withdraw or [])}
    consent_recs = {}
    kept_consent = []
    
    # 1. Load, filter, and rewrite consent
    if args.consent and Path(args.consent).exists():
        for line in Path(args.consent).read_text(encoding="utf-8").splitlines():
            try:
                c = json.loads(line)
                if c.get("pid", "").strip().upper() not in withdrawn_pids:
                    consent_recs[c["pid"]] = c
                    kept_consent.append(c)
            except ValueError:
                pass
                
        if args.consent_out:
            Path(args.consent_out).write_text(
                "".join(json.dumps(c, separators=(",", ":")) + "\n" for c in kept_consent), 
                encoding="utf-8"
            )

    # 2. Merge and filter sessions
    merged = []
    dropped = 0
    for path in args.sessions:
        if not Path(path).exists():
            continue
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("pid", "").strip().upper() in withdrawn_pids:
                    dropped += 1
                    continue
                merged.append(rec)
            except ValueError:
                pass
                
    # 3. Write final session output
    out = Path(args.out)
    out.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in merged), encoding="utf-8")
    
    # 4. Stats (Requiring 3 valid timed levels to count as a complete human)
    valid_collect = [r for r in merged if r.get("source") == "collect" and r.get("valid")]
    
    timed_humans = Counter(r["pid"] for r in valid_collect if r.get("label") == "human" and not r.get("practice"))
    complete_humans = {p for p, n in timed_humans.items() if n >= 3}
    
    bots = [r.get("bot_kind", "unknown") for r in valid_collect if r.get("label") == "bot"]
    low_vision = sum(1 for pid in complete_humans if consent_recs.get(pid, {}).get("group") == "low_vision")
    
    print(f"Wrote {len(merged)} total records to {out} (dropped {dropped} withdrawn sessions).")
    if args.consent_out:
        print(f"Wrote {len(kept_consent)} consent records to {args.consent_out}")
    print(f"Complete Human Participants (>= 3 timed levels): {len(complete_humans)} (Low Vision: {low_vision})")
    print(f"Valid Bot Sessions: {dict(Counter(bots))}")

def run_calibrate(args):
    """Analyzes real human telemetry to report on frame-gate health."""
    recs = []
    for line in Path(args.sessions).read_text(encoding="utf-8").splitlines():
         try: recs.append(json.loads(line))
         except ValueError: pass
         
    # Filter strictly to humans to avoid fake bot telemetry
    rows = [r for r in recs if r.get("source") == "collect" 
            and r.get("label") == "human" 
            and r.get("telemetry")]
            
    if not rows:
        return print("No valid human telemetry found.")
        
    gaps = sorted(r["telemetry"]["dt_max_ms"] for r in rows)
    fps = sorted(r["telemetry"]["n_frames"] / max(1.0, r["level_end_ms"] / 1000.0) for r in rows)
    over_50_ratio = sorted(r["telemetry"]["n_over_50"] / max(1, r["telemetry"]["n_frames"]) for r in rows)
    
    print(f"Telemetry over {len(rows)} real human levels:")
    if len(rows) < 30:
        print("WARNING: Sample size is small (< 30). Percentiles may be noisy. Do not over-tune.")
        
    print(f"Max frame gap (dt_max_ms): Median {statistics.median(gaps):.0f}ms, 95th {gaps[int(len(gaps)*0.95)]:.0f}ms")
    print(f"Avg FPS: Median {statistics.median(fps):.1f}, 5th percentile {fps[int(len(fps)*0.05)]:.1f}")
    print(f"Ratio of frames > 50ms: Median {statistics.median(over_50_ratio):.3f}, 95th {over_50_ratio[int(len(over_50_ratio)*0.95)]:.3f}")
    
    print("\nCurrent backend_api.py limits (for reference):")
    print("  MAX_FRAME_GAP_MS = 250.0")
    print("  MIN_AVG_FPS = 20.0")
    print("  MAX_SLOW_FRAME_RATIO = 0.10")
    print("\nOnly adjust these limits if ordinary participants are repeatedly being flagged for retries.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="goTCHA Operations")
    sub = parser.add_subparsers(dest="cmd", required=True)
    
    p_bots = sub.add_parser("bots")
    p_bots.add_argument("--admin-key", required=True)
    p_bots.add_argument("--human-data", default="")
    p_bots.add_argument("--runs", type=int, default=4)
    
    p_merge = sub.add_parser("merge")
    p_merge.add_argument("--sessions", nargs="+", required=True)
    p_merge.add_argument("--consent", default="")
    p_merge.add_argument("--consent-out", default="")
    p_merge.add_argument("--out", default="final_sessions.jsonl")
    p_merge.add_argument("--withdraw", nargs="*", help="PIDs to delete")
    
    p_cal = sub.add_parser("calibrate")
    p_cal.add_argument("--sessions", required=True)
    
    args = parser.parse_args()
    if args.cmd == "bots": run_bots(args)
    elif args.cmd == "merge": run_merge(args)
    elif args.cmd == "calibrate": run_calibrate(args)