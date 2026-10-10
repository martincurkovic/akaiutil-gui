"""The build setup (workflow, scripts, assets) agrees with itself and with the code.

None of this runs the builds, which only CI can really do; it catches the mistakes that make a
build fail late or, worse, "succeed" and ship an app that's missing a file: a renamed asset, a
bundled data file that doesn't exist, an app name that differs between the workflow and the scripts.
"""

import re
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
SCRIPTS = {name: (ROOT / "scripts" / name).read_text(encoding="utf-8") for name in ("build_macos.sh", "build_linux.sh", "build_windows.ps1")}


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def app_name(workflow):
    return workflow["env"]["APP_NAME"]


def steps(job):
    return job["steps"]


def run_text(job) -> str:
    return "\n".join(s.get("run", "") for s in steps(job))


# ------------------------------------------------------------------- workflow
def test_the_job_graph_matches_akaisds(workflow):
    jobs = workflow["jobs"]
    assert set(jobs) == {"test", "build-macos-arm", "build-macos-intel", "build-linux", "build-windows", "release"}
    for name in ("build-macos-arm", "build-macos-intel", "build-linux", "build-windows"):
        assert jobs[name]["needs"] == "test"
    assert set(jobs["release"]["needs"]) == {"build-macos-arm", "build-macos-intel", "build-linux", "build-windows"}
    assert jobs["release"]["if"] == "github.ref_type == 'tag'"
    assert jobs["release"]["permissions"] == {"contents": "write"}
    # `on:` parses as the boolean True in YAML 1.1
    trigger = workflow.get("on", workflow.get(True))
    assert trigger["push"]["tags"] == ["v*.*.*"] and "workflow_dispatch" in trigger


def test_tests_run_against_a_real_akaiutil(workflow):
    commands = [s.get("run", "") for s in steps(workflow["jobs"]["test"])]
    fetch = next(i for i, c in enumerate(commands) if "scripts/get_akaiutil.py" in c)
    pytest_run = next(i for i, c in enumerate(commands) if "pytest" in c)
    assert fetch < pytest_run, "akaiutil must be fetched before pytest, or most tests silently skip"


def test_every_build_smoke_tests_the_app_it_just_built(workflow):
    for name in ("build-macos-arm", "build-macos-intel", "build-linux", "build-windows"):
        text = run_text(workflow["jobs"][name])
        assert "--smoke-test" in text, name
        assert text.index("--smoke-test") > text.index("build_"), f"{name}: the smoke test must come after the build"


def test_the_release_ships_akaiutils_source(workflow):
    release = workflow["jobs"]["release"]
    assert any(s.get("uses", "").startswith("actions/checkout") for s in steps(release))
    assert "--source-only" in run_text(release)
    assert "release-files/**/*" in str(steps(release)[-1])  # ...into the folder the release uploads from


def test_files_the_workflow_refers_to_exist(workflow, app_name):
    text = "\n".join(l for l in WORKFLOW.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))
    text = text.replace("${{ env.APP_NAME }}", app_name).replace("$env:APP_NAME", app_name).replace("$APP_NAME", app_name)
    paths = set(re.findall(r"(?:scripts|assets)[/\\][\w./\\-]+", text))
    assert paths
    for p in paths:
        p = p.rstrip("\\")
        if p.endswith(("/", "-")):
            continue
        assert (ROOT / p.replace("\\", "/")).exists(), f"build.yml refers to {p}, which doesn't exist"


