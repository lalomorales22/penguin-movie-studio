// The Music gallery (LilyKeys): every saved piece, with a small player.
const $ = id => document.getElementById(id);
async function api(path, body) {
  const r = await fetch('/music' + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Storyboard': '1' }, body: JSON.stringify(body) } : undefined);
  return r.json();
}
const clock = s => Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
const when = ms => new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

const TRACKS = ['kick', 'snare', 'hat', 'openhat', 'clap', 'tom', 'rim', 'cowbell'];
const CODES = { '.': 0, x: 1, X: 2, o: 3 }, VEL = [0, 0.62, 1, 0.3];
const DESK_DEFAULTS = { bright: 18000, reso: 0.7, kdrive: 0, chorus: 0, echo: 0, fb: 0.35, room: 0.2, dlevel: 0.9, dtone: 18000, ddrive: 0, dtune: 0, droom: 0, decho: 0, film: 1 };

let cur = null;          // { piece, song, rate, tempo, beat }
let pausedAt = 0, raf = 0;

// ----- Cards -----
function link(text, href, download) { const a = document.createElement('a'); a.textContent = text; a.href = href; if (download) a.download = ''; return a; }
// "Audio": the app plays the piece into a file with this same sound engine, which takes a little while.
function audioLink(p) {
  const a = link('Audio', '#');
  a.onclick = async e => {
    e.preventDefault();
    if (a.dataset.busy) return;
    a.dataset.busy = '1'; a.textContent = 'Making the audio…';
    try {
      const r = await fetch(`/music/api/pieces/${p.id}/file.m4a`);
      if (!r.ok) throw new Error(await r.text());
      const d = document.createElement('a');
      d.href = URL.createObjectURL(await r.blob()); d.download = (p.name || 'music').replace(/[^\w \-]/g, '') + '.m4a';
      document.body.appendChild(d); d.click(); d.remove(); setTimeout(() => URL.revokeObjectURL(d.href), 60000);
      a.textContent = 'Audio';
    } catch (err) { a.textContent = 'Audio'; alert(String(err.message || err).slice(0, 300) || 'The audio could not be made.'); }
    delete a.dataset.busy;
  };
  return a;
}
async function loadCards() {
  const box = $('cards');
  let pieces = [];
  try { pieces = (await api('/api/pieces')).pieces; }
  catch { box.innerHTML = '<p class="empty">The app is not answering. Start it again with “Start Storyboard”.</p>'; return; }
  box.innerHTML = '';
  $('count').textContent = pieces.length === 1 ? '1 piece' : pieces.length + ' pieces';
  if (!pieces.length) {
    box.innerHTML = '<p class="empty">Nothing here yet. Every piece you make in the studio is saved here on its own. <a class="dl" href="/music/">Go and make one.</a></p>';
    return;
  }
  for (const p of pieces) {
    const card = document.createElement('article'); card.className = 'card'; card.dataset.id = p.id;
    const cover = document.createElement('button'); cover.className = 'cover'; cover.setAttribute('aria-label', 'Play ' + p.name);
    const img = document.createElement('img'); img.loading = 'lazy'; img.alt = ''; img.src = `/music/api/pieces/${p.id}/cover.svg`;
    img.onerror = () => { cover.classList.add('blank'); img.remove(); };
    const badge = document.createElement('span'); badge.className = 'cover-play'; badge.textContent = 'Play';
    cover.append(img, badge); cover.onclick = () => (cur && cur.piece.id === p.id) ? toggle() : open(p.id);
    const h = document.createElement('h2'); h.textContent = p.name;
    const meta = document.createElement('p'); meta.className = 'meta'; meta.textContent = when(p.updated) + (p.origin ? '. ' + p.origin : '');
    const acts = document.createElement('div'); acts.className = 'card-actions';
    const del = document.createElement('button'); del.textContent = 'Delete'; del.className = 'del';
    del.onclick = async () => {
      if (!confirm(`Delete "${p.name}" from the gallery? This cannot be undone.`)) return;
      if (cur && cur.piece.id === p.id) closePlayer();
      await api(`/api/pieces/${p.id}/delete`, {}); loadCards();
    };
    acts.append(link('Open in the studio', '/music/?piece=' + p.id), audioLink(p), link('PDF', `/music/api/pieces/${p.id}/file.pdf`, true), link('MIDI', `/music/api/pieces/${p.id}/file.mid`, true), link('LilyPond', `/music/api/pieces/${p.id}/file.ly`, true), del);
    card.append(cover, h, meta, acts); box.appendChild(card);
  }
  markPlaying();
}
function markPlaying() {
  document.querySelectorAll('.card').forEach(c => {
    const mine = cur && c.dataset.id === cur.piece.id;
    c.classList.toggle('current', !!mine);
    c.querySelector('.cover-play').textContent = mine && Sound.isPlaying ? 'Pause' : 'Play';
  });
}

// ----- Beat (a small copy of the studio sequencer) -----
const Beat = {
  timer: null,
  stop() { clearInterval(this.timer); this.timer = null; },
  start(t0, fromSong) {
    this.stop();
    const b = cur.beat, dur = 60 / cur.tempo / 4, total = b.bars * 16, elapsed = fromSong / cur.rate;
    let n = Math.ceil(elapsed / dur - 1e-6), base = t0 + (n * dur - elapsed), step = n % total;
    const tick = () => {
      while (base < Sound.now + 0.12) {
        const at = base + (step % 2 ? (b.swing / 100) * dur : 0);
        for (const k of TRACKS) { const v = b.tracks[k][step]; if (v) Sound.drum(k, at, VEL[v]); }
        base += dur; step = (step + 1) % total;
      }
    };
    this.timer = setInterval(tick, 25); tick();
  },
};
function readBeat(b) {
  b = b || {};
  const bars = [1, 2, 4, 8].includes(b.bars) ? b.bars : 1, tracks = {};
  for (const k of TRACKS) {
    const c = String((b.tracks || {})[k] || '').replace(/[^xXo.]/g, '');
    tracks[k] = Array.from({ length: bars * 16 }, (_, i) => c.length ? CODES[c[i % c.length]] : 0);
  }
  return { bars, tracks, swing: +b.swing || 0, kit: b.kit || '808', tempo: +b.tempo || 0, any: TRACKS.some(k => tracks[k].some(Boolean)) };
}

// ----- Player -----
async function open(id) {
  halt();
  $('player').hidden = false; document.body.classList.add('has-player');
  $('nowName').textContent = 'Getting the piece ready…'; $('nowTime').textContent = '';
  let piece, r;
  try { [piece, r] = await Promise.all([api('/api/pieces/' + id), api(`/api/pieces/${id}/render`)]); }
  catch { $('nowName').textContent = 'The app is not answering.'; return; }
  let song = null;
  if (r.midi) { try { const s = atob(r.midi), a = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); song = Sound.parseMidi(a.buffer, r.perf); } catch { song = null; } }
  $('nowOpen').href = '/music/?piece=' + id;
  if (!song || !song.notes.length) {
    cur = null; markPlaying();
    $('nowName').textContent = `"${piece.name}" has nothing to play yet. Open it in the studio and check the score.`;
    return;
  }
  const beat = readBeat(piece.beat), written = Math.round(song.bpm);
  const tempo = piece.scoreBpm === written && beat.tempo ? beat.tempo : written;
  cur = { piece, song, beat, tempo, rate: tempo / song.bpm };
  // The piece's own sound, kit and desk settings.
  Sound.setSound(Sound.SOUNDS.some(([k]) => k === piece.sound) ? piece.sound : 'grand');
  Sound.setKit(beat.kit);
  const desk = { ...DESK_DEFAULTS, ...(piece.desk || {}) };
  for (const k of TRACKS) if (desk['mix_' + k] == null) desk['mix_' + k] = 1;
  for (const [k, v] of Object.entries(desk)) if (k !== 'echoTime' && typeof v === 'number') Sound.setDesk(k, v);
  Sound.setDesk('echoTime', (+desk.echoTime || 0.75) * 60 / tempo);
  Sound.setSpace(desk.space || 'hall');
  $('gBeat').checked = !!piece.withBeat && beat.any; $('gBeat').disabled = !beat.any;
  // The instruments, and the recorded drums and film sounds if the piece uses any.
  if (song.parts.some(([p]) => p > 0)) $('nowName').textContent = 'Getting the instruments ready…';
  await Sound.prepare(song, piece.withFilm === false ? null : piece.film);
  if (!cur || cur.song !== song) return;
  $('nowName').textContent = piece.name;
  pausedAt = 0; sizeRoll(); play(0);
}
function play(from) {
  if (!cur) return;
  const t0 = Sound.playScore(cur.song, { from, rate: cur.rate, film: cur.piece.withFilm === false ? null : cur.piece.film, onEnd: () => { halt(); pausedAt = 0; draw(); } });
  if ($('gBeat').checked) Beat.start(t0, from);
  setIcon(true); markPlaying();
  cancelAnimationFrame(raf); raf = requestAnimationFrame(frame);
}
function halt() {
  if (Sound.isPlaying) pausedAt = Sound.scorePos;
  Sound.stopScore(); Beat.stop(); cancelAnimationFrame(raf);
  setIcon(false); markPlaying();
}
function toggle() { if (!cur) return; if (Sound.isPlaying) { halt(); draw(); } else play(pausedAt >= cur.song.length - 0.05 ? 0 : pausedAt); }
function closePlayer() { halt(); cur = null; $('player').hidden = true; document.body.classList.remove('has-player'); markPlaying(); }
function setIcon(playing) {
  $('ppIcon').setAttribute('d', playing ? 'M6 4h4v16H6zM14 4h4v16h-4z' : 'M7 4v16l14-8z');
  $('pp').setAttribute('aria-label', playing ? 'Pause' : 'Play');
}
const position = () => Sound.isPlaying ? Math.min(Sound.scorePos, cur.song.length) : pausedAt;
function frame() { draw(); if (Sound.isPlaying) raf = requestAnimationFrame(frame); }

