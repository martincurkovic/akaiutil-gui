# dev_docs

Notes for whoever works on akaiutil-GUI next (usually future me).

- [ARCHITECTURE.md](ARCHITECTURE.md): how the app talks to akaiutil and how the code is laid out.
- [ROADMAP.md](ROADMAP.md): what's next, what hasn't been verified by hand, and the known limits.
- [program-editor-plan.md](program-editor-plan.md): plan for editing programs and keygroups in disk images, and sharing code with AKAISDS.
- [hfe-plan.md](hfe-plan.md): plan and findings for opening and saving `.hfe` floppy images by converting to and from a flat image (built: `hfe.py` is the codec, `hfe_work.py` the working copies; what's unchecked is listed there).
- [akaiutil-notes.md](akaiutil-notes.md): how akaiutil actually behaves, as found by trying it (not all of it is documented), and what the GUI does about each.

User-facing docs live in the top-level [README.md](../README.md) and [BUILDING.md](../BUILDING.md).
- Logging: `logsetup.py` writes `logs/akaiutil-gui.log` (1 MB x 4) in the app data folder, plus `crash.log` from `faulthandler`.
  Modules just use `logging.getLogger(__name__)`; INFO says what the app did, DEBUG (Settings > Troubleshooting) adds every
  akaiutil command and the start of its answer. `ui/tasks.py` logs every failed worker task (a one-line warning for the
  `EngineError`/`BackupError` reasons shown to the user, a traceback for anything else). Tests point `AKAIUTIL_GUI_LOG_DIR` and
  `AKAIUTIL_GUI_BACKUP_DIR` at temp folders (tests/conftest.py) so nothing lands in the real app data folder.
