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
'--no-access-log' disables Uvicorn access logs. Tunnel providers may separately process connection metadata under their own privacy policies.

The consent form is version `2026-10-v5`; it states that this course project has not received formal IRB or ethics-committee review and that the app is a research pilot, not a working security service. Confirm recruitment is permitted with your instructor or school before sharing the link. Participants open `http://127.0.0.1:8000/collect` on the server computer or use the HTTPS URL printed by the tunnel. Ways to reach it:
- **In person (best for the low-vision participants):** run it on your laptop and let participants play on that machine. You can watch their setup and note problems.
- **Remote:** a tunnel from your laptop, e.g. `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`. This gives an HTTPS link. The server is bound to localhost so it is not also exposed directly to your LAN.
- Keep your laptop awake and the server running while people play. A restart is safe: the registry reloads from 'consent.jsonl', but anyone mid-level has to redo that level.

## 2. What each participant does

Consent -> 3 questions -> practice level -> 3 timed levels (~20 s each) -> participant code. About 5 minutes. A level where the browser stuttered is flagged and redone automatically. Tell participants to use a normal setup, use a physical keyboard, keep the tab in front, play naturally, and not try to be perfect.

## 3. Quotas for 15 people

- Typical vision - 9 to 10  (baseline)
- Low vision / using a magnifier or zoom - 4 to 5 (the accessibility claim rests on these)
- Mixed browsers - at least 2 non-Chrome [timer resolution differs (Firefox is the one to look for)]
- If I can't reach 3 or more low-vision or magnifier users, the accessibility claim becomes "a pilot with n = ...".

## 4. Monitoring and withdrawals

- `python admin-and-tests/data_status.py` — who has finished, stutter rate, timer resolutions seen
- Stop the server, then run `python admin-and-tests/delete_participant.py P1A2B3C` to remove that participant's records; restart the server afterward.
  
## 5. What is stored

- sessions.jsonl - one line per level: raw key events, level layout, telemetry, flags, participant code, group.
- consent.jsonl - participant code, consent version and time, self-reported answers. The application does not write participant IPs to these study files; tunnel providers may process connection metadata under their own privacy policies.
- Bot data is labeled separately and needs 'GOTCHA_ADMIN_KEY'; the public page cannot create bot-labeled rows.

## 6. Known limits

- The bundled model card identifies the current model as synthetic. The API refuses to load it by default and uses its rule-based placeholder instead; this is not a validated CAPTCHA detector. Do not enable `GOTCHA_ALLOW_SYNTHETIC_MODEL` for real participants.
- "Human" labels are self-attested by whoever opens the link. Share the link only with people you recruit.
- Telemetry is reported by the browser, so it is evidence for calibration, not proof.
- 'admin-and-tests/e2e_collect.py' re-runs the whole flow in headless Chromium using temporary files. Run it after any change.
