#!/usr/bin/env python3
"""Turns preset files (presets/*.json) into `pro800` command lines, one per line, for install.sh.

Every name and range is checked against the tool's own tables (tools/pro800.swift: paramNames, settingNames,
sourceNames, destNames, shapeNames, divisionNames, buttonNames), so a typo in a preset fails here, not on the synth.
Lines starting with '#' are comments. Arguments are shell-quoted; install.sh runs each line as `build/pro800 <line>`.

Two kinds of line need commands the tool doesn't have yet (presets/README.md describes them):
  seq set ... / seq step ...   write a patch's step sequence (SysEx 47 and 45, as the web page sends them)
  set morph <slot|none>        the morph target (SysEx 44)
install.sh skips them with a warning when the tool's help doesn't list them.

usage: plan.py [--slots 1-8,41,62] <file.json or directory>...
"""
import glob, json, os, shlex, sys

PARAMS = ["amp-rel", "amp-sus", "amp-dec", "amp-att", "fil-rel", "fil-sus", "fil-dec", "fil-att",
          "resonance", "fil-env-amt", "freq-a", "freq-b", "fine", "a-pw", "b-pw", "a-level",
          "glide", "lfo-freq", "lfo-amount", "master-tune", "b-level", "pmod-osc-b", "pmod-fil-env",
          "cutoff", "noise",
          "env3-att", "env3-dec", "env3-sus", "env3-rel", "env4-att", "env4-dec", "env4-sus", "env4-rel",
          "lfo2-rate", "lfo2-amount"]  # master-vol left out on purpose: it's never stored in a patch
SHARED = ["a-level", "b-level", "noise", "resonance", "a-pw", "b-pw", "pmod-osc-b"]
BUTTONS = ["a-saw", "a-tri", "a-square", "b-saw", "b-tri", "b-square", "lfo-pw", "lfo-freq", "lfo-filter",
           "pmod-freq-a", "pmod-filter", "unison", "sync"]
SOURCES = ["lfo", "filter-env", "amp-env", "velocity", "mod-wheel", "aftertouch", "env3", "env4", "random", "note",
           "lfo2", "seq-a", "seq-b", "seq-c", "seq-d", "lfo-uni", "lfo2-uni", "gate", "timbre",
           "bend", "pedal", "cc-a", "cc-b", "voice", "fixed", "macro1", "macro2"]
DESTS = ["pitch-a", "pitch-b", "pitch", "cutoff", "filter-env-amount", "pmod-env", "lfo-amount", "lfo-to-pitch",
         "lfo-to-filter", "vca", "a-level", "b-level", "noise", "resonance", "a-pw", "b-pw", "pmod-osc-b", "lfo-to-pw",
         "lfo-rate", "lfo2-amount", "lfo2-rate", "lfo2-to-pitch", "lfo2-to-filter", "lfo2-to-pw", "morph",
         "amp-att", "amp-dec", "amp-sus", "amp-rel", "fil-att", "fil-dec", "fil-sus", "fil-rel",
         "env3-att", "env3-dec", "env3-sus", "env3-rel", "env4-att", "env4-dec", "env4-sus", "env4-rel",
         "glide", "detune", "volume", "macro1", "macro2"]
SHAPES = ["tri", "square", "ramp-up", "ramp-down", "sine", "sh", "drift", "steps"]
DIVISIONS = ["8bar", "4bar", "2bar", "1bar", "1/2", "1/4.", "1/4", "1/4T", "1/8.", "1/8", "1/8T", "1/16.", "1/16",
             "1/16T", "1/32"]
SCALES = ["off", "major", "minor", "dorian", "phrygian", "lydian", "mixolydian", "harmonic-minor", "pentatonic-major",
          "pentatonic-minor", "blues", "whole-tone", "chromatic"]
ROOTS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
ARPS = ["off", "up", "down", "up-down", "random", "played"]
MAX_STEPS = {0: 60, 1: 48, 2: 40, 3: 34, 4: 30}
LAYERS = ("1", "2")
LFOS = ("1", "2")
ENVS = ("amp", "filter", "env3", "env4")


class Bad(Exception):
    pass


def check(ok, message):
    if not ok:
        raise Bad(message)


def in_range(v, low, high, what):
    check(isinstance(v, int) and not isinstance(v, bool) and low <= v <= high, "%s must be %d..%d, not %r" % (what, low, high, v))
    return v


def words(key):
    return key.split()


