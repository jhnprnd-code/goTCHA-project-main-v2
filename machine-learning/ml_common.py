"""
Shared data loading, feature sets and model construction for train.py, evaluate.py and the API.

Design choices that matter for the paper
  * Default model = scale + shape + temporal features. Participation features (clear rate,
    stroke rate) are excluded because "skilled" and "bot" would be confounded, and location
    features (mean error, mean dwell) because our own bots' means are arbitrary settings, so a
    model could learn our attacker design instead of behavior.
  * 'replay' bots copy real human sequences, so by construction their features equal human
    features. They are TEST-ONLY (never trained on); the defense against them is the
    near-duplicate check, which has a structural ceiling (see evaluate.py).
  * Class weights are balanced, so many bot sessions do not swamp few human sessions.
"""
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.impute import SimpleImputer  # type: ignore[import-not-found]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-not-found]
from sklearn.model_selection import GroupKFold  # type: ignore[import-not-found]
from sklearn.pipeline import Pipeline  # type: ignore[import-not-found]
from sklearn.preprocessing import FunctionTransformer, StandardScaler  # type: ignore[import-not-found]

from features import COARSE_STEP_MS, FEATURES, record_features
from gamecore import pair_strokes, replay

MIN_ATTEMPTS = 5
TEST_ONLY_KINDS = {"replay", "pw_replay"}      # never trained on; see module docstring
DUP_TOL_MS = 15.0                              # near-duplicate: every common obstacle within this
DUP_MIN_COMMON = 5

FEATURE_GROUPS: Dict[str, List[str]] = {
    "participation": ["attempt_rate", "clear_rate", "extra_press_rate", "stroke_rate"],
    "location": ["err_mean", "err_abs_mean", "frac_early", "dwell_mean", "dwell_median", "dwell_min"],
    "scale": ["err_std", "err_mad", "err_iqr", "err_range", "near_perfect_frac", "dwell_std", "dwell_cv"],
    "shape": ["err_skew", "err_bowley", "dwell_skew"],
    "temporal": ["err_slope", "absdev_slope", "half_mean_shift", "half_logvar_ratio", "err_lag1",
                 "diff_ratio", "dwell_slope", "err_dwell_corr"],
}
_default = FEATURE_GROUPS["scale"] + FEATURE_GROUPS["shape"] + FEATURE_GROUPS["temporal"]
FEATURE_SETS: Dict[str, List[str]] = {
    "default": _default,
    "all": list(FEATURES),
    "no_temporal": FEATURE_GROUPS["scale"] + FEATURE_GROUPS["shape"],
    "no_dwell": [f for f in _default if not (f.startswith("dwell") or f == "err_dwell_corr")],
    **{f"only_{g}": cols for g, cols in FEATURE_GROUPS.items()},
}
assert set(sum(FEATURE_GROUPS.values(), [])) == set(FEATURES), "feature groups must cover FEATURES exactly"


# ------------------------------------------------------------------ loading
def load_records(paths: Sequence[str], allow_synthetic: bool = False) -> Tuple[List[dict], Counter]:
    """Labeled, valid, non-practice sessions. Unlabeled /verify traffic is never used."""
    recs, skipped = [], Counter()
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                skipped["bad json"] += 1
                continue
            src = r.get("source")
            if src == "synthetic" and not allow_synthetic:
                skipped["synthetic (use --allow-synthetic)"] += 1
            elif src not in ("collect", "synthetic") or r.get("label") not in ("human", "bot"):
                skipped["unlabeled"] += 1
            elif not r.get("valid", False):
                skipped["invalid (hard rule / frame gate)"] += 1
            elif r.get("practice"):
                skipped["practice"] += 1
            else:
                recs.append(r)
    return recs, skipped


