# How it works

The GUI starts `akaiutil -r <images>` (without the `-r` once editing is allowed) as a subprocess and types commands into its prompt, using the
`<cwd> >` prompt as the end-of-response marker. Fixed-width listings (`dir`, `dinfo`, …) are parsed in
`engine/parse.py`; everything above that sees typed objects (`engine/models.py`, `engine/api.py`).

```text
src/akaiutil_gui/
  describe.py   akaiutil's info text -> readable sections (Qt-free; goes away with a ctypes engine)
  engine/       session.py (subprocess + prompt protocol) · parse.py · api.py (AkaiEngine) · models.py · locate.py
  ui/           main_window.py · disk_tree.py · file_table.py · tasks.py (worker threads) · theme.py + style.qss.template
```

Things worth knowing:

- `engine/api.py` is the seam for the planned move to a shared library (ctypes/cffi): a new engine just needs the same methods and return types. `parse.py` is the part that goes away.
- akaiutil writes exports into *its own* working directory and can't take paths with spaces, so it runs in a scratch dir and Python moves the results to your chosen folder (asking before overwriting).
- Names with spaces: akaiutil accepts `_` for ` ` on its command line, but the GUI avoids that ambiguity by addressing volumes and files by index (`cdi`, `geti`, …).
- Direct-to-disk takes are listed, exported, imported, renamed and deleted (`engine/api.py`, `takes.py`, `ui/take_table.py`; see [dd-takes-plan.md](dd-takes-plan.md)). Not yet: real floppy/physical-drive access.
