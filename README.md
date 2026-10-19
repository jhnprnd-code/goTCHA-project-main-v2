# goTCHA-project

## 1. Run program

```bash
pip install -r requirements.txt
playwright install chromium                         # only needed for the bots and e2e_collect.py
export GOTCHA_ADMIN_KEY="pick-something-long"       # only needed for labeled bot data
uvicorn backend_api:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

On Windows, `run_gotcha.bat` starts the server (set `GOTCHA_ADMIN_KEY` in your environment first if you will collect bot data).

`--no-access-log` disables Uvicorn access logs. Tunnel providers may separately process connection metadata under their own privacy policies.

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
