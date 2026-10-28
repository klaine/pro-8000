#!/usr/bin/env python3
"""Generates the 64 preset files in presets/ from unit-based helpers (semitones, Hz, ms, cents), so every knob value
comes from the firmware's curves (synth.c, lfo.c) rather than a guess. Run: python3 presets/lib/gen_presets.py presets
The sound-design principles are in presets/DESIGN.md."""
import json, math, os, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "presets"

DIVS = ["8bar", "4bar", "2bar", "1bar", "1/2", "1/4.", "1/4", "1/4T", "1/8.", "1/8", "1/8T", "1/16.", "1/16", "1/16T", "1/32"]

def clamp(v): return max(0, min(99, int(round(v))))
def st(s): return clamp((s + 36) * 99 / 72)                 # FREQ A / B: semitone steps, +-36
def ct(c): return clamp(49.5 + c * 0.495)                   # FINE / M TUNE: cents, +-100
def fea(semi):                                              # FIL ENV AMT: +-72 semitones, squared
    x = math.sqrt(abs(semi) / 72) * (1 if semi >= 0 else -1)
    return clamp(49.5 + x * 49.5)
def hz(f): return clamp(((12 * math.log2(f / 440) + 69) - 12) / 120 * 99)   # CUTOFF 16 Hz .. 16.7 kHz
def div(name): return clamp(DIVS.index(name) * 99 / 14)    # LFO FREQ while clock-synced
def lfo_hz(f): return clamp(math.log2(f / 0.05) / 10 * 99)  # LFO FREQ free, 0.05 .. 51 Hz
def att(ms): return 0 if ms <= 0 else clamp(99 * (1 + math.log2(ms / 10000) / 12))   # attack (hold, rest) 0 .. 10 s
def dec(ms): return clamp(99 * math.log2(ms / 3) / 12.7)    # decay / release 3 ms .. 20 s
def glide(ms): return 0 if ms <= 0 else clamp(99 * math.log2(ms / 2) / 10)
def pmodenv(semi): return clamp(99 * math.sqrt(semi / 48))  # PMOD FIL ENV: 0 .. 48 semitones, squared
def lfo_pitch(semi): return clamp(99 * math.sqrt(semi / 12))    # LFO amount for +- semi on pitch (button)
def lfo_filter(semi): return clamp(99 * math.sqrt(semi / 48))   # LFO amount for +- semi on the filter (button)
def pitch_depth(semi): return clamp(99 * math.sqrt(semi / 24))  # route depth for +- semi on a pitch destination

PER_LAYER = ["freq-a", "freq-b", "fine", "cutoff", "fil-env-amt", "fil-att", "fil-dec", "fil-sus", "fil-rel",
             "amp-att", "amp-dec", "amp-sus", "amp-rel", "glide", "master-tune", "lfo-freq", "lfo-amount",
             "pmod-fil-env", "env3-att", "env3-dec", "env3-sus", "env3-rel", "env4-att", "env4-dec", "env4-sus",
             "env4-rel", "lfo2-rate", "lfo2-amount"]
SHARED = ["a-level", "b-level", "noise", "resonance", "a-pw", "b-pw", "pmod-osc-b"]
BUTTONS = ["a-saw", "a-tri", "a-square", "b-saw", "b-tri", "b-square", "lfo-pw", "lfo-freq", "lfo-filter",
           "pmod-freq-a", "pmod-filter", "unison", "sync"]

# PERF's init sound (main.c init_patch), as 0-99
LAYER_INIT = {"freq-a": 50, "freq-b": 50, "fine": 50, "cutoff": 99, "fil-env-amt": 50,
              "fil-att": 0, "fil-dec": 50, "fil-sus": 99, "fil-rel": 25,
              "amp-att": 0, "amp-dec": 50, "amp-sus": 99, "amp-rel": 25,
              "glide": 0, "master-tune": 50, "lfo-freq": 50, "lfo-amount": 0, "pmod-fil-env": 0,
              "env3-att": 0, "env3-dec": 0, "env3-sus": 0, "env3-rel": 0,
              "env4-att": 0, "env4-dec": 0, "env4-sus": 0, "env4-rel": 0,
              "lfo2-rate": 50, "lfo2-amount": 0}
SHARED_INIT = {"a-level": 79, "b-level": 0, "noise": 0, "resonance": 0, "a-pw": 50, "b-pw": 50, "pmod-osc-b": 0}
SETTINGS_INIT = {"layers": "off", "split": 60, "keytrack": "full",
                 "lfo-sync": "free", "lfo-trigger": "free", "lfo2-sync": "free", "lfo2-trigger": "free",
                 "shape 1 1": "tri", "shape 1 2": "tri", "shape 2 1": "tri", "shape 2 2": "tri",
                 "lfo2-dests 1": "none", "lfo2-dests 2": "none", "cycle 1": "none", "cycle 2": "none",
                 "voice 1": "poly last", "voice 2": "poly last", "bend": 2, "detune": 14, "drift": 0,
                 "scale": "off", "root": "C", "lfo-delay 1": 0, "lfo-fade 1": 0, "lfo-delay 2": 0, "lfo-fade 2": 0,
                 "macro 1 1": 0, "macro 1 2": 0, "macro 2 1": 0, "macro 2 2": 0}

# envelope shorthand: a(ms) d(ms) s(level) r(ms)
def adsr(prefix, a, d, s, r):
    return {prefix + "-att": att(a), prefix + "-dec": dec(d), prefix + "-sus": s, prefix + "-rel": dec(r)}

PATCHES = []

def P(slot, name, group, sound, shows, buttons, shared=None, l1=None, l2=None, settings=None,
      routes1=None, routes2=None, seq=None, morph=None):
    assert len(name) <= 16, name
    assert all(b in BUTTONS for b in buttons), buttons
    sh = dict(SHARED_INIT); sh.update(shared or {})
    a = dict(LAYER_INIT); a.update(l1 or {})
    params = {"shared": sh, "1": a}
    if l2 is not None:
        b = dict(LAYER_INIT); b.update(l2)
        params["2"] = b
    for layer in params.values():
        for k, v in layer.items():
            assert k in PER_LAYER or k in SHARED, k
            assert 0 <= v <= 99, (name, k, v)
    s = dict(SETTINGS_INIT); s.update(settings or {})
    routes = {"1": routes1 or []}
    if l2 is not None or routes2:
        routes["2"] = routes2 or []
    for lr in routes.values():
        assert len(lr) <= 16, name
    patch = {"slot": slot, "name": name, "group": group, "notes": {"sound": sound, "shows": shows},
             "buttons": buttons, "params": params, "settings": s, "routes": routes}
    if seq:
        patch["sequence"] = seq
    if morph:
        patch["morph"] = morph
    PATCHES.append(patch)

def steps(notes, gate=50, vel=None, prob=None, ratchets=None, lanes=None):
    """notes: list of semitone offsets, None for a rest. The other lists are per step (None = default)."""
    out = []
    for i, n in enumerate(notes):
        s = {}
        if n is None:
            s["rest"] = True
        else:
            s["note"] = n
        g = gate[i] if isinstance(gate, list) else gate
        if g != 50:
            s["gate"] = g
        if vel and vel[i]:
            s["vel"] = vel[i]
        if prob and prob[i] and prob[i] != 8:
            s["prob"] = prob[i]
        if ratchets and ratchets[i] and ratchets[i] != 1:
            s["ratchets"] = ratchets[i]
        if lanes:
            for lane, values in lanes.items():
                if values[i]:
                    s[lane] = values[i]
        out.append(s)
    return out

def sequence(stps, step="1/16", clock=True, bpm=120, swing=0, once=False, keys="both", arp="off", lanes=0, smooth=""):
    return {"on": True, "length": len(stps), "step": step, "clock": clock, "bpm": bpm, "swing": swing,
            "once": once, "keys": keys, "arp": arp, "lanes": lanes, "smooth": smooth, "steps": stps}

VIB = {"lfo-freq": lfo_hz(5.5), "lfo-amount": 0}  # LFO 1 ready for the wheel (it adds LFO 1 depth)

# ----------------------------------------------------------------------------------------------- techniques
# What makes the bank sound current rather than like a demo cassette (presets/DESIGN.md): movement from several slow,
# unrelated modulators; per-voice differences so chords spread; expression from velocity and pressure into more than
# the volume; two layers where one isn't enough. These helpers add the routes and settings for that.

def spread(cents=8, pw=0, cutoff=0):
    """Per-voice offsets from the voice-number source: every voice sits a little apart from the others, so a chord
    spreads without unison. `cents`: oscillator B's spread across the 8 voices; `pw`, `cutoff`: route depths."""
    r = []
    if cents:
        r.append(["voice", "pitch-b", pitch_depth(cents / 100)])
    if pw:
        r.append(["voice", "b-pw", pw])
    if cutoff:
        r.append(["voice", "cutoff", cutoff])
    return r

def life(pitch=5, cutoff=6):
    """LFO 2 as the drift shape, per voice and slow, nudging B's pitch and the cutoff so nothing holds still."""
    r = []
    if pitch:
        r.append(["lfo2", "pitch-b", pitch_depth(pitch / 100)])
    if cutoff:
        r.append(["lfo2", "cutoff", cutoff])
    return r

LIFE = {"shape 1 2": "drift", "lfo2-trigger": "voice", "lfo2-sync": "free"}   # with "lfo2-rate": lfo_hz(0.1..0.4)

def touch(cutoff=40, vca=45, bark=0, attack=0, decay=0):
    """Velocity into the things a player feels: cutoff, level, the filter envelope's amount ('bark'), and the amp
    attack (negative: harder is faster) or the filter decay (positive: harder rings longer)."""
    r = [["velocity", "cutoff", cutoff], ["velocity", "vca", vca]]
    if bark:
        r.append(["velocity", "filter-env-amount", bark])
    if attack:
        r.append(["velocity", "amp-att", -abs(attack)])
    if decay:
        r.append(["velocity", "fil-dec", decay])
    return r

