"""
Delete everything for one participant code (withdrawal requests).

    python admin-and-tests/delete_participant.py P1A2B3C [sessions.jsonl] [consent.jsonl]

Rewrites both files without that participant. Stop the server first (or restart it
afterwards), because the server keeps the registry in memory.
"""
import json
import sys
from pathlib import Path

if len(sys.argv) < 2:
    sys.exit(__doc__)
pid = sys.argv[1].strip().upper()
HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "backend"
sessions = Path(sys.argv[2]) if len(sys.argv) > 2 else DATA_DIR / "sessions.jsonl"
consent = Path(sys.argv[3]) if len(sys.argv) > 3 else DATA_DIR / "consent.jsonl"

total = 0
for path in (sessions, consent):
    if not path.exists():
        continue
    keep, removed = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("pid") == pid:
                removed += 1
                continue
        except ValueError:
            pass
        keep.append(line)
    path.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
    print(f"{path.name}: removed {removed} record(s)")
    total += removed

print("Done. Restart the server." if total else f"No records found for {pid}.")
