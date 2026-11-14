"""Phase 1 tests. Test numbers (T1, T2 ...) refer to docs/PSEUDOCODE.md section 26."""

import os
import zipfile
from pathlib import Path

import pytest

from conftest import snapshot, write
from imagecleanuper import backup, settings as settings_mod
from imagecleanuper.app import Runner, recover_journal, startup, summary
from imagecleanuper.catalog import Catalog
from imagecleanuper.discover import discover_files, sync_sources
from imagecleanuper.fileops import CopyVerifyError, atomic_copy, sha256_file
from imagecleanuper.log import UserError
from imagecleanuper.paths import is_inside
from imagecleanuper.restore import restore_files


# ---------- paths -------------------------------------------------------------
def test_is_inside(tmp_path):
    a = tmp_path / "Photos"
    assert is_inside(a / "x" / "y", a)
    assert is_inside(a, a)
    assert not is_inside(tmp_path / "Photos2", a)  # prefix trap
    assert not is_inside(a, a / "x")


# ---------- settings ----------------------------------------------------------
def test_settings_roundtrip_and_percent(tmp_path):
    p = tmp_path / "settings.ini"
    s = settings_mod.Settings(library=str(tmp_path / "Lib 100%"), backup=str(tmp_path / "Bk"),
                              masters=[str(tmp_path)])
    settings_mod.save(s, p)
    s2, _ = settings_mod.load(p)
    assert s2.library == s.library and s2.masters == s.masters and s2.backup == s.backup


def test_T19_library_inside_master_refused(tmp_path):
    m = tmp_path / "Pictures"
    m.mkdir()
    s = settings_mod.Settings(library=str(m / "Sorted"), backup=str(tmp_path / "Bk"), masters=[str(m)])
    probs = settings_mod.check(s)
    assert [p.field for p in probs if p.blocking] == ["library"]  # only the library box is flagged
    assert probs[0].suggestion == str(tmp_path / "Sorted")        # one-click fix: next to it, not inside
    s.library = probs[0].suggestion
    assert not settings_mod.blocking_problems(s)
    s = settings_mod.Settings(library=str(tmp_path / "Lib"), backup=str(m / "Backup"), masters=[str(m)])
    assert [p.field for p in settings_mod.check(s)] == ["backup"]


def test_missing_drive_is_warning_not_blocker(tmp_path):
    s = settings_mod.Settings(library=str(tmp_path / "L"), backup=str(tmp_path / "B"),
                              masters=[str(tmp_path / "unplugged")])
    assert settings_mod.validate(s) and not settings_mod.blocking_problems(s)