def press(cutoff=0, vibrato=0, detune=0, res=0):
    """Aftertouch: brighter, more vibrato (LFO 1 depth), wider (B's pitch), more resonance."""
    r = []
    if cutoff:
        r.append(["aftertouch", "cutoff", cutoff])
    if vibrato:
        r.append(["aftertouch", "lfo-amount", vibrato])
    if detune:
        r.append(["aftertouch", "pitch-b", (1 if detune > 0 else -1) * pitch_depth(abs(detune) / 100)])
    if res:
        r.append(["aftertouch", "resonance", res])
    return r

def late_vibrato(hz=5.2, delay_ms=350, fade_ms=500):
    """LFO 1 as a sine vibrato that waits and fades in per note; the wheel adds more (it always adds LFO 1 depth)."""
    d = 0 if delay_ms <= 0 else clamp(99 * math.log2(delay_ms / 20) / 8)
    f = 0 if fade_ms <= 0 else clamp(99 * math.log2(fade_ms / 20) / 8)
    return {"lfo-freq": lfo_hz(hz)}, {"shape 1 1": "sine", "lfo-delay 1": d, "lfo-fade 1": f}

def swell(amount=30, attack_ms=2500, decay_ms=5000, sustain=40):
    """Env 3 as a slow second filter envelope: the sound opens after the attack, then settles."""
    return {**adsr("env3", attack_ms, decay_ms, sustain, 2000)}, [["env3", "cutoff", amount]]

# ----------------------------------------------------------------------------------------------- basses
P(1, "Deep Sub", "bass",
  "A triangle under a hollow square an octave below, legato glide; round, with a short filter kick that velocity "
  "controls, and a slow per-voice drift so held notes breathe.",
  "Legato, half keytracking, velocity into cutoff and the kick, a little drift.",
  ["a-tri", "b-square"], {"a-level": 85, "b-level": 50, "resonance": 6},
  {"freq-a": st(-12), "freq-b": st(-24), "cutoff": hz(180), "fil-env-amt": fea(18), **adsr("fil", 0, 220, 15, 200),
   **adsr("amp", 0, 400, 80, 150), "glide": glide(45), "lfo2-rate": lfo_hz(0.12)},
  settings={"voice 1": "legato last", "keytrack": "half", "drift": 8, **LIFE},
  routes1=touch(30, 45, bark=25) + life(2, 4))

P(2, "Acid Line", "bass",
  "Saw and a narrow pulse through a resonant filter with a big, fast envelope; mono so every note retriggers, a short "
  "glide for slides. Accents open it and ring longer; pressure screams; the wheel is a resonance pedal.",
  "Velocity into the envelope amount, cutoff and decay; aftertouch to cutoff; wheel to resonance; mono retrigger.",
  ["a-saw", "b-square"], {"a-level": 80, "b-level": 55, "b-pw": 30, "resonance": 70},
  {"cutoff": hz(140), "fil-env-amt": fea(38), **adsr("fil", 0, 170, 0, 120), **adsr("amp", 0, 300, 70, 80),
   "glide": glide(28)},
  settings={"voice 1": "mono last", "keytrack": "half"},
  routes1=[["velocity", "filter-env-amount", 45], ["velocity", "cutoff", 20], ["velocity", "fil-dec", 30],
           ["velocity", "vca", 45], ["aftertouch", "cutoff", 50], ["mod-wheel", "resonance", 30]])

P(3, "Reese Growl", "bass",
  "Two saws 11 cents apart, each voice a touch different, beating against a dark filter that the drift LFO keeps "
  "nudging. Pressure growls, velocity bites.",
  "Per-voice spread and drift so the beat never settles; aftertouch to cutoff and resonance.",
  ["a-saw", "b-saw"], {"a-level": 75, "b-level": 78, "resonance": 14},
  {"fine": ct(11), "cutoff": hz(240), "fil-env-amt": fea(10), **adsr("fil", 30, 700, 35, 300),
   **adsr("amp", 0, 500, 90, 220), "lfo2-rate": lfo_hz(0.22)},
  settings={"drift": 30, "keytrack": "half", **LIFE},
  routes1=spread(cents=6) + life(8, 8) + touch(30, 40) + press(cutoff=45, res=25))

P(4, "Sync Punch", "bass",
  "Oscillator A synced to B an octave below; Env 3 drops A's pitch from 9 semitones up over 250 ms: the sync punch, "
  "mono, with the punch longer and the filter brighter on hard notes.",
  "Sync swept by Env 3 into Pitch A; velocity into Env 3's decay, cutoff and level.",
  ["a-saw", "b-saw", "sync"], {"a-level": 90, "b-level": 30, "resonance": 15},
  {"freq-b": st(-12), "cutoff": hz(450), "fil-env-amt": fea(12), **adsr("fil", 0, 300, 20, 150),
   **adsr("amp", 0, 600, 75, 150), **adsr("env3", 0, 250, 0, 3)},
  settings={"voice 1": "mono last"},
  routes1=[["env3", "pitch-a", pitch_depth(9)], ["velocity", "env3-dec", 25]] + touch(30, 45))

P(5, "Fat Stab", "bass",
  "Two saws an octave apart, every voice a few cents its own, a medium filter envelope with a little resonance: the "
  "workhorse for stabs and chords, alive instead of flat.",
  "Per-voice spread, drift, velocity into cutoff, the envelope amount and the VCA.",
  ["a-saw", "b-saw"], {"a-level": 80, "b-level": 75, "resonance": 16},
  {"freq-b": st(-12), "fine": ct(5), "cutoff": hz(230), "fil-env-amt": fea(26), **adsr("fil", 0, 380, 15, 160),
   **adsr("amp", 0, 500, 60, 250), "lfo2-rate": lfo_hz(0.15)},
  settings={"drift": 10, **LIFE},
  routes1=spread(cents=7) + life(3, 4) + touch(35, 55, bark=20))

P(6, "Clock Wobble", "bass",
  "Saw and square an octave apart, LFO 1 (triangle) swinging the filter 30 semitones in quarter notes from the clock; "
  "the wheel speeds the wobble to 16ths, pressure adds resonance, velocity opens.",
  "Clock-synced LFO 1 (1/4) retriggered on the first key, the wheel into the LFO rate, aftertouch to resonance.",
  ["a-saw", "b-square", "lfo-filter"], {"a-level": 75, "b-level": 70, "b-pw": 42, "resonance": 42},
  {"freq-b": st(-12), "cutoff": hz(110), "fil-env-amt": fea(6), **adsr("fil", 0, 500, 60, 200),
   **adsr("amp", 0, 500, 90, 200), "lfo-freq": div("1/4"), "lfo-amount": lfo_filter(30)},
  settings={"voice 1": "mono last", "lfo-sync": "clock", "lfo-trigger": "first"},
  routes1=[["mod-wheel", "lfo-rate", 50]] + press(res=30) + touch(25, 40))

P(7, "PWM Bass", "bass",
  "A pulse under slow width modulation over a saw an octave down, legato; LFO 2 drifts the width per voice at its own "
  "pace, and pressure widens it. Thick without being static.",
  "LFO 1 → PW with LFO 2 (drift, per voice) also into PW A; legato glide; aftertouch to pulse width.",
  ["a-square", "b-saw", "lfo-pw"], {"a-level": 80, "b-level": 70, "a-pw": 32, "resonance": 8},
  {"freq-b": st(-12), "cutoff": hz(260), "fil-env-amt": fea(14), **adsr("fil", 0, 400, 30, 200),
   **adsr("amp", 0, 500, 85, 180), "glide": glide(35), "lfo-freq": lfo_hz(0.35), "lfo-amount": 32,
   "lfo2-rate": lfo_hz(0.2)},
  settings={"voice 1": "legato last", "keytrack": "half", **LIFE},
  routes1=[["lfo2", "a-pw", 25], ["aftertouch", "a-pw", 30]] + touch(30, 45))

P(8, "PolyMod Bass", "bass",
  "Saw with a triangle an octave down. Poly-mod: the filter envelope kicks oscillator A's pitch at the start of each "
  "note and Env 4 lets B frequency-modulate A for a 150 ms growl; harder notes growl harder.",
  "Poly-mod with Env 4 into the osc B amount; velocity into Env 4's decay.",
  ["a-saw", "b-tri", "pmod-freq-a"], {"a-level": 85, "b-level": 60, "resonance": 12},
  {"freq-b": st(-12), "cutoff": hz(300), "fil-env-amt": fea(20), **adsr("fil", 0, 300, 25, 150),
   **adsr("amp", 0, 500, 75, 150), "pmod-fil-env": pmodenv(3), **adsr("env4", 0, 150, 0, 3)},
  routes1=[["env4", "pmod-osc-b", 55], ["velocity", "pmod-osc-b", 25], ["velocity", "env4-dec", 20]] + touch(30, 45))

# ----------------------------------------------------------------------------------------------- leads
VIB_MS = late_vibrato  # (lfo-freq), (settings)

P(9, "Sync Lead", "lead",
  "A synced to B, the filter envelope sweeping A 20 semitones through poly-mod, legato glide. Vibrato waits 300 ms and "
  "fades in; pressure adds more and opens the filter; harder notes sweep further.",
  "Sync and poly-mod envelope, LFO 1 delay and fade, aftertouch to vibrato and cutoff, velocity to the sweep.",
  ["a-saw", "b-saw", "sync", "lfo-freq", "pmod-freq-a"], {"a-level": 88, "b-level": 35, "resonance": 12},
  {"freq-b": st(-12), "cutoff": hz(900), "fil-env-amt": fea(14), **adsr("fil", 0, 350, 40, 200),
   **adsr("amp", 0, 300, 90, 200), "glide": glide(60), "pmod-fil-env": pmodenv(20), **VIB_MS(5.4, 300, 450)[0]},
  settings={"voice 1": "legato last", **VIB_MS(5.4, 300, 450)[1]},
  routes1=[["velocity", "pmod-env", 30], ["bend", "cutoff", 20]] + press(cutoff=35, vibrato=35) + touch(25, 40))

