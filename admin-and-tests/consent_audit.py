"""
Which version of the consent form did each participant accept? Run it on the machine that holds the data:

    python admin-and-tests/consent_audit.py [sessions.jsonl] [consent.jsonl] [--current 2026-10-v5]

Joins consent.jsonl (the version and time each participant agreed) with sessions.jsonl (how many valid timed
levels they played), then lists everyone, summarizes by consent version, and flags participants who completed
the study under a version other than the current one. Prints to the terminal only. The per-participant table
shows participant codes, so do not commit its output; the by-version summary has no codes in it.

The consent texts themselves are compared in results/CONSENT_VERSIONS.md.
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "backend"
MIN_LEVELS = 3                                  # valid timed levels that make a participant "complete"
PHT = timezone(timedelta(hours=8))              # the study ran in the Philippines; the commit log is also +08:00


def read_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def current_version(api_path: Path = DATA_DIR / "backend_api.py") -> Optional[str]:
    """CONSENT_VERSION as written in backend_api.py, read from the text so no server code is imported."""
    if not api_path.exists():
        return None
    m = re.search(r'^CONSENT_VERSION\s*=\s*["\']([^"\']+)["\']', api_path.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def when(ts: Optional[float]) -> str:
    if not isinstance(ts, (int, float)):
        return "-"
    return datetime.fromtimestamp(ts, PHT).strftime("%Y-%m-%d %H:%M")


def summarize(consent: List[dict], sessions: List[dict], current: Optional[str]) -> Dict:
    """People (human registrations, oldest first), a by-version table, and the two things worth flagging."""
    registered = {r["pid"]: r for r in consent if r.get("pid") and r.get("label") == "human"}
    human_rows = [r for r in sessions if r.get("source") == "collect" and r.get("label") == "human"]
    timed = Counter(r["pid"] for r in human_rows if r.get("valid") and not r.get("practice"))
    people = []
    for pid, r in sorted(registered.items(), key=lambda kv: kv[1].get("ts") or 0):
        n = timed.get(pid, 0)
        people.append({"pid": pid, "group": r.get("group", ""), "version": r.get("consent_version") or "(none)",
                       "ts": r.get("ts"), "valid_timed": n, "completed": n >= MIN_LEVELS})

    by_version = defaultdict(lambda: {"registered": 0, "completed": 0, "ts": []})
    for p in people:
        row = by_version[p["version"]]
        row["registered"] += 1
        row["completed"] += p["completed"]
        if isinstance(p["ts"], (int, float)):
            row["ts"].append(p["ts"])
    completers = [p for p in people if p["completed"]]
    return {
        "people": people,
        "by_version": {v: {"registered": r["registered"], "completed": r["completed"],
                           "first": min(r["ts"], default=None), "last": max(r["ts"], default=None)}
                       for v, r in sorted(by_version.items())},
        "current": current,
        "completers_other_version": [p for p in completers if current and p["version"] != current],
        "completers": len(completers),
        "no_consent_record": sorted({r["pid"] for r in human_rows} - set(registered)),
    }


def render(s: Dict) -> str:
    people, cur = s["people"], s["current"]
    out = [f"{len(people)} registered humans, {s['completers']} completed (>= {MIN_LEVELS} valid timed levels). "
           f"Current consent version in backend_api.py: {cur or 'not found'}.", "",
           f"{'pid':8} {'group':18} {'consent version':16} {'consented (UTC+8)':17} {'valid timed':>11}  status"]
    for p in people:
        status = "complete" if p["completed"] else ("never started" if not p["valid_timed"] else "incomplete")
        out.append(f"{p['pid']:8} {p['group']:18} {p['version']:16} {when(p['ts']):17} {p['valid_timed']:>11}  {status}")

    out += ["", "By consent version (no participant codes below this line):",
            f"  {'version':16} {'registered':>10} {'completed':>9}  first consent      last consent"]
    for v, r in s["by_version"].items():
        out.append(f"  {v:16} {r['registered']:>10} {r['completed']:>9}  {when(r['first']):17}  {when(r['last'])}")

    others = s["completers_other_version"]
    out.append("")
    if not s["completers"]:
        out.append("Nobody has completed the study yet.")
    elif not cur:
        out.append("Could not read CONSENT_VERSION from backend_api.py; pass --current to compare.")
    elif others:
        counts = dict(Counter(p["version"] for p in others))
        out.append(f"{len(others)} of {s['completers']} completers agreed to a version other than {cur}: {counts}. "
                   f"Compare the texts in results/CONSENT_VERSIONS.md.")
    else:
        out.append(f"All {s['completers']} completers agreed to {cur}.")
    if s["no_consent_record"]:
        out.append(f"{len(s['no_consent_record'])} participant code(s) have sessions but no consent record "
                   f"(an incomplete deletion, or a consent.jsonl that is missing lines).")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="?", type=Path, default=DATA_DIR / "sessions.jsonl")
    ap.add_argument("consent", nargs="?", type=Path, default=DATA_DIR / "consent.jsonl")
    ap.add_argument("--current", help="consent version to compare against (default: read from backend_api.py)")
    args = ap.parse_args()
    summary = summarize(read_jsonl(args.consent), read_jsonl(args.sessions), args.current or current_version())
    print(render(summary), end="")


if __name__ == "__main__":
    main()
