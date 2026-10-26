// The live explainer: a recipe becomes a 3D model on your screen that you can
// turn, zoom and click, with Clixy's labels, captions and your buddy perched on
// the part being explained. Python boots it with Clixy3D.boot(payload) and
// hears back through the page title (no file loads, so it runs from file://).
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { BUILDERS, buildChain, v3 } from './parts.js';

const $ = (id) => document.getElementById(id);
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const ease = (k) => 1 - Math.pow(1 - clamp(k, 0, 1), 3);

let payload = null;    // { recipes, order, open, accent, onAccent, buddy: [dataURL], backdrop, fonts }
let current = null;    // the running scene
let messageCount = 0;

// --- Talking to Clixy ------------------------------------------------------------------
function tell(what) {
  messageCount += 1;
  document.title = `clixy:${what}:${messageCount}`;
}

// --- Boot -------------------------------------------------------------------------------
async function loadFonts(fonts) {
  for (const [weight, url] of Object.entries(fonts || {})) {
    try {
      const face = new FontFace('Manrope', `url(${url})`, { weight });
      document.fonts.add(await face.load());
    } catch (e) { /* the fallback sans is fine */ }
  }
}

export async function boot(p) {
  payload = p;
  const root = document.documentElement.style;
  root.setProperty('--accent', p.accent || '#ff5a3d');
  root.setProperty('--on-accent', p.onAccent || '#ffffff');
  if (p.backdrop) $('backdrop').style.backgroundImage = `url(${p.backdrop})`;
  await loadFonts(p.fonts);
  buildTopics();
  show(p.open || p.order[0]);
  document.body.classList.add('in');
}

// Fresh screen behind, maybe another topic, every time the window opens again.
export function reopen(p) {
  Object.assign(payload, p);
  if (p.backdrop) $('backdrop').style.backgroundImage = `url(${p.backdrop})`;
  if (p.accent) document.documentElement.style.setProperty('--accent', p.accent);
  if (p.onAccent) document.documentElement.style.setProperty('--on-accent', p.onAccent);
  document.body.classList.remove('in');
  void document.body.offsetWidth;
  show(p.open || current?.id);
  document.body.classList.add('in');
}

export function pause() { if (current) current.sleeping = true; }

function buildTopics() {
  const box = $('topics');
  box.innerHTML = '';
  for (const id of payload.order) {
    const b = document.createElement('button');
    b.textContent = payload.recipes[id].short || payload.recipes[id].title;
    b.dataset.id = id;
    b.onclick = () => show(id);
    box.appendChild(b);
  }
}

export function show(id) {
  if (current) current.dispose();
  current = null;
  try {
    current = new Explainer(id, payload.recipes[id]);
  } catch (e) {
    // No WebGL: the graphics driver is blocked or off (some VMs, remote desktops).
    document.body.classList.add('no3d');
    $('title').textContent = "3D can't start on this PC";
    $('subtitle').textContent = 'Graphics acceleration is off or blocked here. Updating the graphics driver usually fixes it.';
    tell('error:webgl');
    return;
  }
  document.body.classList.remove('no3d');
  for (const b of $('topics').children) b.classList.toggle('on', b.dataset.id === id);
  tell(`topic:${id}`);
}

// --- One explainer on screen --------------------------------------------------------------
class Explainer {
  constructor(id, recipe) {
    this.id = id;
    this.recipe = recipe;
    this.t = 0;                 // the motion clock: rotations, flows, linkages
    this.paused = false;
    this.step = 0;
    this.stepClock = 0;         // seconds into the current step
    this.picked = null;         // a part the person clicked
    this.userCamera = false;    // they turned it themselves, so stop swaying
    this.born = performance.now();
    this.flowStrength = {};
    this.emberPos = null;
    this.alive = true;
    this.sleeping = false;
    this.build();
    this.bindInput();
    this.last = performance.now();
    const loop = (now) => {
      if (!this.alive) return;
      requestAnimationFrame(loop);
      if (this.sleeping) { this.last = now; return; }
      const dt = Math.min(0.05, (now - this.last) / 1000);
      this.last = now;
      this.frame(dt, now / 1000);
    };
    requestAnimationFrame(loop);
  }

