# ImageCleanuper: design

A photo library you **search, filter and sort by tags** instead of browsing folders.
Photos stay where they are on disk. The app keeps a catalog (tags + thumbnails) on top of them.
AI suggests most of the tags; you only answer what it isn't sure about.

Clickable mockup: [`library-mockup.html`](library-mockup.html)

## Design guide (UI conventions)

Rules agreed while reviewing the mockup. New screens follow these.

### Buttons and actions
- **Adding something uses a small `+` icon button** on the header row it belongs to: `+` on a category header adds a tag, `+` on the Categories header adds a category. No wordy "+ Add …" buttons in panels or lists. Every icon button has a tooltip and screen-reader label ("Add a tag to People").
- An icon button whose inline box is open shows the accent colour until it closes.
- Inline add boxes open right under their header. Enter saves, Esc cancels, and the box gets focus straight away.
- Full text buttons are only for the main page actions ("+ Add folder", "Start tagging", "Done").

### Lists of tags
- Tags are **A–Z by default**. Each category has its own sort toggle on its header (A–Z → Z–A → Most photos). Year defaults to newest first.
- **Favorites:** a ☆ appears on hover (always visible on touch screens). A starred tag moves to the top of its category, with a thin line under the favorites.
- **The list never reshuffles while filtering.** Tags keep their place. Counts update, and tags with no matching photos are greyed out instead of removed.
- **Categories collapse** from their header. A collapsed header shows its tag count; open headers show no count (selected tags are already visible as chips in the search box).
- **Categories reorder by dragging** the ⠿ handle (or with the arrow keys). While dragging, a floating copy with a shadow follows the pointer and a dashed outline shows where it will drop.

### Layout and scrolling
- The header and search bar stay fixed. The category column and the photo grid each scroll on their own; scrolling one never moves the other.
- Each category's tag list has its own scrollbar after about 7 tags, and the scroll position is kept when the list redraws.
- The category column is resizable with a drag handle between it and the grid (200–520 px, double-click to reset). Widths, order, sort, collapsed state and favorites are remembered.
- **Scrollbars never cover content.** Every scrolling area reserves its own lane for the scrollbar plus a small gap, so photos, counts and stars are never under it (this matters on Windows 11, where scrollbars float over content by default). Scrollbars are thin and themed.
- Headers in a column line up: name on the left, small controls (sort, `+`) on the right, in the same positions on every row.

### Photo details
- Opening a photo docks its details as a column on the right. **The grid stays usable**: click any other photo to switch to it in one click, with no dimmed background and nothing to close first. The open photo is outlined in the grid.
- Prev / Next buttons and the left/right arrow keys step through the current results. Esc or Close hides the panel. On a phone the panel fills the screen.

### Search
- Selected tags appear as coloured chips inside the search box, in front of the typed text. Remove a chip with its × or Backspace.
- An **×** at the right end of the search box clears all chips and text (Esc does the same). It only shows when there is something to clear.
- Commas and spaces both separate search words.

## Workflow

1. **Add folder.** Pick a folder and choose which AI taggers to run. Nothing is moved or copied.
2. **Auto-tag.** The AI pipeline runs locally in the background (see below). The library is usable while it runs.
3. **Answer a few questions.** These are grouped so one answer covers many photos: "Who is this? (46 photos)", "Name this event? (212 photos)", "Hide 38 screenshots?"
4. **Find.** Use the search box, tag filters (People / Places / Events / Things / Year) and sort (newest, oldest, recently added, favorites).
5. **Fix as you go.** Open a photo to add or remove tags and to accept or dismiss suggestions. Select several photos to tag them all at once. Your edits are locked and the AI never overrides them.

New photos added to a watched folder are picked up and tagged automatically.

## AI tagging pipeline (the input side)

Each stage writes tags with a **source** (`ai`, `user`, `file`) and a **confidence**.
High-confidence tags are applied. Medium-confidence tags show as *Suggested*. Low-confidence tags are dropped.

| Stage | Produces tags like | How (local first) |
|---|---|---|
| Date & time | `2024`, `July`, `Morning` | EXIF date, then file name, then file dates, then neighbours in the same burst/event |
| Places | `Cape Cod`, `Home` | EXIF GPS → offline reverse geocoding; you can name places like "Home" |
| Duplicates | `Duplicate`, `Burst` | Perceptual hash (exact + near) |
| People | `Emma`, `Mom` | Face detection + face embeddings → clustering; you name each cluster once |
| Pets | `Biscuit` | Animal detection + embedding clusters, named like people |
| Events | `Beach trip 2024`, `Christmas` | Cluster by time gaps + location; holiday calendar for names; you confirm or rename |
| Things & scenes | `Beach`, `Cake`, `Sunset`, `Snow` | CLIP-style image model with a tag vocabulary (also powers free-text search like "kids in the snow") |
| Junk & quality | `Screenshot`, `Blurry`, `Receipt` | Model scores + simple sharpness/brightness checks |
| Text in photos *(optional)* | words found in the image | OCR |
| Old scans *(optional)* | `Old photo`, `1987` | Detect scans, fix rotation, split multi-photo scans, estimate decade |
| Claude second opinion *(optional, off by default)* | any of the above | Only photos the local models are unsure about, sent as small JPEGs, with a spending cap |

### More options to consider later
- **Natural-language search** ("Leo at the lake last summer"): this comes almost free from the scene model's embeddings.
- **Smart albums.** Saved searches that update themselves ("Emma + Birthday").
- **Best shot picker.** In a burst or duplicate group, pick the sharpest photo with eyes open.
- **Write tags back into files** (XMP/IPTC keywords) so other apps see them too.
- **Family tree-style names.** Link "Mom" to a person, with an age estimate for date guessing on old scans.
- **Colour / mood tags** and **video** support (keyframe tagging).

## Open decisions
- Platform: Windows desktop app (like before), or a local app used in the browser.
- Should "Add folder" also offer to copy photos into one library folder, or always leave them in place?
- Which AI taggers are on by default (the mockup turns on everything except OCR and old scans).
