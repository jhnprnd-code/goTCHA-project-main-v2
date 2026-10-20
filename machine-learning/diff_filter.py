"""Compare raw session records with the sessions retained for model training.

Run from any directory:
    python machine-learning/diff_filter.py

By default compares backend/sessions.jsonl with
backend/sessions_model_training.jsonl and writes results/diff_filter.txt.
"""
import argparse
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def record_key(row: dict) -> tuple:
    return row.get("pid"), row.get("level_idx"), row.get("ts")


def kind(row: dict) -> str:
    if row.get("source") not in ("collect", "synthetic") or row.get("label") not in ("human", "bot"):
        return "unlabeled traffic (evaluate.py ignores these anyway)"
    bot_kind = f" {row['bot_kind']}" if row.get("bot_kind") else ""
    session_type = "practice" if row.get("practice") else "TIMED"
    validity = "valid" if row.get("valid") else "INVALID"
    return f"{row['label']}{bot_kind} | {session_type} | {validity}"


def build_report(raw_path: Path, kept_path: Path) -> str:
    raw = read_rows(raw_path)
    kept = {record_key(row) for row in read_rows(kept_path)}
    dropped = [row for row in raw if record_key(row) not in kept]
    lines = [f"{len(dropped)} raw lines are not in the training file", ""]

    for description, count in Counter(kind(row) for row in dropped).most_common():
        lines.append(f"{count:4}  {description}")

    lines.extend(["", "Dropped TIMED human sessions per participant code (the ones that matter for FRR):"])
    timed_humans = [row for row in dropped if row.get("label") == "human" and not row.get("practice")]
    for pid, count in Counter(row["pid"] for row in timed_humans).most_common():
        participant_rows = [row for row in dropped if row.get("pid") == pid]
        flags = sorted({flag for row in participant_rows for flag in row.get("flags", [])})
        valid = sum(1 for row in timed_humans if row.get("pid") == pid and row.get("valid"))
        lines.append(f"  {pid}: {count} dropped ({valid} of them valid); flags: {flags or '-'}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
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
