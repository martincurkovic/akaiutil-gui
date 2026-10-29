# Macintosh System 6 theme

Status 2026-10-29: **a working version, second pass done** (View > Theme / Settings > Appearance > "Macintosh System 6"). Ported from JV Studio's
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

## Done in the second pass (2026-10-29)

- **Volume colours** are hatching in the usage bar and the tree swatches (`volume_patterns`: Qt brush styles, black on white;
  `volume_colors.fill_volume` / `pattern_brush`), 8 patterns, the 9th volume starts over. The loop markers on the sample waveform
  still use the 8 greys (`volume_color_*`).
- **Tree icons** (disk, floppy, folder) are painted in black and white (`ui/mono_icons.py`, palette key `mono_icons`); a selected row
  keeps its icon as it is.
- **Selected rows** show white on black (tree, file table, lists, tabs).
- **Details pane** uses a 13 px font (`font_size_dense`) and waits for 1100 px before it goes to two columns
  (`details_two_columns_min_width`): at 16 px the zone table's headers broke mid-word ("Veloc ity").
- **Buttons** all have the same height (the font's `…` is taller than its line, so "Show Details…" came out taller), and the Allow-editing style dialog (`ThreeWayDialog`) is as wide as its buttons need ("Edit Without Backup" was squeezed and clipped). Its warning icon is drawn in black and white (`mono_icons.warning_pixmap`); `QMessageBox`es still show the style's coloured icons.
- **Message boxes** (`QMessageBox`: information, question, warning, critical) show black-and-white icons: `theme._Style`, a `QProxyStyle` over Fusion, hands them out while the theme is on (`mono_icons.message_icon`). Dark and light are unchanged (compared pixel for pixel).
- **Sliders** are black (the shared stylesheet's green is overridden) and **progress bars** show no percentage (it can't be read on the dither).
- **Links** in the manual and the notices are near black and underlined (palette key `link`). The About dialog's one link stays blue: Qt ignores the palette colour for a `QLabel` link here.
- **Marker waveform** looked at with a real waveform: readable; loop markers use dark greys, told apart by their numbers.
- **Anti-aliasing is off** in the painted widgets (`theme.antialiasing()`, palette key `hard_edges`).
- **Dialogs** (About, Settings, Rename, New Volume, Sample Settings, Copy Volume / Partition, Format, Import, Repair, the three-way
  question) were rendered offscreen: nothing clipped there. Dark and light still render pixel for pixel as before.

## To do, roughly in order

- [ ] **About dialog's link** is blue.
- [ ] **Real screens.** Everything above was looked at offscreen on a Mac at one scale. The owner reported some cut-off labels and
  button text in a couple of dialogs on a real screen: find which, and fix (the font is wider than the 13 px the layouts were made
  for; fixed-size dialogs are the likely ones).
- [ ] **Menus, tooltips and the tab bar** look right offscreen; not seen on a real screen (macOS keeps its native menu bar).
- [ ] **Screenshots / README:** none yet.
- [ ] **Windows and Linux:** check the font loads and the in-window menu bar looks right.

## Cannot be done (see JV Studio's notes)

A real System 6 window frame (native title bars stay), native file dialogs, dithered text, or Apple's own fonts and artwork.
