// General MIDI instruments, shared by the server and the browser.
// Index = MIDI program number. LY is the name LilyPond expects in midiInstrument;
// SAMPLE is the name of the sampled instrument (FluidR3 GM, via midi-js-soundfonts).
(function (root) {
  const LY = [
  "acoustic grand", "bright acoustic", "electric grand", "honky-tonk", "electric piano 1", "electric piano 2",
  "harpsichord", "clav", "celesta", "glockenspiel", "music box", "vibraphone", "marimba", "xylophone",
  "tubular bells", "dulcimer", "drawbar organ", "percussive organ", "rock organ", "church organ", "reed organ",
  "accordion", "harmonica", "concertina", "acoustic guitar (nylon)", "acoustic guitar (steel)",
  "electric guitar (jazz)", "electric guitar (clean)", "electric guitar (muted)", "overdriven guitar",
  "distorted guitar", "guitar harmonics", "acoustic bass", "electric bass (finger)", "electric bass (pick)",
  "fretless bass", "slap bass 1", "slap bass 2", "synth bass 1", "synth bass 2", "violin", "viola", "cello",
  "contrabass", "tremolo strings", "pizzicato strings", "orchestral harp", "timpani", "string ensemble 1",
  "string ensemble 2", "synthstrings 1", "synthstrings 2", "choir aahs", "voice oohs", "synth voice",
  "orchestra hit", "trumpet", "trombone", "tuba", "muted trumpet", "french horn", "brass section", "synthbrass 1",
  "synthbrass 2", "soprano sax", "alto sax", "tenor sax", "baritone sax", "oboe", "english horn", "bassoon",
  "clarinet", "piccolo", "flute", "recorder", "pan flute", "blown bottle", "shakuhachi", "whistle", "ocarina",
  "lead 1 (square)", "lead 2 (sawtooth)", "lead 3 (calliope)", "lead 4 (chiff)", "lead 5 (charang)",
  "lead 6 (voice)", "lead 7 (fifths)", "lead 8 (bass+lead)", "pad 1 (new age)", "pad 2 (warm)", "pad 3 (polysynth)",
  "pad 4 (choir)", "pad 5 (bowed)", "pad 6 (metallic)", "pad 7 (halo)", "pad 8 (sweep)", "fx 1 (rain)",
  "fx 2 (soundtrack)", "fx 3 (crystal)", "fx 4 (atmosphere)", "fx 5 (brightness)", "fx 6 (goblins)", "fx 7 (echoes)",
  "fx 8 (sci-fi)", "sitar", "banjo", "shamisen", "koto", "kalimba", "bagpipe", "fiddle", "shanai", "tinkle bell",
  "agogo", "steel drums", "woodblock", "taiko drum", "melodic tom", "synth drum", "reverse cymbal",
  "guitar fret noise", "breath noise", "seashore", "bird tweet", "telephone ring", "helicopter", "applause",
  "gunshot"
  ];
  const SAMPLE = [
  "acoustic_grand_piano", "bright_acoustic_piano", "electric_grand_piano", "honkytonk_piano", "electric_piano_1",
  "electric_piano_2", "harpsichord", "clavinet", "celesta", "glockenspiel", "music_box", "vibraphone", "marimba",
  "xylophone", "tubular_bells", "dulcimer", "drawbar_organ", "percussive_organ", "rock_organ", "church_organ",
  "reed_organ", "accordion", "harmonica", "tango_accordion", "acoustic_guitar_nylon", "acoustic_guitar_steel",
  "electric_guitar_jazz", "electric_guitar_clean", "electric_guitar_muted", "overdriven_guitar", "distortion_guitar",
  "guitar_harmonics", "acoustic_bass", "electric_bass_finger", "electric_bass_pick", "fretless_bass", "slap_bass_1",
  "slap_bass_2", "synth_bass_1", "synth_bass_2", "violin", "viola", "cello", "contrabass", "tremolo_strings",
  "pizzicato_strings", "orchestral_harp", "timpani", "string_ensemble_1", "string_ensemble_2", "synth_strings_1",
  "synth_strings_2", "choir_aahs", "voice_oohs", "synth_choir", "orchestra_hit", "trumpet", "trombone", "tuba",
  "muted_trumpet", "french_horn", "brass_section", "synth_brass_1", "synth_brass_2", "soprano_sax", "alto_sax",
  "tenor_sax", "baritone_sax", "oboe", "english_horn", "bassoon", "clarinet", "piccolo", "flute", "recorder",
  "pan_flute", "blown_bottle", "shakuhachi", "whistle", "ocarina", "lead_1_square", "lead_2_sawtooth",
  "lead_3_calliope", "lead_4_chiff", "lead_5_charang", "lead_6_voice", "lead_7_fifths", "lead_8_bass__lead",
  "pad_1_new_age", "pad_2_warm", "pad_3_polysynth", "pad_4_choir", "pad_5_bowed", "pad_6_metallic", "pad_7_halo",
  "pad_8_sweep", "fx_1_rain", "fx_2_soundtrack", "fx_3_crystal", "fx_4_atmosphere", "fx_5_brightness",
  "fx_6_goblins", "fx_7_echoes", "fx_8_scifi", "sitar", "banjo", "shamisen", "koto", "kalimba", "bagpipe", "fiddle",
  "shanai", "tinkle_bell", "agogo", "steel_drums", "woodblock", "taiko_drum", "melodic_tom", "synth_drum",
  "reverse_cymbal", "guitar_fret_noise", "breath_noise", "seashore", "bird_tweet", "telephone_ring", "helicopter",
  "applause", "gunshot"
  ];
  const LABEL = LY.map(s => s[0].toUpperCase() + s.slice(1));

  // How each program behaves when played: 'sustain' instruments hold while the note is held,
  // 'decay' instruments ring out. The fallback names a synth recipe used until samples arrive.
  const FAMILY = [
    ['piano', 'decay'], ['mallet', 'decay'], ['organ', 'sustain'], ['pluck', 'decay'],
    ['bass', 'decay'], ['bowed', 'sustain'], ['bowed', 'sustain'], ['brass', 'sustain'],
    ['reed', 'sustain'], ['flute', 'sustain'], ['lead', 'sustain'], ['pad', 'sustain'],
    ['pad', 'sustain'], ['pluck', 'decay'], ['mallet', 'decay'], ['pad', 'decay'],
  ];
  const DECAYS = new Set([45, 46, 47, 55, 105, 106, 107, 108, 112, 113, 114, 115, 116, 117, 118, 119, 127]);
  const HELD = new Set([122, 125, 126]);   // seashore, helicopter, applause: sounds that go on for as long as the note
  function family(p) {
    const [fallback, kind] = FAMILY[p >> 3];
    if (p === 45 || p === 46) return { fallback: 'pluck', sustain: false };   // pizzicato, harp
    if (p === 47) return { fallback: 'timpani', sustain: false };
    if (p >= 52 && p <= 54) return { fallback: 'choir', sustain: true };
    if (p >= 109 && p <= 111) return { fallback: 'reed', sustain: true };      // bagpipe, fiddle, shanai
    if (HELD.has(p)) return { fallback, sustain: true };
    return { fallback, sustain: kind === 'sustain' && !DECAYS.has(p) };
  }

  // Stereo seat for each instrument, as on a concert stage seen from the hall (-1 left, 1 right).
  const SEAT = {
    40: -0.45, 41: 0.2, 42: 0.4, 43: 0.55, 44: -0.2, 45: -0.3, 46: -0.55, 47: 0.1, 48: -0.15, 49: 0.15,
    52: -0.1, 53: 0.1, 56: 0.25, 57: 0.4, 58: 0.5, 59: 0.25, 60: -0.35, 61: 0.3,
    64: -0.1, 65: 0.05, 66: 0.15, 67: 0.25, 68: 0.1, 69: 0.15, 70: 0.2, 71: -0.1, 72: -0.2, 73: -0.15,
    8: -0.3, 9: -0.35, 10: -0.3, 11: -0.3, 12: -0.25, 13: -0.3, 14: -0.25,
    // A band: rhythm guitar to one side, lead to the other, keys and horns in between, bass in the middle.
    4: -0.25, 5: -0.25, 6: 0.25, 7: 0.25, 16: 0.3, 17: 0.3, 18: 0.3, 19: 0.15, 20: 0.3, 21: -0.25, 22: 0.2,
    24: -0.3, 25: -0.3, 26: -0.3, 27: -0.3, 28: 0.3, 29: 0.35, 30: -0.35, 31: 0.3, 62: 0.2, 63: 0.2,
    80: 0.15, 81: 0.15, 82: 0.15, 83: 0.15, 84: 0.15, 85: 0.15, 86: 0.15, 87: 0.15, 88: -0.1, 89: -0.1, 90: -0.1, 91: -0.1,
    104: -0.2, 105: 0.3, 106: 0.2, 107: 0.2, 108: -0.25, 109: 0.2, 110: -0.3, 114: 0.25,
  };

  // The recordings of a few instruments stop below the top of the keyboard: above these notes they are silent, so
  // the player takes the highest note that sounds and tunes it up. (Measured on the FluidR3 GM set.)
  const TOP = { 43: 57, 58: 72, 67: 72, 28: 84, 33: 84, 34: 84, 35: 84, 36: 84, 37: 84, 65: 84, 70: 84, 31: 85, 69: 85, 40: 93, 110: 93 };
  // A few are recorded much louder than the rest, and a few much softer. These bring them into line (in decibels).
  const TRIM = { 58: -4, 78: -4, 57: -3, 71: -3, 39: -3, 79: -3, 31: -3, 76: -3, 93: -3, 65: -2, 72: -2, 75: -2, 85: -2, 77: -2, 1: 3, 3: 3, 86: 3 };

  // Instruments offered on the keyboard's Sound menu, grouped.
  const KEYBOARD = [
    ['Orchestra', [48, 40, 41, 42, 43, 45, 44, 46, 47, 73, 72, 68, 71, 70, 69, 60, 56, 57, 58, 61, 52, 53, 8, 9, 13, 14]],
    ['Band and world', [24, 25, 26, 29, 30, 32, 33, 35, 16, 19, 21, 66, 65, 11, 12, 105, 107, 108, 110, 114]],
    ['Keyboards', [1, 2, 3, 4, 5, 6, 7, 10, 15, 17, 18, 20, 22, 23]],
    ['Guitars and basses', [27, 28, 31, 34, 36, 37, 38, 39]],
    ['More orchestra', [49, 50, 51, 54, 55, 59, 62, 63, 64, 67, 74, 75, 76, 77, 78, 79]],
    ['Synths and pads', [80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103]],
    ['More from around the world', [104, 106, 109, 111, 112, 113, 115, 116, 117, 118, 119]],
    ['Sound effects', [120, 121, 122, 123, 124, 125, 126, 127]],
  ];

  const api = { LY, SAMPLE, LABEL, family, seat: p => SEAT[p] || 0, KEYBOARD, top: p => TOP[p] || 96, trim: p => Math.pow(10, (TRIM[p] || 0) / 20),
    byLyName: Object.fromEntries(LY.map((n, i) => [n, i])) };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.GM = api;
})(typeof window !== 'undefined' ? window : globalThis);
