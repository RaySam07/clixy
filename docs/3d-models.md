# Clixy's 3D models

A 3D note shows a real model: smooth solids, lit like a studio photo (soft shadows, ambient
occlusion, wet tissue, metal, glass), slowly rocking, with labels on leader lines beside
it. You can drag it round. `model3d.py` builds it; `render3d.py` draws it on the GPU;
`scene3d.NoteModel` is the fallback on a PC without OpenGL 3.3.

There are two ways to get one:

- **A ready-made model** from `models3d/`: `{"type": "model", "name": "heart"}`. These are
  hand-built and checked by eye, so they are the best Clixy can show. Sonnet uses one
  whenever the subject has one.
- **Parts** written in the note: `{"type": "part", ...}` items, in the language below.

Check any model by eye with:

    %USERPROFILE%\.venvs\clixy\Scripts\python tools\dev\render_note.py models3d\heart.json out.png [--turn 40] [--tilt 10] [--cut] [--t 0.5]

It renders the note card exactly as Clixy draws it, offscreen, with no keys and no AI.

## A note with a model

```json
{"topic": "human heart", "title": "The heart: a four-chamber pump",
 "subtitle": "Blue carries low-oxygen blood, red carries oxygen-rich blood",
 "view": {"3d": true},
 "items": [{"type": "model", "name": "heart", "cutaway": true,
            "labels": ["left ventricle", "right atrium", "aorta"], "highlight": ["left ventricle"]},
           {"type": "text", "text": "right side to lungs, left side to body"}],
 "followups": ["How do heart valves work?"]}
```

- `labels`: which of the model's labels to show (all of them when left out). Seven or fewer
  reads best.
- `highlight`: parts to pick out; the rest go pale.
- `cutaway`: open it up along the model's own cut (only models that have one).
- `at`, `turn`, `scale`: place the whole model (rarely needed).
- `view`: `turn` (degrees round, default 30), `tilt` (degrees down, default 16), `zoom`
  (1 = fit), `ground` (false for things floating in space), `detail` (mesh resolution,
  120–260, default 190).

## A ready-made model file (`models3d/<name>.json`)

```json
{"title": "The human heart", "subtitle": "...",
 "aliases": ["human heart", "cardiac"],
 "view": {"turn": 30, "tilt": 12},
 "resolution": 220,
 "cutaway": {"normal": [0, -1, 0], "at": [0, 0.05, 0]},
 "parts": [ ...parts... ]}
```

`resolution` (120–320) is grid steps across the model's longest side: higher is finer and
slower to build (built once, then cached). `cutaway` is a plane: everything on the side the
normal points to is removed, so `[0, -1, 0]` removes the half nearest a viewer at -y.

## Parts

Coordinates: **z is up**; a viewer at the default turn looks from +x/+y towards the
middle (turn 30 means the camera sits 30° round from +x towards +y). Units are anything
(the model is scaled to fit); keep the whole model a few units across.

```json
{"type": "part", "label": "left ventricle", "color": "muscle", "material": "flesh",
 "blend": 0.3, "shapes": [ ...shapes... ],
 "bumps": {"size": 0.3, "depth": 0.02}, "move": {"beat": 0.05, "speed": 1.1}}
```

