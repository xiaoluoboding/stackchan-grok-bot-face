// Independent StackChan skin. Upstream player, recipes and firmware are unchanged.
import { makeConfig, createRuntime, createStateMachine, buildModel } from '../../third_party/grokbot/grok-player.js';

const theme = { ink: [0, 0, 0], spot: [1, 1, 1], cutout: false };
const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const NS = 'http://www.w3.org/2000/svg';

export function adaptRecipes(source) {
  const data = structuredClone(source);
  // Morphs hide the eyes inside the upstream runtime: replace the entire state
  // recipe, rather than trying to recover an already invisible eye path.
  const replacements = {
    thinking: ['curious', 'inspecting'], searching: ['working', 'inspecting'],
    waiting: ['idle', 'calm'], orbit: ['curious', 'inspecting'],
    radar: ['working', 'inspecting'], progress: ['working', 'calm'],
    spawning: ['waking', 'surprised'], loading: ['working', 'calm'],
    dictating: ['streaming', 'calm'], sending: ['streaming', 'neutral'],
    receiving: ['listening', 'calm'], uploading: ['working', 'neutral'],
    writing: ['working', 'inspecting'], alerting: ['surprised', 'surprised'],
    bouncing: ['happy', 'happy'], 'powering-down': ['sleeping', 'sleepy'],
  };
  for (const [name, state] of Object.entries(data.states)) {
    if (!state.morph) continue;
    const [base, face] = replacements[name] ?? ['idle', 'neutral'];
    data.states[name] = { ...structuredClone(data.states[base]), face };
    delete data.states[name].morph;
  }
  return data;
}

export function createWhiteEyes(container, opts = {}) {
  const doc = container.ownerDocument, win = doc.defaultView;
  const size = opts.size ?? 320;
  let scale = clamp(opts.scale ?? 1.8, 1.2, 2.2);
  let thickness = clamp(opts.thickness ?? 1.15, 1, 1.5);
  let runtime, machine, config, raf = 0, destroyed = false, error = null;
  let follow = opts.follow ?? false, previous = 0, elapsed = 0;
  let state = opts.state ?? 'idle';
  const frozen = opts.frozenAt != null;
  const reduced = opts.reducedMotion ?? win.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const svg = doc.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
  svg.setAttribute('width', size); svg.setAttribute('height', size);
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'StackChan 透明底白眼');
  svg.style.cssText = 'display:block;width:100%;height:100%;background:transparent';
  const group = doc.createElementNS(NS, 'g');
  const eyes = doc.createElementNS(NS, 'path'); eyes.setAttribute('fill', '#fff');
  group.append(eyes); svg.append(group); container.append(svg);

  function render() {
    const frame = runtime.frame();
    const adapted = { ...frame, morph: null, bodies: [{ body: 'sphere', weight: 1 }], glow: null,
      eyes: frame.eyes?.map(eye => ({ ...eye, wid: eye.wid * thickness })) };
    const model = buildModel(adapted, theme, size, config);
    eyes.setAttribute('d', model.eyesD);
    // Without the sphere silhouette, large clip turns need a screen-safe envelope.
    // Keep eye contours intact and bound the pair's displacement and scale.
    const bounds = eyes.getBBox();
    const safeScale = Math.min(scale, size * .84 / Math.max(bounds.width, 1), size * .84 / Math.max(bounds.height, 1));
    const cx = bounds.x + bounds.width/2, cy = bounds.y + bounds.height/2;
    const targetX = size/2 + clamp((cx-size/2)*safeScale, -size*.10, size*.10);
    const targetY = size/2 + clamp((cy-size/2)*safeScale, -size*.10, size*.10);
    const x = clamp(targetX, size*.08+bounds.width*safeScale/2, size*.92-bounds.width*safeScale/2);
    const y = clamp(targetY, size*.08+bounds.height*safeScale/2, size*.92-bounds.height*safeScale/2);
    group.setAttribute('transform', `translate(${x} ${y}) scale(${safeScale}) translate(${-cx} ${-cy})`);
  }
  function advance(dt) { machine.advance(dt); runtime.advance(dt); elapsed += dt; }
  function tick(now) {
    if (destroyed) return;
    if (!doc.hidden) {
      const dt = Math.min((now - previous) / 1000, .05);
      advance(dt); render();
    }
    previous = now; raf = win.requestAnimationFrame(tick);
  }
  function refresh() {
    if (!runtime || destroyed) return;
    if (reduced) for (let i = 0; i < 60; i++) advance(.1);
    render();
  }
  function pointer(event) {
    if (!follow || !runtime || frozen || reduced) return;
    const r = svg.getBoundingClientRect();
    runtime.setGaze([clamp((event.clientX-r.left)/r.width*2-1, -1, 1), clamp((event.clientY-r.top)/r.height*2-1, -1, 1)]);
  }
  container.addEventListener('pointermove', pointer);
  const clearGaze = () => runtime?.setGaze(null);
  container.addEventListener('pointerleave', clearGaze);
  const ready = (async () => {
    const source = opts.data ?? await fetch(new URL('../../third_party/grokbot/grok-recipes.json', import.meta.url)).then(r => {
      if (!r.ok) throw new Error(`recipes HTTP ${r.status}`); return r.json();
    });
    if (destroyed) return;
    config = makeConfig(adaptRecipes(source));
    if (!config.states[state]) throw new Error(`Unknown state: ${state}`);
    runtime = createRuntime(config, { body: 'sphere', isGazeAlways: follow });
    machine = createStateMachine(config, runtime, state, { seed: opts.seed ?? 23, firstPhase: 0 });
    if (frozen) for (let i = 0; i < Math.round(opts.frozenAt * 60); i++) advance(1/60);
    refresh();
    if (!frozen && !reduced) { previous = win.performance.now(); raf = win.requestAnimationFrame(tick); }
  })().catch(e => { error = e; throw e; });
  return {
    ready, el: svg, get error() { return error; }, get state() { return machine?.state ?? state; },
    setState(value) {
      if (!config?.states[value]) throw new Error(`Unknown or not ready state: ${value}`);
      state = value; machine.setState(value); refresh();
    },
    blink() { runtime?.blink(); refresh(); },
    setExpression(value) { runtime?.setExpression(value); refresh(); },
    setFollow(value) { follow = !!value; runtime?.setGazeAlways(follow); if (!follow) clearGaze(); },
    setScale(value) { scale = clamp(Number(value) || 1.8, 1.2, 2.2); refresh(); },
    setThickness(value) { thickness = clamp(Number(value) || 1.15, 1, 1.5); refresh(); },
    step(n = 1) { if (!frozen || !runtime || destroyed) return; for (let i=0;i<n;i++) advance(1/60); render(); },
    debugFrame() { return runtime ? { state: machine.state, clip: machine.clip, time: elapsed, duration: runtime.duration, scale, thickness } : null; },
    destroy() { destroyed = true; win.cancelAnimationFrame(raf); container.removeEventListener('pointermove', pointer); container.removeEventListener('pointerleave', clearGaze); svg.remove(); },
  };
}
