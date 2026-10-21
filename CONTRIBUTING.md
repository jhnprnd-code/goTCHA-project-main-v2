# Contributing

## Commit messages

- Subject line in the imperative ("Add", "Fix", "Remove"), no trailing period, 72 characters or fewer.
- Optional area prefix when it helps: `ml:`, `backend:`, `frontend:`, `tools:`, `results:`, `docs:`. Example: `tools: add freeze_data.py`.
- Write **only the message**. Type `Add the freeze tool`, not `git commit -m "Add the freeze tool"`. In an IDE's commit box the box is the message; there is no command to type.
- Use the body (after a blank line) for why the change was made, not what changed. Wrap at about 72 characters.
- One logical change per commit. If the subject needs "and", it is probably two commits.

In a terminal the command is `git commit -m "Add the freeze tool"`, and the log then shows `Add the freeze tool`.

## Install the commit-message check (once per clone)

```
git config core.hooksPath .githooks
```

The hook in `.githooks/commit-msg` rejects a message whose first line starts with `git`, which is what a command pasted into a commit box looks like. It works in a terminal and in VS Code's Source Control box; if you use a different Git client, try a bad message once to confirm it is checked. Git for Windows runs the hook with its bundled `sh`, so nothing else needs installing. To skip the check once: `git commit --no-verify`.

The check is local. Edits made on github.com and clones that have not run the command above are not covered.

## Pull requests

Pull requests are squash-merged, so the PR title becomes the commit message on `main`. Write it by the same rules. Avoid committing straight to `main`; the commits with pasted-command messages in the history were all direct commits.

Never commit participant data (`*.jsonl`, `*.joblib`, `consent.jsonl`). They are gitignored; see `COLLECTION.md`.
