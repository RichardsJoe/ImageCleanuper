# ImageCleanuper Pseudocode

The blueprint for version 1. Every module in the real code should match a section here; when code and pseudocode disagree, one of them has a bug. Read with the [flowchart](FLOWCHART.md) (what happens) and [wireframes](WIREFRAMES.md) (what you see).

Each section ends with **Bug traps**: mistakes that are easy to make in that part and must have a test.

**Contents**

0. [Ground rules (apply everywhere)](#0-ground-rules-apply-everywhere)
1. [Modules and build phases](#1-modules-and-build-phases)
2. [Data model (the catalog)](#2-data-model-the-catalog)
3. [Settings](#3-settings)
4. [App start](#4-app-start)
5. [Run (top level)](#5-run-top-level)
6. [Discover files](#6-discover-files)
7. [Backup and verify](#7-backup-and-verify)
8. [Analyze each file](#8-analyze-each-file)
9. [Compare across all files](#9-compare-across-all-files)
10. [Dates and holidays](#10-dates-and-holidays)
11. [People: grouping and matching](#11-people-grouping-and-matching)
12. [Learning from answers](#12-learning-from-answers)
13. [Claude second opinion (optional)](#13-claude-second-opinion-optional)
14. [Review queues](#14-review-queues)
15. [Accuracy check](#15-accuracy-check)
16. [Plan destinations](#16-plan-destinations)
17. [Report](#17-report)
18. [Sync the library](#18-sync-the-library)
19. [Finish](#19-finish)
20. [Restore](#20-restore)
21. [Google Photos import](#21-google-photos-import)
22. [Learned-data backup](#22-learned-data-backup)
23. [Hand-edit detection](#23-hand-edit-detection)
24. [UI, hotkeys and undo](#24-ui-hotkeys-and-undo)
25. [Errors and logging](#25-errors-and-logging)
26. [Test checklist](#26-test-checklist)

---

## 0. Ground rules (apply everywhere)

These are invariants. Code that breaks one is a bug even if nothing visibly fails.

```text
R1  NEVER modify, rename, move or delete a source file before Finish.
    Only Finish (section 19) may move or delete sources, and only after R2 holds.
R2  A source file may only be moved/deleted if its fingerprint is in a VERIFIED backup part.
R3  Every write is atomic: write to "<name>.partial", flush + fsync, verify, then rename.
    A crash can leave a .partial file but never a half-written real file.
R4  Every multi-step file operation is journaled first (intent → do → mark done), so
    a crash can be finished or rolled back on next start.
R5  The app only deletes or overwrites files it created and recorded in the catalog
    (the "owned files" list). Unknown files in the library are never touched.
R6  A locked item's answer always wins over any model prediction.
R7  Nothing leaves the PC except: model downloads, update checks, and (only if the user
    turned it on) photos sent to Claude, shrunk to ~512 px.
R8  Every long loop saves progress per item (catalog commit) so a crash redoes at most one item.
R9  Every user action is undoable (Ctrl+Z) until the next run, except Finish (journaled instead).
R10 Identity of a file = SHA-256 of its bytes (the "fingerprint"). Paths change; fingerprints don't.
R11 Deterministic output: the same catalog + answers always produce the same plan and file
    names, so re-runs don't shuffle files.
```

Shared helpers used throughout:

```text
function long_path(p):                       # Windows 260-char limit (fix 6)
    p = absolute(p)
    if p starts with "\\?\": return p
    if p starts with "\\": return "\\?\UNC\" + p[2:]
    return "\\?\" + p

function atomic_write(dest, write_fn):       # R3
    tmp = dest + ".partial"
    write_fn(open(long_path(tmp), "wb"))
    flush; fsync
    rename(tmp, dest)  (replace if exists only when caller allows)

function atomic_copy(src, dest, expected_sha):
    atomic_write(dest, copy bytes of src)
    if sha256(dest) != expected_sha: delete dest; raise CopyVerifyError

function journal(op_kind, payload) -> entry_id     # R4
    insert into journal(op_kind, payload, state="intent"); commit
function journal_done(entry_id): update state="done"; commit
```

**Bug traps**
- Using a normal path for a deeply nested file → silent "file not found". Always go through `long_path`.
- Forgetting fsync before rename → a power cut can leave an empty "finished" file.
- Comparing paths case-sensitively on Windows (`C:\Photos` vs `c:\photos`). Normalize with `normcase` before comparing.

---

## 1. Modules and build phases

```text
app/
  settings.py        3   load/validate/save settings.ini
  catalog.py         2   SQLite catalog, journal, migrations
  startup.py         4   first-launch downloads, single instance, resume check
  discover.py        6   find files in sources, change detection
  backup.py          7   incremental zip parts + verify + restore reader
  analyze/
    open_image.py    8   decode any supported file, read metadata
    scans.py         8   scan detection + multi-photo split
    fix.py           8   rotation + border trim
    faces.py         8   detect faces, face fingerprints (embeddings)
    content.py       8   pets + categories (image model + descriptions)
    hashes.py        8/9 exact + look-alike hashes
  compare.py         9   duplicates, similar photos
  dates.py           10  date chain, holidays, folder clues
  people.py          11  grouping unknown faces, matching known people
  learning.py        12  apply answers, retrain heads, thresholds, checkpoints
  claude.py          13  optional second opinion with spending limit
  review.py          14  build review queues, record answers
  accuracy.py        15  blind sampling + scoring + ratings
  plan.py            16  destinations, file names, diff vs library
  report.py          17  data for the report screen
  sync.py            18  apply plan to library (copies only)
  finish.py          19  move originals, delete empty folders
  restore.py         20  restore from backups
  google_import.py   21  Takeout zips
  ui/                24  CustomTkinter screens, hotkeys, undo stack
  log.py             25  logging + error reporting
```

| Phase | Sections built |
|---|---|
| 1 Foundation | 0, 2, 3, 4, 6, 7, 20, 22, 25, `.exe` build |
| 2 Analysis | 8 (no faces/content yet), 9, 10 |
| 3 People & categories | 8 faces/content, 11, 12, 14 |
| 4 Sorting | 15, 16, 17, 18, 19, 23 |
| 5 Extras | 13, 21, 24 polish, READ MEs |

---

## 2. Data model (the catalog)

One SQLite file: `<app folder>\data\catalog.db`. WAL mode, `foreign_keys=ON`. Every table has `created_at`/`updated_at`.

```text
sources        (id, kind: folder|google_import|add_new, path, enabled)
files          (id, sha256, size, source_id, rel_path, orig_path, mtime,
                kind: photo|video|other, status: new|backed_up|analyzed|could_not_open,
                open_error, first_seen_run, last_seen_run, missing: bool)
                -- one row per path; the same sha256 may appear on several rows (exact duplicates)
backup_parts   (id, zip_path, created_run, file_count, bytes, verified: bool, verified_at)
backup_entries (part_id, sha256, arcname)           -- what is safely backed up (R2)
assets         (id, file_id, parent_asset_id NULL, kind: whole|cutout,
                cutout_index, derived_path NULL, needs_fix: bool)
                -- an "asset" is one photo the user sees. A plain file = 1 asset.
                -- a multi-photo scan = N cutout assets (parent file kept for reference).
fixes          (asset_id, rotation_deg, trim_box, confidence, status: auto|review|accepted|undone)
meta           (asset_id, exif_date, exif_date_raw, camera_make, camera_model, software,
                google_date, filename_date, width, height, is_scan, scan_confidence,
                scan_reason)
hashes         (asset_id, phash, dhash, image_embedding BLOB)
faces          (id, asset_id, box, score, size_px, embedding BLOB, crop_path,
                person_id NULL, stranger_id NULL, match_score, match_source: model|user|claude)
people         (id, name UNIQUE COLLATE NOCASE, created_at, merged_into NULL)
strangers      (id, label "Stranger 001")
categories     (id, key, folder, description, builtin: bool)
predictions    (asset_id, check: pet|category:<key>|junk|scan|rotation|trim|same_event,
                value, confidence, source: model|claude, run_id)
answers        (id, item_kind, item_id, check, value, source: user|claude|qa,
                locked: bool, best_sample: bool, run_id, undone: bool)
dates          (asset_id, date, source: exif|google|filename|similar|folder|unknown,
                borrowed_from_asset NULL, confidence, is_guess: bool)
holidays       (asset_id, holiday_key NULL, source: date|folder)
dup_groups     (id, kind: exact|near), dup_members (group_id, asset_id, is_kept, user_choice)
qa_items       (id, run_id, check, item_id, blind_name, hidden_prediction, answer NULL,
                correct NULL, scored_run NULL)
ratings        (check_key, run_id, correct, total, accuracy)
plan           (run_id, asset_id, dest_rel_path, role: primary|extra_copy, reason)
owned_files    (rel_path UNIQUE, asset_id, sha256, created_run)   -- R5
journal        (id, op_kind, payload JSON, state: intent|done|rolled_back, created_at)
runs           (id, started_at, ended_at, state: running|done|crashed, last_step,
                last_item_id, counts JSON)
claude_usage   (run_id, month, requests, input_tokens, output_tokens, cost_usd)
model_state    (id, saved_at, labels_seen, path)   -- learned heads checkpoints
```

**Bug traps**
- `people.name` must be case-insensitive unique (`COLLATE NOCASE`), or "calvin" and "Calvin" become two people.
- Keep `orig_path` forever, even after Finish; Restore and "Find an original" depend on it.
- Schema changes need a migration step with a version number; never edit tables in place without one.

---

## 3. Settings

`settings.ini` lives next to the app (fix 7). INI is read with `configparser` and **interpolation turned off** (so `%` in a path doesn't crash).

```text
[Folders]      library, backup, master1..masterN
[Review]       people_per_round = 20, min_face_px = 40
[Accuracy]     start_items = 50, target = 0.95, min_per_check = 3, rounds_to_shrink = 2
[Learning]     save_every_labels = 25, learned_versions_kept = 10
[Holidays]     country = US, window.<holiday> = before,after   (e.g. christmas = 2,2)
[Event:<name>] month, day, window           (custom events, e.g. birthdays)
[Category:<k>] description, folder
[Hotkeys]      save = Return, next = Right, ...
[Claude]       enabled = no, model = claude-sonnet-5-5, monthly_limit_usd = 5
               (the API key is NOT here; it's in Windows Credential Manager)

function load_settings():
    if file missing: return defaults (first launch → setup wizard)
    parse; for each missing key: use default (log it)
    validate():
        library, backup: absolute paths, not inside any master folder, not inside each other
        each master: exists (warn if not; don't crash), not inside library or backup
        numbers in range (target 0.5..1.0, people_per_round 1..100, ...)
        hotkeys: no duplicates within a screen
    return settings + list of problems (shown in Settings screen, never a crash)

function save_settings(s):  atomic_write(settings.ini)   # R3
```

**Bug traps**
- `configparser` lowercases keys by default; that's fine for keys but **never** for values (paths, names).
- A master folder added later that *contains* the library (e.g. `C:\`) must be refused, or the app backs up and sorts its own output.
- Comparing "is inside" must use normalized absolute paths plus a trailing separator (`C:\Photos2` is not inside `C:\Photos`).

---

## 4. App start

```text
function main():
    acquire single-instance lock (named mutex "ImageCleanuper"); if taken: focus other window; exit
    setup logging (section 25)
    settings, problems = load_settings()
    ensure_models_downloaded()                     # first launch only
    catalog = open_catalog(); run migrations
    recover_journal()                              # finish or roll back interrupted ops (R4)
    learned_data_check()                           # section 22: restore offer if damaged
    crashed = runs where state == running
    if crashed: mark crashed; show "Pick up where you left off?" (Continue / Start over)
    if no settings or no masters: show Setup wizard else show Home

function ensure_models_downloaded():
    manifest = built-in list of {name, url (our GitHub Release), sha256, size}
    for item in manifest:
        if file exists and sha256 matches: continue
        download with HTTP Range resume into item.partial; show progress
        if sha256(item.partial) != item.sha256: delete; retry up to 3 times; else show error
        rename partial → final
    pick ONNX Runtime provider: try DirectML; on failure fall back to CPU and tell the user once

function recover_journal():
    for entry in journal where state == intent (oldest first):
        if entry.op_kind == "finish_move": finish.recover(entry)   # section 19
        if entry.op_kind == "sync_write":  delete leftover .partial; mark rolled_back
        if entry.op_kind == "backup_part": delete unverified part file; mark rolled_back
```

**Bug traps**
- Two copies of the app running at once would corrupt the catalog. The single-instance lock is not optional.
- Recovering the journal must happen **before** anything else touches files.
- A model file that fails its fingerprint check must never be loaded "just this once".

---

## 5. Run (top level)

```text
function run(mode):                       # mode = first | normal | resume
    run_id = new run row (state=running); keep PC awake (SetThreadExecutionState)
    try:
        step("discover",  discover_files)                      # 6
        step("backup",    backup_new_files)                    # 7  — gate
        if not all_new_files_backed_up(): stop_run("Backup not verified")   # R2
        step("analyze",   analyze_pending)                     # 8  (per item, resumable)
        step("compare",   compare_all)                         # 9
        step("dates",     resolve_dates_and_holidays)          # 10
        step("answers",   ingest_answers)                      # 12 (labels from last round)
        step("people",    match_and_group_people)              # 11
        if mode == first: open "Who is this?" for the first round; wait until closed
        step("learn",     learn_from_answers)                  # 12
        step("score",     score_accuracy_round)                # 15 (answers from last round)
        step("claude",    claude_second_opinion)               # 13 (no-op if off)
        step("plan",      build_plan)                          # 16
        step("queues",    build_review_queues_and_sample)      # 14, 15
        show Report (17); wait for Approve / Not yet
        if approved: step("sync", sync_library)                # 18
        step("final_check", verify_every_source_accounted_for)
        step("learned_backup", backup_learned_data)            # 22
        run.state = done
    on exception e: log; run.state = crashed; show plain-English error + "Continue later"
    finally: allow sleep again

function step(name, fn):
    if run.last_step is after `name` (resume): skip
    run.last_step = name; commit
    fn()          # fn itself commits per item (R8)
```

**Bug traps**
- `ingest_answers` must run **before** matching and learning, or last round's corrections are ignored for one extra run.
- Scoring the accuracy round must use the predictions stored *when the sample was made*, not today's predictions.
- If the user clicks "Not yet", nothing in the library changes, but analysis and answers are kept.

---

## 6. Discover files

```text
PHOTO_EXT = .jpg .jpeg .png .heic .heif .webp .tif .tiff .bmp .gif
VIDEO_EXT = .mp4 .mov .m4v .avi .mkv .3gp .wmv .mts
SKIP      = Thumbs.db, desktop.ini, .DS_Store, files starting with "._", *.partial

function discover_files():
    excluded = [library, backup, app folder]       # never scan our own output
    seen_paths = set()
    for source in enabled sources:
        for (dirpath, dirs, files) in walk(long_path(source.path), follow_symlinks=False):
            dirs[:] = [d for d in dirs if join(dirpath, d) not inside excluded and not hidden/system]
            for name in files:
                if name in SKIP: continue
                kind = by extension (else: skip, count as "other")
                p = join(dirpath, name); seen_paths.add(p)
                row = files where source_id, rel_path == ...
                st = stat(p)
                if row exists and row.size == st.size and row.mtime == st.mtime:
                    row.last_seen_run = run_id; continue          # unchanged, no rehash
                sha = sha256_stream(p)        # read in 4 MB chunks
                if row exists: update sha/size/mtime; status = new (changed file)
                else: insert files(..., status=new)
                commit every 200 files
    mark rows not in seen_paths as missing (do NOT delete rows; Restore needs them)
    report: counts per source, unreachable paths (permission/too long) listed
```

**Bug traps**
- Following symlinks/junctions can loop forever (`C:\Users\X\AppData\...\Application Data`). Don't follow them.
- Extension checks must be case-insensitive (`IMG_0001.JPG`).
- A file being copied *while* we scan can change between stat and hash. If size/mtime changed after hashing, rehash once, then mark it "busy, try next run".
- Never treat "missing" as "deleted by user, so delete from backup". Backups are append-only.

---

## 7. Backup and verify

```text
PART_MAX_FILES = 1000; PART_MAX_BYTES = 4 GB

function backup_new_files():
    todo = files where sha256 not in backup_entries (distinct by sha256)     # fix 1a: once per content
    check free space on backup drive ≥ sum(todo sizes) * 1.05 + 1 GB, else stop with message
    for chunk in split(todo, PART_MAX_FILES, PART_MAX_BYTES):
        part_path = backup\photos_<YYYY-MM-DD>_<run_id>_<n>.zip
        e = journal("backup_part", {part_path})
        atomic_write(part_path, zip64 writer, compression=STORED):
            for f in chunk:
                arcname = "<source_label>/<rel_path>"     # keeps original folders (unique per source)
                write f bytes from long_path(f.orig_path)
        verify_part(part_path, chunk)                     # re-open, read every member, sha256
        if ok: insert backup_parts(verified=true) + backup_entries; journal_done(e)
        else: delete part; mark rolled_back; stop_run("Backup check failed")   # VER gate
    also write/refresh backup\manifest_<date>.csv  (sha256, size, orig_path, part, arcname)

function verify_part(zip, chunk):
    expected = {arcname: sha256}
    for member in zip: if sha256(member bytes) != expected[member.name]: return false
    return set(zip names) == set(expected)
```

**Bug traps**
- Zip files over 4 GB or with over 65,535 entries need ZIP64. Force it on.
- Two master folders can contain the same `rel_path`; the `<source_label>/` prefix prevents name collisions in the zip.
- Non-ASCII file names (é, emoji) must be stored as UTF-8 in the zip (set the UTF-8 flag).
- A part that is written but not verified must never be counted as a backup (R2).
- Hash the bytes **as read for the zip**, not an earlier read, or a file changed mid-run slips through.

---

## 8. Analyze each file

Runs per file; each file's results are committed before moving on (R8).

```text
function analyze_pending():
    for f in files where status == backed_up (ordered by id, resume from run.last_item_id):
        try: analyze_file(f)
        except DecodeError as e: f.status = could_not_open; f.open_error = plain_reason(e)
        commit; run.last_item_id = f.id

function analyze_file(f):
    if f.kind == video:
        m = read_video_metadata(f)          # creation date, duration; no faces/pets
        create asset(kind=whole); meta.exif_date = m.date; return
    img, info = open_image(f)               # Pillow (+ HEIF plugin); apply nothing yet
    meta = read_metadata(info)              # EXIF DateTimeOriginal, Make/Model/Software, size
    meta.filename_date = parse_date_from_filename(f.name)
    scan = detect_scan(img, meta)           # (is_scan, confidence, reason)
    if scan.is_scan and (boxes = find_photos_on_scan(img)) has ≥ 2 boxes:
        for i, box in enumerate(boxes):
            piece = straighten_and_crop(img, box)
            asset = create asset(kind=cutout, parent=f, cutout_index=i)
            process_asset(asset, piece, meta, is_scan=True, cut_confidence=box.confidence)
    else:
        asset = create asset(kind=whole)
        process_asset(asset, img, meta, is_scan=scan.is_scan)

function process_asset(asset, img, meta, is_scan, cut_confidence=None):
    img = apply_exif_orientation(img)            # physical rotation from EXIF, then orientation=1
    rot = predict_sideways(img)                  # 0/90/180/270 + confidence
    if rot.angle != 0 and rot.confidence ≥ AUTO_ROT: img = rotate(img, rot.angle); status = auto
    elif rot.angle != 0: status = review          # cautious (fix 13)
    trim = find_border(img)                      # uniform white/black margins only
    same rule: auto if confident, review if unsure, none if no border
    if anything changed: save derived image to data\derived\<asset_id>.jpg (quality 95,
                         keep EXIF except Orientation = 1)
    hashes   = phash, dhash, image_embedding (image model)
    faces    = detect_faces(img) → keep size ≥ min_face_px → embedding + 112px crop saved
    content  = score pets, junk and every category (text descriptions + learned heads)
    store meta(is_scan, scan_confidence, ...), predictions, faces; if cut_confidence low → review

function detect_scan(img, meta) -> (bool, confidence, reason):
    if meta.make/model/software matches known scanner list (Epson, Canon Scan, HP Scan, ...): yes, 0.99
    if file is from a cut-out parent: yes
    score = visual score: large uniform background + rectangular print edges + no camera EXIF
    return score ≥ 0.9 → yes; 0.6..0.9 → yes but "Old scan?" goes to review; else no

function find_photos_on_scan(img):
    gray → blur → threshold against background colour → morphological close
    contours → keep areas > 5% of scan → minAreaRect each → reject heavy overlaps
    confidence per box from edge straightness + rectangularity
```

**Bug traps**
- **Double rotation:** after physically rotating pixels you must set EXIF Orientation to 1, or viewers rotate it again.
- Read EXIF **before** any transform (some libraries drop EXIF on rotate).
- HEIC needs the HEIF plugin registered at start-up, or every iPhone photo lands in Could Not Open.
- Border trim must not cut dark night photos: require the border to be near-uniform *and* clearly different from the photo edge.
- Cut-outs inherit "is scan" from the parent; their dates come from the date chain, never the scanner date.
- Save face crops with the asset id in the name so they survive re-runs without duplicates.

---

## 9. Compare across all files

Needs every asset analyzed first (that's why it's a separate pass).

```text
function compare_all():
    # exact duplicates: same sha256 on several file rows
    for sha with ≥ 2 file rows: make dup_group(kind=exact) of their assets
    # near duplicates: same picture resized / re-saved
    index = BK-tree (or sorted buckets) on phash
    for each asset a: candidates = index.within(a.phash, distance ≤ 6)
        confirm with image_embedding cosine ≥ 0.97 and aspect ratio within 2%
        union into dup_group(kind=near)
    burst shots and edited versions must NOT pass the near-duplicate test (thresholds above)
    for each group: kept = choose_best(members)

function choose_best(members):                 # deterministic (R11)
    sort by: user_choice first (Swap/Keep both), then pixels desc, then has exif_date,
             then file size desc, then orig_path asc
    return first

    # similar photos (for date borrowing, section 10), NOT duplicates
    similar_pairs = embedding nearest neighbours with score ≥ same_event_cutoff
```

**Bug traps**
- Comparing every pair of 3,000+ photos is 4.5 million comparisons. Use an index (BK-tree / approximate neighbours).
- A cut-out and the user's own earlier hand-crop of the same print *should* be near-duplicates; make sure the thresholds catch that case.
- The kept copy must be stable across runs unless the user swaps it.

---

## 10. Dates and holidays

```text
DEFAULT_ORDER = [exif, google, filename, similar, folder, unknown]

function resolve_date(asset):
    m = meta(asset)
    if m.is_scan: skip exif and filename-date-from-scanner-software          # scanner dates ignored
    if not m.is_scan and m.exif_date valid: return (m.exif_date, exif)       # trusted as-is (fix 3 not adopted)
    if m.google_date: return (m.google_date, google)
    if m.filename_date and not m.is_scan: return (m.filename_date, filename)
    d = borrow_from_similar(asset)
    if d: return (d.date, similar, is_guess=True, borrowed_from=d.asset)
    d = folder_clue(asset)
    if d: return (d.date, folder, is_guess=True)
    return (None, unknown)

function borrow_from_similar(asset):
    for other in similar_pairs(asset) sorted by score desc:
        if score < same_event_cutoff: break          # cutoff tuned to 95% by accuracy check
        if asset is scan and other is not scan: continue        # scans only borrow from scans
        if other's date source in (similar, folder, unknown): continue   # no chains of guesses
        return other.date
    return None

function folder_clue(asset):
    for folder in parents(orig_path) from nearest to the source root:
        year = regex \b(19[0-9]{2}|20[0-9]{2})\b, between 1900 and this year
        holiday = match holiday names/synonyms (Christmas, Xmas, Thanksgiving, July 4th, ...)
        if year: return date(year, holiday_date or July 1), holiday   # mid-year if only a year
    return None

function holiday_for(date, folder_holiday):
    if folder_holiday: return folder_holiday
    candidates = US holidays for date.year (holidays library) + custom events, each with window
    hits = [h for h in candidates if h.start ≤ date ≤ h.end]
    return hit with the smallest distance to its actual day (ties: settings order)
```

**Bug traps**
- **Chained guesses:** borrowing from a photo whose own date was borrowed spreads one mistake through a whole album. Only borrow from first-hand dates.
- A folder clue with only a year must not invent a holiday or a month in the report; show "1985 (year only)".
- Holiday windows can overlap (Christmas window vs New Year's Eve). Pick the closest one, consistently.
- Holidays that move (Thanksgiving, Easter, Memorial Day) must be computed per year, never hard-coded.
- EXIF dates have no time zone; treat them as local time and never convert.
- Regex `\b(19|20)\d{2}\b` also matches `IMG_2048` folder names; only accept numbers that stand alone as words.

---

## 11. People: grouping and matching

```text
function match_and_group_people():
    refs = reference set per person: embeddings of user-labelled faces,
           best samples weighted ×3, recent locked answers first
    for face in faces where not locked:
        best, second = top two people by max cosine(face.embedding, refs[p])
        if best.score ≥ match_cutoff and best.score - second.score ≥ margin:
            face.person = best; match_source = model
        elif face matches a stranger reference: face.stranger = that stranger
        else: face.person = None (unknown)
    # group the unknown faces for "Who is this?"
    clusters = cluster(unknown face embeddings, cosine, min_size = 3)
    order clusters by size desc; take settings.people_per_round (20)
    for each cluster: pick 6 sample faces = most central + most varied

function name_cluster(cluster, typed_name):           # "Who is this?" Save
    name = proper_case(typed_name)
    if name close to an existing name (edit distance ≤ 2) and not identical:
        ask "Did you mean Calvin?" (Yes / No, new person)
    person = get_or_create(name)          # case-insensitive match
    for face in cluster: record answer(face → person, source=user, locked=True)

function mark_stranger(cluster): create stranger "Stranger NNN"; faces → stranger; locked

function proper_case(s):
    collapse spaces; for each word: capitalise first letter, lowercase rest
    then fix: "Mc"+X → McX, "O'"+x → O'X, "Mac" only if in known list, hyphens per part
```

**Bug traps**
- Re-clustering every run gives new cluster numbers. Never store answers by cluster number; store them per **face**.
- A face the user locked to Calvin must never be re-matched to someone else (R6).
- Very small faces (background people) produce junk matches; filter by `min_face_px` before matching.
- Merging two people must move every face, best sample and rating, then mark the old person `merged_into`.

---

## 12. Learning from answers

```text
function ingest_answers():
    collect answers recorded in the app since last run
    collect hand-made answers: files the user dragged into answer folders inside
        _Needs Review / _Accuracy Check (section 23) → convert to answers
    apply each: update faces.person / predictions / fixes / dup choices; locked = True

function learn_from_answers():
    for each check (pet, junk, scan, each category, rotation):
        examples = answers for that check (best samples weighted ×3)
        if count ≥ 10: train a small logistic-regression "head" on image_embedding
        final score = blend(description score, head score), head weight grows with count
    labels_since_save += new answers
    if labels_since_save ≥ save_every_labels (25): checkpoint_model_state()

function checkpoint_model_state():
    atomic_write(data\models\heads_<n>.bin); insert model_state; keep last 10
```

**Bug traps**
- Training a head on 3 examples overfits badly. Use the description-only score until there are at least 10 answers.
- Each check needs both "yes" and "no" examples; with only "yes" answers the head learns nothing useful.
- Claude's answers count as training data but should be weighted lower than the user's.

---

## 13. Claude second opinion (optional)

```text
function claude_second_opinion():
    if not settings.claude.enabled or no key in Credential Manager: return
    items = unsure predictions (pet, junk, categories, scan, holiday look, rotation, trim,
            cut-out quality, decade of old scans); never "who is this person"
    for item in items (sorted: most valuable first):
        if month_spend + estimate(item) > monthly_limit:
            choice = show limit popup (Continue free / Raise by $5 / Pause), default after 5 min = Continue free
            if choice == continue_free: break
            if choice == raise: monthly_limit += 5; save settings
            if choice == pause: pause run (resumable)
        img = shrink(item image, longest side 512, JPEG 85)
        response = Anthropic SDK messages call (model from settings) with the image +
                   a fixed question, structured output (value, confidence)
        handle refusal / API errors per SDK docs; on rate limit: wait and retry (max 3)
        record usage → claude_usage; record answer(source=claude) if confidence ≥ cutoff
        commit
```

**Bug traps**
- Check the budget **before** each request, not after.
- Spending is per calendar month; reset the counter on the 1st.
- Never send full-size photos, file paths or names; only the shrunk image and the question.
- If the internet is down, skip Claude for this run instead of failing the run.
- Exact SDK calls must come from the current Anthropic SDK docs at build time, not memory.

---

## 14. Review queues

```text
function build_review_queues():
    queue order = [who_is_this, needs_review, accuracy_check, duplicates, junk]
    who_is_this   = clusters from section 11 (up to 20)
    needs_review  = items with status review: rotation, trim, cut-outs, pet, categories, scan
    duplicates    = dup groups not yet decided
    junk          = assets predicted junk, not yet decided
    also refresh the special folders for transparency:
        _Needs Review\<check>\..., _Strangers\Stranger NNN\ (face crops),
        each with READ ME.txt

function start_review():  walk queues in order; Esc saves position; Enter on Home resumes

function record_answer(item, value):
    insert answers(..., locked=True); push onto undo stack; update counts on Home
```

**Bug traps**
- Answers must be saved the instant they're given (not when the screen closes), or a crash loses them.
- Skipped items stay in the queue for next time; they are not answers.

---

## 15. Accuracy check

```text
function build_accuracy_sample():
    budget = settings.start_items on first round, else sum of per-check budgets
    for check in checks (each person, pet, each category, holiday, scan, cut-out, rotation,
                         trim, same_event, claude):
        r = latest ratings(check)
        if r unknown or r.accuracy < target: n = larger share
        elif r ≥ target for rounds_to_shrink rounds: n = min_per_check (3)
        if r dropped since last round: n = larger share
    pick n random CONFIDENT predictions per check (never locked items; never items in review)
    for each: qa_items(blind_name = random 6-char id, hidden_prediction = current prediction)
    write files to _Accuracy Check\<question>\ with blind names + answer subfolders

function score_accuracy_round():
    for qa in qa_items where answer is set and scored_run is null:
        correct = (qa.answer == qa.hidden_prediction)
        if not correct: record answer as a label (fixes the model)
        add to ratings(check, run_id)
    shown on scorecard + report
```

**Bug traps**
- The blind name must not leak the prediction (no person names, no category in the file name or EXIF comment).
- Score against `hidden_prediction` saved at sampling time, never against today's prediction.
- Items the user skipped are not wrong answers; leave them out of the rating.

---

## 16. Plan destinations

```text
function build_plan():
    for asset in assets (not could_not_open):
        if asset in dup_group and not kept and no "keep both":
            add(asset, "_Duplicates/<group_id>/<name>", primary); continue
        if asset is junk (predicted or answered) and not "not junk":
            add(asset, "_Junk/<name>", primary); continue
        root = "Old Family Photos/" if asset.is_scan else ""
        year = date.year or ("Unknown Year" if is_scan else "Unknown Date")
        dests = []
        if video: dests += "<root><year>/Videos"
        else:
            if holiday: dests += "<root><year>/Holidays/<Holiday>"
            for person in distinct people in asset's faces: dests += "<root><year>/People/<Name>"
            if pet: dests += "<root><year>/Pets"
            for category matched (not junk): dests += "<root><year>/<Category folder>"
            if dests empty: dests += "<root><year>/Other"
        first dest = primary (gets the moved original at Finish), others = extra_copy
        name = asset file name (cut-outs: <scan name>_<a,b,c>.jpg)
    resolve name clashes per folder deterministically (R11):
        sort assets in the folder by (original name, sha256); first keeps name, others get _2, _3 ...
    for every multi-photo scan parent: add "Old Family Photos/Original Scans/<name>" (extra_copy)
    for could_not_open files: "_Could Not Open/<name>" + reasons.txt
    diff = compare plan with owned_files → to_add, to_update, to_remove
```

**Bug traps**
- Clash suffixes must depend only on content (name + fingerprint), never on scan order, or `_2` files swap on every run.
- Junk and extra duplicates are exclusive; they must not also appear in year folders.
- Names that are illegal on Windows (`CON`, trailing dots, `:`) must be cleaned.
- A person merged into another must plan under the surviving name only.

---

## 17. Report

```text
function report_data():
    totals: sorted, duplicates, junk, need review, couldn't open, videos
    tree: folder → count → thumbnails (lazy-loaded)
    changes since last run: from diff (to_add/to_update/to_remove grouped by reason)
    scorecard: latest ratings per check with trend arrows
    needs attention: review counts, borrowed-date guesses, could-not-open, unreachable paths,
                     low ratings, missing sources — each with a button to the right screen
    corrections in the report: Move (M) / Remove from folder (Del) → recorded as answers (locked)
```

**Bug traps**
- Loading thousands of thumbnails at once freezes the window. Load thumbnails lazily and off the UI thread.

---

## 18. Sync the library

Copies only (fix 2); the library mirrors the plan (fix 1c).

```text
function sync_library(diff):
    for item in diff.to_add + diff.to_update:
        e = journal("sync_write", {dest})
        src = derived image if the asset was fixed/cut, else original file
        atomic_copy(src, library/dest, expected_sha = sha of src)
        upsert owned_files(dest, asset, sha); journal_done(e)
    for item in diff.to_remove:                  # only files we own (R5)
        if sha256(library/item) != owned_files.sha: leave it, flag as "edited by you" (section 23)
        else: delete; remove owned_files row
    delete library folders that became empty AND contain no non-owned files
    write READ ME.txt into each special folder (once)

function verify_every_source_accounted_for():
    for every file row (not missing): has a plan row, a could_not_open row, or is a parent
        scan with cut-outs planned → else list it in the report as "not accounted for"
```

**Bug traps**
- Never delete a library file the user edited (fingerprint changed); report it instead.
- Copy the **derived** file for fixed photos, the original otherwise; don't mix them up.
- Free-space check before copying (sum of to_add sizes).

---

## 19. Finish

```text
function finish():
    preconditions: last run approved and synced; every source sha in a VERIFIED backup (R2);
                   user typed FINISH
    for asset group by source file f:
        primary = plan row with role primary for f's asset(s)
        e = journal("finish_move", {src: f.orig_path, dest: primary.dest, sha: f.sha256,
                                    derived: asset was fixed or cut})
        if not derived:
            if library/primary.dest exists with same sha: delete src (the copy is already there)
            else: move src → library/primary.dest (same drive: rename; else copy+verify+delete)
        else:
            # library already holds the fixed version; the original lives in the backup
            confirm f.sha256 in verified backup_entries; delete src
        journal_done(e)
    remove empty folders under each master folder, deepest first, only if completely empty
    mark sources as finished; keep _Add New Photos for later

function finish.recover(entry):       # crash mid-finish
    if src missing and dest exists with sha: journal_done
    elif src exists and dest missing: redo the move
    elif both exist with same sha: delete src; journal_done
    else: stop and tell the user (never guess)
```

**Bug traps**
- Moving across drives is copy + verify + delete, never a plain rename.
- Never delete a source without re-checking its fingerprint against the verified backup at that moment.
- "Completely empty" means no files at all, including hidden ones; folders with videos or documents stay.

---

## 20. Restore

```text
function restore_everything(backup_date):
    for entry in backup_entries up to backup_date:
        dest = files.orig_path for that sha (latest known)
        if dest exists with the same sha: skip
        if dest exists with a different sha: ask (Keep both / Overwrite / Skip)
        extract to dest.partial; verify sha; rename                       # R3
function restore_one(query):    search orig_path / name → same as above for chosen items
function find_original(library_file): owned_files → asset → file → orig_path + backup part
```

**Bug traps**
- Recreate missing parent folders with long paths.
- Never overwrite silently.

---

## 21. Google Photos import

```text
function google_import(zip_paths):
    import_id = new
    for z in zip_paths: atomic_copy(z, backup\Google Takeout\<import_id>\<name>, sha of z); verify
    record these zips as backup parts (verified) so R2 holds for their contents
    extract media to data\imports\<import_id>\ (long paths; skip .json except sidecars)
    for each media file: find its sidecar JSON (Takeout truncates names; match by
        "<name>.json", "<name>.supplemental-metadata.json", truncated forms, "(1)" suffix rules)
        meta.google_date = photoTakenTime.timestamp (UTC → local)
    register data\imports\<import_id> as a source (kind=google_import); next run discovers it
```

**Bug traps**
- Takeout sidecar file names are inconsistent (truncated at ~46 characters, `(1)` moved before `.json`). Test the matching on real exports.
- The same photo appears in several album folders inside Takeout; exact-duplicate detection handles it.
- `-edited` versions are separate photos (edited versions are kept).

---

## 22. Learned-data backup

```text
function backup_learned_data():
    bundle = catalog.db (SQLite online backup API) + data\models\* + face crops index
    atomic_write(backup\Learned Data\learned_<date>_<run>.zip, bundle)
    keep newest 10; delete older ones
function learned_data_check():
    if catalog missing or fails integrity_check: offer restore from newest learned bundle
```

**Bug traps**
- Copying `catalog.db` with a plain file copy while it's open can produce a corrupt copy. Use SQLite's backup API.

---

## 23. Hand-edit detection

```text
function detect_hand_edits():          # start of every run
    for f in owned_files: if missing → "you removed X" ; if sha changed → "you edited X"
    for files in library not in owned_files and not in _Add New Photos → "unknown file"
    answer folders (_Needs Review/<check>/<answer>/, _Accuracy Check/<q>/<answer>/):
        files moved by hand into an answer folder → convert to answers
    show a summary in the report; never "fix" the user's changes automatically
```

---

## 24. UI, hotkeys and undo

```text
Screens: Setup (3 steps), Home, Running, Resume prompt, Who is this?, Needs review,
         Accuracy check, Duplicates, Junk, Report, Finish, Settings (tabs), Connect Claude,
         Spending limit, Add Google Photos, Restore, F1 hotkeys
Home.next_step():  first matching of:
    no settings → Setup;  crashed run → Resume;  never run → Start first run;
    review items waiting → Start review;  answers since last run → Run again;
    all ratings ≥ target and nothing waiting → Finish;  else → Run
Long work runs on a worker thread; the UI thread only draws (never blocks).
Worker → UI messages through a queue polled every 100 ms (Tk is not thread-safe).
Hotkeys: one table per screen loaded from settings; shown on buttons; F1 lists all.
Undo: stack of (action, inverse) since the last run; Ctrl+Z pops and applies the inverse.
```

**Bug traps**
- Touching Tk widgets from a worker thread crashes randomly. Only the UI thread may update widgets.
- Hotkeys must not fire while typing in a text box (e.g. "S" in the name box must type an S, not mark Stranger).

---

## 25. Errors and logging

```text
log file: data\logs\imagecleanuper_<date>.log, rotate daily, keep 30 days
every error shown to the user = plain-English message + what to do + "Copy details" button
never show a raw stack trace in the UI; always write it to the log
per-file errors never stop the run (go to Could Not Open / report); run-level errors pause safely
```

---

## 26. Test checklist

Each item becomes an automated test (sample photos are generated in the test suite).

| # | Test | Section |
|---|---|---|
| T1 | A file nested 300+ characters deep is found, backed up and restored | 0, 6, 7, 20 |
| T2 | Killing the app mid-backup leaves no verified part and resumes cleanly | 7, 4 |
| T3 | Killing the app mid-analysis redoes at most one file | 8 |
| T4 | Killing the app mid-Finish: every file ends up in exactly one place | 19 |
| T5 | A source file is never modified before Finish (fingerprints before = after) | 0 |
| T6 | A rotated photo has EXIF Orientation = 1 and looks upright in Windows Photos | 8 |
| T7 | Two master folders with the same `IMG_0001.jpg` → both kept, stable `_2` suffix across runs | 7, 16 |
| T8 | Same photo resized/re-saved → near duplicate; burst shots → not duplicates | 9 |
| T9 | Scan with 3 prints → 3 cut-outs, straightened, no scanner date used | 8, 10 |
| T10 | Undated scan beside a dated phone photo of the same print does not borrow the phone date | 10 |
| T11 | No chained borrowed dates | 10 |
| T12 | Thanksgiving computed correctly for 1985, 2019, 2026 | 10 |
| T13 | "calvin", "CALVIN", "Calvin" → one person; "mcdonald" → "McDonald" | 11 |
| T14 | A locked answer survives re-runs and model changes | 11, 12 |
| T15 | Re-run after labelling moves the photo from Other to People/Calvin and removes the stale copy | 16, 18 |
| T16 | A library file edited by the user is never deleted by sync | 18, 23 |
| T17 | Accuracy blind names leak nothing; scoring uses the saved prediction | 15 |
| T18 | Claude budget is checked before each request; popup default continues free after 5 min | 13 |
| T19 | Library or backup inside a master folder is refused | 3 |
| T20 | Takeout sidecar JSON matched for truncated and "(1)" names | 21 |
| T21 | HEIC, PNG, WEBP, TIFF open; a corrupt JPEG goes to Could Not Open with a reason | 8 |
| T22 | Hotkey "S" types in the name box instead of marking Stranger | 24 |

## Change log

1. 2026-11-14 · v1: First full pseudocode for version 1, matching flowchart v18 and wireframes v3.
