"""The app window (pseudocode section 24). Built exactly to docs/WIREFRAMES.md v4.

Rules: long work runs on a worker thread; only this (UI) thread touches widgets.
Worker → UI messages go through a queue polled every 100 ms.
"""

from __future__ import annotations

import os
import queue
import threading
import traceback
from tkinter import filedialog

import customtkinter as ctk

from .. import __version__, log, settings as settings_mod
from ..app import AppPaths, Runner, startup, summary
from ..catalog import Catalog
from ..learned import restore_learned_data
from ..log import UserError
from ..restore import restore_files

BLUE = "#2f6fdb"
GREEN = "#2f8f5b"
AMBER = "#b7791f"
MUTED = ("#5d6a73", "#9aa5ad")
WRAP = 680  # text width in the middle column


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


class App(ctk.CTk):
    def __init__(self, paths: AppPaths):
        super().__init__()
        self.paths = paths
        self.title(f"ImageCleanuper {__version__}")
        self.geometry("1280x820")
        self.minsize(900, 600)
        self.after(0, self._maximise)
        self.msgs: queue.Queue = queue.Queue()
        self.runner: Runner | None = None
        self.state_info = startup(paths)
        self.settings = self.state_info.settings
        self.body: ctk.CTkFrame | None = None
        self.bind_all("<F1>", lambda e: self.show_hotkeys())
        self.after(100, self._poll)
        if self.state_info.catalog_damaged:
            self.show_damaged()
        elif not self.settings.is_configured:
            self.show_setup_folders()
        else:
            self.show_home()
            if self.state_info.crashed_run:
                self.after(300, self.ask_resume)

    def _maximise(self):
        try:
            self.state("zoomed")              # Windows
        except Exception:
            try:
                self.attributes("-zoomed", True)   # Linux
            except Exception:
                pass

    # ---- helpers ----------------------------------------------------------
    NAV_ITEMS = (("home", "Home"), ("run", "Run  (R)"), ("review", "Review  (V)"), ("report", "Report  (P)"),
                 ("finish", "Finish  (F)"), ("restore", "Restore"), ("settings", "Settings"))

    def _screen(self, title: str, subtitle: str = "", nav: str | None = "home",
                nav_locked: bool = False, review_count: int | None = None) -> ctk.CTkFrame:
        """Every screen: optional nav bar on the left, a title, a scrolling middle and a footer
        pinned to the bottom (so buttons are always visible, however small the window)."""
        if self.body is not None:
            self.body.destroy()
        for seq in ("<Return>", "<Escape>", "<Key-r>", "<Key-a>", "<Key-u>", "<Key-s>", "<Key-n>", "<space>",
                    "<Left>", "<Right>", "<Control-z>", "<Key-d>", "<Key-k>",
                    *[f"<Key-{i}>" for i in range(1, 10)]):
            self.unbind(seq)
        self._images = []
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True)
        if nav is not None:
            self._nav(self.body, nav, nav_locked, review_count)
        main = ctk.CTkFrame(self.body, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True, padx=32, pady=(20, 16))
        ctk.CTkLabel(main, text=title, font=ctk.CTkFont(size=22, weight="bold"), anchor="w").pack(fill="x")
        if subtitle:
            ctk.CTkLabel(main, text=subtitle, text_color=MUTED, anchor="w", justify="left",
                         wraplength=WRAP).pack(fill="x", pady=(4, 8))
        self.footer_hint = ctk.CTkFrame(main, fg_color="transparent", height=1)
        self.footer_hint.pack(side="bottom", fill="x")                  # hotkey line, under the buttons
        self.footer_area = ctk.CTkFrame(main, fg_color="transparent", height=1)
        self.footer_area.pack(side="bottom", fill="x", pady=(12, 0))   # packed before content: always visible
        content = ctk.CTkScrollableFrame(main, fg_color="transparent")
        content.pack(fill="both", expand=True)
        return content

    def _nav(self, parent, current: str, locked: bool, review_count: int | None = None) -> None:
        bar = ctk.CTkFrame(parent, width=170, corner_radius=0)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="ImageCleanuper", font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=16, pady=(20, 16))
        targets = {"home": self.show_home, "run": self.start_run, "review": self.show_review,
                   "report": self.show_report, "finish": self.show_finish, "restore": self.show_restore,
                   "settings": self.show_settings}
        if review_count is None and not locked:
            review_count = self._counts().get("review", 0)
        for key, label in self.NAV_ITEMS:
            active = key == current
            if key == "review" and review_count:
                label = f"{label}   {review_count}"
            ctk.CTkButton(bar, text=label, anchor="w", height=36, corner_radius=6,
                          fg_color=BLUE if active else "transparent",
                          text_color="white" if active else ("gray15", "gray90"),
                          hover_color=("gray80", "gray30") if not active else BLUE,
                          state="disabled" if locked and not active else "normal",
                          command=targets[key]).pack(fill="x", padx=10, pady=2)
        if locked:
            ctk.CTkLabel(bar, text="Pause the run to\nuse the other pages.", text_color=MUTED,
                         justify="left", anchor="w").pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkLabel(bar, text=f"Version {__version__}\nF1  Hotkeys", text_color=MUTED, justify="left",
                     anchor="w").pack(side="bottom", fill="x", padx=16, pady=16)
        if not locked:   # nav hotkeys work on every main screen
            for keysym, fn in (("<Key-v>", self.show_review), ("<Key-p>", self.show_report),
                               ("<Key-f>", self.show_finish)):
                self.bind(keysym, self._not_typing(fn))
            if current not in ("review",):
                self.bind("<Key-r>", self._not_typing(self.start_run))

    def _footer(self, back=None, primary=None):
        bar = self.footer_area
        if back:
            label, cmd = back
            ctk.CTkButton(bar, text=f"{label} (Esc)", fg_color="transparent", border_width=1,
                          text_color=("gray20", "gray85"), command=cmd, width=140).pack(side="left")
            self.bind("<Escape>", lambda e: cmd())
        if primary:
            label, cmd = primary
            ctk.CTkButton(bar, text=f"{label} (Enter)", fg_color=BLUE, command=cmd,
                          width=200, height=38).pack(side="right")
            self.bind("<Return>", lambda e: cmd())
        return bar

    def _not_typing(self, fn):
        """Hotkeys must not fire while typing in a text box (bug trap, section 24)."""
        def handler(event):
            if "entry" in str(self.focus_get()).lower():
                return
            fn()
        return handler

    def error(self, e: Exception, title: str = "Something needs attention"):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("560x260")
        win.transient(self)
        msg = e.message if isinstance(e, UserError) else "Something went wrong. Nothing was lost."
        todo = e.what_to_do if isinstance(e, UserError) else "Try again. If it keeps happening, copy the details and report it."
        details = (e.details if isinstance(e, UserError) else "") or "".join(traceback.format_exception(e))
        ctk.CTkLabel(win, text=msg, font=ctk.CTkFont(size=15, weight="bold"), wraplength=500,
                     justify="left").pack(padx=24, pady=(24, 8), anchor="w")
        ctk.CTkLabel(win, text=todo, wraplength=500, justify="left").pack(padx=24, anchor="w")
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=24, pady=20)

        def copy():
            self.clipboard_clear()
            self.clipboard_append(details)
        ctk.CTkButton(bar, text="Copy details", fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), command=copy).pack(side="left")
        ctk.CTkButton(bar, text="OK (Enter)", command=win.destroy).pack(side="right")
        win.bind("<Return>", lambda ev: win.destroy())
        win.bind("<Escape>", lambda ev: win.destroy())
        win.after(50, win.grab_set)

    # ---- setup step 1: photo folders -----------------------------------------
    def show_setup_folders(self):
        b = self._screen("Where are your photos?", nav=None, subtitle=
                         "Add every top folder that has photos in it. Subfolders are searched automatically, "
                         "however deep. Old scanned photos are found automatically.")
        lst = ctk.CTkScrollableFrame(b, height=220)
        lst.pack(fill="x", pady=(0, 10))

        def redraw():
            for w in lst.winfo_children():
                w.destroy()
            if not self.settings.masters:
                ctk.CTkLabel(lst, text="No folders yet. Press Add a folder.", text_color=MUTED).pack(pady=20)
            for m in self.settings.masters:
                row = ctk.CTkFrame(lst)
                row.pack(fill="x", pady=3)
                ctk.CTkLabel(row, text="📁  " + m, anchor="w").pack(side="left", padx=10, pady=6)
                ctk.CTkButton(row, text="Remove", width=80, fg_color="transparent", border_width=1,
                              text_color=("gray20", "gray85"),
                              command=lambda m=m: (self.settings.masters.remove(m), redraw())).pack(side="right", padx=8)

        def add():
            d = filedialog.askdirectory(title="Choose a folder with photos")
            if d:
                d = os.path.normpath(d)
                if d not in self.settings.masters:
                    self.settings.masters.append(d)
                redraw()
        ctk.CTkButton(b, text="+ Add a folder (A)", command=add, width=200).pack(anchor="w")
        self.bind("<Key-a>", self._not_typing(add))
        g = ctk.CTkFrame(b)
        g.pack(fill="x", pady=16)
        ctk.CTkLabel(g, text="Also have photos in Google Photos?", font=ctk.CTkFont(weight="bold"),
                     anchor="w").pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkLabel(g, text="Coming in a later version. You'll be able to add them from Home.",
                     text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=(0, 12))
        redraw()

        def nxt():
            if not self.settings.masters:
                self.error(UserError("Add at least one photo folder first."))
                return
            self.show_setup_locations()
        self._footer(primary=("Next", nxt))

    # ---- setup step 2: library and backup -----------------------------------
    def _location_fields(self, parent, s: settings_mod.Settings):
        """Library + backup boxes. Problems appear under the box they're about, in orange,
        with a one-click fix when there is an obvious one. Returns check() -> bool."""
        widgets = {}
        for key, label, hint in (
                ("library", "Sorted library", "Your finished, sorted photos. Put it next to your photo folders, not inside."),
                ("backup", "Backup folder", "A verified zip of every original is saved here before anything else.")):
            ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=15, weight="bold"), anchor="w").pack(fill="x", pady=(12, 0))
            ctk.CTkLabel(parent, text=hint, text_color=MUTED, anchor="w").pack(fill="x")
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=4)
            e = ctk.CTkEntry(row)
            e.insert(0, getattr(s, key) or "")
            e.pack(side="left", fill="x", expand=True)
            normal_border = e.cget("border_color")

            def browse(e=e, label=label):
                d = filedialog.askdirectory(title=f"Choose the {label.lower()}")
                if d:
                    e.delete(0, "end")
                    e.insert(0, os.path.normpath(d))
                    check()
            ctk.CTkButton(row, text="Browse…", width=110, command=browse).pack(side="left", padx=(8, 0))
            msg_row = ctk.CTkFrame(parent, fg_color="transparent", height=1)  # empty frames default to 200px tall
            msg_row.pack(fill="x")
            widgets[key] = (e, msg_row, normal_border)

        def check() -> bool:
            s.library = widgets["library"][0].get().strip()
            s.backup = widgets["backup"][0].get().strip()
            problems = [p for p in settings_mod.check(s) if p.blocking]
            ok = True
            for key, (e, msg_row, normal_border) in widgets.items():
                for w in msg_row.winfo_children():
                    w.destroy()
                mine = [p for p in problems if p.field == key]
                if not getattr(s, key):
                    mine.insert(0, settings_mod.Problem(key, "Choose a folder."))
                e.configure(border_color=AMBER if mine else normal_border)
                for p in mine[:2]:
                    ok = False
                    ctk.CTkLabel(msg_row, text=p.message, text_color=AMBER, anchor="w", justify="left",
                                 wraplength=WRAP).pack(fill="x")
                    if p.suggestion:
                        def use(e=e, v=p.suggestion):
                            e.delete(0, "end")
                            e.insert(0, v)
                            check()
                        ctk.CTkButton(msg_row, text=f"Use {p.suggestion} instead", anchor="w",
                                      fg_color="transparent", border_width=1, border_color=AMBER,
                                      text_color=("gray15", "gray90"), command=use).pack(anchor="w", pady=(4, 0))
            return ok

        for e, _, _ in widgets.values():
            e.bind("<FocusOut>", lambda ev: check())
        return check

    def show_setup_locations(self):
        b = self._screen("Where should things go?", nav=None)
        check = self._location_fields(b, self.settings)

        def save_and_next():
            if not check():
                return
            settings_mod.save(self.settings, self.paths.settings)
            self.show_setup_ready()
        self._footer(back=("Back", self.show_setup_folders), primary=("Next", save_and_next))

    # ---- settings (from Home) --------------------------------------------------
    def _settings_tabs(self, parent):
        """Tabs from wireframe 14. Only Folders exists so far; the others arrive with their phases."""
        tabs = ctk.CTkFrame(parent, fg_color="transparent")
        tabs.pack(fill="x", pady=(0, 12))
        for name, ready in (("Folders", True), ("Holidays & categories", False), ("Accuracy", False),
                            ("Hotkeys", False), ("Claude", False)):
            ctk.CTkButton(tabs, text=name if ready else f"{name} · later", height=30, corner_radius=6,
                          fg_color=BLUE if ready else "transparent", border_width=0 if ready else 1,
                          text_color="white" if ready else ("gray50", "gray60"),
                          state="normal" if ready else "disabled", width=10).pack(side="left", padx=(0, 6))

    def show_settings(self):
        """Everything on one screen. Works on a copy: Cancel throws changes away."""
        import copy
        s = copy.deepcopy(self.settings)
        b = self._screen("Settings", nav="settings")
        self._settings_tabs(b)
        ctk.CTkLabel(b, text="Photo folders", font=ctk.CTkFont(size=15, weight="bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(b, text="Every top folder with photos in it. Subfolders are searched automatically.",
                     text_color=MUTED, anchor="w").pack(fill="x")
        lst = ctk.CTkFrame(b)
        lst.pack(fill="x", pady=4)
        masters_msg = ctk.CTkFrame(b, fg_color="transparent", height=1)
        masters_msg.pack(fill="x")

        def redraw():
            for w in lst.winfo_children():
                w.destroy()
            for m in s.masters:
                row = ctk.CTkFrame(lst, fg_color="transparent")
                row.pack(fill="x", padx=6, pady=2)
                ctk.CTkLabel(row, text="📁  " + m, anchor="w").pack(side="left", padx=6)
                ctk.CTkButton(row, text="Remove", width=80, fg_color="transparent", border_width=1,
                              text_color=("gray20", "gray85"),
                              command=lambda m=m: (s.masters.remove(m), redraw())).pack(side="right")
            row = ctk.CTkFrame(lst, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=6)

            def add():
                d = filedialog.askdirectory(title="Choose a folder with photos")
                if d and os.path.normpath(d) not in s.masters:
                    s.masters.append(os.path.normpath(d))
                    redraw()
            ctk.CTkButton(row, text="+ Add a folder", width=160, command=add).pack(side="left")

        redraw()
        check_locations = self._location_fields(b, s)

        def save():
            ok = check_locations()
            for w in masters_msg.winfo_children():
                w.destroy()
            mprobs = [p for p in settings_mod.check(s) if p.field == "masters" and p.blocking]
            if not s.masters:
                mprobs.insert(0, settings_mod.Problem("masters", "Add at least one photo folder."))
            for p in mprobs[:3]:
                ctk.CTkLabel(masters_msg, text=p.message, text_color=AMBER, anchor="w", justify="left",
                             wraplength=WRAP).pack(fill="x")
            if not ok or mprobs:
                return
            from ..paths import same_path
            changed = (len(s.masters) != len(self.settings.masters)
                       or not all(any(same_path(a, b) for b in self.settings.masters) for a in s.masters))
            settings_mod.save(s, self.paths.settings)
            self.settings, self.state_info.problems = settings_mod.load(self.paths.settings)
            if changed:
                self.folders_changed = True
            self.show_home()
        self._footer(back=("Cancel", self.show_home), primary=("Save", save))

    # ---- setup step 3: ready ---------------------------------------------------
    def show_setup_ready(self):
        b = self._screen("Ready for your first run", nav=None, subtitle=
                         "Your photos are not moved or changed in this version. Sorting comes in a later version.")
        for n, title, text in (("1", "Find", "Every photo and video in your folders, however deeply nested."),
                               ("2", "Back up", "Everything is zipped to your backup folder and checked file by file."),
                               ("3", "Look", "Dates, holidays, duplicates, old scans (cut apart), rotation and borders."),
                               ("4", "Done", "You can stop any time; the next run continues where it stopped.")):
            row = ctk.CTkFrame(b, fg_color="transparent")
            row.pack(fill="x", pady=8)
            ctk.CTkLabel(row, text=n, width=36, height=36, corner_radius=18, fg_color=BLUE,
                         text_color="white").pack(side="left")
            col = ctk.CTkFrame(row, fg_color="transparent")
            col.pack(side="left", padx=12)
            ctk.CTkLabel(col, text=title, font=ctk.CTkFont(size=15, weight="bold"), anchor="w").pack(fill="x")
            ctk.CTkLabel(col, text=text, text_color=MUTED, anchor="w").pack(fill="x")
        self._footer(back=("Back", self.show_setup_locations), primary=("Start first run", self.start_run))

    # ---- thumbnails ------------------------------------------------------------
    def _thumb(self, path: str | None, box: tuple[int, int], crop=None):
        """Small preview image, cached in data\\thumbs so pages open quickly next time."""
        import hashlib
        from PIL import Image, ImageDraw
        from ..analyze.imageio import open_image, upright
        from ..paths import long_path
        if not path or not os.path.exists(long_path(path)):
            return None
        cache = self.paths.data / "thumbs"
        cache.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(f"{path}|{os.path.getmtime(long_path(path))}|{box}|{crop}".encode()).hexdigest()
        p = cache / f"{key}.png"
        try:
            if p.exists():
                img = Image.open(p)
                img.load()
            else:
                img, info = open_image(path)
                img, _ = upright(img, info.orientation)
                img = img.convert("RGB")
                if crop and crop[0] == "crop":
                    img = img.crop(crop[1])
                if crop and crop[0] == "rotate":
                    img = img.rotate(crop[1], expand=True)
                if crop and crop[0] == "circle":   # circle the face being asked about
                    x, y, w, h = crop[1]
                    pad = max(w, h) * 0.35
                    ImageDraw.Draw(img).ellipse([x - pad, y - pad, x + w + pad, y + h + pad], outline=(47, 111, 219),
                                                width=max(4, int(max(img.size) / 150)))
                scale = min(box[0] / img.width, box[1] / img.height, 1.0)
                if crop and crop[0] == "outline":   # draw the proposed trim on the original
                    x0, y0, x1, y1 = crop[1]
                    ImageDraw.Draw(img).rectangle([x0, y0, x1, y1], outline=(230, 120, 20),
                                                  width=max(3, int(4 / max(scale, 0.05))))
                img.thumbnail(box)
                img.save(p)
        except Exception:
            return None
        return ctk.CTkImage(light_image=img, dark_image=img, size=img.size)

    def _image_box(self, parent, path, box, label="", crop=None):
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        im = self._thumb(path, box, crop)
        if im is not None:
            self._images.append(im)
            ctk.CTkLabel(cell, image=im, text="").pack()
        else:
            ctk.CTkLabel(cell, text="(no preview)", width=box[0], height=box[1] // 2,
                         fg_color=("gray90", "gray20"), text_color=MUTED).pack()
        if label:
            ctk.CTkLabel(cell, text=label, text_color=MUTED, font=ctk.CTkFont(size=12)).pack()
        return cell

    def _hotkey_line(self, text: str):
        ctk.CTkLabel(self.footer_hint, text=text, text_color=MUTED, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(fill="x", pady=(6, 0))

    def _counts(self) -> dict:
        from .. import review as review_mod
        info = summary(self.paths)
        if self.paths.catalog.exists():
            cat = Catalog(self.paths.catalog)
            try:
                from .. import people as people_mod
                info["fixes"] = review_mod.count(cat)
                info["who"] = len(people_mod.groups(cat, limit=self.settings.people_per_round))
                from .. import copies as copies_mod
                info["copies"] = copies_mod.count_undecided(cat)
                info["review"] = info["fixes"] + info["who"] + info["copies"]
            finally:
                cat.close()
        else:
            info["review"] = info["fixes"] = info["who"] = info["copies"] = 0
        return info

    # ---- home (wireframe 05) ----------------------------------------------------
    def _next_step(self, info):
        last = info["last_run"]
        if getattr(self, "folders_changed", False):
            return ("Scan your updated folders", "Your photo folders changed. Run to pick up the new ones; "
                    "removed folders no longer count (their backups are kept).", "Start run", self.start_run)
        if last is None:
            return ("Run your first scan", "Backs up every photo and video, then works out dates, duplicates "
                    "and old scans.", "Start run", self.start_run)
        if last["state"] in ("crashed", "stopped"):
            return ("Continue the last run", "It stopped early. Nothing was lost.", "Continue", self.start_run)
        if info.get("who"):
            return (f"Name {info['who']:,} {'person' if info['who'] == 1 else 'people'} the app found",
                    "One person at a time: type a name, or press Stranger.", "Start review", self.show_review)
        if info.get("review"):
            return (f"Review {plural(info['review'], 'photo')} the app wasn't sure about",
                    "Then run again so it learns from your answers.", "Start review", self.show_review)
        return ("See where every photo would go", "Check the plan in the report. Sorting itself arrives in "
                "version 0.4.", "Open report", self.show_report)

    def show_home(self):
        import datetime as dt
        info = self._counts()
        b = self._screen("Home", nav="home", review_count=info["review"])
        title, sub, button, action = self._next_step(info)
        nxt = ctk.CTkFrame(b, border_width=1, border_color=BLUE, fg_color=("#eef4ff", "#1e2a3a"))
        nxt.pack(fill="x", pady=(4, 16))
        inner = ctk.CTkFrame(nxt, fg_color="transparent")
        inner.pack(side="left", fill="x", expand=True, padx=20, pady=14)
        ctk.CTkLabel(inner, text="NEXT STEP", text_color=BLUE, font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(inner, text=title, font=ctk.CTkFont(size=19, weight="bold"), anchor="w",
                     justify="left", wraplength=WRAP - 260).pack(fill="x")
        ctk.CTkLabel(inner, text=sub, text_color=MUTED, anchor="w", justify="left",
                     wraplength=WRAP - 260).pack(fill="x")
        ctk.CTkButton(nxt, text=f"{button}  (Enter)", fg_color=BLUE, height=44, width=200,
                      command=action).pack(side="right", padx=20)
        self.bind("<Return>", lambda e: action())

        ctk.CTkLabel(b, text="Waiting for you", font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x")
        chips = ctk.CTkFrame(b, fg_color="transparent")
        chips.pack(fill="x", pady=(4, 16))
        shown = False
        for label, n, cmd in (("Who is this?", info.get("who", 0), self.show_review),
                              ("Needs review", info.get("fixes", 0), lambda: self.show_review(skip_people=True)),
                              ("Duplicates & bursts", info.get("copies", 0), lambda: self.show_dups(0))):
            if n:
                shown = True
                ctk.CTkButton(chips, text=f"{label}   {n:,}", corner_radius=16, height=32,
                              fg_color="transparent", border_width=1, border_color=("gray70", "gray40"),
                              text_color=("gray15", "gray90"), command=cmd).pack(side="left", padx=(0, 8))
        if not shown:
            ctk.CTkLabel(chips, text="Nothing waiting.", text_color=MUTED).pack(side="left")

        ctk.CTkLabel(b, text="Accuracy scorecard   (target 95%)", font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(b, text="Appears once you start answering accuracy checks (arrives with people and pets).",
                     text_color=MUTED, anchor="w").pack(fill="x", pady=(2, 16))

        box = ctk.CTkFrame(b)
        box.pack(fill="x")
        last = info["last_run"]
        if last:
            when = dt.datetime.fromtimestamp(last["ended_at"] or last["started_at"]).strftime("%a %d %b, %I:%M %p")
            state = {"done": "Last run", "stopped": "Last run paused", "crashed": "Last run stopped early"}.get(
                last["state"], "Last run")
            verified = " · Backup verified ✓" if info["backed_up"] >= info["files"] and info["files"] else ""
            line1 = f"{state}: {when} · {plural(info['photos'], 'photo')}{verified}"
        else:
            line1 = "No runs yet."
        line2 = (f"Claude: {'on' if self.settings.claude_enabled else 'off'} · "
                 f"{plural(len(self.settings.masters), 'photo folder')} · library {self.settings.library}")
        ctk.CTkLabel(box, text=line1, anchor="w").pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkLabel(box, text=line2, text_color=MUTED, anchor="w", font=ctk.CTkFont(size=12)
                     ).pack(fill="x", padx=16, pady=(0, 12))
        for p in self.state_info.problems:
            ctk.CTkLabel(b, text="⚠ " + p, text_color=AMBER, anchor="w", wraplength=WRAP,
                         justify="left").pack(fill="x", pady=(8, 0))
        self._hotkey_line("Enter Next step · R Run · V Review · P Report · F Finish · F1 All hotkeys")

    def ask_resume(self):
        r = self.state_info.crashed_run
        win = ctk.CTkToplevel(self)
        win.title("Pick up where you left off?")
        win.geometry("520x230")
        win.transient(self)
        ctk.CTkLabel(win, text="Pick up where you left off?", font=ctk.CTkFont(size=18, weight="bold")
                     ).pack(padx=24, pady=(24, 6), anchor="w")
        step = {"discover": "finding photos", "backup": "backing up", "analyze": "looking at photos",
                "ai": "finding faces", "compare": "comparing photos", "dates": "working out dates", "plan": "planning",
                "learned_backup": "saving what it learned"}.get(r.get("last_step") or "", "starting")
        ctk.CTkLabel(win, text=f"The last run stopped while {step}. Nothing was lost: the backup and every "
                               "photo already looked at are saved.", wraplength=470, justify="left"
                     ).pack(padx=24, anchor="w")
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=24, pady=20)
        ctk.CTkButton(bar, text="Later (Esc)", fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), command=win.destroy).pack(side="left")
        ctk.CTkButton(bar, text="Continue (Enter)", fg_color=BLUE,
                      command=lambda: (win.destroy(), self.start_run())).pack(side="right")
        win.bind("<Return>", lambda e: (win.destroy(), self.start_run()))
        win.bind("<Escape>", lambda e: win.destroy())
        win.after(50, win.grab_set)

    # ---- running (wireframe 06) -------------------------------------------------
    STAGES = (("backup", "Back up"), ("analyze", "Analyze"), ("compare", "Compare & dates"), ("plan", "Plan"))
    STAGE_OF = {"Finding photos": "backup", "Backing up": "backup", "Looking at photos": "analyze",
                "Finding faces": "analyze",
                "Comparing photos": "compare", "Working out dates and holidays": "compare", "Planning": "plan"}

    def start_run(self):
        if self.runner is not None:
            return
        self.settings, self.state_info.problems = settings_mod.load(self.paths.settings)
        b = self._screen("Working on your photos", nav="run", nav_locked=True, subtitle=
                         "You can keep using your PC. If it's interrupted, it continues from the last photo.")
        self.stage_widgets = {}
        grid = ctk.CTkFrame(b, fg_color="transparent")
        grid.pack(fill="x", pady=(8, 0))
        grid.grid_columnconfigure(1, weight=1)
        for row, (key, label) in enumerate(self.STAGES):
            ctk.CTkLabel(grid, text=label, font=ctk.CTkFont(size=15, weight="bold"), anchor="w", width=150
                         ).grid(row=row, column=0, sticky="w", pady=12)
            bar = ctk.CTkProgressBar(grid)
            bar.set(0)
            bar.grid(row=row, column=1, sticky="ew", padx=12)
            status = ctk.CTkLabel(grid, text="Waiting", text_color=MUTED, anchor="w", width=230)
            status.grid(row=row, column=2, sticky="w")
            self.stage_widgets[key] = (bar, status)
        now = ctk.CTkFrame(b, fg_color="transparent")
        now.pack(fill="x", pady=(24, 0))
        self.now_image = ctk.CTkLabel(now, text="", width=120, height=90, fg_color=("gray90", "gray20"))
        self.now_image.pack(side="left")
        self.now_text = ctk.CTkLabel(now, text="", anchor="w", justify="left")
        self.now_text.pack(side="left", padx=16)
        self._current_stage = None
        self._last_thumb = 0.0
        ctk.CTkButton(self.footer_area, text="Pause  (Space)", command=self.pause_run, width=180, height=36
                      ).pack(side="right")
        self.bind("<space>", lambda e: self.pause_run())
        self.runner = Runner(self.paths, self.settings,
                             progress=lambda *a: self.msgs.put(("progress", *a)))
        threading.Thread(target=self._run_worker, daemon=True).start()

    def _run_worker(self):
        try:
            res = self.runner.run()
            self.msgs.put(("done", res))
        except Exception as e:  # shown on the UI thread
            log.get("ui").exception("run failed")
            self.msgs.put(("error", e))

    def pause_run(self):
        if self.runner:
            self.runner.cancel()
            if hasattr(self, "now_text") and self.now_text.winfo_exists():
                self.now_text.configure(text="Pausing after the current photo…")

    def _show_progress(self, label, done, total, detail=None):
        import time as _t
        if not (hasattr(self, "stage_widgets") and self.now_text.winfo_exists()):
            return
        stage = self.STAGE_OF.get(label, self._current_stage or "backup")
        order = [k for k, _ in self.STAGES]
        if stage != self._current_stage:
            for k in order[:order.index(stage)]:   # earlier stages are finished
                bar, status = self.stage_widgets[k]
                bar.set(1)
                status.configure(text="✓ Done", text_color=GREEN)
            self._current_stage = stage
        bar, status = self.stage_widgets[stage]
        if total:
            bar.set(done / total)
            status.configure(text=f"{label}: {done:,} of {total:,}", text_color=MUTED)
        else:
            bar.set(0.5 if label == "Finding photos" else 1)
            status.configure(text=f"{label}…" + (f" {done:,}" if done else ""), text_color=MUTED)
        if detail and _t.time() - self._last_thumb > 1.5:   # a new preview at most every 1.5 s
            self._last_thumb = _t.time()
            self._images = [im for im in getattr(self, "_images", [])][-4:]
            im = self._thumb(detail, (120, 90))
            if im is not None:
                self._images.append(im)
                self.now_image.configure(image=im, text="")
            self.now_text.configure(text=os.path.basename(detail))

    def _poll(self):
        try:
            while True:
                msg = self.msgs.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self._show_progress(*msg[1:])
                elif kind == "done":
                    self.runner = None
                    self.folders_changed = False
                    self.show_home()
                elif kind == "error":
                    self.runner = None
                    self.show_home()
                    self.error(msg[1])
                elif kind == "restore_done":
                    self.show_restore(result=msg[1])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ---- needs review (wireframe 09) ---------------------------------------------
    def show_review(self, index: int = 0, skip_people: bool = False):
        """The review queue in wireframe order: Who is this? first, then Needs review."""
        if not skip_people and self.paths.catalog.exists():
            from .. import people as people_mod
            c = Catalog(self.paths.catalog)
            try:
                waiting = people_mod.groups(c, limit=self.settings.people_per_round)
            finally:
                c.close()
            if waiting and index < len(waiting):
                return self.show_who(index, waiting)
            if waiting:
                index = 0   # skipped past the last person: Needs review starts at its first item
        from .. import review as review_mod
        derived = str(self.paths.data / "derived")
        cat = Catalog(self.paths.catalog) if self.paths.catalog.exists() else None
        items = review_mod.queue(cat, derived, self.settings.categories) if cat else []
        if cat:
            cat.close()
        if index >= len(items):        # past the last question: duplicates and burst shots come next
            return self.show_dups(0)
        index = max(0, index)
        it = items[index]
        b = self._screen(it.title, nav="review",
                         subtitle=f"{it.subtitle}      {index + 1} of {len(items)}")
        self._images = []
        pair = ctk.CTkFrame(b, fg_color="transparent")
        pair.pack(fill="x", pady=(4, 0))
        cw, ch = self._avail()
        size = (int((cw - 30) / 2), int(max(260, ch - 80)))
        left = ctk.CTkFrame(pair, fg_color="transparent")
        left.pack(side="left", padx=(0, 24))
        right = ctk.CTkFrame(pair, fg_color="transparent")
        right.pack(side="left")
        ctk.CTkLabel(left, text="The photo" if it.question == "content" else "Before", text_color=MUTED,
                     font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(right, text="After", text_color=MUTED, font=ctk.CTkFont(weight="bold"), anchor="w"
                     ).pack(fill="x")
        if it.question == "content":
            self._image_box(left, it.derived or it.original, (int(cw * 0.8), int(max(300, ch - 60))), "").pack()
            right.destroy()
        elif it.question == "rotation":
            current = it.derived or it.original
            self._image_box(left, current, size, "as it is now").pack()
            self._image_box(right, current, size, "turned the suggested way", crop=("rotate", it.suggested)).pack()
        elif it.question == "trim":
            self._image_box(left, it.original, size, "original · proposed trim in orange",
                            crop=("outline", it.trim_box) if it.trim_box else None).pack()
            if it.derived:
                self._image_box(right, it.derived, size, "current version").pack()
            else:
                self._image_box(right, it.original, size, "trimmed (if you choose Accept change)",
                                crop=("crop", it.trim_box) if it.trim_box else None).pack()
        else:
            self._image_box(left, it.original, size, "the whole scan").pack()
            self._image_box(right, it.derived, size, "the cut-out").pack()
        ctk.CTkLabel(b, text=os.path.basename(it.original), text_color=MUTED, anchor="w").pack(fill="x", pady=(8, 0))

        def act(value):
            from .. import review as rv
            c = Catalog(self.paths.catalog)
            try:
                rv.answer(c, it, value, derived)
            except Exception as e:
                self.error(e)
                return
            finally:
                c.close()
            self.show_review(index if value != "rotate" else index)
        undo_label = {"trim": "Undo fix", "rotation": "Leave as is", "content": "No"}.get(
            it.question, "Cut wrong: keep whole scan")
        accept_label = "Yes" if it.question == "content" else "Accept change"
        ctk.CTkButton(self.footer_area, text=f"{undo_label}  (U)", fg_color="transparent", border_width=1,
                      border_color="#c0392b", text_color="#c0392b", command=lambda: act("undo"),
                      width=200).pack(side="left")
        if it.question != "content":
            ctk.CTkButton(self.footer_area, text="Rotate again  (R)", fg_color="transparent", border_width=1,
                          text_color=("gray15", "gray90"), command=lambda: act("rotate"), width=160
                          ).pack(side="left", padx=8)
            self.bind("<Key-r>", self._not_typing(lambda: act("rotate")))
        ctk.CTkButton(self.footer_area, text=f"{accept_label}  (Enter)", fg_color=BLUE, height=38, width=190,
                      command=lambda: act("looks_good")).pack(side="right")
        ctk.CTkButton(self.footer_area, text="Skip  →", fg_color="transparent", width=90,
                      text_color=("gray30", "gray80"), command=lambda: self.show_review(index + 1)
                      ).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: act("looks_good"))
        self.bind("<Key-u>", self._not_typing(lambda: act("undo")))
        self.bind("<Right>", lambda e: self.show_review(index + 1))
        self.bind("<Left>", lambda e: self.show_review(index - 1))
        self.bind("<Escape>", lambda e: self.show_home())
        self.bind("<Control-z>", lambda e: self._review_undo(index))
        self._hotkey_line(f"Enter {accept_label} · " + ("" if it.question == "content" else "R Rotate again · ")
                          + f"U {undo_label} · → Skip · ← Back · Ctrl+Z Undo · Esc Home")

    # ---- who is this? (wireframe 08) ----------------------------------------------
    def _avail(self) -> tuple[int, int]:
        """Space for content (width, height) in the middle column, so previews can fill it."""
        self.update_idletasks()
        w = max(self.winfo_width(), 960)
        h = max(self.winfo_height(), 640)
        return w - 170 - 64 - 30, h - 210

    def show_who(self, index: int, groups=None):
        from .. import people as people_mod
        c = Catalog(self.paths.catalog)
        try:
            groups = groups or people_mod.groups(c, limit=self.settings.people_per_round)
            if not groups:
                return self.show_review(skip_people=True)
            index = max(0, min(index, len(groups) - 1))
            g = groups[index]
            samples = []
            for f in g.samples:
                a = c.one("SELECT a.derived_path, fl.orig_path FROM assets a JOIN files fl ON fl.id=a.file_id "
                          "WHERE a.id=?", (f["asset_id"],))
                img = str(self.paths.data / "derived" / a["derived_path"]) if a["derived_path"] else a["orig_path"]
                samples.append((f, img))
            suggestions = people_mod.suggest_for_group(c, g.face_ids)
        finally:
            c.close()
        total = len(groups)
        cw, ch = self._avail()
        b = self._screen("Who is this?", nav="review", subtitle=(
            f"Seen in {plural(g.photos, 'photo')}. Pick a suggestion (1-3), type a name, or press Stranger."
            f"      Person {index + 1} of {total}"))
        side_w = 290
        left_w = cw - side_w - 20
        face_px = int(max(80, min(150, (left_w - 50) / 6)))
        photo_h = int(max(150, min(460, ch - face_px - 90)))
        photo_w = int((left_w - 12) / 2)
        top = ctk.CTkFrame(b, fg_color="transparent")
        top.pack(fill="both", expand=True)
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True)
        side = ctk.CTkFrame(top, fg_color="transparent", width=side_w)
        side.pack(side="left", fill="y", padx=(20, 0), anchor="n")
        faces_row = ctk.CTkFrame(left, fg_color="transparent")
        faces_row.pack(fill="x", pady=(2, 8))
        for f, _ in samples:
            crop = str(self.paths.data / "faces" / f["crop_path"]) if f["crop_path"] else None
            self._image_box(faces_row, crop, (face_px, face_px)).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(left, text="In the photo (the face asked about is circled):", text_color=MUTED,
                     anchor="w").pack(fill="x")
        prow = ctk.CTkFrame(left, fg_color="transparent")
        prow.pack(fill="x", pady=4)
        for f, img in samples[:2]:
            self._image_box(prow, img, (photo_w, photo_h), crop=("circle", (f["x"], f["y"], f["w"], f["h"]))
                            ).pack(side="left", padx=(0, 12))

        def done():
            self.show_review(index)   # the next group slides into this position

        def pick(name):
            self._save_name(g.face_ids, name)
            done()
        ctk.CTkLabel(side, text="Suggestions", font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x")
        if not suggestions:
            ctk.CTkLabel(side, text="None yet: the first people you name\nbecome suggestions here.",
                         text_color=MUTED, justify="left", anchor="w").pack(fill="x", pady=(4, 0))
        for i, (name, score) in enumerate(suggestions, 1):
            label = f"{i}    {name}" + (f"      {int(round(score * 100))}%" if score is not None else "")
            ctk.CTkButton(side, text=label, anchor="w", height=44, width=side_w,
                          font=ctk.CTkFont(size=15, weight="bold" if i == 1 else "normal"),
                          fg_color=BLUE if i == 1 else "transparent", border_width=0 if i == 1 else 1,
                          text_color="white" if i == 1 else ("gray10", "gray90"),
                          command=lambda n=name: pick(n)).pack(fill="x", pady=(6, 0))
            self.bind(f"<Key-{i}>", self._not_typing(lambda n=name: pick(n)))
        ctk.CTkLabel(side, text="Not there? Type a name  (N)", text_color=MUTED, anchor="w").pack(fill="x", pady=(18, 2))
        entry = ctk.CTkEntry(side, placeholder_text="e.g. Calvin", height=36)
        entry.pack(fill="x")
        sugg = ctk.CTkFrame(side, fg_color="transparent", height=1)
        sugg.pack(fill="x")
        hint = ctk.CTkLabel(side, text="Proper case: joseph richards → Joseph Richards", text_color=MUTED,
                            font=ctk.CTkFont(size=11), anchor="w", wraplength=side_w, justify="left")
        hint.pack(fill="x", pady=(6, 0))

        def refresh(_=None):
            for w in sugg.winfo_children():
                w.destroy()
            c2 = Catalog(self.paths.catalog)
            try:
                for name in people_mod.suggestions(c2, entry.get()):
                    ctk.CTkButton(sugg, text=name, anchor="w", height=26, fg_color="transparent",
                                  text_color=BLUE, hover_color=("gray90", "gray25"),
                                  command=lambda n=name: (entry.delete(0, "end"), entry.insert(0, n), refresh())
                                  ).pack(fill="x")
            finally:
                c2.close()
        entry.bind("<KeyRelease>", refresh)

        def save():
            typed = entry.get().strip()
            if not typed:
                hint.configure(text="Pick a suggestion (1-3), type a name, or press Stranger.", text_color=AMBER)
                return
            c2 = Catalog(self.paths.catalog)
            try:
                close = people_mod.did_you_mean(c2, typed)
            finally:
                c2.close()
            if close:
                return self._confirm_name(close, typed, g.face_ids, lambda *a: done())
            pick(typed)

        def stranger():
            c2 = Catalog(self.paths.catalog)
            try:
                people_mod.mark_stranger(c2, g.face_ids)
            finally:
                c2.close()
            done()

        def undo():
            c2 = Catalog(self.paths.catalog)
            try:
                people_mod.undo_last(c2)
            finally:
                c2.close()
            self.show_review(index)
        ctk.CTkButton(self.footer_area, text="Back  (←)", fg_color="transparent", border_width=1, width=110,
                      text_color=("gray20", "gray85"), command=lambda: self.show_who(index - 1),
                      state="normal" if index > 0 else "disabled").pack(side="left")
        ctk.CTkButton(self.footer_area, text="Skip  (→)", fg_color="transparent", border_width=1, width=110,
                      text_color=("gray20", "gray85"), command=lambda: self.show_review(index + 1)
                      ).pack(side="left", padx=8)
        ctk.CTkButton(self.footer_area, text="Save typed name  (Enter)", fg_color=BLUE, height=38, width=210,
                      command=save).pack(side="right")
        ctk.CTkButton(self.footer_area, text="Stranger  (S)", fg_color="transparent", border_width=1, width=150,
                      text_color=("gray15", "gray90"), command=stranger).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: save())
        self.bind("<Key-s>", self._not_typing(stranger))
        self.bind("<Key-n>", self._not_typing(lambda: entry.focus_set()))
        self.bind("<Right>", self._not_typing(lambda: self.show_review(index + 1)))
        self.bind("<Left>", self._not_typing(lambda: self.show_who(index - 1)))
        self.bind("<Escape>", lambda e: self.focus_set() if "entry" in str(self.focus_get()).lower()
                  else self.show_home())
        self.bind("<Control-z>", lambda e: undo())
        self._hotkey_line("1-3 Pick a suggestion · Enter Save typed name · S Stranger · N Name box · → Skip · "
                          "← Back · Ctrl+Z Undo · Esc Home")

    def _save_name(self, face_ids, typed):
        from .. import people as people_mod
        c = Catalog(self.paths.catalog)
        try:
            people_mod.name_group(c, face_ids, typed)
        finally:
            c.close()

    def _confirm_name(self, existing, typed, face_ids, done):
        from .. import people as people_mod
        win = ctk.CTkToplevel(self)
        win.title("Did you mean…?")
        win.geometry("460x190")
        win.transient(self)
        new = people_mod.proper_case(typed)
        ctk.CTkLabel(win, text=f"Did you mean {existing}?", font=ctk.CTkFont(size=17, weight="bold")
                     ).pack(padx=24, pady=(22, 4), anchor="w")
        ctk.CTkLabel(win, text=f"\"{new}\" is very close to a name you already used.", wraplength=410,
                     justify="left").pack(padx=24, anchor="w")
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=24, pady=18)

        def pick(name):
            win.destroy()
            self._save_name(face_ids, name)
            done()
        ctk.CTkButton(bar, text=f"No, add {new}", fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), command=lambda: pick(new)).pack(side="left")
        ctk.CTkButton(bar, text=f"Yes, {existing}  (Enter)", fg_color=BLUE, command=lambda: pick(existing)
                      ).pack(side="right")
        win.bind("<Return>", lambda e: pick(existing))
        win.bind("<Escape>", lambda e: win.destroy())
        win.after(50, win.grab_set)

    # ---- duplicates & burst shots (wireframe 11) ------------------------------------
    def show_dups(self, index: int = 0, selected: set | None = None):
        from .. import copies as copies_mod
        derived = str(self.paths.data / "derived")
        if not self.paths.catalog.exists():
            gs = []
        else:
            c = Catalog(self.paths.catalog)
            try:
                gs = copies_mod.groups(c, derived)
            finally:
                c.close()
        if not gs:
            self._screen("Review", nav="review", subtitle="Nothing to review right now. Questions appear here "
                         "after a run when the app needs your help.")
            self._footer(back=("Home", self.show_home))
            self.bind("<Control-z>", lambda e: self._dups_undo(0))
            self._hotkey_line("Esc Home · Ctrl+Z Undo last answer")
            return
        index = max(0, min(index, len(gs) - 1))
        g = gs[index]
        burst = g.kind == "burst"
        if selected is None:
            selected = set(g.pick)
        n = len(g.copies)
        title = (f"Burst shots, {n} photos: pick the ones to keep" if burst
                 else f"Same photo, {n} copies: pick the one to keep")
        sub = ("Taken seconds apart. The sharpest is selected; press numbers to pick more, or Keep all."
               if burst else "The app's pick (most pixels, has a date, biggest file) is selected. "
                             "Removed copies go to the Recycle Bin at Finish.")
        kind_count = sum(1 for x in gs if (x.kind == "burst") == burst)
        b = self._screen(title, nav="review", subtitle=f"{sub}      Group {index + 1} of {len(gs)}")
        cw, ch = self._avail()
        per_row = min(n, 4)
        tw = int(max(140, min(360, (cw - 20) / per_row - 18)))
        th = int(max(120, min(ch - 160, tw * 0.8)))
        grid = ctk.CTkFrame(b, fg_color="transparent")
        grid.pack(fill="x")

        def toggle(i):
            sel = set(selected)
            if burst:
                sel ^= {i}
            else:
                sel = {i}
            self.show_dups(index, sel)
        for i, cp in enumerate(g.copies):
            on = i in selected
            card = ctk.CTkFrame(grid, border_width=3 if on else 1, border_color=BLUE if on else ("gray75", "gray35"),
                                fg_color=("#eef4ff", "#1e2a3a") if on else "transparent")
            card.grid(row=i // per_row, column=i % per_row, padx=6, pady=6, sticky="n")
            box = self._image_box(card, cp.image, (tw, th))
            box.pack(padx=8, pady=(8, 4))
            mp = f"{cp.pixels / 1e6:.1f} MP" if cp.pixels else ""
            info = f"{i + 1}" + ("   ✓ keep" if on else "") + f"\n{mp} · {cp.size / 1024:,.0f} KB"
            if burst:
                info += f" · sharpness {cp.sharpness:,.0f}"
            info += f"\n{cp.folder[:34]}"
            lab = ctk.CTkLabel(card, text=info, justify="left", anchor="w", text_color=BLUE if on else ("gray20", "gray85"))
            lab.pack(fill="x", padx=10, pady=(0, 8))
            for w in (card, box, lab):
                w.bind("<Button-1>", lambda e, k=i: toggle(k))
            if i < 9:
                self.bind(f"<Key-{i + 1}>", self._not_typing(lambda k=i: toggle(k)))

        def save(keep_all: bool = False):
            keep = {cp.content_key: (keep_all or i in selected) for i, cp in enumerate(g.copies)}
            if not any(keep.values()):
                return self.error(UserError("Pick at least one photo to keep."), title="Nothing selected")
            c2 = Catalog(self.paths.catalog)
            try:
                copies_mod.decide(c2, keep)
            finally:
                c2.close()
            self.show_dups(index)   # the next group slides into this position

        def bulk():
            same = [x for x in gs if (x.kind == "burst") == burst]
            removing = sum(len(x.copies) - len(x.pick) for x in same)
            what = (f"Keep only the sharpest photo in all {len(same):,} bursts? {removing:,} photos will be removed."
                    if burst else f"Keep the app's pick in all {len(same):,} duplicate groups? {removing:,} extra "
                                  f"copies will be removed.")
            self._confirm(what, "They leave the library now and go to the Recycle Bin at Finish. The backup "
                                "keeps everything, and Ctrl+Z undoes this.", "Yes, do it", lambda: apply_bulk(same))

        def apply_bulk(same):
            keep = {}
            for x in same:
                for i, cp in enumerate(x.copies):
                    keep[cp.content_key] = i in x.pick
            c2 = Catalog(self.paths.catalog)
            try:
                copies_mod.decide(c2, keep)
            finally:
                c2.close()
            self.show_dups(0)
        noun = ("burst" if burst else "group") + ("" if kind_count == 1 else "s")
        ctk.CTkButton(self.footer_area, text=f"Do this for {'the' if kind_count == 1 else 'all'} {kind_count:,} {noun}…",
                      fg_color="transparent", border_width=1, text_color=("gray30", "gray80"), width=230,
                      command=bulk).pack(side="left")
        ctk.CTkButton(self.footer_area, text="Keep selected, remove rest  (D)", fg_color="transparent",
                      border_width=1, border_color="#c0392b", text_color="#c0392b", width=250,
                      command=save).pack(side="right")
        ctk.CTkButton(self.footer_area, text="Keep all  (K)", fg_color="transparent", border_width=1, width=130,
                      text_color=("gray15", "gray90"), command=lambda: save(True)).pack(side="right", padx=8)
        ctk.CTkButton(self.footer_area, text="Next  →", fg_color="transparent", width=80,
                      text_color=("gray30", "gray80"), command=lambda: self.show_dups(index + 1)).pack(side="right")
        self.bind("<Key-d>", self._not_typing(save))
        self.bind("<Key-k>", self._not_typing(lambda: save(True)))
        self.bind("<Right>", lambda e: self.show_dups(index + 1))
        self.bind("<Left>", lambda e: self.show_dups(index - 1) if index > 0 else self.show_review(10 ** 6, True))
        self.bind("<Escape>", lambda e: self.show_home())
        self.bind("<Control-z>", lambda e: self._dups_undo(index))
        self._hotkey_line("1-9 Select · D Keep selected, remove the rest · K Keep all · → Next · ← Back · "
                          "Ctrl+Z Undo · Esc Home")

    def _dups_undo(self, index):
        from .. import copies as copies_mod
        c = Catalog(self.paths.catalog)
        try:
            copies_mod.undo_last(c)
        finally:
            c.close()
        self.show_dups(index)

    def _confirm(self, question: str, detail: str, yes: str, on_yes):
        win = ctk.CTkToplevel(self)
        win.title("Please confirm")
        win.geometry("540x230")
        win.transient(self)
        ctk.CTkLabel(win, text=question, font=ctk.CTkFont(size=16, weight="bold"), wraplength=490,
                     justify="left").pack(padx=24, pady=(22, 6), anchor="w")
        ctk.CTkLabel(win, text=detail, wraplength=490, justify="left", text_color=MUTED).pack(padx=24, anchor="w")
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=24, pady=18)

        def go():
            win.destroy()
            on_yes()
        ctk.CTkButton(bar, text="Cancel (Esc)", fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), command=win.destroy).pack(side="left")
        ctk.CTkButton(bar, text=f"{yes} (Enter)", fg_color=BLUE, command=go).pack(side="right")
        win.bind("<Return>", lambda e: go())
        win.bind("<Escape>", lambda e: win.destroy())
        win.after(50, win.grab_set)

    def _review_undo(self, index):
        from .. import review as rv
        c = Catalog(self.paths.catalog)
        try:
            rv.undo_last(c, str(self.paths.data / "derived"))
        finally:
            c.close()
        self.show_review(index)

    # ---- report (wireframe 12) ----------------------------------------------------
    REPORT_PAGE = 8

    def show_report(self, folder: str | None = None, page: int = 0):
        from .. import plan as plan_mod
        derived = str(self.paths.data / "derived")
        if not self.paths.catalog.exists():
            pv = plan_mod.Preview()
        else:
            cat = Catalog(self.paths.catalog)
            try:
                pv = plan_mod.preview(cat, derived, self.settings.categories)
            finally:
                cat.close()
        info_review = pv.totals.get("need_review", 0)
        b = self._screen("Here's the plan", nav="report")
        chips = ctk.CTkFrame(b, fg_color="transparent")
        chips.pack(fill="x", pady=(0, 8))
        for text, color in ((f"{pv.totals.get('sorted', 0):,} sorted", GREEN),
                            (f"{pv.totals.get('duplicates', 0):,} duplicates", None),
                            (f"{info_review:,} need review", AMBER),
                            (f"{pv.totals.get('could_not_open', 0):,} couldn't open", "#c0392b")):
            ctk.CTkLabel(chips, text=f"  {text}  ", corner_radius=11, fg_color="transparent",
                         text_color=color or ("gray30", "gray75"), font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 6))
        body = ctk.CTkFrame(b, fg_color="transparent")
        body.pack(fill="both", expand=True)
        tree = ctk.CTkScrollableFrame(body, width=200, height=360)
        tree.pack(side="left", fill="y")
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        if not pv.folders:
            ctk.CTkLabel(right, text="Nothing to show yet. Run first.", text_color=MUTED).pack(anchor="w")
        folders = list(pv.folders)
        if folder not in pv.folders and folders:
            folder = folders[0]
        # Folder tree: every level shown, the selected folder highlighted.
        seen = set()
        for path in folders:
            parts = path.split("/")
            for depth in range(len(parts)):
                sub = "/".join(parts[:depth + 1])
                if sub in seen:
                    continue
                seen.add(sub)
                count = sum(len(v) for k, v in pv.folders.items() if k == sub or k.startswith(sub + "/"))
                target = sub if sub in pv.folders else next(k for k in folders if k.startswith(sub + "/"))
                active = sub == folder
                arrow = "" if sub in pv.folders else "▾ "
                ctk.CTkButton(tree, text=f"{'    ' * depth}{arrow}{parts[depth]} ({count:,})", anchor="w",
                              height=26, fg_color=(("#dde8fb", "#22324a") if active else "transparent"),
                              text_color=("gray10", "gray90"), hover_color=("gray85", "gray25"),
                              command=lambda t=target: self.show_report(t)).pack(fill="x")
        if folder:
            entries = pv.folders[folder]
            pages = max(1, (len(entries) + self.REPORT_PAGE - 1) // self.REPORT_PAGE)
            page = max(0, min(page, pages - 1))
            ctk.CTkLabel(right, text=f"{folder.replace('/', ' › ')}   ({plural(len(entries), 'photo')})",
                         font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x")
            grid = ctk.CTkFrame(right, fg_color="transparent")
            grid.pack(fill="x", pady=6)
            self._images = []
            cw, ch = self._avail()
            tw = int(max(100, min(220, (cw - 230 - 60) / 4 - 10)))
            th = int(tw * 0.8)
            for i, e in enumerate(entries[page * self.REPORT_PAGE:(page + 1) * self.REPORT_PAGE]):
                name = e.name if len(e.name) <= 14 else e.name[:13] + "…"
                cell = self._image_box(grid, e.image, (tw, th), name + ("\ndate is a guess" if e.is_guess else ""))
                cell.grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky="n")
            for c in range(4):
                grid.grid_columnconfigure(c, minsize=tw + 8, uniform="thumbs")
            if pages > 1:
                nav = ctk.CTkFrame(right, fg_color="transparent")
                nav.pack(fill="x")
                ctk.CTkButton(nav, text="←", width=40, command=lambda: self.show_report(folder, page - 1),
                              state="normal" if page > 0 else "disabled").pack(side="left")
                ctk.CTkLabel(nav, text=f"  page {page + 1} of {pages}  ", text_color=MUTED).pack(side="left")
                ctk.CTkButton(nav, text="→", width=40, command=lambda: self.show_report(folder, page + 1),
                              state="normal" if page < pages - 1 else "disabled").pack(side="left")
            attn = ctk.CTkFrame(right, border_width=1, border_color=AMBER, fg_color=("#fff8eb", "#3a2e1a"))
            attn.pack(fill="x", pady=(10, 0))
            ctk.CTkLabel(attn, text="Needs attention", text_color=AMBER, font=ctk.CTkFont(weight="bold"),
                         anchor="w").pack(fill="x", padx=14, pady=(8, 2))
            rows = []
            if info_review:
                rows.append((f"{plural(info_review, 'photo')} need review", "Open review ›", self.show_review))
            if pv.totals.get("guesses"):
                rows.append((f"{plural(pv.totals['guesses'], 'date')} are best guesses (marked 'guess')", "", None))
            if pv.totals.get("could_not_open"):
                rows.append((f"{plural(pv.totals['could_not_open'], 'file')} couldn't be opened", "See why ›",
                             lambda: self.show_report("_Could Not Open")))
            if not rows:
                rows.append(("Nothing needs attention.", "", None))
            for text, link, cmd in rows:
                r = ctk.CTkFrame(attn, fg_color="transparent")
                r.pack(fill="x", padx=14)
                ctk.CTkLabel(r, text="• " + text, anchor="w").pack(side="left")
                if cmd:
                    ctk.CTkButton(r, text=link, fg_color="transparent", text_color=BLUE, width=110,
                                  hover_color=("gray90", "gray25"), command=cmd).pack(side="right")
            ctk.CTkLabel(attn, text="", height=4).pack()
        self._footer(back=("Not yet", self.show_home))
        ctk.CTkButton(self.footer_area, text="Approve and sort  (arrives in 0.4)", state="disabled",
                      width=260, height=38, fg_color=("gray80", "gray30"),
                      text_color_disabled=("gray45", "gray60")).pack(side="right")
        self._hotkey_line("Esc Back to Home · Sorting (Approve and sort) arrives in version 0.4")

    # ---- finish (wireframe 13) ------------------------------------------------------
    def show_finish(self):
        info = self._counts()
        b = self._screen("Finish: move originals into your library", nav="finish", review_count=info["review"],
                         subtitle="Do this once, when you're happy with how everything is sorted.")
        ok = info["files"] and info["backed_up"] >= info["files"]
        ctk.CTkLabel(b, text=("✓  " if ok else "!  ") + (f"Backup verified ({plural(info['backed_up'], 'file')})"
                     if ok else "Run first so everything is backed up"), text_color=GREEN if ok else AMBER,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(b, text="!  Finish needs sorting, which arrives in version 0.4", text_color=AMBER,
                     anchor="w").pack(fill="x", pady=(4, 12))
        box = ctk.CTkFrame(b)
        box.pack(fill="x")
        ctk.CTkLabel(box, text="What will happen (from version 0.4)", font=ctk.CTkFont(weight="bold"),
                     anchor="w").pack(fill="x", padx=16, pady=(12, 4))
        for line in ("Originals move into your sorted library (fixed photos stay as their fixed version)",
                     "Folders left completely empty are deleted; folders with anything else are left alone",
                     "Every move is logged first, so an interruption can be finished or undone",
                     f"Your backup zips stay in {self.settings.backup}"):
            ctk.CTkLabel(box, text="• " + line, anchor="w", justify="left", wraplength=WRAP - 40
                         ).pack(fill="x", padx=16)
        ctk.CTkLabel(box, text="", height=6).pack()
        self._footer(back=("Home", self.show_home))
        ctk.CTkButton(self.footer_area, text="Move originals  (arrives in 0.4)", state="disabled",
                      width=260, height=38, fg_color=("gray80", "gray30"),
                      text_color_disabled=("gray45", "gray60")).pack(side="right")
        self._hotkey_line("Esc Home")

    # ---- restore (wireframe 15) -------------------------------------------------------
    def show_restore(self, result=None):
        from ..restore import backups
        b = self._screen("Get something back", nav="restore")
        cards = (("Restore everything", "Put every original back exactly where it was, from your backups.",
                  self._restore_all, True),
                 ("Restore a photo or folder", "Search by name or original folder and restore just that.",
                  self.show_restore_search, True),
                 ("Find an original", "Pick a photo in your library and see the untouched file it came from. "
                                      "(Arrives with sorting in 0.4.)", None, False))
        for title, text, cmd, enabled in cards:
            card = ctk.CTkFrame(b, border_width=1, border_color=("gray75", "gray35"), fg_color="transparent",
                                cursor="hand2" if enabled else "")
            card.pack(fill="x", pady=5)
            col = ("gray10", "gray90") if enabled else ("gray55", "gray55")
            t = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=15, weight="bold"), text_color=col, anchor="w")
            t.pack(fill="x", padx=18, pady=(12, 0))
            d = ctk.CTkLabel(card, text=text, text_color=MUTED, anchor="w", justify="left", wraplength=WRAP - 60)
            d.pack(fill="x", padx=18, pady=(0, 12))
            arrow = ctk.CTkLabel(card, text="›" if enabled else "", font=ctk.CTkFont(size=22), text_color=col)
            arrow.place(relx=1.0, rely=0.5, x=-20, anchor="e")
            if enabled:
                for w in (card, t, d, arrow):
                    w.bind("<Button-1>", lambda e, c=cmd: c())
        if self.paths.catalog.exists():
            cat = Catalog(self.paths.catalog)
            try:
                bk = backups(cat)
            finally:
                cat.close()
            if bk:
                ctk.CTkLabel(b, text=f"Backups available: {plural(len(bk), 'verified zip part')}, "
                                     f"{sum(x['file_count'] for x in bk):,} files.", text_color=MUTED,
                             anchor="w").pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(b, text="Every restore is checked against the backup's fingerprints and never overwrites "
                             "without asking.", text_color=MUTED, anchor="w", wraplength=WRAP, justify="left"
                     ).pack(fill="x")
        if result is not None:
            text = (f"Restored {result.restored}, already in place {result.already_there}, skipped "
                    f"{result.skipped} (changed since the backup), failed {len(result.failed)}.")
            ctk.CTkLabel(b, text=text, text_color=GREEN if not result.failed else AMBER, anchor="w",
                         wraplength=WRAP, justify="left").pack(fill="x", pady=(10, 0))
        self._footer(back=("Home", self.show_home))
        self._hotkey_line("Esc Home")

    def show_restore_search(self):
        b = self._screen("Restore a photo or folder", nav="restore",
                         subtitle="Type part of a file or folder name, e.g. Christmas or IMG_0412.")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        q = ctk.CTkEntry(row, placeholder_text="Search")
        q.pack(side="left", fill="x", expand=True)
        results = ctk.CTkFrame(b, fg_color="transparent")
        results.pack(fill="x")

        def search():
            from ..restore import find_files
            for w in results.winfo_children():
                w.destroy()
            cat = Catalog(self.paths.catalog)
            rows = find_files(cat, q.get().strip())
            cat.close()
            if not rows:
                ctk.CTkLabel(results, text="Nothing found.", text_color=MUTED).pack(anchor="w")
            for r in rows:
                line = ctk.CTkFrame(results)
                line.pack(fill="x", pady=2)
                ctk.CTkLabel(line, text=r["orig_path"], anchor="w").pack(side="left", padx=8)
                ctk.CTkButton(line, text="Restore", width=90,
                              command=lambda i=r["id"]: self._restore_ids([i])).pack(side="right", padx=6)
        ctk.CTkButton(row, text="Search", width=110, command=search).pack(side="left", padx=(8, 0))
        q.bind("<Return>", lambda e: search())
        q.focus_set()
        self._footer(back=("Back", lambda: self.show_restore()))
        self._hotkey_line("Enter Search · Esc Back")

    def _restore_all(self):
        self._restore_ids(None)

    def _restore_ids(self, ids):
        def worker():
            cat = Catalog(self.paths.catalog)
            try:
                # Conflicts are skipped (never overwritten).
                r = restore_files(cat, self.settings.backup, file_ids=ids)
                self.msgs.put(("restore_done", r))
            except Exception as e:
                self.msgs.put(("error", e))
            finally:
                cat.close()
        threading.Thread(target=worker, daemon=True).start()

    def show_damaged(self):
        zip_path = self.state_info.learned_backup_available
        self._screen("The app's data needs repairing", nav=None, subtitle=
                         "The file where the app keeps what it has learned is damaged. "
                         + ("It can be restored from your newest learned-data backup."
                            if zip_path else "No learned-data backup was found."))

        def repair():
            restore_learned_data(zip_path, self.paths.catalog, self.paths.data)
            self.state_info = startup(self.paths)
            self.show_home()
        if zip_path:
            self._footer(primary=("Restore learned data", repair))

    def show_hotkeys(self):
        win = ctk.CTkToplevel(self)
        win.title("Hotkeys")
        win.geometry("420x260")
        text = ("Enter    Next step / confirm\nEsc      Back / close\nR        Run\nA        Add a folder (setup)\n"
                "Space    Pause a run\nF1       This list")
        ctk.CTkLabel(win, text=text, font=ctk.CTkFont(family="Consolas", size=14), justify="left"
                     ).pack(padx=24, pady=24, anchor="w")
        win.bind("<Escape>", lambda e: win.destroy())


def run_window() -> int:
    paths = AppPaths.default()
    log.setup_logging(paths.data)
    ctk.set_appearance_mode("system")
    app = App(paths)
    app.mainloop()
    return 0
