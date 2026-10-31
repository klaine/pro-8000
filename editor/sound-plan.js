// What the editor (index.html) and the installer (install.html) share: the settings' numbers and the preset format
// turned into SysEx. A classic script, so these are plain globals for the page's own script after it.

// --- model ---
const SETTINGS = ["lfo1Sync", "lfo1Trigger", "layers", "split", "keytrack", "lfo2Sync", "lfo2Trigger", "editLayer",
  "lfoSelect", "shape11", "shape12", "shape21", "shape22", "lfo2Dests1", "lfo2Dests2", "cycle1", "cycle2",
  "cycleSync1_0", "cycleSync1_1", "cycleSync1_2", "cycleSync1_3", "cycleSync2_0", "cycleSync2_1", "cycleSync2_2",
  "cycleSync2_3", "bend", "detune", "voice1", "voice2", "drift", "scale", "root", "lfoDelay1", "lfoFade1", "lfoDelay2", "lfoFade2",
  "ccA", "ccB", "macro11", "macro12", "macro21", "macro22", "clockSource", "bpm",
  "envUp1", "envUp2", "dahdsr1", "dahdsr2", "curve1", "curve2", // 0.9.0: a bit per envelope; 2 bits per envelope
  ...[1, 2].flatMap(l => [0, 1, 2, 3].map(e => `envDelay${l}_${e}`)), ...[1, 2].flatMap(l => [0, 1, 2, 3].map(e => `envHold${l}_${e}`)),
  "layerVoices", ...[1, 2].flatMap(l => [0, 1, 2, 3].map(e => `envCount${l}_${e}`)), // cycles, 0 endless
  "envTempo1", "envTempo2", // a bit per envelope: stage times in note values
  "midiChannel", // 1-16, 0 every channel (system-wide)
  "oscTune1A", "oscTune1B", "oscTune2A", "oscTune2B", // FREQ A / B per layer: semitones, octave, free, fixed
  "pickup", // 1 knob pickup, 0 jump (system-wide)
  "layerLink", // what layer 2 takes from layer 1: a bit per envelope (ENV_CYCLE_* order), 16 LFO 1, 32 LFO 2, 64 filter
  "lfoCycles1", "lfoCycles2", // a one shot's cycles, 1-99 (0 is taken as 1)
  "lfoRestart", // a sequence's LFO restarts: 0 from its steps, 1 from the keys
  "routeBoth1Lo", "routeBoth1Hi", "routeBoth2Lo", "routeBoth2Hi", // per layer, a bit per route slot: plays on both layers
  "envSelect", "presetMode", "patch", "morph"];
const SET_ID = Object.fromEntries(SETTINGS.map((n, i) => [n, i]));
const READ_ONLY = 4; // envSelect, presetMode, patch, morph: the end of the 54 reply

const SEQ_MAX = 64, SEQ_BYTES = 240;
const seqMax = lanes => Math.min(SEQ_MAX, Math.floor(SEQ_BYTES / (4 + lanes)));