P(10, "Wide Lead", "lead",
  "Both layers on every note: layer 1 two saws 6 cents flat, layer 2 the same 6 cents sharp with a slower attack, so "
  "every note is four oscillators drifting around each other. Vibrato fades in late; pressure spreads them further.",
  "Stack mode as a chorus; per-voice spread; LFO fade; aftertouch into B's pitch on both layers.",
  ["a-saw", "b-saw", "lfo-freq"], {"a-level": 72, "b-level": 72, "resonance": 10},
  {"fine": ct(-6), "cutoff": hz(1200), "fil-env-amt": fea(8), **adsr("fil", 0, 500, 55, 250), **adsr("amp", 0, 400, 85, 300),
   "glide": glide(30), **VIB_MS(5.0, 400, 600)[0]},
  l2={"fine": ct(6), "cutoff": hz(1000), "fil-env-amt": fea(8), **adsr("fil", 30, 600, 50, 250),
      **adsr("amp", 25, 400, 85, 320), "glide": glide(30), **VIB_MS(5.0, 400, 600)[0]},
  settings={"layers": "stack", "voice 1": "legato last", "voice 2": "legato last", "drift": 12, **VIB_MS(5.0, 400, 600)[1]},
  routes1=touch(25, 40) + press(detune=10, vibrato=30), routes2=touch(25, 40) + press(detune=-10, vibrato=30))

P(11, "Press to Scream", "lead",
  "Saw with a pulse an octave up, resonance high, mono. Pressure opens the filter, raises the resonance and adds "
  "vibrato all at once through Macro 1; the wheel does the same gently. Velocity sets the bite.",
  "Macro 1 fed by aftertouch and the wheel, fanning out to cutoff, resonance and LFO depth.",
  ["a-saw", "b-square", "lfo-freq"], {"a-level": 80, "b-level": 45, "b-pw": 40, "resonance": 62},
  {"freq-b": st(12), "cutoff": hz(500), "fil-env-amt": fea(16), **adsr("fil", 0, 300, 35, 200),
   **adsr("amp", 0, 300, 90, 180), "glide": glide(20), **VIB_MS(5.8, 0, 0)[0]},
  settings={"voice 1": "mono last", "shape 1 1": "sine"},
  routes1=[["aftertouch", "macro1", 99], ["mod-wheel", "macro1", 50], ["macro1", "cutoff", 50], ["macro1", "resonance", 30],
           ["macro1", "lfo-amount", 35]] + touch(30, 40, bark=25))

P(12, "Hollow Square", "lead",
  "Two pulses at 35 % and 62 % width, 10 cents apart, LFO 2 slowly moving both widths per voice; press for a "
  "clock-synced octave trill from LFO 1's square. Soft filter with keytracking so the top stays round.",
  "LFO 2 (sine, per voice) into both pulse widths; LFO 1 square synced to 1/8 into pitch, depth from aftertouch.",
  ["a-square", "b-square", "lfo-freq"], {"a-level": 75, "b-level": 70, "a-pw": 33, "b-pw": 62, "resonance": 15},
  {"fine": ct(10), "cutoff": hz(700), "fil-env-amt": fea(10), **adsr("fil", 0, 400, 50, 250),
   **adsr("amp", 0, 300, 85, 250), "glide": glide(15), "lfo-freq": div("1/8"), "lfo-amount": 0, "lfo2-rate": lfo_hz(0.3),
   "lfo2-amount": 0},
  settings={"voice 1": "legato last", "shape 1 1": "square", "lfo-sync": "clock", "lfo-trigger": "first",
            "shape 1 2": "sine", "lfo2-trigger": "voice"},
  routes1=[["lfo2", "a-pw", 20], ["lfo2", "b-pw", -20], ["aftertouch", "lfo-amount", lfo_pitch(12)]] + touch(25, 40))

P(13, "Scoop Whistle", "lead",
  "Triangles an octave apart, a resonant, open filter: a whistle. Env 4 scoops each note up a semitone in 90 ms, "
  "faster on hard notes; vibrato fades in; the filter follows the key fully so high notes sing.",
  "Env 4 into pitch, velocity into Env 4's decay, LFO delay and fade, full keytracking.",
  ["a-tri", "b-tri", "lfo-freq"], {"a-level": 85, "b-level": 40, "resonance": 45},
  {"freq-b": st(12), "cutoff": hz(2200), "fil-env-amt": fea(4), **adsr("fil", 0, 300, 70, 300),
   **adsr("amp", 5, 300, 90, 350), "glide": glide(25), **adsr("env4", 0, 90, 0, 3), **VIB_MS(5.6, 250, 400)[0]},
  settings={"voice 1": "legato last", **VIB_MS(5.6, 250, 400)[1]},
  routes1=[["env4", "pitch", -pitch_depth(1)], ["velocity", "env4-dec", -25]] + press(vibrato=30, cutoff=20) + touch(20, 40))

P(14, "Velo Glide", "lead",
  "Saws an octave apart, legato with a long glide that shortens with velocity: slow slides into soft notes, snappy "
  "ones into hard. Per-voice drift keeps the two oscillators moving; pressure brightens.",
  "Velocity into glide time (negative: harder is faster), drift, aftertouch to cutoff.",
  ["a-saw", "b-saw", "lfo-freq"], {"a-level": 82, "b-level": 60, "resonance": 14},
  {"freq-b": st(-12), "fine": ct(4), "cutoff": hz(800), "fil-env-amt": fea(12), **adsr("fil", 0, 400, 50, 250),
   **adsr("amp", 0, 300, 90, 250), "glide": glide(140), **VIB_MS(5.2, 350, 500)[0]},
  settings={"voice 1": "legato last", "drift": 14, **VIB_MS(5.2, 350, 500)[1]},
  routes1=[["velocity", "glide", -45]] + press(cutoff=35, vibrato=25) + touch(30, 45))

P(15, "Brass Solo", "lead",
  "Two saws 7 cents apart, the filter opening over 60 ms into a 30-semitone envelope; velocity makes the attack snap "
  "and the sweep bigger; vibrato arrives late; CC 2 (breath) rides the cutoff for a wind player's swell.",
  "Velocity into the filter attack time, the envelope amount and level; CC B (breath) to cutoff; LFO delay and fade.",
  ["a-saw", "b-saw", "lfo-freq"], {"a-level": 80, "b-level": 78, "resonance": 12},
  {"fine": ct(7), "cutoff": hz(300), "fil-env-amt": fea(30), **adsr("fil", 60, 500, 45, 250),
   **adsr("amp", 20, 400, 90, 250), "glide": glide(15), **VIB_MS(5.0, 450, 700)[0]},
  settings={"voice 1": "legato last", "drift": 8, **VIB_MS(5.0, 450, 700)[1]},
  routes1=[["velocity", "fil-att", -35], ["cc-b", "cutoff", 50]] + touch(25, 40, bark=30) + press(vibrato=30))

P(16, "Human Lead", "lead",
  "Saw and pulse, legato with glide; every note lands with a slightly different cutoff, pitch and width, as if played "
  "by hand, and the drift LFO keeps it moving while it's held. Pressure for vibrato.",
  "Random into cutoff, pitch and PW per note; the drift LFO per voice; aftertouch to vibrato.",
  ["a-saw", "b-square", "lfo-freq"], {"a-level": 78, "b-level": 60, "b-pw": 40, "resonance": 18},
  {"fine": ct(3), "cutoff": hz(650), "fil-env-amt": fea(12), **adsr("fil", 10, 450, 45, 250),
   **adsr("amp", 5, 300, 90, 250), "glide": glide(50), "lfo2-rate": lfo_hz(0.25), **VIB_MS(5.3, 0, 300)[0]},
  settings={"voice 1": "legato last", "drift": 20, **LIFE, **VIB_MS(5.3, 0, 300)[1]},
  routes1=[["random", "cutoff", 12], ["random", "pitch", pitch_depth(0.08)], ["random", "b-pw", 15]] + life(5, 5)
          + press(vibrato=35, cutoff=25) + touch(25, 40))

# ----------------------------------------------------------------------------------------------- polys
P(17, "Vintage Poly", "poly",
  "Saw and a width-modulated pulse, every voice a few cents and a little width its own, a soft filter that velocity "
  "opens. LFO 2 drifts each voice slowly; the drift setting adds the rest. Chords spread, nothing holds still.",
  "Per-voice spread of pitch and PW, the drift LFO per voice, drift, velocity to cutoff and level.",
  ["a-saw", "b-square", "lfo-pw"], {"a-level": 72, "b-level": 68, "b-pw": 42, "resonance": 12},
  {"fine": ct(5), "cutoff": hz(650), "fil-env-amt": fea(12), **adsr("fil", 0, 600, 50, 400),
   **adsr("amp", 5, 500, 85, 450), "lfo-freq": lfo_hz(0.22), "lfo-amount": 28, "lfo2-rate": lfo_hz(0.14)},
  settings={"drift": 22, **LIFE},
  routes1=spread(cents=9, pw=20) + life(5, 6) + touch(35, 45, attack=20))

P(18, "Poly Brass", "poly",
  "Two saws 8 cents apart on layer 1, a third saw an octave up on layer 2 at a lower level, both with a 40 ms filter "
  "attack into a 28-semitone sweep that velocity scales. Stacked, so each note is three saws; pressure swells.",
  "Stack as a layered brass; velocity into the envelope amount and attack; aftertouch to cutoff on both layers.",
  ["a-saw", "b-saw"], {"a-level": 76, "b-level": 72, "resonance": 10},
  {"fine": ct(8), "cutoff": hz(280), "fil-env-amt": fea(28), **adsr("fil", 40, 500, 40, 250), **adsr("amp", 15, 400, 90, 280),
   "lfo2-rate": lfo_hz(0.18)},
  l2={"freq-a": st(12), "freq-b": st(12), "fine": ct(-8), "cutoff": hz(220), "fil-env-amt": fea(24),
      **adsr("fil", 60, 550, 35, 250), **adsr("amp", 30, 400, 80, 280), "lfo2-rate": lfo_hz(0.11)},
  settings={"layers": "stack", "drift": 10, **LIFE, "shape 2 2": "drift", "lfo2-trigger": "voice"},
  routes1=life(3, 4) + touch(30, 45, bark=30, attack=25) + press(cutoff=35),
  routes2=life(3, 4) + touch(25, 35, bark=25, attack=25) + press(cutoff=30))

