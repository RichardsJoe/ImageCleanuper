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
from .. import app as app_mod
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


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} " + (word if n == 1 else (many or word + "s"))


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
        ctk.CTkButton(bar, text="Check for updates ↗", fg_color="transparent", text_color=BLUE, anchor="w",
                      hover_color=("gray85", "gray25"), height=22, font=ctk.CTkFont(size=12),
                      command=self._check_updates).pack(side="bottom", fill="x", padx=8, pady=(0, 14))
        ctk.CTkLabel(bar, text=f"Version {__version__}\nF1  Hotkeys", text_color=MUTED, justify="left",
                     anchor="w").pack(side="bottom", fill="x", padx=16, pady=(16, 2))
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

    RELEASES_URL = "https://github.com/RichardsJoe/ImageCleanuper/releases"

    def _check_updates(self):
        """The repo is private, so the app can't ask GitHub itself; your browser is already signed in."""
        import webbrowser
        webbrowser.open(self.RELEASES_URL)

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
        self._centre(win)
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
    SETTINGS_TABS = ("Folders", "People", "Holidays & categories", "Accuracy", "Hotkeys", "Claude")

    def _settings_tabs(self, parent, current="Folders"):
        """Tabs from wireframe 14 (plus People). All tabs edit one draft; Save keeps everything."""
        tabs = ctk.CTkFrame(parent, fg_color="transparent")
        tabs.pack(fill="x", pady=(0, 12))
        for name in self.SETTINGS_TABS:
            on = name == current
            ctk.CTkButton(tabs, text=name, height=30, corner_radius=6, width=10,
                          fg_color=BLUE if on else "transparent", border_width=0 if on else 1,
                          text_color="white" if on else ("gray20", "gray85"), hover_color=("gray85", "gray25"),
                          command=lambda n=name: self._settings_switch(n)).pack(side="left", padx=(0, 6))

    def _settings_switch(self, tab):
        for f in getattr(self, "_settings_flush", []):    # keep what you typed on the tab you're leaving
            f()
        self.show_settings(tab, keep=True)

    def show_settings(self, tab: str = "Folders", keep: bool = False):
        """Works on a draft copy: Cancel throws changes away, Save keeps changes from every tab."""
        import copy
        if not keep or getattr(self, "_settings_draft", None) is None:
            self._settings_draft = copy.deepcopy(self.settings)
        s = self._settings_draft
        b = self._screen("Settings", nav="settings")
        self._settings_tabs(b, tab)
        check_locations = None
        self._settings_flush = []
        if tab == "Folders":
            check_locations = self._settings_folders(b, s)
        elif tab == "People":
            self._settings_people(b)
        elif tab == "Holidays & categories":
            self._settings_flush = [self._settings_holidays(b, s)]
        elif tab == "Accuracy":
            self._settings_flush = [self._settings_accuracy(b, s)]
        elif tab == "Hotkeys":
            self._settings_hotkeys(b)
        elif tab == "Claude":
            self._settings_claude(b, s)

        def save():
            if check_locations is not None and not check_locations():
                return
            for f in getattr(self, "_settings_flush", []):
                f()
            blocking = [p for p in settings_mod.check(s) if p.blocking]
            if not s.masters:
                blocking.insert(0, settings_mod.Problem("masters", "Add at least one photo folder (Folders tab)."))
            if blocking:
                self._notice("Please fix this first", "\n".join(p.message for p in blocking[:4]))
                return
            from ..paths import same_path
            changed = (len(s.masters) != len(self.settings.masters)
                       or not all(any(same_path(a, b) for b in self.settings.masters) for a in s.masters))
            settings_mod.save(s, self.paths.settings)
            self.settings, self.state_info.problems = settings_mod.load(self.paths.settings)
            self._settings_draft = None
            if changed:
                self.folders_changed = True
            self.show_home()

        def cancel():
            self._settings_draft = None
            self.show_home()
        self._footer(back=("Cancel", cancel), primary=("Save", save))

    def _settings_folders(self, b, s):
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
        ctk.CTkButton(b, text="Bring in Google Photos…", width=200, fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), command=self.show_google).pack(anchor="w", pady=(2, 8))
        check_locations = self._location_fields(b, s)

        return check_locations

    def _section(self, b, title, text=""):
        ctk.CTkLabel(b, text=title, font=ctk.CTkFont(size=15, weight="bold"), anchor="w").pack(fill="x", pady=(8, 0))
        if text:
            ctk.CTkLabel(b, text=text, text_color=MUTED, anchor="w", justify="left", wraplength=WRAP).pack(fill="x")

    def _settings_people(self, b):
        from .. import people as people_mod
        self._section(b, "People", "Rename someone, or rename them to an existing name to merge the two. Click a "
                      "face to mark it as a best sample (★): best samples count extra when recognising that "
                      "person. Changes here are saved straight away.")
        if not self.paths.catalog.exists():
            ctk.CTkLabel(b, text="Nobody yet. Run first, then answer Who is this?", text_color=MUTED,
                         anchor="w").pack(fill="x")
            return
        cat = Catalog(self.paths.catalog)
        try:
            ppl = people_mod.people_summary(cat)
            strangers = people_mod.strangers_summary(cat)
        finally:
            cat.close()
        self._images = []

        def act(fn, *args):
            c = Catalog(self.paths.catalog)
            try:
                fn(c, *args)
            except ValueError as e:
                self._notice("Please check", str(e))
            finally:
                c.close()
            self.show_settings("People", keep=True)

        def faces_row(parent, faces, clickable):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(side="left", padx=8)
            for f in faces:
                crop = str(self.paths.data / "faces" / f["crop_path"])
                im = self._thumb(crop, (56, 56))
                cell = ctk.CTkFrame(row, border_width=2 if f.get("best") else 0, border_color=AMBER,
                                    fg_color="transparent")
                cell.pack(side="left", padx=2)
                if im is not None:
                    self._images.append(im)
                    lab = ctk.CTkLabel(cell, image=im, text="")
                    lab.pack(padx=1, pady=1)
                    if clickable:
                        lab.bind("<Button-1>", lambda e, fid=f["id"]: act(people_mod.toggle_best, fid))
        if not ppl:
            ctk.CTkLabel(b, text="Nobody named yet.", text_color=MUTED, anchor="w").pack(fill="x")
        for p in ppl:
            row = ctk.CTkFrame(b)
            row.pack(fill="x", pady=3)
            name = ctk.CTkEntry(row, width=200)
            name.insert(0, p["name"])
            name.pack(side="left", padx=10, pady=8)
            ctk.CTkButton(row, text="Rename", width=90,
                          command=lambda e=name, pid=p["id"]: act(people_mod.rename_person, pid, e.get())
                          ).pack(side="left")
            ctk.CTkLabel(row, text=plural(p["photos"], "photo"), text_color=MUTED, width=90).pack(side="left", padx=8)
            faces_row(row, p["faces"], True)
        self._section(b, "Strangers", "People you marked as strangers. Type a name if you know who it is: they "
                      "become a known person and get their own folder.")
        if not strangers:
            ctk.CTkLabel(b, text="No strangers.", text_color=MUTED, anchor="w").pack(fill="x")
        for st in strangers:
            row = ctk.CTkFrame(b)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=st["label"], width=120, anchor="w").pack(side="left", padx=10, pady=8)
            ctk.CTkLabel(row, text=plural(st["photos"], "photo"), text_color=MUTED, width=80).pack(side="left")
            faces_row(row, st["faces"], False)
            e = ctk.CTkEntry(row, width=180, placeholder_text="Who is it?")
            e.pack(side="right", padx=10)
            ctk.CTkButton(row, text="Name", width=80,
                          command=lambda e=e, sid=st["id"]: act(people_mod.name_stranger, sid, e.get())
                          ).pack(side="right")

    def _settings_holidays(self, b, s):
        from ..dates import HOLIDAY_NAMES
        from ..settings import DEFAULT_HOLIDAY_WINDOWS
        self._section(b, "Holidays", "Photos taken this many days before or after a holiday go in its folder. "
                      "Untick a holiday you don't want a folder for.")
        grid = ctk.CTkFrame(b, fg_color="transparent")
        grid.pack(fill="x", pady=4)
        rows = []
        for i, (key, name) in enumerate(HOLIDAY_NAMES.items()):
            on = ctk.BooleanVar(value=key in s.holiday_windows)
            bef, aft = s.holiday_windows.get(key, DEFAULT_HOLIDAY_WINDOWS.get(key, (1, 1)))
            r, c = divmod(i, 2)
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=r, column=c, sticky="w", padx=(0, 30), pady=2)
            ctk.CTkCheckBox(cell, text=name, variable=on, width=180).pack(side="left")
            eb = ctk.CTkEntry(cell, width=44)
            eb.insert(0, str(bef))
            eb.pack(side="left")
            ctk.CTkLabel(cell, text=" days before ", text_color=MUTED).pack(side="left")
            ea = ctk.CTkEntry(cell, width=44)
            ea.insert(0, str(aft))
            ea.pack(side="left")
            ctk.CTkLabel(cell, text=" after", text_color=MUTED).pack(side="left")
            rows.append((key, on, eb, ea))

        self._section(b, "Your own events", "Birthdays, anniversaries, a yearly trip: they get a folder like a "
                      "holiday. Month and day as numbers.")
        ev_box = ctk.CTkFrame(b, fg_color="transparent")
        ev_box.pack(fill="x")
        events = [list(e) for e in s.custom_events]

        def draw_events():
            for w in ev_box.winfo_children():
                w.destroy()
            for ev in events:
                r = ctk.CTkFrame(ev_box, fg_color="transparent")
                r.pack(fill="x", pady=1)
                ctk.CTkLabel(r, text=f"{ev[0]}  ·  {ev[1]}/{ev[2]}  ·  {ev[3]} before, {ev[4]} after",
                             anchor="w").pack(side="left")
                ctk.CTkButton(r, text="Remove", width=80, fg_color="transparent", border_width=1,
                              text_color=("gray20", "gray85"),
                              command=lambda ev=ev: (events.remove(ev), draw_events())).pack(side="left", padx=10)
            add = ctk.CTkFrame(ev_box, fg_color="transparent")
            add.pack(fill="x", pady=4)
            fields = []
            for ph, w in (("Name, e.g. Calvin's Birthday", 220), ("Month", 64), ("Day", 56), ("Before", 64),
                          ("After", 64)):
                e = ctk.CTkEntry(add, placeholder_text=ph, width=w)
                e.pack(side="left", padx=(0, 6))
                fields.append(e)

            def add_event():
                import datetime as _dt
                try:
                    name = fields[0].get().strip()
                    m, d = int(fields[1].get()), int(fields[2].get())
                    bf, af = int(fields[3].get() or 0), int(fields[4].get() or 0)
                    _dt.date(2000, m, d)
                    if not name:
                        raise ValueError
                except ValueError:
                    self._notice("Please check", "Type a name, and month and day as numbers (e.g. 7 and 14).")
                    return
                events.append([name, m, d, max(0, bf), max(0, af)])
                draw_events()
            ctk.CTkButton(add, text="+ Add event", width=120, command=add_event).pack(side="left")
        draw_events()

        self._section(b, "Categories", "Extra folders the image model sorts into. Describe what you can SEE in "
                      "these photos; your Yes/No answers teach it the rest.")
        cat_box = ctk.CTkFrame(b, fg_color="transparent")
        cat_box.pack(fill="x")
        cats = {k: [v[0], v[1]] for k, v in s.categories.items()}
        cat_fields = {}

        def draw_cats():
            for w in cat_box.winfo_children():
                w.destroy()
            cat_fields.clear()
            for key, (folder, desc) in cats.items():
                r = ctk.CTkFrame(cat_box, fg_color="transparent")
                r.pack(fill="x", pady=2)
                ef = ctk.CTkEntry(r, width=160)
                ef.insert(0, folder)
                ef.pack(side="left")
                ed = ctk.CTkEntry(r)
                ed.insert(0, desc)
                ed.pack(side="left", fill="x", expand=True, padx=6)
                ctk.CTkButton(r, text="Remove", width=80, fg_color="transparent", border_width=1,
                              text_color=("gray20", "gray85"),
                              command=lambda k=key: (cats.pop(k), draw_cats())).pack(side="left")
                cat_fields[key] = (ef, ed)

            def add_cat():
                n = 1
                while f"custom{n}" in cats:
                    n += 1
                cats[f"custom{n}"] = ["New folder", ""]
                draw_cats()
            ctk.CTkButton(cat_box, text="+ Add a category", width=160, command=add_cat).pack(anchor="w", pady=4)
        draw_cats()

        def flush():
            win = {}
            for key, on, eb, ea in rows:
                if on.get():
                    try:
                        win[key] = (max(0, min(14, int(eb.get()))), max(0, min(14, int(ea.get()))))
                    except ValueError:
                        win[key] = DEFAULT_HOLIDAY_WINDOWS.get(key, (1, 1))
            s.holiday_windows = win
            s.custom_events = [tuple(e) for e in events]
            new = {}
            for key, (ef, ed) in cat_fields.items():
                folder, desc = ef.get().strip(), ed.get().strip()
                if folder and desc:
                    new[key] = (folder, desc)
            s.categories = new
        return flush

    def _settings_accuracy(self, b, s):
        self._section(b, "Accuracy check", "Blind questions that measure how often the app is right. Checks that "
                      "stay above the target get fewer questions.")
        fields = []
        for label, attr, scale in (("Target accuracy (%)", "accuracy_target", 100),
                                   ("Questions in the first round", "accuracy_start_items", 1),
                                   ("Fewest questions per check", "accuracy_min_per_check", 1),
                                   ("Rounds above target before asking fewer", "accuracy_rounds_to_shrink", 1)):
            r = ctk.CTkFrame(b, fg_color="transparent")
            r.pack(fill="x", pady=3)
            ctk.CTkLabel(r, text=label, width=320, anchor="w").pack(side="left")
            e = ctk.CTkEntry(r, width=90)
            v = getattr(s, attr)
            e.insert(0, str(int(round(v * scale))))
            e.pack(side="left")
            fields.append((attr, scale, e))
        self._section(b, "People", "")
        for label, attr in (("Faces per Who is this? round", "people_per_round"),
                            ("Smallest face to look at (pixels)", "min_face_px")):
            r = ctk.CTkFrame(b, fg_color="transparent")
            r.pack(fill="x", pady=3)
            ctk.CTkLabel(r, text=label, width=320, anchor="w").pack(side="left")
            e = ctk.CTkEntry(r, width=90)
            e.insert(0, str(getattr(s, attr)))
            e.pack(side="left")
            fields.append((attr, 1, e))

        def flush():
            limits = {"accuracy_target": (50, 100), "accuracy_start_items": (5, 500), "accuracy_min_per_check": (1, 50),
                      "accuracy_rounds_to_shrink": (1, 10), "people_per_round": (5, 100), "min_face_px": (20, 400)}
            for attr, scale, e in fields:
                try:
                    lo, hi = limits[attr]
                    v = max(lo, min(hi, int(float(e.get()))))
                    setattr(s, attr, v / 100 if scale == 100 else v)
                except ValueError:
                    pass
        return flush

    HOTKEYS = (("Anywhere", (("Enter", "Next step / confirm / Approve"), ("Esc", "Back / close"),
                             ("R", "Run"), ("V", "Review"), ("P", "Report"), ("F", "Finish"), ("F1", "Hotkeys"),
                             ("Ctrl+Z", "Undo your last answer"))),
               ("Running", (("Space", "Pause"),)),
               ("Who is this?", (("1 2 3", "Pick a suggested name"), ("Enter", "Save the typed name"),
                                 ("S", "Stranger"))),
               ("Needs review / Accuracy check", (("1–9", "Answer"), ("Y / N", "Yes / No"), ("→", "Skip"),
                                                  ("←", "Back"))),
               ("Duplicates & bursts", (("1–9", "Select a copy"), ("D", "Keep selected, remove the rest"),
                                        ("K", "Keep all"))),
               ("Report", (("Click", "Select a photo"), ("M", "Move it to another folder"),
                           ("Del", "Remove it from this folder"), ("O", "Show the original"))))

    def _settings_hotkeys(self, b):
        self._section(b, "Hotkeys", "Every action has a key. Press F1 anywhere to see this list.")
        for group, keys in self.HOTKEYS:
            ctk.CTkLabel(b, text=group, font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x", pady=(8, 2))
            for k, what in keys:
                r = ctk.CTkFrame(b, fg_color="transparent")
                r.pack(fill="x")
                ctk.CTkLabel(r, text=k, width=110, anchor="w", font=ctk.CTkFont(family="Consolas", size=13)
                             ).pack(side="left")
                ctk.CTkLabel(r, text=what, anchor="w").pack(side="left")

    def _settings_claude(self, b, s):
        from .. import claude_assist as ca
        self._section(b, "Claude second opinion", "Claude looks at photos the local image model is unsure about "
                      "(Pets, Junk, your categories). Only those photos are sent to Anthropic; everything else "
                      "stays on your PC. Your own answers always win.")
        has_key = bool(ca.get_key())
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=8)
        on = ctk.BooleanVar(value=s.claude_enabled and has_key)

        def toggled():
            if on.get() and not ca.get_key():
                on.set(False)
                self._connect_claude(s)
            else:
                s.claude_enabled = on.get()
        ctk.CTkSwitch(row, text="Claude second opinion", variable=on, command=toggled).pack(side="left")
        ctk.CTkButton(row, text="Connect Claude…" if not has_key else "Change key, model or limit…", width=220,
                      command=lambda: self._connect_claude(s)).pack(side="left", padx=16)
        spent = 0.0
        if self.paths.catalog.exists():
            c = Catalog(self.paths.catalog)
            try:
                spent = ca.spent_this_month(c)
            finally:
                c.close()
        lines = [f"Key: {'saved in Windows Credential Manager' if has_key else 'not added yet'}",
                 f"Model: {ca.MODELS.get(s.claude_model, s.claude_model)}",
                 f"Spent this month: ${spent:.2f} of your ${s.claude_monthly_limit_usd:.2f} limit"]
        for line in lines:
            ctk.CTkLabel(b, text=line, anchor="w", text_color=MUTED).pack(fill="x")

    def _connect_claude(self, s):
        """Wireframe 14's popup: get a key, set up billing, paste and test the key, model, limit."""
        import webbrowser
        from .. import claude_assist as ca
        win = ctk.CTkToplevel(self)
        win.title("Connect Claude")
        win.transient(self)
        pad = {"padx": 26}
        ctk.CTkLabel(win, text="Connect Claude", font=ctk.CTkFont(size=20, weight="bold"), anchor="w"
                     ).pack(fill="x", pady=(20, 2), **pad)
        ctk.CTkLabel(win, text="Claude gives a second opinion on photos the app is unsure about.\nThose photos are "
                     "sent to Anthropic; everything else stays on your PC.", text_color=MUTED, justify="left",
                     anchor="w").pack(fill="x", **pad)
        for n, label, url in (("1", "Get an API key  ↗", ca.KEYS_URL), ("2", "Set up billing  ↗", ca.BILLING_URL)):
            r = ctk.CTkFrame(win, fg_color="transparent")
            r.pack(fill="x", pady=(10, 0), **pad)
            ctk.CTkLabel(r, text=n, text_color=BLUE, font=ctk.CTkFont(size=15, weight="bold"), width=24
                         ).pack(side="left")
            ctk.CTkButton(r, text=label, width=220, command=lambda u=url: webbrowser.open(u)).pack(side="left")
        r = ctk.CTkFrame(win, fg_color="transparent")
        r.pack(fill="x", pady=(14, 0), **pad)
        ctk.CTkLabel(r, text="3", text_color=BLUE, font=ctk.CTkFont(size=15, weight="bold"), width=24).pack(side="left")
        ctk.CTkLabel(r, text="Paste your key", anchor="w").pack(side="left")
        r = ctk.CTkFrame(win, fg_color="transparent")
        r.pack(fill="x", pady=4, **pad)
        key = ctk.CTkEntry(r, width=300, show="•", placeholder_text="sk-ant-…")
        key.pack(side="left", padx=(24, 6))
        existing = ca.get_key()
        if existing:
            key.insert(0, existing)

        def paste():
            try:
                key.delete(0, "end")
                key.insert(0, self.clipboard_get().strip())
            except Exception:
                pass
        ctk.CTkButton(r, text="Paste", width=70, command=paste).pack(side="left", padx=3)
        ctk.CTkButton(r, text="Show", width=70,
                      command=lambda: key.configure(show="" if key.cget("show") else "•")).pack(side="left", padx=3)
        r2 = ctk.CTkFrame(win, fg_color="transparent")
        r2.pack(fill="x", pady=4, **pad)
        status = ctk.CTkLabel(r2, text="", anchor="w")
        def chosen_model():
            return next(k for k, v in ca.MODELS.items() if v == model.get())

        def test():
            status.configure(text="Testing…", text_color=MUTED)
            win.update_idletasks()
            ok, msg = ca.test_key(key.get().strip(), chosen_model())
            status.configure(text=("✓ " if ok else "! ") + msg, text_color=GREEN if ok else AMBER)
        ctk.CTkButton(r2, text="Test key", width=100, command=test).pack(side="left", padx=(24, 10))
        status.pack(side="left")
        r3 = ctk.CTkFrame(win, fg_color="transparent")
        r3.pack(fill="x", pady=(10, 0), **pad)
        ctk.CTkLabel(r3, text="Model:", width=110, anchor="w").pack(side="left")
        model_names = list(ca.MODELS.values())
        model = ctk.CTkOptionMenu(r3, values=model_names, width=260)
        model.set(ca.MODELS.get(s.claude_model, model_names[0]))
        model.pack(side="left")
        r4 = ctk.CTkFrame(win, fg_color="transparent")
        r4.pack(fill="x", pady=(6, 0), **pad)
        ctk.CTkLabel(r4, text="Spending limit:", width=110, anchor="w").pack(side="left")
        limits = [f"${v:g}" for v in ca.LIMIT_STEPS]
        limit = ctk.CTkOptionMenu(r4, values=limits, width=120)
        limit.set(f"${s.claude_monthly_limit_usd:g}" if f"${s.claude_monthly_limit_usd:g}" in limits else "$5")
        limit.pack(side="left")
        ctk.CTkLabel(r4, text="  a month", text_color=MUTED).pack(side="left")
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", pady=18, **pad)

        def apply(enabled):
            for target in {id(s): s, id(self.settings): self.settings}.values():
                target.claude_enabled = enabled
                target.claude_model = chosen_model()
                target.claude_monthly_limit_usd = float(limit.get().lstrip("$"))
            settings_mod.save(self.settings, self.paths.settings)

        def save():
            k = key.get().strip()
            if not k:
                status.configure(text="! Paste your key first", text_color=AMBER)
                return
            try:
                ca.set_key(k)
            except Exception as e:
                status.configure(text=f"! Couldn't save the key in Windows Credential Manager ({e})",
                                 text_color=AMBER)
                return
            apply(True)
            win.destroy()
            self.show_settings("Claude", keep=True)

        def remove():
            ca.delete_key()
            apply(False)
            win.destroy()
            self.show_settings("Claude", keep=True)
        if existing:
            ctk.CTkButton(bar, text="Remove key", width=110, fg_color="transparent", border_width=1,
                          text_color=("#c0392b", "#e57373"), command=remove).pack(side="left")
        ctk.CTkButton(bar, text="Save  (Enter)", fg_color=BLUE, width=140, command=save).pack(side="right")
        ctk.CTkButton(bar, text="Cancel  (Esc)", width=130, fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), command=win.destroy).pack(side="right", padx=8)
        win.bind("<Return>", lambda e: save())
        win.bind("<Escape>", lambda e: win.destroy())
        self._centre(win)
        win.after(50, lambda: (win.grab_set(), key.focus_set()))

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
                from .. import accuracy as acc_mod
                info["scorecard"] = acc_mod.scorecard(cat, self.settings)
                info["review"] = info["fixes"] + info["who"] + info["copies"] + info.get("accuracy_waiting", 0)
            finally:
                cat.close()
        else:
            info["review"] = info["fixes"] = info["who"] = info["copies"] = 0
            info["scorecard"] = []
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
        if not info.get("last_sort"):
            return ("Check the plan, then sort", "The report shows where every photo will go. Approve and sort "
                    "copies them into your library; your originals stay put.", "Open report", self.show_report)
        if info.get("finished"):
            return ("Your library is up to date", "Add photos to a photo folder (or _Add New Photos) and run "
                    "again; they're sorted the same way.", "Start run", self.start_run)
        return ("Your library is sorted", "Run again whenever you add photos. When you're happy with "
                "everything, Finish moves the originals in.", "Open Finish", self.show_finish)

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
                              ("Duplicates & bursts", info.get("copies", 0), lambda: self.show_dups(0)),
                              ("Accuracy check", info.get("accuracy_waiting", 0), self.show_accuracy)):
            if n:
                shown = True
                ctk.CTkButton(chips, text=f"{label}   {n:,}", corner_radius=16, height=32,
                              fg_color="transparent", border_width=1, border_color=("gray70", "gray40"),
                              text_color=("gray15", "gray90"), command=cmd).pack(side="left", padx=(0, 8))
        if not shown:
            ctk.CTkLabel(chips, text="Nothing waiting.", text_color=MUTED).pack(side="left")

        target = int(round(self.settings.accuracy_target * 100))
        ctk.CTkLabel(b, text=f"Accuracy scorecard   (target {target}%)", font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x")
        card = info.get("scorecard") or []
        if not card:
            ctk.CTkLabel(b, text="Appears after your first accuracy check (Review › Accuracy check, then run again).",
                         text_color=MUTED, anchor="w").pack(fill="x", pady=(2, 16))
        else:
            grid = ctk.CTkFrame(b, fg_color="transparent")
            grid.pack(fill="x", pady=(4, 16))
            per_row = 4
            for i, sc in enumerate(card[:12]):
                pct = int(round(sc.accuracy * 100))
                ok = sc.accuracy >= self.settings.accuracy_target
                arrow = {1: " ▲", -1: " ▼", 0: ""}[sc.trend]
                cell = ctk.CTkFrame(grid, border_width=1, border_color=GREEN if ok else AMBER)
                cell.grid(row=i // per_row, column=i % per_row, padx=(0, 8), pady=4, sticky="ew")
                ctk.CTkLabel(cell, text=sc.label, anchor="w").pack(fill="x", padx=12, pady=(6, 0))
                ctk.CTkLabel(cell, text=f"{pct}%{arrow}", anchor="w", text_color=GREEN if ok else AMBER,
                             font=ctk.CTkFont(size=18, weight="bold")).pack(fill="x", padx=12)
                ctk.CTkLabel(cell, text=f"{plural(sc.answered, 'answer')}", anchor="w", text_color=MUTED,
                             font=ctk.CTkFont(size=11)).pack(fill="x", padx=12, pady=(0, 6))
            for c in range(per_row):
                grid.grid_columnconfigure(c, weight=1, uniform="score")

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
        srt = info.get("last_sort")
        if srt:
            when = dt.datetime.fromtimestamp(srt["ended_at"]).strftime("%a %d %b, %I:%M %p")
            ctk.CTkLabel(box, text=f"Last sorted: {when} · {plural(info.get('library_files', 0), 'file')} "
                         "in your library", anchor="w").pack(fill="x", padx=16)
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
        self._centre(win)
        win.after(50, win.grab_set)

    # ---- running (wireframe 06) -------------------------------------------------
    STAGES = (("backup", "Back up"), ("analyze", "Analyze"), ("compare", "Compare & dates"), ("plan", "Plan"))
    STAGE_OF = {"Finding photos": "backup", "Backing up": "backup", "Looking at photos": "analyze",
                "Finding faces": "analyze", "Asking Claude about unsure photos": "analyze",
                "Comparing photos": "compare", "Working out dates and holidays": "compare", "Planning": "plan"}

    def start_run(self):
        if self.runner is not None or getattr(self, "sorting", None):
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
                             progress=lambda *a: self.msgs.put(("progress", *a)), ask_limit=self._ask_limit)
        threading.Thread(target=self._run_worker, daemon=True).start()

    def _run_worker(self):
        try:
            res = self.runner.run()
            self.msgs.put(("done", res))
        except Exception as e:  # shown on the UI thread
            log.get("ui").exception("run failed")
            self.msgs.put(("error", e))

    def _ask_limit(self, spent, limit, done, left):
        """Called on the worker thread: show wireframe 17 and wait up to 5 minutes for an answer."""
        import threading as _th
        ev, box = _th.Event(), {"choice": "local"}
        self.msgs.put(("limit", spent, limit, done, left, ev, box))
        ev.wait(300)                    # no answer in 5 minutes: continue with the free local models
        return box["choice"]

    def _limit_popup(self, spent, limit, done, left, ev, box):
        win = ctk.CTkToplevel(self)
        win.title("Claude spending limit reached")
        win.transient(self)

        def choose(c):
            box["choice"] = c
            ev.set()
            win.destroy()
        ctk.CTkLabel(win, text="Claude spending limit reached", font=ctk.CTkFont(size=17, weight="bold"),
                     anchor="w").pack(fill="x", padx=22, pady=(18, 4))
        ctk.CTkLabel(win, text=f"You've spent ${spent:.2f} of your ${limit:.2f} limit this month.\n"
                     f"Claude gave a second opinion on {plural(done, 'unsure photo')}.\n"
                     f"{plural(left, 'unsure photo')} are left. What should happen to them?",
                     anchor="w", justify="left").pack(fill="x", padx=22)
        for i, (text, c) in enumerate((("Continue with the free local models   (they go to Needs Review)", "local"),
                                       ("Raise the limit by $5 and keep using Claude", "raise"),
                                       ("Pause the run", "pause")), 1):
            ctk.CTkButton(win, text=f"{i}   {text}", anchor="w", height=38, fg_color="transparent", border_width=1,
                          border_color=("gray70", "gray40"), text_color=("gray10", "gray90"),
                          hover_color=("gray88", "gray25"), command=lambda c=c: choose(c)).pack(fill="x", padx=22, pady=3)
            win.bind(str(i), lambda e, c=c: choose(c))
        ctk.CTkLabel(win, text="No answer in 5 minutes? The run continues with the free local models.",
                     text_color=MUTED, anchor="w").pack(fill="x", padx=22, pady=(6, 18))
        win.protocol("WM_DELETE_WINDOW", lambda: choose("local"))
        win.after(300_000, lambda: win.winfo_exists() and win.destroy())
        self._centre(win)
        win.after(50, win.grab_set)

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
                    self.runner = self.sorting = None
                    self.show_home()
                    self.error(msg[1])
                elif kind == "limit":
                    self._limit_popup(*msg[1:])
                elif kind == "sort_progress":
                    self._sort_progress(*msg[1:])
                elif kind == "job_done":
                    msg[1](msg[2])
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
        self._centre(win)
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
            if self._counts().get("accuracy_waiting"):
                return self.show_accuracy()
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

    # ---- accuracy check (wireframe 10) ----------------------------------------------------
    def show_accuracy(self):
        from .. import accuracy as acc
        derived = str(self.paths.data / "derived")
        qs = []
        if self.paths.catalog.exists():
            c = Catalog(self.paths.catalog)
            try:
                qs = acc.questions(c, derived)
            finally:
                c.close()
        todo = [q for q in qs if q.answer is None and not q.skipped]
        if not todo:
            self._screen("Accuracy check", nav="review", subtitle="All done for this round. Your answers update "
                         "the scorecard when the next run starts.")
            self._footer(back=("Home", self.show_home))
            self.bind("<Control-z>", lambda e: self._acc_undo())
            self.bind("<Left>", self._not_typing(lambda: self._acc_undo()))
            self._hotkey_line("Esc Home · Ctrl+Z Undo last answer")
            return
        q = todo[0]
        done = len(qs) - len(todo)
        b = self._screen(q.question, nav="review", subtitle="Accuracy check: the app's guess is hidden so your "
                         f"answer is a fair test.      {done + 1} of {len(qs)}")
        cw, ch = self._avail()
        side_w = 300
        top = ctk.CTkFrame(b, fg_color="transparent")
        top.pack(fill="both", expand=True)
        crop = ("circle", q.face_box) if q.face_box else None
        self._images = []
        self._image_box(top, q.image, (int(cw - side_w - 40), int(ch - 120)), crop=crop).pack(side="left", anchor="n")
        side = ctk.CTkFrame(top, fg_color="transparent", width=side_w)
        side.pack(side="left", fill="y", padx=(20, 0), anchor="n")
        ctk.CTkLabel(side, text="Pick one", font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x", pady=(0, 6))

        def pick(choice):
            c2 = Catalog(self.paths.catalog)
            try:
                acc.answer(c2, q.id, choice)
            finally:
                c2.close()
            self.show_accuracy()

        def skip():
            c2 = Catalog(self.paths.catalog)
            try:
                acc.skip(c2, q.id)
            finally:
                c2.close()
            self.show_accuracy()
        for i, opt in enumerate(q.options[:9], 1):
            ctk.CTkButton(side, text=f"{i}   {opt}", anchor="w", height=40, width=side_w,
                          fg_color="transparent", border_width=1, border_color=("gray70", "gray40"),
                          text_color=("gray10", "gray90"), hover_color=("gray88", "gray25"),
                          command=lambda o=opt: pick(o)).pack(fill="x", pady=3)
            self.bind(str(i), self._not_typing(lambda o=opt: pick(o)))
        if q.options == ["Yes", "No"]:
            self.bind("y", self._not_typing(lambda: pick("Yes")))
            self.bind("n", self._not_typing(lambda: pick("No")))
        ctk.CTkLabel(side, text="Results update your scorecard after the next run.", text_color=MUTED,
                     wraplength=side_w, justify="left", anchor="w").pack(fill="x", pady=(12, 0))
        ctk.CTkButton(self.footer_area, text="Back  (←)", fg_color="transparent", border_width=1, width=140,
                      text_color=("gray20", "gray85"), command=self._acc_undo).pack(side="left")
        self.bind("<Escape>", lambda e: self.show_home())
        ctk.CTkButton(self.footer_area, text="Skip  (→)", width=140, height=36, fg_color="transparent",
                      border_width=1, text_color=("gray20", "gray85"), command=skip).pack(side="right")
        self.bind("<Right>", self._not_typing(skip))
        self.bind("<Left>", self._not_typing(self._acc_undo))
        self.bind("<Control-z>", lambda e: self._acc_undo())
        self._hotkey_line("1–9 Answer · → Skip · ← Back · Ctrl+Z Undo · Esc Close (progress saved)")

    def _acc_undo(self):
        from .. import accuracy as acc
        if self.paths.catalog.exists():
            c = Catalog(self.paths.catalog)
            try:
                acc.undo_last(c)
            finally:
                c.close()
        self.show_accuracy()

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
        self._centre(win)
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

    def show_report(self, folder: str | None = None, page: int = 0, selected: str | None = None):
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
        info_last = summary(self.paths).get("last_run")
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
            ctk.CTkLabel(right, text="Spot a wrong one? Select it and press M to move it or Del to remove it "
                         "from this folder.  O shows where the original is.", text_color=MUTED, anchor="w"
                         ).pack(fill="x")
            grid = ctk.CTkFrame(right, fg_color="transparent")
            grid.pack(fill="x", pady=6)
            self._images = []
            cw, ch = self._avail()
            tw = int(max(100, min(220, (cw - 230 - 60) / 4 - 10)))
            th = int(tw * 0.8)
            shown = entries[page * self.REPORT_PAGE:(page + 1) * self.REPORT_PAGE]
            if selected not in {e.key for e in shown}:
                selected = None
            for i, e in enumerate(shown):
                name = e.name if len(e.name) <= 14 else e.name[:13] + "…"
                on = e.key == selected
                frame = ctk.CTkFrame(grid, border_width=3 if on else 0, border_color=BLUE,
                                     fg_color=("#eef4ff", "#1e2a3a") if on else "transparent")
                frame.grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky="n")
                cell = self._image_box(frame, e.image, (tw, th), name + ("\ndate is a guess" if e.is_guess else ""))
                cell.pack(padx=3, pady=3)
                for w in [frame, cell, *cell.winfo_children()]:
                    w.bind("<Button-1>", lambda ev, k=e.key: self.show_report(folder, page, k))
            sel_entry = next((e for e in shown if e.key == selected), None)
            self.bind("m", self._not_typing(lambda: sel_entry and self._report_move(folder, page, sel_entry)))
            self.bind("<Delete>", self._not_typing(lambda: sel_entry and self._report_remove(folder, page, sel_entry)))
            self.bind("o", self._not_typing(lambda: sel_entry and self._report_original(sel_entry)))
            self.bind("<Control-z>", lambda ev: self._report_undo(folder, page))
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
            import json as _json
            run_counts = (info_last or {}).get("counts") or {}
            if isinstance(run_counts, str):
                try:
                    run_counts = _json.loads(run_counts)
                except ValueError:
                    run_counts = {}
            hand = run_counts.get("library") or {}
            if hand.get("moved") or hand.get("removed"):
                rows.append((f"You moved {hand.get('moved', 0):,} and removed {hand.get('removed', 0):,} library "
                             "photos by hand; those count as your answers", "", None))
            if hand.get("edited") or hand.get("unknown"):
                rows.append((f"{plural(hand.get('edited', 0) + hand.get('unknown', 0), 'library file')} you edited or "
                             "added yourself are left alone", "", None))
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
        can_sort = bool(pv.folders) and bool(self.settings.library)
        ctk.CTkButton(self.footer_area, text="Approve and sort  (Enter)", fg_color=BLUE, width=240, height=38,
                      state="normal" if can_sort else "disabled", command=self.start_sort).pack(side="right")
        ctk.CTkLabel(self.footer_area, text=f"Copies into {self.settings.library}. Originals aren't touched.",
                     text_color=MUTED, font=ctk.CTkFont(size=12)).pack(side="right", padx=12)
        if can_sort:
            self.bind("<Return>", lambda e: self.start_sort())
        self._hotkey_line("Enter Approve and sort · Esc Back to Home")

    # ---- report corrections ------------------------------------------------------------------
    def _report_fix(self, folder, page, entry, action):
        from .. import corrections, dates as dates_mod
        cat = Catalog(self.paths.catalog)
        try:
            a = corrections.asset_of(cat, entry.key)
            if a is None:
                self._notice("Can't change this one", "Whole scans and files that couldn't be opened stay where "
                             "they are. Mark a cut-out as wrong in Review instead.")
                return
            tokens = action(cat, *a)
            if tokens:
                dates_mod.resolve_all(cat, self.settings)
                self._report_undo_stack = getattr(self, "_report_undo_stack", []) + [tokens]
        finally:
            cat.close()
        self.show_report(folder, page)

    def _report_remove(self, folder, page, entry):
        from .. import corrections
        if folder == "_Duplicates":
            return self.show_dups(0)
        self._report_fix(folder, page, entry, lambda cat, aid, ck, year:
                         corrections.remove_from(cat, self.settings, aid, ck, folder))

    def _report_move(self, folder, page, entry):
        from .. import corrections, plan as plan_mod, people as people_mod
        cat = Catalog(self.paths.catalog)
        try:
            pv = plan_mod.preview(cat, str(self.paths.data / "derived"), self.settings.categories)
            known = people_mod.names(cat)
        finally:
            cat.close()
        year = folder.split("/")[1] if folder.startswith("Old Family Photos/") else folder.split("/")[0]
        root = "Old Family Photos/" if folder.startswith("Old Family Photos/") else ""
        options = {k for k in pv.folders if not k.startswith("_") and k != folder and "Original Scans" not in k}
        if year[:4].isdigit() or year.startswith("Unknown"):
            options |= {f"{root}{year}/People/{n}" for n in known}
            options |= {f"{root}{year}/{lab}" for lab, *_ in self.settings.categories.values()}
            options |= {f"{root}{year}/Pets", "_Junk"}
        options.discard(folder)
        win = ctk.CTkToplevel(self)
        win.title("Move to…")
        win.geometry("520x520")
        win.transient(self)
        ctk.CTkLabel(win, text=f"Move {entry.name} to…", font=ctk.CTkFont(size=16, weight="bold"), anchor="w"
                     ).pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(win, text="Type to filter, or type a new person's name or a year.", text_color=MUTED,
                     anchor="w").pack(fill="x", padx=20)
        q = ctk.CTkEntry(win, placeholder_text="e.g. Mom, Christmas, 2004")
        q.pack(fill="x", padx=20, pady=8)
        lst = ctk.CTkScrollableFrame(win, height=330)
        lst.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        def go(dest):
            win.destroy()
            self._report_fix(folder, page, entry, lambda cat, aid, ck, yr:
                             corrections.move(cat, self.settings, aid, ck, folder, dest, yr))

        def refresh(_=None):
            for w in lst.winfo_children():
                w.destroy()
            text = q.get().strip()
            shown = sorted(o for o in options if text.lower() in o.lower())
            if text and text.isdigit() and len(text) == 4:
                rest = folder.split("/", 2 if root else 1)[-1] if "/" in folder else "Other"
                shown.insert(0, f"{root}{text}/{rest}")
            elif text and not shown:
                shown = [f"{root}{year}/People/{people_mod.proper_case(text)}"]
            for o in shown[:200]:
                ctk.CTkButton(lst, text=o.replace("/", " › "), anchor="w", fg_color="transparent",
                              text_color=("gray10", "gray90"), hover_color=("gray88", "gray25"),
                              command=lambda d=o: go(d)).pack(fill="x")
            win.bind("<Return>", lambda e: shown and go(shown[0]))
        q.bind("<KeyRelease>", refresh)
        win.bind("<Escape>", lambda e: win.destroy())
        refresh()
        self._centre(win)
        win.after(50, lambda: (win.grab_set(), q.focus_set()))

    def _report_undo(self, folder, page):
        from .. import corrections, dates as dates_mod
        stack = getattr(self, "_report_undo_stack", [])
        if not stack:
            return
        cat = Catalog(self.paths.catalog)
        try:
            corrections.undo(cat, stack.pop())
            dates_mod.resolve_all(cat, self.settings)
        finally:
            cat.close()
        self.show_report(folder, page)

    def _report_original(self, entry):
        from .. import restore as restore_mod
        if not entry.key[1:].isdigit():
            return
        cat = Catalog(self.paths.catalog)
        try:
            fid = int(entry.key[1:]) if entry.key.startswith("f") else \
                cat.scalar("SELECT file_id FROM assets WHERE id=?", (int(entry.key[1:]),))
            info = restore_mod.original_by_file_id(cat, fid) if fid else None
        finally:
            cat.close()
        self._show_original(info)

    def _show_original(self, info):
        if not info:
            self._notice("Original not found", "This file isn't one the app knows about.")
            return
        where = info["orig_path"]
        if info["finished"] and not info["exists"]:
            state = "Moved into your library at Finish."
        elif info["exists"]:
            state = "Still in its original folder."
        else:
            state = "Not in its folder any more."
        backup = f"Backed up in {info['backup_zip']}." if info["backup_zip"] else "Not backed up yet."
        win = ctk.CTkToplevel(self)
        win.title("Original")
        win.transient(self)
        ctk.CTkLabel(win, text="Where this photo came from", font=ctk.CTkFont(size=16, weight="bold"), anchor="w"
                     ).pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(win, text=f"{where}\n\n{state}\n{backup}", justify="left", anchor="w", wraplength=520
                     ).pack(fill="x", padx=20)
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=16)
        if info["exists"]:
            ctk.CTkButton(bar, text="Show in folder", width=140,
                          command=lambda: self._reveal(where)).pack(side="left")
        elif info["backup_zip"]:
            ctk.CTkButton(bar, text="Restore it", width=140,
                          command=lambda: (win.destroy(), self._restore_ids([info["file_id"]]))).pack(side="left")
        ctk.CTkButton(bar, text="OK  (Enter)", width=120, command=win.destroy).pack(side="right")
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())
        self._centre(win)
        win.after(50, win.grab_set)

    def _reveal(self, path):
        import subprocess
        import sys as _sys
        try:
            if _sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except OSError:
            pass

    # ---- Google Photos (wireframe 16) -------------------------------------------------------
    def show_google(self, zips: list | None = None):
        import datetime as dt
        import webbrowser
        from .. import google
        zips = zips or []
        b = self._screen("Bring in your Google Photos", nav="settings",
                         subtitle="Google only lets you take your whole library out as an export. It takes three steps.")
        requested = None
        if self.paths.catalog.exists():
            c = Catalog(self.paths.catalog)
            try:
                requested = google.get_state(c, "takeout_requested")
            finally:
                c.close()

        def step(n, title, text):
            box = ctk.CTkFrame(b, border_width=1, border_color=("gray75", "gray35"), fg_color="transparent")
            box.pack(fill="x", pady=6)
            ctk.CTkLabel(box, text=str(n), font=ctk.CTkFont(size=20, weight="bold"), text_color=BLUE, width=40
                         ).pack(side="left", padx=(14, 4), pady=14, anchor="n")
            col = ctk.CTkFrame(box, fg_color="transparent")
            col.pack(side="left", fill="x", expand=True, pady=12)
            ctk.CTkLabel(col, text=title, font=ctk.CTkFont(size=15, weight="bold"), anchor="w").pack(fill="x")
            ctk.CTkLabel(col, text=text, text_color=MUTED, anchor="w", justify="left", wraplength=WRAP - 320
                         ).pack(fill="x")
            right = ctk.CTkFrame(box, fg_color="transparent")
            right.pack(side="right", padx=14)
            return right

        def open_takeout():
            webbrowser.open(google.TAKEOUT_URL)
            c = Catalog(self.paths.catalog)
            try:
                google.set_state(c, "takeout_requested", dt.date.today().isoformat())
            finally:
                c.close()
            self.show_google(zips)
        r = step(1, "Ask Google for an export", "Opens Google Takeout with only Google Photos selected. "
                 "Choose 50 GB zip files.")
        ctk.CTkButton(r, text="Open Google Takeout  ↗", width=200, command=open_takeout).pack()
        if requested:
            ctk.CTkLabel(r, text=f"✓ Requested {dt.date.fromisoformat(requested):%d %b}", text_color=GREEN).pack()
        r = step(2, "Wait for Google's email", "Usually a few hours, sometimes a day or two. You can close the "
                 "app meanwhile.")
        ctk.CTkLabel(r, text="Waiting for email…" if requested and not zips else "", text_color=MUTED).pack()
        r = step(3, "Choose the downloaded zips", "Download every zip from the email, then pick them here.")

        def choose():
            paths = filedialog.askopenfilenames(title="Choose the Google Takeout zips",
                                                filetypes=[("Zip files", "*.zip")])
            if paths:
                self.show_google(sorted(set(zips) | set(paths)))
        ctk.CTkButton(r, text="Choose zip files…", width=200, command=choose).pack()
        problems = []
        if zips:
            n, size, problems = google.check_zips(zips)
            amount = f"{size / 1024 ** 3:.1f} GB" if size >= 1024 ** 3 else f"{size / 1024 ** 2:.0f} MB"
            ctk.CTkLabel(r, text=f"{plural(len(zips), 'zip')} · {amount} · {plural(n, 'photo')}",
                         text_color=GREEN if not problems else AMBER).pack()
        for p in problems:
            ctk.CTkLabel(b, text="!  " + p, text_color=AMBER, anchor="w").pack(fill="x")
        ctk.CTkLabel(b, text="The zips are copied into your backup folder and checked. Google's own dates are used "
                     "for each photo.", text_color=MUTED, anchor="w", wraplength=WRAP, justify="left"
                     ).pack(fill="x", pady=(8, 0))
        self._footer(back=("Cancel", lambda: self.show_settings("Folders")))
        ok = bool(zips) and not problems

        def start():
            if ok:
                self._job("Bringing in your Google Photos", "The zips are copied into your backup, then unpacked.",
                          "Import", "files", lambda prog: app_mod.google_import(self.paths, self.settings, zips, prog),
                          self._google_done)
        ctk.CTkButton(self.footer_area, text="Add to my photos  (Enter)", fg_color=BLUE, width=240, height=38,
                      state="normal" if ok else "disabled", command=start).pack(side="right")
        self.bind("<Return>", lambda e: start())
        self._hotkey_line("Enter Add to my photos · Esc Cancel")

    def _google_done(self, res):
        self.sorting = None
        self.folders_changed = True
        self.show_home()
        if res is None:
            self._notice("Import paused", "Choose the same zips again to carry on.")
            return
        text = (f"{plural(res.photos, 'photo')} and videos from {plural(res.zips, 'zip')} are ready "
                f"({res.dated:,} with Google's date). Click Run to sort them.")
        if res.skipped:
            text += f"\n{plural(len(res.skipped), 'file')} couldn't be unpacked (see the log)."
        self._notice("Google Photos added", text)

    # ---- sorting (wireframe 06 layout, one bar) ------------------------------------------
    def start_sort(self):
        self._job("Sorting into your library", "Copies only. Your originals stay where they are until Finish.",
                  "Sort", "changes", lambda prog: app_mod.sort_library(self.paths, self.settings, prog),
                  self._sort_finished)

    def _job(self, title, subtitle, bar_label, unit, work, finished):
        """A one-bar progress screen (wireframe 06 layout) for Approve and sort and Finish."""
        import threading as _th
        if self.runner is not None or getattr(self, "sorting", None):
            return
        self._job_unit = unit
        b = self._screen(title, nav="run", nav_locked=True, subtitle=subtitle)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=bar_label, font=ctk.CTkFont(size=15, weight="bold"), anchor="w", width=150
                     ).grid(row=0, column=0, sticky="w", pady=12)
        self.sort_bar = ctk.CTkProgressBar(row)
        self.sort_bar.set(0)
        self.sort_bar.grid(row=0, column=1, sticky="ew", padx=12)
        self.sort_status = ctk.CTkLabel(row, text="Getting ready…", text_color=MUTED, anchor="w", width=260)
        self.sort_status.grid(row=0, column=2, sticky="w")
        now = ctk.CTkFrame(b, fg_color="transparent")
        now.pack(fill="x", pady=(24, 0))
        self.now_image = ctk.CTkLabel(now, text="", width=120, height=90, fg_color=("gray90", "gray20"))
        self.now_image.pack(side="left")
        self.now_text = ctk.CTkLabel(now, text="", anchor="w", justify="left")
        self.now_text.pack(side="left", padx=16)
        self._last_thumb = 0.0
        stop = _th.Event()

        def progress(label, done, total, detail=None):
            if stop.is_set():
                raise app_mod.Cancelled()
            self.msgs.put(("sort_progress", label, done, total, detail))

        def pause():
            stop.set()
            self.now_text.configure(text="Pausing after the current photo…")

        ctk.CTkButton(self.footer_area, text="Pause  (Space)", command=pause, width=180, height=36).pack(side="right")
        self.bind("<space>", lambda e: pause())
        self.sorting = stop      # busy: a run can't start meanwhile

        def worker():
            try:
                self.msgs.put(("job_done", finished, work(progress)))
            except app_mod.Cancelled:
                self.msgs.put(("job_done", finished, None))
            except Exception as e:
                log.get("ui").exception("sort failed")
                self.msgs.put(("error", e))
        _th.Thread(target=worker, daemon=True).start()

    def _sort_progress(self, label, done, total, detail):
        import time as _t
        if not (hasattr(self, "sort_bar") and self.sort_bar.winfo_exists()):
            return
        if total:
            self.sort_bar.set(done / total)
            self.sort_status.configure(text=f"{done:,} of {total:,} {self._job_unit}")
        else:
            self.sort_status.configure(text=label + "…")
        if detail and _t.time() - self._last_thumb > 1.5:
            self._last_thumb = _t.time()
            im = self._thumb(detail, (120, 90))
            if im is not None:
                self._images = (getattr(self, "_images", []))[-4:] + [im]
                self.now_image.configure(image=im, text="")
            self.now_text.configure(text=os.path.basename(detail))

    def _sort_finished(self, res):
        self.sorting = None
        self.show_home()
        if res is None:
            self._notice("Sorting paused", "Press Approve and sort again to carry on. Nothing was lost.")
            return
        parts = [f"{res.added:,} added", f"{res.updated:,} updated", f"{res.removed:,} removed"]
        if res.restored:
            parts.append(f"{res.restored:,} put back")
        lines = [", ".join(parts) + f"; {res.unchanged:,} already right."]
        if res.edited_by_you:
            lines.append(f"{plural(len(res.edited_by_you), 'file')} you changed were left alone.")
        if res.in_the_way:
            lines.append(f"{plural(len(res.in_the_way), 'photo')} weren't copied because another file "
                         "already has that name.")
        if res.failed:
            lines.append(f"{plural(len(res.failed), 'photo')} couldn't be copied. Run Approve and sort "
                         "again; the log lists them.")
        self._notice("Your library is sorted", "\n".join(lines))

    def _centre(self, win):
        """Put a popup in the middle of the main window (not the corner of the screen)."""
        win.update_idletasks()
        w, h = max(win.winfo_reqwidth(), win.winfo_width()), max(win.winfo_reqheight(), win.winfo_height())
        x = self.winfo_rootx() + (self.winfo_width() - w) // 2
        y = self.winfo_rooty() + (self.winfo_height() - h) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _notice(self, title: str, text: str):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.transient(self)
        ctk.CTkLabel(win, text=title, font=ctk.CTkFont(size=17, weight="bold"), anchor="w"
                     ).pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(win, text=text, anchor="w", justify="left", wraplength=460).pack(fill="x", padx=20)
        ctk.CTkButton(win, text="OK  (Enter)", width=120, command=win.destroy).pack(anchor="e", padx=20, pady=16)
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())
        self._centre(win)
        win.after(50, win.grab_set)

    # ---- finish (wireframe 13) ------------------------------------------------------
    def show_finish(self):
        import datetime as dt
        from .. import finish as finish_mod
        info = self._counts()
        b = self._screen("Finish: move originals into your library", nav="finish", review_count=info["review"],
                         subtitle="Do this once, when you're happy with how everything is sorted.")
        if self.paths.catalog.exists():
            cat = Catalog(self.paths.catalog)
            try:
                fp = finish_mod.plan(cat, self.settings.library, str(self.paths.data / "derived"),
                                     self.settings.categories)
            finally:
                cat.close()
        else:
            fp = finish_mod.Plan(problems=["Run first so everything is backed up."])

        def check(ok, text):
            ctk.CTkLabel(b, text=("✓  " if ok else "!  ") + text, text_color=GREEN if ok else AMBER, anchor="w",
                         justify="left", wraplength=WRAP).pack(fill="x", pady=(0, 4))
        if fp.backup_date:
            check(True, f"Backup verified on {dt.datetime.fromtimestamp(fp.backup_date):%d %b} "
                        f"({plural(fp.backup_files, 'file')})")
        else:
            check(False, "Run first so everything is backed up")
        for prob in fp.problems:
            check(False, prob)
        if not fp.problems:
            check(True, "Your library is sorted and up to date")
        card = info.get("scorecard") or []
        if card:
            t = self.settings.accuracy_target
            good = [c for c in card if c.accuracy >= t]
            check(len(good) == len(card), f"Accuracy at or above {int(round(t * 100))}% for {len(good)} of "
                                          f"{plural(len(card), 'check')}")
            for c in card:
                if c.accuracy < t:
                    check(False, f"{c.label} is at {int(round(c.accuracy * 100))}% — you may want one more "
                                 "review round")
        if info.get("review"):
            check(False, f"{plural(info['review'], 'photo')} still waiting for review. You can finish anyway; "
                         "they stay sorted as they are now.")
        ctk.CTkLabel(b, text="", height=6).pack()
        box = ctk.CTkFrame(b)
        box.pack(fill="x")
        ctk.CTkLabel(box, text="What will happen", font=ctk.CTkFont(weight="bold"),
                     anchor="w").pack(fill="x", padx=16, pady=(12, 4))
        lines = [f"{plural(len(fp.move), 'original')} move into {self.settings.library} "
                 "(fixed photos stay as their fixed version)"]
        if fp.recycle:
            lines.append(f"{plural(len(fp.recycle), 'copy', 'copies')} you chose to remove go to the Recycle Bin")
        lines += ["Folders left completely empty are deleted; folders with anything else are left alone",
                  "Every move is logged first, so an interruption can be finished or undone",
                  f"Your backup zips stay in {self.settings.backup}"]
        if fp.left:
            lines.append(f"{plural(len(fp.left), 'file')} stay where they are for now (not sorted yet)")
        for line in lines:
            ctk.CTkLabel(box, text="• " + line, anchor="w", justify="left", wraplength=WRAP - 40
                         ).pack(fill="x", padx=16)
        ctk.CTkLabel(box, text="", height=6).pack()
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(16, 0))
        ctk.CTkLabel(row, text="Type FINISH to confirm:", anchor="w").pack(side="left")
        word = ctk.CTkEntry(row, width=200, height=34, state="normal" if fp.ready else "disabled")
        word.pack(side="left", padx=12)
        self._footer(back=("Cancel", self.show_home))
        go = ctk.CTkButton(self.footer_area, text="Move originals  (Enter)", fg_color=BLUE, width=240, height=38,
                           state="disabled", command=lambda: start())
        go.pack(side="right")

        def typed(_=None):
            ok = fp.ready and word.get().strip().upper() == "FINISH"
            go.configure(state="normal" if ok else "disabled")
            return ok

        def start():
            if typed():
                self._job("Moving originals into your library", "Each original is checked against the backup "
                          "and your library before it leaves its old folder.", "Finish", "photos",
                          lambda prog: app_mod.finish_library(self.paths, self.settings, prog), self._finish_done)
        word.bind("<KeyRelease>", typed)
        word.bind("<Return>", lambda e: start())
        if fp.ready:
            word.focus_set()
        self._hotkey_line("Type FINISH, then Enter · Esc Cancel")

    def _finish_done(self, res):
        self.sorting = None
        self.show_home()
        if res is None:
            self._notice("Finish paused", "Open Finish again to carry on. Nothing was lost.")
            return
        lines = [f"{plural(res.moved, 'original')} moved into your library."]
        if res.recycled:
            lines.append(f"{plural(res.recycled, 'copy', 'copies')} sent to the Recycle Bin.")
        if res.folders_removed:
            lines.append(f"{plural(res.folders_removed, 'empty folder')} deleted.")
        if res.skipped:
            lines.append(f"{plural(len(res.skipped), 'file')} were left where they are (changed since the backup, "
                         "or not in your library yet). The log lists them.")
        self._notice("Finished", "\n".join(lines))

    # ---- restore (wireframe 15) -------------------------------------------------------
    def show_restore(self, result=None):
        from ..restore import backups
        b = self._screen("Get something back", nav="restore")
        cards = (("Restore everything", "Put every original back exactly where it was, from your backups.",
                  self._restore_all, True),
                 ("Restore a photo or folder", "Search by name or original folder and restore just that.",
                  self.show_restore_search, True),
                 ("Find an original", "Pick a photo in your library and see the untouched file it came from.",
                  self._find_original, True))
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

    def _find_original(self):
        from tkinter import filedialog
        from .. import restore as restore_mod
        path = filedialog.askopenfilename(title="Pick a photo in your library", initialdir=self.settings.library
                                          or None)
        if not path or not self.paths.catalog.exists():
            return
        cat = Catalog(self.paths.catalog)
        try:
            info = restore_mod.original_of(cat, self.settings.library, path)
        finally:
            cat.close()
        self._show_original(info)

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
        win.transient(self)
        lines = []
        for group, keys in self.HOTKEYS:
            lines.append(group)
            lines += [f"  {k:<8} {what}" for k, what in keys]
            lines.append("")
        ctk.CTkLabel(win, text="\n".join(lines).rstrip(), font=ctk.CTkFont(family="Consolas", size=13),
                     justify="left").pack(padx=24, pady=20, anchor="w")
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Return>", lambda e: win.destroy())
        self._centre(win)


def run_window() -> int:
    paths = AppPaths.default()
    log.setup_logging(paths.data)
    ctk.set_appearance_mode("system")
    app = App(paths)
    app.mainloop()
    return 0
