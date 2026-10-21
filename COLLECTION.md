# Running data collection

> **Status (October 2026): collection is finished and closed.** 12 people registered and 9 completed (4 low vision, 4 typical vision, 1 prefer not to say), giving 27 timed sessions. Two registrants never started a level, and one opened the link in a phone's in-app browser with no physical spacebar and dropped out. The deletion-request window closed on 2026-10-21 and no further participants will take part. The rest of this file is the procedure that was used, kept for anyone repeating the study.

## 0. BEFORE ANYONE PLAYS

1. The consent form is version '2026-10-v5'. It states that the project has not been formally reviewed by an IRB or ethics committee, that the app is a pilot rather than a working security service, that participation is voluntary, and that a third-party tunnel provider may process visitors' IP addresses and connection metadata. Confirm with your instructor or school whether participant recruitment is permitted for this assignment before sharing the link.
2. The form gives October 21, 2026 as the data-deletion request deadline. Confirm any extension directly with the researcher. If the consent wording changes, bump 'CONSENT_VERSION' in both 'frontend/collect.html' and 'backend/backend_api.py'.
3. Participants must be 18 or older. The page enforces a checkbox, not verification.

## 1. Run the program

```bash
pip install -r requirements.txt
export GOTCHA_ADMIN_KEY="pick-something-long"      # only needed for labeled bot data; there is no default
uvicorn backend_api:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

`--no-access-log` disables Uvicorn access logs. The application does not write visitors' IP addresses to study records; tunnel providers may separately process connection metadata under their own privacy policies.

Participants open 'http://YOUR_ADDRESS:8000/collect'. Ways to reach it:

- **In person (best for the low-vision participants):** run it on laptop and let participants play on the machine or on theirs over the same Wi-Fi (`http://<your-LAN-IP>:8000/collect`). You can watch their setup and note problems.
- **Remote:** a tunnel from your laptop, e.g. `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`. This gives an HTTPS link. The server is bound to localhost so it is not also exposed directly to your LAN.

- Keep your laptop awake and the server running while people play. A restart is safe: the registry reloads from 'consent.jsonl', but anyone mid-level has to redo that level.

## 2. What each participant does

Consent -> 3 questions -> practice level -> 3 timed levels (~20 s each) -> participant code. About 5 minutes. A level where the browser stuttered is flagged and redone automatically. Tell participants to use a normal setup, use a physical keyboard, keep the tab in front, play naturally, and not try to be perfect.

A **physical keyboard is required**. In the October 2026 run, one registrant opened the link inside a phone's in-app browser, had no spacebar, and dropped out. Say so up front, and ask people to open the link in a normal desktop browser.

## 3. Recruitment targets and outcome

| | Planned (15 people) | Completed (9 people) |
|---|---|---|
| Typical vision | 9 to 10 (baseline) | 4 |
| Low vision / magnifier or zoom | 4 to 5 (the accessibility claim rests on these) | 4 |
| Prefer not to say | n/a | 1 |
| Mixed browsers | at least 2 non-Chrome (timer resolution differs; Firefox is the one to look for) | check `data_status.py` for the timer resolutions seen |

The accessibility claim therefore rests on 4 low-vision participants: a pilot with n = 4, not evidence of accessibility.

## 4. Monitoring and withdrawals

- `python admin-and-tests/data_status.py` — who has finished, stutter rate, timer resolutions seen
- Stop the server, then run `python admin-and-tests/delete_participant.py P1A2B3C` to remove that participant's records; restart the server afterward.
- Anything derived from the data (merged files, bots built from the human pool, the model, the evaluation and the permutation test) must be regenerated after a deletion.
- The withdrawal window has closed. A late deletion request is still a request to honor: delete the records, then regenerate everything derived from them.

## 5. What is stored

- sessions.jsonl - one line per level: raw key events, level layout, telemetry, flags, participant code, group. Telemetry includes the browser as name, major version and mobile / in-app flags. Records written before that reduction was added contain the full user-agent string, which can include a phone model.
- consent.jsonl - participant code, consent version and time, self-reported answers. No names, no emails, no IPs.
- Every live `/verify` call also appends an unlabeled line to sessions.jsonl. The evaluation ignores those lines, but they change the file's line count.
- Back both up after each day. Do not commit them to a public repository.
- Bot data is labeled separately and needs 'GOTCHA_ADMIN_KEY'; the public page cannot create bot-labeled rows.

## 6. Keep the original data safe

The October 2026 evaluation could not independently verify what was excluded before the training file was assembled, because the original raw backup had been lost. To avoid that:

1. **Before** merging, filtering or splitting anything, stop the server and freeze the raw files untouched:
   `python admin-and-tests/freeze_data.py --dest <folder outside the project> --manifest results/data_manifest.json`
   It copies `sessions.jsonl`, `consent.jsonl` and the other study files found in `backend/`, checks each copy byte for byte, marks the copies read-only, and records a SHA-256, line count and size for each in `MANIFEST.json`. It never overwrites a different file already in the destination. The manifest holds no data and can be committed.
2. Build every derived file (merged, bot-split, training) **from the frozen copy**, never by editing the original in place.
3. Check each derived file against the original:
   `python machine-learning/diff_filter.py --raw <frozen sessions.jsonl> --kept <derived file>`
   DROPPED lists what was excluded (it should be explainable: unlabeled traffic, invalid levels). ADDED should contain only what you merged in on purpose. The verdict line summarizes how the files relate, and the report shows each file's SHA-256.
4. `train.py` (model card), `evaluate.py` (`report.md`, `metrics.json`) and `permutation_test.py` (`permutation.json`) record the SHA-256 of the file they read. Quote that value next to any number you report, and check it against the manifest:
   `python admin-and-tests/freeze_data.py --verify results/data_manifest.json --dir backend`
5. Before reporting how many participants completed, check which consent version each accepted:
   `python admin-and-tests/consent_audit.py`
   `results/CONSENT_VERSIONS.md` compares the texts; `results/PROVENANCE.md` records what the October 2026 data can and cannot show.

## 7. Known limits of this harness

- "Human" labels are self-attested by whoever opens the link. Share the link only with people you recruit.
- Telemetry is reported by the browser, so it is evidence for calibration, not proof.
- The game needs a physical keyboard and the ability to see the moving blocks.
- 'admin-and-tests/e2e_collect.py' re-runs the whole flow in headless Chromium (about 2 minutes) using temporary files. Run it after any change.
- 'admin-and-tests/test_coarse_ua.py' checks that the stored user-agent keeps only the browser name and version.
- 'admin-and-tests/test_provenance_tools.py' checks the checksum, freeze and consent-audit tools on temporary files.
