// The Music studio (LilyKeys): keyboard, score editor, beat machine, Claude panel.
const $ = id => document.getElementById(id);
const ed = $('ly');

const TEMPLATE = `\\version "2.26.0"

\\header {
  title = "First Light"
  composer = "you, with Claude"
  tagline = ##f
}

upper = \\relative c'' {
  \\key d \\minor \\time 4/4
  \\tempo "Gently" 4 = 84
  a4 f8 g a4 d | c4 bes8 a g2 | f4 a8 g f4 e | d2. r4 |
  a'4 f8 g a4 d | d4 c8 bes a2 | a4 g8 f e4 cis | d1 \\bar "|."
}

dm = \\relative c { d8 a' d a d, a' d a }
gm = \\relative c { g8 d' g d g, d' g d }
am = \\relative c { a8 e' a e a, e' a e }
ending = \\relative c { d8 a' d a d,2 }

lower = {
  \\clef bass \\key d \\minor
  \\dm \\gm \\am \\ending
  \\dm \\gm \\am \\ending
}

\\score {
  \\new PianoStaff <<
    \\new Staff \\upper
    \\new Staff \\lower
  >>
  \\layout { }
  \\midi { }
}
`;

const TRACKS = [['kick', 'Kick'], ['snare', 'Snare'], ['hat', 'Hat'], ['openhat', 'Open hat'], ['clap', 'Clap'], ['tom', 'Tom'], ['rim', 'Rim'], ['cowbell', 'Cowbell']];
const GROOVES = {
  'Boom bap': { tempo: 88, swing: 22, kit: '808', tracks: { kick: 'X.......x.x.....', snare: '....X.......X...', hat: 'x.x.x.x.x.x.x.x.' } },
  'Four on the floor': { tempo: 124, swing: 0, kit: 'tight', tracks: { kick: 'X...X...X...X...', clap: '....x.......x...', hat: 'x.x.x.x.x.x.x.x.', openhat: '..x...x...x...x.' } },
  'Trap': { tempo: 140, swing: 0, kit: '808', tracks: { kick: 'X.....x...x.....', clap: '........X.......', hat: 'x.xxx.x.x.xxxxx.', openhat: '..............x.' } },
  'Bossa': { tempo: 120, swing: 0, kit: 'tight', tracks: { kick: 'x..x....x..x....', rim: 'x..x..x...x..x..', hat: 'x.x.x.x.x.x.x.x.' } },
  'Straight rock': { tempo: 112, swing: 0, kit: 'tight', tracks: { kick: 'X.......x.x.....', snare: '....X.......X...', hat: 'x.x.x.x.x.x.x.x.', tom: '..............x.' } },
  'Dembow': { tempo: 96, swing: 0, kit: '808', tracks: { kick: 'X...X...X...X...', snare: '...x..x....x..x.', hat: 'x.x.x.x.x.x.x.x.' } },
  'Dusty lo-fi': { tempo: 76, swing: 34, kit: 'lofi', tracks: { kick: 'X......x..x.....', snare: '....X.......X...', hat: 'x.xXx.x.x.xXx.x.', rim: '.......x........' } },
};

// Step values: 0 off, 1 hit, 2 accent, 3 ghost. A beat is 1, 2, 4 or 8 bars of 16 steps.
const CODES = { '.': 0, x: 1, X: 2, o: 3 }, CHARS = '.xXo', VEL = [0, 0.62, 1, 0.3];
const cleanTrack = s => String(s || '').replace(/[^xXo.]/g, '');
const parseTrack = (s, total) => { const c = cleanTrack(s); return Array.from({ length: total }, (_, i) => c.length ? CODES[c[i % c.length]] : 0); };
const rowToStr = r => r.map(v => CHARS[v]).join('');
const emptyTracks = (bars = 1) => Object.fromEntries(TRACKS.map(([k]) => [k, Array(16 * bars).fill(0)]));
const fitBars = n => [1, 2, 4, 8].find(b => b >= n) || 8;

const state = { id: null, token: { id: null }, origin: '', movie: null, sound: 'grand', octave: 0, beat: { tempo: 92, swing: 0, kit: '808', bars: 1, tracks: emptyTracks() }, desk: {}, film: null };
let viewBar = 0, playBar = -1;
let song = null, dirty = true, urls = [], scoreBpm = null;

function setStatus(text, bad) { const s = $('status'); s.textContent = text || ''; s.classList.toggle('bad', !!bad); }
async function api(path, body) {
  const r = await fetch('/music' + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Storyboard': '1' }, body: JSON.stringify(body) } : undefined);
  return r.json();
}
const inField = e => /^(TEXTAREA|INPUT|SELECT)$/.test(e.target.tagName) && e.target.type !== 'checkbox' && e.target.type !== 'range';

// ---------- Autosave ----------
let saveTimer;
function autosave() {
  queueSave();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try { localStorage.setItem('lilykeys', JSON.stringify(snapshot())); } catch { /* storage unavailable */ }
  }, 400);
}
const snapshot = () => ({ id: state.id, name: $('pieceName').value, origin: state.origin, movie: state.movie || undefined, ly: ed.value, sound: state.sound, beat: beatForExport(), desk: state.desk, film: state.film || undefined, withFilm: $('withFilm').checked, scoreBpm, withBeat: $('withBeat').checked });
// The layer of film sounds that goes with the piece (drones, risers, hits ...). Claude writes it; the box next to
// Play switches it on and off, and pointing at it lists what is in it.
function setFilm(f, quiet) {
  const layer = f && Array.isArray(f.layer) ? f.layer : [];
  state.film = layer.length ? { layer } : null;
  $('filmWrap').hidden = !state.film;
  $('filmLabel').textContent = `Film sounds (${layer.length})`;
  $('filmWrap').title = layer.map(e => `${e.sound}, bar ${e.bar}${e.bars ? ' for ' + e.bars + (e.bars === 1 ? ' bar' : ' bars') : ''}`).join('\n');
  if (!quiet) autosave();
}
function setSpace(kind, quiet) {
  kind = Sound.SPACES[kind] ? kind : 'hall';
  if (kind === 'hall') delete state.desk.space; else state.desk.space = kind;
  if ($('space')) $('space').value = kind;
  Sound.setSpace(kind);
  if (!quiet) autosave();
}

