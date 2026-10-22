"""
Checks that the trained model is a BaseEvaluator, so /verify calls one interface and never has to
ask which engine is loaded.

    python admin-and-tests/test_model_evaluator.py
"""
import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE.parent / "machine-learning"))
_tmp = Path(tempfile.mkdtemp())                 # never touch the real study files or the real model
os.environ["GOTCHA_DATA"], os.environ["GOTCHA_CONSENT"] = str(_tmp / "s.jsonl"), str(_tmp / "c.jsonl")
os.environ["GOTCHA_MODEL"] = str(_tmp / "model.joblib")

import joblib  # noqa: E402
import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gamecore import make_layout, obstacle_window  # noqa: E402
from ml_common import FEATURE_SETS, make_pipeline  # noqa: E402

# A throwaway model fitted on noise: these tests are about wiring, not about accuracy.
NAMES = FEATURE_SETS["default"]
_rng = np.random.default_rng(0)
BUNDLE = {"pipeline": make_pipeline().fit(_rng.normal(size=(80, len(NAMES))), np.array([0, 1] * 40)),
          "features": NAMES, "feature_set": "default", "threshold": 0.5, "min_attempts": 5,
          "coarse_step_ms": 15.0, "synthetic": False, "trained_at": "test", "n_human_sessions": 40,
          "n_human_pids": 4, "bot_kinds": {}}
joblib.dump(BUNDLE, os.environ["GOTCHA_MODEL"])

import backend_api as api  # noqa: E402


def make_level(seed):
    """A random level and a human-like set of key events for it: (spawns, level_end, raw events)."""
    rng = random.Random(seed)
    spawns, level_end = make_layout(rng)
    events = []
    for s in spawns:
        down = obstacle_window(s)[2] + rng.gauss(0, 40)
        events += [{"k": "d", "t": round(down, 1)}, {"k": "u", "t": round(down + max(30.0, rng.gauss(90, 20)), 1)}]
    events.sort(key=lambda e: e["t"])
    return spawns, level_end, events


def fresh_evaluator():
    """A model evaluator with its own empty replay history, so levels cannot flag each other."""
    return api.ModelEvaluator(api.ModelRuntime(BUNDLE), api.engine)


def play_level_through_verify(seed=7):
    """Get a level from /challenge, answer it with human-like key events and POST them to /verify.
    Returns the issued Challenge, the raw events and the response."""
    client = TestClient(api.app)
    body = client.get("/challenge").json()
    issued = api.CHALLENGES[body["nonce"]]
    rng = random.Random(seed)
    raw = []
    for s in issued.spawns_ms:
        down = obstacle_window(s)[2] + rng.gauss(0, 40)
        raw += [{"k": "d", "t": round(down, 1)}, {"k": "u", "t": round(down + max(30.0, rng.gauss(90, 20)), 1)}]
    raw.sort(key=lambda e: e["t"])
    issued.issued_at -= 60.0                                       # pretend the level has been played
    return issued, raw, client.post("/verify", json={"nonce": body["nonce"], "events": raw})


def last_logged():
    return json.loads((_tmp / "s.jsonl").read_text(encoding="utf-8").splitlines()[-1])


class ModelIsAnEvaluator(unittest.TestCase):
    def test_both_engines_are_base_evaluators(self):
        self.assertIsInstance(api.engine, api.BaseEvaluator)
        self.assertIsInstance(fresh_evaluator(), api.BaseEvaluator)

    def test_a_loaded_model_is_the_evaluator(self):
        self.assertIsInstance(api.ml_engine, api.ModelRuntime)
        self.assertIsInstance(api.evaluator, api.ModelEvaluator)

    def test_without_a_model_the_rule_engine_is_the_evaluator(self):
        self.assertIs(api.pick_evaluator(api.engine, None), api.engine)
        picked = api.pick_evaluator(api.engine, api.ml_engine)
        self.assertIsInstance(picked, api.ModelEvaluator)
        self.assertIs(picked.runtime, api.ml_engine)

    def test_hard_rules_are_shared(self):
        spawns, level_end, raw = make_level(1)
        ch = api.Challenge(spawns, level_end, 0.0)
        events = [api.Event(**e) for e in raw]
        for elapsed in (100.0, level_end + 100.0):              # far too early, then plausible
            _, want, _ = api.engine.precheck(ch, events, elapsed)
            _, got, _ = fresh_evaluator().precheck(ch, events, elapsed)
            self.assertEqual(got is None, want is None)
            if want is not None:
                self.assertEqual((got.verdict, got.reasons), (want.verdict, want.reasons))

    def test_behavior_matches_the_model_runtime(self):
        for seed in range(20):
            with self.subTest(seed=seed):
                spawns, level_end, raw = make_level(seed)
                ch = api.Challenge(spawns, level_end, 0.0)
                strokes, early, feats = api.engine.precheck(ch, [api.Event(**e) for e in raw], level_end + 100.0)
                self.assertIsNone(early)
                got = fresh_evaluator().behavior(ch, strokes, feats)
                want = api.ModelRuntime(BUNDLE).judge({"spawns_ms": spawns, "events": raw})
                self.assertEqual((got.verdict, got.reasons), (want["verdict"], [want["reason"]]))

    def test_verify_endpoint_returns_the_models_verdict(self):
        issued, raw, reply = play_level_through_verify()
        self.assertEqual(reply.status_code, 200)
        want = api.ModelRuntime(BUNDLE).judge({"spawns_ms": issued.spawns_ms, "events": raw})
        self.assertEqual(last_logged()["verdict"], want["verdict"])
        self.assertEqual(reply.json()["success"], want["verdict"] == "human")

    def test_verify_endpoint_returns_the_rule_engines_verdict_when_no_model_loaded(self):
        with mock.patch.object(api, "evaluator", api.engine):      # same /verify code, other evaluator
            issued, raw, reply = play_level_through_verify()
        self.assertEqual(reply.status_code, 200)
        want = api.engine.analyze(issued, [api.Event(**e) for e in raw], 60000.0)
        self.assertEqual(last_logged()["verdict"], want.verdict)
        self.assertEqual(reply.json()["success"], want.verdict == "human")


if __name__ == "__main__":
    unittest.main(verbosity=2)
