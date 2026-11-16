"""Opens every phase 1 screen once. Runs where tkinter and a display exist (GitHub's Windows build)."""

import pytest

tk = pytest.importorskip("tkinter")


def test_screens_open(env):
    paths, s, m1, _ = env
    try:
        from imagecleanuper.ui.window import App
        app = App(paths)
    except tk.TclError:
        pytest.skip("no display")
    try:
        from images import save_jpeg, scene
        from imagecleanuper.app import Runner
        save_jpeg(scene(1), m1 / "a.jpg", date="2019:12:25 10:00:00", make="Apple")
        Runner(paths, s).run()  # so Home shows the "What the app found" panel
        app.settings = s
        for show in (app.show_setup_folders, app.show_setup_locations, app.show_setup_ready,
                     app.show_home, app.show_review, app.show_dups, app.show_accuracy, app.show_report, app.show_finish, app.show_settings,
                     app.show_restore, app.show_restore_search, app.show_google, app.show_hotkeys):
            show()
            app.update()
        for tab in app.SETTINGS_TABS:
            app.show_settings(tab, keep=True)
            app.update()
        app._settings_draft = None
        # Approve and sort from the Report: progress screen, then Home with the summary.
        import os
        import time
        app.show_report()
        app.start_sort()
        deadline = time.time() + 60
        while getattr(app, "sorting", None) and time.time() < deadline:
            app.update()
            time.sleep(0.05)
        assert not getattr(app, "sorting", None)
        assert os.path.exists(os.path.join(s.library, "2019", "Holidays", "Christmas", "a.jpg"))
    finally:
        app.destroy()