// Every piece is kept in the gallery without asking. A piece gets its id from the server on its first save.
let galleryTimer, saving = Promise.resolve();
function queueSave() { clearTimeout(galleryTimer); galleryTimer = setTimeout(saveNow, 1500); }
function saveNow(always) {
  clearTimeout(galleryTimer);
  if (!ed.value.trim() || (!always && ed.value === TEMPLATE && !state.id)) return saving;
  const snap = snapshot(), token = state.token;
  saving = saving.then(async () => {
    try {
      const r = await api('/api/pieces', { id: token.id, data: snap });
      token.id = r.id;
      if (state.token === token) { state.id = r.id; $('saveState').textContent = 'Saved to the gallery'; }
    } catch { if (state.token === token) $('saveState').textContent = 'Not saved: the server is not answering'; }
  });
  return saving;
}
// Start a separate piece: what was open stays in the gallery as it was.
async function beginNewPiece(name, origin = '') {
  await saveNow();
  state.id = null; state.token = { id: null }; state.origin = origin;
  $('pieceName').value = name; $('saveState').textContent = '';
}
const beatForExport = () => ({ tempo: state.beat.tempo, swing: state.beat.swing, kit: state.beat.kit, bars: state.beat.bars, tracks: Object.fromEntries(TRACKS.map(([k]) => [k, rowToStr(state.beat.tracks[k])])) });

// ---------- Keyboard ----------
const LOW = 36, HIGH = 96, BLACK = [1, 3, 6, 8, 10];
const keyEls = new Map();
const KEYMAP = { a: 0, w: 1, s: 2, e: 3, d: 4, f: 5, t: 6, g: 7, y: 8, h: 9, u: 10, j: 11, k: 12, o: 13, l: 14, p: 15, ';': 16 };

function buildKeys() {
  const box = $('keys'); box.innerHTML = '';
  const whites = []; for (let m = LOW; m <= HIGH; m++) if (!BLACK.includes(m % 12)) whites.push(m);
  const w = 100 / whites.length;
  for (let m = LOW; m <= HIGH; m++) {
    const el = document.createElement('div'), black = BLACK.includes(m % 12);
    el.className = 'key ' + (black ? 'black' : 'white'); el.dataset.m = m;
    el.appendChild(document.createElement('span'));
    if (black) { el.style.width = w * 0.62 + '%'; el.style.left = (whites.indexOf(m + 1) * w - w * 0.31) + '%'; }
    box.appendChild(el); keyEls.set(m, el);
  }
  labelKeys();
}
function labelKeys() {
  const base = 60 + 12 * state.octave, letters = {};
  for (const [ch, off] of Object.entries(KEYMAP)) letters[base + off] = ch.toUpperCase();
  keyEls.forEach((el, m) => {
    const name = m % 12 === 0 ? 'C' + (m / 12 - 1) : '';
    el.firstChild.innerHTML = (letters[m] ? `<b>${letters[m]}</b>` : '') + name;
  });
  $('octLabel').textContent = 'C' + (4 + state.octave);
}
const mark = (m, cls, on) => { const el = keyEls.get(m); if (el) el.classList.toggle(cls, on); };

function press(m, vel = 0.8) {
  if (m < 12 || m > 120) return;
  Sound.noteOn(m, vel); mark(m, 'down', true);
  if (rec.on) rec.open.set(m, { m, t0: Sound.now, t1: null });
  if ($('stepMode').checked) typeNote(m);
}
function release(m) {
  Sound.noteOff(m); mark(m, 'down', false);
  const r = rec.open.get(m);
  if (r) { r.t1 = Sound.now; rec.events.push(r); rec.open.delete(m); }
}

(function pointerPlay() {
  const box = $('keys'), held = new Map();
  const hit = e => {
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el || !el.dataset || !el.dataset.m || !box.contains(el)) return null;
    const r = el.getBoundingClientRect();
    return { m: +el.dataset.m, vel: Math.min(1, 0.35 + 0.65 * (e.clientY - r.top) / r.height) };
  };
  box.addEventListener('pointerdown', e => {
    e.preventDefault(); box.setPointerCapture(e.pointerId);
    const h = hit(e); if (h) { held.set(e.pointerId, h.m); press(h.m, h.vel); }
  });
  box.addEventListener('pointermove', e => {
    if (!held.has(e.pointerId)) return;
    const h = hit(e), cur = held.get(e.pointerId);
    if (h && h.m !== cur) { release(cur); held.set(e.pointerId, h.m); press(h.m, h.vel); }
  });
  const up = e => { if (held.has(e.pointerId)) { release(held.get(e.pointerId)); held.delete(e.pointerId); } };
  box.addEventListener('pointerup', up); box.addEventListener('pointercancel', up);
})();

const typed = new Map();
window.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); engrave(); return; }
  if (inField(e) || e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === ' ') { e.preventDefault(); if (!e.repeat) Sound.setSustain(true); return; }
  if (e.repeat) return;
  if (k === 'z') return shiftOctave(-1);
  if (k === 'x') return shiftOctave(1);
  if (k in KEYMAP && !typed.has(k)) { const m = 60 + 12 * state.octave + KEYMAP[k]; typed.set(k, m); press(m, 0.78); }
});
window.addEventListener('keyup', e => {
  const k = e.key.toLowerCase();
  if (k === ' ') { Sound.setSustain($('sustain').checked); return; }
  if (typed.has(k)) { release(typed.get(k)); typed.delete(k); }
});
window.addEventListener('blur', () => { typed.forEach(m => release(m)); typed.clear(); });
function shiftOctave(d) { state.octave = Math.max(-2, Math.min(2, state.octave + d)); labelKeys(); }

// Hardware MIDI keyboards
if (navigator.requestMIDIAccess) {
  navigator.requestMIDIAccess().then(access => {
    const hook = () => {
      const names = [];
      access.inputs.forEach(inp => {
        names.push(inp.name);
        inp.onmidimessage = ev => {
          const [st, note, vel] = ev.data, type = st & 0xf0;
          if (type === 0x90 && vel > 0) press(note, vel / 127);
          else if (type === 0x80 || type === 0x90) release(note);
          else if (type === 0xb0 && note === 64) Sound.setSustain(vel >= 64 || $('sustain').checked);
        };
      });
      $('midiStatus').textContent = names.length ? 'MIDI keyboard connected: ' + names[0] : '';
    };
    hook(); access.onstatechange = hook;
  }).catch(() => {});
}

