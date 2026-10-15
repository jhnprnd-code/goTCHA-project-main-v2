"""
features.py
Shared feature extractor for training and inference.
Calculates 28 behavioral metrics covering timing variance, shape, and temporal drift.
"""
import importlib
import math
import numpy as np
from gamecore import replay, pair_strokes

{
    "python.analysis.extraPaths": [
        "./backend",
        "./gamecore",
        "./frontend",
        "./machine_learning",
        "./admin_and_tests"
    ]
}

COARSE_STEP_MS = 15.0

# Must exactly match the groups defined in ml_common.py
FEATURES = [
    # Participation
    "attempt_rate", "clear_rate", "extra_press_rate", "stroke_rate",
    # Location
    "err_mean", "err_abs_mean", "frac_early", "dwell_mean", "dwell_median", "dwell_min",
    # Scale
    "err_std", "err_mad", "err_iqr", "err_range", "near_perfect_frac", "dwell_std", "dwell_cv",
    # Shape
    "err_skew", "err_bowley", "dwell_skew",
    # Temporal
    "err_slope", "absdev_slope", "half_mean_shift", "half_logvar_ratio", "err_lag1",
    "diff_ratio", "dwell_slope", "err_dwell_corr"
]

def estimate_step(times: list) -> float:
    """Coarsest time grid that all event times sit on (browser timer resolution)."""
    if len(times) < 2:
        return 0.0
    t = np.asarray(times, float) - times[0]   # differences remove the unknown start offset
    for q in (100.0, 50.0, 20.0, 10.0, 5.0, 2.0, 1.0, 0.5):
        if np.all(np.abs(t - np.round(t / q) * q) < 0.11):
            return q
    return 0.1

def _skew(y: np.ndarray) -> float:
    if len(y) < 3:
        return np.nan
    std = np.std(y)
    if std == 0:
        return 0.0
    return float(np.mean(((y - np.mean(y)) / std) ** 3))

def _slope(y: np.ndarray) -> float:
    n = len(y)
    if n < 2:
        return np.nan
    x = np.arange(n)
    return float(np.polyfit(x, y, 1)[0])

def _corr(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 2 or len(y) < 2:
        return np.nan
    c = np.corrcoef(x, y)[0, 1]
    return float(c) if not np.isnan(c) else 0.0

def extract(spawns_ms: list, strokes: list, step_ms: float = 0.0) -> dict:
    """Computes all 28 behavioral features from a physical replay of the key events."""
    n_spawns = len(spawns_ms)
    accepted, per_obs = replay(spawns_ms, strokes)
    
    errs = np.array([o["err"] for o in per_obs if o["err"] is not None])
    dwells = np.array([o["dwell"] for o in per_obs if o["dwell"] is not None])
    cleared = sum(1 for o in per_obs if o["cleared"])
    
    f = {
        "n_attempts": len(errs),
        "step_ms": step_ms
    }
    
    # 1. Participation
    duration = spawns_ms[-1] / 1000.0 if n_spawns > 0 else 1.0
    f["attempt_rate"] = len(errs) / n_spawns if n_spawns > 0 else 0.0
    f["clear_rate"] = cleared / n_spawns if n_spawns > 0 else 0.0
    f["extra_press_rate"] = max(0, len(strokes) - len(errs)) / n_spawns if n_spawns > 0 else 0.0
    f["stroke_rate"] = len(strokes) / duration if duration > 0 else 0.0
    
    if len(errs) < 2 or len(dwells) < 2:
        for k in FEATURES:
            if k not in f:
                f[k] = np.nan
        return f
        
    # 2. Location
    f["err_mean"] = float(np.mean(errs))
    f["err_abs_mean"] = float(np.mean(np.abs(errs)))
    f["frac_early"] = float(np.mean(errs < 0))
    f["dwell_mean"] = float(np.mean(dwells))
    f["dwell_median"] = float(np.median(dwells))
    f["dwell_min"] = float(np.min(dwells))
    
    # 3. Scale
    f["err_std"] = float(np.std(errs))
    f["err_mad"] = float(np.median(np.abs(errs - np.median(errs))))
    q25, q50, q75 = np.percentile(errs, [25, 50, 75])
    f["err_iqr"] = float(q75 - q25)
    f["err_range"] = float(np.max(errs) - np.min(errs))
    f["near_perfect_frac"] = float(np.mean(np.abs(errs) <= 15.0))
    f["dwell_std"] = float(np.std(dwells))
    f["dwell_cv"] = float(f["dwell_std"] / f["dwell_mean"]) if f["dwell_mean"] > 0 else 0.0
    
    # 4. Shape
    f["err_skew"] = _skew(errs)
    f["err_bowley"] = float((q75 + q25 - 2 * q50) / (q75 - q25)) if q75 != q25 else 0.0
    f["dwell_skew"] = _skew(dwells)
    
    # 5. Temporal
    f["err_slope"] = _slope(errs)
    f["absdev_slope"] = _slope(np.abs(errs - q50))
    
    half = len(errs) // 2
    if half >= 2:
        h1, h2 = errs[:half], errs[half:]
        f["half_mean_shift"] = float(np.mean(h2) - np.mean(h1))
        v1, v2 = np.var(h1), np.var(h2)
        f["half_logvar_ratio"] = float(np.log(v2 / v1)) if v1 > 0 and v2 > 0 else 0.0
    else:
        f["half_mean_shift"] = np.nan
        f["half_logvar_ratio"] = np.nan
        
    f["err_lag1"] = _corr(errs[:-1], errs[1:])
    v_diff = np.var(np.diff(errs))
    v_err = np.var(errs)
    f["diff_ratio"] = float(v_diff / v_err) if v_err > 0 else 0.0
    
    f["dwell_slope"] = _slope(dwells)
    f["err_dwell_corr"] = _corr(errs, dwells)
    
    return f

def record_features(rec: dict, quantize: float = 0.0) -> dict:
    """
    Main entry point for evaluating a session dictionary. 
    Matches the schema built in ml_common.py and evaluate.py.
    """
    events = [(e["k"], float(e["t"])) for e in rec.get("events", [])]
    
    if quantize > 0.0:
        events = [(k, math.floor(t / quantize) * quantize) for k, t in events]
        step_ms = quantize
    else:
        times = [t for _, t in events]
        step_ms = estimate_step(times)
        
    strokes = pair_strokes(events)
    return extract(rec.get("spawns_ms", []), strokes, step_ms)