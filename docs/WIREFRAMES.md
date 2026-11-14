# ImageCleanuper Wireframes

What the app looks like from first launch to Finish. These are wireframes (layout and wording), not final visuals. See the [process flowchart](FLOWCHART.md) for what happens behind each screen.

## Screen flow

```mermaid
flowchart LR
  A["First launch<br/>download AI files"] --> B["Setup 1<br/>photo folders"] --> C["Setup 2<br/>library + backup"] --> D["Setup 3<br/>ready"]
  D --> R["Running"]
  R --> W["Who is this?<br/>first run only"]
  W --> P["Report"]
  R --> P
  P -- "Approve" --> H["Home"]
  P -- "Not yet" --> H
  H -- "Start review" --> V["Review queue<br/>Who is this? → Needs review →<br/>Accuracy check → Duplicates → Junk"]
  V -- "done or Esc" --> H
  H -- "Run" --> R
  H -- "Finish" --> F["Finish"]
  H -.-> S["Settings<br/>+ Connect Claude"]
  H -.-> X["Restore"]
  K["Crash or restart"] -.-> Q["Pick up where<br/>you left off?"] -.-> R
```

**The rule that keeps it simple:** the Home screen always shows **one Next step** with one big button. You can always just press **Enter** and the app takes you to the right place.

## Walkthrough

### 1. First launch
The installer is small; the AI files download once, with progress and automatic retry.

![First launch](wireframes/01-first-launch.svg)

### 2. Setup: where are your photos?
Add top folders with a folder picker. No typing paths. Optionally mark a folder of old scans.

![Setup step 1](wireframes/02-setup-folders.svg)

### 3. Setup: where should things go?
Library and backup locations, each checked on the spot (free space, not inside a photo folder).

![Setup step 2](wireframes/03-setup-locations.svg)

### 4. Setup: ready
Explains the four things that happen next, and how long it will take, before anything starts.

![Setup step 3](wireframes/04-setup-ready.svg)

### 5. Running
One bar per stage, time remaining, and the photo being worked on. Pause any time.

![Running](wireframes/06-run.svg)

### 6. If the PC crashes or restarts
The app offers to continue from the last photo. Nothing is lost.

![Resume](wireframes/07-resume.svg)

### 7. Who is this? (first run, then whenever new faces appear)
One person at a time. Type a name (suggestions appear as you type) or press Stranger.

![Who is this](wireframes/08-who-is-this.svg)

### 8. Report
Totals, a folder tree with thumbnails, a "Needs attention" list with buttons, and Approve and sort.

![Report](wireframes/12-report.svg)

### 9. Home
The Next step, the four main actions, what's waiting for you, and the accuracy scorecard.

![Home](wireframes/05-home.svg)

### 10. Review queue
**Start review** walks through everything waiting, in this order, so you never have to choose: Who is this? → Needs review → Accuracy check → Duplicates → Junk. Press Esc any time; progress is saved.

![Needs review](wireframes/09-needs-review.svg)

![Accuracy check](wireframes/10-accuracy-check.svg)

![Duplicates](wireframes/11-duplicates.svg)

Junk uses the same side-by-side layout as Duplicates, with **Delete (D)** and **Not junk (K)**.

### 11. Finish
A checklist (backup verified, accuracy), exactly what will happen, and typing FINISH to confirm.

![Finish](wireframes/13-finish.svg)

### 12. Settings and Connect Claude
Tabs for Folders, Holidays & categories, Accuracy, Hotkeys and Claude. Turning Claude on opens the API key popup.

![Settings and Claude](wireframes/14-settings-claude.svg)

### 13. Restore
Restore everything, one photo or folder, or find the untouched original of any photo.

![Restore](wireframes/15-restore.svg)

## Friendliness check

What makes it easy to follow from start to finish:

- **One next step, always.** Home tells you what to do in plain words, with one big button. Enter always does the obvious thing.
- **One review button.** Every question the app has goes into a single queue. There are no separate places to remember.
- **Nothing happens by surprise.** The backup comes first, the report before sorting, and Finish needs you to type FINISH.
- **No technical words on screen.** "Accuracy check" instead of QA, "fingerprint" instead of hash, and no file paths to type.
- **You can stop any time.** Esc closes any screen with progress saved; crashes resume.
- **Hotkeys everywhere,** shown on each button and in the bar at the bottom. F1 lists them all.
- **Everything is undoable** with Ctrl+Z, and deleted files go to the Recycle Bin.

## Change log

1. 2026-11-14 · v1: First wireframe set (13 screens) and screen flow.
