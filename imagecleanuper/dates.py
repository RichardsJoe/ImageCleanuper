"""Dates and holidays (pseudocode section 10)."""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass

from .catalog import Catalog
from .settings import Settings

HOLIDAY_NAMES = {
    "new_years_day": "New Year's Day",
    "valentines_day": "Valentine's Day",
    "easter": "Easter",
    "memorial_day": "Memorial Day",
    "independence_day": "Fourth of July",
    "labor_day": "Labor Day",
    "halloween": "Halloween",
    "thanksgiving": "Thanksgiving",
    "christmas": "Christmas",
    "new_years_eve": "New Year's Eve",
}

# Words in folder names that name a holiday (checked longest first).
FOLDER_HOLIDAY_WORDS = [
    ("new years eve", "new_years_eve"), ("new year's eve", "new_years_eve"), ("nye", "new_years_eve"),
    ("new years", "new_years_day"), ("new year's", "new_years_day"), ("new year", "new_years_day"),
    ("christmas", "christmas"), ("xmas", "christmas"), ("x-mas", "christmas"),
    ("thanksgiving", "thanksgiving"), ("halloween", "halloween"), ("easter", "easter"),
    ("fourth of july", "independence_day"), ("4th of july", "independence_day"),
    ("july 4th", "independence_day"), ("july 4", "independence_day"), ("independence day", "independence_day"),
    ("valentine", "valentines_day"), ("memorial day", "memorial_day"), ("labor day", "labor_day"),
]


def easter(year: int) -> dt.date:
    """Gregorian Easter (anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th weekday (0=Mon) of a month; n=-1 for the last one."""
    if n > 0:
        d = dt.date(year, month, 1)
        d += dt.timedelta(days=(weekday - d.weekday()) % 7)
        return d + dt.timedelta(weeks=n - 1)
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def holiday_date(key: str, year: int) -> dt.date:
    """Holidays that move (Thanksgiving, Easter, Memorial Day...) are computed per year."""
    return {
        "new_years_day": lambda: dt.date(year, 1, 1),
        "valentines_day": lambda: dt.date(year, 2, 14),
        "easter": lambda: easter(year),
        "memorial_day": lambda: _nth_weekday(year, 5, 0, -1),
        "independence_day": lambda: dt.date(year, 7, 4),
        "labor_day": lambda: _nth_weekday(year, 9, 0, 1),
        "halloween": lambda: dt.date(year, 10, 31),
        "thanksgiving": lambda: _nth_weekday(year, 11, 3, 4),
        "christmas": lambda: dt.date(year, 12, 25),
        "new_years_eve": lambda: dt.date(year, 12, 31),
    }[key]()


@dataclass(frozen=True)
class Event:
    key: str
    name: str
    day: dt.date
    before: int
    after: int


def events_for_year(year: int, s: Settings) -> list[Event]:
    out = []
    for key, (before, after) in s.holiday_windows.items():
        if key in HOLIDAY_NAMES:
            out.append(Event(key, HOLIDAY_NAMES[key], holiday_date(key, year), before, after))
    for name, month, day, before, after in s.custom_events:
        try:
            d = dt.date(year, month, day)
        except ValueError:  # Feb 29 in a non-leap year
            d = dt.date(year, 2, 28)
        out.append(Event("event:" + name, name, d, before, after))
    return out


def holiday_for(date: dt.date, s: Settings) -> Event | None:
    """The holiday whose window contains the date; overlapping windows go to the closest day."""
    hits = []
    order = 0
    for year in (date.year - 1, date.year, date.year + 1):  # windows can cross New Year
        for ev in events_for_year(year, s):
            order += 1
            if ev.day - dt.timedelta(days=ev.before) <= date <= ev.day + dt.timedelta(days=ev.after):
                hits.append((abs((date - ev.day).days), order, ev))
    return min(hits)[2] if hits else None


# ---- folder-name clues -----------------------------------------------------------
_YEAR = re.compile(r"(?<![0-9])(1[89][0-9]{2}|20[0-9]{2})(?![0-9])")
# Only a whole word before the number counts ("Trip 2015" is a year; "IMG_2015" is a file number).
_CAMERA_PREFIX = re.compile(r"(?i)(?:^|[^a-z])(img|dsc|dscn|dscf|dcim|vid|pxl|mvi|cimg|imag|pict|p)[_\-\s]?$")


