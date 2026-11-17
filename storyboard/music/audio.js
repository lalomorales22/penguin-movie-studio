// The Music studio's sound engine (from LilyKeys): sampled grand, sampled General MIDI instruments (the
// orchestra and band), synth voices, drum synth, MIDI file reader, score player. Needs instruments.js first.
//
// makeSound(AC, isLive) builds one engine on an audio context. The pages use the live one below (Sound).
// render.js builds another on an OfflineAudioContext to play a whole piece into an audio file.
function makeSound(AC, isLive) {
  const master = AC.createGain();
  const comp = AC.createDynamicsCompressor();
  comp.threshold.value = -14; comp.ratio.value = 4;
  master.connect(comp); comp.connect(AC.destination);

  // Keys chain: voices -> drive -> filter -> (+chorus) -> out.  Drums: hits -> crunch -> tone -> out.
  const lowpass = f => { const n = AC.createBiquadFilter(); n.type = 'lowpass'; n.frequency.value = f; return n; };
  const gain = v => { const n = AC.createGain(); n.gain.value = v; return n; };
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

  const verb = AC.createConvolver(), keysRoom = gain(0.18), drumRoom = gain(0);
  instOut.connect(keysRoom); keysRoom.connect(verb); drumOut.connect(drumRoom); drumRoom.connect(verb); verb.connect(master);

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
    room: v => { keysRoom.gain.value = v * 0.9; },
    dlevel: v => { drumOut.gain.value = v; },
    dtone: v => { knobTone = v; applyTone(); },
    ddrive: v => { drumDrive.curve = driveCurve(v); },
    dtune: v => { tune = Math.pow(2, v / 12); },
    droom: v => { drumRoom.gain.value = v * 0.8; },
    decho: v => { drumEcho.gain.value = v * 0.7; },
    echoTime: v => echo.delayTime.setTargetAtTime(Math.max(0.03, Math.min(1.9, v)), AC.currentTime, 0.05),
  };
  function setDesk(id, v) {
    if (id.startsWith('mix_')) levels[id.slice(4)] = v;
    else if (DESK[id]) DESK[id](v);
  }

  (function makeRoom() {
    const len = AC.sampleRate * 2.8, buf = AC.createBuffer(2, len, AC.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3.2);
    }
    verb.buffer = buf;
  })();

  const noise = AC.createBuffer(1, AC.sampleRate, AC.sampleRate);
  { const d = noise.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1; }

  const wake = () => { if (isLive && AC.state !== 'running') AC.resume(); };
  if (isLive) ['pointerdown', 'keydown'].forEach(ev => window.addEventListener(ev, wake, { capture: true }));

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

  function pianoVoice(m, vel, t) {
    let s = 21 + 3 * Math.round((m - 21) / 3);
    s = Math.max(21, Math.min(108, s));
    const buf = samples[s];
    if (!buf) return synthVoice(PRESETS.epiano, m, vel, t);
    const src = AC.createBufferSource(), lp = AC.createBiquadFilter(), g = AC.createGain();
    src.buffer = buf; src.playbackRate.value = Math.pow(2, (m - s) / 12);
    lp.type = 'lowpass'; lp.frequency.value = 700 + vel * vel * 15000;
    g.gain.value = 0.2 + vel * vel * 1.2;
    src.connect(lp); lp.connect(g); g.connect(instBus);
    src.start(t);
    return {
      stop(tr, fast) {
        tr = Math.max(tr, t + 0.02);
        const tc = fast ? 0.02 : 0.11;
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
  function gmVoice(prog, m, vel, t, pan) {
    const fam = GM.family(prog), out = seatFor(prog, pan), bank = banks[prog];
    const s = bank && nearest(bank.buf, m);
    if (s == null || Math.abs(s - m) > 6) {
      if (bank) decodeNote(prog, m);
      return synthVoice(FALLBACK[fam.fallback] || PRESETS.epiano, m, vel, t, out);
    }
    const buf = bank.buf[s], src = AC.createBufferSource(), g = AC.createGain(), peak = 0.25 + vel * vel * 1.1;
    src.buffer = buf; src.playbackRate.value = Math.pow(2, (m - s) / 12);
    // Held notes on bowed, blown and sung instruments loop the steady middle of the recording.
    if (fam.sustain && buf.duration > 0.8) { src.loop = true; src.loopStart = buf.duration * 0.4; src.loopEnd = buf.duration * 0.85; }
    const attack = fam.sustain ? 0.02 : 0.003;
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(peak, t + attack);
    src.connect(g); g.connect(out); src.start(t);
    return {
      stop(tr, fast) {
        tr = Math.max(tr, t + attack + 0.01);
        const tc = fast ? 0.02 : fam.sustain ? 0.09 : 0.18;
        g.gain.cancelScheduledValues(tr); g.gain.setTargetAtTime(0, tr, tc);
        try { src.stop(tr + tc * 8); } catch { /* already stopped */ }
      },
    };
  }

  // Sounds for the keyboard: built-in keys and synths, then sampled instruments ('gm:<program>').
  const SOUNDS = [['grand', 'Grand piano', 'Keys and synths'], ...Object.entries(PRESETS).map(([k, v]) => [k, v.label, 'Keys and synths']),
    ...GM.KEYBOARD.flatMap(([group, progs]) => progs.map(p => ['gm:' + p, GM.LABEL[p], group]))];
  let current = 'grand';
  const voice = (m, vel, t) => current === 'grand' ? pianoVoice(m, vel, t)
    : current.startsWith('gm:') ? gmVoice(+current.slice(3), m, vel, t)
    : synthVoice(PRESETS[current] || PRESETS.epiano, m, vel, t);
  // In a score, a staff with its own midiInstrument plays that instrument; staves without one
  // (or marked as a grand piano) play whatever sound the keyboard is set to.
  const scoreVoice = n => n.prog > 0 ? gmVoice(n.prog, n.note, 0.25 + n.vel * 0.7, n.when, n.pan) : voice(n.note, 0.25 + n.vel * 0.7, n.when);

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
  let kit = KITS['808'], openHatGain = null;
  function setKit(name) { kit = KITS[name] || KITS['808']; applyTone(); }

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

  // ----- Standard MIDI file reader -----
  function parseMidi(buffer) {
    const d = new DataView(buffer); let p = 0;
    const u8 = () => d.getUint8(p++);
    const tag = () => String.fromCharCode(u8(), u8(), u8(), u8());
    const u32 = () => { const v = d.getUint32(p); p += 4; return v; };
    const u16 = () => { const v = d.getUint16(p); p += 2; return v; };
    const varlen = () => { let v = 0, b; do { b = u8(); v = (v << 7) | (b & 0x7f); } while (b & 0x80); return v; };
    if (tag() !== 'MThd') throw new Error('Not a MIDI file');
    const hlen = u32(); u16(); const ntr = u16(), div = u16(); p += hlen - 6;
    const evs = [], names = {};
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
    const open = new Map(), notes = [], prog = {}, pan = {}, parts = new Map();
    for (const e of evs) {
      time += (e.tick - lastTick) * us / div / 1e6; lastTick = e.tick;
      if (e.kind === 3) { total = Math.max(total, time); continue; }
      if (e.kind === 0) {
        if (e.us) { us = e.us; if (bpm == null) bpm = 6e7 / us; }
        // Kept per track as well as per channel: LilyPond writes one track per staff, and when a big
        // score runs out of channels two staves can share one, each with its own instrument.
        else if (e.prog != null) prog[e.ch] = prog[e.tr + ':' + e.ch] = e.prog;
        else if (e.pan != null) pan[e.ch] = pan[e.tr + ':' + e.ch] = e.pan;
        continue;
      }
      const key = e.tr + ':' + e.ch + ':' + e.note;
      if (e.kind === 2) {
        const n = { t: time, dur: 0.25, ch: e.ch, note: e.note, vel: e.vel / 127, prog: e.ch === 9 ? -1 : (prog[e.tr + ':' + e.ch] ?? prog[e.ch] ?? 0), pan: pan[e.tr + ':' + e.ch] ?? pan[e.ch] };
        if (e.ch !== 9 && !parts.has(n.prog)) parts.set(n.prog, names[e.tr] || '');
        notes.push(n); open.set(key, n);
      } else if (open.has(key)) { const n = open.get(key); n.dur = Math.max(0.03, time - n.t); open.delete(key); }
    }
    notes.sort((a, b) => a.t - b.t);
    // parts: the instruments that play, as [program, name]. Program 0 plays on the keyboard's sound.
    // length is where the last note ends; total is where the written music ends (never shorter).
    const length = notes.reduce((mx, n) => Math.max(mx, n.t + n.dur), 0);
    return { notes, bpm: bpm || 120, length, total: Math.max(total, length), parts: [...parts] };
  }

  // ----- Score player -----
  let playing = null;
  function playScore(song, { onKey, onEnd, rate = 1, from = 0 } = {}) {
    stopScore(); wake();
    // anchorT/anchorS pin a moment of clock time to a moment of song time, so the speed can change mid-play.
    const t0 = AC.currentTime + 0.12, st = { i: 0, voices: new Set(), timers: new Set(), rate, anchorT: t0, anchorS: from };
    while (st.i < song.notes.length && song.notes[st.i].t < from - 0.001) st.i++;
    const at = s => st.anchorT + (s - st.anchorS) / st.rate;
    const later = (fn, when) => { const id = setTimeout(() => { st.timers.delete(id); fn(); }, Math.max(0, (when - AC.currentTime) * 1000)); st.timers.add(id); };
    const tick = () => {
      while (st.i < song.notes.length && at(song.notes[st.i].t) < AC.currentTime + 0.25) {
        const n = song.notes[st.i++], when = at(n.t), dur = n.dur / st.rate;
        if (n.ch === 9) { const dn = GM_DRUM[n.note]; if (dn) drum(dn, when, 0.4 + n.vel * 0.6); continue; }
        const v = scoreVoice({ ...n, when });
        v.stop(when + dur); st.voices.add(v);
        later(() => onKey && onKey(n.note, true), when);
        later(() => { st.voices.delete(v); onKey && onKey(n.note, false); }, when + Math.max(0.06, dur - 0.02));
      }
      if (st.i >= song.notes.length && AC.currentTime > at(song.length) + 0.3) { stopScore(); onEnd && onEnd(); }
    };
    st.interval = setInterval(tick, 40); st.onKey = onKey; playing = st; tick();
    return t0;
  }
  function setScoreRate(rate) {
    if (!playing || !(rate > 0)) return;
    const now = AC.currentTime;
    playing.anchorS += (now - playing.anchorT) * playing.rate; playing.anchorT = now; playing.rate = rate;
  }
  function stopScore() {
    if (!playing) return;
    clearInterval(playing.interval);
    playing.timers.forEach(clearTimeout);
    playing.voices.forEach(v => v.stop(AC.currentTime, true));
    playing = null;
  }

  // Get every sampled instrument in a song ready (fetched and decoded) before it plays. Staves without an
  // instrument of their own play on the keyboard's sound, so that one is made ready too when it is sampled.
  async function prepare(song) {
    const want = new Map(), keys = current.startsWith('gm:') ? +current.slice(3) : 0;
    for (const n of song.notes) {
      const p = n.ch === 9 ? 0 : n.prog > 0 ? n.prog : keys;
      if (p > 0) { if (!want.has(p)) want.set(p, new Set()); want.get(p).add(n.note); }
    }
    const ok = await Promise.all([...want].map(([p, set]) => warmInstrument(p, [...set])));
    return ok.every(Boolean);
  }

  // ----- A whole piece ahead of time -----
  // The live player above feeds notes in as the clock runs. These two put every note and every drum hit on
  // the timeline at once, which is what an OfflineAudioContext needs to turn a piece into an audio file.
  function schedule(song, { rate = 1, at = 0 } = {}) {
    for (const n of song.notes) {
      const when = at + n.t / rate, dur = n.dur / rate;
      if (n.ch === 9) { const dn = GM_DRUM[n.note]; if (dn) drum(dn, when, 0.4 + n.vel * 0.6); continue; }
      scoreVoice({ ...n, when }).stop(when + dur);
    }
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
    AC, SOUNDS, KITS, loadPiano, noteOn, noteOff, setSustain, drum, setKit, parseMidi, playScore, stopScore, setScoreRate,
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
