"""
Evaluation for the paper: confusion matrix, FAR / FRR with participant-cluster bootstrap CIs,
ROC / EER, per-attacker and per-group breakdowns, unseen-attacker test, timer-precision and
feature ablations.

    python evaluate.py --data sessions.jsonl --out results/

Protocol
  * Leave-one-participant-out: every held-out person (human or bot run) is scored by a model
    that never saw them. Humans' sessions are correlated, so CIs resample PEOPLE, not sessions.
  * Decision threshold is chosen INSIDE each training fold (inner grouped CV), never on test
    scores, so the reported FAR/FRR are not tuned on the data they describe.
  * A rate of exactly 0% or 100% leaves the participant bootstrap nothing to resample (its CI
    collapses to a point), so those rates get a Wilson interval over participants, marked with a trailing *.
  * Unscorable sessions (too few attempts, or timer too coarse to judge) are not accepted:
    for humans that is an 'inconclusive, try again' (counted in the strict FRR), for bots a
    rejection.
  * 'replay' bots are test-only: they copy human sequences, so features cannot separate them.
    The near-duplicate rule catches reuse of the same recording, nothing more.
  * Unseen-attacker test: for each bot kind, retrain without it and score it.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless backend; must be set before importing pyplot
import matplotlib.pyplot as plt
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline as skl_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from features import COARSE_STEP_MS, FEATURES, estimate_step, extract
from gamecore import pair_strokes
from ml_common import FEATURE_SETS, MIN_ATTEMPTS, TEST_ONLY_KINDS, Dataset, dup_flags, load_records
from ml_common import logo_scores as _logo_scores

ap = argparse.ArgumentParser()
ap.add_argument("--data", nargs="+", default=["sessions.jsonl"])
ap.add_argument("--out", default="results")
ap.add_argument("--features", choices=sorted(FEATURE_SETS), default="default")
ap.add_argument("--C", type=float, default=0.3)
ap.add_argument("--target-frr", type=float, default=0.05)
ap.add_argument("--boot", type=int, default=2000)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--allow-synthetic", action="store_true")
ap.add_argument("--fast", action="store_true", help="skip model comparison and the timer ablation")
args = ap.parse_args()

rng = np.random.default_rng(args.seed)
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)

recs, skipped = load_records(args.data, args.allow_synthetic)
if not recs:
    raise SystemExit(f"No usable sessions. Skipped: {dict(skipped)}")
ds = Dataset(recs)
names = FEATURE_SETS[args.features]
X = ds.cols(names)
n = len(recs)
is_h = ds.y == 1
trainable = ds.trainable()
dup = dup_flags(recs)


# ------------------------------------------------------------------ CV engine
def logo_scores(Xm, train_ok, nested=True, exclude_kind=None):
    """Leave-one-participant-out P(human) for all scorable sessions, plus the per-session
    threshold chosen inside that session's training fold."""
    allowed = train_ok & (ds.kind != exclude_kind) if exclude_kind else train_ok
    return _logo_scores(Xm, ds.y, ds.pid, ds.scorable, allowed, args.C, args.target_frr, nested)


def auc_eer(score, mask):
    m = mask & ~np.isnan(score)
    if len(set(ds.y[m])) < 2:
        return float("nan"), float("nan")
    fpr, tpr, _ = roc_curve(ds.y[m], score[m])
    fnr = 1 - tpr
    i = int(np.argmin(np.abs(fnr - fpr)))
    return float(roc_auc_score(ds.y[m], score[m])), float((fpr[i] + fnr[i]) / 2)


def cluster_rate(bad, mask, boot=args.boot):
    """Rate of `bad` among sessions in mask, with a 95% CI from resampling participants."""
    pids = sorted(set(ds.pid[mask]))
    if not pids:
        return {"rate": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0, "pids": 0}
    num = np.array([bad[mask & (ds.pid == p)].sum() for p in pids], float)
    den = np.array([(mask & (ds.pid == p)).sum() for p in pids], float)
    idx = rng.integers(0, len(pids), (boot, len(pids)))
    r = num[idx].sum(1) / np.maximum(den[idx].sum(1), 1)
    lo, hi = np.percentile(r, [2.5, 97.5])
    method = "bootstrap"
    if num.sum() in (0, den.sum()):      # nobody (or everybody) is bad: every resample gives the same rate
        lo, hi = wilson(int(num.sum() > 0) * len(pids), len(pids))
        method = "wilson"
    return {"rate": float(num.sum() / den.sum()), "lo": float(lo), "hi": float(hi),
            "n": int(den.sum()), "pids": len(pids), "ci": method}


