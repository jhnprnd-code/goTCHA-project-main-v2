"""
Permutation test: does the detector beat chance at this sample size?

    python permutation_test.py --data sessions.jsonl --perms 1000 --jobs -1 --out results/permutation.json

Null hypothesis: session features carry no information about who is human. Under it the identities
(human / bot kind / group) can be shuffled across PARTICIPANTS. Each shuffle keeps every person's
sessions together, keeps the number of humans and of each bot kind (test-only kinds stay test-only),
and reruns the same leave-one-participant-out protocol as evaluate.py (ml_common.logo_scores:
threshold chosen inside each training fold, then the near-duplicate rule).

  * Primary statistics (one-sided p = (1 + #{null at least as good}) / (1 + perms)):
      AUC, and balanced error = (FRR + FAR) / 2 at the deployed operating point, for the deployed
      system (model + duplicate rule) and for the learned model alone.
  * Human sessions rejected and FAR are shown against the null for context only: they depend on the
    operating point, so a small count by itself says little (a lenient threshold gives 0 rejections).
  * The duplicate rule uses no labels, so it flags the same sessions in every shuffle; if it catches
    your repetitive bots, the deployed-system statistic will beat the null for that reason alone.
    Read the model-alone row to judge the learned detector.
  * Resolution is 1 / (perms + 1): use at least 1000 permutations for anything you quote.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import argparse
import json
import time
from pathlib import Path

import numpy as np
from joblib import Parallel, delayed
from sklearn.metrics import roc_auc_score

from ml_common import (FEATURE_SETS, TEST_ONLY_KINDS, Dataset, dup_flags, load_records, logo_scores)

# (label, key, which tail counts as "at least as good as observed")
PRIMARY = [("AUC (learned model)", "auc", "hi"),
           ("balanced error, deployed system", "bal_err", "lo"),
           ("balanced error, learned model alone", "bal_err_model", "lo")]
CONTEXT = [("human sessions rejected (deployed)", "h_rejected", "lo"),
           ("FAR, deployed system", "far", "lo"),
           ("FAR, learned model alone", "far_model", "lo")]


def run(X, y, kind, pid, scorable, dup, C, target_frr):
    """All statistics for one labeling (the real one or a shuffled one)."""
    trainable = scorable & ~np.isin(kind, list(TEST_ONLY_KINDS))
    score, thr = logo_scores(X, y, pid, scorable, trainable, C, target_frr)
    with np.errstate(invalid="ignore"):
        model_accept = scorable & ~np.isnan(score) & (score >= thr)
    accepted = model_accept & ~dup
    human, bots = y == 1, y == 0
    ok = scorable & ~np.isnan(score)
    auc = float(roc_auc_score(y[ok], score[ok])) if len(set(y[ok])) == 2 else float("nan")
    frr, far = (human & ~accepted).sum() / human.sum(), (bots & accepted).sum() / bots.sum()
    frr_m, far_m = (human & ~model_accept).sum() / human.sum(), (bots & model_accept).sum() / bots.sum()
    return {"auc": auc, "bal_err": float((frr + far) / 2), "bal_err_model": float((frr_m + far_m) / 2),
            "h_rejected": int((human & ~accepted).sum()), "far": float(far), "far_model": float(far_m)}


def shuffle_run(seed, X, y_id, kind_id, rows, pid, scorable, dup, C, target_frr):
    """Give every participant another participant's identity (human / kind), then rerun."""
    perm = np.random.default_rng(seed).permutation(len(y_id))
    return run(X, y_id[perm][rows], kind_id[perm][rows], pid, scorable, dup, C, target_frr)


def tail(null, obs, which):
    null = null[~np.isnan(null)]
    hit = (null >= obs - 1e-12) if which == "hi" else (null <= obs + 1e-12)
    return float((1 + hit.sum()) / (1 + len(null)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", default=["sessions.jsonl"])
    ap.add_argument("--out", default="results/permutation.json")
    ap.add_argument("--features", choices=sorted(FEATURE_SETS), default="default")
    ap.add_argument("--C", type=float, default=0.3)
    ap.add_argument("--target-frr", type=float, default=0.05)
    ap.add_argument("--perms", type=int, default=1000)
    ap.add_argument("--jobs", type=int, default=1, help="-1 = all cores")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--allow-synthetic", action="store_true")
    args = ap.parse_args()

    recs, skipped = load_records(args.data, args.allow_synthetic)
    if not recs:
        raise SystemExit(f"No usable sessions. Skipped: {dict(skipped)}")
    ds = Dataset(recs)
    X = ds.cols(FEATURE_SETS[args.features])
    dup = dup_flags(recs)
    ids, first, rows = np.unique(ds.pid, return_index=True, return_inverse=True)   # session -> participant
    y_id, kind_id = ds.y[first], ds.kind[first]
    if not ((ds.y == y_id[rows]).all() and (ds.kind == kind_id[rows]).all()):
        raise SystemExit("A participant has sessions with different labels or bot kinds; "
                         "identities can only be shuffled between whole participants.")

    t0 = time.time()
    obs = run(X, ds.y, ds.kind, ds.pid, ds.scorable, dup, args.C, args.target_frr)
    per_run = time.time() - t0
    print(f"{len(recs)} sessions, {len(ids)} people ({len(set(ds.pid[ds.y == 1]))} human); one pass {per_run:.1f}s, "
          f"{args.perms} shuffles on {args.jobs} job(s) ~ {args.perms * per_run / max(abs(args.jobs), 1) / 60:.0f} min "
          f"(more if jobs exceeds cores)", flush=True)

    seeds = [int(q.generate_state(1)[0]) for q in np.random.SeedSequence(args.seed).spawn(args.perms)]
    shared = (X, y_id, kind_id, rows, ds.pid, ds.scorable, dup, args.C, args.target_frr)
    null = Parallel(n_jobs=args.jobs, verbose=5)(delayed(shuffle_run)(sd, *shared) for sd in seeds)

    out = {"data": [str(p) for p in args.data], "perms": args.perms, "seed": args.seed, "features": args.features,
           "n_sessions": len(recs), "n_people": int(len(ids)), "synthetic": ds.synthetic, "observed": obs, "stats": {}}
    banner = "SYNTHETIC DATA: pipeline test only, do not quote.\n" if ds.synthetic else ""
    print(f"\n{banner}Permutation test, {args.perms} shuffles of participant identities (seed {args.seed})\n")
    print(f"{'statistic':40} {'observed':>9} {'null mean':>10} {'null 2.5-97.5%':>18} {'p (one-sided)':>14}")
    for group, rows_ in (("primary", PRIMARY), ("context", CONTEXT)):
        for label, key, which in rows_:
            v = np.array([r[key] for r in null], float)
            lo, hi = np.nanpercentile(v, [2.5, 97.5])
            p = tail(v, obs[key], which)
            out["stats"][key] = {"observed": obs[key], "null_mean": float(np.nanmean(v)), "null_lo": float(lo),
                                 "null_hi": float(hi), "p": p, "role": group}
            fmt = (lambda x: f"{x:9.0f}") if key == "h_rejected" else (lambda x: f"{x:9.3f}")
            print(f"{label:40} {fmt(obs[key])} {fmt(np.nanmean(v))}  [{lo:7.3f}, {hi:7.3f}] "
                  f"{p:13.4f}{'' if group == 'primary' else '  (context)'}")
    print(f"\np = share of shuffles at least as good as observed, counting the observed labeling itself; "
          f"smallest possible p = {1 / (1 + args.perms):.4f}.")
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
