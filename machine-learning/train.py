"""
Train the behavioral classifier from labeled sessions.

    python train.py --data sessions.jsonl [more.jsonl ...] --out model.joblib

Reads only labeled collection data (humans from collect.html, bots from bot_api.py /
bot_playwright.py). Logistic regression on behavior-only features; the decision threshold is
chosen from participant-grouped out-of-fold scores so that about --target-frr of human
sessions would be turned away.

Safety rails
  * Refuses synthetic data unless --allow-synthetic, and then marks the model synthetic; the
    API refuses to load a synthetic model unless GOTCHA_ALLOW_SYNTHETIC_MODEL=1.
  * Warns when the threshold rests on fewer than 10 human participants.
"""
import argparse
import json
import time
from collections import Counter

import joblib
import numpy as np
from sklearn.metrics import roc_auc_score

from features import COARSE_STEP_MS
from ml_common import (FEATURE_SETS, MIN_ATTEMPTS, TEST_ONLY_KINDS, Dataset, choose_threshold, load_records,
                       make_pipeline, oof_scores)

ap = argparse.ArgumentParser()
ap.add_argument("--data", nargs="+", default=["sessions.jsonl"])
ap.add_argument("--out", default="model.joblib")
ap.add_argument("--features", choices=sorted(FEATURE_SETS), default="default")
ap.add_argument("--C", type=float, default=0.3, help="inverse L2 strength; small = strong regularization")
ap.add_argument("--target-frr", type=float, default=0.05)
ap.add_argument("--allow-synthetic", action="store_true")
args = ap.parse_args()

recs, skipped = load_records(args.data, args.allow_synthetic)
if not recs:
    raise SystemExit(f"No usable sessions. Skipped: {dict(skipped)}")
ds = Dataset(recs)
names = FEATURE_SETS[args.features]
X = ds.cols(names)
train = ds.trainable()

print(f"sessions used: {len(recs)}  (skipped: {dict(skipped)})")
print(f"scorable (>= {MIN_ATTEMPTS} attempts, timer step <= {COARSE_STEP_MS} ms): {int(ds.scorable.sum())}")
print(f"  unscorable by kind: {dict(Counter(ds.kind[~ds.scorable].tolist()))}")
print(f"  held out of training (test-only kinds {sorted(TEST_ONLY_KINDS)}): "
      f"{int((ds.scorable & ~train).sum())} sessions")
h_pids = set(ds.pid[train & (ds.y == 1)])
b_pids = set(ds.pid[train & (ds.y == 0)])
print(f"training set: {int((train & (ds.y == 1)).sum())} human sessions / {len(h_pids)} people, "
      f"{int((train & (ds.y == 0)).sum())} bot sessions / {len(b_pids)} bot runs")
print(f"  bot kinds trained on: {dict(Counter(ds.kind[train & (ds.y == 0)].tolist()))}")
if len(h_pids) < 2 or len(b_pids) < 1:
    raise SystemExit("Need at least 2 human participants and 1 bot run with scorable sessions.")
if len(h_pids) < 10:
    print(f"WARNING: only {len(h_pids)} human participants. The threshold and every number here is unstable.")

Xt, yt, gt = X[train], ds.y[train], ds.pid[train]
oof = oof_scores(Xt, yt, gt, args.C)
ok = ~np.isnan(oof)
thr = choose_threshold(oof[ok & (yt == 1)], args.target_frr)
auc = roc_auc_score(yt[ok], oof[ok]) if len(set(yt[ok])) == 2 else float("nan")
print(f"grouped out-of-fold AUC: {auc:.3f}   threshold (P(human) >= t accepts): {thr:.3f} "
      f"for target FRR {args.target_frr:.0%}")

pipe = make_pipeline(args.C).fit(Xt, yt)
coefs = pipe.named_steps["clf"].coef_[0]
print("\nlargest coefficients (standardized; + = more human-like):")
for i in np.argsort(-np.abs(coefs))[:8]:
    print(f"  {names[i]:20s} {coefs[i]:+.2f}")

bundle = {
    "schema": 1, "pipeline": pipe, "features": names, "feature_set": args.features, "threshold": thr,
    "target_frr": args.target_frr, "C": args.C, "min_attempts": MIN_ATTEMPTS, "coarse_step_ms": COARSE_STEP_MS,
    "synthetic": ds.synthetic, "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "n_human_sessions": int((train & (ds.y == 1)).sum()), "n_human_pids": len(h_pids),
    "n_bot_sessions": int((train & (ds.y == 0)).sum()), "bot_kinds": dict(Counter(ds.kind[train & (ds.y == 0)].tolist())),
    "oof_auc": float(auc),
}
joblib.dump(bundle, args.out)
card = {k: v for k, v in bundle.items() if k != "pipeline"}
with open(args.out + ".card.json", "w") as f:
    json.dump(card, f, indent=2)
print(f"\nsaved {args.out} (+ .card.json)" + ("   *** SYNTHETIC MODEL: pipeline test only ***" if ds.synthetic else ""))
