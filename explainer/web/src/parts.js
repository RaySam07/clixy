// The parts library: every shape a recipe can name. A recipe never runs code,
// it only picks parts from here, sizes them and says where they go.
import * as THREE from 'three';

export const CORAL = 0xff5a3d;

// --- Materials ---------------------------------------------------------------------
export function material(spec = {}) {
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
    case 'glow': return new THREE.MeshBasicMaterial({ color: c || 0xffd23f, transparent: true, opacity: spec.opacity ?? 1 });
    case 'glass': return new THREE.MeshStandardMaterial({ color: c || 0xbfe3ff, metalness: 0.1, roughness: 0.08,
                                                          transparent: true, opacity: spec.opacity ?? 0.22,
                                                          emissive: c || 0xbfe3ff, emissiveIntensity: 0.22,
                                                          side: THREE.DoubleSide, depthWrite: false });
    case 'ceramic': return new THREE.MeshStandardMaterial({ color: c || 0xf4f1ea, metalness: 0, roughness: 0.35 });
    default: return new THREE.MeshStandardMaterial({ color: c || CORAL, metalness: 0.15, roughness: 0.42 });
  }
}

export const v3 = (a) => new THREE.Vector3(a[0], a[1], a[2]);

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
export const BUILDERS = {
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
  // A pipe along a path: ducts, hoses, an exhaust. Glass lets flows show inside.
  pipe: (s) => {
    const curve = new THREE.CatmullRomCurve3(s.path.map(v3));
    return shadowed(new THREE.Mesh(new THREE.TubeGeometry(curve, 64, s.radius || 0.06, 16, false),
                                   material({ material: 'darkmetal', ...s })));
  },
  group: () => new THREE.Group(),
  // A cutaway wall: a thick tube with the front sliced off so you see inside.
  // arc 180 keeps the back half; radius 0 inside makes a solid slice (a fin).
  shell: (s) => {
    const ri = s.inner ?? s.radius * 0.88, ro = s.radius, h = s.height;
    const profile = [[ri, -h / 2], [ro, -h / 2], [ro, h / 2], [ri, h / 2], [ri, -h / 2]].map(([x, y]) => new THREE.Vector2(x, y));
    const arc = THREE.MathUtils.degToRad(s.arc ?? 180);
    const g = new THREE.LatheGeometry(profile, 64, Math.PI - arc / 2, arc);
    const mat = material({ material: 'metal', ...s });
    mat.side = THREE.DoubleSide;
    return shadowed(new THREE.Mesh(g, mat));
  },
  // A rod between two moving points (a connecting rod, a piston arm); the
  // runtime stretches it every frame, see updateLinks.
  link: (s) => {
    const grp = new THREE.Group();
    const rod = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(s.radius || 0.05, s.radius || 0.05, 1, 20),
                                        material({ material: 'metal', ...s })));
    grp.add(rod);
    for (const y of [-0.5, 0.5]) {
      const eye = shadowed(new THREE.Mesh(new THREE.CylinderGeometry((s.radius || 0.05) * 1.9, (s.radius || 0.05) * 1.9,
                                                                    (s.radius || 0.05) * 2.2, 24), material({ material: 'metal', ...s })));
      eye.rotation.x = Math.PI / 2;
      eye.userData.end = y;
      grp.add(eye);
    }
    return grp;
  },
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
export function buildChain(s, parts) {
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
