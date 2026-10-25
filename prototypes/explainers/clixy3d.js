// Clixy 3D explainers: a recipe (parts, motions, steps, labels) becomes a
// lit, shadowed, animated 3D scene with Clixy's labels, captions and Ember.
// No recipe runs code: it only names parts from this library and how they move.
import * as THREE from './three/build/three.module.js';

const CORAL = 0xff5a3d;

// --- Materials ---------------------------------------------------------------------
function material(spec = {}) {
  const c = spec.color ? new THREE.Color(spec.color) : null;
  switch (spec.material || 'paint') {
    case 'metal': return new THREE.MeshStandardMaterial({ color: c || 0xcfd4de, metalness: 0.85, roughness: 0.26 });
    case 'darkmetal': return new THREE.MeshStandardMaterial({ color: c || 0x515867, metalness: 0.75, roughness: 0.35 });
    case 'rubber': return new THREE.MeshStandardMaterial({ color: c || 0x1c1d24, metalness: 0, roughness: 0.92 });
    case 'water': return new THREE.MeshStandardMaterial({ color: c || 0x2f8cff, metalness: 0.15, roughness: 0.12,
                                                          transparent: true, opacity: spec.opacity ?? 0.88 });
    case 'ground': return new THREE.MeshStandardMaterial({ color: c || 0x63c46e, roughness: 0.95, flatShading: true,
                                                           vertexColors: !!spec.vertexColors });
    case 'cloud': return new THREE.MeshStandardMaterial({ color: c || 0xffffff, roughness: 1, flatShading: true });
    case 'glow': return new THREE.MeshBasicMaterial({ color: c || 0xffd23f });
    default: return new THREE.MeshStandardMaterial({ color: c || CORAL, metalness: 0.15, roughness: 0.42 });
  }
}

const v3 = (a) => new THREE.Vector3(a[0], a[1], a[2]);

function shadowed(mesh) {
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function tubeBetween(a, b, r, mat) {
  const A = v3(a), B = v3(b);
  const len = A.distanceTo(B);
  const m = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 20), mat));
  m.position.copy(A).add(B).multiplyScalar(0.5);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), B.clone().sub(A).normalize());
  return m;
}

function gearShape(radius, teeth, depth) {
  const s = new THREE.Shape();
  const n = teeth * 4;
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * Math.PI * 2;
    const r = (Math.floor(i / 2) % 2 === 0) ? radius : radius - depth;
    const x = Math.cos(a) * r, y = Math.sin(a) * r;
    if (i === 0) s.moveTo(x, y); else s.lineTo(x, y);
  }
  const hole = new THREE.Path();
  hole.absarc(0, 0, radius * 0.22, 0, Math.PI * 2, true);
  s.holes.push(hole);
  return s;
}

// Fractal-ish hills: a mountain on one side, sea level at 0.
function terrainGeometry(spec) {
  const [w, d] = spec.size;
  const g = new THREE.PlaneGeometry(w, d, 64, 48);
  g.rotateX(-Math.PI / 2);
  const pos = g.attributes.position;
  const colors = [];
  const peak = spec.peak || [w * 0.25, 0];
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    const dx = x - peak[0], dz = z - peak[1];
    const mountain = spec.height * Math.exp(-(dx * dx + dz * dz) / (2 * (w * 0.16) ** 2));
    const hills = 0.12 * Math.sin(x * 2.1) * Math.cos(z * 1.7) + 0.06 * Math.sin(x * 5.3 + z * 3.1);
    const shore = Math.max(0, (-x + w * 0.05)) * 0.18;
    const y = Math.max(-0.25, mountain + hills + shore - 0.15);
    pos.setY(i, y);
    const c = new THREE.Color(y > spec.height * 0.7 ? 0xf6f7fb : y > 0.35 ? 0x4fae5d : y > 0.02 ? 0x76c86a : 0xe9d9a6);
    colors.push(c.r, c.g, c.b);
  }
  g.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  g.computeVertexNormals();
  return g;
}

