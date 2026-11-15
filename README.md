# ImageCleanuper

Automatically back up, identify, label and sort photos by year, holiday, person and pets. Runs locally on Windows.

See the [process flowchart](docs/FLOWCHART.md), the [app wireframes](docs/WIREFRAMES.md) and the [pseudocode](docs/PSEUDOCODE.md).

## Status

| Phase | What it does | State |
|---|---|---|
| 1 Foundation | Settings, find every photo/video in nested folders, verified incremental zip backup, crash recovery, Restore, learned-data backup, Windows installer | **Built** (0.1) |
| 2 Analysis | Dates, US holidays + custom events, duplicates, camera rotation, border trim, scan detection and splitting (prints in corners, grids, Polaroids, blank cards), upright prints via faces | **Built** (0.2–0.3) |
| — Screens | Built to wireframes v4: nav bar, Home, Running, Needs review, Report (preview), Finish, Restore, Settings | **Built** (0.3) |
| 3 People & categories | Faces, Who is this?, pets, categories, Junk, learning, local image model (also judges which way is up) | Next |
| 4 Sorting | Plan, report, library sync, accuracy check, Finish | Planned |
| 5 Extras | Google Photos import, optional Claude, polish | Planned |

Versions 0.1–0.3 do **not** move, rename or change any original photo. They find, back up and analyze; fixed versions (rotated, trimmed, cut out of scans) are saved separately in the app's `data\derived` folder.

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

Every push to `main` runs the tests and builds the installer on GitHub's Windows machines. Pushing a tag like `v0.1.0` also publishes a Release.
