# VoiceMate brand: the mark and the status light

VoiceMate turns your voice into text ready to paste. Its icon has to do two jobs: be
recognizable as VoiceMate (Start menu, taskbar, installer, windows) and, in the tray,
tell at a glance what the companion is doing, the way the Windows microphone-in-use
indicator does. It is not a microphone: a microphone says "audio device", every
recorder and every Windows sound icon already uses it, and VoiceMate is a companion,
not a device.

## The mark: the voice that writes M(ate)

- **The M wave:** one continuous, thick, white sound-wave stroke that draws a capital M
  (up, down, up, down) with round caps and round joins. Your voice (the wave) becomes
  the companion (the M of VoiceMate).
- **The coral dot:** one solid dot floating above the middle valley of the M, between
  the two peaks, like a listening light. It is the companion paying attention, and in
  the tray it is the status light (below).
- **The tile:** a rounded square with a diagonal gradient from indigo (top left) to
  violet (bottom right). It carries its own colors, so the icon reads on dark and
  light taskbars without a theme-specific variant.

The code is the source of truth: `app/companion/ui/icons.py` draws the mark and the
tray glyphs, and `tools/gen_icon.py` (`python -m tools.gen_icon`) writes the packaged
`app/companion/assets/voicemate.ico` and `voicemate-<size>.png` from the same code.
`tests/companion/ui/test_ui_icons.py` fails when the PNGs are older than the drawing.

### Colors

| Role | Hex |
|---|---|
| Tile, top left | `#4338CA` |
| Tile, bottom right | `#7C3AED` |
| M wave | `#FFFFFF` |
| Brand dot (coral) | `#FB7185` |
| Recording | `#EF4444` |
| Transcribing, warning | `#F59E0B` |
| Claude answering | `#22D3EE` |
| Copied (ready) | `#34D399` |
| Starting rings | `#E5E7EB` |
| Stopped tile | `#3F3F46` to `#52525B`, wave `#D4D4D8` |
| Ink on the warning badge | `#1B1B1B` |

### Geometry

Designed on a 32-unit grid (y grows downwards); every size is painted on its own,
never downscaled from a big bitmap.

| | 32-unit grid (20, 32, 40, 48, 64, 128, 256 px) | 24 px grid | 16 px grid |
|---|---|---|---|
| Tile | 1..31, corner radius 7 (22 %) | 1..23, radius 5 | 0..16, radius 3.5 |
| Wave stroke | 4 | 3 | 2 |
| Wave points | (6, 22.5) (10.75, 12.5) (16, 20) (21.25, 12.5) (26, 22.5) | (4.5, 18.5) (8.5, 10.5) (12, 16.5) (15.5, 10.5) (19.5, 18.5) | (3, 13) (5.5, 7) (8, 11) (10.5, 7) (13, 13) |
| Dot | centre (16, 9.5), radius 2.6 | centre (12, 5.5), radius 2.5 | centre (8, 4), radius 2 |

- The 16 and 24 px designs are drawn on their own pixel grids: the wave's outer edges
  and the tile land on whole pixels, and the mark is symmetric about the centre line.
  16 px has no transparent margin (half a pixel would only blur the tile's edge).
- The dot sits between the peaks, slightly above them, with a clear gap to the wave.

## The status light (tray)

The tray shows the full mark, and the state is told by the dot. Every state has its
own SHAPE; the color only reinforces it.

| `TrayState` | Signal | Shape |
|---|---|---|
| `idle` | brand dot | the coral dot: listening for the hotkey |
| `recording` | record | a bigger red dot inside a white ring, the most prominent state |
| `transcribing` | ellipsis | three small amber dots in a row |
| `thinking` | sparkle | a cyan four-point sparkle (Claude is thinking) |
| `speaking` | bubble | a cyan speech bubble whose tail points into the M (the answer is read aloud) |
| `ready` (3 s) | check | a mint check mark (copied to the clipboard) |
| `starting` | ring | a hollow light-grey ring: the light is not on yet |
| `restarting` | open ring | the ring with a gap and an arrow head: going round again |
| `stopped` | off | grey tile and wave, no dot |
| `warning` | triangle | no dot; an amber triangle with a dark "!" in the bottom-right corner, cut out of the tile |
| `error` | cross | no dot; a red disc with a white X in the bottom-right corner, cut out of the tile |

The warning and error badges replace the dot: the companion cannot listen properly
(no microphone, WSL audio down, the engine failed), so the listening light is off.
No animation by default.

## Do and don't

- **Do** keep the mark on its tile, with the dot above the valley of the M.
- **Do** tell a state by shape first. A new state gets a new silhouette, and
  `tests/companion/ui/test_ui_icons.py` checks every pair at 16, 24 and 32 px with the
  hue removed (luma and coverage only).
- **Do** check new artwork at 16 px on a dark (`#202020`) and a light (`#F3F3F3`)
  taskbar before shipping it.
- **Don't** draw a microphone, a speaker or any other device: the identity is the M wave
  and its dot.
- **Don't** rely on color alone (red vs green dot, for example): color-blind users and
  high contrast themes would lose the state.
- **Don't** scale a big bitmap down for the small sizes, or add thin details that
  vanish at 16 px.
- **Don't** recolor the tile per theme: the gradient tile is what makes the icon read
  on any taskbar.