// ---------- Notes into LilyPond ----------
const PITCH = ['c', 'cis', 'd', 'ees', 'e', 'f', 'fis', 'g', 'aes', 'a', 'bes', 'b'];
function lyPitch(m) {
  const oct = Math.floor(m / 12) - 4;
  return PITCH[m % 12] + (oct > 0 ? "'".repeat(oct) : ','.repeat(-oct));
}
function insertAtCursor(text) {
  const a = ed.selectionStart, b = ed.selectionEnd;
  ed.setRangeText(text, a, b, 'end'); edited();
}
function typeNote(m) { insertAtCursor(' ' + lyPitch(m) + $('stepDur').value); }

const rec = { on: false, events: [], open: new Map() };
const PARTS = [[16, '1'], [12, '2.'], [8, '2'], [6, '4.'], [4, '4'], [3, '8.'], [2, '8'], [1, '16']];
function spell(pos, len, head, isRest) {
  const out = [];
  while (len > 0) {
    const room = 16 - (pos % 16), [n, d] = PARTS.find(([n]) => n <= Math.min(len, room));
    len -= n; pos += n;
    out.push(head + d + (!isRest && len > 0 ? '~' : '') + (pos % 16 === 0 ? ' |\n     ' : ''));
  }
  return out.join(' ');
}
function voiceToLy(evs, total) {
  const groups = new Map();
  evs.forEach(e => { if (!groups.has(e.s)) groups.set(e.s, []); groups.get(e.s).push(e); });
  const starts = [...groups.keys()].sort((a, b) => a - b), out = [];
  let pos = 0;
  starts.forEach((s, i) => {
    if (s < pos) return;
    if (s > pos) out.push(spell(pos, s - pos, 'r', true));
    const g = groups.get(s), next = i + 1 < starts.length ? starts[i + 1] : total;
    const len = Math.max(1, Math.min(Math.max(...g.map(e => e.len)), next - s));
    const names = [...new Set(g.map(e => e.m))].sort((a, b) => a - b).map(lyPitch);
    out.push(spell(s, len, names.length > 1 ? `<${names.join(' ')}>` : names[0], false));
    pos = s + len;
  });
  if (pos < total) out.push(spell(pos, total - pos, 'r', true));
  return out.join(' ').trim();
}
function takeToLy(events, tempo, origin) {
  const step = 60 / tempo / 4;
  const q = events.map(e => ({ m: e.m, s: Math.max(0, Math.round((e.t0 - origin) / step)), len: Math.max(1, Math.round((e.t1 - e.t0) / step)) }));
  const total = Math.ceil(Math.max(...q.map(e => e.s + e.len)) / 16) * 16;
  const rh = q.filter(e => e.m >= 60), lh = q.filter(e => e.m < 60);
  const staff = (clef, evs) => `    \\new Staff {\n      \\clef ${clef} \\tempo 4 = ${tempo}\n      ${voiceToLy(evs, total)}\n    }`;
  return `\\version "2.26.0"\n\n\\header {\n  title = "${$('pieceName').value.replace(/["\\]/g, '') || 'New take'}"\n  tagline = ##f\n}\n\n\\score {\n  \\new PianoStaff <<\n${staff('treble', rh)}\n${staff('bass', lh)}\n  >>\n  \\layout { }\n  \\midi { }\n}\n`;
}
$('btnRec').onclick = async () => {
  const b = $('btnRec');
  if (!rec.on) {
    rec.on = true; rec.events = []; rec.open.clear();
    b.textContent = 'Stop and write it down'; b.classList.add('on');
    setStatus(beat.running ? 'Recording in time with the beat. Play something.' : `Recording at ${state.beat.tempo} bpm. Play something.`);
    return;
  }
  rec.on = false; b.textContent = 'Record a take'; b.classList.remove('on');
  rec.open.forEach(r => { r.t1 = Sound.now; rec.events.push(r); }); rec.open.clear();
  if (!rec.events.length) return setStatus('Nothing was played, so the score is unchanged.');
  if (ed.value !== TEMPLATE || state.id) {
    const base = $('pieceName').value.replace(/ take( \d+)?$/, '');
    await beginNewPiece(base === 'Untitled' ? 'New take' : base + ' take', 'Recorded at the keyboard');
  } else state.origin = 'Recorded at the keyboard';
  const first = Math.min(...rec.events.map(e => e.t0));
  const origin = beat.running ? beat.barStartBefore(first) : first;
  setScore(takeToLy(rec.events, state.beat.tempo, origin), true);
  engrave();
};

// ---------- Score ----------
let previousLy = null;
function setScore(text, keepUndo) {
  if (keepUndo) previousLy = ed.value;
  ed.value = text; edited();
}
function edited() { dirty = true; $('pages').classList.add('stale'); autosave(); }
ed.addEventListener('input', edited);
ed.addEventListener('keydown', e => {
  if (e.key === 'Tab') { e.preventDefault(); insertAtCursor('  '); }
});

function b64ToBytes(b64) { const s = atob(b64), a = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return a; }
function blobUrl(data, type) { const u = URL.createObjectURL(new Blob([data], { type })); urls.push(u); return u; }
const clock = s => Math.floor(s / 60) + ':' + String(Math.round(s % 60)).padStart(2, '0');