def setting_line(key, value):
    """One `set` command for a settings entry, checked."""
    w = words(key)
    name = w[0]
    v = str(value)
    if name == "layers":
        check(len(w) == 1 and v in ["off", "split", "stack", "channel", "8ch", "mpe"], "layers: " + v)
    elif name == "split":
        in_range(value, 0, 127, "split")
    elif name == "keytrack":
        check(v in ["off", "half", "full"], "keytrack: " + v)
    elif name in ("lfo-sync", "lfo2-sync"):
        check(v in ["free", "clock"], name + ": " + v)
    elif name in ("lfo-trigger", "lfo2-trigger"):
        check(v in ["free", "first", "every", "voice"], name + ": " + v)
    elif name == "shape":
        check(len(w) == 3 and w[1] in LAYERS and w[2] in LFOS and v in SHAPES, "shape: %s %s" % (key, v))
    elif name == "lfo2-dests":
        check(len(w) == 2 and w[1] in LAYERS, "lfo2-dests: " + key)
        check(v == "none" or all(d in ["pitch", "filter", "pw"] for d in v.split(",")), "lfo2-dests: " + v)
    elif name == "cycle":
        check(len(w) == 2 and w[1] in LAYERS, "cycle: " + key)
        check(v == "none" or all(e in ENVS for e in v.split(",")), "cycle: " + v)
    elif name == "cycle-sync":
        check(len(w) == 3 and w[1] in LAYERS and w[2] in ENVS and (v == "free" or v in DIVISIONS), "cycle-sync: %s %s" % (key, v))
    elif name == "steps":
        check(len(w) == 3 and w[1] in LAYERS and w[2] in LFOS, "steps: " + key)
        check(1 <= len(v) <= 16 and all(c in "x1.0-" for c in v), "steps pattern: " + v)
    elif name == "voice":
        parts = v.split()
        check(len(w) == 2 and w[1] in LAYERS and len(parts) == 2 and parts[0] in ["poly", "mono", "legato"]
              and parts[1] in ["last", "low", "high"], "voice: %s %s" % (key, v))
    elif name == "bend":
        in_range(value, 1, 12, "bend")
    elif name == "detune":
        in_range(value, 0, 50, "detune")
    elif name == "drift":
        in_range(value, 0, 99, "drift")
    elif name == "scale":
        check(v in SCALES, "scale: " + v)
    elif name == "root":
        check(v in ROOTS, "root: " + v)
    elif name in ("lfo-delay", "lfo-fade"):
        check(len(w) == 2 and w[1] in LFOS, name + ": " + key)
        in_range(value, 0, 99, name)
    elif name == "macro":
        check(len(w) == 3 and w[1] in LAYERS and w[2] in ("1", "2"), "macro: " + key)
        in_range(value, -99, 99, "macro offset")
    else:
        raise Bad("unknown setting: " + key)
    return "set " + key + " " + v


def sequence_lines(seq):
    """`seq set` and `seq step` lines (the proposed tool commands, see README)."""
    lanes = in_range(seq.get("lanes", 0), 0, 4, "lanes")
    steps = seq["steps"]
    check(1 <= len(steps) <= MAX_STEPS[lanes], "a sequence with %d lanes holds at most %d steps" % (lanes, MAX_STEPS[lanes]))
    check(seq.get("length", len(steps)) == len(steps), "sequence length doesn't match its steps")
    step = seq.get("step", "1/16")
    check(step in DIVISIONS, "step division: " + step)
    keys = str(seq.get("keys", "both"))
    check(keys in ["both", "1", "2"], "keys: " + keys)
    arp = seq.get("arp", "off")
    check(arp in ARPS, "arp: " + arp)
    smooth = seq.get("smooth", "")
    check(all(c in "ABCD" for c in smooth), "smooth: " + smooth)
    bpm = in_range(seq.get("bpm", 120), 20, 255, "bpm")
    swing = in_range(seq.get("swing", 0), 0, 75, "swing")
    head = ("seq set on=%d length=%d step=%s clock=%d bpm=%d swing=%d once=%d keys=%s arp=%s lanes=%d smooth=%s"
            % (1 if seq.get("on", True) else 0, len(steps), step, 1 if seq.get("clock", True) else 0, bpm, swing,
               1 if seq.get("once") else 0, keys, arp, lanes, smooth or "none"))
    lines = [head]
    for i, s in enumerate(steps):
        parts = ["seq step %d" % (i + 1)]
        if s.get("rest"):
            parts.append(".")
        else:
            parts.append("%+d" % in_range(s.get("note", 0), -36, 36, "step %d note" % (i + 1)))
        gate = s.get("gate", 50)
        in_range(gate, 1, 100, "step %d gate" % (i + 1))
        parts.append("tie" if gate == 100 else str(gate))
        if s.get("vel"):
            parts.append("v%d" % in_range(s["vel"], 1, 127, "step %d vel" % (i + 1)))
        if s.get("prob", 8) != 8:
            parts.append("p%d" % in_range(s["prob"], 1, 7, "step %d prob" % (i + 1)))
        if s.get("ratchets", 1) != 1:
            parts.append("x%d" % in_range(s["ratchets"], 2, 4, "step %d ratchets" % (i + 1)))
        for lane in "abcd"[:lanes]:
            if s.get(lane):
                parts.append("%s%+d" % (lane, in_range(s[lane], -99, 99, "step %d lane %s" % (i + 1, lane))))
        for lane in "abcd"[lanes:]:
            check(not s.get(lane), "step %d uses lane %s but the sequence has %d lane(s)" % (i + 1, lane, lanes))
        lines.append(" ".join(parts))
    return lines


