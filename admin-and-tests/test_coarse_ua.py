import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
_tmp = tempfile.mkdtemp()                       # never touch the real study files
os.environ["GOTCHA_DATA"], os.environ["GOTCHA_CONSENT"] = f"{_tmp}/s.jsonl", f"{_tmp}/c.jsonl"

import backend_api as api  # noqa: E402

FB_ANDROID = ("Mozilla/5.0 (Linux; Android 14; SM-A546E Build/UP1A.231005.007; wv) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Version/4.0 Chrome/126.0.6478.122 Mobile Safari/537.36 [FB_IAB/FB4A;FBAV/470.0.0.0;]")
CASES = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", "Chrome 127"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.2651.74", "Edge 127"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36 OPR/96.0.0.0", "Opera 96"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0", "Firefox 128"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15", "Safari 17"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1", "Safari 17, mobile"),
    ("Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36", "Chrome 126, mobile"),
    (FB_ANDROID, "Chrome 126, mobile, in-app"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/127.0.6533.5 Safari/537.36", "Chrome 127"),
    ("bot-simulator", "Other"),
    ("test", "Other"),
    ("", ""),
]
LEAKS = ("SM-A546E", "Build/", "Android 14", "Windows NT", "Macintosh", "iPhone OS", "x86_64", "FBAV", "UP1A")


class CoarseUA(unittest.TestCase):
    def test_known_agents(self):
        for raw, want in CASES:
            with self.subTest(raw=raw[:50]):
                self.assertEqual(api.coarse_ua(raw), want)

    def test_nothing_identifying_survives(self):
        for raw, _ in CASES:
            out = api.coarse_ua(raw)
            for leak in LEAKS:
                self.assertNotIn(leak, out)

    def test_stored_record_is_coarse(self):
        ch = api.Challenge([2000], 5000.0, 0.0)
        rec = api._session_record("verify", ch, [], api.Telemetry(ua=FB_ANDROID, dpr=2.0, inner_w=384), 5000.0)
        self.assertEqual(rec["telemetry"]["ua"], "Chrome 126, mobile, in-app")
        self.assertEqual(rec["telemetry"]["inner_w"], 384)                     # other telemetry is untouched
        self.assertNotIn("SM-A546E", str(rec))
        self.assertIsNone(api._session_record("verify", ch, [], None, 5000.0)["telemetry"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
