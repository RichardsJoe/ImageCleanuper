"""The app window, phase 1 (pseudocode section 24, wireframes 02-07 and 15).

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
        self.geometry("960x640")
        self.minsize(820, 560)
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

    # ---- helpers ----------------------------------------------------------
    NAV_ITEMS = (("home", "Home"), ("run", "Run  (R)"), ("restore", "Restore"), ("settings", "Settings"))

    def _screen(self, title: str, subtitle: str = "", nav: str | None = "home",
                nav_locked: bool = False) -> ctk.CTkFrame:
        """Every screen: optional nav bar on the left, a title, a scrolling middle and a footer
        pinned to the bottom (so buttons are always visible, however small the window)."""
        if self.body is not None:
            self.body.destroy()
        for seq in ("<Return>", "<Escape>", "<Key-r>", "<Key-a>", "<space>"):
            self.unbind(seq)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True)
        if nav is not None:
            self._nav(self.body, nav, nav_locked)
        main = ctk.CTkFrame(self.body, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True, padx=32, pady=(20, 16))
        ctk.CTkLabel(main, text=title, font=ctk.CTkFont(size=22, weight="bold"), anchor="w").pack(fill="x")
        if subtitle:
            ctk.CTkLabel(main, text=subtitle, text_color=MUTED, anchor="w", justify="left",
                         wraplength=WRAP).pack(fill="x", pady=(4, 8))
        self.footer_area = ctk.CTkFrame(main, fg_color="transparent", height=1)
        self.footer_area.pack(side="bottom", fill="x", pady=(12, 0))   # packed before content: always visible
        content = ctk.CTkScrollableFrame(main, fg_color="transparent")
        content.pack(fill="both", expand=True)
        return content

    def _nav(self, parent, current: str, locked: bool) -> None:
        bar = ctk.CTkFrame(parent, width=170, corner_radius=0)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="ImageCleanuper", font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=16, pady=(20, 16))
        targets = {"home": self.show_home, "run": self.start_run, "restore": self.show_restore,
                   "settings": self.show_settings}
        for key, label in self.NAV_ITEMS:
            active = key == current
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

    def error(self, e: Exception):
        win = ctk.CTkToplevel(self)
        win.title("Something needs attention")
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
    def show_settings(self):
        """Everything on one screen. Works on a copy: Cancel throws changes away."""
        import copy
        s = copy.deepcopy(self.settings)
        b = self._screen("Folders & settings", nav="settings")
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

    # ---- home ----------------------------------------------------------------
    def show_home(self):
        b = self._screen("Home", nav="home")
        info = summary(self.paths)
        last = info["last_run"]
        nxt = ctk.CTkFrame(b, border_width=1, border_color=BLUE)
        nxt.pack(fill="x", pady=(8, 16))
        if getattr(self, "folders_changed", False):
            step, sub = "Scan your updated folders", ("Your photo folders changed. Run to pick up the new ones; "
                                                      "removed folders no longer count (their backups are kept).")
        elif last is None:
            step, sub = "Run your first scan", "Backs up every photo and video, then works out dates, duplicates and scans."
        elif last["state"] in ("crashed", "stopped"):
            step, sub = "Continue the last run", "It stopped early. Nothing was lost."
        else:
            step, sub = "Run again", "Picks up any new photos. Your photos aren't moved yet; sorting comes in a later version."
        ctk.CTkLabel(nxt, text="NEXT STEP", text_color=BLUE, font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=20, pady=(14, 0))
        ctk.CTkLabel(nxt, text=step, font=ctk.CTkFont(size=19, weight="bold"), anchor="w").pack(fill="x", padx=20)
        ctk.CTkLabel(nxt, text=sub, text_color=MUTED, anchor="w", justify="left",
                     wraplength=WRAP - 260).pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkButton(nxt, text="Start  (Enter)", fg_color=BLUE, height=40, width=200,
                      command=self.start_run).place(relx=1.0, rely=0.5, x=-20, anchor="e")
        self.bind("<Return>", lambda e: self.start_run())

        self.bind("<Key-r>", self._not_typing(self.start_run))
        stats = (f"{plural(info['photos'], 'photo')} and {plural(info['videos'], 'video')} found · "
                 f"{info['backed_up']:,} backed up in {plural(info['parts'], 'verified zip part')}")
        ctk.CTkLabel(b, text=stats, anchor="w").pack(fill="x", pady=(24, 4))
        if info.get("analyzed"):
            self._found_panel(b, info)
        if last:
            ctk.CTkLabel(b, text=f"Last run: #{last['id']} · {last['state']}" +
                         (f" · {last['message']}" if last.get("message") else ""),
                         text_color=MUTED, anchor="w", wraplength=WRAP, justify="left").pack(fill="x")
        for p in self.state_info.problems:
            ctk.CTkLabel(b, text="⚠ " + p, text_color=AMBER, anchor="w", wraplength=WRAP,
                         justify="left").pack(fill="x")
        ctk.CTkLabel(self.footer_area, text="Enter Next step · R Run · F1 All hotkeys", text_color=MUTED,
                     anchor="w").pack(side="left")

    def _found_panel(self, b, info):
        """What phase 2 worked out. Read-only for now; the review screens come in later phases."""
        box = ctk.CTkFrame(b)
        box.pack(fill="x", pady=(12, 4))
        ctk.CTkLabel(box, text="What the app found", font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=16, pady=(12, 4))
        src = info.get("date_sources", {})
        dated = sum(v for k, v in src.items() if k != "unknown")
        guesses = src.get("similar", 0) + src.get("folder", 0)
        hol = info.get("holidays", {})
        from ..dates import HOLIDAY_NAMES
        top = ", ".join(f"{HOLIDAY_NAMES.get(k, k.split(':', 1)[-1])} {v}" for k, v in list(hol.items())[:4])
        lines = [
            f"Dates: {dated:,} dated ({src.get('exif', 0):,} from the camera, {src.get('filename', 0):,} from file "
            f"names, {guesses:,} best guesses) · {src.get('unknown', 0):,} unknown",
            f"Holidays: {sum(hol.values()):,} photos" + (f" ({top})" if top else ""),
            f"Duplicates: {plural(info.get('dup_extras', 0), 'extra copy').replace('copys', 'copies')} "
            f"in {plural(info.get('dup_groups', 0), 'group')}",
            f"Old scans: {plural(info.get('scans', 0), 'scan')}, cut into {plural(info.get('cutouts', 0), 'photo')}",
            f"Fixed: {plural(info.get('fixed', 0), 'photo')} rotated or trimmed"
            + (f" · {info['to_check']:,} to check later" if info.get("to_check") else ""),
        ]
        if info.get("could_not_open"):
            lines.append(f"Couldn't open: {plural(info['could_not_open'], 'file')}")
        for line in lines:
            ctk.CTkLabel(box, text=line, anchor="w", justify="left", wraplength=WRAP - 30).pack(fill="x", padx=16)
        ctk.CTkLabel(box, text="Nothing has been moved. Your originals are untouched.", text_color=MUTED,
                     anchor="w").pack(fill="x", padx=16, pady=(4, 12))

    def ask_resume(self):
        r = self.state_info.crashed_run
        win = ctk.CTkToplevel(self)
        win.title("Pick up where you left off?")
        win.geometry("520x220")
        win.transient(self)
        ctk.CTkLabel(win, text="Pick up where you left off?", font=ctk.CTkFont(size=18, weight="bold")
                     ).pack(padx=24, pady=(24, 6), anchor="w")
        ctk.CTkLabel(win, text=f"Run #{r['id']} stopped during '{r.get('last_step') or 'start'}'. "
                     "Nothing was lost; the next run continues from there.",
                     wraplength=470, justify="left").pack(padx=24, anchor="w")
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=24, pady=20)
        ctk.CTkButton(bar, text="Later (Esc)", fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), command=win.destroy).pack(side="left")
        ctk.CTkButton(bar, text="Continue (Enter)", fg_color=BLUE,
                      command=lambda: (win.destroy(), self.start_run())).pack(side="right")
        win.bind("<Return>", lambda e: (win.destroy(), self.start_run()))
        win.bind("<Escape>", lambda e: win.destroy())
        win.after(50, win.grab_set)

    # ---- running -------------------------------------------------------------
    def start_run(self):
        if self.runner is not None:
            return
        self.settings, self.state_info.problems = settings_mod.load(self.paths.settings)
        b = self._screen("Working on your photos", nav="run", nav_locked=True, subtitle=
                         "You can keep using your PC. If it's interrupted, the next run continues from there.")
        self.run_label = ctk.CTkLabel(b, text="Starting…", anchor="w")
        self.run_label.pack(fill="x", pady=(20, 6))
        self.run_bar = ctk.CTkProgressBar(b)
        self.run_bar.set(0)
        self.run_bar.pack(fill="x")
        bar = self._footer()
        ctk.CTkButton(bar, text="Pause (Space)", command=self.pause_run, width=160).pack(side="right")
        self.bind("<space>", lambda e: self.pause_run())
        self.runner = Runner(self.paths, self.settings,
                             progress=lambda label, d, t: self.msgs.put(("progress", label, d, t)))
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
            self.run_label.configure(text="Pausing after the current file…")

    def _poll(self):
        try:
            while True:
                msg = self.msgs.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, label, done, total = msg
                    if hasattr(self, "run_label") and self.run_label.winfo_exists():
                        self.run_label.configure(text=f"{label}: {done:,}" + (f" of {total:,}" if total else ""))
                        if total:
                            self.run_bar.set(done / total)
                elif kind == "done":
                    self.runner = None
                    self.folders_changed = False
                    self.unbind("<space>")
                    self.show_home()
                elif kind == "error":
                    self.runner = None
                    self.unbind("<space>")
                    self.show_home()
                    self.error(msg[1])
                elif kind == "restore_done":
                    self.show_restore(result=msg[1])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ---- restore ---------------------------------------------------------------
    def show_restore(self, result=None):
        b = self._screen("Get something back", nav="restore", subtitle=
                         "Put originals back exactly where they were, from your verified backups. "
                         "Nothing is ever overwritten without asking.")
        ctk.CTkButton(b, text="Restore everything", height=56, anchor="w",
                      command=self._restore_all).pack(fill="x", pady=6)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(16, 4))
        q = ctk.CTkEntry(row, placeholder_text="Search by file or folder name, e.g. Christmas or IMG_0412")
        q.pack(side="left", fill="x", expand=True)
        results = ctk.CTkScrollableFrame(b, height=200)

        def search():
            from ..restore import find_files
            for w in results.winfo_children():
                w.destroy()
            cat = Catalog(self.paths.catalog)
            rows = find_files(cat, q.get().strip())
            cat.close()
            for r in rows:
                line = ctk.CTkFrame(results)
                line.pack(fill="x", pady=2)
                ctk.CTkLabel(line, text=r["orig_path"], anchor="w").pack(side="left", padx=8)
                ctk.CTkButton(line, text="Restore", width=90,
                              command=lambda i=r["id"]: self._restore_ids([i])).pack(side="right", padx=6)
        ctk.CTkButton(row, text="Search", width=110, command=search).pack(side="left", padx=(8, 0))
        q.bind("<Return>", lambda e: search())
        results.pack(fill="both", expand=True, pady=6)
        if result is not None:
            text = (f"Restored {result.restored}, already in place {result.already_there}, "
                    f"skipped {result.skipped}, kept both {result.kept_both}, failed {len(result.failed)}.")
            ctk.CTkLabel(b, text=text, text_color=GREEN if not result.failed else AMBER,
                         anchor="w").pack(fill="x")
        self._footer(back=("Home", self.show_home))

    def _restore_all(self):
        self._restore_ids(None)

    def _restore_ids(self, ids):
        def worker():
            cat = Catalog(self.paths.catalog)
            try:
                # Conflicts are skipped (never overwritten); keep-both can be chosen per file later.
                r = restore_files(cat, self.settings.backup, file_ids=ids)
                self.msgs.put(("restore_done", r))
            except Exception as e:
                self.msgs.put(("error", e))
            finally:
                cat.close()
        threading.Thread(target=worker, daemon=True).start()

    # ---- damaged catalog ---------------------------------------------------------
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