P(19, "Combo Organ", "poly",
  "Two squares an octave apart, filter wide open, instant envelopes; the filter envelope gives a 40 ms click at each "
  "key, LFO 2 a tremolo, and a slow vibrato fades in after a while. Pressure deepens the tremolo.",
  "The filter envelope as a click transient, LFO 2 into the VCA (tremolo), LFO 1 delay and fade.",
  ["a-square", "b-square", "lfo-freq"], {"a-level": 70, "b-level": 55, "resonance": 0},
  {"freq-b": st(12), "cutoff": hz(5000), "fil-env-amt": fea(21), **adsr("fil", 0, 40, 0, 40), **adsr("amp", 2, 300, 99, 60),
   "lfo2-rate": lfo_hz(6.2), **VIB_MS(5.5, 600, 900)[0]},
  settings={"shape 1 2": "sine", **VIB_MS(5.5, 600, 900)[1]},
  routes1=[["lfo2", "vca", 30], ["aftertouch", "lfo2-amount", 40]])

P(20, "Poly Sync", "poly",
  "Synced saws with a 10-semitone poly-mod sweep from the filter envelope: hard-edged chords, deeper sweeps on harder "
  "notes, and every voice's sync pitch a few cents apart so the edge shimmers. Pressure pushes the sweep further.",
  "Sync and poly-mod with velocity into the poly-mod amount; per-voice spread; aftertouch into poly-mod.",
  ["a-saw", "b-saw", "sync", "pmod-freq-a"], {"a-level": 85, "b-level": 35, "resonance": 10},
  {"cutoff": hz(500), "fil-env-amt": fea(18), **adsr("fil", 0, 500, 30, 250), **adsr("amp", 0, 600, 80, 300),
   "pmod-fil-env": pmodenv(10), "lfo2-rate": lfo_hz(0.2)},
  settings={"drift": 8, **LIFE},
  routes1=spread(cents=6) + [["velocity", "pmod-env", 40], ["aftertouch", "pmod-env", 35]] + life(3, 3) + touch(25, 45))

P(21, "E Piano", "poly",
  "Soft triangles, B an octave up and quiet for the tine; a fast filter bark (+24 semitones, 300 ms decay to a closed "
  "sustain) over a long amp decay. Harder notes bark brighter and louder; a slow sine tremolo under it, deeper with "
  "pressure.",
  "Velocity into cutoff, filter envelope amount and the VCA at once; LFO 2 as tremolo with aftertouch on its depth; "
  "full keytracking; a little drift.",
  ["a-tri", "b-tri"], {"a-level": 80, "b-level": 22, "resonance": 14},
  {"freq-b": st(12), "cutoff": 52, "fil-env-amt": 79, "fil-att": 0, "fil-dec": 46, "fil-sus": 8, "fil-rel": 42,
   "amp-att": 0, "amp-dec": 62, "amp-sus": 32, "amp-rel": 44, "lfo2-rate": 60},
  settings={"shape 1 2": "sine", "drift": 6},
  routes1=[["velocity", "cutoff", 45], ["velocity", "filter-env-amount", 30], ["velocity", "vca", 60], ["lfo2", "vca", 12],
           ["aftertouch", "lfo2-amount", 30]])

P(22, "Poly Press", "poly",
  "Two saws 10 cents apart, filter fairly closed, a slow swell from Env 3 after the attack; pressing a key opens the "
  "filter, adds resonance and widens the detune (Macro 1), so a held chord blooms under the hand.",
  "Env 3 as a slow swell; aftertouch into Macro 1 fanning to cutoff, resonance and B's pitch.",
  ["a-saw", "b-saw"], {"a-level": 75, "b-level": 72, "resonance": 15},
  {"fine": ct(10), "cutoff": hz(350), "fil-env-amt": fea(8), **adsr("fil", 20, 500, 60, 400), **adsr("amp", 10, 500, 85, 450),
   **swell(25, 1800, 4000, 30)[0], "lfo2-rate": lfo_hz(0.16)},
  settings={"drift": 12, **LIFE},
  routes1=swell(25, 1800, 4000, 30)[1] + [["aftertouch", "macro1", 99], ["macro1", "cutoff", 50], ["macro1", "resonance", 25],
           ["macro1", "pitch-b", pitch_depth(0.12)]] + life(4, 4) + touch(25, 40))

P(23, "Old Poly", "poly",
  "Two saws with heavy drift and a slow per-voice drift LFO on pitch and cutoff; every voice a different width and "
  "pitch offset; a soft filter. The worn-out polysynth: nothing in tune, everything pleasant.",
  "Drift at 55, per-voice spread, the drift LFO, random cutoff per note.",
  ["a-saw", "b-square", "lfo-pw"], {"a-level": 74, "b-level": 66, "b-pw": 45, "resonance": 8},
  {"fine": ct(-7), "cutoff": hz(550), "fil-env-amt": fea(10), **adsr("fil", 15, 600, 55, 450), **adsr("amp", 10, 500, 85, 500),
   "lfo-freq": lfo_hz(0.13), "lfo-amount": 22, "lfo2-rate": lfo_hz(0.09)},
  settings={"drift": 55, **LIFE},
  routes1=spread(cents=14, pw=25, cutoff=10) + life(9, 10) + [["random", "cutoff", 12]] + touch(30, 40))

P(24, "Cycling Wah", "poly",
  "Saw and pulse through a resonant filter; Env 3 cycles in quarter notes from the clock, opening and closing the "
  "filter in a rhythmic wah while a key is held; velocity sets how far it opens, pressure the resonance.",
  "A cycling envelope synced to the clock into the cutoff; velocity into its depth via Macro 1.",
  ["a-saw", "b-square"], {"a-level": 78, "b-level": 60, "b-pw": 38, "resonance": 55},
  {"cutoff": hz(200), "fil-env-amt": fea(8), **adsr("fil", 0, 400, 40, 300), **adsr("amp", 0, 400, 90, 350),
   "env3-att": 30, "env3-dec": 50, "env3-sus": 40, "env3-rel": 40},
  settings={"cycle 1": "env3", "cycle-sync 1 env3": "1/4", "keytrack": "half"},
  routes1=[["env3", "cutoff", 40], ["velocity", "macro1", 99], ["macro1", "cutoff", 20]] + press(res=25) + touch(10, 40))

# ----------------------------------------------------------------------------------------------- pads
P(25, "Warm Pad", "pad",
  "Two saws 9 cents apart, each voice its own few cents, a slow attack; the filter breathes with LFO 2 (sine, 7 s) "
  "while a slower drift moves B's pitch per voice. Velocity shapes the attack: hard notes bloom faster.",
  "Two slow modulators at unrelated rates, per-voice spread, drift, velocity into the amp attack.",
  ["a-saw", "b-saw"], {"a-level": 74, "b-level": 72, "resonance": 12},
  {"fine": ct(9), "cutoff": hz(420), "fil-env-amt": fea(10), **adsr("fil", 900, 2500, 60, 1500), **adsr("amp", 700, 2000, 80, 1800),
   "lfo2-rate": lfo_hz(0.14), "lfo2-amount": 20},
  settings={"drift": 28, "shape 1 2": "sine", "lfo2-dests 1": "filter", "lfo2-trigger": "free"},
  routes1=spread(cents=8) + [["lfo", "pitch-b", pitch_depth(0.05)]] + touch(25, 35, attack=35))

P(26, "Analog Strings", "pad",
  "Both layers: layer 1 a pulse under slow PWM with a saw 11 cents away, layer 2 an octave up and quieter with its own "
  "PWM rate, 250 ms attack. Vibrato fades in over a second; pressure deepens it. An ensemble with no chorus.",
  "Stack with two PWM rates, LFO 1 delay and fade on both layers, aftertouch to vibrato.",
  ["a-square", "b-saw", "lfo-pw", "lfo-freq"], {"a-level": 70, "b-level": 66, "a-pw": 36, "resonance": 10},
  {"fine": ct(11), "cutoff": hz(900), "fil-env-amt": fea(6), **adsr("fil", 300, 1000, 70, 900),
   **adsr("amp", 250, 1500, 85, 1100), "lfo-freq": lfo_hz(0.3), "lfo-amount": 30, "lfo2-rate": lfo_hz(0.11)},
  l2={"freq-a": st(12), "freq-b": st(12), "fine": ct(-11), "cutoff": hz(1400), "fil-env-amt": fea(6),
      **adsr("fil", 400, 1000, 65, 900), **adsr("amp", 400, 1500, 70, 1200), "lfo-freq": lfo_hz(0.23), "lfo-amount": 26,
      "lfo2-rate": lfo_hz(0.17)},
  settings={"layers": "stack", "drift": 14, "lfo-delay 1": 40, "lfo-fade 1": 62, **LIFE, "shape 2 2": "drift", "lfo2-trigger": "voice"},
  routes1=life(4, 5) + press(vibrato=30) + touch(20, 35, attack=20),
  routes2=life(4, 5) + press(vibrato=30) + touch(20, 30, attack=20))

P(27, "Slow Evolve", "pad",
  "Saw with a narrow pulse an octave down; LFO 1 (sine, 4 bars of clock) sweeps the filter while LFO 2 (drift, per "
  "voice) walks the pulse width and B's pitch, and Env 3 swells the cutoff after each attack. Never the same twice.",
  "Three modulators on three timescales: a clock-synced LFO, a per-voice drift LFO, a slow Env 3.",
  ["a-saw", "b-square", "lfo-filter"], {"a-level": 78, "b-level": 60, "b-pw": 25, "resonance": 22},
  {"freq-b": st(-12), "cutoff": hz(300), "fil-env-amt": fea(6), **adsr("fil", 600, 2000, 60, 1500), **adsr("amp", 900, 2000, 85, 2000),
   "lfo-freq": div("4bar"), "lfo-amount": lfo_filter(18), "lfo2-rate": lfo_hz(0.08), **swell(22, 3000, 6000, 35)[0]},
  settings={"lfo-sync": "clock", "lfo-trigger": "free", "shape 1 1": "sine", "drift": 18, **LIFE},
  routes1=swell(22, 3000, 6000, 35)[1] + [["lfo2", "b-pw", 30]] + life(7, 0) + touch(20, 35, attack=30))