| field | meaning |
|---|---|
| `label` | text on its leader line (empty for none). Short: 1–3 words. |
| `color` | a name below or `#rrggbb` |
| `material` | how the surface takes light (below) |
| `shapes` | the solids that make the part, in order (below) |
| `blend` | how smoothly the shapes melt together (0 = hard joins; 0.1–0.4 organic) |
| `at`, `turn`, `scale` | move, turn (x, y, z degrees, x first) and size the whole part |
| `mirror` | `"x"` (or `["x","y"]`): the part is symmetric across that plane through its origin. Model one side only. |
| `repeat` | `{"count": 12, "axis": "z"}`: copies round the axis through the part's origin (petals, spikes, teeth, blades). Model the one copy on the +x side of the axis (for axis z). |
| `bumps` | `{"size": 0.3, "depth": 0.02, "style": "smooth" or "ridged"}`: lumpy or folded surface (ridged = brain-like folds) |
| `shell` | wall thickness: makes it hollow (seen when cut open) |
| `cut` | `{"normal": [..], "at": [..]}` (or a list): cut this part open, in its own frame |
| `inside` | colour of cut faces (default: a paler version of its colour) |
| `uncut` | true: the model's cutaway leaves this part whole |
| `opacity` | 0.05–1; glass and water are see-through by themselves |
| `anchor` | `[x, y, z]`: where its label points (default: the middle of what's visible) |
| `labels` | `[{"text": "apex", "at": [x, y, z]}, ...]`: more labels on the same part, each pointing at a spot (snapped onto its surface). `"tip": true` writes the name right beside the spot instead of on a leader line (arrows, planets). One smooth part with several named spots beats seams between parts. |
| `move` | a motion (below) |

### Shapes

Every shape takes `color`: the colour of the surface wherever that shape makes it (followed
through the joins, so a shape added later and blended paints its own region; a coloured
`subtract` shape colours the cavity it carves),
`subtract: true` (carve it out of what came before), `intersect: true` (keep only the
overlap), and `blend` (its own smoothness for this join).

| shape | fields |
|---|---|
| `sphere` | `at`, `r` |
| `ellipsoid` | `at`, `size` [rx, ry, rz] (radii), `turn` |
| `box` | `at`, `size` [w, d, h] (full sizes), `round` (edge radius), `turn` |
| `rod` (capsule) | `from`, `to`, `r`, `r2` (radius at `to`; tapered limbs, horns, roots) |
| `cylinder` | `from`, `to`, `r`, `r2`, `round` — or `at`, `height`, `turn` (axis z) |
| `cone` | like cylinder, `r2` defaults to 0 |
| `disc` | a short cylinder: `at`, `r`, `height`, `turn` |
| `torus` | `at`, `r` (ring), `tube` (thickness), `turn` (ring lies in local x-y) |
| `tube` | `points` [[x,y,z], …] (a smooth curve through them), `r` (one number, or one per point: tapering vessels, roots, tails), `loop` |
| `helix` | `at`, `r`, `pitch`, `turns`, `tube`, `turn` (springs, coils, DNA backbones) |
| `lathe` | `profile` [[radius, height], …] spun round local z: vases, flasks, bottles, bells, lenses, mountains, volcanoes, eyeballs, light bulbs. `at`, `turn`, `smooth` (default true) |
| `extrude` | `outline` [[x, y], …] (a closed outline, counter-clockwise), `height` (thickness along local z), `round`, `smooth` (rounds the outline), `at`, `turn`: leaves, wings, blades, plates, arrows, letters |
| `gear` | `at`, `r`, `teeth`, `thickness`, `hole`, `turn` (axis is local z) |

Smallest detail that shows: about 1/120 of the model's size (a tube thinner than that
breaks up). The whole model can have 60 parts and 240 shapes.

### Colours

red, crimson, blood, muscle, artery, flesh, pink, rose, salmon, blue, vein, navy, sky,
cyan, teal, green, leaf, lime, olive, yellow, gold, orange, amber, fat, brown, tan, wood,
sand, skin, purple, violet, magenta, lilac, white, ivory, bone, cream, grey, silver,
steel, iron, black, charcoal, copper, brass, bronze, rock, stone, lava, magma, ice, water,
glass, ink, coral, mint — or any `#rrggbb`.

### Materials

| material | looks like |
|---|---|
| `flesh`, `muscle` | wet living tissue: soft light under the surface, a wet sheen, fine grain |
| `skin`, `leaf`, `bone`, `tooth` | as named |
| `plastic`, `glossy`, `matte`, `rubber`, `fabric`, `ceramic` | as named (glossy = lacquered) |
| `metal`, `steel`, `brushed`, `chrome`, `gold`, `copper`, `brass` | real metals: they reflect the studio |
| `glass`, `water`, `jelly` | see-through |
| `wood`, `rock`, `soil` | grainy, rough |
| `glow`, `lava` | give off light (the sun, magma, an LED) |

### Motion (`move`)

| motion | fields |
|---|---|
| `{"spin": "z", "about": [x,y,z], "speed": 40}` | turns round an axis through `about` (default: its middle), degrees a second; also planets and electrons orbiting |
| `{"swing": "x", "amount": 20, "speed": 0.5, "about": [..]}` | rocks to and fro (pendulums, valves, wings) |
| `{"slide": [0,0,1], "amount": 0.3, "speed": 0.8}` | moves to and fro along a direction (pistons) |
| `{"beat": 0.05, "speed": 1.1}` | swells and squeezes (a heartbeat, lungs breathing) |

All take `phase` (0–1) to offset one part's cycle from another's (meshing gears turn at
opposite speeds in the ratio of their teeth).

## 3D maths and forces

`axes3d`, `vector3d`, `point3d`, `line3d`, `curve3d` (x, y, z of t), `surface` (z = f(x, y),
coloured by height), `field` (arrows on a grid) and `plane` become real solids too, sized to
the note, and mix with parts (the forces on a beam). Arrows, axes and points have their
names right at their tips. With vectors and no chosen `turn`, the view is picked so no
arrow points at the viewer as the model rocks. Axes float: no floor under them. Only the
older notes' `cutaway` wall and parts linked by id (`"follow": "crank"`) still go to the
painter's 3D (`scene3d.NoteModel`).

## What makes a model look real

- Build forms the way a sculptor would: big masses first (ellipsoids blended with `blend`
  0.2–0.4), then secondary forms, then details. Organic things are never one sphere.
- Use real proportions and real colours. Asymmetry and slight turns make things look alive.
- Join vessels, stems, roots and limbs into bodies with `blend` so they grow out of them.
- Use `subtract` for cavities, sockets and holes, and `shell` + the model's `cutaway` to
  show insides (chambers, layers, cells).
- Give each distinct part its own part (and label), so labels point at the right thing
  and parts can be picked out.
