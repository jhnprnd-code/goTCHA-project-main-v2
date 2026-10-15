# goTCHA_project

## 1. Run program

pip install -r requirements.txt
export GOTCHA_ADMIN_KEY="pick-something-long"      # only needed for labeled bot data
uvicorn backend_api:app --host 0.0.0.0 --port 8000 --no-access-log
'--no-access-log' keeps IP addresses out of your logs.

Participants open 'http://<your-address>:8000/collect'. Ways to reach it:
- **In person (best for the low-vision participants):** run it on your own laptop and let participants play on the machine or on theirs over the same Wi-Fi ('http://<your-LAN-IP>:8000/collect'). You can watch their setup and note problems.
- **Remote:** a tunnel from your laptop, e.g. 'cloudflared tunnel --url http://localhost:8000' or `ngrok http 8000`. This gives an HTTPS link. Tunnel providers keep their own logs, so say so if your school asks.
- Keep your laptop awake and the server running while people play. A restart is safe: the registry reloads from 'consent.jsonl', but anyone mid-level has to redo that level.

## 2. What each participant does

Consent -> 3 questions -> practice level -> 3 timed levels (~20 s each) -> participant code. About 5 minutes. A level where the browser stuttered is flagged and redone automatically. Tell participants to use a normal setup, use a physical keyboard, keep the tab in front, play naturally, and not try to be perfect.

## 3. Quotas for 15 people

- Typical vision - 9 to 10  (baseline)
- Low vision / using a magnifier or zoom - 4 to 5 (the accessibility claim rests on these)
- Mixed browsers - at least 2 non-Chrome [timer resolution differs (Firefox is the one to look for)]
- If I can't reach 3 or more low-vision or magnifier users, the accessibility claim becomes "a pilot with n = ...".

## 4. Monitoring and withdrawals

- python data_status.py  # who has finished, stutter rate, timer resolutions seen
- python delete_participant.py P1A2B3C # withdrawal: removes that code from both files (restart the server after)
  
## 5. What is stored

- sessions.jsonl - one line per level: raw key events, level layout, telemetry, flags, participant code, group.
- consent.jsonl - participant code, consent version and time, self-reported answers. No names, no emails, no IPs.
- Bot data is labeled separately and needs 'GOTCHA_ADMIN_KEY'; the public page cannot create bot-labeled rows.

## 6. Known limits

- "Human" labels are self-attested by whoever opens the link. Share the link only with people you recruit.
- Telemetry is reported by the browser, so it is evidence for calibration, not proof.
- 'tests/e2e_collect.py' re-runs the whole flow in headless Chromium (about 2 minutes) using temporary files. Run it after any change.

