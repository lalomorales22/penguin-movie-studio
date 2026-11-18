// The Music studio's sound engine (from LilyKeys): sampled grand, sampled General MIDI instruments (the
// orchestra and band), synth voices, drum synth, recorded drums and percussion (when the Models page has fetched
// them), MIDI file reader, score player. Needs instruments.js first.
//
// A score is played the way it is written, not the way plain MIDI flattens it: the app reads the dynamics,
// hairpins, accents and rolls out of the score (music.py, perform.ily) and this engine follows them, so a held
// chord under a crescendo really swells. On top of the instruments there is a film-sound layer (drones, risers,
// hits, a heartbeat, wind, rain ...) and a hall for everything to sound in.
//
// makeSound(AC, isLive) builds one engine on an audio context. The pages use the live one below (Sound).
// render.js builds another on an OfflineAudioContext to play a whole piece into an audio file.
function makeSound(AC, isLive) {
  // Random numbers that come out the same every time, so a piece sounds the same each time it is made.
  const rng = seed => {
    let a = (seed >>> 0) || 1;
    return () => { a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  const master = AC.createGain();
  const comp = AC.createDynamicsCompressor();
  comp.threshold.value = -14; comp.ratio.value = 4;
  master.connect(comp); comp.connect(AC.destination);

  // Keys chain: voices -> drive -> filter -> (+chorus) -> out.  Drums: hits -> crunch -> tone -> out.
  const lowpass = f => { const n = AC.createBiquadFilter(); n.type = 'lowpass'; n.frequency.value = f; return n; };
  const filter = (type, f, q) => { const n = AC.createBiquadFilter(); n.type = type; n.frequency.value = f; if (q != null) n.Q.value = q; return n; };
  const gain = v => { const n = AC.createGain(); n.gain.value = v; return n; };
  const panner = v => { const n = AC.createStereoPanner ? AC.createStereoPanner() : AC.createGain(); if (n.pan) n.pan.value = clamp(v, -1, 1); return n; };
  const shaper = () => { const n = AC.createWaveShaper(); n.oversample = '2x'; return n; };
  const driveCurve = amt => {
    if (amt < 0.01) return null;
    const n = 1024, c = new Float32Array(n), k = amt * amt * 60;
    for (let i = 0; i < n; i++) { const x = i / (n - 1) * 2 - 1; c[i] = (1 + k) * x / (1 + k * Math.abs(x)); }
    return c;
  };
  const instBus = gain(1), instDrive = shaper(), instFilter = lowpass(18000), instOut = gain(1);
  const chorusDelay = AC.createDelay(0.1), chorusMix = gain(0), lfo = AC.createOscillator(), lfoDepth = gain(0.004);
  chorusDelay.delayTime.value = 0.022; lfo.frequency.value = 0.7; lfo.connect(lfoDepth); lfoDepth.connect(chorusDelay.delayTime); lfo.start();
  instBus.connect(instDrive); instDrive.connect(instFilter); instFilter.connect(instOut); instOut.connect(master);
  instFilter.connect(chorusDelay); chorusDelay.connect(chorusMix); chorusMix.connect(instOut);

  const drumBus = gain(1), drumDrive = shaper(), drumTone = lowpass(20000), drumOut = gain(0.9);
  drumBus.connect(drumDrive); drumDrive.connect(drumTone); drumTone.connect(drumOut); drumOut.connect(master);

  // One hall for everything. Besides the Room knobs, each instrument of a score sends a little extra of its own
  // (depthBus): strings, brass and choir sit further back than a piano does.
  const verb = AC.createConvolver(), keysRoom = gain(0.18), drumRoom = gain(0), depthBus = gain(1);
  instOut.connect(keysRoom); keysRoom.connect(verb); drumOut.connect(drumRoom); drumRoom.connect(verb);
  depthBus.connect(verb); verb.connect(master);

  const echo = AC.createDelay(2), echoTone = lowpass(3200), echoFb = gain(0.35), keysEcho = gain(0), drumEcho = gain(0);
  echo.delayTime.value = 0.33;
  instOut.connect(keysEcho); keysEcho.connect(echo); drumOut.connect(drumEcho); drumEcho.connect(echo);
  echo.connect(echoTone); echoTone.connect(echoFb); echoFb.connect(echo); echoTone.connect(master);

  let knobTone = 20000, tune = 1;
  const levels = {};
  const applyTone = () => { drumTone.frequency.value = Math.min(knobTone, kit.tone); };
  const DESK = {
    bright: v => instFilter.frequency.setTargetAtTime(v, AC.currentTime, 0.02),
    reso: v => { instFilter.Q.value = v; },
    kdrive: v => { instDrive.curve = driveCurve(v); },
    chorus: v => { chorusMix.gain.value = v * 0.8; },
    echo: v => { keysEcho.gain.value = v * 0.7; },
    fb: v => { echoFb.gain.value = Math.min(0.9, v); },
    room: v => { keysRoom.gain.value = v * 0.9; depthBus.gain.value = v / 0.2; },
    dlevel: v => { drumOut.gain.value = v; },
    dtone: v => { knobTone = v; applyTone(); },
    ddrive: v => { drumDrive.curve = driveCurve(v); },
    dtune: v => { tune = Math.pow(2, v / 12); },
    droom: v => { drumRoom.gain.value = v * 0.8; },
    decho: v => { drumEcho.gain.value = v * 0.7; },
    echoTime: v => echo.delayTime.setTargetAtTime(Math.max(0.03, Math.min(1.9, v)), AC.currentTime, 0.05),
    film: v => { filmLevel = v; },
  };
  function setDesk(id, v) {
    if (id.startsWith('mix_')) levels[id.slice(4)] = v;
    else if (DESK[id]) DESK[id](v);
  }

  // ----- The space the music sounds in -----
  // Each space is built from noise: a short burst of early echoes, then a tail whose low, middle and high
  // parts die away at their own speed (highs first, as in a real room).
  const SPACES = {
    room: { label: 'Small room', len: 1.3, pre: 0.004, build: 0.004, low: 0.8, mid: 0.7, high: 0.4, lowGain: 1.6, highGain: 0.5, early: 8, span: 0.03, earlyGain: 2.5 },
    hall: { label: 'Concert hall', len: 3.4, pre: 0.02, build: 0.012, low: 2.4, mid: 2.1, high: 1.1, lowGain: 2.0, highGain: 0.5, early: 10, span: 0.07, earlyGain: 3.5 },
    cathedral: { label: 'Cathedral', len: 6.5, pre: 0.035, build: 0.03, low: 5.0, mid: 4.3, high: 1.8, lowGain: 2.2, highGain: 0.45, early: 12, span: 0.12, earlyGain: 3.0 },
  };
  const spaceBuffers = {};
  function spaceBuffer(kind) {
    if (spaceBuffers[kind]) return spaceBuffers[kind];
    const P = SPACES[kind], sr = AC.sampleRate, len = Math.floor(sr * P.len), buf = AC.createBuffer(2, len, sr);
    const a1 = 1 - Math.exp(-2 * Math.PI * 250 / sr), a2 = 1 - Math.exp(-2 * Math.PI * 4000 / sr), pre = Math.floor(P.pre * sr);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch), r = rng(977 + ch * 131 + len), both = rng(555 + len), e = rng(31 + ch * 17 + len);
      let lp1 = 0, lp2 = 0;
      for (let i = pre; i < len; i++) {
        const t = (i - pre) / sr, x = 0.8 * (r() * 2 - 1) + 0.6 * (both() * 2 - 1);
        lp1 += a1 * (x - lp1); lp2 += a2 * (x - lp2);
        d[i] = (1 - Math.exp(-t / P.build)) * (lp1 * P.lowGain * Math.exp(-6.91 * t / P.low) + (lp2 - lp1) * Math.exp(-6.91 * t / P.mid) + (x - lp2) * P.highGain * Math.exp(-6.91 * t / P.high));
      }
      for (let k = 0; k < P.early; k++) {
        const at = pre + Math.floor((0.004 + e() * P.span) * sr);
        if (at < len) d[at] += P.earlyGain * (0.9 - 0.6 * k / P.early) * (e() < 0.5 ? -1 : 1);
      }
    }
    return (spaceBuffers[kind] = buf);
  }
  let space = '';
  function setSpace(kind) {
    kind = SPACES[kind] ? kind : 'hall';
    if (kind !== space) { space = kind; verb.buffer = spaceBuffer(kind); }
  }
  setSpace('hall');

  const noise = AC.createBuffer(1, AC.sampleRate * 3, AC.sampleRate), wideNoise = AC.createBuffer(2, AC.sampleRate * 3, AC.sampleRate);
  { const d = noise.getChannelData(0), r = rng(4711); for (let i = 0; i < d.length; i++) d[i] = r() * 2 - 1; }
  for (let ch = 0; ch < 2; ch++) { const d = wideNoise.getChannelData(ch), r = rng(4712 + ch); for (let i = 0; i < d.length; i++) d[i] = r() * 2 - 1; }

  const wake = () => { if (isLive && AC.state !== 'running') AC.resume(); };
  if (isLive) ['pointerdown', 'keydown'].forEach(ev => window.addEventListener(ev, wake, { capture: true }));

  // ----- How loud is piano, how loud is forte -----
  // A level runs from 0 (silence) to 1 (as loud as it gets); the score's markings are placed on it in music.py
  // (pp 0.28, mf 0.62, ff 0.87). Soft is also darker, the way real instruments are.
  const MF = 0.62, GAIN0 = 1.55;
  const levelGain = L => Math.pow(10, -1.2 * Math.pow(1 - clamp(L, 0, 1), 1.15));   // 24 dB between silence and full
  const levelTone = L => L >= 0.72 ? 19000 : 19000 * Math.pow(2, -(0.72 - L) * 6.2);
  const ACCENT = { accent: 1.35, marcato: 1.6, sfz: 1.9 };
  function levelAt(line, tick) {
    if (!line.length) return MF;
    let i = 0;
    while (i + 1 < line.length && line[i + 1][0] <= tick) i++;
    const a = line[i], b = line[i + 1];
    if (tick <= a[0]) return a[1];
    return b && b[2] && b[0] > a[0] ? a[1] + (b[1] - a[1]) * (tick - a[0]) / (b[0] - a[0]) : a[1];
  }
  // How loud a note of the score starts and how that changes while it sounds, or null when the score said
  // nothing we could read about its staff (then the note plays at the one loudness the MIDI file gives it).
  function noteShape(song, n) {
    const d = song.dyn && song.dyn[n.tr];
    if (!d) return null;
    const line = d.line, level = levelAt(line, n.tick), pts = [];
    let prev = level;
    for (const e of line) {
      if (e[0] <= n.tick) continue;
      if (e[0] >= n.tend) break;
      if (e[2]) pts.push([e[0], e[1], 0]); else pts.push([e[0], prev, 0], [e[0], e[1], 0.03]);
      prev = e[1];
    }
    const last = levelAt(line, n.tend - 1);   // a dynamic that begins with the next note is not this note's
    if (Math.abs(last - prev) > 0.004) pts.push([n.tend, last, 0]);
    const near = map => map.get(n.tick) ?? map.get(n.tick - 1) ?? map.get(n.tick + 1);
    return { level, pts: pts.length ? pts : null, mark: near(d.marks), roll: near(d.rolls) };
  }

  // ----- Sampled grand piano (one sample every minor third) -----
  const samples = {};
  const NAMES = ['A', 'C', 'Ds', 'Fs'];
  let samplesReady = false;
  async function loadPiano(onProgress) {
    const jobs = [];
    for (let m = 21; m <= 108; m += 3) {
      const i = (m - 21) / 3, name = NAMES[i % 4] + (Math.floor((i + 3) / 4));
      jobs.push(fetch(`samples/${name}.mp3`).then(r => { if (!r.ok) throw 0; return r.arrayBuffer(); })
        .then(b => AC.decodeAudioData(b)).then(buf => { samples[m] = buf; onProgress && onProgress(); }).catch(() => {}));
    }
    await Promise.all(jobs);
    samplesReady = Object.keys(samples).length > 20;
    return samplesReady;
  }

  // o.level (with o.boost for an accent) plays the note as loud as the score says; without it, vel decides.
  function pianoVoice(m, vel, t, o = {}) {
    let s = 21 + 3 * Math.round((m - 21) / 3);
    s = Math.max(21, Math.min(108, s));
    const buf = samples[s], scored = o.level != null;
    if (!buf) return synthVoice(PRESETS.epiano, m, scored ? clamp(levelGain(o.level) * 2.2 * (o.boost || 1), 0.05, 1.2) : vel, t, o.out);
    const src = AC.createBufferSource(), lp = AC.createBiquadFilter(), g = AC.createGain();
    src.buffer = buf; src.playbackRate.value = Math.pow(2, (m - s) / 12);
    lp.type = 'lowpass'; lp.frequency.value = scored ? 700 + o.level * o.level * 16000 : 700 + vel * vel * 15000;
    g.gain.value = scored ? 1.5 * levelGain(o.level) * (o.boost || 1) : 0.2 + vel * vel * 1.2;
    src.connect(lp); lp.connect(g); g.connect(o.out || instBus);
    src.start(t);
    return {
      stop(tr, fast, fade) {
        tr = Math.max(tr, t + 0.02);
        const tc = fade || (fast ? 0.02 : 0.11);
        g.gain.cancelScheduledValues(tr); g.gain.setTargetAtTime(0, tr, tc);
        try { src.stop(tr + tc * 8); } catch { /* already stopped */ }
      },
    };
  }

  // ----- Synth voices -----
  const PRESETS = {
    epiano: { label: 'Electric piano', vol: 0.5, a: 0.004, d: 2.2, s: 0.12, r: 0.3, oscs: [{ type: 'sine' }, { type: 'sine', ratio: 2, gain: 0.12 }], fm: { ratio: 1, index: 1.5, decay: 0.7, sustain: 0.12 } },
    organ: { label: 'Drawbar organ', vol: 0.2, a: 0.012, d: 0.1, s: 1, r: 0.07, oscs: [{ type: 'sine', ratio: 0.5, gain: 0.6 }, { type: 'sine' }, { type: 'sine', ratio: 2, gain: 0.6 }, { type: 'sine', ratio: 3, gain: 0.35 }, { type: 'sine', ratio: 4, gain: 0.22 }, { type: 'sine', ratio: 6, gain: 0.1 }] },
    strings: { label: 'String section', vol: 0.14, a: 0.22, d: 0.6, s: 0.9, r: 0.55, oscs: [{ type: 'sawtooth', detune: -9 }, { type: 'sawtooth' }, { type: 'sawtooth', detune: 9 }], filter: { from: 2400, q: 0.5 } },
    pad: { label: 'Warm pad', vol: 0.2, a: 0.55, d: 1.5, s: 0.8, r: 1.3, oscs: [{ type: 'triangle', detune: -6 }, { type: 'triangle', detune: 6 }, { type: 'sawtooth', ratio: 0.5, gain: 0.3 }], filter: { from: 500, to: 1400, decay: 2.5, q: 0.8 } },
    bells: { label: 'Glass bells', vol: 0.34, a: 0.002, d: 2.6, s: 0, r: 0.9, oscs: [{ type: 'sine' }], fm: { ratio: 3.5, index: 2.2, decay: 1.4, sustain: 0.05 } },
    pluck: { label: 'Plucked synth', vol: 0.3, a: 0.002, d: 0.7, s: 0, r: 0.25, oscs: [{ type: 'sawtooth' }, { type: 'square', detune: 5, gain: 0.4 }], filter: { from: 5200, to: 380, decay: 0.22, q: 2 } },
    lead: { label: 'Analog lead', vol: 0.2, a: 0.01, d: 0.4, s: 0.75, r: 0.16, oscs: [{ type: 'sawtooth' }, { type: 'square', detune: 8, gain: 0.5 }], filter: { from: 4200, to: 1300, decay: 0.35, q: 5 } },
    bass: { label: 'Sub bass', vol: 0.5, a: 0.006, d: 0.5, s: 0.6, r: 0.12, oscs: [{ type: 'sine' }, { type: 'sawtooth', gain: 0.35 }], filter: { from: 900, to: 320, decay: 0.3, q: 1.5 } },
    chip: { label: '8-bit', vol: 0.12, a: 0.002, d: 0.2, s: 0.8, r: 0.04, oscs: [{ type: 'square' }] },
  };

  // Synth stand-ins for each instrument family, used until (or unless) the sampled instrument is ready.
  const FALLBACK = {
    piano: PRESETS.epiano, mallet: PRESETS.bells, organ: PRESETS.organ, pluck: PRESETS.pluck, bass: PRESETS.bass,
    bowed: { vol: 0.13, a: 0.12, d: 0.6, s: 0.9, r: 0.35, oscs: [{ type: 'sawtooth', detune: -7 }, { type: 'sawtooth', detune: 7 }], filter: { from: 2600, q: 0.6 } },
    brass: { vol: 0.16, a: 0.04, d: 0.5, s: 0.8, r: 0.15, oscs: [{ type: 'sawtooth' }, { type: 'sawtooth', detune: 4, gain: 0.6 }], filter: { from: 900, to: 2400, decay: 0.25, q: 1.2 } },
    reed: { vol: 0.16, a: 0.03, d: 0.4, s: 0.85, r: 0.1, oscs: [{ type: 'square' }, { type: 'sine', ratio: 2, gain: 0.2 }], filter: { from: 2200, q: 1 } },
    flute: { vol: 0.3, a: 0.06, d: 0.4, s: 0.85, r: 0.12, oscs: [{ type: 'sine' }, { type: 'triangle', ratio: 2, gain: 0.12 }] },
    choir: { vol: 0.16, a: 0.25, d: 0.8, s: 0.85, r: 0.5, oscs: [{ type: 'triangle', detune: -8 }, { type: 'triangle', detune: 8 }, { type: 'sawtooth', gain: 0.25 }], filter: { from: 1300, q: 2 } },
    timpani: { vol: 0.6, a: 0.003, d: 1.6, s: 0, r: 0.8, oscs: [{ type: 'sine' }, { type: 'sine', ratio: 1.5, gain: 0.35 }, { type: 'sine', ratio: 2, gain: 0.2 }], filter: { from: 1400, to: 300, decay: 0.4 } },
    lead: PRESETS.lead, pad: PRESETS.pad,
  };

  function synthVoice(p, m, vel, t, out = instBus) {
    const f = 440 * Math.pow(2, (m - 69) / 12), amp = AC.createGain(), peak = p.vol * (0.3 + 0.7 * vel);
    let dest = amp;
    if (p.filter) {
      const filt = AC.createBiquadFilter();
      filt.type = 'lowpass'; filt.Q.value = p.filter.q || 0.7;
      filt.frequency.setValueAtTime(Math.min(18000, p.filter.from * (0.5 + vel)), t);
      if (p.filter.to) filt.frequency.setTargetAtTime(p.filter.to, t, p.filter.decay / 3);
      filt.connect(amp); dest = filt;
    }
    const nodes = p.oscs.map(o => {
      const osc = AC.createOscillator(), g = AC.createGain();
      osc.type = o.type; osc.frequency.value = f * (o.ratio || 1); osc.detune.value = o.detune || 0;
      g.gain.value = o.gain ?? 1; osc.connect(g); g.connect(dest); osc.start(t);
      return osc;
    });
    if (p.fm) {
      const mod = AC.createOscillator(), mg = AC.createGain(), idx = p.fm.index * f * (0.4 + vel);
      mod.frequency.value = f * p.fm.ratio;
      mg.gain.setValueAtTime(idx, t); mg.gain.setTargetAtTime(idx * (p.fm.sustain || 0), t, p.fm.decay / 3);
      mod.connect(mg); mg.connect(nodes[0].frequency); mod.start(t); nodes.push(mod);
    }
    amp.gain.setValueAtTime(0, t);
    amp.gain.linearRampToValueAtTime(peak, t + p.a);
    amp.gain.setTargetAtTime(peak * p.s, t + p.a, p.d / 3);
    amp.connect(out);
    return {
      stop(tr, fast) {
        tr = Math.max(tr, t + p.a + 0.005);
        const r = fast ? 0.04 : p.r;
        amp.gain.cancelScheduledValues(tr); amp.gain.setTargetAtTime(0, tr, r / 3);
        nodes.forEach(o => { try { o.stop(tr + r * 2 + 0.05); } catch { /* already stopped */ } });
      },
    };
  }

  // ----- Sampled General MIDI instruments -----
  // The server keeps a copy of each instrument in sounds/ and hands it over as {"C4": base64 mp3, ...}.
  // Notes are decoded the first time they are needed; until then the family's synth stands in.
  const banks = {};
  const NOTE_PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  const noteNumber = n => { const m = /^([A-G])(b?)(-?\d)$/.exec(n); return m ? 12 * (+m[3] + 1) + NOTE_PC[m[1]] - (m[2] ? 1 : 0) : null; };
  function loadInstrument(prog) {
    if (!(prog >= 0 && prog < 128)) return Promise.resolve(false);
    if (banks[prog]) return banks[prog].ready;
    const bank = banks[prog] = { raw: {}, buf: {}, pending: {} };
    bank.ready = fetch('sounds/' + GM.SAMPLE[prog] + '.json').then(r => r.ok ? r.json() : null).then(j => {
      if (!j) return false;
      for (const [name, b64] of Object.entries(j)) { const m = noteNumber(name); if (m != null) bank.raw[m] = b64; }
      return Object.keys(bank.raw).length > 0;
    }).catch(() => false);
    return bank.ready;
  }
  const nearest = (obj, m) => { let best = null; for (const k in obj) if (best == null || Math.abs(k - m) < Math.abs(best - m)) best = +k; return best; };
  function decodeNote(prog, m) {
    const bank = banks[prog]; if (!bank) return Promise.resolve();
    m = Math.min(m, GM.top(prog));   // above its top note an instrument's recordings are silent
    const s = bank.raw[m] ? m : nearest(bank.raw, m);
    if (s == null || bank.buf[s]) return Promise.resolve();
    if (!bank.pending[s]) {
      const bin = atob(bank.raw[s]), bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      bank.pending[s] = AC.decodeAudioData(bytes.buffer).then(b => { bank.buf[s] = b; }).catch(() => {});
    }
    return bank.pending[s];
  }
  async function warmInstrument(prog, notes) {
    if (!(await loadInstrument(prog))) return false;
    const list = notes || Object.keys(banks[prog].raw).map(Number);
    await Promise.all(list.map(m => decodeNote(prog, m)));
    return true;
  }
  // The steady middle of a recording, for a held note to loop: from 40% of the way in to the last moment the
  // sound is still as strong as it was there (many recordings fade before they end, and a loop that runs into
  // the fade pumps). null for a sound that simply dies away: such a note rings out and is not looped.
  const steady = new WeakMap();
  function steadyPart(buf) {
    if (steady.has(buf)) return steady.get(buf);
    const d = buf.getChannelData(0), n = d.length, win = Math.max(64, Math.floor(buf.sampleRate * 0.12));
    const rms = at => { const a = Math.max(0, Math.floor(at * n - win / 2)), b = Math.min(n, a + win); let s = 0; for (let i = a; i < b; i++) s += d[i] * d[i]; return Math.sqrt(s / Math.max(1, b - a)); };
    const ref = rms(0.4);
    let end = 0.85;
    while (end > 0.6 && rms(end) < ref * 0.75) end -= 0.05;
    const ok = ref > 1e-4 && rms(end) >= ref * 0.75 && rms((0.4 + end) / 2) >= ref * 0.8;
    const part = ok ? [buf.duration * 0.4, buf.duration * end] : null;
    steady.set(buf, part);
    return part;
  }
  const panners = {};
  function seatFor(prog, pan) {
    const key = prog + ':' + (pan ?? 'seat');
    if (!panners[key]) {
      const p = AC.createStereoPanner ? AC.createStereoPanner() : AC.createGain();
      if (p.pan) p.pan.value = pan ?? GM.seat(prog);
      p.connect(instBus); panners[key] = p;
    }
    return panners[key];
  }
  // One note of a sampled instrument. Called with a number for `pan` it is the plain note of the keyboard and
  // of unmarked MIDI; called with o = { out, level, pts, boost } it is a note of a score: `level` is how loud it
  // starts, `pts` ([[time, level], ...]) how that changes while it is held, `boost` an accent on its attack.
  function gmVoice(prog, m, vel, t, o) {
    if (o == null || typeof o !== 'object') o = { pan: o };
    const fam = GM.family(prog), out = o.out || seatFor(prog, o.pan), bank = banks[prog], scored = o.level != null;
    const reach = Math.min(m, GM.top(prog)), s = bank && nearest(bank.buf, reach);
    if (s == null || Math.abs(s - reach) > 6) {
      if (bank) decodeNote(prog, m);
      return synthVoice(FALLBACK[fam.fallback] || PRESETS.epiano, m, scored ? clamp(levelGain(o.level) * 2.2 * (o.boost || 1), 0.05, 1.2) : vel, t, out);
    }
    const buf = bank.buf[s], src = AC.createBufferSource(), g = AC.createGain();
    const peak = (scored ? GAIN0 * levelGain(o.level) : 0.25 + vel * vel * 1.1) * GM.trim(prog);
    src.buffer = buf; src.playbackRate.value = Math.pow(2, (m - s) / 12);
    // Held notes on bowed, blown and sung instruments loop the steady middle of the recording.
    const loop = fam.sustain && buf.duration > 0.8 ? steadyPart(buf) : null;
    if (loop) { src.loop = true; src.loopStart = loop[0]; src.loopEnd = loop[1]; }
    // A soft note speaks more slowly than a loud one.
    const attack = !fam.sustain ? 0.003 : scored ? 0.014 + (1 - o.level) * 0.07 : 0.02;
    const boost = o.boost || 1;
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(peak * boost, t + attack);
    if (boost > 1 && fam.sustain) g.gain.linearRampToValueAtTime(peak, t + attack + 0.16);   // an accent bites, then settles
    let head = src;
    if (scored) {
      const lows = [o.level, ...(o.pts || []).map(p => p[1])];
      if (Math.min(...lows) < 0.7) {   // soft is darker; a filter only where it will be heard
        const lp = lowpass(levelTone(o.level)); lp.Q.value = 0.5;
        if (o.pts && fam.sustain) { lp.frequency.setValueAtTime(levelTone(o.level), t); o.pts.forEach(([at, L]) => lp.frequency.linearRampToValueAtTime(levelTone(L), Math.max(at, t + 0.01))); }
        head.connect(lp); head = lp;
      }
      if (o.pts && fam.sustain) {      // the swell or fade written over this note
        const dyn = gain(1), base = levelGain(o.level);
        dyn.gain.setValueAtTime(1, t);
        o.pts.forEach(([at, L]) => dyn.gain.linearRampToValueAtTime(levelGain(L) / base, Math.max(at, t + 0.01)));
        head.connect(dyn); head = dyn;
      }
    }
    head.connect(g); g.connect(out); src.start(t);
    return {
      stop(tr, fast, fade) {
        tr = Math.max(tr, t + attack + 0.01);
        const tc = fade || (fast ? 0.02 : fam.sustain ? 0.09 : 0.18);
        g.gain.cancelScheduledValues(tr); g.gain.setTargetAtTime(0, tr, tc);
        try { src.stop(tr + tc * 8); } catch { /* already stopped */ }
      },
    };
  }

  // ----- Where each instrument of a score sits -----
  // Every staff gets a seat on the stage, an amount of hall, and, for the string sections of an orchestra, the
  // slight spread of many players (two delayed copies whose pitch drifts a few cents, out to the sides).
  const DEPTH = { bowed: 0.10, brass: 0.14, choir: 0.18, reed: 0.08, flute: 0.08, timpani: 0.10, mallet: 0.07, organ: 0.08, pad: 0.06, lead: 0.03, pluck: 0.03 };
  function stripFor(song, n) {
    const st = song.strips || (song.strips = { ac: AC, map: new Map(), stop: [] });
    if (st.ac !== AC) return null;                  // this song's seats belong to another engine
    let s = st.map.get(n.tr);
    if (s) return s;
    const input = gain(1), fam = GM.family(n.prog).fallback, at = clamp(n.pan ?? (song.seat && song.seat[n.tr]) ?? GM.seat(n.prog), -1, 1);
    const far = gain(DEPTH[fam] || 0); far.connect(depthBus);
    const place = (node, x) => { const p = panner(x); node.connect(p); p.connect(instBus); p.connect(far); st.stop.push(p); };
    if (song.sections && song.sections.has(n.tr)) {
      const r = rng(900 + n.tr * 7), dry = gain(0.78);
      input.connect(dry); place(dry, at);
      [[-0.2, 0.0085], [0.2, 0.0135]].forEach(([side, base]) => {
        const d = AC.createDelay(0.05), o = AC.createOscillator(), depth = gain(0.0009 + r() * 0.0005), mix = gain(0.5);
        d.delayTime.value = base + r() * 0.003; o.frequency.value = 0.38 + r() * 0.34; o.connect(depth); depth.connect(d.delayTime); o.start();
        input.connect(d); d.connect(mix); place(mix, at + side);
        st.stop.push(o, d);
      });
    } else place(input, at);
    st.stop.push(input, far);
    s = { input };
    st.map.set(n.tr, s);
    return s;
  }
  function freeStrips(song) {
    const st = song && song.strips;
    if (!st || st.ac !== AC) return;
    song.strips = null;
    st.stop.forEach(node => { try { if (node.stop) node.stop(); } catch { /* not started */ } try { node.disconnect(); } catch { /* gone */ } });
  }

  // Sounds for the keyboard: built-in keys and synths, then sampled instruments ('gm:<program>').
  const SOUNDS = [['grand', 'Grand piano', 'Keys and synths'], ...Object.entries(PRESETS).map(([k, v]) => [k, v.label, 'Keys and synths']),
    ...GM.KEYBOARD.flatMap(([group, progs]) => progs.map(p => ['gm:' + p, GM.LABEL[p], group]))];
  let current = 'grand';
  const voice = (m, vel, t) => current === 'grand' ? pianoVoice(m, vel, t)
    : current.startsWith('gm:') ? gmVoice(+current.slice(3), m, vel, t)
    : synthVoice(PRESETS[current] || PRESETS.epiano, m, vel, t);
  const STRING_PROGS = new Set([40, 41, 42, 43, 48, 49]), TREMOLO = 44;
  // One note of a score. A staff with its own midiInstrument plays that instrument; staves without one (or
  // marked as a grand piano) play whatever sound the keyboard is set to. `time` turns a tick of the score into
  // the moment it sounds. Returns something with stop(), like every voice.
  function scoreVoice(song, n, when, dur, time) {
    const shape = noteShape(song, n), prog = n.prog > 0 ? n.prog : current.startsWith('gm:') ? +current.slice(3) : 0;
    if (!shape) return prog > 0 && n.prog > 0 ? gmVoice(prog, n.note, 0.25 + n.vel * 0.7, when, n.pan) : voice(n.note, 0.25 + n.vel * 0.7, when);
    const strip = prog > 0 || current === 'grand' ? stripFor(song, { ...n, prog }) : null, out = strip ? strip.input : undefined;
    const boost = ACCENT[shape.mark] || 1, sustain = prog > 0 && GM.family(prog).sustain;
    const pts = shape.pts && sustain ? shape.pts.map(([tick, L, dt]) => [time(tick) + dt, L]) : null;
    const one = (p, at, level, o = {}) => p > 0 ? gmVoice(p, n.note, 0, at, { out, level, boost, ...o })
      : current === 'grand' ? pianoVoice(n.note, 0, at, { out, level, boost, ...o })
      : synthVoice(PRESETS[current] || PRESETS.epiano, n.note, clamp(levelGain(level) * 2.2 * boost, 0.05, 1.2), at);
    if (!shape.roll) return one(prog, when, shape.level, { pts });
    // A tremolo or roll. Bowed strings have a recording of it; everything that rings (timpani, mallets, harp,
    // piano) is struck again and again, each stroke as loud as the score says at that moment.
    if (sustain) return one(STRING_PROGS.has(prog) && banks[TREMOLO] && Object.keys(banks[TREMOLO].buf).length ? TREMOLO : prog, when, shape.level, { pts });
    const d = song.dyn[n.tr], gap = clamp(time(n.tick + shape.roll) - when, 0.055, 0.09), r = rng(n.tick * 31 + n.note), strokes = [];
    for (let at = when, k = 0; at < when + dur - 0.02 && k < 400; at += gap * (0.94 + 0.12 * r()), k++) {
      const tick = n.tick + (n.tend - n.tick) * (at - when) / Math.max(dur, 0.001);
      const v = one(prog, at, clamp(levelAt(d.line, tick) * (0.97 + 0.06 * r()), 0.02, 1), { boost: (k ? 0.62 : 0.8) * boost });
      if (strokes.length) strokes[strokes.length - 1].stop(at, false, 0.07);   // the new stroke takes over the drum head
      strokes.push(v);
    }
    return { stop(tr, fast) { strokes.forEach(v => v.stop(tr, fast)); } };
  }

  // ----- Live playing -----
  const live = new Map(), pedalHeld = new Set();
  let sustain = false;
  function noteOn(m, vel = 0.8) {
    wake();
    const old = live.get(m); if (old) old.stop(AC.currentTime, true);
    live.set(m, voice(m, vel, AC.currentTime));
  }
  function noteOff(m) {
    const v = live.get(m); if (!v) return;
    live.delete(m);
    if (sustain) pedalHeld.add(v); else v.stop(AC.currentTime);
  }
  function setSustain(on) {
    sustain = on;
    if (!on) { pedalHeld.forEach(v => v.stop(AC.currentTime)); pedalHeld.clear(); }
  }

  // ----- Drums -----
  const KITS = {
    '808': { label: 'Deep 808', kick: [150, 42, 0.55], snare: 185, hat: 8200, tone: 20000 },
    tight: { label: 'Tight studio', kick: [190, 58, 0.2], snare: 230, hat: 9500, tone: 20000 },
    lofi: { label: 'Dusty lo-fi', kick: [130, 48, 0.32], snare: 165, hat: 6000, tone: 3600 },
  };
  let kit = KITS['808'], kitName = '808', openHatGain = null;
  function setKit(name) { kitName = name; kit = KITS[name] || KITS['808']; applyTone(); if (kit.recorded) warmKit(); }

  // ----- Recorded drums and percussion -----
  // When the Models page has fetched them, real recordings take the place of the drums made below: seven drum
  // kits and an orchestra's percussion (percussion.py cuts them and writes the index read here). Each drum of a
  // kit has layers from soft to loud, and each layer one or more takes that are played in turn, so two strokes in
  // a row are never the very same recording. Without the recordings everything plays as before.
  let perc = null;
  const percBufs = {}, percWait = {}, chokes = {}, takes = {};
  const BEAT_KEY = { kick: 36, snare: 38, hat: 42, openhat: 46, clap: 39, tom: 45, rim: 37, cowbell: 56, crash: 49, ride: 51, triangle: 81, tamb: 54 };
  const KIT_PROGRAM = { 8: 'room', 16: 'power', 24: 'electronic', 25: 'tr808', 32: 'jazz', 40: 'brush', 48: 'orchestra' };   // midiInstrument = "jazz kit" ...
  const percussion = fetch('percussion/index.json').then(r => r.ok ? r.json() : null).then(ix => {
    if (!ix || !ix.kits || !ix.sounds) return null;
    perc = ix;
    for (const [id, k] of Object.entries(ix.kits)) KITS[id] = { ...KITS.tight, label: k.label, recorded: true, id, tone: 20000 };
    setKit(kitName);   // a recorded kit that was asked for before the index had arrived
    return ix;
  }).catch(() => null);
  function loadPerc(id) {
    if (!percWait[id]) {
      percWait[id] = fetch('percussion/sounds/' + id + '.flac').then(r => { if (!r.ok) throw new Error('missing'); return r.arrayBuffer(); })
        .then(b => AC.decodeAudioData(b)).then(buf => { percBufs[id] = buf; }).catch(() => {});
    }
    return percWait[id];
  }
  const percBuf = id => percBufs[id] || (loadPerc(id), null);   // decoded and ready, or null (and now on its way)
  const layerOf = (layers, L) => layers[Math.min(layers.length - 1, Math.floor(clamp((L - 0.15) / 0.8, 0, 0.999) * layers.length))];
  // How hard a stroke at loudness L is, against one at mezzo forte. Above mezzo forte a drum gains a little less than
  // the instruments do (the louder recording is brighter and does part of the work), and an accent on top stops at 2.3.
  const hitGain = L => { const g = levelGain(L) / levelGain(MF); return g > 1 ? Math.pow(g, 0.75) : g; };
  const HIT_MAX = 2.3;
  const warmKeys = (kitId, keys) => { const K = perc && perc.kits[kitId]; return K ? [...new Set(keys.flatMap(k => K.keys[k] ? K.keys[k].s.flat() : []))].map(loadPerc) : []; };
  const warmKit = () => Promise.all(warmKeys(kit.id, Object.values(BEAT_KEY).slice(0, 8)));
  // One stroke on a recorded drum. key is its General MIDI number, L how hard it is struck (0 to 1) and mul a
  // gain on top. Returns false when that drum has no recording ready: the caller then plays its own drum.
  function strike(kitId, key, t, L, mul = 1, o = {}) {
    const z = perc && perc.kits[kitId] && perc.kits[kitId].keys[key];
    if (!z) return false;
    const layer = layerOf(z.s, L), n = takes[kitId + key] = (takes[kitId + key] || 0) + 1, id = layer[n % layer.length], buf = percBuf(id);
    if (!buf) return false;
    const lead = (perc.sounds[id] || {}).lead || 0, src = AC.createBufferSource(), g = gain(z.g * Math.min(mul, HIT_MAX)), p = panner(z.pan || 0);
    src.buffer = buf; src.playbackRate.value = (z.rate || 1) * tune;
    src.connect(g); g.connect(p); p.connect(drumBus);
    if (o.depth) { const far = gain(o.depth); p.connect(far); far.connect(depthBus); }
    if (z.cut) g.gain.setTargetAtTime(0, t + z.cut, 0.03);   // a drum that is damped by the hand
    if (z.choke) {                                            // the open hi-hat stops when the hat is closed
      const c = kitId + ':' + z.choke, old = chokes[c];
      if (old) { old.gain.cancelScheduledValues(t); old.gain.setTargetAtTime(0, t, 0.012); }
      chokes[c] = g;
    }
    src.start(Math.max(AC.currentTime, t - lead));
    return true;
  }

  function noiseHit(t, { type, freq, q = 0.8, decay, gain }) {
    const src = AC.createBufferSource(), f = AC.createBiquadFilter(), g = AC.createGain();
    src.buffer = noise; src.loop = true; f.type = type; f.frequency.value = freq; f.Q.value = q;
    g.gain.setValueAtTime(gain, t); g.gain.exponentialRampToValueAtTime(0.0008, t + decay);
    src.connect(f); f.connect(g); g.connect(drumBus);
    src.start(t, Math.random() * 0.5); src.stop(t + decay + 0.02);
    return g;
  }
  function toneHit(t, { type = 'sine', from, to, sweep, decay, gain }) {
    const o = AC.createOscillator(), g = AC.createGain();
    o.type = type; o.frequency.setValueAtTime(from, t);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t + sweep);
    g.gain.setValueAtTime(gain, t); g.gain.exponentialRampToValueAtTime(0.0008, t + decay);
    o.connect(g); g.connect(drumBus); o.start(t); o.stop(t + decay + 0.02);
  }
  function drum(name, t = AC.currentTime, vel = 0.8) {
    const v = vel * (levels[name] ?? 1), T = tune, hatF = Math.min(15000, kit.hat * T);
    if (v <= 0.001) return;
    if (kit.recorded && BEAT_KEY[name] && strike(kit.id, BEAT_KEY[name], t, 0.25 + 0.62 * vel, v / 0.887)) return;
    switch (name) {
      case 'kick':
        toneHit(t, { from: kit.kick[0] * T, to: kit.kick[1] * T, sweep: 0.11, decay: kit.kick[2], gain: 1.1 * v });
        noiseHit(t, { type: 'lowpass', freq: 2200, decay: 0.012, gain: 0.25 * v });
        break;
      case 'snare':
        toneHit(t, { type: 'triangle', from: kit.snare * T, to: kit.snare * 0.72 * T, sweep: 0.08, decay: 0.12, gain: 0.5 * v });
        noiseHit(t, { type: 'highpass', freq: 1400 * T, decay: 0.19, gain: 0.55 * v });
        break;
      case 'hat':
        if (openHatGain) { openHatGain.gain.cancelScheduledValues(t); openHatGain.gain.setTargetAtTime(0, t, 0.008); openHatGain = null; }
        noiseHit(t, { type: 'highpass', freq: hatF, decay: 0.05, gain: 0.3 * v });
        break;
      case 'openhat':
        openHatGain = noiseHit(t, { type: 'highpass', freq: hatF * 0.9, decay: 0.34, gain: 0.26 * v });
        break;
      case 'clap':
        [0, 0.011, 0.023].forEach(d => noiseHit(t + d, { type: 'bandpass', freq: 1500 * T, q: 1.2, decay: 0.02, gain: 0.5 * v }));
        noiseHit(t + 0.03, { type: 'bandpass', freq: 1400 * T, q: 1, decay: 0.2, gain: 0.5 * v });
        break;
      case 'tom':
        toneHit(t, { from: 170 * T, to: 95 * T, sweep: 0.18, decay: 0.3, gain: 0.8 * v });
        break;
      case 'rim':
        toneHit(t, { type: 'triangle', from: 820 * T, decay: 0.03, gain: 0.45 * v });
        noiseHit(t, { type: 'bandpass', freq: 2600 * T, q: 3, decay: 0.03, gain: 0.35 * v });
        break;
      case 'crash':
        noiseHit(t, { type: 'highpass', freq: 4200 * T, q: 0.5, decay: 1.6, gain: 0.32 * v });
        noiseHit(t, { type: 'bandpass', freq: 6800 * T, q: 0.8, decay: 0.9, gain: 0.18 * v });
        break;
      case 'ride':
        noiseHit(t, { type: 'highpass', freq: 7000 * T, decay: 0.45, gain: 0.16 * v });
        toneHit(t, { type: 'triangle', from: 3200 * T, decay: 0.5, gain: 0.05 * v });
        break;
      case 'triangle':
        toneHit(t, { from: 5100 * T, decay: 1.2, gain: 0.12 * v }); toneHit(t, { from: 7650 * T, decay: 0.8, gain: 0.05 * v });
        break;
      case 'wash':   // one soft-mallet stroke of a cymbal roll
        noiseHit(t, { type: 'highpass', freq: 5200 * T, q: 0.5, decay: 0.7, gain: 0.2 * v });
        noiseHit(t, { type: 'bandpass', freq: 9000 * T, q: 0.7, decay: 0.4, gain: 0.08 * v });
        break;
      case 'tamb':
        [0, 0.012].forEach(d => noiseHit(t + d, { type: 'bandpass', freq: 9000 * T, q: 1.5, decay: 0.12, gain: 0.25 * v }));
        break;
      case 'cowbell': {
        const bp = { type: 'square', decay: 0.28, gain: 0.16 * v };
        toneHit(t, { ...bp, from: 545 * T }); toneHit(t, { ...bp, from: 812 * T });
        break;
      }
    }
  }
  const GM_DRUM = { 35: 'kick', 36: 'kick', 37: 'rim', 38: 'snare', 40: 'snare', 39: 'clap', 42: 'hat', 44: 'hat', 46: 'openhat', 49: 'crash', 51: 'ride', 52: 'crash', 53: 'ride', 54: 'tamb', 55: 'crash', 57: 'crash', 59: 'ride', 41: 'tom', 43: 'tom', 45: 'tom', 47: 'tom', 48: 'tom', 50: 'tom', 56: 'cowbell', 75: 'rim', 76: 'rim', 77: 'rim', 80: 'triangle', 81: 'triangle' };
  // A drum note of a score: one hit, or a roll when the score writes one (c1:32). A roll is many soft strokes,
  // each as loud as the score says at that moment, so a roll under a crescendo grows. With the recorded drums
  // the note plays its own drum of the score's kit (see kitOf); without them, the nearest drum made above.
  const ROLL_GAIN = { wash: 0.16, snare: 0.6, tom: 0.6, kick: 0.6, triangle: 0.45, tamb: 0.6 };
  // One stroke of a roll on the recordings: on a drum, on the orchestra's hanging cymbal (it rings for ten seconds,
  // so the strokes pile up), and on a drum kit's crash (it dies quickly).
  const REAL_ROLL = { drum: 0.62, hanging: 0.2, crash: 0.9 };
  const kitOf = (song, n) => perc ? (KIT_PROGRAM[n.kit] || song.kit || 'studio') : null;
  const isCymbal = name => name === 'crash' || name === 'ride';
  // In an orchestra a cymbal roll is played with soft sticks on the hanging cymbal (key 57), whichever cymbal is written.
  const rollKey = (kitId, n) => kitId === 'orchestra' && isCymbal(GM_DRUM[n.note]) ? 57 : n.note;
  function scoreDrum(song, n, when, dur, time) {
    const name = GM_DRUM[n.note], kitId = kitOf(song, n), real = kitId && perc.kits[kitId] && perc.kits[kitId].keys[n.note] ? kitId : null;
    if (!name && !real) return;
    const o = { depth: kitId === 'orchestra' ? 0.14 : 0.05 };
    const play = (key, synth, at, L, mul, vel) => (real && strike(real, key, at, L, mul, o)) || (synth && drum(synth, at, vel));
    const shape = noteShape(song, n);
    if (!shape) { const L = 0.25 + n.vel * 0.62; return play(n.note, name, when, L, hitGain(L), 0.4 + n.vel * 0.6); }
    const hit = L => clamp(levelGain(L) * 2.2, 0.06, 1.2), boost = ACCENT[shape.mark] || 1;
    if (!shape.roll) return play(n.note, name, when, shape.level, hitGain(shape.level) * boost, hit(shape.level) * boost);
    const stroke = isCymbal(name) ? 'wash' : name, key = real ? rollKey(real, n) : n.note;
    const realSoft = !isCymbal(name) ? REAL_ROLL.drum : real === 'orchestra' ? REAL_ROLL.hanging : REAL_ROLL.crash;
    const d = song.dyn[n.tr], gap = clamp(time(n.tick + shape.roll) - when, 0.05, 0.085), r = rng(n.tick * 17 + n.note), soft = ROLL_GAIN[stroke] || 0.5;
    for (let at = when, k = 0; at < when + dur - 0.02 && k < 500; at += gap * (0.93 + 0.14 * r()), k++) {
      const tick = n.tick + (n.tend - n.tick) * (at - when) / Math.max(dur, 0.001), L = levelAt(d.line, tick), wobble = 0.9 + 0.2 * r();
      play(key, stroke, at, L, hitGain(L) * realSoft * wobble, hit(L) * soft * wobble);
    }
  }
  // The recordings a song's drum notes will need, so that they can be made ready before it plays.
  function drumSounds(song) {
    const ids = new Set();
    if (!perc) return ids;
    for (const n of song.notes) {
      if (n.ch !== 9) continue;
      const kitId = kitOf(song, n), K = perc.kits[kitId], shape = noteShape(song, n), d = song.dyn && song.dyn[n.tr];
      const add = (key, L) => { const z = K && K.keys[key]; if (z) layerOf(z.s, L).forEach(id => ids.add(id)); };
      if (!shape) add(n.note, 0.25 + n.vel * 0.62);
      else if (!shape.roll) add(n.note, shape.level);
      else for (let i = 0; i <= 12; i++) add(rollKey(kitId, n), levelAt(d.line, n.tick + (n.tend - n.tick) * i / 12));
    }
    return ids;
  }

  // ----- Standard MIDI file reader -----
  // perf is what music.py read out of the score (see its performance()): for each track the loudness line,
  // the accents and the rolls. With it, song.dyn says how every staff wants to be played.
  let songCount = 0;
  function parseMidi(buffer, perf) {
    const d = new DataView(buffer); let p = 0;
    const u8 = () => d.getUint8(p++);
    const tag = () => String.fromCharCode(u8(), u8(), u8(), u8());
    const u32 = () => { const v = d.getUint32(p); p += 4; return v; };
    const u16 = () => { const v = d.getUint16(p); p += 2; return v; };
    const varlen = () => { let v = 0, b; do { b = u8(); v = (v << 7) | (b & 0x7f); } while (b & 0x80); return v; };
    if (tag() !== 'MThd') throw new Error('Not a MIDI file');
    const hlen = u32(); u16(); const ntr = u16(), div = u16(); p += hlen - 6;
    const evs = [], names = {};
    let meter = null;
    for (let tr = 0; tr < ntr && p < d.byteLength; tr++) {
      if (tag() !== 'MTrk') break;
      const end = u32() + p; let tick = 0, status = 0;
      while (p < end) {
        tick += varlen();
        let b = u8();
        if (b === 0xff) {
          const type = u8(), len = varlen();
          if (type === 0x51) evs.push({ tick, kind: 0, us: (u8() << 16) | (u8() << 8) | u8() });
          else if (type === 0x04 || type === 0x03) { let s = ''; for (let i = 0; i < len; i++) s += String.fromCharCode(u8()); names[tr] = s; }
          else if (type === 0x58 && len >= 2) { const num = u8(), den = Math.pow(2, u8()); if (!meter) meter = [num, den]; p += len - 2; }
          else p += len;
        } else if (b === 0xf0 || b === 0xf7) { p += varlen(); }
        else {
          if (b & 0x80) status = b; else p--;
          const hi = status & 0xf0, ch = status & 0x0f;
          if (hi === 0x90 || hi === 0x80) {
            const note = u8(), vel = u8();
            evs.push({ tick, kind: hi === 0x90 && vel > 0 ? 2 : 1, ch, note, vel, tr });
          } else if (hi === 0xc0) evs.push({ tick, kind: 0, ch, tr, prog: u8() });
          else if (hi === 0xb0) { const cc = u8(), val = u8(); if (cc === 10) evs.push({ tick, kind: 0, ch, tr, pan: (val - 64) / 64 }); }
          else p += hi === 0xd0 ? 1 : 2;
        }
      }
      evs.push({ tick, kind: 3 });   // where this track ends, closing rests included
      p = end;
    }
    evs.sort((a, b) => a.tick - b.tick || a.kind - b.kind);
    let us = 500000, lastTick = 0, time = 0, bpm = null, total = 0;
    const open = new Map(), notes = [], prog = {}, pan = {}, parts = new Map(), tempos = [{ tick: 0, time: 0, us }];
    for (const e of evs) {
      time += (e.tick - lastTick) * us / div / 1e6; lastTick = e.tick;
      if (e.kind === 3) { total = Math.max(total, time); continue; }
      if (e.kind === 0) {
        if (e.us) { us = e.us; if (bpm == null) bpm = 6e7 / us; tempos.push({ tick: e.tick, time, us }); }
        // Kept per track as well as per channel: LilyPond writes one track per staff, and when a big
        // score runs out of channels two staves can share one, each with its own instrument.
        else if (e.prog != null) prog[e.ch] = prog[e.tr + ':' + e.ch] = e.prog;
        else if (e.pan != null) pan[e.ch] = pan[e.tr + ':' + e.ch] = e.pan;
        continue;
      }
      const key = e.tr + ':' + e.ch + ':' + e.note;
      if (e.kind === 2) {
        const n = { t: time, dur: 0.25, ch: e.ch, note: e.note, vel: e.vel / 127, prog: e.ch === 9 ? -1 : (prog[e.tr + ':' + e.ch] ?? prog[e.ch] ?? 0), pan: pan[e.tr + ':' + e.ch] ?? pan[e.ch],
          tr: e.tr, tick: e.tick, tend: e.tick + Math.round(div / 4) };
        if (e.ch === 9) n.kit = prog[e.tr + ':9'] ?? prog[9] ?? 0;   // the drum kit this staff names, if it names one
        if (e.ch !== 9 && !parts.has(n.prog)) parts.set(n.prog, names[e.tr] || '');
        notes.push(n); open.set(key, n);
      } else if (open.has(key)) { const n = open.get(key); n.dur = Math.max(0.03, time - n.t); n.tend = Math.max(n.tick + 1, e.tick); open.delete(key); }
    }
    notes.sort((a, b) => a.t - b.t);
    // parts: the instruments that play, as [program, name]. Program 0 plays on the keyboard's sound.
    // length is where the last note ends; total is where the written music ends (never shorter).
    const length = notes.reduce((mx, n) => Math.max(mx, n.t + n.dur), 0);
    const tickTime = tick => { let i = tempos.length - 1; while (i > 0 && tempos[i].tick > tick) i--; return tempos[i].time + (tick - tempos[i].tick) * tempos[i].us / div / 1e6; };
    const song = { id: ++songCount, notes, bpm: bpm || 120, length, total: Math.max(total, length), parts: [...parts], div,
      ticksPerBar: Math.round(div * 4 * (meter ? meter[0] / meter[1] : 1)), tickTime, dyn: null, seat: {}, sections: new Set() };
    if (perf && perf.div === div && perf.tracks) {
      song.dyn = {};
      for (const [tr, v] of Object.entries(perf.tracks)) {
        song.dyn[tr] = { name: v.name || '', line: Array.isArray(v.line) ? v.line : [], marks: new Map((v.marks || []).map(m => [m[0], m[1]])), rolls: new Map((v.rolls || []).map(m => [m[0], m[1]])) };
      }
    }
    // Who sits where. Staves that share an instrument (first and second violins) are moved a little apart, and
    // in an orchestra the string staves are sections of many players, not soloists.
    const byProg = new Map();
    for (const n of notes) if (n.ch !== 9 && n.prog > 0 && n.pan == null) { if (!byProg.has(n.prog)) byProg.set(n.prog, new Set()); byProg.get(n.prog).add(n.tr); }
    for (const [pr, trs] of byProg) {
      const list = [...trs].sort((a, b) => a - b);
      list.forEach((tr, i) => { song.seat[tr] = GM.seat(pr) + (list.length > 1 ? (i - (list.length - 1) / 2) * 0.16 : 0); });
    }
    const staves = new Set(notes.filter(n => n.ch !== 9).map(n => n.tr)).size, fiddles = (byProg.get(40) || new Set()).size;
    if ((byProg.has(43) && fiddles >= 2) || staves >= 10) for (const pr of [40, 41, 42, 43]) (byProg.get(pr) || []).forEach(tr => song.sections.add(tr));
    // Which recorded drums suit a drum staff that names no kit of its own: an orchestra's percussion for orchestral
    // music (unless the part is plainly a drum-kit part, with a hi-hat), a drum machine for synth music, a studio kit otherwise.
    const progs = [...new Set(notes.filter(n => n.ch !== 9).map(n => n.prog))], any = (a, b) => progs.some(p => p >= a && p <= b);
    const hats = notes.some(n => n.ch === 9 && (n.note === 42 || n.note === 44 || n.note === 46));
    const band = any(16, 18) || any(24, 39) || any(64, 67), synths = any(80, 103) || any(38, 39) || any(50, 51) || any(62, 63);
    const orchestra = any(40, 49) || any(52, 61) || any(68, 73);
    song.kit = synths && !any(24, 37) ? 'tr808' : orchestra && !band && !hats ? 'orchestra' : 'studio';
    return song;
  }

  // ----- Film sounds -----
  // The layer under and around the instruments: drones, risers, hits, a heartbeat, weather. A piece carries it
  // as { space, layer: [{ sound, bar, bars, level, ... }] } (music.py's clean_film); every sound here is made
  // from oscillators and noise, so nothing has to be downloaded. Each recipe gets (t0, dur, p, B, now, beat):
  // when the sound begins, how long it lasts, its settings, where to send it, the earliest moment that can
  // still be heard (later than t0 when playing starts in the middle), and the length of one beat.
  let filmLevel = 1;
  const hz = m => 440 * Math.pow(2, (m - 69) / 12);
  function pitchOf(x, fallback) {
    if (typeof x === 'number' && x > 0 && x < 128) return x;
    const m = /^([A-Ga-g])([#b]?)(-?\d)$/.exec(String(x || '').trim());
    return m ? 12 * (+m[3] + 1) + NOTE_PC[m[1].toUpperCase()] + (m[2] === '#' ? 1 : m[2] === 'b' ? -1 : 0) : fallback;
  }
  const soft = (() => { const n = 1024, c = new Float32Array(n); for (let i = 0; i < n; i++) { const x = i / (n - 1) * 2 - 1; c[i] = Math.tanh(2.6 * x) / Math.tanh(2.6); } return c; })();
  const warm = () => { const w = AC.createWaveShaper(); w.curve = soft; return w; };   // adds overtones, so deep sounds are heard on small speakers too
  function noiseLoop(t, end, seed) {
    const src = AC.createBufferSource(); src.buffer = noise; src.loop = true;
    src.start(t, (rng(seed || 1)() * 2.5)); src.stop(end);
    return src;
  }
  const into = (node, B, wet) => { node.connect(B.dry); if (wet) { const s = gain(wet); node.connect(s); s.connect(B.wet); } };
  // A rising curve: peak * x^e for x from x0 (where we come in) to 1, as the list of values an AudioParam wants.
  const power = (peak, e, x0 = 0, n = 48) => { const c = new Float32Array(n); for (let i = 0; i < n; i++) c[i] = Math.max(0.0001, peak * Math.pow(x0 + (1 - x0) * i / (n - 1), e)); return c; };
  const grow = (p, x) => { const to = p.to ?? p.level, from = p.from ?? (p.to != null ? p.level * 0.4 : p.level); return from + (to - from) * clamp(x, 0, 1); };
  // A buffer of little sounds scattered at random, for rain and crackle: made once, then looped.
  const scattered = {};
  function scatter(name, seconds, perSecond, make) {
    if (scattered[name]) return scattered[name];
    const sr = AC.sampleRate, len = Math.floor(sr * seconds), buf = AC.createBuffer(2, len, sr), r = rng(name.length * 7919 + 3);
    for (let k = 0; k < seconds * perSecond; k++) make(buf.getChannelData(k % 2), Math.floor(r() * len), len, sr, r);
    return (scattered[name] = buf);
  }
  function bed(buf, t0, dur, now, B, level, fade, shape) {   // a looped bed of sound with a fade at both ends
    const start = Math.max(t0, now), end = t0 + dur, src = AC.createBufferSource(), g = gain(0);
    src.buffer = buf; src.loop = true; src.start(start, (start - t0) % buf.duration); src.stop(end + 0.05);
    g.gain.setValueAtTime(0, start); g.gain.linearRampToValueAtTime(level, Math.min(end, start + (start > t0 ? 0.3 : fade)));
    g.gain.setValueAtTime(level, Math.max(start, end - fade)); g.gain.linearRampToValueAtTime(0, end);
    let node = src;
    if (shape) { node.connect(shape); node = shape; }
    node.connect(g); into(g, B, 0.1);
  }
  const FILM = {
    // A long low tone that hangs under the music. tone: dark, warm, eerie or bright.
    drone(t0, dur, p, B, now) {
      const f = hz(pitchOf(p.note, 38)), tone = p.tone || 'dark', A = 0.19 * p.level;
      const cut = { dark: 340, warm: 560, eerie: 460, bright: 1500 }[tone] || 340;
      const start = Math.max(t0, now), rel = Math.min(4, Math.max(1.2, dur * 0.3)), end = t0 + dur + rel * 0.5, att = start > t0 ? 0.4 : Math.min(3, dur * 0.3);
      const out = gain(0), lp = lowpass(cut), sway = AC.createOscillator(), swayBy = gain(cut * 0.25);
      out.gain.setValueAtTime(0, start); out.gain.linearRampToValueAtTime(A, start + att);
      out.gain.setValueAtTime(A, Math.max(start + att, t0 + dur - rel * 0.5)); out.gain.linearRampToValueAtTime(0, end);
      lp.Q.value = 0.8; sway.frequency.value = 0.07; sway.connect(swayBy); swayBy.connect(lp.frequency); sway.start(start); sway.stop(end);
      const drift = AC.createOscillator(), driftBy = gain(tone === 'eerie' ? 9 : 3); drift.frequency.value = 0.05; drift.connect(driftBy); drift.start(start); drift.stop(end);
      const part = (type, ratio, cents, level, side) => {
        const o = AC.createOscillator(), g = gain(level), pn = panner(side);
        o.type = type; o.frequency.value = f * ratio; o.detune.value = cents; driftBy.connect(o.detune);
        o.connect(g); g.connect(pn); pn.connect(lp); o.start(start); o.stop(end);
      };
      part('sawtooth', 1, -7, 0.55, -0.45); part('sawtooth', 1, 7, 0.55, 0.45); part('sine', 0.5, 0, 0.35, 0); part('triangle', 2, 3, 0.2, 0);
      if (tone === 'warm') part('triangle', 1.5, 0, 0.3, 0.2);
      if (tone === 'eerie') { part('sine', 2 * Math.pow(2, 1 / 12), 0, 0.22, -0.3); part('sine', 3 * Math.pow(2, 6 / 12) / 1.5, 4, 0.12, 0.3); }
      if (tone === 'bright') { part('sawtooth', 2, 5, 0.3, -0.2); part('sawtooth', 3, -4, 0.14, 0.2); }
      lp.connect(out); into(out, B, 0.3);
    },
    // Noise and a rising tone that climb to the bar where they end. tone: air (noise only), tone, or both.
    riser(t0, dur, p, B, now) {
      const start = Math.max(t0, now), end = t0 + dur, x0 = (start - t0) / dur, A = 0.42 * p.level;
      if (end - start < 0.08) return;
      const out = gain(0); out.gain.setValueCurveAtTime(power(A, 1.6, x0), start, end - start);
      out.gain.setValueAtTime(A, end); out.gain.linearRampToValueAtTime(0, end + 0.04);
      if (p.tone !== 'tone') {
        const src = noiseLoop(start, end + 0.06, 11), bp = filter('bandpass', 200, 1.3);
        bp.frequency.setValueAtTime(180 * Math.pow(7000 / 180, x0), start); bp.frequency.exponentialRampToValueAtTime(7000, end);
        src.connect(bp); bp.connect(out);
      }
      if (p.tone !== 'air') {
        const f = hz(pitchOf(p.note, 45)), o = AC.createOscillator(), o2 = AC.createOscillator(), g = gain(0.3), lp = lowpass(3200);
        o.type = o2.type = 'sawtooth'; o2.detune.value = 9;
        [o, o2].forEach(n => { n.frequency.setValueAtTime(f * Math.pow(4, x0), start); n.frequency.exponentialRampToValueAtTime(f * 4, end); n.connect(lp); n.start(start); n.stop(end + 0.06); });
        lp.connect(g); g.connect(out);
      }
      into(out, B, 0.4);
    },
    // A breath of bright noise that grows and stops dead: the sound of a cymbal played backwards.
    swell(t0, dur, p, B, now) {
      const start = Math.max(t0, now), end = t0 + dur, x0 = (start - t0) / dur, A = 0.17 * p.level;
      if (end - start < 0.08) return;
      const out = gain(0); out.gain.setValueCurveAtTime(power(A, 2.2, x0), start, end - start);
      out.gain.setValueAtTime(A, end); out.gain.linearRampToValueAtTime(0, end + 0.03);
      const src = noiseLoop(start, end + 0.05, 23), hp = filter('highpass', 2600, 0.6), pk = filter('peaking', 6500, 1); pk.gain.value = 6;
      src.connect(hp); hp.connect(pk); pk.connect(out); into(out, B, 0.45);
    },
    // A deep hit. size: small, big or huge.
    impact(t0, dur, p, B) {
      const [amp, ring] = { small: [0.3, 1.1], big: [0.5, 2.2], huge: [0.68, 3.6] }[p.size] || [0.5, 2.2], A = amp * p.level;
      const out = gain(1), o = AC.createOscillator(), og = gain(0), w = warm();
      o.frequency.setValueAtTime(110, t0); o.frequency.exponentialRampToValueAtTime(36, t0 + 0.32);
      og.gain.setValueAtTime(0, t0); og.gain.linearRampToValueAtTime(A, t0 + 0.004); og.gain.exponentialRampToValueAtTime(0.0008, t0 + ring);
      o.connect(w); w.connect(og); og.connect(out); o.start(t0); o.stop(t0 + ring + 0.05);
      const body = noiseLoop(t0, t0 + 0.9, 5), blp = lowpass(1500), bg = gain(0);
      blp.frequency.setValueAtTime(1500, t0); blp.frequency.exponentialRampToValueAtTime(160, t0 + 0.45);
      bg.gain.setValueAtTime(A * 0.75, t0); bg.gain.exponentialRampToValueAtTime(0.0008, t0 + 0.8);
      body.connect(blp); blp.connect(bg); bg.connect(out);
      const crack = noiseLoop(t0, t0 + 0.12, 9), chp = filter('highpass', 2000, 0.7), cg = gain(0);
      cg.gain.setValueAtTime(A * 0.28, t0); cg.gain.exponentialRampToValueAtTime(0.0008, t0 + 0.08);
      crack.connect(chp); chp.connect(cg); cg.connect(out);
      into(out, B, 0.7);
    },
    // A soft deep boom, felt more than heard.
    boom(t0, dur, p, B) {
      const A = 0.5 * p.level, o = AC.createOscillator(), g = gain(0), w = warm(), lp = lowpass(300);
      o.frequency.setValueAtTime(80, t0); o.frequency.exponentialRampToValueAtTime(36, t0 + 0.5);
      g.gain.setValueAtTime(0, t0); g.gain.linearRampToValueAtTime(A, t0 + 0.014); g.gain.exponentialRampToValueAtTime(0.0008, t0 + 2.6);
      o.connect(w); w.connect(lp); lp.connect(g); o.start(t0); o.stop(t0 + 2.7); into(g, B, 0.3);
    },
    // A heartbeat (lub-dub) at `bpm`, growing from `from` to `to`.
    heartbeat(t0, dur, p, B, now) {
      const T = 60 / clamp(+p.bpm || 60, 36, 170);
      const thump = (at, f0, f1, len, A) => {
        const o = AC.createOscillator(), g = gain(0), w = warm(), lp = lowpass(240);
        o.frequency.setValueAtTime(f0, at); o.frequency.exponentialRampToValueAtTime(f1, at + len);
        g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(A, at + 0.005); g.gain.exponentialRampToValueAtTime(0.0008, at + len * 2.4);
        o.connect(w); w.connect(lp); lp.connect(g); o.start(at); o.stop(at + len * 2.5); into(g, B, 0.12);
      };
      for (let at = t0, k = 0; at < t0 + dur - 0.05 && k < 2000; at += T, k++) {
        if (at < now - 0.001) continue;
        const A = 0.29 * grow(p, (at - t0) / dur);
        thump(at, 66, 40, 0.1, A); thump(at + Math.min(0.3, T * 0.3), 76, 47, 0.085, A * 0.62);
      }
    },
    // A low note repeated in time with the music: tension that ticks along. rate: notes in a bar (4, 8 or 16).
    pulse(t0, dur, p, B, now, beat) {
      const per = clamp(Math.round(+p.rate || 8), 2, 16), gap = beat * 4 / per, f = hz(pitchOf(p.note, 38));
      for (let at = t0, k = 0; at < t0 + dur - 0.02 && k < 4000; at += gap, k++) {
        if (at < now - 0.001) continue;
        const A = 0.09 * grow(p, (at - t0) / dur) * (k % Math.max(1, per / 4) === 0 ? 1 : 0.68), len = Math.min(0.3, gap * 0.9);
        const o = AC.createOscillator(), o2 = AC.createOscillator(), lp = lowpass(900), g = gain(0);
        o.type = 'sawtooth'; o2.type = 'square'; o.frequency.value = f; o2.frequency.value = f * 1.004; lp.Q.value = 3;
        lp.frequency.setValueAtTime(950, at); lp.frequency.exponentialRampToValueAtTime(190, at + 0.13);
        g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(A, at + 0.004); g.gain.exponentialRampToValueAtTime(0.0008, at + len);
        o.connect(lp); o2.connect(lp); lp.connect(g); o.start(at); o2.start(at); o.stop(at + len + 0.02); o2.stop(at + len + 0.02); into(g, B, 0.12);
      }
    },
    // A clock: tick, tock, on every beat (rate: ticks in a bar).
    tick(t0, dur, p, B, now, beat) {
      const gap = beat * 4 / clamp(Math.round(+p.rate || 4), 1, 16);
      for (let at = t0, k = 0; at < t0 + dur - 0.02 && k < 4000; at += gap, k++) {
        if (at < now - 0.001) continue;
        const A = 0.16 * grow(p, (at - t0) / dur), src = noiseLoop(at, at + 0.03, 40 + k), bp = filter('bandpass', k % 2 ? 2300 : 3100, 9), g = gain(0);
        g.gain.setValueAtTime(A * 4.2, at); g.gain.exponentialRampToValueAtTime(0.0008, at + 0.022);
        src.connect(bp); bp.connect(g); into(g, B, 0.1);
      }
    },
    // Wind that rises and falls in gusts.
    wind(t0, dur, p, B, now) {
      const start = Math.max(t0, now), end = t0 + dur, span = end - start, fade = Math.min(2.5, dur * 0.3);
      if (span < 0.3) return;
      const n = Math.max(4, Math.ceil(span / 0.9)), r = rng(Math.round(t0 * 100) + 5);
      [-0.55, 0.55].forEach((side, s) => {
        const src = noiseLoop(start, end + 0.05, 60 + s), bp = filter('bandpass', 600, 0.7), g = gain(0), pn = panner(side);
        const fc = new Float32Array(n), gc = new Float32Array(n);
        for (let i = 0; i < n; i++) {
          const at = start + span * i / (n - 1), env = Math.min(1, (at - t0) / fade + (start > t0 ? 1 : 0), (end - at) / fade);
          fc[i] = 320 + 800 * Math.pow(r(), 1.3); gc[i] = 0.5 * p.level * (0.35 + 0.65 * r()) * Math.max(0, env);
        }
        if (start > t0) gc[0] = 0;
        bp.frequency.setValueCurveAtTime(fc, start, span); g.gain.setValueCurveAtTime(gc, start, span);
        src.connect(bp); bp.connect(g); g.connect(pn); into(pn, B, 0.15);
      });
    },
    // Steady rain.
    rain(t0, dur, p, B, now) {
      const buf = scatter('rain', 5, 260, (d, at, len, sr, r) => {
        const f = 1500 + 4500 * r(), amp = 0.5 * r() * r(), n = Math.floor(sr * (0.004 + 0.006 * r()));
        for (let i = 0; i < n; i++) d[(at + i) % len] += amp * Math.sin(2 * Math.PI * f * i / sr) * Math.exp(-5 * i / n);
      });
      bed(buf, t0, dur, now, B, 0.9 * p.level, Math.min(2, dur * 0.3));
      bed(wideNoise, t0, dur, now, B, 0.09 * p.level, Math.min(2, dur * 0.3), filter('highpass', 2200, 0.5));
    },
    // The sea: slow waves breaking.
    waves(t0, dur, p, B, now) {
      const start = Math.max(t0, now), end = t0 + dur, span = end - start, fade = Math.min(2.5, dur * 0.3);
      if (span < 0.5) return;
      const n = Math.max(8, Math.ceil(span / 0.4));
      [[lowpass(800), 0, 0.3, -0.3], [filter('highpass', 1800, 0.5), 1.3, 0.12, 0.3]].forEach(([shape, lag, level, side], s) => {
        const src = noiseLoop(start, end + 0.05, 80 + s), g = gain(0), pn = panner(side), gc = new Float32Array(n);
        for (let i = 0; i < n; i++) {
          const at = start + span * i / (n - 1), env = Math.min(1, (at - t0) / fade + (start > t0 ? 1 : 0), (end - at) / fade);
          gc[i] = level * p.level * (0.2 + 0.8 * Math.pow(0.5 - 0.5 * Math.cos(2 * Math.PI * (at - t0 - lag) / 8.3), 1.6)) * Math.max(0, env);
        }
        if (start > t0) gc[0] = 0;
        g.gain.setValueCurveAtTime(gc, start, span);
        src.connect(shape); shape.connect(g); g.connect(pn); into(pn, B, 0.15);
      });
    },
    // The crackle of a fire, or of an old record.
    crackle(t0, dur, p, B, now) {
      const buf = scatter('crackle', 4, 90, (d, at, len, sr, r) => {
        const amp = 0.9 * r() * r() * r(), n = Math.floor(sr * (0.0006 + 0.002 * r()));
        for (let i = 0; i < n; i++) d[(at + i) % len] += amp * (r() * 2 - 1) * Math.exp(-6 * i / n);
      });
      bed(buf, t0, dur, now, B, 1.6 * p.level, Math.min(1.5, dur * 0.3), lowpass(5200));
    },
    // A low rumble that grows: something large is coming.
    rumble(t0, dur, p, B, now) {
      const start = Math.max(t0, now), end = t0 + dur;
      if (end - start < 0.2) return;
      const src = noiseLoop(start, end + 0.3, 97), lp = lowpass(75), w = warm(), drive = gain(6), lp2 = lowpass(210), g = gain(0);
      lp.Q.value = 1.2; g.gain.setValueAtTime(0, start); g.gain.linearRampToValueAtTime(0.12 * grow(p, (start - t0) / dur + 0.02), start + 0.3);
      g.gain.linearRampToValueAtTime(0.12 * grow(p, 1), end); g.gain.linearRampToValueAtTime(0, end + 0.25);
      src.connect(lp); lp.connect(drive); drive.connect(w); w.connect(lp2); lp2.connect(g); into(g, B, 0.2);
    },
    // Something rushing past, at the bar it is given.
    whoosh(t0, dur, p, B, now) {
      const start = t0 - 0.75, end = t0 + 0.55, A = 0.5 * p.level;
      if (start < now - 0.05) return;
      const src = noiseLoop(start, end + 0.05, 131), bp = filter('bandpass', 250, 1.2), g = gain(0), pn = panner(-0.8);
      bp.frequency.setValueAtTime(250, start); bp.frequency.exponentialRampToValueAtTime(4200, t0); bp.frequency.exponentialRampToValueAtTime(700, end);
      g.gain.setValueCurveAtTime(power(A, 2), start, 0.75); g.gain.setValueAtTime(A, t0); g.gain.linearRampToValueAtTime(0, end);
      if (pn.pan) { pn.pan.setValueAtTime(-0.8, start); pn.pan.linearRampToValueAtTime(0.8, end); }
      src.connect(bp); bp.connect(g); g.connect(pn); into(pn, B, 0.3);
    },
    // A scatter of small bright bells. tone: dark for a minor colour.
    shimmer(t0, dur, p, B, now) {
      const base = pitchOf(p.note, 74), steps = p.tone === 'dark' ? [0, 3, 5, 7, 10] : [0, 2, 4, 7, 9], r = rng(Math.round(t0 * 100) + 77);
      for (let at = t0, k = 0; at < t0 + dur && k < 1500; at += 0.14 + 0.3 * r(), k++) {
        const f = hz(base + steps[Math.floor(r() * 5)] + 12 * Math.floor(r() * 3)), A = 0.07 * p.level * (0.45 + 0.55 * r()), side = r() * 1.6 - 0.8;
        if (at < now - 0.001) continue;
        const o = AC.createOscillator(), mod = AC.createOscillator(), mg = gain(0), g = gain(0), pn = panner(side);
        o.frequency.value = f; mod.frequency.value = f * 3.01;
        mg.gain.setValueAtTime(f * 1.2, at); mg.gain.exponentialRampToValueAtTime(1, at + 0.5);
        g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(A, at + 0.003); g.gain.exponentialRampToValueAtTime(0.0005, at + 1.6);
        mod.connect(mg); mg.connect(o.frequency); o.connect(g); g.connect(pn);
        o.start(at); mod.start(at); o.stop(at + 1.7); mod.stop(at + 1.7); into(pn, B, 0.5);
      }
    },
    // One toll of a large bell.
    bell(t0, dur, p, B) {
      const f = hz(pitchOf(p.note, 48)), A = 0.13 * p.level, out = gain(1);
      [[0.5, 0.3], [1, 1], [1.2, 0.5], [1.5, 0.35], [2, 0.6], [2.5, 0.2], [2.67, 0.25], [3, 0.15], [4, 0.1]].forEach(([ratio, amp]) => {
        const o = AC.createOscillator(), g = gain(0), ring = 9 / (0.6 + ratio * 0.5);
        o.frequency.value = f * ratio;
        g.gain.setValueAtTime(0, t0); g.gain.linearRampToValueAtTime(A * amp, t0 + 0.004); g.gain.exponentialRampToValueAtTime(0.0005, t0 + ring);
        o.connect(g); g.connect(out); o.start(t0); o.stop(t0 + ring + 0.05);
      });
      const strike = noiseLoop(t0, t0 + 0.05, 151), bp = filter('bandpass', 1200, 1), sg = gain(0);
      sg.gain.setValueAtTime(A * 1.2, t0); sg.gain.exponentialRampToValueAtTime(0.0008, t0 + 0.035);
      strike.connect(bp); bp.connect(sg); sg.connect(out); into(out, B, 0.45);
    },
    // A clap of thunder that rolls away.
    thunder(t0, dur, p, B) {
      const A = 0.6 * p.level, r = rng(Math.round(t0 * 100) + 9), n = 40, gc = new Float32Array(n);
      for (let i = 0; i < n; i++) gc[i] = A * (0.35 + 0.65 * r()) * Math.exp(-3.2 * i / n);
      gc[n - 1] = 0;
      const src = noiseLoop(t0, t0 + 4.3, 171), lp = lowpass(170), w = warm(), drive = gain(4), lp2 = lowpass(420), g = gain(0);
      g.gain.setValueCurveAtTime(gc, t0, 4.2);
      src.connect(lp); lp.connect(drive); drive.connect(w); w.connect(lp2); lp2.connect(g);
      const crack = noiseLoop(t0, t0 + 0.4, 173), bp = filter('bandpass', 900, 0.5), cg = gain(0);
      cg.gain.setValueAtTime(A * 0.8, t0); cg.gain.exponentialRampToValueAtTime(0.0008, t0 + 0.3);
      crack.connect(bp); bp.connect(cg); into(g, B, 0.4); into(cg, B, 0.4);
    },
  };
  // Film sounds that are recordings (they come with the recorded drums and percussion): a tam-tam, a roll on a
  // hanging cymbal that grows into the bar where it ends, a huge drum, wind chimes, a scraped gong, a bowed
  // cymbal. filmReal says which recordings one of them plays and how; without the recordings each falls back on
  // one of the sounds made above.
  function filmReal(kind, p, dur) {
    const F = perc && perc.film;
    if (!F) return null;
    const lv = p.level, turn = Math.round(+p.bar || 0), layer = (layers, L) => layers && layers.length ? layerOf(layers, L) : null;
    const pick = (lay, k = 0) => lay && lay[(turn + k) % lay.length];
    switch (kind) {
      case 'gong': { const id = pick(layer(F.gong, 0.2 + lv * 0.62)); return id ? [{ id, gain: 0.55 * lv, wet: 0.5, pan: -0.15 }] : null; }
      case 'bigdrum': {
        const size = p.size || 'big', out = [];
        const drum = pick(layer(F.bigdrum, size === 'small' ? 0.6 : 0.95)), bass = pick(layer(F.bassdrum, size === 'huge' ? 0.95 : 0.8));
        if (size !== 'small' && drum) out.push({ id: drum, gain: (size === 'huge' ? 1.1 : 0.75) * lv, wet: 0.6, rate: size === 'huge' ? 0.8 : 1 });
        if (bass) out.push({ id: bass, gain: (size === 'small' ? 0.75 : size === 'huge' ? 1 : 0.62) * lv, wet: 0.6 });
        return out.length ? out : null;
      }
      case 'chimes': {
        const id = p.size === 'small' ? pick(layer(F.belltree, 0.9)) : p.tone === 'dark' ? F.chimes_down : F.chimes_up;
        return id ? [{ id, gain: 0.4 * lv, wet: 0.4, pan: 0.25 }] : null;
      }
      case 'scrape': return F.scrape ? [{ id: F.scrape, gain: 0.5 * lv, wet: 0.6, pan: -0.2 }] : null;
      case 'bow': { const id = dur && dur < 3 && F.bow_short ? F.bow_short : F.bow; return id ? [{ id, gain: 0.36 * lv, wet: 0.6, hold: dur || 6 }] : null; }
      case 'cymbal': {   // the roll whose growing is nearest in length to what is asked, sped up or slowed a little to fit
        if (!F.cymroll || !F.cymroll.length) return null;
        const want = Math.max(0.3, dur || 2);
        let best = F.cymroll[0];
        for (const r of F.cymroll) if (Math.abs(Math.log(r[1] / want)) < Math.abs(Math.log(best[1] / want))) best = r;
        const rate = clamp(best[1] / want, 0.8, 1.25);
        return [{ id: best[0], gain: 0.62 * lv, wet: 0.45, rate, lead: best[1] / rate, peakAt: want }];
      }
    }
    return null;
  }
  // Plays what filmReal chose. `peakAt` and `lead` place a sound so that its loudest moment falls where it is aimed.
  function playReal(list, t0, B, now) {
    let played = false;
    for (const e of list) {
      const buf = percBufs[e.id];
      if (!buf) continue;
      const rate = e.rate || 1, start = t0 + (e.peakAt || 0) - (e.lead || 0), at = Math.max(start, now), skip = (at - start) * rate;
      played = true;
      if (skip > buf.duration - 0.05) continue;
      const src = AC.createBufferSource(), g = gain(e.gain), pn = panner(e.pan || 0);
      src.buffer = buf; src.playbackRate.value = rate; src.connect(g); g.connect(pn); into(pn, B, e.wet);
      if (skip > 0) { g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(e.gain, at + 0.05); }
      src.start(at, skip);
      if (e.hold) { const end = Math.max(at + 0.2, t0 + e.hold); g.gain.setValueAtTime(e.gain, end); g.gain.setTargetAtTime(0, end, 0.5); src.stop(end + 4); }
    }
    return played;
  }
  const FILM_REAL = {   // the recorded sound: the made sound that stands in for it, and with which settings
    gong: ['bell', p => ({ ...p, note: p.note || 'C2' })], cymbal: ['swell', p => p], bigdrum: ['impact', p => p],
    chimes: ['shimmer', p => ({ ...p, note: p.note || 'D6' })], scrape: ['riser', p => ({ ...p, tone: 'air' })],
    bow: ['drone', p => ({ ...p, note: p.note || 'A4', tone: 'eerie', level: p.level * 0.5 })],
  };
  for (const [kind, [standIn, settings]] of Object.entries(FILM_REAL)) {
    FILM[kind] = (t0, dur, p, B, now, beat) => {
      const list = filmReal(kind, p, dur);
      if (list && playReal(list, t0, B, now)) return;
      FILM[standIn](t0, dur || beat * 4, settings(p), B, now, beat);
    };
  }
  const FILM_ONCE = new Set(['impact', 'boom', 'whoosh', 'bell', 'thunder', 'gong', 'bigdrum', 'chimes', 'scrape']);
  const FILM_BARS = { drone: 4, riser: 2, swell: 1, heartbeat: 4, pulse: 4, tick: 4, wind: 8, rain: 8, waves: 8, crackle: 8, rumble: 2, shimmer: 2, cymbal: 2, bow: 4 };
  const FILM_LEAD = { whoosh: 0.8, cymbal: 1.1 };   // sounds that begin before the bar they are aimed at
  // The layer as moments of the song: [{ kind, t, dur, beat, p }], in seconds at the written tempo.
  function filmPlan(song, film) {
    if (!film || !Array.isArray(film.layer)) return [];
    const barTick = b => (Math.max(1, +b || 1) - 1) * song.ticksPerBar;
    return film.layer.map(e => {
      const kind = String((e && e.sound) || '').toLowerCase().replace(/[^a-z]/g, '');
      if (!FILM[kind]) return null;
      const bar = Math.max(1, +e.bar || 1), bars = FILM_ONCE.has(kind) ? 0 : Math.max(0.25, +e.bars || FILM_BARS[kind] || 2);
      const tick = barTick(bar), t = song.tickTime(tick);
      return { kind, t, dur: bars ? song.tickTime(barTick(bar + bars)) - t : 0, beat: song.tickTime(tick + song.div) - t,
        p: { ...e, level: clamp(e.level == null ? 0.7 : +e.level || 0, 0, 1.5), from: e.from == null ? undefined : +e.from, to: e.to == null ? undefined : +e.to } };
    }).filter(Boolean).sort((a, b) => a.t - b.t);
  }
  function filmBus() {
    const dry = gain(filmLevel), wet = gain(filmLevel);
    dry.connect(master); wet.connect(verb);
    return { dry, wet, close(at) { dry.gain.setTargetAtTime(0, at, 0.03); wet.gain.setTargetAtTime(0, at, 0.03); if (isLive) setTimeout(() => { dry.disconnect(); wet.disconnect(); }, 600); } };
  }
  function playFilm(e, B, when, rate, now) {
    try { FILM[e.kind](when, e.dur / rate, e.p, B, now, e.beat / rate); } catch (err) { /* one odd setting must not stop the music */ }
  }

  // ----- Score player -----
  let playing = null;
  function playScore(song, { onKey, onEnd, rate = 1, from = 0, film = null } = {}) {
    stopScore(); wake();
    // anchorT/anchorS pin a moment of clock time to a moment of song time, so the speed can change mid-play.
    const t0 = AC.currentTime + 0.12, st = { i: 0, voices: new Set(), timers: new Set(), rate, anchorT: t0, anchorS: from, song, f: 0, plan: filmPlan(song, film), bus: null };
    while (st.i < song.notes.length && song.notes[st.i].t < from - 0.001) st.i++;
    const at = s => st.anchorT + (s - st.anchorS) / st.rate;
    const later = (fn, when) => { const id = setTimeout(() => { st.timers.delete(id); fn(); }, Math.max(0, (when - AC.currentTime) * 1000)); st.timers.add(id); };
    const time = tick => at(song.tickTime(tick));
    const tick = () => {
      while (st.f < st.plan.length && at(st.plan[st.f].t) - (FILM_LEAD[st.plan[st.f].kind] || 0) < AC.currentTime + 0.25) {
        const e = st.plan[st.f++];
        if (!st.bus) st.bus = filmBus();
        if (at(e.t) + e.dur / st.rate > AC.currentTime + 0.1 || (!e.dur && at(e.t) > AC.currentTime - 0.05)) playFilm(e, st.bus, at(e.t), st.rate, AC.currentTime + 0.02);
      }
      while (st.i < song.notes.length && at(song.notes[st.i].t) < AC.currentTime + 0.25) {
        const n = song.notes[st.i++], when = at(n.t), dur = n.dur / st.rate;
        if (n.ch === 9) { scoreDrum(song, n, when, dur, time); continue; }
        const v = scoreVoice(song, n, when, dur, time);
        v.stop(when + dur); st.voices.add(v);
        later(() => onKey && onKey(n.note, true), when);
        later(() => { st.voices.delete(v); onKey && onKey(n.note, false); }, when + Math.max(0.06, dur - 0.02));
      }
      if (st.i >= song.notes.length && AC.currentTime > at(song.length) + 0.3) { stopScore(true); onEnd && onEnd(); }
    };
    st.interval = setInterval(tick, 40); st.onKey = onKey; playing = st; tick();
    return t0;
  }
  function setScoreRate(rate) {
    if (!playing || !(rate > 0)) return;
    const now = AC.currentTime;
    playing.anchorS += (now - playing.anchorT) * playing.rate; playing.anchorT = now; playing.rate = rate;
  }
  let lastSong = null;
  function stopScore(ended) {
    if (!playing) return;
    const st = playing;
    clearInterval(st.interval);
    st.timers.forEach(clearTimeout);
    if (!ended) { st.voices.forEach(v => v.stop(AC.currentTime, true)); if (st.bus) st.bus.close(AC.currentTime); }
    playing = null;
    // The seats of the song before this one are cleared away; this one keeps its seats while its last notes ring.
    if (lastSong && lastSong !== st.song) freeStrips(lastSong);
    lastSong = st.song;
  }

  // Get every sampled instrument in a song ready (fetched and decoded) before it plays. Staves without an
  // instrument of their own play on the keyboard's sound, so that one is made ready too when it is sampled.
  async function prepare(song, film) {
    await percussion;
    const real = [...drumSounds(song)].map(loadPerc);
    for (const e of filmPlan(song, film)) (FILM_REAL[e.kind] && filmReal(e.kind, e.p, e.dur) || []).forEach(x => real.push(loadPerc(x.id)));
    if (kit.recorded) real.push(warmKit());
    const want = new Map(), keys = current.startsWith('gm:') ? +current.slice(3) : 0;
    const add = (p, note) => { if (!want.has(p)) want.set(p, new Set()); want.get(p).add(note); };
    for (const n of song.notes) {
      const p = n.ch === 9 ? 0 : n.prog > 0 ? n.prog : keys;
      if (p > 0) add(p, n.note);
      if (STRING_PROGS.has(p) && song.dyn && song.dyn[n.tr] && song.dyn[n.tr].rolls.size) {   // a string tremolo has its own recording
        const d = song.dyn[n.tr];
        if (d.rolls.has(n.tick) || d.rolls.has(n.tick - 1) || d.rolls.has(n.tick + 1)) add(TREMOLO, n.note);
      }
    }
    const ok = await Promise.all([...want].map(([p, set]) => warmInstrument(p, [...set]).then(r => r || p === TREMOLO)));
    await Promise.all(real);
    return ok.every(Boolean);
  }

  // ----- A whole piece ahead of time -----
  // The live player above feeds notes in as the clock runs. These two put every note and every drum hit on
  // the timeline at once, which is what an OfflineAudioContext needs to turn a piece into an audio file.
  function schedule(song, { rate = 1, at = 0, film = null } = {}) {
    const time = tick => at + song.tickTime(tick) / rate;
    for (const n of song.notes) {
      const when = at + n.t / rate, dur = n.dur / rate;
      if (n.ch === 9) { scoreDrum(song, n, when, dur, time); continue; }
      scoreVoice(song, n, when, dur, time).stop(when + dur);
    }
    const plan = filmPlan(song, film);
    if (plan.length) { const B = filmBus(); plan.forEach(e => playFilm(e, B, at + e.t / rate, rate, 0)); }
    return plan.length;
  }
  const BEAT_TRACKS = ['kick', 'snare', 'hat', 'openhat', 'clap', 'tom', 'rim', 'cowbell'], BEAT_VEL = [0, 0.62, 1, 0.3];
  function scheduleBeat(b, tempo, from, until) {   // b: { bars, swing, tracks: { kick: [0, 1, 2, 3, ...], ... } }
    const dur = 60 / tempo / 4, total = b.bars * 16;
    for (let i = 0, t = from; t < until - 1e-6; i++, t += dur) {
      const step = i % total, when = t + (step % 2 ? (b.swing / 100) * dur : 0);
      for (const k of BEAT_TRACKS) { const v = b.tracks[k][step]; if (v) drum(k, when, BEAT_VEL[v]); }
    }
  }
  const instrumentName = p => GM.LABEL[p] || 'Instrument ' + p;

  return {
    prepare, instrumentName, warmInstrument, schedule, scheduleBeat, get pianoReady() { return samplesReady; },
    AC, SOUNDS, KITS, SPACES, FILM_SOUNDS: Object.keys(FILM), percussion, loadPiano, noteOn, noteOff, setSustain, drum, setKit, setSpace, parseMidi, playScore, stopScore, setScoreRate,
    get now() { return AC.currentTime; },
    get isPlaying() { return !!playing; },
    get scorePos() { return playing ? Math.max(0, playing.anchorS + (AC.currentTime - playing.anchorT) * playing.rate) : 0; },
    setSound(k) { current = SOUNDS.some(([s]) => s === k) ? k : 'grand'; if (current.startsWith('gm:')) warmInstrument(+current.slice(3)); },
    setVolume(v) { master.gain.value = v; },
    setDesk,
  };
}
// The engine the pages play with. A page that only renders audio sets NO_LIVE_SOUND and builds its own.
const Sound = window.NO_LIVE_SOUND ? null : makeSound(new (window.AudioContext || window.webkitAudioContext)({ latencyHint: 'interactive' }), true);
