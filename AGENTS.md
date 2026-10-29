# Agent notes for akaiutil-GUI

Cold-start context for AI agents. Human docs: `README.md`, `BUILDING.md`, `CONTRIBUTING.md`. Long-form notes and plans: `dev_docs/`
(start at `dev_docs/README.md`; `ROADMAP.md` lists what is next and what has not been checked on a real sampler).

## House rules

- **Never commit, branch, tag or push unless asked.** The owner handles git: a commit is where the human checks the code. Agents may `git add` the files they changed, and should suggest a single-sentence commit message. Say when a good point to commit has been reached, but leave the commit to the owner. Ask before anything outward-facing.
- Never edit akaiutil's source (`akaiutil-4.6.8/` is git-ignored and re-fetched by `scripts/get_akaiutil.py`). Work around it in the GUI and record the quirk in `dev_docs/akaiutil-notes.md`.
- Keep user-facing text short (a line or two). Detail goes in the log.
- Record what was measured versus guessed, dated, and on what (a real sampler, a test image). Unchecked claims go in the roadmap's verification debt.
- Tests: `uv run pytest tests/ -q`. Fetch akaiutil first with `uv run python scripts/get_akaiutil.py`; most tests drive the real program and skip without it.

## macOS DMG

macOS DMG (2026-10-29, ported from AKAISDS; the build was not run here, but `dmgbuild` was run on a dummy .app with the real settings and the result mounted with its background): `scripts/build_macos.sh` also writes `LSMinimumSystemVersion` 14.0 into the bundle's Info.plist and re-signs ad hoc (editing the plist alone breaks the seal), then builds `akaiutil-GUI-macos-{arm,intel}.dmg` at the repo root with `dmgbuild` (a `build` group dependency): layout `scripts/dmg_settings.py`, background `assets/dmg/dmg_background.tiff` (regenerate on a Mac with `uv run --with pillow python assets/dmg/create_dmg_background.py`; keep its window size and icon positions in step with the settings file, `tests` check that). CI no longer runs a bare `hdiutil`; it uploads the script's DMG. The background is a mid-tone slate: Finder draws icon names near-black whatever the appearance, and a DMG window has one background picture (AKAISDS's notes have the dark-mode finding).