P(28, "Breathing Pad", "pad",
  "Two saws, the filter nearly closed; Env 3 cycles on its own (2.5 s up, 2 s hold, 3 s down, 1 s rest) opening the "
  "filter in slow waves while a key is held, and LFO 2 drifts each voice's pitch. Pressure lifts the floor.",
  "A free-cycling envelope into the cutoff, the drift LFO per voice, aftertouch to cutoff.",
  ["a-saw", "b-saw"], {"a-level": 74, "b-level": 72, "resonance": 20},
  {"fine": ct(6), "cutoff": hz(180), "fil-env-amt": fea(4), **adsr("fil", 500, 2000, 70, 1500), **adsr("amp", 800, 2000, 85, 2200),
   "env3-att": att(2500), "env3-dec": dec(3000), "env3-sus": att(2000), "env3-rel": att(1000), "lfo2-rate": lfo_hz(0.1)},
  settings={"cycle 1": "env3", "drift": 15, **LIFE},
  routes1=[["env3", "cutoff", 45]] + life(6, 0) + press(cutoff=35) + touch(10, 35, attack=25))

P(29, "Glass Pad", "pad",
  "Triangles a fifth apart with a touch of poly-mod FM that Env 4 fades out over two seconds, a resonant open filter "
  "with full keytracking; each voice a few cents apart. Bell-like on the attack, glassy after.",
  "Env 4 into the poly-mod osc B amount (FM that decays), per-voice spread, the drift LFO.",
  ["a-tri", "b-tri", "pmod-freq-a"], {"a-level": 80, "b-level": 40, "pmod-osc-b": 12, "resonance": 40},
  {"freq-b": st(7), "cutoff": hz(1800), "fil-env-amt": fea(4), **adsr("fil", 400, 1500, 70, 1500), **adsr("amp", 400, 1500, 85, 2000),
   **adsr("env4", 0, 2000, 0, 500), "lfo2-rate": lfo_hz(0.12)},
  settings={"drift": 10, **LIFE},
  routes1=spread(cents=5) + [["env4", "pmod-osc-b", 25]] + life(3, 4) + touch(20, 40, attack=20))

P(30, "Dark Choir", "pad",
  "A narrow pulse (18 %) slowly width-modulated over a saw an octave down, a dark filter with half keytracking; LFO 2 "
  "(sine) moves the width while LFO 1 vibrato fades in late. Pressure opens it like a swell pedal.",
  "LFO 2 → PW on its own rate, LFO 1 delay and fade, aftertouch to cutoff.",
  ["a-square", "b-saw", "lfo-freq"], {"a-level": 76, "b-level": 62, "a-pw": 18, "resonance": 18},
  {"freq-b": st(-12), "cutoff": hz(380), "fil-env-amt": fea(6), **adsr("fil", 600, 2000, 65, 1500), **adsr("amp", 600, 2000, 85, 1800),
   "lfo-freq": lfo_hz(4.6), "lfo2-rate": lfo_hz(0.19), "lfo2-amount": 35},
  settings={"keytrack": "half", "shape 1 1": "sine", "lfo-delay 1": 55, "lfo-fade 1": 70, "shape 1 2": "sine",
            "lfo2-dests 1": "pw", "drift": 12},
  routes1=press(cutoff=45, vibrato=20) + touch(15, 35, attack=25))

P(31, "Touch Swell", "pad",
  "Two saws with a 1.5 s swell that shrinks to about 200 ms when you hit hard, so the same patch is a pad or a stab "
  "by touch; the filter follows with its own slower swell from Env 3. Per-voice spread and drift underneath.",
  "Velocity into the amp attack time (16× range), Env 3 as a second filter envelope, spread, drift.",
  ["a-saw", "b-saw"], {"a-level": 76, "b-level": 72, "resonance": 14},
  {"fine": ct(7), "cutoff": hz(500), "fil-env-amt": fea(8), **adsr("fil", 800, 2000, 60, 1200), **adsr("amp", 1500, 2000, 85, 1200),
   **swell(20, 2000, 4000, 40)[0], "lfo2-rate": lfo_hz(0.15)},
  settings={"drift": 16, **LIFE},
  routes1=swell(20, 2000, 4000, 40)[1] + spread(cents=7) + life(4, 4) + [["velocity", "amp-att", -60]] + touch(30, 40))

P(32, "Wide Stack", "pad",
  "Both layers on every key: layer 1 two saws 8 cents flat with a slow sine on the filter, layer 2 two saws 8 cents "
  "sharp an octave up at a lower level with a drift LFO per voice; the two layers breathe at different speeds. "
  "The biggest pad in the bank; pressure swells both.",
  "Stack with unrelated modulation on each layer; aftertouch on both; velocity into both attacks.",
  ["a-saw", "b-saw"], {"a-level": 70, "b-level": 70, "resonance": 12},
  {"fine": ct(-8), "cutoff": hz(450), "fil-env-amt": fea(8), **adsr("fil", 800, 2000, 60, 1500), **adsr("amp", 900, 2000, 85, 2000),
   "lfo2-rate": lfo_hz(0.12), "lfo2-amount": 18},
  l2={"freq-a": st(12), "freq-b": st(12), "fine": ct(8), "cutoff": hz(700), "fil-env-amt": fea(6),
      **adsr("fil", 1200, 2000, 55, 1500), **adsr("amp", 1400, 2000, 65, 2200), "lfo2-rate": lfo_hz(0.09)},
  settings={"layers": "stack", "drift": 20, "shape 1 2": "sine", "lfo2-dests 1": "filter", "lfo2-trigger": "free",
            "shape 2 2": "drift", "lfo2-trigger": "voice"},
  routes1=spread(cents=6) + press(cutoff=35) + touch(20, 35, attack=25),
  routes2=[["lfo2", "pitch-b", pitch_depth(0.06)], ["lfo2", "cutoff", 8]] + press(cutoff=30) + touch(20, 30, attack=25))

# ----------------------------------------------------------------------------------------------- plucks
P(33, "Clavi", "pluck",
  "A 20 % pulse with a wide pulse an octave up, a 30-semitone filter snap that velocity scales and lengthens; a 2 ms "
  "pitch click from Env 3 for the pick. Full keytracking keeps the top bright and thin.",
  "Velocity into the envelope amount and the filter decay; Env 3 as a pitch click; keytracking.",
  ["a-square", "b-square"], {"a-level": 82, "b-level": 40, "a-pw": 20, "b-pw": 55, "resonance": 25},
  {"freq-b": st(12), "cutoff": hz(500), "fil-env-amt": fea(30), **adsr("fil", 0, 160, 10, 120), **adsr("amp", 0, 350, 50, 150),
   **adsr("env3", 0, 4, 0, 3)},
  routes1=[["env3", "pitch", pitch_depth(3)]] + touch(30, 50, bark=30, decay=30))

P(34, "Kalimba", "pluck",
  "Triangles two octaves apart with a little noise for the thumb, a 120 ms filter pluck and a 15 ms click from Env 3; "
  "every note a slightly different brightness (random) and the tine detuned per voice.",
  "Random into cutoff per note, per-voice spread, Env 3 click, noise as the transient.",
  ["a-tri", "b-tri"], {"a-level": 85, "b-level": 30, "noise": 8, "resonance": 30},
  {"freq-b": st(24), "cutoff": hz(900), "fil-env-amt": fea(22), **adsr("fil", 0, 120, 0, 150), **adsr("amp", 0, 900, 0, 400),
   **adsr("env3", 0, 15, 0, 3)},
  routes1=[["env3", "cutoff", 25], ["random", "cutoff", 10]] + spread(cents=10) + touch(30, 50, decay=20))

P(35, "Harp", "pluck",
  "Saws an octave apart, a 24-semitone filter pluck and a long amp decay that velocity lengthens; each voice a few "
  "cents apart so arpeggios shimmer; a slow drift LFO keeps sustained notes alive.",
  "Velocity into the filter decay and cutoff, per-voice spread, the drift LFO.",
  ["a-saw", "b-saw"], {"a-level": 80, "b-level": 45, "resonance": 15},
  {"freq-b": st(12), "cutoff": hz(400), "fil-env-amt": fea(24), **adsr("fil", 0, 300, 5, 400), **adsr("amp", 0, 1800, 0, 600),
   "lfo2-rate": lfo_hz(0.2)},
  settings={"drift": 8, **LIFE},
  routes1=spread(cents=6) + life(2, 3) + touch(35, 50, decay=35))

P(36, "Pizzicato", "pluck",
  "Saw and a 30 % pulse, an 80 ms filter snap and a 180 ms body: tight and dry; velocity opens it and a touch of "
  "random cutoff keeps repeated notes from sounding like a machine.",
  "Velocity into cutoff and level; random into cutoff; half keytracking.",
  ["a-saw", "b-square"], {"a-level": 80, "b-level": 55, "b-pw": 30, "resonance": 18},
  {"cutoff": hz(350), "fil-env-amt": fea(26), **adsr("fil", 0, 80, 0, 100), **adsr("amp", 0, 180, 0, 150)},
  settings={"keytrack": "half"},
  routes1=[["random", "cutoff", 8]] + touch(40, 55, bark=20))

P(37, "FM Bell", "pluck",
  "A triangle frequency-modulated by oscillator B at +22 semitones (an inharmonic 3.6:1), the FM index decaying with "
  "Env 4 over 900 ms and scaled by velocity; B itself silent. A long ring with a little per-voice drift.",
  "Env 4 and velocity into the poly-mod osc B amount; the amp envelope nudging B's pitch for a bell's slight droop.",
  ["a-tri", "b-tri", "pmod-freq-a"], {"a-level": 85, "b-level": 0, "resonance": 10},
  {"freq-b": st(22), "cutoff": hz(3000), "fil-env-amt": fea(6), **adsr("fil", 0, 800, 20, 800), **adsr("amp", 0, 2500, 0, 900),
   **adsr("env4", 0, 900, 0, 200), "lfo2-rate": lfo_hz(0.15)},
  settings={"drift": 6, **LIFE},
  routes1=[["env4", "pmod-osc-b", 70], ["velocity", "pmod-osc-b", 25], ["amp-env", "pitch-b", 6]] + life(2, 0) + touch(20, 50))

