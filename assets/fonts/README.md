# Brand fonts — operator-committed (all SIL Open Font License)

Commit the **STATIC** .ttf instances here, with exactly these filenames (they are referenced
by name in the `config.fonts` map):

| File | Family | Role |
|---|---|---|
| `BricolageGrotesque-Bold.ttf` | Bricolage Grotesque Bold | display / headlines |
| `HankenGrotesk-Regular.ttf` | Hanken Grotesk Regular | body |
| `HankenGrotesk-SemiBold.ttf` | Hanken Grotesk SemiBold | body emphasis |
| `SpaceMono-Regular.ttf` | Space Mono Regular | labels / eyebrows |

**Do NOT commit the variable-font files.** Pillow's FreeType binding renders variable fonts
at their default instance and silently ignores weight axes — a variable Bricolage Grotesque
renders Regular where Bold was asked. Download the static weights from Google Fonts
("Download family" → `static/` folder inside the zip).

`hermes doctor` is RED and TEXT CARD raises a named error until these four files exist.
