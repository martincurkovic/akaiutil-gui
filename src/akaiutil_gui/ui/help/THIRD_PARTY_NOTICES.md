# Third-party notices

akaiutil-gui is free software, licensed under the GNU General Public License,
version 2 or (at your option) any later version. The full text is in the `LICENSE`
file shipped with the app. It includes or uses the following third-party software.

## akaiutil

The app contains a compiled copy of **akaiutil 4.6.8** and does all of its work by
running it as a separate program.

- Copyright (C) 2008-2025 Klaus Michael Indlekofer. All rights reserved.
- Licensed under the GNU General Public License, version 2 or (at your option) any
  later version (see `LICENSE`). There is no warranty.
- Included **unmodified**: built from the original source release, whose SHA-256 is
  `48ea733cc38d90c2f9f70a6ff9c4fb12a8e64ddd7024d4b436e9ebeef2bf57b1`.
- Project page: <https://sourceforge.net/projects/akaiutil/>
- **Source code:** the exact source this copy was built from is attached to every
  release of akaiutil-gui on GitHub as `SOURCE-CODE-ONLY-akaiutil-4.6.8.tar.gz`, and can also be
  downloaded from SourceForge:
  <https://sourceforge.net/projects/akaiutil/files/akaiutil-4.6.8.tar.gz/download>

The name akaiutil is used here only to say what the program is. This project is not
affiliated with or endorsed by its author. "Akai" and the names of Akai samplers are
trademarks of their respective owners.

## Qt and PySide6

The user interface uses **Qt 6** through **Qt for Python (PySide6)**, both
Copyright (C) The Qt Company Ltd. and other contributors. They are used under the
GNU Lesser General Public License, version 3 (and other licences offered by the
Qt Company, see <https://www.qt.io/licensing/>), as unmodified shared libraries
that are shipped alongside the app and can be replaced with other builds of the
same version. Source code: <https://code.qt.io/> and <https://pypi.org/project/PySide6/>.

## Audio preview: miniaudio, soundfile, libsndfile and NumPy

- **miniaudio** (the Python package by Irmen de Jong, wrapping David Reid's C library of the same name) plays the
  preview through the computer's own audio system. MIT licence (the C library is also available as public domain).
- **soundfile** reads the WAV files akaiutil exports. BSD 3-Clause licence. It uses **libsndfile**
  (Copyright Erik de Castro Lopo and contributors), licensed under the GNU Lesser General Public License 2.1 or later,
  shipped as an unmodified shared library that can be replaced with another build of the same version.
  Source: <https://github.com/libsndfile/libsndfile>
- **NumPy** does the waveform and sample arithmetic. BSD 3-Clause licence, with the licences of the libraries bundled in its
  wheels listed in the package's own `LICENSE.txt`.

## Python

The app is written in Python and ships with a Python runtime, which is
Copyright (C) the Python Software Foundation and licensed under the
Python Software Foundation License, see <https://docs.python.org/3/license.html>.

## Nuitka

The standalone builds are produced with **Nuitka** (Apache License 2.0),
<https://nuitka.net/>, whose small runtime is part of the built app.
