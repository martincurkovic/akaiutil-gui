"""Checking GitHub Releases for a newer version: the core, the runner that reacts, and the dialog."""

import io
import json
import re
import time
import urllib.error

import pytest
from PySide6.QtWidgets import QMessageBox

from akaiutil_gui import settings, updates
from akaiutil_gui.ui import update_helper
from akaiutil_gui.ui.update_dialog import UpdateAvailableDialog


def release(tag="v1.2.0", name="Big update", body="## Notes\n\n- faster", url="https://github.com/o/r/releases/tag/v1.2.0"):
    payload = {"tag_name": tag, "name": name, "body": body, "html_url": url}
    return json.dumps({k: v for k, v in payload.items() if v is not None}).encode()


class Fake:
    """A stand-in for urllib.request.urlopen that records the request and answers with a canned body."""

    def __init__(self, body=None, error=None):
        self.body, self.error, self.requests = body, error, []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(self.body)


# ------------------------------------------------------------------------- the core
@pytest.mark.parametrize("text, expected", [("1.2.3", (1, 2, 3)), ("v1.2.3", (1, 2, 3)), (" v10.0.1 ", (10, 0, 1)),
                                            ("1.2", None), ("1.2.3-rc1", None), ("0.0.0-dev", None), ("nightly", None), ("", None)])
def test_only_clean_version_numbers_parse(text, expected):
    assert updates.parse_version(text) == expected


def test_a_newer_release_is_reported_with_its_details():
    fake = Fake(release())
    info = updates.check_for_update("1.1.0", repo="owner/repo", urlopen=fake)
    assert (info.version, info.tag, info.name, info.notes) == ("1.2.0", "v1.2.0", "Big update", "## Notes\n\n- faster")
    assert info.html_url == "https://github.com/o/r/releases/tag/v1.2.0"
    request, timeout = fake.requests[0]
    assert request.full_url == "https://api.github.com/repos/owner/repo/releases/latest"
    assert request.get_header("User-agent") == "akaiutil-gui/1.1.0"  # GitHub's API rejects requests without one
    assert timeout == updates.REQUEST_TIMEOUT <= 5, "a check must never hang the app"


@pytest.mark.parametrize("current", ["1.2.0", "1.3.0", "2.0.0"])
def test_the_same_or_an_older_release_is_not_an_update(current):
    assert updates.check_for_update(current, repo="o/r", urlopen=Fake(release("v1.2.0"))) is None


def test_versions_compare_numerically_not_as_text():
    assert updates.check_for_update("1.9.0", repo="o/r", urlopen=Fake(release("v1.10.0"))).version == "1.10.0"


def test_a_dev_build_never_asks_the_network():
    fake = Fake(release())
    assert updates.check_for_update("0.0.0-dev", repo="o/r", urlopen=fake) is None and fake.requests == []
    assert updates.is_dev_build("0.0.0-dev") and not updates.is_dev_build("1.0.0")


def test_without_a_repository_it_says_so_instead_of_guessing():
    with pytest.raises(updates.UpdatesNotConfigured, match="no GitHub repository"):
        updates.check_for_update("1.0.0", repo="", urlopen=Fake(release()))


def test_a_release_with_odd_data_is_handled():
    bare = Fake(json.dumps({"tag_name": "v2.0.0"}).encode())  # no name, body or url
    info = updates.check_for_update("1.0.0", repo="o/r", urlopen=bare)
    assert (info.name, info.notes, info.html_url) == ("v2.0.0", "", "https://github.com/o/r/releases")
    assert updates.check_for_update("1.0.0", repo="o/r", urlopen=Fake(release(tag="nightly"))) is None  # not a version
    assert updates.check_for_update("1.0.0", repo="o/r", urlopen=Fake(b'{"body": null}')) is None


def test_network_and_parse_failures_are_raised_for_the_caller_to_decide():
    with pytest.raises(urllib.error.URLError):
        updates.check_for_update("1.0.0", repo="o/r", urlopen=Fake(error=urllib.error.URLError("offline")))
    with pytest.raises(ValueError):
        updates.check_for_update("1.0.0", repo="o/r", urlopen=Fake(b"<html>rate limited</html>"))


# --------------------------------------------------------------------- the runner
@pytest.fixture
def runner(qt_app, monkeypatch):
    from PySide6.QtWidgets import QWidget

    shown, messages = [], {"info": [], "warning": []}
    monkeypatch.setattr(update_helper, "UpdateAvailableDialog", lambda info, parent: type("D", (), {"show": lambda self: shown.append(info)})())
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: messages["info"].append((title, text)))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: messages["warning"].append((title, text)))
    monkeypatch.setattr(update_helper.updates, "is_dev_build", lambda version=None: False)  # act like a released build
    parent = QWidget()
    r = update_helper.UpdateCheckRunner(parent)
    r.shown, r.messages, r._keep = shown, messages, parent
    return r