function showRender(r) {
  urls.forEach(u => URL.revokeObjectURL(u)); urls = [];
  const pages = $('pages'), log = $('log'), fname = ($('pieceName').value || 'score').replace(/[^\w \-]/g, '');
  if (r.svgs && r.svgs.length) {
    pages.innerHTML = '';
    r.svgs.forEach((svg, i) => {
      const img = document.createElement('img');
      img.className = 'page'; img.alt = `Score, page ${i + 1}`; img.src = blobUrl(svg, 'image/svg+xml');
      pages.appendChild(img);
    });
    pages.classList.remove('stale');
  }
  log.hidden = !(r.log && (!r.ok || /error|warning/i.test(r.log)));
  log.textContent = r.log || '';
  song = null;
  if (r.midi) { try { song = Sound.parseMidi(b64ToBytes(r.midi).buffer, r.perf); } catch { song = null; } }
  const set = (id, data, type, ext) => { const a = $(id); a.hidden = !data; if (data) { a.href = blobUrl(b64ToBytes(data), type); a.download = fname + ext; } };
  set('dlPdf', r.pdf, 'application/pdf', '.pdf'); set('dlMidi', r.midi, 'audio/midi', '.mid');
  $('dlAudio').hidden = !(song && song.notes.length);
  if (!r.ok) setStatus('LilyPond found a problem. The details are under the code.', true);
  else if (!song || !song.notes.length) setStatus('Engraved. Add \\midi { } inside \\score to hear it.');
  else {
    // A new or changed tempo in the score takes over the Tempo box; after that the box sets the playing speed.
    const written = Math.round(song.bpm);
    if (written !== scoreBpm) { scoreBpm = written; setTempo(written); }
    setStatus(`Engraved. ${clock(song.length)} of music, written at ${written} bpm.` + (state.beat.tempo !== written ? ` Playing at ${state.beat.tempo}.` : '') + partsText(song));
    Sound.prepare(song, state.film);
  }
  dirty = !r.ok;
  return r.ok;
}
// "Instruments: violin, viola, cello", for scores that use more than the keyboard sound.
function partsText(s) {
  const named = s.parts.filter(([p]) => p > 0).map(([p]) => Sound.instrumentName(p).toLowerCase());
  if (!named.length) return '';
  const piano = s.parts.some(([p]) => p === 0) ? ['piano'] : [];
  const all = [...piano, ...named];
  return ` ${all.length} instrument${all.length > 1 ? 's' : ''}: ${all.join(', ')}.`;
}
let engraving = null;
function engrave() {
  if (engraving) return engraving;
  setStatus('Engraving…');
  engraving = api('/api/render', { ly: ed.value }).then(showRender)
    .catch(() => { setStatus('The app is not answering. Start it again with “Start Storyboard”.', true); return false; })
    .finally(() => { engraving = null; });
  return engraving;
}
$('btnEngrave').onclick = engrave;
// An audio file of the piece. The app plays the saved piece into a file with this same sound engine.
let makingAudio = false;
$('dlAudio').onclick = async e => {
  e.preventDefault();
  if (makingAudio) return;
  if (dirty && !(await engrave())) return;
  await saveNow(true);
  if (!state.id) return setStatus('The piece could not be saved, so there is nothing to turn into audio yet.', true);
  makingAudio = true; setStatus('Making the audio file… this takes a little while.');
  try {
    const r = await fetch('/music/api/pieces/' + state.id + '/file.m4a');
    if (!r.ok) throw new Error(await r.text());
    const a = document.createElement('a');
    a.href = blobUrl(await r.blob(), 'audio/mp4'); a.download = ($('pieceName').value || 'music').replace(/[^\w \-]/g, '') + '.m4a';
    document.body.appendChild(a); a.click(); a.remove();
    setStatus('The audio file is in your Downloads folder.');
  } catch (err) { setStatus(String(err.message || err).slice(0, 200) || 'The audio could not be made.', true); }
  makingAudio = false;
};
$('dlLy').onclick = e => { const a = e.currentTarget; a.href = blobUrl(ed.value, 'text/plain'); a.download = ($('pieceName').value || 'score').replace(/[^\w \-]/g, '') + '.ly'; };

let beatStartedByScore = false;
function stopScore() {
  Sound.stopScore();
  keyEls.forEach(el => el.classList.remove('sung'));
  $('btnPlay').textContent = 'Play score';
  if (beatStartedByScore) { beat.stop(); beatStartedByScore = false; }
}
let starting = null;
$('btnPlay').onclick = async () => {
  if (Sound.isPlaying) return stopScore();
  if (starting) { starting.cancelled = true; starting = null; $('btnPlay').textContent = 'Play score'; return; }
  const run = starting = {};
  $('btnPlay').textContent = 'Stop';
  if (dirty || !song) { const ok = await engrave(); if (!ok || run.cancelled) { if (!run.cancelled) { starting = null; $('btnPlay').textContent = 'Play score'; } return; } }
  if (!song || !song.notes.length) { starting = null; $('btnPlay').textContent = 'Play score'; return; }
  {   // the instruments, and the recorded drums and film sounds if the piece uses any
    const before = $('status').textContent;
    const slow = setTimeout(() => setStatus('Getting the instrument sounds ready… The first time takes a moment.'), 300);
    const ready = await Sound.prepare(song, $('withFilm').checked ? state.film : null);
    clearTimeout(slow);
    if (run.cancelled) return;
    if ($('status').textContent !== before || !ready) setStatus(ready ? before : 'Some instrument sounds could not be downloaded, so synths stand in for them.', !ready);
  }
  starting = null;
  const t0 = Sound.playScore(song, { onKey: (m, on) => mark(m, 'sung', on), onEnd: stopScore, rate: state.beat.tempo / song.bpm, film: $('withFilm').checked ? state.film : null });
  if ($('withBeat').checked) { beat.start(t0); beatStartedByScore = true; }
};

