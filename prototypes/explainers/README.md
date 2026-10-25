# 3D explainers prototype

The engine behind Clixy's planned Explain mode (see `docs/plans/explainers.md`).
A **recipe** (plain JSON: parts from the library, how they move, steps,
labels) becomes a lit, shadowed, animated three.js scene with Clixy's labels,
step captions and Ember. Recipes never contain code.

Recipes here: `bicycle`, `water` (the water cycle), `solar`, `dna`,
`water_molecule`.

## Run it

```bash
cd prototypes/explainers
npm pack three@0.160.0 && tar xzf three-0.160.0.tgz && mv package three
QT_QPA_PLATFORM=offscreen python prep.py .        # Ember sprites and dimmed backdrops
python -m http.server 8765 --bind 127.0.0.1 &
# open http://localhost:8765/explainer.html?recipe=bicycle and call renderAt(t),
# or render a video with Playwright:
node capture.cjs bicycle bicycle.mp4 30
```

`clixy3d.js` is the runtime: the parts library (`BUILDERS`), materials,
motions (`rotate` with gear-ratio `follow`, `drift`, `bob`, `orbit`), chains,
particle flows, highlights, labels, captions and Ember.
