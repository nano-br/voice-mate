# VoiceMate brand: the mark, the tray glyph and the app icon

VoiceMate turns your voice into text ready to paste. Its icon has two jobs: be
recognizable as VoiceMate (Start menu, taskbar, installer, windows) and, in the tray,
tell at a glance what the companion is doing, the way the Windows microphone-in-use
indicator does. It is not a microphone: a microphone says "audio device", and VoiceMate
is a companion, not a device.

## The mark: a companion made of your voice

One continuous, thick sound-wave stroke with round caps and round joins, and one dot:

- **The arms:** the two outer ends of the wave, raised.
- **The body:** the deep V in the middle of the wave, which goes lower than the arms.
  The body is your voice.
- **The head:** a solid coral dot above the V, between the shoulders, with a clear gap.
  It is the companion paying attention, and it stays the same coral in every state.

Read together: a person with raised arms whose body is your voice wave, an assistant
glad to help. The concept drawing it comes from is the reference; the code is the
source of truth: `app/companion/ui/icons.py` draws the mark, the tray glyphs and the app
icon, and `tools/gen_icon.py` (`python -m tools.gen_icon`) writes the packaged
`app/companion/assets/voicemate.ico` and `voicemate-<size>.png` from the same code.
`tests/companion/ui/test_ui_icons.py` fails when the PNGs or the `.ico` entries are older
than the drawing.

### Geometry

The mark's own units (from the concept drawing: 1024 px canvas divided by 32; y grows
downwards):

| Part | Value |
|---|---|
| Wave | (7, 17.3) left arm, (10.8, 10.3) left shoulder, (16, 21.9) body, (21.2, 10.3) right shoulder, (25, 17.3) right arm |
| Stroke | 3, round caps and joins |
| Head | centre (16, 7.9), radius 1.95 |

- Every size is painted on its own, never downscaled from a big bitmap. Below 48 px the
  stroke is a whole number of pixels, the caps and the head land on the pixel grid and
  the figure stays symmetric about its axis; the head is never narrower than the stroke.
- At 16, 20 and 24 px (the tray at 100, 125 and 150 %) the figure is hand-tuned on its
  own pixel grid wherever the automatic snap loses it: a 2 px stroke, the head raised
  above the shoulders with a 1 px gap, the body's lowest point well below the arms.

### Colors

| Role | Hex |
|---|---|
| Head (coral) | `#FB7185` |
| Mark on the app icon | `#FFFFFF` |
| App tile frame | `#4F6EF7` (top right) to `#A24FE0` (bottom left) |
| App tile inner square | `#2A0FA0` (top) to `#3B12B5` (bottom) |
| Tray glyph on a dark taskbar | `#FFFFFF` (stopped: `#9A9A9A`) |
| Tray glyph on a light taskbar | `#111111` (stopped: `#6B6B6B`) |

## The tray glyph: the mark plus a state badge

The tray shows the mark alone, with no tile and no background, in the taskbar's
foreground color: white on a dark taskbar, near-black on a light one (the tray follows
`SystemUsesLightTheme` and redraws when it changes). The head stays coral. The STATE is
told only by a small badge in the bottom-right corner; with a badge the mark gets smaller
and moves to the top-left corner so the badge never cuts its right arm, and a transparent
ring separates the badge from the mark.

| `TrayState` | Badge |
|---|---|
| `idle` | none: the full-size mark |
| `stopped` | grey octagon with a white bar; the mark and its head are dimmed to grey |
| `starting` | grey disc with clock hands |
| `restarting` | grey circular arrow (a ring with a gap, no disc) |
| `recording` | small solid red dot (the REC light) |
| `transcribing` | amber disc with a dark hourglass |
| `thinking` | violet speech bubble with three dots |
| `speaking` | violet disc with a white speaker |
| `ready` (3 s) | green disc with a white check |
| `warning` | yellow triangle with a dark "!" |
| `error` | red rounded square with a white X |

Badges differ by SHAPE (disc, small dot, octagon, triangle, rounded square, speech
bubble, open ring) and by their symbol, never only by color. No animation by default.

## The app icon: the framed tile

The exe, the Start menu shortcut, the installer and the windows show the mark in white
with its coral head on a framed tile, like the concept drawing: an outer rounded square
with a blue-to-magenta gradient, around a deep-indigo inner square.

| | 32-unit grid (32 px and up) | 24 px | 20 px | 16 px |
|---|---|---|---|---|
| Margin | 0.5 | 1 | 0 | 0 |
| Outer corner | 7 (22 %) | 5 | 4.5 | 3.5 |
| Frame | 3 | 2 | 1 | 1 |
| Inner corner | 5 | 3.5 | 3.5 | 2.5 |

The mark fills about 86 % of the inner square's width. At 16 and 20 px the frame is a
one-pixel bright edge around the dark square.

## Do and don't

- **Do** keep the body's V lower than the arms and the head above the shoulders, with a
  gap: that is what makes it a figure and not a plain wave.
- **Do** tell a tray state by the badge's shape first. A new state gets a new silhouette
  or symbol, and `tests/companion/ui/test_ui_icons.py` checks every pair at 16, 20, 24 and
  32 px with colors removed (lightness and coverage only).
- **Do** check new artwork at 16 px on a dark (`#202020`) and a light (`#F3F3F3`) taskbar.
- **Don't** draw a microphone, a speaker or any other device as the identity.
- **Don't** recolor the head per state, or rely on color alone to tell states apart.
- **Don't** put a tile or background behind the tray glyph, or scale a big bitmap down
  for the small sizes.
