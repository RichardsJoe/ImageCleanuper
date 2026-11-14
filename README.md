# ImageCleanuper

Automatically back up, identify, label and sort photos by year, holiday, person and pets. Runs locally on Windows.

See the [process flowchart](docs/FLOWCHART.md), the [app wireframes](docs/WIREFRAMES.md) and the [pseudocode](docs/PSEUDOCODE.md).

## Status

| Phase | What it does | State |
|---|---|---|
| 1 Foundation | Settings, find every photo/video in nested folders, verified incremental zip backup, crash recovery, Restore, learned-data backup, Windows installer | **Built** |
| 2 Analysis | Dates, holidays, duplicates, rotation, trim, scan detection and splitting | Next |
| 3 People & categories | Faces, Who is this?, pets, categories, Junk, learning | Planned |
| 4 Sorting | Plan, report, library sync, accuracy check, Finish | Planned |
| 5 Extras | Google Photos import, optional Claude, polish | Planned |

Version 0.1 does **not** move, rename or change any photo. It only finds them and makes a verified backup.

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
