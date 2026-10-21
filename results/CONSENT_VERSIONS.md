# Consent form versions

The consent form went through five version numbers on 2026-10-15, the first day of the repository. Four of them were committed. `consent.jsonl` records the version each participant accepted, so two things can be checked:

1. **What each version said**, which is recovered below from the git history.
2. **Which version each participant accepted**, which only the data can answer. Run `python admin-and-tests/consent_audit.py` on the machine that holds `backend/consent.jsonl` and `backend/sessions.jsonl`. Its by-version summary has no participant codes in it and can be added to this file; its per-participant table has codes and should not be committed.

## Versions

All times are 2026-10-15, UTC+8. A commit time says when the text was saved to the repository, not when participants could first see it.

| Version | Commit | Time | What it is |
|---|---|---|---|
| `2026-10-v1` | `e55f4a0` | 13:13 | Original form ("Timing study: the goTCHA game"). |
| `2026-10-v2` | none | n/a | **Never committed. Its text cannot be recovered.** A participant who accepted v2 cannot be compared against anything. |
| `2026-10-v3` | `9b7f6f2` | 16:42 | Adds the tunnel-provider disclosure and an ethics statement. |
| `2026-10-v4` | `f806e19` | 18:10 | New title; ethics statement moved to its own paragraph. No change of substance. |
| `2026-10-v5` | `ca930a4` | 18:49 | Adds a "Prototype limitation" paragraph. This is the version at the end of the study. |

The server rejects a registration whose version is not its current `CONSENT_VERSION` (HTTP 409), so the version stored for a participant is the one the server was running when they registered. That is a record of the server's setting, and it is the same as the text on screen only if `frontend/collect.html` and `backend/backend_api.py` were deployed together, as `COLLECTION.md` requires.

## What changed

**v1 to v3** (the substantive step):

| Passage | v1 | v3 |
|---|---|---|
| IP addresses | "The study server is set up not to save IP addresses with your data." | "IP addresses are not written to the study data files. If you open this page through a third-party tunnel, its provider may process your IP address and connection metadata under its own privacy policy." |
| Withdrawal deadline | "before OCTOBER 20, 2026" | "before October 21, 2026" |
| Ethics | "Approval reference: [APPROVAL / ETHICS REFERENCE]." The bracketed template field was never filled in. | "Ethics Status: This is an independent undergraduate course assignment. It has not been formally reviewed by an Institutional Review Board (IRB) or ethics committee. Participation is strictly voluntary, and you are evaluating a student-built prototype." |
| Course description | "For a course project at Mapúa Malayan Colleges Mindanao, BS in Computer Science, Year 2." | "For a second-year undergraduate course project in the BS Computer Science program at Mapúa Malayan Colleges Mindanao." |

**v3 to v4:** the title becomes "goTCHA: Behavioral-focused CAPTCHA for low-vision users", the researcher's name is written in full, and the ethics statement gets its own line. The wording of the statement is unchanged.

**v4 to v5:** adds this paragraph:

> PROTOTYPE LIMITATION. This is a research pilot, not a working security service. The bundled statistical detector was trained on synthetic data and is disabled; an unvalidated rule-based placeholder handles decisions instead. Do not rely on goTCHA to protect accounts, personal information, or other services.

Everything else (what is recorded, the 5-minute task, voluntary participation, the 18+ confirmation, who may see the anonymous data) is the same in all four recovered versions.

## What a participant on an older version was not told

| Accepted | Not told |
|---|---|
| v1 | That a tunnel provider may process their IP address. That the project has not been reviewed by an IRB or ethics committee. That it is a student prototype. The form instead shows an unfilled "Approval reference" field, which reads as if an approval exists. Their stated deadline was October 20, not October 21. |
| v3, v4 | That the tool is a research pilot and not a working security service, and that the detector is disabled. |
| v5 | Nothing that the later changes add. |

The v5 statement about the detector described the system when participants registered. If a trained model is installed on the server later, the statement stops being true and the version needs a bump (`COLLECTION.md`, section 0).

## If completers accepted an older version

The audit prints a line such as "1 of 9 completers agreed to a version other than 2026-10-v5". What to do about it:

- Report the count by version wherever the sample is described. It is a fact about the data, not something to bury in a footnote.
- For anyone on v1 or v3, tell them in person what the later forms added and offer deletion. The withdrawal deadline has passed, but `COLLECTION.md` already treats a late request as one to honor, and these participants were shown less than the final form. Deleting means `delete_participant.py`, then regenerating everything derived from the data.
- Do not rewrite the stored version to `2026-10-v5` to make the table tidy. That would erase the one record that shows what they actually agreed to.