def wilson(k, m, z=1.96):
    if m == 0:
        return float("nan"), float("nan")
    p = k / m
    d = 1 + z * z / m
    c = (p + z * z / (2 * m)) / d
    h = z * math.sqrt(p * (1 - p) / m + z * z / (4 * m * m)) / d
    return max(0.0, c - h), min(1.0, c + h)


def pct(r):
    if r["n"] == 0:
        return "n/a"
    return f"{r['rate']:.1%} [{r['lo']:.1%}, {r['hi']:.1%}]" + ("*" if r.get("ci") == "wilson" else "")


# ------------------------------------------------------------ main protocol
t0 = time.time()
print(f"{n} sessions; {len(set(ds.pid))} people; scorable {int(ds.scorable.sum())}; running LOPO...", flush=True)
score, thr = logo_scores(X, trainable)
model_accept = ds.scorable & ~np.isnan(score) & (score >= thr)
accepted = model_accept & ~dup                        # deployed system = model + duplicate rule
accepted_nodup = model_accept

human = is_h
bots = ~is_h
frr_strict = cluster_rate(~accepted, human)
frr_model = cluster_rate(~model_accept, human & ds.scorable)
far_all = cluster_rate(accepted, bots)
far_trainkinds = cluster_rate(accepted, bots & ~np.isin(ds.kind, list(TEST_ONLY_KINDS)))
auc, eer = auc_eer(score, ds.scorable)

H_acc, H_rej = int((human & accepted).sum()), int((human & ~accepted).sum())
B_acc, B_rej = int((bots & accepted).sum()), int((bots & ~accepted).sum())

kinds = sorted(set(ds.kind[bots]))
per_kind = {}
for k in kinds:
    m = ds.kind == k
    a, e = auc_eer(score, ds.scorable & (human | m))
    per_kind[k] = {"far": cluster_rate(accepted, m), "far_nodup": cluster_rate(accepted_nodup, m),
                   "auc": a, "sessions": int(m.sum()), "unscorable": int((m & ~ds.scorable).sum()),
                   "test_only": k in TEST_ONLY_KINDS}

groups = sorted(set(ds.group[human]))
per_group = {g: cluster_rate(~accepted, human & (ds.group == g)) for g in groups}

# pass within 3 attempts: first 3 sessions of each human
passes, n_h = 0, 0
for p in sorted(set(ds.pid[human])):
    idx = list(np.where(ds.pid == p)[0])
    idx.sort(key=lambda i: (recs[i].get("ts", 0), i))
    n_h += 1
    passes += int(any(accepted[i] for i in idx[:3]))
p3_lo, p3_hi = wilson(passes, n_h)

# --------------------------------------------------------- unseen attackers
print("unseen-attacker test...", flush=True)
unseen = {}
for k in kinds:
    if k in TEST_ONLY_KINDS or (ds.kind == k).sum() == 0:
        continue
    sc, th = logo_scores(X, trainable, exclude_kind=k)
    m = ds.kind == k
    acc = ds.scorable & ~np.isnan(sc) & (sc >= th) & ~dup
    a, _ = auc_eer(sc, ds.scorable & (human | m))
    unseen[k] = {"far": cluster_rate(acc, m), "auc": a}

# ------------------------------------------------------------------ ablations
print("feature-set ablation...", flush=True)
feat_abl = {}
for name, cols in FEATURE_SETS.items():
    sc, _ = logo_scores(ds.cols(cols), trainable, nested=False)
    feat_abl[name] = auc_eer(sc, ds.scorable)

model_cmp, timer_abl = {}, {}
if not args.fast:
    print("model comparison...", flush=True)
    Xd = np.nan_to_num(X, nan=0.0)
    for label, make in {
        "logistic regression (L2)": None,
        "SVM (RBF)": lambda: skl_pipeline(StandardScaler(), SVC(C=1.0, class_weight="balanced", probability=True,
                                                               random_state=0)),
        "random forest": lambda: RandomForestClassifier(200, min_samples_leaf=2, class_weight="balanced",
                                                        random_state=0, n_jobs=-1),
    }.items():
        if make is None:
            sc, _ = logo_scores(X, trainable, nested=False)
        else:
            sc = np.full(n, np.nan)
            for pid in sorted(set(ds.pid[ds.scorable])):
                te, tr = ds.scorable & (ds.pid == pid), trainable & (ds.pid != pid)
                if len(set(ds.y[tr])) == 2:
                    sc[te] = make().fit(Xd[tr], ds.y[tr]).predict_proba(Xd[te])[:, 1]
        model_cmp[label] = auc_eer(sc, ds.scorable)

    print("timer-precision ablation...", flush=True)
    for q in [0.0, 1.0, 5.0, 20.0, 100.0]:
        for corrected in (True, False):
            rows = []
            for r in recs:
                ev = [(e["k"], float(e["t"])) for e in r["events"]]
                if q:
                    ev = [(k, math.floor(t / q) * q) for k, t in ev]
                step = (q if q else estimate_step([t for _, t in ev])) if corrected else 0.1
                rows.append(extract(r["spawns_ms"], pair_strokes(ev), step))
            Xq = np.array([[f[k] for k in FEATURES] for f in rows], float)
            Xq = Xq[:, [FEATURES.index(c) for c in names]]
            att = np.array([f["n_attempts"] for f in rows])
            saved = ds.scorable
            ds.scorable = att >= MIN_ATTEMPTS          # bypass the coarse-timer gate on purpose
            sc, _ = logo_scores(Xq, trainable & ds.scorable, nested=False)
            timer_abl[(q, corrected)] = auc_eer(sc, ds.scorable)
            ds.scorable = saved

