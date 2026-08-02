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
5. **My Drive mode (current setup — personal Gmail, Shared Drives are Workspace-only).**
   The bank is a My Drive folder shared directly with the service account as **Editor**;
   `GOOGLE_SHARED_DRIVE_ID` stays empty. Caveat: files HERMES uploads into `_generated/`
   are owned by the service account and count against the service account's OWN Drive
   quota — if `hermes doctor`'s write probe ever fails on quota, delete old
   service-account-owned renders or move the bank to a Workspace Shared Drive (then set
   `GOOGLE_SHARED_DRIVE_ID`; the client switches modes automatically).

## The rhythm

```
hermes wishlist show   →   shoot & file into exactly the named folders (daily uploads)
hermes assets sync     →   new files indexed and tagged from their paths
hermes wishlist match  →   parked posts that can now be serviced return to APPROVED
```

The wishlist is written in folder-path vocabulary on purpose: there is never a translation
step between what the agent asks for and where you put the file.

## Fonts (also operator-committed, in the repo)

See `assets/fonts/README.md` — the three families (Bricolage Grotesque Bold, Hanken
Grotesk Regular + SemiBold, Space Mono Regular) must be committed as **static** .ttf
files; variable fonts render the wrong weight in Pillow.