def run_check(runner, manual, qt_app):
    done = []
    runner.start(manual=manual, on_finished=lambda: done.append(True))
    end = time.time() + 5
    while not done and time.time() < end:
        qt_app.processEvents()
        time.sleep(0.01)
    qt_app.processEvents()
    assert done, "the check never finished"


def newer(monkeypatch):
    info = updates.UpdateInfo("9.9.9", "v9.9.9", "x", "https://example.invalid/r")
    monkeypatch.setattr(updates, "check_for_update", lambda *a, **k: info)
    return info


def test_a_manual_check_that_finds_an_update_shows_the_dialog_and_records_the_time(runner, qt_app, monkeypatch):
    info = newer(monkeypatch)
    before = time.time()
    run_check(runner, manual=True, qt_app=qt_app)
    assert runner.shown == [info] and settings.get_last_update_check() >= before


def test_an_automatic_check_stays_quiet_about_a_version_the_user_skipped(runner, qt_app, monkeypatch):
    newer(monkeypatch)
    settings.set_skipped_update_version("9.9.9")
    run_check(runner, manual=False, qt_app=qt_app)
    assert runner.shown == []
    run_check(runner, manual=True, qt_app=qt_app)  # but asking by hand brings it back
    assert len(runner.shown) == 1


def test_up_to_date_is_only_announced_when_asked(runner, qt_app, monkeypatch):
    monkeypatch.setattr(updates, "check_for_update", lambda *a, **k: None)
    run_check(runner, manual=False, qt_app=qt_app)
    assert runner.messages["info"] == []
    run_check(runner, manual=True, qt_app=qt_app)
    assert runner.messages["info"][0][0] == "No Updates Available"


def test_failures_are_only_shown_for_a_manual_check(runner, qt_app, monkeypatch):
    def offline(*a, **k):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(updates, "check_for_update", offline)
    run_check(runner, manual=False, qt_app=qt_app)
    assert runner.messages["warning"] == []
    run_check(runner, manual=True, qt_app=qt_app)
    assert runner.messages["warning"][0][0] == "Update Check Failed" and "offline" in runner.messages["warning"][0][1]


def test_a_build_with_no_repository_says_updates_are_not_set_up(runner, qt_app, monkeypatch):
    def unset(*a, **k):
        raise updates.UpdatesNotConfigured("no GitHub repository is set up")

    monkeypatch.setattr(updates, "check_for_update", unset)
    run_check(runner, manual=False, qt_app=qt_app)
    assert runner.messages["info"] == [] and runner.messages["warning"] == []  # silent on startup
    run_check(runner, manual=True, qt_app=qt_app)
    assert runner.messages["info"][0][0] == "Update Checking Isn't Set Up"


def test_a_development_build_explains_itself_without_touching_the_network(runner, qt_app, monkeypatch):
    monkeypatch.setattr(update_helper.updates, "is_dev_build", lambda version=None: True)
    monkeypatch.setattr(updates, "check_for_update", lambda *a, **k: pytest.fail("no network for a dev build"))
    done = []
    runner.start(manual=True, on_finished=lambda: done.append(True))
    assert done and runner.messages["info"][0][0] == "Development Build" and not runner.is_running()


def test_only_one_check_runs_at_a_time(runner, qt_app, monkeypatch):
    release_it, started = [], []

    def slow(*a, **k):
        started.append(1)
        while not release_it:
            time.sleep(0.01)
        return None

    monkeypatch.setattr(updates, "check_for_update", slow)
    runner.start(manual=False)
    runner.start(manual=False)  # ignored: one is already in flight
    assert runner.is_running()
    release_it.append(True)
    runner.wait()
    qt_app.processEvents()
    assert started == [1]


# --------------------------------------------------------------------- the dialog
def test_the_update_dialog_skips_a_version_and_only_opens_web_links(qt_app, monkeypatch):
    from PySide6.QtCore import QUrl

    opened = []
    monkeypatch.setattr("akaiutil_gui.ui.update_dialog.open_url", opened.append)
    info = updates.UpdateInfo("2.0.0", "v2.0.0", "Two", "https://example.invalid/r", notes="See [site](https://example.invalid) now")
    dialog = UpdateAvailableDialog(info)
    from PySide6.QtWidgets import QLabel

    assert dialog.notes_view is not None
    assert any("2.0.0 is available" in label.text() for label in dialog.findChildren(QLabel))
    dialog._open_notes_link(QUrl("https://example.invalid/page"))
    dialog._open_notes_link(QUrl("file:///etc/passwd"))  # release notes are text from the network
    dialog._open_notes_link(QUrl("javascript:alert(1)"))
    assert opened == ["https://example.invalid/page"]
    dialog._skip_version()
    assert settings.get_skipped_update_version() == "2.0.0"
    assert UpdateAvailableDialog(updates.UpdateInfo("2.0.1", "v2.0.1", "x", "https://e.invalid")).notes_view is None  # no notes, no empty box


def test_the_configured_repository_looks_like_owner_slash_name():
    assert re.fullmatch(r"[\w.-]+/[\w.-]+", updates.REPO)