# -------------------------------------------------------------------- figures
fig, ax = plt.subplots(figsize=(4.8, 3.8))
cm = np.array([[H_acc, H_rej], [B_acc, B_rej]])
ax.imshow(cm, cmap="Blues")
for (i, j), v in np.ndenumerate(cm):
    ax.text(j, i, str(v), ha="center", va="center", fontsize=16, color="white" if v > cm.max() / 2 else "black")
ax.set_xticks([0, 1], ["accepted", "not accepted"])
ax.set_yticks([0, 1], ["human sessions", "bot sessions"])
ax.set_title("Confusion matrix (sessions)")
fig.tight_layout()
fig.savefig(out / "confusion_matrix.png", dpi=160)
plt.close(fig)

fig, ax = plt.subplots(figsize=(5.2, 4.4))
m = ds.scorable & ~np.isnan(score)
if len(set(ds.y[m])) == 2:
    f_, t_, _ = roc_curve(ds.y[m], score[m])
    ax.plot(f_, 1 - t_, lw=2.5, color="black", label=f"all bots (AUC {auc:.2f})")
    for k in kinds:
        mk = m & (human | (ds.kind == k))
        if len(set(ds.y[mk])) == 2:
            f_, t_, _ = roc_curve(ds.y[mk], score[mk])
            ax.plot(f_, 1 - t_, lw=1.2, label=k)
ax.plot([0, 1], [1, 0], ls=":", color="gray")
ax.set_xlabel("FAR (bots accepted)")
ax.set_ylabel("FRR (humans rejected)")
ax.set_title("FRR vs FAR (out-of-fold)")
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(out / "far_frr_curve.png", dpi=160)
plt.close(fig)

fig, ax = plt.subplots(figsize=(6.4, 3.6))
order = ["human"] + kinds
data = [score[(ds.kind == k) & m] for k in order]
ax.boxplot(data, tick_labels=order, vert=True)
ax.set_ylabel("P(human), out-of-fold")
ax.axhline(np.nanmedian(thr), ls="--", color="red", lw=1)
ax.set_title("Score by participant type (dashed = threshold)")
plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=8)
fig.tight_layout()
fig.savefig(out / "score_distributions.png", dpi=160)
plt.close(fig)

# --------------------------------------------------------------------- report
banner = ""
if ds.synthetic:
    banner = ("> **SYNTHETIC DATA.** At least one session comes from `synth_data.py`, a generator we wrote. "
              "These numbers test the pipeline only and are circular. Do not put them in the paper.\n\n")
L = [f"# goTCHA evaluation\n\n{banner}Generated {time.strftime('%Y-%m-%d %H:%M')} from {', '.join(args.data)}.\n"]
L.append("## Dataset\n")
L.append(f"- Sessions used: {n} ({int(human.sum())} human from {len(set(ds.pid[human]))} people; "
         f"{int(bots.sum())} bot from {len(set(ds.pid[bots]))} bot runs)")
L.append(f"- Skipped: {dict(skipped)}")
L.append(f"- Unscorable (fewer than {MIN_ATTEMPTS} attempts, or timer step above {COARSE_STEP_MS} ms): "
         f"{dict(Counter(ds.kind[~ds.scorable].tolist()))}")
L.append(f"- Human groups: {dict(Counter(ds.group[human].tolist()))} sessions")
L.append(f"- Features: `{args.features}` ({len(names)}), logistic regression C={args.C}, "
         f"target FRR {args.target_frr:.0%}, threshold chosen inside each training fold\n")

