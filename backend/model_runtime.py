"""
Inference wrapper used by the API. Loads model.joblib (written by train.py) and judges a level.

Everything goes through features.record_features, the same code path as training, so a
session scores identically offline and live.

Verdicts
  inconclusive  too few attempts, or the browser's timer is too coarse to see micro-variance
                (we say so instead of guessing)
  bot           P(human) below the threshold, or the per-obstacle timing is a near-duplicate of an
                earlier session (replay of one recording)
  human         otherwise

Env: GOTCHA_MODEL (path), GOTCHA_THRESHOLD (override), GOTCHA_ALLOW_SYNTHETIC_MODEL=1
"""
import logging
import os
import sys
from collections import deque
from pathlib import Path
from typing import Deque, List, Optional

import numpy as np

log = logging.getLogger("gotcha")
ML_DIR = str(Path(__file__).resolve().parent.parent / "machine-learning")
if ML_DIR not in sys.path:
    sys.path.insert(0, ML_DIR)


class ModelRuntime:
    def __init__(self, bundle: dict):
        self.bundle = bundle
        self.pipe = bundle["pipeline"]
        self.names: List[str] = bundle["features"]
        self.threshold = float(os.environ.get("GOTCHA_THRESHOLD", bundle["threshold"]))
        self.history: Deque[list] = deque(maxlen=5000)   # earlier sessions' timing errors (replay rule)

    def describe(self) -> str:
        b = self.bundle
        return (f"trained model: {b['feature_set']} features, threshold {self.threshold:.3f}, "
                f"trained {b['trained_at']} on {b['n_human_sessions']} human sessions / "
                f"{b['n_human_pids']} people, bot kinds {b['bot_kinds']}")

    def judge(self, rec: dict) -> dict:
        """rec = {"spawns_ms": [...], "events": [{"k","t"}]}"""
        from features import record_features
        from ml_common import err_vector, near_duplicate

        f = record_features(rec)
        info = {"engine": "model", "attempts": int(f["n_attempts"]), "step_ms": f["step_ms"]}
        if f["n_attempts"] < self.bundle["min_attempts"]:
            return {"verdict": "inconclusive", "reason": "Not enough attempts to evaluate.", "info": info}
        if f["step_ms"] > self.bundle["coarse_step_ms"]:
            return {"verdict": "inconclusive", "info": info,
                    "reason": f"Browser timer too coarse ({f['step_ms']:g} ms) to judge timing."}

        vec = err_vector(rec)
        dup = any(near_duplicate(vec, old) for old in self.history)
        self.history.append(vec)
        x = np.array([[f[k] for k in self.names]], float)
        p = float(self.pipe.predict_proba(x)[0, 1])
        info["p_human"] = round(p, 3)
        if dup:
            return {"verdict": "bot", "reason": "Timing is a near-duplicate of an earlier session (replay).", "info": info}
        if p < self.threshold:
            return {"verdict": "bot", "reason": f"Behavior unlike the human model (p={p:.2f}).", "info": info}
        return {"verdict": "human", "reason": f"Behavior consistent with the human model (p={p:.2f}).", "info": info}


def load_model(default_path: Path) -> Optional[ModelRuntime]:
    path = Path(os.environ.get("GOTCHA_MODEL", default_path))
    if not path.exists():
        log.warning("No model at %s: using the rule-based placeholder engine.", path)
        return None
    try:
        import joblib
        bundle = joblib.load(path)
    except Exception as exc:  # missing sklearn, corrupt file, version mismatch
        log.error("Could not load model %s (%s): using the rule-based placeholder engine.", path, exc)
        return None
    if bundle.get("synthetic") and os.environ.get("GOTCHA_ALLOW_SYNTHETIC_MODEL") != "1":
        log.error("Model %s was trained on SYNTHETIC data. Refusing to load it "
                  "(set GOTCHA_ALLOW_SYNTHETIC_MODEL=1 for pipeline tests only).", path)
        return None
    rt = ModelRuntime(bundle)
    log.info("verdict engine: %s", rt.describe())
    if bundle.get("synthetic"):
        log.warning("*** SYNTHETIC MODEL LOADED: not a real detector ***")
    return rt