// --- the preset format (presets/README.md), with the pro800 tool's names ---
const JN = {
  triggers: ["free", "first", "every", "voice", "one-shot", "one-shot-voice"],
  params: ["amp-rel", "amp-sus", "amp-dec", "amp-att", "fil-rel", "fil-sus", "fil-dec", "fil-att", "resonance",
    "fil-env-amt", "freq-a", "freq-b", "fine", "a-pw", "b-pw", "a-level", "glide", "lfo-freq", "lfo-amount", "macro1",
    "master-vol", "b-level", "pmod-osc-b", "pmod-fil-env", "cutoff", "noise", "env3-att", "env3-dec", "env3-sus",
    "env3-rel", "env4-att", "env4-dec", "env4-sus", "env4-rel", "lfo2-rate", "lfo2-amount"], // P order
  shared: ["a-level", "b-level", "noise", "resonance", "a-pw", "b-pw", "pmod-osc-b"],
  buttons: { 0: "a-saw", 1: "a-tri", 2: "a-square", 3: "b-saw", 4: "b-tri", 5: "b-square", 6: "lfo-pw", 7: "lfo-freq",
    8: "pmod-freq-a", 10: "lfo-filter", 11: "pmod-filter", 12: "unison", 15: "sync" },
  sources: ["lfo", "filter-env", "amp-env", "velocity", "mod-wheel", "aftertouch", "env3", "env4", "random", "note",
    "lfo2", "seq-a", "seq-b", "seq-c", "seq-d", "lfo-uni", "lfo2-uni", "gate", "timbre", "bend", "pedal", "cc-a", "cc-b",
    "voice", "fixed", "macro1", "macro2"], // source 1 on
  dests: ["pitch-a", "pitch-b", "pitch", "cutoff", "filter-env-amount", "pmod-env", "lfo-amount", "lfo-to-pitch",
    "lfo-to-filter", "vca", "a-level", "b-level", "noise", "resonance", "a-pw", "b-pw", "pmod-osc-b", "lfo-to-pw",
    "lfo-rate", "lfo2-amount", "lfo2-rate", "lfo2-to-pitch", "lfo2-to-filter", "lfo2-to-pw", "morph", "amp-att",
    "amp-dec", "amp-sus", "amp-rel", "fil-att", "fil-dec", "fil-sus", "fil-rel", "env3-att", "env3-dec", "env3-sus",
    "env3-rel", "env4-att", "env4-dec", "env4-sus", "env4-rel", "glide", "detune", "volume", "macro1", "macro2"],
  shapes: ["tri", "square", "ramp-up", "ramp-down", "sine", "sh", "drift", "steps", "noise"],
  divisions: ["8bar", "4bar", "2bar", "1bar", "1/2", "1/4.", "1/4", "1/4T", "1/8.", "1/8", "1/8T", "1/16.", "1/16",
    "1/16T", "1/32"],
  envs: ["amp", "filter", "env3", "env4"],
  arps: ["off", "up", "down", "up-down", "random", "played"],
};
class JsonProblem extends Error {}
const bad = text => { throw new JsonProblem(text); };
const jInt = (lo, hi) => ({ dec: v => v, enc: (x, k) => Number.isInteger(x) && x >= lo && x <= hi ? x : bad(`${k}: ${lo}-${hi}, not ${JSON.stringify(x)}`) });
const jOne = names => ({ dec: v => names[v], enc: (x, k) => names.includes(String(x)) ? names.indexOf(String(x)) : bad(`${k}: one of ${names.join(" ")}, not ${JSON.stringify(x)}`) });
const jBits = names => ({ dec: v => names.filter((_, i) => v >> i & 1).join(",") || "none",
  enc: (x, k) => String(x) === "none" ? 0 : String(x).split(",").reduce((b, n) => names.includes(n.trim()) ? b | 1 << names.indexOf(n.trim()) : bad(`${k}: none or some of ${names.join(",")}, not ${JSON.stringify(x)}`), 0) });