// ---------- Beat machine ----------
const beat = {
  running: false, step: 0, base: 0, timer: null, bars: [],
  start(at) {
    this.stop();
    this.running = true; this.step = 0; this.base = at ?? Sound.now + 0.06; this.bars = [];
    this.timer = setInterval(() => this.tick(), 25); this.tick();
    $('beatPlay').textContent = 'Stop beat';
  },
  stop() {
    clearInterval(this.timer); this.running = false;
    $('beatPlay').textContent = 'Play beat';
    document.querySelectorAll('.step.now').forEach(el => el.classList.remove('now'));
    playBar = -1; paintBars();
  },
  tick() {
    while (this.base < Sound.now + 0.12) {
      const b = state.beat, dur = 60 / b.tempo / 4, total = b.bars * 16, step = this.step % total;
      const when = this.base + (step % 2 ? (b.swing / 100) * dur : 0);
      if (step % 16 === 0) { this.bars.push(this.base); if (this.bars.length > 64) this.bars.shift(); }
      for (const [k] of TRACKS) { const v = b.tracks[k][step]; if (v) Sound.drum(k, when, VEL[v]); }
      setTimeout(() => { if (this.running) showStep(step); }, Math.max(0, (when - Sound.now) * 1000));
      this.base += dur; this.step = (step + 1) % total;
    }
  },
  barStartBefore(t) {
    const earlier = this.bars.filter(b => b <= t + 0.08);
    return earlier.length ? earlier[earlier.length - 1] : t;
  },
};
const stepEls = [];
function showStep(step) {
  const bar = Math.floor(step / 16);
  if (bar !== playBar) {
    playBar = bar;
    if ($('follow').checked && viewBar !== bar && bar < state.beat.bars) { viewBar = bar; paintGrid(); }
    paintBars();
  }
  document.querySelectorAll('.step.now').forEach(el => el.classList.remove('now'));
  if (bar === viewBar) stepEls.forEach(row => row[step % 16].classList.add('now'));
}
function paintBars() {
  const box = $('barTabs'); box.innerHTML = '';
  for (let i = 0; i < state.beat.bars; i++) {
    const b = document.createElement('button');
    b.className = 'bar' + (i === viewBar ? ' on' : '') + (i === playBar ? ' playing' : '');
    b.textContent = i + 1; b.setAttribute('aria-label', 'Show bar ' + (i + 1));
    b.onclick = () => { viewBar = i; $('follow').checked = false; paintGrid(); paintBars(); };
    box.appendChild(b);
  }
}
function setBars(n) {
  const old = state.beat.bars * 16;
  TRACKS.forEach(([k]) => { const r = state.beat.tracks[k]; state.beat.tracks[k] = Array.from({ length: n * 16 }, (_, i) => r[i % old]); });
  state.beat.bars = n; viewBar = Math.min(viewBar, n - 1); $('bars').value = n;
  paintGrid(); paintBars(); autosave();
}
function buildGrid() {
  const grid = $('grid'); grid.innerHTML = ''; stepEls.length = 0;
  TRACKS.forEach(([k, label]) => {
    const name = document.createElement('button');
    name.className = 'track'; name.textContent = label; name.title = 'Hear the ' + label.toLowerCase();
    name.onclick = () => Sound.drum(k);
    grid.appendChild(name);
    const row = [];
    for (let i = 0; i < 16; i++) {
      const s = document.createElement('button');
      s.className = 'step' + (i % 4 === 0 ? ' beat1' : '');
      s.setAttribute('aria-label', `${label}, step ${i + 1}`);
      s.onclick = e => {
        const at = viewBar * 16 + i, cur = state.beat.tracks[k][at];
        const next = e.shiftKey ? (cur === 2 ? 0 : 2) : e.altKey ? (cur === 3 ? 0 : 3) : (cur ? 0 : 1);
        state.beat.tracks[k][at] = next; paintGrid(); autosave();
        if (next && !beat.running) Sound.drum(k, undefined, VEL[next]);
      };
      grid.appendChild(s); row.push(s);
    }
    stepEls.push(row);
  });
  paintGrid();
}
function paintGrid() {
  TRACKS.forEach(([k], r) => stepEls[r].forEach((el, i) => {
    const v = state.beat.tracks[k][viewBar * 16 + i] || 0;
    el.classList.toggle('on', v === 1); el.classList.toggle('accent', v === 2); el.classList.toggle('ghost', v === 3);
    el.setAttribute('aria-pressed', v ? 'true' : 'false');
  }));
}
function applyBeat(b) {
  if (!b || typeof b !== 'object') return;
  if (b.tempo) state.beat.tempo = Math.max(40, Math.min(220, Math.round(+b.tempo) || 92));
  if (b.swing != null) state.beat.swing = Math.max(0, Math.min(60, +b.swing || 0));
  if (b.kit && Sound.KITS[b.kit]) state.beat.kit = b.kit;
  if (b.tracks) {
    const longest = Math.max(0, ...TRACKS.map(([k]) => cleanTrack(b.tracks[k]).length));
    state.beat.bars = fitBars(Math.max(+b.bars || 0, Math.ceil(longest / 16), 1));
    TRACKS.forEach(([k]) => { state.beat.tracks[k] = parseTrack(b.tracks[k], state.beat.bars * 16); });
    viewBar = 0;
  }
  $('tempo').value = state.beat.tempo; $('swing').value = state.beat.swing; $('kit').value = state.beat.kit; $('bars').value = state.beat.bars;
  Sound.setKit(state.beat.kit); paintGrid(); paintBars(); syncEcho(); autosave();
}
$('beatPlay').onclick = () => { beatStartedByScore = false; beat.running ? beat.stop() : beat.start(); };
function setTempo(v) {
  state.beat.tempo = Math.max(40, Math.min(220, Math.round(+v) || 92)); $('tempo').value = state.beat.tempo;
  if (song) Sound.setScoreRate(state.beat.tempo / song.bpm);
  syncEcho(); autosave();
}
$('tempo').onchange = e => setTempo(e.target.value);
$('bars').onchange = e => setBars(+e.target.value);
$('barCopy').onclick = () => {
  if (viewBar + 1 >= state.beat.bars) return setStatus('This is the last bar. Choose a longer length first, then copy.');
  TRACKS.forEach(([k]) => { const r = state.beat.tracks[k]; for (let i = 0; i < 16; i++) r[(viewBar + 1) * 16 + i] = r[viewBar * 16 + i]; });
  viewBar++; $('follow').checked = false; paintGrid(); paintBars(); autosave();
};
$('barClear').onclick = () => { TRACKS.forEach(([k]) => state.beat.tracks[k].fill(0, viewBar * 16, viewBar * 16 + 16)); paintGrid(); autosave(); };
$('swing').oninput = e => { state.beat.swing = +e.target.value; autosave(); };
$('kit').onchange = e => { state.beat.kit = e.target.value; Sound.setKit(state.beat.kit); autosave(); };
$('groove').onchange = e => { if (GROOVES[e.target.value]) applyBeat(GROOVES[e.target.value]); e.target.value = ''; };
$('beatClear').onclick = () => { state.beat.tracks = emptyTracks(state.beat.bars); paintGrid(); autosave(); };
$('beatFold').onclick = e => {
  const folded = $('beat').classList.toggle('folded');
  e.currentTarget.textContent = folded ? 'Show this panel' : 'Hide this panel';
  e.currentTarget.setAttribute('aria-expanded', String(!folded));
};

// ---------- Sound controls ----------
(function soundMenu() {
  const groups = {};
  Sound.SOUNDS.forEach(([k, label, group]) => {
    if (!groups[group]) { groups[group] = document.createElement('optgroup'); groups[group].label = group; $('sound').appendChild(groups[group]); }
    groups[group].appendChild(new Option(label, k));
  });
})();
// The Kit menu: the drum machines the app makes itself and, when they are on this computer, the recorded kits.
function fillKits() {
  const menu = $('kit'), all = Object.entries(Sound.KITS), real = all.filter(([, v]) => v.recorded);
  menu.textContent = '';
  all.filter(([, v]) => !v.recorded).forEach(([k, v]) => menu.add(new Option(v.label, k)));
  if (real.length) {
    const group = document.createElement('optgroup'); group.label = 'Recorded drums';
    real.forEach(([k, v]) => group.appendChild(new Option(v.label, k)));
    menu.appendChild(group);
  }
  menu.value = state.beat.kit;
}
fillKits();
Object.keys(GROOVES).forEach(n => $('groove').add(new Option(n, n)));
$('sound').onchange = e => {
  state.sound = e.target.value; Sound.setSound(state.sound); autosave(); e.target.blur();
  if (state.sound.startsWith('gm:')) {
    setStatus('Loading the ' + e.target.selectedOptions[0].text.toLowerCase() + ' sound…');
    Sound.warmInstrument(+state.sound.slice(3)).then(ok => setStatus(ok ? e.target.selectedOptions[0].text + ' is ready.' : 'That instrument could not be downloaded, so a synth stands in for it. Check the internet connection.', !ok));
  }
};
$('volume').oninput = e => Sound.setVolume(e.target.value / 100);
$('sustain').onchange = e => Sound.setSustain(e.target.checked);
$('octDown').onclick = () => shiftOctave(-1);
$('octUp').onclick = () => shiftOctave(1);

