/* The PRO-8000 V engine (p8v.wasm, web/wasm_host.cpp) instantiated: the Asyncify half of the firmware's fiber, stubs
 * for the few WASI calls the C library makes, and a small API over the module's buffers. Used by the AudioWorklet
 * (worklet.js) and by the node test (tests/web/test_wasm.mjs).
 *
 * The firmware's main loop yields at its watchdog write (fw/hal.c): p8_yield starts an unwind there, back to
 * resume_firmware, which calls p8_unwound; the next pass calls p8_rewind and firmware_main again, which winds back
 * down to the same p8_yield, where the rewind ends and the loop goes on. The unwound frames live in p8_async_data.
 */
export function instantiateEngine(bytes) {
  const module = new WebAssembly.Module(bytes);
  let x = null, data = 0, rewinding = false;
  const env = {
    p8_yield() {
      if (rewinding) { x.asyncify_stop_rewind(); rewinding = false; return; }
      const v = new Int32Array(x.memory.buffer, data, 2);
      v[0] = data + 8; v[1] = data + 8 + 4096 * 4;
      x.asyncify_start_unwind(data);
    },
    p8_rewind() { rewinding = true; x.asyncify_start_rewind(data); },
    p8_unwound() { x.asyncify_stop_unwind(); },
    p8_halt() { throw new Error("the firmware stopped (update mode or a crash on purpose)"); },
  };
  // The C library's few system calls: nothing to print to, no clock but the host's, no exit
  const wasi = {};
  for (const imp of WebAssembly.Module.imports(module)) {
    if (imp.module !== "wasi_snapshot_preview1") continue;
    wasi[imp.name] = imp.name === "proc_exit" ? (code) => { throw new Error("exit " + code); }
      : imp.name === "fd_write" ? (fd, iovs, n, written) => { // count the bytes as written, show nothing
        const m = new DataView(x.memory.buffer);
        let total = 0;
        for (let k = 0; k < n; k++) total += m.getUint32(iovs + 8 * k + 4, true);
        m.setUint32(written, total, true);
        return 0;
      }
      : imp.name === "random_get" ? (buf, len) => { const u = new Uint8Array(x.memory.buffer, buf, len); for (let k = 0; k < len; k++) u[k] = (Math.random() * 256) | 0; return 0; }
      : imp.name === "clock_time_get" ? (id, precision, out) => { new DataView(x.memory.buffer).setBigUint64(out, BigInt(Math.round(Date.now() * 1e6)), true); return 0; }
      : imp.name === "environ_sizes_get" || imp.name === "args_sizes_get" ? (a, b) => { const m = new DataView(x.memory.buffer); m.setUint32(a, 0, true); m.setUint32(b, 0, true); return 0; }
      : () => 52; // ENOSYS
  }
  x = new WebAssembly.Instance(module, { env, wasi_snapshot_preview1: wasi }).exports;
  if (x._initialize) x._initialize();
  data = x.p8_async_data.value !== undefined ? x.p8_async_data.value : x.p8_async_data;

  const u8 = () => new Uint8Array(x.memory.buffer);
  const api = {
    x,
    /** Boots on `image` (the EEPROM, FW_EEPROM_SIZE bytes); `formatted`: its system area is from an earlier run */
    init(sampleRate, image, formatted, ideal = false) {
      u8().set(image, x.wa_image());
      return x.wa_init(sampleRate, formatted ? 1 : 0, ideal ? 1 : 0) === 1;
    },
    version() { const v = x.wa_version(); return `${v >> 16}.${(v >> 8) & 255}.${v & 255}`; },
    send(bytes) { if (bytes.length > x.wa_in_cap()) return; u8().set(bytes, x.wa_in()); x.wa_send(bytes.length); },
    /** Raw bytes the firmware sent since the last call */
    output() { const n = x.wa_read_output(); return n ? u8().slice(x.wa_out(), x.wa_out() + n) : null; },
    render(frames) {
      x.wa_render(frames);
      return [new Float32Array(x.memory.buffer, x.wa_left(), frames), new Float32Array(x.memory.buffer, x.wa_right(), frames)];
    },
    /** { slots: [n...], records: [Uint8Array...] } the firmware saved since the last call, or null */
    dirty() {
      if (!(x.wa_take_dirty() & 1)) return null;
      const words = new Uint32Array(x.memory.buffer, x.wa_dirty(), 4), eep = x.wa_eeprom(), slots = [], records = [];
      for (let s = 0; s < 128; s++)
        if (words[s >> 5] >>> (s & 31) & 1) { slots.push(s); records.push(u8().slice(eep + s * 768, eep + (s + 1) * 768)); }
      return { slots, records };
    },
    eeprom() { const eep = x.wa_eeprom(); return u8().slice(eep, eep + 128 * 1024); },
    showBank(area, slot) { u8().set(area.subarray(0, 98304), x.wa_image()); return x.wa_show_bank(slot) === 1; },
    capture(name = "") {
      // by hand: an AudioWorkletGlobalScope may have no TextEncoder (Amino's worklet.js found the same)
      const b = Array.from(String(name).slice(0, 16), (c) => { const k = c.charCodeAt(0); return k >= 0x20 && k < 0x7F ? k : 0x20; });
      const m = u8(); m.set(b, x.wa_in()); m[x.wa_in() + b.length] = 0;
      return x.wa_capture() ? u8().slice(x.wa_record(), x.wa_record() + 768) : null;
    },
    loadRecord(record, slot) { u8().set(record, x.wa_record()); return x.wa_load_record(slot) === 1; },
    slot() { return x.wa_current_slot(); },
    halted() { return x.wa_halted() !== 0; },
    fxInfo() {
      const n = x.wa_fx_info();
      let s = "";
      const m = u8(), at = x.wa_out();
      for (let k = 0; k < n; k++) s += String.fromCharCode(m[at + k]);
      return JSON.parse(s);
    },
    fxValues() { const out = []; for (let i = 0; i < x.wa_fx_count(); i++) out.push(x.wa_fx_get(i)); return out; },
  };
  return api;
}

/** SysEx and short messages from a raw MIDI byte stream (running across calls) */
export function midiSplitter() {
  let sysex = null;
  return (bytes, out) => {
    for (const b of bytes) {
      if (b === 0xF0) sysex = [b];
      else if (sysex) { sysex.push(b); if (b === 0xF7) { out.push(sysex); sysex = null; } else if (sysex.length > 65536) sysex = null; }
    }
  };
}