def test_bad_values_fall_back(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text("[Accuracy]\ntarget = lots\nstart_items = 99999\n", encoding="utf-8")
    s, problems = settings_mod.load(p)
    assert s.accuracy_target == 0.95 and s.accuracy_start_items == 50 and len(problems) >= 2


# ---------- file ops ----------------------------------------------------------
def test_atomic_copy_verifies(tmp_path):
    src = write(tmp_path / "a.jpg", b"hello")
    dest = tmp_path / "out" / "a.jpg"
    assert atomic_copy(src, dest) == sha256_file(src)
    with pytest.raises(FileExistsError):
        atomic_copy(src, dest)
    with pytest.raises(CopyVerifyError):
        atomic_copy(src, tmp_path / "b.jpg", expected_sha="0" * 64)
    assert not (tmp_path / "b.jpg").exists() and not (tmp_path / "b.jpg.partial").exists()


# ---------- discovery ---------------------------------------------------------
def _discover(paths, s, run_id=1, **kw):
    cat = Catalog(paths.catalog)
    sync_sources(cat, s.masters)
    res = discover_files(cat, run_id, [s.library, s.backup, str(paths.root)], **kw)
    return cat, res


def test_discover_nested_and_filters(env):
    paths, s, m1, m2 = env
    write(m1 / "a" / "b" / "c" / "IMG_0001.JPG", b"1")
    write(m1 / "video.MOV", b"2")
    write(m1 / "notes.txt", b"3")
    write(m1 / "Thumbs.db", b"4")
    write(m1 / ".hidden" / "x.jpg", b"5")
    write(m2 / "IMG_0001.JPG", b"6")
    cat, res = _discover(paths, s)
    assert res.found == 3 and res.new == 3 and res.other_files == 1
    kinds = sorted(r["kind"] for r in cat.q("SELECT kind FROM files"))
    assert kinds == ["photo", "photo", "video"]


def test_T1_very_deep_path(env):
    paths, s, m1, _ = env
    deep = m1
    for i in range(30):
        deep = deep / f"folder_number_{i:02d}"
    f = write(deep / "deep.jpg", b"deep")
    assert len(str(f)) > 300
    cat, res = _discover(paths, s)
    assert res.found == 1 and not res.unreachable


def test_unchanged_files_not_rehashed(env):
    paths, s, m1, _ = env
    write(m1 / "a.jpg", b"1")
    calls = []

    def hasher(p):
        calls.append(p)
        return sha256_file(p)
    cat, _ = _discover(paths, s, hasher=hasher)
    cat.close()
    calls.clear()
    cat, res = _discover(paths, s, run_id=2, hasher=hasher)
    assert res.unchanged == 1 and calls == []


def test_missing_vs_unplugged(env):
    paths, s, m1, m2 = env
    write(m1 / "a.jpg", b"1")
    write(m2 / "b.jpg", b"2")
    cat, _ = _discover(paths, s)
    cat.close()
    os.remove(m1 / "a.jpg")
    os.rename(m2, str(m2) + "_unplugged")
    cat, res = _discover(paths, s, run_id=2)
    assert res.missing == 1  # a.jpg really gone
    assert res.unavailable_sources == [str(m2)]
    assert cat.scalar("SELECT missing FROM files WHERE rel_path='b.jpg'") == 0  # unplugged ≠ deleted


@pytest.mark.skipif(os.name == "nt", reason="symlinks need admin rights on Windows")
def test_symlink_loop_not_followed(env):
    paths, s, m1, _ = env
    write(m1 / "a.jpg", b"1")
    os.symlink(m1, m1 / "loop")
    _, res = _discover(paths, s)
    assert res.found == 1


# ---------- backup ------------------------------------------------------------
def test_backup_once_and_incremental(env):
    paths, s, m1, m2 = env
    write(m1 / "a.jpg", b"same content")
    write(m2 / "copy_of_a.jpg", b"same content")
    write(m1 / "b.jpg", b"other")
    cat, _ = _discover(paths, s)
    r = backup.backup_new_files(cat, s.backup, 1)
    assert r.files_backed_up == 2  # identical content stored once
    assert backup.all_backed_up(cat)
    write(m1 / "new.jpg", b"brand new")
    cat.close()
    cat, _ = _discover(paths, s, run_id=2)
    r2 = backup.backup_new_files(cat, s.backup, 2)
    assert r2.files_backed_up == 1 and r2.parts_written == 1
    assert os.path.exists(os.path.join(s.backup, "manifest.csv"))


def test_T7_same_name_in_two_folders_both_backed_up(env):
    paths, s, m1, m2 = env
    write(m1 / "DCIM" / "IMG_0001.jpg", b"phone one")
    write(m2 / "DCIM" / "IMG_0001.jpg", b"phone two")
    cat, _ = _discover(paths, s)
    backup.backup_new_files(cat, s.backup, 1)
    names = [r["arcname"] for r in cat.q("SELECT arcname FROM backup_entries")]
    assert len(names) == 2 and len(set(names)) == 2


def test_parts_split_and_unicode(env):
    paths, s, m1, _ = env
    for i in range(5):
        write(m1 / f"Été 2019 🎉 {i}.jpg", bytes([i]) * 10)
    cat, _ = _discover(paths, s)
    r = backup.backup_new_files(cat, s.backup, 1, max_files=2)
    assert r.parts_written == 3
    for p in cat.q("SELECT zip_name FROM backup_parts"):
        with zipfile.ZipFile(os.path.join(s.backup, p["zip_name"])) as zf:
            assert all("Été" in n for n in zf.namelist())


def test_T2_crash_during_backup_leaves_no_verified_part(env, monkeypatch):
    paths, s, m1, _ = env
    write(m1 / "a.jpg", b"1")
    write(m1 / "b.jpg", b"2")
    cat, _ = _discover(paths, s)

    def boom(*a, **k):
        raise RuntimeError("power cut")
    monkeypatch.setattr(backup, "verify_zip", boom)
    with pytest.raises(RuntimeError):
        backup.backup_new_files(cat, s.backup, 1)
    assert cat.scalar("SELECT COUNT(*) FROM backup_parts") == 0
    assert not [f for f in os.listdir(s.backup) if f.endswith((".zip", ".partial"))]
    monkeypatch.undo()
    r = backup.backup_new_files(cat, s.backup, 2)
    assert r.files_backed_up == 2 and backup.all_backed_up(cat)


def test_journal_recovery_after_hard_kill(env):
    """Simulate the process dying after the zip was renamed but before it was recorded."""
    paths, s, m1, _ = env
    write(m1 / "a.jpg", b"1")
    cat, _ = _discover(paths, s)
    final = os.path.join(s.backup, "photos_x.zip")
    os.makedirs(s.backup, exist_ok=True)
    Path(final).write_bytes(b"half")
    Path(final + ".partial").write_bytes(b"half")
    cat.journal("backup_part", {"tmp": final + ".partial", "final": final, "zip_name": "photos_x.zip"})
    assert recover_journal(cat) == 1
    assert not os.path.exists(final) and not os.path.exists(final + ".partial")
    assert not cat.open_journal_entries()


def test_corrupt_zip_fails_verification(tmp_path):
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.jpg", b"abc")
    assert backup.verify_zip(str(z), {"a.jpg": __import__("hashlib").sha256(b"abc").hexdigest()})
    assert not backup.verify_zip(str(z), {"a.jpg": "0" * 64})
    assert not backup.verify_zip(str(z), {"a.jpg": "x", "b.jpg": "y"})


def test_not_enough_space(env, monkeypatch):
    paths, s, m1, _ = env
    write(m1 / "a.jpg", b"1")
    cat, _ = _discover(paths, s)
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(UserError, match="Not enough space"):
        backup.backup_new_files(cat, s.backup, 1)


# ---------- full run, restore, learned data ----------------------------------
def test_T5_full_run_never_touches_sources_and_restore(env):
    paths, s, m1, m2 = env
    write(m1 / "2019" / "Christmas" / "IMG_1.jpg", b"xmas")
    write(m1 / "2019" / "IMG_2.mov", b"video")
    write(m2 / "DCIM" / "IMG_1.jpg", b"phone")
    before = {**snapshot(m1), **{"m2/" + k: v for k, v in snapshot(m2).items()}}
    res = Runner(paths, s).run()
    assert res.state == "done"
    after = {**snapshot(m1), **{"m2/" + k: v for k, v in snapshot(m2).items()}}
    assert before == after  # R1: sources untouched
    assert summary(paths)["backed_up"] == 3
    assert os.listdir(os.path.join(s.backup, "Learned Data"))

    # Lose a file, then restore everything.
    os.remove(m1 / "2019" / "Christmas" / "IMG_1.jpg")
    cat = Catalog(paths.catalog)
    r = restore_files(cat, s.backup)
    assert r.restored == 1 and r.already_there == 2
    assert (m1 / "2019" / "Christmas" / "IMG_1.jpg").read_bytes() == b"xmas"


def test_restore_never_overwrites_silently(env):
    paths, s, m1, _ = env
    f = write(m1 / "a.jpg", b"original")
    Runner(paths, s).run()
    f.write_bytes(b"edited since")
    cat = Catalog(paths.catalog)
    r = restore_files(cat, s.backup)
    assert r.skipped == 1 and f.read_bytes() == b"edited since"
    r = restore_files(cat, s.backup, on_conflict=lambda d: "keep_both")
    assert r.kept_both == 1 and (m1 / "a (restored).jpg").read_bytes() == b"original"


def test_run_refuses_bad_settings(env):
    paths, s, m1, _ = env
    s.library = str(m1 / "Sorted")
    with pytest.raises(UserError):
        Runner(paths, s).run()


def test_learned_backup_keeps_last_n(env):
    paths, s, m1, _ = env
    write(m1 / "a.jpg", b"1")
    s.learned_versions_kept = 3
    for _ in range(5):
        Runner(paths, s).run()
    files = os.listdir(os.path.join(s.backup, "Learned Data"))
    assert len(files) == 3


def test_crashed_run_detected_on_startup(env):
    paths, s, m1, _ = env
    settings_mod.save(s, paths.settings)
    cat = Catalog(paths.catalog)
    cat.start_run()  # never finished = crash
    cat.close()
    st = startup(paths)
    assert st.crashed_run is not None and st.crashed_run["state"] == "running"
    assert startup(paths).crashed_run is None  # reported once


def test_migration_is_versioned(tmp_path):
    cat = Catalog(tmp_path / "c.db")
    v = cat.schema_version
    cat.close()
    assert Catalog(tmp_path / "c.db").schema_version == v >= 1