$('pp').onclick = toggle;
$('gBeat').onchange = () => { if (Sound.isPlaying) { const at = Sound.scorePos; halt(); play(at); } };
let seeking = false;
$('seek').oninput = e => {
  if (!cur) return;
  seeking = true; const at = e.target.value / 1000 * cur.song.length;
  if (Sound.isPlaying) { Sound.stopScore(); Beat.stop(); play(at); } else { pausedAt = at; draw(); }
  seeking = false;
};
window.addEventListener('keydown', e => { if (e.key === ' ' && cur && e.target.tagName !== 'INPUT' && e.target.tagName !== 'BUTTON') { e.preventDefault(); toggle(); } });

// ----- Piano roll -----
const cv = $('roll'), cx = cv.getContext('2d');
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const INK = { ivory: css('--ivory'), brass: css('--brass'), dim: css('--brass-dim'), felt: css('--felt'), well: css('--well') };
function sizeRoll() {
  const r = cv.getBoundingClientRect(), d = window.devicePixelRatio || 1;
  cv.width = Math.max(10, Math.round(r.width * d)); cv.height = Math.max(10, Math.round(r.height * d));
  if (cur) draw();
}
window.addEventListener('resize', sizeRoll);
function draw() {
  if (!cur) return;
  const { song } = cur, pos = position(), W = cv.width, H = cv.height, d = window.devicePixelRatio || 1;
  const BEFORE = 1.5, SPAN = 8, px = W / SPAN, headX = BEFORE * px;
  if (!song.range) {
    const pitched = song.notes.filter(n => n.ch !== 9).map(n => n.note);
    song.range = pitched.length ? [Math.min(...pitched) - 2, Math.max(...pitched) + 2] : [48, 72];
  }
  const [lo, hi] = song.range, drumH = 6 * d, rowH = (H - drumH - 4 * d) / (hi - lo + 1), barH = Math.max(2 * d, Math.min(rowH, 7 * d));
  cx.clearRect(0, 0, W, H);
  for (const n of song.notes) {
    if (n.t > pos + SPAN - BEFORE) break;
    if (n.t + n.dur < pos - BEFORE) continue;
    const x = headX + (n.t - pos) * px, w = Math.max(2 * d, n.dur * px - 1.5 * d);
    const sounding = n.t <= pos && n.t + n.dur > pos, past = n.t + n.dur <= pos;
    if (n.ch === 9) { cx.fillStyle = past ? INK.dim : INK.brass; cx.fillRect(x, H - drumH, 2 * d, drumH); continue; }
    const y = (hi - n.note) * rowH + (rowH - barH) / 2;
    cx.fillStyle = sounding ? INK.felt : past ? INK.dim : INK.ivory;
    cx.globalAlpha = sounding ? 1 : past ? 0.7 : 0.35 + 0.6 * n.vel;
    cx.shadowColor = INK.felt; cx.shadowBlur = sounding ? 10 * d : 0;
    cx.beginPath(); cx.roundRect(x, y, w, barH, barH / 2); cx.fill();
  }
  cx.globalAlpha = 1; cx.shadowBlur = 0;
  cx.fillStyle = INK.brass; cx.fillRect(headX - d / 2, 0, d, H);
  if (!seeking) $('seek').value = Math.round(pos / song.length * 1000);
  $('nowTime').textContent = `${clock(pos / cur.rate)} of ${clock(song.length / cur.rate)}` + (Math.round(cur.tempo) !== Math.round(song.bpm) ? `, at ${cur.tempo} bpm` : '');
}

Sound.setVolume(0.8);
Sound.loadPiano();
loadCards();
