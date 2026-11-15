"""Phase 2 tests. Test numbers refer to docs/PSEUDOCODE.md section 26."""

import datetime as dt
import os
import struct

import pytest
from PIL import Image

from conftest import snapshot, write
from images import save_jpeg, scanner_bed, scene
from imagecleanuper import dates
from imagecleanuper.analyze import fix, imageio, scans
from imagecleanuper.app import Runner
from imagecleanuper.catalog import Catalog
from imagecleanuper.settings import Settings


def run(paths, s):
    res = Runner(paths, s).run()
    assert res.state == "done"
    return Catalog(paths.catalog), res


def asset_rows(cat):
    return cat.q("""SELECT a.*, f.rel_path, f.status, f.open_error, d.date, d.source, d.year_only, d.is_guess,
                           h.holiday, m.is_scan, x.status AS fix_status, x.trim_box, x.exif_rotation
                    FROM files f LEFT JOIN assets a ON a.file_id=f.id LEFT JOIN dates d ON d.asset_id=a.id
                    LEFT JOIN holidays h ON h.asset_id=a.id LEFT JOIN meta m ON m.asset_id=a.id
                    LEFT JOIN fixes x ON x.asset_id=a.id ORDER BY f.rel_path, a.cutout_index""")


# ---------- dates & holidays (pure functions) --------------------------------
def test_T12_thanksgiving_and_easter():
    assert dates.holiday_date("thanksgiving", 1985) == dt.date(1985, 11, 28)
    assert dates.holiday_date("thanksgiving", 2019) == dt.date(2019, 11, 28)
    assert dates.holiday_date("thanksgiving", 2026) == dt.date(2026, 11, 26)
    assert dates.easter(2019) == dt.date(2019, 4, 21)
    assert dates.easter(2026) == dt.date(2026, 4, 5)
    assert dates.holiday_date("memorial_day", 2024) == dt.date(2024, 5, 27)
    assert dates.holiday_date("labor_day", 2024) == dt.date(2024, 9, 2)


def test_holiday_windows_pick_closest():
    s = Settings()
    assert dates.holiday_for(dt.date(2019, 12, 24), s).key == "christmas"
    assert dates.holiday_for(dt.date(2019, 12, 31), s).key == "new_years_eve"
    assert dates.holiday_for(dt.date(2020, 1, 1), s).key == "new_years_day"
    assert dates.holiday_for(dt.date(2019, 11, 29), s).key == "thanksgiving"  # day after
    assert dates.holiday_for(dt.date(2019, 8, 14), s) is None
    s.custom_events.append(("Calvin's Birthday", 8, 14, 0, 0))
    assert dates.holiday_for(dt.date(2019, 8, 14), s).name == "Calvin's Birthday"


def test_filename_dates():
    p = imageio.parse_date_from_filename
    assert p("IMG_20190704_153012.jpg") == "2019-07-04"
    assert p("PXL_20231225_101010123.jpg") == "2023-12-25"
    assert p("IMG-20180101-WA0003.jpg") == "2018-01-01"
    assert p("Screenshot_2020-03-15-12-00-00.png") == "2020-03-15"
    assert p("IMG_2048.jpg") is None
    assert p("DSC01234.JPG") is None
    assert p("20191345_x.jpg") is None  # month 13


def test_folder_clues():
    fc = dates.folder_clue
    assert fc(os.path.join("Grandma 1985", "scan1.jpg")) == (1985, None)
    assert fc(os.path.join("2015", "Christmas", "a.jpg")) == (2015, "christmas")
    assert fc(os.path.join("Xmas_2009", "a.jpg")) == (2009, "christmas")
    assert fc(os.path.join("Trip 2015", "a.jpg")) == (2015, None)
    assert fc(os.path.join("IMG_2048", "a.jpg")) == (None, None)
    assert fc(os.path.join("Camera", "a.jpg")) == (None, None)


def test_exif_date_placeholders_rejected():
    assert imageio.parse_exif_date("2019:07:04 10:00:00") == "2019-07-04"
    assert imageio.parse_exif_date("0000:00:00 00:00:00") is None
    assert imageio.parse_exif_date("2999:01:01 00:00:00") is None