// [JSON key, the page's setting, codec]; a codec with a shift is a field of a shared setting (the curves)
const JSON_SETTINGS = [
  ["layers", "layers", jOne(["off", "split", "stack", "channel", "8ch", "mpe"])], ["split", "split", jInt(0, 127)],
  ["layer-voices", "layerVoices", { dec: v => v || "auto", enc: (x, k) => x === "auto" ? 0 : jInt(1, 7).enc(x, k) }],
  ...[1, 2].flatMap(l => ["a", "b"].map(o => [`osc-tune ${l} ${o}`, `oscTune${l}${o.toUpperCase()}`, jOne(["semitones", "octave", "free", "fixed"])])),
  ["link", "layerLink", jBits(["amp-env", "filter-env", "env3", "env4", "lfo1", "lfo2", "filter"])],
  ["keytrack", "keytrack", jOne(["off", "half", "full"])],
  ["lfo-sync", "lfo1Sync", jOne(["free", "clock"])], ["lfo-trigger", "lfo1Trigger", jOne(JN.triggers)],
  ["lfo2-sync", "lfo2Sync", jOne(["free", "clock"])], ["lfo2-trigger", "lfo2Trigger", jOne(JN.triggers)],
  ["lfo-cycles", "lfoCycles1", jInt(1, 99)], ["lfo2-cycles", "lfoCycles2", jInt(1, 99)],
  ["lfo-restart", "lfoRestart", jOne(["steps", "keys"])],
  ...[1, 2].flatMap(l => [1, 2].map(n => [`shape ${l} ${n}`, `shape${l}${n}`, jOne(JN.shapes)])),
  ...[1, 2].flatMap(l => [
    [`lfo2-dests ${l}`, `lfo2Dests${l}`, jBits(["pitch", "filter", "pw"])],
    [`voice ${l}`, `voice${l}`, { dec: v => `${["poly", "mono", "legato"][v & 3]} ${["last", "low", "high"][v >> 2 & 3]}`,
      enc: (x, k) => { const [m, p] = String(x).split(" "); const mi = ["poly", "mono", "legato"].indexOf(m), pi = ["last", "low", "high"].indexOf(p);
        return mi >= 0 && pi >= 0 ? mi | pi << 2 : bad(`${k}: "poly|mono|legato last|low|high", not ${JSON.stringify(x)}`); } }],
    [`cycle ${l}`, `cycle${l}`, jBits(JN.envs)],
    ...JN.envs.map((e, i) => [`cycle-sync ${l} ${e}`, `cycleSync${l}_${i}`,
      { dec: v => v ? JN.divisions[v - 1] : "free", enc: (x, k) => x === "free" ? 0 : jOne(JN.divisions).enc(x, k) + 1 }]),
    [`env-up ${l}`, `envUp${l}`, jBits(JN.envs)], [`dahdsr ${l}`, `dahdsr${l}`, jBits(JN.envs)],
    ...JN.envs.map((e, i) => [`env-curve ${l} ${e}`, `curve${l}`, { ...jOne(["analog", "linear", "swell"]), shift: 2 * i, mask: 3 }]),
    ...JN.envs.map((e, i) => [`env-delay ${l} ${e}`, `envDelay${l}_${i}`, jInt(0, 99)]),
    ...JN.envs.map((e, i) => [`env-hold ${l} ${e}`, `envHold${l}_${i}`, jInt(0, 99)]),
    ...JN.envs.map((e, i) => [`env-count ${l} ${e}`, `envCount${l}_${i}`, { dec: v => v || "loop", enc: (x, k) => x === "loop" ? 0 : jInt(1, 99).enc(x, k) }]),
    [`env-tempo ${l}`, `envTempo${l}`, jBits(JN.envs)],
    ...[1, 2].map(m => [`macro ${l} ${m}`, `macro${l}${m}`, { dec: v => v - 99, enc: (x, k) => jInt(-99, 99).enc(x, k) + 99 }]),
  ]),
  ["bend", "bend", jInt(1, 12)], ["detune", "detune", jInt(0, 50)], ["drift", "drift", jInt(0, 99)],
  ["scale", "scale", jOne(["off", "major", "minor", "dorian", "phrygian", "lydian", "mixolydian", "harmonic-minor",
    "pentatonic-major", "pentatonic-minor", "blues", "whole-tone", "chromatic"])],
  ["root", "root", jOne(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])],
  ...[1, 2].flatMap(n => [[`lfo-delay ${n}`, `lfoDelay${n}`, jInt(0, 99)], [`lfo-fade ${n}`, `lfoFade${n}`, jInt(0, 99)]]),
];