def folder_clue(rel_path: str) -> tuple[int | None, str | None]:
    """(year, holiday key) from folder names, nearest folder first. Only for undated photos."""
    parts = os.path.normpath(rel_path).split(os.sep)[:-1]
    year = None
    holiday = None
    for folder in reversed(parts):
        low = folder.lower().replace("_", " ")
        if holiday is None:
            for word, key in FOLDER_HOLIDAY_WORDS:
                if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", low):
                    holiday = key
                    break
        if year is None:
            for m in _YEAR.finditer(folder):
                y = int(m.group(1))
                if y > dt.date.today().year:
                    continue
                if _CAMERA_PREFIX.search(folder[:m.start()]):
                    continue  # IMG_2048 is a file number, not a year
                year = y
                break
        if year is not None:
            break  # the nearest folder with a year wins; holiday must come from it or nearer
    return year, holiday


# ---- the date chain --------------------------------------------------------------
@dataclass
class Resolved:
    date: str | None
    source: str
    year_only: bool = False
    is_guess: bool = False
    borrowed_from: int | None = None
    folder_holiday: str | None = None


FIRST_HAND = ("exif", "google", "filename")


def resolve_all(cat: Catalog, s: Settings) -> dict:
    """Work out a date and holiday for every asset (cheap; redone every run)."""
    rows = cat.q(
        """
        SELECT a.id, a.file_id, f.rel_path, f.kind AS file_kind, m.exif_date, m.google_date, m.filename_date,
               m.is_scan
        FROM assets a JOIN files f ON f.id = a.file_id LEFT JOIN meta m ON m.asset_id = a.id
        WHERE f.missing = 0
        """)
    first: dict[int, Resolved] = {}
    for r in rows:
        first[r["id"]] = _first_hand(r)
    similar = _similar_map(cat)
    counts: dict[str, int] = {}
    with cat.tx():
        cat.db.execute("DELETE FROM dates")
        cat.db.execute("DELETE FROM holidays")
        scan_of = {r["id"]: bool(r["is_scan"]) for r in rows}
        for r in rows:
            res = first[r["id"]]
            if res.date is None:
                res = _borrow(r["id"], scan_of, first, similar) or _from_folder(r) or Resolved(None, "unknown")
            counts[res.source] = counts.get(res.source, 0) + 1
            cat.db.execute(
                "INSERT INTO dates(asset_id, date, year_only, source, borrowed_from, is_guess) VALUES (?,?,?,?,?,?)",
                (r["id"], res.date, int(res.year_only), res.source, res.borrowed_from, int(res.is_guess)))
            hol, src = None, None
            if res.folder_holiday:
                hol, src = res.folder_holiday, "folder"
            elif res.date and not res.year_only:
                ev = holiday_for(dt.date.fromisoformat(res.date), s)
                if ev:
                    hol, src = ev.key, "date"
            if hol:
                cat.db.execute("INSERT INTO holidays(asset_id, holiday, source) VALUES (?,?,?)", (r["id"], hol, src))
                counts["holiday"] = counts.get("holiday", 0) + 1
    return counts


def _first_hand(r) -> Resolved:
    if r["is_scan"]:
        # Scanner dates are the day it was scanned, not when the photo was taken.
        if r["google_date"]:
            return Resolved(r["google_date"], "google")
        return Resolved(None, "unknown")
    if r["exif_date"]:
        return Resolved(r["exif_date"], "exif")  # camera dates trusted as they are
    if r["google_date"]:
        return Resolved(r["google_date"], "google")
    if r["filename_date"]:
        return Resolved(r["filename_date"], "filename")
    return Resolved(None, "unknown")


def _similar_map(cat: Catalog) -> dict[int, list[int]]:
    """Highly similar photos. Phase 2: members of the same duplicate group. Phase 3 adds
    'same event' matches from the image model, with a cutoff tuned to 95% by the accuracy check."""
    groups: dict[int, list[int]] = {}
    for r in cat.q("SELECT group_id, asset_id FROM dup_members ORDER BY asset_id"):
        groups.setdefault(r["group_id"], []).append(r["asset_id"])
    out: dict[int, list[int]] = {}
    for members in groups.values():
        for a in members:
            out[a] = [b for b in members if b != a]
    return out


def _borrow(aid: int, scan_of: dict, first: dict, similar: dict) -> Resolved | None:
    for other in similar.get(aid, []):
        o = first.get(other)
        if not o or o.source not in FIRST_HAND or not o.date:
            continue  # no chains of guesses: only borrow first-hand dates
        if scan_of.get(aid) and not scan_of.get(other):
            continue  # a scan never borrows a modern photo's date
        return Resolved(o.date, "similar", is_guess=True, borrowed_from=other)
    return None


def _from_folder(r) -> Resolved | None:
    year, holiday = folder_clue(r["rel_path"])
    if year is None:
        return None
    if holiday:
        return Resolved(holiday_date(holiday, year).isoformat(), "folder", is_guess=True, folder_holiday=holiday)
    return Resolved(dt.date(year, 7, 1).isoformat(), "folder", year_only=True, is_guess=True)
