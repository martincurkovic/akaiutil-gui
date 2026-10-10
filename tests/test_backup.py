"""Backups of disk images: made before editing, kept in the app's folder, and put back by Revert."""

import os
from datetime import datetime, timedelta

import pytest

from akaiutil_gui import backup


@pytest.fixture
def root(tmp_path):
    return tmp_path / "backups"


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "disks" / "HD5.img"
    path.parent.mkdir()
    path.write_bytes(b"original image contents" * 1000)
    return path


def test_a_backup_is_a_copy_in_the_apps_folder_named_for_when_it_was_made(root, image):
    made = backup.make_backup(root, image, now=datetime(2026, 10, 10, 20, 15, 30))
    assert made.path.parent == backup.folder_for(root, image) and made.path.name == "2026-10-10 20-15-30.img"
    assert made.path.read_bytes() == image.read_bytes() and made.size == image.stat().st_size
    assert made.image == image.resolve()
    assert image.read_bytes() == b"original image contents" * 1000  # (the image itself is untouched)


def test_backups_are_listed_newest_first_and_only_for_that_image(root, image, tmp_path):
    other = tmp_path / "disks" / "OTHER.img"
    other.write_bytes(b"x" * 100)
    base = datetime(2026, 1, 1, 12, 0, 0)
    for i in range(3):
        backup.make_backup(root, image, now=base + timedelta(minutes=i))
    backup.make_backup(root, other, now=base)
    assert [b.created for b in backup.list_backups(root, image)] == [base + timedelta(minutes=i) for i in (2, 1, 0)]
    assert len(backup.list_backups(root, other)) == 1
    assert backup.list_backups(root, tmp_path / "nowhere.img") == []


def test_two_images_with_the_same_name_in_different_folders_keep_separate_backups(root, tmp_path):
    a, b = tmp_path / "a" / "DISK.img", tmp_path / "b" / "DISK.img"
    for p in (a, b):
        p.parent.mkdir()
        p.write_bytes(p.parent.name.encode())
    backup.make_backup(root, a)
    assert len(backup.list_backups(root, a)) == 1 and backup.list_backups(root, b) == []
    assert backup.folder_for(root, a) != backup.folder_for(root, b)


def test_two_backups_in_the_same_second_both_survive(root, image):
    now = datetime(2026, 10, 10, 9, 0, 0)
    one, two = backup.make_backup(root, image, now=now), backup.make_backup(root, image, now=now)
    assert one.path != two.path and one.path.exists() and two.path.exists()
    assert len(backup.list_backups(root, image)) == 2


def test_only_the_newest_few_are_kept(root, image):
    base = datetime(2026, 1, 1)
    for i in range(backup.KEEP + 3):
        backup.make_backup(root, image, now=base + timedelta(days=i))
    kept = backup.list_backups(root, image)
    assert len(kept) == backup.KEEP
    assert kept[-1].created == base + timedelta(days=3)  # the oldest three went


def test_pruning_leaves_files_that_are_not_ours_alone(root, image):
    folder = backup.folder_for(root, image)
    base = datetime(2026, 1, 1)
    for i in range(backup.KEEP + 1):
        backup.make_backup(root, image, now=base + timedelta(days=i))
    (folder / "notes.txt").write_text("mine")
    backup.prune(root, image)
    assert (folder / "notes.txt").exists()


def test_reverting_puts_the_backup_back_and_backs_up_what_was_there(root, image):
    saved = backup.make_backup(root, image, now=datetime(2026, 1, 1))
    image.write_bytes(b"edited")
    before = backup.restore(root, saved, image)
    assert image.read_bytes() == b"original image contents" * 1000
    assert before.path.read_bytes() == b"edited"  # the revert can itself be undone
    assert {b.path for b in backup.list_backups(root, image)} >= {saved.path, before.path}
    assert not [p for p in image.parent.iterdir() if p.name.endswith(".restoring")]


def test_reverting_to_the_oldest_backup_works_even_when_the_folder_is_full(root, image):
    base = datetime(2026, 1, 1)
    made = [backup.make_backup(root, image, now=base + timedelta(days=i)) for i in range(backup.KEEP)]
    image.write_bytes(b"edited")
    backup.restore(root, made[0], image)  # (the new backup would otherwise have pushed it out before it was copied)
    assert image.read_bytes() == b"original image contents" * 1000
    assert len(backup.list_backups(root, image)) == backup.KEEP


