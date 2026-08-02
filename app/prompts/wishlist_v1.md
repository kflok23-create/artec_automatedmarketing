A artec post could not be rendered — no bank asset or tool chain hits the match. Write the
asset wishlist that tells the human exactly what to shoot and where to file it.

Post genome:
$genome
Needed media kind: $media_kind · aspect: $aspect

Valid target_folder values (the bank's own vocabulary — the folder IS the tag):
raw-photo | raw-photo/assembled | raw-photo/parent-child | raw-photo/child-face |
raw-video | raw-video/assembled | raw-video/parent-child | raw-video/child-face |
classroom | lesson-books | lesson-pdfs | UGC

Reply with ONLY a JSON array of 1–3 entries:
[
  {
    "target_folder": "raw-video/child-face",
    "medium": "video",
    "aspect": "vertical",
    "duration_s": "8-15",
    "description": "child snapping two blocks together, hands and face visible, natural light"
  }
]
Descriptions must be shootable instructions (subject, action, framing, light) — not vibes.
