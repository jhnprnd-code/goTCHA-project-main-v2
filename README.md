# goTCHA

A game-based CAPTCHA that judges human vs. bot from SPACE-bar timing, plus the consent-based timing study used to build and evaluate its detector. The study deliberately includes low-vision participants, because image and text CAPTCHAs are hard or impossible for them.

**This is a research pilot, not a working security product.** The detector is weak (see [Results](#results)), and the study has 9 participants. Last updated 2026-10-20.

## Status

| Part | Status |
|---|---|
| Game widget and challenge/verify API | Working. Challenge, verify and siteverify endpoints, signed tokens, rate limits, a frame-quality check. |
| Timing study (data collection) | Finished and closed: 9 people completed (4 low vision, 4 typical vision, 1 prefer not to say), 27 timed sessions. The deletion-request window closed on 2026-10-21 and no further participants will take part. |
| Training and evaluation pipeline | Working end to end. |
| Detector | Pilot only. It rejected no human in the study, but accepted 85% of bot sessions when used alone. Not validated. |

## How it works

1. `GET /challenge` returns a seeded obstacle layout and a single-use nonce. A level lasts about 20 seconds: press SPACE to jump over red blocks.
2. The browser records only key-down and key-up times (plus frame-timing telemetry), and posts them to `POST /verify`.
3. The server replays the game physics against those timings. Hard rules reject impossible submissions (finished too fast, malformed or out-of-order events, too many events). A frame-quality gate asks a stuttering level to be replayed (capped per IP) rather than rejecting a human.
4. The verdict comes from the **trained model** if `backend/model.joblib` exists and loads, otherwise from a **rule-based placeholder** (`behavior()` in `backend/backend_api.py`). The trained model also runs a near-duplicate check that catches a recording being replayed. If the browser timer is coarser than 15 ms, the verdict is "inconclusive" instead of a guess.
5. On a human verdict the server returns a signed JWT (valid 120 s). The host site's backend redeems it once with `POST /siteverify` and its site secret.

### Embedding the widget

```html
<div id="gotcha-container"></div>
<script src="http://localhost:8000/gotcha-widget.js"></script>
<script>
  new GotchaWidget("gotcha-container", {
    apiBase: "http://localhost:8000",
    onSuccess: (token) => { /* send token to your backend */ },
  });
</script>
```

Your backend then calls `POST /siteverify` with `{"token": ..., "secret": <site secret>}`. The study page `frontend/collect.html` is a complete worked example.

## Run it

```bash
pip install -r requirements.txt
playwright install chromium        # only for the bots and the end-to-end test
export GOTCHA_ADMIN_KEY="pick-something-long"   # only needed to collect labeled bot data
uvicorn backend_api:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

Open `http://127.0.0.1:8000/collect`. On Windows, `run_gotcha.bat` does the same. `build_exe.bat` builds a single-file `dist\goTCHA.exe` that starts the server, opens the page, and keeps `sessions.jsonl`, `consent.jsonl` and `model.joblib` next to the `.exe` (`GOTCHA_PORT` changes the port). Set `GOTCHA_ADMIN_KEY` in the environment before starting either one if you will collect bot data.

### Configuration

| Variable | Purpose | Default |
|---|---|---|
| `GOTCHA_SECRET` | Signs tokens | Random per start (tokens die on restart) |
| `GOTCHA_SITE_SECRET` | Secret the host backend sends to `/siteverify` | `dev-site-secret` (demo only) |
| `GOTCHA_ORIGINS` | Allowed CORS origins, comma-separated | `http://localhost:9000,http://127.0.0.1:9000` (the launcher sets its own port) |
| `GOTCHA_ADMIN_KEY` | Allows bot-labeled data to be recorded | none: bot-labeled collection is disabled until you set it (see [Security notes](#security-notes)) |
| `GOTCHA_DEBUG` | `1` leaks verdict reasons to the client | off |
| `GOTCHA_DATA`, `GOTCHA_CONSENT` | Paths of `sessions.jsonl` and `consent.jsonl` | next to the backend (or the `.exe`) |
| `GOTCHA_MODEL`, `GOTCHA_THRESHOLD` | Model path; override the model's decision threshold | `backend/model.joblib`; the model's own |
| `GOTCHA_ALLOW_SYNTHETIC_MODEL` | `1` lets a synthetic model load. Pipeline tests only. | off |
| `GOTCHA_MAX_FRAME_GAP_MS`, `GOTCHA_MAX_SLOW_RATIO`, `GOTCHA_MIN_FPS` | Frame-quality gate (uncalibrated) | 500, 0.30, 10 |

## Timing Study

Each participant: consent, 3 setup questions, one practice level, three timed levels, then an anonymous participant code. About 5 minutes. The app stores no names, emails or IP addresses. The browser user-agent is reduced to browser name, major version and mobile / in-app flags before it is stored; records written before that change contain the full string. Participants must be 18 or older (the page enforces a checkbox, not verification). The consent form (version `2026-10-v5`) states that the project has had no formal IRB or ethics-committee review and that the app is a research pilot. Confirm with your instructor or school that recruitment is permitted before sharing a link.

Full procedure (recruiting, quotas, tunnels, monitoring, withdrawals): [COLLECTION.md](COLLECTION.md).

| File | Contents |
|---|---|
| `sessions.jsonl` | One line per level: raw key events, level layout, telemetry, flags, participant code, group. Also receives one unlabeled line per live `/verify` call. |
| `consent.jsonl` | Participant code, consent version and time, self-reported answers. |

Both are gitignored. Back them up and never commit them. To withdraw a participant, stop the server and run `python admin-and-tests/delete_participant.py <CODE>`.

## Detector and its Evaluation

**Model.** 18 timing features (spread, shape and drift of timing error and key-hold time; 28 are extracted, the default set uses 18) feed an L2 logistic regression with balanced classes. The decision threshold is the 5% quantile of participant-grouped out-of-fold human scores. The near-duplicate rule flags a session whose per-obstacle timing errors all agree within 15 ms (at least 5 obstacles) with an earlier session. "Replay" bots copy human sequences, so they are held out of training and only test the duplicate rule.

**Protocol.** Leave-one-participant-out cross-validation; the threshold is chosen inside each training fold; confidence intervals resample people, not sessions; a rate of exactly 0% or 100% gets a Wilson interval (marked `*`). A permutation test shuffles human/bot identities across whole participants and reruns the full protocol.

| Script | Purpose |
|---|---|
| `machine-learning/train.py` | Train a model on labeled sessions; writes `model.joblib` and a `.card.json` |
| `machine-learning/evaluate.py` | Confusion matrix, FAR/FRR with intervals, ROC, per-attacker, unseen-attacker and ablation tables; writes `results/` |
| `machine-learning/permutation_test.py` | Is the result better than chance at this sample size? |
| `machine-learning/diff_filter.py` | Compare a raw session file with the evaluation file in both directions; prints each file's SHA-256 |
| `admin-and-tests/freeze_data.py` | Copy the raw study files to a read-only folder with a checksum manifest; `--verify` checks files against it later, and `--card` checks which data a model was trained on |
| `admin-and-tests/consent_audit.py` | Which consent version each participant accepted, and how many completers were on an older one |
| `machine-learning/analyze_artifacts.py` | Does browser automation leave timing artifacts? |
| `admin-and-tests/bot_playwright.py`, `admin-and-tests/bot_api.py`, `admin-and-tests/bot_lib.py` | The scripted attackers: perfect, gauss, fitted, adaptive, replay |

```bash
python machine-learning/evaluate.py --data backend/sessions_model_training.jsonl --out results
python machine-learning/permutation_test.py --data backend/sessions_model_training.jsonl --perms 1000 --jobs -1 --out results/permutation.json
```

### Results

Evaluation file: 87 sessions, 27 human (9 people, 3 timed levels each) and 60 bot (5 attackers, 4 runs each, 3 timed levels). Details and plots are in `results/`.

| Measure | Result |
|---|---|
| Human sessions rejected | 0 of 27 (0 of 9 people). 95% upper bound for the true rate about 30% (Wilson, `[0.0%, 29.9%]`). |
| Bot sessions accepted, deployed system (model + duplicate rule) | 60% (36 of 60) |
| Bot sessions accepted, learned model alone | 85% (51 of 60) |
| AUC (leave-one-participant-out) | 0.72, EER 36% (optimistic: the EER picks its threshold on test scores) |
| Permutation test, AUC | p = 0.018 (about 0.054 after correcting for three tests) |
| Permutation test, balanced error, learned model alone | p = 0.079 (not distinguishable from chance at the threshold used) |
| Permutation test, balanced error, deployed system | p = 0.003, mostly from the duplicate rule, which uses no labels |

| Attacker | Accepted by deployed system | Accepted by model alone |
|---|---|---|
| perfect | 8.3% | 83.3% |
| gauss | 75.0% | 75.0% |
| adaptive | 75.0% | 75.0% |
| fitted | 91.7% | 91.7% |
| replay (test-only) | 50.0% | 100.0% |

How to read this:

- The duplicate rule, not the model, stops the perfect bot, and it caused 15 of the 24 bot rejections. Adding noise defeats it.
- The model's signal comes mostly from key-hold time (without those features, AUC falls to 0.53), and key-hold time is also how our bots were built. Retrained without an attacker, three of four held-out attackers were accepted 83-100% of the time.
- The false-reject figure is not evidence of accuracy. The threshold targets 5% false rejections on training scores, which is lenient enough to accept most bots.
- With 4 low-vision participants, the accessibility result is a pilot: no evidence of a problem, but far too small to prove accessibility.

### Data provenance

The evaluation file `backend/sessions_model_training.jsonl` (not tracked) is the 37 human records from `sessions.jsonl` plus 80 bot records (60 timed, 20 practice) from `sessions_bots.jsonl`. All 117 records are valid and labeled. An earlier, larger raw log is no longer available, and the bot records were extracted from the evaluation file itself, so the `results/diff_filter_*.txt` comparisons are consistency checks, not independent verification that nothing was excluded. Treat every figure as describing the evaluation file as it stands. `train.py`, `evaluate.py` and `permutation_test.py` now record the SHA-256 of the file they read, which ties a figure to one version of the data; the model card, `report.md` and `metrics.json` carry it since 2026-10-21, while `permutation.json` does not until it is rerun. `results/PROVENANCE.md` sets out what the data can and cannot show, and `results/CONSENT_VERSIONS.md` compares the five consent form versions used on the first day.

## Model status and deployment

`backend/model.joblib` is never committed (`*.joblib` is gitignored), so a fresh clone runs the rule-based placeholder. `backend/model.joblib.card.json` describes the pilot model trained on 2026-10-20: not synthetic, 27 human and 48 bot sessions, threshold 0.140, grouped out-of-fold AUC 0.723. A model trained on synthetic data refuses to load unless `GOTCHA_ALLOW_SYNTHETIC_MODEL=1`.

Train, check, roll back:

```bash
python machine-learning/train.py --data backend/sessions_model_training.jsonl --out backend/model.joblib

# confirm it loads ("NOT LOADED" means the placeholder is still in use)
python -c "import sys; sys.path.insert(0,'backend'); from model_runtime import load_model; from pathlib import Path; rt=load_model(Path('backend/model.joblib')); print(rt.describe() if rt else 'NOT LOADED')"
```

Restart the server after training. To roll back, delete `backend/model.joblib` and restart. Retrain after any change to the data (for example a late deletion request), because the model contains every participant's data; with unchanged data, training reproduces the same threshold and AUC. The pilot model is not validated; expect the results above, not a working CAPTCHA.

## Security notes

- **Not a security product.** Sample attackers get through, so it must not guard anything that is a sophisticated deployed product.
- **There is no default admin key.** Bot-labeled collection is disabled until you set `GOTCHA_ADMIN_KEY`, and `run_gotcha.bat` and the launcher print a notice when it is unset. An earlier default was committed to this repository and stays in git history, so never reuse it: pick a new long random value. Anyone who has the key can submit bot-labeled rows to your dataset.
- Use real `GOTCHA_SECRET` and `GOTCHA_SITE_SECRET` values outside a demo, and leave `GOTCHA_DEBUG` off.
- Telemetry comes from the browser, so it is evidence for calibration, not proof. "Human" labels are self-attested by whoever opens the study link, so share it only with people you recruit.
- Do not enable `GOTCHA_ALLOW_SYNTHETIC_MODEL` for real participants.

## Repository layout

```
backend/            backend_api.py (API), gamecore.py (game physics), model_runtime.py (model loader), model card
frontend/           gotcha-widget.js (embeddable widget), collect.html (study page)
machine-learning/   features.py, ml_common.py, provenance.py, train.py, evaluate.py, permutation_test.py, diff_filter.py, analyze_artifacts.py
admin-and-tests/    bots, data tools (data_status.py, delete_participant.py, freeze_data.py, consent_audit.py, gotcha_tools.py), synth_data.py, e2e_collect.py
results/            evaluation outputs (report.md, metrics.json, permutation.json, plots, diff_filter_*.txt), PROVENANCE.md, CONSENT_VERSIONS.md
```

Also: `gotcha_launcher.py`, `run_gotcha.bat`, `build_exe.bat`, `COLLECTION.md`, `CONTRIBUTING.md` (commit-message rules and the `.githooks/commit-msg` check), `requirements.txt`, and an older source snapshot, `goTCHA-project-main-v2-source.zip`, that is not kept in sync with the tracked files.

## Tests

`python admin-and-tests/e2e_collect.py` replays the whole collection flow in headless Chromium (about 2 minutes) on port 8100 with temporary files, so it never touches your real data. Run it after any change. `python admin-and-tests/test_coarse_ua.py` checks that the stored user-agent keeps only the browser name and version (no device model). `python admin-and-tests/test_provenance_tools.py` checks the checksum, freeze and consent-audit tools on temporary files. `admin-and-tests/synth_data.py` generates synthetic sessions for pipeline tests only: anything computed from them is circular and must not be reported as a result.
