# The Asset Bank — Google Drive "Artec Assets Bank"

Layer 0 of the architecture: human-curated, machine-consumed. **The folder path IS the tag
schema** — file a photo correctly once and HERMES knows what it depicts forever. There is
no separate tagging system by design.

## The folder tree (build exactly this, in a Shared Drive)

```
Artec Assets Bank/                     ← DRIVE_ROOT_FOLDER_ID
├── UGC/
├── classroom/
├── raw-photo/
│   ├── parent-child/
│   ├── child-face/
│   └── assembled/
├── raw-video/
│   ├── parent-child/
│   ├── child-face/
│   └── assembled/
├── lesson-pdfs/
├── lesson-books/
└── _generated/                        ← created for HERMES; only IT writes here
```

## Path → tag derivation (mirrored in `app/taxonomy.py`, unit-tested)

| Path | Medium | Subject | Person in frame |
|---|---|---|---|
| `raw-photo/` (root) | photo | loose_blocks | hands only |
| `raw-photo/assembled/` | photo | assembled_blocks | hands only |
| `raw-photo/parent-child/` | photo | parent_child | yes — faces |
| `raw-photo/child-face/` | photo | child_face | yes — faces |
| `raw-video/` (root) | video | loose_blocks | hands only |
| `raw-video/assembled/` | video | assembled_blocks | hands only |
| `raw-video/parent-child/` | video | parent_child | yes — faces |
| `raw-video/child-face/` | video | child_face | yes — faces |
| `classroom/` | photo | classroom | yes — faces |
| `lesson-books/` | photo | lesson_book | no |
| `lesson-pdfs/` | pdf | lesson_pdf | no |
| `UGC/` | mixed | ugc | unknown |

An unrecognised path imports as `subject='unknown'` with a warning — never a guess.

## The rules that make it work

1. **NEVER rename or restructure the Layer 1 / Layer 2 folders.** Tags derive from exact
   path strings; renaming `child-face` to `child_face` silently breaks matching forever.
2. **Use Drive's per-file Description field** for anything the folder can't express —
   colour, orientation, "block set #3", "shot on white". Sync reads it into a searchable
   column and the toolbox router sees it when matching ideas to assets.
3. **Faces are gated.** Anything under `parent-child/`, `child-face/`, or `classroom/`
   carries `has_person=true` and is excluded from selection until the config flag
   `allow_person_assets` is flipped to `true` (once model releases are settled).
4. **`_generated/` belongs to HERMES.** Every render is uploaded to
   `_generated/{week_start}/{post_id}.{ext}`; don't file your own media there.
5. **Shared Drive on the techup.my Workspace (current setup).** The service account is a
   **Content Manager**; both `GOOGLE_SHARED_DRIVE_ID` and `GOOGLE_DRIVE_ROOT_FOLDER_ID`
   are set (they are different ids — drive vs folder). Why not My Drive: service accounts
   have ZERO Drive quota of their own, so every HERMES upload into a My Drive folder
   fails on storageQuota no matter how the folder is shared. (The My Drive code path
   still exists for reads — leave `GOOGLE_SHARED_DRIVE_ID` empty — but it cannot host
   `_generated/`.) Also required: the service account's GCP project must have the
   **Drive API explicitly enabled**; fresh projects don't. If the bank ever migrates
   again, just update the two env vars — `artec assets sync` detects the root change and
   forces a full rescan automatically.

## The rhythm

```
artec wishlist show   →   shoot & file into exactly the named folders (daily uploads)
artec assets sync     →   new files indexed and tagged from their paths
artec wishlist match  →   parked posts that can now be serviced return to APPROVED
```

The wishlist is written in folder-path vocabulary on purpose: there is never a translation
step between what the agent asks for and where you put the file.

## Fonts (also operator-committed, in the repo)

See `app/assets/fonts/README.md` — the three families (Bricolage Grotesque Bold, Hanken
Grotesk Regular + SemiBold, Space Mono Regular) must be committed as **static** .ttf
files; variable fonts render the wrong weight in Pillow. They live INSIDE the `app`
package so the wheel ships them — `pip install .` deployments resolve fonts from
site-packages, not the source tree.