// ---------- Sound desk ----------
const KNOBS = [
  ['Keys', [['bright', 'Brightness', 200, 18000, 18000, 'log'], ['reso', 'Resonance', 0.5, 14, 0.7, 'num'], ['kdrive', 'Drive', 0, 1, 0], ['chorus', 'Chorus', 0, 1, 0], ['echo', 'Echo', 0, 1, 0], ['fb', 'Repeats', 0, 0.85, 0.35], ['room', 'Room', 0, 1, 0.2]]],
  ['Drums', [['dlevel', 'Level', 0, 1.5, 0.9], ['dtone', 'Tone', 300, 18000, 18000, 'log'], ['ddrive', 'Crunch', 0, 1, 0], ['dtune', 'Tune', -12, 12, 0, 'int'], ['droom', 'Room', 0, 1, 0], ['decho', 'Echo', 0, 1, 0]]],
  ['Drum levels', TRACKS.map(([k, label]) => ['mix_' + k, label, 0, 1.5, 1])],
  ['Film', [['film', 'Film sounds', 0, 1.5, 1]]],
];
const knobs = {};
function syncEcho() { Sound.setDesk('echoTime', (+$('echoTime')?.value || 0.75) * 60 / state.beat.tempo); }
function makeKnob([id, label, min, max, def, kind]) {
  const cell = document.createElement('div'), dial = document.createElement('div'), name = document.createElement('span');
  cell.className = 'knob-cell'; dial.className = 'knob'; name.className = 'knob-name'; name.textContent = label;
  dial.appendChild(document.createElement('i')); cell.append(dial, name);
  dial.tabIndex = 0; dial.setAttribute('role', 'slider'); dial.setAttribute('aria-label', label);
  dial.title = 'Drag up or down. Double-click to reset.';
  const toValue = n => kind === 'log' ? min * Math.pow(max / min, n) : kind === 'int' ? Math.round(min + (max - min) * n) : min + (max - min) * n;
  const toNorm = v => kind === 'log' ? Math.log(v / min) / Math.log(max / min) : (v - min) / (max - min);
  const show = v => kind === 'log' ? (v >= 1000 ? (v / 1000).toFixed(1) + ' kHz' : Math.round(v) + ' Hz') : kind === 'int' ? (v > 0 ? '+' : '') + v + ' st' : kind === 'num' ? v.toFixed(1) : Math.round(v * 100) + '%';
  let norm = 0;
  const k = {
    def,
    set(v, quiet) {
      norm = Math.max(0, Math.min(1, toNorm(v))); const val = toValue(norm);
      dial.style.setProperty('--p', norm); dial.setAttribute('aria-valuetext', show(val));
      state.desk[id] = val; Sound.setDesk(id, val);
      if (!quiet) { name.textContent = show(val); cell.classList.add('live'); clearTimeout(k.t); k.t = setTimeout(() => { name.textContent = label; cell.classList.remove('live'); }, 900); autosave(); }
    },
    nudge(d) { k.set(toValue(Math.max(0, Math.min(1, norm + d)))); },
  };
  let lastY = null;
  dial.addEventListener('pointerdown', e => { e.preventDefault(); dial.setPointerCapture(e.pointerId); dial.focus(); lastY = e.clientY; });
  dial.addEventListener('pointermove', e => { if (lastY == null) return; k.nudge((lastY - e.clientY) / (e.shiftKey ? 600 : 150)); lastY = e.clientY; });
  const end = () => { lastY = null; };
  dial.addEventListener('pointerup', end); dial.addEventListener('pointercancel', end);
  dial.addEventListener('dblclick', () => k.set(def));
  dial.addEventListener('wheel', e => { e.preventDefault(); k.nudge(e.deltaY < 0 ? 0.03 : -0.03); }, { passive: false });
  dial.addEventListener('keydown', e => {
    const d = { ArrowUp: 0.04, ArrowRight: 0.04, ArrowDown: -0.04, ArrowLeft: -0.04 }[e.key];
    if (d) { e.preventDefault(); k.nudge(d); }
  });
  knobs[id] = k; k.set(def, true);
  return cell;
}
function buildDesk() {
  const desk = $('desk');
  KNOBS.forEach(([title, list]) => {
    const g = document.createElement('div'), h = document.createElement('h3'), row = document.createElement('div');
    g.className = 'desk-group'; h.textContent = title; row.className = 'knobs';
    list.forEach(def => row.appendChild(makeKnob(def)));
    g.append(h, row); desk.appendChild(g);
  });
  const side = document.createElement('div'); side.className = 'desk-side';
  side.innerHTML = `<label class="field" title="The place the music sounds in">Space <select id="space" class="sel small">${Object.entries(Sound.SPACES).map(([k, v]) => `<option value="${k}"${k === 'hall' ? ' selected' : ''}>${v.label}</option>`).join('')}</select></label>
    <label class="field">Echo time <select id="echoTime" class="sel small">
    <option value="0.25">sixteenth note</option><option value="0.5">eighth note</option><option value="0.75" selected>dotted eighth</option><option value="1">quarter note</option></select></label>
    <button id="deskReset" class="btn tiny ghost">Reset every knob</button>`;
  desk.appendChild(side);
  $('echoTime').onchange = () => { state.desk.echoTime = +$('echoTime').value; syncEcho(); autosave(); };
  $('space').onchange = () => setSpace($('space').value);
  $('deskReset').onclick = () => { Object.values(knobs).forEach(k => k.set(k.def, true)); setSpace('hall', true); autosave(); };
}
function loadDesk(d) {
  Object.entries(knobs).forEach(([id, k]) => k.set(typeof d[id] === 'number' ? d[id] : k.def, true));
  if (d.echoTime) { $('echoTime').value = d.echoTime; state.desk.echoTime = d.echoTime; }
  setSpace(d.space, true);
  syncEcho();
}
function showTab(desk) {
  $('desk').hidden = !desk; $('beatView').hidden = desk;
  $('tabDesk').classList.toggle('on', desk); $('tabBeat').classList.toggle('on', !desk);
  $('tabDesk').setAttribute('aria-selected', desk); $('tabBeat').setAttribute('aria-selected', !desk);
  $('beat').classList.remove('folded'); $('beatFold').textContent = 'Hide this panel';
}
$('tabBeat').onclick = () => showTab(false);
$('tabDesk').onclick = () => showTab(true);