P(38, "Velo Pluck", "pluck",
  "Saws an octave apart with no filter envelope of their own: velocity supplies the pluck (cutoff, bark and decay), "
  "so a soft note is a dull tap and a hard one a bright snap. Pressure after the note lets it swell back open.",
  "Velocity into cutoff, envelope amount and decay; aftertouch to cutoff; per-voice spread.",
  ["a-saw", "b-saw"], {"a-level": 80, "b-level": 50, "resonance": 20},
  {"freq-b": st(12), "cutoff": hz(220), "fil-env-amt": fea(12), **adsr("fil", 0, 200, 0, 200), **adsr("amp", 0, 700, 20, 300)},
  routes1=spread(cents=5) + [["velocity", "cutoff", 55], ["velocity", "filter-env-amount", 45], ["velocity", "fil-dec", 35],
           ["velocity", "vca", 50]] + press(cutoff=45))

P(39, "Dice Pluck", "pluck",
  "Pulse and saw an octave up through a resonant pluck; cutoff, pulse width and decay change at random on every note, "
  "and LFO 2 (S&H, per voice) steps B's pitch slightly while a note rings. No two notes alike.",
  "Random into three destinations, the S&H LFO per voice into pitch, velocity on top.",
  ["a-square", "b-saw"], {"a-level": 78, "b-level": 50, "a-pw": 40, "resonance": 38},
  {"freq-b": st(12), "cutoff": hz(300), "fil-env-amt": fea(24), **adsr("fil", 0, 200, 0, 200), **adsr("amp", 0, 500, 10, 250),
   "lfo2-rate": lfo_hz(6)},
  settings={"shape 1 2": "sh", "lfo2-trigger": "voice"},
  routes1=[["random", "cutoff", 25], ["random", "a-pw", 30], ["random", "fil-dec", 25], ["lfo2", "pitch-b", pitch_depth(0.15)]]
          + touch(30, 45))

P(40, "Marimba", "pluck",
  "A triangle with a quiet square two octaves up (the bar's 4:1 partial) and a touch of noise for the mallet; a 60 ms "
  "filter pluck, a decay that shortens up the keyboard (note → decay) and lengthens with velocity.",
  "Note number into the amp decay, velocity into the decay and cutoff, noise as the mallet.",
  ["a-tri", "b-square"], {"a-level": 85, "b-level": 25, "noise": 6, "resonance": 22},
  {"freq-b": st(24), "cutoff": hz(700), "fil-env-amt": fea(20), **adsr("fil", 0, 60, 0, 100), **adsr("amp", 0, 600, 0, 300)},
  routes1=[["note", "amp-dec", -30], ["velocity", "amp-dec", 25]] + touch(30, 50, decay=15))


# ----------------------------------------------------------------------------------------------- arps and sequences
ramp16 = [-40, -33, -26, -19, -12, -5, 2, 9, 16, 23, 30, 37, 44, 51, 58, 65]
P(41, "Trance Bass Seq", "arp",
  "Hold a key: a 16th-note octave bassline in that key, saw plus square an octave down, with lane A opening the "
  "filter over the bar. Accents on the downbeats.",
  "The step sequencer from MIDI clock, note offsets, per-step velocity, an automation lane (Seq A) gliding into cutoff.",
  ["a-saw", "b-square"], {"a-level": 75, "b-level": 65, "b-pw": 45, "resonance": 25},
  {"freq-b": st(-12), "cutoff": hz(250), "fil-env-amt": fea(24), **adsr("fil", 0, 200, 10, 150),
   **adsr("amp", 0, 500, 70, 100)},
  settings={"voice 1": "mono last"},
  routes1=[["seq-a", "cutoff", 30], ["velocity", "vca", 40]],
  seq=sequence(steps([0, 0, 12, 0, 0, 0, 12, 0, 0, 0, 12, 0, 0, 12, 0, 12],
                     vel=[127, 0, 0, 0, 0, 0, 0, 0, 127, 0, 0, 0, 0, 0, 0, 0], lanes={"a": ramp16}),
               lanes=1, smooth="A"))

P(42, "Acid Seq", "arp",
  "A 303-style line: one saw, resonance 70, a big fast filter envelope; ties slide (gate 100 %), accented steps hit "
  "the filter harder; lane A adds a slow filter rise.",
  "Ties/slides, accents through velocity into the filter envelope amount, mono glide, a lane on cutoff.",
  ["a-saw"], {"a-level": 85, "b-level": 0, "resonance": 70},
  {"cutoff": hz(180), "fil-env-amt": fea(36), **adsr("fil", 0, 180, 0, 150), **adsr("amp", 0, 500, 75, 100),
   "glide": glide(30)},
  settings={"voice 1": "mono last", "keytrack": "half"},
  routes1=[["velocity", "filter-env-amount", 50], ["velocity", "vca", 40], ["seq-a", "cutoff", 25]],
  seq=sequence(steps([0, 0, 12, 0, 3, 0, 0, -2, 0, 12, 0, 0, 7, 0, 5, 0],
                     gate=[50, 100, 50, 50, 50, 50, 50, 50, 100, 50, 50, 50, 50, 50, 100, 50],
                     vel=[127, 0, 0, 0, 127, 0, 0, 0, 0, 0, 127, 0, 0, 0, 0, 0],
                     lanes={"a": [-30, -30, -20, -20, -10, -10, 0, 0, 10, 10, 20, 20, 30, 40, 50, 60]}),
               lanes=1, smooth="A"))

P(43, "Arp Up Pluck", "arp",
  "Hold a chord: it arpeggiates upward in 16ths, every fifth and eighth step jumping an octave. Saw plus a pulse an "
  "octave up, a short pluck.",
  "The arpeggiator (up) with per-step note offsets transposing the arp note.",
  ["a-saw", "b-square"], {"a-level": 70, "b-level": 55, "b-pw": 40, "resonance": 20},
  {"freq-b": st(12), "cutoff": hz(500), "fil-env-amt": fea(18), **adsr("fil", 0, 200, 0, 150),
   **adsr("amp", 0, 400, 0, 150)},
  routes1=[["velocity", "vca", 50]],
  seq=sequence(steps([0, 0, 0, 0, 12, 0, 0, 12], gate=40), arp="up"))

P(44, "Ratchet Groove", "arp",
  "An eighth-note bass groove where some steps stutter (2, 3 or 4 ratchets) and two of them only play sometimes "
  "(4 and 6 in 8).",
  "Ratchets and probability per step.",
  ["a-saw", "b-saw"], {"a-level": 70, "b-level": 70, "resonance": 30},
  {"freq-b": st(-12), "fine": ct(5), "cutoff": hz(300), "fil-env-amt": fea(18), **adsr("fil", 0, 150, 5, 100),
   **adsr("amp", 0, 300, 60, 100)},
  settings={"voice 1": "mono last"},
  routes1=[["velocity", "vca", 40], ["velocity", "cutoff", 25]],
  seq=sequence(steps([0, 0, 7, 0, 10, 0, 7, 12], gate=60, ratchets=[1, 2, 1, 3, 1, 2, 1, 4],
                     prob=[8, 8, 8, 8, 8, 4, 8, 6]), step="1/8"))

riff32 = [0, None, 0, 12, None, 0, 10, None, 0, None, 0, 7, None, 0, 12, None,
          0, None, 0, 12, None, 0, 10, None, -2, None, -2, 10, None, -2, 7, 5]
laneA = [round(-60 + 120 * math.sin(math.pi * i / 31)) for i in range(32)]           # rise and fall
laneB = [(-50 if i % 4 == 0 else 20 if i % 4 == 2 else 0) for i in range(32)]        # stepped
P(45, "Lane Sweep", "arp",
  "A 32-step 16th riff with rests; lane A glides the cutoff up and back over the two bars, lane B steps the pulse "
  "width every beat.",
  "Two automation lanes: one smooth (Seq A into cutoff), one stepped (Seq B into PW A).",
  ["a-square", "b-saw"], {"a-level": 70, "b-level": 65, "a-pw": 50, "resonance": 40},
  {"freq-b": st(-12), "cutoff": hz(200), "fil-env-amt": fea(12), **adsr("fil", 0, 300, 30, 150),
   **adsr("amp", 0, 500, 80, 150)},
  settings={"voice 1": "mono last"},
  routes1=[["seq-a", "cutoff", 40], ["seq-b", "a-pw", 40], ["velocity", "vca", 40]],
  seq=sequence(steps(riff32, lanes={"a": laneA, "b": laneB}), lanes=2, smooth="A"))

P(46, "Chance Melody", "arp",
  "A 16-step eighth-note melody in minor pentatonic where half the steps play only sometimes; whatever key you hold, "
  "the scale quantizer keeps the transposed notes in C minor pentatonic.",
  "Probability per step and scale quantize (pentatonic minor, root C).",
  ["a-tri", "b-saw"], {"a-level": 75, "b-level": 40, "resonance": 30},
  {"freq-b": st(12), "cutoff": hz(900), "fil-env-amt": fea(12), **adsr("fil", 0, 250, 0, 200),
   **adsr("amp", 0, 600, 0, 300)},
  settings={"scale": "pentatonic-minor", "root": "C"},
  routes1=[["velocity", "vca", 40], ["random", "cutoff", 10]],
  seq=sequence(steps([0, 3, 5, 7, 10, 12, 7, 5, 3, 0, -2, 0, 3, 7, 10, 12],
                     prob=[8, 6, 8, 4, 8, 6, 8, 3, 8, 5, 8, 4, 8, 6, 5, 8]), step="1/8"))

P(47, "Swing Walk", "arp",
  "An eighth-note walking bass with heavy swing, saw plus a triangle an octave down, a little glide; the filter "
  "drops when the keys are released.",
  "Swing, mono glide, the Gate source (1 while a key is down).",
  ["a-saw", "b-tri"], {"a-level": 70, "b-level": 60, "resonance": 10},
  {"freq-b": st(-12), "cutoff": hz(350), "fil-env-amt": fea(18), **adsr("fil", 0, 300, 10, 150),
   **adsr("amp", 0, 600, 50, 150), "glide": glide(20)},
  settings={"voice 1": "mono last"},
  routes1=[["velocity", "vca", 40], ["gate", "cutoff", 15]],
  seq=sequence(steps([0, 4, 7, 12, 10, 7, 4, -2], gate=65), step="1/8", swing=62))