// A preset-format object as the SysEx messages that build it, after the init sound, checked first; `has` tells
// whether the firmware has a setting. Returns { messages, warnings }.
function planSound(j, has) {
  if (!j || typeof j !== "object" || Array.isArray(j)) bad("the JSON isn't an object");
  const messages = [[0x5F]], warnings = [], st = { ...(j.settings || {}) };
  const setting = (id, v) => messages.push([0x7D, id, v >> 7, v & 127]);
  const layersFirst = JSON_SETTINGS[0];
  if (st.layers !== undefined) { setting(SET_ID.layers, layersFirst[2].enc(st.layers, "layers")); delete st.layers; }
  const on = j.buttons || [];
  if (!Array.isArray(on)) bad("buttons: a list of names");
  for (const name of on) if (!Object.values(JN.buttons).includes(name)) bad(`button: ${JSON.stringify(name)}`);
  for (const [i, name] of Object.entries(JN.buttons)) messages.push([0x4B, +i, on.includes(name) ? 1 : 0]);
  for (const [group, layer] of [["shared", 0], ["1", 0], ["2", 1]])
    for (const [name, v] of Object.entries((j.params || {})[group] || {})) {
      const i = JN.params.indexOf(name);
      if (i < 0 || name === "master-vol") bad(`parameter: ${JSON.stringify(name)}`);
      if ((group === "shared") !== JN.shared.includes(name)) bad(group === "shared" ? `${name} isn't shared by the layers` : `${name} is shared: put it under params.shared`);
      const q = Math.round(jInt(0, 99).enc(v, name) / 99 * 16383);
      messages.push([0x52, layer, i, q >> 7, q & 0x7F]);
    }
  const fields = {};
  for (const [key, value] of Object.entries(st)) {
    const m = /^steps ([12]) ([12])$/.exec(key);
    if (m) {
      const pat = String(value);
      if (!/^[x1.0-]{1,16}$/.test(pat)) bad(`${key}: 1-16 of x and ., not ${JSON.stringify(value)}`);
      const bits = [...pat].reduce((b, c, i) => "x1".includes(c) ? b | 1 << i : b, 0);
      messages.push([0x4E, m[1] - 1, m[2] - 1, pat.length, bits >> 14 & 0x7F, bits >> 7 & 0x7F, bits & 0x7F]);
      continue;
    }
    const entry = JSON_SETTINGS.find(e => e[0] === key);
    if (!entry) { warnings.push(`unknown setting "${key}"`); continue; }
    const [, set, c] = entry, v = c.enc(value, key);
    if (!has(set)) { warnings.push(`"${key}": this firmware hasn't got it`); continue; }
    if (c.shift !== undefined) { const f = fields[set] || (fields[set] = { v: 0 }); f.v |= v << c.shift; continue; }
    setting(SET_ID[set], v);
  }
  for (const [set, f] of Object.entries(fields)) setting(SET_ID[set], f.v); // unnamed curves are analog, as in the init sound
  for (const [layer, list] of Object.entries(j.routes || {})) {
    if (layer !== "1" && layer !== "2") bad(`routes: layer "1" or "2", not ${JSON.stringify(layer)}`);
    if (!Array.isArray(list) || list.length > 16) bad(`routes ${layer}: a list of up to 16`);
    let bothBits = 0;
    for (const [slot, r] of list.entries()) { // after the init sound a layer's routes fill its slots in order
      if (!Array.isArray(r) || !(r.length === 3 || (r.length === 4 && r[3] === "both"))) bad(`route: [source, destination, depth] or [source, destination, depth, "both"], not ${JSON.stringify(r)}`);
      if (r.length === 4) bothBits |= 1 << slot;
      const s = JN.sources.indexOf(r[0]) + 1, d = JN.dests.indexOf(r[1]);
      if (!s) bad(`route source: ${JSON.stringify(r[0])}`);
      if (d < 0) bad(`route destination: ${JSON.stringify(r[1])}`);
      const depth = jInt(-99, 99).enc(r[2], `depth of ${r[0]} → ${r[1]}`) + 99;
      messages.push([0x56, layer - 1, s, d, depth >> 7, depth & 0x7F]);
    }
    if (bothBits && !has(`routeBoth${layer}Lo`)) warnings.push(`routes ${layer}: "both": this firmware hasn't got it`);
    else if (bothBits) { setting(SET_ID[`routeBoth${layer}Lo`], bothBits & 255); setting(SET_ID[`routeBoth${layer}Hi`], bothBits >> 8); }
  }
  const q = j.sequence;
  if (q) {
    const lanes = jInt(0, 4).enc(q.lanes ?? 0, "sequence lanes"), chord = jInt(0, 2).enc(q.chord ?? 0, "sequence chord"), list = q.steps || [];
    if (lanes + chord > 4) bad("sequence: lanes + chord, at most 4");
    if (!Array.isArray(list) || !list.length || list.length > seqMax(lanes + chord)) bad(`sequence: 1-${seqMax(lanes + chord)} steps with ${lanes} lane(s) and chord ${chord}`);
    const keys = ["both", "1", "2"].indexOf(String(q.keys ?? "both")), arp = JN.arps.indexOf(q.arp === "updown" ? "up-down" : q.arp ?? "off");
    if (keys < 0) bad(`sequence keys: both, 1 or 2, not ${JSON.stringify(q.keys)}`);
    if (arp < 0) bad(`sequence arp: one of ${JN.arps.join(" ")}, not ${JSON.stringify(q.arp)}`);
    const flags = (q.on ?? true ? 1 : 0) | (q.clock ?? true ? 2 : 0) | (q.once ? 4 : 0) | keys << 3 | chord << 5;
    const smooth = [...String(q.smooth || "")].reduce((b, c) => "ABCD".includes(c.toUpperCase()) ? b | 1 << "ABCD".indexOf(c.toUpperCase()) : bad(`sequence smooth: lanes A-D, not ${JSON.stringify(q.smooth)}`), 0);
    const bpm = jInt(30, 255).enc(q.bpm ?? 120, "sequence bpm"); // the firmware keeps it in a byte
    messages.push([0x47, list.length, lanes + chord, jOne(JN.divisions).enc(q.step ?? "1/16", "sequence step"), bpm >> 7, bpm & 0x7F,
      flags, jInt(0, 75).enc(q.swing ?? 0, "sequence swing"), smooth, arp]);
    list.forEach((x, k) => {
      const n = `step ${k + 1}`, rest = !!x.rest;
      const note = rest ? 64 : 64 + jInt(-36, 36).enc(x.note ?? 0, `${n} note`);
      const prob = jInt(1, 8).enc(x.prob ?? 8, `${n} prob`), ratchets = jInt(1, 4).enc(x.ratchets ?? 1, `${n} ratchets`);
      const lane = [0, 1, 2, 3].map(i => 64 + Math.round(jInt(-99, 99).enc(x["abcd"[i]] ?? 0, `${n} lane ${"abcd"[i]}`) * 63 / 99));
      for (const c of [0, 1]) {
        const v = jInt(-36, 36).enc(x[`n${c + 2}`] ?? 0, `${n} n${c + 2}`);
        if (v && c >= chord) bad(`${n}: n${c + 2} needs sequence chord ${c + 1}`);
        if (c < chord) lane[lanes + c] = 64 + v;
      }
      messages.push([0x45, k, 1, note, jInt(0, 127).enc(x.vel ?? 0, `${n} vel`), jInt(1, 100).enc(x.gate ?? 50, `${n} gate`),
        rest ? 0 : 1 | jInt(0, 7).enc(x.hold ?? 0, `${n} hold`) << 1, (8 - prob) | (ratchets - 1) << 3, ...lane]);
    });
  }
  if (j.morph) { const t = jInt(1, 128).enc(j.morph, "morph"); messages.push([0x44, t >> 7, t & 0x7F]); }
  if (j.name !== undefined && (typeof j.name !== "string" || j.name.length > 16)) bad("name: up to 16 characters");
  return { messages, warnings };
}