// --- Parts library -----------------------------------------------------------------
const BUILDERS = {
  tube: (s) => tubeBetween(s.from, s.to, s.radius || 0.02, material(s)),
  box: (s) => shadowed(new THREE.Mesh(new THREE.BoxGeometry(...s.size), material(s))),
  sphere: (s) => shadowed(new THREE.Mesh(new THREE.SphereGeometry(s.radius, 32, 20), material(s))),
  cylinder: (s) => shadowed(new THREE.Mesh(new THREE.CylinderGeometry(s.radius, s.radius, s.height, 32), material(s))),
  torus: (s) => shadowed(new THREE.Mesh(new THREE.TorusGeometry(s.radius, s.tube, 18, 64), material(s))),
  gear: (s) => {
    const g = new THREE.ExtrudeGeometry(gearShape(s.radius, s.teeth, s.depth || s.radius * 0.07),
                                        { depth: s.thickness || 0.01, bevelEnabled: true, bevelThickness: 0.002,
                                          bevelSize: 0.002, bevelSegments: 1 });
    g.translate(0, 0, -(s.thickness || 0.01) / 2);
    return shadowed(new THREE.Mesh(g, material({ material: 'metal', ...s })));
  },
  wheel: (s) => {
    const grp = new THREE.Group();
    grp.add(shadowed(new THREE.Mesh(new THREE.TorusGeometry(s.radius, s.radius * 0.075, 18, 72), material({ material: 'rubber' }))));
    grp.add(shadowed(new THREE.Mesh(new THREE.TorusGeometry(s.radius * 0.93, s.radius * 0.025, 10, 72), material({ material: 'metal' }))));
    const spokes = s.spokes || 24;
    for (let i = 0; i < spokes; i++) {
      const a = (i / spokes) * Math.PI * 2;
      const side = (i % 2 ? 1 : -1) * s.radius * 0.08;
      grp.add(tubeBetween([0, 0, side], [Math.cos(a) * s.radius * 0.92, Math.sin(a) * s.radius * 0.92, 0],
                          s.radius * 0.006, material({ material: 'metal' })));
    }
    const hub = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(s.radius * 0.07, s.radius * 0.07, s.radius * 0.3, 20),
                                        material({ material: 'darkmetal' })));
    hub.rotation.x = Math.PI / 2;
    grp.add(hub);
    return grp;
  },
  terrain: (s) => {
    const m = new THREE.Mesh(terrainGeometry(s), material({ material: 'ground', vertexColors: true, color: '#ffffff' }));
    m.receiveShadow = true; m.castShadow = true;
    return m;
  },
  water: (s) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(s.size[0], s.size[1], 1, 1), material({ material: 'water', ...s }));
    m.rotation.x = -Math.PI / 2; m.receiveShadow = true;
    return m;
  },
  cloud: (s) => {
    const grp = new THREE.Group();
    const puffs = [[0, 0, 0, 1], [0.8, -0.1, 0.1, 0.8], [-0.8, -0.15, 0, 0.75], [0.35, 0.35, -0.1, 0.7], [-0.4, 0.3, 0.2, 0.65]];
    for (const [x, y, z, r] of puffs) {
      const m = shadowed(new THREE.Mesh(new THREE.IcosahedronGeometry(r * s.scale, 1), material({ material: 'cloud', ...s })));
      m.position.set(x * s.scale, y * s.scale, z * s.scale);
      grp.add(m);
    }
    return grp;
  },
  river: (s) => {
    const curve = new THREE.CatmullRomCurve3(s.path.map(v3));
    const m = new THREE.Mesh(new THREE.TubeGeometry(curve, 64, s.radius || 0.06, 10, false), material({ material: 'water', opacity: 0.95, ...s }));
    m.receiveShadow = true;
    return m;
  },
  group: () => new THREE.Group(),
  helix: (s) => {
    // A DNA-style double helix along y. mode "strands": the two backbones;
    // mode "pairs": the rungs, only those whose pair matches s.pairs (AT or GC).
    const grp = new THREE.Group();
    const turns = s.turns || 2, r = s.radius || 0.5, h = s.height || 3, n = s.steps || 20;
    const strand = (phase) => new THREE.CatmullRomCurve3([...Array(121).keys()].map((i) => {
      const u = i / 120, a = u * turns * Math.PI * 2 + phase;
      return new THREE.Vector3(Math.cos(a) * r, u * h - h / 2, Math.sin(a) * r);
    }));
    if ((s.mode || 'strands') === 'strands') {
      for (const [phase, color] of [[0, '#ff5a3d'], [Math.PI, '#ffd23f']]) {
        grp.add(shadowed(new THREE.Mesh(new THREE.TubeGeometry(strand(phase), 240, r * 0.09, 12, false),
                                        material({ color }))));
      }
    } else {
      const seq = s.sequence || 'ATGCGTACCGATTGCAATCG';
      for (let i = 0; i < n; i++) {
        const base = seq[i % seq.length];
        const kind = base === 'A' || base === 'T' ? 'AT' : 'GC';
        if (kind !== s.pairs) continue;
        const u = (i + 0.5) / n, a = u * turns * Math.PI * 2;
        const p1 = [Math.cos(a) * r, u * h - h / 2, Math.sin(a) * r];
        const p2 = [Math.cos(a + Math.PI) * r, u * h - h / 2, Math.sin(a + Math.PI) * r];
        const mid = [(p1[0] + p2[0]) / 2, p1[1], (p1[2] + p2[2]) / 2];
        const colors = kind === 'AT' ? ['#4aa3ff', '#2fbf71'] : ['#9b7bff', '#ff9f43'];
        grp.add(tubeBetween(p1, mid, r * 0.07, material({ color: colors[0] })));
        grp.add(tubeBetween(mid, p2, r * 0.07, material({ color: colors[1] })));
      }
    }
    return grp;
  },
  sun: (s) => {
    const grp = new THREE.Group();
    grp.add(new THREE.Mesh(new THREE.SphereGeometry(s.radius, 32, 20), material({ material: 'glow', color: '#ffd23f' })));
    const halo = new THREE.Mesh(new THREE.SphereGeometry(s.radius * 1.5, 32, 20),
                                new THREE.MeshBasicMaterial({ color: 0xffb340, transparent: true, opacity: 0.25 }));
    grp.add(halo);
    return grp;
  },
};

