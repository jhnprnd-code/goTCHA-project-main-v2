"""
Checks the data-provenance tools: fingerprint(), freeze_data.py, consent_audit.py and the diff_filter.py checksum.
Everything runs on temporary files; nothing under backend/ is read or written.

    python admin-and-tests/test_provenance_tools.py
"""
import hashlib
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "machine-learning"))

import consent_audit  # noqa: E402
import diff_filter  # noqa: E402
import freeze_data  # noqa: E402
from provenance import fingerprint, short  # noqa: E402


class Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        for p in self.dir.rglob("*"):
            if p.is_file():
                p.chmod(p.stat().st_mode | stat.S_IWUSR)        # frozen copies are read-only
        self._tmp.cleanup()

    def write(self, name, text, folder=None):
        p = (folder or self.dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        return p


class Fingerprint(Scratch):
    def test_hash_lines_bytes(self):
        p = self.write("a.jsonl", '{"a":1}\n{"b":2}\n')
        fp = fingerprint(p)
        self.assertEqual(fp, {"file": "a.jsonl", "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                              "lines": 2, "bytes": 16})

    def test_last_line_without_newline_counts(self):
        self.assertEqual(fingerprint(self.write("a", "x\ny"))["lines"], 2)
        self.assertEqual(fingerprint(self.write("b", "x\ny\n"))["lines"], 2)
        self.assertEqual(fingerprint(self.write("c", ""))["lines"], 0)

    def test_name_only_no_folder(self):
        fp = fingerprint(self.write("a.jsonl", "x\n", self.dir / "private" / "deep"))
        self.assertNotIn("private", json.dumps(fp))
        self.assertEqual(short(fp), f"a.jsonl (sha256 {fp['sha256'][:16]}..., 1 lines)")


class Freeze(Scratch):
    def test_copy_is_identical_read_only_and_in_manifest(self):
        src = self.write("sessions.jsonl", "a\nb\n", self.dir / "backend")
        dest = self.dir / "frozen"
        lines = freeze_data.freeze([src], dest)
        self.assertEqual((dest / "sessions.jsonl").read_bytes(), src.read_bytes())
        self.assertEqual((dest / "sessions.jsonl").stat().st_mode & 0o222, 0)
        manifest = json.loads((dest / "MANIFEST.json").read_text())
        self.assertEqual(manifest["files"][0]["sha256"], fingerprint(src)["sha256"])
        self.assertEqual(manifest["files"][0]["lines"], 2)
        self.assertNotIn(str(self.dir), json.dumps(manifest))
        self.assertTrue(any(l.startswith("frozen") for l in lines))
        self.assertFalse(list(dest.glob(".*.partial")))

    def test_refreezing_same_content_is_a_no_op(self):
        src = self.write("sessions.jsonl", "a\n", self.dir / "backend")
        dest = self.dir / "frozen"
        freeze_data.freeze([src], dest)
        first = json.loads((dest / "MANIFEST.json").read_text())
        lines = freeze_data.freeze([src], dest)
        self.assertTrue(any(l.startswith("unchanged") for l in lines))
        self.assertEqual(json.loads((dest / "MANIFEST.json").read_text()), first)

    def test_never_overwrites_a_different_frozen_file(self):
        src = self.write("sessions.jsonl", "a\n", self.dir / "backend")
        other = self.write("consent.jsonl", "c\n", self.dir / "backend")
        dest = self.dir / "frozen"
        freeze_data.freeze([src], dest)
        src.write_bytes(b"a\nCHANGED\n")
        with self.assertRaises(freeze_data.FreezeError):
            freeze_data.freeze([other, src], dest)
        self.assertEqual((dest / "sessions.jsonl").read_bytes(), b"a\n")
        self.assertFalse((dest / "consent.jsonl").exists())           # refused before copying anything

    def test_missing_source_and_name_clash(self):
        with self.assertRaises(freeze_data.FreezeError):
            freeze_data.freeze([self.dir / "nope.jsonl"], self.dir / "frozen")
        a = self.write("s.jsonl", "1\n", self.dir / "one")
        b = self.write("s.jsonl", "2\n", self.dir / "two")
        with self.assertRaises(freeze_data.FreezeError):
            freeze_data.freeze([a, b], self.dir / "frozen")

    def test_manifest_copy_and_later_files_are_added(self):
        a = self.write("a.jsonl", "1\n", self.dir / "backend")
        b = self.write("b.jsonl", "2\n", self.dir / "backend")
        dest, extra = self.dir / "frozen", self.dir / "results" / "data_manifest.json"
        freeze_data.freeze([a], dest, extra)
        freeze_data.freeze([b], dest, extra)                          # manifests are read-only after each run
        for path in (dest / "MANIFEST.json", extra):
            self.assertEqual([e["file"] for e in json.loads(path.read_text())["files"]], ["a.jsonl", "b.jsonl"])

    def test_verify_ok_mismatch_missing(self):
        a = self.write("a.jsonl", "1\n", self.dir / "backend")
        b = self.write("b.jsonl", "2\n", self.dir / "backend")
        dest = self.dir / "frozen"
        freeze_data.freeze([a, b], dest)
        manifest = dest / "MANIFEST.json"
        self.assertTrue(all(l.startswith("ok") for l in freeze_data.verify(manifest, dest)))
        a.write_bytes(b"1\nextra\n")
        b.unlink()
        got = freeze_data.verify(manifest, a.parent)
        self.assertTrue(got[0].startswith("MISMATCH") and "a.jsonl" in got[0])
        self.assertTrue(got[1].startswith("MISSING") and "b.jsonl" in got[1])

    def test_unreadable_manifest_is_not_overwritten(self):
        a = self.write("a.jsonl", "1\n", self.dir / "backend")
        dest = self.dir / "frozen"
        dest.mkdir()
        (dest / "MANIFEST.json").write_text("not json")
        with self.assertRaises(freeze_data.FreezeError):
            freeze_data.freeze([a], dest)
        self.assertEqual((dest / "MANIFEST.json").read_text(), "not json")


def consent_row(pid, version, ts, label="human", group="low_vision"):
    return {"pid": pid, "ts": ts, "consent_version": version, "label": label, "group": group}


def level(pid, valid=True, practice=False, label="human", source="collect"):
    return {"pid": pid, "source": source, "label": label, "valid": valid, "practice": practice}


class ConsentAudit(unittest.TestCase):
    CONSENT = [consent_row("P1", "2026-10-v4", 1_760_000_000), consent_row("P2", "2026-10-v5", 1_760_100_000),
               consent_row("P3", "2026-10-v5", 1_760_200_000), consent_row("P4", "2026-10-v5", 1_760_300_000),
               consent_row("B1", "2026-10-v5", 1_760_400_000, label="bot")]
    SESSIONS = ([level("P1", practice=True)] + [level("P1")] * 3
                + [level("P2")] * 3
                + [level("P3")] * 2 + [level("P3", valid=False)]          # two valid timed levels: incomplete
                + [level("B1", label="bot")] * 3                         # bot runs never count
                + [level("P9")] * 3                                      # sessions with no consent record
                + [level("P2", source="verify")])                        # live /verify traffic is ignored

    def setUp(self):
        self.s = consent_audit.summarize(self.CONSENT, self.SESSIONS, "2026-10-v5")

    def test_completion_by_version(self):
        self.assertEqual({v: (r["registered"], r["completed"]) for v, r in self.s["by_version"].items()},
                         {"2026-10-v4": (1, 1), "2026-10-v5": (3, 1)})
        self.assertEqual(self.s["completers"], 2)
        self.assertEqual([p["valid_timed"] for p in self.s["people"]], [3, 3, 2, 0])

    def test_flags_completers_on_an_older_version(self):
        self.assertEqual([p["pid"] for p in self.s["completers_other_version"]], ["P1"])
        self.assertIn("1 of 2 completers agreed to a version other than 2026-10-v5", consent_audit.render(self.s))

    def test_orphans_and_bots(self):
        self.assertEqual(self.s["no_consent_record"], ["P9"])
        self.assertNotIn("B1", [p["pid"] for p in self.s["people"]])

    def test_all_current_and_nobody_complete(self):
        ok = consent_audit.summarize(self.CONSENT[1:2], self.SESSIONS, "2026-10-v5")
        self.assertIn("All 1 completers agreed to 2026-10-v5.", consent_audit.render(ok))
        none = consent_audit.summarize(self.CONSENT[:1], [], "2026-10-v5")
        self.assertIn("Nobody has completed", consent_audit.render(none))

    def test_missing_version_field_is_shown_not_crashed(self):
        s = consent_audit.summarize([{"pid": "P5", "label": "human"}], [level("P5")] * 3, "2026-10-v5")
        self.assertEqual(s["people"][0]["version"], "(none)")
        self.assertEqual(len(s["completers_other_version"]), 1)

    def test_by_version_section_has_no_participant_codes(self):
        text = consent_audit.render(self.s)
        self.assertRegex(text.split("By consent version", 1)[0], r"\bP1\b")        # the table above lists codes
        self.assertNotRegex(text.split("By consent version", 1)[1], r"\bP\d\b")


class DiffFilterChecksum(Scratch):
    def test_report_shows_each_files_sha256(self):
        row = json.dumps({"pid": "P1", "level_idx": 0, "ts": 1.0, "source": "collect", "label": "human",
                          "valid": True, "practice": False})
        raw = self.write("raw.jsonl", row + "\n")
        kept = self.write("kept.jsonl", row + "\n")
        report = diff_filter.build_report(raw, kept)
        self.assertEqual(report.count(fingerprint(raw)["sha256"]), 2)       # identical files, shown for both
        self.assertIn(f"({fingerprint(raw)['bytes']} bytes)", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
