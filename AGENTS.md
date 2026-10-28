# Agent notes for akaiutil-GUI

Cold-start context for AI agents. Human docs: `README.md`, `BUILDING.md`, `CONTRIBUTING.md`. Long-form notes and plans: `dev_docs/`
(start at `dev_docs/README.md`; `ROADMAP.md` lists what is next and what has not been checked on a real sampler).

## House rules

- **Never commit, branch, tag or push unless asked.** The owner handles git: a commit is where the human checks the code. Agents may `git add` the files they changed, and should suggest a single-sentence commit message. Say when a good point to commit has been reached, but leave the commit to the owner. Ask before anything outward-facing.
- Never edit akaiutil's source (`akaiutil-4.6.8/` is git-ignored and re-fetched by `scripts/get_akaiutil.py`). Work around it in the GUI and record the quirk in `dev_docs/akaiutil-notes.md`.
- Keep user-facing text short (a line or two). Detail goes in the log.
- Record what was measured versus guessed, dated, and on what (a real sampler, a test image). Unchecked claims go in the roadmap's verification debt.
- Tests: `uv run pytest tests/ -q`. Fetch akaiutil first with `uv run python scripts/get_akaiutil.py`; most tests drive the real program and skip without it.