// ---------- Claude ----------
const history = [];
const IDEAS = [
  'Write a rainy-day waltz in A minor, 16 bars, with a simple left hand.',
  'Write a two-minute cinematic orchestral cue in D minor that starts with quiet strings and builds to full brass, timpani and choir.',
  'Keep my melody but give the left hand a walking bass line.',
  'Make a 4-bar boom-bap beat around 88 bpm with ghost notes and a fill in the last bar.',
  'Explain what each part of this LilyPond file does.',
];
function addMsg(cls, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + cls; el.textContent = text;
  $('msgs').appendChild(el); $('msgs').scrollTop = 1e9;
  return el;
}
function showIdeas() {
  const box = document.createElement('div'); box.className = 'ideas'; box.id = 'ideas';
  const p = document.createElement('p'); p.textContent = 'Claude can see your score and your beat. Ask for a new piece or a change, for example:';
  box.appendChild(p);
  IDEAS.forEach(t => { const b = document.createElement('button'); b.className = 'btn ghost idea'; b.textContent = t; b.onclick = () => sendToClaude(t); box.appendChild(b); });
  $('msgs').appendChild(box);
}
let asking = false, attached = null;
const shorten = t => t.length > 320 ? t.slice(0, 300).trimEnd() + `… (${(t.length - 300).toLocaleString()} more characters)` : t;
async function sendToClaude(message, source) {
  message = message.trim();
  if ((!message && !source) || asking) return;
  asking = true; $('chatSend').disabled = true;
  const ideas = $('ideas'); if (ideas) ideas.remove();
  const shown = source ? `Turn this into music${source.filename ? ` (${source.filename})` : ''}:\n${shorten(source.text)}${message ? '\n\n' + message : ''}` : message;
  addMsg('user', shown);
  const wait = addMsg('claude thinking', ''), began = Date.now();
  const waitText = () => { wait.textContent = (source ? 'Claude is reading the text and composing… ' : 'Claude is composing… ') + Math.round((Date.now() - began) / 1000) + 's'; };
  waitText(); const ticker = setInterval(waitText, 1000);
  let r;
  try {
    r = await api('/api/claude', {
      message, history, ly: ed.value, beat: beatForExport(), film: state.film, sound: $('sound').selectedOptions[0].text, model: $('model').value, ensemble: $('ensemble').value,
      ...(source ? { mode: 'text', text: source.text, filename: source.filename, style: $('textStyle').value } : {}),
    });
  } catch { r = { error: 'The app is not answering. Start it again with “Start Storyboard”.' }; }
  clearInterval(ticker); wait.remove();
  asking = false; $('chatSend').disabled = false;
  if (r.error) return addMsg('error', 'Claude could not answer: ' + r.error);
  history.push({ role: 'user', text: shorten(shown) }, { role: 'claude', text: r.reply || '(updated the score)' });
  const el = addMsg('claude', r.reply || 'Done.');
  const notes = [];
  if (r.ly) {
    const before = { ly: ed.value, id: state.id, token: state.token, name: $('pieceName').value, origin: state.origin };
    // A different title means a different piece: keep the old one in the gallery and start a new entry.
    const title = (r.title || '').trim(), isNew = title && title.toLowerCase() !== before.name.trim().toLowerCase();
    if (isNew) await beginNewPiece(title, source ? (source.filename ? 'From ' + source.filename : 'From pasted text') : 'Written with Claude');
    setScore(r.ly.trim() + '\n');
    if (r.render) showRender(r.render); else engrave();
    notes.push(r.render && !r.render.ok ? 'The new score has a LilyPond error. Ask Claude to fix it, or check the log under the code.'
      : (isNew ? `"${title}" was added to the gallery` : 'The score was updated') + (r.fixed ? ' (after one automatic fix).' : '.'));
    const undo = document.createElement('button');
    undo.className = 'btn tiny undo'; undo.textContent = isNew ? 'Go back to the previous piece' : 'Put the previous score back';
    undo.onclick = async () => {
      await saveNow();
      state.id = before.id; state.token = before.token; state.origin = before.origin; $('pieceName').value = before.name;
      setScore(before.ly); engrave(); undo.remove();
    };
    el.appendChild(document.createElement('br')); el.appendChild(undo);
  }
  // A new score brings its own film sounds, or none; a film block on its own changes only the film sounds.
  if (r.ly || r.film) {
    const had = !!state.film;
    setFilm(r.film || null);
    if (r.film && r.film.space) setSpace(r.film.space);
    if (state.film) { $('withFilm').checked = true; notes.push(`It has ${state.film.layer.length} film sound${state.film.layer.length > 1 ? 's' : ''}; the box next to Play switches them off.`); }
    else if (had && !r.ly) notes.push('The film sounds were removed.');
  }
  if (r.beat) {
    applyBeat(r.beat);
    if (r.ly) { $('withBeat').checked = true; notes.push('The beat machine was updated to go with it. Play score will start both together.'); }
    else notes.push('The beat machine was updated.');
  }
  if (notes.length) { const n = document.createElement('span'); n.className = 'note'; n.textContent = notes.join(' '); el.appendChild(n); }
  $('msgs').scrollTop = 1e9;
}

