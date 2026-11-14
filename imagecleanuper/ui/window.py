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
    def _screen(self, title: str, subtitle: str = "") -> ctk.CTkFrame:
        if self.body is not None:
            self.body.destroy()
        for seq in ("<Return>", "<Escape>", "<Key-r>", "<Key-a>"):
            self.unbind(seq)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=40, pady=24)
        ctk.CTkLabel(self.body, text=title, font=ctk.CTkFont(size=22, weight="bold"),
                     anchor="w").pack(fill="x")
        if subtitle:
            ctk.CTkLabel(self.body, text=subtitle, text_color=MUTED, anchor="w", justify="left",
                         wraplength=860).pack(fill="x", pady=(4, 12))
        return self.body

    def _footer(self, back=None, primary=None):
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.pack(side="bottom", fill="x", pady=(12, 0))
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
        b = self._screen("Where are your photos?",
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
    def show_setup_locations(self):
        b = self._screen("Where should things go?")
        entries = {}
        for key, label, hint in (
                ("library", "Sorted library", "Your finished, sorted photos. Can't be inside a photo folder."),
                ("backup", "Backup folder", "A verified zip of every original is saved here before anything else.")):
            ctk.CTkLabel(b, text=label, font=ctk.CTkFont(size=15, weight="bold"), anchor="w").pack(fill="x", pady=(12, 0))
            ctk.CTkLabel(b, text=hint, text_color=MUTED, anchor="w").pack(fill="x")
            row = ctk.CTkFrame(b, fg_color="transparent")
            row.pack(fill="x", pady=4)
            e = ctk.CTkEntry(row)
            e.insert(0, getattr(self.settings, key) or "")
            e.pack(side="left", fill="x", expand=True)

            def browse(e=e, label=label):
                d = filedialog.askdirectory(title=f"Choose the {label.lower()}")
                if d:
                    e.delete(0, "end")
                    e.insert(0, os.path.normpath(d))
            ctk.CTkButton(row, text="Browse…", width=110, command=browse).pack(side="left", padx=(8, 0))
            entries[key] = e
        status = ctk.CTkLabel(b, text="", anchor="w", justify="left", wraplength=860)
        status.pack(fill="x", pady=12)

        def save_and_next():
            self.settings.library = entries["library"].get().strip()
            self.settings.backup = entries["backup"].get().strip()
            probs = settings_mod.blocking_problems(self.settings)
            if not self.settings.library or not self.settings.backup:
                probs.insert(0, "Choose both folders.")
            if probs:
                status.configure(text="\n".join(probs), text_color=AMBER)
                return
            settings_mod.save(self.settings, self.paths.settings)
            self.show_setup_ready()
        self._footer(back=("Back", self.show_setup_folders), primary=("Next", save_and_next))

    # ---- setup step 3: ready ---------------------------------------------------
    def show_setup_ready(self):
        b = self._screen("Ready for your first run",
                         "In this version the run finds every photo and video and makes a verified backup. "
                         "Your photos are not moved or changed. Sorting comes in the next versions.")
        for n, title, text in (("1", "Find", "Every photo and video in your folders, however deeply nested."),
                               ("2", "Back up", "Everything is zipped to your backup folder and checked file by file."),
                               ("3", "Done", "You can stop any time; the next run continues where it stopped.")):
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
        b = self._screen("ImageCleanuper")
        info = summary(self.paths)
        last = info["last_run"]
        nxt = ctk.CTkFrame(b, border_width=1, border_color=BLUE)
        nxt.pack(fill="x", pady=(8, 16))
        if last is None:
            step, sub = "Run your first backup", "Finds every photo and video and backs it up."
        elif last["state"] in ("crashed", "stopped"):
            step, sub = "Continue the last run", "It stopped early. Nothing was lost."
        else:
            step, sub = "Run again", "Backs up any new photos. Sorting arrives in the next version."
        ctk.CTkLabel(nxt, text="NEXT STEP", text_color=BLUE, font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=20, pady=(14, 0))
        ctk.CTkLabel(nxt, text=step, font=ctk.CTkFont(size=19, weight="bold"), anchor="w").pack(fill="x", padx=20)
        ctk.CTkLabel(nxt, text=sub, text_color=MUTED, anchor="w").pack(fill="x", padx=20, pady=(0, 14))
        self.bind("<Return>", lambda e: self.start_run())

        tiles = ctk.CTkFrame(b, fg_color="transparent")
        tiles.pack(fill="x")
        for label, cmd in (("Run (R)", self.start_run), ("Restore", self.show_restore),
                           ("Folders & settings", self.show_setup_folders)):
            ctk.CTkButton(tiles, text=label, command=cmd, height=56, width=200,
                          fg_color="transparent", border_width=1,
                          text_color=("gray15", "gray90")).pack(side="left", padx=(0, 12))
        self.bind("<Key-r>", self._not_typing(self.start_run))
        stats = (f"{info['photos']:,} photos and {info['videos']:,} videos found · "
                 f"{info['backed_up']:,} backed up in {info['parts']} verified zip parts")
        ctk.CTkLabel(b, text=stats, anchor="w").pack(fill="x", pady=(24, 4))
        if last:
            ctk.CTkLabel(b, text=f"Last run: #{last['id']} · {last['state']}" +
                         (f" · {last['message']}" if last.get("message") else ""),
                         text_color=MUTED, anchor="w", wraplength=860, justify="left").pack(fill="x")
        for p in self.state_info.problems:
            ctk.CTkLabel(b, text="⚠ " + p, text_color=AMBER, anchor="w", wraplength=860,
                         justify="left").pack(fill="x")
        ctk.CTkLabel(b, text="Enter Next step · R Run · F1 All hotkeys", text_color=MUTED,
                     anchor="w").pack(side="bottom", fill="x")

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
        b = self._screen("Working on your photos",
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
        b = self._screen("Get something back",
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
        self._footer(back=("Back", self.show_home))

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
        self._screen("The app's data needs repairing",
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
