// Turns one piece into an audio file. The app opens render.html?job=<id> in a hidden browser; this script
// fetches the piece, plays it ahead of time with the studio's own sound engine (audio.js) on an
// OfflineAudioContext, which runs much faster than real time, and sends back a WAV.
const RENDER_TRACKS = ['kick', 'snare', 'hat', 'openhat', 'clap', 'tom', 'rim', 'cowbell'];
const RENDER_CODES = { '.': 0, x: 1, X: 2, o: 3 };
const RENDER_DESK = { bright: 18000, reso: 0.7, kdrive: 0, chorus: 0, echo: 0, fb: 0.35, room: 0.2, dlevel: 0.9, dtone: 18000, ddrive: 0, dtune: 0, droom: 0, decho: 0, film: 1 };

function readBeat(b) {
  b = b || {};
  const bars = [1, 2, 4, 8].includes(b.bars) ? b.bars : 1, tracks = {};
  for (const k of RENDER_TRACKS) {
    const c = String((b.tracks || {})[k] || '').replace(/[^xXo.]/g, '');
    tracks[k] = Array.from({ length: bars * 16 }, (_, i) => c.length ? RENDER_CODES[c[i % c.length]] : 0);
  }
  return { bars, tracks, swing: +b.swing || 0, kit: b.kit || '808', tempo: +b.tempo || 0, any: RENDER_TRACKS.some(k => tracks[k].some(Boolean)) };
}

// spec: { midi (base64), perf, beat, desk, sound, withBeat, film, withFilm, scoreBpm, seconds, rate, tail }, as music.py's
// piece_spec builds it. perf is how the score wants to be played; film is the layer of film sounds.
async function renderPiece(spec) {
  const SR = 44100, LEAD = 0.05;
  const bytes = Uint8Array.from(atob(spec.midi), c => c.charCodeAt(0));
  const song = makeSound(new OfflineAudioContext(2, 1, SR), false).parseMidi(bytes.buffer, spec.perf);
  if (!song.notes.length) throw new Error('This piece has no notes to play.');
  const beat = readBeat(spec.beat), written = Math.round(song.bpm);
  // Same rule as the gallery player: the Tempo box sets the speed once the piece has a tempo of its own there.
  let rate = (spec.scoreBpm === written && beat.tempo ? beat.tempo : written) / song.bpm;
  if (spec.rate > 0) rate = spec.rate;
  else if (spec.seconds > 0) rate = song.total / spec.seconds;
  const tempo = song.bpm * rate, musicEnd = LEAD + song.total / rate;
  const seconds = Math.min(musicEnd + (spec.tail ?? 3.5), 20 * 60);

  const AC = new OfflineAudioContext(2, Math.ceil(seconds * SR), SR), S = makeSound(AC, false);
  const sound = S.SOUNDS.some(([k]) => k === spec.sound) ? spec.sound : 'grand';
  S.setVolume(0.8); S.setSound(sound); S.setKit(beat.kit);
  const desk = { ...RENDER_DESK, ...(spec.desk || {}) };
  for (const k of RENDER_TRACKS) if (desk['mix_' + k] == null) desk['mix_' + k] = 1;
  for (const [k, v] of Object.entries(desk)) if (k !== 'echoTime' && typeof v === 'number') S.setDesk(k, v);
  S.setDesk('echoTime', (+desk.echoTime || 0.75) * 60 / tempo);
  const film = spec.withFilm === false ? null : spec.film || null;
  S.setSpace(desk.space || (film && film.space) || 'hall');

  // Everything that has to be loaded before the clock starts: the piano, and each instrument's notes.
  const usesKeys = song.notes.some(n => n.ch !== 9 && !(n.prog > 0));
  const piano = usesKeys && sound === 'grand' ? await S.loadPiano() : true;
  const sampled = await S.prepare(song, film);

  const filmSounds = S.schedule(song, { rate, at: LEAD, film });
  const withBeat = !!spec.withBeat && beat.any;
  if (withBeat) S.scheduleBeat(beat, tempo, LEAD, musicEnd);
  const out = await AC.startRendering();

  // 16-bit stereo WAV, turned down first if any sample would clip.
  const L = out.getChannelData(0), R = out.getChannelData(1);
  let peak = 0;
  for (let i = 0; i < L.length; i++) { const a = Math.abs(L[i]), b = Math.abs(R[i]); if (a > peak) peak = a; if (b > peak) peak = b; }
  const gain = peak > 0.94 ? 0.94 / peak : 1;   // a little room above the loudest sample, for what lies between samples
  const wav = new DataView(new ArrayBuffer(44 + L.length * 4));
  const text = (at, s) => { for (let i = 0; i < s.length; i++) wav.setUint8(at + i, s.charCodeAt(i)); };
  text(0, 'RIFF'); wav.setUint32(4, 36 + L.length * 4, true); text(8, 'WAVEfmt ');
  wav.setUint32(16, 16, true); wav.setUint16(20, 1, true); wav.setUint16(22, 2, true);
  wav.setUint32(24, SR, true); wav.setUint32(28, SR * 4, true); wav.setUint16(32, 4, true); wav.setUint16(34, 16, true);
  text(36, 'data'); wav.setUint32(40, L.length * 4, true);
  for (let i = 0, at = 44; i < L.length; i++, at += 4) {
    wav.setInt16(at, Math.round(L[i] * gain * 32767), true);
    wav.setInt16(at + 2, Math.round(R[i] * gain * 32767), true);
  }
  return {
    wav: wav.buffer,
    info: {
      seconds: +seconds.toFixed(3), music: +(song.total / rate).toFixed(3), rate: +rate.toFixed(5), tempo: +tempo.toFixed(2),
      written, notes: song.notes.length, instruments: song.parts.map(([p]) => p), sampled: !!sampled, piano: !!piano, withBeat, peak: +peak.toFixed(4),
      dynamics: song.dyn ? Object.keys(song.dyn).length : 0, film: filmSounds,
    },
  };
}

// The hidden-browser job. (The studio can also call renderPiece itself.)
(async () => {
  const job = new URLSearchParams(location.search).get('job');
  if (!job) return;
  const post = (path, body, type) => fetch(`/music/api/renderjob/${job}/${path}`, { method: 'POST', headers: { 'X-Storyboard': '1', 'Content-Type': type }, body });
  try {
    const spec = await (await fetch(`/music/api/renderjob/${job}`)).json();
    if (spec.error) throw new Error(spec.error);
    const { wav, info } = await renderPiece(spec);
    await post('wav?' + encodeURIComponent(JSON.stringify(info)), wav, 'audio/wav');
  } catch (e) {
    await post('failed?' + encodeURIComponent(JSON.stringify({ error: String((e && e.message) || e) })), '{}', 'application/json');
  }
})();