L.append("## Headline (deployed system = model + near-duplicate rule)\n")
L.append("| | accepted | not accepted |\n|---|---|---|")
L.append(f"| human sessions | {H_acc} | {H_rej} |\n| bot sessions | {B_acc} | {B_rej} |\n")
L.append(f"- **FRR (strict, includes inconclusive):** {pct(frr_strict)}")
L.append(f"- FRR (model only, scorable sessions): {pct(frr_model)}")
L.append(f"- **FAR (all bot sessions):** {pct(far_all)}")
L.append(f"- FAR excluding test-only attackers: {pct(far_trainkinds)}")
L.append(f"- AUC {auc:.3f}, EER {eer:.1%} (optimistic: EER picks its threshold on test scores)")
L.append(f"- Human passes within 3 attempts: {passes}/{n_h} people ({passes / max(n_h, 1):.0%}, "
         f"95% Wilson CI [{p3_lo:.0%}, {p3_hi:.0%}])")
L.append("\nCIs resample participants, not sessions. Humans contribute 3 correlated sessions each. "
         "A trailing * marks a rate of exactly 0% or 100%: nothing to resample, so a Wilson interval over participants is shown.\n")

L.append("## FAR by attacker\n")
L.append("| attacker | sessions | unscorable | FAR (model + dup rule) | FAR (model only) | AUC vs humans | note |\n|---|---|---|---|---|---|---|")
for k, v in per_kind.items():
    note = "test-only (copies humans; never trained on)" if v["test_only"] else ""
    L.append(f"| {k} | {v['sessions']} | {v['unscorable']} | {pct(v['far'])} | {pct(v['far_nodup'])} | {v['auc']:.2f} | {note} |")

L.append("\n## FRR by human group\n")
L.append("| group | sessions | people | strict FRR |\n|---|---|---|---|")
for g, r in per_group.items():
    warn = "  (fewer than 5 people: treat as a pilot)" if r["pids"] < 5 else ""
    L.append(f"| {g or 'unknown'} | {r['n']} | {r['pids']} | {pct(r)}{warn} |")

L.append("\n## Unseen attackers (retrained without that attacker)\n")
L.append("| attacker held out | FAR | AUC vs humans |\n|---|---|---|")
for k, v in unseen.items():
    L.append(f"| {k} | {pct(v['far'])} | {v['auc']:.2f} |")

L.append("\n## Which features carry the signal (AUC / EER, no threshold)\n")
L.append("| feature set | AUC | EER |\n|---|---|---|")
for k, (a, e) in feat_abl.items():
    L.append(f"| {k} | {a:.3f} | {e:.1%} |")

if model_cmp:
    L.append("\n## Model comparison (default features)\n")
    L.append("| model | AUC | EER |\n|---|---|---|")
    for k, (a, e) in model_cmp.items():
        L.append(f"| {k} | {a:.3f} | {e:.1%} |")
if timer_abl:
    L.append("\n## Timer precision (event times floored to a coarser clock)\n")
    L.append("| timer step | AUC with quantization correction | AUC without |\n|---|---|---|")
    for q in [0.0, 1.0, 5.0, 20.0, 100.0]:
        c, u = timer_abl[(q, True)][0], timer_abl[(q, False)][0]
        L.append(f"| {'native' if not q else f'{q:g} ms'} | {c:.3f} | {u:.3f} |")
    L.append(f"\nAbove {COARSE_STEP_MS:g} ms the API refuses to judge (inconclusive) instead of guessing.")

L.append("\n## Limits that belong in the paper\n")
L += [
    "- Timing alone cannot stop an attacker who replays recordings of many different real people; the near-duplicate rule only catches reuse.",
    "- Bot data comes from attackers we wrote. FAR is a lower bound for stronger attackers; the unseen-attacker table is the closest proxy for generalization.",
    "- Few participants: every interval above is wide. Report intervals, not point estimates.",
    "- Self-reported group labels; a 'low vision' row with few people is a pilot, not evidence of accessibility.",
]
(out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")

metrics = {"n_sessions": n, "far": far_all, "frr_strict": frr_strict, "frr_model": frr_model, "auc": auc, "eer": eer,
           "per_kind": per_kind,
           "per_group": per_group, "unseen": unseen, "synthetic": ds.synthetic,
           "confusion": {"human_accepted": H_acc, "human_not_accepted": H_rej,
                         "bot_accepted": B_acc, "bot_not_accepted": B_rej},
           "pass_within_3": {"passes": passes, "people": n_h}}
(out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
print(f"\ndone in {time.time() - t0:.0f}s -> {out}/report.md")
print("\n".join(L[3:16]))
