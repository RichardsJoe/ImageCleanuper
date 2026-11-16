# ImageCleanuper

Automatically back up, identify, label and sort photos by year, holiday, person and pets. Runs locally on Windows.

See the [process flowchart](docs/FLOWCHART.md), the [app wireframes](docs/WIREFRAMES.md) and the [pseudocode](docs/PSEUDOCODE.md).

## What it does

1. **Backs up first.** Every photo and video in your photo folders (nested folders too) goes into verified zip files in your backup folder before anything else happens. Google Photos come in through Google Takeout; the zips themselves are kept as their backup.
2. **Analyzes.** Dates (camera info, Google's dates, file names, similar photos, folder names), US holidays and your own events, duplicates and burst shots, camera rotation, border trim, old scans (split into separate prints, turned upright using faces), faces, pets, your categories and junk (a local image model; optionally a second opinion from Claude on the unsure ones).
3. **Asks you only what it isn't sure about.** Who is this? (three name suggestions, 1/2/3), Needs review (Yes/No), Duplicates & bursts (pick what to keep), and a short blind **accuracy check** with a scorecard. Your answers teach it; locked answers always win.
4. **Shows the plan** (Report): `Year/Holidays/<Holiday>`, `Year/People/<Name>`, `Year/Pets`, `Year/<Category>`, `Year/Videos`, `Year/Other`, `Unknown Date`, `Old Family Photos/…` (+ `Original Scans`), `_Junk`, `_Duplicates`, `_Could Not Open`. Fix anything with M (move), Del (remove from this folder) or O (show the original).
5. **Approve and sort** copies everything into your library. Originals stay untouched. Moving or deleting photos in the library yourself counts as an answer; files you edit or add are left alone.
6. **Finish** (type FINISH) moves the originals in: each one is checked against the backup and the library first, copies you removed go to the Recycle Bin, and folders left empty are deleted. Later, drop new photos in `_Add New Photos` (in the library) and click Run.

Everything is undoable or recoverable: every file operation is journaled, Ctrl+Z undoes answers, and **Restore** puts originals back from the backup zips.

## Claude (optional)

Settings › Claude › Connect Claude: get an API key, set up billing, paste and test the key (it's stored in Windows Credential Manager, never in `settings.ini`). Only photos the local model is unsure about are sent, one small JPEG each, using Claude Sonnet 5.5 by default. A monthly spending limit (default $5) is enforced: when it's reached you choose to continue with the free local models, raise the limit, or pause; with no answer in 5 minutes the run continues locally.

## Install (Windows)

Download `ImageCleanuper-Setup-<version>.exe` from this repo's **Releases** page (or, for test builds, from the **Actions** tab → latest run → Artifacts) and double-click it. It installs for your user only, so no admin rights are needed.

The first time you run it, Windows may show "Windows protected your PC" because the app isn't code-signed. Click **More info → Run anyway**.

Your settings (`settings.ini`) and the app's data (`data\`) live in the install folder and are kept when you update or uninstall.

## For developers

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

```bash
python -m pytest -q
```

```bash
python -m imagecleanuper            # app window
```

```bash
python -m imagecleanuper --cli run  # run without the window (also: status, restore-all)
```

Every push to `main` runs the tests and builds the installer on GitHub's Windows machines (artifact `<version>-dev.<run>`). Pushing a tag like `v1.0.0` also publishes a Release. In the app, **Check for updates** (bottom of the nav bar) opens the Releases page.
