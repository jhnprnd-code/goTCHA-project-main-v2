"""
Freeze the raw study files before anything is merged, filtered or split, and check them later.

    python admin-and-tests/freeze_data.py --dest D:\\goTCHA_frozen_20261021 --manifest results/data_manifest.json
    python admin-and-tests/freeze_data.py --dest <folder> backend/sessions.jsonl backend/consent.jsonl
    python admin-and-tests/freeze_data.py --verify results/data_manifest.json --dir D:\\goTCHA_frozen_20261021
    python admin-and-tests/freeze_data.py --verify results/data_manifest.json --dir backend --card backend/model.joblib.card.json

Freeze mode copies each file into --dest, checks the copy byte for byte against the source, marks the copy
read-only and writes MANIFEST.json (file name, sha256, line count, size, time) next to the copies. It never
overwrites a different file already in --dest. With no file arguments it takes whichever of the usual study
files exist in backend/. Stop the server first: a file that changes while it is being copied is refused.

Verify mode re-hashes the files in --dir (default: the manifest's own folder) and exits 1 on any mismatch or
missing file. Point --dir at backend/ to see whether the working files still match what was frozen.

With --card MODEL.card.json, verify mode also checks which data a model was trained on: the card's data_files
fingerprints (written by train.py) must match the manifest. A card with no data_files, written by a copy of
train.py from before checksums, cannot be tied to any data and fails the check.

The manifest holds names and checksums only, no participant data, so it is safe to commit.
"""
import argparse
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "machine-learning"))
from provenance import fingerprint  # noqa: E402

USUAL_FILES = ["sessions.jsonl", "consent.jsonl", "sessions_model_training.jsonl", "sessions_bots.jsonl",
               "sessions_backup.jsonl"]
MANIFEST_NOTE = "Names and checksums only; no participant data."
READ_ONLY = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH


class FreezeError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_manifest(path: Path) -> Dict[str, Dict]:
    """{file name: entry} from an existing manifest, or {} if there is none."""
    if not path.exists():
        return {}
    try:
        return {e["file"]: e for e in json.loads(path.read_text(encoding="utf-8"))["files"]}
    except (ValueError, KeyError, TypeError) as exc:
        raise FreezeError(f"{path} exists but is not a manifest this tool wrote ({exc}); not touching it") from exc