def plan(patch):
    slot = in_range(patch["slot"], 1, 128, "slot")
    name = patch["name"]
    check(isinstance(name, str) and 1 <= len(name) <= 16 and all(0x20 <= ord(c) < 0x7F for c in name), "name: %r" % name)
    out = ["# P%d %s (%s)" % (slot, name, patch.get("group", "?")), "patch init"]
    settings = dict(patch.get("settings", {}))
    if "layers" in settings:  # first, so layer 2 is there for what follows
        out.append(setting_line("layers", settings.pop("layers")))
    on = patch.get("buttons", [])
    for b in on:
        check(b in BUTTONS, "button: " + b)
    for b in BUTTONS:
        out.append("set button %s %s" % (b, "on" if b in on else "off"))
    params = patch.get("params", {})
    for layer_key, layer in [("shared", "1"), ("1", "1"), ("2", "2")]:
        for pname, v in params.get(layer_key, {}).items():
            check(pname in PARAMS, "parameter: " + pname)
            if layer_key == "shared":
                check(pname in SHARED, "%s isn't shared by the layers" % pname)
            else:
                check(pname not in SHARED, "%s is shared: put it under params.shared" % pname)
            in_range(v, 0, 99, pname)
            out.append("param %s %s %d" % (layer, pname, v))
    for key, value in settings.items():
        out.append(setting_line(key, value))
    for layer, routes in patch.get("routes", {}).items():
        check(layer in LAYERS, "routes layer: " + layer)
        check(len(routes) <= 16, "layer %s has more than 16 routes" % layer)
        seen = set()
        for r in routes:
            check(isinstance(r, list) and len(r) == 3, "route must be [source, dest, depth]: %r" % (r,))
            src, dest, depth = r
            check(src in SOURCES, "source: %s" % src)
            check(dest in DESTS, "destination: %s" % dest)
            in_range(depth, -99, 99, "depth of %s -> %s" % (src, dest))
            check(depth != 0, "route %s -> %s has depth 0 (it would be removed)" % (src, dest))
            check((src, dest) not in seen, "route %s -> %s twice on layer %s" % (src, dest, layer))
            seen.add((src, dest))
            out.append("route %s %s %s %d" % (layer, src, dest, depth))
    if patch.get("sequence"):
        out.extend(sequence_lines(patch["sequence"]))
    if patch.get("morph"):
        out.append("set morph P%d" % in_range(patch["morph"], 1, 128, "morph"))
    out.append("patch save P%d" % slot)
    out.append("patch rename P%d %s" % (slot, shlex.quote(name)))
    return out


def parse_slots(text):
    slots = set()
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            slots.update(range(int(a), int(b) + 1))
        elif part:
            slots.add(int(part))
    return slots


def main(argv):
    only = None
    paths = []
    i = 0
    while i < len(argv):
        if argv[i] == "--slots":
            only = parse_slots(argv[i + 1])
            i += 2
        elif argv[i] in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        else:
            paths.append(argv[i])
            i += 1
    files = []
    for p in paths:
        files.extend(sorted(glob.glob(os.path.join(p, "*.json"))) if os.path.isdir(p) else [p])
    check(files, "no preset files given")
    patches = []
    for f in files:
        with open(f) as fh:
            try:
                patch = json.load(fh)
            except ValueError as e:
                raise Bad("%s: %s" % (f, e))
        patch["_file"] = f
        patches.append(patch)
    patches.sort(key=lambda p: p.get("slot", 0))
    slots = [p.get("slot") for p in patches]
    check(len(slots) == len(set(slots)), "two files share a slot")
    for patch in patches:
        if only is not None and patch.get("slot") not in only:
            continue
        try:
            lines = plan(patch)
        except (Bad, KeyError, TypeError) as e:
            raise Bad("%s: %s" % (patch["_file"], e))
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Bad as e:
        sys.stderr.write("plan.py: %s\n" % e)
        sys.exit(2)