// Chain: links travelling around two sprockets (open belt), from their radii.
function buildChain(s, parts) {
  const ring = parts[s.ring], cog = parts[s.cog];
  const A = ring.spec.at, B = cog.spec.at;
  const ra = ring.spec.radius * 0.96, rb = cog.spec.radius * 0.96;
  const z = A[2];
  const dx = B[0] - A[0], dy = B[1] - A[1];
  const d = Math.hypot(dx, dy);
  const base = Math.atan2(dy, dx);
  const phi = Math.acos((ra - rb) / d);
  const pts = [];
  // Ring wrap (far side), straight run, cog wrap, straight run back.
  const arc = (cx, cy, r, a0, a1, n) => { for (let i = 0; i <= n; i++) { const a = a0 + (a1 - a0) * i / n; pts.push(new THREE.Vector3(cx + r * Math.cos(a), cy + r * Math.sin(a), z)); } };
  arc(A[0], A[1], ra, base + phi, base + 2 * Math.PI - phi, 60);
  arc(B[0], B[1], rb, base - phi, base + phi, 30);
  pts.push(pts[0].clone());
  const curve = new THREE.CatmullRomCurve3(pts, true, 'centripetal');
  const length = curve.getLength();
  const n = Math.round(length / (s.pitch || 0.0127));
  const link = new THREE.BoxGeometry(0.011, 0.006, 0.012);
  const mesh = new THREE.InstancedMesh(link, material({ material: 'darkmetal' }), n);
  mesh.castShadow = true;
  return { mesh, curve, n, length, ra };
}

