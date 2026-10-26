# Explain mode: 3D explainers and visual diagrams

**Status:** M1 built as a preview on the `explainers` branch: the live,
on-screen explainer (`explainer/`), opened by voice or from the tray, with six
library topics including the 2-stroke engine. The video-capture prototype stays
in `prototypes/explainers/`.
**Target:** Clixy 1.1 (beta) → 1.2.

### In the preview build

- `explainer/__init__.py`: the library, which questions open which topic
  (`match`), and the boot payload (recipes, fonts, buddy frames, a blurred
  picture of your screen, your buddy's colour).
- `explainer/window.py`: a frameless, always-on-top window over the screen your
  mouse is on, hosting an off-the-record `QWebEngineView` that can't navigate
  or reach the network. Esc, the close button or Alt+F4 hide it; it stays
  loaded so the next one opens at once.
- `explainer/web/`: the page and `clixy3d.js`, built from `src/` with esbuild
  (`npm install && npm run build`) into one classic script, because a
  `file://` page can't load ES modules, fonts or JSON.
- Live engine: drag to turn, scroll to zoom, double-click or R to reset,
  click any part or label for a card about it, ← → for steps, Space to pause,
  topic switcher. New parts (`shell` cutaways, `link` rods, `pipe` ducts,
  `glass`, `ceramic`) and motions (`slider` crank-sliders, `flash` gated by a
  crank's phase, particle `gate`s), plus a studio environment map so metal
  looks like metal.
- App: `sig_explain`; a question `explainer.match` recognises opens the topic
  and speaks one intro line instead of asking the AI. Tray: "Show me in 3D".

## The idea in one paragraph

Ask Clixy "show me how a 2-stroke engine works", "explain the water cycle",
"why is water bent?" or "turn this function into a flowchart", and a visual
explainer appears over your screen: a real 3D model you can drag around, or a
clean animated diagram. It plays step by step while Clixy talks, each part
lights up and gets a label as it's explained, Ember hops to what's being
explained, and clicking any part asks Clixy about it. Every explainer comes
from a **recipe**: plain data the AI writes on the spot, or one from Clixy's
built-in library. Recipes never contain code.

## What exists today (the prototype)

`prototypes/explainers/` renders five topics with one engine and five recipes:

| Recipe | Shows | Library parts it uses |
|---|---|---|
| `bicycle` | pedals → chainring → chain → cog → wheel, 44 ÷ 16 = 2.75 | tube, wheel, gear, chain, box |
| `water` | evaporation, condensation, precipitation, collection | terrain, water, river, cloud, sun, particles |
| `solar` | the Sun, rocky planets, gas giants, orbital speed | sun, sphere, torus, orbit motion |
| `dna` | double helix, A–T and G–C pairs | helix (strands / pairs) |
| `water_molecule` | oxygen, bonds, lone pairs, 104.5°, polarity | group, sphere, tube |

The engine already does: perspective camera with a slow orbit, studio
lighting with soft shadows, materials (metal, paint, rubber, water, ground,
cloud, glow), motions (spin, gear-ratio following, drift, bob, orbit), chains
running at the right speed, particle flows, per-step highlights, labels pinned
to 3D parts, step captions and Ember.

## What users get

1. **Ask and see.** Any "how does … work", "show me", "explain the process
   of", "visualize", "in 3D", "draw a diagram of" question opens an explainer.
2. **Narrated steps.** Clixy speaks each step; the scene highlights the parts
   for that step at the moment it says them. Space pauses, ← → step.
3. **Hands on.** Drag to rotate, scroll to zoom, hover a part to see its name,
   **click a part to ask about it** ("what does the transfer port do?").
4. **A library.** A browsable gallery (tray › *Explainers*): categories,
   search, thumbnails. One click plays any explainer. Ones you asked for are
   saved there too, so asking again is instant and free.
5. **Visualize my work.** "Turn this into a diagram" reads what's on screen
   and draws it: code → flowchart, a repo or config → architecture diagram,
   a document → timeline, notes → mind map, a table → animated chart.
6. **Take it with you.** Export any explainer as a PNG or a short MP4 for
   slides, notes or sharing.

## The library

### Families

| Family | Runtime | Examples |
|---|---|---|
| Machines and objects | 3D | 2- and 4-stroke engine, bicycle, gearbox, electric motor, generator, jet engine, wind turbine, hydraulic press, pin-tumbler lock, camera lens |
| Earth and nature | 3D diorama | water cycle, carbon cycle, volcano, plate tectonics, rock cycle, weather fronts, seasons |
| Space | 3D | solar system, moon phases, eclipses, day and night, Earth's tilt |
| Biology | 3D | DNA, animal cell, neuron, mitosis, photosynthesis, heart and lungs (needs organic shapes, milestone 4) |
| Chemistry | 3D | molecules (water, CO₂, methane, caffeine), salt crystal lattice, atom models, states of matter |
| Physics | 3D | pendulum, waves, levers and pulleys, circuits with flowing current, magnetic fields, refraction |
| Maths | 3D + 2D | unit circle and sine, Pythagoras, volumes of solids, 3D function surfaces, vectors |
| Tech | 2D/3D | how the internet routes a packet, client–server, git branching, CPU pipeline, neural network, B-tree index |
| Work diagrams | 2D | flowchart, cycle, timeline/roadmap, mind map, org chart, funnel, sequence diagram, architecture |

**Size:** 15 hand-tuned recipes in 1.1, 60+ by 1.2. Anything else is
generated on the spot and saved to the user's library.

### Recipe format (v1)

```json
{
  "kind": "3d",
  "title": "How a bike turns pedals into speed",
  "subtitle": "one push, a chain, and gears doing the maths",
  "duration": 12,
  "camera": { "target": [0.42, 0.44, 0.03], "distance": 2.05, "elevation": 12, "azimuth": [-32, 24] },
  "parts": [
    { "id": "chainring", "type": "gear", "at": [0.42, 0.28, 0.07], "radius": 0.105, "teeth": 44 },
    { "id": "cog", "type": "gear", "at": [0, 0.34, 0.07], "radius": 0.042, "teeth": 16 },
    { "id": "chain", "type": "chain", "ring": "chainring", "cog": "cog" }
  ],
  "motions": [
    { "part": "chainring", "rotate": { "axis": "z", "rps": 0.32 } },
    { "part": "cog", "rotate": { "axis": "z", "follow": "chainring", "ratio": 2.75 } }
  ],
  "steps": [
    { "name": "Chainring", "caption": "The 44-tooth chainring pulls the chain along.", "highlight": ["chainring", "chain"] }
  ],
  "labels": [ { "text": "Chainring · 44 teeth", "part": "chainring", "anchor": [0, 0.1, 0] } ]
}
```

Changes from the prototype: step times come from narration (no `from`/`to`
needed), label offsets and the camera are optional (placed automatically), and
`kind` picks the runtime (`3d` or `diagram`).

Diagram recipes use the same shell with `nodes`, `edges`, `groups` and a
`layout` (`flow`, `cycle`, `timeline`, `mindmap`, `tree`, `sequence`,
`architecture`); steps highlight nodes and send animated "packets" along
edges.

### Parts library

- **Have:** tube, box, sphere, cylinder, torus, gear, wheel, chain, terrain,
  water, river, cloud, sun, helix, group, particles.
- **1.1:** cone, capsule, lathe (a profile spun round: pistons, vases,
  lenses), extrude (2D outline → 3D), spring/coil, arrow, path tube, text
  card, molecule (atoms and bonds from a formula with standard bond angles),
  lattice.
- **1.2:** organic blob (sculpted spheres for organs and cells), exploded
  views (parts slide apart along an axis), cut-away (cross-section plane).
- **Later:** CC0 model packs (glTF) for things primitives can't do well.
  Only CC0 sources, listed in `THIRD_PARTY_NOTICES.md`.

**Motions:** have rotate (with `follow` and `ratio`), drift, bob, orbit. Add
oscillate (pendulum), piston (slider-crank linked to a crank), path-follow,
pulse (heartbeat), explode, split/merge.

## How it works

```
question ──► intent (wants_explainer) ──► library lookup ──► hit: play it
                                              │
                                              └─ miss: AI writes a recipe (tool call, JSON)
                                                     ──► validate + auto-fix ──► save ──► play
play: ExplainerWindow (QWebEngineView, transparent) ◄── QWebChannel ──► Python
      runtime.js: three.js scene + labels + captions + Ember
      steps ◄── narration (TTS says step N → highlight step N)
      clicks on parts ──► "ask about this part" ──► island answers
```

### New code

| File | What it does |
|---|---|
| `explainer/recipe.py` | Schema v1, validation, auto-fix: unknown part types dropped, numbers clamped, missing ids repaired, parts capped (≤120 meshes, ≤600 particles), camera fitted to the scene's bounds |
| `explainer/generate.py` | Builds the request: schema and 3 example recipes as a cached system prompt, the question, a screenshot for "visualize this". Forces JSON with a tool call (Anthropic) or JSON mode (others). Haiku 5.5 first, one retry with the validator's errors, then Sonnet 5.5 |
| `explainer/library.py` | Built-ins in `assets/explainers/*.json`, user recipes in `~/.clixy/explainers/`, an index with titles and keywords, fuzzy search, thumbnails |
| `explainer/window.py` | `ExplainerWindow`: frameless, translucent, dims the screen, hosts `QWebEngineView`; offline (a request interceptor blocks all network); keyboard and mouse; export PNG/MP4 |
| `explainer/library_window.py` | The gallery: categories, search, cards with thumbnails, an "ask for anything" box |
| `explainer/web/` | `runtime.js` (split from the prototype: engine, parts, motions, overlay), `diagram.js` (layout and animation for diagrams), vendored three.js (MIT) and its OrbitControls |

### Changes to existing code

| File | Change |
|---|---|
| `annotations.py` | `wants_explainer(question)` next to `wants_drawing` |
| `app.py` | Pipeline branch: explainer intent → notch says "Building your explainer…" → library or generate → show; captions and narration from the recipe's steps |
| `ai.py` | `AIClient.ask_json(system, user, schema, images=None, model=None)` with tool-forced JSON on Anthropic and JSON mode elsewhere |
| `tts.py` | `on_sentence_start(callback)`: fired by each provider's playback worker as a sentence starts, so step highlights land on the words |
| `overlay.py` | Hide Ember and marks while an explainer shows; Ember lives in the explainer instead |
| `settings_dialog.py` | Explainers section: on/off, "best quality" (Sonnet) vs "cheapest" (Haiku), open library folder, clear saved explainers |
| `tray.py` | *Explainers…* opens the gallery |
| `clixy.spec`, `requirements.txt` | `PyQt6-WebEngine`; bundle `explainer/web/` and `assets/explainers/` |
| `THIRD_PARTY_NOTICES.md` | three.js (MIT), any layout library (MIT only) |

## Safety and privacy

- Recipes are data, checked against the schema. No AI-written code runs.
- The web view loads only bundled files; every network request is blocked.
- Same privacy as today: the screenshot goes to your AI only for "visualize
  this", and only when you ask. Saved explainers stay in `~/.clixy/`.

## Cost and speed

- Built-in or saved explainer: free, opens in under a second.
- New explainer: one AI call writing 2–5k tokens of recipe. Roughly **0.3¢**
  on Haiku 5.5, **about 5¢** on Sonnet 5.5, less with prompt caching on the
  schema and examples. Shown in the notch like today's answers.
- Generation takes about 3–8 seconds. The notch shows progress, and Ember
  starts talking through the first step while the scene builds.
- Rendering budget: 60 fps on integrated graphics with ≤120 meshes, 2048 px
  shadows and pixel ratio capped at 1.25. It stops rendering when hidden.

## Packaging

- Adds `PyQt6-WebEngine`. **The installer grows by an estimated 100–150 MB**
  (measure in milestone 1). Fallback if that's a problem: download the 3D
  engine on first use.
- Transparent `QWebEngineView` on Windows: set the page background
  transparent and use a frameless translucent window. Verify on Windows 10
  and 11 in milestone 1; fallback is an opaque rounded card.

## Testing

- **Python:** schema validation and auto-fix, library search, intent
  detection, generation with a fake AI client (valid, invalid → retry →
  fallback), the bridge contract, settings.
- **Rendering:** a Linux CI job runs every built-in recipe in headless
  Chromium (as the prototype does), samples frames, fails on console errors
  or an empty canvas, and refreshes gallery thumbnails.
- **Windows:** the existing Windows job covers the Python side; the web view
  is checked by hand on a real machine each release.

## Milestones

| | Ships | Contents |
|---|---|---|
| **M1** | 1.1.0-beta | ExplainerWindow + runtime in the app, 10 built-ins, library lookup, narration sync, drag/rotate/zoom, Esc, Settings toggle. Measure installer size |
| **M2** | 1.1.x | AI-written recipes (Haiku → Sonnet), validation and auto-fix, saved explainers, click-a-part-to-ask, the gallery, PNG/MP4 export |
| **M3** | 1.2 | Diagram runtime (flowchart, cycle, timeline, mind map, architecture, sequence), "visualize this" from the screen, charts from on-screen tables, lathe/extrude/coil/molecule parts, exploded views, 60+ built-ins |
| **M4** | 1.3 | Organic shapes and cut-aways, CC0 model packs, optional community recipe gallery |

## Risks

- **AI-written scenes look simpler than hand-tuned ones.** Mitigation: rich
  parts (lathe, extrude, molecule), strong examples, auto-fix, the Sonnet
  fallback, and a "this looks wrong" button whose reports improve the
  examples.
- **Installer size.** Mitigation: measure first; on-demand download if needed.
- **Old GPUs.** Mitigation: quality presets (shadows off, lower pixel ratio)
  chosen automatically from the first frame times.

## Decisions needed

1. Accept the bigger installer for real 3D? *(recommended: yes)*
2. Window style for 1.1: full-screen dim with the explainer centred
   *(recommended)*, or a floating panel beside your work?
3. Ship 1.1 with Explain mode **on by default, labelled beta**? *(recommended)*
4. Model policy: Haiku first with automatic Sonnet fallback *(recommended)*,
   or always ask before using Sonnet?