P(48, "Chan Duo Seq", "arp",
  "Channel mode: channel 1 plays a sequenced 16th bass (layer 1, mono), channel 2 a legato lead with vibrato "
  "(layer 2). Only layer 1's keys start the sequence.",
  "Channel layer mode, a sequence that takes layer 1's keys only, per-layer voice modes.",
  ["a-saw", "b-saw", "lfo-freq"], {"a-level": 75, "b-level": 65, "resonance": 20},
  {"freq-b": st(-12), "cutoff": hz(250), "fil-env-amt": fea(24), **adsr("fil", 0, 200, 10, 150),
   **adsr("amp", 0, 500, 70, 100), "lfo-amount": 0},
  {"fine": ct(9), "cutoff": hz(900), "fil-env-amt": fea(9), **adsr("fil", 0, 600, 50, 250),
   **adsr("amp", 5, 500, 90, 300), "glide": glide(40), "lfo-freq": lfo_hz(5.5), "lfo-amount": lfo_pitch(0.25)},
  settings={"layers": "channel", "voice 1": "mono last", "voice 2": "legato last", "shape 2 1": "sine",
            "lfo-delay 1": 40, "lfo-fade 1": 45},
  routes1=[["velocity", "vca", 40]], routes2=[["aftertouch", "cutoff", 35], ["velocity", "vca", 35]],
  seq=sequence(steps([0, 0, 12, 0, 0, 0, 10, 0, 0, 0, 12, 0, -2, 0, 10, 12]), keys="1"))

# ----------------------------------------------------------------------------------------------- FX
P(49, "S&H Sci-Fi", "fx",
  "Pulse and saw a fifth apart through a screaming filter; LFO 1 (sample and hold at 8 Hz) throws pitch and cutoff "
  "around while LFO 2's falling ramp pulls the filter down. The wheel speeds it up.",
  "The S&H shape on pitch and filter (buttons), the ramp-down shape, the wheel into the LFO rate.",
  ["a-square", "b-saw", "lfo-freq", "lfo-filter"], {"a-level": 70, "b-level": 60, "a-pw": 50, "resonance": 75},
  {"freq-b": st(7), "cutoff": hz(600), "fil-env-amt": fea(0), **adsr("fil", 0, 300, 99, 300),
   **adsr("amp", 20, 300, 99, 400), "lfo-freq": lfo_hz(8), "lfo-amount": 99,
   "lfo2-rate": lfo_hz(0.3), "lfo2-amount": 50},
  settings={"shape 1 1": "sh", "shape 1 2": "ramp-down", "lfo2-dests 1": "filter"},
  routes1=[["mod-wheel", "lfo-rate", 50]])

P(50, "Wind & Thunder", "fx",
  "Noise alone through a resonant filter wandered by two drift LFOs; low keys rumble, high keys whistle. Press to "
  "bring the storm closer.",
  "Noise as the only source, the drift shape on both LFOs, Note into cutoff, aftertouch.",
  ["lfo-filter"], {"a-level": 0, "b-level": 0, "noise": 99, "resonance": 60},
  {"cutoff": hz(300), "fil-env-amt": fea(0), **adsr("fil", 0, 300, 99, 1500), **adsr("amp", 1500, 600, 90, 3000),
   "lfo-freq": lfo_hz(0.3), "lfo-amount": lfo_filter(30), "lfo2-rate": lfo_hz(0.1), "lfo2-amount": 45},
  settings={"shape 1 1": "drift", "shape 1 2": "drift", "lfo2-dests 1": "filter", "lfo-trigger": "voice",
            "keytrack": "off"},
  routes1=[["note", "cutoff", 40], ["aftertouch", "cutoff", 40], ["aftertouch", "resonance", 20]])

P(51, "Laser", "fx",
  "Synced saws; Env 4 drops the pitch two octaves in 300 ms while sweeping the sync: pew. Harder notes fall slower.",
  "Env 4 into pitch at full depth (24 semitones) plus Pitch A for the sync sweep, velocity into Env 4's decay.",
  ["a-saw", "b-saw", "sync"], {"a-level": 90, "b-level": 30, "resonance": 55},
  {"freq-a": st(12), "cutoff": hz(2000), "fil-env-amt": fea(24), **adsr("fil", 0, 400, 0, 150),
   **adsr("amp", 0, 600, 0, 150), **adsr("env4", 0, 300, 0, 3)},
  routes1=[["env4", "pitch", 99], ["env4", "pitch-a", 60], ["velocity", "env4-dec", 30], ["velocity", "vca", 40]])

P(52, "Helicopter", "fx",
  "Low pulse, saw and noise chopped by a free-cycling amp envelope at about 12 Hz; the wheel slows the rotor, "
  "pressure opens the filter, a drift LFO moves it.",
  "A cycling amp envelope (attack, hold, fall, rest) as a chopper, the mod wheel scaling its rest time.",
  ["a-square", "b-saw", "lfo-filter"], {"a-level": 60, "b-level": 50, "noise": 60, "a-pw": 50, "resonance": 50},
  {"freq-a": st(-24), "freq-b": st(-36), "cutoff": hz(250), "fil-env-amt": fea(0), **adsr("fil", 0, 300, 99, 150),
   "amp-att": att(8), "amp-sus": att(30), "amp-dec": dec(40), "amp-rel": att(10),
   "lfo-freq": lfo_hz(0.5), "lfo-amount": 40},
  settings={"cycle 1": "amp", "shape 1 1": "drift"},
  routes1=[["mod-wheel", "amp-rel", 50], ["aftertouch", "cutoff", 40]])

P(53, "Siren", "fx",
  "Two squares an octave apart; LFO 1's triangle swings the pitch a full octave each way at 0.5 Hz, restarting with "
  "the first key. The wheel speeds it, pressure tames it.",
  "A full-depth triangle LFO on pitch, retrigger on the first key, the wheel into the rate, aftertouch reducing depth.",
  ["a-square", "b-square", "lfo-freq"], {"a-level": 80, "b-level": 40, "a-pw": 50, "b-pw": 50, "resonance": 20},
  {"freq-b": st(12), "cutoff": hz(1500), "fil-env-amt": fea(0), **adsr("fil", 0, 300, 99, 150),
   **adsr("amp", 100, 300, 99, 500), "lfo-freq": lfo_hz(0.5), "lfo-amount": 99},
  settings={"voice 1": "mono last", "lfo-trigger": "first"},
  routes1=[["mod-wheel", "lfo-rate", 40], ["aftertouch", "lfo-amount", -40]])

P(54, "Glitch Steps", "fx",
  "Saw and pulse; LFO 1's step pattern (one bar of clock) jumps the pitch an octave between its high and low steps, "
  "snapped to minor pentatonic, while LFO 2 (S&H in 16ths) picks a new cutoff every step.",
  "The Steps shape on pitch (via a route), S&H synced to 1/16 on the filter, scale quantize.",
  ["a-saw", "b-square"], {"a-level": 70, "b-level": 60, "b-pw": 30, "resonance": 50},
  {"freq-b": st(-12), "cutoff": hz(400), "fil-env-amt": fea(12), **adsr("fil", 0, 300, 60, 150),
   **adsr("amp", 0, 300, 90, 150), "lfo-freq": div("1bar"), "lfo-amount": 0,
   "lfo2-rate": div("1/16"), "lfo2-amount": lfo_filter(24)},
  settings={"shape 1 1": "steps", "lfo-sync": "clock", "lfo-trigger": "first", "steps 1 1": "x..x.x.xx.x..x.x",
            "shape 1 2": "sh", "lfo2-sync": "clock", "lfo2-dests 1": "filter", "scale": "pentatonic-minor", "root": "C"},
  routes1=[["lfo", "pitch", pitch_depth(6)], ["mod-wheel", "lfo2-amount", 40], ["velocity", "vca", 40]])

P(55, "Seismic Drone", "fx",
  "All eight voices in unison, saws an octave apart 45 cents spread, drift on top, a slow triangle on the filter and a "
  "per-voice drift LFO on pitch; 2 s swell. The wheel spreads the detune further.",
  "Unison with detune, drift, LFO 2 drift per voice, the wheel into the Detune destination.",
  ["a-saw", "b-saw", "unison", "lfo-filter"], {"a-level": 80, "b-level": 80, "resonance": 60},
  {"freq-b": st(-12), "fine": ct(12), "cutoff": hz(150), "fil-env-amt": fea(0), **adsr("fil", 0, 300, 99, 600),
   **adsr("amp", 2000, 300, 99, 4000), "lfo-freq": lfo_hz(0.1), "lfo-amount": lfo_filter(16),
   "lfo2-rate": lfo_hz(0.2), "lfo2-amount": lfo_pitch(0.5)},
  settings={"voice 1": "mono low", "detune": 45, "drift": 60, "shape 1 2": "drift", "lfo2-dests 1": "pitch",
            "lfo2-trigger": "voice"},
  routes1=[["aftertouch", "cutoff", 40], ["aftertouch", "resonance", 20], ["mod-wheel", "detune", 50]])

P(56, "Metal Clang", "fx",
  "Oscillator B at +31 semitones modulates both oscillator A and the filter through poly-mod (filter FM), Env 4 "
  "adding more at the start; resonant and inharmonic, with each note's partials shifted a little.",
  "Poly-mod into the filter (filter FM) as well as freq A, Env 4 adding poly-mod depth, random per-note detune of B.",
  ["a-tri", "b-saw", "pmod-freq-a", "pmod-filter"], {"a-level": 80, "b-level": 20, "pmod-osc-b": 50, "resonance": 70},
  {"freq-b": st(31), "cutoff": hz(800), "fil-env-amt": fea(24), **adsr("fil", 0, 600, 0, 500),
   **adsr("amp", 0, 1500, 0, 800), **adsr("env4", 0, 400, 30, 600)},
  routes1=[["env4", "pmod-osc-b", 50], ["velocity", "vca", 50], ["velocity", "cutoff", 30], ["random", "pitch-b", 10]])

