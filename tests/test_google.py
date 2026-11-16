"""Google Photos import (Takeout): sidecar matching quirks, backup, dates, sorting, restore."""

import io
import json
import os
import zipfile

from images import scene
from imagecleanuper import google
from imagecleanuper.app import Runner, google_import, sort_library
from imagecleanuper.catalog import Catalog


def _jpeg(seed):
    buf = io.BytesIO()
    scene(seed, (400, 300)).save(buf, "JPEG", quality=85)        # no camera info: the date comes from Google
    return buf.getvalue()


def _sidecar(ts):
    return json.dumps({"title": "x", "photoTakenTime": {"timestamp": str(ts), "formatted": "x"}}).encode()


LONG = "PXL_20210704_183455123.NIGHT_from_my_phone_long.jpg"     # 51 characters: sidecar name gets cut


def _takeout(path):
    with zipfile.ZipFile(path, "w") as z:
        base = "Takeout/Google Photos/Photos from 2021/"
        z.writestr(base + "IMG_0001.JPG", _jpeg(1))
        z.writestr(base + "IMG_0001.JPG.supplemental-metadata.json", _sidecar(1625425200))   # 4 Jul 2021
        z.writestr(base + "IMG_0001-edited.JPG", _jpeg(2))                                   # shares that sidecar
        z.writestr(base + "IMG_0002(1).JPG", _jpeg(3))
        z.writestr(base + "IMG_0002.JPG(1).json", _sidecar(1640433600))                       # 25 Dec 2021
        z.writestr(base + LONG, _jpeg(4))
        z.writestr(base + LONG[:43] + ".json", _sidecar(1609502400))                         # 1 Jan 2021
        z.writestr(base + "metadata.json", b"{}")
        z.writestr("Takeout/archive_browser.html", b"<html></html>")


def test_sidecar_matching_quirks():
    media = ["a/IMG_0001.JPG", "a/IMG_0001-edited.JPG", "a/IMG_0002(1).JPG", "a/" + LONG, "a/IMG_9.JPG"]
    jsons = ["a/IMG_0001.JPG.supplemental-metadata.json", "a/IMG_0002.JPG(1).json", "a/" + LONG[:43] + ".json",
             "a/metadata.json", "a/IMG_0002.JPG.supplemental-meta.json"]
    m = google.match_sidecars(media, jsons)
    assert m["a/IMG_0001.JPG"] == m["a/IMG_0001-edited.JPG"] == "a/IMG_0001.JPG.supplemental-metadata.json"
    assert m["a/IMG_0002(1).JPG"] == "a/IMG_0002.JPG(1).json"
    assert m["a/" + LONG] == "a/" + LONG[:43] + ".json"
    assert "a/IMG_9.JPG" not in m


def test_import_backs_up_dates_and_sorts(env, tmp_path):
    paths, s, m1, _ = env
    z = tmp_path / "takeout-001.zip"
    _takeout(z)
    Runner(paths, s).run()                                   # an empty first run is fine
    res = google_import(paths, s, [str(z)])
    assert res.photos == 4 and res.dated == 4
    assert os.path.exists(os.path.join(s.backup, "Google Takeout", "001", "takeout-001.zip"))
    assert Runner(paths, s).run().state == "done"
    cat = Catalog(paths.catalog)
    assert cat.scalar("SELECT COUNT(*) FROM backup_parts WHERE kind='photos'") == 0     # the zip is the backup
    srcs = {r[0] for r in cat.q("SELECT source FROM dates")}
    assert srcs == {"google"}
    sort_library(paths, s)
    lib = set()
    for dp, _, fs in os.walk(s.library):
        lib |= {os.path.relpath(os.path.join(dp, f), s.library).replace(os.sep, "/") for f in fs}
    assert any(f.startswith("2021/Holidays/Fourth of July/IMG_0001") for f in lib)
    assert any(f.startswith("2021/Holidays/Christmas/IMG_0002") for f in lib)
    # Restoring one from the Takeout zip works like any backup.
    from imagecleanuper.restore import restore_files
    fid, path = cat.one("SELECT id, orig_path FROM files WHERE rel_path LIKE '%IMG_0001.JPG'")
    os.remove(path)
    r = restore_files(cat, s.backup, [fid])
    assert r.restored == 1 and os.path.exists(path)