class Dataset:
    """All features for all sessions, plus metadata arrays aligned by row."""

    def __init__(self, recs: List[dict], quantize: float = 0.0):
        feats = [record_features(r, quantize) for r in recs]
        self.recs = recs
        self.X = np.array([[f[k] for k in FEATURES] for f in feats], float)
        self.attempts = np.array([f["n_attempts"] for f in feats])
        self.step = np.array([f["step_ms"] for f in feats])
        self.scorable = (self.attempts >= MIN_ATTEMPTS) & (self.step <= COARSE_STEP_MS)
        self.pid = np.array([r["pid"] for r in recs])
        self.y = np.array([1 if r["label"] == "human" else 0 for r in recs])   # 1 = human
        self.kind = np.array(["human" if r["label"] == "human" else (r.get("bot_kind") or "bot") for r in recs])
        self.group = np.array([r.get("group", "") for r in recs])
        self.synthetic = any(r.get("source") == "synthetic" for r in recs)

    def cols(self, names: Sequence[str]) -> np.ndarray:
        return self.X[:, [FEATURES.index(n) for n in names]]

    def trainable(self) -> np.ndarray:
        return self.scorable & ~np.isin(self.kind, list(TEST_ONLY_KINDS))


# ------------------------------------------------------------------- model
def _clip5(x):
    return np.clip(x, -5.0, 5.0)


def make_pipeline(C: float = 0.3) -> Pipeline:
    return Pipeline([
        ("imp", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("sc", StandardScaler()),
        ("clip", FunctionTransformer(_clip5)),     # tame heavy-tailed features
        ("clf", LogisticRegression(C=C, class_weight="balanced", max_iter=2000)),
    ])


def oof_scores(X: np.ndarray, y: np.ndarray, groups: np.ndarray, C: float, n_splits: int = 5) -> np.ndarray:
    """Out-of-fold P(human), grouped by participant so a person never trains and tests."""
    s = np.full(len(y), np.nan)
    n_groups = len(set(groups))
    if n_groups < 2:
        return s
    for tr, te in GroupKFold(n_splits=min(n_splits, n_groups)).split(X, y, groups):
        if len(set(y[tr])) < 2:
            continue
        s[te] = make_pipeline(C).fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
    return s


def choose_threshold(human_scores: np.ndarray, target_frr: float) -> float:
    """Accept when P(human) >= threshold. Chosen so ~target_frr of human scores fall below it."""
    return float(np.quantile(human_scores, target_frr))


def logo_scores(X: np.ndarray, y: np.ndarray, pid: np.ndarray, scorable: np.ndarray, train_ok: np.ndarray,
                C: float, target_frr: float, nested: bool = True) -> Tuple[np.ndarray, np.ndarray]:
    """Leave-one-participant-out P(human) for all scorable sessions, plus the per-session
    threshold chosen inside that session's training fold (NaN when nested=False).
    Shared by evaluate.py and permutation_test.py so both run the identical protocol."""
    n = len(y)
    score = np.full(n, np.nan)
    thr = np.full(n, np.nan)
    for p in sorted(set(pid[scorable])):
        te = scorable & (pid == p)
        tr = train_ok & (pid != p)
        if len(set(y[tr])) < 2:
            continue
        model = make_pipeline(C).fit(X[tr], y[tr])
        score[te] = model.predict_proba(X[te])[:, 1]
        if nested:
            inner = oof_scores(X[tr], y[tr], pid[tr], C)
            ok = ~np.isnan(inner) & (y[tr] == 1)
            if ok.sum() >= 2:
                thr[te] = choose_threshold(inner[ok], target_frr)
    return score, thr


# ------------------------------------------------------------- replay defense
def err_vector(rec: dict) -> List[Optional[float]]:
    strokes = pair_strokes([(e["k"], float(e["t"])) for e in rec["events"]])
    _, per = replay(rec["spawns_ms"], strokes)
    return [o["err"] for o in per]


def near_duplicate(a: Sequence[Optional[float]], b: Sequence[Optional[float]]) -> bool:
    """True if two sessions' per-obstacle timing errors agree everywhere they overlap."""
    common = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    return len(common) >= DUP_MIN_COMMON and max(abs(x - y) for x, y in common) < DUP_TOL_MS


def dup_flags(recs: List[dict]) -> np.ndarray:
    """Process sessions in arrival order; flag any that near-duplicates an EARLIER session.
    The server cannot know labels, so every earlier session counts."""
    order = sorted(range(len(recs)), key=lambda i: (recs[i].get("ts", 0), i))
    seen: List[List[Optional[float]]] = []
    flags = np.zeros(len(recs), bool)
    for i in order:
        v = err_vector(recs[i])
        if sum(x is not None for x in v) >= DUP_MIN_COMMON:
            flags[i] = any(near_duplicate(v, s) for s in seen)
            seen.append(v)
    return flags