// --- The runtime --------------------------------------------------------------------
export async function start(recipe) {
  window.recipeDuration = recipe.duration;
  const W = 1920, H = 1080;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setSize(W, H);
  renderer.setPixelRatio(1);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  document.getElementById('stage').appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const cam = recipe.camera;
  const camera = new THREE.PerspectiveCamera(cam.fov || 32, W / H, 0.01, 200);

  // Studio light: warm key with soft shadows, cool fill, a coral rim (Clixy's colour).
  scene.add(new THREE.HemisphereLight(0xfff4e6, 0x30324a, 1.1));
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
  if (recipe.floor !== false) {
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.ShadowMaterial({ opacity: 0.42 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = recipe.floorY ?? 0;
    floor.receiveShadow = true;
    scene.add(floor);
  }

  // Parts, then chains and attachments.
  const parts = {};
  for (const spec of recipe.parts) {
    if (spec.type === 'chain' || spec.type === 'particles') continue;
    const obj = BUILDERS[spec.type](spec);
    const holder = new THREE.Group();
    holder.add(obj);
    if (spec.at && spec.type !== 'tube') holder.position.set(...spec.at);
    if (spec.axis === 'x') obj.rotation.y = Math.PI / 2;
    if (spec.axis === 'y') obj.rotation.x = Math.PI / 2;
    if (spec.rotation) obj.rotation.set(...spec.rotation);
    parts[spec.id] = { spec, holder, obj, angle: 0 };
  }
  for (const p of Object.values(parts)) {
    const parent = p.spec.attach ? parts[p.spec.attach].holder : scene;
    parent.add(p.holder);
  }
  const chains = recipe.parts.filter((s) => s.type === 'chain').map((s) => ({ spec: s, ...buildChain(s, parts) }));
  for (const c of chains) { scene.add(c.mesh); parts[c.spec.id] = { spec: c.spec, holder: c.mesh, obj: c.mesh }; }

  // Particles: dots that flow along a path in their time window.
  const flows = recipe.parts.filter((s) => s.type === 'particles').map((s) => {
    const curve = new THREE.CatmullRomCurve3(s.path.map(v3));
    const geo = new THREE.SphereGeometry(s.size || 0.03, 10, 8);
    const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(s.color || '#4aa3ff'), roughness: 0.3,
                                                transparent: true, opacity: 0.95 });
    const mesh = new THREE.InstancedMesh(geo, mat, s.count || 24);
    scene.add(mesh);
    parts[s.id] = { spec: s, holder: mesh, obj: mesh };
    return { spec: s, curve, mesh, mat };
  });

  // Highlight: every material of a part can glow coral.
  const glowMats = {};
  for (const [id, p] of Object.entries(parts)) {
    if (p.spec.type === 'particles') continue;  // flows show in their own colour, only in their step
    const mats = [];
    p.obj.traverse((o) => { if (o.material && o.material.emissive) { o.material = o.material.clone(); mats.push(o.material); } });
    glowMats[id] = mats;
  }

  // Overlay: title, steps, labels, caption, Ember.
  const $ = (id) => document.getElementById(id);
  $('title').textContent = recipe.title;
  $('subtitle').textContent = recipe.subtitle || '';
  const dots = $('dots');
  recipe.steps.forEach(() => dots.appendChild(document.createElement('i')));
  const svg = $('leaders');
  const labelEls = recipe.labels.map((l) => {
    const el = document.createElement('div');
    el.className = 'label';
    el.textContent = l.text;
    $('labels').appendChild(el);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    svg.appendChild(line);
    const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    dot.setAttribute('r', 6);
    svg.appendChild(dot);
    return { l, el, line, dot };
  });
  const ember = $('ember');
  let emberPos = null;

  function stepAt(t) {
    const total = recipe.duration;
    const tt = t % total;
    let i = recipe.steps.findIndex((s) => tt >= s.from && tt < s.to);
    return i < 0 ? 0 : i;
  }

  function speedOf(id, seen = new Set()) {
    const m = recipe.motions.find((x) => x.part === id && x.rotate);
    if (!m || seen.has(id)) return 0;
    seen.add(id);
    if (m.rotate.follow) return speedOf(m.rotate.follow, seen) * (m.rotate.ratio || 1);
    return m.rotate.rps;
  }

  window.renderAt = (t) => {
    // Motions.
    for (const m of recipe.motions) {
      const p = parts[m.part];
      if (!p) continue;
      if (m.rotate) {
        const ax = m.rotate.axis || 'z';
        p.holder.rotation[ax] = -2 * Math.PI * speedOf(m.part) * t + (m.rotate.phase || 0);
      }
      if (m.drift) {
        const base = p.spec.at;
        const k = Math.sin((2 * Math.PI * t) / m.drift.period);
        p.holder.position.set(base[0] + m.drift.axis[0] * m.drift.amplitude * k,
                              base[1] + m.drift.axis[1] * m.drift.amplitude * k,
                              base[2] + m.drift.axis[2] * m.drift.amplitude * k);
      }
      if (m.bob) p.holder.position.y = p.spec.at[1] + Math.sin(t * m.bob.speed) * m.bob.amplitude;
      if (m.orbit) {
        const a = (2 * Math.PI * t) / m.orbit.period + (m.orbit.phase || 0);
        const c = m.orbit.center || [0, 0, 0];
        p.holder.position.set(c[0] + Math.cos(a) * m.orbit.radius, c[1], c[2] - Math.sin(a) * m.orbit.radius);
      }
    }
    // Chains run at the ring's surface speed.
    const dummy = new THREE.Object3D();
    for (const c of chains) {
      const surface = 2 * Math.PI * c.ra * speedOf(c.spec.ring);
      const shift = (surface * t) / c.length;
      for (let i = 0; i < c.n; i++) {
        const u = (((i / c.n - shift) % 1) + 1) % 1;
        const pt = c.curve.getPointAt(u);
        const tan = c.curve.getTangentAt(u);
        dummy.position.copy(pt);
        dummy.rotation.set(0, 0, Math.atan2(tan.y, tan.x));
        dummy.updateMatrix();
        c.mesh.setMatrixAt(i, dummy.matrix);
      }
      c.mesh.instanceMatrix.needsUpdate = true;
    }
    // Flows: on in their window, fading in and out.
    const tt = t % recipe.duration;
    for (const f of flows) {
      const [a, b] = f.spec.window || [0, recipe.duration];
      const strength = Math.max(0, Math.min(1, (tt - a) / 0.4, (b - tt) / 0.4));
      f.mesh.visible = strength > 0.01;
      f.mat.opacity = 0.95 * strength;
      const n = f.spec.count || 24;
      for (let i = 0; i < n; i++) {
        const u = ((t * (f.spec.speed || 0.25) + i / n) % 1 + 1) % 1;
        const pt = f.curve.getPointAt(u);
        const jit = f.spec.spread || 0;
        dummy.position.set(pt.x + Math.sin(i * 12.9) * jit, pt.y + Math.cos(i * 7.3) * jit * 0.3, pt.z + Math.sin(i * 4.1) * jit);
        const sc = Math.min(1, u * 8, (1 - u) * 8);
        dummy.scale.setScalar(Math.max(0.05, sc));
        dummy.rotation.set(0, 0, 0);
        dummy.updateMatrix();
        f.mesh.setMatrixAt(i, dummy.matrix);
      }
      f.mesh.instanceMatrix.needsUpdate = true;
    }
    // Step: glow its parts.
    const si = stepAt(t);
    const step = recipe.steps[si];
    const pulse = 0.55 + 0.45 * Math.sin(t * 6);
    for (const [id, mats] of Object.entries(glowMats)) {
      const on = step.highlight.includes(id);
      for (const m of mats) {
        // Light parts (clouds, snow) get a gentle warm glow, not a coral wash.
        const light = m.transparent || m.color.r * 0.3 + m.color.g * 0.59 + m.color.b * 0.11 > 0.5;
        m.emissive.setHex(light ? 0xffb38a : CORAL);
        m.emissiveIntensity = on ? (light ? 0.16 : 0.55) * pulse : 0;
      }
    }
    // Camera: a slow orbit.
    const k = Math.min(1, t / recipe.duration);
    const az = THREE.MathUtils.degToRad(cam.azimuth[0] + (cam.azimuth[1] - cam.azimuth[0]) * (0.5 - 0.5 * Math.cos(Math.PI * k)));
    const el = THREE.MathUtils.degToRad(cam.elevation);
    const tgt = v3(cam.target);
    camera.position.set(tgt.x + cam.distance * Math.cos(el) * Math.sin(az), tgt.y + cam.distance * Math.sin(el),
                        tgt.z + cam.distance * Math.cos(el) * Math.cos(az));
    camera.lookAt(tgt);
    scene.updateMatrixWorld(true);
    renderer.render(scene, camera);

    // Overlay.
    $('caption-chip').textContent = `${si + 1} · ${step.name}`;
    $('caption-text').textContent = step.caption;
    [...dots.children].forEach((d, i) => d.className = i === si ? 'on' : '');
    const local = (tt - step.from) / Math.max(0.01, step.to - step.from);
    $('caption').style.opacity = Math.min(1, local * 8).toFixed(3);
    let emberTarget = null;
    for (const L of labelEls) {
      const p = parts[L.l.part];
      // Anchor: an offset in world space from where the part is, so a label
      // on a spinning part stays put while the part turns under it.
      const world = new THREE.Vector3();
      if (L.l.part && p) p.holder.getWorldPosition(world);
      world.add(new THREE.Vector3(...(L.l.anchor || [0, 0, 0])));
      const s = world.project(camera);
      const x = (s.x * 0.5 + 0.5) * W, y = (-s.y * 0.5 + 0.5) * H;
      const lx = x + L.l.offset[0], ly = y + L.l.offset[1];
      const on = step.highlight.includes(L.l.part);
      L.el.className = 'label' + (on ? ' on' : '');
      L.el.style.left = `${lx}px`;
      L.el.style.top = `${ly}px`;
      const r = L.el.getBoundingClientRect();
      const ex = lx < x ? lx + r.width / 2 : lx - r.width / 2;
      L.line.setAttribute('d', `M ${ex} ${ly} Q ${(ex + x) / 2} ${ly} ${x} ${y}`);
      L.line.setAttribute('class', on ? 'on' : '');
      L.dot.setAttribute('cx', x);
      L.dot.setAttribute('cy', y);
      L.dot.setAttribute('class', on ? 'on' : '');
      if (on && !emberTarget) emberTarget = [lx - 42, ly - r.height / 2 - 106];  // perched on the label
    }
    if (emberTarget) {
      emberPos = emberPos || emberTarget.slice();
      emberPos[0] += (emberTarget[0] - emberPos[0]) * 0.12;
      emberPos[1] += (emberTarget[1] - emberPos[1]) * 0.12;
      ember.style.left = `${emberPos[0]}px`;
      ember.style.top = `${emberPos[1] + Math.sin(t * 5) * 4}px`;
      ember.src = Math.floor(t * 10) % 2 ? 'ember.png' : 'ember2.png';
    }
  };
  window.renderAt(0);
  window.ready = true;
}
