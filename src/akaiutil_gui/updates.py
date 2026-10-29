"""Checks GitHub Releases for a newer version of this app.

Uses the unauthenticated GitHub REST API (`/releases`, the most recent ones). Only published, non-prerelease releases count, which is
exactly what .github/workflows/build.yml publishes. The newest of them is the update; the notes shown are those of every release newer
than the installed version, newest first, so someone several versions behind reads everything they missed.
A port of AKAISDS's core/update_checker.py. No Qt in here; the worker thread lives in ui/update_helper.py.
"""

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from akaiutil_gui import __version__ as APP_VERSION

# "owner/name" of the GitHub repository whose Releases are checked.
REPO = "martincurkovic/akaiutil-gui"

REQUEST_TIMEOUT = 5  # seconds: this must never hang the app
RELEASES_PER_PAGE = 30  # how many recent releases are asked for: more than anyone is behind by
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class UpdatesNotConfigured(Exception):
    """This build doesn't know which repository to ask."""


@dataclass
class UpdateInfo:
    version: str  # e.g. "1.1.0", no leading "v"
    tag: str  # e.g. "v1.1.0"
    name: str  # the release title, may be blank
    html_url: str  # the release page to send the user to
    notes: str = ""  # release notes, as the Markdown they were written in: those of every release newer than the installed one, newest first


def parse_version(version: str) -> tuple[int, int, int] | None:
    """(major, minor, patch), or None if this isn't a clean released version (dev builds, anything with a suffix)."""
    m = _VERSION_RE.match(version.strip())
    return tuple(int(part) for part in m.groups()) if m else None


def is_dev_build(version: str = APP_VERSION) -> bool:
    return parse_version(version) is None


def releases_api_url(repo: str) -> str:
    return f"https://api.github.com/repos/{repo}/releases?per_page={RELEASES_PER_PAGE}"


def combined_notes(newer: list[tuple[str, str, str]]) -> str:
    """The notes of the releases newer than the installed one, as [(tag, name, body)] newest first. One release's notes are
    its own, as written; several get a heading each (the release's title) and a rule between them."""
    if len(newer) == 1:
        return newer[0][2]
    parts = [f"# {name}\n\n{body.strip()}".rstrip() for _tag, name, body in newer]
    return "\n\n---\n\n".join(parts)


def check_for_update(
    current_version: str = APP_VERSION,
    repo: str | None = None,
    urlopen: Callable = urllib.request.urlopen,
) -> UpdateInfo | None:
    """An UpdateInfo if a newer release exists, else None.

    Raises UpdatesNotConfigured when there's no repository to ask, and urllib.error.URLError/HTTPError or
    ValueError on a network or parse failure: callers decide whether that's worth showing the user.
    """
    current = parse_version(current_version)
    if current is None:
        return None  # a dev build: nothing meaningful to compare against
    repo = REPO if repo is None else repo
    if not repo:
        raise UpdatesNotConfigured("This build doesn't know where to look for updates yet (no GitHub repository is set up).")

    request = urllib.request.Request(
        releases_api_url(repo),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"akaiutil-gui/{current_version}",  # GitHub's API rejects requests with no User-Agent
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise ValueError("GitHub's answer wasn't a list of releases")

    newer = []  # (version, release) for each published release newer than this one
    for release in payload:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        version = parse_version(release.get("tag_name") or "")
        if version is not None and version > current:
            newer.append((version, release))
    if not newer:
        return None
    newer.sort(key=lambda pair: pair[0], reverse=True)  # (GitHub lists by date, not by version)
    latest, release = newer[0]
    tag = release["tag_name"]
    return UpdateInfo(
        version=".".join(str(part) for part in latest),
        tag=tag,
        name=release.get("name") or tag,
        html_url=release.get("html_url", f"https://github.com/{repo}/releases"),
        notes=combined_notes([(r["tag_name"], r.get("name") or r["tag_name"], r.get("body") or "") for _v, r in newer]),  # (body is null for a release with no description)
    )
