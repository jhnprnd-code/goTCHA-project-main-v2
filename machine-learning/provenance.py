"""
Tie a reported number to one exact data file. Standard library only, so the admin tools and the
packaged server can use it without scikit-learn.

    fingerprint(path) -> {"file": name, "sha256": hex, "lines": n, "bytes": n}

Only the file's NAME is recorded (never its folder), and a fingerprint reveals nothing about the
contents, so manifests and result files that contain fingerprints are safe to commit.
"""
import hashlib
from pathlib import Path
from typing import Dict, Union


def fingerprint(path: Union[str, Path]) -> Dict[str, object]:
    p = Path(path)
    h, size, lines, last = hashlib.sha256(), 0, 0, b""
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
            lines += chunk.count(b"\n")
            last = chunk[-1:]
    if size and last != b"\n":          # a final line with no trailing newline still counts
        lines += 1
    return {"file": p.name, "sha256": h.hexdigest(), "lines": lines, "bytes": size}


def short(fp: Dict[str, object], n: int = 16) -> str:
    """'name (sha256 0123456789abcdef..., 117 lines)' for report text."""
    return f"{fp['file']} (sha256 {str(fp['sha256'])[:n]}..., {fp['lines']} lines)"
