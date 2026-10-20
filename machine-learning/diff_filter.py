"""Compare raw session records with the sessions retained for model training, in both directions.

Run from any directory:
    python machine-learning/diff_filter.py

By default compares backend/sessions.jsonl with
backend/sessions_model_training.jsonl and writes results/diff_filter.txt.

Sessions are matched on (pid, level_idx, ts), so a re-serialized file still matches. The report gives
line and record counts for each file, what is in the raw file but not the training file (dropped),
what is in the training file but not the raw file (added), and a one-line verdict on how they relate.
"""
import argparse
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read_rows(path: Path) -> tuple[list[dict], int, int]:
    """(records, blank lines, unparseable lines)."""
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    rows, blank, bad = [], 0, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            blank += 1
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    return rows, blank, bad


def record_key(row: dict) -> tuple:
    return row.get("pid"), row.get("level_idx"), row.get("ts")


def kind(row: dict) -> str:
    if row.get("source") not in ("collect", "synthetic") or row.get("label") not in ("human", "bot"):
        return "unlabeled traffic (evaluate.py ignores these anyway)"
    bot_kind = f" {row['bot_kind']}" if row.get("bot_kind") else ""
    session_type = "practice" if row.get("practice") else "TIMED"
    validity = "valid" if row.get("valid") else "INVALID"
    return f"{row['label']}{bot_kind} | {session_type} | {validity}"


def usable(row: dict) -> bool:
    """Would evaluate.py use this record (real, labeled, valid, not practice)?"""
    return (row.get("source") == "collect" and row.get("label") in ("human", "bot")
            and bool(row.get("valid")) and not row.get("practice"))


def describe_file(name: str, path: Path, rows: list[dict], blank: int, bad: int) -> list[str]:
    keys = Counter(record_key(row) for row in rows)
    duplicates = sum(count - 1 for count in keys.values())
    total = len(rows) + blank + bad
    return [f"{name}: {path}",
            f"  {total} lines = {len(rows)} records + {blank} blank + {bad} unparseable",
            f"  {len(keys)} distinct (pid, level_idx, ts) keys ({duplicates} duplicate)"]


def describe_side(title: str, side: list[dict]) -> list[str]:
    lines = [title, f"  {len(side)} records, {sum(usable(row) for row in side)} of them usable by evaluate.py"]
    for description, count in Counter(kind(row) for row in side).most_common():
        lines.append(f"  {count:4}  {description}")
    timed_humans = [row for row in side if row.get("label") == "human" and not row.get("practice")]
    lines.append("  timed human sessions per participant code (the ones that matter for FRR):")
    if not timed_humans:
        lines.append("    none")
    for pid, count in Counter(row["pid"] for row in timed_humans).most_common():
        participant_rows = [row for row in side if row.get("pid") == pid]
        flags = sorted({flag for row in participant_rows for flag in row.get("flags", [])})
        valid = sum(1 for row in timed_humans if row.get("pid") == pid and row.get("valid"))
        lines.append(f"    {pid}: {count} ({valid} of them valid); flags: {flags or '-'}")
    return lines


def verdict(dropped: int, added: int) -> str:
    if not dropped and not added:
        return "Same sessions in both files: nothing was dropped and nothing was added."
    if dropped and not added:
        return "The training file is a filtered subset of the raw file: the dropped records above were removed."
    if added and not dropped:
        return ("The training file is a SUPERSET of the raw file. It has records the raw file lacks, so it is "
                "not a filtered subset. Find where the added records came from.")
    return "The files differ in both directions: some raw records were dropped and others were added."


def build_report(raw_path: Path, kept_path: Path) -> str:
    raw, raw_blank, raw_bad = read_rows(raw_path)
    kept, kept_blank, kept_bad = read_rows(kept_path)
    raw_keys, kept_keys = {record_key(row) for row in raw}, {record_key(row) for row in kept}
    dropped = [row for row in raw if record_key(row) not in kept_keys]
    added = [row for row in kept if record_key(row) not in raw_keys]

    lines = describe_file("raw", raw_path, raw, raw_blank, raw_bad)
    lines += describe_file("training", kept_path, kept, kept_blank, kept_bad)
    lines += ["", f"In both files: {len(raw_keys & kept_keys)} distinct keys", ""]
    lines += describe_side(f"DROPPED (in raw, not in training file): {len(dropped)}", dropped)
    lines.append("")
    lines += describe_side(f"ADDED (in training file, not in raw): {len(added)}", added)
    lines += ["", f"Verdict: {verdict(len(dropped), len(added))}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", type=Path, default=ROOT / "backend" / "sessions.jsonl")
    parser.add_argument("--kept", type=Path, default=ROOT / "backend" / "sessions_model_training.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "diff_filter.txt")
    args = parser.parse_args()

    report = build_report(args.raw, args.kept)
    print(report, end="")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