def _write_manifest(path: Path, entries: Dict[str, Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(path.stat().st_mode | stat.S_IWUSR)           # an earlier freeze left it read-only
    body = {"note": MANIFEST_NOTE, "files": [entries[k] for k in sorted(entries)]}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    path.chmod(READ_ONLY)


def freeze(files: List[Path], dest: Path, manifest_copy: Optional[Path] = None) -> List[str]:
    """Copy `files` into `dest` and return the lines to print. Raises FreezeError before copying anything
    if a name clashes or a file is missing; nothing in `dest` is ever overwritten."""
    names = [f.name for f in files]
    if len(set(names)) != len(names):
        raise FreezeError("two source files share a name; the copies would collide in the destination")
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise FreezeError("not found: " + ", ".join(missing))

    source_fp = {f.name: fingerprint(f) for f in files}
    clashes = [n for n in names if (dest / n).exists() and fingerprint(dest / n)["sha256"] != source_fp[n]["sha256"]]
    if clashes:
        raise FreezeError(f"{', '.join(clashes)} already in {dest} with different content. The frozen copy is "
                          f"left alone; use a new --dest if this is a later snapshot.")

    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / "MANIFEST.json"
    entries = _read_manifest(manifest_path)
    out = []
    for f in files:
        target, fp = dest / f.name, source_fp[f.name]
        if target.exists():
            out.append(f"unchanged  {f.name}  (already frozen, sha256 {fp['sha256'][:16]}...)")
        else:
            tmp = dest / f".{f.name}.partial"
            shutil.copyfile(f, tmp)
            copy_fp, after_fp = fingerprint(tmp), fingerprint(f)
            if copy_fp["sha256"] != fp["sha256"] or after_fp["sha256"] != fp["sha256"]:
                tmp.unlink()
                raise FreezeError(f"{f.name} changed while it was being copied. Stop the server and try again.")
            os.replace(tmp, target)
            target.chmod(READ_ONLY)
            out.append(f"frozen     {f.name}  sha256 {fp['sha256']}  {fp['lines']} lines")
        old = entries.get(f.name)
        if old is None or old["sha256"] != fp["sha256"]:
            entries[f.name] = {**fp, "frozen_at": _now()}
    _write_manifest(manifest_path, entries)
    out.append(f"manifest   {manifest_path}")
    if manifest_copy:
        _write_manifest(manifest_copy, entries)
        out.append(f"manifest   {manifest_copy}")
    return out


def verify(manifest_path: Path, directory: Path) -> List[str]:
    """One line per manifest entry: 'ok ...', 'MISMATCH ...' or 'MISSING ...'."""
    entries = _read_manifest(manifest_path)
    if not entries:
        raise FreezeError(f"{manifest_path} lists no files")
    lines = []
    for name in sorted(entries):
        want, path = entries[name], directory / name
        if not path.is_file():
            lines.append(f"MISSING   {name}")
            continue
        got = fingerprint(path)
        if got["sha256"] == want["sha256"]:
            lines.append(f"ok        {name}  ({got['lines']} lines)")
        else:
            lines.append(f"MISMATCH  {name}  frozen {want['lines']} lines sha256 {want['sha256'][:16]}..., "
                         f"now {got['lines']} lines sha256 {got['sha256'][:16]}...")
    return lines


def verify_card(manifest_path: Path, card_path: Path) -> List[str]:
    """Is the model on this card trained on the data the manifest describes? One line per data file the card
    names: 'ok ...', 'MISMATCH ...' or 'UNKNOWN ...'; a card that names none gives 'NO FINGERPRINT ...'."""
    entries = _read_manifest(manifest_path)
    if not entries:
        raise FreezeError(f"{manifest_path} lists no files")
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FreezeError(f"cannot read {card_path} as a model card ({exc})") from exc
    if not isinstance(card, dict):
        raise FreezeError(f"{card_path} is not a model card (expected a JSON object)")
    trained = card.get("trained_at", "unknown time")
    used = card.get("data_files")
    if not used:
        return [f"NO FINGERPRINT  {card_path.name} (trained {trained}) has no data_files, so it cannot be tied to "
                f"any data. Retrain with a current train.py (git pull --ff-only first)."]
    lines = []
    for fp in used:
        name, want = fp.get("file", "?"), entries.get(fp.get("file", "?"))
        if want is None:
            lines.append(f"UNKNOWN   model was trained on {name}, which is not in the manifest")
        elif want["sha256"] == fp.get("sha256"):
            lines.append(f"ok        model trained {trained} on {name} (sha256 {want['sha256'][:16]}..., "
                         f"{want['lines']} lines), as frozen")
        else:
            lines.append(f"MISMATCH  model was trained on {name} sha256 {str(fp.get('sha256'))[:16]}..., "
                         f"but the manifest has {want['sha256'][:16]}...: not the frozen version of that file")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="files to freeze (default: the usual study files in backend/)")
    ap.add_argument("--dest", type=Path, help="folder to hold the frozen copies (outside the repo is best)")
    ap.add_argument("--manifest", type=Path, help="also write the manifest here, e.g. results/data_manifest.json")
    ap.add_argument("--verify", type=Path, metavar="MANIFEST", help="check files against a manifest instead of freezing")
    ap.add_argument("--dir", type=Path, help="with --verify: where the files are (default: the manifest's folder)")
    ap.add_argument("--card", type=Path, help="with --verify: also check this model card's data_files against the manifest")
    args = ap.parse_args()
    if args.card and not args.verify:
        ap.error("--card only works together with --verify")

    try:
        if args.verify:
            lines = verify(args.verify, args.dir or args.verify.parent)
            if args.card:
                lines += ["", f"Model card {args.card}:"] + verify_card(args.verify, args.card)
            print("\n".join(lines))
            bad = [ln for ln in lines if ln and not ln.startswith("ok") and not ln.startswith("Model card ")]
            print("\nEverything matches the manifest." if not bad else f"\n{len(bad)} problem(s) found.")
            return 1 if bad else 0
        if not args.dest:
            ap.error("--dest is required when freezing")
        files = args.files
        if not files:
            files = [ROOT / "backend" / n for n in USUAL_FILES if (ROOT / "backend" / n).is_file()]
            for n in USUAL_FILES:
                if not (ROOT / "backend" / n).is_file():
                    print(f"skipped    {n}  (not in backend/)")
            if not files:
                raise FreezeError("no study files found in backend/; name them on the command line")
        print("\n".join(freeze(files, args.dest, args.manifest)))
        return 0
    except FreezeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
