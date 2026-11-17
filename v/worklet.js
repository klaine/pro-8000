/* AudioWorklet host for PRO-8000 V (p8v.wasm through p8-core.js). The engine lives on the audio thread; the page
 * (p8-web.js) talks to it with messages, handled in order:
 *   {t:"send", m}            a message for the firmware (the editor's SysEx, a key, a controller)
 *   {t:"bank", area, slot}   show a bank (98,304 bytes) and load `slot` from it
 *   {t:"capture", id, name}  -> {t:"captured", id, record}     the sound as a record
 *   {t:"load", record, slot} the record as the sound
 *   {t:"fx", i, v} / {t:"fxPreset", k} / {t:"fxDefaults"} / {t:"fxSet", values}
 *   {t:"fxInfo", id} -> {t:"fxInfo", id, info}   {t:"fxValues", id} -> {t:"fxValues", id, values, active}
 *   {t:"timing", ideal}  {t:"eeprom", id} -> {t:"eeprom", id, image}
 * and hears back {t:"ready"}, {t:"midi", list} (the firmware's SysEx), {t:"saved", slots, records} (slots the
 * firmware wrote), {t:"slot", slot} (the sound's slot changed), {t:"peak", v}.
 */
import { instantiateEngine, midiSplitter } from "./p8-core.js";

class P8Processor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = options.processorOptions;
    this.ok = false;
    this.split = midiSplitter();
    this.blocks = 0;
    this.peak = 0;
    this.slot = -1;
    try {
      this.e = instantiateEngine(o.wasm);
      this.ok = this.e.init(sampleRate, new Uint8Array(o.image), o.formatted, o.ideal);
      if (!this.ok) this.error = "the firmware didn't start";
    } catch (err) {
      this.error = String(err && err.message || err);
    }
    this.port.onmessage = (ev) => this.onMessage(ev.data);
    this.port.postMessage({ t: "ready", ok: this.ok, error: this.error, sampleRate, version: this.ok ? this.e.version() : "" });
  }

  onMessage(d) {
    if (!this.ok) { if (d.id) this.port.postMessage({ t: "failed", id: d.id }); return; }
    try { this.handle(d); } catch (err) { // a request still gets its answer, so the page doesn't wait for ever
      if (d.id) this.port.postMessage({ t: "failed", id: d.id, error: String(err && err.message || err) });
    }
  }

  handle(d) {
    const e = this.e, x = e.x;
    switch (d.t) {
      case "send": e.send(d.m); break;
      case "bank": e.showBank(new Uint8Array(d.area), d.slot); break;
      case "capture": this.port.postMessage({ t: "captured", id: d.id, record: e.capture(d.name || "") }); break;
      case "load": e.loadRecord(new Uint8Array(d.record), d.slot); break;
      case "fx": x.wa_fx_set(d.i, d.v); break;
      case "fxSet": d.values.forEach((v, i) => x.wa_fx_set(i, v)); break;
      case "fxPreset": x.wa_fx_preset(d.k); break;
      case "fxDefaults": x.wa_fx_defaults(); break;
      case "fxInfo": this.port.postMessage({ t: "fxInfo", id: d.id, info: e.fxInfo() }); break;
      case "fxValues": this.port.postMessage({ t: "fxValues", id: d.id, values: e.fxValues(), active: !!x.wa_fx_active() }); break;
      case "timing": x.wa_set_timing(d.ideal ? 1 : 0); break;
      case "eeprom": this.port.postMessage({ t: "eeprom", id: d.id, image: e.eeprom() }); break;
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0];
    if (!this.ok || !out || !out.length) return true;
    const n = out[0].length;
    let l, r;
    try {
      [l, r] = this.e.render(n);
    } catch (err) { // the firmware stopped for good: silence from here
      this.ok = false;
      this.port.postMessage({ t: "stopped", error: String(err && err.message || err) });
      return true;
    }
    out[0].set(l);
    if (out[1]) out[1].set(r);
    let pk = this.peak;
    for (let i = 0; i < n; i++) { const a = l[i] < 0 ? -l[i] : l[i]; if (a > pk) pk = a; }
    this.peak = pk;
    const bytes = this.e.output();
    if (bytes) {
      const list = [];
      this.split(bytes, list);
      if (list.length) this.port.postMessage({ t: "midi", list });
    }
    if (++this.blocks % 16 === 0) { // about every 40 ms: what the firmware saved, the slot, the level
      const saved = this.e.dirty();
      if (saved) this.port.postMessage({ t: "saved", ...saved });
      const s = this.e.slot();
      if (s !== this.slot) { this.slot = s; this.port.postMessage({ t: "slot", slot: s }); }
      this.port.postMessage({ t: "peak", v: this.peak });
      this.peak = 0;
    }
    return true;
  }
}
registerProcessor("pro8000v", P8Processor);