# --------------------------------------------------- one app name everywhere
def test_the_app_name_is_the_same_in_every_place_it_appears(app_name):
    assert f'APP_NAME="{app_name}"' in SCRIPTS["build_macos.sh"]
    assert f'APP_NAME="{app_name}"' in SCRIPTS["build_linux.sh"]
    assert f'$AppName = "{app_name}"' in SCRIPTS["build_windows.ps1"]
    iss = (ROOT / "assets" / "windows" / f"{app_name}.iss").read_text(encoding="utf-8")
    assert f'#define MyAppName "{app_name}"' in iss
    assert f"OutputBaseFilename={app_name}-Setup" in iss
    assert f'Source: "..\\..\\src\\{app_name}.dist\\*"' in iss  # where Nuitka puts the Windows build
    desktop = (ROOT / "assets" / "linux" / f"{app_name}.desktop").read_text(encoding="utf-8")
    assert f"Name={app_name}" in desktop
    assert (ROOT / "assets" / "icon" / f"{app_name}-macos.icns").is_file()


def test_the_linux_desktop_file_matches_how_the_appimage_is_built(workflow):
    desktop = (ROOT / "assets" / "linux" / "akaiutil-GUI.desktop").read_text(encoding="utf-8")
    fields = dict(line.split("=", 1) for line in desktop.splitlines() if "=" in line)
    assert fields["Exec"] == "main.bin"  # Nuitka names the executable after src/main.py
    assert (ROOT / "assets" / "icon" / f"{fields['Icon']}.png").is_file()
    assert "main.bin" in run_text(workflow["jobs"]["build-linux"])


def test_the_installer_has_its_own_id():
    iss = (ROOT / "assets" / "windows" / "akaiutil-GUI.iss").read_text(encoding="utf-8")
    guid = re.search(r"AppId=\{\{([0-9A-F-]{36})\}", iss).group(1)
    assert str(uuid.UUID(guid)).upper() == guid
    assert guid != "1B7BBB59-F925-454B-90D1-A9D7E1AE24CC", "that's AKAISDS's: two apps sharing an AppId would replace each other"


def test_icons_are_square_and_the_right_size():
    import struct

    def png_size(path):
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        return struct.unpack(">II", data[16:24])

    assert png_size(ROOT / "assets/icon/icon_512x512.png") == (512, 512)
    w, h = png_size(ROOT / "assets/icon/icon_source.png")
    assert w == h and w >= 1024


def icns_entries(path: Path) -> dict[str, bytes]:
    """The entries of an .icns file: four-letter type -> payload (a PNG for the Retina-era sizes)."""
    import struct

    data = path.read_bytes()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data), "not a well-formed .icns"
    entries, pos = {}, 8
    while pos < len(data):
        kind, size = data[pos : pos + 4].decode("latin1"), struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        entries[kind] = data[pos + 8 : pos + size]
        pos += size
    return entries


def test_the_macos_icon_has_all_ten_sizes():
    entries = icns_entries(ROOT / "assets" / "icon" / "akaiutil-GUI-macos.icns")
    # 16, 32, 128, 256, 512 points at 1x and 2x. 1x 16/32 px are ic04/ic05 (iconutil's older encoding)
    expected = {"ic04": 16, "ic05": 32, "ic11": 32, "ic12": 64, "ic07": 128, "ic13": 256, "ic08": 256, "ic14": 512, "ic09": 512, "ic10": 1024}
    assert expected.keys() <= entries.keys(), f"missing sizes: {sorted(expected.keys() - entries.keys())}"
    import struct

    for kind, pixels in expected.items():
        payload = entries[kind]
        if payload[:8] == b"\x89PNG\r\n\x1a\n":  # the PNG-encoded ones state their own size
            assert struct.unpack(">II", payload[16:24]) == (pixels, pixels), f"{kind} should be {pixels}px"


