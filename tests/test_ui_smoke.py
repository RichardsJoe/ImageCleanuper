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
        app.settings = s
        for show in (app.show_setup_folders, app.show_setup_locations, app.show_setup_ready,
                     app.show_home, app.show_settings, app.show_restore, app.show_hotkeys):
            show()
            app.update()
    finally:
        app.destroy()