# ----------------------------------------------------------------------------------------------- other
P(57, "Eight Channels", "other",
  "One mono voice per MIDI channel 1-8, each with its own pitch bend, pressure and CC 74 brightness: a saw with a "
  "pulse an octave down, a little glide. Made for sequencers that drive eight lines.",
  "The 8ch layer mode; Timbre (CC 74) and aftertouch per voice.",
  ["a-saw", "b-square"], {"a-level": 75, "b-level": 60, "b-pw": 45, "resonance": 25},
  {"freq-b": st(-12), "cutoff": hz(500), "fil-env-amt": fea(18), **adsr("fil", 0, 300, 30, 200),
   **adsr("amp", 0, 500, 80, 200), "glide": glide(25)},
  settings={"layers": "8ch", "bend": 2},
  routes1=[["timbre", "cutoff", 50], ["aftertouch", "cutoff", 30], ["velocity", "vca", 50], ["velocity", "cutoff", 20]])

P(58, "MPE Glide", "other",
  "MPE (lower zone): each note on channels 2-9 has 48 semitones of bend, its slide (CC 74) opens its filter and its "
  "pressure brings in vibrato. Two saws 7 cents apart, soft attack.",
  "The mpe layer mode; per-note Timbre, aftertouch and bend; per-voice LFOs.",
  ["a-saw", "b-saw", "lfo-freq"], {"a-level": 70, "b-level": 70, "resonance": 20},
  {"fine": ct(7), "cutoff": hz(400), "fil-env-amt": fea(9), **adsr("fil", 10, 600, 50, 300),
   **adsr("amp", 15, 500, 90, 600), **VIB},
  settings={"layers": "mpe", "shape 1 1": "sine", "lfo-trigger": "voice"},
  routes1=[["timbre", "cutoff", 60], ["aftertouch", "lfo-amount", 40], ["aftertouch", "cutoff", 20],
           ["velocity", "vca", 45]])

P(59, "Wheel Morph", "other",
  "A dry saw pluck that the mod wheel turns, knob by knob, into P25 Warm Pad (same oscillators, so only the sound "
  "moves); LFO 2 (one bar of clock) keeps it drifting a little between the two on its own.",
  "The Morph destination: mod wheel and a synced LFO morphing towards another patch of the set by slot number.",
  ["a-saw", "b-saw"], {"a-level": 70, "b-level": 70, "resonance": 8},
  {"fine": ct(9), "cutoff": hz(350), "fil-env-amt": fea(24), **adsr("fil", 0, 250, 0, 200),
   **adsr("amp", 0, 500, 0, 300), "lfo2-rate": div("1bar")},
  settings={"shape 1 2": "sine", "lfo2-sync": "clock"},
  routes1=[["mod-wheel", "morph", 99], ["lfo2", "morph", 25], ["velocity", "vca", 40]],
  morph=25)

P(60, "Split Bass Lead", "other",
  "Split at C3: below it a mono saw bass with a short filter envelope; from it up a legato lead with glide, an Env 4 "
  "scoop into each note and a late vibrato.",
  "Split mode, per-layer voice modes, envelopes and routes (Env 4 into Pitch A on the lead only).",
  ["a-saw", "b-saw", "lfo-freq"], {"a-level": 75, "b-level": 70, "resonance": 15},
  {"freq-b": st(-12), "cutoff": hz(220), "fil-env-amt": fea(24), **adsr("fil", 0, 250, 10, 150),
   **adsr("amp", 0, 500, 70, 150), "glide": glide(15), "lfo-amount": 0},
  {"fine": ct(8), "cutoff": hz(900), "fil-env-amt": fea(9), **adsr("fil", 0, 600, 50, 250),
   **adsr("amp", 5, 500, 90, 300), "glide": glide(45), **adsr("env4", 0, 80, 0, 3),
   "lfo-freq": lfo_hz(5.5), "lfo-amount": lfo_pitch(0.25)},
  settings={"layers": "split", "split": 60, "voice 1": "mono last", "voice 2": "legato last", "shape 2 1": "sine",
            "lfo-delay 1": 40, "lfo-fade 1": 50},
  routes1=[["velocity", "vca", 40], ["velocity", "cutoff", 25]],
  routes2=[["env4", "pitch-a", -pitch_depth(1)], ["aftertouch", "cutoff", 35], ["velocity", "vca", 35]])

P(61, "Unison Monster", "other",
  "All eight voices on one note, saws an octave apart spread 45 cents with drift and a per-voice drift LFO; legato "
  "with glide. The wheel pulls the voices even further apart.",
  "Unison (8 voices), detune, drift, legato glide, the wheel into Detune.",
  ["a-saw", "b-saw", "unison"], {"a-level": 80, "b-level": 80, "resonance": 15},
  {"freq-b": st(-12), "fine": ct(9), "cutoff": hz(400), "fil-env-amt": fea(18), **adsr("fil", 0, 400, 30, 200),
   **adsr("amp", 0, 500, 85, 300), "glide": glide(30), "lfo2-rate": lfo_hz(0.3), "lfo2-amount": lfo_pitch(0.2)},
  settings={"voice 1": "legato last", "detune": 45, "drift": 40, "shape 1 2": "drift", "lfo2-dests 1": "pitch",
            "lfo2-trigger": "voice"},
  routes1=[["aftertouch", "cutoff", 40], ["mod-wheel", "detune", 40], ["velocity", "vca", 35], ["velocity", "cutoff", 25]])

P(62, "Trance Gate", "other",
  "A saw pad cut into 16ths by LFO 1's step pattern over one bar of clock (the 'ta-ta-taa' gate), restarting with "
  "every chord; LFO 2 (sine, 2 bars) sweeps the filter underneath.",
  "The Steps shape into the VCA at 99 (a trance gate), retrigger on every key, LFO 2 synced to 2 bars.",
  ["a-saw", "b-saw"], {"a-level": 70, "b-level": 70, "resonance": 15},
  {"fine": ct(8), "cutoff": hz(600), "fil-env-amt": fea(6), **adsr("fil", 0, 600, 60, 400),
   **adsr("amp", 5, 600, 90, 250), "lfo-freq": div("1bar"), "lfo-amount": 0,
   "lfo2-rate": div("2bar"), "lfo2-amount": lfo_filter(12)},
  settings={"shape 1 1": "steps", "lfo-sync": "clock", "lfo-trigger": "every", "steps 1 1": "x.x.xx..x.x.xx..",
            "shape 1 2": "sine", "lfo2-sync": "clock", "lfo2-dests 1": "filter"},
  routes1=[["lfo", "vca", 99], ["aftertouch", "cutoff", 30]])

P(63, "Self Player", "other",
  "Hold a key or a chord and it plays itself: the amp and filter envelopes cycle in eighths from the clock, and each "
  "voice's own S&H LFO picks a new pitch every eighth, snapped to C minor pentatonic; a drift LFO moves the filter. "
  "Pressure speeds the dice.",
  "Generative: S&H LFO into pitch with scale quantize, per-voice LFOs, synced cycling amp and filter envelopes, "
  "aftertouch into the LFO rate.",
  ["a-saw", "b-tri"], {"a-level": 70, "b-level": 50, "resonance": 35},
  {"freq-b": st(12), "cutoff": hz(500), "fil-env-amt": fea(12),
   "fil-att": 5, "fil-dec": 50, "fil-sus": 10, "fil-rel": 35,
   "amp-att": 5, "amp-dec": 50, "amp-sus": 25, "amp-rel": 20,
   "lfo-freq": div("1/8"), "lfo-amount": 0, "lfo2-rate": lfo_hz(0.15), "lfo2-amount": lfo_filter(12)},
  settings={"shape 1 1": "sh", "lfo-sync": "clock", "lfo-trigger": "voice", "shape 1 2": "drift",
            "lfo2-dests 1": "filter", "cycle 1": "amp,filter", "cycle-sync 1 amp": "1/8", "cycle-sync 1 filter": "1/8",
            "scale": "pentatonic-minor", "root": "C"},
  routes1=[["lfo", "pitch", pitch_depth(7)], ["velocity", "vca", 30], ["aftertouch", "lfo-rate", 30]])

P(64, "Two Macros", "other",
  "Saw and pulse. Macro 1 is 'brightness': the wheel feeds it (from a hand-set -20 offset) and it opens the cutoff, "
  "adds resonance and narrows pulse B. Macro 2 is 'motion': pressure and CC A (expression, CC 11) feed it, and it "
  "brings in vibrato, detunes B and adds noise.",
  "Both macros as sums of several sources (with a per-layer offset) fanning out to three destinations each.",
  ["a-saw", "b-square", "lfo-freq"], {"a-level": 70, "b-level": 65, "b-pw": 50, "resonance": 10},
  {"fine": ct(6), "cutoff": hz(300), "fil-env-amt": fea(12), **adsr("fil", 0, 600, 40, 250),
   **adsr("amp", 5, 500, 85, 300), **VIB},
  settings={"shape 1 1": "sine", "macro 1 1": -20, "macro 1 2": 0},
  routes1=[["mod-wheel", "macro1", 99], ["macro1", "cutoff", 45], ["macro1", "resonance", 30], ["macro1", "b-pw", -35],
           ["aftertouch", "macro2", 99], ["cc-a", "macro2", 60], ["macro2", "lfo-amount", 40], ["macro2", "pitch-b", 8],
           ["macro2", "noise", 30], ["velocity", "vca", 40]])

# ----------------------------------------------------------------------------------------------- write
assert [p["slot"] for p in PATCHES] == list(range(1, 65))
assert len({p["name"] for p in PATCHES}) == 64
GROUPS = ["bass", "lead", "poly", "pad", "pluck", "arp", "fx", "other"]
for g in GROUPS:
    assert sum(1 for p in PATCHES if p["group"] == g) == 8, g
os.makedirs(OUT, exist_ok=True)
for p in PATCHES:
    slug = "".join(c if c.isalnum() else "-" for c in p["name"].lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    path = os.path.join(OUT, "%02d-%s.json" % (p["slot"], slug))
    with open(path, "w") as f:
        json.dump(p, f, indent=1)
        f.write("\n")
print("wrote %d patches to %s" % (len(PATCHES), OUT))