def test_the_macos_icon_master_is_in_the_apple_template_form():
    import struct

    master = (ROOT / "assets" / "icon" / "AppIcon1024.png").read_bytes()
    assert master[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", master[16:24]) == (1024, 1024)
    assert master[25] == 6, "needs an alpha channel (RGBA): the rounded corners are transparent"

    Image = pytest.importorskip("PIL.Image")  # decoding pixels needs Pillow; the CI test job doesn't install it
    im = Image.open(ROOT / "assets/icon/AppIcon1024.png").convert("RGBA")
    assert all(im.getpixel(p)[3] == 0 for p in [(0, 0), (1023, 0), (0, 1023), (1023, 1023)]), "corners must be transparent"
    solid = im.getchannel("A").point(lambda v: 255 if v > 200 else 0).getbbox()
    assert solid == (100, 100, 924, 924), f"Apple's template is 824px centred in 1024; this is {solid}"

    import io

    biggest = Image.open(io.BytesIO(icns_entries(ROOT / "assets/icon/akaiutil-GUI-macos.icns")["ic10"])).convert("RGBA")
    assert biggest.tobytes() == im.tobytes(), "the .icns is out of date: rerun scripts/make_icns.sh"


# ------------------------------------------------- what the build bundles
def data_args(script: str) -> list[tuple[str, str, str]]:
    return re.findall(r"--include-data-(dir|files)=([^\s=\"]+)=([^\s\"]+)", script)


@pytest.mark.parametrize("name", list(SCRIPTS))
def test_every_bundled_data_file_exists(name):
    args = data_args(SCRIPTS[name])
    assert args, f"{name} bundles nothing"
    for kind, src, dest in args:
        source = (ROOT / "src" / src).resolve()  # the build runs inside src/
        if "build/bin" in src:
            assert source.parent.name == "bin"  # generated by get_akaiutil.py during the build itself
            continue
        assert source.is_dir() if kind == "dir" else source.is_file(), f"{name} bundles {src}, which doesn't exist"


def test_the_code_reads_the_files_the_build_bundles():
    from akaiutil_gui.engine import locate
    from akaiutil_gui.ui import notices_dialog, theme

    bundled = {dest for _, _, dest in data_args(SCRIPTS["build_macos.sh"])}
    # relative to src/ these are what the modules open at run time
    for needed in (
        Path(theme._TEMPLATE_PATH).relative_to(ROOT / "src").as_posix(),
        Path(theme._ICONS_DIR).relative_to(ROOT / "src").as_posix(),
        notices_dialog.NOTICES_PATH.parent.relative_to(ROOT / "src").as_posix(),
    ):
        assert any(needed == d or needed.startswith(d.rstrip("/") + "/") or d.startswith(needed) for d in bundled), needed
    assert f"bin/{locate.EXE_NAME}" in bundled or "bin/akaiutil" in bundled
    assert "bin/akaiutil.exe" in {d for _, _, d in data_args(SCRIPTS["build_windows.ps1"])}
    assert "LICENSE" in bundled  # the GPL text has to travel with the GPL binary


def test_every_script_fetches_akaiutil_before_deploying():
    for name, text in SCRIPTS.items():
        assert "get_akaiutil.py" in text and text.index("get_akaiutil.py") < text.index("pyside6-deploy -c"), name
        assert "akaiutil_gui/_version.py" in text.replace("\\", "/"), f"{name}: the version has to land where the package reads it"


def test_the_notices_name_the_exact_akaiutil_that_is_bundled():
    from akaiutil_gui.engine import fetch
    from akaiutil_gui.ui import notices_dialog

    text = notices_dialog.notices_text()
    assert fetch.SOURCE_SHA256 in text, "the notices must carry the checksum of the source that's pinned"
    assert fetch.VERSION in text and fetch.SOURCE_ARCHIVE in text
    assert "GNU General Public License" in text and "Klaus Michael Indlekofer" in text


# --------------------------------------------------------------- .gitignore
def test_build_outputs_are_ignored_and_assets_are_not():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("src/akaiutil_gui/_version.py", "*.spec", "*.dist/", "*.app/", "*.build/", "*.dmg", "*.AppImage"):
        assert pattern in ignored, pattern
    # *.exe is ignored, so nothing the repo needs may end in .exe
    assert not list((ROOT / "assets").rglob("*.exe")) and not list((ROOT / "src").rglob("*.exe"))
