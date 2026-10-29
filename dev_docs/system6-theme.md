# Macintosh System 6 theme

Status 2026-10-29: **a basic working version** (View > Theme / Settings > Appearance > "Macintosh System 6"). Ported from JV Studio's
System 6 skin (`~/Development/jv-studio`, its `dev_docs/system6-theme.md` has the long story and what QSS cannot do). Seen only in
offscreen screenshots on macOS; not looked at on Windows or Linux.

## What exists

- `theme.SYSTEM6_PALETTE`, picked by the preference `"system6"` (`settings.THEME_SYSTEM6`), ignoring the OS light/dark scheme.
  Black, white and greys; warning orange and critical red stay (they carry meaning).
- **The shared stylesheet is untouched.** The palette's `square_corners` turns every `border-radius` in it into 0 at render time, and
  `style_system6.qss.template` (menus, 16 px scroll bars with arrow boxes and a dithered track, X check boxes, ruled combo/spin boxes,
  heavy default-button ring, bitmap font, 6 px push buttons) is appended for this palette only. Dark and light render as before
  (checked pixel for pixel against the last commit, main window with a disk open).
- Font **ChiKareGo2** (CC BY, credited in Help > Third-party notices and `ui/fonts/README.txt`), icons `ui/icons/s6_*.svg`; all three
  build scripts and `pysidedeploy.spec` ship `fonts/` and the extra template.
- Qt's own widgets that QSS doesn't draw take a black/white `QPalette` while the theme is on (`_apply_qt_palette`).
- Selected rows (tree, file table, lists) are inverted video: white text on black (fixed 2026-10-29 after the first version left the text black).
- Tests: `tests/test_theme.py`.

## To do, roughly in order

- [ ] **Volume colours.** The 8 categorical volume colours are 8 greys for now, which is hard to tell apart in the disk usage bar and
  the tree swatches. Hatch or dither patterns (painted in `usage_bar.py` and `volume_colors.py`) would do it properly.
- [ ] **Swatches on a selected row.** A selected tree row turns black, so a black or very dark volume swatch disappears on it.
- [ ] **Look at every window and dialog**: file table, details, import / copy / format dialogs, sample settings and its waveform,
  console dock, search results. Only the main window with a disk open has been looked at.
- [ ] **Coloured icons.** The partition and disk icons in the tree keep their colours (blue folders); black-and-white versions needed.
- [ ] **Painted widgets in 1-bit:** `waveform_preview`, `marker_waveform`, `usage_bar`, `splitter` without anti-aliasing.
- [ ] **Dense lists in a bitmap font:** check the file table and tree stay readable and don't clip at 16 px; maybe 13-14 px there.
- [ ] **Tooltips, progress bar, tab bar, menus** are styled but haven't been seen on a real screen (macOS keeps its native menu bar).
- [ ] **Screenshots / README:** none yet.
- [ ] **Windows and Linux:** check the font loads and the in-window menu bar looks right.

## Cannot be done (see JV Studio's notes)

A real System 6 window frame (native title bars stay), native file dialogs, dithered text, or Apple's own fonts and artwork.