def test_a_revert_keeps_the_images_permissions(root, image):
    saved = backup.make_backup(root, image)
    os.chmod(image, 0o640)
    backup.restore(root, saved, image)
    assert image.stat().st_mode & 0o777 == 0o640


def test_a_missing_image_or_backup_is_explained(root, tmp_path, image):
    with pytest.raises(backup.BackupError, match="Can't read gone.img"):
        backup.make_backup(root, tmp_path / "gone.img")
    saved = backup.make_backup(root, image)
    saved.path.unlink()
    with pytest.raises(backup.BackupError, match="gone"):
        backup.restore(root, saved, image)


def test_not_enough_room_is_refused_before_anything_is_copied(root, image, monkeypatch):
    monkeypatch.setattr(backup, "_clone", lambda src, dst: False)  # (a real copy is needed)

    class Usage:
        free = 10

    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: Usage)
    with pytest.raises(backup.BackupError, match="isn't room for a backup of HD5.img"):
        backup.make_backup(root, image)
    folder = backup.folder_for(root, image)
    assert not any(folder.iterdir())  # no half-written file left


def test_the_default_folder_can_be_pointed_elsewhere_for_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("AKAIUTIL_GUI_BACKUP_DIR", str(tmp_path / "x"))
    assert backup.default_root() == tmp_path / "x"


# ------------------------------------------------------------------- cleaning up old backups
def test_cleanup_deletes_backups_older_than_the_limit_and_keeps_newer_ones(root, image):
    now = datetime(2026, 10, 10, 12, 0, 0)
    old = backup.make_backup(root, image, now=now - timedelta(days=20))
    edge = backup.make_backup(root, image, now=now - timedelta(days=13))
    new = backup.make_backup(root, image, now=now - timedelta(hours=1))
    removed = backup.cleanup(root, 14, now=now)
    assert removed == [old.path]
    assert edge.path.exists() and new.path.exists() and not old.path.exists()


def test_cleanup_with_zero_days_never_deletes_backups(root, image):
    now = datetime(2026, 10, 10)
    made = backup.make_backup(root, image, now=now - timedelta(days=500))
    assert backup.cleanup(root, 0, now=now) == [] and made.path.exists()


def test_cleanup_removes_a_folder_that_ends_up_empty_but_not_one_with_something_in_it(root, tmp_path, image):
    other = tmp_path / "disks" / "B.img"
    other.write_bytes(b"b")
    now = datetime(2026, 10, 10)
    backup.make_backup(root, image, now=now - timedelta(days=30))
    backup.make_backup(root, other, now=now - timedelta(days=30))
    (backup.folder_for(root, other) / "notes.txt").write_text("keep me")
    backup.cleanup(root, 14, now=now)
    assert not backup.folder_for(root, image).exists()
    assert (backup.folder_for(root, other) / "notes.txt").exists()  # (our files went; the user's note stays, and so does its folder)


def test_cleanup_clears_half_written_copies_but_not_one_being_written_right_now(root, image):
    folder = backup.folder_for(root, image)
    folder.mkdir(parents=True)
    stale, fresh = folder / ".partial-aaa.img", folder / ".partial-bbb.img"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"y")
    os.utime(stale, (0, 0))  # (the year 1970)
    removed = backup.cleanup(root, 14)
    assert removed == [stale] and fresh.exists()


def test_cleanup_only_touches_things_that_look_like_backups(root, tmp_path):
    root.mkdir()
    (root / "loose.img").write_bytes(b"x")  # (a file directly in the root)
    folder = root / "somefolder"
    folder.mkdir()
    (folder / "2020-01-01.img").write_bytes(b"x")  # (not our naming)
    (folder / "mine.img").write_bytes(b"x")
    sub = folder / "2019-01-01 00-00-00.img"
    sub.mkdir()  # (a directory with a backup-like name)
    assert backup.cleanup(root, 1) == []
    assert (root / "loose.img").exists() and (folder / "mine.img").exists() and sub.exists()


def test_cleanup_of_a_folder_that_isnt_there_is_fine(tmp_path):
    assert backup.cleanup(tmp_path / "nothing", 14) == []