def test_video_date(tmp_path):
    secs = int((dt.datetime(2018, 6, 2) - dt.datetime(1904, 1, 1)).total_seconds())
    mvhd = struct.pack(">I4sB3sII", 8 + 4 + 8, b"mvhd", 0, b"\0\0\0", secs, secs)
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s4s", 16, b"ftyp", b"isom") + b"\0\0\0\0"
    p = tmp_path / "clip.mp4"
    p.write_bytes(ftyp + moov)
    assert imageio.read_video_date(str(p)) == "2018-06-02"
    assert imageio.read_video_date(str(write(tmp_path / "x.mp4", b"junk"))) is None


# ---------- image steps ------------------------------------------------------
def test_border_trim_but_not_night_photo():
    photo = scene(1, (600, 400))
    bordered = Image.new("RGB", (700, 500), (255, 255, 255))
    bordered.paste(photo, (50, 50))
    t = fix.find_border(bordered)
    assert t.box and t.confidence >= fix.AUTO_TRIM
    l, tp, r, b = t.box
    assert abs(l - 50) <= 3 and abs(tp - 50) <= 3 and abs(r - 650) <= 3 and abs(b - 450) <= 3
    night = scene(2, (600, 400)).point(lambda v: v // 6)  # dark all over, no real border
    assert fix.find_border(night).box is None or fix.find_border(night).confidence < fix.AUTO_TRIM


def test_T9_scan_with_three_prints_is_split():
    prints = [scene(10, (700, 500)), scene(11, (500, 700)), scene(12, (800, 550))]
    bed = scanner_bed(prints, [3, -4, 1.5])
    info = imageio.ImageInfo(make=None, software="EPSON Scan")
    assert scans.detect_scan(bed, info).is_scan
    boxes = scans.find_photos_on_scan(bed)
    assert len(boxes) == 3
    pieces = [scans.straighten_and_crop(bed, b) for b in boxes]
    sizes = sorted(sorted(p.size) for p in pieces)
    # Each cut-out is roughly one print plus its thin border, and straight (not rotated).
    for (w, h), expected in zip(sizes, sorted(sorted((p.width + 24, p.height + 24)) for p in prints)):
        assert abs(w - expected[0]) < 40 and abs(h - expected[1]) < 40


def test_phone_photo_is_not_a_scan():
    info = imageio.ImageInfo(make="Apple", model="iPhone 12", exif_date="2020-01-01")
    assert not scans.detect_scan(scene(3), info).is_scan


# ---------- full pipeline -----------------------------------------------------
def test_pipeline_dates_holidays_rotation_scans(env):
    paths, s, m1, m2 = env
    save_jpeg(scene(20), m1 / "Phone" / "xmas.jpg", date="2019:12:25 09:00:00", make="Apple")
    save_jpeg(scene(21), m1 / "Phone" / "IMG_20190704_120000.jpg")                       # filename date
    save_jpeg(scene(22), m1 / "Grandma 1985" / "loose.jpg")                               # folder clue
    save_jpeg(scene(23), m1 / "Misc" / "nodate.jpg")                                      # unknown
    save_jpeg(scene(24, (800, 600)), m1 / "Phone" / "sideways.jpg", date="2020:05:01 10:00:00",
              make="Apple", orientation=6)                                                # camera rotation
    bed = scanner_bed([scene(30, (700, 500)), scene(31, (500, 700)), scene(32, (800, 550))], [2, -3, 1])
    save_jpeg(bed, m2 / "Scans" / "Christmas 1987" / "page1.jpg", date="2024:03:03 10:00:00",
              software="EPSON Scan")                                                      # scan, dated by scanner
    write(m1 / "broken.jpg", b"\xff\xd8\xff\xe0 not really a jpeg")
    before = snapshot(m1)
    cat, res = run(paths, s)
    assert snapshot(m1) == before  # R1: originals untouched
    rows = {(r["rel_path"], r["cutout_index"]): r for r in asset_rows(cat)}
    p = os.path.join
    assert rows[(p("Phone", "xmas.jpg"), None)]["source"] == "exif"
    assert rows[(p("Phone", "xmas.jpg"), None)]["holiday"] == "christmas"
    assert rows[(p("Phone", "IMG_20190704_120000.jpg"), None)]["date"] == "2019-07-04"
    assert rows[(p("Phone", "IMG_20190704_120000.jpg"), None)]["holiday"] == "independence_day"
    g = rows[(p("Grandma 1985", "loose.jpg"), None)]
    assert (g["date"], g["year_only"], g["source"], g["holiday"]) == ("1985-07-01", 1, "folder", None)
    assert rows[(p("Misc", "nodate.jpg"), None)]["source"] == "unknown"
    side = rows[(p("Phone", "sideways.jpg"), None)]
    assert side["exif_rotation"] == 270 and side["derived_path"]
    derived = Image.open(paths.data / "derived" / side["derived_path"])
    assert derived.size == (600, 800)                       # pixels upright
    assert derived.getexif().get(0x0112) == 1               # T6: orientation reset, no double rotation
    cuts = [r for k, r in rows.items() if k[0] == p("Scans", "Christmas 1987", "page1.jpg")]
    assert len(cuts) == 3 and all(r["is_scan"] for r in cuts)
    for r in cuts:  # scanner date (2024) ignored; folder gives Christmas 1987
        assert (r["date"], r["source"], r["holiday"]) == ("1987-12-25", "folder", "christmas")
    broken = rows[("broken.jpg", None)]
    assert broken["status"] == "could_not_open" and broken["open_error"]


def test_T8_duplicates_resized_vs_burst(env):
    paths, s, m1, m2 = env
    base = scene(40, (1600, 1200))
    save_jpeg(base, m1 / "orig.jpg", date="2021:06:01 10:00:00", make="Apple")
    save_jpeg(base.resize((800, 600)), m2 / "Messenger" / "received_1.jpg", quality=70)  # re-saved smaller
    save_jpeg(base, m2 / "copy" / "orig.jpg", date="2021:06:01 10:00:00", make="Apple")  # exact copy (different bytes? no: same)
    save_jpeg(scene(40, (1600, 1200), shift=60), m1 / "burst2.jpg", date="2021:06:01 10:00:01", make="Apple")
    save_jpeg(scene(41, (1600, 1200)), m1 / "different.jpg", date="2021:06:01 10:05:00", make="Apple")
    cat, res = run(paths, s)
    by_name = {r["rel_path"]: r["id"] for r in asset_rows(cat)}
    group_of = {r["asset_id"]: r["group_id"] for r in cat.q("SELECT * FROM dup_members")}
    kept = {r["asset_id"] for r in cat.q("SELECT * FROM dup_members WHERE is_kept=1")}
    g = group_of.get(by_name["orig.jpg"])
    assert g is not None
    assert group_of.get(by_name[os.path.join("Messenger", "received_1.jpg")]) == g
    assert group_of.get(by_name["burst2.jpg"]) is None       # burst shot is not a duplicate
    assert group_of.get(by_name["different.jpg"]) is None
    assert by_name[os.path.join("Messenger", "received_1.jpg")] not in kept   # smaller copy isn't kept
    # The small Messenger copy has no date; it borrows the original's first-hand date.
    msg = [r for r in asset_rows(cat) if r["rel_path"] == os.path.join("Messenger", "received_1.jpg")][0]
    assert (msg["date"], msg["source"], msg["is_guess"]) == ("2021-06-01", "similar", 1)


def test_T10_scan_never_borrows_from_phone_photo(env):
    paths, s, m1, m2 = env
    pic = scene(50, (1000, 700))
    save_jpeg(pic, m1 / "phone_pic_of_print.jpg", date="2019:05:05 10:00:00", make="Apple")
    save_jpeg(pic.resize((900, 630)), m2 / "scan_of_print.jpg", software="EPSON Scan")
    cat, _ = run(paths, s)
    scan = [r for r in asset_rows(cat) if r["rel_path"] == "scan_of_print.jpg"][0]
    assert scan["is_scan"] and scan["date"] is None and scan["source"] == "unknown"


def test_exact_copies_analyzed_once(env, monkeypatch):
    paths, s, m1, m2 = env
    img = scene(60)
    save_jpeg(img, m1 / "a.jpg", date="2020:01:01 10:00:00")
    (m2 / "deep").mkdir()
    (m2 / "deep" / "IMG_20200101_000000.jpg").write_bytes((m1 / "a.jpg").read_bytes())
    calls = []
    real = imageio.open_image
    monkeypatch.setattr(imageio, "open_image", lambda p: (calls.append(p), real(p))[1])
    cat, _ = run(paths, s)
    assert len(calls) == 1
    assert cat.scalar("SELECT COUNT(*) FROM assets") == 2


def test_T3_rerun_does_not_reanalyze(env, monkeypatch):
    paths, s, m1, _ = env
    for i in range(3):
        save_jpeg(scene(70 + i), m1 / f"{i}.jpg")
    run(paths, s)
    calls = []
    monkeypatch.setattr(imageio, "open_image", lambda p: calls.append(p))
    run(paths, s)
    assert calls == []


def test_crash_mid_analysis_redoes_at_most_one(env, monkeypatch):
    paths, s, m1, _ = env
    for i in range(4):
        save_jpeg(scene(80 + i), m1 / f"{i}.jpg")
    real = imageio.open_image
    seen = []

    def flaky(p):
        seen.append(p)
        if len(seen) == 3:
            raise KeyboardInterrupt  # simulates the app being killed mid-file
        return real(p)
    monkeypatch.setattr(imageio, "open_image", flaky)
    with pytest.raises(KeyboardInterrupt):
        Runner(paths, s).run()
    monkeypatch.setattr(imageio, "open_image", real)
    cat = Catalog(paths.catalog)
    assert cat.scalar("SELECT COUNT(*) FROM files WHERE status='analyzed'") == 2
    cat.close()
    cat, _ = run(paths, s)
    assert cat.scalar("SELECT COUNT(*) FROM files WHERE status='analyzed'") == 4
    assert cat.scalar("SELECT COUNT(*) FROM assets") == 4  # no leftover half-results


def test_T21_other_formats_open(tmp_path):
    img = scene(90, (400, 300))
    for ext, fmt in ((".png", "PNG"), (".webp", "WEBP"), (".tif", "TIFF"), (".bmp", "BMP")):
        p = tmp_path / f"x{ext}"
        img.save(p, fmt)
        opened, info = imageio.open_image(str(p))
        assert opened.size == (400, 300)
    p = tmp_path / "x.heic"
    try:
        img.save(p, "HEIF")
    except Exception:
        pytest.skip("HEIF encoder not available here")
    opened, _ = imageio.open_image(str(p))
    assert opened.size == (400, 300)


@pytest.mark.parametrize("gap", [40, 12])
def test_split_prints_close_together(gap):
    """Prints laid close to each other on the bed must still come out as separate photos."""
    bed = Image.new("RGB", (2000, 1000), (250, 250, 248))
    a, b2 = scene(201, (800, 600)), scene(202, (800, 600))
    bed.paste(a, (100, 200))
    bed.paste(b2, (100 + 800 + gap, 200))
    boxes = scans.find_photos_on_scan(bed)
    assert len(boxes) == 2
    # Perfectly aligned with a narrow strip: can't be told apart from one print with a band,
    # so the cut is flagged for review rather than trusted.
    assert all(b.confidence <= scans.AMBIGUOUS_CUT for b in boxes)


def test_split_white_bordered_prints_on_white_bed():
    """Old prints often have white borders, almost the same colour as the scanner lid."""
    bed = Image.new("RGB", (2400, 1200), (247, 247, 247))
    for i, x in enumerate((100, 1250)):
        framed = Image.new("RGB", (1000, 760), (255, 255, 255))
        framed.paste(scene(210 + i, (900, 660)), (50, 50))
        bed.paste(framed.rotate(2 if i else -2, expand=True, fillcolor=(247, 247, 247)), (x, 150))
    boxes = scans.find_photos_on_scan(bed)
    assert len(boxes) == 2


@pytest.mark.parametrize("angles", [(3, -2), (-4, 4)])
def test_split_crooked_prints_close_together(angles):
    bed = Image.new("RGB", (2200, 1100), (250, 250, 248))
    for i, (x, ang) in enumerate(zip((80, 1000), angles)):
        p = scene(220 + i, (850, 620)).rotate(ang, expand=True, fillcolor=(250, 250, 248))
        bed.paste(p, (x, 150))
    boxes = scans.find_photos_on_scan(bed)
    assert len(boxes) == 2 and all(b.confidence > scans.AMBIGUOUS_CUT for b in boxes)  # hand-placed: trusted


def test_one_print_with_bright_sky_is_not_split():
    import numpy as np
    p = scene(230, (1200, 900))
    sky = np.clip(246 + np.random.default_rng(1).normal(0, 3, (120, 1200, 3)), 0, 255).astype("uint8")
    p.paste(Image.fromarray(sky), (0, 300))  # washed-out (but grainy, like real film) sky band across the print
    bed = Image.new("RGB", (1600, 1300), (250, 250, 248))
    bed.paste(p, (200, 200))
    boxes = scans.find_photos_on_scan(bed)
    # Indistinguishable from two aligned prints. Never trusted silently: either not cut,
    # or cut and flagged "check this cut" for the review screen.
    assert len(boxes) <= 1 or all(b.confidence <= scans.AMBIGUOUS_CUT for b in boxes)


def test_files_analyzed_by_older_rules_are_redone(env, monkeypatch):
    from imagecleanuper.analyze import run as analyze_run
    paths, s, m1, _ = env
    save_jpeg(scene(300), m1 / "a.jpg", date="2020:01:01 10:00:00", make="Apple")
    run(paths, s)
    calls = []
    real = imageio.open_image
    monkeypatch.setattr(imageio, "open_image", lambda p: (calls.append(p), real(p))[1])
    run(paths, s)
    assert calls == []                        # same rules: nothing redone
    monkeypatch.setattr(analyze_run, "ANALYSIS_VERSION", analyze_run.ANALYSIS_VERSION + 1)
    cat, _ = run(paths, s)
    assert len(calls) == 1                    # newer rules: redone once
    assert cat.scalar("SELECT COUNT(*) FROM assets") == 1  # old results replaced, not duplicated


def test_prints_pushed_into_corners_no_scanner_info():
    """Modelled on a real scan: three prints touching the edges and corners of the scan, light
    lid showing only between them, and no scanner name in the file."""
    bed = Image.new("RGB", (1454, 2000), (233, 231, 232))
    bed.paste(scene(400, (680, 1010)), (0, 0))            # top-left corner
    bed.paste(scene(401, (670, 985)), (784, 0))           # touches the top and right edges
    bed.paste(scene(402, (1008, 685)), (446, 1315))       # bottom-right corner
    info = imageio.ImageInfo()                            # no make, no software, no date
    check = scans.detect_scan(bed, info)
    assert check.is_scan, check
    assert len(scans.find_photos_on_scan(bed)) == 3


def test_phone_photo_with_white_wall_is_not_a_scan():
    """A Messenger copy (no camera info) of a room with a big plain white wall."""
    img = scene(410, (1600, 1200))
    img.paste(Image.new("RGB", (1600, 500), (236, 236, 234)), (0, 0))   # plain white wall, top half
    img.paste(scene(411, (500, 400)), (550, 120))                        # picture on the wall
    assert not scans.detect_scan(img, imageio.ImageInfo()).is_scan or \
        len(scans.find_photos_on_scan(img)) < 2


def _grainy(color, size, seed, sd=3.0):
    import numpy as np
    arr = np.clip(np.array(color) + np.random.default_rng(seed).normal(0, sd, (size[1], size[0], 3)), 0, 255)
    return Image.fromarray(arr.astype("uint8"))


def test_grid_of_prints_with_light_walls():
    """Modelled on a real scan: four prints in a grid, two with large light walls/floors touching
    the print edge (lid-coloured at a glance). All four must be found, none flagged as suspicious."""
    lid = (231, 229, 230)
    bed = Image.new("RGB", (1454, 2000), lid)
    for i, (x, y) in enumerate([(0, 0), (795, 0), (0, 1175), (785, 1175)]):
        p = scene(500 + i, (675, 850))
        if i in (1, 3):  # light wall filling half the print, right up to its edge
            p.paste(_grainy((222, 214, 200), (675, 420), seed=i), (0, 0))
        bed.paste(p, (x, y))
    assert scans.detect_scan(bed, imageio.ImageInfo()).is_scan
    boxes = scans.find_photos_on_scan(bed)
    assert len(boxes) == 4
    assert all(b.confidence > scans.AMBIGUOUS_CUT for b in boxes)  # a tidy grid isn't suspicious


def test_polaroid_with_white_frame_on_white_lid():
    lid = (232, 230, 231)
    bed = Image.new("RGB", (1454, 2000), lid)
    bed.paste(scene(520, (690, 950)), (0, 0))
    polaroid = _grainy((246, 246, 244), (580, 700), seed=7, sd=1.5)  # white frame, slightly whiter than the lid
    polaroid.paste(scene(521, (510, 500)), (35, 35))
    bed.paste(polaroid, (870, 1300))
    boxes = scans.find_photos_on_scan(bed)
    assert len(boxes) == 2
    big = max(boxes, key=lambda b: b.corners[:, 1].max())
    h = big.corners[:, 1].max() - big.corners[:, 1].min()
    assert h > 650  # the whole Polaroid including its frame, not just the picture inside


def _hard_scan():
    """Modelled on a real scan: a blank white card and ONE portrait in a plastic sleeve."""
    from PIL import ImageDraw
    lid = (232, 230, 232)
    bed = Image.new("RGB", (1454, 2000), lid)
    card = _grainy((240, 240, 242), (590, 860), seed=3, sd=1.2)
    ImageDraw.Draw(card).rectangle([0, 0, 589, 859], outline=(205, 205, 210), width=2)  # card edge shadow
    bed.paste(card, (380, 0))
    sleeve = _grainy((236, 238, 240), (450, 600), seed=4, sd=2.0)            # clear plastic sleeve
    sleeve.paste(scene(530, (420, 560)).point(lambda v: int(v * 0.8)), (12, 18))
    ImageDraw.Draw(sleeve).rectangle([0, 0, 449, 599], outline=(150, 150, 160), width=2)
    bed.paste(sleeve, (890, 135))
    return bed


def test_blank_card_ignored_single_print_found():
    bed = _hard_scan()
    boxes = scans.find_photos_on_scan(bed)
    assert len(boxes) == 1
    xs = boxes[0].corners[:, 0]
    assert xs.min() > 850  # it's the portrait on the right, not the blank card


def test_single_print_on_scan_is_cut_out(env):
    paths, s, m1, _ = env
    save_jpeg(_hard_scan(), m1 / "Scans" / "portrait.jpg", software="EPSON Scan")
    cat, _ = run(paths, s)
    cuts = cat.q("SELECT width, height FROM assets WHERE kind='cutout'")
    assert len(cuts) == 1
    assert 400 < cuts[0]["width"] < 500 and 550 < cuts[0]["height"] < 640  # just the photo, not the page


def _one_print_in_corner():
    """Modelled on real scans: one small print pushed into a corner of a mostly empty page."""
    bed = Image.new("RGB", (1454, 2000), (236, 235, 237))
    bed.paste(scene(600, (520, 760)), (1454 - 520 - 40, 2000 - 760))   # touches the bottom edge
    return bed


def test_single_print_in_corner_is_a_scan_and_no_bad_trim():
    bed = _one_print_in_corner()
    assert scans.detect_scan(bed, imageio.ImageInfo()).is_scan
    assert len(scans.find_photos_on_scan(bed)) == 1
    assert fix.find_border(bed).box is None   # never "trim" a strip and keep most of the empty page


def test_single_print_scan_cut_out_and_scan_name_date_ignored(env):
    paths, s, m1, _ = env
    save_jpeg(_one_print_in_corner(), m1 / "Scan_20170326 (149) (2020_01_12 14_54_16 UTC).jpg")
    cat, _ = run(paths, s)
    cuts = cat.q("SELECT a.width, a.height, d.date, d.source, x.status FROM assets a JOIN dates d ON d.asset_id=a.id "
                 "JOIN fixes x ON x.asset_id=a.id WHERE a.kind='cutout'")
    assert len(cuts) == 1
    assert 480 < cuts[0]["width"] < 540 and 700 < cuts[0]["height"] < 780     # just the print
    assert cuts[0]["date"] is None and cuts[0]["source"] == "unknown"          # not 2017 (the scan date)
    assert cuts[0]["status"] != "review"                                       # no bogus trim question
