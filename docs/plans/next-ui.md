# Next: a sleek, professional UI, on-screen 3D, and the cursor

Written at the hand-off from the cloud session (3 Nov 2026). Build these on the
laptop with Clixy dev running, so every change can be looked at live.

## 1. The notch: professional, blue, less rounded (Rayan's brief)

**The approved design is `docs/plans/blue-notch-design.png`**, drawn by
`docs/plans/blue-notch-mock.py`. Copy its colours, sizes and shapes into
`island.py`: the Clixy spark glyph, the cyan → blue pill, the navy-black gradient
with a blue edge line, radius 10, and the new pointer cursor.

Rayan's reference is HeyClicky's notch: a longer black tab that says **Listening**,
with a glowing pill on the right. He **doesn't want the orange fire** in it.

- **Shape.** Make it longer and less rounded: about 340 × 36 while listening, with
  corner radius about 10 (not a full pill). Keep it flush with the top edge, and
  keep only the lower corners rounded.
- **Colour.** Black with a subtle deep-blue gradient (#05070d → #0b1630 toward the
  bottom/right), plus a faint blue edge glow. Use blue everywhere the notch now uses
  the buddy's orange: the voice pill should be a cyan → blue gradient (#3ee0ff →
  #2f6fe0) with white dots.
- **Words.** "Listening" / "Thinking" in Manrope SemiBold, white, centred. "Thinking"
  keeps the shimmer.
- **No fire in the notch.** Replace the Ember sprite on the left with either nothing
  or a small, clean Clixy mark (a monochrome glyph). Apply the same to the resting
  notch, the hello ("Hey Rayan!") and the dashboard header.
- **Where it lives in code.**
  - `island.py`: `_paint_pill`, `_paint_voice_pill`, `_paint_notch`, `_paint_dash`,
    `_shape_path`, plus the `NOTCH` / `HELLO` / `PILL` sizes.
  - The colours are on `theme.current_palette().flood`. Add a fixed professional
    blue palette for the notch rather than the buddy's colour.
- **Check** with the island tests (`tests/test_island.py`) and a real look.

## 2. The cursor

The companion cursor that flies to things and points is currently the pixel buddy
(the fire). Rayan wants it changed.

- Make a sleek pointer the default: a smooth blue-gradient arrow or rounded triangle
  with a soft glow, the same blue as the notch, that flies and points like the buddy
  does now.
- Keep the pixel buddies as an option: Settings > General > "Cursor: Pointer / Buddy".
- **Where it lives in code.** `overlay.py` (`avatar_rect`, the paint of the pointer,
  `_paint_point_tag`), `drawing.py` (the tour the pen follows) and `theme.py`
  (`paint_avatar`).

## 3. On-screen 3D models (built, not wired in yet)

`scene3d.py` is a native 3D renderer (numpy + QPainter, no web engine):
- a parts library: box, cylinder, cone, sphere, torus, ring, fan blades, gear, tube,
  cutaway shell;
- materials, spinning parts, a soft shadow, and numbered chips ("1 · Fan") with
  leader lines;
- `ModelOverlay`, a click-through window that floats the model over the screen.

To do:
1. **Speed.** About 135 ms a frame for ~1,200 triangles at 2× (offscreen). Aim for
   under 33 ms:
   - batch triangles of similar shade into one QPainterPath per colour band, or
     draw into a QImage at 1× and scale;
   - cache the projection when only spinning parts move;
   - cap triangles per part.
2. **Look.**
   - Leader lines and dots should be white on dark screens; check the backdrop's
     brightness the way `aim.busy_map` does.
   - The highlight tint should be the new blue, not orange.
   - The shell cutaway's orientation should face the camera.
3. **Wire it smart: any concept, not just machines.** Rayan's brief: "3D wiring needs
   to be smart and adaptable to any scenario, whatever concept needs teaching."
   - **One teaching plan per question.** The sketch call (Sonnet 5.5, light thinking)
     returns `{"note": {...}, "model": {...}}` with either or both. The model chooses:
     - **3D** for anything spatial or physical: machines, anatomy, molecules,
       planets, solids, vectors and fields, buildings;
     - **the note** for graphs, processes, comparisons and timelines;
     - **both** when the 3D needs a written takeaway beside it.
   - **3D for abstract ideas too.** Add builders the model can use as spatial metaphors:
     - `graph3d` (nodes as spheres, edges as tubes): data structures, networks,
       family trees, how ideas connect;
     - `bars3d`: comparing amounts;
     - `molecule` (atoms by element colour, bonds as sticks);
     - `field` (a grid of arrows): forces, flow, gradients;
     - `path` (a point moving along a curve): orbits, journeys, a signal travelling;
     - `stack` (labelled layers): the OSI model, earth's layers, a cell wall.
   - **Build big things cheaply.** Add `repeat` (radial or linear arrays with a step,
     a turn and a scale per copy), so 20 compressor stages or a DNA ladder is one
     part, not twenty.
   - **Teach in steps, in sync with the voice.** The model gives `steps`. Each step:
     - names the parts to highlight;
     - can move the camera (turn or zoom to a part);
     - shows that step's chip and caption;
     - can show or hide parts and play motions: spin, move along a path, pulse,
       explode a part outward to show inside.
     Step timing follows the spoken sentences (the TTS already reports each
     sentence as it plays).
   - **Always land somewhere useful.** If the 3D JSON is invalid or too heavy, show
     the note card; if that fails too, the voice answer stands alone.
   - **Show and hide.** In `app._on_show_sketch`, show `ModelOverlay` for a model and
     the note card for a note. Hide both on the next hotkey press and on Clear.
   - **Try it with a spread of questions.** For example: how a jet engine works; the
     heart's four chambers; why ice floats (a water molecule); how a binary search
     tree finds a number; supply and demand; the OSI model; Earth's seasons (an
     orbit with tilt); and the cross product. Each should pick a sensible form.
4. **Tests.** Copy the pattern of `tests/test_sketch.py`:
   - parse_model refusing bad input;
   - every shape building triangles;
   - the overlay painting with spinning parts and chips.

## 4. A brain that does tasks (decision pending)

Rayan wants Clixy to do tasks for him, like Hark: connectors (MCP) and on-screen
computer use. The proposal:
- **Connectors first** (Gmail, Calendar, Notion, Slack, Google Docs through the
  Claude API's MCP connector): fast and cheap.
- **Then on-screen "do it for me"** with Claude's computer-use toolset
  (`computer_toolset_20260801` on Sonnet 5.5).
- **Safety rule:** always ask before anything that sends, buys, deletes or posts;
  Esc or Ctrl+Win stops it instantly; it never types passwords or card numbers.

Ask him to confirm which connectors to do first before building. Read the
`claude-api` skill docs for the MCP connector and computer-use toolset before
writing that code.