  build() {
    const r = this.recipe;
    const renderer = this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    $('stage').appendChild(renderer.domElement);

    const scene = this.scene = new THREE.Scene();
    const cam = r.camera;
    this.camera = new THREE.PerspectiveCamera(cam.fov || 32, 16 / 9, 0.01, 200);
    this.view = {
      az: cam.azimuth[0] + (cam.azimuth[1] - cam.azimuth[0]) * 0.25,
      el: cam.elevation,
      dist: cam.distance,
      target: v3(cam.target),
    };

    // A soft studio room for metal to reflect, so steel reads as steel, not black.
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(renderer), 0.04).texture;
    pmrem.dispose();
    scene.add(new THREE.HemisphereLight(0xfff4e6, 0x30324a, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 2.4);
    key.position.set(...(cam.light || [3, 6, 4]));
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    const ext = cam.shadowExtent || 3;
    Object.assign(key.shadow.camera, { left: -ext, right: ext, top: ext, bottom: -ext, near: 0.1, far: 40 });
    key.shadow.bias = -0.0005;
    key.shadow.radius = 6;
    scene.add(key);
    const rim = new THREE.DirectionalLight(0xff7a52, 1.3);
    rim.position.set(-4, 3, -5);
    scene.add(rim);
    if (r.floor !== false) {
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.ShadowMaterial({ opacity: 0.42 }));
      floor.rotation.x = -Math.PI / 2;
      floor.position.y = r.floorY ?? 0;
      floor.receiveShadow = true;
      scene.add(floor);
    }