// Text into music: pasted text or an attached .txt / .md file becomes the source material.
function paintTextMode() {
  const on = $('textMode').checked;
  $('textStyle').hidden = !on;
  $('chatSend').textContent = on ? 'Turn into music' : 'Send to Claude';
  $('chatIn').placeholder = !on ? 'Describe the music you want, or ask for a change…'
    : attached ? 'Anything to add, like a mood or a style? This is optional.'
    : 'Paste a transcript, course notes, an article… Claude will turn its ideas into music.';
  $('fileChip').hidden = !attached;
  if (attached) $('fileName').textContent = `${attached.filename}, ${attached.text.length.toLocaleString()} characters`;
}
$('textMode').onchange = () => { if (!$('textMode').checked) attached = null; paintTextMode(); };
$('attachBtn').onclick = () => $('fileIn').click();
$('fileRemove').onclick = () => { attached = null; paintTextMode(); };
$('fileIn').onchange = async e => {
  const f = e.target.files[0]; e.target.value = '';
  if (!f) return;
  if (f.size > 2e6) return setStatus('That file is over 2 MB. Use a shorter text file.', true);
  const text = await f.text();
  if (!text.trim() || text.includes('\u0000')) return setStatus('That file does not look like plain text. Use a .txt or .md file.', true);
  attached = { filename: f.name, text }; $('textMode').checked = true; paintTextMode();
};
function submitChat() {
  const typed = $('chatIn').value;
  if ($('textMode').checked) {
    const source = attached || (typed.trim() ? { filename: '', text: typed } : null);
    if (!source) return setStatus('Paste some text or attach a file first.', true);
    $('chatIn').value = ''; const extra = attached ? typed : '';
    attached = null; $('textMode').checked = false; paintTextMode();
    return sendToClaude(extra, source);
  }
  $('chatIn').value = ''; sendToClaude(typed);
}
$('chatForm').onsubmit = e => { e.preventDefault(); submitChat(); };
$('chatIn').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('chatForm').requestSubmit(); } });

// ---------- Pieces ----------
function loadPiece(p) {
  if (Sound.isPlaying) stopScore();
  $('pieceName').value = p.name || 'Untitled';
  state.id = p.id || null; state.token = { id: state.id }; state.origin = p.origin || ''; state.movie = p.movie || null; showMovie();
  $('withBeat').checked = !!p.withBeat; $('saveState').textContent = '';
  if (p.sound) { state.sound = p.sound; $('sound').value = p.sound; Sound.setSound(p.sound); }
  if (p.beat) applyBeat({ tracks: {}, ...p.beat });
  state.desk = {}; loadDesk(p.desk || {});
  setFilm(p.film || null, true); $('withFilm').checked = p.withFilm !== false;
  scoreBpm = p.scoreBpm ?? null;
  ed.value = p.ly || ''; edited();
}
$('btnNew').onclick = async () => {
  await beginNewPiece('Untitled'); state.movie = null;
  loadPiece({ name: 'Untitled', ly: TEMPLATE }); engrave();
  setStatus('New piece. The one you had open is in the gallery.');
};
$('toGallery').onclick = async e => { e.preventDefault(); await saveNow(); location.href = '/music/gallery'; };

// ---------- The movie this music belongs to ----------
// A piece written for a movie remembers which one. From here it can be sent back as that movie's soundtrack.
async function showMovie() {
  const bar = $('movieBar');
  if (!state.movie) { bar.hidden = true; return; }
  let name = '';
  try { const st = await (await fetch('/api/state', { cache: 'no-store' })).json(); name = (st.projects.find(p => p.id === state.movie) || {}).name || ''; } catch { /* the bar still works without the name */ }
  if (!name) { bar.hidden = true; return; }
  $('movieName').textContent = 'Music for “' + name + '”'; $('movieBack').href = '/#p=' + state.movie; bar.hidden = false;
}
$('movieUse').onclick = async () => {
  if (dirty && !(await engrave())) return setStatus('Fix the score first: LilyPond found a problem in it.', true);
  await saveNow(true);
  try {
    const r = await fetch('/api/project/' + state.movie + '/music/use', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Storyboard': '1' }, body: JSON.stringify({ piece: state.id }) });
    const j = await r.json();
    setStatus(r.ok ? 'Lined up. The movie gets this music and is put together again.' : (j.error || 'That did not work.'), !r.ok);
  } catch { setStatus('The app is not answering. Start it again with “Start Storyboard”.', true); }
};
$('withBeat').addEventListener('change', autosave);
$('withFilm').addEventListener('change', autosave);
try { const en = localStorage.getItem('lilykeys-ensemble'); if (en && $('ensemble').querySelector(`option[value="${en}"]`)) $('ensemble').value = en; } catch { /* storage unavailable */ }
$('ensemble').onchange = e => { try { localStorage.setItem('lilykeys-ensemble', e.target.value); } catch { /* storage unavailable */ } };
$('pieceName').addEventListener('input', autosave);

// ---------- Start ----------
buildKeys(); buildDesk(); buildGrid(); paintBars();
if (window.innerHeight < 900) $('beatFold').click();   // on a short screen the score and the chat get the room first
let saved = null;
try { saved = JSON.parse(localStorage.getItem('lilykeys')); } catch { /* nothing saved */ }
const wanted = new URLSearchParams(location.search).get('piece');
(async () => {
  if (await Sound.percussion) fillKits();   // before a piece is opened: it may ask for one of the recorded kits
  if (wanted) {
    try { const p = await api('/api/pieces/' + encodeURIComponent(wanted)); if (!p.error) saved = p; } catch { /* fall back to the last session */ }
    window.history.replaceState(null, '', '/music/');
  }
  // A session from before the gallery existed has no id: link it to the gallery piece of the same name.
  if (saved && saved.ly && !saved.id) {
    try { const twin = (await api('/api/pieces')).pieces.find(x => x.name === saved.name); if (twin) saved.id = twin.id; } catch { /* offline */ }
  }
  if (saved && saved.ly) loadPiece(saved);
  else { ed.value = TEMPLATE; applyBeat(GROOVES['Boom bap']); }
  engrave();
})();
Sound.setVolume(0.8); Sound.setKit(state.beat.kit);
// Tell the user plainly if something LilyKeys needs is missing on this computer.
api('/api/status').then(s => {
  // The Sound menu offers the instruments that are on this computer (the Models page has the rest as a kit).
  if (Array.isArray(s.have)) {
    for (const o of [...$('sound').options]) if (o.value.startsWith('gm:') && !s.have.includes(+o.value.slice(3)) && o.value !== state.sound) o.remove();
    for (const g of [...$('sound').querySelectorAll('optgroup')]) if (!g.children.length) g.remove();
  }
  const say = [];
  if (!s.lilypond) say.push('LilyPond is not installed, so scores cannot be engraved or played.');
  if (!s.claude) say.push('Claude Code is not installed, so composing with Claude will not work.');
  else if (s.claudeSignedIn === false) say.push('Claude Code is not signed in. Open Terminal, run <code>claude</code> once and sign in.');
  if (!say.length) return;
  $('setupNote').innerHTML = say.join(' ') + ' To fix it, double-click <code>setup.command</code> in the app’s folder, then start the app again.';
  $('setupNote').hidden = false;
}).catch(() => {});
showIdeas();
Sound.loadPiano().then(ok => { if (!ok) { $('sound').value = 'epiano'; state.sound = 'epiano'; Sound.setSound('epiano'); } });