    // Everything the recipe builds hangs off one root, so it can pop in.
    const root = this.root = new THREE.Group();
    scene.add(root);
    const parts = this.parts = {};
    for (const spec of r.parts) {
      if (spec.type === 'chain' || spec.type === 'particles') continue;
      const builder = BUILDERS[spec.type];
      if (!builder) continue;
      const obj = builder(spec);
      const holder = new THREE.Group();
      holder.add(obj);
      if (spec.at && spec.type !== 'tube' && spec.type !== 'link') holder.position.set(...spec.at);
      if (spec.axis === 'x') obj.rotation.y = Math.PI / 2;
      if (spec.axis === 'y') obj.rotation.x = Math.PI / 2;
      if (spec.rotation) obj.rotation.set(...spec.rotation);
      obj.traverse((o) => { o.userData.part = spec.id; });
      parts[spec.id] = { spec, holder, obj };
    }
    for (const p of Object.values(parts)) {
      const parent = p.spec.attach && parts[p.spec.attach] ? parts[p.spec.attach].holder : root;
      parent.add(p.holder);
    }
    this.chains = r.parts.filter((s) => s.type === 'chain').map((s) => ({ spec: s, ...buildChain(s, parts) }));
    for (const c of this.chains) { root.add(c.mesh); parts[c.spec.id] = { spec: c.spec, holder: c.mesh, obj: c.mesh }; c.mesh.userData.part = c.spec.id; }
    this.flows = r.parts.filter((s) => s.type === 'particles').map((s) => {
      const curve = new THREE.CatmullRomCurve3(s.path.map(v3));
      const geo = new THREE.SphereGeometry(s.size || 0.03, 10, 8);
      const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(s.color || '#4aa3ff'), roughness: 0.3,
                                                  transparent: true, opacity: 0.95,
                                                  emissive: new THREE.Color(s.color || '#4aa3ff'), emissiveIntensity: s.glow || 0 });
      const mesh = new THREE.InstancedMesh(geo, mat, s.count || 24);
      mesh.raycast = () => {};
      root.add(mesh);
      parts[s.id] = { spec: s, holder: mesh, obj: mesh };
      this.flowStrength[s.id] = 0;
      return { spec: s, curve, mesh, mat };
    });
    this.links = Object.values(parts).filter((p) => p.spec.type === 'link');

    const envStrength = r.environment ?? 0.55;
    root.traverse((o) => { if (o.material && 'envMapIntensity' in o.material) o.material.envMapIntensity = envStrength; });

    // Every material of a part can glow in the buddy's colour.
    this.glowMats = {};
    for (const [id, p] of Object.entries(parts)) {
      if (p.spec.type === 'particles') continue;
      const mats = [];
      p.obj.traverse((o) => {
        if (o.material && o.material.emissive) {
          o.material = o.material.clone();
          o.material.userData.baseEmissive = o.material.emissive.getHex();
          o.material.userData.baseIntensity = o.material.emissiveIntensity;
          mats.push(o.material);
        }
      });
      this.glowMats[id] = mats;
    }
    // Flashes (a spark, a burst) fade in and out with their crank.
    this.flashMats = {};
    for (const m of r.motions) {
      if (!m.flash || !parts[m.part]) continue;
      const mats = [];
      parts[m.part].obj.traverse((o) => { if (o.material) { o.material = o.material.clone(); o.material.transparent = true; mats.push(o.material); } });
      this.flashMats[m.part] = mats;
    }

    // Labels, leader lines, step dots, caption.
    $('title').textContent = r.title;
    $('subtitle').textContent = r.subtitle || '';
    const dots = $('dots');
    dots.innerHTML = '';
    r.steps.forEach((s, i) => {
      const d = document.createElement('i');
      d.title = s.name;
      d.onclick = () => this.goStep(i);
      dots.appendChild(d);
    });
    $('labels').innerHTML = '';
    const svg = $('leaders');
    svg.innerHTML = '';
    this.labelEls = r.labels.map((l) => {
      const el = document.createElement('div');
      el.className = 'label';
      el.textContent = l.text;
      el.onclick = (e) => {  // the card opens beside the label, not over it
        e.stopPropagation();
        const r = el.getBoundingClientRect();
        this.pick(l.part, r.left + r.width / 2 < window.innerWidth / 2 ? r.left : r.right, r.top + r.height / 2);
      };
      $('labels').appendChild(el);
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      svg.appendChild(line);
      const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      svg.appendChild(dot);
      return { l, el, line, dot };
    });
    this.hideCard();
    this.resize();
  }

  dispose() {
    this.alive = false;
    if (!this.renderer) return;
    this.unbind();
    this.renderer.domElement.remove();
    this.renderer.dispose();
    this.scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose());
    });
  }

  // --- Input ---------------------------------------------------------------------------
  bindInput() {
    const canvas = this.renderer.domElement;
    let down = null;
    const onDown = (e) => {
      if (e.button !== 0) return;
      down = { x: e.clientX, y: e.clientY, az: this.view.az, el: this.view.el, moved: false };
      canvas.setPointerCapture(e.pointerId);
    };
    const onMove = (e) => {
      if (!down) return;
      const dx = e.clientX - down.x, dy = e.clientY - down.y;
      if (!down.moved && Math.hypot(dx, dy) < 5) return;
      down.moved = true;
      this.userCamera = true;
      document.body.classList.add('dragging');
      this.view.az = down.az - dx * 0.32;
      this.view.el = clamp(down.el + dy * 0.22, -12, 75);
    };
    const onUp = (e) => {
      if (!down) return;
      document.body.classList.remove('dragging');
      if (!down.moved) this.clickAt(e.clientX, e.clientY);
      down = null;
    };
    const onWheel = (e) => {
      e.preventDefault();
      this.userCamera = true;
      const d = this.recipe.camera.distance;
      this.view.dist = clamp(this.view.dist * Math.exp(e.deltaY * 0.0012), d * 0.45, d * 2.4);
    };
    const onDouble = () => this.resetView();
    const onKey = (e) => {
      if (e.key === 'Escape') { if (this.picked) this.hideCard(); else tell('close'); }
      else if (e.key === 'ArrowRight') this.goStep(this.step + 1);
      else if (e.key === 'ArrowLeft') this.goStep(this.step - 1);
      else if (e.key === ' ') { e.preventDefault(); this.togglePause(); }
      else if (e.key === 'r' || e.key === 'R') this.resetView();
    };
    const onResize = () => this.resize();
    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    canvas.addEventListener('dblclick', onDouble);
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', onResize);
    $('prev').onclick = () => this.goStep(this.step - 1);
    $('next').onclick = () => this.goStep(this.step + 1);
    $('pause').onclick = () => this.togglePause();
    $('close').onclick = () => tell('close');
    $('card-close').onclick = () => this.hideCard();
    this.unbind = () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onResize);
    };
  }

  resize() {
    const w = window.innerWidth, h = window.innerHeight;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    // Recipes are framed for 16:9; a narrower screen backs the camera off.
    this.fit = Math.max(1, (16 / 9) / (w / h));
    this.camera.updateProjectionMatrix();
    this.scale = clamp(Math.min(w / 1920, h / 1080), 0.62, 1.3);
    document.documentElement.style.setProperty('--s', this.scale.toFixed(3));
  }

  resetView() {
    const cam = this.recipe.camera;
    this.userCamera = false;
    this.view.dist = cam.distance;
    this.view.el = cam.elevation;
  }

  togglePause() {
    this.paused = !this.paused;
    $('pause').classList.toggle('paused', this.paused);
  }

  goStep(i) {
    const n = this.recipe.steps.length;
    this.step = ((i % n) + n) % n;
    this.stepClock = 0;
    this.hideCard();
  }

  // --- Picking a part ----------------------------------------------------------------
  clickAt(x, y) {
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2((x / window.innerWidth) * 2 - 1, -(y / window.innerHeight) * 2 + 1), this.camera);
    const hit = ray.intersectObject(this.root, true).find((h) => h.object.userData.part && h.object.visible);
    if (hit) this.pick(hit.object.userData.part, x, y);
    else this.hideCard();
  }

  describe(id) {
    const r = this.recipe;
    const label = r.labels.find((l) => l.part === id || (l.also || []).includes(id));
    const spec = this.parts[id]?.spec || {};
    const name = label?.text || spec.name;
    const info = label?.info || spec.info;
    if (!name) return null;
    return { name, info, parts: label ? [label.part, ...(label.also || [])] : [id] };
  }

  pick(id, x, y) {
    const d = this.describe(id);
    if (!d) { this.hideCard(); return; }
    this.picked = d;
    $('card-name').textContent = d.name;
    $('card-info').textContent = d.info || '';
    const card = $('card');
    card.classList.add('on');
    const w = card.offsetWidth, h = card.offsetHeight;
    // Beside what was clicked, on the side away from the middle of the model.
    const left = clamp(x < window.innerWidth / 2 ? x - w - 28 : x + 28, 24, window.innerWidth - w - 24);
    const top = clamp(y - h / 2, 140 * this.scale, window.innerHeight - h - 150 * this.scale);
    card.style.left = `${left}px`;
    card.style.top = `${top}px`;
    tell(`part:${id}`);
  }

  hideCard() {
    this.picked = null;
    $('card').classList.remove('on');
  }

  // --- Motion ----------------------------------------------------------------------------
  speedOf(id, seen = new Set()) {
    const m = this.recipe.motions.find((x) => x.part === id && x.rotate);
    if (!m || seen.has(id)) return 0;
    seen.add(id);
    if (m.rotate.follow) return this.speedOf(m.rotate.follow, seen) * (m.rotate.ratio || 1);
    return m.rotate.rps;
  }

  // Where a crank is in its turn, 0 to 1 (0 = its pin at the top).
  phaseOf(id) {
    const v = this.speedOf(id) * this.t;
    return ((v % 1) + 1) % 1;
  }

  // 0..1..0 across a phase window; windows may wrap past 1.
  gate(g) {
    const ph = this.phaseOf(g.crank);
    const len = ((g.to - g.from) % 1 + 1) % 1 || 1;
    const into = ((ph - g.from) % 1 + 1) % 1;
    if (into > len) return 0;
    const k = into / len;
    return g.shape === 'flat' ? clamp(Math.min(k, 1 - k) * 8, 0, 1) : Math.sin(Math.PI * k);
  }

  stepRange() {
    const s = this.recipe.steps[this.step];
    return [s.from, s.to];
  }

  move() {
    const t = this.t;
    const parts = this.parts;
    for (const m of this.recipe.motions) {
      const p = parts[m.part];
      if (!p) continue;
      const base = p.spec.at || [0, 0, 0];
      if (m.rotate) p.holder.rotation[m.rotate.axis || 'z'] = -2 * Math.PI * this.speedOf(m.part) * t + (m.rotate.phase || 0);
      if (m.drift) {
        const k = Math.sin((2 * Math.PI * t) / m.drift.period);
        p.holder.position.set(base[0] + m.drift.axis[0] * m.drift.amplitude * k,
                              base[1] + m.drift.axis[1] * m.drift.amplitude * k,
                              base[2] + m.drift.axis[2] * m.drift.amplitude * k);
      }
      if (m.bob) p.holder.position.y = base[1] + Math.sin(t * m.bob.speed) * m.bob.amplitude;
      if (m.orbit) {
        const a = (2 * Math.PI * t) / m.orbit.period + (m.orbit.phase || 0);
        const c = m.orbit.center || [0, 0, 0];
        p.holder.position.set(c[0] + Math.cos(a) * m.orbit.radius, c[1], c[2] - Math.sin(a) * m.orbit.radius);
      }
    }
    this.root.updateMatrixWorld(true);
    // Sliders (a piston): ride a rod of fixed length from a crank pin.
    for (const m of this.recipe.motions) {
      const p = parts[m.part];
      if (!p || !m.slider || !parts[m.slider.crank]) continue;
      const pin = parts[m.slider.crank].holder.localToWorld(v3(m.slider.pin));
      p.holder.parent.worldToLocal(pin);
      const base = p.spec.at || [0, 0, 0];
      const dx = pin.x - base[0];
      p.holder.position.set(base[0], pin.y + Math.sqrt(Math.max(0, m.slider.rod ** 2 - dx * dx)), base[2]);
    }
    this.root.updateMatrixWorld(true);
    this.updateLinks();
    // Flashes.
    for (const m of this.recipe.motions) {
      if (!m.flash || !parts[m.part]) continue;
      const k = this.gate(m.flash);
      const p = parts[m.part];
      p.holder.visible = k > 0.01;
      p.holder.scale.setScalar((m.flash.grow ? 0.55 + 0.45 * k : 1) * 1);
      for (const mat of this.flashMats[m.part]) mat.opacity = (m.flash.opacity ?? 0.9) * k;
    }
  }

  updateLinks() {
    const up = new THREE.Vector3(0, 1, 0);
    for (const p of this.links) {
      const a = this.worldPoint(p.spec.from), b = this.worldPoint(p.spec.to);
      if (!a || !b) continue;
      const parent = p.holder.parent;
      parent.worldToLocal(a);
      parent.worldToLocal(b);
      const len = a.distanceTo(b);
      p.holder.position.copy(a).add(b).multiplyScalar(0.5);
      p.holder.quaternion.setFromUnitVectors(up, b.clone().sub(a).normalize());
      const [rod, ...eyes] = p.obj.children;
      rod.scale.set(1, len, 1);
      for (const e of eyes) e.position.y = e.userData.end * len;
    }
  }

  worldPoint(ref) {
    const p = this.parts[ref.part];
    if (!p) return null;
    return p.holder.localToWorld(v3(ref.at || [0, 0, 0]));
  }

  flowsUpdate(dt) {
    const [s0, s1] = this.stepRange();
    const dummy = new THREE.Object3D();
    for (const f of this.flows) {
      const w = f.spec.window;
      let want = !w || (w[0] < s1 && w[1] > s0) ? 1 : 0;
      if (f.spec.gate) want *= this.gate({ shape: 'flat', ...f.spec.gate });
      const cur = this.flowStrength[f.spec.id];
      const strength = this.flowStrength[f.spec.id] = cur + (want - cur) * Math.min(1, dt * (f.spec.gate ? 14 : 3));
      f.mesh.visible = strength > 0.01;
      f.mat.opacity = 0.95 * strength;
      const n = f.spec.count || 24;
      for (let i = 0; i < n; i++) {
        const u = ((this.t * (f.spec.speed || 0.25) + i / n) % 1 + 1) % 1;
        const pt = f.curve.getPointAt(u);
        const jit = f.spec.spread || 0;
        dummy.position.set(pt.x + Math.sin(i * 12.9) * jit, pt.y + Math.cos(i * 7.3) * jit * 0.3, pt.z + Math.sin(i * 4.1) * jit);
        dummy.scale.setScalar(Math.max(0.05, Math.min(1, u * 8, (1 - u) * 8)));
        dummy.updateMatrix();
        f.mesh.setMatrixAt(i, dummy.matrix);
      }
      f.mesh.instanceMatrix.needsUpdate = true;
    }
    for (const c of this.chains) {
      const surface = 2 * Math.PI * c.ra * this.speedOf(c.spec.ring);
      const shift = (surface * this.t) / c.length;
      for (let i = 0; i < c.n; i++) {
        const u = (((i / c.n - shift) % 1) + 1) % 1;
        const pt = c.curve.getPointAt(u);
        const tan = c.curve.getTangentAt(u);
        dummy.position.copy(pt);
        dummy.rotation.set(0, 0, Math.atan2(tan.y, tan.x));
        dummy.scale.setScalar(1);
        dummy.updateMatrix();
        c.mesh.setMatrixAt(i, dummy.matrix);
      }
      c.mesh.instanceMatrix.needsUpdate = true;
    }
  }

  // --- One frame -------------------------------------------------------------------------
  frame(dt, now) {
    const r = this.recipe;
    if (!this.paused) {
      this.t += dt * (r.speed || 1);
      if (!this.picked) {
        this.stepClock += dt;
        const s = r.steps[this.step];
        const hold = s.hold || clamp(3.2 + s.caption.length * 0.05, 5, 9);
        if (this.stepClock > hold) this.goStep(this.step + 1);
      }
    }
    this.move();
    this.flowsUpdate(dt);

    // Glow: the picked part, or this step's parts.
    const step = r.steps[this.step];
    const lit = this.picked ? this.picked.parts : step.highlight;
    const accent = new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#ff5a3d');
    const pulse = 0.6 + 0.4 * Math.sin(now * 5);
    for (const [id, mats] of Object.entries(this.glowMats)) {
      const on = lit.includes(id);
      for (const m of mats) {
        if (this.flashMats[id]) continue;
        const light = m.transparent || m.color.r * 0.3 + m.color.g * 0.59 + m.color.b * 0.11 > 0.5;
        if (on) {
          if (light) m.emissive.setHex(0xffb38a); else m.emissive.copy(accent);
          m.emissiveIntensity = (light ? 0.16 : 0.55) * pulse;
        } else {
          m.emissive.setHex(m.userData.baseEmissive);
          m.emissiveIntensity = m.userData.baseIntensity;
        }
      }
    }

    // Camera: a slow sway until they take over; it eases in when it opens.
    const cam = r.camera;
    const intro = ease((performance.now() - this.born) / 1300);
    if (!this.userCamera) {
      const mid = (cam.azimuth[0] + cam.azimuth[1]) / 2, half = (cam.azimuth[1] - cam.azimuth[0]) / 2;
      const want = mid + half * Math.sin(now * 0.22);
      this.view.az += (want - this.view.az) * Math.min(1, dt * 1.5);
    }
    const az = THREE.MathUtils.degToRad(this.view.az - 26 * (1 - intro));
    const el = THREE.MathUtils.degToRad(this.view.el);
    const dist = this.view.dist * this.fit * (1 + 0.45 * (1 - intro));
    const tgt = this.view.target;
    this.camera.position.set(tgt.x + dist * Math.cos(el) * Math.sin(az), tgt.y + dist * Math.sin(el),
                             tgt.z + dist * Math.cos(el) * Math.cos(az));
    this.camera.lookAt(tgt);
    this.root.scale.setScalar(0.82 + 0.18 * intro);
    this.scene.updateMatrixWorld(true);
    this.renderer.render(this.scene, this.camera);
    this.overlay(now, step, lit, intro);
  }

  overlay(now, step, lit, intro) {
    const W = window.innerWidth, H = window.innerHeight, s = this.scale;
    $('caption-chip').textContent = `${this.step + 1} · ${step.name}`;
    $('caption-text').textContent = step.caption;
    [...$('dots').children].forEach((d, i) => { d.className = i === this.step ? 'on' : ''; });
    let emberTarget = null;
    for (const L of this.labelEls) {
      const p = this.parts[L.l.part];
      const world = new THREE.Vector3();
      if (L.l.part && p) p.holder.getWorldPosition(world);
      world.add(new THREE.Vector3(...(L.l.anchor || [0, 0, 0])));
      const sp = world.project(this.camera);
      const x = (sp.x * 0.5 + 0.5) * W, y = (-sp.y * 0.5 + 0.5) * H;
      const lx = x + L.l.offset[0] * s, ly = y + L.l.offset[1] * s;
      const on = lit.includes(L.l.part);
      L.el.className = 'label' + (on ? ' on' : '');
      L.el.style.left = `${lx}px`;
      L.el.style.top = `${ly}px`;
      L.el.style.opacity = intro.toFixed(3);
      const rw = L.el.offsetWidth, rh = L.el.offsetHeight;
      const ex = lx < x ? lx + rw / 2 : lx - rw / 2;
      L.line.setAttribute('d', `M ${ex} ${ly} Q ${(ex + x) / 2} ${ly} ${x} ${y}`);
      L.line.setAttribute('class', on ? 'on' : '');
      L.line.style.opacity = intro.toFixed(3);
      L.dot.setAttribute('cx', x);
      L.dot.setAttribute('cy', y);
      L.dot.setAttribute('r', 6 * s);
      L.dot.setAttribute('class', on ? 'on' : '');
      if (on && !emberTarget) emberTarget = [lx - 42 * s, ly - rh / 2 - 106 * s];
    }
    if (this.picked) {  // a card is open: perch on its corner instead
      const card = $('card');
      emberTarget = [card.offsetLeft + card.offsetWidth - 96 * s, card.offsetTop - 74 * s];
    }
    const ember = $('ember');
    const frames = payload.buddy || [];
    if (emberTarget && frames.length) {
      this.emberPos = this.emberPos || emberTarget.slice();
      this.emberPos[0] += (emberTarget[0] - this.emberPos[0]) * 0.12;
      this.emberPos[1] += (emberTarget[1] - this.emberPos[1]) * 0.12;
      ember.style.left = `${this.emberPos[0]}px`;
      ember.style.top = `${this.emberPos[1] + Math.sin(now * 5) * 4 * s}px`;
      const src = frames[Math.floor(now * 6) % frames.length];
      if (ember.getAttribute('src') !== src) ember.setAttribute('src', src);
      ember.style.opacity = intro.toFixed(3);
    } else {
      ember.style.opacity = 0;
    }
  }
}

window.Clixy3D = { boot, reopen, show, pause };
