#!/usr/bin/env python3
# User-interaction tests: `python tools/test_panel.py [-k name]`. Builds the real firmware for this computer against
# simulated hardware (tools/sim/hw.c, with zig cc, into build/sim/) and uses it the way a person does: knobs, buttons,
# the VALUE encoder and MIDI go in, and every test checks what the display and the LEDs show. No synth is needed.
# A power cycle is a new process on the same EEPROM file. What each case should show comes from MANUAL.md.
import base64
import importlib.util
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import pro800  # noqa: E402

BUILD = os.path.join(ROOT, "build", "sim")
SIM = os.path.join(BUILD, "pro800sim.exe" if os.name == "nt" else "pro800sim")
FIRMWARE = ["main.c", "synth.c", "lfo.c", "seq.c", "store.c", "fastmath.c", "display.c", "midiout.c"]
VERSION = open(os.path.join(ROOT, "VERSION")).read().strip()


def build():
    os.makedirs(BUILD, exist_ok=True)
    src = os.path.join(ROOT, "src")
    inputs = ([os.path.join(src, f) for f in os.listdir(src)] + [os.path.join(HERE, "sim", f) for f in os.listdir(os.path.join(HERE, "sim"))]
              + [os.path.join(ROOT, "VERSION")])  # FW_VERSION_* come from it
    if os.path.exists(SIM) and os.path.getmtime(SIM) > max(os.path.getmtime(f) for f in inputs):
        return
    major, minor, patch = VERSION.split(".")
    subprocess.check_call(["zig", "cc", "-std=gnu11", "-O1", "-g", "-w", "-I" + src, "-include", os.path.join(HERE, "sim", "sim_regs.h"),
                           f"-DFW_VERSION_MAJOR={major}", f"-DFW_VERSION_MINOR={minor}", f"-DFW_VERSION_PATCH={patch}", "-Dmain=firmware_main"]
                          + [os.path.join(src, f) for f in FIRMWARE] + [os.path.join(HERE, "sim", "hw.c"), "-o", SIM, "-lm"])


# --- what the display can show: read from src/display.c, so it can't drift ---

DISPLAY_C = open(os.path.join(ROOT, "src", "display.c")).read()
GLYPHS = {str(i): int(v, 16) for i, v in enumerate(re.search(r"numbers\[10\] = \{(.*?)\}", DISPLAY_C).group(1).split(", "))}
GLYPHS.update({c: int(v, 16) for c, v in re.findall(r"case '(\w)': return (0x\w+);", DISPLAY_C)})
GLYPHS.update({"-": 0x40, "_": 0x08})
LEDS = {name: (int(a), int(b)) for name, a, b in re.findall(r"\[LED_(\w+)\] = \{(\d+), (\d+)\}", DISPLAY_C)}
DIGITS = (2, 0, 6, 4)
DOT = 0x80
READABLE = {}
for c, g in reversed(list(GLYPHS.items())):
    READABLE[g] = c


def segments_of(text):
    """What src/display.c's display_text() lights for a text. A character it has no glyph for is a failure here."""
    out, n = [0, 0, 0, 0], 0
    for c in text:
        if c == ".":
            if n:
                out[n - 1] |= DOT
            continue
        if n == 4:
            break
        if c != " ":
            g = GLYPHS.get(c.upper())
            if g is None:
                raise AssertionError(f"{text!r}: the display has no {c!r}")
            out[n] = g
        n += 1
    return out


def read_digits(ram):
    out = []
    for lo in DIGITS:
        a, b = ram[lo], ram[lo + 1]
        out.append((1 if a & 1 else 0) | (2 if a & 2 else 0) | (0x40 if a & 4 else 0) | (0x20 if b & 8 else 0) | (0x10 if b & 0x10 else 0)
                   | (8 if b & 0x20 else 0) | (4 if b & 0x40 else 0) | (DOT if b & 0x80 else 0))
    return out


def readable(digits):
    text = ""
    for d in digits:
        g = d & ~DOT
        text += " " if not g else READABLE.get(g, "?")
        if d & DOT:
            text += "."
    return text


POT = {name[4:]: i for i, name in enumerate(["POT_AMP_REL", "POT_AMP_SUS", "POT_AMP_DEC", "POT_AMP_ATT", "POT_FIL_REL", "POT_FIL_SUS", "POT_FIL_DEC", "POT_FIL_ATT",
                                             "POT_RESONANCE", "POT_FIL_ENV_AMT", "POT_FREQ_A", "POT_FREQ_B", "POT_FINE", "POT_A_PW", "POT_B_PW", "POT_A_LEVEL",
                                             "POT_GLIDE", "POT_LFO_FREQ", "POT_LFO_AMOUNT", "POT_MASTER_TUNE", "POT_MASTER_VOL", "POT_B_LEVEL", "POT_PMOD_OSC_B",
                                             "POT_PMOD_FIL_ENV", "POT_CUTOFF", "POT_NOISE"])}
SWITCH = {n: i for i, n in enumerate(["A_SAW", "A_TRI", "A_SQUARE", "B_SAW", "B_TRI", "B_SQUARE", "LFO_PW", "LFO_FREQ", "PMOD_FREQ_A", "LFO_SHAPE", "LFO_FILTER",
                                      "PMOD_FILTER", "UNISON", "KEYTRACK_HALF", "KEYTRACK_FULL", "SYNC"])}
BUTTON = {str(i): i for i in range(10)}
BUTTON.update({n: 10 + i for i, n in enumerate(["PRESET", "PERF", "SETTINGS", "TUNE", "SHIFT", "CLOCK", "REC", "SEQ1", "SEQ2", "ARP_UP", "ASSIGN"])})
CV_AMP1, CV_FIL1, CV_NOISE, CV_PW_A = 24, 16, 36, 38
VELOCITY, VCA, CUTOFF = pro800.SOURCE_NAMES.index("velocity"), pro800.DEST_NAMES.index("vca"), pro800.DEST_NAMES.index("cutoff")
INIT_ROUTES = [(VELOCITY, VCA, 70), (VELOCITY, CUTOFF, 20)]  # src/main.c, init_patch(); MANUAL, the mod matrix
KNOB_START = [(VELOCITY, VCA, 40), (VELOCITY, CUTOFF, 20)]  # where the knob tests begin: their setUp sets it, whatever init holds
ROUTE_HOLD_MS = 5000  # src/synth.h


class Synth:
    def __init__(self, eeprom, *options):
        self.eeprom = eeprom
        self.process = subprocess.Popen([SIM, eeprom, *options], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=BUILD)
        assert self.process.stdout.readline().strip() == "ready", "the firmware didn't reach its main loop"

    def cmd(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError(f"the firmware stopped on {text!r}")
        return line.strip()

    def close(self):
        if self.process.poll() is None:
            try:
                self.cmd("quit")
            except (AssertionError, OSError):
                pass  # it left on its own (update mode)
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.wait()

    # time and the panel
    def ms(self, n=1):
        self.cmd(f"ms {n}")

    def now(self):
        return int(self.cmd("disp").split()[0])

    def hold(self, button):
        self.cmd(f"btn {BUTTON[button]} 1")
        self.ms(50)

    def let_go(self, button):
        self.cmd(f"btn {BUTTON[button]} 0")
        self.ms(50)

    def press(self, *buttons):
        for b in buttons:
            self.hold(b)
            self.let_go(b)

    def long_press(self, button, ms=650):
        """Held past HOLD_MS (600 ms), then let go: Perf's init sound, Settings' system menu."""
        self.hold(button)
        self.ms(ms)
        self.let_go(button)

    def pot(self, name, fraction):
        self.cmd(f"pot {POT[name]} {round(fraction * 4095)}")
        self.ms(40)

    def switch(self, name, level=None):
        """A latching button: flips it. The keyboard-tracking lines take their level."""
        self.levels = getattr(self, "levels", {})
        level = (1 - self.levels.get(name, 1)) if level is None else level
        self.levels[name] = level
        self.cmd(f"sw {SWITCH[name]} {level}")
        self.ms(25)

    def turn(self, cycles, every=40):
        """VALUE: cycles of four transitions, slowly by default (one patch at a time)."""
        self.cmd(f"enc {4 * cycles} {every}")
        self.ms(abs(4 * cycles) * every + 1)

    # what it shows
    def ram(self):
        return bytes.fromhex(self.cmd("disp").split()[1])

    def digits(self):
        return read_digits(self.ram())

    def shows(self):
        return readable(self.digits())

    def led(self, name):
        address, bit = LEDS[name]
        return bool(self.ram()[address] >> bit & 1)

    def history(self):
        """(ms, what the display showed) at each change since last asked."""
        out, last = [], None
        for item in self.cmd("hist").split():
            at, ram = item.split(":")
            digits = read_digits(bytes.fromhex(ram))
            if digits != last:
                out.append((int(at), readable(digits)))
                last = digits
        return out

    # MIDI
    def midi(self, *data):
        self.cmd("midi " + bytes(data).hex())

    def din(self, *data):
        self.cmd("din " + bytes(data).hex())

    def note(self, note, velocity=100, channel=0):
        self.midi(0x90 | channel, note, velocity)
        self.ms(2)

    def cc(self, number, value, channel=0):
        self.midi(0xB0 | channel, number, value)

    def midi_stat(self):
        names = ("usb", "din", "discard", "tunes_discarding", "tunes_not_discarding")
        return dict(zip(names, map(int, self.cmd("midistat").split())))

    def request(self, *data):
        self.cmd("out")
        self.cmd("midi " + (pro800.SYSEX_HEADER + bytes(data) + b"\xF7").hex())
        self.ms(5)
        return bytes.fromhex(self.cmd("out"))

    def settings(self):
        reply = self.request(0x54)
        start = reply.index(pro800.SYSEX_HEADER + b"\x54")
        b = reply[start + pro800.HEADER_SIZE + 1:]
        count = b[0]
        names = pro800.SETTING_NAMES
        self.assert_names = count == len(names)
        return {names[r]: b[1 + 2 * r] << 7 | b[2 + 2 * r] for r in range(count)}

    def params_raw(self, layer=0):
        """Every parameter as the host reads it, 0-16383 (a tenth of 0-99 is about 16.5)."""
        self.ms(300)  # the knobs glide to a new reading
        reply = self.request(0x53, layer)
        start = reply.index(pro800.SYSEX_HEADER + bytes([0x53, layer]))
        b = reply[start + pro800.HEADER_SIZE + 2:]
        return {pro800.PARAM_NAMES[i]: b[1 + 2 * i] << 7 | b[2 + 2 * i] for i in range(b[0])}

    def params(self, layer=0):
        return {name: (v * 99 + 8191) // 16383 for name, v in self.params_raw(layer).items()}

    def route_table(self, layer=0):
        """All 16 slots, as 57 sends them: source 0 is an empty one."""
        reply = self.request(0x57, layer)
        start = reply.index(pro800.SYSEX_HEADER + bytes([0x57, layer]))
        d = reply[start + pro800.HEADER_SIZE:]
        return [(d[2 + 4 * r], d[3 + 4 * r], (d[4 + 4 * r] << 7 | d[5 + 4 * r]) - 99) for r in range(16)]

    def routes(self, layer=0):
        return [r for r in self.route_table(layer) if r[0]]

    def rest(self):
        """Long enough for a route at 0 to go (ROUTE_HOLD_MS)."""
        self.ms(ROUTE_HOLD_MS + 50)

    def no_routes(self):
        """Takes every route off both layers: the start for a test about something else, as the init sound comes with two."""
        for layer in (0, 1):
            for source, dest, _ in self.routes(layer):
                self.request(0x56, layer, source, dest, 0, 99)
        self.rest()  # they go after resting at 0 (MANUAL, the mod matrix)

    def cvs(self):
        return [int(x) for x in self.cmd("cv").split()]

    def memory(self):
        self.cmd("save")
        with open(self.eeprom, "rb") as f:
            return f.read()

    def slot_is_ours(self, slot):
        return self.memory()[(slot - 1) * 768:(slot - 1) * 768 + 4] == b"P8P2"

    # the host's view of the engine, for what the display can't show
    def reply(self, *data):
        """The data of the reply to a request, after the command byte it echoes."""
        out = self.request(*data)
        start = out.index(pro800.SYSEX_HEADER + bytes(data[:1]))
        return out[start + pro800.HEADER_SIZE + 1:out.index(0xF7, start)]

    def setting(self, name, value):
        """What came out meanwhile (the reply, and what the change sent)"""
        i = pro800.SETTING_NAMES.index(name)
        return self.request(0x7D, i, value >> 7, value & 0x7F) if value > 127 else self.request(0x7D, i, value)

    def param(self, layer, name, value):
        """0-16383, as the host sets it"""
        self.request(0x52, layer, pro800.PARAM_NAMES.index(name), value >> 7, value & 0x7F)

    def probe(self):
        d = self.reply(0x55)
        return {"held": [list(d[1 + 5 * l:1 + 5 * l + d[5 * l]]) for l in range(2)],
                "gates": [d[10 + 3 * v] for v in range(8)], "amp": [d[11 + 3 * v] for v in range(8)]}

    def gated(self):
        return [v for v, g in enumerate(self.probe()["gates"]) if g]

    def note_off(self, note, channel=0):
        self.midi(0x80 | channel, note, 0)
        self.ms(2)

    def amp(self):
        return max(self.cvs()[CV_AMP1 + v] for v in range(8))

    def pitch_of_gated_voice(self):
        voices = self.gated()
        assert len(voices) == 1, voices
        return self.cvs()[voices[0]]

    def seq_set(self, length, lanes=0, division=12, bpm=120, flags=1, swing=0, smooth=0, arp=0):
        """The sequence's header: division 12 is a 16th; flags: 1 on, 2 MIDI clock, 4 once"""
        self.request(0x47, length, lanes, division, bpm >> 7, bpm & 0x7F, flags, swing, smooth, arp)

    def seq_steps(self, first, steps):
        """(offset, velocity, gate %, on, extra, lanes) per step; velocity 0 = the key's"""
        data = []
        for s in steps:
            offset, velocity, gate, on, extra, lanes = (list(s) + [0, 50, 1, 0, (64, 64, 64, 64)][len(s) - 1:])
            data += [offset + 64, velocity, gate, on, extra, *lanes]
        self.request(0x45, first, len(steps), *data)

    def seq_header(self):
        d = self.reply(0x48)
        return {"length": d[0], "lanes": d[1], "division": d[2], "bpm": d[3] << 7 | d[4], "flags": d[5], "swing": d[6], "smooth": d[7], "max": d[8], "arp": d[9]}

    def seq_read(self, first=0, n=None):
        """The steps, as many as the sequence has (up to 8) unless n says"""
        n = min(8, self.seq_header()["length"]) if n is None else n
        d = self.reply(0x46, first, n)
        return [{"offset": d[2 + 9 * i] - 64, "velocity": d[3 + 9 * i], "gate": d[4 + 9 * i], "on": d[5 + 9 * i]} for i in range(d[1])]

    def patch_info(self, slot):
        d = self.reply(0x5E, slot >> 7, slot & 0x7F)
        return d[2], bytes(d[3:]).decode()

    def rename(self, slot, name):
        self.request(0x59, slot >> 7, slot & 0x7F, *name.encode())

    def buttons(self):
        return list(self.reply(0x4C))

    def set_button(self, switch, on):
        self.request(0x4B, SWITCH[switch], on)

    def version(self):
        return self.request(0x08, 0x00)


FIXTURES = None


def formatted_memory():
    """A formatted EEPROM, as after the installer: every slot empty, the global block ours."""
    global FIXTURES
    if FIXTURES is None:
        FIXTURES = tempfile.mkdtemp(prefix="pro800sim")
        build()
        path = os.path.join(FIXTURES, "formatted.bin")
        s = Synth(path)
        s.request(0x5B)
        s.close()
    return os.path.join(FIXTURES, "formatted.bin")


def stock_memory():
    """A memory as the stock firmware leaves it: 256-byte records with its magic, no global block of ours."""
    formatted_memory()
    path = os.path.join(FIXTURES, "stock.bin")
    if not os.path.exists(path):
        image = bytearray(0x20000)
        for r in range(400):
            image[r * 256:r * 256 + 8] = bytes([0xA5, 0x16, 0x61, 0x00, 0x10, 0x20, 0x30, 0x40])
        with open(path, "wb") as f:
            f.write(image)
    return path


class PanelTest(unittest.TestCase):
    MEMORY = "formatted"
    OPTIONS = ()

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pro800sim")
        self.path = os.path.join(self.dir, "eeprom.bin")
        if self.MEMORY == "formatted":
            shutil.copy(formatted_memory(), self.path)
        elif self.MEMORY == "stock":
            shutil.copy(stock_memory(), self.path)
        self.s = Synth(self.path, *self.OPTIONS)
        self.s.ms(1600)  # past the boot's version

    def tearDown(self):
        self.s.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def power_cycle(self, *options):
        self.s.close()
        self.s = Synth(self.path, *options)

    def assertShows(self, text, why=None):
        shown = self.s.digits()
        self.assertEqual(shown, segments_of(text), f"display shows {readable(shown)!r}, expected {text!r}" + (f": {why}" if why else ""))

    def assertSaw(self, texts, since=None):
        """The display went through these, in this order, among the changes since the last call."""
        seen = [t for _, t in self.s.history()]
        wanted = [readable(segments_of(t)) for t in texts]
        i = 0
        for t in seen:
            if i < len(wanted) and t == wanted[i]:
                i += 1
        self.assertEqual(i, len(wanted), f"display went through {seen}, expected {texts} among them")

    def store_patch(self, slot):
        """Rec, the slot, Rec: leaves that slot loaded."""
        self.s.press("REC")
        self.type_number(slot)
        self.s.press("REC")
        self.s.ms(1100)

    def type_number(self, n):
        for c in str(n):
            self.s.press(c)

    def manual(self):
        self.s.press("PRESET")
        self.assertShows("PAnL")


class Boot(PanelTest):
    def test_shows_the_version_then_the_first_slot_on_formatted_memory(self):
        self.power_cycle()
        major, minor, patch = VERSION.split(".")
        boot = self.s.history()
        self.assertIn(f"{major}.{minor}.{patch}", boot[0][1].replace(" ", "").replace("V", "").replace("U", ""), boot)
        self.s.ms(1600)
        self.assertShows("P  1")
        self.assertTrue(self.s.led("PRESET"))
        self.assertTrue(self.s.led("POWER"))
        self.assertTrue(self.s.led("SEQ1"))
        self.assertFalse(self.s.led("SEQ2"))

    def test_the_version_stays_long_enough_to_read(self):
        self.power_cycle()
        version = self.s.shows()
        self.assertIn(VERSION[0], version)
        self.s.ms(1400)
        self.assertEqual(self.s.shows(), version, "1.4 s after boot")
        self.s.ms(200)
        self.assertShows("P  1")

    def test_comes_back_on_the_last_patch(self):
        self.store_patch(5)
        self.s.press("2")
        self.s.ms(3100)
        self.assertShows("P  2")
        self.power_cycle()
        self.s.ms(1600)
        self.assertShows("P  2")
        self.assertEqual(self.s.settings()["patch"], 1)

    def test_comes_back_on_the_last_patch_after_leaving_manual_mode(self):
        self.store_patch(5)
        self.manual()
        self.assertFalse(self.s.led("PRESET"))
        self.power_cycle()
        self.s.ms(1600)
        self.assertShows("P  5")
        self.assertTrue(self.s.led("PRESET"))
        self.assertEqual(self.s.settings()["preset-mode"], 1)

    def test_the_stored_sound_plays_after_boot_whatever_the_knobs_say(self):
        self.s.request(0x52, 0, 24, 0x20, 0)
        self.store_patch(3)
        stored = self.s.params()["cutoff"]
        self.assertEqual(stored, 25)
        self.power_cycle()
        self.s.ms(100)
        self.s.cmd(f"pot {POT['CUTOFF']} 4000")
        self.s.ms(1600)
        self.assertEqual(self.s.params()["cutoff"], stored)

    def test_a_patch_loaded_at_boot_keeps_its_stored_knobs(self):
        self.s.request(0x52, 0, 24, 0x10, 0)  # cutoff at a quarter, from the host, whatever the knob says
        self.s.request(0x52, 0, 8, 0x30, 0)   # resonance
        self.store_patch(3)
        stored = self.s.params()
        self.power_cycle()
        self.s.ms(100)
        after = self.s.params()
        for name in ("cutoff", "resonance", "amp-rel", "fil-dec", "fil-env-amt"):
            self.assertEqual(after[name], stored[name], name)

    def test_a_knob_after_boot_is_in_pickup(self):
        self.s.request(0x52, 0, 8, 0x20, 0)  # resonance 25, the knob at 50
        self.store_patch(3)
        self.power_cycle()
        self.s.ms(1600)
        self.s.pot("RESONANCE", 0.7)
        self.assertShows("  25.")
        self.s.pot("RESONANCE", 0.1)
        self.assertEqual(self.s.params()["resonance"], 10, "swept past it")

    def test_settings_survive_a_power_cycle(self):
        self.s.hold("SHIFT")
        self.s.pot("NOISE", 0.0)
        self.s.pot("NOISE", 0.2)
        self.s.pot("NOISE", 0.5)
        self.s.let_go("SHIFT")
        channel = self.s.settings()["midi-channel"]
        self.assertGreater(channel, 0)
        self.s.ms(1100)  # stored once they have rested
        self.power_cycle()
        self.assertEqual(self.s.settings()["midi-channel"], channel)

    def test_a_format_mark_on_an_older_layout(self):
        self.s.close()
        with open(self.path, "r+b") as f:
            data = bytearray(f.read())
            data[0x1F407] = 1  # an older format number
            s = 0x12345678
            for b in data[0x1F400:0x1F40C]:
                s = ((s << 5 | s >> 27) & 0xFFFFFFFF) ^ b
            data[0x1F40C:0x1F410] = s.to_bytes(4, "little")
            f.seek(0)
            f.write(data)
        self.s = Synth(self.path)
        self.s.ms(5)
        self.assertShows("Forn")
        self.s.ms(1600)
        self.assertShows("P  1")
        self.assertFalse(self.s.slot_is_ours(1))


class BootOnBlankMemory(PanelTest):
    MEMORY = "blank"

    def test_a_patch_loaded_while_a_setting_rests_is_the_one_it_boots_on(self):
        self.s.setting("pickup", 0)
        self.s.ms(200)
        self.s.request(0x5C, 0, 4)
        self.assertEqual(self.s.settings()["patch"], 4)
        self.s.ms(1100)
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 4)
        self.assertEqual(self.s.settings()["pickup"], 0)

    def test_a_patch_loaded_before_a_setting_too(self):
        self.s.request(0x5C, 0, 4)
        self.s.ms(1100)
        self.s.setting("pickup", 0)
        self.s.ms(1100)
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 4)

    def test_a_load_alone_doesnt_make_the_memory_ours(self):
        start = int(self.s.cmd("writes").split()[0])
        self.s.request(0x5C, 0, 4)
        self.s.ms(1100)
        self.assertEqual(int(self.s.cmd("writes").split()[0]), start)
        self.assertEqual(self.s.memory()[0x1F400:0x1F404], bytes(4))

    def test_stays_on_the_panel(self):
        self.assertShows("PAnL")
        self.assertFalse(self.s.led("PRESET"))

    def test_preset_finds_the_first_slot_empty(self):
        self.s.press("PRESET")
        self.assertTrue(self.s.led("PRESET") or self.s.shows() == "nonE")


class BootUntuned(PanelTest):
    OPTIONS = ("--untuned",)

    def test_tunes_first_with_tune_on_the_display(self):
        self.power_cycle("--untuned")
        seen = [t for _, t in self.s.history()]
        self.assertEqual(seen[0], readable(segments_of("tUnE")), seen)


class ManualMode(PanelTest):
    def test_panl_stays_with_the_led_off(self):
        self.manual()
        self.assertFalse(self.s.led("PRESET"))
        self.s.ms(5000)
        self.assertShows("PAnL")

    def test_a_knob_shows_its_value_then_panl_again(self):
        self.manual()
        self.s.pot("RESONANCE", 0.25)
        self.assertShows("  25")
        self.s.ms(1450)
        self.assertShows("  25")
        self.s.ms(100)
        self.assertShows("PAnL")

    def test_a_switch_leaves_panl(self):
        self.manual()
        self.s.switch("A_SAW")
        self.s.ms(1100)
        self.assertShows("PAnL")

    def test_settings_and_back_to_panl(self):
        self.manual()
        self.s.press("SETTINGS")
        self.assertShows("SEt")
        self.s.press("SETTINGS")
        self.assertShows("PAnL")

    def test_the_knobs_are_live(self):
        self.manual()
        self.s.pot("CUTOFF", 0.25)
        self.assertEqual(self.s.params()["cutoff"], 25)
        self.s.pot("RESONANCE", 0.75)
        self.assertEqual(self.s.params()["resonance"], 74)

    def test_preset_goes_back_to_the_slot(self):
        self.store_patch(4)
        self.manual()
        self.s.press("PRESET")
        self.assertShows("P  4")
        self.assertTrue(self.s.led("PRESET"))

    def test_keypad_does_nothing_on_the_panel(self):
        self.manual()
        self.s.press("5")
        self.s.ms(3100)
        self.assertShows("PAnL")
        self.assertEqual(self.s.settings()["preset-mode"], 0)

    def test_value_does_nothing_on_the_panel(self):
        self.manual()
        self.s.turn(3)
        self.s.ms(200)
        self.assertShows("PAnL")

    def test_rec_in_manual_saves_the_panel(self):
        self.manual()
        self.s.pot("RESONANCE", 0.25)
        self.s.ms(1600)
        self.s.press("REC")
        self.assertShows("P  1")
        self.s.press("7", "REC")
        self.assertShows("donE")
        self.s.ms(1100)
        self.assertShows("P  7")
        self.assertTrue(self.s.led("PRESET"))
        self.assertTrue(self.s.slot_is_ours(7))


class TypingASlot(PanelTest):
    def setUp(self):
        super().setUp()
        for slot in (7, 12, 72, 128):
            self.store_patch(slot)
        self.s.press("1")
        self.s.ms(3100)
        self.assertShows("P  1")

    def test_one_digit_waits_then_loads(self):
        self.s.press("7")
        self.assertShows("P  7")
        self.assertEqual(self.s.settings()["patch"], 0, "loads only after the pause")
        self.s.ms(2800)
        self.assertEqual(self.s.settings()["patch"], 0)
        self.s.ms(300)
        self.assertShows("P  7")
        self.assertEqual(self.s.settings()["patch"], 6)

    def test_two_digits_no_more_can_follow(self):
        self.s.press("7", "2")
        self.assertShows("P 72")
        self.assertEqual(self.s.settings()["patch"], 71)

    def test_two_digits_a_third_may_follow(self):
        self.s.press("1", "2")
        self.assertShows("P 12")
        self.assertEqual(self.s.settings()["patch"], 0)
        self.s.ms(3100)
        self.assertShows("P 12")
        self.assertEqual(self.s.settings()["patch"], 11)

    def test_three_digits(self):
        self.s.press("1", "2", "8")
        self.assertShows("P128")
        self.assertEqual(self.s.settings()["patch"], 127)

    def test_past_128_starts_a_new_number(self):
        self.s.press("1", "2", "9")
        self.s.ms(3100)
        self.assertEqual(self.s.settings()["patch"], 8)
        self.assertShows("P  9")

    def test_a_lone_zero_just_goes(self):
        self.s.press("0")
        self.assertShows("P  0")
        self.s.ms(3100)
        self.assertShows("P  1")
        self.assertEqual(self.s.settings()["patch"], 0)

    def test_an_empty_slot_loads_the_init_sound_and_keeps_its_number(self):
        self.s.press("9")
        self.s.ms(3100)
        self.assertShows("P  9")
        self.assertEqual(self.s.settings()["patch"], 8)
        self.assertEqual(self.s.params()["cutoff"], 99)

    def test_another_button_drops_the_number(self):
        self.s.press("7", "SEQ2")
        self.s.ms(3200)
        self.assertEqual(self.s.settings()["patch"], 0)
        self.assertShows("P  1")

    def test_program_change_loads_and_shows(self):
        self.s.midi(0xC0, 11)
        self.s.ms(5)
        self.assertShows("P 12")
        self.assertEqual(self.s.settings()["patch"], 11)


class ValueThroughThePatches(PanelTest):
    def test_slow_steps_one_at_a_time_and_loads_at_rest(self):
        self.s.turn(1)
        self.assertShows("P  2")
        self.s.ms(100)
        self.assertEqual(self.s.settings()["patch"], 1)
        self.s.turn(1)
        self.s.ms(100)
        self.assertShows("P  3")
        self.assertEqual(self.s.settings()["patch"], 2)

    def test_back_from_the_first_wraps_to_the_last(self):
        self.s.turn(-1)
        self.s.ms(100)
        self.assertShows("P128")
        self.assertEqual(self.s.settings()["patch"], 127)

    def test_forward_from_the_last_wraps_to_the_first(self):
        self.s.press("1", "2", "8")
        self.s.turn(1)
        self.s.ms(100)
        self.assertShows("P  1")

    def test_a_fast_turn_jumps(self):
        self.s.turn(10, every=3)  # 12 ms a cycle
        self.s.ms(100)
        patch = self.s.settings()["patch"]
        self.assertGreater(patch, 30, "10 fast cycles should travel well past 10 slots")
        self.assertEqual(self.s.shows(), f"P{patch + 1:3d}")

    def test_medium_speeds_travel_between(self):
        self.s.turn(10, every=10)  # 40 ms a cycle: 4 slots each, after the first
        self.s.ms(100)
        patch = self.s.settings()["patch"]
        self.assertGreater(patch, 25)
        self.assertLess(patch, 60)

    def test_a_fast_turn_doesnt_load_every_slot_on_the_way(self):
        self.store_patch(9)
        self.s.press("1")
        self.s.ms(3100)
        self.s.history()
        before = self.s.settings()["patch"]
        self.s.cmd("enc 40 3")
        self.s.ms(100)
        self.assertEqual(self.s.settings()["patch"], before, "still turning: nothing loads")
        self.s.ms(120)
        self.s.ms(100)
        self.assertNotEqual(self.s.settings()["patch"], before)

    def test_shift_and_value_step_ten_slots_and_go_round_the_ends(self):
        self.s.hold("SHIFT")
        self.s.turn(1)
        self.assertShows("P 11")
        self.s.turn(1, every=5)
        self.assertShows("P 21", "tens, however fast it turns")
        self.s.turn(-3)
        self.assertShows("P119", "P1 - 10 goes round to P119")
        self.s.turn(1)
        self.assertShows("P  1")
        self.s.let_go("SHIFT")
        self.s.ms(100)
        self.assertEqual(self.s.settings()["patch"], 0)

    def test_value_loads_after_it_rests_80_ms(self):
        self.s.cmd("enc 4 15")
        self.s.ms(61)
        self.assertShows("P  2")
        self.assertEqual(self.s.settings()["patch"], 0)
        self.s.ms(90)
        self.assertEqual(self.s.settings()["patch"], 1)

    def test_a_button_acts_on_the_patch_turned_to(self):
        self.s.cmd("enc 4 15")
        self.s.ms(61)
        self.s.press("REC")  # before it rests
        self.assertEqual(self.s.settings()["patch"], 1)
        self.assertShows("P  2")
        self.s.press("REC")
        self.assertShows("donE")
        self.assertTrue(self.s.slot_is_ours(2))

    def test_program_change_cancels_a_pending_turn(self):
        self.s.cmd("enc 4 15")
        self.s.ms(61)
        self.s.midi(0xC0, 40)
        self.s.ms(200)
        self.assertEqual(self.s.settings()["patch"], 40)
        self.assertShows("P 41")

    def test_three_transitions_are_not_a_step(self):
        self.s.cmd("enc 3 15")
        self.s.ms(100)
        self.assertShows("P  1")


class Saving(PanelTest):
    def test_rec_shows_the_slot_and_blinks(self):
        self.s.press("REC")
        self.assertShows("P  1")
        lit = set()
        for _ in range(12):
            self.s.ms(50)
            lit.add(self.s.led("REC"))
        self.assertEqual(lit, {True, False}, "the LED blinks")

    def test_rec_digit_rec_saves_there_at_once(self):
        self.s.press("REC", "5")
        self.assertShows("P  5")
        self.s.press("REC")
        self.assertShows("donE")
        self.assertFalse(self.s.led("REC"))
        self.assertTrue(self.s.slot_is_ours(5))
        self.s.ms(1100)
        self.assertShows("P  5")
        self.assertEqual(self.s.settings()["patch"], 4)

    def test_value_picks_the_slot_without_loading(self):
        self.s.request(0x52, 0, 8, 0x40, 0)
        self.s.press("REC")
        self.s.turn(3)
        self.assertShows("P  4")
        self.assertEqual(self.s.params()["resonance"], 50, "the sound being saved stays")
        self.s.press("REC")
        self.assertShows("donE")
        self.assertTrue(self.s.slot_is_ours(4))

    def test_preset_cancels(self):
        self.s.press("REC", "5")
        self.s.press("PRESET")
        self.assertShows("P  1")
        self.assertFalse(self.s.led("REC"))
        self.assertEqual(self.s.settings()["preset-mode"], 1)
        self.assertFalse(self.s.slot_is_ours(5))

    def test_shift_preset_saves_to_the_current_slot(self):
        self.s.hold("SHIFT")
        self.s.press("PRESET")
        self.s.let_go("SHIFT")
        self.assertShows("donE")
        self.assertTrue(self.s.slot_is_ours(1))
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_shift_preset_in_manual_says_no(self):
        self.manual()
        self.s.hold("SHIFT")
        self.s.press("PRESET")
        self.assertShows("no")
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("PAnL")


class InitAndUndo(PanelTest):
    def test_perf_shows_init_then_the_slot(self):
        self.s.press("3")
        self.s.ms(3100)
        self.s.long_press("PERF")
        self.assertShows("InIt")
        self.s.ms(1500)
        self.assertShows("P  3")
        self.assertEqual(self.s.params()["cutoff"], 99)

    def test_undo_brings_the_sound_back_and_again_redoes(self):
        self.s.request(0x52, 0, 8, 0x60, 0)
        self.store_patch(2)
        resonance = self.s.params()["resonance"]
        self.assertEqual(resonance, 74)
        self.s.long_press("PERF")
        self.s.ms(1600)
        self.assertEqual(self.s.params()["resonance"], 0)
        self.s.hold("SHIFT")
        self.s.press("PERF")
        self.assertShows("Undo")
        self.assertEqual(self.s.params()["resonance"], resonance)
        self.s.press("PERF")
        self.assertEqual(self.s.params()["resonance"], 0)
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("P  2")

    def undo_from_the_host(self):
        reply = self.s.request(0x60)
        start = reply.index(pro800.SYSEX_HEADER + b"\x60") + len(pro800.SYSEX_HEADER) + 1
        return reply[start], reply[start + 1:reply.index(0xF7, start)]

    def test_undo_from_the_host_as_shift_perf(self):
        self.s.request(0x52, 0, 8, 0x60, 0)
        self.store_patch(2)
        self.s.request(0x59, 0, 1, *b"Pad")
        self.s.request(0x5C, 0, 1)  # with its name
        self.s.long_press("PERF")
        self.assertEqual(self.s.params()["resonance"], 0)
        self.assertEqual(self.undo_from_the_host(), (1, b"Pad"))
        self.assertEqual(self.s.params()["resonance"], 74)
        self.assertShows("Undo")
        self.assertEqual(self.undo_from_the_host(), (1, b"Init"), "again redoes, and the name says so")
        self.assertEqual(self.s.params()["resonance"], 0)

    def test_undo_from_the_host_with_nothing_to_undo(self):
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.undo_from_the_host()[0], 0)
        self.assertShows("no")

    def test_undo_with_nothing_to_undo(self):
        self.power_cycle()
        self.s.ms(1600)
        self.s.hold("SHIFT")
        self.s.press("PERF")
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_compare_while_rec_held(self):
        self.s.request(0x52, 0, 8, 0x40, 0)
        self.store_patch(6)
        self.s.request(0x52, 0, 8, 0x20, 0)
        edited = self.s.params()["resonance"]
        self.assertEqual(edited, 25)
        self.s.hold("SHIFT")
        self.s.hold("REC")
        self.assertShows("CoNP")
        self.assertEqual(self.s.params()["resonance"], 50, "the stored patch while held")
        self.s.let_go("REC")
        self.assertShows("EdIt")
        self.assertEqual(self.s.params()["resonance"], edited)
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("P  6")

    def test_compare_in_manual_says_no_and_goes_away(self):
        self.manual()
        self.s.hold("SHIFT")
        self.s.press("REC")
        self.assertShows("no")
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("PAnL")

    def test_compare_on_an_empty_slot_says_no_and_goes_away(self):
        self.s.hold("SHIFT")
        self.s.press("REC")
        self.assertShows("no")
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("P  1")


class PerfHold(PanelTest):
    """#34: Perf loads the init sound only when held (600 ms); a tap says HoLd and changes nothing."""

    def test_a_tap_says_hold_and_changes_nothing(self):
        self.s.param(0, "resonance", 12000)
        before = self.s.params_raw()["resonance"]
        self.s.press("PERF")
        self.assertShows("HoLd")
        self.assertEqual(self.s.params_raw()["resonance"], before)
        self.s.ms(900)
        self.assertShows("P  1")

    def test_held_it_loads_the_init_sound_at_600_ms(self):
        self.s.param(0, "resonance", 12000)
        self.s.hold("PERF")
        self.s.ms(200)
        self.assertEqual(self.s.params_raw()["resonance"], 12000, "not yet at 500 ms (params_raw waits 300)")
        self.s.ms(150)
        self.assertShows("InIt")
        self.s.let_go("PERF")
        self.assertShows("InIt", "letting go after the hold adds nothing")
        self.assertEqual(self.s.params_raw()["resonance"], 0)
        self.assertEqual(self.s.routes(0), INIT_ROUTES)


class InitPatch(PanelTest):
    """#35: the user's own init patch, a stored slot that Perf loads as a template instead of the built-in sound."""

    def template(self, slot=5):
        """A sound unlike the init sound in `slot`: resonance, one route, a 5-step sequence at 97 BPM; left loaded."""
        self.s.no_routes()
        self.s.param(0, "resonance", 12000)
        self.s.request(0x56, 0, VELOCITY, CUTOFF, (40 + 99) >> 7, (40 + 99) & 0x7F)
        self.s.seq_set(5, bpm=97, flags=0)
        self.store_patch(slot)
        return self.s.params_raw()["resonance"], self.s.routes(0)

    def test_the_setting_survives_a_power_cycle(self):
        self.s.setting("init-patch", 5)
        self.assertEqual(self.s.settings()["init-patch"], 5)
        self.s.ms(1100)
        self.power_cycle()
        self.assertEqual(self.s.settings()["init-patch"], 5)

    def test_perf_loads_it_as_a_template(self):
        resonance, routes = self.template(5)
        self.s.setting("init-patch", 5)
        self.s.press("3")
        self.s.ms(3100)  # the empty P3: the built-in init sound there
        self.assertEqual(self.s.params_raw()["resonance"], 0)
        self.s.long_press("PERF")
        self.assertShows("InIt.", "the user's init: a dot")
        self.assertEqual((self.s.params_raw()["resonance"], self.s.routes(0)), (resonance, routes))
        header = self.s.seq_header()
        self.assertEqual((header["length"], header["bpm"]), (5, 97), "the template's sequence, not P3's")
        self.assertEqual(self.s.settings()["patch"], 2, "still P3: Rec saves there, not over the template")
        self.s.ms(1600)
        self.assertShows("P  3")
        self.s.press("REC", "REC")
        self.s.ms(1100)
        self.s.press("1")
        self.s.ms(3100)
        self.s.press("3")
        self.s.ms(3100)
        self.assertEqual(self.s.params_raw()["resonance"], resonance, "P3 has it now")

    def test_undo_brings_back_the_sound_before(self):
        self.template(5)
        self.s.setting("init-patch", 5)
        self.s.press("3")
        self.s.ms(3100)
        self.s.long_press("PERF")
        self.s.hold("SHIFT")
        self.s.press("PERF")
        self.s.let_go("SHIFT")
        self.assertShows("Undo")
        self.assertEqual(self.s.params_raw()["resonance"], 0)

    def test_an_empty_slot_or_none_gives_the_built_in_sound(self):
        self.s.param(0, "resonance", 12000)
        self.s.setting("init-patch", 7)  # P7 is empty
        self.s.long_press("PERF")
        self.assertShows("InIt", "the built-in sound: no dot")
        self.assertEqual((self.s.params_raw()["resonance"], self.s.routes(0)), (0, INIT_ROUTES))

    def test_a_format_clears_it(self):
        self.s.setting("init-patch", 5)
        self.s.request(0x5B)
        self.s.ms(100)
        self.assertEqual(self.s.settings()["init-patch"], 0)

    def test_the_host_5f_stays_built_in_and_5f_1_loads_what_perf_does(self):
        resonance, _ = self.template(5)
        self.s.setting("init-patch", 5)
        self.s.request(0x5F)
        self.assertEqual(self.s.params_raw()["resonance"], 0, "5F: the built-in sound, which presets build on")
        self.assertEqual(self.s.reply(0x5F, 1)[:1], bytes([1]))
        self.assertEqual(self.s.params_raw()["resonance"], resonance)
        self.s.setting("init-patch", 0)
        self.assertEqual(self.s.reply(0x5F, 1)[:1], bytes([0]))
        self.assertEqual(self.s.params_raw()["resonance"], 0)

    def test_past_p128_is_refused(self):
        self.s.setting("init-patch", 5)
        self.s.request(0x7D, 95, 1, 1)  # 129
        self.assertEqual(self.s.settings()["init-patch"], 5)

    def test_a_block_written_by_0_9_5_reads_as_none(self):
        """0.9.5 writes the global block's first 20 bytes: its own, a downgrade's or leftovers fail the new checksum."""
        self.s.setting("init-patch", 5)
        self.s.setting("midi-channel", 3)
        self.s.ms(1100)
        self.s.close()
        with open(self.path, "r+b") as f:
            f.seek(0x1F400 + 28)
            f.write(bytes(4))
        self.s = Synth(self.path)
        self.s.ms(1600)
        settings = self.s.settings()
        self.assertEqual((settings["init-patch"], settings["midi-channel"]), (0, 3), "the rest of the block as it was")

    def test_rec_perf_with_no_digits_takes_the_current_slot(self):
        self.s.press("REC", "PERF")
        self.assertShows("InIt.")
        self.assertEqual(self.s.settings()["init-patch"], 1)
        self.assertTrue(self.s.slot_is_ours(1), "really saved")


class SystemMenu(PanelTest):
    """#2, #34: hold Settings (or Shift + Settings): the system-wide settings, on keys 1-7, the Settings LED blinking."""

    def open(self):
        self.s.long_press("SETTINGS")
        self.assertShows("SYS")

    def page(self, key, name):
        self.s.press(key)
        self.assertShows(name)
        self.s.ms(800)

    def test_a_tap_is_the_sound_menu_a_hold_the_system_menu(self):
        self.s.press("SETTINGS")
        self.assertShows("SEt")
        self.s.press("SETTINGS")
        self.assertShows("P  1")
        self.open()
        lit = set()
        for _ in range(12):
            self.s.ms(50)
            lit.add(self.s.led("SETTINGS"))
        self.assertEqual(lit, {True, False}, "it blinks")
        self.s.press("SETTINGS")
        self.assertShows("P  1")
        self.s.ms(300)
        self.assertFalse(self.s.led("SETTINGS"))

    def test_shift_settings_opens_it(self):
        self.s.hold("SHIFT")
        self.s.press("SETTINGS")
        self.s.let_go("SHIFT")
        self.assertShows("SYS")

    def test_the_midi_channel(self):
        self.open()
        self.page("1", "CHnL")
        self.assertShows("ALL")
        self.s.turn(1)
        self.assertShows("   1")
        self.assertEqual(self.s.settings()["midi-channel"], 1)
        self.s.ms(1100)
        self.power_cycle()
        self.assertEqual(self.s.settings()["midi-channel"], 1)

    def test_pickup_the_ccs_the_clock(self):
        self.open()
        self.page("2", "P.UP")
        self.assertShows("on")
        self.s.turn(1)
        self.assertShows("OFF")
        self.page("3", "CC.A")
        self.assertShows("  11")
        self.s.turn(1)
        self.page("4", "CC.b")
        self.assertShows("   2")
        self.page("5", "CLoC")
        self.assertShows("nIdI")
        self.s.turn(1)
        self.assertShows("Int")
        settings = self.s.settings()
        self.assertEqual((settings["pickup"], settings["cc-a"], settings["clock"]), (0, 12, 1))

    def test_the_tempo_and_shift_for_tens(self):
        self.open()
        self.page("6", "tEnP")
        self.assertShows(" 120")
        self.s.turn(1)
        self.assertShows(" 121")
        self.s.hold("SHIFT")
        self.s.turn(1)
        self.s.let_go("SHIFT")
        self.assertShows(" 131")
        self.assertEqual(self.s.settings()["bpm"], 131)

    def test_the_init_patch(self):
        self.open()
        self.page("7", "InIt")
        self.assertShows("nonE")
        self.s.turn(1)
        self.assertShows("P  1")
        self.s.hold("SHIFT")
        self.s.turn(1)
        self.s.let_go("SHIFT")
        self.assertShows("P 11")
        self.assertEqual(self.s.settings()["init-patch"], 11)
        self.s.turn(-2)
        self.assertShows("P  9")
        self.s.hold("SHIFT")
        self.s.turn(-1)
        self.s.let_go("SHIFT")
        self.assertShows("P128", "round past none")
        self.s.turn(1)
        self.assertShows("nonE")
        self.assertEqual(self.s.settings()["init-patch"], 0)

    def test_keys_9_0_and_perf_and_rec_do_nothing(self):
        self.open()  # (key 8 is MIDI out from 0.10.0: MidiOut)
        for key in ("9", "0", "PERF", "REC"):
            self.s.press(key)
            self.assertShows("SYS", key)
        self.assertEqual(self.s.settings()["init-patch"], 0)

    def test_shift_settings_closes_an_open_menu(self):
        """With Shift held (or latched), Settings still closes a menu: it opens the system menu only from none."""
        self.s.press("SETTINGS")
        self.s.hold("SHIFT")
        self.s.press("SETTINGS")
        self.s.let_go("SHIFT")
        self.assertShows("P  1")

    def test_shift_settings_from_mod_assign_ends_it(self):
        self.s.press("ASSIGN")
        self.assertShows("SrC")
        self.s.hold("SHIFT")
        self.s.press("SETTINGS")
        self.s.let_go("SHIFT")
        self.assertShows("SYS")
        self.s.press("SETTINGS")
        self.s.ms(600)
        self.assertShows("P  1", "MOD ASSIGN didn't come back")
        self.assertFalse(self.s.led("ARP_ASSIGN"))

    def test_perf_in_a_save_with_a_menu_open_does_nothing(self):
        self.s.press("REC", "5", "SETTINGS", "PERF")
        self.assertEqual(self.s.settings()["init-patch"], 0, "as Rec does nothing there")

    def test_shift_digit_goes_to_the_sound_menus_page(self):
        self.open()
        self.s.hold("SHIFT")
        self.s.press("3")
        self.s.let_go("SHIFT")
        self.s.ms(300)
        self.assertShows("OFF", "the layer page")
        lit = set()
        for _ in range(12):
            self.s.ms(50)
            lit.add(self.s.led("SETTINGS"))
        self.assertEqual(lit, {True}, "steady: the sound menu")


class ReleaseVelocity(PanelTest):
    """#8: release velocity as source 29. A route from it does nothing while the voice's key is down, only after the key
    is up, by its lift (rv x depth); 64 for releases no key made, and for a note-on at velocity 0."""

    RVEL = None

    def setUp(self):
        super().setUp()
        self.RVEL = pro800.SOURCE_NAMES.index("release-velocity")
        self.s.no_routes()
        self.s.setting("keytrack", 0)
        self.s.param(0, "fil-env-amt", 8192)   # no envelope on the cutoff
        self.s.param(0, "cutoff", 6000)
        self.s.param(0, "amp-rel", 14000)      # a long tail to look at
        self.s.param(0, "fil-rel", 14000)

    def route(self, dest, depth, source=None):
        d = depth + 99
        self.s.request(0x56, 0, self.RVEL if source is None else source, dest, d >> 7, d & 0x7F)

    def cutoff_after(self, release=None, note_on_zero=False):
        """The cutoff CV of the voice a note took, while held and 20 ms after its release."""
        self.s.note(60)
        self.s.ms(20)
        v = self.s.gated()[0]
        held = self.s.cvs()[CV_FIL1 + v]
        if note_on_zero:
            self.s.midi(0x90, 60, 0)
        else:
            self.s.midi(0x80, 60, release)
        self.s.ms(20)
        after = self.s.cvs()[CV_FIL1 + v]
        self.s.ms(3000)
        return held, after

    def test_nothing_while_held_the_lift_after(self):
        plain_held, plain_after = self.cutoff_after(127)
        self.route(CUTOFF, 40)
        held, after = self.cutoff_after(127)
        self.assertEqual(held, plain_held, "held: the route adds nothing")
        self.assertGreater(after - plain_after, 3000, "after a lift at 127: up by 40 x 96/99 semitones' worth")
        held0, after0 = self.cutoff_after(0)
        self.assertLess(abs(after0 - plain_after), 200, "a lift at 0 adds nothing")
        _, after64 = self.cutoff_after(64)
        self.assertAlmostEqual(after64 - plain_after, (after - plain_after) * 64 / 127, delta=300)

    def test_a_note_on_at_velocity_0_is_64(self):
        self.route(CUTOFF, 40)
        _, after64 = self.cutoff_after(64)
        _, after_zero = self.cutoff_after(note_on_zero=True)
        self.assertLess(abs(after_zero - after64), 100)

    def test_a_route_into_the_vca_doesnt_silence_a_held_note(self):
        self.s.note(60)
        self.s.ms(50)
        v = self.s.gated()[0]
        plain = self.s.cvs()[CV_AMP1 + v]
        self.route(VCA, 99)
        self.s.ms(50)
        self.assertEqual(self.s.cvs()[CV_AMP1 + v], plain, "held: full, as without the route")
        self.s.midi(0x80, 60, 0)
        self.s.ms(20)
        self.assertLess(self.s.cvs()[CV_AMP1 + v], plain // 4, "a lift at 0: the tail's gone")

    def pedal_lift(self, release):
        """The cutoff's rise at pedal-up, after a key lifted at `release` while the pedal held it."""
        self.s.midi(0xB0, 64, 127)
        self.s.note(60)
        self.s.ms(20)
        v = self.s.gated()[0]
        held = self.s.cvs()[CV_FIL1 + v]
        self.s.midi(0x80, 60, release)
        self.s.ms(20)
        self.assertEqual(self.s.gated(), [v], "the pedal holds it")
        self.assertEqual(self.s.cvs()[CV_FIL1 + v], held)
        self.s.midi(0xB0, 64, 0)
        self.s.ms(20)
        rise = self.s.cvs()[CV_FIL1 + v] - held
        self.s.ms(3000)
        return rise

    def test_the_pedal_keeps_the_lift_for_its_release(self):
        self.route(CUTOFF, 40)
        loud, soft = self.pedal_lift(127), self.pedal_lift(10)
        self.assertGreater(loud, 8000, "pedal up: the key's lift at 127 (64 would give about 5000)")
        self.assertLess(soft, 1500, "pedal up: the key's lift at 10")
        self.assertAlmostEqual(soft, loud * 10 / 127, delta=300)

    def test_as_a_via_it_is_x1_while_held(self):
        fixed = pro800.SOURCE_NAMES.index("fixed")
        plain_held, plain_after = self.cutoff_after(0)
        self.route(CUTOFF, 40, source=fixed)
        self.s.request(0x62, 0, fixed, CUTOFF, (1000 + self.RVEL) >> 7, (1000 + self.RVEL) & 0x7F)
        held, after = self.cutoff_after(0)
        self.assertGreater(held - plain_held, 3000, "held: fixed x 1")
        self.assertLess(abs(after - plain_after), 200, "lifted at 0: fixed x 0")

    def test_the_probe_shows_each_voices_release_velocity(self):
        self.s.note(60)
        self.s.ms(20)
        v = self.s.gated()[0]
        self.s.midi(0x80, 60, 90)
        self.s.ms(20)
        d = self.s.reply(0x55)
        self.assertEqual(list(d[93 + 3 * v:96 + 3 * v]), [60, 60, 90], "key, note, release velocity")

    def test_a_route_from_it_is_saved(self):
        self.route(CUTOFF, 40)
        self.store_patch(5)
        self.s.press("1")
        self.s.ms(3100)
        self.s.press("5")
        self.s.ms(3100)
        self.assertIn((self.RVEL, CUTOFF, 40), self.s.routes(0))


class InitRoutes(PanelTest):
    """The init sound's two velocity routes, on both layers, wherever the init sound comes from (MANUAL, Init)."""

    def assertInit(self, why=None):
        self.assertEqual(self.s.routes(0), INIT_ROUTES, why)
        self.assertEqual(self.s.routes(1), INIT_ROUTES, why)

    def assertNone(self, why=None):
        self.assertEqual(self.s.routes(0), [], why)
        self.assertEqual(self.s.routes(1), [], why)

    def test_the_first_boot_on_formatted_memory(self):
        self.assertEqual(self.s.settings()["patch"], 0)
        self.assertInit("P1 is empty")

    def test_a_format_and_the_boot_after_it(self):
        self.s.no_routes()
        self.store_patch(4)
        self.s.request(0x5B)
        self.power_cycle()
        self.s.ms(1600)
        self.assertInit("the last patch's slot is empty now")

    def test_perf(self):
        self.s.no_routes()
        self.s.long_press("PERF")
        self.assertShows("InIt")
        self.assertInit()

    def test_an_empty_slot(self):
        self.s.no_routes()
        self.s.press("7")
        self.s.ms(3100)
        self.assertShows("P  7")
        self.assertInit()

    def test_an_empty_slot_from_the_host_and_by_program_change(self):
        self.s.no_routes()
        self.s.request(0x5C, 0, 6)
        self.assertInit("5C")
        self.s.no_routes()
        self.s.midi(0xC0, 8)
        self.s.ms(5)
        self.assertEqual(self.s.settings()["patch"], 8)
        self.assertInit("program change")

    def test_5f(self):
        self.s.no_routes()
        self.assertIn(pro800.SYSEX_HEADER + bytes([0x7F, 0x5F]), self.s.request(0x5F))
        self.assertInit()

    def test_a_save_keeps_them_as_ordinary_routes_and_a_reload_brings_them_back(self):
        self.store_patch(9)
        self.assertEqual(self.s.memory()[8 * 768 + 240:8 * 768 + 246], bytes(x for r in INIT_ROUTES for x in r), "store.c: routes at 240")
        self.s.press("4")
        self.s.ms(3100)
        self.s.no_routes()
        self.s.press("9")
        self.s.ms(3100)
        self.assertInit()
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 8)
        self.assertInit()

    def test_they_take_two_of_each_layers_sixteen_slots(self):
        for layer in (0, 1):
            for dest in range(4, 18):
                self.assertTrue(self.s.request(0x56, layer, 1, dest, 0, 99 + 5))
            self.assertEqual(len(self.s.routes(layer)), 16)
            self.assertNotIn(bytes([0x7F, 0x56]), self.s.request(0x56, layer, 1, 18, 0, 99 + 5), "the table is full")
            self.assertEqual(self.s.routes(layer)[:2], INIT_ROUTES)

    # what they do, measured
    def voice(self, velocity, note=60):
        """The gated voice of a note, its VCA gain and its filter's CV in semitones (the note is let go again)."""
        self.s.note(note, velocity)
        self.s.ms(100)
        voices = self.s.gated()
        self.assertEqual(len(voices), 1)
        cvs = self.s.cvs()
        self.s.note_off(note)
        self.s.ms(1000)
        return (cvs[CV_AMP1 + voices[0]] / 65535 - 0.07) / 0.93, cvs[CV_FIL1 + voices[0]] / CV_PER_SEMITONE  # synth.c: VCA_THRESHOLD

    def test_velocity_on_the_vca(self):
        self.assertAlmostEqual(self.voice(127)[0], 1, delta=0.002)
        self.assertAlmostEqual(self.voice(64)[0], 1 - (1 - 64 / 127) * 70 / 99, delta=0.002)
        self.assertAlmostEqual(self.voice(64)[0], 0.65, delta=0.005, msg="MANUAL's number")
        self.s.no_routes()
        self.assertAlmostEqual(self.voice(64)[0], 1, delta=0.002, msg="and it's the route that does it")

    def test_velocity_on_the_cutoff_with_the_knob_down(self):
        self.s.param(0, "cutoff", 0x2000)
        self.s.ms(400)
        high, low = self.voice(127)[1], self.voice(64)[1]
        self.s.no_routes()
        knob = self.voice(127)[1]
        self.assertAlmostEqual(high - knob, 20 / 99 * 96, delta=0.05, msg="about 19 semitones above the knob")
        self.assertAlmostEqual(low - knob, 64 / 127 * 20 / 99 * 96, delta=0.05)
        self.assertAlmostEqual(high - low, (127 - 64) / 127 * 20 / 99 * 96, delta=0.05)

    def test_layer_2_plays_them_too(self):
        self.s.setting("layers", 1)  # split: from the split note up is layer 2
        self.s.param(1, "cutoff", 0x2000)
        self.s.ms(400)
        gain, cv = self.voice(64, note=72)
        self.assertAlmostEqual(gain, 1 - (1 - 64 / 127) * 70 / 99, delta=0.002)
        self.s.request(0x56, 0, VELOCITY, VCA, 0, 99)
        self.s.request(0x56, 0, VELOCITY, CUTOFF, 0, 99)
        self.assertEqual(self.voice(64, note=72), (gain, cv), "layer 1's routes aren't what it plays")
        self.s.no_routes()
        self.assertAlmostEqual(cv - self.voice(64, note=72)[1], 64 / 127 * 20 / 99 * 96, delta=0.05)

    def test_velocity_moves_a_low_notes_cutoff_with_the_knob_at_its_maximum(self):
        """Full keyboard tracking takes a low note's cutoff well under the knob's top, so the route is in reach there without
        the knob coming down: the CVs, not the sound. MANUAL, Init."""
        reach = 126 / 127 * 20 / 99 * 96
        top = {}
        for note in (24, 36, 60, 96):
            with self.subTest(note=note):
                top[note], soft = self.voice(127, note)[1], self.voice(1, note)[1]
                self.assertAlmostEqual(top[note] - soft, reach, delta=0.05, msg="the whole of it, no end stop")
        self.assertAlmostEqual(top[60] - top[36], 24, delta=0.1, msg="full keyboard tracking: 2 octaves down, so note 36 at 127 is still under note 60")
        self.s.no_routes()
        self.assertEqual(self.voice(127, 36)[1], self.voice(1, 36)[1], "and it's the route that does it")

    def test_with_layers_off_taking_them_off_leaves_layer_2s(self):
        """Per layer, and layer 2 doesn't play with layers off: what's left there is saved, and plays once the patch has two
        layers. MANUAL, Init."""
        self.s.request(0x56, 0, VELOCITY, VCA, 0, 99)
        self.s.request(0x56, 0, VELOCITY, CUTOFF, 0, 99)
        self.assertEqual(self.s.settings()["layers"], 0, "layers off")
        self.assertAlmostEqual(self.voice(64)[0], 1, delta=0.002, msg="layer 1's are gone, and it's layer 1 that plays")
        self.store_patch(9)
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 8)
        self.assertEqual((self.s.routes(0), self.s.routes(1)), ([], INIT_ROUTES))
        self.s.setting("layers", 1)  # split: from the split note up is layer 2
        self.assertAlmostEqual(self.voice(64, note=40)[0], 1, delta=0.002)
        self.assertAlmostEqual(self.voice(64, note=72)[0], 0.65, delta=0.005)

    def test_the_manual_and_the_knobs_title_say_what_happens(self):
        manual = source("MANUAL.md")
        init = manual[manual.index("- **Init:**"):manual.index("- **Names, export")]
        self.assertNotIn("heard once", init, "low notes hear it with the knob at the top")
        self.assertIn("keyboard tracking", init)
        self.assertIn("layers off", init)
        self.assertIn("older", init, "a page or installer from before these routes")
        page = source("tools", "web", "index.html")
        knob = page[page.index('velocityKnob(DEST_CUTOFF, "Velocity'):page.index('velocityKnob(DEST_VCA, "Velocity')]
        self.assertNotIn("once the Cutoff knob is down", knob)
        self.assertIn("low note", knob)

    # a patch from before
    def test_a_saved_patch_gets_none_added(self):
        self.s.no_routes()
        self.store_patch(5)
        self.s.request(0x56, 1, 1, 13, 0, 99 - 20)
        self.store_patch(6)
        self.s.long_press("PERF")
        self.assertInit()
        self.s.press("5")
        self.s.ms(3100)
        self.assertNone()
        self.s.press("6")
        self.s.ms(3100)
        self.assertEqual((self.s.routes(0), self.s.routes(1)), ([], [(1, 13, -20)]))
        self.s.press("5")
        self.s.ms(3100)
        self.s.ms(1100)
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 4)
        self.assertNone("and the boot's load adds none either")
        self.s.request(0x5C, 0, 5)
        self.assertEqual((self.s.routes(0), self.s.routes(1)), ([], [(1, 13, -20)]))

    def test_undo_brings_back_the_sound_before(self):
        self.s.no_routes()
        self.store_patch(5)
        self.s.long_press("PERF")
        self.assertInit()
        self.s.hold("SHIFT")
        self.s.press("PERF")
        self.assertShows("Undo")
        self.assertNone("the routes before")
        self.s.press("PERF")
        self.assertInit("again redoes")
        self.s.let_go("SHIFT")

    # on the panel
    def test_mod_assign_lists_them(self):
        for layer, button in ((0, "SEQ1"), (1, "SEQ2")):
            with self.subTest(layer=layer + 1):
                self.s.press(button)
                self.s.ms(1100)
                self.s.hold("ASSIGN")
                self.s.ms(650)
                self.s.let_go("ASSIGN")
                self.assertShows("nod1", "a hold browses: there are routes")
                self.s.history()
                self.s.ms(800 + 600 * 4)
                self.assertSaw(["VEL", "VCA", "  70"])
                self.s.turn(1)
                self.assertShows("nod2")
                self.s.history()
                self.s.ms(800 + 600 * 4)
                self.assertSaw(["VEL", "CUt", "  20"])
                self.s.turn(1)
                self.assertShows("nod1", "two routes: round to the first")
                self.s.hold("ASSIGN")
                self.s.ms(650)
                self.s.let_go("ASSIGN")
                self.assertShows("P  1")

    def test_touching_their_destinations_in_mod_assign_shows_their_depths(self):
        for pot, depth in (("AMP_SUS", "  70"), ("CUTOFF", "  20")):
            with self.subTest(pot=pot):
                self.s.press("ASSIGN")
                self.s.press("4")
                self.s.pot(pot, 0.3)
                self.s.pot(pot, 0.4)
                self.s.ms(800)
                self.assertShows(depth)
                self.s.press("ASSIGN")
                self.s.ms(100)
        self.assertInit("looking changes nothing")

    def test_turning_one_down_in_mod_assign_removes_it(self):
        self.s.press("ASSIGN")
        self.s.press("4")
        self.s.pot("AMP_SUS", 0.3)
        self.s.pot("AMP_SUS", 0.4)
        self.s.ms(800)
        for _ in range(35):
            self.s.turn(-1)
        self.assertShows("   0")
        self.s.press("ASSIGN")
        self.s.rest()
        self.assertEqual(self.s.routes(0), INIT_ROUTES[1:], "the edit layer's")
        self.assertEqual(self.s.routes(1), INIT_ROUTES)

    def test_manual_mode_changes_no_routes(self):
        """Manual mode makes the panel live; the mod matrix isn't on the panel, so the sound keeps the routes it had."""
        self.manual()
        self.assertInit("from the init sound on P1")
        self.s.press("PRESET")
        self.s.ms(100)
        self.s.no_routes()
        self.manual()
        self.assertNone("from a sound without any")


class NoInitRoutesOnStockMemory(PanelTest):
    """Stock-format memory starts on the panel: no patch, no init sound, no routes."""
    MEMORY = "stock"

    def test_none_until_the_init_sound_is_asked_for(self):
        self.assertShows("PAnL")
        self.assertEqual((self.s.routes(0), self.s.routes(1)), ([], []))
        self.s.press("PRESET")
        self.assertShows("nonE")
        self.assertEqual((self.s.routes(0), self.s.routes(1)), ([], []), "the slot is stock's: the sound stays")
        self.s.long_press("PERF")
        self.assertEqual((self.s.routes(0), self.s.routes(1)), (INIT_ROUTES, INIT_ROUTES))


class NoInitRoutesOnBlankMemory(PanelTest):
    """Nor does memory never formatted, until an empty slot is loaded, which is the init sound."""
    MEMORY = "blank"

    def test_none_until_an_empty_slot_loads(self):
        self.assertShows("PAnL")
        self.assertEqual((self.s.routes(0), self.s.routes(1)), ([], []))
        self.s.press("PRESET")
        self.assertTrue(self.s.led("PRESET"))
        self.assertEqual((self.s.routes(0), self.s.routes(1)), (INIT_ROUTES, INIT_ROUTES))


class DamagedSlot(PanelTest):
    def test_shows_none_and_keeps_the_sound(self):
        self.store_patch(4)
        self.s.press("1")
        self.s.ms(3100)
        self.s.close()
        with open(self.path, "r+b") as f:
            f.seek(3 * 768 + 100)
            f.write(b"\xAA\xAA\xAA")
        self.s = Synth(self.path)
        self.s.ms(1600)
        self.s.press("4")
        self.s.ms(3100)
        self.s.ms(10)
        self.assertSaw(["nonE"])
        self.s.ms(1100)
        self.assertEqual(self.s.settings()["patch"], 0)
        self.assertShows("P  1")


class KnobValues(PanelTest):
    """Manual mode, so nothing is in pickup: what the table under 'The panel at a glance' says."""

    def setUp(self):
        super().setUp()
        self.manual()

    def value(self, pot, fraction):
        self.s.pot(pot, 0.5 if fraction != 0.5 else 0.25)
        self.s.pot(pot, fraction)
        return self.s.digits()

    def assertKnob(self, pot, fraction, text):
        shown = self.value(pot, fraction)
        self.assertEqual(shown, segments_of(text), f"{pot} at {fraction}: shows {readable(shown)!r}, expected {text!r}")

    def test_plain_knobs_read_0_to_99(self):
        for pot in ("A_LEVEL", "B_LEVEL", "NOISE", "RESONANCE", "A_PW", "B_PW", "PMOD_OSC_B", "LFO_AMOUNT"):
            self.assertKnob(pot, 0.0, "   0")
            self.assertKnob(pot, 1.0, "  99")

    def test_cutoff_runs_16_hz_to_16_7_khz(self):
        self.assertKnob("CUTOFF", 0.0, " 16.4")
        self.assertKnob("CUTOFF", 1.0, " 16.7")

    def test_cutoff_has_a_point_from_1_khz(self):
        self.value("CUTOFF", 0.7)
        self.assertRegex(self.s.shows(), r"^ ?\d+\.\d+$")

    def test_fine_shows_cents(self):
        self.assertKnob("FINE", 0.0, "-100")
        self.assertKnob("FINE", 1.0, " 100")
        self.assertEqual(self.value("FINE", 0.5), segments_of("   0"))

    def test_m_tune_is_macro_1(self):
        self.assertKnob("MASTER_TUNE", 0.0, " -99")
        self.assertKnob("MASTER_TUNE", 1.0, "  99")
        self.assertEqual(self.value("MASTER_TUNE", 0.5), segments_of("   0"))

    def test_fil_env_amt_is_squared(self):
        self.assertKnob("FIL_ENV_AMT", 0.5, "   0")
        self.assertKnob("FIL_ENV_AMT", 0.75, "  18")
        self.assertKnob("FIL_ENV_AMT", 0.625, "  4.5")
        self.assertKnob("FIL_ENV_AMT", 0.25, " -18")
        self.assertKnob("FIL_ENV_AMT", 1.0, "  72")

    def test_pmod_fil_env_is_squared(self):
        self.assertKnob("PMOD_FIL_ENV", 0.25, " 3.00")
        self.assertKnob("PMOD_FIL_ENV", 0.5, " 12.0")
        self.assertKnob("PMOD_FIL_ENV", 1.0, " 48.0")

    def test_glide_is_off_at_the_bottom(self):
        self.assertKnob("GLIDE", 0.0, "OFF")
        self.assertKnob("GLIDE", 1.0, " 2.05")

    def test_lfo_freq_in_hz(self):
        self.assertKnob("LFO_FREQ", 0.0, " 0.05")
        self.assertKnob("LFO_FREQ", 1.0, " 51.2")

    def test_freq_a_in_semitones(self):
        self.assertKnob("FREQ_A", 0.5, "   0")
        self.assertKnob("FREQ_A", 0.0, " -36")
        self.assertKnob("FREQ_A", 1.0, "  36")

    def test_envelope_times(self):
        for pot in ("AMP_ATT", "AMP_DEC", "AMP_REL", "FIL_ATT", "FIL_DEC", "FIL_REL"):
            self.value(pot, 0.0)
            low = self.s.shows()
            self.value(pot, 1.0)
            high = self.s.shows()
            self.assertRegex(low, r"^ +\d+$", f"{pot} at the bottom in ms")
            self.assertRegex(high, r"\.", f"{pot} at the top in seconds")

    def test_sustains_read_0_to_99(self):
        for pot in ("AMP_SUS", "FIL_SUS"):
            self.assertKnob(pot, 0.0, "   0")
            self.assertKnob(pot, 1.0, "  99")

    def test_m_vol_keeps_the_display(self):
        self.s.ms(1100)
        self.s.pot("MASTER_VOL", 0.2)
        # M VOL shows like any other knob: 0-99
        self.assertShows("  20")

    def test_a_small_wobble_shows_nothing(self):
        self.s.pot("RESONANCE", 0.5)
        self.s.ms(1100)
        self.assertShows("PAnL")
        self.s.cmd(f"pot {POT['RESONANCE']} {round(0.5 * 4095) + 10}")  # under the hysteresis
        self.s.ms(100)
        self.assertShows("PAnL")


class Pickup(PanelTest):
    def setUp(self):
        super().setUp()
        self.manual()
        self.s.pot("RESONANCE", 0.5)
        self.s.ms(1100)
        self.s.press("REC", "3", "REC")
        self.s.ms(1100)
        self.s.press("1")
        self.s.ms(3100)
        self.s.pot("RESONANCE", 0.0)  # while on the empty P1: 0 from init anyway
        self.s.press("3")
        self.s.ms(3100)
        self.assertShows("P  3")

    def test_a_dot_until_the_knob_reaches_the_stored_value(self):
        self.s.pot("RESONANCE", 0.2)
        self.assertShows("  50.", "the stored value, with a dot")
        self.assertEqual(self.s.params()["resonance"], 50)
        self.s.pot("RESONANCE", 0.4)
        self.assertShows("  50.")
        self.s.pot("RESONANCE", 0.6)
        self.assertEqual(self.s.params()["resonance"], 59, "past the stored value: taken over")
        self.s.pot("RESONANCE", 0.7)
        self.assertShows("  69", "no dot once it has taken over")

    def test_the_value_goes_back_to_the_slot(self):
        self.s.pot("RESONANCE", 0.2)
        self.s.ms(1550)
        self.assertShows("P  3")

    def test_pickup_off_jumps_on_the_first_move(self):
        self.s.hold("SHIFT")
        self.s.pot("PMOD_FIL_ENV", 0.9)
        self.s.pot("PMOD_FIL_ENV", 0.1)
        self.assertShows("P.UP")
        self.s.ms(600)
        self.assertShows("OFF")
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.settings()["pickup"], 0)
        self.s.press("3")
        self.s.ms(3100)
        self.s.pot("RESONANCE", 0.2)
        self.assertEqual(self.s.params()["resonance"], 20)
        self.s.pot("RESONANCE", 0.3)
        self.assertShows("  30", "no dot once it has taken over")

    def test_the_dot_stays_on_a_patch_loaded_by_program_change(self):
        self.s.pot("RESONANCE", 0.6)  # taken over
        self.s.midi(0xC0, 2)
        self.s.ms(5)
        self.s.pot("RESONANCE", 0.3)
        self.assertShows("  50.")


class PickupNeedsATurn(PanelTest):
    """0.10.0 (#39's first part): a waiting knob takes over only once it's been turned, however close it rests. On
    0.9.5 one within 1.5 % of its value took over on the next pass, untouched."""

    def test_a_value_from_the_host_next_to_a_resting_knob_stays(self):
        self.manual()
        self.s.pot("RESONANCE", 0.5)
        self.s.ms(1100)
        self.s.param(0, "resonance", 8355)  # 0.51: inside the window of the knob at 0.5
        self.s.ms(2000)
        self.assertEqual(self.s.params_raw()["resonance"], 8355, "not taken back by the knob")
        self.s.pot("RESONANCE", 0.6)
        self.assertShows("  59", "rested on it: the first turn takes it, with no dot")
        self.assertEqual(self.s.params()["resonance"], 59)

    def test_a_semitone_patch_loaded_one_step_off_keeps_its_tuning(self):
        seven = round(43 / 72 * 16383)  # FREQ A at +7 semitones
        self.s.param(0, "freq-a", seven)
        self.s.press("REC", "3", "REC")
        self.s.ms(1100)
        self.s.press("1")
        self.s.ms(3100)
        self.s.pot("FREQ_A", 42 / 72)  # +6 on the empty P1
        self.s.press("3")
        self.s.ms(3100)
        self.assertShows("P  3")
        self.s.ms(2000)
        self.assertEqual(self.s.params_raw()["freq-a"], seven, "a step is less than 1.5 %: 0.9.5 took the knob's +6")
        self.s.pot("FREQ_A", 41 / 72)  # turned a step away, to +5: a step's window (half a step) doesn't take it
        self.assertEqual(self.s.params_raw()["freq-a"], seven, "it waits until the knob reaches +7")
        self.s.pot("FREQ_A", 43 / 72)
        self.assertEqual(self.s.params_raw()["freq-a"], seven, "onto it: the knob has it, at +7")
        self.s.pot("FREQ_A", 44 / 72)
        self.assertEqual(round(self.s.params_raw()["freq-a"] * 72 / 16383), 44)

    def test_a_semitone_knob_short_of_its_end_takes_a_value_stored_at_the_end(self):
        """A pot that stops at 4070 can't reach +36's step, so it can't sweep past: turned to its end, it takes over."""
        self.manual()
        self.s.param(0, "freq-a", 16383)  # +36
        self.s.pot("FREQ_A", 0.8)
        self.s.pot("FREQ_A", 71 / 72)
        self.s.pot("FREQ_A", 4070 / 4095)  # as far as it goes: still step 71 (the step's hysteresis)
        self.assertEqual(round(self.s.params_raw()["freq-a"] * 72 / 16383), 71, "the knob has it")

    def test_a_knob_whose_steps_change_while_it_waits_hasnt_been_turned(self):
        self.manual()
        self.s.pot("LFO_FREQ", 0.3)
        self.s.ms(100)
        near = round(0.305 * 16383)  # within the window of the knob
        self.s.param(0, "lfo-freq", near)
        self.s.ms(100)
        self.s.setting("lfo-sync", 1)  # now 14 divisions: the knob reads as a step, which isn't a move
        self.s.ms(500)
        self.assertEqual(self.s.params_raw()["lfo-freq"], near)

    def test_a_knob_resting_by_its_value_after_a_load_waits_to_be_turned(self):
        self.manual()
        self.s.pot("RESONANCE", 0.5)
        self.s.ms(1100)
        self.s.press("REC", "3", "REC")
        self.s.ms(1100)
        stored = self.s.params_raw()["resonance"]
        self.s.press("1")
        self.s.ms(3100)
        self.s.pot("RESONANCE", 0.51)  # on P1, a hair from P3's value
        self.s.press("3")
        self.s.ms(3100)
        self.s.ms(2000)
        self.assertEqual(self.s.params_raw()["resonance"], stored, "untouched: the stored value")
        self.s.pot("RESONANCE", 0.53)
        self.assertShows("  52", "turned: it's the knob's at once, no dot")
        self.assertEqual(self.s.params()["resonance"], 52)


class ValueNudge(PanelTest):
    """#39: while a knob's value is on the display, VALUE moves it a tenth a cycle (a stepped knob a step) and the knob
    waits to be turned. 0-99 values show their tenth; the value is the patch's, so a save keeps it."""

    def touch(self, pot, fraction):
        """The knob turned to `fraction` (the simulator starts every pot at 2048, so from 0.5 it's moved there first)."""
        if abs(fraction - 0.5) < 0.05:
            self.s.pot(pot, 0.3)
        self.s.pot(pot, fraction)
        self.s.ms(100)  # the glide to the reading

    def tenths(self, name, layer=0):
        return round(self.s.params_raw(layer)[name] * 990 / 16383)

    def test_two_cycles_two_tenths_and_it_stays(self):
        self.manual()
        self.touch("RESONANCE", 0.3)  # 29.7
        self.s.turn(2)
        self.assertShows(" 29.9", "from the nearest tenth, 29.7, two up")
        self.assertEqual(self.tenths("resonance"), 299)
        self.s.ms(2000)
        self.assertEqual(self.tenths("resonance"), 299, "the knob, untouched, doesn't take it back")
        self.assertShows("PAnL")
        self.s.turn(1)
        self.assertEqual(self.tenths("resonance"), 299, "the value's gone from the display: VALUE does nothing")
        self.s.turn(-1)
        self.assertShows("PAnL")

    def test_a_save_keeps_the_tenth_through_a_load_and_a_power_cycle(self):
        self.manual()
        self.touch("RESONANCE", 0.3)
        self.s.turn(-3)
        self.assertShows(" 29.4")
        self.s.press("REC", "3", "REC")
        self.s.ms(1100)
        self.s.press("1")
        self.s.ms(3100)
        self.s.press("3")
        self.s.ms(3100)
        self.assertEqual(self.tenths("resonance"), 294)
        self.s.ms(1100)
        self.power_cycle()
        self.assertEqual(self.tenths("resonance"), 294)

    def test_a_report_on_noise_doesnt_take_the_value_back(self):
        """The panel reports a knob 24 counts from its last report, not from where it began waiting: a knob that crept
        20 counts unreported, then 4 of noise after the nudge, sends a report, and that isn't a turn."""
        self.manual()
        self.touch("RESONANCE", 0.3)
        x = round(0.3 * 4095)
        self.s.cmd(f"pot {POT['RESONANCE']} {x + 20}")  # under the report threshold
        self.s.ms(100)
        self.s.turn(1)
        nudged = self.s.params_raw()["resonance"]
        self.s.cmd(f"pot {POT['RESONANCE']} {x + 24}")  # a report: 24 from the last one, 4 from where it rests
        self.s.ms(500)
        self.assertEqual(self.s.params_raw()["resonance"], nudged)

    def test_turning_the_knob_afterwards_takes_over(self):
        self.manual()
        self.touch("RESONANCE", 0.3)
        self.s.turn(1)
        self.touch("RESONANCE", 0.35)
        self.assertShows("  35", "it rested on the value: the first turn takes it, no dot")
        self.assertEqual(self.s.params()["resonance"], 35)

    def test_in_preset_mode_the_window_takes_value_from_the_patches(self):
        self.s.press("REC", "3", "REC")
        self.s.ms(1100)
        self.touch("RESONANCE", 0.3)  # P3 has the init sound's 0; the knob waits for it
        self.assertShows("   0.")
        self.s.turn(1)
        self.assertShows("  0.1", "the value on the display: the stored one")
        self.assertEqual(self.s.settings()["patch"], 2, "still P3")
        self.s.ms(1600)
        self.s.turn(1)
        self.assertShows("P  4", "the window over: VALUE steps the patches again")

    def test_shift_value_keeps_stepping_ten_patches(self):
        self.s.press("REC", "3", "REC")
        self.s.ms(1100)
        self.touch("RESONANCE", 0.3)
        before = self.s.params_raw()["resonance"]
        self.s.hold("SHIFT")
        self.s.turn(1)
        self.assertShows("P 13")
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.params_raw()["resonance"], before)

    def test_free_tuning_by_tenths_of_a_semitone(self):
        self.manual()
        self.s.setting("osc-tune 1 a", 2)
        self.touch("FREQ_A", 0.5 + 12.3 / 72)
        self.assertShows(" 12.3")
        self.s.turn(3)
        self.assertShows(" 12.6", "a tenth of a semitone a cycle (the 0-99 grid would make it 12.5)")
        self.s.cmd(f"pot {POT['FREQ_A']} {round((0.5 + 12.3 / 72) * 4095) + 3}")  # the knob drifts a little
        self.s.ms(40)
        self.assertShows(" 12.6", "the follow doesn't redraw the knob over the value")
        self.assertEqual(round(self.s.params_raw()["freq-a"] * 720 / 16383), 486)

    def test_stepped_knobs_step(self):
        self.manual()
        self.touch("FREQ_A", 43 / 72)  # +7 semitones
        self.s.turn(1)
        self.s.ms(2000)
        self.assertEqual(round(self.s.params_raw()["freq-a"] * 72 / 16383), 44, "+8, and it stays")
        self.s.setting("osc-tune 1 a", 1)  # octaves
        self.touch("FREQ_A", 0.5)
        self.s.turn(-1)
        self.assertEqual(round(self.s.params_raw()["freq-a"] * 6 / 16383), 2)
        self.s.setting("lfo-sync", 1)
        self.touch("LFO_FREQ", 0.5)
        self.s.turn(1)
        self.assertEqual(round(self.s.params_raw()["lfo-freq"] * 14 / 16383), 8, "one division")

    def test_a_unit_knob_moves_a_tenth_of_a_0_99_unit(self):
        self.manual()
        self.touch("CUTOFF", 0.5)
        self.s.turn(1)
        self.assertEqual(self.tenths("cutoff"), 496)

    def test_m_vol_isnt_adjusted(self):
        self.manual()
        self.touch("MASTER_VOL", 0.5)
        shown = self.s.shows()
        self.s.turn(2)
        self.assertEqual(self.s.shows(), shown)
        self.assertEqual(self.s.params()["master-vol"], 50)

    def test_layer_2s_value_on_layer_2(self):
        self.manual()
        self.s.setting("layers", 2)
        self.s.press("SEQ2")
        self.s.ms(1100)
        self.touch("CUTOFF", 0.3)  # per layer (resonance is one wire for both); layer 2's filter is the init sound's, open
        self.s.turn(-1)
        self.assertEqual(self.tenths("cutoff", 1), 989, "layer 2's stored value, a tenth down")
        self.assertEqual(self.tenths("cutoff", 0), 495, "layer 1's stays")

    def test_a_controller_takes_the_display_and_value(self):
        self.manual()
        self.touch("RESONANCE", 0.3)
        self.s.midi(0xB0, 71, 100)  # CC 71 is resonance
        self.s.ms(100)
        self.s.turn(1)
        self.assertShows("  78", "the controller's value, and VALUE no longer adjusts it")
        self.assertEqual(self.s.params()["resonance"], 78)

    def test_a_knob_ends_the_lfo_shapes_window(self):
        self.manual()
        shape = self.s.settings()["shape 1 1"]
        self.s.switch("LFO_SHAPE")
        self.touch("RESONANCE", 0.3)
        self.s.turn(1)
        self.assertEqual(self.s.settings()["shape 1 1"], (shape + 1) % 9, "only the press stepped it")
        self.assertEqual(self.tenths("resonance"), 298)

    def test_after_shift_value_leaves_env_3_and_the_filter_envelope_alone(self):
        """Under Shift FIL ATT is Env 3's attack; let go, it's the filter's again: VALUE mustn't adjust either."""
        self.manual()
        before = self.s.params_raw()
        self.s.hold("SHIFT")
        self.touch("FIL_ATT", 0.3)
        self.s.let_go("SHIFT")
        touched = self.s.params_raw()["env3-att"]
        self.s.turn(2)
        after = self.s.params_raw()
        self.assertEqual((after["env3-att"], after["fil-att"]), (touched, before["fil-att"]))

    def test_value_keeps_its_jobs_in_a_menu(self):
        self.manual()
        self.touch("RESONANCE", 0.3)
        self.s.press("SETTINGS")
        self.s.turn(1)
        self.assertEqual(self.tenths("resonance"), round(round(0.3 * 4095) / 4095 * 990))


class Shift(PanelTest):
    def test_hold_lights_the_led_and_lets_go(self):
        self.s.hold("SHIFT")
        self.assertTrue(self.s.led("SYNC_SOURCE"))
        self.s.let_go("SHIFT")
        self.assertFalse(self.s.led("SYNC_SOURCE"))
        self.assertShows("P  1")

    def test_two_quick_presses_latch_and_two_more_release(self):
        self.s.ms(500)
        self.s.press("SHIFT")
        self.s.hold("SHIFT")
        self.assertShows("SHFt")
        self.s.let_go("SHIFT")
        self.assertTrue(self.s.led("SYNC_SOURCE"))
        self.s.ms(900)
        self.assertShows("P  1")
        self.s.ms(500)
        self.s.press("SHIFT")
        self.s.hold("SHIFT")
        self.assertShows("oFF")
        self.s.let_go("SHIFT")
        self.assertFalse(self.s.led("SYNC_SOURCE"))

    def test_a_press_just_after_boot_doesnt_latch(self):
        self.power_cycle()
        self.s.ms(10)
        self.s.press("SHIFT")
        self.assertFalse(self.s.led("SYNC_SOURCE"))

    def secondary(self, pot, name, start=0.3, end=0.6):
        self.s.hold("SHIFT")
        self.s.pot(pot, start)
        self.s.pot(pot, end)
        self.assertShows(name)
        self.s.ms(520)
        value = self.s.shows()
        self.s.let_go("SHIFT")
        return value

    def test_names_then_value(self):
        for pot, name in (("LFO_FREQ", "dLY"), ("LFO_AMOUNT", "FAdE"), ("GLIDE", "drFt"), ("MASTER_TUNE", "dEtU"), ("FINE", "bEnd"),
                          ("CUTOFF", "nAC1"), ("RESONANCE", "nAC2"), ("A_LEVEL", "CC.A"), ("B_LEVEL", "CC.b"), ("NOISE", "CHnL"), ("MASTER_VOL", "VE.A"), ("FIL_ENV_AMT", "VE.F")):
            with self.subTest(pot=pot):
                value = self.secondary(pot, name)
                self.assertRegex(value, r"\d|ALL", f"{pot}'s value after the name")
                self.s.ms(1600)
                self.assertShows("P  1")

    def test_secondary_doesnt_change_the_printed_parameter(self):
        before = self.s.params()["cutoff"]
        self.secondary("CUTOFF", "nAC1")
        self.assertEqual(self.s.params()["cutoff"], before)

    def test_letting_go_moves_nothing(self):
        before = self.s.params()["resonance"]
        self.secondary("RESONANCE", "nAC2")
        self.s.ms(100)
        self.assertEqual(self.s.params()["resonance"], before)

    def test_midi_channel_reaches_all_and_16(self):
        self.s.hold("SHIFT")
        for f in (0.5, 0.0, 0.5, 1.0):  # one sweep has to reach both ends
            self.s.pot("NOISE", f)
        self.s.ms(600)
        self.assertShows("  16")
        self.assertEqual(self.s.settings()["midi-channel"], 16)
        self.s.pot("NOISE", 0.0)
        self.s.ms(100)
        self.assertShows("ALL")
        self.assertEqual(self.s.settings()["midi-channel"], 0)
        self.s.let_go("SHIFT")

    def test_letting_go_of_shift_at_once_still_shows_the_words(self):
        """The value comes half a second after the name, and Shift can be up by then."""
        for pot, name, start, end, word in (("NOISE", "CHnL", 0.3, 0.1, "ALL"), ("PMOD_FIL_ENV", "P.UP", 0.9, 0.1, "OFF"), ("PMOD_FIL_ENV", "P.UP", 0.3, 0.9, "on")):
            with self.subTest(word=word):
                self.s.hold("SHIFT")
                self.s.pot(pot, start)
                self.s.pot(pot, end)
                self.assertShows(name)
                self.s.let_go("SHIFT")
                self.s.ms(520)
                self.assertShows(word)
                self.s.ms(1600)

    def test_detune_stays_0_to_100(self):
        self.s.hold("SHIFT")
        for f in (0.5, 1.0, 0.0, 1.0, 0.0, 1.0):
            self.s.pot("MASTER_TUNE", f)
        self.s.ms(600)
        self.assertShows(" 100")
        self.s.let_go("SHIFT")

    def test_copying_a_layer(self):
        self.s.hold("SHIFT")
        self.s.press("SEQ2")
        self.assertShows("CoPY")
        self.s.press("SEQ1")
        self.assertShows("SAnE", "onto the layer it's on")
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_panic(self):
        self.s.note(60)
        self.s.ms(50)
        self.assertTrue(any(self.s.cvs()[CV_AMP1 + v] for v in range(8)))
        self.s.hold("SHIFT")
        self.s.press("0")
        self.assertShows("PAnC")
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_digit_opens_that_settings_page(self):
        self.s.hold("SHIFT")
        self.s.press("3")
        self.s.let_go("SHIFT")
        self.assertTrue(self.s.led("SETTINGS"))
        self.assertShows("OFF", "the layer mode's value")
        self.s.press("SETTINGS")
        self.assertShows("P  1")

    def test_env_3_on_the_filter_knobs(self):
        self.s.hold("SHIFT")
        self.s.pot("FIL_SUS", 0.5)
        self.s.let_go("SHIFT")
        self.s.ms(100)
        self.assertGreaterEqual(self.s.params()["env3-sus"], 0)


class SettingsMenu(PanelTest):
    def open(self, key):
        self.s.press("SETTINGS", key)

    def test_opens_and_closes(self):
        self.s.press("SETTINGS")
        self.assertShows("SEt")
        self.assertTrue(self.s.led("SETTINGS"))
        self.s.ms(3000)
        self.assertShows("SEt", "it stays")
        self.s.press("SETTINGS")
        self.assertShows("P  1")
        self.assertFalse(self.s.led("SETTINGS"))

    def test_each_page_shows_its_name_then_its_value(self):
        for key, name, value in (("1", "SYnC", "FrEE"), ("2", "trIG", "FrEE"), ("3", "LAYr", "OFF"), ("4", "SPLt", "  60"), ("5", "trAC", "FULL"),
                                 ("6", "En.A", "AdSr"), ("7", "bEnd", "   2"), ("8", "dEtU", "  14"), ("9", "UOIC", "POLY"), ("0", "drFt", "   0")):
            with self.subTest(key=key):
                self.open(key)
                self.assertShows(name)
                self.s.ms(700)
                self.assertShows(value)
                self.s.press("SETTINGS")

    def test_value_steps_a_page_and_it_sticks(self):
        self.open("1")
        self.s.ms(700)
        self.s.turn(1)
        self.assertShows("CLoC")
        self.assertEqual(self.s.settings()["lfo-sync"], 1)
        self.s.turn(1)
        self.assertShows("FrEE")

    def test_value_wraps_through_the_layer_modes(self):
        self.open("3")
        self.s.ms(700)
        for name in ("SPLt", "StAC", "CHAn", "8CH", "nPE", "OFF"):
            self.s.turn(1)
            self.assertShows(name)
        self.s.turn(-1)
        self.assertShows("nPE")
        self.s.turn(1)

    def test_value_doesnt_browse_patches_in_the_menu(self):
        self.open("7")
        self.s.ms(700)
        self.s.turn(2)
        self.s.ms(100)
        self.assertShows("   4")
        self.assertEqual(self.s.settings()["patch"], 0)
        self.assertEqual(self.s.settings()["bend"], 4)

    def test_lfo_pages_follow_the_selected_lfo(self):
        self.s.press("ARP_UP")
        self.open("1")
        self.assertShows("SYn2")
        self.s.ms(700)
        self.s.turn(1)
        self.assertEqual(self.s.settings()["lfo2-sync"], 1)
        self.assertEqual(self.s.settings()["lfo-sync"], 0)
        self.s.press("2")
        self.assertShows("trI2")

    def test_arp_up_changes_the_lfo_on_a_page(self):
        self.open("1")
        self.s.ms(700)
        self.s.press("ARP_UP")
        self.assertShows("LFO2")
        self.s.ms(700)
        self.assertShows("FrEE")

    def test_retrigger_second_press_shows_cycles(self):
        self.open("2")
        self.s.press("2")
        self.assertShows("Cn.1")
        self.s.ms(700)
        self.assertShows("   1")
        self.s.turn(1)
        self.assertShows("   2")
        self.assertEqual(self.s.settings()["lfo-cycles"], 2)

    def test_retrigger_names(self):
        self.open("2")
        self.s.ms(700)
        for name in ("1St", "EACH", "PoLY", "1Sht", "1S.Po", "FrEE"):
            self.s.turn(1)
            self.assertShows(name)

    def test_voices_per_layer(self):
        self.open("3")
        self.s.press("ARP_UP")
        self.assertShows("Lr.Uo")
        self.s.ms(700)
        self.assertShows("AUto")
        self.s.turn(1)
        self.assertShows("1-7")
        self.s.turn(3)
        self.assertShows("4-4")

    def test_envelope_page_cycles_the_envelopes(self):
        self.open("6")
        self.assertShows("En.A")
        for name in ("En.F", "En.3", "En.4", "En.A"):
            self.s.press("6")
            self.assertShows(name)

    def test_envelope_page_alternates(self):
        self.open("6")
        for name, value in (("tr.A", "dn"), ("tI.A", "SEC"), ("CU.A", "AnLG"), ("Cn.A", "LOOP")):
            self.s.press("ARP_UP")
            self.assertShows(name)
            self.s.ms(700)
            self.assertShows(value)

    def test_envelope_modes(self):
        self.open("6")
        self.s.ms(700)
        for name in ("dAHd", "CYCL", "8bAr"):
            self.s.turn(1)
            self.assertShows(name)

    def test_voice_modes(self):
        self.open("9")
        self.s.ms(700)
        for name in ("nono", "LEG", "POLY"):
            self.s.turn(1)
            self.assertShows(name)

    def test_split_by_playing_a_note(self):
        self.open("4")
        self.s.ms(700)
        self.s.note(72)
        self.s.ms(5)
        self.s.midi(0x80, 72, 0)
        self.s.ms(5)
        self.assertShows("  72")
        self.assertEqual(self.s.settings()["split"], 72)

    def test_keytrack_page_and_switch_agree(self):
        self.open("5")
        self.s.ms(700)
        self.assertShows("FULL")
        self.s.turn(1)
        self.assertShows("OFF")
        self.s.turn(1)
        self.assertShows("HALF")
        self.assertEqual(self.s.settings()["keytrack"], 1)

    def test_rec_does_nothing_in_the_menu(self):
        self.open("7")
        self.s.ms(700)
        self.s.press("REC")
        self.assertShows("   2")
        self.assertFalse(self.s.led("REC"))

    def test_a_setting_is_lost_without_a_save(self):
        self.open("7")
        self.s.ms(700)
        self.s.turn(1)
        self.s.press("SETTINGS")
        self.s.press("2")
        self.s.ms(3100)
        self.s.press("1")
        self.s.ms(3100)
        self.assertEqual(self.s.settings()["bend"], 2)

    def test_a_setting_is_kept_by_a_save(self):
        self.open("7")
        self.s.ms(700)
        self.s.turn(1)
        self.s.press("SETTINGS")
        self.store_patch(3)
        self.s.press("1")
        self.s.ms(3100)
        self.s.press("3")
        self.s.ms(3100)
        self.assertEqual(self.s.settings()["bend"], 3)


class LfoControls(PanelTest):
    def test_shape_button_steps_through_every_name(self):
        for name in ("PULS", "SA.UP", "SA.dn", "SInE", "S-H", "drFt", "StEP", "nOIS", "trI"):
            self.s.switch("LFO_SHAPE")
            self.assertShows(name)

    def test_shape_gives_way_after_2_s(self):
        self.s.switch("LFO_SHAPE")
        self.s.ms(2000)
        self.assertShows("P  1")

    def test_value_steps_the_shape_both_ways_for_2_s(self):
        self.s.switch("LFO_SHAPE")
        self.assertShows("PULS")
        self.s.turn(1)
        self.assertShows("SA.UP")
        self.s.turn(-2)
        self.assertShows("trI")
        self.assertEqual(self.s.settings()["patch"], 0)

    def test_value_browses_patches_again_after_2_s(self):
        self.s.switch("LFO_SHAPE")
        self.s.ms(2100)
        self.s.turn(1)
        self.s.ms(100)
        self.assertShows("P  2")

    def test_arp_up_picks_lfo_2(self):
        self.s.press("ARP_UP")
        self.assertShows("LFO2")
        self.assertTrue(self.s.led("ARP_UP"))
        self.s.ms(1100)
        self.assertShows("P  1")
        self.s.press("ARP_UP")
        self.assertShows("LFO1")
        self.assertFalse(self.s.led("ARP_UP"))

    def test_routing_buttons_on_lfo_2_say_on_or_off(self):
        self.s.press("ARP_UP")
        self.s.ms(1100)
        for button, on, off in (("LFO_FREQ", "Fr.on", "Fr.oF"), ("LFO_FILTER", "FL.on", "FL.oF"), ("LFO_PW", "PU.on", "PU.oF")):
            self.s.switch(button)
            first = self.s.shows()
            self.assertIn(first, (readable(segments_of(on)), readable(segments_of(off))))
            self.s.switch(button)
            self.assertNotEqual(self.s.shows(), first)
            self.s.ms(1100)
            self.assertShows("P  1")

    def test_lfo_knobs_edit_the_selected_lfo(self):
        self.manual()
        self.s.press("ARP_UP")
        self.s.ms(1100)
        self.s.pot("LFO_AMOUNT", 0.0)  # past LFO 2's stored value
        self.s.pot("LFO_AMOUNT", 0.75)
        self.assertEqual(self.s.params()["lfo2-amount"], 74)
        self.assertEqual(self.s.params()["lfo-amount"], 50, "LFO 1 stays where the knob left it")

    def test_lfo_freq_shows_the_division_when_synced(self):
        self.manual()
        self.s.press("SETTINGS", "1")
        self.s.ms(700)
        self.s.turn(1)
        self.s.press("SETTINGS")
        self.s.pot("LFO_FREQ", 0.2)
        self.s.pot("LFO_FREQ", 0.5)
        self.assertRegex(self.s.shows(), r"\d-\d|\d\.|^\S")
        self.assertNotRegex(self.s.shows(), r"^ +\d+\.\d+$")


class KeyboardTracking(PanelTest):
    def test_switch_names(self):
        self.s.switch("KEYTRACK_HALF", 0)
        self.assertShows("HALF")
        self.s.switch("KEYTRACK_HALF", 1)
        self.assertShows("OFF")
        self.s.switch("KEYTRACK_FULL", 0)
        self.assertShows("FULL")
        self.s.ms(1100)
        self.assertShows("P  1")


class SwitchNames(PanelTest):
    """A latching button shows what it now does for a second (#34): an oscillator's waves as a set in fixed places, the
    others as two letters and on / oF. The init sound on P1 has osc A's saw on; the buttons are all up (off)."""

    def test_a_wave_shows_its_oscillators_set_as_it_sounds(self):
        self.s.switch("A_TRI")
        self.assertShows("A.St_", "the saw is the patch's, its button still up")
        self.s.switch("A_TRI")
        self.assertShows("A.S__")
        self.s.switch("A_SAW")
        self.assertShows("A.S__", "the button now agrees with the patch")
        self.s.switch("A_SAW")
        self.assertShows("A.oFF")
        self.s.switch("B_SQUARE")
        self.assertShows("b.__Q")
        self.s.switch("B_TRI")
        self.assertShows("b._tQ")
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_the_other_buttons_say_on_or_off(self):
        for button, name in (("SYNC", "SY"), ("PMOD_FREQ_A", "PA"), ("PMOD_FILTER", "PF"), ("LFO_FREQ", "Fr"),
                             ("LFO_FILTER", "FL"), ("LFO_PW", "PU")):
            self.s.switch(button)
            self.assertShows(f"{name}.on", button)
            self.s.switch(button)
            self.assertShows(f"{name}.oF", button)
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_nothing_over_mod_assign_or_a_save(self):
        self.s.press("ASSIGN")
        self.assertShows("SrC")
        self.s.switch("A_TRI")
        self.assertShows("SrC", "MOD ASSIGN keeps the display")
        self.s.press("ASSIGN")
        self.s.ms(1100)
        self.s.press("REC", "1", "2")
        shown = self.s.shows()
        self.s.switch("B_SAW")
        self.s.switch("SYNC")
        self.assertEqual(self.s.shows(), shown, "the slot being typed stays")

    def test_lfo_2s_routing_shows_over_a_menu_as_it_always_did(self):
        """The buttons show LFO 1's routing, so for LFO 2 the display is the only sign; a wave keeps the menu's text."""
        self.s.press("ARP_UP")
        self.s.ms(1100)
        self.s.press("SETTINGS")
        menu = self.s.shows()
        self.s.switch("LFO_FREQ")
        self.assertShows("Fr.on")
        self.s.ms(1100)
        self.assertEqual(self.s.shows(), menu)
        self.s.switch("A_TRI")
        self.assertEqual(self.s.shows(), menu, "a wave says nothing over the menu")

    def test_after_an_lfo_2_toggle_lfo_1s_first_press_only_brings_the_button_back(self):
        """As MANUAL.md says: LFO 2's press moves the latch but LFO 1 keeps its routing (held); with LFO 1 selected the
        next press puts the button back in step and changes nothing, the one after switches LFO 1."""
        self.s.press("ARP_UP")
        self.s.ms(1100)
        self.s.switch("LFO_FREQ")
        self.assertShows("Fr.on", "LFO 2 → pitch")
        self.s.press("ARP_UP")
        self.s.ms(1100)
        self.s.switch("LFO_FREQ")
        self.assertShows("Fr.oF", "LFO 1 → pitch still off")
        self.s.switch("LFO_FREQ")
        self.assertShows("Fr.on", "now LFO 1")
        self.assertEqual(self.s.settings()["lfo2-dests 1"], 1, "LFO 2 kept its pitch route")

    def test_the_hosts_switches_say_nothing(self):
        self.s.request(0x4B, SWITCH["SYNC"], 1)
        self.s.ms(100)
        self.assertShows("P  1")


class HostShowsAParameter(PanelTest):
    """#34: 6B shows a parameter's value on the display as its knob would; the editor sends it after its own edits."""

    def show(self, rule, name):
        self.s.request(0x6B, rule, pro800.PARAM_NAMES.index(name))
        self.s.ms(100)

    def test_set_then_shown_then_gone(self):
        self.s.param(0, "resonance", 8192)
        self.s.ms(100)
        self.assertShows("P  1", "52 alone shows nothing")
        self.show(1, "resonance")
        self.assertShows("  50")
        self.s.ms(1600)
        self.assertShows("P  1")

    def test_the_layer_rule(self):
        self.s.setting("layers", 2)
        self.s.param(1, "cutoff", 0)  # layer 2's cutoff, in Hz on the display
        self.s.param(0, "cutoff", 16383)
        self.show(2, "cutoff")
        layer2 = self.s.shows()
        self.show(1, "cutoff")
        self.assertNotEqual(self.s.shows(), layer2)
        self.s.press("SEQ2")
        self.s.ms(1100)
        self.show(0, "cutoff")
        self.assertEqual(self.s.shows(), layer2, "rule 0: the edit layer")

    def test_not_over_a_menu(self):
        self.s.press("SETTINGS")
        self.show(1, "resonance")
        self.assertShows("SEt")

    def knob_shows(self, pot, value):
        """What the panel's own knob shows at `value` (0..1): the reference for a 6B of the parameter it edits."""
        self.s.pot(pot, value)
        self.s.pot(pot, value + 0.03)
        self.s.pot(pot, value)
        shown = self.s.shows().rstrip(".")
        self.s.ms(1600)
        return shown

    def test_env_3_and_lfo_2_in_their_own_units(self):
        self.s.param(0, "env3-att", 8192)
        self.s.param(0, "lfo2-rate", 8192)
        self.show(1, "env3-att")
        env3 = self.s.shows()
        self.assertNotEqual(env3, "  50", "a time, not 0-99")
        self.show(1, "lfo2-rate")
        lfo2 = self.s.shows()
        self.assertNotEqual(lfo2, "  50", "Hz, not 0-99")
        self.s.ms(1600)
        self.s.hold("SHIFT")  # the filter knobs edit Env 3
        self.assertEqual(self.knob_shows("FIL_ATT", 0.5), env3, "as the panel's knob on Env 3 shows it")
        self.s.let_go("SHIFT")
        self.s.setting("lfo", 1)  # the LFO knobs edit LFO 2
        self.assertEqual(self.knob_shows("LFO_FREQ", 0.5), lfo2)

    def test_by_the_parameter_not_the_panels_selection(self):
        self.s.param(0, "fil-att", 8192)
        self.s.param(0, "lfo-freq", 8192)
        self.show(1, "fil-att")
        filter_attack = self.s.shows()
        self.show(1, "lfo-freq")
        lfo1 = self.s.shows()
        self.s.ms(1600)
        self.s.setting("env-tempo 1", 4 | 8)  # Env 3 and 4's times in note values
        self.s.hold("SHIFT")  # the panel's ADSR knobs on Env 3 and 4
        self.s.setting("lfo", 1)
        self.s.setting("lfo2-sync", 1)  # LFO 2 synced: divisions
        self.show(1, "fil-att")
        self.assertEqual(self.s.shows(), filter_attack, "the filter envelope's time, whatever the knob edits now")
        self.show(1, "lfo-freq")
        self.assertEqual(self.s.shows(), lfo1, "LFO 1's Hz, not LFO 2's division")
        self.s.let_go("SHIFT")

    def test_out_of_range_does_nothing(self):
        self.s.request(0x6B, 3, 0)
        self.s.request(0x6B, 1, 124)  # no such parameter (tools/params.json)
        self.s.ms(100)
        self.assertShows("P  1")


class ChordMemory(PanelTest):
    """#36: Unison turned on with 2 or more keys held takes them as the patch's chord (CHrd), and every key then plays
    it in place of the unison stack; the pedal pressed over held keys in Unison does the same. Saved with the patch."""

    def voices(self):
        """(key, note) of each gated voice, low to high"""
        d = self.s.reply(0x55)
        return sorted((d[93 + 3 * v], d[94 + 3 * v]) for v in range(8) if d[10 + 3 * v])

    def notes(self, key=None):
        return [n for k, n in self.voices() if key is None or k == key]

    def chord(self):
        d = self.s.reply(0x68)
        return list(d[0:8]), list(d[8:16])

    def capture(self, *keys, shows="CHrd"):
        for k in keys:
            self.s.note(k)
        self.s.switch("UNISON")
        self.s.ms(20)
        self.assertShows(shows)
        for k in keys:
            self.s.note_off(k)

    def route(self, source, dest, depth):
        d = depth + 99
        self.s.request(0x56, 0, pro800.SOURCE_NAMES.index(source), pro800.DEST_NAMES.index(dest), d >> 7, d & 0x7F)

    def test_unison_over_held_keys_takes_their_chord(self):
        self.capture(60, 64, 67)
        self.assertEqual(self.chord()[0], [4, 7, 0, 0, 0, 0, 0, 0])
        self.assertEqual(self.s.gated(), [], "the keys that made it are let go")
        self.s.note(62)
        self.assertEqual(self.voices(), [(62, 62), (62, 66), (62, 69)])
        self.s.note_off(62)
        self.assertEqual(self.s.gated(), [])
        self.s.ms(1000)
        self.s.switch("UNISON")
        self.assertShows("Un.oF")

    def test_chords_share_the_voices_and_never_steal_their_own(self):
        self.capture(60, 64, 67)
        for k in (50, 55):
            self.s.note(k)
        self.assertEqual(len(self.s.gated()), 6)
        self.s.note(70)
        self.assertEqual(len(self.s.gated()), 8)
        self.assertEqual(self.notes(70), [70, 74, 77], "the newest chord is whole")
        self.assertEqual(self.notes(55), [55, 59, 62])
        self.assertEqual(len(self.notes(50)), 2, "the oldest gave up a voice")
        self.s.note(55)
        self.assertEqual(len(self.notes(55)), 3, "struck again: its own voices")
        for k in (50, 55, 70):
            self.s.note_off(k)
        self.assertEqual(self.s.gated(), [])

    def test_one_key_or_none_is_plain_unison_and_clears_it(self):
        self.capture(60, 64, 67)
        self.s.switch("UNISON")
        for keys in ((), (60,)):
            with self.subTest(keys=keys):
                self.s.request(0x69, 0, 4, 7, 0, 0, 0, 0, 0, 0)
                self.s.ms(1100)
                self.capture(*keys, shows="Un.on")
                self.assertEqual(self.chord()[0], [0] * 8)
                self.s.note(62)
                self.assertEqual(len(self.s.gated()), 8)
                self.s.note_off(62)
                self.s.switch("UNISON")

    def test_off_keeps_it_for_the_host_to_bring_back(self):
        self.capture(60, 64, 67)
        self.s.switch("UNISON")
        self.s.note(62)
        self.assertEqual(len(self.s.gated()), 1, "Unison off: poly")
        self.s.note_off(62)
        self.assertEqual(self.chord()[0][:2], [4, 7], "kept")
        self.s.request(0x4B, SWITCH["UNISON"], 1)
        self.s.ms(20)
        self.s.note(62)
        self.assertEqual(self.notes(), [62, 66, 69], "the host's Unison: the chord again, no capture")
        self.s.note_off(62)

    def test_mono_and_legato_play_their_line_as_chords(self):
        for mode in (1, 2):
            with self.subTest(mode=pro800.VOICE_MODES[mode]):
                self.s.setting("voice 1", mode)
                self.s.param(0, "amp-att", 8700)  # 0.2 s: past it by 400 ms, in it 2 ms after a retrigger
                self.capture(60, 64, 67)
                self.s.note(60)
                self.s.ms(400)
                self.s.note(65)
                self.assertEqual(self.voices(), [(65, 65), (65, 69), (65, 72)])
                self.s.ms(400)
                self.s.note_off(65)
                self.assertEqual(self.voices(), [(60, 60), (60, 64), (60, 67)], "back to the held key's chord")
                amp = self.s.probe()["amp"]
                self.assertEqual(all(amp[v] in (1, 6) for v in self.s.gated()), mode == 1, "mono retriggers, legato doesn't")
                self.s.note_off(60)
                self.assertEqual(self.s.gated(), [])
                self.s.switch("UNISON")

    def test_a_split_captures_where_the_keys_are(self):
        self.s.setting("layers", 1)  # split at 60
        self.capture(48, 52, 55)
        a, b = self.chord()
        self.assertEqual((a[:2], b[0]), ([4, 7], 0))
        self.s.note(50)
        self.s.note(72)
        self.assertEqual(self.notes(50), [50, 54, 57])
        self.assertEqual(self.notes(72), [72] * 4, "layer 2: plain unison on its four voices")

    def test_not_in_the_per_channel_modes(self):
        self.s.setting("layers", 4)  # 8ch
        for c, k in enumerate((60, 64, 67)):
            self.s.note(k, channel=c)
        self.s.switch("UNISON")
        self.s.ms(20)
        self.assertShows("Un.on")
        self.assertEqual(self.chord()[0], [0] * 8)

    def test_keys_the_pedal_holds_count(self):
        self.s.cc(64, 127)
        for k in (60, 64, 67):
            self.s.note(k)
        for k in (60, 64, 67):
            self.s.note_off(k)
        self.capture(shows="CHrd")
        self.assertEqual(self.chord()[0][:2], [4, 7])
        self.s.cc(64, 0)

    def test_the_pedal_over_held_keys_in_unison_latches_a_new_chord(self):
        self.s.switch("UNISON")
        for k in (60, 63, 67):
            self.s.note(k)
        self.s.cc(64, 127)
        self.s.ms(20)
        self.assertShows("CHrd")
        self.assertEqual(self.chord()[0][:2], [3, 7])
        self.s.cc(64, 0)
        for k in (60, 63, 67):
            self.s.note_off(k)
        self.s.note(50)
        self.assertEqual(self.notes(), [50, 53, 57])
        self.s.note_off(50)
        for k in (62, 66):  # and again, over the chord's own keys
            self.s.note(k)
        self.s.cc(64, 127)
        self.s.ms(5)
        self.s.cc(64, 0)
        for k in (62, 66):
            self.s.note_off(k)
        self.assertEqual(self.chord()[0][:2], [4, 0])

    def test_the_pedal_over_one_key_or_without_unison_only_sustains(self):
        for k in (60, 64):
            self.s.note(k)
        self.s.cc(64, 127)
        self.s.ms(5)
        self.assertEqual(self.chord()[0], [0] * 8, "Unison off: a sustain")
        self.s.cc(64, 0)
        for k in (60, 64):
            self.s.note_off(k)
        self.s.switch("UNISON")
        self.s.note(60)
        self.s.cc(64, 127)
        self.s.note_off(60)
        self.assertEqual(len(self.s.gated()), 8, "one key: sustained in plain unison")
        self.assertEqual(self.chord()[0], [0] * 8)
        self.s.cc(64, 0)

    def test_it_is_saved_with_the_patch(self):
        self.capture(60, 64, 67)
        self.store_patch(5)
        self.assertEqual(list(self.s.memory()[4 * 768 + 455:4 * 768 + 471]), [4, 7] + [0] * 14, "store.c: chords at 455")
        self.s.ms(1100)
        self.power_cycle()
        self.s.ms(3000)
        self.s.note(62)
        self.assertEqual(self.notes(), [62, 66, 69], "loaded with Unison on: the chord")

    def test_the_host_reads_sets_and_captures(self):
        out = self.s.request(0x69, 1, 3, 7, 10, 0, 0, 0, 0, 0)
        self.assertIn(b"\x7f\x69", out)
        self.assertEqual(self.chord()[1][:4], [3, 7, 10, 0])
        self.s.request(0x69, 0, 7, 4, 9, 0, 0, 0, 0, 0)
        self.assertEqual(self.chord()[0], [7, 0, 0, 0, 0, 0, 0, 0], "as far as it's valid: up to the step back")
        for k in (60, 65):
            self.s.note(k)
        self.assertEqual(list(self.s.reply(0x6A, 1)), [2, 127], "layer 2 not asked for: left alone")
        self.assertEqual(self.chord()[0][:2], [5, 0])
        for k in (60, 65):
            self.s.note_off(k)
        self.assertEqual(list(self.s.reply(0x6A, 1)), [0, 127], "nothing held: cleared")
        self.assertEqual(self.chord()[0], [0] * 8)

    def test_a_sequence_step_plays_the_chord(self):
        self.capture(60, 64, 67)
        self.s.seq_set(4)
        self.s.seq_steps(0, [(0,), (2,), (4,), (5,)])
        self.s.note(60)
        self.s.ms(10)
        self.assertEqual(self.notes(), [60, 64, 67])
        self.s.ms(125)
        self.assertEqual(self.notes(), [62, 66, 69], "the next step: its note's chord")
        self.s.note_off(60)
        self.s.ms(300)
        self.assertEqual(self.s.gated(), [])

    def test_a_step_with_its_own_chord_plays_that_alone(self):
        self.capture(60, 64, 67)
        self.s.seq_set(1, lanes=1, flags=1 | 1 << 5)
        self.s.seq_steps(0, [(0, 0, 50, 1, 0, (76, 64, 64, 64))])  # Note 2: +12
        self.s.note(60)
        self.s.ms(10)
        self.assertEqual(self.notes(), [60, 72])
        self.s.note_off(60)

    def test_the_arp_walks_the_chord_of_one_key(self):
        self.capture(60, 64, 67)
        self.s.seq_set(1, arp=1)  # up
        self.s.seq_steps(0, [(0,)])
        self.s.note(60)
        played = []
        for _ in range(4):
            self.s.ms(10)
            played.append(self.notes())
            self.s.ms(115)
        self.assertEqual(played, [[60], [64], [67], [60]])
        self.s.note_off(60)
        self.s.ms(300)
        self.assertEqual(self.s.gated(), [])

    def test_a_capture_while_the_arp_runs_takes_the_keys(self):
        self.s.seq_set(1, arp=1)
        self.s.seq_steps(0, [(0,)])
        for k in (60, 63, 67):
            self.s.note(k)
        self.s.ms(50)
        self.capture(shows="CHrd")
        self.assertEqual(self.chord()[0][:2], [3, 7])
        for k in (60, 63, 67):
            self.s.note_off(k)

    def test_the_voicing_ladder(self):
        """Depth 99 reaches as many steps as the chord has notes: C E G +1 E G C, +2 E C G, +3 C G E; down mirrors it."""
        self.capture(60, 64, 67)
        for depth, notes in ((33, [64, 67, 72]), (66, [64, 72, 79]), (99, [72, 79, 88]), (-33, [55, 60, 64]),
                             (-66, [48, 55, 64]), (-99, [40, 48, 55]), (0, [60, 64, 67])):
            with self.subTest(depth=depth):
                self.route("fixed", "chord-voicing", depth)
                self.s.note(60)
                self.assertEqual(self.notes(), notes)
                self.s.note_off(60)

    def test_velocity_voices_each_note_by_its_own(self):
        self.capture(60, 64, 67)
        self.route("velocity", "chord-voicing", 99)
        for velocity, notes in ((127, [72, 79, 88]), (1, [60, 64, 67]), (127, [72, 79, 88])):
            self.s.note(60, velocity)
            self.assertEqual(self.notes(), notes, f"velocity {velocity}")
            self.s.note_off(60)

    def test_the_voicing_holds_its_step_near_a_boundary(self):
        self.capture(60, 64, 67)
        self.route("macro2", "chord-voicing", 99)
        heard = []
        for offset in (0, 20, 22, 18, 10):
            self.s.setting("macro 1 2", offset + 99)
            self.s.ms(20)
            self.s.note(60)
            heard.append(self.notes())
            self.s.note_off(60)
        c, up = [60, 64, 67], [64, 67, 72]
        self.assertEqual(heard, [c, c, up, up, c])

    def test_a_held_chord_follows_the_voicing(self):
        self.s.param(0, "amp-att", 8700)  # 0.2 s: a retrigger would show as the attack again
        self.capture(60, 64, 67)
        self.route("macro2", "chord-voicing", 99)
        self.s.note(60)
        self.s.ms(400)
        gated = self.s.gated()
        self.s.setting("macro 1 2", 99 + 33)
        self.s.ms(3)
        amp = self.s.probe()["amp"]
        self.assertTrue(all(amp[v] not in (1, 6) for v in gated), "no retrigger")
        self.assertEqual(self.notes(), [64, 67, 72])
        self.assertEqual(self.s.gated(), gated, "the same voices")
        self.s.note_off(60)
        self.assertEqual(self.s.gated(), [])

    def test_note_into_the_voicing_reads_the_key(self):
        """The voicing moves the pitch: Note as its source is the chord's key, or a held chord would flip every pass."""
        self.capture(60, 64, 67)
        self.route("note", "chord-voicing", -99)
        self.s.note(96)
        heard = []
        for gap in (6, 7, 6, 7, 6, 7):  # a reply takes 5 ms: 11 and 12 passes apart, both parities
            heard.append(self.notes())
            self.s.ms(gap)
        self.assertEqual(heard, [heard[0]] * 6, "steady")
        self.assertEqual(heard[0], [84, 91, 100], "key 96 at -99: x -1.8, step -2 (G and C below)")
        self.s.note_off(96)

    def test_a_restarted_lfo_voices_the_chord_from_its_start(self):
        self.s.param(0, "lfo-freq", 5440)  # 0.5 Hz (0.05 x 2^(10 x 0.332))
        self.s.setting("shape 1 1", 1)  # square: the other half of its cycle reads the other end of the ladder
        self.s.setting("lfo-trigger", 2)  # every key
        self.capture(60, 64, 67)
        self.route("lfo", "chord-voicing", 99)
        self.s.ms(1500)  # three quarters into the cycle the capture's keys restarted
        # the note-on and the probe in one read: what the chord started as, before the first pass could re-voice it
        self.s.cmd("out")
        self.s.cmd("midi " + (bytes([0x90, 60, 100]) + pro800.SYSEX_HEADER + bytes([0x55]) + b"\xF7").hex())
        self.s.ms(5)
        out = bytes.fromhex(self.s.cmd("out"))
        start = out.index(pro800.SYSEX_HEADER + bytes([0x55]))
        d = out[start + pro800.HEADER_SIZE + 1:out.index(0xF7, start)]
        first = sorted(d[94 + 3 * v] for v in range(8) if d[10 + 3 * v])
        self.assertEqual(first, [72, 79, 88], "the restarted square's first half: the top of the ladder")
        self.s.ms(30)
        self.assertEqual(self.notes(), first, "and the passes after it agree")
        self.s.note_off(60)

    def test_without_its_route_the_voicing_goes_back(self):
        self.capture(60, 64, 67)
        self.route("fixed", "chord-voicing", 99)
        self.s.note(60)
        self.assertEqual(self.notes(), [72, 79, 88])
        self.route("fixed", "chord-voicing", 0)
        self.s.ms(20)
        self.assertEqual(self.notes(), [60, 64, 67], "held: back to the stored chord")
        self.s.note_off(60)
        self.s.seq_set(1, arp=1)
        self.s.seq_steps(0, [(0,)])
        self.s.note(60)
        played = []
        for _ in range(3):
            self.s.ms(10)
            played.append(self.notes())
            self.s.ms(115)
        self.assertEqual(played, [[60], [64], [67]], "the arp joins the stored chord")
        self.s.note_off(60)

    def test_the_pedals_capture_plays_on(self):
        self.s.switch("UNISON")
        for k in (60, 63, 67):
            self.s.note(k)
        self.s.cc(64, 127)
        self.s.ms(20)
        self.assertShows("CHrd")
        self.assertTrue(self.s.gated(), "the keys held play again, with the new chord")
        self.assertEqual(self.notes(67), [67, 70, 74], "the newest key whole (three triads take nine voices)")
        for k in (60, 63, 67):
            self.s.note_off(k)
        self.assertTrue(self.s.gated(), "the pedal holds them")
        self.s.cc(64, 0)
        self.s.ms(5)
        self.assertEqual(self.s.gated(), [])

    def test_stack_captures_on_both_layers(self):
        self.s.setting("layers", 2)
        self.capture(60, 64, 67)
        a, b = self.chord()
        self.assertEqual((a[:2], b[:2]), ([4, 7], [4, 7]))
        self.assertEqual(list(self.s.reply(0x6A, 3)), [0, 0], "nothing held now: both cleared")
        self.s.setting("layers", 0)
        self.assertEqual(list(self.s.reply(0x6A, 3)), [0, 127], "layers off: layer 2 left alone")

    def test_with_no_chord_the_voicing_does_nothing(self):
        self.route("fixed", "chord-voicing", 99)
        self.s.note(60)
        self.assertEqual(self.notes(), [60])
        self.s.note_off(60)
        self.s.switch("UNISON")
        self.s.note(60)
        self.assertEqual(self.notes(), [60] * 8)


def nrpn_ids():
    with open(os.path.join(ROOT, "tools", "params.json"), encoding="utf-8") as f:
        return {p["name"]: p["id"] for p in json.load(f)["params"]}


class Nrpn(PanelTest):
    """#37: NRPN reaches every parameter of tools/params.json: CC 99 the layer rule, 98 the id, 6 / 38 the value."""

    ID = nrpn_ids()

    def nrpn(self, name, msb=None, lsb=None, rule=0, channel=0):
        self.s.cc(99, rule, channel)
        self.s.cc(98, self.ID[name] if isinstance(name, str) else name, channel)
        if msb is not None:
            self.s.cc(6, msb, channel)
        if lsb is not None:
            self.s.cc(38, lsb, channel)
        self.s.ms(20)

    def param(self, layer, name):
        d = self.s.reply(0x53, layer)
        i = pro800.PARAM_NAMES.index(name)
        return d[2 + 2 * i] << 7 | d[3 + 2 * i]

    def test_a_knob_takes_coarse_then_fine(self):
        self.nrpn("cutoff", 64, rule=1)
        self.assertEqual(self.param(0, "cutoff"), 64 * 16383 // 127)
        self.s.cc(38, 5)
        self.s.ms(20)
        self.assertEqual(self.param(0, "cutoff"), 64 << 7 | 5, "CC 38 with the MSB: 14 bits")
        self.nrpn("cutoff", 127, rule=1)
        self.assertEqual(self.param(0, "cutoff"), 16383, "the MSB alone reaches the top")

    def test_a_knob_shows_as_its_knob(self):
        self.nrpn("resonance", 127)
        self.s.ms(60)
        self.assertShows("  99")

    def test_the_others_take_their_number_and_show_their_name_first(self):
        self.nrpn("layers", 2)
        self.assertEqual(self.s.settings()["layers"], 2)
        self.s.ms(60)
        self.assertShows("LAYr")
        self.s.ms(500)
        self.assertShows("   2")
        self.nrpn("layers", 1)
        self.s.ms(60)
        self.assertShows("   1", "the same one again: its value at once")
        self.nrpn("env-delay amp", 40)
        self.nrpn("shape 1", 4)
        self.nrpn("env-curve filter", 2)
        st = self.s.settings()
        self.assertEqual((st["env-delay 1 amp"], st["shape 1 1"], st["env-curve 1"] >> 2 & 3), (40, 4, 2))
        self.nrpn("layers", 99)
        self.assertEqual(self.s.settings()["layers"], 5, "clamped to its range")
        self.nrpn("layers", 2)
        self.s.cc(38, 1)
        self.s.ms(20)
        self.assertEqual(self.s.settings()["layers"], 2, "CC 38 means nothing to a number (it would make 2 << 7 | 1)")

    def test_a_button_and_its_display(self):
        self.nrpn("button a-saw", 0)
        self.assertEqual(self.s.reply(0x4C)[0], 0)
        self.s.ms(600)
        self.assertShows(" OFF")
        self.nrpn("button a-saw", 1)
        self.assertEqual(self.s.reply(0x4C)[0], 1)

    def test_a_macro_offset_is_centred(self):
        for msb, offset in ((64, 0), (127, 99), (0, -99)):
            self.nrpn("macro 1", msb)
            self.assertEqual(self.s.settings()["macro 1 1"] - 99, offset)
        self.nrpn("macro 2", 64, 0)
        self.assertEqual(self.s.settings()["macro 1 2"] - 99, 0)

    def test_a_macro_offset_in_14_bits_comes_back(self):
        """pro800 nrpn sends round(v x 8191 / 99) + 8192: the firmware rounds it back to v."""
        for offset in (2, -2, 33, 50, -60, -98, 98):
            v14 = max(0, min(16383, round(offset * 8191 / 99) + 8192))
            with self.subTest(offset=offset):
                self.nrpn("macro 1", v14 >> 7, v14 & 0x7F)
                self.assertEqual(self.s.settings()["macro 1 1"] - 99, offset)

    def test_a_field_leaves_the_rest_of_its_setting(self):
        self.s.setting("link", 64)  # the filter
        self.nrpn("link amp-env", 1)
        self.assertEqual(self.s.settings()["link"], 65)
        self.s.setting("voice 1", 1 | 2 << 2)  # mono, high note
        self.nrpn("voice", 2, rule=1)
        self.assertEqual(self.s.settings()["voice 1"], 2 | 2 << 2, "legato, the priority kept")
        self.s.setting("env-curve 1", 0b10100110)  # swell, swell, linear, swell (env 4 to amp)
        self.nrpn("env-curve filter", 0, rule=1)
        self.assertEqual(self.s.settings()["env-curve 1"], 0b10100010, "the filter's two bits alone")

    def test_a_linked_layer_2_reaches_layer_1s_envelope_and_lfo(self):
        self.s.setting("layers", 2)
        self.s.setting("link", 1 | 16)  # the amp envelope, LFO 1
        self.s.setting("edit-layer", 1)
        self.nrpn("env-delay amp", 30)
        self.nrpn("shape 1", 4)
        st = self.s.settings()
        self.assertEqual((st["env-delay 1 amp"], st["env-delay 2 amp"]), (30, 0))
        self.assertEqual((st["shape 1 1"], st["shape 2 1"]), (4, 0))
        self.nrpn("env-mode amp", 0, rule=3)
        self.s.cc(96, 0)
        self.s.ms(20)
        self.assertEqual(self.s.settings()["dahdsr 1"] & 1, 1, "both layers, one linked value: one step (ADSR to DAHDSR)")
        self.assertEqual(self.s.settings()["cycle 1"] & 1, 0, "not two (cycling)")

    def test_an_envelope_mode(self):
        self.nrpn("env-mode env3", 2 + 7, rule=2)  # synced cycling at the seventh division
        st = self.s.settings()
        self.assertEqual((st["cycle 2"] >> 2 & 1, st["cycle-sync 2 env3"]), (1, 7))
        self.nrpn("env-mode env3", 1, rule=2)
        st = self.s.settings()
        self.assertEqual((st["cycle 2"] >> 2 & 1, st["dahdsr 2"] >> 2 & 1), (0, 1))

    def test_the_layer_rules(self):
        self.s.setting("layers", 2)
        before = self.param(0, "cutoff")
        self.nrpn("cutoff", 10, rule=2)
        self.assertEqual((self.param(0, "cutoff"), self.param(1, "cutoff")), (before, 10 * 16383 // 127), "2: layer 2 alone")
        self.nrpn("cutoff", 20, rule=3)
        self.assertEqual((self.param(0, "cutoff"), self.param(1, "cutoff")), (20 * 16383 // 127,) * 2)
        self.s.setting("edit-layer", 1)
        self.nrpn("cutoff", 30)
        self.assertEqual((self.param(0, "cutoff"), self.param(1, "cutoff")), (20 * 16383 // 127, 30 * 16383 // 127), "0: the edit layer")
        self.s.setting("layers", 3)  # channel: channel 2 plays layer 2
        self.s.setting("edit-layer", 0)
        self.nrpn("cutoff", 40, channel=1)
        self.assertEqual(self.param(1, "cutoff"), 40 * 16383 // 127)

    def test_a_linked_layer_2_edits_layer_1_as_its_knob_does(self):
        self.s.setting("layers", 2)
        self.s.setting("link", 64)  # the filter
        self.s.setting("edit-layer", 1)
        self.nrpn("cutoff", 50)
        self.assertEqual(self.param(0, "cutoff"), 50 * 16383 // 127)
        self.s.cc(74, 100)
        self.s.ms(20)
        self.assertLessEqual(abs(self.param(0, "cutoff") - round(100 / 127 * 16383)), 1, "a CC too")

    def test_an_rpn_or_nothing_selected_does_nothing(self):
        before = self.s.settings()["layers"]
        self.s.cc(6, 3)
        self.s.ms(20)
        self.assertEqual(self.s.settings()["layers"], before, "nothing selected")
        self.nrpn("layers")
        self.s.cc(101, 0)
        self.s.cc(100, 0)
        self.s.cc(6, 3)
        self.s.ms(20)
        self.assertEqual(self.s.settings()["layers"], before, "an RPN deselects it")
        knobs = self.s.reply(0x53, 0)
        self.nrpn(20, 50)  # master volume: the panel's alone
        self.nrpn(124, 50)
        self.assertEqual(self.s.reply(0x53, 0), knobs, "neither an id left out nor one past the list sets anything")

    def test_increment_and_decrement(self):
        self.nrpn("drift", 10)
        self.s.cc(96, 0)
        self.s.cc(96, 0)
        self.s.cc(97, 0)
        self.s.ms(20)
        self.assertEqual(self.s.settings()["drift"], 11)

    def test_per_channel_and_din(self):
        self.s.cc(99, 0, 0)
        self.s.cc(98, self.ID["drift"], 0)
        self.s.cc(98, self.ID["detune"], 1)
        self.s.cc(6, 33, 0)
        self.s.ms(20)
        self.assertEqual((self.s.settings()["drift"], self.s.settings()["detune"]), (33, 14), "each channel its own")
        self.s.din(0xB0, 99, 0, 98, self.ID["drift"], 6, 44)  # running status
        self.s.ms(20)
        self.assertEqual(self.s.settings()["drift"], 44)

    def test_the_host_shows_any_parameter(self):
        self.s.request(0x6B, 1, self.ID["split"])
        self.s.ms(60)
        self.assertShows("SPLt")
        self.s.ms(500)
        self.assertShows("  60")
        self.s.ms(1600)
        self.s.request(0x6B, 1, 124)
        self.s.ms(60)
        self.assertShows("P  1", "no such parameter")

    def test_not_over_a_menu(self):
        self.s.press("SETTINGS")
        self.nrpn("drift", 5)
        self.s.ms(600)
        self.assertShows("SEt")


class CcMap(PanelTest):
    """#37: the user's CC map (65 / 43 / 42), stored at 0x1F420; by default the table the firmware always had."""

    ID = nrpn_ids()
    DEFAULT = {5: "glide", 14: "freq-a", 15: "freq-b", 16: "fine", 17: "a-pw", 18: "b-pw", 19: "a-level", 20: "b-level",
               21: "noise", 74: "cutoff", 71: "resonance", 22: "fil-env-amt", 23: "fil-att", 24: "fil-dec", 25: "fil-sus",
               26: "fil-rel", 73: "amp-att", 75: "amp-dec", 70: "amp-sus", 72: "amp-rel", 76: "lfo-freq", 77: "lfo-amount",
               27: "lfo2-rate", 28: "lfo2-amount", 29: "pmod-fil-env", 30: "pmod-osc-b", 31: "macro1", 85: "env3-att",
               86: "env3-dec", 87: "env3-sus", 88: "env3-rel", 89: "env4-att", 90: "env4-dec", 102: "env4-sus", 103: "env4-rel"}

    def read(self):
        ids, nibbles = [], []
        for q in range(4):
            d = self.s.reply(0x65, q)
            ids += list(d[1:65:2])
            nibbles += list(d[2:66:2])
        return ids, nibbles

    def set(self, cc, name, nibble=0):
        out = self.s.request(0x43, cc, 127 if name is None else self.ID[name], nibble)
        return pro800.SYSEX_HEADER + bytes([0x7F, 0x43]) in out

    def param(self, layer, name):
        d = self.s.reply(0x53, layer)
        i = pro800.PARAM_NAMES.index(name)
        return d[2 + 2 * i] << 7 | d[3 + 2 * i]

    def test_the_default_is_the_old_table(self):
        ids, nibbles = self.read()
        self.assertEqual({cc: i for cc, i in enumerate(ids) if i != 127}, {cc: self.ID[n] for cc, n in self.DEFAULT.items()})
        self.assertEqual(set(nibbles), {0})

    def test_a_mapped_setting_steps_through_its_range(self):
        self.assertTrue(self.set(9, "shape 2", 1))  # layer 1
        shown = []
        for v in (0, 15, 14 * 9, 127):
            self.s.cc(9, v)
            self.s.ms(20)
            shown.append(self.s.settings()["shape 1 2"])
        self.assertEqual(shown, [0, 1, 8, 8], "index = v x 9 / 128")
        self.s.ms(60)
        self.assertShows("SH.L2", "a setting's name first")

    def test_a_button_on_from_64(self):
        self.set(9, "button a-saw")
        for v, on in ((63, 0), (64, 1), (0, 0)):
            self.s.cc(9, v)
            self.s.ms(20)
            self.assertEqual(self.s.reply(0x4C)[0], on)

    def test_rules_and_an_empty_slot(self):
        self.s.setting("layers", 2)
        self.set(9, "cutoff", 3)  # both
        self.s.cc(9, 100)
        self.s.ms(20)
        self.assertEqual(self.param(0, "cutoff"), self.param(1, "cutoff"))
        self.set(74, None)
        before = self.param(0, "cutoff")
        self.s.cc(74, 0)
        self.s.ms(20)
        self.assertEqual(self.param(0, "cutoff"), before, "CC 74 cleared: nothing")
        self.s.setting("layers", 3)  # channel
        self.set(9, "cutoff", 0)
        self.s.cc(9, 20, channel=1)
        self.s.ms(20)
        self.assertEqual(self.param(1, "cutoff"), 20 * 16383 // 127, "rule edit, channel 2: layer 2")

    def test_fine_makes_14_bits(self):
        self.set(15, "cutoff")
        self.set(87, "cutoff", 4)  # stock's pair
        self.s.cc(15, 64)
        self.s.cc(87, 5)
        self.s.ms(20)
        self.assertEqual(self.param(0, "cutoff"), 64 << 7 | 5)
        self.assertFalse(self.set(88, "layers", 4), "fine is for a knob or a macro offset")

    def test_a_fine_cc_alone_nudges_the_value(self):
        """As on the stock synth: a fine CC is the low 7 of the parameter's 14 bits as they are, not under a coarse 0."""
        self.set(87, "cutoff", 4)
        self.s.param(0, "cutoff", 10000)
        self.s.cc(87, 5)
        self.s.ms(20)
        self.assertEqual(self.param(0, "cutoff"), (10000 & ~0x7F) | 5)
        self.set(88, "macro 1", 4)
        self.s.setting("macro 1 1", 99 + 50)
        v14 = round(50 * 8191 / 99) + 8192
        self.s.cc(88, v14 & 0x7F)
        self.s.ms(20)
        self.assertEqual(self.s.settings()["macro 1 1"] - 99, 50, "a macro offset keeps its value under the same low bits")

    def test_refused(self):
        for cc in (0, 1, 6, 32, 38, 64, 96, 101, 120, 127):
            with self.subTest(cc=cc):
                self.assertFalse(self.set(cc, "cutoff"))
        self.assertNotIn(pro800.SYSEX_HEADER + bytes([0x7F, 0x43]), self.s.request(0x43, 9, 124, 0), "no such parameter")
        self.assertNotIn(pro800.SYSEX_HEADER + bytes([0x7F, 0x43]), self.s.request(0x43, 9, 24, 8), "no such nibble")

    def test_cc_a_comes_first(self):
        self.set(9, "cutoff")
        self.s.setting("cc-a", 9)
        before = self.param(0, "cutoff")
        self.s.cc(9, 0)
        self.s.ms(20)
        self.assertEqual(self.param(0, "cutoff"), before)
        self.s.setting("cc-a", 11)

    def test_kept_after_a_power_cycle_and_a_format_once_written(self):
        self.s.cmd("writes")
        for v in range(20):
            self.set(9, "cutoff" if v % 2 else "shape 2")  # the last one: cutoff
        self.s.ms(1100)
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.read()[0][9], self.ID["cutoff"])
        self.s.request(0x5B)
        self.s.ms(1100)
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.read()[0][9], self.ID["cutoff"], "a format leaves it")
        self.assertIn(pro800.SYSEX_HEADER + bytes([0x7F, 0x42]), self.s.request(0x42))
        self.s.ms(1100)
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.read()[0][9], 127, "the default again, across a power cycle")

    def test_a_damaged_block_is_the_default(self):
        self.set(9, "cutoff")
        self.s.ms(1100)
        self.s.close()
        with open(self.path, "r+b") as f:
            f.seek(0x1F420 + 4 + 9)
            f.write(bytes([self.ID["resonance"]]))  # the checksum no longer agrees
        self.s = Synth(self.path)
        self.s.ms(1600)
        ids, _ = self.read()
        self.assertEqual((ids[9], ids[74]), (127, self.ID["cutoff"]))

    def test_one_write_after_a_burst(self):
        def count():
            return int(self.s.cmd("writes").split()[0])
        self.s.ms(1100)
        start = count()
        for v in range(20):
            self.set(9, "cutoff", v % 4)
        self.s.ms(1100)
        self.assertEqual(count() - start, 1, "the map once, a second after the last change")


def channel_messages(data):
    """The channel and realtime messages in a byte stream, as tuples; SysEx (the replies) left out."""
    out, i = [], 0
    while i < len(data):
        b = data[i]
        if b == 0xF0:
            i = data.index(0xF7, i) + 1
            continue
        if b >= 0xF8:
            out.append((b,))
            i += 1
            continue
        n = 2 if (b & 0xE0) == 0xC0 else 3
        out.append(tuple(data[i:i + n]))
        i += n
    return out


class MidiOut(PanelTest):
    """#38: what the synth sends besides its replies, on USB and the THRU socket (din.c, midiout.c)."""

    def usb(self):
        return channel_messages(bytes.fromhex(self.s.cmd("out")))

    def din(self):
        return bytes.fromhex(self.s.cmd("dinout"))

    def socket(self):
        return [int(x) for x in self.s.cmd("socket").split()]

    def setUp(self):
        super().setUp()
        self.s.cmd("out")
        self.s.cmd("dinout")

    def test_quiet_by_default(self):
        self.assertEqual(self.socket(), [0, 0], "THRU, MIDI_SEL at its THRU level")
        self.s.note(60)
        self.s.cc(74, 30)
        self.s.ms(50)
        self.s.note_off(60)
        self.s.ms(50)
        self.assertEqual((self.usb(), self.din()), ([], b""))

    def test_the_socket_setting_is_kept(self):
        self.s.setting("midi-socket", 1)
        self.assertEqual(self.socket(), [1, 1], "OUT: MIDI_SEL holds U5A's copy off")
        self.s.ms(1100)
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.socket(), [1, 1])
        self.assertEqual(self.s.settings()["midi-socket"], 1)

    def test_played_notes_once_each(self):
        self.s.setting("notes-out", 2)
        self.s.setting("midi-socket", 1)
        self.s.cmd("out")
        self.s.switch("UNISON")  # eight voices on one note: one note out
        self.s.note(60, 90)
        self.s.ms(10)
        self.s.note_off(60)
        self.s.ms(10)
        self.assertEqual(self.usb(), [(0x90, 60, 90), (0x80, 60, 0)])
        self.assertEqual(self.din(), bytes([0x90, 60, 90, 0x80, 60, 0]), "the socket too, in OUT")
        self.s.switch("UNISON")

    def test_played_follows_a_chord_and_the_out_channel(self):
        self.s.setting("notes-out", 2)
        self.s.setting("out-channel", 5)
        self.s.cmd("out")
        self.s.request(0x69, 0, 4, 7, 0, 0, 0, 0, 0, 0)
        self.s.request(0x4B, 12, 1)  # Unison on: the chord plays
        self.s.ms(20)
        self.s.cmd("out")
        self.s.note(60, 100)
        self.s.ms(10)
        ons = sorted(m for m in self.usb() if m[0] == 0x94)
        self.assertEqual(ons, [(0x94, 60, 100), (0x94, 64, 100), (0x94, 67, 100)])
        self.s.note_off(60)
        self.s.ms(10)
        self.assertEqual(sorted(m[1] for m in self.usb() if m[0] == 0x84), [60, 64, 67])

    def test_keys_never_go_back_where_they_came_from(self):
        self.s.setting("notes-out", 1)
        self.s.setting("midi-socket", 1)
        self.s.cmd("out")
        self.s.din(0x90, 62, 80)
        self.s.ms(10)
        self.assertEqual((self.usb(), self.din()), ([(0x90, 62, 80)], b""), "from the socket: out on USB only")
        self.s.note(64, 70)
        self.s.ms(10)
        self.assertEqual((self.usb(), self.din()), ([], bytes([0x90, 64, 70])), "from USB: out on the socket only")

    def test_channel_mode_sends_layer_2_one_higher(self):
        self.s.setting("layers", 3)  # channel
        self.s.setting("notes-out", 2)
        self.s.cmd("out")
        self.s.note(60, 100, channel=1)
        self.s.ms(10)
        self.assertIn((0x91, 60, 100), self.usb())

    def test_a_patch_load_and_cc_123_end_what_went_out(self):
        self.s.setting("notes-out", 2)  # played: a load silences the voices, and their notes end out too
        self.s.cmd("out")
        for n in (60, 64):
            self.s.note(n)
        self.s.ms(10)
        self.usb()
        out = channel_messages(self.s.request(0x5C, 0, 3))  # (a request reads what went out meanwhile)
        self.s.ms(20)
        out += self.usb()
        self.assertEqual(sorted(m for m in out if m[0] == 0x80), [(0x80, 60, 64), (0x80, 64, 64)])
        self.s.setting("notes-out", 1)  # keys: no voice says when they stop, a CC 123 does
        self.s.din(0x90, 67, 100)  # from the socket: out on USB
        self.s.ms(10)
        self.usb()
        self.s.cc(123, 0)
        self.s.ms(20)
        self.assertEqual([m for m in self.usb() if m[0] == 0x80], [(0x80, 67, 64)])
        self.s.din(0x90, 69, 100)
        self.s.ms(10)
        self.usb()
        self.s.hold("SHIFT")
        self.s.press("0")  # the panic
        self.s.let_go("SHIFT")
        self.s.ms(20)
        self.assertEqual([m for m in self.usb() if m[0] == 0x80], [(0x80, 69, 64)], "and the panel's panic")

    def test_a_usb_host_that_stops_reading(self):
        self.s.setting("notes-out", 2)
        self.s.cmd("out")
        self.s.note(60)
        self.s.ms(10)
        self.assertEqual(self.usb(), [(0x90, 60, 100)])
        self.s.cmd("usbbusy 600")
        for n in range(62, 80):
            self.s.note(n)
            self.s.ms(20)
            self.s.note_off(n)
        self.s.note_off(60)
        self.s.ms(400)
        out = self.usb()
        self.assertEqual(out, [(0x80, 60, 64)], "the stall dropped what came meanwhile; the note left sounding gets its off")

    def test_merge_forwards_whole_messages(self):
        self.s.setting("midi-socket", 2)
        self.s.setting("pc-out", 1)  # the synth's own message here: a program change as a patch loads
        self.s.cmd("dinout")
        sysex = bytes([0xF0, 0x7D] + list(range(1, 59)) + [0xF7])  # 20 ms at 31250 baud
        self.s.din(*sysex)
        self.s.request(0x5C, 0, 3)  # while the SysEx is still going out
        self.s.din(0x90, 50, 40, 0xF8, 51, 41)  # a clock byte inside, then running status
        self.s.ms(40)
        out = self.din()
        self.assertEqual(out.count(0xF8), 1, "the forwarded clock once (realtime: wherever it lands, even in a SysEx)")
        self.assertEqual(out.replace(bytes([0xF8]), b""), sysex + bytes([0xC0, 3, 0x90, 50, 40, 51, 41]),
                         "the SysEx whole, ours after its F7, the forwarded notes with their running status")

    def test_merge_puts_the_status_back_after_ours(self):
        self.s.setting("midi-socket", 2)
        self.s.setting("pc-out", 1)
        self.s.cmd("dinout")
        self.s.din(0x90, 50, 40)
        self.s.ms(5)
        self.s.request(0x5C, 0, 3)
        self.s.ms(5)
        self.s.din(51, 41)  # running status on the IN socket, after the synth's own message went out between
        self.s.ms(20)
        self.assertEqual(self.din(), bytes([0x90, 50, 40, 0xC0, 3, 0x90, 51, 41]), "the forwarded note gets its status again")

    def test_program_change_out(self):
        self.s.setting("pc-out", 1)
        self.s.setting("out-channel", 3)
        self.s.cmd("out")
        out = channel_messages(self.s.request(0x5C, 0, 9))
        self.s.ms(10)
        self.assertIn((0xC2, 9), out + self.usb())
        self.s.midi(0xC0, 4)
        self.s.ms(20)
        self.assertNotIn((0xC2, 4), self.usb(), "one a program change loaded doesn't go back out")

    def test_a_usb_stall_with_nothing_owed_ends_when_the_host_reads(self):
        """The clock alone going out, then a host that takes nothing: once it reads again, the clock does too."""
        self.s.setting("clock", 1)
        self.s.setting("clock-out", 1)
        self.s.ms(100)
        self.usb()
        self.s.cmd("usbbusy 400")
        self.s.ms(450)
        self.usb()
        self.s.ms(200)
        self.assertGreater(self.usb().count((0xF8,)), 5, "the clock again (no note was sounding, so nothing was owed)")

    def test_out_then_thru_sends_the_offs_first(self):
        self.s.setting("midi-socket", 1)
        self.s.setting("notes-out", 2)
        self.s.note(60)
        self.s.ms(10)
        self.din()
        self.s.setting("midi-socket", 0)
        self.s.ms(30)
        self.assertEqual(self.din(), bytes([0x80, 60, 64]), "the note the socket sent ends before it gives the IN socket's")
        self.assertEqual(self.socket(), [0, 0])
        self.assertEqual(self.s.settings()["midi-socket"], 0)

    def test_a_forwarded_sysex_left_unfinished_doesnt_block_the_synths_own(self):
        self.s.setting("notes-out", 2)
        self.s.setting("midi-socket", 2)
        self.s.din(0xF0, 0x7D, 1, 2)  # no F7: the cable pulled mid-dump
        self.s.ms(10)
        self.s.setting("midi-socket", 1)
        self.din()
        self.s.note(60)
        self.s.ms(10)
        self.assertEqual(self.din(), bytes([0x90, 60, 100]), "OUT forwards nothing, so nothing of it holds ours")
        self.s.note_off(60)
        self.s.setting("midi-socket", 2)
        self.s.din(0xF0, 0x7D, 1)  # and in MERGE: given up after 50 ms with nothing more
        self.s.ms(10)
        self.din()
        self.s.note(62)
        self.s.ms(100)
        self.assertIn(bytes([0x90, 62, 100]), self.din())

    def test_a_channel_change_ends_the_notes_on_the_old_one(self):
        self.s.setting("notes-out", 2)
        self.s.note(60)
        self.s.ms(10)
        self.usb()
        out = channel_messages(self.s.setting("midi-channel", 3))  # the out channel follows it: 1 before, 3 after
        self.s.ms(10)
        self.assertIn((0x80, 60, 64), out + self.usb())

    def test_a_layer_mode_change_ends_the_keys_sent(self):
        self.s.setting("notes-out", 1)
        self.s.setting("midi-socket", 1)
        self.s.din(0x90, 62, 80)
        self.s.ms(10)
        self.usb()
        out = channel_messages(self.s.setting("layers", 3))  # channel mode: the key's off would go out on another channel
        self.s.ms(10)
        self.assertIn((0x80, 62, 64), out + self.usb())

    def test_played_notes_in_8ch_and_mpe_go_out_on_each_voices_channel(self):
        self.s.setting("notes-out", 2)
        for mode, channel in ((4, 3), (5, 2)):  # 8CH: channel 4 plays voice 4; MPE: member 2 plays voice 2
            with self.subTest(mode=mode):
                self.s.setting("layers", mode)
                self.s.ms(10)
                self.usb()
                self.s.note(60, 100, channel=channel)
                self.s.ms(10)
                self.assertIn((0x90 | channel, 60, 100), self.usb())
                self.s.note_off(60, channel=channel)
                self.s.ms(10)

    def test_a_flood_of_keys_leaves_no_note_hanging_on_the_socket(self):
        """60 keys down and up at once from USB, to a socket that takes a note a millisecond: what doesn't fit is dropped,
        and every note that went out gets its off after its on (an off with no room takes its own queued on with it)."""
        self.s.setting("notes-out", 1)
        self.s.setting("midi-socket", 1)
        self.din()
        self.s.midi(*[b for n in range(30, 90) for b in (0x90, n, 100)])
        self.s.midi(*[b for n in range(30, 90) for b in (0x80, n, 0)])
        self.s.ms(400)
        out, last = self.din(), {}
        for i in range(0, len(out), 3):
            last[out[i + 1]] = out[i] & 0xF0
        self.assertGreater(len(last), 20)
        self.assertEqual([n for n, kind in last.items() if kind == 0x90], [], "notes left sounding")

    def test_two_ons_of_a_note_in_the_flood_still_end(self):
        """A source that sends a note on twice before its off: the off with no room takes both queued ons with it."""
        self.s.setting("notes-out", 1)
        self.s.setting("midi-socket", 1)
        self.din()
        self.s.midi(*[b for n in range(30, 90) for b in (0x90, n, 100, 0x90, n, 90)])
        self.s.midi(*[b for n in range(30, 90) for b in (0x80, n, 0)])
        self.s.ms(500)
        out, last = self.din(), {}
        for i in range(0, len(out), 3):
            last[out[i + 1]] = out[i] & 0xF0
        self.assertEqual([n for n, kind in last.items() if kind == 0x90], [], "notes left sounding")

    def test_the_sysex_tune_ends_the_keys_sent(self):
        self.s.setting("notes-out", 1)
        self.s.setting("midi-socket", 1)
        self.s.note(64)  # from USB: out on the socket
        self.s.ms(10)
        self.din()
        self.s.request(0x75)  # the web page's Autotune
        self.s.ms(10)
        self.assertIn(bytes([0x80, 64, 64]), self.din())

    def test_a_slow_forwarded_sysex_goes_through_whole(self):
        """Nothing of the synth's waits behind it: a SysEx with gaps longer than 50 ms isn't cut."""
        self.s.setting("midi-socket", 2)
        self.din()
        self.s.din(0xF0, 0x7D, 1)
        self.s.ms(100)
        self.s.din(2, 3, 0xF7)
        self.s.ms(10)
        self.assertEqual(self.din(), bytes([0xF0, 0x7D, 1, 2, 3, 0xF7]))

    def test_an_in_clock_doesnt_keep_an_unfinished_sysex_going(self):
        self.s.setting("notes-out", 2)
        self.s.setting("midi-socket", 2)
        self.s.din(0xF0, 0x7D, 1)  # and the dump stops, while the device's clock goes on
        self.s.ms(5)
        self.s.note(62)
        for _ in range(10):
            self.s.din(0xF8)
            self.s.ms(20)
        self.assertIn(bytes([0x90, 62, 100]), self.din())

    def test_a_full_forward_buffer_loses_whole_messages(self):
        """In running status: a data byte lost to a full ring would pair the rest up wrongly, so the rest of that run goes
        too, up to the next status."""
        self.s.setting("midi-socket", 2)
        self.din()
        first = [0x90] + [b for n in range(63) for b in (30 + n % 60, 100)]  # 127 bytes: the ring full
        self.s.din(*first)
        self.s.din(0x30)  # no room: lost
        self.s.ms(2)  # a few bytes go out: room again
        self.s.din(0x31, 0x32, 0x33)  # the run goes on: these would pair up as 31 32, 33 ...
        self.s.din(0x80, 0x10, 0)
        self.s.ms(100)
        self.assertEqual(self.din(), bytes(first + [0x80, 0x10, 0]))

    def test_back_to_thru_gives_the_receiver_the_in_streams_status(self):
        """OUT: the receiver's last status is the synth's; a keyboard on IN in running status goes on under its own."""
        self.s.setting("notes-out", 2)
        self.s.setting("midi-socket", 1)
        self.s.din(0x91, 50, 40)  # the keyboard's status: 91 (OUT doesn't forward it, the synth plays it)
        self.s.ms(10)
        self.s.note_off(50, channel=1)
        self.s.note(60)
        self.s.ms(10)
        self.din()
        self.s.setting("midi-socket", 0)
        self.s.ms(30)
        out = self.din()
        self.assertEqual(out[-1:], bytes([0x91]), out.hex())
        self.assertEqual(self.socket(), [0, 0])

    def test_out_channel_16_in_8ch_stays_on_16(self):
        self.s.setting("notes-out", 2)
        self.s.setting("out-channel", 16)
        self.s.setting("layers", 4)
        self.s.ms(10)
        self.usb()
        self.s.note(60, 100, channel=3)
        self.s.ms(10)
        self.assertIn((0x9F, 60, 100), self.usb())

    def test_a_midi_channel_change_ends_the_keys_on_a_fixed_out_channel(self):
        self.s.setting("notes-out", 1)
        self.s.setting("out-channel", 1)
        self.s.setting("midi-socket", 1)
        self.s.din(0x92, 62, 80)  # all channels: the key on channel 3 goes out on 1
        self.s.ms(10)
        self.assertIn((0x90, 62, 80), self.usb())
        out = channel_messages(self.s.setting("midi-channel", 1))  # its off on channel 3 won't be listened to now
        self.s.ms(10)
        self.assertIn((0x80, 62, 64), out + self.usb())

    def test_program_change_out_on_a_stored_patch(self):
        self.s.request(0x5D, 0, 6)  # the init sound, stored in P7
        self.s.ms(20)
        self.s.setting("pc-out", 1)
        out = channel_messages(self.s.request(0x5C, 0, 6))
        self.s.ms(10)
        self.assertIn((0xC0, 6), out + self.usb())

    def test_the_system_menus_key_8(self):
        self.s.hold("SETTINGS")
        self.s.ms(700)
        self.s.let_go("SETTINGS")
        self.s.press("8")
        self.assertShows("dIn")
        self.s.ms(800)
        self.assertShows("tHru")
        self.s.turn(1)
        self.assertShows("Out")
        self.assertEqual(self.socket()[0], 1)
        self.s.press("ARP_UP")
        self.assertShows("nOtE")
        self.s.ms(800)
        self.assertShows("OFF")
        self.s.press("ARP_UP")
        self.assertShows("CH.ou")
        self.s.ms(800)
        self.assertShows("FoL")
        self.s.press("SETTINGS")


class ClockAndControllersOut(PanelTest):
    """#38: clock out (the generator's, or the followed MIDI clock forwarded), knob moves as CCs, CC A / B out."""

    def usb(self):
        return channel_messages(bytes.fromhex(self.s.cmd("out")))

    def din(self):
        return bytes.fromhex(self.s.cmd("dinout"))

    def setUp(self):
        super().setUp()
        self.s.setting("clock", 1)  # internal
        self.s.setting("bpm", 120)
        self.s.cmd("out")
        self.s.cmd("dinout")

    def test_internal_clock_out(self):
        self.s.ms(500)
        self.assertEqual([m for m in self.usb() if m == (0xF8,)], [], "off: nothing")
        out = channel_messages(self.s.setting("clock-out", 1))
        self.s.ms(500)
        out += self.usb()
        self.assertEqual(out[0], (0xFA,), "on: it starts the receivers")
        self.assertLessEqual(abs(out.count((0xF8,)) - 24), 1, "24 a quarter note at 120 BPM: 24 in 500 ms")
        out = channel_messages(self.s.setting("clock", 0))  # MIDI: the synth isn't the clock any more
        self.s.ms(100)
        self.assertEqual(out + self.usb(), [(0xFC,)])

    def test_the_socket_gets_it_in_out(self):
        self.s.setting("midi-socket", 1)
        self.s.setting("clock-out", 1)
        self.s.ms(500)
        din = self.din()
        self.assertEqual(din[0], 0xFA)
        self.assertLessEqual(abs(din.count(0xF8) - 24), 1)

    def test_a_followed_clock_goes_on_to_the_other_port(self):
        self.s.setting("clock", 0)
        self.s.setting("midi-socket", 1)
        self.s.setting("clock-out", 1)
        self.s.cmd("out")
        self.s.cmd("dinout")
        self.s.midi(0xF8)
        self.s.midi(0xF8)
        self.s.ms(10)
        self.assertEqual((self.din(), self.usb()), (bytes([0xF8, 0xF8]), []), "USB's to the socket, not back")
        self.s.din(0xFA, 0xF8)
        self.s.ms(10)
        self.assertEqual((self.usb(), self.din()), ([(0xFA,), (0xF8,)], b""), "the socket's to USB")

    def test_merge_leaves_out_the_input_clock_while_sending_its_own(self):
        self.s.setting("midi-socket", 2)
        self.s.setting("clock-out", 1)
        self.s.ms(100)
        self.din()
        self.s.din(0xF8, 0xF8, 0xF8, 0x90, 60, 1)
        self.s.ms(1)
        self.assertNotIn(bytes([0xF8, 0xF8, 0xF8]), self.din(), "the IN socket's clock isn't merged into the synth's")

    def test_knob_moves_out_after_pickup(self):
        self.s.setting("knob-cc", 1)
        self.s.request(0x5C, 0, 3)  # the init sound: CUTOFF waits at 99 for its knob
        self.s.ms(20)
        self.usb()
        self.s.pot("CUTOFF", 0.5)
        self.s.ms(100)
        self.assertEqual([m for m in self.usb() if m[0] == 0xB0], [], "in pickup: nothing")
        for v in (0.8, 1.0, 0.7):  # through 99: it takes over
            self.s.pot("CUTOFF", v)
            self.s.ms(200)
        out = [m for m in self.usb() if m[0] == 0xB0]
        self.assertTrue(out and all(m[1] == 74 for m in out), out)
        self.assertEqual(out[-1][2], round(self.s.params_raw()["cutoff"] / 16383 * 127))

    def test_a_knobs_own_cc_coming_back_changes_nothing(self):
        self.s.setting("knob-cc", 1)
        self.s.pot("CUTOFF", 1.0)  # takes over at the init sound's 99
        self.s.ms(300)
        self.usb()
        for v in (0.8, 0.7, 0.6):
            self.s.pot("CUTOFF", v)
        sent = [m[2] for m in self.usb() if m[:2] == (0xB0, 74)]
        self.assertGreater(len(sent), 2)
        for value in (sent[-1], sent[0]):  # a DAW's thru: the last value, and a late one from earlier in the sweep
            self.s.cc(74, value)
        self.assertAlmostEqual(self.s.params_raw()["cutoff"] / 16383, 0.6, delta=0.01, msg="the knob keeps it")
        self.s.pot("CUTOFF", 0.5)
        self.s.ms(100)
        self.assertTrue([m for m in self.usb() if m[:2] == (0xB0, 74)], "and goes on sending: no pickup")

    def test_the_echo_is_the_knobs_layers_only(self):
        """Channel mode: turning layer 1's cutoff leaves CC 74 on layer 2's channel working."""
        self.s.setting("layers", 3)
        self.s.setting("knob-cc", 1)
        self.s.pot("CUTOFF", 1.0)
        self.s.ms(300)
        self.s.pot("CUTOFF", 0.7)
        self.s.cc(74, 10, channel=1)  # within the knob's window, for layer 2
        self.assertAlmostEqual(self.s.params_raw(1)["cutoff"] / 16383, 10 / 127, delta=0.01)

    def test_a_knob_resting_on_a_steps_edge_sends_nothing(self):
        self.s.setting("knob-cc", 1)
        self.s.pot("CUTOFF", 1.0)
        self.s.ms(300)
        edge = round(63.5 / 127 * 4095)
        self.s.cmd(f"pot {POT['CUTOFF']} {edge}")
        self.s.ms(400)
        self.usb()
        for _ in range(10):  # its noise, a fifth of a step either way
            for n in (edge - 6, edge + 6):
                self.s.cmd(f"pot {POT['CUTOFF']} {n}")
                self.s.ms(30)
        self.assertLessEqual(len([m for m in self.usb() if m[:2] == (0xB0, 74)]), 1)

    def test_cc_out_sends_its_value_again_when_the_host_reads_again(self):
        """The value changed while USB was stalled (and dropped): it goes out once the host reads again."""
        self.s.no_routes()
        self.s.setting("notes-out", 2)
        fixed, out_a = pro800.SOURCE_NAMES.index("fixed"), pro800.DEST_NAMES.index("cc-out-a")
        self.s.request(0x56, 0, fixed, out_a, (50 + 99) >> 7, (50 + 99) & 0x7F)
        self.s.ms(50)
        self.usb()
        self.s.cmd("usbbusy 400")
        self.s.note(60)  # waiting: the stall comes after 100 ms
        self.s.ms(200)
        self.s.cmd("midi " + (pro800.SYSEX_HEADER + bytes([0x56, 0, fixed, out_a, (99 + 99) >> 7, (99 + 99) & 0x7F, 0xF7])).hex())
        self.s.ms(300)
        self.assertIn((0xB0, 12, 127), self.usb())

    def test_cc_out_follows_the_out_channel(self):
        self.s.no_routes()
        fixed, out_a = pro800.SOURCE_NAMES.index("fixed"), pro800.DEST_NAMES.index("cc-out-a")
        self.s.request(0x56, 0, fixed, out_a, (50 + 99) >> 7, (50 + 99) & 0x7F)
        self.s.ms(50)
        self.usb()
        out = channel_messages(self.s.setting("out-channel", 5))
        self.s.ms(50)
        self.assertEqual([m for m in out + self.usb() if m[0] & 0xF0 == 0xB0], [(0xB4, 12, 64)])

    def test_sync_in_stopping_runs_on_without_a_burst(self):
        self.s.setting("clock", 2)
        self.s.setting("clock-out", 1)
        for _ in range(4):
            self.s.cmd("sw 22 1")
            self.s.ms(5)
            self.s.cmd("sw 22 0")
            self.s.ms(245)
        self.usb()  # the last pulse was 250 ms ago: its 12 have gone
        self.s.ms(740)  # a second after it, less a little: still waiting
        self.assertEqual(self.usb().count((0xF8,)), 0)
        self.s.ms(60)
        self.assertLessEqual(self.usb().count((0xF8,)), 4, "no burst of the ticks the wait missed")
        self.s.ms(500)
        self.assertLessEqual(abs(self.usb().count((0xF8,)) - 24), 2, "on at the last tempo: 120 BPM")

    def test_after_a_rest_a_cc_sets_the_knobs_parameter_again(self):
        self.s.setting("knob-cc", 1)
        self.s.pot("CUTOFF", 1.0)
        self.s.ms(300)
        self.s.pot("CUTOFF", 0.7)
        self.s.ms(300)
        sent = [m for m in self.usb() if m[:2] == (0xB0, 74)][-1][2]
        knob = self.s.params_raw()["cutoff"]
        self.s.ms(500)
        self.s.cc(74, sent)  # the same value, from a controller or a DAW's automation, long after
        raw = self.s.params_raw()["cutoff"]
        self.assertGreater(abs(knob - round(sent / 127 * 16383)), 5, "(the CC's value isn't the knob's exactly)")
        self.assertLessEqual(abs(raw - round(sent / 127 * 16383)), 2, "it takes the CC's value")

    def test_knob_moves_on_layer_2_go_out_one_channel_higher_in_channel_mode(self):
        self.s.setting("layers", 3)
        self.s.setting("edit-layer", 1)
        self.s.setting("knob-cc", 1)
        self.s.pot("CUTOFF", 1.0)
        self.s.ms(300)
        self.usb()
        self.s.pot("CUTOFF", 0.7)
        self.s.ms(100)
        out = [m for m in self.usb() if m[0] & 0xF0 == 0xB0]
        self.assertTrue(out and all(m[:2] == (0xB1, 74) for m in out), out)

    def test_clock_out_off_stops_the_receivers(self):
        self.s.setting("clock-out", 1)
        self.s.ms(100)
        self.usb()
        out = channel_messages(self.s.setting("clock-out", 0))
        self.s.ms(100)
        out += self.usb()
        self.assertEqual([m for m in out if m != (0xF8,)], [(0xFC,)])
        self.assertLessEqual(out.count((0xF8,)), 1, "and the clock ends")

    def test_sync_in_sends_12_clocks_a_pulse(self):
        """Two pulses a quarter note at the jack: 12 F8s each, never 13, as the tempo moves."""
        self.s.setting("clock", 2)
        self.s.setting("clock-out", 1)
        at_pulse, after = [], []  # F8s as each pulse is taken (its own, and the last half beat's rest if it came early),
        for gap in (250, 250, 250, 240, 230, 260, 250, 210, 270, 250, 250):  # and until the next; ms: 120 BPM, moving
            self.usb()
            self.s.cmd("sw 22 1")
            self.s.ms(4)
            at_pulse.append(self.usb().count((0xF8,)))
            self.s.cmd("sw 22 0")
            self.s.ms(gap - 4)
            after.append(self.usb().count((0xF8,)))
        per_half_beat = [1 + after[i] + at_pulse[i + 1] - 1 for i in range(2, len(after) - 1)]
        self.assertEqual(per_half_beat, [12] * len(per_half_beat), (at_pulse, after))

    def test_cc_a_out_on_both_layers_adds_up(self):
        self.s.no_routes()
        self.s.setting("layers", 2)  # stack
        fixed, out_a = pro800.SOURCE_NAMES.index("fixed"), pro800.DEST_NAMES.index("cc-out-a")
        self.s.request(0x56, 0, fixed, out_a, (30 + 99) >> 7, (30 + 99) & 0x7F)
        out = channel_messages(self.s.request(0x56, 1, fixed, out_a, (20 + 99) >> 7, (20 + 99) & 0x7F))
        self.s.ms(50)
        out += self.usb()
        self.assertEqual([m for m in out if m[0] == 0xB0][-1], (0xB0, 12, round(50 / 99 * 127)))

    def test_a_new_cc_out_number_gets_the_value_at_once(self):
        self.s.no_routes()
        fixed, out_a = pro800.SOURCE_NAMES.index("fixed"), pro800.DEST_NAMES.index("cc-out-a")
        self.s.request(0x56, 0, fixed, out_a, (50 + 99) >> 7, (50 + 99) & 0x7F)
        self.s.ms(50)
        self.usb()
        out = channel_messages(self.s.setting("cc-out-a", 20))
        self.s.ms(50)
        self.assertEqual([m for m in out + self.usb() if m[0] == 0xB0], [(0xB0, 20, 64)])

    def test_a_cc_with_its_own_meaning_cant_be_cc_out(self):
        for cc in (1, 64, 123):
            self.s.setting("cc-out-a", cc)
            self.assertEqual(self.s.settings()["cc-out-a"], 0, cc)

    def test_cc_out_goes_at_most_every_5_ms(self):
        self.s.no_routes()
        lfo, out_b = pro800.SOURCE_NAMES.index("lfo-uni"), pro800.DEST_NAMES.index("cc-out-b")
        self.s.param(0, "lfo-freq", 16383)
        self.s.request(0x56, 0, lfo, out_b, (99 + 99) >> 7, (99 + 99) & 0x7F)
        self.s.ms(100)
        self.usb()
        self.s.ms(100)
        n = len([m for m in self.usb() if m[:2] == (0xB0, 13)])
        self.assertGreater(n, 10)
        self.assertLessEqual(n, 21)

    def test_a_cc_out_route_with_no_note_leaves_the_rest_alone(self):
        """The routes of a layer with no note yet are worked out for CC A / B out only: a volume route waits for a note."""
        self.s.no_routes()
        fixed = pro800.SOURCE_NAMES.index("fixed")
        self.s.request(0x56, 0, fixed, pro800.DEST_NAMES.index("volume"), (99 + 99) >> 7, (99 + 99) & 0x7F)
        self.s.ms(50)
        volume = self.s.cvs()[35]  # CV_MASTER_VOL
        self.s.request(0x56, 0, fixed, pro800.DEST_NAMES.index("cc-out-a"), (50 + 99) >> 7, (50 + 99) & 0x7F)
        self.s.ms(50)
        self.assertEqual(self.s.cvs()[35], volume)

    def test_cc_a_out_from_a_route(self):
        self.s.no_routes()
        fixed, out_a = pro800.SOURCE_NAMES.index("fixed"), pro800.DEST_NAMES.index("cc-out-a")
        out = channel_messages(self.s.request(0x56, 0, fixed, out_a, (50 + 99) >> 7, (50 + 99) & 0x7F))
        self.s.ms(100)
        out += self.usb()
        self.assertEqual([m for m in out if m[0] == 0xB0], [(0xB0, 12, 64)], "once, on CC 12, not every pass")
        self.s.setting("cc-out-a", 20)
        out = channel_messages(self.s.request(0x56, 0, fixed, out_a, (99 + 99) >> 7, (99 + 99) & 0x7F))
        self.s.ms(50)
        out += self.usb()
        self.assertEqual([m for m in out if m[0] == 0xB0], [(0xB0, 20, 127)])

    def test_an_lfo_streams_with_no_note_played(self):
        self.s.no_routes()
        lfo, out_b = pro800.SOURCE_NAMES.index("lfo-uni"), pro800.DEST_NAMES.index("cc-out-b")
        self.s.param(0, "lfo-freq", 14000)
        self.s.request(0x56, 0, lfo, out_b, (99 + 99) >> 7, (99 + 99) & 0x7F)
        self.s.ms(500)
        values = [m[2] for m in self.usb() if m[:2] == (0xB0, 13)]
        self.assertGreater(len(set(values)), 10, "a changing stream from power-up")
        self.s.request(0x56, 0, lfo, out_b, 0, 99)  # removed (5 s at 0)
        self.s.ms(5500)
        self.usb()
        self.s.ms(200)
        self.assertEqual([m for m in self.usb() if m[:2] == (0xB0, 13)], [], "no route: no stream")


class Layers(PanelTest):
    def test_seq_buttons_choose_the_edit_layer(self):
        self.s.press("SEQ2")
        self.assertShows("L  2")
        self.assertTrue(self.s.led("SEQ2"))
        self.assertFalse(self.s.led("SEQ1"))
        self.assertEqual(self.s.settings()["edit-layer"], 1)
        self.s.ms(1100)
        self.assertShows("P  1")
        self.s.press("SEQ1")
        self.assertShows("L  1")
        self.assertTrue(self.s.led("SEQ1"))

    def test_knobs_edit_the_chosen_layer(self):
        self.manual()
        self.s.press("SEQ2")
        self.s.ms(1100)
        self.s.pot("RESONANCE", 0.5)
        self.s.pot("RESONANCE", 0.75)
        self.assertEqual(self.s.params(1)["resonance"], 74)

    def test_holding_seq_and_turning_freq_cycles_the_tuning_mode(self):
        self.s.hold("SEQ1")
        self.s.pot("FREQ_A", 0.2)
        self.assertShows("SEnI")
        for fraction, name in ((0.46, "OCt"), (0.72, "FrEE"), (0.98, "FIHd")):
            self.s.pot("FREQ_A", fraction)
            self.assertShows(name)
        self.s.let_go("SEQ1")
        self.assertEqual(self.s.settings()["osc-tune 1 a"], 3)
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_the_knobs_quarter_picks_the_tuning_mode(self):
        """#34: where the knob is, not how far it moved: SEnI, OCt, FrEE, FIHd from the bottom quarter up."""
        self.s.hold("SEQ1")
        self.s.pot("FREQ_A", 0.6)
        self.assertShows("FrEE", "from the first move")
        self.s.pot("FREQ_A", 0.9)
        self.assertShows("FIHd")
        self.s.pot("FREQ_A", 0.1)
        self.assertShows("SEnI")
        self.s.pot("FREQ_A", 0.3)
        self.assertShows("OCt")
        self.s.let_go("SEQ1")
        self.assertEqual(self.s.settings()["osc-tune 1 a"], 1)

    def test_a_knob_on_a_quarters_edge_doesnt_flicker(self):
        self.s.hold("SEQ1")
        self.s.pot("FREQ_A", 0.6)
        self.assertShows("FrEE")
        self.s.pot("FREQ_A", 2007 / 4095)  # 41 counts below FrEE's quarter: still FrEE
        self.assertShows("FrEE")
        self.s.pot("FREQ_A", 1966 / 4095)  # 82 below
        self.assertShows("OCt")
        self.s.pot("FREQ_A", 2088 / 4095)  # 40 into FrEE's quarter: still OCt
        self.assertShows("OCt")
        self.s.pot("FREQ_A", 2170 / 4095)
        self.assertShows("FrEE")
        self.s.let_go("SEQ1")
        self.assertEqual(self.s.settings()["osc-tune 1 a"], 2)

    def test_layer_2s_osc_b(self):
        self.s.press("SEQ2")
        self.s.ms(1100)
        self.s.hold("SEQ2")
        self.s.pot("FREQ_B", 0.95)
        self.assertShows("FIHd")
        self.s.let_go("SEQ2")
        settings = self.s.settings()
        self.assertEqual((settings["osc-tune 2 b"], settings["osc-tune 1 b"], settings["osc-tune 2 a"]), (3, 0, 0))

    def test_octave_mode_shows_octaves(self):
        self.manual()
        self.s.hold("SEQ1")
        self.s.pot("FREQ_B", 0.2)
        self.s.pot("FREQ_B", 0.46)
        self.assertShows("OCt")
        self.s.let_go("SEQ1")
        self.s.ms(1100)
        self.s.pot("FREQ_B", 0.0)
        self.s.pot("FREQ_B", 1.0)
        self.assertShows("   3")


    def free_mode(self, pot):
        self.manual()
        self.s.hold("SEQ1")
        self.s.pot(pot, 0.2)
        self.s.pot(pot, 0.72)
        self.assertShows("FrEE")
        self.s.let_go("SEQ1")
        self.s.ms(1100)
        self.s.pot(pot, 0.0)  # through the value it held while frozen: out of pickup
        self.s.pot(pot, 1.0)

    def test_free_mode_shows_tenths_across_the_range(self):
        self.free_mode("FREQ_A")
        for fraction, text in ((0.5, "  0.0"), (0.5 + 12.3 / 72, " 12.3"), (0.5 - 12.3 / 72, "-12.3"), (1.0, " 36.0"),
                               (0.5 - 1.2 / 72, " -1.2"), (0.0, "-36.0")):
            self.s.pot("FREQ_A", fraction)
            self.assertShows(text)

    def test_the_display_follows_a_free_knob_below_the_report_threshold(self):
        self.free_mode("FREQ_B")
        self.s.pot("FREQ_B", 0.5 + 12.3 / 72)
        self.assertShows(" 12.3")
        raw = round((0.5 + 12.3 / 72) * 4095)
        self.s.cmd(f"pot {POT['FREQ_B']} {raw + 8}")  # 0.14 semitones: under the 24 steps a report takes
        self.s.ms(40)
        self.assertShows(" 12.4", "the display reads the knob meanwhile")
        self.s.cmd(f"pot {POT['FREQ_B']} {raw + 9}")
        self.s.ms(40)
        self.assertShows(" 12.4", "a step of one doesn't redraw")
        self.s.ms(1600)
        self.assertShows("PAnL", "and the value times out as any knob's")
        self.s.cmd(f"pot {POT['FREQ_B']} {raw + 16}")
        self.s.ms(40)
        self.assertShows("PAnL", "once it's gone, only a report brings it back")

    def test_a_free_knob_in_pickup_shows_the_stored_value_and_doesnt_follow(self):
        self.free_mode("FREQ_A")
        self.s.pot("FREQ_A", 0.5 + 12.3 / 72)
        self.s.ms(1100)
        self.s.press("REC", "9", "REC")
        self.s.ms(1100)
        self.s.press("1")
        self.s.ms(3100)
        self.s.pot("FREQ_A", 0.3)  # while on the empty P1
        self.s.press("9")
        self.s.ms(3100)
        self.assertShows("P  9")
        self.s.pot("FREQ_A", 0.31)
        self.assertShows(" 12.3.", "the stored value, with the pickup dot")
        raw = round(0.31 * 4095)
        self.s.cmd(f"pot {POT['FREQ_A']} {raw + 8}")
        self.s.ms(40)
        self.assertShows(" 12.3.", "the knob isn't the value yet")


class ModAssign(PanelTest):
    def begin(self):
        self.s.press("ASSIGN")
        self.assertShows("SrC")

    def test_press_opens_with_a_blinking_led(self):
        self.begin()
        lit = set()
        for _ in range(12):
            self.s.ms(50)
            lit.add(self.s.led("ARP_ASSIGN"))
        self.assertEqual(lit, {True, False})

    def test_every_source_key(self):
        for key, name in (("1", "LFO1"), ("2", "FEnV"), ("3", "AEnV"), ("4", "VEL"), ("5", "nod"), ("6", "PrES"), ("7", "EnV3"), ("8", "EnV4"), ("9", "rAnd"), ("0", "notE")):
            with self.subTest(key=key):
                self.begin()
                self.s.press(key)
                self.assertShows(name)
                self.s.press("ASSIGN")
                self.assertShows("P  1")

    def test_key_4_again_is_release_velocity(self):
        self.begin()
        self.s.press("4")
        self.assertShows("VEL")
        self.s.press("4")
        self.assertShows("rVEL")
        self.s.press("4")
        self.assertShows("VEL")
        self.s.press("ASSIGN")

    def test_key_4_again_on_a_route_says_rvel_then_the_depth(self):
        self.s.no_routes()
        self.begin()
        self.s.press("4")
        self.s.pot("CUTOFF", 0.3)
        self.s.pot("CUTOFF", 0.35)
        self.s.turn(10)
        self.s.press("4")
        self.assertShows("rVEL", "the toggled source first")
        self.s.ms(800)
        self.assertRegex(self.s.shows(), r"\d", "then the depth: rVEL → cutoff's own, a new route")
        self.s.turn(5)
        self.s.press("ASSIGN")
        sources = [r[0] for r in self.s.routes(0)]
        self.assertIn(pro800.SOURCE_NAMES.index("release-velocity"), sources)
        self.assertIn(pro800.SOURCE_NAMES.index("velocity"), sources, "the velocity route stays as it was")

    def test_key_1_again_is_the_other_lfo(self):
        self.begin()
        self.s.press("1")
        self.assertShows("LFO1")
        self.s.press("1")
        self.assertShows("LFO2")
        self.s.press("ASSIGN")

    def test_source_destination_depth(self):
        self.s.no_routes()
        self.begin()
        self.s.press("4")
        self.s.pot("CUTOFF", 0.3)
        self.s.pot("CUTOFF", 0.35)
        self.assertSaw(["VEL", "CUt"])
        self.s.turn(10)
        self.assertRegex(self.s.shows(), r"\d")
        self.s.press("ASSIGN")
        self.assertShows("P  1")
        routes = self.s.routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0][:2], (4, 3), "velocity to cutoff")
        self.assertGreater(routes[0][2], 0)

    def test_knobs_dont_change_the_sound_while_assigning(self):
        before = self.s.params()["resonance"]
        self.begin()
        self.s.press("4")
        self.s.pot("RESONANCE", 0.3)
        self.s.pot("RESONANCE", 0.7)
        self.s.press("ASSIGN")
        self.assertEqual(self.s.params()["resonance"], before)

    def test_destination_names_by_touch(self):
        for pot, name in (("FREQ_A", "P.A"), ("FREQ_B", "P.b"), ("FINE", "P.b"), ("MASTER_TUNE", "nAC1"), ("CUTOFF", "CUt"), ("FIL_ENV_AMT", "F.EA"), ("PMOD_FIL_ENV", "P.EA"),
                          ("AMP_SUS", "VCA"), ("A_LEVEL", "A.LE"), ("B_LEVEL", "b.LE"), ("NOISE", "noIS"), ("RESONANCE", "rES"), ("A_PW", "A.PU"), ("B_PW", "b.PU"), ("PMOD_OSC_B", "P.ob"),
                          ("AMP_ATT", "EA-A"), ("AMP_DEC", "EA-d"), ("AMP_REL", "EA-r"), ("GLIDE", "GLId")):
            with self.subTest(pot=pot):
                self.begin()
                self.s.press("9")
                self.s.history()
                self.s.pot(pot, 0.3)
                self.s.pot(pot, 0.4)
                self.assertSaw([name])
                self.s.press("ASSIGN")
                self.s.ms(100)

    def test_lfo_routing_buttons_are_destinations(self):
        for button, name in (("LFO_FREQ", "L.Fr"), ("LFO_FILTER", "L.FL"), ("LFO_PW", "L.PU")):
            with self.subTest(button=button):
                self.begin()
                self.s.press("9")
                self.s.history()
                self.s.switch(button)
                self.assertSaw([name])
                self.s.press("ASSIGN")

    def test_the_unison_button_is_the_chord_voicing(self):
        self.begin()
        self.s.press("9")
        self.s.history()
        self.s.switch("UNISON")
        self.assertSaw(["CH.Uo"])
        self.assertEqual(self.s.reply(0x4C)[SWITCH["UNISON"]], 0, "selecting, not switching")
        self.s.press("ASSIGN")

    def test_value_scrolls_sources_and_assign_takes_one(self):
        self.begin()
        names = []
        for _ in range(29):
            self.s.turn(1)
            names.append(self.s.shows())
        self.assertEqual(len(set(names)), len(names), f"every source reads differently: {names}")
        for wanted in ("SEqA", "SEqb", "SEqC", "SEqd", "L1.U", "L2.U", "GAtE", "tInb", "bEnd", "PEdL", "CC.A", "CC.b", "VoIC", "FIX", "nAC1", "nAC2", "ArP", "rVEL"):
            self.assertIn(readable(segments_of(wanted)), names)
        self.s.press("ASSIGN")

    def test_value_scrolls_destinations(self):
        self.begin()
        self.s.press("4")
        names = []
        for _ in range(46):
            self.s.turn(1)
            names.append(self.s.shows())
        for wanted in ("PIt", "CUt", "norP", "dEtU", "VOL", "EA-S"):
            self.assertIn(readable(segments_of(wanted)), names)
        self.s.press("ASSIGN")

    def test_a_recent_knob_is_already_the_destination(self):
        self.s.no_routes()
        self.manual()
        self.s.pot("CUTOFF", 0.4)
        self.s.ms(1100)
        self.begin()
        self.s.press("4")
        self.s.history()
        self.s.ms(800)
        self.s.turn(5)
        self.s.press("ASSIGN")
        self.assertEqual(self.s.routes()[0][:2], (4, 3))

    def move_route(self, source, dest, to_source, to_dest, depth, layer=0):
        names, dests, d = pro800.SOURCE_NAMES, pro800.DEST_NAMES, depth + 99
        return bytes([0x7F, 0x61]) in self.s.request(0x61, layer, names.index(source), dests.index(dest), names.index(to_source), dests.index(to_dest), d >> 7, d & 0x7F)

    def route_row(self, source, dest, depth):
        return (pro800.SOURCE_NAMES.index(source), pro800.DEST_NAMES.index(dest), depth)

    def test_a_moved_route_goes_at_once_in_its_slot(self):
        self.s.no_routes()
        for dest, depth in (("pitch-a", 10), ("pitch-b", 20), ("cutoff", 30)):
            self.s.request(0x56, 0, pro800.SOURCE_NAMES.index("lfo"), pro800.DEST_NAMES.index(dest), (depth + 99) >> 7, (depth + 99) & 0x7F)
        self.assertTrue(self.move_route("lfo", "pitch-b", "lfo", "resonance", 20))
        table = self.s.route_table()
        self.assertEqual(table[1], self.route_row("lfo", "resonance", 20), "in its slot, with no wait")
        self.assertEqual(len(self.s.routes()), 3, "and nothing left behind")

    def test_a_moved_route_keeps_its_both_mark(self):
        self.s.no_routes()
        self.s.setting("layers", 2)
        self.s.request(0x56, 0, pro800.SOURCE_NAMES.index("lfo"), pro800.DEST_NAMES.index("cutoff"), (30 + 99) >> 7, (30 + 99) & 0x7F)
        i = pro800.SETTING_NAMES.index("route-both 1 (0-7)")
        self.s.request(0x7D, i, 0, 1)
        self.assertTrue(self.move_route("lfo", "cutoff", "env3", "cutoff", 30))
        d = self.s.reply(0x54)
        self.assertEqual(d[1 + 2 * i] << 7 | d[2 + 2 * i], 1)

    def test_a_route_moved_onto_another_gives_it_the_depth(self):
        self.s.no_routes()
        for dest, depth in (("pitch-a", 10), ("pitch-b", 20)):
            self.s.request(0x56, 0, pro800.SOURCE_NAMES.index("lfo"), pro800.DEST_NAMES.index(dest), (depth + 99) >> 7, (depth + 99) & 0x7F)
        self.assertTrue(self.move_route("lfo", "pitch-a", "lfo", "pitch-b", 44))
        self.assertEqual(self.s.routes(), [self.route_row("lfo", "pitch-b", 44)])

    def test_moving_a_route_in_a_full_table(self):
        self.s.no_routes()
        for d in range(16):
            self.s.request(0x56, 0, pro800.SOURCE_NAMES.index("lfo"), d, 0, 104)
        self.assertEqual(len(self.s.routes()), 16)
        self.assertTrue(self.move_route("lfo",
                                        pro800.DEST_NAMES[3], "env3", pro800.DEST_NAMES[3], 5), "it needs no other slot")
        self.assertEqual(self.s.route_table()[3], self.route_row("env3", pro800.DEST_NAMES[3], 5))

    def test_moving_a_route_refuses_a_depth_of_0(self):
        self.s.no_routes()
        self.s.request(0x56, 0, pro800.SOURCE_NAMES.index("lfo"), pro800.DEST_NAMES.index("cutoff"), 0, 120)
        self.assertFalse(self.move_route("lfo", "cutoff", "env3", "cutoff", 0))
        self.assertEqual(len(self.s.routes()), 1)

    def test_a_route_waiting_at_0_moved_doesnt_go(self):
        self.s.no_routes()
        lfo, cutoff = pro800.SOURCE_NAMES.index("lfo"), pro800.DEST_NAMES.index("cutoff")
        self.s.request(0x56, 0, lfo, cutoff, 0, 120)
        self.s.request(0x56, 0, lfo, cutoff, 0, 99)  # on its way out
        self.assertTrue(self.move_route("lfo", "cutoff", "env3", "resonance", 50))
        self.s.rest()
        self.assertEqual(self.s.routes(), [self.route_row("env3", "resonance", 50)])

    def test_depth_zero_removes_the_route(self):
        self.s.no_routes()
        self.begin()
        self.s.press("4")
        self.s.pot("CUTOFF", 0.3)
        self.s.pot("CUTOFF", 0.35)
        self.s.turn(5)
        self.s.press("ASSIGN")
        self.assertEqual(len(self.s.routes()), 1)
        self.begin()
        self.s.press("4")
        self.s.pot("CUTOFF", 0.3)
        self.s.pot("CUTOFF", 0.35)
        self.s.ms(800)
        self.s.turn(-5)
        self.assertShows("   0")
        self.s.press("ASSIGN")
        self.assertEqual(self.s.routes(), [(4, 3, 0)], "at 0 it waits")
        self.s.rest()
        self.assertEqual(self.s.routes(), [])

    def test_seq_and_arp_up_still_work_while_assigning(self):
        self.begin()
        self.s.press("ARP_UP")
        self.s.press("1")
        self.assertShows("LFO2")
        self.s.press("ASSIGN")
        self.s.press("ARP_UP")

    def test_preset_leaves_and_does_its_job(self):
        self.begin()
        self.s.press("PRESET")
        self.assertShows("PAnL")
        self.assertEqual(self.s.settings()["preset-mode"], 0)

    def test_settings_leaves_and_opens(self):
        self.begin()
        self.s.press("SETTINGS")
        self.assertShows("SEt")
        self.s.press("SETTINGS")
        self.assertShows("P  1")

    def test_no_routes_hold_starts_assign(self):
        self.s.no_routes()
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        self.assertShows("SrC")
        self.s.press("ASSIGN")
        self.assertShows("P  1")

    def test_full_at_16_routes(self):
        for dest in range(16):
            self.s.request(0x56, 0, 4, dest, 0, 99 + 5)
        self.assertEqual(len(self.s.routes()), 16)
        self.begin()
        self.s.press("9")
        self.s.pot("CUTOFF", 0.3)
        self.s.pot("CUTOFF", 0.35)
        self.s.ms(800)
        self.s.turn(1)
        self.assertShows("FULL")
        self.s.press("ASSIGN")


class Browse(PanelTest):
    def setUp(self):
        super().setUp()
        self.s.no_routes()
        for dest in (3, 13, 15):
            self.s.request(0x56, 0, 4, dest, 0, 99 + 20)
        self.assertEqual(len(self.s.routes()), 3)

    def test_hold_opens_with_a_steady_led(self):
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        self.s.history()
        self.assertTrue(self.s.led("ARP_ASSIGN"))
        self.s.ms(100)
        self.assertTrue(self.s.led("ARP_ASSIGN"))

    def test_value_steps_the_routes_by_number(self):
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        self.s.turn(1)
        self.assertShows("nod2")
        self.s.turn(1)
        self.assertShows("nod3")

    def test_at_rest_cycles_number_source_destination_depth(self):
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        self.s.turn(1)
        self.s.history()
        self.s.ms(800 + 600 * 4)
        self.assertSaw(["VEL", "rES", "  20"])

    def test_at_rest_a_depth_in_tenths_shows_its_point(self):
        t = 535 + 990
        self.s.request(0x66, 0, 4, 13, t >> 7, t & 0x7F)
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        self.s.turn(1)
        self.s.history()
        self.s.ms(800 + 600 * 4)
        self.assertSaw(["VEL", "rES", " 53.5"])

    def test_hold_again_leaves(self):
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        self.assertShows("P  1")
        self.assertFalse(self.s.led("ARP_ASSIGN"))

    def test_short_press_opens_the_route_for_editing(self):
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        self.s.press("ASSIGN")
        self.s.turn(1)
        self.assertRegex(self.s.shows(), r"\d")
        self.s.press("ASSIGN")
        self.assertShows("P  1")


class StepRecording(PanelTest):
    def begin(self):
        self.s.hold("REC")
        self.s.press("SEQ1")
        self.s.let_go("REC")
        self.assertShows("rSEq")

    def test_rec_and_seq1_start_with_a_fast_blink(self):
        self.begin()
        changes = 0
        last = self.s.led("REC")
        for _ in range(40):
            self.s.ms(25)
            now = self.s.led("REC")
            changes += now != last
            last = now
        self.assertGreaterEqual(changes, 6, "faster than a save's blink")

    def test_notes_count_up(self):
        self.begin()
        for i, note in enumerate((60, 62, 64), 1):
            self.s.note(note)
            self.s.ms(5)
            self.assertShows(f"St{i:2d}")
            self.s.midi(0x80, note, 0)
            self.s.ms(5)
        self.s.ms(1100)
        self.assertShows("rSEq")
        self.s.press("REC")
        self.assertShows("donE")
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_seq2_writes_a_rest(self):
        self.begin()
        self.s.press("SEQ2")
        self.assertShows("St 1")

    def test_seq1_ends_it(self):
        self.begin()
        self.s.note(60)
        self.s.midi(0x80, 60, 0)
        self.s.ms(5)
        self.s.press("SEQ1")
        self.assertShows("donE")
        self.assertFalse(self.s.led("REC"))

    def test_ending_with_no_steps_says_no(self):
        self.begin()
        self.s.press("SEQ1")
        self.assertShows("no")
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_a_patch_load_ends_it(self):
        self.begin()
        self.s.midi(0xC0, 3)
        self.s.ms(5)
        self.assertShows("P  4")
        self.assertFalse(self.s.led("REC"))

    def test_rec_after_ending_doesnt_start_a_save(self):
        self.begin()
        self.s.note(60)
        self.s.midi(0x80, 60, 0)
        self.s.ms(5)
        self.s.press("REC")
        self.s.ms(1100)
        self.assertFalse(self.s.led("REC"))
        self.assertShows("P  1")


class Clock(PanelTest):
    def test_shift_cycles_the_source(self):
        self.s.hold("SHIFT")
        for name in ("Int", "SYnC", "nIdI"):
            self.s.press("CLOCK")
            self.assertShows(name)
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_tapping_shows_tap_then_the_bpm(self):
        for _ in range(4):
            self.s.hold("CLOCK")
            self.s.let_go("CLOCK")
            self.s.ms(400)  # 500 ms apart
        self.s.history()
        self.s.press("CLOCK")
        self.assertShows("tAP")
        self.s.ms(540)
        self.assertRegex(self.s.shows(), r"^ +1[12]\d$")
        self.s.ms(1600)
        self.assertShows("P  1")

    def test_holding_restarts_the_bar(self):
        self.s.hold("CLOCK")
        self.s.ms(600)
        self.assertShows("StAr")
        self.s.let_go("CLOCK")
        self.s.ms(650)
        self.assertShows("P  1")

    def test_the_source_survives_a_power_cycle(self):
        self.s.hold("SHIFT")
        self.s.press("CLOCK")
        self.s.let_go("SHIFT")
        self.s.ms(1100)  # stored once it has rested
        self.power_cycle()
        self.assertEqual(self.s.settings()["clock"], 1)

    def blinks(self):
        lit = set()
        for _ in range(60):
            self.s.ms(25)
            lit.add(self.s.led("SYNC_CLOCK"))
        return lit == {True, False}

    def test_the_led_blinks_on_the_internal_clock(self):
        self.s.hold("SHIFT")
        self.s.press("CLOCK")
        self.s.let_go("SHIFT")
        self.assertTrue(self.blinks())

    def test_a_missing_midi_clock_shows_at_once(self):
        self.assertFalse(self.blinks())

    def test_the_led_blinks_on_midi_clock(self):
        self.s.midi(0xFA)
        lit = set()
        for _ in range(96):
            self.s.midi(0xF8)
            self.s.ms(21)
            lit.add(self.s.led("SYNC_CLOCK"))
        self.assertEqual(lit, {True, False})


class Tune(PanelTest):
    def test_shows_tune_then_the_slot(self):
        self.s.press("TUNE")
        seen = [t for _, t in self.s.history()]
        self.assertIn(readable(segments_of("tUnE")), seen)
        self.s.ms(1100)
        self.assertShows("P  1")

    def test_tune_in_manual_goes_back_to_panl(self):
        self.manual()
        self.s.press("TUNE")
        self.s.ms(1100)
        self.assertShows("PAnL")

    def assertTunedDroppingMidi(self):
        stat = self.s.midi_stat()
        self.assertEqual((stat["tunes_discarding"], stat["tunes_not_discarding"]), (1, 0), "USB must not make the host wait 30 s")
        self.assertEqual(stat["discard"], 0, "and it's back to waiting after")

    def test_the_panels_tune_keeps_usb_accepting(self):
        self.s.press("TUNE")
        self.assertTunedDroppingMidi()

    def test_a_tune_asked_for_over_midi_keeps_usb_accepting_and_replies(self):
        self.assertEqual(self.s.request(0x75)[8:9], b"\x76")
        self.assertTunedDroppingMidi()

    def test_what_came_in_before_a_tune_is_gone_after_it(self):
        burst = pro800.SYSEX_HEADER + b"\x75\xF7" + bytes([0x90, 60, 100])
        self.s.cmd("midi " + burst.hex())
        self.s.ms(5)
        self.assertEqual(self.s.gated(), [], "stale notes don't play after the 30 s")


class MidiOnTheDisplay(PanelTest):
    def test_a_controller_shows_like_its_knob_then_goes_back(self):
        self.s.cc(71, 64)
        self.s.ms(60)
        self.assertShows("  50")
        self.s.ms(1500)
        self.assertShows("P  1")

    def test_cutoff_is_cc_74_in_hz(self):
        self.s.cc(74, 127)
        self.s.ms(60)
        self.assertShows(" 16.7")
        self.assertEqual(self.s.params()["cutoff"], 99)
        self.s.cc(74, 0)
        self.s.ms(60)
        self.assertShows(" 16.4")

    def test_resonance_is_cc_71(self):
        self.s.cc(71, 127)
        self.s.ms(60)
        self.assertEqual(self.s.params()["resonance"], 99)

    def test_cc_79_is_nothing_now(self):
        before = self.s.params()
        self.s.cc(79, 40)
        self.s.ms(60)
        self.assertEqual(self.s.params(), before)

    def test_cc_74_is_the_timbre_source_as_well(self):
        """Outside the per-channel modes it moves the knob, and a route from Timbre goes on working: measured on the noise
        level's wire, which the cutoff doesn't touch."""
        self.s.no_routes()
        self.s.request(0x56, 0, pro800.SOURCE_NAMES.index("timbre"), pro800.DEST_NAMES.index("noise"), (50 + 99) >> 7, (50 + 99) & 0x7F)
        self.s.cc(74, 0)
        self.s.note(60)
        self.s.ms(100)
        low = self.s.cvs()[CV_NOISE]
        self.s.cc(74, 127)
        self.s.ms(100)
        self.assertEqual(self.s.params()["cutoff"], 99)
        self.assertGreater(self.s.cvs()[CV_NOISE] - low, 1000)

    def test_in_the_per_channel_modes_cc_74_is_timbre_and_leaves_the_knob(self):
        """An MPE keyboard sends it for each note: it must not sweep everyone's cutoff. The two MPE presets rely on it."""
        for mode in ("8ch", "mpe"):
            with self.subTest(mode=mode):
                self.s.setting("layers", pro800.LAYER_MODES.index(mode))
                self.s.cc(74, 64, channel=1)
                self.s.ms(60)
                self.assertEqual(self.s.params()["cutoff"], 99)
                self.s.cc(74, 64, channel=0)
                self.s.ms(60)
                self.assertEqual(self.s.params()["cutoff"], 99)

    def test_cc_a_set_to_74_takes_it_from_the_knob(self):
        self.s.setting("cc-a", 74)
        self.s.cc(74, 0)
        self.s.ms(60)
        self.assertEqual(self.s.params()["cutoff"], 99)

    def test_a_stream_shows_at_most_20_times_a_second(self):
        self.s.history()
        for v in range(100):
            self.s.cc(71, v)
            self.s.ms(3)
        changes = len(self.s.history())
        self.assertLessEqual(changes, 8, "300 ms at 20 a second")
        self.s.ms(60)
        self.assertShows("  77", "the latest value shows")

    def test_controllers_leave_a_menu_alone(self):
        self.s.press("SETTINGS", "7")
        self.s.ms(700)
        self.s.cc(71, 64)
        self.s.ms(60)
        self.assertShows("   2")
        self.assertEqual(self.s.params()["resonance"], 50)

    def test_controllers_leave_a_save_alone(self):
        self.s.press("REC")
        self.s.cc(71, 64)
        self.s.ms(60)
        self.assertShows("P  1")
        self.s.press("PRESET")

    def test_controllers_leave_mod_assign_alone(self):
        self.s.press("ASSIGN")
        self.s.cc(71, 64)
        self.s.ms(60)
        self.assertShows("SrC")
        self.s.press("ASSIGN")

    def test_a_controller_on_the_panel_goes_back_to_panl(self):
        self.manual()
        self.s.cc(71, 64)
        self.s.ms(60)
        self.assertShows("  50")
        self.s.ms(1500)
        self.assertShows("PAnL")

    def test_a_controller_elsewhere_than_the_channel_set_is_ignored(self):
        self.s.hold("SHIFT")
        for f in (0.5, 0.0, 0.1):
            self.s.pot("NOISE", f)
        self.s.let_go("SHIFT")
        channel = self.s.settings()["midi-channel"]
        self.assertGreater(channel, 0)
        self.s.ms(1600)
        self.s.history()
        self.s.cc(71, 64, channel=channel % 16)
        self.s.ms(60)
        self.assertEqual(self.s.history(), [])
        self.assertEqual(self.s.params()["resonance"], 0)
        self.s.cc(71, 64, channel=channel - 1)
        self.s.ms(60)
        self.assertEqual(self.s.params()["resonance"], 50)

    def test_din_takes_controllers_too(self):
        self.s.din(0xB0, 71, 64)
        self.s.ms(60)
        self.assertShows("  50")

    def test_lfo_2_rate_and_env_3_times_show_their_knobs_units(self):
        """As the knobs show them with LFO 2 or Env 3 selected: the units LFO 1's rate and the filter attack show."""
        for theirs, ours in ((76, 27), (23, 85)):  # LFO 1 / 2 rate, filter / Env 3 attack
            with self.subTest(cc=ours):
                self.s.cc(theirs, 100)
                self.s.ms(60)
                shown = self.s.shows()
                self.s.ms(1600)
                self.s.cc(ours, 100)
                self.s.ms(60)
                self.assertShows(shown)
                self.s.ms(1600)

    def test_env_3_sustain_shows_0_to_99(self):  # a level, as its knob shows it
        self.s.cc(87, 64)
        self.s.ms(60)
        self.assertShows("  50")

    def test_the_host_setting_a_parameter_doesnt_disturb_the_display(self):
        self.manual()
        self.s.request(0x52, 0, 8, 0x40, 0)
        self.assertShows("PAnL")


class Sound(PanelTest):
    def amp(self):
        return max(self.s.cvs()[CV_AMP1 + v] for v in range(8))

    def test_a_note_opens_a_voice_and_note_off_closes_it(self):
        self.s.ms(100)
        self.assertEqual(self.amp(), 0)
        self.s.note(60)
        self.s.ms(100)
        self.assertGreater(self.amp(), 0)
        self.s.midi(0x80, 60, 0)
        self.s.ms(3000)
        self.assertEqual(self.amp(), 0)

    def test_panic_closes_a_held_note(self):
        self.s.note(60)
        self.s.ms(100)
        self.s.hold("SHIFT")
        self.s.press("0")
        self.s.let_go("SHIFT")
        self.s.ms(3000)
        self.assertEqual(self.amp(), 0)

    def test_a_note_doesnt_touch_the_display(self):
        self.s.history()
        self.s.note(60)
        self.s.ms(100)
        self.assertEqual([t for _, t in self.s.history()], [])
        self.assertShows("P  1")


class TwoThingsAtOnce(PanelTest):
    """Whatever arrives meanwhile, the display comes back to what the panel is doing."""

    def test_a_knob_in_the_menu_shows_its_value_then_the_page(self):
        self.s.press("SETTINGS", "7")
        self.s.ms(700)
        self.s.pot("MASTER_VOL", 0.25)
        self.assertShows("  25")
        self.s.ms(1600)
        self.assertShows("   2")

    def test_a_knob_in_a_save_shows_its_value_then_the_slot(self):
        self.s.press("REC", "5")
        self.s.pot("MASTER_VOL", 0.25)
        self.assertShows("  25")
        self.s.ms(1600)
        self.assertShows("P  5")
        self.s.press("REC")
        self.assertShows("donE")

    def test_a_knob_while_recording_steps_comes_back_to_rseq(self):
        self.s.hold("REC")
        self.s.press("SEQ1")
        self.s.let_go("REC")
        self.s.pot("MASTER_VOL", 0.25)
        self.assertShows("  25")
        self.s.ms(1600)
        self.assertShows("rSEq")
        self.s.press("SEQ1")

    def test_a_program_change_in_the_menu_leaves_the_page_on_the_display(self):
        self.s.press("SETTINGS", "7")
        self.s.ms(700)
        self.s.midi(0xC0, 3)
        self.s.ms(5)
        self.assertEqual(self.s.settings()["patch"], 3)
        self.s.ms(1600)
        self.assertShows("   2")
        self.s.press("SETTINGS")
        self.assertShows("P  4")

    def test_a_program_change_in_a_save_is_ignored(self):
        self.s.request(0x52, 0, 8, 0x40, 0)
        self.s.press("REC", "5")
        self.s.midi(0xC0, 3)
        self.s.ms(1600)
        self.assertShows("P  5", "the slot you were choosing")
        self.assertEqual(self.s.params()["resonance"], 50, "the sound being saved")
        self.s.press("REC")
        self.assertTrue(self.s.slot_is_ours(5))
        self.assertFalse(self.s.slot_is_ours(4))
        self.assertEqual(self.s.settings()["patch"], 4)

    def test_a_program_change_in_mod_assign_comes_back_to_it(self):
        self.s.press("ASSIGN")
        self.s.midi(0xC0, 3)
        self.s.ms(1600)
        self.assertShows("SrC")
        self.s.press("ASSIGN")
        self.assertShows("P  4")

    def test_a_program_change_while_typing_a_slot_keeps_the_number(self):
        self.s.press("1", "2")
        self.s.midi(0xC0, 3)
        self.s.ms(5)
        self.assertShows("P 12")
        self.s.press("8")
        self.assertShows("P128")
        self.assertEqual(self.s.settings()["patch"], 127)

    def test_a_program_change_on_the_panel_leaves_manual_mode(self):
        self.manual()
        self.s.midi(0xC0, 3)
        self.s.ms(5)
        self.assertShows("P  4")
        self.assertTrue(self.s.led("PRESET"))

    def test_preset_in_the_menu_leaves_the_page_on_the_display(self):
        self.s.press("SETTINGS", "7")
        self.s.ms(700)
        self.s.press("PRESET")
        self.s.ms(1600)
        self.assertShows("   2")
        self.s.press("SETTINGS")
        self.assertShows("PAnL")
        self.assertFalse(self.s.led("PRESET"))

    def test_perf_in_the_menu_does_nothing(self):
        self.s.press("SETTINGS", "7")
        self.s.ms(700)
        self.s.press("PERF")
        self.assertShows("   2")
        self.s.long_press("PERF")
        self.assertShows("   2", "held, too")

    def test_perf_in_a_save_saves_there_as_the_init_patch(self):
        """0.10.0 (#35): Rec, the slot, Perf. Before, Perf dropped the number and did nothing."""
        self.s.press("REC", "5")
        self.s.press("PERF")
        self.assertShows("InIt.")
        settings = self.s.settings()
        self.assertEqual((settings["patch"], settings["init-patch"]), (4, 5), "saved to P5, which is the init now")
        self.assertTrue(self.s.slot_is_ours(5), "really saved")
        self.assertFalse(self.s.led("REC"))
        self.power_cycle()  # at once: kept as InIt. shows, as a save is at donE
        self.assertEqual(self.s.settings()["init-patch"], 5)

    def test_shift_digit_from_a_save_keeps_the_display_sensible(self):
        self.s.press("REC")
        self.s.hold("SHIFT")
        self.s.press("3")
        self.s.let_go("SHIFT")
        self.assertShows("OFF", "the layer page opens")
        self.s.press("SETTINGS")
        self.assertShows("P  1", "back in the save")
        self.s.press("PRESET")

    def test_tune_in_the_menu_comes_back_to_the_page(self):
        self.s.press("SETTINGS", "7")
        self.s.ms(700)
        self.s.press("TUNE")
        self.s.ms(100)
        self.assertShows("   2")
        self.s.press("SETTINGS")

    def test_tune_keeps_the_sound(self):
        self.s.request(0x52, 0, 8, 0x40, 0)
        self.s.request(0x52, 0, 24, 0x20, 0)
        self.store_patch(3)
        self.s.press("TUNE")
        self.s.ms(100)
        self.assertShows("P  3")
        after = self.s.params()
        self.assertEqual((after["resonance"], after["cutoff"]), (50, 25), "tuning leaves the sound as it was")
        self.assertEqual(self.s.settings()["patch"], 2)

    def test_undo_back_to_manual_mode(self):
        self.manual()
        self.s.press("PRESET")
        self.assertShows("P  1")
        self.s.hold("SHIFT")
        self.s.press("PERF")
        self.assertShows("Undo")
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("PAnL")
        self.assertFalse(self.s.led("PRESET"))

    def test_a_long_held_button_does_nothing_more(self):
        self.s.hold("PERF")
        self.s.ms(650)
        self.assertShows("InIt")
        self.s.ms(3000)
        self.assertShows("P  1")
        self.s.let_go("PERF")
        self.assertShows("P  1")

    def test_perf_and_tune_light_their_leds_while_held(self):
        self.s.hold("PERF")
        self.assertTrue(self.s.led("PERF"))
        self.s.let_go("PERF")
        self.assertFalse(self.s.led("PERF"))

    def test_the_host_loading_a_patch_shows_it(self):
        self.s.request(0x5C, 0, 3)
        self.assertShows("P  4")

    def test_the_host_saving_shows_the_slot(self):
        self.s.request(0x5D, 0, 3)
        self.assertShows("P  4")
        self.assertTrue(self.s.slot_is_ours(4))

    def test_the_host_formatting_shows_the_slot(self):
        self.store_patch(4)
        self.s.request(0x5B)
        self.s.ms(5)
        self.assertFalse(self.s.slot_is_ours(4))
        self.assertShows("P  4")

    def test_report_mode_leaves_the_display_to_the_host(self):
        self.s.request(0x7A, 1)
        self.assertShows("    ")
        self.s.pot("MASTER_VOL", 0.25)
        self.s.ms(100)
        self.assertShows("    ", "the knob is only reported")
        self.assertIn(pro800.SYSEX_HEADER + bytes([0x60, POT["MASTER_VOL"]]), bytes.fromhex(self.s.cmd("out")))
        self.s.cc(71, 64)
        self.s.ms(100)
        self.assertShows("    ", "nor a controller")
        self.s.request(0x7A, 0)
        self.assertShows("P  1")


class EnvelopePage(PanelTest):
    def open(self):
        self.s.press("SETTINGS", "6")
        self.s.ms(700)

    def test_att_and_dec_set_delay_and_hold(self):
        self.open()
        self.s.pot("AMP_ATT", 0.2)
        self.s.pot("AMP_ATT", 0.5)
        self.assertShows("dLY")
        self.s.ms(520)
        self.assertRegex(self.s.shows(), r"\d")
        self.assertGreater(self.s.settings()["env-delay 1 amp"], 0)
        self.s.pot("AMP_DEC", 0.2)
        self.s.pot("AMP_DEC", 0.5)
        self.assertShows("HoLd")
        self.assertGreater(self.s.settings()["env-hold 1 amp"], 0)
        self.s.ms(2100)
        self.assertShows("AdSr")

    def test_the_filter_knobs_set_the_filter_envelopes(self):
        self.s.press("SETTINGS", "6", "6")
        self.s.ms(700)
        self.s.pot("FIL_ATT", 0.2)
        self.s.pot("FIL_ATT", 0.5)
        self.assertShows("dLY")
        self.s.press("SETTINGS")

    def test_a_knob_on_the_page_doesnt_move_the_sound(self):
        before = self.s.params()["amp-att"]
        self.open()
        self.s.pot("AMP_ATT", 0.2)
        self.s.pot("AMP_ATT", 0.9)
        self.s.press("SETTINGS")
        self.assertEqual(self.s.params()["amp-att"], before)

    def test_tempo_timing_shows_note_values_on_the_knob(self):
        self.manual()
        self.open()
        self.s.press("ARP_UP", "ARP_UP")
        self.assertShows("tI.A")
        self.s.ms(700)
        self.s.turn(1)
        self.assertShows("SYnC")
        self.s.press("SETTINGS")
        self.s.pot("AMP_DEC", 0.2)
        self.s.pot("AMP_DEC", 0.6)
        self.assertNotRegex(self.s.shows(), r"^ +\d+$")

    def test_cycling_cycles_show_a_share_on_the_knob(self):
        self.manual()
        self.open()
        for _ in range(3):
            self.s.turn(1)
        self.assertShows("8bAr")
        self.s.press("SETTINGS")
        self.s.pot("AMP_ATT", 0.2)
        self.s.pot("AMP_ATT", 0.6)
        self.assertRegex(self.s.shows(), r"^ +\d+$")


class ModAssignMore(PanelTest):
    def begin(self):
        self.s.press("ASSIGN")
        self.assertShows("SrC")

    def test_knobs_that_stand_for_a_source(self):
        for pot, name in (("LFO_FREQ", "LFO1"), ("LFO_AMOUNT", "LFO1"), ("FIL_ATT", "FEnV"), ("FIL_REL", "FEnV"), ("AMP_ATT", "AEnV"), ("AMP_REL", "AEnV")):
            with self.subTest(pot=pot):
                self.begin()
                self.s.pot(pot, 0.3)
                self.s.pot(pot, 0.4)
                self.assertShows(name)
                self.s.press("ASSIGN")
                self.s.ms(100)

    def test_shift_makes_the_adsr_knobs_env_3_and_4(self):
        for pot, name in (("FIL_ATT", "EnV3"), ("AMP_ATT", "EnV4")):
            with self.subTest(pot=pot):
                self.begin()
                self.s.hold("SHIFT")
                self.s.pot(pot, 0.3)
                self.s.pot(pot, 0.4)
                self.assertShows(name)
                self.s.let_go("SHIFT")
                self.s.press("ASSIGN")
                self.s.ms(100)

    def test_shift_makes_adsr_destinations_env_3_and_4(self):
        for pot, name in (("FIL_ATT", "E3-A"), ("AMP_REL", "E4-r"), ("AMP_SUS", "E4-S")):
            with self.subTest(pot=pot):
                self.begin()
                self.s.press("9")
                self.s.history()
                self.s.hold("SHIFT")
                self.s.pot(pot, 0.3)
                self.s.pot(pot, 0.4)
                self.assertSaw([name])
                self.s.let_go("SHIFT")
                self.s.press("ASSIGN")
                self.s.ms(100)

    def test_lfo_2_destination_names(self):
        self.s.press("ARP_UP")
        for button, name in (("LFO_FREQ", "L2.Fr"), ("LFO_FILTER", "L2.FL"), ("LFO_PW", "L2.PU")):
            with self.subTest(button=button):
                self.begin()
                self.s.press("9")
                self.s.history()
                self.s.switch(button)
                self.assertSaw([name])
                self.s.press("ASSIGN")

    def test_lfo_2_knob_destinations(self):
        self.s.press("ARP_UP")
        for pot, name in (("LFO_AMOUNT", "L2.An"), ("LFO_FREQ", "L2.rt")):
            with self.subTest(pot=pot):
                self.begin()
                self.s.press("9")
                self.s.history()
                self.s.pot(pot, 0.3)
                self.s.pot(pot, 0.4)
                self.assertSaw([name])
                self.s.press("ASSIGN")

    def test_a_negative_depth_shows_its_sign(self):
        self.s.no_routes()
        self.begin()
        self.s.press("4")
        self.s.pot("RESONANCE", 0.3)
        self.s.pot("RESONANCE", 0.4)
        self.s.ms(800)
        self.s.turn(-5)
        self.assertRegex(self.s.shows(), r"^ *-\d+$")
        self.s.press("ASSIGN")
        self.assertLess(self.s.routes()[0][2], 0)

    def test_touching_a_destination_shows_its_depth(self):
        self.s.request(0x56, 0, 4, 13, 0, 99 + 20)
        self.begin()
        self.s.press("4")
        self.s.pot("RESONANCE", 0.3)
        self.s.pot("RESONANCE", 0.4)
        self.s.ms(800)
        self.assertShows("  20")
        self.s.press("ASSIGN")

    def open_depth(self, tenths):
        """MOD ASSIGN on source 4 -> resonance, with the route at this depth in tenths."""
        t = tenths + 990
        self.s.request(0x66, 0, 4, 13, t >> 7, t & 0x7F)
        self.begin()
        self.s.press("4")
        self.s.pot("RESONANCE", 0.3)
        self.s.pot("RESONANCE", 0.4)
        self.s.ms(800)

    def test_a_depth_in_tenths_shows_its_point(self):
        for tenths, text in ((535, " 53.5"), (-535, "-53.5"), (55, "  5.5"), (-55, " -5.5"), (1, "  0.1"), (-1, " -0.1"), (985, " 98.5"), (530, "  53")):
            with self.subTest(tenths):
                self.open_depth(tenths)
                self.assertShows(text)
                self.s.press("ASSIGN")

    def test_value_goes_from_tenths_to_the_next_whole_number_that_way(self):
        for tenths, step, text, depth in ((535, 1, "  54", 54), (535, -1, "  53", 53), (-535, 1, " -53", -53), (-535, -1, " -54", -54)):
            with self.subTest((tenths, step)):
                self.open_depth(tenths)
                self.s.cmd(f"enc {2 * step} 40") # half a cycle, one step of a depth
                self.s.ms(100)
                self.assertShows(text)
                self.s.press("ASSIGN")
                self.assertIn(depth, [r[2] for r in self.s.routes()])

    def test_the_edit_layer_gets_the_route(self):
        self.s.no_routes()
        self.s.press("SEQ2")
        self.begin()
        self.s.press("4")
        self.s.pot("RESONANCE", 0.3)
        self.s.pot("RESONANCE", 0.4)
        self.s.ms(800)
        self.s.turn(5)
        self.s.press("ASSIGN")
        self.assertEqual(self.s.routes(0), [])
        self.assertEqual(len(self.s.routes(1)), 1)

    def test_routes_are_saved_with_the_patch(self):
        self.s.no_routes()
        self.begin()
        self.s.press("4")
        self.s.pot("RESONANCE", 0.3)
        self.s.pot("RESONANCE", 0.4)
        self.s.ms(800)
        self.s.turn(5)
        self.s.press("ASSIGN")
        self.store_patch(3)
        self.s.press("1")
        self.s.ms(3100)
        self.assertEqual(self.s.routes(), INIT_ROUTES, "P1 is empty: the init sound, not the saved route")
        self.s.press("3")
        self.s.ms(3100)
        self.assertEqual(len(self.s.routes()), 1)

    def test_browse_numbers_past_nine_read_no10(self):
        for dest in range(12):
            self.s.request(0x56, 0, 4, dest, 0, 99 + 5)
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        for _ in range(9):
            self.s.turn(1)
        self.assertShows("no10")
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")


class ButtonsOnTheFace(PanelTest):
    def test_the_waveforms_sync_and_pmod_name_themselves_for_a_second(self):
        """Since 0.10.0 (#34) they say what now sounds (SwitchNames has the texts); before, they kept the display."""
        for name in ("A_SAW", "A_TRI", "A_SQUARE", "B_SAW", "B_TRI", "B_SQUARE", "SYNC", "PMOD_FREQ_A", "PMOD_FILTER"):
            with self.subTest(button=name):
                self.s.history()
                self.s.switch(name)
                self.assertEqual(len(self.s.history()), 1, "one text")
                self.s.ms(1100)
                self.assertShows("P  1")

    def test_unison_says_on_and_off(self):
        """Since chord memory (0.10.0), which names it CHrd when it takes a chord."""
        self.s.switch("UNISON")
        self.assertShows("Un.on")
        self.s.ms(1100)
        self.assertShows("P  1")
        self.s.switch("UNISON")
        self.assertShows("Un.oF")

    def test_a_button_takes_over_from_a_loaded_patch(self):
        self.s.switch("A_SAW")
        self.s.ms(10)
        gates = [int(x) for x in self.s.cmd("gates").split()]
        self.s.switch("A_SAW")
        self.s.ms(10)
        self.assertNotEqual([int(x) for x in self.s.cmd("gates").split()], gates)

    def test_m_vol_isnt_in_pickup_after_a_load(self):
        self.store_patch(3)
        self.s.pot("MASTER_VOL", 0.25)
        self.assertShows("  25", "no dot: M VOL is always where its knob is")

    def test_lfo_routing_on_lfo_1_says_on_or_off_as_lfo_2s_does(self):
        self.s.history()
        self.s.switch("LFO_FREQ")
        self.assertEqual([t for _, t in self.s.history()], [readable(segments_of("Fr.on"))])
        self.assertFalse(self.s.led("ARP_UP"), "still LFO 1")


class MoreNames(PanelTest):
    """The display names the earlier classes don't reach."""

    def test_the_other_envelopes_pages(self):
        self.s.press("SETTINGS", "6", "6")
        self.assertShows("En.F")
        for name in ("tr.F", "tI.F", "CU.F", "Cn.F"):
            self.s.press("ARP_UP")
            self.assertShows(name)
        self.s.press("6")
        self.assertShows("Cn.3", "the next envelope, on the same page")
        self.s.press("ARP_UP")
        self.assertShows("En.3", "the last page wraps to the first")
        self.s.press("6", "ARP_UP", "ARP_UP")
        self.assertShows("tI.4")

    def test_trigger_and_curve_values(self):
        self.s.press("SETTINGS", "6", "ARP_UP")
        self.s.ms(700)
        self.s.turn(1)
        self.assertShows("UP")
        self.assertEqual(self.s.settings()["env-up 1"], 1)
        self.s.press("ARP_UP", "ARP_UP")
        self.assertShows("CU.A")
        self.s.ms(700)
        self.s.turn(1)
        self.assertShows("LIn")
        self.s.turn(1)
        self.assertShows("SUEL")
        self.assertEqual(self.s.settings()["env-curve 1"] & 3, 2)

    def test_filter_envelope_stage_destinations(self):
        for pot, name in (("FIL_ATT", "EF-A"), ("FIL_DEC", "EF-d"), ("FIL_SUS", "EF-S"), ("FIL_REL", "EF-r")):
            with self.subTest(pot=pot):
                self.s.press("ASSIGN", "9")
                self.s.history()
                self.s.pot(pot, 0.3)
                self.s.pot(pot, 0.4)
                self.assertSaw([name])
                self.s.press("ASSIGN")
                self.s.ms(100)

    def test_env_3_and_4_stage_destinations_under_shift(self):
        for pot, name in (("FIL_SUS", "E3-S"), ("FIL_DEC", "E3-d"), ("FIL_REL", "E3-r"), ("AMP_ATT", "E4-A"), ("AMP_DEC", "E4-d")):
            with self.subTest(pot=pot):
                self.s.press("ASSIGN", "9")
                self.s.history()
                self.s.hold("SHIFT")
                self.s.pot(pot, 0.3)
                self.s.pot(pot, 0.4)
                self.assertSaw([name])
                self.s.let_go("SHIFT")
                self.s.press("ASSIGN")
                self.s.ms(100)

    def test_lfo_1_knob_destinations(self):
        for pot, name in (("LFO_AMOUNT", "L.An"), ("LFO_FREQ", "L.rt")):
            with self.subTest(pot=pot):
                self.s.press("ASSIGN", "4")
                self.s.history()
                self.s.pot(pot, 0.3)
                self.s.pot(pot, 0.4)
                self.assertSaw([name])
                self.s.press("ASSIGN")
                self.s.ms(100)


class EnvelopeTimes(PanelTest):
    """The table under Envelopes in MANUAL.md."""

    def setUp(self):
        super().setUp()
        self.manual()

    def assertKnob(self, pot, fraction, text):
        self.s.pot(pot, 0.5 if fraction != 0.5 else 0.25)
        self.s.pot(pot, fraction)
        shown = self.s.digits()
        self.assertEqual(shown, segments_of(text), f"{pot} at {fraction}: shows {readable(shown)!r}, expected {text!r}")

    def test_attack(self):
        for fraction, text in ((0.0, "   0"), (0.1, "   3"), (0.25, "  17"), (0.5, " 154"), (0.75, " 1.25"), (1.0, " 10.0")):
            self.assertKnob("AMP_ATT", fraction, text)
            self.assertKnob("FIL_ATT", fraction, text)

    def test_decay_and_release(self):
        for fraction, text in ((0.0, "   3"), (0.1, "   9"), (0.25, "  37"), (0.5, " 311"), (0.75, " 2.50"), (1.0, " 20.0")):
            for pot in ("AMP_DEC", "AMP_REL", "FIL_DEC", "FIL_REL"):
                self.assertKnob(pot, fraction, text)

    def test_env_3_times_read_the_same(self):
        self.s.hold("SHIFT")
        self.s.pot("FIL_ATT", 0.0)  # Env 3's attack is 0: the knob picks it up there
        self.s.pot("FIL_ATT", 0.5)
        self.assertShows(" 154")
        self.s.let_go("SHIFT")


class MidiPlaying(PanelTest):
    def test_a_note_opens_one_voice_and_the_pedal_holds_it(self):
        self.s.note(60)
        self.assertEqual(len(self.s.gated()), 1)
        self.s.cc(64, 127)
        self.s.note_off(60)
        self.s.ms(50)
        self.assertEqual(len(self.s.gated()), 1, "held by the pedal")
        self.s.cc(64, 0)
        self.s.ms(5)
        self.assertEqual(self.s.gated(), [])

    def test_all_notes_off(self):
        self.s.note(60)
        self.s.note(64)
        self.s.cc(123, 0)
        self.s.ms(5)
        self.assertEqual(self.s.gated(), [])

    def test_every_channel_plays_by_default(self):
        for channel in (0, 5, 15):
            self.s.note(60 + channel, channel=channel)
        self.assertEqual(len(self.s.gated()), 3)

    def test_the_channel_set_is_the_only_one_heard(self):
        self.s.setting("midi-channel", 5)
        self.s.note(60, channel=3)
        self.assertEqual(self.s.gated(), [], "channel 4")
        self.s.note(62, channel=4)
        self.assertEqual(len(self.s.gated()), 1, "channel 5")

    def test_channel_mode_plays_layer_2_on_the_next_channel(self):
        self.s.setting("layers", 3)
        self.s.setting("midi-channel", 5)
        self.s.note(60, channel=4)
        self.assertEqual([g < 4 for g in self.s.gated()], [True], "channel 5: layer 1")
        self.s.note(64, channel=5)
        self.assertEqual([g < 4 for g in self.s.gated()], [True, False], "channel 6: layer 2")
        self.s.note(67, channel=6)
        self.assertEqual(len(self.s.gated()), 2, "channel 7 plays nothing")

    def test_controllers_in_channel_mode_go_to_their_layer(self):
        self.s.setting("layers", 3)
        self.s.cc(74, 64, channel=1)
        self.s.ms(5)
        self.assertEqual(self.s.params(1)["cutoff"], 50)
        self.assertEqual(self.s.params(0)["cutoff"], 99, "resonance would reach both: it's one wire")

    def test_a_voice_per_channel(self):
        self.s.setting("layers", 4)
        self.s.note(60, channel=0)
        self.s.note(60, channel=2)
        self.s.note(60, channel=7)
        self.assertEqual(self.s.gated(), [0, 2, 7])
        self.s.note(60, channel=8)
        self.assertEqual(self.s.gated(), [0, 2, 7], "channel 9 plays nothing")

    def test_mpe_members_and_master(self):
        self.s.setting("layers", 5)
        self.s.note(60, channel=1)
        self.s.note(64, channel=3)
        self.assertEqual(len(self.s.gated()), 2)
        self.s.cc(123, 0)
        self.s.ms(5)
        self.s.note(60, channel=0)
        self.s.note(64, channel=0)
        self.assertEqual(len(self.s.gated()), 2, "the master plays polyphonically")

    def test_pitch_bend_raises_the_pitch(self):
        self.s.note(60)
        self.s.ms(100)
        before = self.s.pitch_of_gated_voice()
        self.s.midi(0xE0, 0x7F, 0x7F)
        self.s.ms(100)
        self.assertGreater(self.s.pitch_of_gated_voice(), before)

    def test_din_plays_and_loads(self):
        self.s.din(0x90, 60, 100)
        self.s.ms(2)
        self.assertEqual(len(self.s.gated()), 1)
        self.s.din(0xC0, 3)
        self.s.ms(5)
        self.assertShows("P  4")

    def test_the_version_request(self):
        major, minor, patch = (int(x) for x in VERSION.split("."))
        self.assertIn(bytes([major, minor, patch]), self.s.version())

    def test_the_stock_update_mode_sysex_enters_the_bootloader(self):
        self.s.cmd("midi " + (pro800.SYSEX_HEADER + b"\x03\x30\xF7").hex())
        self.assertEqual(self.s.cmd("ms 5"), "update mode")


class VoiceModes(PanelTest):
    def test_poly(self):
        for note in (60, 64, 67):
            self.s.note(note)
        self.assertEqual(len(self.s.gated()), 3)

    def test_mono_one_voice_every_key_held(self):
        self.s.setting("voice 1", 1)
        for note in (60, 64, 67):
            self.s.note(note)
        self.assertEqual(len(self.s.gated()), 1)
        self.assertEqual(self.s.probe()["held"][0], [60, 64, 67])
        self.s.note_off(67)
        self.assertEqual(len(self.s.gated()), 1, "back to the note still held")

    def test_legato_one_voice(self):
        self.s.setting("voice 1", 2)
        for note in (60, 64):
            self.s.note(note)
        self.assertEqual(len(self.s.gated()), 1)

    def test_unison_plays_every_voice(self):
        self.s.switch("UNISON")
        self.s.note(60)
        self.assertEqual(len(self.s.gated()), 8)
        self.s.note_off(60)
        self.s.switch("UNISON")

    def test_unison_voices_and_how_many_notes_fit(self):
        self.s.switch("UNISON")
        for voices, notes in ((2, 4), (3, 2), (4, 2), (5, 1), (6, 1), (7, 1), (8, 1)):
            self.s.setting("unison-voices", voices)
            keys = range(60, 60 + notes + 1)
            for i, key in enumerate(keys):
                self.s.note(key)
                if i < notes:
                    self.assertEqual(len(self.s.gated()), voices * (i + 1), f"{voices} voices, note {i + 1}")
            # one more note: a whole stack goes to it (one line: it takes the stack over), and the count stays
            self.assertEqual(len(self.s.gated()), voices * notes, f"{voices} voices, all taken")
            for key in keys:
                self.s.note_off(key)
            self.s.ms(1)

    def test_unison_voices_leave_the_spare_voices_silent(self):
        self.s.switch("UNISON")
        self.s.setting("unison-voices", 3)
        for key in (60, 62, 64):
            self.s.note(key)
        self.assertEqual(self.s.gated(), [0, 1, 2, 3, 4, 5])

    def test_a_note_off_releases_its_whole_stack(self):
        self.s.switch("UNISON")
        self.s.setting("unison-voices", 2)
        self.s.note(60)
        self.s.note(64)
        self.s.note_off(60)
        self.assertEqual(len(self.s.gated()), 2)

    def test_mono_and_legato_play_one_stack(self):
        self.s.switch("UNISON")
        self.s.setting("unison-voices", 2)
        for mode in (1, 2):
            self.s.setting("voice 1", mode)
            self.s.note(60)
            self.s.note(64)
            self.assertEqual(len(self.s.gated()), 2, f"mode {mode}")
            self.s.note_off(60)
            self.s.note_off(64)

    def test_unison_voices_in_a_layer_of_four(self):
        self.s.switch("UNISON")
        self.s.setting("layers", 2)  # stack: 4 + 4
        self.s.setting("unison-voices", 2)
        self.s.note(60)
        self.s.note(64)
        self.s.note(67)  # steals the first stack on each layer
        self.assertEqual(len(self.s.gated()), 8)
        self.s.note_off(64)
        self.assertEqual(len(self.s.gated()), 4)
        self.s.note_off(67)
        self.s.setting("unison-voices", 8)  # no more than the layer has: one line on its four
        self.s.note(60)
        self.assertEqual(len(self.s.gated()), 8)

    def test_changing_unison_voices_releases_the_notes(self):
        self.s.switch("UNISON")
        self.s.setting("unison-voices", 4)
        self.s.note(60)
        self.s.setting("unison-voices", 2)
        self.assertEqual(self.s.gated(), [])

    def test_unison_voices_outside_2_to_8_are_refused(self):
        self.s.setting("unison-voices", 3)
        self.s.setting("unison-voices", 1)
        self.s.setting("unison-voices", 9)
        self.assertEqual(self.setting_value("unison-voices"), 3)

    def test_unison_voices_stay_in_the_patch(self):
        self.s.setting("unison-voices", 3)
        self.s.ms(10)
        self.store_patch(5)
        self.s.setting("unison-voices", 6)
        self.power_cycle()
        self.s.ms(1600)
        self.s.press("PRESET")
        self.type_number(5)
        self.s.ms(1100)
        self.assertEqual(self.setting_value("unison-voices"), 3)

    def test_a_patch_from_before_unison_voices_plays_all_eight(self):
        self.assertEqual(self.setting_value("unison-voices"), 8)
        self.s.switch("UNISON")
        self.s.note(60)
        self.assertEqual(len(self.s.gated()), 8)

    def setting_value(self, name):
        d = self.s.reply(0x54)
        i = pro800.SETTING_NAMES.index(name)
        return d[1 + 2 * i] << 7 | d[2 + 2 * i]


class MonoEnvelopes(PanelTest):
    """Mono restarts Env 3 and 4 from 0, so a high sustain still sweeps (P71: Env 3 → pitch at sustain 99). Pitch shows the
    level: the route's CV, less the note's own semitones."""

    def setUp(self):
        super().setUp()
        self.s.no_routes()
        for name in ("amp", "fil", "env3", "env4"):
            self.s.param(0, name + "-att", 9000)
            self.s.param(0, name + "-dec", 3000)
            self.s.param(0, name + "-sus", 16383)
            self.s.param(0, name + "-rel", 12000)

    def route(self, source):
        i = pro800.SOURCE_NAMES.index(source)
        self.s.request(0x56, 0, i, pro800.DEST_NAMES.index("pitch"), 198 >> 7, 198 & 0x7F)
        self.s.ms(20)

    def sweep(self, ms=400):
        """How far the pitch moves, in semitones, sampled every 5 ms from now."""
        cvs = []
        for _ in range(ms // 5):
            self.s.ms(5)
            cvs.append(self.s.cvs()[0])
        return (max(cvs) - min(cvs)) / CV_PER_SEMITONE

    def test_a_legato_note_sweeps_again(self):
        self.s.setting("voice 1", 1)
        for source in ("env3", "env4"):
            self.route(source)
            self.s.note(53)
            self.s.ms(3000)
            self.s.note(50)
            self.assertGreater(self.sweep(), 10, source)
            self.s.note_off(53)
            self.s.note_off(50)
            self.s.ms(10)
            self.s.request(0x56, 0, pro800.SOURCE_NAMES.index(source), pro800.DEST_NAMES.index("pitch"), 0, 99)
            self.s.rest()

    def test_back_to_a_held_note_sweeps_again(self):
        self.s.setting("voice 1", 1)
        self.route("env3")
        self.s.note(53)
        self.s.note(50)
        self.s.ms(3000)
        self.s.note_off(50)
        self.assertGreater(self.sweep(), 10)

    def test_a_press_in_the_release_sweeps_again(self):
        self.s.setting("voice 1", 1)
        self.route("env3")
        self.s.note(53)
        self.s.ms(3000)
        self.s.note_off(53)
        self.s.ms(50)  # the long release has hardly begun
        self.s.note(53)
        self.assertGreater(self.sweep(), 10)

    def test_legato_mode_does_not(self):
        self.s.setting("voice 1", 2)
        self.route("env3")
        self.s.note(53)
        self.s.ms(3000)
        self.s.note(50)
        self.assertLess(self.sweep(), 0.1)

    def test_a_short_attack_on_the_amp_and_filter_envelopes_goes_on_from_where_they_are(self):
        self.s.setting("voice 1", 1)
        for source in ("amp-env", "filter-env"):
            self.route(source)
            self.s.param(0, "amp-att", 0)
            self.s.param(0, "fil-att", 0)
            self.s.note(53)
            self.s.ms(3000)
            self.s.note(50)
            self.assertLess(self.sweep(), 0.1, source)
            self.s.note_off(53)
            self.s.note_off(50)
            self.s.request(0x56, 0, pro800.SOURCE_NAMES.index(source), pro800.DEST_NAMES.index("pitch"), 0, 99)
            self.s.rest()


class Via(PanelTest):
    """A route's Via: source x via x depth. What it does is read from the CVs."""

    def setUp(self):
        super().setUp()
        self.s.no_routes()
        self.s.param(0, "amp-sus", 16383)
        self.s.ms(20)

    def route(self, source, dest, depth=99, layer=0):
        d = depth + 99
        return bytes([0x7F, 0x56]) in self.s.request(0x56, layer, pro800.SOURCE_NAMES.index(source), pro800.DEST_NAMES.index(dest), d >> 7, d & 0x7F)

    def via(self, source, dest, value, layer=0):
        value = 1000 + pro800.SOURCE_NAMES.index(value) if isinstance(value, str) else value
        return bytes([0x7F, 0x62]) in self.s.request(0x62, layer, pro800.SOURCE_NAMES.index(source), pro800.DEST_NAMES.index(dest), value >> 7, value & 0x7F)

    def vias(self, layer=0):
        reply = self.s.request(0x63, layer)
        start = reply.index(pro800.SYSEX_HEADER + bytes([0x63, layer])) + pro800.HEADER_SIZE + 2
        return [reply[start + 2 * r] << 7 | reply[start + 2 * r + 1] for r in range(16)]

    def pitch(self):
        gated = self.s.gated()  # a new note takes the oldest silent voice
        return self.s.cvs()[gated[0] if gated else 0] / CV_PER_SEMITONE

    def fixed_pitch_swing(self, via):
        """How many semitones Fixed -> Pitch A at 99 adds to a held note, with this Via."""
        self.s.note(40)
        self.s.ms(20)
        base = self.pitch()
        self.route("fixed", "pitch-a")
        if via is not None:
            self.assertTrue(self.via("fixed", "pitch-a", via))
        self.s.ms(20)
        moved = self.pitch() - base
        self.s.note_off(40)
        self.route("fixed", "pitch-a", 0)
        self.s.rest()
        return moved

    def tenths(self, source, dest, tenths, layer=0):
        t = tenths + 990
        return bytes([0x7F, 0x66]) in self.s.request(0x66, layer, pro800.SOURCE_NAMES.index(source), pro800.DEST_NAMES.index(dest), t >> 7, t & 0x7F)

    def fines(self, layer=0):
        reply = self.s.request(0x67, layer)
        start = reply.index(pro800.SYSEX_HEADER + bytes([0x67, layer])) + pro800.HEADER_SIZE + 2
        return [reply[start + r] - 9 for r in range(16)]

    def fixed_pitch_at(self, tenths):
        """How many semitones Fixed -> Pitch A adds to a held note at a depth in tenths."""
        self.s.note(40)
        self.s.ms(20)
        base = self.pitch()
        self.assertTrue(self.tenths("fixed", "pitch-a", tenths))
        self.s.ms(20)
        moved = self.pitch() - base
        self.s.note_off(40)
        self.route("fixed", "pitch-a", 0)
        self.s.rest()
        return moved

    def test_a_depth_in_tenths_lands_between_the_whole_numbers(self):
        # 24 x (d / 99)^2: a fifth is about 53.5, where 53 and 54 are 6.88 and 7.14
        self.assertAlmostEqual(self.fixed_pitch_at(535), 24 * (53.5 / 99) ** 2, delta=0.01)
        self.assertAlmostEqual(self.fixed_pitch_at(530), 24 * (53 / 99) ** 2, delta=0.01)
        self.assertAlmostEqual(self.fixed_pitch_at(-535), -24 * (53.5 / 99) ** 2, delta=0.01)

    def test_tenths_keep_the_nearest_whole_number_for_older_firmware(self):
        for tenths, whole, fine in ((535, 54, -5), (534, 53, 4), (-535, -54, 5), (1, 1, -9), (-1, -1, 9), (985, 99, -5)):
            self.assertTrue(self.tenths("lfo", "cutoff", tenths))
            self.assertEqual(self.depth_and_fine(), (whole, fine), tenths)

    def depth_and_fine(self):
        reply = self.s.request(0x57, 0)
        start = reply.index(pro800.SYSEX_HEADER + bytes([0x57, 0])) + pro800.HEADER_SIZE + 2
        for r in range(16):
            if reply[start + 4 * r] == pro800.SOURCE_NAMES.index("lfo") and reply[start + 4 * r + 1] == pro800.DEST_NAMES.index("cutoff"):
                return (reply[start + 4 * r + 2] << 7 | reply[start + 4 * r + 3]) - 99, self.fines()[r]
        return None

    def test_a_whole_depth_replaces_the_tenths(self):
        self.assertTrue(self.tenths("lfo", "cutoff", 535))
        self.assertTrue(self.route("lfo", "cutoff", 54))
        self.assertEqual(self.depth_and_fine(), (54, 0))

    def test_tenths_survive_a_save_and_a_load(self):
        self.assertTrue(self.tenths("lfo", "cutoff", 535))
        self.s.request(0x5D, 0, 127)
        self.s.request(0x5F)
        self.s.ms(20)
        self.assertNotIn(-5, self.fines(), "the init sound has none")
        self.s.request(0x5C, 0, 127)
        self.s.ms(20)
        self.assertEqual(self.depth_and_fine(), (54, -5))

    def test_a_move_keeps_the_tenths_at_the_same_whole_number(self):
        self.assertTrue(self.tenths("lfo", "cutoff", 535))
        lfo, cutoff, res = pro800.SOURCE_NAMES.index("lfo"), pro800.DEST_NAMES.index("cutoff"), pro800.DEST_NAMES.index("resonance")
        d = 54 + 99
        self.assertIn(bytes([0x7F, 0x61]), self.s.request(0x61, 0, lfo, cutoff, lfo, res, d >> 7, d & 0x7F))
        self.assertIn(-5, self.fines())
        d = 60 + 99
        self.assertIn(bytes([0x7F, 0x61]), self.s.request(0x61, 0, lfo, res, lfo, cutoff, d >> 7, d & 0x7F))
        self.assertEqual(self.depth_and_fine(), (60, 0))

    def test_at_zero_it_loses_its_tenths(self):
        self.assertTrue(self.tenths("lfo", "cutoff", 535))
        self.assertTrue(self.tenths("lfo", "cutoff", 0))
        self.assertEqual(self.depth_and_fine(), (0, 0))

    def test_the_ends_hold(self):
        self.assertTrue(self.tenths("lfo", "cutoff", 990))
        self.assertEqual(self.depth_and_fine(), (99, 0))
        t = 1980 + 40  # past the end, as a request can say
        self.s.request(0x66, 0, pro800.SOURCE_NAMES.index("lfo"), pro800.DEST_NAMES.index("cutoff"), t >> 7, t & 0x7F)
        self.assertEqual(self.depth_and_fine(), (99, 0))

    def test_a_number_multiplies_the_pitch_range(self):
        self.assertAlmostEqual(self.fixed_pitch_swing(None), 24, delta=0.1)
        self.assertAlmostEqual(self.fixed_pitch_swing(200), 48, delta=0.1)
        self.assertAlmostEqual(self.fixed_pitch_swing(25), 6, delta=0.1)

    def test_a_source_multiplies_it_too(self):
        self.s.note(40)
        self.s.ms(20)
        base = self.pitch()
        self.route("fixed", "pitch-a")
        self.assertTrue(self.via("fixed", "pitch-a", "mod-wheel"))
        self.s.ms(20)
        self.assertAlmostEqual(self.pitch(), base, delta=0.1, msg="the wheel is down")
        self.s.cc(1, 127)
        self.s.ms(20)
        self.assertAlmostEqual(self.pitch() - base, 24, delta=0.3, msg="the wheel is up")

    def test_an_lfo_rate_is_exactly_an_octave_at_depth_99_via_a_quarter(self):
        s = self.s
        s.setting("lfo-sync", 1)
        s.setting("lfo-trigger", 1)
        s.setting("shape 1 1", 2)
        s.param(0, "lfo-freq", 42 * 16383 // 99)  # 1/4: 500 ms before any clock
        self.route("lfo", "pitch")
        self.route("fixed", "lfo-rate")
        self.assertTrue(self.via("fixed", "lfo-rate", 25))
        s.ms(20)
        s.note(60)
        wraps, last = [], None
        for ms in range(4000):
            s.ms(1)
            cv = s.cvs()[0]
            if last is not None and last - cv > 2000:
                wraps.append(ms)
            last = cv
        periods = [b - a for a, b in zip(wraps, wraps[1:])]
        self.assertAlmostEqual(sum(periods) / len(periods), 250, delta=1)

    def test_a_via_that_moves_fast_makes_the_route_fast(self):
        s = self.s
        self.route("fixed", "cutoff")
        self.assertTrue(self.via("fixed", "cutoff", "env3"))
        s.param(0, "env3-att", 0)
        s.param(0, "env3-dec", 40 * 16383 // 99)
        s.param(0, "env3-sus", 0)
        s.ms(20)
        s.note(60)
        seen = []
        for _ in range(60):
            s.ms(1)
            seen.append(s.cvs()[CV_FIL1])
        self.assertGreater(max(seen) - min(seen), 5 * CV_PER_SEMITONE, "Env 3 runs, read only by the Via")

    def test_an_envelope_via_itself_is_squared(self):
        s = self.s
        s.param(0, "env3-att", 0)
        s.param(0, "env3-dec", 16383)  # slow, so it can be read as it falls
        s.param(0, "env3-sus", 8192)   # a level of about 0.5, and it stays there
        self.route("env3", "pitch-a")
        s.ms(20)
        s.note(40)
        s.ms(120000)
        base_plain = self.pitch()
        s.note_off(40)
        s.ms(2000)
        self.assertTrue(self.via("env3", "pitch-a", "env3"))
        s.ms(20)
        s.note(40)
        s.ms(120000)
        squared = self.pitch()
        s.note_off(40)
        s.ms(2000)
        self.route("env3", "pitch-a", 0)
        s.rest()
        s.note(40)
        s.ms(20)
        off = self.pitch()
        level = (base_plain - off) / 24
        self.assertAlmostEqual(level, 0.5, delta=0.02)
        self.assertAlmostEqual((squared - off) / 24, level * level, delta=0.02, msg="0.5 x 0.5")

    def test_a_macro_as_via(self):
        s = self.s
        s.note(40)
        s.ms(20)
        base = self.pitch()
        self.route("fixed", "macro1", 50)
        self.route("fixed", "pitch-a")
        self.assertTrue(self.via("fixed", "pitch-a", "macro1"))
        s.ms(30)
        self.assertAlmostEqual(self.pitch() - base, 24 * 50 / 99, delta=0.3)

    def test_what_isnt_a_via_is_refused(self):
        self.route("lfo", "cutoff")
        self.route("lfo", "macro1")
        before = self.vias()
        for value in (801, 999, 1000, 1000 + pro800.SOURCE_NAMES.index("fixed"), 1000 + len(pro800.SOURCE_NAMES)):
            self.assertFalse(self.via("lfo", "cutoff", value), value)
        self.assertFalse(self.via("lfo", "macro1", "macro2"), "it would depend on its own result")
        self.assertFalse(self.via("lfo", "pitch-a", 200), "no such route")
        self.assertEqual(self.vias(), before)

    def test_a_number_of_1_reads_as_none(self):
        self.route("lfo", "cutoff")
        self.assertTrue(self.via("lfo", "cutoff", 100))
        self.assertEqual(self.vias()[0], 0)

    def test_it_stays_in_the_patch(self):
        self.route("lfo", "cutoff")
        self.route("env3", "pitch-a")
        self.via("lfo", "cutoff", 25)
        self.via("env3", "pitch-a", "mod-wheel")
        self.s.ms(10)
        self.store_patch(5)
        self.power_cycle()
        self.s.ms(1600)
        self.s.request(0x5C, 0, 4)
        wheel = 1000 + pro800.SOURCE_NAMES.index("mod-wheel")
        self.assertEqual(self.vias()[:2], [25, wheel])

    def test_a_new_route_in_an_old_slot_starts_at_1(self):
        self.route("lfo", "cutoff")
        self.via("lfo", "cutoff", 25)
        self.route("lfo", "cutoff", 0)
        self.s.rest()
        self.assertEqual(self.vias()[0], 0)
        self.route("env3", "pitch-a")
        self.assertEqual(self.vias()[0], 0)

    def test_a_moved_route_keeps_its_via(self):
        names, dests = pro800.SOURCE_NAMES, pro800.DEST_NAMES
        self.route("lfo", "cutoff")
        self.via("lfo", "cutoff", 300)
        self.s.request(0x61, 0, names.index("lfo"), dests.index("cutoff"), names.index("env3"), dests.index("pitch-a"), 0, 120)
        self.assertEqual(self.vias()[0], 300)

    def test_a_macro_via_doesnt_move_into_a_macro(self):
        names, dests = pro800.SOURCE_NAMES, pro800.DEST_NAMES
        self.route("lfo", "cutoff")
        self.via("lfo", "cutoff", "macro1")
        self.s.request(0x61, 0, names.index("lfo"), dests.index("cutoff"), names.index("lfo"), dests.index("macro2"), 0, 120)
        self.assertEqual(self.vias()[0], 0)

    def test_copying_a_layer_takes_it_along(self):
        self.route("lfo", "cutoff")
        self.via("lfo", "cutoff", 300)
        self.s.hold("SHIFT")
        self.s.press("SEQ2")
        self.s.let_go("SHIFT")
        self.s.ms(10)
        self.assertEqual(self.s.routes(1), self.s.routes(0))
        self.assertEqual(self.vias(1)[0], 300)

    def test_rates_and_times_stop_at_256_times(self):
        s = self.s
        s.note(60)
        self.route("fixed", "lfo-rate")
        self.via("fixed", "lfo-rate", 800)
        self.route("fixed", "amp-rel")
        self.via("fixed", "amp-rel", 800)
        self.route("fixed", "lfo2-rate")
        self.via("fixed", "lfo2-rate", 800)
        for _ in range(200):
            s.ms(5)
        self.assertEqual(len(s.gated()), 1, "and it all goes on running")
        self.assertEqual(s.request(0x54)[:1] != b"", True, "the firmware still answers")

    def test_the_tool_prints_it(self):
        self.route("lfo", "cutoff")
        self.via("lfo", "cutoff", 25)
        self.assertEqual(pro800.via_text(25), "0.25")
        self.assertEqual(pro800.via_text(0), "1")
        self.assertEqual(pro800.via_text(1005), "mod-wheel")
        self.assertEqual(pro800.via_from_text("0.25"), 25)
        self.assertEqual(pro800.via_from_text("mod-wheel"), 1005)
        for bad in ("fixed", "9", "0", "foo"):
            self.assertIsNone(pro800.via_from_text(bad), bad)


class ShiftOnASyncedEnvelope(PanelTest):
    """Under Shift the ADSR knobs are Env 3's and Env 4's, and the display speaks for those, not the filter's and the amp's."""

    def turned(self, pot, name):
        self.s.pot(pot, 0.3)
        self.s.hold("SHIFT")
        self.s.ms(10)
        self.s.param(0, name + "-att", 0)  # the knob takes over at once
        self.s.param(0, name + "-dec", 8192)
        self.s.param(0, name + "-sus", 0)
        self.s.param(0, name + "-rel", 0)
        self.s.ms(10)
        self.s.pot(pot, 0.0)
        self.s.pot(pot, 0.5)
        self.s.ms(150)
        shown = readable(self.s.digits())
        self.s.let_go("SHIFT")
        return shown

    def test_env_3_shows_its_share(self):
        self.s.setting("cycle 1", 4)
        self.s.setting("cycle-sync 1 env3", 2)
        self.assertEqual(self.turned("FIL_ATT", "env3"), "  50")

    def test_env_4_shows_its_share(self):
        self.s.setting("cycle 1", 8)
        self.s.setting("cycle-sync 1 env4", 2)
        self.assertEqual(self.turned("AMP_ATT", "env4"), "  50")

    def test_without_shift_the_filter_and_amp_envelopes_still_show_theirs(self):
        self.s.setting("cycle 1", 2)
        self.s.setting("cycle-sync 1 filter", 2)
        self.s.param(0, "fil-att", 0)
        self.s.param(0, "fil-dec", 8192)
        self.s.param(0, "fil-sus", 0)
        self.s.param(0, "fil-rel", 0)
        self.s.ms(10)
        self.s.pot("FIL_ATT", 0.0)
        self.s.pot("FIL_ATT", 0.5)
        self.s.ms(150)
        self.assertEqual(readable(self.s.digits()), "  50")


class FilterBeforeVca(PanelTest):
    """Issue 17: a voice's VCA mustn't open on a cutoff the S&H still holds from before, or a closed filter lets a click through."""

    def setUp(self):
        super().setUp()
        self.s.no_routes()
        self.s.param(0, "amp-att", 0)
        self.s.param(0, "amp-rel", 0)
        self.s.ms(20)

    def held(self):
        return [int(x) for x in self.s.cmd("held").split()]

    def released_chord(self, base=60):
        for i in range(8):
            self.s.note(base + i)
        self.s.ms(20)
        for i in range(8):
            self.s.note_off(base + i)
        self.s.ms(50)
        self.assertEqual(self.s.gated(), [])

    def test_a_note_landing_between_two_ticks_writes_its_cutoff_first(self):
        self.s.setting("keytrack", 2)  # so a note far from the last one needs another cutoff
        self.s.ms(5)
        for reads in range(1, 9):
            with self.subTest(reads=reads):
                self.released_chord(20)
                self.s.cmd("later %d %s" % (reads, bytes((0x90, 110, 100)).hex()))
                self.s.ms(4)
                self.assertTrue(self.s.gated(), "it played")
                self.assertEqual(self.s.cmd("vcaahead"), "0")
                self.s.note_off(110)
                self.s.ms(20)

    def test_a_silent_voice_follows_the_cutoff_knob(self):
        self.s.param(0, "cutoff", 16383)
        self.released_chord()
        self.s.param(0, "cutoff", 0)
        self.s.ms(30)  # every voice's turn, one a pass
        cvs = self.s.cvs()
        for v in range(8):
            self.assertLess(cvs[CV_FIL1 + v], 25000, f"voice {v}, was 50176 with the knob up")
            self.assertEqual(self.held()[CV_FIL1 + v], cvs[CV_FIL1 + v], f"voice {v}, in its S&H")

    def test_a_silent_voices_vca_stays_shut(self):
        self.s.param(0, "cutoff", 16383)
        self.released_chord()
        self.s.param(0, "cutoff", 0)
        self.s.ms(30)
        self.assertEqual([self.held()[CV_AMP1 + v] for v in range(8)], [0] * 8)

    def test_the_issues_steps_never_open_a_vca_on_the_old_cutoff(self):
        self.s.param(0, "cutoff", 16383)
        self.released_chord()
        self.s.param(0, "cutoff", 0)
        self.s.ms(30)
        for i in range(8):
            self.s.cmd("later %d %s" % (i % 4 + 1, bytes((0x90, 60 + i, 100)).hex()))  # any tick of a pass
            self.s.ms(4)
            self.s.note_off(60 + i)
            self.s.ms(20)
        self.assertEqual(self.s.cmd("vcaahead"), "0")


class Retrigger(PanelTest):
    """A retriggered envelope with a long attack fades out first, so the note has its whole attack (issue 32). A short attack
    goes on from where it is, as nothing could be heard of the fade but a gap."""

    def setUp(self):
        super().setUp()
        self.s.no_routes()
        self.s.param(0, "amp-sus", 16383)
        self.s.param(0, "amp-rel", 60 * 16383 // 99)
        self.s.ms(20)

    def level(self, voice=0):
        return self.s.cvs()[CV_AMP1 + voice]

    def rise(self, voice, top):
        """ms until the voice's VCA is at 90 % of the top"""
        for n in range(0, 12000, 5):
            if self.level(voice) >= 0.9 * top:
                return n
            self.s.ms(5)
        self.fail("it never got there")

    def played_long(self):
        self.s.param(0, "amp-att", 85 * 16383 // 99)
        self.s.ms(20)
        self.s.note(60)
        self.s.ms(9000)
        return self.level()

    def test_a_note_in_its_release_has_the_whole_attack_again(self):
        top = self.played_long()
        self.s.note_off(60)
        self.s.ms(20)
        self.assertGreater(self.level(), 0.5 * top, "the release has hardly begun")
        self.s.note(60)
        replayed = self.rise(0, top)
        self.s.note_off(60)
        self.s.ms(30000)
        self.s.note(60)
        fresh = self.rise(self.s.gated()[0], top)
        self.assertAlmostEqual(replayed, fresh, delta=30)

    def test_the_fade_is_short_and_has_no_jump(self):
        top = self.played_long()
        self.s.note(60)  # the note it's on, once more
        levels = [self.level()]
        for _ in range(12):
            self.s.ms(1)
            levels.append(self.level())
        self.assertLess(min(levels), 0.2 * top, "down in the 12 ms")
        for a, b in zip(levels, levels[1:]):
            self.assertLess(a - b, 0.25 * top, f"a step, not a click: {levels}")

    def test_a_short_attack_doesnt_dip(self):
        self.s.param(0, "amp-att", 8 * 16383 // 99)
        self.s.ms(20)
        self.s.note(60)
        self.s.ms(2000)
        top = self.level()
        self.s.note(60)
        for _ in range(12):
            self.s.ms(1)
            self.assertGreater(self.level(), 0.9 * top)

    def test_mono_with_a_long_attack(self):
        self.s.setting("voice 1", 1)
        top = self.played_long()
        self.s.note(64)  # 60 held
        self.s.ms(20)
        self.assertLess(self.level(), 0.3 * top)
        self.assertGreater(self.rise(0, top), 1000)

    def test_legato_keeps_on(self):
        self.s.setting("voice 1", 2)
        top = self.played_long()
        self.s.note(64)
        self.s.ms(20)
        self.assertGreater(self.level(), 0.9 * top)

    def test_a_first_note_isnt_held_up(self):
        self.s.param(0, "amp-att", 85 * 16383 // 99)
        self.s.ms(20)
        self.s.note(60)
        self.s.ms(1)
        before = self.level()
        self.s.ms(5)
        self.assertGreater(self.level(), before, "rising from its first milliseconds")


class Sustain(PanelTest):
    """Once the decay's time is up the level follows the sustain knob, whatever the decay (issue 33)."""

    def setUp(self):
        super().setUp()
        self.s.no_routes()
        self.s.param(0, "cutoff", 10 * 16383 // 99)
        self.s.param(0, "fil-env-amt", 16383)
        self.s.param(0, "fil-att", 0)

    def cutoff(self):
        return self.s.cvs()[CV_FIL1 + self.s.gated()[0]]

    def time_to_follow(self, decay):
        self.s.param(0, "fil-dec", decay * 16383 // 99)
        self.s.param(0, "fil-sus", 90 * 16383 // 99)
        self.s.note(60)
        self.s.ms(30000)
        high = self.cutoff()
        self.s.param(0, "fil-sus", 20 * 16383 // 99)
        self.s.ms(5000)
        low = self.cutoff()
        self.s.param(0, "fil-sus", 90 * 16383 // 99)
        self.s.ms(5000)
        self.assertAlmostEqual(self.cutoff(), high, delta=40)
        self.s.param(0, "fil-sus", 20 * 16383 // 99)
        n = 0
        while abs(self.cutoff() - low) > 0.1 * abs(high - low) and n < 5000:
            self.s.ms(1)
            n += 1
        self.s.note_off(60)
        self.s.ms(100)
        return n

    def test_any_decay_follows_the_knob_at_once(self):
        for decay in (10, 60, 85):
            self.assertLess(self.time_to_follow(decay), 20, f"decay {decay}")

    def test_the_decay_still_takes_its_time(self):
        self.s.param(0, "fil-dec", 85 * 16383 // 99)
        self.s.param(0, "fil-sus", 0)
        self.s.note(60)
        self.s.ms(5)
        top = self.cutoff()
        self.s.ms(1000)
        middle = self.cutoff()
        self.s.ms(25000)
        bottom = self.cutoff()
        self.assertGreater(middle, bottom + 0.2 * (top - bottom), "falling a second in")
        self.assertLess(middle, top)

    def test_turning_the_sustain_during_the_decay_doesnt_cut_it_short(self):
        self.s.param(0, "fil-dec", 85 * 16383 // 99)
        self.s.param(0, "fil-sus", 50 * 16383 // 99)
        self.s.note(60)
        self.s.ms(5)
        top = self.cutoff()
        self.s.param(0, "fil-sus", 0)
        self.s.ms(200)
        self.assertGreater(self.cutoff(), top - 0.5 * top, "still on its way")

    def test_the_panel_setting_page_changes_the_mode(self):
        self.s.press("SETTINGS", "9")
        self.s.ms(700)
        self.s.turn(1)
        self.assertShows("nono")
        self.s.press("SETTINGS")
        for note in (60, 64):
            self.s.note(note)
        self.assertEqual(len(self.s.gated()), 1)


class LayerModes(PanelTest):
    def test_split_puts_low_keys_on_layer_1s_voices(self):
        self.s.setting("layers", 1)
        self.s.note(48)
        self.s.note(72)
        gated = self.s.gated()
        self.assertEqual(len(gated), 2)
        self.assertLess(gated[0], 4)
        self.assertGreaterEqual(gated[1], 4)

    def test_stack_plays_a_key_on_both_layers(self):
        self.s.setting("layers", 2)
        self.s.note(60)
        gated = self.s.gated()
        self.assertEqual(len(gated), 2)
        self.assertEqual([g < 4 for g in gated], [True, False])

    def test_voices_per_layer(self):
        self.s.setting("layers", 1)
        self.s.setting("layer-voices", 6)
        for note in range(48, 54):
            self.s.note(note)
        self.assertEqual(self.s.gated(), [0, 1, 2, 3, 4, 5])
        self.s.note(72)
        self.assertIn(self.s.gated()[-1], (6, 7))

    def test_a_linked_filter_knob_on_layer_2_edits_layer_1(self):
        self.manual()
        self.s.setting("layers", 2)
        self.s.setting("link", 64)
        self.s.press("SEQ2")
        self.s.ms(1100)
        self.s.pot("CUTOFF", 0.7)
        self.assertTrue(self.s.led("SEQ1") and self.s.led("SEQ2"), "both layer LEDs say so")
        self.s.ms(300)
        self.assertEqual(self.s.params(0)["cutoff"], 69)
        self.s.ms(1100)
        self.assertFalse(self.s.led("SEQ1"))


class EnvelopeBehaviour(PanelTest):
    def test_a_key_up_envelope_plays_after_the_key(self):
        self.s.setting("env-up 1", 1)
        self.s.note(60)
        self.s.ms(300)
        self.assertEqual(self.s.amp(), 0, "silent while held")
        self.s.note_off(60)
        self.s.ms(100)
        self.assertGreater(self.s.amp(), 0)

    def test_a_dahdsr_delay_holds_the_note_back(self):
        self.s.setting("dahdsr 1", 1)
        self.s.setting("env-delay 1 amp", 60)
        self.s.note(60)
        self.s.ms(100)
        self.assertEqual(self.s.amp(), 0, "in the delay")
        self.s.ms(1500)
        self.assertGreater(self.s.amp(), 0)

    def test_a_cycling_envelope_repeats_while_held(self):
        self.s.setting("cycle 1", 1)
        self.s.param(0, "amp-att", 0x2000)
        self.s.param(0, "amp-sus", 0)
        self.s.param(0, "amp-dec", 0x2000)
        self.s.param(0, "amp-rel", 0x2000)
        self.s.note(60)
        last, rises = 0, 0
        for _ in range(150):
            self.s.ms(10)
            now = self.s.amp()
            if last == 0 and now > 0:
                rises += 1
            last = now
        self.assertGreaterEqual(rises, 2, "the envelope restarts itself")

    def test_a_count_stops_the_cycling(self):
        self.s.setting("cycle 1", 1)
        self.s.setting("env-count 1 amp", 1)
        self.s.param(0, "amp-att", 0x1000)
        self.s.param(0, "amp-sus", 0)
        self.s.param(0, "amp-dec", 0x1000)
        self.s.param(0, "amp-rel", 0x1000)
        self.s.note(60)
        self.s.ms(1500)
        self.assertEqual(self.s.amp(), 0, "one cycle, then rest")


class ArpGenerative(PanelTest):
    """A mono layer's held list is the note playing, so these read the notes an arp plays. Steps of a 16th at 120 BPM, gate 50 %."""
    WALK, UP, DOWN, RANDOM = 6, 1, 2, 4

    def setUp(self):
        super().setUp()
        self.s.no_routes()
        self.s.setting("voice 1", 1)

    def arp(self, kind, steps=8, chaos=0, extend=0, octaves=1, gate=50):
        self.s.seq_set(length=steps, arp=kind)
        self.s.seq_steps(0, [(0, 0, gate)] * steps)
        self.s.setting("arp-range", octaves - 1)
        self.s.setting("arp-extend", extend)
        self.s.setting("arp-chaos", chaos)

    def notes(self, count):
        """The next notes started, as they start"""
        seen, last = [], None
        for _ in range(count * 60):
            self.s.ms(5)
            held = self.s.probe()["held"][0]
            now = held[-1] if held else None
            if now is not None and now != last:
                seen.append(now)
                if len(seen) == count:
                    break
            last = now
        return seen

    def chord(self, *notes):
        for n in notes:
            self.s.note(n)

    def test_a_walk_goes_two_on_and_one_back_and_turns_at_the_ends(self):
        self.arp(self.WALK)
        self.chord(48, 51, 55, 58)
        self.assertEqual(self.notes(14), [48, 51, 55, 51, 55, 58, 55, 58, 55, 58, 55, 51, 55, 51])

    def test_a_walk_over_two_octaves_goes_on_up(self):
        self.arp(self.WALK, octaves=2)
        self.chord(48, 52, 55)
        self.assertEqual(self.notes(12), [48, 52, 55, 52, 55, 60, 55, 60, 64, 60, 64, 67])

    def test_a_walk_follows_on_from_the_nearest_note_of_a_new_chord(self):
        self.arp(self.WALK)
        self.chord(60, 64, 67)
        self.notes(3)
        for n in (60, 64, 67):
            self.s.note_off(n)
            self.s.note(n + 5)  # a note stays held throughout
        got = self.notes(5)[1:]  # the first may be the old chord's, caught as the keys change
        self.assertTrue(set(got) <= {65, 69, 72}, got)

    def test_a_ratchet_repeats_a_walks_note(self):
        self.arp(self.WALK, steps=1)
        self.s.seq_steps(0, [(0, 0, 50, 1, 3 << 3)])  # 4 ratchets
        self.chord(48, 52, 55)
        seen = []
        for _ in range(12):
            self.s.ms(5)
            held = self.s.probe()["held"][0]
            if held:
                seen.append(held[-1])
        self.assertEqual(len(set(seen)), 1, seen)

    def test_up_is_as_it_was_with_none_of_it_set(self):
        self.arp(self.UP)
        self.chord(60, 64, 67)
        self.assertEqual(self.notes(5), [60, 64, 67, 60, 64])

    def test_range_spreads_the_notes_over_octaves(self):
        self.arp(self.UP, octaves=3)
        self.chord(60, 64)
        self.assertEqual(self.notes(7), [60, 64, 72, 76, 84, 88, 60])

    def test_a_chord_wider_than_an_octave_stays_in_order(self):
        self.arp(self.UP, octaves=2)
        self.chord(60, 76)
        self.assertEqual(self.notes(5), [60, 72, 76, 88, 60])

    def test_the_extension_adds_notes_above_the_chord(self):
        for level, added in ((1, [72]), (2, [72, 79]), (3, [71, 72, 79]), (4, [71, 72, 74, 79])):
            with self.subTest(level):
                self.arp(self.UP, extend=level)
                self.chord(60, 64, 67)
                want = [60, 64, 67] + added
                self.assertEqual(self.notes(len(want)), want)
                for n in (60, 64, 67):
                    self.s.note_off(n)

    def test_a_minor_chord_takes_a_minor_seventh(self):
        self.arp(self.UP, extend=3)
        self.chord(60, 63, 67)
        self.assertEqual(self.notes(6), [60, 63, 67, 70, 72, 79])

    def test_an_inversion_finds_its_root(self):
        self.arp(self.UP, extend=4)
        self.chord(64, 67, 72)  # C major, E lowest: the ninth is a D, not an F sharp
        self.assertEqual(self.notes(7), [64, 67, 72, 74, 79, 83, 84])

    def test_with_no_third_it_stops_at_the_fifth(self):
        self.arp(self.UP, extend=4)
        self.chord(60, 67)
        self.assertEqual(self.notes(4), [60, 67, 72, 79])

    def test_with_a_scale_the_added_notes_are_in_it(self):
        self.s.setting("scale", pro800.SCALE_NAMES.index("minor"))
        self.arp(self.UP, extend=4)
        self.chord(60, 67)  # no third, but the scale knows: B flat and D
        self.assertEqual(self.notes(6), [60, 67, 70, 72, 74, 79])

    def test_chaos_makes_a_walk_jump_within_its_notes(self):
        self.arp(self.WALK, chaos=99, octaves=2)
        self.chord(48, 52, 55)
        got = self.notes(40)
        self.assertTrue(set(got) <= {48, 52, 55, 60, 64, 67}, got)
        ladder = [48, 52, 55, 60, 64, 67]
        self.assertTrue(any(abs(ladder.index(a) - ladder.index(b)) > 1 for a, b in zip(got, got[1:])), got)

    def test_chaos_in_up_plays_other_notes_of_the_chord(self):
        self.arp(self.UP, chaos=99)
        self.chord(60, 64, 67, 70)
        got = self.notes(24)
        self.assertTrue(set(got) <= {60, 64, 67, 70}, got)
        self.assertNotEqual(got[:8], [60, 64, 67, 70, 60, 64, 67, 70])

    def test_chaos_makes_added_notes_come_and_go(self):
        self.arp(self.UP, chaos=50, extend=2)
        self.chord(60, 64, 67)
        got = self.notes(120)
        self.assertTrue(set(got) <= {60, 64, 67, 72, 79}, got)
        counts = {n: got.count(n) for n in (60, 72, 79)}
        self.assertGreater(counts[60], counts[72], got)

    def test_a_route_into_chaos_adds_to_it(self):
        self.arp(self.WALK, octaves=2)
        names, dests = pro800.SOURCE_NAMES, pro800.DEST_NAMES
        self.s.request(0x56, 0, names.index("fixed"), dests.index("arp-chaos"), (99 + 99) >> 7, (99 + 99) & 0x7F)
        self.chord(48, 52, 55)
        got = self.notes(40)
        ladder = [48, 52, 55, 60, 64, 67]
        self.assertTrue(any(abs(ladder.index(a) - ladder.index(b)) > 1 for a, b in zip(got, got[1:])), got)

    def test_a_route_can_take_chaos_away(self):
        self.arp(self.WALK, chaos=99)
        names, dests = pro800.SOURCE_NAMES, pro800.DEST_NAMES
        self.s.request(0x56, 0, names.index("fixed"), dests.index("arp-chaos"), 0, 0)  # -99
        self.s.note(1)  # a first note, so the layer's routes have a voice to read
        self.s.ms(20)
        self.s.note_off(1)
        self.s.ms(20)
        self.chord(48, 51, 55, 58)
        self.assertEqual(self.notes(14), [48, 51, 55, 51, 55, 58, 55, 58, 55, 58, 55, 51, 55, 51])

    def cutoff_for(self, wanted, count=24):
        """The cutoff CV while this note plays"""
        for _ in range(count * 30):
            self.s.ms(5)
            held = self.s.probe()["held"][0]
            if held and held[-1] == wanted:
                self.s.ms(3)
                return self.s.cvs()[CV_FIL1]
        self.fail(f"note {wanted} never came")

    def test_note_position_as_a_source_runs_low_to_high(self):
        self.s.setting("keytrack", 0)
        self.arp(self.UP)
        names, dests = pro800.SOURCE_NAMES, pro800.DEST_NAMES
        depth = 40 + 99
        self.s.request(0x56, 0, names.index("arp-note"), dests.index("cutoff"), depth >> 7, depth & 0x7F)
        self.s.param(0, "cutoff", 4000)
        self.chord(60, 64, 67)
        low, mid, high = self.cutoff_for(60), self.cutoff_for(64), self.cutoff_for(67)
        self.assertLess(low, mid)
        self.assertLess(mid, high)
        semitones = (high - low) / CV_PER_SEMITONE
        self.assertAlmostEqual(semitones, 96 * 40 / 99, delta=1.5)

    def test_note_position_is_nothing_for_a_played_note(self):
        self.s.setting("keytrack", 0)
        names, dests = pro800.SOURCE_NAMES, pro800.DEST_NAMES
        self.s.param(0, "cutoff", 4000)
        self.s.note(60)
        self.s.ms(5)
        without = self.s.cvs()[CV_FIL1]
        depth = 40 + 99
        self.s.request(0x56, 0, names.index("arp-note"), dests.index("cutoff"), depth >> 7, depth & 0x7F)
        self.s.ms(5)
        self.assertEqual(self.s.cvs()[CV_FIL1], without)

    def test_in_a_stack_the_pad_holds_the_chord_while_the_arp_plays(self):
        self.s.setting("layers", pro800.LAYER_MODES.index("stack"))
        self.s.setting("voice 1", 0)
        self.s.setting("voice 2", 1)
        self.s.seq_set(length=8, arp=self.WALK, flags=1 | 2 << 3)
        self.s.seq_steps(0, [(0, 0, 50)] * 8)
        self.chord(60, 64, 67)
        seen = []
        for _ in range(100):
            self.s.ms(5)
            self.assertGreaterEqual(len(self.s.gated()), 3, "the chord stays on layer 1")
            held = self.s.probe()["held"][1]
            if held and (not seen or seen[-1] != held[-1]):
                seen.append(held[-1])
        self.assertEqual(seen[:4], [60, 64, 67, 64], "and layer 2 walks it")

    def test_the_settings_stay_in_the_patch(self):
        self.arp(self.WALK, chaos=40, extend=4, octaves=3)
        self.store_patch(5)
        for name in ("arp-range", "arp-extend", "arp-chaos"):
            self.s.setting(name, 0)
        self.s.press("1")
        self.s.ms(3100)
        self.assertEqual([self.setting_value(n) for n in ("arp-range", "arp-extend", "arp-chaos")], [0, 0, 0], "an empty slot")
        self.s.press("5")
        self.s.ms(3100)
        self.assertEqual([self.setting_value(n) for n in ("arp-range", "arp-extend", "arp-chaos")], [2, 4, 40])

    def test_values_past_the_ends_are_refused(self):
        self.arp(self.WALK, chaos=40, extend=4, octaves=3)
        self.s.setting("arp-range", 3)
        self.s.setting("arp-extend", 5)
        self.s.setting("arp-chaos", 100)
        self.assertEqual([self.setting_value(n) for n in ("arp-range", "arp-extend", "arp-chaos")], [2, 4, 40])

    def test_an_arp_kind_past_the_last_is_off(self):
        self.s.seq_set(length=4, arp=7)
        self.assertEqual(self.s.seq_header()["arp"], 0)

    def setting_value(self, name):
        return self.s.settings()[name]


class SequencerPlays(PanelTest):
    def setUp(self):
        super().setUp()
        self.s.seq_set(length=2)
        self.s.seq_steps(0, [(0,), (12,)])

    def pitches_while_held(self, ms):
        seen = set()
        for _ in range(ms // 10):
            self.s.ms(10)
            cvs = self.s.cvs()
            for v in self.s.gated():
                seen.add(cvs[v])
        return seen

    def test_a_key_runs_the_steps_and_letting_go_stops_them(self):
        self.s.note(60)
        self.assertGreaterEqual(len(self.pitches_while_held(600)), 2, "two different pitches")
        self.s.note_off(60)
        self.s.ms(20)
        self.assertEqual(self.s.gated(), [])

    def test_once_plays_through_and_stops(self):
        self.s.seq_set(length=2, flags=1 | 4)
        self.s.note(60)
        self.s.ms(700)
        self.assertEqual(self.s.gated(), [], "still held, the steps are over")

    def test_the_pedal_keeps_it_running(self):
        self.s.cc(64, 127)
        self.s.note(60)
        self.s.note_off(60)
        self.assertGreaterEqual(len(self.pitches_while_held(600)), 2)
        self.s.cc(64, 0)
        self.s.ms(20)
        self.assertEqual(self.s.gated(), [])

    def test_panic_stops_it(self):
        self.s.note(60)
        self.s.hold("SHIFT")
        self.s.press("0")
        self.s.let_go("SHIFT")
        self.s.ms(300)
        self.assertEqual(self.s.gated(), [])

    def test_a_sequence_is_saved_with_the_patch(self):
        self.store_patch(5)
        self.s.press("1")
        self.s.ms(3100)
        self.assertEqual(self.s.seq_header()["flags"] & 1, 0, "the empty slot's init sound has none")
        self.s.press("5")
        self.s.ms(3100)
        self.assertEqual(self.s.seq_header()["length"], 2)
        self.assertEqual([s["offset"] for s in self.s.seq_read()], [0, 12])

    def test_recording_writes_the_steps(self):
        self.s.hold("REC")
        self.s.press("SEQ1")
        self.s.let_go("REC")
        for note, velocity in ((60, 100), (64, 80), (67, 60)):
            self.s.note(note, velocity)
            self.s.note_off(note)
        self.s.press("SEQ2")
        self.s.press("REC")
        header = self.s.seq_header()
        self.assertEqual((header["length"], header["flags"] & 1), (4, 1))
        steps = self.s.seq_read()
        self.assertEqual([s["offset"] for s in steps[:3]], [0, 4, 7])
        self.assertEqual([s["velocity"] for s in steps[:3]], [100, 80, 60])
        self.assertEqual([s["gate"] for s in steps[:3]], [50, 50, 50])
        self.assertEqual(steps[3]["on"], 0, "the rest")

    def test_full_when_the_steps_run_out(self):
        self.s.hold("REC")
        self.s.press("SEQ1")
        self.s.let_go("REC")
        for i in range(60):
            self.s.note(60)
            self.s.note_off(60)
        self.assertShows("St60")
        self.s.note(60)
        self.s.note_off(60)
        self.assertShows("FULL")
        self.assertFalse(self.s.led("REC"))
        self.assertEqual(self.s.seq_header()["length"], 60)


class PatchStores(PanelTest):
    """What a patch stores, per the list under Patches: set it all, save, wreck it, load it back."""

    def make(self):
        self.s.no_routes()
        self.s.param(0, "cutoff", 0x1000)
        self.s.param(1, "resonance", 0x3000)
        self.s.param(1, "env3-att", 0x2000)
        self.s.param(0, "lfo2-rate", 0x1000)
        self.s.set_button("B_SAW", 1)
        self.s.set_button("SYNC", 1)
        self.s.setting("keytrack", 1)
        self.s.setting("layers", 1)
        self.s.setting("split", 72)
        self.s.setting("lfo-sync", 1)
        self.s.setting("lfo-trigger", 2)
        self.s.setting("lfo-cycles", 5)
        self.s.setting("shape 1 1", 4)
        self.s.setting("lfo2-dests 1", 1)
        self.s.request(0x56, 0, 4, 3, 0, 99 + 30)
        self.s.request(0x56, 1, 1, 13, 0, 99 - 20)
        self.s.seq_set(length=3)
        self.s.seq_steps(0, [(0,), (5,), (7,)])

    def check(self):
        p0, p1, st = self.s.params(0), self.s.params(1), self.s.settings()
        self.assertEqual((p0["cutoff"], p1["resonance"], p1["env3-att"], p0["lfo2-rate"]), (25, 74, 50, 25))
        buttons = self.s.buttons()
        self.assertEqual((buttons[SWITCH["B_SAW"]], buttons[SWITCH["SYNC"]]), (1, 1))
        self.assertEqual((st["keytrack"], st["layers"], st["split"]), (1, 1, 72))
        self.assertEqual((st["lfo-sync"], st["lfo-trigger"], st["lfo-cycles"], st["shape 1 1"], st["lfo2-dests 1"]), (1, 2, 5, 4, 1))
        self.assertEqual(self.s.routes(0), [(4, 3, 30)])
        self.assertEqual(self.s.routes(1), [(1, 13, -20)])
        self.assertEqual(self.s.seq_header()["length"], 3)
        self.assertEqual([s["offset"] for s in self.s.seq_read()], [0, 5, 7])

    def test_everything_comes_back(self):
        self.make()
        self.check()
        self.store_patch(9)
        self.s.long_press("PERF")
        self.s.ms(1600)
        self.assertEqual(self.s.params(0)["cutoff"], 99, "the init sound replaced it")
        self.assertEqual(self.s.routes(0), INIT_ROUTES)
        self.s.press("9")
        self.s.ms(3100)
        self.check()
        self.power_cycle()
        self.s.ms(1600)
        self.check()

    def test_m_vol_is_never_stored(self):
        self.s.pot("MASTER_VOL", 0.75)
        self.store_patch(9)
        self.s.pot("MASTER_VOL", 0.25)
        self.s.press("1")
        self.s.ms(3100)
        self.s.press("9")
        self.s.ms(3100)
        self.assertEqual(self.s.params()["master-vol"], 25)

    def test_a_loaded_patchs_buttons_hold_until_pressed(self):
        self.s.set_button("B_SAW", 1)
        self.store_patch(4)
        self.s.press("1")
        self.s.ms(3100)
        self.assertEqual(self.s.buttons()[SWITCH["B_SAW"]], 0)
        self.s.press("4")
        self.s.ms(3100)
        self.assertEqual(self.s.buttons()[SWITCH["B_SAW"]], 1, "stored on, the button itself off")
        self.s.switch("B_SAW")
        self.s.switch("B_SAW")
        self.s.ms(10)
        self.assertEqual(self.s.buttons()[SWITCH["B_SAW"]], 0, "pressed: the button's own state")

    def test_a_loaded_patchs_keyboard_tracking_holds_until_the_switch_moves(self):
        self.s.setting("keytrack", 1)
        self.store_patch(4)
        self.s.press("1")
        self.s.ms(3100)
        self.assertEqual(self.s.settings()["keytrack"], 2, "the init sound's")
        self.s.press("4")
        self.s.ms(3100)
        self.assertEqual(self.s.settings()["keytrack"], 1)
        self.s.switch("KEYTRACK_FULL", 0)
        self.assertShows("FULL")
        self.assertEqual(self.s.settings()["keytrack"], 2)


class Names(PanelTest):
    def test_rename_and_the_name_follows_the_sound_into_an_empty_slot(self):
        self.store_patch(1)
        self.s.rename(0, "Bass")
        self.assertEqual(self.s.patch_info(0), (1, "Bass"))
        self.s.press("1")
        self.s.ms(3100)
        self.store_patch(2)
        self.assertEqual(self.s.patch_info(1), (1, "Bass"))

    def test_saving_over_a_named_slot_keeps_that_slots_name(self):
        self.store_patch(1)
        self.s.rename(0, "Bass")
        self.store_patch(3)
        self.s.rename(2, "Lead")
        self.s.press("1")
        self.s.ms(3100)
        self.store_patch(3)
        self.assertEqual(self.s.patch_info(2), (1, "Lead"))

    def test_the_init_sound_is_named_init(self):
        self.s.long_press("PERF")
        self.store_patch(2)
        self.assertEqual(self.s.patch_info(1), (1, "Init"))

    def test_empty_and_cleared_slots(self):
        self.assertEqual(self.s.patch_info(5)[0], 0)
        self.store_patch(6)
        self.assertEqual(self.s.patch_info(5)[0], 1)
        self.s.request(0x4F, 0, 5)
        self.assertEqual(self.s.patch_info(5)[0], 0)
        self.assertShows("P  6", "the slot stays shown")


class StockMemory(PanelTest):
    MEMORY = "stock"

    def test_boots_on_the_panel(self):
        self.assertShows("PAnL")
        self.assertFalse(self.s.led("PRESET"))
        self.assertEqual(self.s.settings()["preset-mode"], 0)

    def test_preset_says_none_and_stays_on_the_panel(self):
        self.s.press("PRESET")
        self.assertShows("nonE")
        self.assertFalse(self.s.led("PRESET"))
        self.s.ms(1100)
        self.assertShows("PAnL")
        self.s.pot("CUTOFF", 0.25)
        self.assertEqual(self.s.params()["cutoff"], 25, "the knobs are live")

    def test_a_save_makes_the_memory_ours_and_the_next_boot_loads_it(self):
        self.s.pot("RESONANCE", 0.25)
        self.s.ms(1600)
        self.s.press("REC", "3", "REC")
        self.assertShows("donE")
        self.assertTrue(self.s.slot_is_ours(3))
        self.assertTrue(self.s.led("PRESET"))
        self.power_cycle()
        self.s.ms(1600)
        self.assertShows("P  3")
        self.assertEqual(self.s.params()["resonance"], 25)
        self.s.press("1")
        self.s.ms(3100)
        self.assertSaw(["nonE"])
        self.assertEqual(self.s.settings()["patch"], 2, "the other slots stay as they are")


class PickupAfterSwitching(PanelTest):
    """A knob waits (dot) after switching layer, LFO or envelope, as after a load."""

    def setUp(self):
        super().setUp()
        self.manual()

    def test_layer(self):
        self.s.press("SEQ2")
        self.s.ms(1100)
        self.s.param(1, "cutoff", 0x1000)
        self.s.pot("CUTOFF", 0.7)
        self.assertRegex(self.s.shows(), r"\.$")
        self.assertEqual(self.s.params(1)["cutoff"], 25)

    def test_lfo(self):
        self.s.param(0, "lfo2-rate", 0x1000)
        self.s.press("ARP_UP")
        self.s.ms(1100)
        self.s.pot("LFO_FREQ", 0.7)
        self.assertRegex(self.s.shows(), r"\.$")
        self.assertEqual(self.s.params()["lfo2-rate"], 25)

    def test_envelope_under_shift(self):
        self.s.hold("SHIFT")
        self.s.pot("FIL_ATT", 0.3)
        self.s.pot("FIL_ATT", 0.4)
        self.assertRegex(self.s.shows(), r"\.$")
        self.assertEqual(self.s.params()["env3-att"], 0)
        self.s.let_go("SHIFT")


class Scale(PanelTest):
    def test_notes_off_the_scale_snap(self):
        self.s.setting("scale", 1)
        self.s.setting("root", 0)
        pitches = {}
        for note in (60, 61, 62):
            self.s.note(note)
            self.s.ms(100)
            pitches[note] = self.s.pitch_of_gated_voice()
            self.s.note_off(note)
            self.s.ms(10)
        self.assertNotEqual(pitches[60], pitches[62])
        self.assertIn(pitches[61], (pitches[60], pitches[62]), "C# snaps to C or D")


class SyncIn(PanelTest):
    def test_pulses_on_the_jack_set_the_beat(self):
        self.s.hold("SHIFT")
        self.s.press("CLOCK", "CLOCK")
        self.assertShows("SYnC")
        self.s.let_go("SHIFT")
        lit = set()
        for _ in range(12):
            self.s.cmd("sw 22 1")
            self.s.ms(5)
            self.s.cmd("sw 22 0")
            for _ in range(24):
                self.s.ms(10)
                lit.add(self.s.led("SYNC_CLOCK"))
        self.assertEqual(lit, {True, False})


CV_PER_SEMITONE = 256  # tools/sim/hw.c's tuning_note_cv()
NODE = shutil.which("node")
PAGE_RANGES = None


def source(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def js_block(text, start):
    """A statement or function of the page, from its first line to the line that ends it."""
    i = text.index("\n" + start) + 1
    out = []
    for line in text[i:].split("\n"):
        out.append(line)
        s = re.sub(r"\s*//.*$", "", line.rstrip())
        if re.match(r"(async )?function", start):
            if s == "}" or (len(out) == 1 and s.endswith("}")):
                break
        elif s.endswith(";"):
            break
    return "\n".join(out)


RANGE_BLOCKS = ["const P = {", "const ENVS = [", "const ENV_KNOB = {};", "for (const [, bit, ...knobs] of ENVS)", "function cycling(",
                "const envIndex", "function cycleSync(", "const envOf", "function cycleShares(", "function cycleShare(", "const knobTime",
                "function envTime(", "const envFlag", "const QUARTERS", "const envTempo", "const noteStep", "const noteName", "const noteSeconds",
                "const DIVISIONS", "function seconds(", "function lfoHz(", "function format(", "const DEST_KNOBS", "const DEST_SCALE",
                "const BIPOLAR_SOURCES", "const FIXED_SOURCE", "const isMacroSource", "const VIA_SOURCE", "const viaName", "const SQUARED_DESTS", "const timeDest", "const rateDest", "const factor",
                "let chords = null;", "function chordSize(", "function swingUnit(", "function routeSwing(", "function swingWhere(", "const SOURCES", "const DESTS", "function modRange(",
                "function hertz(", "function noteValue(", "function paintRange("]


def page_ranges():
    """What tools/web/index.html works out for routes, rings and the 'where' strings, from its own code in Node."""
    global PAGE_RANGES
    if PAGE_RANGES is not None:
        return PAGE_RANGES
    page = source("tools", "web", "index.html")
    blocks = RANGE_BLOCKS
    script = "let settings = null, params = null, paramsLayer = 0, layerRoutes = null, otherRoutes = [];\nconst feature = () => false;\n"
    script += "const h = (tag, attrs, ...kids) => ({ tag, text: kids.join('') });\nconst cell = { replaceChildren(...kids) { this.kids = kids; } };\n"
    script += "\n".join(js_block(page, b) for b in blocks)
    script += """
const out = { unit: {}, swing: {}, where: {}, ring: {}, scale: DEST_SCALE, dests: DESTS.length };
for (let d = 0; d < DESTS.length; d++) {
  out.unit[d] = swingUnit(d)[0];
  for (const s of [25, 4, 1]) for (const dep of [99, 50, 25, 10, -50, -99]) out.swing[`${s},${d},${dep}`] = routeSwing(s, d, dep);
}
params = new Array(36).fill(0.5);
for (const d of [3, 18, 20, 25, 26, 29, 41]) for (const dep of [50, 25, -50]) out.where[`${d},${dep}`] = swingWhere(d, routeSwing(25, d, dep));
for (const [knob, routes] of [[P.CUTOFF, [[25, 3, 50]]], [P.FREQ_A, [[1, 0, 50]]], [P.AMP_ATT, [[25, 25, 50]]], [P.CUTOFF, [[25, 3, 50], [1, 3, 25]]],
    [P.AMP_SUS, [[25, 27, 99]]], [P.AMP_SUS, [[25, 27, 50]]], [P.FIL_SUS, [[25, 31, 99]]], [P.ENV3 + 2, [[25, 35, 99]]], [P.ENV4 + 2, [[25, 39, 50]]]]) {
  layerRoutes = routes.map(([source, dest, depth]) => ({ source, dest, depth }));
  const r = modRange(knob);
  out.ring[routes.map(x => x.join(",")).join(";")] = { lo: r.lo, hi: r.hi, parts: r.parts.map(p => [p.lo, p.hi]) };
}
// LFO 1's knob at 1/4 (synced) or 0.05 × 2^(60/14) Hz; the Range cell's main text and what goes under it
params[P.LFO_RATE] = 6 / 14;
out.cell = {};
for (const [s, d, dep] of [[25, 18, 50], [4, 18, 99], [25, 3, 50], [25, 25, 50]]) { paintRange(cell, s, d, dep); out.cell[`${s},${d},${dep}`] = [cell.kids[0], cell.kids[1].text]; }
settings = { lfo1Sync: 1 };
out.whereSynced = {};
for (const [s, dep] of [[25, 50], [4, 99], [1, 50]]) out.whereSynced[`${s},${dep}`] = swingWhere(18, routeSwing(s, 18, dep));
paintRange(cell, 4, 18, 99);
out.cell["synced 4,18,99"] = [cell.kids[0], cell.kids[1].text];
settings = null;
out.note = { table: QUARTERS.map(noteValue), long: noteValue(1 / 32), bars: noteValue(6), off: noteValue(0.29) };
console.log(JSON.stringify(out));
"""
    path = os.path.join(tempfile.mkdtemp(prefix="pro800page"), "ranges.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    PAGE_RANGES = json.loads(subprocess.check_output([NODE, path], encoding="utf-8"))
    return PAGE_RANGES


def number_in(text):
    """The last number in a range text, with its unit: '±6.1 st' → (6.1, 'st'), '0 to −100 %' → (-100, '%'), '×4.1' → (4.1, '×')."""
    m = re.findall(r"([−\-+]?)([×÷]?)(\d+(?:\.\d+)?)\s*([a-z%]*)", text)
    sign, mul, value, unit = m[-1]
    value = float(value) * (-1 if sign in "−-" and sign else 1)
    if mul == "÷":
        value = 1 / value
    return value, mul or unit


def firmware_constants():
    synth = source("src", "synth.c")
    c = {name: float(v) for name, v in re.findall(r"#define (\w+)\s+([\d.]+)f", synth)}
    c["TIME_OCTAVES"] = float(re.search(r"fast_exp2\(-?(\d+\.\d+)f \* exp_mod\(m\[", synth).group(1))
    c["DETUNE_CENTS"] = float(re.search(r"DEST_DETUNE\] \* (\d+\.\d+)f", synth).group(1))
    c["GLIDE_OCTAVES"] = float(re.search(r"0\.002f \* fast_exp2\(knob \* (\d+\.\d+)f\)", synth).group(1))
    c["LFO_KNOB_OCTAVES"] = float(re.search(r"0\.05f \* fast_exp2\(knob \* (\d+\.\d+)f\)", source("src", "lfo.c")).group(1))
    return c


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebRanges(PanelTest):
    """The editor's Range column, rings and 'where' strings (tools/web/index.html) against what the firmware does: a route
    from FIX (always 1) is measured on the CVs in the destination's own unit and compared with the page's number for the
    same route. Not measured: Morph and the macros."""

    def setUp(self):
        super().setUp()
        self.page = page_ranges()
        s = self.s
        s.no_routes()  # each test measures one route
        s.setting("keytrack", 0)
        s.setting("env-curve 1", 1 | 4)  # amp and filter linear: stage times measure exactly
        s.setting("shape 1 1", 7)         # steps, one step on: an LFO that is always 1
        s.setting("shape 1 2", 7)
        s.request(0x4E, 0, 0, 1, 0, 0, 1)
        s.request(0x4E, 0, 1, 1, 0, 0, 1)
        s.setting("lfo2-dests 1", 0)
        for name in ("LFO_FREQ", "LFO_FILTER", "LFO_PW"):
            s.set_button(name, 0)
        for name, value in (("cutoff", 0x2000), ("fil-env-amt", 0x2000), ("pmod-fil-env", 0), ("fil-sus", 16383), ("fil-att", 0), ("fil-dec", 0),
                            ("amp-sus", 16383), ("amp-att", 0), ("amp-dec", 0), ("amp-rel", 0), ("glide", 0), ("lfo-amount", 0), ("lfo2-amount", 0),
                            ("lfo-freq", 0x2000), ("lfo2-rate", 0x2000), ("a-level", 0), ("b-level", 0), ("noise", 0), ("resonance", 0), ("pmod-osc-b", 0)):
            s.param(0, name, value)
        s.ms(50)

    # the page's numbers
    def swing(self, dest, depth, source=25):
        return self.page["swing"][f"{source},{dest},{depth}"]

    TOLERANCE = {"st": 0.05, "ct": 0.5, "%": 0.6, "": 0.6, "oct": 0.15, "×": 0.25}  # the CVs are 16-bit, the LFO phase 1 %

    def assertPage(self, dest, depth, measured, unit, source=25):
        """The page's hi (or lo for a drop) and its text agree with what was measured, in the unit the page uses. The text
        rounds (24.2 ct is "+24 ct", 50.5 % is "+51 %"), so it gets the unit's tolerance on top of its rounding."""
        sw = self.swing(dest, depth, source)
        self.assertIsNotNone(sw, f"the page has no range for {source} → {dest} at {depth}")
        claimed = sw["hi"] if measured >= 0 else sw["lo"]
        if unit == "×":  # a time: the page holds octaves, the measurement is a factor
            claimed = 2 ** claimed
        tolerance = self.TOLERANCE[unit]
        self.assertAlmostEqual(claimed, measured, delta=tolerance, msg=f"dest {dest} at {depth}: page {sw['text']!r}, measured {measured:.3f} {unit}")
        value, u = number_in(sw["text"])
        if unit == "st" and u == "ct":  # under a semitone the text says cents
            value, u, tolerance = value / 100, "st", 0.006
        self.assertEqual(u, unit, sw["text"])
        rounding = 0.05 if "." in sw["text"].split(" ")[0] else 0.5
        self.assertAlmostEqual(value, measured, delta=tolerance + rounding, msg=f"the text {sw['text']!r} vs measured {measured:.3f} {unit}")

    # the firmware, measured
    def route(self, dest, depth, source=25):
        self.s.request(0x56, 0, source, dest, (depth + 99) >> 7, (depth + 99) & 0x7F)

    def delta(self, dest, depth, read, source=25, settle=150):
        before = read()
        self.route(dest, depth, source)
        self.s.ms(settle)
        after = read()
        self.route(dest, 0, source)
        self.s.ms(settle)
        return after - before

    def voice(self, note=60, velocity=100):
        self.s.note(note, velocity)
        self.s.ms(100)
        return self.s.gated()[0]

    def osc_a(self, v): return lambda: self.s.cvs()[v] / CV_PER_SEMITONE
    def osc_b(self, v): return lambda: self.s.cvs()[8 + v] / CV_PER_SEMITONE
    def filt(self, v): return lambda: self.s.cvs()[16 + v] / CV_PER_SEMITONE
    def gain(self, v):
        def read():
            cv = self.s.cvs()[24 + v]
            return 0.0 if cv == 0 else (cv / 65535 - 0.07) / 0.93  # synth.c: VCA_THRESHOLD
        return read
    def knob_units(self, cv): return lambda: self.s.cvs()[cv] / 65535 * 99

    def test_the_pages_constants_are_the_firmwares(self):
        c, unit, scale = firmware_constants(), self.page["unit"], self.page["scale"]
        for dest in (0, 1, 2):
            self.assertEqual(unit[str(dest)], c["MOD_PITCH_SEMITONES"])
            self.assertAlmostEqual(scale[str(dest)], c["MOD_PITCH_SEMITONES"] / (2 * c["OSC_RANGE_SEMITONES"]), 6)
        self.assertEqual(unit["3"], c["MOD_CUTOFF_SEMITONES"])
        self.assertAlmostEqual(scale["3"], c["MOD_CUTOFF_SEMITONES"] / c["CUTOFF_SPAN"], 6)
        self.assertEqual(unit["4"], c["FILTER_ENV_SEMITONES"])
        self.assertAlmostEqual(scale["4"], c["FILTER_ENV_SEMITONES"] / (2 * c["FILTER_ENV_SEMITONES"]), 6)
        self.assertEqual(unit["5"], c["PMOD_ENV_SEMITONES"])
        for dest in (7, 21):
            self.assertEqual(unit[str(dest)], c["LFO_PITCH_SEMITONES"])
        for dest in (8, 22):
            self.assertEqual(unit[str(dest)], c["LFO_FILTER_SEMITONES"])
        for dest in (14, 15):
            self.assertEqual(unit[str(dest)], c["MOD_PW"] * 100)
            self.assertAlmostEqual(scale[str(dest)], c["MOD_PW"], 6)
        for dest in (18, 20):
            self.assertEqual(unit[str(dest)], c["MOD_LFO_OCTAVES"])
            self.assertAlmostEqual(scale[str(dest)], c["MOD_LFO_OCTAVES"] / c["LFO_KNOB_OCTAVES"], 6)
        for dest in range(25, 41):
            self.assertEqual(unit[str(dest)], c["TIME_OCTAVES"] if (dest - 25) % 4 != 2 else 99)
            if (dest - 25) % 4 == 2:
                self.assertEqual(scale[str(dest)], 1, "a sustain route adds to the level: depth 99 is the whole knob")
        self.assertEqual(unit["41"], c["TIME_OCTAVES"])
        self.assertAlmostEqual(scale["41"], c["TIME_OCTAVES"] / c["GLIDE_OCTAVES"], 6)
        self.assertEqual(unit["42"], c["DETUNE_CENTS"])
        self.assertEqual(self.page["dests"], len(pro800.DEST_NAMES))

    def test_pitch_a_is_squared(self):
        v = self.voice()
        for depth in (99, 50, 25, 10, -50):
            self.assertPage(0, depth, self.delta(0, depth, self.osc_a(v)), "st")

    def test_pitch_b_and_both(self):
        v = self.voice()
        self.assertPage(1, 50, self.delta(1, 50, self.osc_b(v)), "st")
        self.assertAlmostEqual(self.delta(1, 50, self.osc_a(v)), 0, 2, "Pitch B leaves A alone")
        self.assertPage(2, 50, self.delta(2, 50, self.osc_a(v)), "st")
        self.assertPage(2, 50, self.delta(2, 50, self.osc_b(v)), "st")

    def test_an_lfo_into_pitch_swings_both_ways(self):
        v = self.voice()
        st = self.delta(0, 50, self.osc_a(v), source=1)  # the step LFO: always 1
        sw = self.swing(0, 50, 1)
        self.assertAlmostEqual(sw["hi"], st, 1)
        self.assertAlmostEqual(sw["lo"], -st, 1)
        self.assertTrue(sw["text"].startswith("±"), sw["text"])

    def test_cutoff(self):
        v = self.voice()
        for depth in (99, 50, -99):
            self.assertPage(3, depth, self.delta(3, depth, self.filt(v)), "st")

    def test_filter_envelope_amount(self):
        v = self.voice()  # the filter envelope rests at its sustain, 1
        for depth in (99, 50):
            self.assertPage(4, depth, self.delta(4, depth, self.filt(v)), "st")

    def test_poly_mod_envelope_amount(self):
        v = self.voice()
        for depth in (99, 50):
            self.assertPage(5, depth, self.delta(5, depth, self.osc_a(v)), "st")

    def test_lfo_amount_in_percent_of_its_depth(self):
        self.s.set_button("LFO_FREQ", 1)
        v = self.voice()
        st = self.delta(6, 50, self.osc_a(v))
        self.assertPage(6, 50, st / 12 * 100, "%")

    def test_lfo_to_pitch_and_filter(self):
        v = self.voice()
        for depth in (99, 50):
            self.assertPage(7, depth, self.delta(7, depth, self.osc_a(v)), "st")
        self.assertPage(8, 99, self.delta(8, 99, self.filt(v)), "st")

    def test_lfo_2_to_pitch_filter_and_its_amount(self):
        v = self.voice()
        self.assertPage(21, 99, self.delta(21, 99, self.osc_a(v)), "st")
        self.assertPage(22, 50, self.delta(22, 50, self.filt(v)), "st")
        self.s.setting("lfo2-dests 1", 1)
        self.assertPage(19, 50, self.delta(19, 50, self.osc_a(v)) / 12 * 100, "%")

    def test_the_vca_counts_down(self):
        v = self.voice()
        self.assertPage(9, 50, self.delta(9, 50, self.gain(v)) * 100, "%")
        self.assertPage(9, 99, self.delta(9, 99, self.gain(v)) * 100, "%")
        self.assertEqual(self.swing(9, 50)["text"], self.swing(9, -50)["text"], "FIX turns it down whichever the sign")

    def test_velocity_into_the_vca(self):
        sw = self.swing(9, 99, 4)
        self.assertEqual((sw["lo"], sw["hi"]), (-100, 0), sw["text"])
        for velocity, expected in ((127, 0), (64, -(1 - 64 / 127) * 100)):
            v = self.voice(velocity=velocity)
            self.assertAlmostEqual(self.delta(9, 99, self.gain(v), source=4) * 100, expected, 0)
            self.s.note_off(60)
            self.s.ms(50)

    def test_levels_noise_resonance_and_poly_mod_b(self):
        self.voice()
        for dest, cv in ((10, 33), (11, 34), (12, 36), (13, 40), (16, 32)):
            self.assertPage(dest, 50, self.delta(dest, 50, self.knob_units(cv)), "")
        self.s.param(0, "a-level", 16383)
        self.s.ms(50)
        self.assertPage(10, -50, self.delta(10, -50, self.knob_units(33)), "")

    def test_pulse_width(self):
        self.s.set_button("A_SQUARE", 1)
        self.s.set_button("B_SQUARE", 1)
        self.voice()
        for dest, name, cv in ((14, "a-pw", 38), (15, "b-pw", 39)):
            self.s.param(0, name, 0)
            self.s.ms(50)
            bottom = self.s.cvs()[cv]
            self.s.param(0, name, 16383)
            self.s.ms(50)
            top = self.s.cvs()[cv]
            self.s.param(0, name, 0)
            self.s.ms(50)
            self.assertPage(dest, 99, self.delta(dest, 99, lambda: self.s.cvs()[cv]) / (top - bottom) * 100, "%")
            self.s.param(0, name, 16383)
            self.s.ms(50)
            self.assertPage(dest, -99, self.delta(dest, -99, lambda: self.s.cvs()[cv]) / (top - bottom) * 100, "%")

    def test_lfo_to_pw_in_percent_of_full(self):
        self.s.set_button("A_SQUARE", 1)
        self.s.param(0, "a-pw", 0x1000)
        self.voice()
        for dest in (17, 23):
            full = self.delta(dest, 99, lambda: self.s.cvs()[38])
            half = self.delta(dest, 50, lambda: self.s.cvs()[38])
            self.assertGreater(full, 0)
            self.assertPage(dest, 50, half / full * 100, "%")

    def lfo_rate(self, lfo, ms):
        """The LFO's rate from its shared phase (synth_probe(), hundredths) over about ms, under a cycle. The window is
        the simulator's own clock: a probe takes passes too (request() runs 5), which a fixed ms would leave out."""
        at = 53 + 10 * lfo
        p1, t1 = self.s.reply(0x55)[at], self.s.now()
        self.s.ms(ms)
        p2, t2 = self.s.reply(0x55)[at], self.s.now()
        return (p2 - p1) % 100 / 100 / (t2 - t1) * 1000  # Hz

    def lfo_rate_long(self, lfo, step_ms=40, steps=10):
        """As lfo_rate(), summed over several windows: the phase only has to fit a window without wrapping, and the
        engine's 2.3 ms update steps average out (a 60 ms window is good to about 6 %, this to 1 %)."""
        at = 53 + 10 * lfo
        total, p1, t1 = 0, self.s.reply(0x55)[at], self.s.now()
        for _ in range(steps):
            self.s.ms(step_ms)
            p2 = self.s.reply(0x55)[at]
            total += (p2 - p1) % 100
            p1 = p2
        return total / 100 / (self.s.now() - t1) * 1000  # Hz

    def test_lfo_rates_in_octaves(self):
        self.voice()
        for dest, lfo in ((18, 0), (20, 2)):
            base = self.lfo_rate(lfo, 200)
            self.assertAlmostEqual(base, 1.6, 0, "the knob at half: 0.05 Hz × 2^5")
            for depth, ms in ((25, 100), (50, 60)):
                self.route(dest, depth)
                self.s.ms(20)
                ratio = self.lfo_rate(lfo, ms) / base
                self.route(dest, 0)
                self.s.ms(20)
                self.assertPage(dest, depth, math.log2(ratio), "oct")
                sw = self.swing(dest, depth)
                self.assertAlmostEqual(2 ** sw["hi"], ratio, delta=0.08 * ratio)

    def attack_ms(self, reader, note=60):
        """Passes from the note-on until the linear attack stops rising, on the voice the note takes."""
        self.s.midi(0x90, note, 127)
        self.s.ms(1)
        read = reader(self.s.gated()[0])
        last, t = read(), 1
        for _ in range(1500):
            self.s.ms(1)
            t += 1
            now = read()
            if now <= last and t > 3:
                break
            last = now
        self.s.note_off(note)
        self.s.ms(100)
        return t - 1

    def decay_ms(self, reader):
        """Passes from the note-on until the level is down to -60 dB, the manual's definition of a decay's time."""
        self.s.midi(0x90, 60, 127)
        self.s.ms(1)
        read = reader(self.s.gated()[0])
        t = 1
        for _ in range(1500):
            self.s.ms(1)
            t += 1
            if read() < 0.001:
                break
        self.s.note_off(60)
        self.s.ms(50)
        return t

    def test_amp_attack_and_decay_times_multiply(self):
        self.s.param(0, "amp-att", 0x2000)
        base = self.attack_ms(self.gain)
        # knob_time(0x2000 / 16383) is 153.9 ms; counting the passes until the attack stops rising reads 149-150 (0.9.5's
        # build and this one alike), at the edge of the old ±4
        self.assertAlmostEqual(base, 154, delta=5)
        self.route(25, 25)
        with_route = self.attack_ms(self.gain)
        self.route(25, 0)
        self.assertPage(25, 25, with_route / base, "×")
        self.assertAlmostEqual(2 ** self.swing(25, 25)["hi"], with_route / base, delta=0.2)
        self.s.param(0, "amp-att", 0)
        self.s.param(0, "amp-sus", 0)
        self.s.param(0, "amp-dec", 0x1000)
        base = self.decay_ms(self.gain)
        self.assertAlmostEqual(base, 37, delta=5, msg="37 ms on the display; a pass or two of offset")
        self.route(26, 50)
        with_route = self.decay_ms(self.gain)
        self.route(26, 0)
        self.assertAlmostEqual(2 ** self.swing(26, 50)["hi"], with_route / base, delta=0.4)
        self.assertEqual(number_in(self.swing(26, 50)["text"])[1], "×")

    def test_a_shorter_time_reads_as_a_division(self):
        self.s.param(0, "amp-att", 0x2000)
        base = self.attack_ms(self.gain)
        self.route(25, -50)
        with_route = self.attack_ms(self.gain)
        self.route(25, 0)
        sw = self.swing(25, -50)
        self.assertTrue(sw["text"].startswith("÷"), sw["text"])
        self.assertAlmostEqual(2 ** sw["hi"], with_route / base, delta=0.05)

    def test_sustain_adds_a_level(self):
        self.s.param(0, "amp-sus", 0)
        v = self.voice()
        self.assertPage(27, 50, self.delta(27, 50, self.gain(v)) * 99, "")

    def test_a_sustain_arc_is_the_level_the_route_adds(self):
        ring = self.page["ring"]
        self.s.param(0, "amp-sus", 0)
        v = self.voice()
        for depth in (99, 50):
            self.assertAlmostEqual(ring[f"25,27,{depth}"]["hi"], self.delta(27, depth, self.gain(v)), delta=0.01, msg="in knob turns, as the level is")
        self.assertAlmostEqual(ring["25,31,99"]["hi"], 1, 6, "the filter's sustain: the whole knob")
        self.assertAlmostEqual(ring["25,35,99"]["hi"], 1, 6, "Env 3's")
        self.assertAlmostEqual(ring["25,39,50"]["hi"], 50 / 99, 6, "Env 4's")

    def test_filter_attack_time_multiplies(self):
        self.s.param(0, "fil-att", 0x2000)
        self.s.param(0, "fil-env-amt", 16383)
        base = self.attack_ms(self.filt)
        self.route(29, 25)
        with_route = self.attack_ms(self.filt)
        self.route(29, 0)
        self.assertAlmostEqual(2 ** self.swing(29, 25)["hi"], with_route / base, delta=0.2)

    def glide_ms(self):
        """Time for a legato slide to cover 63 % of its way: the time constant."""
        self.s.note(48)
        self.s.ms(500)
        v = self.s.gated()[0]
        start = self.s.cvs()[v]
        self.s.note(60)
        target = start + 12 * CV_PER_SEMITONE
        t = 0
        for _ in range(3000):
            self.s.ms(1)
            t += 1
            if self.s.cvs()[v] >= start + 0.632 * (target - start):
                break
        self.s.note_off(60)
        self.s.note_off(48)
        self.s.ms(50)
        return t

    def test_glide_time_multiplies(self):
        self.s.setting("voice 1", 2)
        self.s.param(0, "glide", 0x2000)
        base = self.glide_ms()
        self.assertAlmostEqual(base, 64, delta=6)
        self.route(41, 50)
        with_route = self.glide_ms()
        self.route(41, 0)
        self.assertAlmostEqual(2 ** self.swing(41, 50)["hi"], with_route / base, delta=0.4)
        where = self.page["where"]["41,50"]
        self.assertAlmostEqual(number_in(where)[0], with_route, delta=with_route * 0.1, msg=where)

    def test_unison_detune_reaches_100_cents_and_a_stack_spreads_by_it(self):
        self.s.switch("UNISON")
        self.s.setting("detune", 100)
        self.s.setting("unison-voices", 3)
        self.s.note(60)
        self.s.note(72)
        self.s.ms(100)
        cvs = self.s.cvs()
        for first in (0, 3):
            stack = cvs[first:first + 3]
            self.assertAlmostEqual((max(stack) - min(stack)) / CV_PER_SEMITONE * 100, 100, delta=1)
            self.assertAlmostEqual(stack[1] - stack[0], stack[2] - stack[1], delta=2, msg="evenly")

    def test_unison_detune_in_cents(self):
        self.s.setting("detune", 0)
        self.s.switch("UNISON")
        self.s.note(60)
        self.s.ms(100)
        self.assertEqual(len(self.s.gated()), 8)

        def spread():
            cvs = self.s.cvs()[:8]
            return (max(cvs) - min(cvs)) / CV_PER_SEMITONE * 100
        self.assertEqual(spread(), 0)
        for depth in (99, 50):
            self.assertPage(42, depth, self.delta(42, depth, spread), "ct")

    def test_a_cc_out_reaches_0_to_127(self):
        """CC A out: the page's reach is the CC's value, and below 0 there's nothing (the firmware clamps the sum)."""
        fixed, lfo, velocity = 25, 1, 4
        self.assertEqual(self.swing(48, 50)["text"], "+64")
        self.assertEqual(self.swing(48, -50)["text"], "none on its own")
        self.assertEqual(self.swing(48, -50, source=velocity)["text"], "none on its own")
        self.assertEqual((self.swing(48, 50, source=lfo)["lo"], self.swing(48, 50, source=lfo)["text"]), (0, "0 to +64"))
        out = channel_messages(self.s.request(0x56, 0, fixed, 48, (50 + 99) >> 7, (50 + 99) & 0x7F))
        self.s.ms(50)
        out += channel_messages(bytes.fromhex(self.s.cmd("out")))
        self.assertEqual([m for m in out if m[0] == 0xB0], [(0xB0, 12, 64)], "what the firmware sends for it")

    def test_volume_counts_down(self):
        self.voice()  # a single wire follows the layer's newest note: until one has played, the route does nothing
        self.s.pot("MASTER_VOL", 1.0)
        self.s.ms(300)
        self.assertEqual(self.s.cvs()[35], 65535)
        self.assertPage(43, 50, self.delta(43, 50, lambda: self.s.cvs()[35] / 65535) * 100, "%")

    def test_where_the_cutoff_goes_matches_the_display(self):
        """swingWhere's endpoint is the knob's own display value at the position the route reaches."""
        where = self.page["where"]["3,50"]
        hz_claimed = number_in(where.split("–")[-1])[0] * (1000 if "kHz" in where.split("–")[-1] else 1)
        self.manual()
        self.s.pot("CUTOFF", 0.5 + self.swing(3, 50)["hi"] / 120)
        shown = self.s.shows()
        hz_shown = float(shown) * (1000 if "." in shown and float(shown) < 100 else 1)
        self.assertAlmostEqual(hz_claimed, hz_shown, delta=hz_shown * 0.01, msg=f"page {where!r}, display {shown!r}")

    def test_where_the_lfo_rate_goes_matches_the_display(self):
        where = self.page["where"]["18,50"]
        hz_claimed = number_in(where.split("–")[-1])[0]
        self.manual()
        self.s.pot("LFO_FREQ", 0.5 + self.swing(18, 50)["hi"] / 10)
        self.assertAlmostEqual(hz_claimed, float(self.s.shows()), delta=hz_claimed * 0.01, msg=where)

    def test_where_the_attack_goes_is_the_measured_time(self):
        where = self.page["where"]["25,50"]
        ms_claimed, unit = number_in(where.split("–")[-1])
        self.s.param(0, "amp-att", 0x2000)
        self.route(25, 50)
        measured = self.attack_ms(self.gain)
        self.route(25, 0)
        self.assertEqual(unit, "ms", where)
        self.assertAlmostEqual(ms_claimed, measured, delta=measured * 0.05, msg=f"page {where!r}, measured {measured} ms")

    def test_the_rings_are_the_swing_in_knob_turns(self):
        ring = self.page["ring"]
        v = self.voice()
        self.assertAlmostEqual(ring["25,3,50"]["hi"], self.delta(3, 50, self.filt(v)) / 120, 3, "cutoff: 120 semitones of travel")
        self.assertEqual(ring["25,3,50"]["lo"], 0)
        st = self.delta(0, 50, self.osc_a(v), source=1)
        self.assertAlmostEqual(ring["1,0,50"]["hi"], st / 72, 3, "FREQ A: 72 semitones of travel")
        self.assertAlmostEqual(ring["1,0,50"]["lo"], st / 72, 3)
        self.assertAlmostEqual(ring["25,25,50"]["hi"], 4 * 50 / 99 / 12, 3, "attack: 12 octaves of travel")
        two = ring["25,3,50;1,3,25"]
        self.assertEqual(len(two["parts"]), 2, "a ring per source")
        self.assertAlmostEqual(two["hi"], two["parts"][0][1] + two["parts"][1][1], 6)

    def test_note_values_name_the_divisions(self):
        """noteValue() gives every division of lfo.c its own name back, and names what falls between them."""
        note = self.page["note"]
        self.assertEqual(note["table"], ["8 bars", "4 bars", "2 bars", "1 bar", "1/2", "1/4.", "1/4", "1/4T", "1/8.", "1/8", "1/8T",
                                         "1/16.", "1/16", "1/16T", "1/32"])
        self.assertEqual(note["long"], "1/128")
        self.assertEqual(note["bars"], "1.5 bars")
        self.assertTrue(note["off"].startswith("~"), note["off"])

    def test_a_synced_lfo_rate_route_reads_in_note_values(self):
        """LFO 1 synced at 1/4: FIX +50 (× 4.06) lands within 2 % of 1/16, an envelope at 99 sweeps 1/4 to 1/64, a source that
        swings both ways reaches a bar."""
        synced = self.page["whereSynced"]
        self.assertEqual(synced["25,50"], "1/16", "a fixed offset: one value")
        self.assertEqual(synced["4,99"], "1/4 – 1/64")
        self.assertEqual(synced["1,50"], "1 bar – 1/16")

    def test_the_range_cell_leads_with_the_rates(self):
        """An LFO rate's cell: the rates on top, the octaves under them; the other destinations as before."""
        cell = self.page["cell"]
        self.assertEqual(cell["25,18,50"], ["3.96 Hz", "+2 oct"], "0.05 × 2^(60/14) Hz × 4.06")
        self.assertEqual(cell["4,18,99"], ["0.98 Hz – 15.6 Hz", "0 to +4 oct"])
        self.assertEqual(cell["synced 4,18,99"], ["1/4 – 1/64", "0 to +4 oct"])
        self.assertEqual(cell["25,3,50"][0], "+48 st")
        self.assertIn("Hz", cell["25,3,50"][1])
        self.assertEqual(cell["25,25,50"][0], "×4.1")

    def test_a_synced_lfo_sweeps_as_the_note_values_say(self):
        """LFO 1 synced at 1/4 runs at 2 Hz without a clock (120 BPM); FIX +50 into its rate gives the page's 1/16."""
        self.voice()
        self.s.setting("lfo-sync", 1)
        self.s.param(0, "lfo-freq", round(16383 * 6 / 14))
        self.s.ms(100)
        base = self.lfo_rate(0, 200)
        self.assertAlmostEqual(base, 2.0, delta=0.1, msg="1/4 at 120 BPM")
        self.route(18, 50)
        self.s.ms(100)
        rate = self.lfo_rate_long(0)
        self.route(18, 0)
        self.s.setting("lfo-sync", 0)
        name = self.page["whereSynced"]["25,50"]
        quarters = 4 / int(name.lstrip("~").split("/")[1].rstrip(".T"))
        self.assertAlmostEqual(rate, 1 / (0.5 * quarters), delta=rate * 0.05, msg=f"page {name!r}, measured {rate:.2f} Hz")


class MidiFlood(PanelTest):
    """handle_midi() takes at most 64 events a call, four calls a pass: a flood can't hold up the CV refresh."""
    PER_PASS = 4 * 64

    def flood(self, count):
        self.s.cmd("midi " + bytes(b for i in range(count) for b in (0xB0, 1, i & 0x7F)).hex())

    def test_a_burst_over_the_bound_is_handled_within_a_few_passes(self):
        self.s.ms(1)
        self.flood(1000)
        self.s.note(60)  # the last in the burst, 2 ms for it
        usb = self.s.midi_stat()["usb"]
        self.assertGreater(usb, 0, "two passes can't have taken it all")
        self.assertGreaterEqual(usb, 1001 - 2 * self.PER_PASS)
        self.s.ms(3)
        self.assertEqual(self.s.midi_stat()["usb"], 0)
        self.assertEqual(len(self.s.gated()), 1, "the note behind the flood played")

    def test_the_din_socket_isnt_starved_by_a_usb_flood(self):
        self.flood(1000)
        self.s.din(0x90, 72, 100)
        self.s.ms(1)
        stat = self.s.midi_stat()
        self.assertGreater(stat["usb"], 0)
        self.assertEqual(stat["din"], 0)
        self.s.ms(5)
        self.assertEqual(len(self.s.gated()), 1)

    def test_a_sysex_cut_by_the_bound_carries_on(self):
        self.s.seq_set(64, flags=0)
        steps = [(i % 24 - 12, i % 100 + 1, 50, 1, 0, 64, 64, 64, 64) for i in range(64)]
        burst = bytes(b for i in range(50) for b in (0xB0, 1, i))  # so the bound falls inside the first SysEx
        for first in range(0, 64, 8):
            data = [v + 64 if k == 0 else v for s in steps[first:first + 8] for k, v in enumerate(s)]
            burst += pro800.SYSEX_HEADER + bytes([0x45, first, 8, *data]) + b"\xF7"
        self.s.cmd("midi " + burst.hex())
        self.s.ms(5)
        self.assertEqual(self.s.midi_stat()["usb"], 0)
        for first in range(0, 64, 8):
            read = self.s.seq_read(first, 8)
            self.assertEqual([r["offset"] for r in read], [s[0] for s in steps[first:first + 8]], f"steps from {first}")
            self.assertEqual([r["velocity"] for r in read], [s[1] for s in steps[first:first + 8]], f"steps from {first}")


USB_HARNESS = os.path.join(BUILD, "usbharness.exe" if os.name == "nt" else "usbharness")


def build_usb_harness():
    os.makedirs(BUILD, exist_ok=True)
    src, sim = os.path.join(ROOT, "src"), os.path.join(HERE, "sim")
    usb, harness = os.path.join(src, "usb.c"), os.path.join(sim, "usb_harness.c")
    regs = os.path.join(sim, "sim_regs.h")
    inputs = [usb, harness, regs, os.path.join(src, "usb.h"), os.path.join(src, "regs.h")]
    if os.path.exists(USB_HARNESS) and os.path.getmtime(USB_HARNESS) > max(os.path.getmtime(f) for f in inputs):
        return
    subprocess.check_call(["zig", "cc", "-std=gnu11", "-O1", "-g", "-w", "-I" + src, "-include", regs, usb, harness, "-o", USB_HARNESS])


def rewrite_record(path, slot, *changes):
    """Bytes of a stored patch changed on the chip, its checksum made right: what no panel or host request can write."""
    with open(path, "r+b") as f:
        data = bytearray(f.read())
        record = (slot - 1) * 768
        for offset, value in changes:
            data[record + offset] = value
        s = 0x12345678
        for b in data[record:record + 508]:
            s = ((s << 5 | s >> 27) & 0xFFFFFFFF) ^ b
        data[record + 508:record + 512] = s.to_bytes(4, "little")
        f.seek(0)
        f.write(data)


class SettingsRest(PanelTest):
    """MANUAL: the system-wide settings are stored a second after the last change, not on every step."""
    GLOBAL = 0x1F400
    CC_A = GLOBAL + 8

    def writes(self):
        return int(self.s.cmd("writes").split()[0])

    def stored_cc_a(self):
        return self.s.memory()[self.CC_A]

    def test_a_sweep_writes_once_after_the_rest(self):
        start = self.writes()
        self.s.hold("SHIFT")
        seen = set()
        for i in range(60):
            self.s.pot("A_LEVEL", i / 59 * 0.8)  # short of the end, where the value stops changing
            seen.add(self.s.settings()["cc-a"])
            self.assertEqual(self.writes(), start, "none during the sweep")
        self.assertGreater(len(seen), 30, "the value did move, and shows at once")
        self.s.let_go("SHIFT")
        self.s.ms(900)
        self.assertEqual(self.writes(), start, "not yet")
        self.s.ms(100)
        self.assertEqual(self.writes(), start + 1, "one, after the rest")
        self.assertEqual(self.stored_cc_a(), self.s.settings()["cc-a"])
        self.s.ms(3000)
        self.assertEqual(self.writes(), start + 1, "and no more")

    def test_each_change_starts_the_rest_again(self):
        start = self.writes()
        for value in (50, 51, 52, 53):
            self.s.setting("cc-a", value)
            self.s.ms(800)
            self.assertEqual(self.writes(), start)
        self.s.ms(300)
        self.assertEqual(self.writes(), start + 1)
        self.assertEqual(self.stored_cc_a(), 53)

    def test_every_setting_shows_at_once_and_is_stored_after_the_rest(self):
        for name, value in (("cc-a", 77), ("cc-b", 78), ("midi-channel", 5), ("pickup", 0), ("clock", 1), ("bpm", 200)):
            with self.subTest(setting=name):
                start = self.writes()
                self.s.setting(name, value)
                self.assertEqual(self.s.settings()[name], value)
                self.assertEqual(self.writes(), start)
                self.s.ms(1100)
                self.assertEqual(self.writes(), start + 1)
                self.power_cycle()
                self.assertEqual(self.s.settings()[name], value)

    def test_tap_tempo_writes_once_after_the_rest(self):
        start = self.writes()
        for wait in (300, 320, 340, 0):  # 400 to 440 ms apart: a tempo that moves with each tap
            self.s.press("CLOCK")
            self.s.ms(wait)
            self.assertEqual(self.writes(), start, "none per tap")
        self.assertAlmostEqual(self.s.settings()["bpm"], 143, delta=2)
        self.s.ms(1100)
        self.assertEqual(self.writes(), start + 1)

    def test_a_power_cut_inside_the_rest_loses_the_change(self):
        self.s.setting("cc-a", 77)
        self.s.ms(1100)
        self.s.setting("cc-a", 78)
        self.s.ms(500)
        self.power_cycle()
        self.assertEqual(self.s.settings()["cc-a"], 77)

    def test_update_mode_stores_it_at_once(self):
        self.s.setting("cc-a", 77)
        self.s.cmd("midi " + (pro800.SYSEX_HEADER + b"\x03\x30\xF7").hex())
        self.assertEqual(self.s.cmd("ms 5"), "update mode")
        self.power_cycle()
        self.assertEqual(self.s.settings()["cc-a"], 77)

    def test_an_autotune_stores_it_first(self):
        for tune in (lambda: self.s.press("TUNE"), lambda: self.s.request(0x75)):
            with self.subTest():
                self.s.setting("cc-a", self.s.settings()["cc-a"] + 1)
                wanted, start = self.s.settings()["cc-a"], self.writes()
                tune()
                self.assertEqual(int(self.s.cmd("writes").split()[1]), start + 1, "before the 25 s begin")
                self.assertEqual(self.stored_cc_a(), wanted)

    def test_loading_a_patch_carries_it(self):
        self.s.setting("cc-a", 77)
        start = self.writes()
        self.s.request(0x5C, 0, 1)
        self.assertEqual(self.writes(), start + 1, "the last patch, in the same block")
        self.assertEqual(self.stored_cc_a(), 77)
        self.s.ms(1100)
        self.assertEqual(self.writes(), start + 1, "nothing left to write")

    def test_a_format_carries_it(self):
        self.s.setting("cc-a", 77)
        self.s.request(0x5B)
        start = self.writes()
        self.assertEqual(self.stored_cc_a(), 77)
        self.s.ms(1100)
        self.assertEqual(self.writes(), start, "nothing left to write")

    def test_the_hosts_writes_arent_overwritten_afterwards(self):
        self.s.setting("cc-a", 77)
        a = self.CC_A
        self.s.request(0x5A, a >> 14, a >> 7 & 0x7F, a & 0x7F, 1, 5, 5)  # 0x55
        self.assertEqual(self.stored_cc_a(), 0x55)
        self.s.ms(1100)
        self.assertEqual(self.stored_cc_a(), 0x55, "an image written back stays as written")

    def read_global(self):
        a = self.CC_A
        return self.s.request(0x79, a >> 14, a >> 7 & 0x7F, a & 0x7F, 1)

    def test_a_chip_that_fails_gets_three_tries_and_then_rest_until_the_next_change(self):
        self.s.cmd("eepromfail 1")
        start = self.writes()
        self.s.setting("cc-a", 77)
        self.s.ms(3500)
        self.assertEqual(self.writes(), start + 3, "a try a second")
        self.s.ms(10000)
        self.assertEqual(self.writes(), start + 3, "and no more: each holds the engine for tens of ms")
        self.assertEqual(self.s.settings()["cc-a"], 77, "the value still counts")
        self.assertTrue(self.read_global(), "what stores it first leaves it alone too")
        self.assertEqual(self.writes(), start + 3)
        self.s.cmd("eepromfail 0")
        self.s.ms(3000)
        self.assertEqual(self.writes(), start + 3)
        self.s.setting("cc-a", 78)
        self.s.ms(1100)
        self.assertEqual(self.writes(), start + 4, "a new change tries again, and it carries the lot")
        self.assertEqual(self.stored_cc_a(), 78)

    def test_three_failures_by_any_route_count(self):
        self.s.cmd("eepromfail 1")
        self.s.setting("cc-a", 77)
        start = self.writes()
        for _ in range(5):
            self.assertTrue(self.read_global())
        self.assertEqual(self.writes(), start + 3)
        self.s.ms(3000)
        self.assertEqual(self.writes(), start + 3)

    def test_a_chip_that_recovers_inside_the_tries_is_written(self):
        self.s.cmd("eepromfail 1")
        self.s.setting("cc-a", 77)
        self.s.ms(1100)
        self.s.cmd("eepromfail 0")
        self.s.ms(1100)
        self.assertEqual(self.stored_cc_a(), 77)

    def test_a_read_of_the_memory_sees_it(self):
        self.s.setting("cc-a", 77)
        a = self.CC_A
        d = self.s.reply(0x79, a >> 14, a >> 7 & 0x7F, a & 0x7F, 1)
        self.assertEqual(d[6] << 4 | d[7], 77, "a backup holds the settings as they are")


class SettingsRestOnStockMemory(PanelTest):
    MEMORY = "stock"

    def test_the_stored_setting_makes_the_memory_ours(self):
        self.s.setting("cc-a", 77)
        self.assertNotEqual(self.s.memory()[0x1F400:0x1F404], b"P8G1")
        self.s.ms(1100)
        self.assertEqual(self.s.memory()[0x1F400:0x1F404], b"P8G1")
        self.power_cycle()
        self.assertEqual(self.s.settings()["cc-a"], 77)


class FactoryImport(PanelTest):
    def test_a_forced_import_leaves_no_old_sequence(self):
        self.s.seq_set(length=3)
        self.s.seq_steps(0, [(0,), (5,), (7,)])
        self.store_patch(5)
        self.s.close()
        with open(self.path, "r+b") as f:  # a factory bank, where the stock firmware leaves it
            record = bytearray(256)
            record[:4] = bytes([0xA5, 0x16, 0x61, 0x00])
            record[150:154] = b"Bell"
            f.seek(0x19000 + 7 * 256)
            f.write(record)
        self.s = Synth(self.path)
        self.s.ms(1600)
        self.assertEqual(self.s.seq_header()["length"], 3, "the slot had one")
        self.assertEqual(self.s.reply(0x51, 7, 0, 4, 1)[0], 0)
        self.assertEqual(self.s.memory()[4 * 768 + 512:5 * 768], bytes(256))
        self.s.request(0x5C, 0, 4)
        self.assertEqual(self.s.patch_info(4), (1, "Bell"))
        self.assertEqual(self.s.seq_header()["length"], 16, "a cleared sequence")
        self.assertEqual(self.s.seq_header()["flags"] & 1, 0, "and off")

    def test_a_factory_sound_gets_no_routes(self):
        self.s.close()
        with open(self.path, "r+b") as f:
            record = bytearray(256)
            record[:4] = bytes([0xA5, 0x16, 0x61, 0x00])
            record[150:154] = b"Bell"
            f.seek(0x19000 + 7 * 256)
            f.write(record)
        self.s = Synth(self.path)
        self.s.ms(1600)
        self.assertEqual(self.s.routes(0), INIT_ROUTES, "P1 is empty")
        self.assertEqual(self.s.reply(0x51, 7, 0, 4, 1)[0], 0)
        self.s.request(0x5C, 0, 4)
        self.assertEqual(self.s.patch_info(4), (1, "Bell"))
        self.assertEqual((self.s.routes(0), self.s.routes(1)), ([], []))


class HostProtocol(PanelTest):
    def assertSevenBit(self, reply):
        self.assertEqual((reply[0], reply[-1]), (0xF0, 0xF7))
        self.assertEqual([b for b in reply[1:-1] if b >= 0x80], [], reply.hex())

    def test_the_format_reply_is_seven_bit(self):
        self.assertEqual(self.s.request(0x5B), pro800.SYSEX_HEADER + bytes([0x5B, 1, 0, 0, 0xF7]))

    def test_every_reply_is_seven_bit(self):
        """The worst there is to report: a stored patch with every byte at FF, a crash record with high bits, and the format"""
        self.s.request(0x56, 0, 4, 3, 0, 99 + 30)
        self.store_patch(3)
        self.s.close()
        rewrite_record(self.path, 3, *[(offset, 0xFF) for offset in range(4, 508) if offset not in (240, 241)], (242, 0x80))  # the first route stays one, depth -128
        self.s = Synth(self.path, "--fault")
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 2, "it loaded")
        for request in ((0x54,), (0x55,), (0x48,), (0x46, 0, 8), (0x4C,), (0x4D, 0, 0), (0x4D, 0, 1), (0x4D, 1, 0), (0x4D, 1, 1),
                        (0x53, 0), (0x53, 1), (0x57, 0), (0x57, 1), (0x58,), (0x49,), (0x4F, 0, 2), (0x5E, 0, 2), (0x50, 0), (0x51, 0, 0, 9),
                        (0x6D,), (0x7C,), (0x7E, 0), (0x7E, 1), (0x77, 0), (0x78, 0, 0, 0), (0x79, 0, 0, 0, 48), (0x79, 0, 0, 40, 48),
                        (0x7A, 1), (0x75,), (0x08, 0x00), (0x5B,)):
            with self.subTest(request=request):
                reply = self.s.request(*request)
                self.assertTrue(reply, "it is answered, or this says nothing")
                self.assertSevenBit(reply)

    def test_a_stored_patch_out_of_range_comes_back_in_range(self):
        self.s.no_routes()  # the rewrite below is of the record's first route
        self.s.request(0x56, 0, 4, 3, 0, 99 + 30)
        self.store_patch(3)
        self.s.close()
        rewrite_record(self.path, 3, (70, 120), (71, 0x88), (242, 0x80))  # macro offsets 120, -120, route depth -128
        self.s = Synth(self.path)
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 2)
        self.assertEqual(self.s.settings()["macro 1 1"], 99 + 99)
        self.assertEqual(self.s.settings()["macro 1 2"], 99 - 99)
        self.assertSevenBit(self.s.request(0x57, 0))
        self.assertEqual(self.s.routes(0), [(4, 3, -99)])

    def test_the_sequences_tempo_clamps_to_what_it_holds(self):
        for sent, kept in ((300, 255), (256, 255), (255, 255), (200, 200), (20, 120)):
            with self.subTest(bpm=sent):
                self.s.seq_set(length=3, bpm=sent)
                self.assertEqual(self.s.seq_header()["bpm"], kept)

    def read_memory(self, address, length):
        return self.s.request(0x79, address >> 14, address >> 7 & 0x7F, address & 0x7F, length)

    def test_a_memory_read_stops_at_the_end_of_the_chip(self):
        size = 128 * 1024
        reply = self.read_memory(size - 48, 48)
        self.assertEqual(len(reply), pro800.HEADER_SIZE + 5 + 2 + 96 + 1, "up to the last byte")
        self.assertEqual(len(self.read_memory(size - 1, 1)), pro800.HEADER_SIZE + 5 + 2 + 2 + 1)
        self.assertEqual(self.read_memory(size - 47, 48), b"", "as a write past the end: no reply")
        self.assertEqual(self.read_memory(size, 1), b"")
        self.assertEqual(self.read_memory((1 << 21) - 1, 48), b"")


class ClockAfterATune(PanelTest):
    """The internal clock's ticks go on at once after an autotune (the simulator's takes 25 s), steady from there."""

    def setUp(self):
        super().setUp()
        self.s.setting("clock", 1)
        self.s.ms(1000)

    def beats(self, span=2500):
        """When the beat light came on, in ms from now"""
        out, was = [], self.s.led("SYNC_CLOCK")
        for t in range(0, span, 5):
            self.s.ms(5)
            lit = self.s.led("SYNC_CLOCK")
            if lit and not was:
                out.append(t)
            was = lit
        return out

    def assertSteady(self, span=2500):
        beats = self.beats(span)
        self.assertGreaterEqual(len(beats), 4, beats)
        for a, b in zip(beats, beats[1:]):
            self.assertLess(abs(b - a - 500), 30, f"120 BPM, beats at {beats}")

    def test_the_panels_tune(self):
        self.s.press("TUNE")
        self.assertSteady()

    def test_a_tune_asked_for_over_midi(self):
        self.s.request(0x75)
        self.assertSteady()

    def test_leaving_direct_mode_after_long_enough_for_the_counter_to_read_ahead(self):
        self.s.request(0x74, 1)
        self.s.ms(13000)
        self.s.request(0x74, 0)
        self.assertSteady(span=14000)  # the light runs on at the last tempo, and without its ticks jumps after 12.5 s


class ClockTicksHeldUp(PanelTest):
    """A tick that waited behind a parked USB endpoint is stamped late and the next on time, which measures short. The
    position and an LFO on the grid must not run ahead for that one tick. The probe's LFO 1 phase shows them."""
    TICK = 21  # ms: about 119 BPM
    EIGHTH = 12  # ticks: LFO 1's cycle here
    PHASE = 2 * 5 + 8 * 3 + 3 + 16  # in the probe's reply, after the held keys, the voices, keytrack and the switches

    def setUp(self):
        super().setUp()
        self.s.setting("lfo-sync", 1)
        self.s.pot("LFO_FREQ", 9 / 14)  # 1-8 of the 15 divisions
        self.s.ms(300)
        self.s.midi(0xFA)
        self.count = 0
        self.ticks(48)

    def tick(self):
        self.s.midi(0xF8)
        self.count += 1

    def ticks(self, n, every=TICK):
        for _ in range(n):
            self.tick()
            self.s.ms(every)

    def phase(self):
        """As of the next millisecond, which the reply goes out in; 5 ms have passed when it's back"""
        return self.s.reply(0x55)[self.PHASE] / 100

    def assertOnTheGrid(self, since, delta=0.05):
        position = self.count - 1 + (since + 1) / self.TICK
        self.assertAlmostEqual(self.phase(), position / self.EIGHTH % 1, delta=delta, msg=f"{position} ticks in")

    def test_a_late_tick_and_the_next_on_time(self):
        self.s.ms(15)
        self.tick()  # 36 ms after the last
        self.s.ms(6)
        self.tick()  # 6 ms: under half the tempo
        self.s.ms(14)
        self.assertOnTheGrid(14)
        self.s.ms(2)
        self.tick()  # and on time from there
        self.s.ms(14)
        self.assertOnTheGrid(14)

    def test_a_tick_later_than_two_intervals_with_the_next_right_behind(self):
        self.s.ms(25)
        self.tick()  # 46 ms
        self.tick()  # at once: too short to count
        self.s.ms(3)
        self.tick()  # 3 ms
        self.s.ms(14)
        self.assertOnTheGrid(14, delta=0.1)  # its position jumped two ticks, which the phase follows over a cycle

    def test_a_real_change_of_tempo_takes_over(self):
        slow = 84
        self.s.ms(slow - self.TICK)
        self.tick()  # on its own it could be a late one
        self.s.ms(slow)
        self.ticks(36, slow)  # and they go on like that, for long enough to settle on the grid
        self.tick()
        self.s.ms(30)
        position = self.count - 1 + 31 / slow
        self.assertAlmostEqual(self.phase(), position / self.EIGHTH % 1, delta=0.05, msg=f"{position} ticks in, at the new tempo")


class SingleWireRoutes(PanelTest):
    def test_a_voice_that_moved_to_the_other_layer_doesnt_feed_its_old_layers_routes(self):
        velocity, noise = pro800.SOURCE_NAMES.index("velocity"), pro800.DEST_NAMES.index("noise")
        self.s.request(0x56, 0, velocity, noise, 198 >> 7, 198 & 0x7F)
        self.assertEqual(self.s.cvs()[CV_NOISE], 0)
        before = []
        for i in range(6):  # the last to play is in the upper half
            self.s.note(60 + i)
            now = self.s.gated()
            last = [v for v in now if v not in before]
            before = now
        self.assertGreaterEqual(last[0], 4)
        self.s.ms(5)
        self.assertGreater(self.s.cvs()[CV_NOISE], 0, "the route, from the voice played last")
        self.s.setting("layers", pro800.LAYER_MODES.index("stack"))
        self.s.ms(5)
        self.assertEqual(self.s.cvs()[CV_NOISE], 0, "that voice is layer 2's now")
        self.s.note(72)
        self.s.ms(5)
        self.assertGreater(self.s.cvs()[CV_NOISE], 0, "layer 1 has played again")

    def test_a_route_on_both_layers_follows_the_layer_that_still_has_its_voice(self):
        velocity, noise = pro800.SOURCE_NAMES.index("velocity"), pro800.DEST_NAMES.index("noise")
        self.s.setting("layers", pro800.LAYER_MODES.index("split"))
        self.s.setting("split", 60)
        self.s.request(0x56, 0, velocity, noise, 198 >> 7, 198 & 0x7F)
        self.s.setting("route-both 1 (0-7)", 1)
        self.s.note(48)  # layer 1
        self.s.ms(10)
        self.s.note(72)  # layer 2, the newest
        self.s.ms(5)
        self.assertGreater(self.s.cvs()[CV_NOISE], 0)
        self.s.setting("layer-voices", 7)  # that voice is layer 1's now, and layer 2 has played nothing
        self.s.ms(5)
        self.assertGreater(self.s.cvs()[CV_NOISE], 0, "the route, once, from layer 1's voice")

    def test_lfo_to_pw_fades_in_with_the_voice_that_is_still_layer_1s(self):
        """LFO 1 fades in over a voice's first second or so. The last to play has moved to layer 2 and only just started"""
        self.s.param(0, "lfo-amount", 16383)
        self.s.param(0, "lfo-freq", 12000)
        self.s.param(0, "a-pw", 8000)
        self.s.setting("lfo-delay", 80)  # about 1.8 s
        self.s.set_button("LFO_PW", 1)
        self.s.set_button("A_SQUARE", 1)
        self.s.ms(100)
        for i in range(5):
            self.s.note(60 + i)
        self.s.ms(3000)
        self.s.note(70)
        self.s.setting("layers", pro800.LAYER_MODES.index("stack"))
        self.s.ms(5)
        widths = set()
        for _ in range(60):
            self.s.ms(5)
            widths.add(self.s.cvs()[CV_PW_A])
        self.assertGreater(max(widths) - min(widths), 1000, "voice 1 is well past its delay")


class LfoNoteTimes(PanelTest):
    """A synced LFO's delay and fade are note values (MANUAL, Delay and fade-in), measured on the voice's pitch CV: LFO 1 is
    a square into pitch, so the pitch sits still until the delay ends and then swings by the fade's gain either side."""
    QUARTER = 60  # a value on the note scale's step for 1/4 (57-62)
    NEAR_QUARTER = 58  # where a nudge up stays on it
    SHOTS = ("one-shot", "one-shot-voice")
    RATE_16TH = 12 / 14  # the rate knob
    RAMP_UP, SQUARE = pro800.SHAPE_NAMES.index("ramp-up"), pro800.SHAPE_NAMES.index("square")
    NAMES = ["1-32", "16t", "1-16", "16d", "1-8t", "1-8", "1-8d", "1-4t", "1-4", "1-4d", "1-2", "1bAr", "2bAr", "4bAr", "8bAr"]

    def setUp(self):
        super().setUp()
        self.s.param(0, "lfo-amount", 16383)
        self.s.param(0, "lfo-freq", round(16383 * self.RATE_16TH))
        self.s.setting("shape 1 1", self.SQUARE)
        self.s.set_button("LFO_FREQ", 1)
        self.s.setting("lfo-sync", 1)
        self.s.ms(400)  # the knobs glide to a new reading

    @staticmethod
    def free_seconds(value):
        return 0.02 * 2 ** (value * 8 / 99)

    def ticks(self, count, every):
        for _ in range(count):
            self.s.midi(0xF8)
            self.s.ms(every)

    def run_clock(self, every):
        """A MIDI clock at `every` ms a tick, long enough for its tempo to settle"""
        self.ticks(96, every)

    def key(self):
        """A key down. Returns the moment before it, and its voice."""
        at = self.s.now()
        self.s.note(60)
        return at, self.s.gated()[-1]

    def key_up(self):
        self.s.note_off(60)
        self.s.ms(100)

    # Ms from the key until the pitch leaves where it starts, which is when a delay with no fade ends. `clock`: a tick every
    # that many ms meanwhile (else a look every 5 ms).
    def motion(self, limit=3000, clock=0):
        at, voice = self.key()
        start = self.s.cvs()[voice]
        while self.s.now() - at < limit:
            self.ticks(1, clock) if clock else self.s.ms(5)
            if self.s.cvs()[voice] != start:
                moved = self.s.now() - at
                self.key_up()
                return moved
        self.fail(f"the pitch still hadn't moved {limit} ms after the key")

    def gains(self, duration, every=5):
        """The LFO's gain on the voice from a key down, as (ms since the key, gain): how far the pitch is from the middle,
        against the swing at the end, which has to be full by `duration`."""
        at, voice = self.key()
        samples, elapsed = [], self.s.now() - at
        while elapsed < duration:  # counted here: the simulator's clock wraps with the cycle counter, every 25 s
            self.s.ms(every)
            elapsed += every
            samples.append((elapsed, self.s.cvs()[voice]))
        full = [cv for _, cv in samples[-40:]]
        middle, swing = (max(full) + min(full)) / 2, (max(full) - min(full)) / 2
        self.assertGreater(swing, 1000, "the square is on the pitch at the end")
        self.key_up()
        return [(t, abs(cv - middle) / swing) for t, cv in samples]

    def assertGainAt(self, gains, ms, expected, delta=0.06):
        gain = min(gains, key=lambda g: abs(g[0] - ms))[1]
        self.assertAlmostEqual(gain, expected, delta=delta, msg=f"at {ms} ms")

    # --- the delay ---

    def test_a_quarter_before_any_clock_is_500_ms(self):
        self.s.setting("lfo-delay", self.QUARTER)
        self.assertAlmostEqual(self.motion(), 500, delta=15)

    def test_a_running_clock_scales_it(self):
        self.s.setting("lfo-delay", self.QUARTER)
        for every in (15, 40):
            with self.subTest(ms_a_tick=every):
                self.run_clock(every)
                self.assertAlmostEqual(self.motion(clock=every), 24 * every, delta=every + 15)

    def test_when_the_clock_stops_the_last_tempo_stays(self):
        self.s.setting("lfo-delay", self.QUARTER)
        self.run_clock(15)
        self.s.ms(500)
        self.assertAlmostEqual(self.motion(), 360, delta=15)

    def test_a_tempo_change_takes_effect_at_once(self):
        """1 bar: 2 s at the 120 BPM before any clock. A clock of 10 ms a tick comes in 200 ms after the key: 1 bar is 960 ms now."""
        self.s.setting("lfo-delay", 79)
        at, voice = self.key()
        start = self.s.cvs()[voice]
        self.s.ms(200)
        self.assertEqual(self.s.cvs()[voice], start)
        while self.s.cvs()[voice] == start:
            self.assertLess(self.s.now() - at, 1500, "still at the old tempo")
            self.ticks(1, 10)
        self.assertAlmostEqual(self.s.now() - at, 960, delta=30)

    # --- what has opened stays open until the next key ---

    def watch(self, duration, spacing=None):
        """The voice's pitch CV each ms from a key down, against where it starts: 0 while the LFO's gain is 0.
        `spacing(ms)`: the ms from a clock tick then to the next."""
        _, voice = self.key()
        start = self.s.cvs()[voice]
        out, next_tick = [], 0
        for elapsed in range(duration):
            if spacing and next_tick <= 0:
                self.s.midi(0xF8)
                next_tick = spacing(elapsed)
            self.s.ms(1)
            next_tick -= 1
            out.append(self.s.cvs()[voice] - start)
        self.key_up()
        return out

    def assertOpensOnce(self, moved, near, delta):
        first = next((i for i, d in enumerate(moved) if d), None)
        self.assertIsNotNone(first, "it never opened")
        self.assertAlmostEqual(first, near, delta=delta)
        closed = [i for i, d in enumerate(moved) if d == 0 and i > first]
        self.assertFalse(closed, f"opened at {first} ms and shut again at {closed[:5]}")

    def test_a_jittery_clock_opens_a_long_delay_once(self):
        """Ticks 20-22 ms apart, as USB can bring them, move the averaged tempo and with it the end of 4 bars by more than a
        tick's worth. Also with a short fade after it, which reads the same end."""
        rng = random.Random(1)
        jitter = lambda _=0: rng.choice((20, 21, 22))
        for _ in range(96):
            self.ticks(1, jitter())
        self.s.setting("lfo-delay", 90)
        for fade, seeds in ((0, 4), (20, 2)):
            self.s.setting("lfo-fade", fade)
            for run in range(seeds):
                with self.subTest(fade=fade, run=run):
                    self.assertOpensOnce(self.watch(8700, jitter), 8064, 300)

    def test_a_slower_tempo_doesnt_close_a_delay_that_has_ended(self):
        self.s.setting("lfo-delay", self.QUARTER)
        self.run_clock(15)
        self.assertOpensOnce(self.watch(1200, lambda ms: 15 if ms < 400 else 40), 360, 20)

    def test_a_slower_tempo_doesnt_lower_a_fade(self):
        """360 ms at 15 ms a tick; 960 at 40, of which the time it has run is less than it has reached. It waits, then goes on."""
        self.s.setting("lfo-fade", self.QUARTER)
        self.run_clock(15)
        moved = self.watch(1100, lambda ms: 15 if ms < 200 else 40)
        full = max(moved)
        for side in (1, -1):  # the pitch's two sides sit a little apart, so each against itself
            most = 0
            for ms, d in enumerate(moved):
                if d * side > 0:
                    self.assertGreaterEqual(d * side, most - 0.02 * full, f"at {ms} ms")
                    most = max(most, d * side)
        self.assertLess(abs(moved[700]), 0.9 * full, "the slower tempo counts: it hasn't finished yet")
        self.assertGreater(abs(moved[-1]), 0.95 * full)

    def test_a_one_shot_and_its_gain_agree_on_a_slower_tempo(self):
        """It has started and run out at 15 ms a tick, so the gain has to stay open while the tempo slows to 40."""
        self.s.setting("lfo-delay", self.QUARTER)
        self.shot_mode("one-shot")
        self.run_clock(15)
        moved = self.watch(1200, lambda ms: 15 if ms < 400 else 40)
        self.assertEqual(set(moved[600:]), {max(moved)}, "stopped at the ramp's top, and heard")

    def test_every_new_note_waits_again(self):
        """More notes than voices, so each voice's second note goes through it too."""
        self.s.setting("lfo-delay", 4)
        for run in range(10):
            with self.subTest(run=run):
                self.assertAlmostEqual(self.motion(), 62.5, delta=15)

    def test_synced_a_larger_value_counts_from_the_next_key(self):
        self.s.setting("lfo-delay", 4)
        at, voice = self.key()
        start = self.s.cvs()[voice]
        self.s.ms(200)
        self.s.setting("lfo-delay", self.QUARTER)
        for _ in range(60):
            self.s.ms(5)
            self.assertNotEqual(self.s.cvs()[voice], start, "the note had opened")
        self.key_up()
        self.assertAlmostEqual(self.motion(), 500, delta=15)

    def test_free_a_larger_value_still_closes_a_note_that_has_opened(self):
        """Free mode's gain keeps nothing from one pass to the next, as it always has."""
        self.s.setting("lfo-sync", 0)
        self.s.setting("lfo-delay", 4)  # 26 ms
        at, voice = self.key()
        start = self.s.cvs()[voice]
        self.s.ms(100)
        self.assertNotEqual(self.s.cvs()[voice], start, "it has opened")
        self.s.setting("lfo-delay", self.QUARTER)
        self.s.ms(100)
        self.assertEqual(self.s.cvs()[voice], start)
        while self.s.cvs()[voice] == start:
            self.assertLess(self.s.now() - at, 800)
            self.s.ms(5)
        self.assertAlmostEqual(self.s.now() - at, self.free_seconds(self.QUARTER) * 1000, delta=15)

    def test_the_values_steps(self):
        """57 is where 1/4 starts: 56 is still 1/4T (333 ms). 62 is where it ends: 63 is 1/4. (750 ms). 4 is the first that isn't 0: 1/32."""
        for value, ms in ((56, 333), (57, 500), (62, 500), (63, 750), (4, 62.5)):
            with self.subTest(value=value):
                self.s.setting("lfo-delay", value)
                self.assertAlmostEqual(self.motion(), ms, delta=15)

    def test_a_value_that_rounds_to_no_note_value_is_no_delay(self):
        """1-3 are 20-24 ms in free mode. Synced they name no note value, so the first look after the key finds it at full."""
        for setting, other in (("lfo-delay", "lfo-fade"), ("lfo-fade", "lfo-delay")):
            for value in (1, 2, 3):
                with self.subTest(setting=setting, value=value):
                    self.s.setting(setting, value)
                    self.s.setting(other, 0)
                    gains = self.gains(300)
                    self.assertLess(gains[0][0], 20)
                    self.assertAlmostEqual(gains[0][1], 1.0, delta=0.06)

    def test_the_same_value_in_free_mode_takes_the_seconds_it_always_did(self):
        self.run_clock(15)  # a tempo that would show
        self.s.setting("lfo-sync", 0)
        for value in (self.QUARTER, 90):
            with self.subTest(value=value):
                self.s.setting("lfo-delay", value)
                self.assertAlmostEqual(self.motion(limit=4000), self.free_seconds(value) * 1000, delta=15)
        self.s.setting("lfo-delay", 1)
        self.assertAlmostEqual(self.motion(), self.free_seconds(1) * 1000, delta=10)

    def test_each_lfo_goes_by_its_own_sync(self):
        """LFO 2 alone on the pitch: synced its value is a note value, whatever LFO 1 is doing, and the other way round."""
        self.s.set_button("LFO_FREQ", 0)
        self.s.param(0, "lfo2-amount", 16383)
        self.s.param(0, "lfo2-rate", round(16383 * self.RATE_16TH))
        self.s.setting("shape 1 2", self.SQUARE)
        self.s.setting("lfo2-dests 1", 1)
        self.s.setting("lfo2-delay", 90)
        self.s.ms(400)
        self.s.setting("lfo2-sync", 1)
        self.s.setting("lfo-sync", 0)
        self.assertAlmostEqual(self.motion(limit=9000), 8000, delta=15, msg="4 bars")
        self.s.setting("lfo2-sync", 0)
        self.s.setting("lfo-sync", 1)
        self.assertAlmostEqual(self.motion(limit=4000), self.free_seconds(90) * 1000, delta=15)

    # --- the fade ---

    def test_the_fade_reaches_full_over_its_note_value(self):
        self.s.setting("lfo-fade", self.QUARTER)
        gains = self.gains(1000)
        for ms, gain in ((125, 0.25), (250, 0.5), (375, 0.75), (480, 0.96), (520, 1.0), (800, 1.0)):
            self.assertGainAt(gains, ms, gain)

    def test_the_fade_follows_the_clock(self):
        self.s.setting("lfo-fade", self.QUARTER)
        self.run_clock(15)
        gains = self.gains(1000)  # the last tempo: 360 ms
        for ms, gain in ((90, 0.25), (180, 0.5), (270, 0.75), (380, 1.0)):
            self.assertGainAt(gains, ms, gain)

    def test_the_fade_starts_when_the_delay_ends(self):
        self.s.setting("lfo-delay", self.QUARTER)
        self.s.setting("lfo-fade", self.QUARTER)
        gains = self.gains(1400)
        for ms, gain in ((200, 0), (480, 0), (750, 0.5), (1020, 1.0)):
            self.assertGainAt(gains, ms, gain)

    def test_a_fade_in_free_mode_takes_its_seconds(self):
        self.s.setting("lfo-sync", 0)
        self.s.setting("lfo-fade", self.QUARTER)
        gains = self.gains(1200)
        full = self.free_seconds(self.QUARTER) * 1000
        for part in (0.25, 0.5, 0.75):
            self.assertGainAt(gains, full * part, part)
        self.assertGainAt(gains, full + 20, 1.0)

    def test_a_long_delay_and_fade_at_a_slow_tempo_still_finish(self):
        """8 bars each at 30 BPM: 64 + 64 s, past the 100 s a voice's age used to stop at."""
        self.s.setting("clock", 1)
        self.s.setting("bpm", 30)
        self.s.setting("lfo-delay", 99)
        self.s.setting("lfo-fade", 99)
        self.s.ms(100)
        gains = self.gains(130000, every=61)  # 61: never in step with the square
        self.assertGainAt(gains, 63000, 0)
        self.assertGainAt(gains, 96000, 0.5)
        self.assertGainAt(gains, 129000, 1.0)

    # --- the one shot's wait ---

    def shot_top(self, limit=1200, clock=0):
        """Ms from the key until a one shot's ramp has run and stopped at its top. Through its wait the gain holds the pitch in
        the middle; then it starts from the bottom."""
        at, voice = self.key()
        top, seen = None, 0
        while self.s.now() - at < limit:
            self.ticks(1, clock) if clock else self.s.ms(2)
            cv = self.s.cvs()[voice]
            if cv > seen:
                seen, top = cv, self.s.now() - at
        self.key_up()
        return top

    def shot_mode(self, mode):
        self.s.setting("shape 1 1", self.RAMP_UP)
        self.s.setting("lfo-trigger", pro800.TRIGGER_MODES.index(mode))

    def test_a_one_shots_wait_is_the_note_value(self):
        """500 ms, then the ramp's 1/16: 125 ms. In free mode's seconds it would be 576 + 125."""
        self.s.setting("lfo-delay", self.QUARTER)
        for mode in self.SHOTS:
            with self.subTest(mode=mode):
                self.shot_mode(mode)
                self.assertAlmostEqual(self.shot_top(), 625, delta=20)

    def test_a_one_shots_wait_follows_a_running_clock(self):
        self.s.setting("lfo-delay", self.QUARTER)
        self.run_clock(10)
        for mode in self.SHOTS:
            with self.subTest(mode=mode):
                self.shot_mode(mode)
                self.assertAlmostEqual(self.shot_top(limit=600, clock=10), 300, delta=30)  # 240 ms, then 60

    def test_a_tempo_change_moves_a_one_shots_wait_at_once(self):
        """1 bar, 2 s before any clock; 10 ms a tick from 200 ms after the key: 960 ms, and a 1/16 of 60."""
        self.s.setting("lfo-delay", 79)
        self.s.setting("shape 1 1", self.RAMP_UP)
        self.s.setting("lfo-trigger", pro800.TRIGGER_MODES.index("one-shot"))
        at, voice = self.key()
        self.s.ms(200)
        top, seen = None, 0
        while self.s.now() - at < 1500:
            self.ticks(1, 10)
            cv = self.s.cvs()[voice]
            if cv > seen:
                seen, top = cv, self.s.now() - at
        self.assertAlmostEqual(top, 1020, delta=40)

    def assertTurnedFreeWaitsTheSeconds(self):
        """8 bars, 16 s synced, 5.12 s free. Turned free at 100 ms, it goes as one free from the key: the gain and the ramp at
        5.12 s, the ramp's cycle 52 ms at this knob. With the 16 s kept, the ramp would sit at its bottom until then."""
        self.s.setting("lfo-delay", 99)
        for mode in self.SHOTS:
            with self.subTest(mode=mode):
                self.shot_mode(mode)
                self.s.setting("lfo-sync", 1)
                at, voice = self.key()
                start = self.s.cvs()[voice]
                self.s.ms(100)
                self.s.setting("lfo-sync", 0)
                top, seen = None, start
                while self.s.now() - at < 5600:
                    self.s.ms(5)
                    cv = self.s.cvs()[voice]
                    if cv > seen:
                        seen, top = cv, self.s.now() - at
                self.key_up()
                self.assertIsNotNone(top, "the ramp never rose past where it started")
                self.assertAlmostEqual(top, self.free_seconds(99) * 1000 + 52, delta=25)

    def test_a_one_shot_turned_free_during_its_wait_waits_the_seconds(self):
        self.assertTurnedFreeWaitsTheSeconds()

    def test_and_one_that_restarts_from_the_keys_under_a_sequence(self):
        self.s.seq_set(1, division=0)
        self.s.setting("lfo-restart", 1)
        self.assertTurnedFreeWaitsTheSeconds()

    def test_a_one_shot_waits_its_seconds_in_free_mode(self):
        self.s.setting("lfo-sync", 0)
        self.s.param(0, "lfo-freq", round(16383 * 0.8))  # 0.05 × 2^8 = 12.8 Hz: a cycle of 78 ms
        self.s.ms(400)
        self.s.setting("lfo-delay", self.QUARTER)
        for mode in self.SHOTS:
            with self.subTest(mode=mode):
                self.shot_mode(mode)
                self.assertAlmostEqual(self.shot_top(), self.free_seconds(self.QUARTER) * 1000 + 78, delta=20)

    def test_a_one_shot_that_restarts_from_the_keys_under_a_sequence(self):
        """lfo_key_on(): the keys restart the LFOs, not the steps. One long step, so the voice stays."""
        self.s.seq_set(1, division=0)
        self.s.setting("lfo-restart", 1)
        self.s.setting("lfo-delay", self.QUARTER)
        for mode in self.SHOTS:
            with self.subTest(mode=mode):
                self.shot_mode(mode)
                self.assertAlmostEqual(self.shot_top(), 625, delta=20)

    # --- the panel ---

    def turn(self, pot):
        """A nudge under Shift: leaves the value's display up, with Shift held"""
        self.s.hold("SHIFT")
        self.s.pot(pot, 0.4)
        self.s.ms(150)  # the panel has read it: where the nudge starts from
        self.s.pot(pot, 0.43)
        self.s.ms(520)

    def assertNudged(self, pot, setting, text):
        self.s.setting(setting, self.NEAR_QUARTER)
        self.turn(pot)
        now = self.s.settings()[setting]
        self.assertGreater(now, self.NEAR_QUARTER, "a nudge")
        self.assertShows(text(now))
        self.s.let_go("SHIFT")
        self.s.ms(1600)

    def note_name(self, value):
        step = int(value / 99 * 15 + 0.5)
        return "0" if not step else self.NAMES[step - 1]

    def test_the_display_shows_the_note_value_when_synced_and_the_number_when_free(self):
        number = lambda v: f"{v:4d}"
        for pot, one, two in (("LFO_FREQ", "lfo-delay", "lfo2-delay"), ("LFO_AMOUNT", "lfo-fade", "lfo2-fade")):
            with self.subTest(pot=pot):
                self.s.setting("lfo-sync", 1)
                self.s.setting("lfo2-sync", 0)
                self.assertNudged(pot, one, self.note_name)
                self.s.setting("lfo-sync", 0)
                self.s.setting("lfo2-sync", 1)
                self.assertNudged(pot, one, number)
                self.s.press("ARP_UP")  # LFO 2 selected: its own sync decides
                self.s.ms(1000)
                self.assertNudged(pot, two, self.note_name)
                self.s.setting("lfo-sync", 1)
                self.s.setting("lfo2-sync", 0)
                self.assertNudged(pot, two, number)
                self.s.press("ARP_UP")
                self.s.ms(1000)

    def test_the_name_comes_first_and_the_ends_of_the_scale(self):
        self.s.setting("lfo-delay", 40)
        self.s.hold("SHIFT")
        self.s.pot("LFO_FREQ", 0.3)
        self.s.pot("LFO_FREQ", 0.6)
        self.assertShows("dLY")
        self.s.ms(520)
        self.assertShows(self.note_name(self.s.settings()["lfo-delay"]))
        self.s.pot("LFO_FREQ", 1.0)
        self.assertShows("8bAr")
        self.s.pot("LFO_FREQ", 0.5)
        self.s.pot("LFO_FREQ", 0.0)
        self.s.pot("LFO_FREQ", 0.5)
        self.s.pot("LFO_FREQ", 0.0)
        self.assertEqual(self.s.settings()["lfo-delay"], 0)
        self.assertShows("0", "at the left, as an envelope's note value shows it; free mode's number 0 is at the right")

    def test_a_value_on_the_way_to_the_first_note_value(self):
        self.s.setting("lfo-fade", 3)
        self.turn("LFO_AMOUNT")
        self.assertIn(self.s.settings()["lfo-fade"], (5, 6, 7), "1/32 from 4")
        self.assertShows("1-32")
        self.s.pot("LFO_AMOUNT", 0.4)
        self.s.ms(100)
        self.assertIn(self.s.settings()["lfo-fade"], (1, 2, 3))
        self.assertShows("0")

    def test_letting_go_of_shift_at_once_still_shows_the_note_value(self):
        self.s.setting("lfo-delay", self.NEAR_QUARTER)
        self.s.hold("SHIFT")
        self.s.pot("LFO_FREQ", 0.4)
        self.s.ms(150)
        self.s.pot("LFO_FREQ", 0.43)
        self.s.ms(100)
        self.assertShows("dLY")
        self.s.let_go("SHIFT")
        self.s.ms(450)
        self.assertShows("1-4")

    def test_a_sync_changed_meanwhile_counts_from_the_next_turn(self):
        """Nothing else needs to happen: a value on the display stays for its moment, and the next turn looks again."""
        self.s.setting("lfo-delay", self.NEAR_QUARTER)
        self.turn("LFO_FREQ")
        self.assertShows("1-4")
        self.s.setting("lfo-sync", 0)
        self.assertShows("1-4")
        self.s.pot("LFO_FREQ", 0.46)
        self.assertShows(f"{self.s.settings()['lfo-delay']:4d}")
        self.s.setting("lfo-sync", 1)
        self.s.pot("LFO_FREQ", 0.49)
        self.assertShows(self.note_name(self.s.settings()["lfo-delay"]))
        self.s.let_go("SHIFT")

    def test_the_tapped_tempo_is_still_a_number(self):
        """The display's value is a tempo here, whichever knob Shift turned last."""
        self.s.setting("lfo-delay", self.NEAR_QUARTER)
        self.turn("LFO_FREQ")
        self.s.let_go("SHIFT")
        self.s.ms(1600)
        self.s.history()
        for _ in range(3):
            self.s.press("CLOCK")
            self.s.ms(400)
        self.s.ms(600)
        self.assertSaw(["tAP", " 120"])

    def test_saving_and_recalling_a_patch_keeps_the_values(self):
        self.s.setting("lfo-delay", self.QUARTER)
        self.s.setting("lfo-fade", 30)
        self.s.setting("lfo2-delay", 7)
        self.s.setting("lfo2-fade", 90)
        self.store_patch(5)
        self.s.long_press("PERF")
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["lfo-delay"], 0, "the init sound replaced it")
        self.s.press("5")
        self.s.ms(3100)
        st = self.s.settings()
        self.assertEqual((st["lfo-sync"], st["lfo-delay"], st["lfo-fade"], st["lfo2-delay"], st["lfo2-fade"]), (1, self.QUARTER, 30, 7, 90))
        self.s.setting("lfo-fade", 0)
        self.assertAlmostEqual(self.motion(), 500, delta=15, msg="and it acts on them")
        self.s.setting("lfo-sync", 0)
        self.assertEqual(self.s.settings()["lfo-delay"], self.QUARTER, "the sync doesn't change what's stored")

    def test_the_host_reads_back_what_it_set(self):
        for value in (0, 3, self.QUARTER, 99):
            self.s.setting("lfo-delay", value)
            self.assertEqual(self.s.settings()["lfo-delay"], value)

    # --- the envelopes, on the scale they've always had ---

    def envelope_opens(self, clock=0):
        """Ms from the key until the amp envelope leaves 0: its DAHDSR delay"""
        at, voice = self.key()
        while self.s.cvs()[CV_AMP1 + voice] == 0:
            self.assertLess(self.s.now() - at, 3000)
            self.ticks(1, clock) if clock else self.s.ms(5)
        opened = self.s.now() - at
        self.key_up()
        self.s.ms(500)
        return opened

    def test_an_envelopes_note_values_are_as_before(self):
        """DAHDSR delay at 1/4, in note values: the amp stays shut for 500 ms, or a quarter at the clock"""
        self.s.setting("dahdsr 1", 1)
        self.s.setting("env-tempo 1", 1)
        self.s.setting("env-delay 1 amp", self.QUARTER)
        self.s.ms(50)
        self.assertAlmostEqual(self.envelope_opens(), 500, delta=25)
        self.run_clock(15)
        self.assertAlmostEqual(self.envelope_opens(clock=15), 360, delta=40)
        self.s.setting("env-tempo 1", 0)
        self.assertAlmostEqual(self.envelope_opens(), 374, delta=25, msg="in seconds again, on the envelope's own curve")


class UsbDriver(unittest.TestCase):
    """The real src/usb.c against a model of the OTG core (tools/sim/usb_harness.c): the checks are in there. They cover the
    driver's register writes against what the model assumes, not the core: what it assumes stays to be seen on the synth."""

    @classmethod
    def setUpClass(cls):
        build_usb_harness()

    def run_scenario(self, name):
        done = subprocess.run([USB_HARNESS, name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=BUILD)
        self.assertEqual(done.returncode, 0, done.stdout)
        return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", done.stdout)}

    def test_a_500_event_burst_with_a_slow_reader_arrives_whole_and_in_order(self):
        counts = self.run_scenario("burst")
        self.assertEqual(counts["events"], 500)
        self.assertGreater(counts["naks"], 0, "the host had to wait")

    def test_a_full_ring_parks_and_the_read_that_makes_room_opens_it(self):
        self.run_scenario("park")

    def test_a_flush_opens_a_parked_endpoint(self):
        self.run_scenario("flush")

    def test_ready_follows_the_in_endpoint(self):
        """usb_midi_ready(), which ends midiout.c's stall: configured and the last transfer read."""
        self.run_scenario("ready")

    def test_a_bus_reset_clears_parked(self):
        self.run_scenario("reset")

    def test_discard_mode_keeps_the_endpoint_armed_and_drops(self):
        self.assertEqual(self.run_scenario("discard")["naks"], 1, "only after it's off again")

    def test_a_realtime_byte_still_reaches_the_handler(self):
        self.run_scenario("realtime")

    def test_a_pipe_reset_from_the_host_loses_no_packet(self):
        self.run_scenario("toggles")

    def test_an_interrupt_as_the_driver_goes_to_mask_it(self):
        self.run_scenario("preempted")

    def test_a_random_walk_loses_nothing_and_never_stays_parked_on_an_empty_ring(self):
        self.assertGreater(self.run_scenario("random")["naks"], 0)


CRASH_FIELDS = ("pc", "lr", "exc_return", "cfsr", "hfsr", "mmfar", "bfar")
CRASH_NONE = dict(present=0, streak=0, exception=0, **dict.fromkeys(CRASH_FIELDS, 0))
# what tools/sim/hw.c's --fault leaves
CRASH_FAKE = dict(present=1, streak=1, exception=3, pc=0x08012345, lr=0x0801ABCD, exc_return=0xFFFFFFE9, cfsr=0x00008200, hfsr=0x40000000,
                  mmfar=0xFEDCBA98, bfar=0x30000000)
PROVOKE = (0x4A, 0x43, 0x52)


class CrashTest(PanelTest):
    def crash_record(self):
        """SysEx 49, byte for byte: every byte a MIDI data byte, each value five of them, high first."""
        reply = self.s.request(0x49)
        head = pro800.SYSEX_HEADER + b"\x49"
        self.assertTrue(reply.startswith(head) and reply.endswith(b"\xF7"), reply.hex())
        body = reply[len(head):-1]
        self.assertEqual(len(body), 3 + 5 * len(CRASH_FIELDS), body.hex())
        self.assertLess(max(body), 0x80, body.hex())
        record = dict(present=body[0], streak=body[1], exception=body[2])
        for i, name in enumerate(CRASH_FIELDS):
            value = 0
            for b in body[3 + 5 * i:8 + 5 * i]:
                value = value << 7 | b
            record[name] = value
        return record


class CrashRecord(CrashTest):
    def test_no_crash_reports_none(self):
        self.assertEqual(self.crash_record(), CRASH_NONE)

    def test_a_record_from_the_last_run_reports_its_values(self):
        self.power_cycle("--fault")
        self.assertEqual(self.crash_record(), CRASH_FAKE)

    def test_the_streak_clears_after_10_s_and_the_rest_stays(self):
        self.power_cycle("--fault")
        self.s.ms(9900 - self.s.now())
        self.assertEqual(self.crash_record()["streak"], 1)
        self.s.ms(200)
        self.assertEqual(self.crash_record(), dict(CRASH_FAKE, streak=0))

    def test_a_bad_check_word_is_no_record(self):
        self.power_cycle("--fault-bad")
        self.assertEqual(self.crash_record(), CRASH_NONE)


class CrashOnTheDisplay(CrashTest):
    def version_shown(self, seen):
        return readable(segments_of(VERSION)) in [t for _, t in seen]

    def test_crsh_instead_of_the_version_then_the_idle_display(self):
        idle = self.s.shows()
        self.power_cycle("--fault")
        self.assertShows("CrSh")
        self.s.ms(1400)
        self.assertShows("CrSh")
        self.s.ms(200)
        self.assertEqual(self.s.shows(), idle)
        self.assertFalse(self.version_shown(self.s.history()), "CrSh takes the version's place")

    def test_the_version_when_a_clean_run_cleared_the_streak(self):
        self.power_cycle("--fault-old")
        self.assertShows(VERSION)
        self.assertEqual(self.crash_record(), dict(CRASH_FAKE, streak=0))

    def test_the_version_when_the_record_is_noise(self):
        self.power_cycle("--fault-bad")
        self.assertShows(VERSION)

    def test_the_version_with_no_record(self):
        self.power_cycle()
        self.assertShows(VERSION)


class ProvokingACrash(CrashTest):
    def test_anything_but_the_two_magic_bytes_does_nothing(self):
        for data in ((0x4A,), (0x4A, 0x43), (0x4A, 0x43, 0x53), (0x4A, 0x42, 0x52), (*PROVOKE, 0)):
            with self.subTest(data=data):
                self.assertEqual(self.s.request(*data), b"")
                self.assertEqual(self.s.cmd("ms 5"), "ok")

    def test_the_two_magic_bytes_crash_it(self):
        self.s.cmd("midi " + (pro800.SYSEX_HEADER + bytes(PROVOKE) + b"\xF7").hex())
        self.assertEqual(self.s.cmd("ms 5"), "fault provoked")

    def test_settings_waiting_to_be_written_are_stored_first(self):
        self.s.setting("cc-a", 77)
        self.s.cmd("midi " + (pro800.SYSEX_HEADER + bytes(PROVOKE) + b"\xF7").hex())
        self.assertEqual(self.s.cmd("ms 5"), "fault provoked")
        self.power_cycle()
        self.assertEqual(self.s.settings()["cc-a"], 77)


class CrashHandlerLogic(CrashTest):
    """src/fault.h's fault_store(), which src/startup.c's handler calls, on a made-up stack of 16 words holding
    0x08000000 + index; the probe then reads what it left."""

    def store(self, kind="ok", exception=3):
        return self.s.cmd(f"faultstore {kind} {exception}")

    def test_the_third_crash_in_a_row_stops_resetting(self):
        for streak, reset in ((1, "1"), (2, "1"), (3, "0"), (4, "0")):
            self.assertEqual(self.store(), reset, streak)
            self.assertEqual(self.crash_record()["streak"], streak)

    def test_10_s_of_uptime_starts_the_count_again_once(self):
        self.store()
        self.s.ms(10001 - self.s.now())
        self.s.ms(1)
        self.assertEqual(self.crash_record()["streak"], 0)
        self.assertEqual(self.store(), "1")
        self.assertEqual(self.crash_record()["streak"], 1, "counted from nothing")
        self.s.ms(11000)
        self.assertEqual(self.crash_record()["streak"], 1, "only once in a run: a real crash resets anyway")

    def test_the_frame_is_read_only_where_it_can_be(self):
        for kind, pc, lr in (("ok", 0x0800000A, 0x08000009), ("top", 0x0800000E, 0x0800000D),  # exactly 32 bytes left
                             ("full", 0, 0), ("below", 0, 0), ("odd", 0, 0)):
            with self.subTest(kind):
                self.store(kind, 19)
                self.assertEqual(self.crash_record(), dict(present=1, streak=self.crash_record()["streak"], exception=19, pc=pc, lr=lr,
                                                           exc_return=0xFFFFFFF9, cfsr=1, hfsr=2, mmfar=3, bfar=4))


class NoCrystal(CrashTest):
    """clock_init() fails: 16 MHz, no 48 MHz, so no USB. (usb_init() on the real chip would wait for ever.)"""
    OPTIONS = ("--no-crystal",)

    def test_boots_to_the_same_idle_display_after_the_version(self):
        self.assertIn(readable(segments_of(VERSION)), [t for _, t in self.s.history()])
        idle = self.s.shows()
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.s.shows(), idle)

    def amp(self):
        """The loudest voice's VCA: with no USB the probes can't answer, so this is how a note shows."""
        return max(self.s.cvs()[CV_AMP1:CV_AMP1 + 8])

    def test_din_plays(self):
        silent = self.amp()
        self.s.din(0x90, 60, 100)
        self.s.ms(500)
        self.assertGreater(self.amp(), silent)
        self.s.din(0x80, 60, 0)
        self.s.ms(10000)
        self.assertEqual(self.amp(), silent)

    def test_usb_stays_silent(self):
        silent = self.amp()
        self.s.midi(0x90, 60, 100)
        self.s.ms(500)
        self.assertEqual(self.amp(), silent)
        self.assertGreater(self.s.midi_stat()["usb"], 0, "nothing reads it")

    def test_no_request_gets_in_over_din(self):
        """src/din.c throws SysEx away, so without USB no message reaches the bootloader, the crash record or a probe."""
        silent = self.amp()
        for request in (b"\x03\x30", b"\x08\x00", b"\x49", b"\x4A\x43\x52"):
            with self.subTest(request.hex()):
                self.s.cmd("din " + (pro800.SYSEX_HEADER + request + b"\xF7").hex())
                self.assertEqual(self.s.cmd("ms 5"), "ok")
                self.assertEqual(self.s.cmd("out"), "")
        self.s.din(0x90, 60, 100)
        self.s.ms(500)
        self.assertGreater(self.amp(), silent, "the loop goes on and the SysEx left nothing behind")


class EveryName(unittest.TestCase):
    def test_every_text_for_the_display_has_glyphs(self):
        with open(os.path.join(ROOT, "src", "main.c"), encoding="utf-8") as f:
            main = f.read()
        texts = set(re.findall(r'display_text\("([^"]*)"\)', main))
        for table in re.findall(r"(?:names?|modes|curves|values|table)\[[^\]]*\](?:\[[^\]]*\])* = \{(.*?)\};", main, re.S):
            texts.update(re.findall(r'"([^"\n]*)"', table))
        with open(os.path.join(ROOT, "src", "lfo.c"), encoding="utf-8") as f:
            lfo = f.read()
        for table in re.findall(r"names?\[[^\]]*\](?:\[[^\]]*\])* = \{(.*?)\};", lfo, re.S):
            texts.update(re.findall(r'"([^"\n]*)"', table))
        self.assertGreater(len(texts), 80)
        self.assertLessEqual({"VE.A", "VE.F", "P.UP", "FULL"}, texts)
        missing = {}
        for text in sorted(texts):
            for c in text:
                if c not in ". " and c.upper() not in GLYPHS:
                    missing.setdefault(text, []).append(c)
        self.assertEqual(missing, {}, "these can't be read on the display")


# --- the browser pages' own code, in Node: with a fake clock and fake MIDI ports, or against the simulated firmware ---

def page_script(name):
    """What a page runs: the shared script, then its inline one, in one scope as a browser has them."""
    html = source("tools", "web", name)
    return source("tools", "web", "sound-plan.js") + "\n" + re.findall(r"<script>(.*?)</script>", html, re.S)[-1]


def run_node(script, synth=None):
    """Runs a script in Node and returns what it gives report(). A line "REQ <hex>" is a request for the simulated firmware:
    it goes to synth.request() and what the firmware sent comes back on stdin. "WAIT <ms>" runs the firmware that long."""
    folder = tempfile.mkdtemp(prefix="pro800page")
    path = os.path.join(folder, "page.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    process = subprocess.Popen([NODE, path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    other = []
    try:
        for line in process.stdout:
            if line.startswith("REQ "):
                process.stdin.write(synth.request(*bytes.fromhex(line[4:].strip())).hex() + "\n")
                process.stdin.flush()
            elif line.startswith("WAIT "):  # the firmware's time passes under the page
                synth.ms(int(line[5:]))
                process.stdin.write("\n")
                process.stdin.flush()
            elif line.startswith("OUT "):
                return json.loads(line[4:])
            else:
                other.append(line)
        raise AssertionError("the page's code gave no result:\n" + "".join(other))
    finally:
        process.kill()
        process.stdin.close()
        process.stdout.close()
        process.wait()
        shutil.rmtree(folder, ignore_errors=True)


NODE_DRIVER = """
const report = out => process.stdout.write("OUT " + JSON.stringify(out) + "\\n", () => process.exit(0));
(async () => {
%s
})().catch(e => { console.log(e && e.stack || String(e)); process.exit(1); });
"""

FAKE_CLOCK = """
const clock = { now: 0, timers: [], id: 0 };
globalThis.setTimeout = (f, ms = 0) => { clock.timers.push({ id: ++clock.id, at: clock.now + ms, f }); return clock.id; };
globalThis.setInterval = (f, ms) => { clock.timers.push({ id: ++clock.id, at: clock.now + ms, f, every: ms }); return clock.id; };
globalThis.clearTimeout = id => { clock.timers = clock.timers.filter(t => t.id !== id); };
Object.defineProperty(globalThis, "performance", { value: { now: () => clock.now }, configurable: true });
const settle = () => new Promise(r => setImmediate(r));
async function advance(ms) {
  const end = clock.now + ms;
  await settle();
  for (;;) {
    const next = clock.timers.filter(t => t.at <= end).sort((a, b) => a.at - b.at)[0];
    if (!next) break;
    clock.now = next.at;
    if (next.every) next.at += next.every; else clock.timers = clock.timers.filter(t => t !== next);
    next.f();
    await settle();
  }
  clock.now = end;
}
// Runs the clock until a promise has settled; a rejection comes back as {thrown}
async function drive(promise) {
  let done = false, value;
  promise.then(v => { done = true; value = v; }, e => { done = true; value = { thrown: e.message }; });
  for (let i = 0; i < 100000 && !done; i++) await advance(10);
  return value;
}
"""

FAKE_DOM = """
const elements = {};
const fakeElement = id => ({ id, style: {}, hidden: false, textContent: "", innerHTML: "", className: "", checked: false, value: "", disabled: false, firstChild: {},
  classList: { add() {}, remove() {}, toggle() {} }, append() {}, appendChild(x) { return x; }, scrollIntoView() {}, addEventListener() {}, click() {} });
globalThis.document = { hidden: false, body: fakeElement("body"), getElementById: id => elements[id] || (elements[id] = fakeElement(id)),
  querySelector: () => null, querySelectorAll: () => [], createElement: () => fakeElement("") };
"""

# The ports and what's on them: "ours" answers everything, "bootloader" only the version, "silent" nothing
FAKE_MIDI = """
let midi = null;
Object.defineProperty(globalThis, "navigator", { value: { requestMIDIAccess: async () => midi.access }, configurable: true });
function fakeFirmware(d, m) {
  if (m.kind === "silent") return null;
  if (d[0] === 0x08) return [0x09, 0, 0, 9, 4];
  if (m.kind !== "ours") return null;
  if (d[0] === 0x79) return [...d.slice(0, 5), 0, 0, ...(d[4] === 4 ? [5, 0, 3, 8, 4, 7, 3, 1] : [])];
  if (d[0] === 0x54) return [0x54, SETTINGS.length, ...SETTINGS.flatMap(() => [0, 0])];
  if (d[0] === 0x5E) return [0x5E, d[1], d[2], m.kindOfSlot, ...(m.names[d[1] << 7 | d[2]] || [])];
  if (d[0] === 0x59) m.names[d[1] << 7 | d[2]] = d.slice(3);
  if (d[0] === 0x5F) m.routes = [Array(16).fill(null), Array(16).fill(null)]; // a table as before the hold: 0 takes a route at once
  if (d[0] === 0x56) {
    const t = m.routes[d[1]], same = t.findIndex(r => r && r[0] === d[2] && r[1] === d[3]), depth = (d[4] << 7 | d[5]) - 99;
    if (same >= 0) t[same] = depth ? d.slice(2, 6) : null;
    else if (depth) t[t.indexOf(null)] = d.slice(2, 6);
  }
  if (d[0] === 0x57) return [0x57, d[1], ...m.routes[d[1]].flatMap(r => r || [0, 0, 0, 99])];
  if (d[0] === 0x5B) return m.formatAnswer;
  return [0x7F, d[0]];
}
function fakeMidi(kind) {
  const m = { kind, sent: [], names: {}, kindOfSlot: 1, formatAnswer: [0x5B, 1, 0, 0], throwing: false };
  m.input = { name: "PRO 800", type: "input", id: "i", state: "connected", onmidimessage: null };
  m.output = { name: "PRO 800", type: "output", id: "o", state: "connected", send(bytes) {
    if (m.throwing) throw new Error("the port is gone");
    const d = Array.from(bytes).slice(8, -1);
    m.sent.push(d);
    const r = fakeFirmware(d, m);
    if (r) setTimeout(() => { if (m.input.onmidimessage) m.input.onmidimessage({ data: [0xF0, 0, 0x20, 0x32, 0, 1, 0x24, 0, ...r, 0xF7] }); }, 1);
  } };
  m.access = { inputs: new Map([["i", m.input]]), outputs: new Map([["o", m.output]]), onstatechange: null };
  // the ports go away or come back, each with its event
  m.power = state => { for (const p of [m.input, m.output]) { p.state = state; if (m.access.onstatechange) m.access.onstatechange({ port: p }); } };
  return m;
}
"""

FAKE_CHANNEL = """
class FakeChannel { constructor() { this.sent = []; fakeChannels.push(this); } postMessage(d) { this.sent.push(d); if (this.onPost) this.onPost(d); } }
const fakeChannels = [];
Object.defineProperty(globalThis, "BroadcastChannel", { value: FakeChannel, configurable: true });
"""

INSTALLER_STUBS = """
globalThis.window = globalThis;
window.addEventListener = () => {};
globalThis.Worker = class { postMessage([id, ms]) { setTimeout(() => this.onmessage({ data: id }), ms); } };
URL.createObjectURL = () => "blob:worker";
let bankToServe = null;
globalThis.fetch = async name => name === "presets.json" && bankToServe ? { ok: true, json: async () => bankToServe } : { ok: false, status: 404 };
"""


def load_plan_py():
    """presets/lib/plan.py, as a module: its main() runs only as a script."""
    spec = importlib.util.spec_from_file_location("plan", os.path.join(ROOT, "presets", "lib", "plan.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def installer(body, bank=None):
    """install.html whole, then a test's code in its scope; the page has loaded when that starts."""
    prelude = FAKE_CLOCK + FAKE_DOM + FAKE_MIDI + FAKE_CHANNEL + INSTALLER_STUBS + "bankToServe = %s;\n" % json.dumps(bank)
    return run_node(prelude + page_script("install.html") + NODE_DRIVER % ("await advance(0);\n" + body))


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebInstaller(unittest.TestCase):
    """tools/web/install.html against fake ports: a second tab keeps off the synth, and what it checks in a preset bank."""
    RUN = """
    const channel = fakeChannels[0], text = () => $("state").textContent;
    midi = fakeMidi("ours");
    await drive(connect());
    await advance(100);
    """

    def test_a_file_cut_between_two_messages_is_refused(self):
        out = installer("""
        const message = (kind, bytes) => [...HEADER, kind, ...bytes, 0xF7];
        const update = count => { // a header and its blocks, laid out as PRO800_update_gen lays them out
            const size = count * 210, head = new Array(40).fill(0);
            head[1] = size & 127; head[2] = size >> 7 & 127; head[3] = size >> 14 & 127; // bytes 10-12 of the message
            [..."BEHRINGER PRO 800"].forEach((c, i) => { head[15 + i] = c.charCodeAt(0); });
            head[33] = 0; head[34] = 9; head[35] = 4;
            const blocks = [];
            for (let i = 0; i < count; i++) blocks.push(message(0x0B, [0, i & 127, i >> 7, ...new Array(220).fill(0)]));
            return [message(0x0A, head), ...blocks];
        };
        const tryIt = async messages => {
            try { const f = await parseUpdate(Uint8Array.from(messages.flat()).buffer); return "ok " + f.version; }
            catch (e) { return e.message; }
        };
        const whole = update(60);
        const swapped = [...whole]; [swapped[5], swapped[6]] = [swapped[6], swapped[5]];
        report({
            whole: await tryIt(whole),
            cut: await tryIt(whole.slice(0, 50)),
            headerOnly: await tryIt(whole.slice(0, 1)),
            tooSmall: await tryIt(update(10)),
            swapped: await tryIt(swapped),
        });
        """)
        self.assertEqual(out["whole"], "ok 0.9.4")
        self.assertEqual(out["cut"], "it's cut short")
        self.assertEqual(out["headerOnly"], "it isn't a PRO-800 firmware update")
        self.assertEqual(out["tooSmall"], "it's cut short")
        self.assertEqual(out["swapped"], "its data blocks are out of order, or one is missing")

    def test_a_tab_keeps_off_the_synth_while_another_runs(self):
        out = installer(self.RUN + """
        const before = midi.sent.length;
        channel.onPost = d => { if (d === "ask") channel.onmessage({ data: "busy" }); }; // the other tab, alive
        channel.onmessage({ data: "busy" });
        await advance(0);
        const shown = text(), ready = $("choose").hidden;
        midi.power("disconnected"); midi.power("connected"); // the bootloader's port appears
        await advance(30000);
        await refresh();
        const kind = (await identify()).kind;
        state = { kind: "pro8000" };
        await readSlots();
        await advance(30000);
        const during = midi.sent.length - before, afterwards = text();
        channel.onPost = null;
        channel.onmessage({ data: "idle" });
        await advance(3000);
        const tooSoon = midi.sent.length - before;
        await advance(1000);
        report({ shown, ready, kind, during, afterwards, tooSoon, resumed: midi.sent.length - before, end: text(), slots: slots && slots.length, asks: channel.sent.filter(d => d === "ask").length });
        """)
        self.assertIn("Another installer tab", out["shown"])
        self.assertTrue(out["ready"], "nothing to start from here")
        self.assertEqual(out["kind"], "none", "identify doesn't look")
        self.assertEqual(out["during"], 0, "not a message")
        self.assertIn("Another installer tab", out["afterwards"])
        self.assertEqual(out["tooSoon"], 0, "the port gets its rest first")
        self.assertGreater(out["resumed"], 100, "then it looks at the synth again, slots too")
        self.assertIn("PRO-8000", out["end"])
        self.assertEqual(out["slots"], 128)
        self.assertGreater(out["asks"], 5, "it keeps asking while busy")

    def test_a_look_under_way_stops_at_the_next_message(self):
        out = installer("""
        const channel = fakeChannels[0];
        midi = fakeMidi("ours");
        const looking = connect();
        await advance(2); // the version has gone out and been answered
        const before = midi.sent.length;
        channel.onmessage({ data: "busy" });
        await drive(looking);
        report({ before, after: midi.sent.length, text: $("state").textContent, state: state.kind });
        """)
        self.assertGreaterEqual(out["before"], 1)
        self.assertEqual(out["after"], out["before"], "nothing more goes out")
        self.assertIn("Another installer tab", out["text"])
        self.assertEqual(out["state"], "none")

    def test_a_tab_that_goes_away_without_a_word_stops_counting(self):
        out = installer(self.RUN + """
        channel.onmessage({ data: "busy" });
        await advance(10000);
        const still = otherRunning;
        await advance(10000);
        report({ still, after: otherRunning });
        """)
        self.assertTrue(out["still"], "two asks are given their chance")
        self.assertFalse(out["after"])

    def test_a_slow_clock_doesnt_count_as_silence(self):
        out = installer(self.RUN + """
        channel.onPost = d => { if (d === "ask") channel.onmessage({ data: "busy" }); };
        channel.onmessage({ data: "busy" });
        await advance(600000);
        report({ still: otherRunning });
        """)
        self.assertTrue(out["still"])

    def test_only_a_running_tab_answers_and_says_when_it_ends(self):
        out = installer(self.RUN + """
        channel.sent.length = 0;
        channel.onmessage({ data: "ask" });
        const idle = channel.sent.length;
        running = true;
        channel.onmessage({ data: "ask" });
        report({ idle, running: channel.sent, name: tabName });
        """)
        self.assertEqual(out["idle"], 0)
        self.assertEqual(out["running"], ["busy " + out["name"]])

    def test_start_does_nothing_while_another_runs(self):
        out = installer(self.RUN + """
        channel.onPost = d => { if (d === "ask") channel.onmessage({ data: "busy" }); };
        channel.onmessage({ data: "busy" });
        channel.sent.length = 0;
        let went = 0;
        plan = () => [{ label: "a step", run: async () => { went++; } }];
        await drive(start());
        report({ running, went, sent: channel.sent.filter(d => d === "busy").length });
        """)
        self.assertFalse(out["running"])
        self.assertEqual(out["sent"], 0)
        self.assertEqual(out["went"], 0)

    def test_two_tabs_pressing_start_at_once_both_stand_back(self):
        out = installer(self.RUN + """
        plan = () => [{ label: "a step", run: async () => { throw new Error("it went ahead"); } }];
        const before = midi.sent.length;
        const started = start();
        await advance(10);
        channel.onmessage({ data: "busy" }); // the other tab said so inside the wait
        await drive(started);
        report({ running, sent: midi.sent.length - before, said: channel.sent.slice(-2), name: tabName, text: $("state").textContent });
        """)
        self.assertFalse(out["running"])
        self.assertEqual(out["sent"], 0, "the synth wasn't touched")
        self.assertEqual(out["said"], ["busy " + out["name"], "idle " + out["name"]], "of this tab, by name")
        self.assertIn("Another installer tab", out["text"])

    def test_another_tab_standing_back_doesnt_end_a_run_for_this_one(self):
        """Tab a is flashing. Tab b comes out of its save dialog, says busy, sees a and stands back: a is still flashing."""
        out = installer(self.RUN + """
        const before = midi.sent.length;
        channel.onPost = d => { if (d === "ask") channel.onmessage({ data: "busy a" }); };
        channel.onmessage({ data: "busy a" });
        channel.onmessage({ data: "busy b" });
        channel.onmessage({ data: "idle b" });
        await advance(30000);
        const during = midi.sent.length - before, still = otherRunning, shown = $("state").textContent;
        channel.onPost = null;
        channel.onmessage({ data: "idle a" });
        await advance(4000);
        report({ during, still, shown, after: otherRunning, resumed: midi.sent.length - before });
        """)
        self.assertEqual(out["during"], 0, "not a message to a bootloader being flashed")
        self.assertTrue(out["still"])
        self.assertIn("Another installer tab", out["shown"])
        self.assertFalse(out["after"])
        self.assertGreater(out["resumed"], 0)

    def test_a_name_reads_back_as_it_was_sent(self):
        """A name outside 20-7E goes out without those characters, and the read-back must expect the same."""
        out = installer(self.RUN + """
        const result = await drive(installPresets());
        report({ result: result || null, names: midi.names });
        """, bank=[{"slot": 1, "name": "Café Bass"}, {"slot": 2, "name": "Plain"}])
        self.assertIsNone(out["result"], "it read back")
        self.assertEqual(bytes(out["names"]["0"]), b"Caf Bass")
        self.assertEqual(bytes(out["names"]["1"]), b"Plain")

    def test_a_name_that_really_differs_still_fails(self):
        out = installer(self.RUN + """
        midi.names = new Proxy(midi.names, { set: (o, k, v) => { o[k] = v.slice(1); return true; } });
        report({ result: await drive(installPresets()) });
        """, bank=[{"slot": 1, "name": "Plain"}])
        self.assertIn("didn't read back", out["result"]["thrown"])

    def test_the_install_takes_the_init_sounds_routes_off_first(self):
        out = installer(self.RUN + """
        await drive(installPresets());
        report(midi.sent);
        """, bank=[{"slot": 1, "name": "a", "routes": {"1": [["lfo", "vca", 5]]}}])
        start = out.index([0x5F])
        self.assertEqual(out[start + 1:start + 5], [[0x56, l, VELOCITY, d, 0, 99] for l in (0, 1) for d in (VCA, CUTOFF)])
        self.assertIn([0x56, 0, 1, VCA, 0, 104], out[start + 5:])
        self.assertLess(out.index([0x56, 0, 1, VCA, 0, 104]), out.index([0x5D, 0, 0]), "and it saves after")

    def test_a_both_mark_goes_by_the_slot_the_firmware_gave_it(self):
        """The fake firmware's table has no hold, as one older than 0.9.4 that the page runs on with Flash unticked: the list starts
        at slot 0. On this firmware it starts past the init sound's two, or doesn't if they've gone: the same message reads both."""
        out = installer(self.RUN + """
        const result = await drive(installPresets());
        report({ result: result || null, lo: SET_ID.routeBoth1Lo, hi: SET_ID.routeBoth1Hi, sent: midi.sent });
        """, bank=[{"slot": 1, "name": "a", "routes": {"1": [["lfo", "vca", 5, "both"], ["env3", "resonance", 9]]}}])
        self.assertIsNone(out["result"])
        self.assertIn([0x7D, out["lo"], 0, 0b1], out["sent"], "the first route's slot, 0, marked, and nothing else")
        self.assertLess(out["sent"].index([0x57, 0]), out["sent"].index([0x7D, out["lo"], 0, 0b1]), "read before it's marked")
        self.assertLess(out["sent"].index([0x7D, out["hi"], 0, 0]), out["sent"].index([0x5D, 0, 0]), "and marked before the save")

    def test_a_bank_with_a_slot_out_of_range_isnt_offered(self):
        for slot in (0, 129, 1.5, "7", None):
            with self.subTest(slot=slot):
                out = installer("""
                report({ bank, problem: bankProblem });
                """, bank=[{"slot": 1, "name": "Good"}, {"slot": slot, "name": "Bad one"}])
                self.assertIsNone(out["bank"])
                self.assertIn("1-128", out["problem"])
                self.assertIn("Bad one", out["problem"])
        out = installer("report({ count: bankList().length, problem: bankProblem });", bank=[{"slot": 1, "name": "a"}, {"slot": 128, "name": "b"}])
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["problem"], "")

    def test_the_format_reply(self):
        out = installer(self.RUN + """
        const outcome = async answer => { midi.formatAnswer = answer; const r = await drive(formatMemory()); return r ? r.thrown : $("log").textContent.trim().split("\\n").pop(); };
        report({ now: await outcome([0x5B, 1, 0, 0]), old: await outcome([0x5B, 0, 0x80, 0]), failed: await outcome([0x5B, 1, 0, 1]), none: await outcome(null) });
        """)
        self.assertTrue(out["now"].endswith("memory formatted: 128 slots"), out["now"])
        self.assertTrue(out["old"].endswith("memory formatted: 128 slots"), out["old"])
        self.assertIn("write error", out["failed"])
        self.assertIn("didn't answer", out["none"])

    def test_a_slot_that_isnt_ours_is_named_for_what_it_may_be(self):
        out = installer(self.RUN + """
        state = { kind: "pro8000", memory: "ours" };
        slots = Array.from({ length: 128 }, (_, n) => n < 2 ? 3 : n < 5 ? 1 : 0);
        report({ text: memoryText() });
        """)
        self.assertEqual(out["text"], "3 patches, 123 empty slots, 2 stock or unreadable.")


EDITOR_BLOCKS = ["const HEADER", "function offline(", "function send(", "function request(", "function onMessage(",
                 "let installerBusy", "const RECONNECT_QUIET", "const portSeen", "const pro800Port", "function letGoOfSynth(", "function installerChanged(",
                 "function scheduleReconnect(", "function portChanged(", "function watchPorts(", "async function connect("]
EDITOR_STUBS = """
let input = null, output = null, waiters = [], midiAccess = null, firmwareVersion = "", tab = "sound";
let slots = [{ kind: 1 }], seqFor = "old", patchNameFor = 3, connected = 0, paintedSlots = 0;
const fillRouteInputs = () => {}, paintSlots = () => { paintedSlots++; }, afterConnect = () => { connected++; };
const refreshSettings = async () => !!await request([0x54], [0x54], 1000), refreshParams = async () => {};
const status = () => document.getElementById("status").textContent;
"""


def editor(body):
    """The editor's MIDI code, cut out of index.html, with its poll, on fake ports."""
    page = source("tools", "web", "index.html")
    poll = page[page.index("let polling = false;"):page.index("}, 900);") + len("}, 900);")]
    prelude = FAKE_CLOCK + FAKE_DOM + FAKE_MIDI + source("tools", "web", "sound-plan.js") + EDITOR_STUBS
    code = "\n".join(js_block(page, b) for b in EDITOR_BLOCKS) + "\n" + poll + "\n"
    helpers = """
    const channel = { sent: [], postMessage(d) { this.sent.push(d); if (this.onPost) this.onPost(d); } };
    const watch = watchInstaller(channel, installerChanged);
    const commands = () => midi.sent.map(d => d[0]);
    """
    return run_node(prelude + code + NODE_DRIVER % (helpers + body))


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebReconnect(unittest.TestCase):
    """index.html comes back by itself to a PRO 800, and stays off the bootloader's port and an installer's way."""
    UP = """
    midi = fakeMidi("ours");
    await drive(connect());
    const first = status(), firstCommands = commands();
    midi.sent.length = 0;
    await advance(100);
    """

    def test_a_power_cycle_brings_it_back_after_three_quiet_seconds(self):
        out = editor(self.UP + """
        midi.power("disconnected");
        const gone = status(), offlineThen = output === null;
        midi.sent.length = 0;
        await advance(10000);
        midi.power("connected");
        await advance(2000);
        midi.power("disconnected");
        midi.power("connected"); // macOS can drop the port and set it up again right after it appears
        await advance(2900);
        const tooSoon = commands();
        await advance(200);
        await advance(100);
        report({ first, firstCommands, gone, offlineThen, tooSoon, then: commands().slice(0, 2), back: status(), connected, slots, seqFor, patchNameFor });
        """)
        self.assertIn("Connected: firmware 0.9.4", out["first"])
        self.assertEqual(out["firstCommands"], [0x08, 0x79])
        self.assertEqual(out["gone"], "Disconnected")
        self.assertTrue(out["offlineThen"])
        self.assertEqual(out["tooSoon"], [], "the port hasn't rested")
        self.assertEqual(out["then"], [0x08, 0x79])
        self.assertIn("Connected: firmware 0.9.4", out["back"])
        self.assertEqual(out["connected"], 2)
        self.assertEqual(out["slots"], [], "what it read before the power cycle is gone")
        self.assertIsNone(out["seqFor"])
        self.assertEqual(out["patchNameFor"], -1)

    def test_the_poll_runs_again_once_it_is_back(self):
        out = editor(self.UP + """
        midi.power("disconnected");
        midi.power("connected");
        midi.sent.length = 0;
        await advance(8000);
        report({ polls: commands().filter(c => c === 0x54).length });
        """)
        self.assertGreater(out["polls"], 2)

    def test_it_keeps_off_the_bootloaders_port(self):
        """Two messages say what's there, and after them nothing, however long it stays."""
        out = editor(self.UP + """
        midi.power("disconnected");
        midi.kind = "bootloader";
        midi.sent.length = 0;
        midi.power("connected");
        await advance(60000);
        const onThePort = output;
        report({ commands: commands(), text: status(), onThePort, connected, handler: midi.input.onmidimessage });
        """)
        self.assertEqual(out["commands"], [0x08, 0x79])
        self.assertIn("bootloader", out["text"])
        self.assertIsNone(out["onThePort"])
        self.assertEqual(out["connected"], 1, "only the first time, on our firmware")
        self.assertIsNone(out["handler"])

    def test_loading_or_connecting_on_the_bootloader_stays_off_it_too(self):
        out = editor("""
        midi = fakeMidi("bootloader");
        await drive(connect());
        await advance(30000);
        report({ commands: commands(), output, text: status() });
        """)
        self.assertEqual(out["commands"], [0x08, 0x79])
        self.assertIsNone(out["output"])
        self.assertIn("bootloader", out["text"])

    def test_opening_a_port_isnt_a_change_to_react_to(self):
        """Opening fires statechange with the state unchanged: a page that took that for a new device would ask again."""
        out = editor("""
        midi = fakeMidi("bootloader");
        await drive(connect());
        midi.sent.length = 0;
        midi.access.onstatechange({ port: midi.input }); // connection "open", state still "connected"
        midi.access.onstatechange({ port: midi.output });
        await advance(30000);
        report({ commands: commands() });
        """)
        self.assertEqual(out["commands"], [])

    def test_a_silent_port_gets_one_try(self):
        out = editor("""
        midi = fakeMidi("silent");
        await drive(connect());
        await advance(30000);
        report({ commands: commands(), output, text: status() });
        """)
        self.assertEqual(out["commands"], [0x08])
        self.assertIsNone(out["output"])
        self.assertIn("didn't answer", out["text"])

    def test_the_synth_comes_later_than_the_page(self):
        out = editor("""
        midi = fakeMidi("ours");
        midi.input.state = midi.output.state = "disconnected";
        await drive(connect());
        const early = status();
        midi.power("connected");
        await advance(3200);
        report({ early, later: status(), connected });
        """)
        self.assertIn("no MIDI port", out["early"])
        self.assertIn("Connected", out["later"])
        self.assertEqual(out["connected"], 1)

    def test_a_dead_port_of_the_same_name_isnt_taken(self):
        out = editor("""
        midi = fakeMidi("ours");
        const dead = { name: "PRO 800", type: "output", id: "dead", state: "disconnected", send() { throw new Error("dead"); } };
        midi.access.outputs = new Map([["dead", dead], ["o", midi.output]]);
        await drive(connect());
        report({ text: status() });
        """)
        self.assertIn("Connected", out["text"])

    def test_it_stays_off_while_an_installer_runs(self):
        out = editor(self.UP + """
        channel.onPost = d => { if (d === "ask") watch.heard("busy"); };
        watch.heard("busy");
        const told = status(), held = output;
        midi.sent.length = 0;
        midi.power("disconnected");
        midi.kind = "bootloader";
        midi.power("connected");
        await advance(10000);
        await drive(connect());
        midi.power("disconnected");
        midi.kind = "ours";
        midi.power("connected");
        await advance(60000);
        const during = commands(), still = status();
        channel.onPost = null;
        watch.heard("idle");
        const done = status();
        await advance(2900);
        const tooSoon = commands();
        await advance(300);
        report({ told, held, during, still, done, tooSoon, then: commands().slice(0, 2), back: status() });
        """)
        self.assertIn("installer is running", out["told"])
        self.assertIsNone(out["held"])
        self.assertEqual(out["during"], [], "not a message, whatever the ports do")
        self.assertIn("installer is running", out["still"])
        self.assertIn("installer has finished", out["done"])
        self.assertEqual(out["tooSoon"], [])
        self.assertEqual(out["then"], [0x08, 0x79])
        self.assertIn("Connected", out["back"])

    def test_a_reconnect_waiting_when_the_installer_starts_is_called_off(self):
        out = editor(self.UP + """
        midi.power("disconnected");
        midi.power("connected");
        await advance(1000);
        channel.onPost = d => { if (d === "ask") watch.heard("busy"); };
        midi.sent.length = 0;
        watch.heard("busy");
        await advance(30000);
        report({ commands: commands() });
        """)
        self.assertEqual(out["commands"], [])

    def test_an_installer_starting_in_the_middle_of_connecting_stops_it(self):
        out = editor("""
        midi = fakeMidi("ours");
        const going = connect();
        await settle(); await settle();
        const asked = commands();
        watch.heard("busy");
        await drive(going);
        await advance(100);
        report({ asked, all: commands(), output, text: status(), connected });
        """)
        self.assertEqual(out["asked"], [0x08])
        self.assertEqual(out["all"], [0x08], "the EEPROM isn't asked for")
        self.assertIsNone(out["output"])
        self.assertIn("installer is running", out["text"])
        self.assertEqual(out["connected"], 0)

    def test_a_tab_standing_back_doesnt_let_it_back_while_another_runs(self):
        out = editor(self.UP + """
        channel.onPost = d => { if (d === "ask") watch.heard("busy a"); };
        watch.heard("busy a");
        midi.sent.length = 0;
        watch.heard("busy b");
        watch.heard("idle b");
        const told = status();
        await advance(30000);
        const during = commands(), held = installerBusy;
        channel.onPost = null;
        watch.heard("idle a");
        await advance(3200);
        report({ told, during, held, busy: installerBusy, then: commands().slice(0, 2), back: status() });
        """)
        self.assertIn("installer is running", out["told"])
        self.assertEqual(out["during"], [], "not a message")
        self.assertTrue(out["held"], "and it goes on asking, so that it hears the real end")
        self.assertFalse(out["busy"])
        self.assertEqual(out["then"], [0x08, 0x79])
        self.assertIn("Connected", out["back"])

    def test_two_pressing_start_at_once_the_one_that_goes_ahead_still_counts(self):
        out = editor(self.UP + """
        channel.onPost = d => { if (d === "ask") watch.heard("busy b"); }; // b goes ahead; a stands back
        watch.heard("busy a");
        watch.heard("busy b");
        midi.sent.length = 0;
        watch.heard("idle a");
        await advance(30000);
        report({ during: commands(), busy: installerBusy });
        """)
        self.assertEqual(out["during"], [])
        self.assertTrue(out["busy"])

    def test_an_idle_from_a_tab_it_never_heard_changes_nothing(self):
        out = editor(self.UP + """
        channel.onPost = d => { if (d === "ask") watch.heard("busy a"); };
        watch.heard("busy a");
        watch.heard("idle b");
        report({ busy: installerBusy, text: status() });
        """)
        self.assertTrue(out["busy"])
        self.assertIn("installer is running", out["text"])

    def test_a_tab_that_vanished_stops_counting_while_another_still_runs(self):
        out = editor(self.UP + """
        channel.onPost = d => { if (d === "ask") watch.heard("busy b"); };
        watch.heard("busy a"); // says no more
        watch.heard("busy b");
        await advance(30000);
        const still = installerBusy;
        channel.onPost = null;
        midi.sent.length = 0;
        watch.heard("idle b");
        const after = installerBusy;
        report({ still, after });
        """)
        self.assertTrue(out["still"])
        self.assertFalse(out["after"], "a was given up on, so b's end is the end")

    def test_an_installer_tab_that_vanished_lets_it_back(self):
        out = editor(self.UP + """
        midi.sent.length = 0;
        watch.heard("busy");
        await advance(7000);
        const still = installerBusy;
        await advance(9000);
        report({ still, busy: installerBusy, commands: commands().slice(0, 2) });
        """)
        self.assertTrue(out["still"])
        self.assertFalse(out["busy"])
        self.assertEqual(out["commands"], [0x08, 0x79])

    def test_a_page_opened_during_an_install_connects_after_it(self):
        out = editor("""
        midi = fakeMidi("ours");
        watch.heard("busy");
        await drive(connect());
        const during = commands();
        watch.heard("idle");
        await advance(3200);
        report({ during, text: status() });
        """)
        self.assertEqual(out["during"], [])
        self.assertIn("Connected", out["text"])

    def test_without_ever_connecting_it_doesnt_ask_for_midi(self):
        out = editor("""
        let asked = 0;
        navigator.requestMIDIAccess = async () => { asked++; };
        watch.heard("busy");
        watch.heard("idle");
        await advance(10000);
        report({ asked, text: status() });
        """)
        self.assertEqual(out["asked"], 0)
        self.assertIn("click Connect", out["text"])

    def test_a_send_that_throws_leaves_no_waiter(self):
        out = editor(self.UP + """
        midi.throwing = true;
        const failed = await request([0x54], [0x54], 1000);
        const left = waiters.length;
        midi.throwing = false;
        const reply = await drive(request([0x54], [0x54], 1000));
        report({ failed, left, reply: reply && reply[0] });
        """)
        self.assertIsNone(out["failed"], "a null, as for no reply: every caller knows that")
        self.assertEqual(out["left"], 0)
        self.assertEqual(out["reply"], 0x54, "the next reply goes to the one who asked")

    def test_the_poll_survives_a_port_going_away(self):
        out = editor(self.UP + """
        midi.throwing = true;
        await advance(5000);
        midi.throwing = false;
        midi.sent.length = 0;
        await advance(2000);
        report({ commands: commands(), waiters: waiters.length });
        """)
        self.assertIn(0x54, out["commands"], "polling isn't stuck")

    def test_a_step_timer_with_the_sequence_gone(self):
        page = source("tools", "web", "index.html")
        code = FAKE_CLOCK + js_block(page, "const dirtySteps") + "\n" + js_block(page, "let stepTimer") + "\n" + js_block(page, "function sendStep(")
        out = run_node("let seq = { steps: [{ note: 64, vel: 0, gate: 50, on: 1, hold: 0, extra: 0, lane: [64, 64, 64, 64] }] };\nconst offline = () => 0, paintAll = () => {}, send = () => {};\n" + code + NODE_DRIVER % """
        sendStep(0);
        seq = null;
        await advance(50);
        report({ left: dirtySteps.size });
        """)
        self.assertEqual(out["left"], 0)


RESTORE_BLOCKS = ["function slotLabel(", "async function slotInfo(", "function scanSummary(", "async function scanSlots(", "async function readBytes(", "async function writeBytes(",
                  "function recordChecksumOk(", "async function mayOverwrite(", "const quotedName", "const needsFormatFirst", "async function markMemoryOurs(",
                  "async function restoreEverything("]
ASK_THE_SIMULATOR = """
const header = 8, answers = [];
require("readline").createInterface({ input: process.stdin }).on("line", line => answers.shift()(line));
let deaf = false, sent = [], confirmAnswer = true, confirms = [], slots = [], patchMsg = "";
async function ask(data, prefix) {
  sent.push(data);
  if (deaf) return null;
  const line = await new Promise(r => { answers.push(r); process.stdout.write("REQ " + Buffer.from(data).toString("hex") + "\\n"); });
  const out = Array.from(Buffer.from(line, "hex"));
  for (let start = out.indexOf(0xF0); start >= 0; start = out.indexOf(0xF0, start + 1)) {
    const d = out.slice(start + header, out.indexOf(0xF7, start));
    if (prefix.every((b, i) => d[i] === b)) return d;
  }
  return null;
}
const ack = (data, ms) => ask(data, [0x7F, data[0]], ms);
const confirm = q => { confirms.push(q); return confirmAnswer; };
const offline = () => 0, paintSlots = () => {};
const msg = (id, text, kind = "") => { if (id === "patch-msg") patchMsg = text; };
"""


def editor_on_the_firmware(synth, body):
    """The Patches tab's code, cut out of index.html, talking to the simulated firmware."""
    page = source("tools", "web", "index.html")
    code = source("tools", "web", "sound-plan.js") + ASK_THE_SIMULATOR + "\n".join(js_block(page, b) for b in RESTORE_BLOCKS)
    return run_node(code + NODE_DRIVER % body, synth)


BACKUP = None


def a_backup():
    """Back up everything's file: one patch in P3, named, with the rest empty."""
    global BACKUP
    if BACKUP is None:
        path = os.path.join(tempfile.mkdtemp(prefix="pro800sim"), "eeprom.bin")
        shutil.copy(formatted_memory(), path)
        s = Synth(path)
        s.ms(1600)
        s.request(0x5D, 0, 2)
        s.rename(2, "Kept")
        BACKUP = s.memory()[:128 * 768]
        s.close()
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    return BACKUP


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebRestore(PanelTest):
    """Restore everything on the editor's Patches tab, against the firmware's own replies."""
    BLOCK = 0x1F400

    def blank_memory(self):
        """All zero and never formatted: every slot reads empty, and no global block."""
        self.s.close()
        with open(self.path, "wb") as f:
            f.write(bytes(0x20000))
        self.s = Synth(self.path)
        self.s.ms(1600)

    def restore(self):
        data = base64.b64encode(a_backup()).decode()
        return editor_on_the_firmware(self.s, """
        await restoreEverything({ name: "backup.bin", arrayBuffer: async () => Uint8Array.from(Buffer.from("%s", "base64")).buffer });
        report({ message: patchMsg, confirms, settingWrites: sent.filter(d => d[0] === 0x7D), writes: sent.filter(d => d[0] === 0x5A).length });
        """ % data)

    def test_on_never_formatted_memory_it_marks_the_memory_ours(self):
        self.blank_memory()
        self.assertNotEqual(self.s.memory()[self.BLOCK:self.BLOCK + 4], b"P8G1")
        self.assertEqual(self.s.patch_info(2)[0], 0)
        pickup = self.s.settings()["pickup"]
        out = self.restore()
        self.assertIn("restored backup.bin", out["message"])
        self.assertEqual(self.s.memory()[self.BLOCK:self.BLOCK + 4], b"P8G1", "the file holds no block: this is the restore's doing")
        self.assertEqual(self.s.settings()["pickup"], pickup, "and it changed no setting")
        self.assertEqual(self.s.patch_info(2), (1, "Kept"))
        # what the block is for: the firmware keeps the last patch now
        self.s.request(0x5C, 0, 2)
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 2)
        self.assertEqual(self.s.patch_info(2), (1, "Kept"))

    def test_on_formatted_memory_it_leaves_the_settings_alone(self):
        out = self.restore()
        self.assertIn("restored backup.bin", out["message"])
        self.assertEqual(out["settingWrites"], [], "the block is there already")
        self.assertEqual(self.s.patch_info(2), (1, "Kept"))

    def test_a_second_restore_writes_nothing(self):
        self.restore()
        out = self.restore()
        self.assertEqual(out["writes"], 0)
        self.assertIn("0 slots changed", out["message"])


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebRestoreOnStock(PanelTest):
    MEMORY = "stock"

    def test_it_asks_for_a_format_first_and_writes_nothing(self):
        before = self.s.memory()
        self.assertEqual(self.s.patch_info(0)[0], 3, "what the firmware says for a stock record: not 2")
        data = base64.b64encode(a_backup()).decode()
        out = editor_on_the_firmware(self.s, """
        await restoreEverything({ name: "backup.bin", arrayBuffer: async () => Uint8Array.from(Buffer.from("%s", "base64")).buffer });
        report({ message: patchMsg, confirms, writes: sent.filter(d => d[0] === 0x5A || d[0] === 0x7D).length });
        """ % data)
        self.assertIn("Format the patch memory first", out["message"])
        self.assertEqual(out["confirms"], [], "it doesn't get as far as asking")
        self.assertEqual(out["writes"], 0)
        self.assertEqual(self.s.memory()[:128 * 768], before[:128 * 768])

    def test_the_scan_says_what_those_slots_may_be(self):
        out = editor_on_the_firmware(self.s, """
        await scanSlots();
        report({ message: patchMsg });
        """)
        self.assertEqual(out["message"], "0 patches, 0 empty, 128 stock or unreadable")


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebSaveInASlot(PanelTest):
    """Save here, Import and the JSON box's Save look at the slot itself, not at the last scan."""

    def test_a_slot_saved_since_the_scan_gets_the_question(self):
        self.s.request(0x5D, 0, 2)
        self.s.rename(2, "Since")
        out = editor_on_the_firmware(self.s, """
        slots[2] = { kind: 0, name: "" }; // the last scan's view
        const may = await mayOverwrite(2, now => "Replace" + quotedName(now));
        report({ may, confirms, asks: sent.length, what: sent[0], slot: slots[2] });
        """)
        self.assertTrue(out["may"])
        self.assertEqual(out["confirms"], ['Replace "Since"'])
        self.assertEqual(out["asks"], 1, "one 5E")
        self.assertEqual(out["what"], [0x5E, 0, 2])
        self.assertEqual(out["slot"]["kind"], 1, "and the list learns it")

    def test_an_answer_of_no_stands(self):
        self.s.request(0x5D, 0, 2)
        out = editor_on_the_firmware(self.s, """
        confirmAnswer = false;
        report({ may: await mayOverwrite(2, () => "q") });
        """)
        self.assertFalse(out["may"])

    def test_a_slot_emptied_since_the_scan_goes_without_one(self):
        out = editor_on_the_firmware(self.s, """
        slots[2] = { kind: 1, name: "Gone" };
        report({ may: await mayOverwrite(2, () => "q"), confirms });
        """)
        self.assertTrue(out["may"])
        self.assertEqual(out["confirms"], [])

    def test_no_answer_means_asking(self):
        out = editor_on_the_firmware(self.s, """
        deaf = true;
        slots[2] = { kind: 0, name: "" };
        const may = await mayOverwrite(2, now => "q" + quotedName(now));
        report({ may, confirms, slot: slots[2] });
        """)
        self.assertTrue(out["may"])
        self.assertEqual(out["confirms"], ["q"])

    def test_all_three_go_through_it(self):
        page = source("tools", "web", "index.html")
        self.assertEqual(page.count("await mayOverwrite("), 3)
        self.assertNotIn("info.kind !== 0 && !confirm", page)
        self.assertNotIn("!info || info.kind !== 0", page)


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebMarkingTheMemory(unittest.TestCase):
    def mark(self, pickup, count=None):
        """markMemoryOurs() against scripted replies: no global block until a second pickup write has gone out."""
        n = len(pro800.SETTING_NAMES) if count is None else count
        page = source("tools", "web", "index.html")
        return run_node(source("tools", "web", "sound-plan.js") + """
        const sent = [];
        const pickupId = SET_ID.pickup, count = %d, pickup = %d;
        const ask = async (data, prefix) => {
          sent.push(data);
          if (data[0] === 0x54) { const d = [0x54, count]; for (let i = 0; i < count; i++) d.push(0, i === pickupId ? pickup : 0); return d; }
          if (data[0] === 0x79) { const writes = sent.filter(x => x[0] === 0x7D).length, m = writes >= 2 ? [5, 0, 3, 8, 4, 7, 3, 1] : [0, 0, 0, 0, 0, 0, 0, 0]; return [...data, 0, 0, ...m]; }
          return [0x7F, data[0]];
        };
        const ack = (data, ms) => ask(data, [0x7F, data[0]], ms);
        """ % (n, pickup) + js_block(page, "async function readBytes(") + "\n" + js_block(page, "async function markMemoryOurs(") + NODE_DRIVER % """
        const marked = await markMemoryOurs();
        report({ marked, writes: sent.filter(d => d[0] === 0x7D).map(d => d.slice(2)), id: pickupId });
        """)

    def test_pickup_goes_there_and_back(self):
        out = self.mark(1)
        self.assertTrue(out["marked"])
        self.assertEqual(out["writes"], [[0, 0], [0, 1]])
        out = self.mark(0)
        self.assertTrue(out["marked"])
        self.assertEqual(out["writes"], [[0, 1], [0, 0]], "off stays off")

    def test_a_firmware_without_the_setting_says_so(self):
        out = self.mark(1, count=pro800.SETTING_NAMES.index("pickup") + 4)
        self.assertFalse(out["marked"])
        self.assertEqual(out["writes"], [])


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebSmallThings(unittest.TestCase):
    def test_a_bank_with_an_entry_that_isnt_a_preset_is_refused_whatever_the_entry(self):
        """null and 0 are falsy: a check on what badSlotPreset() returns has to tell them from "none"."""
        page = source("tools", "web", "index.html")
        out = run_node(source("tools", "web", "sound-plan.js") + """
        let settings = {}, shown = [], looked = 0, slots = [];
        const bankMsg = text => shown.push(text), refreshSettings = async () => { looked++; }, scanSlots = async () => {}, confirm = () => false;
        """ + js_block(page, "async function installBank(") + NODE_DRIVER % """
        const out = [];
        for (const bank of [[null], [0], [""], [false], { presets: [null] }, [{ slot: 1 }, 0], [{ slot: 129 }]]) {
          shown = []; looked = 0;
          await installBank(bank, "the file");
          out.push({ bank, shown, looked });
        }
        shown = [];
        await installBank([{ slot: 1 }], "the file");
        report({ out, good: shown, looked });
        """)
        for case in out["out"]:
            with self.subTest(bank=case["bank"]):
                self.assertEqual(case["looked"], 0, "stopped before anything was asked of the synth")
                self.assertEqual(len(case["shown"]), 1)
                self.assertIn("isn't a preset bank", case["shown"][0])
        self.assertEqual(out["looked"], 1, "a good one goes on")

    def test_release_velocity_on_older_firmware_says_so(self):
        """#8: source 29 is 0.10.0's. To older firmware the page says that, not that the layer's 16 routes are taken."""
        page = source("tools", "web", "index.html")
        blocks = ["const FEATURES", "const feature", "const SOURCES", "const VIA_SOURCE", "const ROUTES", "const routesFull",
                  "const routesTaken", "const RELEASE_VELOCITY_SOURCE", "const sourceTooNew", "async function addRoute("]
        out = run_node(NODE_DRIVER % ("""
        let settings = {}, paramsLayer = 0, layerRoutes = [], sent = [], takes = false;
        const setRoute = async (...a) => { sent.push(a); return takes; }, setRouteVia = async (...a) => { sent.push(a); return takes; };
        """ + chr(10).join(js_block(page, b) for b in blocks) + """
        const out = { old: await addRoute(29, 3, 40, 100), oldVia: await addRoute(25, 3, 40, VIA_SOURCE + 29), sentOld: sent.length };
        out.refused = await addRoute(4, 3, 40, 100);
        layerRoutes = Array(16).fill({ depth: 10 });
        out.full = await addRoute(4, 3, 40, 100);
        settings = { initPatch: 0 };
        takes = true;
        out.now = await addRoute(29, 3, 40, VIA_SOURCE + 29);
        out.sent = sent.length;
        report(out);
        """))
        self.assertEqual(out["old"], "Release velocity (key up) needs firmware 0.10.0 or later")
        self.assertEqual(out["oldVia"], out["old"], "as a Via too")
        self.assertEqual(out["sentOld"], 0, "nothing asked of the synth")
        self.assertEqual(out["refused"], "The synth didn't take that route", "free slots: not 'all in use'")
        self.assertIn("are in use", out["full"])
        self.assertEqual(out["now"], "", "0.10.0 takes it")
        self.assertEqual(out["sent"], 4, "the two refused, then the route and its Via")

    def test_a_knob_edit_sends_52_then_6b_and_plans_never_6b(self):
        page = source("tools", "web", "index.html")
        out = run_node(NODE_DRIVER % ("""
        let params = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], sent = [];
        const offline = () => false, touch = () => {}, NAMES = [], format = () => "", label = i => `#${i}`;
        const paramLayer = i => i === 8 ? 0 : 1;
        const send = m => sent.push(m);
        """ + js_block(page, "const editedAt") + js_block(page, "function setParam(") + """
        setParam(8, 0.5); setParam(8, 0.6);
        await new Promise(r => setTimeout(r, 60));
        report(sent);
        """))
        q = round(0.6 * 16383)
        self.assertEqual(out, [[0x52, 0, 8, q >> 7, q & 0x7F], [0x6B, 1, 8]], "the last value, then show it on layer 1's rule")
        self.assertNotIn("0x6B", source("tools", "web", "sound-plan.js"), "a preset plan leaves the display alone")

    def test_a_drag_goes_out_as_it_moves_and_ends_on_its_value(self):
        """A pointer drag calls setParam once a frame (about 16 ms): a throttle sends every 25 ms meanwhile, a debounce
        would wait for the drag to rest, and the synth's display (6B) would jump at the end."""
        page = source("tools", "web", "index.html")
        out = run_node(FAKE_CLOCK + NODE_DRIVER % ("""
        let params = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], sent = [];
        const offline = () => false, touch = () => {}, NAMES = [], format = () => "", label = i => `#${i}`;
        const paramLayer = i => 0, send = m => { if (m[0] === 0x52) sent.push({ at: clock.now, q: m[3] << 7 | m[4] }); };
        """ + js_block(page, "const editedAt") + js_block(page, "function setParam(") + """
        for (let f = 0; f <= 30; f++) { setParam(8, f / 30); await advance(16); }
        const during = sent.length;
        await advance(100);
        report({ during, sent });
        """))
        self.assertGreaterEqual(out["during"], 15, "about every 25 ms of a 500 ms drag")
        self.assertEqual(out["sent"][-1]["q"], 16383, "the last value goes out")
        gaps = [b["at"] - a["at"] for a, b in zip(out["sent"], out["sent"][1:])]
        self.assertGreaterEqual(min(gaps), 25, "never faster than the throttle")

    def test_the_init_buttons(self):
        page = source("tools", "web", "index.html")
        self.assertRegex(page[page.index("<header>"):page.index("</header>")], r'<button[^>]*id="init-top"[^>]*>Init</button>')
        out = run_node(NODE_DRIVER % ("""
        let patchName = "Pad", seqFor = "1:70", calls = [], answer = true, settings = null, theirs = 1;
        const feature = name => !settings || settings[name] !== undefined;
        const ack = async (m, t) => { calls.push(m); return answer; };
        const ask = async (m, prefix, t) => { calls.push(m); return answer ? [0x5F, theirs] : null; };
        const refreshSettings = async () => { calls.push(["settings", seqFor]); }, refreshParams = async () => { calls.push("params"); };
        """ + js_block(page, "let initWasTheirs") + js_block(page, "async function loadInitSound(") + """
        const out = { ok: await loadInitSound(), name: patchName, calls };
        answer = false; calls = []; patchName = "Pad"; seqFor = "1:70";
        out.failed = { ok: await loadInitSound(), name: patchName, seq: seqFor, calls };
        answer = true; calls = []; settings = { initPatch: 5 };  // 0.10.0: what Perf loads
        out.perf = { ok: await loadInitSound(), theirs: initWasTheirs, calls };
        theirs = 0; calls = [];
        out.builtIn = { ok: await loadInitSound(), theirs: initWasTheirs };
        report(out);
        """))
        self.assertEqual(out["calls"], [[0x5F], ["settings", None], "params"], "the page reads the sound back, and the sequence that's gone")
        self.assertEqual((out["ok"], out["name"]), (True, "Init"))
        self.assertEqual(out["failed"], {"ok": False, "name": "Pad", "seq": "1:70", "calls": [[0x5F]]}, "no answer: nothing changes")
        self.assertEqual(out["perf"], {"ok": True, "theirs": True, "calls": [[0x5F, 1], ["settings", None], "params"]})
        self.assertEqual(out["builtIn"], {"ok": True, "theirs": False}, "no init patch set: the reply says 0")

    def test_the_patch_arrows(self):
        page = source("tools", "web", "index.html")
        header = page[page.index("<header>"):page.index("</header>")]
        self.assertLess(header.index('class="screen"'), header.index('id="patch-prev"'))
        self.assertLess(header.index('id="patch-prev"'), header.index('id="patch-next"'))
        out = run_node(FAKE_CLOCK + """
        let settings = { patch: 70 }, patchNameFor = 70, sent = [], read = 0, isOffline = false, shown = [], failed = [], answers = [], release = null;
        const offline = () => isOffline, slotLabel = n => "P" + (n + 1), touch = (l, v) => shown.push(v), noAnswer = w => failed.push(w);
        let last = 0;
        const ack = async m => { sent.push(m.slice()); last = m[1] << 7 | m[2]; if (release) await new Promise(r => { release = r; }); return answers.length ? answers.shift() : true; };
        const refreshSettings = async () => { read++; settings.patch = last; }, refreshParams = async () => {};
        """ + js_block(page, "let steppedTo") + "\n" + js_block(page, "function stepPatch(") + "\n" + js_block(page, "async function loadStepped(") + NODE_DRIVER % """
        const out = {};
        stepPatch(1); await advance(79); out.waits = sent.length; await advance(2); await settle();
        out.next = sent.splice(0); out.nameFor = patchNameFor; out.read = read;
        for (let i = 0; i < 3; i++) { stepPatch(1); await advance(30); }
        await advance(100); await settle();
        out.run = sent.splice(0); out.shown = shown.slice(-3);
        settings.patch = 0; stepPatch(-1); await advance(100); await settle();
        out.down = sent.splice(0);
        settings.patch = 127; stepPatch(1); await advance(100); await settle();
        out.up = sent.splice(0);
        settings.patch = 9; answers = [false]; stepPatch(1); await advance(100); await settle();
        out.damaged = sent.splice(0); out.failed = failed; stepPatch(1); await advance(100); await settle();
        out.past = sent.splice(0);
        release = () => {}; settings.patch = 20; stepPatch(1); await advance(100); await settle();
        stepPatch(1); await advance(100); await settle();
        out.during = sent.splice(0); const r = release; release = null; r(); await settle(); await advance(100); await settle();
        out.after = sent.splice(0);
        isOffline = true; stepPatch(1); await advance(100); await settle();
        out.offline = sent.splice(0);
        report(out);
        """)
        self.assertEqual(out["waits"], 0, "the load waits for the rest")
        self.assertEqual(out["next"], [[0x5C, 0, 71]])
        self.assertEqual((out["nameFor"], out["read"]), (-1, 1), "the new name, and the page reads the sound back")
        self.assertEqual(out["run"], [[0x5C, 0, 74]], "a run of clicks loads where it ends")
        self.assertEqual(out["shown"], ["P73", "P74", "P75"], "and shows where it is at once")
        self.assertEqual(out["down"], [[0x5C, 0, 127]], "P1 goes round to P128")
        self.assertEqual(out["up"], [[0x5C, 0, 0]], "P128 goes round to P1")
        self.assertEqual((out["damaged"], out["failed"]), ([[0x5C, 0, 10]], ["P11"]))
        self.assertEqual(out["past"], [[0x5C, 0, 11]], "the next click gets past a slot that won't load")
        self.assertEqual(out["during"], [[0x5C, 0, 21]], "one load at a time")
        self.assertEqual(out["after"], [[0x5C, 0, 22]], "and then the one that was waiting")
        self.assertEqual(out["offline"], [], "not connected: nothing is sent")

    def test_via_in_the_preset_format(self):
        out = run_node(source("tools", "web", "sound-plan.js") + NODE_DRIVER % """
        const has = () => true, plan = j => planSound(j, has), last = j => plan(j).messages.filter(m => m[0] === 0x62);
        const refused = r => { try { plan({ routes: { 1: [r] } }); return null; } catch (e) { return e.message; } };
        report({
          number: last({ routes: { 1: [["lfo", "cutoff", 5, "via:0.25"]] } }),
          source: last({ routes: { 2: [["lfo", "cutoff", 5, "both", "via:mod-wheel"]] } }),
          both: plan({ routes: { 1: [["lfo", "cutoff", 5, "via:2", "both"]] } }).both.length,
          none: last({ routes: { 1: [["lfo", "cutoff", 5]] } }),
          old: (({ messages, warnings }) => [messages.filter(m => m[0] === 0x62).length, warnings.length])(planSound({ routes: { 1: [["lfo", "cutoff", 5, "via:2"]] } }, () => false)),
          bad: [["lfo", "cutoff", 5, "via:fixed"], ["lfo", "cutoff", 5, "via:9"], ["lfo", "cutoff", 5, "via:"], ["lfo", "macro1", 5, "via:macro2"], ["lfo", "cutoff", 5, "via:2", "via:3"], ["lfo", "cutoff", 5, "x"]].map(refused),
          text: [viaToText(25), viaToText(100), viaToText(1005), viaFromText(viaToText(1005))],
        });
        """)
        self.assertEqual(out["number"], [[0x62, 0, 1, 3, 0, 25]])
        self.assertEqual(out["source"], [[0x62, 1, 1, 3, 1005 >> 7, 1005 & 0x7F]])
        self.assertEqual(out["both"], 1)
        self.assertEqual(out["none"], [])
        self.assertEqual(out["old"], [0, 1], "older firmware: a warning, not a failed install")
        for why in out["bad"]:
            self.assertIsNotNone(why)
        self.assertEqual(out["text"], ["0.25", "1", "mod-wheel", 1005])

    def test_only_a_macro_into_a_macro_is_refused_as_via(self):
        """The firmware's rule (via_allowed() in synth.c): a macro can't be the Via of a route into a macro. Arp note
        into a macro, and a macro into Arp Chaos, are fine; the page and the preset format used to refuse those too."""
        page = source("tools", "web", "index.html")
        menu = run_node("\n".join(js_block(page, b) for b in ("const SOURCES", "const FIXED_SOURCE", "const isMacroSource", "const VIA_SOURCE", "const viaOptions"))
                        + NODE_DRIVER % """
        const offered = d => [...viaOptions(d).matchAll(/value="(\\d+)"/g)].map(m => +m[1]);
        report({ macro1: offered(44), macro2: offered(45), chaos: offered(46), cutoff: offered(3) });
        """)
        every = [0] + [1000 + s for s in range(1, len(pro800.SOURCE_NAMES)) if s != 25]
        self.assertEqual(menu["cutoff"], every)
        self.assertEqual(menu["chaos"], every, "a macro may drive Arp Chaos's Via")
        for dest in ("macro1", "macro2"):
            self.assertEqual(menu[dest], [v for v in every if v not in (1026, 1027)], dest)
        out = run_node(source("tools", "web", "sound-plan.js") + NODE_DRIVER % """
        const refused = r => { try { planSound({ routes: { 1: [r] } }, () => true); return null; } catch (e) { return e.message; } };
        report([["lfo", "macro1", 5, "via:arp-note"], ["lfo", "arp-chaos", 5, "via:macro1"], ["lfo", "macro2", 5, "via:macro1"]].map(refused));
        """)
        self.assertEqual(out[:2], [None, None])
        self.assertIn("a macro can't be the Via", out[2])

    def test_the_arp_in_the_preset_format(self):
        out = run_node(source("tools", "web", "sound-plan.js") + NODE_DRIVER % """
        const plan = planSound({ settings: { "arp-range": 3, "arp-extend": "ninth", "arp-chaos": 40 },
          routes: { 1: [["arp-note", "arp-chaos", 20]] }, sequence: { arp: "walk", steps: [{}] } }, () => true);
        const old = planSound({ settings: { "arp-chaos": 40 } }, name => name !== "arpChaos");
        const refused = j => { try { planSound(j, () => true); return null; } catch (e) { return e.message; } };
        report({ sets: plan.messages.filter(m => m[0] === 0x7D && m[1] >= 92 && m[1] <= 94), header: plan.messages.find(m => m[0] === 0x47),
          route: plan.messages.find(m => m[0] === 0x56 && m[3] === 46), oldWarned: old.warnings.length,
          bad: [{ settings: { "arp-range": 4 } }, { settings: { "arp-extend": "eleventh" } }, { settings: { "arp-chaos": 100 } }, { sequence: { arp: "stroll", steps: [{}] } }].map(refused) })
        """)
        self.assertEqual(out["sets"], [[0x7D, 92, 0, 2], [0x7D, 93, 0, 4], [0x7D, 94, 0, 40]])
        self.assertEqual(out["header"][9], 6)
        self.assertEqual(out["route"][2], 28)
        for why in out["bad"]:
            self.assertIsNotNone(why)

    def test_an_import_reads_the_map_first(self):
        """The page's Import diffs against the map as the synth has it now, not one it read earlier."""
        page = source("tools", "web", "index.html")
        out = run_node(FAKE_DOM + source("tools", "web", "params.js") + source("tools", "web", "sound-plan.js") + NODE_DRIVER % ("""
        let sent = [], now = { ids: Array(128).fill(127), nibbles: Array(128).fill(0) };
        now.ids[20] = 24;
        const offline = () => false, confirm = () => true, paintCcMap = () => {};
        const ask = async (m) => [0x65, m[1], ...Array.from({ length: 32 }, (_, i) => [now.ids[32 * m[1] + i], now.nibbles[32 * m[1] + i]]).flat()];
        const ack = async (m) => { sent.push(m); if (m[0] === 0x43) { now.ids[m[1]] = m[2]; now.nibbles[m[1]] = m[3]; } return true; };
        """ + chr(10).join(js_block(page, b) for b in ["let ccMap", "const ccMapMsg", "async function readCcMap(", "async function refreshCcMap(", "async function applyCcMapFile("]) + """
        ccMap = { ids: Array(128).fill(127), nibbles: Array(128).fill(0) }; // what the page read long ago: nothing on CC 20
        await applyCcMapFile({ format: "pro8000-cc-map", version: 1, map: [{ cc: 74, param: "cutoff" }] }, "the file");
        report({ sent, cc20: now.ids[20], cc74: now.ids[74] });
        """))
        self.assertIn([0x43, 20, 127, 0], out["sent"], "CC 20, mapped on the synth since, is cleared")
        self.assertEqual((out["cc20"], out["cc74"]), (127, 24))

    def test_the_cc_map_file(self):
        """#37: the map's JSON, as the editor and the tools read and write it, and the stock map."""
        out = run_node(source("tools", "web", "params.js") + source("tools", "web", "sound-plan.js") + NODE_DRIVER % ("""
        const stock = """ + source("tools", "web", "stock-cc-map.json") + """;
        const ids = Array(128).fill(127), nibbles = Array(128).fill(0);
        ids[74] = 24; ids[87] = 24; nibbles[87] = 4; ids[20] = 84; nibbles[20] = 1;
        const j = ccMapToJson(ids, nibbles, PARAMS), back = ccMapFromJson(JSON.parse(JSON.stringify(j)), PARAMS);
        const refused = map => ccMapFromJson({ format: "pro8000-cc-map", version: 1, map }, PARAMS).problems;
        const st = ccMapFromJson(stock, PARAMS);
        report({ j, slots: [...back.slots], problems: back.problems, changes: ccMapChanges(ids, nibbles, back.slots).length,
          clear: ccMapChanges(ids, nibbles, new Map()).length, stock: { n: st.slots.size, problems: st.problems, cc8: st.slots.get(8) },
          bad: [[{ cc: 1, param: "cutoff" }], [{ cc: 9, param: "cutoff" }, { cc: 9, param: "noise" }], [{ cc: 9, param: "nope" }],
                [{ cc: 9, param: "cutoff", layer: "3" }], [{ cc: 9, param: "layers", fine: true }], [{ cc: 128, param: "cutoff" }]].map(refused),
          notAFile: ccMapFromJson({ map: [] }, PARAMS).problems.length });
        """))
        self.assertEqual(out["j"]["map"], [{"cc": 20, "param": "shape 2", "layer": "1", "fine": False, "nrpn": 84},
                                           {"cc": 74, "param": "cutoff", "layer": "edit", "fine": False, "nrpn": 24},
                                           {"cc": 87, "param": "cutoff", "layer": "edit", "fine": True, "nrpn": 24}])
        self.assertEqual(out["slots"], [[20, [84, 1]], [74, [24, 0]], [87, [24, 4]]])
        self.assertEqual((out["problems"], out["changes"], out["clear"]), ([], 0, 3), "the round trip changes nothing; an empty file clears three")
        self.assertEqual((out["stock"]["problems"], out["stock"]["cc8"]), ([], [10, 0]), "the stock map is a good file: CC 8 is FREQ A")
        self.assertGreater(out["stock"]["n"], 50)
        for why in out["bad"]:
            self.assertEqual(len(why), 1, why)
        self.assertEqual(out["notAFile"], 1)

    def test_cc_out_on_a_cc_that_comes_back(self):
        """#38: the page warns when CC A / B out sends a CC that would set something coming back in."""
        out = run_node(source("tools", "web", "params.js") + source("tools", "web", "sound-plan.js") + NODE_DRIVER % """
        const ids = Array(128).fill(127); ids[74] = 24;
        const s = { ccA: 11, ccB: 2, ccOutA: 0, ccOutB: 0 };
        report({ fine: ccOutClashes(s, { ids }, PARAMS), map: ccOutClashes({ ...s, ccOutA: 74 }, { ids }, PARAMS),
          source: ccOutClashes({ ...s, ccOutB: 11 }, null, PARAMS), old: ccOutClashes({ ccA: 11, ccB: 2 }, { ids }, PARAMS),
          reserved: ccOutClashes({ ...s, ccOutA: 123 }, null, PARAMS) });
        """)
        self.assertEqual((out["fine"], out["old"]), ([], []), "the defaults (12, 13) clash with nothing; a firmware before 0.10.0 has no CC out")
        self.assertEqual(len(out["map"]), 1)
        self.assertIn("CC 74", out["map"][0])
        self.assertIn("Cutoff", out["map"][0])
        self.assertIn("CC A's source", out["source"][0])
        self.assertIn("its own meaning", out["reserved"][0])

    def test_a_chord_in_the_preset_format(self):
        """#36: "chord 1" / "chord 2" as 69, after the buttons; a firmware before 0.10.0 warns and gets nothing."""
        out = run_node(source("tools", "web", "sound-plan.js") + NODE_DRIVER % """
        const plan = planSound({ buttons: ["unison"], settings: { "chord 1": "0 4 7", "chord 2": "off" } }, () => true);
        const old = planSound({ settings: { "chord 1": "4,7" } }, name => name !== "initPatch");
        const refused = c => { try { planSound({ settings: { "chord 1": c } }, () => true); return null; } catch (e) { return e.message; } };
        const at = m => plan.messages.findIndex(x => x[0] === m);
        report({ chords: plan.messages.filter(m => m[0] === 0x69), afterButtons: at(0x69) > plan.messages.map(m => m[0]).lastIndexOf(0x4B),
          warned: plan.warnings, old: old.messages.filter(m => m[0] === 0x69).length, oldWarned: old.warnings.length,
          parsed: ["0 4 7", "4,7", "0, 3, 7, 10", "", "none", " OFF "].map(chordFromText),
          bad: ["0 7 4", "1 2 3 4 5 6 7 8", "0 128", "0 4 4", "C E G", 5.5].map(refused) });
        """)
        self.assertEqual(out["chords"], [[0x69, 0, 4, 7, 0, 0, 0, 0, 0, 0], [0x69, 1, 0, 0, 0, 0, 0, 0, 0, 0]])
        self.assertTrue(out["afterButtons"])
        self.assertEqual(out["warned"], [], "not an unknown setting")
        self.assertEqual((out["old"], out["oldWarned"]), (0, 1))
        self.assertEqual(out["parsed"], [[4, 7], [4, 7], [3, 7, 10], [], [], []])
        for why in out["bad"]:
            self.assertIsNotNone(why)

    def test_the_chord_rows_names_and_the_voicings_range(self):
        page = source("tools", "web", "index.html")
        out = run_node(NODE_DRIVER % ("""
        let paramsLayer = 0, settings = null;
        """ + chr(10).join(js_block(page, b) for b in RANGE_BLOCKS + ["const NOTE_NAMES", "const chordIntervals", "const chordNames"]) + """
        const range = d => routeSwing(25, 47, d).text;
        const none = range(99);
        chords = [[4, 7, 0, 0, 0, 0, 0, 0], [3, 7, 10, 14, 17, 21, 24, 0]];
        const out = { none, full: range(99), third: range(33), down: range(-99), names: chordNames(chordIntervals(0)) };
        paramsLayer = 1;
        out.seven = range(99);
        out.names2 = chordNames(chordIntervals(1));
        out.via = routeSwing(25, 47, 99, 400).text;
        settings = { layers: 1, layerVoices: 0 };
        out.split = range(99);
        paramsLayer = 0;
        chords[0] = [2, 4, 5, 7, 9, 0, 0, 0];
        out.split1 = range(99);
        report(out);
        """))
        self.assertEqual(out["none"], "none: the layer has no chord")
        self.assertEqual((out["full"], out["third"], out["down"]), ("+3 steps", "+1 step", "-3 steps"))
        self.assertEqual(out["seven"], "+8 steps", "all seven intervals: eight notes")
        self.assertEqual(out["via"], "+8 steps", "a Via of 4 doesn't take it past the chord's notes")
        self.assertEqual((out["split"], out["split1"]), ("+4 steps", "+4 steps"), "split: four voices a layer")
        self.assertEqual(out["names"], "C E G")
        self.assertEqual(out["names2"], "C D♯ G A♯ D' F' A' C''")

    def test_tenths_in_the_preset_format(self):
        out = run_node(source("tools", "web", "sound-plan.js") + NODE_DRIVER % """
        const has = () => true, plan = j => planSound(j, has), route = m => m[0] === 0x56 && m[1] === 0 && m[2] === 1 || m[0] === 0x66;
        const sent = depth => plan({ routes: { 1: [["lfo", "cutoff", depth]] } }).messages.filter(route);
        const refused = d => { try { plan({ routes: { 1: [["lfo", "cutoff", d]] } }); return null; } catch (e) { return e.message; } };
        const old = planSound({ routes: { 1: [["lfo", "cutoff", 53.5]] } }, () => false);
        report({ whole: sent(53), half: sent(53.5), small: sent(0.1), negative: sent(-53.5), oldSent: old.messages.filter(route), oldWarned: old.warnings.length,
          bad: [99.1, -99.1, 53.55, "53", null].map(refused) });
        """)
        self.assertEqual(out["whole"], [[0x56, 0, 1, 3, 1, 152 & 0x7F]])
        self.assertEqual(out["half"], [[0x56, 0, 1, 3, 1, 153 & 0x7F], [0x66, 0, 1, 3, 1525 >> 7, 1525 & 0x7F]], "the nearest whole number first")
        self.assertEqual(out["small"], [[0x56, 0, 1, 3, 0, 100], [0x66, 0, 1, 3, 991 >> 7, 991 & 0x7F]], "never 0, which is no route")
        self.assertEqual(out["negative"], [[0x56, 0, 1, 3, 0, 45], [0x66, 0, 1, 3, 455 >> 7, 455 & 0x7F]])
        self.assertEqual(out["oldSent"], [[0x56, 0, 1, 3, 1, 153 & 0x7F]], "older firmware: the whole number")
        self.assertEqual(out["oldWarned"], 1)
        for why in out["bad"]:
            self.assertIsNotNone(why)

    def test_tenths_in_plan_py(self):
        src = source("presets", "lib", "plan.py")
        self.assertIn('"route %s %s %s %s"', src)
        self.assertIn("in steps of 0.1", src)

    def test_via_in_plan_py(self):
        plan = load_plan_py()
        src = source("presets", "lib", "plan.py")
        self.assertIn("route-via %s %s %s %s", src)

    def test_the_via_range_text(self):
        page = source("tools", "web", "index.html")
        out = run_node("\n".join(js_block(page, b) for b in ("const SOURCES", "const DESTS", "const BIPOLAR_SOURCES", "const SQUARED_DESTS", "const FIXED_SOURCE", "const VIA_SOURCE", "const timeDest", "const rateDest", "const factor", "function swingUnit(", "function routeSwing(")) + NODE_DRIVER % """
        const t = (...a) => routeSwing(...a).text;
        report({ plain: t(1, 0, 99), two: t(1, 0, 99, 200), quarter: t(25, 18, 99, 25), wheel: t(1, 3, 99, 1005), oneWay: t(4, 3, 99, 1005), bip: t(4, 3, 99, 1001), capped: t(25, 18, 99, 800) });
        """)
        self.assertEqual(out["plain"], "±24 st")
        self.assertEqual(out["two"], "±48 st")
        self.assertEqual(out["quarter"], "+1 oct")
        self.assertEqual(out["wheel"], "up to ±96 st")
        self.assertEqual(out["oneWay"], "up to +96 st")
        self.assertEqual(out["bip"], "up to ±96 st", "a source as Via that swings both ways flips the route")
        self.assertEqual(out["capped"], "+8 oct", "exp_mod()")

    def test_the_sections_fold(self):
        page = source("tools", "web", "index.html")
        for call in ('collapsible(sec("voice", "Voice",', 'collapsible(matrix, "matrix")', 'collapsible(seqSection(), "sequencer")'):
            self.assertIn(call, page)
        out = run_node("""
        let store = {}, painted = 0, broken = false;
        const localStorage = { getItem: k => { if (broken) throw new Error("no"); return store[k] ?? null; }, setItem: (k, v) => { if (broken) throw new Error("no"); store[k] = v; } };
        const paintAll = () => painted++;
        const el = (tag, text) => { const e = { tag, textContent: text || "", attrs: {}, kids: [], classes: new Set(), setAttribute(k, v) { e.attrs[k] = v; }, replaceChildren(...k) { e.kids = k; },
          classList: { toggle(c, on) { on ? e.classes.add(c) : e.classes.delete(c); }, contains: c => e.classes.has(c) } }; return e; };
        const h = (tag, attrs, ...kids) => { const e = el(tag); e.kids = kids; return e; };
        const section = title => { const s = el("section"), t = el("h2", title); s.querySelector = () => t; s.title = t; return s; };
        """ + js_block(page, "function collapsible(") + NODE_DRIVER % """
        const out = {};
        const a = collapsible(section("Voice"), "voice"), button = a.title.kids[0];
        out.first = [a.classes.has("collapsed"), button.attrs["aria-expanded"], button.kids[1], painted];
        button.onclick();
        out.folded = [a.classes.has("collapsed"), button.attrs["aria-expanded"], store["pro800-folded"], painted];
        const b = collapsible(section("Voice"), "voice"), other = collapsible(section("Sequencer"), "sequencer");
        out.again = [b.classes.has("collapsed"), other.classes.has("collapsed")];
        button.onclick();
        out.open = [a.classes.has("collapsed"), store["pro800-folded"]];
        store["pro800-folded"] = "not json";
        out.bad = collapsible(section("Voice"), "voice").classes.has("collapsed");
        broken = true;
        const c = collapsible(section("Voice"), "voice");
        c.title.kids[0].onclick();
        out.noStorage = c.classes.has("collapsed");
        report(out);
        """)
        self.assertEqual(out["first"], [False, "true", "Voice", 0], "open by default")
        self.assertEqual(out["folded"], [True, "false", '{"voice":true}', 1])
        self.assertEqual(out["again"], [True, False], "remembered, each by its own")
        self.assertEqual(out["open"], [False, "{}"])
        self.assertFalse(out["bad"], "what can't be read is open")
        self.assertTrue(out["noStorage"], "and without storage it still folds")

    def test_a_synced_envelopes_knob_sets_its_share(self):
        page = source("tools", "web", "index.html")
        out = run_node("""
        let params = [], paramsLayer = 0, settings = { patch: 5 }, sent = [];
        const A = 0, H = 1, F = 2, R = 3;
        const envOf = () => ["", 4, A, F, H, R];
        const setParam = (i, v) => { sent.push(i); params[i] = v; };
        """ + js_block(page, "function cycleShares(") + "\n" + js_block(page, "function cycleShare(") + "\nconst cycleMix = new Map();\n" + js_block(page, "function setCycleShare(") + NODE_DRIVER % """
        const start = (a, d, s, r) => { params = []; params[A] = a; params[F] = d; params[H] = s; params[R] = r; cycleMix.clear(); sent = []; };
        const read = () => ({ attack: +cycleShare(4, 0).toFixed(3), fall: +cycleShare(4, 1).toFixed(3), hold: +cycleShare(4, 2).toFixed(3), rest: +cycleShare(4, 3).toFixed(3), top: +Math.max(...params).toFixed(3) });
        const out = {};
        start(1, 1, 0, 0); setCycleShare(4, 0, 0.7); out.up = read(); out.lastSent = sent[sent.length - 1];
        start(1, 1, 0, 0); setCycleShare(4, 0, 1); out.all = read(); setCycleShare(4, 0, 0.5); out.back = read();
        start(0.5, 0, 0, 0); setCycleShare(4, 0, 0.6); out.alone = read();
        start(0, 0, 0, 1); setCycleShare(4, 3, 0.4); out.restAlone = read();
        start(0.2, 0.4, 0.2, 0.2); setCycleShare(4, 2, 0.5); out.keeps = read();
        start(0, 0, 0, 0); setCycleShare(4, 0, 0.1); out.empty = read();
        start(1, 1, 0, 0); setCycleShare(4, 0, 0); out.none = read();
        start(1, 1, 0, 0); setCycleShare(4, 0, 2); out.clamped = read();
        report(out);
        """)
        self.assertEqual(out["up"], {"attack": 0.7, "fall": 0.3, "hold": 0, "rest": 0, "top": 1}, "at its top, it can still take more")
        self.assertEqual(out["lastSent"], 0, "the screen says the one being turned")
        self.assertEqual(out["all"]["attack"], 1)
        self.assertEqual(out["back"], {"attack": 0.5, "fall": 0.5, "hold": 0, "rest": 0, "top": 1}, "through 100 % and back, the fall comes back")
        self.assertEqual(out["alone"], {"attack": 0.6, "fall": 0, "hold": 0, "rest": 0.4, "top": 1}, "the only knob above 0 moves: the rest takes the time")
        self.assertEqual(out["restAlone"], {"attack": 0, "fall": 0.6, "hold": 0, "rest": 0.4, "top": 1})
        self.assertEqual(out["keeps"], {"attack": 0.125, "fall": 0.25, "hold": 0.5, "rest": 0.125, "top": 1}, "the others keep their proportions")
        self.assertEqual(out["empty"]["attack"], 0.1)
        self.assertEqual(out["none"], {"attack": 0, "fall": 1, "hold": 0, "rest": 0, "top": 1})
        self.assertEqual(out["clamped"]["attack"], 1)

    def test_the_knobs_use_it_on_a_synced_envelope(self):
        page = source("tools", "web", "index.html")
        block = js_block(page, "function knob(i, text)")
        self.assertIn("setCycleShare(e[0], e[1], v)", block)
        self.assertIn("cycleShare(e[0], e[1])", block)

    def test_the_mod_matrix_columns_are_set(self):
        page = source("tools", "web", "index.html")
        self.assertIn('<table class="routes-table">', page)
        self.assertRegex(page, r"table\.routes-table \{ table-layout: fixed;", "or a Range cell's text moves the slider under the mouse")

    def test_the_undo_button(self):
        page = source("tools", "web", "index.html")
        self.assertRegex(page[page.index("<header>"):page.index("</header>")], r'<button[^>]*id="undo-top"[^>]*data-needs="unisonVoices"[^>]*>Undo</button>')
        out = run_node(NODE_DRIVER % ("""
        let patchName = "Init", patchNameFor = 1, seqFor = "1:1", settings = { patch: 70 }, calls = [], answer = null;
        const ask = async (m, e, t) => { calls.push(m); return answer; };
        const refreshSettings = async () => { calls.push(["settings", seqFor]); patchName = "stored"; }, refreshParams = async () => { calls.push("params"); };
        """ + js_block(page, "async function undoSound(") + """
        const out = { none: await undoSound(), noneCalls: calls };
        calls = []; answer = [0x60, 0];
        out.nothing = await undoSound(); out.nothingName = patchName; out.nothingCalls = calls;
        calls = []; answer = [0x60, 1, 80, 97, 100];
        out.done = await undoSound(); out.name = patchName; out.nameFor = patchNameFor; out.calls = calls;
        report(out);
        """))
        self.assertEqual((out["none"], out["noneCalls"]), (None, [[0x60]]))
        self.assertEqual((out["nothing"], out["nothingName"], out["nothingCalls"]), (False, "Init", [[0x60]]))
        self.assertEqual(out["calls"], [[0x60], ["settings", None], "params"], "the page reads the sound back, and its sequence")
        self.assertEqual((out["done"], out["name"], out["nameFor"]), (True, "Pad", 70), "the reply's name, not the slot's")

    def test_the_mod_matrix_has_no_long_tip(self):
        self.assertNotIn("Sixteen routes per layer", source("tools", "web", "index.html"))

    def test_the_unison_knobs(self):
        page = source("tools", "web", "index.html")
        out = run_node(source("tools", "web", "sound-plan.js") + """
        let settings = { detune: 14, unisonVoices: 3 }, sent = [], newFirmware = true;
        const offline = () => false, touch = () => {}, feature = () => newFirmware, setSetting = (id, v) => sent.push([id, v]);
        const modelKnob = (label, get, set, show, reset) => ({ get, set, show, reset, dataset: {} });
        const setTimeout = f => f(), clearTimeout = () => {};
        """ + js_block(page, "function settingKnob(") + "\n" + js_block(page, "const detuneMax")
            + "\n" + js_block(page, "function unisonDetuneKnob(") + "\n" + js_block(page, "function unisonVoicesKnob(") + NODE_DRIVER % """
        const detune = unisonDetuneKnob(), voices = unisonVoicesKnob(), out = {};
        out.needs = voices.dataset.needs;
        out.at = [detune.get(), voices.get()];
        out.shown = [detune.show(1), detune.show(0), voices.show(0), voices.show(1), voices.show(1 / 6)];
        out.reset = [Math.round(detune.reset() * 100), voices.reset()];
        detune.set(1); voices.set(0); voices.set(1); voices.set(0.5);
        out.sent = sent;
        newFirmware = false; sent = [];
        const old = unisonDetuneKnob();
        old.set(1);
        out.old = { sent, shown: old.show(1) };
        report(out);
        """)
        self.assertEqual(out["needs"], "unisonVoices")
        self.assertAlmostEqual(out["at"][0], 0.14)
        self.assertAlmostEqual(out["at"][1], 1 / 6)
        self.assertEqual(out["shown"], ["100 ct", "0 ct", "2", "8", "3"])
        self.assertEqual(out["reset"], [14, 1], "a double-click: the defaults, 14 cents and 8 voices")
        detune_id, voices_id = pro800.SETTING_NAMES.index("detune"), pro800.SETTING_NAMES.index("unison-voices")
        self.assertEqual(out["sent"], [[detune_id, 100], [voices_id, 2], [voices_id, 8], [voices_id, 5]])
        self.assertEqual(out["old"], {"sent": [[detune_id, 50]], "shown": "50 ct"}, "older firmware refuses more than 50")

    def test_the_pages_compile_with_the_shared_script(self):
        """A const in both would stop a page dead, and only a browser would otherwise show it."""
        for name in ("index.html", "install.html"):
            with self.subTest(page=name):
                folder = tempfile.mkdtemp(prefix="pro800page")
                path = os.path.join(folder, "page.js")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(page_script(name))
                check = subprocess.run([NODE, "--check", path], capture_output=True, text=True, encoding="utf-8")
                shutil.rmtree(folder, ignore_errors=True)
                self.assertEqual(check.returncode, 0, check.stderr)

    def test_the_format_reply_with_the_firmwares_own_bytes(self):
        path = os.path.join(tempfile.mkdtemp(prefix="pro800sim"), "eeprom.bin")
        shutil.copy(formatted_memory(), path)
        s = Synth(path)
        reply = s.reply(0x5B)
        s.close()
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        out = run_node(source("tools", "web", "sound-plan.js") + NODE_DRIVER % ("report({ now: formatReply(%s), old: formatReply([0x5B, 0, 0x80, 0]), failed: formatReply([0x5B, 1, 0, 1]), none: formatReply(null) });" % json.dumps([0x5B, *reply])))
        self.assertEqual(out["now"], {"count": 128, "failed": False})
        self.assertEqual(out["old"], {"count": 128, "failed": False})
        self.assertEqual(out["failed"], {"count": 128, "failed": True})
        self.assertIsNone(out["none"])

    def test_both_pages_read_the_format_reply_with_it(self):
        for name in ("index.html", "install.html"):
            self.assertIn("formatReply(await ", source("tools", "web", name), name)

    def test_the_kinds_in_the_editor(self):
        page = source("tools", "web", "index.html")
        out = run_node("\n".join(js_block(page, b) for b in ["const kindText", "const kindClass", "function scanSummary(", "const needsFormatFirst"]) + NODE_DRIVER % """
        const k = kinds => kinds.map(kind => ({ kind }));
        report({ text: [0, 1, 3].map(kindText), css: [0, 1, 3].map(kindClass), summary: scanSummary(k([0, 1, 1, 3])), clean: scanSummary(k([0, 1])),
                 stock: needsFormatFirst(k([3, 3])), mixed: needsFormatFirst(k([3, 1])), blank: needsFormatFirst(k([0, 0])), oldStock: needsFormatFirst(k([2, 0])) });
        """)
        self.assertEqual(out["text"], ["empty", "ours", "stock or unreadable"])
        self.assertEqual(out["css"][2], "corrupt")
        self.assertEqual(out["summary"], "2 patches, 1 empty, 1 stock or unreadable")
        self.assertEqual(out["clean"], "1 patches, 1 empty")
        self.assertTrue(out["stock"])
        self.assertFalse(out["mixed"], "a patch of ours says the memory is ours: the block may be missing, and the restore sees to that")
        self.assertFalse(out["blank"], "nothing to wipe: the restore writes the block itself")
        self.assertTrue(out["oldStock"])

    def test_the_routes_count_comes_from_one_place(self):
        page = source("tools", "web", "index.html")
        routes = int(re.search(r"#define ROUTES (\d+)", source("src", "mod.h")).group(1))
        self.assertEqual(int(re.search(r"const ROUTES = (\d+);", page).group(1)), routes)
        self.assertEqual(re.findall(r"All \d+ (?:mod )?routes", page), [], "the message comes from routesFull()")
        out = run_node("let paramsLayer = 1;\n" + js_block(page, "const ROUTES") + "\n" + js_block(page, "const routesFull") + NODE_DRIVER % "report({ text: routesFull() });")
        self.assertEqual(out["text"], f"All {routes} mod routes of layer 2 are in use")

    def test_the_cheat_sheet_knows_the_new_behaviour(self):
        page = source("tools", "web", "index.html")
        sheet = page[page.index('id="tab-device"'):page.index('id="tab-matrix"')]
        self.assertIn('<code class="lcd">CrSh</code>', sheet)
        self.assertIn("a second after the last change", sheet)

    def test_no_page_promises_lanes_a_lossless_trip(self):
        """A lane value comes back +-1 through JSON: 7 bits against 0-99."""
        for name in ("index.html", "install.html"):
            self.assertNotRegex(source("tools", "web", name), r"(?i)lossless|round.trip|bit.exact")


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebLfoTimes(unittest.TestCase):
    """The editor's LFO Delay and Fade in knobs read as the firmware connected acts: note values on a synced LFO from 0.9.4."""

    def texts(self, script):
        page = source("tools", "web", "index.html")
        blocks = ["let firmwareVersion", "const noteStep", "const noteName", "const DIVISIONS", "const fadeSeconds", "const lfoNoteTimes", "const fadeText"]
        prelude = "let settings = null;\n" + "\n".join(js_block(page, b) for b in blocks[:5]) + "\n" + js_block(page, blocks[5]) + "\n"
        a = page.index("\nconst fadeText")
        prelude += page[a:page.index("\n};\n", a) + 4]
        return run_node(prelude + NODE_DRIVER % script)

    def test_note_values_when_the_lfo_shown_is_synced(self):
        out = self.texts("""
        firmwareVersion = "0.9.4";
        settings = { lfo1Sync: 1, lfo2Sync: 0 };
        report({ one: [0, 2, 3, 4, 56, 57, 60, 62, 63, 79, 99].map(v => fadeText(1)(v / 99)), two: [0, 60].map(v => fadeText(2)(v / 99)) });
        """)
        self.assertEqual(out["one"], ["off", "off", "off", "1/32", "1/4T", "1/4", "1/4", "1/4", "1/4.", "1 bar", "8 bars"])
        self.assertEqual(out["two"], ["off", "576 ms"], "each LFO's own sync")

    def test_the_other_lfo_the_other_way_round(self):
        out = self.texts("""
        firmwareVersion = "0.9.4";
        settings = { lfo1Sync: 0, lfo2Sync: 1 };
        report({ one: fadeText(1)(60 / 99), two: fadeText(2)(60 / 99) });
        """)
        self.assertEqual((out["one"], out["two"]), ("576 ms", "1/4"))

    def test_older_firmware_still_acts_in_seconds(self):
        out = self.texts("""
        const out = {};
        settings = { lfo1Sync: 1, lfo2Sync: 1 };
        for (const v of ["0.9.3", "0.8.9", "0.7.0"]) { firmwareVersion = v; out[v] = [fadeText(1)(60 / 99), fadeText(2)(90 / 99), fadeText(1)(0)]; }
        report(out);
        """)
        for version in ("0.9.3", "0.8.9", "0.7.0"):
            self.assertEqual(out[version], ["576 ms", "3.1 s", "off"], version)

    def test_later_firmware_keeps_them(self):
        out = self.texts("""
        const out = {};
        settings = { lfo1Sync: 1 };
        for (const v of ["0.9.4", "0.9.5", "0.10.0", "0.19.9", "1.0.0"]) { firmwareVersion = v; out[v] = fadeText(1)(60 / 99); }
        report(out);
        """)
        self.assertEqual(set(out.values()), {"1/4"}, out)
        self.assertEqual(len(out), 5, out)

    def test_free_and_not_connected_read_in_time(self):
        out = self.texts("""
        const a = [50, 60, 90].map(v => fadeText(1)(v / 99));
        firmwareVersion = "0.9.4";
        settings = { lfo1Sync: 0 };
        report({ unconnected: a, free: [50, 60, 90].map(v => fadeText(1)(v / 99)) });
        """)
        self.assertEqual(out["unconnected"], ["329 ms", "576 ms", "3.1 s"])
        self.assertEqual(out["free"], out["unconnected"])

    def test_the_same_step_as_the_tool_for_every_value(self):
        out = self.texts("""
        firmwareVersion = "0.9.4";
        settings = { lfo1Sync: 1 };
        report({ texts: Array.from({ length: 100 }, (_, v) => fadeText(1)(v / 99)) });
        """)
        for value, text in enumerate(out["texts"]):
            tool = pro800.lfo_note_text(value)
            self.assertEqual(text.replace(" bars", "bar").replace(" bar", "bar"), "off" if "off" in tool else tool.split("(")[1].rstrip(")"), value)

    def test_during_a_drag_it_names_the_value_that_is_sent(self):
        """A drag's position isn't on the 0-99 grid. 0.0347 sends 3, which is off, though the position alone would round to 1/32;
        56.4 of 99 sends 56, 1/4T, though the position alone would round to 1/4."""
        out = self.texts("""
        firmwareVersion = "0.9.4";
        settings = { lfo1Sync: 1 };
        report({ low: fadeText(1)(0.0347), middle: fadeText(1)(56.4 / 99) });
        """)
        self.assertEqual((out["low"], out["middle"]), ("off", "1/4T"))

    def test_the_knobs_use_it(self):
        page = source("tools", "web", "index.html")
        self.assertEqual(len(re.findall(r"settingKnob\(`lfo(?:Delay|Fade)\$\{n\}`.*?, fadeText\(n\)\)", page)), 2)

    def test_the_cheat_sheet_says_so(self):
        page = source("tools", "web", "index.html")
        sheet = page[page.index('id="tab-device"'):page.index('id="tab-matrix"')]
        self.assertIn("With the clock, its delay and fade are note values too", sheet)
        for name in ("1-32", "8bAr"):
            self.assertIn(f'<code class="lcd">{name}</code>', sheet)


@unittest.skipUnless(NODE, "needs node to run the page's code")
class PresetRoutes(PanelTest):
    """A sound built from JSON has exactly the routes it lists, whatever the init sound under it comes with: planSound()
    (the editor's Load JSON, both bank installs) and plan.py (presets/install.sh), each against the simulated firmware."""

    def plan_on_the_firmware(self, presets, save=False, each_ms=0):
        """Each through planSound(), its messages to the firmware, each_ms of its time after each; saved in its slot if asked.
        Returns what it reported."""
        return run_node(source("tools", "web", "sound-plan.js") + ASK_THE_SIMULATOR + NODE_DRIVER % """
        const failed = [], each = %d;
        const firmwareMs = ms => new Promise(r => { answers.push(r); process.stdout.write("WAIT " + ms + "\\n"); });
        for (const p of %s) {
          const plan = planSound(p, () => true);
          for (const m of plan.messages) {
            if (!await ack(m)) failed.push(m);
            if (each) await firmwareMs(each);
          }
          const stuck = await markBothRoutes(plan, ask, ack);
          if (stuck) failed.push(stuck);
          if (%s) await ack([0x5D, (p.slot - 1) >> 7, (p.slot - 1) & 0x7F]);
        }
        report({ failed });
        """ % (each_ms, json.dumps(presets), "true" if save else "false"), self.s)

    def assertLists(self, preset):
        """Exactly the routes listed, each with its own Both mark and nothing marked besides. Which slot each has isn't the
        preset's business: the init sound's two wait at 0 in slots 0 and 1 until a save or their 5 s, so a list goes round them.
        Nothing here treats a depth 0 as absent: unsaved, the caller lets them go first (rest()); from a saved slot there are none."""
        for layer in (0, 1):
            listed = (preset.get("routes") or {}).get(str(layer + 1), [])
            wanted = [(pro800.SOURCE_NAMES.index(r[0]), pro800.DEST_NAMES.index(r[1]), r[2], "both" in r[3:]) for r in listed]
            st = self.s.settings()
            both = st[f"route-both {layer + 1} (0-7)"] | st[f"route-both {layer + 1} (8-15)"] << 8
            table = self.s.route_table(layer)
            self.assertEqual(sorted((*r, bool(both >> i & 1)) for i, r in enumerate(table) if r[0]), sorted(wanted), f"{preset.get('name')}, layer {layer + 1}")
            self.assertEqual([i for i, r in enumerate(table) if not r[0] and both >> i & 1], [], "a mark on an empty slot")

    @staticmethod
    def shipped():
        out = []
        folder = os.path.join(ROOT, "presets")
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    out.append(json.load(f))
        return out

    def test_a_json_gets_exactly_what_it_lists(self):
        for preset in ({"name": "nothing"},
                       {"name": "empty lists", "routes": {"1": [], "2": []}},
                       {"name": "layer 1 only", "routes": {"1": [["lfo", "cutoff", 10]]}},
                       {"name": "layer 2 only", "routes": {"2": [["lfo", "cutoff", 10]]}},
                       {"name": "init's otherwise", "routes": {"1": [["lfo", "pitch", 9], ["velocity", "vca", -30]], "2": [["velocity", "cutoff", 77]]}},
                       {"name": "both", "routes": {"1": [["lfo", "vca", 5], ["velocity", "cutoff", 33, "both"]], "2": [["env3", "resonance", 7, "both"]]}},
                       {"name": "sixteen", "routes": {"1": [["lfo", d, 5] for d in pro800.DEST_NAMES[:16]], "2": [["env3", d, -5] for d in pro800.DEST_NAMES[:16]]}}):
            with self.subTest(preset=preset["name"]):
                out = self.plan_on_the_firmware([preset])
                self.assertEqual(out["failed"], [], "every message taken")
                self.s.rest()
                self.assertLists(preset)

    def test_the_editors_own_load_and_bank_install(self):
        """The page's loadSound() and installBank(), cut out of index.html, on the firmware."""
        page = source("tools", "web", "index.html")
        one, two = ({"slot": 5, "name": "One", "routes": {"1": [["lfo", "vca", 5], ["velocity", "cutoff", 9, "both"]]}}, {"slot": 6, "name": "Two", "routes": {"2": [["env3", "resonance", 7, "both"]]}})
        out = run_node(source("tools", "web", "sound-plan.js") + ASK_THE_SIMULATOR + """
        const settings = new Proxy({}, { get: () => 0 }), shown = [];
        let seqFor = null;
        const refreshSettings = async () => true, refreshParams = async () => true, scanSlots = async () => {};
        const jsonMsg = text => shown.push(text), bankMsg = text => shown.push(text), bankRun = f => f();
        const document = { getElementById: () => ({}) };
        """ + js_block(page, "async function loadSound(") + js_block(page, "async function installBank(") + NODE_DRIVER % """
        await loadSound(%s, "the box");
        report(shown);
        """ % json.dumps(json.dumps(two)), self.s)
        self.assertIn("into the sound", out[-1])
        self.s.rest()
        self.assertLists(two)
        out = run_node(source("tools", "web", "sound-plan.js") + ASK_THE_SIMULATOR + """
        const settings = new Proxy({}, { get: () => 0 }), shown = [];
        let seqFor = null;
        const refreshSettings = async () => true, refreshParams = async () => true, scanSlots = async () => {};
        const bankMsg = text => shown.push(text);
        """ + js_block(page, "async function installBank(") + NODE_DRIVER % """
        await installBank(%s, "the file");
        report(shown);
        """ % json.dumps([one, two]), self.s)
        self.assertIn("installed 2 presets", out[-1])
        self.s.rest()
        self.assertLists(two)  # the sound on the synth is the last one
        self.s.request(0x5C, 0, 4)
        self.assertLists(one)
        self.s.request(0x5C, 0, 5)
        self.assertLists(two)

    def test_a_json_read_back_from_the_init_sound_keeps_its_routes(self):
        routes = {"1": [["velocity", "vca", 70], ["velocity", "cutoff", 20]], "2": [["velocity", "vca", 70], ["velocity", "cutoff", 20]]}
        self.plan_on_the_firmware([{"name": "as read", "routes": routes}])
        self.assertEqual((self.s.routes(0), self.s.routes(1)), (INIT_ROUTES, INIT_ROUTES))
        self.s.rest()
        self.assertEqual((self.s.routes(0), self.s.routes(1)), (INIT_ROUTES, INIT_ROUTES), "set again, they aren't waiting")

    def test_the_whole_bank_saved_and_loaded(self):
        bank = self.shipped()
        self.assertEqual(len(bank), 70)
        out = self.plan_on_the_firmware(bank, save=True)
        self.assertEqual(out["failed"], [])
        for preset in bank:  # no rest: what was saved holds no route at 0
            self.s.request(0x5C, (preset["slot"] - 1) >> 7, (preset["slot"] - 1) & 0x7F)
            self.assertLists(preset)

    def test_sixteen_with_both_marks_saved_and_loaded(self):
        """16 routes get past the init sound's two, the marks land on the right two, wherever those went."""
        routes = [["lfo", d, 5] for d in pro800.DEST_NAMES[:16]]
        routes[0].append("both")
        routes[14].append("both")  # slot 0 by then
        routes[15].append("both")  # slot 1
        preset = {"name": "marked", "slot": 7, "routes": {"1": routes}}
        self.assertEqual(self.plan_on_the_firmware([preset], save=True)["failed"], [])
        self.s.request(0x5C, 0, 6)
        self.assertLists(preset)

    def test_the_marks_land_right_however_long_the_plan_takes(self):
        """The init sound's two wait at 0 for 5 s from the start of the plan: a slow one outlives them, before the list or in it,
        and the list's slots move. At 250 ms a message they go about when the list starts, at 1000 long before it."""
        routes = [["lfo", d, 5] for d in pro800.DEST_NAMES[:16]]
        for at in (0, 14, 15):
            routes[at].append("both")
        small = [["lfo", "vca", 5, "both"], ["env3", "resonance", 9], ["env4", "resonance", 7]]
        for each_ms in (0, 250, 1000):
            with self.subTest(each_ms=each_ms):
                preset = {"name": "slow", "slot": 7, "routes": {"1": routes, "2": small}}
                self.assertEqual(self.plan_on_the_firmware([preset], save=True, each_ms=each_ms)["failed"], [])
                self.s.request(0x5C, 0, 6)
                self.assertLists(preset)

    def test_the_plan_names_the_marked_routes_and_the_table_gives_the_slots(self):
        out = run_node(source("tools", "web", "sound-plan.js") + ASK_THE_SIMULATOR + NODE_DRIVER % """
        const preset = { routes: { 2: [["lfo", "vca", 5, "both"], ["lfo", "cutoff", 0, "both"], ["env3", "resonance", 7]] } };
        const plan = planSound(preset, () => true), older = planSound(preset, () => false);
        deaf = true;
        const silent = await markBothRoutes(plan, ask, ack);
        report({ both: plan.both, olderBoth: older.both, olderSaid: older.warnings, silent, none: await markBothRoutes(planSound({}, () => true), ask, ack) });
        """, self.s)
        self.assertEqual(out["both"], [[1, [[1, VCA]]]], "layer 2's lfo -> vca: a listed depth 0 lands nowhere")
        self.assertEqual(out["olderBoth"], [])
        self.assertEqual(out["olderSaid"], ['routes 2: "both": this firmware hasn\'t got it'])
        self.assertEqual(out["silent"], [0x57, 1], "no reading, no marks: said, not guessed")
        self.assertIsNone(out["none"])

    def test_a_firmware_whose_init_sound_has_none_takes_the_removals(self):
        """The messages planSound() adds, against a route table without those routes: what any firmware before them replies."""
        self.s.no_routes()
        for layer in (0, 1):
            for source_, dest, _ in INIT_ROUTES:
                self.assertIn(pro800.SYSEX_HEADER + bytes([0x7F, 0x56]), self.s.request(0x56, layer, source_, dest, 0, 99))

    def test_plan_py_takes_a_chord(self):
        plan = load_plan_py()
        lines = plan.plan({"slot": 1, "name": "Chord", "buttons": ["unison"], "settings": {"chord 1": "0 4 7", "chord 2": ""}})
        self.assertIn("chord 1 4,7", lines)
        self.assertIn("chord 2 off", lines)
        self.assertGreater(lines.index("chord 1 4,7"), max(i for i, l in enumerate(lines) if l.startswith("set button")))
        for bad in ("0 7 4", "1 2 3 4 5 6 7 8", "0 128", "C E G"):
            with self.subTest(chord=bad), self.assertRaises(plan.Bad):
                plan.plan({"slot": 1, "name": "Chord", "settings": {"chord 1": bad}})
        with self.assertRaises(plan.Bad):
            plan.plan({"slot": 1, "name": "Chord", "settings": {"chord 3": "0 4 7"}})

    def test_install_sh_gets_exactly_what_each_preset_lists(self):
        plan = load_plan_py()
        for preset in self.shipped():
            lines = plan.plan(preset)
            self.assertEqual(lines[1:6], ["patch init", "route 1 velocity vca 0", "route 1 velocity cutoff 0", "route 2 velocity vca 0", "route 2 velocity cutoff 0"], "the removals come first")
            for line in lines:
                w = line.split()
                if line == "patch init":
                    self.s.request(0x5F)
                elif w[0] == "route":
                    depth = int(w[4]) + 99
                    self.assertIn(bytes([0x7F, 0x56]), self.s.request(0x56, int(w[1]) - 1, pro800.SOURCE_NAMES.index(w[2]), pro800.DEST_NAMES.index(w[3]), depth >> 7, depth & 0x7F), line)
            self.s.rest()
            for layer in (0, 1):
                listed = preset.get("routes", {}).get(str(layer + 1), [])
                self.assertEqual(sorted(self.s.routes(layer)), sorted((pro800.SOURCE_NAMES.index(r[0]), pro800.DEST_NAMES.index(r[1]), r[2]) for r in listed), f"{preset['name']}, layer {layer + 1}")

    def test_the_lists_of_what_the_init_sound_comes_with_are_the_firmwares(self):
        out = run_node(source("tools", "web", "sound-plan.js") + NODE_DRIVER % "report(INIT_ROUTES.map(([s, d]) => [JN.sources.indexOf(s) + 1, JN.dests.indexOf(d)]));")
        wanted = [list(r[:2]) for r in self.s.routes(0)]
        self.assertEqual(wanted, [list(r[:2]) for r in INIT_ROUTES])
        self.assertEqual(out, wanted, "sound-plan.js")
        self.assertEqual([[pro800.SOURCE_NAMES.index(s), pro800.DEST_NAMES.index(d)] for s, d in load_plan_py().INIT_ROUTES], wanted, "plan.py")

    def test_every_page_path_from_the_init_sound_goes_through_plansound(self):
        page, installer_page, shared = source("tools", "web", "index.html"), source("tools", "web", "install.html"), source("tools", "web", "sound-plan.js")
        init = js_block(page, "async function loadInitSound(")
        self.assertEqual(page.count("0x5F"), init.count("0x5F"), "only the Init buttons, which stay on it")
        self.assertIn('ack([0x5F], 3000);', init)
        self.assertIn('ask([0x5F, 1], [0x5F], 3000);', init, "0.10.0: what Perf loads")
        self.assertEqual(installer_page.count("0x5F"), 0)
        self.assertEqual(shared.count("[0x5F]"), 1)
        self.assertEqual((page.count("planSound("), installer_page.count("planSound(")), (2, 2), "Load JSON and the bank; the install and its check")


# --- the faceplate's Velocity knobs in Node: the page's own code on a small fake DOM ---

VELOCITY_DOM = r"""
class El {
  constructor(tag) {
    Object.assign(this, { tag, kids: [], attrs: {}, on: {}, className: "", style: {}, dataset: {}, text: "", nodeType: 1, q: {}, html: "" });
    const el = this;
    this.classList = { add: c => el.setClass(c, true), remove: c => el.setClass(c, false), toggle: (c, on) => el.setClass(c, on), contains: c => el.classes().includes(c) };
  }
  classes() { return [...this.className.split(" "), ...(this.attrs.class || "").split(" ")].filter(Boolean); }
  setClass(c, on) { const l = this.className.split(" ").filter(x => x && x !== c); if (on) l.push(c); this.className = l.join(" "); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(name, f) { (this.on[name] = this.on[name] || []).push(f); }
  fire(name, ev = {}) { for (const f of this.on[name] || []) f({ preventDefault() {}, button: 0, pointerId: 1, clientX: 0, clientY: 0, shiftKey: false, ...ev }); }
  append(...k) { this.kids.push(...k); }
  appendChild(k) { this.kids.push(k); return k; }
  replaceChildren(...k) { this.kids = k; }
  get children() { return this.kids; }
  set innerHTML(v) { this.html = v; this.kids = []; this.q = {}; }
  get innerHTML() { return this.html; }
  set textContent(v) { this.text = String(v); }
  get textContent() { return this.text; }
  focus() { document.activeElement = this; this.fire("focus"); }
  setPointerCapture() {}
  contains(x) { return x === this || [...this.kids, ...Object.values(this.q)].some(k => k && k.contains && k.contains(x)); }
  querySelector(sel) {
    if (!this.q[sel]) {
      this.q[sel] = new El(sel);
      if (sel === ".dp") this.q[sel].value = (this.html.match(/type="range"[^>]*value="(-?\d+)"/) || [])[1];
    }
    return this.q[sel];
  }
  find(cls) {
    if (this.classes().includes(cls)) return this;
    for (const k of this.kids) { const hit = k && k.find && k.find(cls); if (hit) return hit; }
    return null;
  }
}
const byId = {};
const document = { activeElement: null, createElement: t => new El(t), createElementNS: (_, t) => new El(t), getElementById: id => byId[id] || (byId[id] = new El("#" + id)) };
"""

VELOCITY_DRIVER = r"""
let settings = null, params = null, paramsLayer = 0, layerRoutes = null, routesLayer = null, otherRoutes = [], rawSettings = null;
let patchNameFor = -1, patchName = "", seqFor = null, midiChannelSeen = null, betweenLayers = null, skew = 0;
const realNow = Date.now.bind(Date);
Date.now = () => realNow() + skew;
let touched = null; // what the screen says about the control last touched
const window = new El("window");
const editedAt = {}, paints = [], paintAll = () => { for (const f of paints) f(); }, touch = (label, value) => { touched = value; paintAll(); };
let showFirmware = () => {};
const paintFeatures = () => {}, slotInfo = async () => null, refreshSequence = async () => {}, followChannel = () => {};
const paintSettings = paintAll;
const refreshButtons = async () => { if (betweenLayers) { const f = betweenLayers; betweenLayers = null; await f(); } }, refreshSteps = async () => {};
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function settle() { // until nothing is due: the 60 ms before a write, and every request answered
  let quiet = 0, seen = sent.length;
  while (quiet < 6) {
    await sleep(20);
    if (sent.length !== seen || answers.length) { seen = sent.length; quiet = 0; } else quiet++;
  }
}
const status = () => document.getElementById("status");
const writes = () => sent.filter(m => m[0] === 0x56);
const state = k => ({ u: (parseFloat(k.find("k-ptr").getAttribute("transform").slice(7)) + 135) / 270, val: k.find("val").text, out: k.classList.contains("out"), title: k.title });
const rows = () => byId.routes.kids.filter(tr => tr.q[".s"]).map(tr => [+tr.q[".s"].value, +tr.q[".d"].value, +tr.q[".dp"].value]);
const down = k => { const d = k.find("dial"); d.focus(); d.fire("pointerdown", { clientY: 0 }); };
const move = (k, px) => k.find("dial").fire("pointermove", { clientY: -px });
const up = k => k.find("dial").fire("pointerup");
const key = (k, name, shift) => k.find("dial").fire("keydown", { key: name, shiftKey: shift });
async function drag(k, px) { down(k); move(k, px); up(k); await settle(); }
const reach = { vca: 200 / 99, cutoff: 200 / 198 }; // pixels of drag to a step of depth
async function setLayer(n) { await setSetting(SET_ID.editLayer, n); await refreshParams(); } // what the layer buttons do
async function poll() { await refreshSettings(); await refreshParams(); }                    // what the page does every 900 ms
const firmwareMs = ms => new Promise(r => { answers.push(r); process.stdout.write(`WAIT ${ms}\n`); });
async function readRoutes(layer) {
  const d = await ask([0x57, layer], [0x57, layer]), list = [];
  for (let r = 0; r < ROUTES; r++) if (d[2 + 4 * r]) list.push({ layer, slot: r, source: d[2 + 4 * r], dest: d[3 + 4 * r], depth: (d[4 + 4 * r] << 7 | d[5 + 4 * r]) - 99, both: false });
  return list;
}
"""

VELOCITY_PAGE = None


def velocity_page():
    """Everything of index.html's that the knobs touch, cut out of it, with what they don't need stubbed."""
    global VELOCITY_PAGE
    if VELOCITY_PAGE is not None:
        return VELOCITY_PAGE
    page = source("tools", "web", "index.html")
    blocks = RANGE_BLOCKS + ["const ROUTES", "const routesFull", "const routesTaken", "const ROUTE_HOLD_TEXT", "const routeBothMask", "const LINK_FILTER", "const twoLayers", "const linkOn", "const routesBothShown",
                             "const FEATURES", "const feature", "const missingFeatures", "function linkedView(", "function linkedWrites(", "const paramLink",
                             "const wholeDepth", "async function setRoute(", "async function setRouteVia(", "async function setRouteBoth(", "let viaAnswers", "async function readVias(", "let fineAnswers", "async function readFines(", "const exactDepth", "async function refreshLayerRoutes(", "async function setSetting(",
                             "async function refreshSettings(", "async function refreshChords(", "async function refreshParams(", "const WIRE", "const options", "const viaOptions", "const viaOf", "function showVia(", "let routeRowHeld", "const ROUTE_ROW_HOLD",
                             'for (const type of ["pointerup"', "const routeRowInUse", "const ROW_CONTROLS", "function renderRoutes(", "async function loadRoutes(",
                             "function adjustable(", "const RING_R", "function dial(", "const SRC_VELOCITY", "let velocitySaid", "const velocityStatus", "function velocityUnsay(", "function velocityKnob(",
                             "const LFO_SOURCE", "async function readParams(", "async function copyParams(", "const set7D", "async function copyLfo("]
    code = "\n".join(js_block(page, b) for b in blocks)
    a = page.index("\nconst h = (tag")
    dom = page[a:page.index("\n// --- the screen", a)]
    knobs = re.findall(r"^\s*(velocityKnob\(DEST_\w+, .*\)),$", page, re.M)
    assert len(knobs) == 2, knobs
    harness = ASK_THE_SIMULATOR.replace("const offline = () => 0,", "let online = true;\nconst offline = () => online ? 0 : 1,")
    assert "online" in harness
    VELOCITY_PAGE = (source("tools", "web", "sound-plan.js") + VELOCITY_DOM + harness + VELOCITY_DRIVER + dom + "\n" + code + f"""
const cutoff = {knobs[0]}, vca = {knobs[1]};
let routesShown = null;
async function start() {{ await refreshSettings(); await refreshParams(); sent.length = 0; }}
""")
    return VELOCITY_PAGE


@unittest.skipUnless(NODE, "needs node to run the page's code")
class WebVelocityKnobs(PanelTest):
    """The faceplate's two Velocity knobs: the page's own code (its knob, adjustable(), dial(), refreshLayerRoutes(), renderRoutes(),
    modRange(), settings and layer changes) on a small fake DOM, talking to the simulated firmware. MOD ASSIGN is the panel's."""

    def setUp(self):
        super().setUp()
        for layer in (0, 1):  # about the knob, not init's depth (InitRoutes has that): a depth it can turn both ways
            self.s.request(0x56, layer, VELOCITY, VCA, (40 + 99) >> 7, (40 + 99) & 0x7F)

    def run_page(self, body, synth=None):
        return run_node(velocity_page() + NODE_DRIVER % ("await start();\n" + body), synth or self.s)

    def route(self, layer, source, dest, depth):
        self.s.request(0x56, layer, source, dest, (depth + 99) >> 7, (depth + 99) & 0x7F)

    def fill(self, keep=()):
        """All 16 slots of layer 1 taken, with these of the init sound's destinations kept."""
        for dest in (VCA, CUTOFF):
            if dest not in keep:
                self.route(0, VELOCITY, dest, 0)
        self.s.rest()
        free = 16 - len(self.s.routes(0))
        for dest in [d for d in range(46) if d not in (VCA, CUTOFF)][:free]:
            self.route(0, 1, dest, 5)
        self.assertEqual(len(self.s.routes(0)), 16)

    # Copy LFO
    def test_copy_lfo_leaves_the_routes_it_replaced_unmarked(self):
        """It sends 0, then the other layer's. Back from 0 inside the hold a route keeps its mark, but this one's the other
        layer's now, and a mark would play it on that layer's voices on top of their own."""
        env3 = pro800.SOURCE_NAMES.index("env3")
        self.route(0, 1, CUTOFF, 50)
        self.route(1, 1, CUTOFF, 30)  # slot 2, past the init sound's two
        self.route(1, 1, VCA, 25)  # slot 3: this layer's only, it goes
        self.route(1, env3, CUTOFF, 10)  # slot 4: not this LFO's, stays as it was
        self.s.setting("route-both 2 (0-7)", 0b11100)
        out = self.run_page("""
        await setLayer(1);
        await copyLfo(0);
        report(document.getElementById("status").textContent);
        """)
        self.assertIn("Copied layer 1's LFO 1 to layer 2", out)
        self.assertEqual(self.s.route_table(1)[2:5], [(1, CUTOFF, 50), (1, VCA, 0), (env3, CUTOFF, 10)])
        self.assertEqual(self.s.settings()["route-both 2 (0-7)"], 0b10000, "only the other source's keeps its mark")
        self.s.rest()
        self.assertEqual(self.s.route_table(1)[3], (0, 0, 0), "and the one it dropped goes")
        self.assertEqual(self.s.settings()["route-both 2 (0-7)"], 0b10000)

    # reading
    def test_they_read_the_init_sounds_routes(self):
        out = self.run_page("report({ vca: state(vca), cutoff: state(cutoff), writes: writes() });")
        self.assertEqual((out["vca"]["val"], out["cutoff"]["val"]), ("+40", "+20"))
        self.assertAlmostEqual(out["vca"]["u"], 40 / 99, delta=0.002)
        self.assertAlmostEqual(out["cutoff"]["u"], (20 + 99) / 198, delta=0.002)
        self.assertFalse(out["vca"]["out"] or out["cutoff"]["out"])
        self.assertEqual(out["writes"], [], "reading writes nothing")

    def test_no_route_reads_off(self):
        self.s.no_routes()
        out = self.run_page("report({ vca: state(vca), cutoff: state(cutoff) });")
        self.assertEqual((out["vca"]["val"], out["cutoff"]["val"]), ("off", "off"))
        self.assertAlmostEqual(out["vca"]["u"], 0, delta=0.002)
        self.assertAlmostEqual(out["cutoff"]["u"], 0.5, delta=0.002, msg="bipolar: 0 is the middle")

    def test_each_section_has_its_own_with_its_range_and_a_title(self):
        page = source("tools", "web", "index.html")
        filter_part = page[page.index('ps("flt", "Filter"'):page.index('h("div", { class: "loose loose-tune" }')]
        amp_part = page[page.index('ps("amp", "Amplifier"'):page.index('h("div", { class: "loose loose-vol" }')]
        self.assertRegex(filter_part, r'velocityKnob\(DEST_CUTOFF, "Velocity → Cutoff", -99, "[^"]*semitones[^"]*"\)')
        self.assertRegex(amp_part, r'velocityKnob\(DEST_VCA, "Velocity → VCA", 0, "[^"]*0\.65[^"]*"\)')
        self.assertNotIn("velocityKnob", page[page.index("function buildPanel"):page.index('ps("flt", "Filter"')])
        for part in (filter_part, amp_part):
            self.assertRegex(part, r"pflex\(\s*velocityKnob\(", "first in the envelope row, so the two sections' rows keep lining up")
        out = self.run_page("report({ vca: vca.find('lbl').kids, cutoff: cutoff.find('lbl').kids, classes: vca.className, label: vca.find('dial').getAttribute('aria-label') });")
        self.assertEqual((out["vca"], out["cutoff"]), (["Velocity"], ["Velocity"]))
        self.assertNotIn("shared", out["classes"], "per layer: not the amber of a shared knob")
        self.assertEqual(out["label"], "Velocity → VCA")

    # writing
    def test_a_turn_writes_the_edit_layers_route(self):
        out = self.run_page("""
        await drag(vca, 30 * reach.vca); const a = state(vca);
        await drag(cutoff, -50 * reach.cutoff); const b = state(cutoff);
        report({ a, b, writes: writes() });
        """)
        self.assertEqual((out["a"]["val"], out["b"]["val"]), ("+70", "-30"))
        self.assertEqual(self.s.routes(0), [(VELOCITY, VCA, 70), (VELOCITY, CUTOFF, -30)], "in their slots")
        self.assertEqual(self.s.routes(1), KNOB_START, "the other layer's stay")
        self.assertEqual(out["writes"], [[0x56, 0, VELOCITY, VCA, 1, 169 - 128], [0x56, 0, VELOCITY, CUTOFF, 0, 69]], "setRoute's message, byte for byte")

    def test_a_drag_sends_once_it_rests(self):
        out = self.run_page("""
        down(vca);
        for (let px = 1; px <= 40; px++) move(vca, px);
        const during = [state(vca).val, writes().length];
        up(vca);
        await settle();
        report({ during, after: state(vca).val, writes: writes() });
        """)
        self.assertEqual(out["during"], ["+60", 0], "the knob moves at once; nothing's sent while it moves")
        self.assertEqual(len(out["writes"]), 1, "like settingKnob: the last position")
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 60))

    def test_fine_steps_add_up(self):
        """Shift + an arrow is under one depth step: they have to add up rather than round away."""
        out = self.run_page("""
        for (let k = 0; k < 6; k++) key(vca, "ArrowUp", true);
        await settle();
        report({ val: state(vca).val });
        """)
        self.assertEqual(out["val"], "+41")
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 41))

    def test_zero_removes_and_turning_creates(self):
        out = self.run_page("""
        await drag(vca, -40 * reach.vca); const a = state(vca);
        await drag(cutoff, -20 * reach.cutoff); const b = state(cutoff);
        const gone = writes().length;
        await drag(vca, 25 * reach.vca); const c = state(vca);
        await drag(cutoff, -25 * reach.cutoff); const d = state(cutoff);
        report({ a, b, c, d, gone });
        """)
        self.assertEqual((out["a"]["val"], out["b"]["val"]), ("off", "off"))
        self.assertEqual((out["c"]["val"], out["d"]["val"]), ("+25", "-25"))
        self.assertEqual(self.s.routes(0), [(VELOCITY, VCA, 25), (VELOCITY, CUTOFF, -25)])
        self.assertEqual(self.s.routes(1), KNOB_START)
        self.assertEqual(out["gone"], 2)

    def test_zero_really_takes_the_route_away(self):
        self.run_page("await drag(vca, -40 * reach.vca); report({});")
        self.assertEqual(self.s.routes(0), [(VELOCITY, VCA, 0), KNOB_START[1]], "after 5 s at 0")
        self.s.rest()
        self.assertEqual(self.s.routes(0), KNOB_START[1:])
        self.run_page("await drag(cutoff, -20 * reach.cutoff); report({});")
        self.s.rest()
        self.assertEqual(self.s.routes(0), [])

    def test_the_vca_knob_stops_at_0_and_the_cutoff_knob_at_both_ends(self):
        out = self.run_page("""
        await drag(vca, -400); const low = state(vca);
        await drag(cutoff, 400); const high = state(cutoff);
        await drag(cutoff, -900); const under = state(cutoff);
        await drag(vca, 900); const top = state(vca);
        report({ low, high, under, top });
        """)
        self.assertEqual([out[k]["val"] for k in ("low", "high", "under", "top")], ["off", "+99", "-99", "+99"])
        self.assertEqual(sorted(self.s.routes(0)), sorted([(VELOCITY, VCA, 99), (VELOCITY, CUTOFF, -99)]))

    def test_a_double_click_takes_it_off(self):
        out = self.run_page("""
        vca.find("dial").fire("dblclick"); cutoff.find("dial").fire("dblclick");
        await settle();
        report({ vca: state(vca), cutoff: state(cutoff) });
        """)
        self.assertEqual((out["vca"]["val"], out["cutoff"]["val"]), ("off", "off"))
        self.assertAlmostEqual(out["cutoff"]["u"], 0.5, delta=0.002)
        self.s.rest()
        self.assertEqual(self.s.routes(0), [])

    # all 16 slots
    def test_a_full_table_says_so_and_the_knob_stays_true(self):
        self.fill()
        before = self.s.routes(0)
        out = self.run_page("""
        await drag(vca, 30 * reach.vca); const a = state(vca);
        const said = status().text, cls = status().className;
        status().text = "";
        await drag(cutoff, 30 * reach.cutoff);
        report({ a, c: state(cutoff), said, cls, again: status().text, writes: writes() });
        """)
        self.assertEqual((out["a"]["val"], out["c"]["val"]), ("off", "off"))
        self.assertAlmostEqual(out["a"]["u"], 0, delta=0.002)
        self.assertAlmostEqual(out["c"]["u"], 0.5, delta=0.002)
        self.assertIn("All 16 mod routes of layer 1 are in use", out["said"])
        self.assertEqual(out["cls"], "bad")
        self.assertIn("All 16", out["again"])
        self.assertEqual(out["writes"], [], "it knows without asking")
        self.assertEqual(self.s.routes(0), before)

    def test_a_table_that_filled_behind_the_pages_back(self):
        """The page saw a free slot; the panel took it. The firmware doesn't answer, and the knob goes back."""
        self.fill()
        self.route(0, 1, 0, 0)  # one free when the page reads them
        out = self.run_page("""
        await ask([0x56, 0, 1, 0, 0, 104], [0x7F, 0x56]);
        await drag(vca, 30 * reach.vca);
        report({ state: state(vca), said: status().text, count: writes().length });
        """)
        self.assertEqual(out["state"]["val"], "off")
        self.assertAlmostEqual(out["state"]["u"], 0, delta=0.002)
        self.assertIn("All 16 mod routes", out["said"])
        self.assertEqual(out["count"], 2, "the panel's stand-in and the knob's")

    def test_a_route_that_exists_can_still_be_turned_with_the_table_full(self):
        self.fill(keep=(VCA, CUTOFF))
        out = self.run_page("""
        await drag(vca, 10 * reach.vca); const a = state(vca);
        await drag(vca, -50 * reach.vca); const b = state(vca);
        await drag(vca, 15 * reach.vca); const c = state(vca);
        report({ a, b, c, said: status().text });
        """)
        self.assertEqual([out[k]["val"] for k in "abc"], ["+50", "off", "+15"], "taking it off frees the slot it comes back to")
        self.assertEqual(out["said"], "")
        self.assertIn((VELOCITY, VCA, 15), self.s.routes(0))

    # the layer
    def test_a_layer_change_follows(self):
        self.route(1, VELOCITY, VCA, 77)
        self.route(1, VELOCITY, CUTOFF, -12)
        out = self.run_page("""
        const one = [state(vca).val, state(cutoff).val];
        await setLayer(1);
        const two = [state(vca).val, state(cutoff).val];
        await drag(vca, -7 * reach.vca);
        const edited = state(vca).val;
        await setLayer(0);
        report({ one, two, edited, back: [state(vca).val, state(cutoff).val], writes: writes() });
        """)
        self.assertEqual(out["one"], ["+40", "+20"])
        self.assertEqual(out["two"], ["+77", "-12"])
        self.assertEqual(out["edited"], "+70")
        self.assertEqual(out["back"], ["+40", "+20"])
        self.assertEqual(self.s.routes(0), KNOB_START, "only the edit layer's")
        self.assertEqual(self.s.routes(1), [(VELOCITY, VCA, 70), (VELOCITY, CUTOFF, -12)])

    def test_a_turn_stays_on_the_layer_it_was_made_on(self):
        """Layer 2 comes up inside the 60 ms before the write: layer 1 still gets it, and layer 2's knob doesn't show it."""
        self.route(1, VELOCITY, VCA, 77)
        out = self.run_page("""
        const theirs = await readRoutes(1);
        down(vca); move(vca, 20 * reach.vca); up(vca);
        const moved = state(vca).val;
        paramsLayer = 1; layerRoutes = theirs; routesLayer = 1; paintAll();
        const there = state(vca).val;
        await settle();
        report({ moved, there, after: state(vca).val });
        """)
        self.assertEqual(out["moved"], "+60")
        self.assertEqual(out["there"], "+77", "not the other layer's turn")
        self.assertEqual(out["after"], "+77")
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 60))
        self.assertEqual(self.s.routes(1)[0], (VELOCITY, VCA, 77))

    def test_a_drag_begun_on_one_layer_doesnt_write_to_the_other(self):
        """The panel's layer buttons, with the mouse held on the knob: where the drag began was the first layer's depth."""
        self.route(1, VELOCITY, VCA, 77)
        out = self.run_page("""
        down(vca);
        await setLayer(1);
        sent.length = 0;
        move(vca, 1); move(vca, 30);
        const during = state(vca).val;
        await settle();
        const dropped = writes();
        up(vca);
        await drag(vca, 3 * reach.vca); // a new drag starts from what the layer has
        report({ during, dropped, after: state(vca).val });
        """)
        self.assertEqual(out["during"], "+77")
        self.assertEqual(out["dropped"], [])
        self.assertEqual(out["after"], "+80")
        self.assertEqual(self.s.routes(0), KNOB_START)
        self.assertEqual(self.s.routes(1)[0], (VELOCITY, VCA, 80))

    def test_a_fine_drag_keeps_the_layer_it_began_on(self):
        """Shift starts the drag anew at each move: that mustn't take it to the new layer."""
        self.route(1, VELOCITY, VCA, 77)
        out = self.run_page("""
        down(vca);
        vca.find("dial").fire("pointermove", { clientY: -60, shiftKey: true });
        vca.find("dial").fire("pointermove", { clientY: -120, shiftKey: true });
        const fine = state(vca).val;
        await settle();
        await setLayer(1);
        sent.length = 0;
        vca.find("dial").fire("pointermove", { clientY: -180, shiftKey: true });
        up(vca);
        await settle();
        report({ fine, val: state(vca).val, writes: writes() });
        """)
        self.assertEqual(out["fine"], "+50", "on the layer it began on, one fine move after another adds up")
        self.assertEqual(out["val"], "+77")
        self.assertEqual(out["writes"], [])
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 50))
        self.assertEqual(self.s.routes(1)[0], (VELOCITY, VCA, 77))

    def test_between_a_layer_change_and_its_routes_nothing_is_written(self):
        """refreshParams() moves paramsLayer first and reads the routes last: the old list isn't the new layer's."""
        self.route(1, VELOCITY, VCA, 77)
        out = self.run_page("""
        let inside = null;
        betweenLayers = async () => {
          sent.length = 0;
          const shown = state(vca).val;
          await drag(vca, 20 * reach.vca);
          inside = { shown, writes: writes(), val: state(vca).val, layer: paramsLayer };
        };
        await setLayer(1);
        report({ inside, after: state(vca).val });
        """)
        self.assertEqual(out["inside"]["layer"], 1)
        self.assertEqual(out["inside"]["shown"], "+40", "it keeps what it showed until the routes are in")
        self.assertEqual(out["inside"]["writes"], [])
        self.assertEqual(out["after"], "+77")
        self.assertEqual(self.s.routes(0), KNOB_START)

    # the matrix, the rings and the knob are one thing
    def test_a_knob_turn_repaints_the_matrix_and_the_ring(self):
        out = self.run_page("""
        const before = { rows: rows(), ring: modRange(P.CUTOFF).hi };
        await drag(cutoff, 40 * reach.cutoff);
        const turned = { rows: rows(), ring: modRange(P.CUTOFF).hi };
        await drag(vca, -40 * reach.vca);
        const removed = rows();
        await drag(vca, 11 * reach.vca);
        await drag(cutoff, -160 * reach.cutoff);
        report({ before, turned, removed, created: rows(), ring: [modRange(P.CUTOFF).lo, modRange(P.CUTOFF).hi] });
        """)
        self.assertEqual(out["before"]["rows"], [[VELOCITY, VCA, 40], [VELOCITY, CUTOFF, 20]])
        self.assertAlmostEqual(out["before"]["ring"], 20 / 99 * 96 / 120, delta=0.001)
        self.assertEqual(out["turned"]["rows"], [[VELOCITY, VCA, 40], [VELOCITY, CUTOFF, 60]])
        self.assertAlmostEqual(out["turned"]["ring"], 60 / 99 * 96 / 120, delta=0.001)
        self.assertEqual(out["removed"], [[VELOCITY, VCA, 0], [VELOCITY, CUTOFF, 60]], "at 0 the row stays, for 5 s")
        self.assertEqual(sorted(out["created"]), sorted([[VELOCITY, VCA, 11], [VELOCITY, CUTOFF, -99]]))
        self.assertAlmostEqual(out["ring"][0], 96 / 120, delta=0.001, msg="a negative route's ring goes down")
        self.assertEqual(out["ring"][1], 0)

    def test_the_depth_box_takes_tenths(self):
        out = self.run_page("""
        const tr = byId.routes.kids[0], box = tr.q[".dn"], range = tr.q[".dp"];
        const was = box.value;
        box.value = "53.5"; box.oninput();
        const slider = range.value;
        await box.onchange();
        await settle();
        const row = byId.routes.kids[0];
        const after = [row.q[".dn"].value, layerRoutes[0].depth];
        // the slider puts it back on a whole number, on the synth too
        row.q[".dp"].value = "60"; row.q[".dp"].oninput();
        await settle();
        await loadRoutes();
        report({ was, slider, after, whole: [byId.routes.kids[0].q[".dn"].value, layerRoutes[0].depth] });
        """)
        self.assertEqual(out["slider"], "53.5", "the slider follows, and the browser puts it on a step")
        self.assertEqual(out["after"], [53.5, 53.5])
        self.assertEqual(out["whole"], [60, 60])

    def test_a_matrix_edit_repaints_the_knob(self):
        out = self.run_page("""
        // the slider: no refresh of the row under it, the knob and the firmware follow
        const tr = byId.routes.kids[0], range = tr.q[".dp"];
        document.activeElement = range;
        range.value = "55"; range.oninput();
        await settle();
        const slid = [state(vca).val, modRange(P.CUTOFF).hi, byId.routes.kids[0] === tr];
        document.activeElement = null;
        // a cutoff ring and knob from the same place
        const row2 = byId.routes.kids[1];
        document.activeElement = row2.q[".dp"];
        row2.q[".dp"].value = "-33"; row2.q[".dp"].oninput();
        await settle();
        const slid2 = [state(cutoff).val, modRange(P.CUTOFF).lo];
        document.activeElement = null;
        await loadRoutes();
        // Remove
        await byId.routes.kids[0].q[".del"].onclick();
        await settle();
        const removed = state(vca).val;
        // a new route, as Add route does
        await setRoute(0, 4, 9, 33); await loadRoutes();
        const added = state(vca).val;
        // moved to another source: not the velocity route any more
        const moved = byId.routes.kids.find(r => +r.q[".d"].value === 9);
        const place = byId.routes.kids.indexOf(moved);
        moved.q[".s"].value = "5"; await moved.q[".s"].onchange();
        await settle();
        report({ slid, slid2, removed, added, left: state(vca).val, cut: state(cutoff).val, place, rows: rows() });
        """)
        self.assertEqual(out["slid"][0], "+55")
        self.assertTrue(out["slid"][2], "the row under the slider isn't rebuilt")
        self.assertEqual(out["slid2"][0], "-33")
        self.assertAlmostEqual(out["slid2"][1], 33 / 99 * 96 / 120, delta=0.001)
        self.assertEqual(out["removed"], "off")
        self.assertEqual(out["added"], "+33")
        self.assertEqual(out["left"], "off")
        self.assertEqual(out["cut"], "-33")
        self.assertEqual(out["rows"][out["place"]], [5, 9, 33], "the row itself changes")
        self.assertEqual([r for r in out["rows"] if r[0] == 4 and r[1] == 9], [], "with nothing left behind to wait out its hold")

    def test_mod_assign_on_the_synth_reaches_the_knob(self):
        s = self.s

        class Panel:
            """A request 0E stands for the person at the synth, in the middle of the page's run."""
            @staticmethod
            def request(*data):
                if data[0] != 0x0E:
                    return s.request(*data)
                s.press("ASSIGN")
                s.press("4")
                s.pot("AMP_SUS", 0.3)
                s.pot("AMP_SUS", 0.35)
                s.turn(5)
                s.press("ASSIGN")
                return b""

        out = self.run_page("""
        const before = state(vca).val;
        await ask([0x0E], [0x0E]);
        await poll();
        report({ before, after: state(vca), rows: rows() });
        """, Panel)
        depth = dict((r[1], r[2]) for r in s.routes(0))[VCA]
        self.assertEqual(out["before"], "+40")
        self.assertNotEqual(depth, 40, "the panel changed it")
        self.assertEqual(out["after"]["val"], f"{depth:+d}")
        self.assertAlmostEqual(out["after"]["u"], max(depth, 0) / 99, delta=0.002)
        self.assertIn([VELOCITY, VCA, depth], out["rows"])

    # focus stays on a slider after the mouse lets go, in Chrome for as long as nothing else is clicked
    SLIDE = """
        const tr = byId.routes.kids[0], range = tr.q[".dp"];
        range.fire("pointerdown");
        document.activeElement = range;
        range.value = "55"; range.oninput();
        await settle();
        window.fire("pointerup");
        sent.length = 0;
        """

    def test_the_matrix_follows_mod_assign_with_focus_left_on_a_slider(self):
        out = self.run_page(self.SLIDE + """
        await ask([0x56, 0, 4, 9, 0, 99 + 77], [0x7F, 0x56]); // as MOD ASSIGN would
        skew = 2000;
        await poll();
        const row = byId.routes.kids[0];
        report({ knob: state(vca).val, rows: rows(), rebuilt: row !== tr, focused: document.activeElement === row.q[".dp"] });
        """)
        self.assertEqual(out["knob"], "+77")
        self.assertEqual(out["rows"], [[VELOCITY, VCA, 77], [VELOCITY, CUTOFF, 20]])
        self.assertTrue(out["rebuilt"])
        self.assertTrue(out["focused"], "the rebuilt row's slider has the focus back")

    def test_the_matrix_follows_a_layer_change_with_focus_left_on_a_slider(self):
        self.route(1, VELOCITY, VCA, 77)
        out = self.run_page(self.SLIDE + """
        skew = 2000;
        await setLayer(1);
        const row = byId.routes.kids[0], knob = state(vca).val, list = rows();
        row.q[".dp"].value = "70"; row.q[".dp"].oninput();
        await settle();
        report({ knob, list, writes: writes() });
        """)
        self.assertEqual(out["knob"], "+77")
        self.assertEqual(out["list"], [[VELOCITY, VCA, 77], [VELOCITY, CUTOFF, 20]], "layer 2's rows")
        self.assertEqual(out["writes"], [[0x56, 1, VELOCITY, VCA, 1, 169 - 128]], "and they write to layer 2")
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 55))
        self.assertEqual(self.s.routes(1)[0], (VELOCITY, VCA, 70))

    def test_a_row_being_held_isnt_rebuilt_until_it_lets_go(self):
        out = self.run_page("""
        const tr = byId.routes.kids[0], range = tr.q[".dp"];
        range.fire("pointerdown");
        document.activeElement = range;
        range.value = "55"; range.oninput();
        await settle();
        await ask([0x56, 0, 4, 9, 0, 99 + 77], [0x7F, 0x56]);
        skew = 2000;
        await poll();
        const during = [byId.routes.kids[0] === tr, tr.q[".dp"].value];
        window.fire("pointerup");
        await poll();
        report({ during, rows: rows() });
        """)
        self.assertEqual(out["during"], [True, "55"], "held long past the slider's last move")
        self.assertEqual(out["rows"][0], [VELOCITY, VCA, 77])

    def test_a_slider_lets_go_with_its_change_too(self):
        """If the pointer's release went astray: a slider's change event is the release as well."""
        out = self.run_page("""
        const tr = byId.routes.kids[0], range = tr.q[".dp"];
        range.fire("pointerdown");
        range.value = "55"; range.oninput();
        await settle();
        range.onchange();
        await ask([0x56, 0, 4, 9, 0, 99 + 77], [0x7F, 0x56]);
        skew = 2000;
        await poll();
        report({ rows: rows() });
        """)
        self.assertEqual(out["rows"][0], [VELOCITY, VCA, 77])

    def test_an_open_menu_isnt_rebuilt_for_a_while(self):
        """A rebuild would shut a route's source or destination menu. Nothing tells a page when one is closed without a pick, so
        the hold has an end, and the rows catch up then."""
        out = self.run_page("""
        const tr = byId.routes.kids[0];
        tr.q[".s"].onpointerdown();
        await ask([0x56, 0, 4, 9, 0, 99 + 77], [0x7F, 0x56]);
        await poll();
        const open = [byId.routes.kids[0] === tr, state(vca).val];
        skew = 11000;
        await poll();
        report({ open, rows: rows() });
        """)
        self.assertEqual(out["open"], [True, "+77"], "the knob doesn't wait")
        self.assertEqual(out["rows"][0], [VELOCITY, VCA, 77])

    def test_a_rebuild_elsewhere_doesnt_take_the_focus(self):
        out = self.run_page("""
        vca.find("dial").focus();
        const dial = document.activeElement;
        await ask([0x56, 0, 4, 9, 0, 99 + 77], [0x7F, 0x56]);
        await poll();
        report({ same: document.activeElement === dial, rows: rows() });
        """)
        self.assertTrue(out["same"])
        self.assertEqual(out["rows"][0], [VELOCITY, VCA, 77])

    def test_a_slider_edit_then_back_to_the_depth_last_built(self):
        """The rows were built at 40, the slider made it 30, the knob made it 40 again: the list reads as it did when they were built."""
        out = self.run_page(self.SLIDE.replace('"55"', '"30"') + """
        document.activeElement = null;
        await drag(vca, 10 * reach.vca);
        const knob = state(vca).val;
        skew = 2000;
        await poll();
        report({ knob, rows: rows() });
        """)
        self.assertEqual(out["knob"], "+40")
        self.assertEqual(out["rows"][0], [VELOCITY, VCA, 40])

    def test_the_same_with_mod_assign_going_back(self):
        out = self.run_page(self.SLIDE.replace('"55"', '"30"') + """
        await ask([0x56, 0, 4, 9, 0, 99 + 40], [0x7F, 0x56]);
        skew = 2000;
        await poll();
        report({ knob: state(vca).val, rows: rows() });
        """)
        self.assertEqual(out["knob"], "+40")
        self.assertEqual(out["rows"][0], [VELOCITY, VCA, 40])

    # a depth the knob can't reach
    def test_a_negative_vca_route_shows_and_is_left_alone(self):
        self.route(0, VELOCITY, VCA, -40)
        before = self.s.routes(0)
        out = self.run_page("""
        const a = state(vca);
        down(vca); up(vca);                                  // a click
        down(vca); move(vca, -30); move(vca, -90); up(vca);  // down past the bottom
        key(vca, "ArrowDown", false);
        vca.find("dial").fire("wheel", { deltaY: 100 });
        await settle();
        await poll();
        report({ a, b: state(vca), rows: rows(), writes: writes() });
        """)
        for k in ("a", "b"):
            self.assertEqual(out[k]["val"], "-40", "as it is")
            self.assertTrue(out[k]["out"], "marked, so the number stays in sight")
            self.assertAlmostEqual(out[k]["u"], 0, delta=0.002, msg="the knob at its end")
            self.assertIn("does nothing here on its own", out[k]["title"])
        self.assertEqual(out["writes"], [], "not rewritten")
        self.assertEqual(self.s.routes(0), before)
        self.assertIn([VELOCITY, VCA, -40], out["rows"], "the matrix says the same")

    def test_a_negative_routes_readout_claims_no_reach(self):
        """VelocityOnTheEngine measures it: on its own it adds nothing. So neither the knob's screen nor the matrix's Range cell
        can say "0 to +40 %", with the title beside it saying nothing happens."""
        self.route(0, VELOCITY, VCA, -40)
        out = self.run_page("""
        vca.find("dial").focus();
        const screen = touched, cell = byId.routes.kids[0].q[".range"].kids[0];
        await ask([0x56, 0, 4, 43, 0, 99 - 40], [0x7F, 0x56]);
        await refreshLayerRoutes();
        const volume = modRange(P.VOL);
        report({ screen, cell, vca: routeSwing(4, 9, -40), volume: routeSwing(4, 43, -40), ring: [volume.lo, volume.hi], who: volume.who,
                 up: routeSwing(4, 9, 40).text, fixed: routeSwing(25, 9, -40).text, cutoff: routeSwing(4, 3, -40).text });
        """)
        self.assertEqual(out["screen"], "-40 (none on its own)")
        self.assertEqual(out["cell"], "none on its own")
        self.assertEqual((out["vca"]["lo"], out["vca"]["hi"]), (0, 0))
        self.assertEqual(out["volume"]["text"], "none on its own", "synth.c treats the master volume the same")
        self.assertEqual(out["ring"], [0, 0], "no ring on the volume knob")
        self.assertEqual(len(out["who"]), 1, "but its title still lists the route")
        self.assertEqual(out["up"], "-40 % to 0", "the depths that do something read as they did")
        self.assertEqual(out["fixed"], "-40 %", "FIX turns it down whichever the sign")
        self.assertEqual(out["cutoff"], "-39 st to 0", "a negative route on the cutoff does close it")

    def test_the_mark_for_a_depth_out_of_reach_keeps_the_number_in_sight(self):
        page = source("tools", "web", "index.html")
        self.assertRegex(page, r"\.face:not\(\.values\) \.knob\.out \.val \{ visibility: visible;")
        self.assertRegex(page, r"\.face:not\(\.values\) \.knob\.out \.lbl \{ visibility: hidden;")

    def test_turning_up_replaces_a_negative_route(self):
        self.route(0, VELOCITY, VCA, -40)
        out = self.run_page("""
        down(vca); move(vca, 30 * reach.vca); const moved = state(vca); up(vca);
        await settle();
        report({ moved, after: state(vca) });
        """)
        self.assertEqual(out["moved"]["val"], "+30")
        self.assertEqual(out["after"]["val"], "+30")
        self.assertFalse(out["after"]["out"])
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 30))

    def test_a_double_click_removes_a_negative_route(self):
        self.route(0, VELOCITY, VCA, -40)
        out = self.run_page("""vca.find("dial").fire("dblclick"); await settle(); report({ after: state(vca) });""")
        self.assertEqual(out["after"]["val"], "off")
        self.s.rest()
        self.assertEqual(self.s.routes(0), KNOB_START[1:])

    def test_a_negative_cutoff_route_is_in_reach(self):
        self.route(0, VELOCITY, CUTOFF, -33)
        out = self.run_page("report({ cutoff: state(cutoff) });")
        self.assertEqual(out["cutoff"]["val"], "-33")
        self.assertFalse(out["cutoff"]["out"])
        self.assertAlmostEqual(out["cutoff"]["u"], 66 / 198, delta=0.002)

    # both layers, links
    def both_on_layer_1(self, slot=0):
        self.s.setting("layers", 2)  # stack
        self.s.setting("route-both 1 (0-7)", 1 << slot)

    def test_a_both_route_is_one_route_and_says_so(self):
        self.both_on_layer_1()
        out = self.run_page("""
        const a = state(vca);
        await drag(vca, 10 * reach.vca);
        report({ a, b: state(vca), other: state(cutoff) });
        """)
        self.assertEqual(out["a"]["val"], "+40 both")
        self.assertIn("Marked Both", out["a"]["title"])
        self.assertEqual(out["b"]["val"], "+50 both", "its depth moves and it stays marked")
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 50))
        self.assertEqual(self.s.settings()["route-both 1 (0-7)"], 1)
        self.assertEqual(self.s.routes(1), KNOB_START, "layer 2's own route isn't this one")
        self.assertEqual(out["other"]["val"], "+20", "not marked")

    def test_the_matrix_keeps_a_row_at_0_and_drops_it_when_it_goes(self):
        self.both_on_layer_1()
        out = self.run_page("""
        await loadRoutes();
        const tr = byId.routes.kids[0], range = tr.q[".dp"];
        range.value = "0"; range.oninput();
        await settle();
        range.onchange();
        await settle();
        skew += 2000; // past the moment a row is left alone after its slider moves
        await poll();
        const cell = byId.routes.kids[0].q[".range"].kids;
        const at0 = { rows: rows(), cell: [cell[0], cell[1].kids], both: layerRoutes[0].both, knob: state(vca).val };
        await firmwareMs(5050);
        const stale = rows();
        await poll();
        report({ at0, stale, after: rows(), masks: routeBothMask(0), knob: state(vca).val, button: byId["add-route"].disabled });
        """)
        self.assertEqual(out["at0"]["rows"], [[VELOCITY, VCA, 0], [VELOCITY, CUTOFF, 20]], "it keeps the row")
        self.assertEqual(out["at0"]["cell"][0], "–", "the Range cell has no reach to show")
        self.assertIn("5 s at 0 takes it off", " ".join(out["at0"]["cell"][1]))
        self.assertTrue(out["at0"]["both"], "with its mark")
        self.assertEqual(out["at0"]["knob"], "off both")
        self.assertEqual(out["stale"], out["at0"]["rows"], "until the next read")
        self.assertEqual(out["after"], [[VELOCITY, CUTOFF, 20]], "the poll drops it")
        self.assertEqual(out["masks"], 0)
        self.assertEqual(out["knob"], "off")
        self.assertFalse(out["button"])

    def test_remove_leaves_the_row_at_0_until_it_goes(self):
        out = self.run_page("""
        await loadRoutes();
        await byId.routes.kids[0].q[".del"].onclick();
        await settle();
        const pressed = rows();
        await firmwareMs(5050);
        await poll();
        report({ pressed, after: rows() });
        """)
        self.assertEqual(out["pressed"], [[VELOCITY, VCA, 0], [VELOCITY, CUTOFF, 20]])
        self.assertEqual(out["after"], [[VELOCITY, CUTOFF, 20]])

    def test_a_full_table_counts_none_at_0(self):
        self.fill()
        self.route(0, 1, 0, 0)  # one of the 16 on its way
        out = self.run_page("""
        await loadRoutes();
        const button = byId["add-route"].disabled;
        await drag(vca, 30 * reach.vca);
        report({ button, said: status().text, val: state(vca).val, rows: rows().length });
        """)
        self.assertFalse(out["button"], "Add route: one of the 16 is at 0")
        self.assertEqual(out["said"], "", "no message that all 16 are in use")
        self.assertEqual(out["val"], "+30")
        self.assertEqual(out["rows"], 16)
        self.assertIn((VELOCITY, VCA, 30), self.s.routes(0), "it took the slot")
        self.assertNotIn((1, 0, 0), self.s.routes(0))

    def test_a_lane_or_morph_menu_reads_past_a_row_at_0(self):
        page = source("tools", "web", "index.html")
        self.assertIn("layerRoutes.find(r => r.source === src && r.depth)", page)
        self.assertIn("layerRoutes.find(r => r.dest === MORPH_DEST && r.depth)", page)

    def test_a_json_read_back_leaves_a_row_at_0_out(self):
        page = source("tools", "web", "index.html")
        self.assertIn("!== 99) list.push([JN.sources", page, "a save wouldn't keep it, and plan.py refuses one")

    def test_zero_on_a_both_route_says_what_5_s_there_does(self):
        self.both_on_layer_1()
        out = self.run_page("""
        await drag(vca, -40 * reach.vca);
        const said = status().text;
        await drag(vca, 40 * reach.vca);
        report({ said, back: state(vca) });
        """)
        self.assertIn("5 s there takes it off, and its Both mark with it", out["said"])
        self.assertEqual(self.s.settings()["route-both 1 (0-7)"], 1, "inside the hold it keeps it")
        self.assertEqual(out["back"]["val"], "+40 both", "back from 0 it's as it was")
        self.run_page("await drag(vca, -40 * reach.vca); report({});")
        self.s.rest()
        self.assertEqual(self.s.settings()["route-both 1 (0-7)"], 0, "the firmware takes it off with the route")
        out = self.run_page("await drag(vca, 40 * reach.vca); report({ back: state(vca) });")
        self.assertEqual(out["back"]["val"], "+40", "it comes back as this layer's alone")

    # what the status line says
    def test_a_later_turn_takes_the_both_message_away(self):
        self.both_on_layer_1()
        out = self.run_page("""
        await drag(vca, -40 * reach.vca);
        const said = status().text;
        await drag(vca, 10 * reach.vca);
        report({ said, after: status().text, cls: status().className, val: state(vca).val });
        """)
        self.assertIn("Both mark", out["said"])
        self.assertEqual(out["after"], "", "the route is live again: the message isn't true any more")
        self.assertEqual(out["val"], "+10 both")

    def test_a_turn_that_works_takes_the_full_table_message_away(self):
        self.fill()
        out = self.run_page("""
        await drag(vca, 30 * reach.vca);
        const said = [status().text, status().className];
        await ask([0x56, 0, 1, 4, 0, 99], [0x7F, 0x56]); // a slot comes free
        await poll();
        await drag(vca, 30 * reach.vca);
        report({ said, after: [status().text, status().className], val: state(vca).val });
        """)
        self.assertIn("All 16", out["said"][0])
        self.assertEqual(out["said"][1], "bad")
        self.assertEqual(out["val"], "+30")
        self.assertEqual(out["after"], ["", ""])

    def test_the_status_line_comes_back_to_the_firmware(self):
        """On the real page the line under the message is "Connected: firmware x"."""
        self.both_on_layer_1()
        out = self.run_page("""
        await drag(vca, -40 * reach.vca);
        const line = status();
        showFirmware = () => { line.textContent = "Connected: firmware 0.9.4"; };
        await drag(vca, 10 * reach.vca);
        report({ after: [line.text, line.className] });
        """)
        self.assertEqual(out["after"], ["Connected: firmware 0.9.4", "ok"])

    def test_a_turn_leaves_anyone_elses_message_alone(self):
        self.both_on_layer_1()
        out = self.run_page("""
        await drag(vca, -40 * reach.vca);
        status().textContent = "Renamed P3"; status().className = "ok";
        await drag(vca, 10 * reach.vca);
        report({ after: [status().text, status().className] });
        """)
        self.assertEqual(out["after"], ["Renamed P3", "ok"])

    def test_the_other_layers_both_route_is_only_mentioned(self):
        self.both_on_layer_1()
        self.route(1, VELOCITY, VCA, 0)
        out = self.run_page("""
        await setLayer(1);
        const a = state(vca);
        await drag(vca, 25 * reach.vca);
        report({ a, b: state(vca), rows: rows() });
        """)
        self.assertEqual(out["a"]["val"], "off +L1", "layer 2 has none of its own")
        self.assertIn("Layer 1's own route at +40 also plays on this layer", out["a"]["title"])
        self.assertAlmostEqual(out["a"]["u"], 0, delta=0.002)
        self.assertEqual(out["b"]["val"], "+25 +L1", "its own, beside it")
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 40), "layer 1's untouched")
        self.assertIn((VELOCITY, VCA, 25), self.s.routes(1))

    def test_one_layer_mode_doesnt_mention_both(self):
        self.s.setting("route-both 1 (0-7)", 1)
        out = self.run_page("report({ vca: state(vca) });")
        self.assertEqual(out["vca"]["val"], "+40", "the bit waits, and does nothing in one layer")
        self.assertNotIn("Both", out["vca"]["title"])

    def test_links_dont_take_a_route(self):
        self.s.setting("layers", 2)
        self.s.setting("link", 64 | 1)  # the filter, the amp envelope
        out = self.run_page("""
        await setLayer(1);
        const a = [state(vca), state(cutoff)];
        await drag(vca, 10 * reach.vca);
        await drag(cutoff, 10 * reach.cutoff);
        report({ a, b: [state(vca).val, state(cutoff).val], linked: vca.classList.contains("linked") });
        """)
        for knob_ in out["a"]:
            self.assertIn("this is layer 2's own", knob_["title"])
        self.assertEqual(out["b"], ["+50", "+30"])
        self.assertFalse(out["linked"])
        self.assertEqual(self.s.routes(0), KNOB_START, "layer 1's stay")
        self.assertEqual(self.s.routes(1), [(VELOCITY, VCA, 50), (VELOCITY, CUTOFF, 30)])

    def test_linked_or_not_layer_1_says_nothing_about_links(self):
        self.s.setting("layers", 2)
        self.s.setting("link", 64 | 1)
        out = self.run_page("report({ vca: state(vca) });")
        self.assertNotIn("layer 2's own", out["vca"]["title"])

    # a route stored twice: only a hand-made record has one, as every write overwrites the first match
    def twice(self):
        self.store_patch(9)
        self.s.close()
        rewrite_record(self.path, 9, (246, VELOCITY), (247, CUTOFF), (248, 30))  # store.c: routes at 240, three bytes each
        self.s = Synth(self.path)
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 8)
        self.assertEqual(self.s.routes(0), KNOB_START + [(VELOCITY, CUTOFF, 30)])

    def test_a_route_stored_twice_says_so_and_0_takes_them_all_after_the_hold(self):
        self.twice()
        out = self.run_page("""
        const a = state(cutoff);
        await drag(cutoff, -20 * reach.cutoff);
        report({ a, b: state(cutoff), rows: rows() });
        """)
        self.assertEqual(out["a"]["val"], "+20 ×2")
        self.assertIn("2 times", out["a"]["title"])
        self.assertEqual(out["b"]["val"], "off", "no ×2: they're both on their way")
        self.assertEqual(self.s.routes(0), [KNOB_START[0], (VELOCITY, CUTOFF, 0), (VELOCITY, CUTOFF, 0)])
        self.assertEqual(out["rows"], [[VELOCITY, VCA, 40], [VELOCITY, CUTOFF, 0], [VELOCITY, CUTOFF, 0]])
        self.s.rest()
        self.assertEqual(self.s.routes(0), KNOB_START[:1])

    def test_a_route_stored_twice_turns_the_first(self):
        self.twice()
        out = self.run_page("await drag(cutoff, 5 * reach.cutoff); report({ val: state(cutoff).val });")
        self.assertEqual(out["val"], "+25 ×2")
        self.assertEqual(self.s.routes(0), [(VELOCITY, VCA, 40), (VELOCITY, CUTOFF, 25), (VELOCITY, CUTOFF, 30)])

    # the rest
    def test_offline_it_does_nothing(self):
        out = self.run_page("""
        online = false;
        sent.length = 0;
        await drag(vca, 30 * reach.vca);
        key(vca, "ArrowUp", false);
        vca.find("dial").fire("dblclick");
        await settle();
        report({ state: state(vca), sent });
        """)
        self.assertEqual(out["sent"], [])
        self.assertEqual(out["state"]["val"], "+40")
        self.assertAlmostEqual(out["state"]["u"], 40 / 99, delta=0.002)
        self.assertEqual(self.s.routes(0), KNOB_START)

    def test_a_refresh_during_a_drag_doesnt_move_it(self):
        out = self.run_page("""
        const stale = layerRoutes;
        down(vca);
        move(vca, 30 * reach.vca);
        layerRoutes = stale; paintAll();                      // a poll's answer, from before the turn
        const during = state(vca);
        await settle();                                       // the write and the refresh after it, the pointer still down
        const rested = state(vca).val;
        move(vca, 40 * reach.vca);                            // on from where the drag began
        const onward = state(vca).val;
        up(vca);
        await settle();
        report({ during, rested, onward, after: state(vca).val });
        """)
        self.assertEqual(out["during"]["val"], "+70")
        self.assertAlmostEqual(out["during"]["u"], 70 / 99, delta=0.002)
        self.assertEqual(out["rested"], "+70")
        self.assertEqual(out["onward"], "+80")
        self.assertEqual(out["after"], "+80")
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 80))

    def test_a_second_turn_during_a_write_isnt_undone(self):
        """The first write's answer comes in while the knob has moved on: it must not snap back to what the first one said."""
        out = self.run_page("""
        down(vca);
        move(vca, 30 * reach.vca);
        for (let k = 0; k < 2000 && !writes().length; k++) await sleep(1); // the first write is on its way
        move(vca, 40 * reach.vca);
        const seen = new Set();
        for (let k = 0; k < 1000 && writes().length < 2; k++) { seen.add(state(vca).val); await sleep(2); }
        up(vca);
        await settle();
        report({ seen: [...seen], after: state(vca).val, writes: writes().length });
        """)
        self.assertEqual(out["seen"], ["+80"])
        self.assertEqual(out["after"], "+80")
        self.assertEqual(out["writes"], 2)
        self.assertEqual(self.s.routes(0)[0], (VELOCITY, VCA, 80))

    def test_a_panel_change_after_a_turn_isnt_held_off(self):
        """Once the synth has confirmed a turn the knob holds nothing back: the next change anywhere shows at once."""
        out = self.run_page("""
        await drag(vca, 10 * reach.vca);
        await ask([0x56, 0, 4, 9, 0, 99 + 12], [0x7F, 0x56]);
        await refreshLayerRoutes();
        report({ val: state(vca).val });
        """)
        self.assertEqual(out["val"], "+12")

    def test_no_answer_goes_back_to_the_truth_and_says_so(self):
        out = self.run_page("""
        deaf = true;
        await drag(vca, 30 * reach.vca);
        const a = state(vca);
        deaf = false;
        await refreshLayerRoutes();
        report({ a, said: status().text, b: state(vca) });
        """)
        self.assertEqual(out["a"]["val"], "+40")
        self.assertEqual(out["said"], "The synth didn't take that route", "not a full table")
        self.assertEqual(self.s.routes(0), KNOB_START)

    def test_a_turn_that_gets_no_answer_lets_go_after_the_hold(self):
        """The hold can't outlast its 1.5 s, whatever happens to the write."""
        out = self.run_page("""
        down(vca); move(vca, 30 * reach.vca);
        const held = state(vca).val;
        skew = 1600; paintAll();
        const later = state(vca).val;
        up(vca);
        skew = 0;
        await settle();
        report({ held, later });
        """)
        self.assertEqual((out["held"], out["later"]), ("+70", "+40"))

    def test_old_firmware_needs_no_check(self):
        """Velocity, the VCA, the cutoff, 56 and 57 are as they were in the first firmware with routes (29e8b88), whose table had 8
        slots and a shorter 57 reply. The knob asks for no feature or version."""
        page = source("tools", "web", "index.html")
        code = js_block(page, "function velocityKnob(")
        self.assertNotRegex(code, r"feature\(|firmwareVersion|data-needs")
        s = self.s

        class EightSlots:
            @staticmethod
            def request(*data):
                reply = s.request(*data)
                if data[0] != 0x57:
                    return reply
                start = reply.index(pro800.SYSEX_HEADER + bytes(data[:2]))
                return reply[start:start + pro800.HEADER_SIZE + 2 + 8 * 4] + b"\xF7"

        out = self.run_page("""
        await drag(vca, 10 * reach.vca);
        report({ vca: state(vca).val, cutoff: state(cutoff).val, count: layerRoutes.length, rows: rows().length });
        """, EightSlots)
        self.assertEqual(out, {"vca": "+50", "cutoff": "+20", "count": 2, "rows": 2})

    def test_the_cheat_sheet_says_so(self):
        page = source("tools", "web", "index.html")
        sheet = page[page.index('id="tab-device"'):page.index('id="tab-matrix"')]
        self.assertIn("Velocity knob", sheet)


class VelocityOnTheEngine(PanelTest):
    """What the editor's Velocity knobs say about the engine, measured: a negative route on the VCA and the links."""

    def route(self, layer, source_, dest, depth):
        self.s.request(0x56, layer, source_, dest, (depth + 99) >> 7, (depth + 99) & 0x7F)

    def voice(self, velocity, note=60):
        self.s.ms(50)
        self.s.note(note, velocity)
        self.s.ms(100)
        voices = self.s.gated()
        cvs = self.s.cvs()
        out = (cvs[24 + voices[0]] / 65535 - 0.07) / 0.93, cvs[16 + voices[0]] / CV_PER_SEMITONE  # tools/sim/hw.c, synth.c: VCA_THRESHOLD
        self.s.midi(0x80, note, 0)
        self.s.ms(1500)
        return out

    def test_a_negative_velocity_route_does_nothing_on_its_own(self):
        self.route(0, VELOCITY, VCA, -40)
        self.assertAlmostEqual(self.voice(64)[0], 1, delta=0.002)
        self.assertAlmostEqual(self.voice(1)[0], 1, delta=0.002)

    def test_both_on_the_init_sounds_route_adds_to_the_other_layers_own(self):
        """Layer 2 has its own copy, and a Both route doesn't replace it: the two add up. MANUAL, A route on both layers."""
        one, two = 1 - (1 - 64 / 127) * 70 / 99, 1 - 2 * (1 - 64 / 127) * 70 / 99
        self.s.setting("layers", 1)  # split: from the split note up is layer 2
        self.assertAlmostEqual(self.voice(64, note=40)[0], one, delta=0.002)
        self.assertAlmostEqual(self.voice(64, note=72)[0], one, delta=0.002)
        self.s.setting("route-both 1 (0-7)", 1)
        self.assertAlmostEqual(self.voice(64, note=40)[0], one, delta=0.002)
        self.assertAlmostEqual(self.voice(64, note=72)[0], two, delta=0.002, msg="twice the drop")
        self.route(1, VELOCITY, VCA, 0)
        self.assertAlmostEqual(self.voice(64, note=72)[0], one, delta=0.002, msg="without its own: one response, shared")

    def test_the_docs_say_both_adds_to_the_other_layers_own(self):
        manual = source("MANUAL.md")
        self.assertIn("init sound", manual[manual.index("### A route on both layers"):manual.index("### Ideas to try")])
        design = source("docs", "sound-design.html")
        self.assertIn("init sound", design[design.index("A route can be set to Both"):design.index("That's what lets a single route")])

    def test_beside_another_route_it_takes_some_of_that_ones_drop_back(self):
        """Why the knob's title says "on its own", and why it doesn't throw the route away."""
        wheel = 5
        self.route(0, VELOCITY, VCA, 0)
        self.route(0, wheel, VCA, 50)  # the wheel at 0: down by 50/99
        alone = self.voice(64)[0]
        self.route(0, VELOCITY, VCA, -40)
        beside = self.voice(64)[0]
        self.assertAlmostEqual(alone, 1 - 50 / 99, delta=0.003)
        self.assertAlmostEqual(beside, 1 - 50 / 99 + (1 - 64 / 127) * 40 / 99, delta=0.003)

    def test_a_filter_link_leaves_layer_2_its_own_route(self):
        self.s.setting("layers", 1)  # split: from the split note up is layer 2
        self.s.setting("link", 64)
        self.s.param(0, "cutoff", 0x2000)
        self.s.ms(400)
        with_ = self.voice(127, note=72)[1]
        self.route(1, VELOCITY, CUTOFF, 0)
        without = self.voice(127, note=72)[1]
        self.assertAlmostEqual(with_ - without, 20 / 99 * 96, delta=0.05, msg="layer 2's route, under layer 1's knob")
        self.route(0, VELOCITY, CUTOFF, 0)
        self.assertAlmostEqual(self.voice(127, note=72)[1], without, delta=0.02, msg="layer 1's route isn't layer 2's")


class RouteHold(PanelTest):
    """Depth 0 keeps a route's slot, source, destination and Both mark for 5 s, playing nothing (MANUAL, the mod matrix)."""
    BOTH = "route-both 1 (0-7)"

    def route(self, depth, source=VELOCITY, dest=VCA, layer=0):
        reply = self.s.request(0x56, layer, source, dest, (depth + 99) >> 7, (depth + 99) & 0x7F)
        return bytes([0x7F, 0x56]) in reply

    def counts(self, pot, move):
        self.position += move
        self.s.cmd(f"pot {POT[pot]} {self.position}")
        self.s.ms(40)

    def fill(self, source=1):
        """The 14 slots the init sound leaves: lfo into dests 16 up."""
        for dest in range(16, 30):
            self.assertTrue(self.route(5, source, dest))
        self.assertEqual(len(self.s.routes()), 16)

    def record_routes(self, slot):
        return self.s.memory()[(slot - 1) * 768 + 240:(slot - 1) * 768 + 288]

    def record_both(self, slot):
        return int.from_bytes(self.s.memory()[(slot - 1) * 768 + 351:(slot - 1) * 768 + 353], "little")

    # sweeping through 0
    def test_a_sweep_on_shift_fil_env_amt_keeps_the_slot_and_the_mark(self):
        self.s.setting(self.BOTH, 2)  # slot 1: velocity -> cutoff, the init sound's
        self.s.hold("SHIFT")
        self.s.pot("FIL_ENV_AMT", 0.3)
        self.position = round(0.3 * 4095)
        depths = []
        for _ in range(32):
            self.counts("FIL_ENV_AMT", -24)  # the knob's smallest move: it stops on 0 on the way
            slot = self.s.route_table()[1]
            self.assertEqual(slot[:2], (VELOCITY, CUTOFF))
            self.assertEqual(self.s.settings()[self.BOTH], 2)
            depths.append(slot[2])
        self.assertIn(0, depths)
        self.assertGreaterEqual(depths[0], 19)
        self.assertLessEqual(depths[-1], -20)
        self.s.let_go("SHIFT")
        self.s.rest()
        self.assertEqual(self.s.route_table()[1], (VELOCITY, CUTOFF, depths[-1]))
        self.assertEqual(self.s.settings()[self.BOTH], 2, "not at 0: nothing goes")

    def test_a_sweep_in_mod_assign_keeps_the_slot_and_the_mark(self):
        self.s.setting(self.BOTH, 2)
        self.s.press("ASSIGN")
        self.s.press("4")
        self.s.pot("CUTOFF", 0.3)
        self.s.pot("CUTOFF", 0.35)
        self.s.ms(800)
        self.assertShows("  20")
        self.s.turn(-10)
        self.assertShows("   0")
        self.assertEqual(self.s.route_table()[1], (VELOCITY, CUTOFF, 0))
        self.s.turn(-10)
        self.assertShows(" -20")
        self.s.press("ASSIGN")
        self.s.rest()
        self.assertEqual(self.s.route_table()[1], (VELOCITY, CUTOFF, -20))
        self.assertEqual(self.s.settings()[self.BOTH], 2)

    def test_a_sweep_through_the_hosts_message_keeps_the_slot_and_the_mark(self):
        self.s.setting(self.BOTH, 2)
        for depth in (20, 1, 0, -1, -20):
            self.assertTrue(self.route(depth, VELOCITY, CUTOFF), "the same ack as ever")
            self.assertEqual(self.s.route_table()[1], (VELOCITY, CUTOFF, depth), "57 sends it, at 0 too")
            self.assertEqual(self.s.settings()[self.BOTH], 2)
        self.s.rest()
        self.assertEqual(self.s.route_table()[1], (VELOCITY, CUTOFF, -20))

    # the hold
    def test_just_under_5_s_keeps_it_and_just_over_takes_it_and_its_mark(self):
        self.s.setting(self.BOTH, 1)
        self.route(0)
        self.s.ms(ROUTE_HOLD_MS - 100)
        self.assertEqual(self.s.route_table()[0], (VELOCITY, VCA, 0))
        self.assertEqual(self.s.settings()[self.BOTH], 1)
        self.s.ms(120)
        self.assertEqual(self.s.route_table()[0], (0, 0, 0))
        self.assertEqual(self.s.settings()[self.BOTH], 0)
        self.assertEqual(self.s.routes(), INIT_ROUTES[1:], "and only it")

    def test_the_same_through_the_panels_stop_on_0(self):
        self.s.request(0x56, 0, VELOCITY, VCA, (40 + 99) >> 7, (40 + 99) & 0x7F)
        self.s.hold("SHIFT")
        self.s.pot("MASTER_VOL", 0.6)
        self.s.pot("MASTER_VOL", 0.0)  # VE.A from 40 down to its end
        self.assertEqual(self.s.route_table()[0], (VELOCITY, VCA, 0))
        self.s.let_go("SHIFT")
        self.s.ms(ROUTE_HOLD_MS - 300)
        self.assertEqual(self.s.route_table()[0], (VELOCITY, VCA, 0))
        self.s.ms(400)
        self.assertEqual(self.s.route_table()[0], (0, 0, 0))

    def test_back_from_0_cancels_it(self):
        self.route(0)
        self.s.ms(ROUTE_HOLD_MS - 100)
        self.route(7)
        self.s.rest()
        self.assertEqual(self.s.route_table()[0], (VELOCITY, VCA, 7))

    def test_0_after_coming_back_is_a_whole_hold_again(self):
        self.route(0)
        self.s.ms(4000)
        self.route(7)
        self.route(0)
        self.s.ms(ROUTE_HOLD_MS - 100)
        self.assertEqual(self.s.route_table()[0], (VELOCITY, VCA, 0))
        self.s.ms(120)
        self.assertEqual(self.s.route_table()[0], (0, 0, 0))

    def test_0_again_at_0_doesnt_start_it_over(self):
        self.route(0)
        self.s.ms(3000)
        self.route(0)
        self.s.ms(ROUTE_HOLD_MS - 3000 + 20)
        self.assertEqual(self.s.route_table()[0], (0, 0, 0))

    def test_each_has_its_own_time_on_each_layer(self):
        self.route(0, VELOCITY, VCA)
        self.s.ms(3000)
        self.route(0, VELOCITY, CUTOFF)
        self.route(0, VELOCITY, VCA, layer=1)
        self.s.ms(2050)
        self.assertEqual(self.s.routes(0), [(VELOCITY, CUTOFF, 0)])
        self.assertEqual(self.s.routes(1), [(VELOCITY, VCA, 0), INIT_ROUTES[1]])
        self.s.ms(3000)
        self.assertEqual(self.s.routes(0), [])
        self.assertEqual(self.s.routes(1), INIT_ROUTES[1:])

    def test_a_route_that_isnt_there_stays_not_there(self):
        self.assertTrue(self.route(0, 1, 3), "acknowledged, as ever")
        self.assertEqual(self.s.routes(), INIT_ROUTES)

    # playing nothing
    def voice_cvs(self):
        """A note's CVs at 300 ms: the voice's four and the single wires, by voice since their tuning differs."""
        self.s.note(60, 100)
        self.s.ms(300)
        v = self.s.gated()[0]
        cvs = self.s.cvs()
        self.s.note_off(60)
        self.s.ms(250)
        return v, [cvs[8 * k + v] for k in range(4)] + cvs[32:]

    def test_at_0_every_source_into_every_destination_changes_no_cv(self):
        """Into the VCA and the volume a 0..1 source counts down from full and FIX turns down by its depth, pitch squares it, the
        time destinations go through 2^x. The routes reach 0 with no voice sounding, so a depth on the way disturbs nothing."""
        self.s.param(0, "amp-rel", 0)
        self.s.ms(300)
        self.s.no_routes()
        base = {}
        for _ in range(16):
            v, cvs = self.voice_cvs()
            base.setdefault(v, cvs)
            self.assertEqual(base[v], cvs, "the measure itself is steady")
        fixed = 25
        self.assertTrue(self.route(50, fixed, VCA))
        self.s.ms(100)
        v, cvs = self.voice_cvs()
        self.assertNotEqual(cvs, base[v], "and it sees a route that plays")
        self.route(0, fixed, VCA)
        names = pro800.SOURCE_NAMES
        for source_ in (fixed, VELOCITY, names.index("lfo"), names.index("env3"), names.index("mod-wheel"), names.index("macro1"), names.index("note"), names.index("amp-env")):
            for dest in range(len(pro800.DEST_NAMES)):
                self.assertTrue(self.route(1, source_, dest))
                self.route(0, source_, dest)
                self.s.ms(20)
                v, cvs = self.voice_cvs()
                self.assertEqual(cvs, base[v], f"{names[source_]} -> {pro800.DEST_NAMES[dest]}")

    def test_at_0_a_morph_source_doesnt_morph(self):
        self.s.param(0, "cutoff", 0x1000)
        self.s.ms(400)
        self.s.request(0x5D, 0, 8)  # P9, a darker sound: the target
        self.s.request(0x5F)
        self.s.request(0x44, 0, 9)
        self.s.no_routes()
        self.s.ms(300)
        _, plain = self.voice_cvs()
        v, plain = self.voice_cvs()
        fixed, morph = 25, pro800.DEST_NAMES.index("morph")
        self.route(50, fixed, morph)
        self.s.ms(100)
        self.assertNotEqual(self.voice_cvs()[1][2], plain[2], "it does at 50")
        self.route(0, fixed, morph)
        self.s.ms(300)
        for _ in range(8):
            w, cvs = self.voice_cvs()
            if w == v:
                self.assertEqual(cvs, plain)

    # a full table
    def test_a_new_route_takes_a_slot_waiting_at_0(self):
        self.fill()
        self.assertFalse(self.route(9, 2, 3), "16 playing: full")
        self.s.setting(self.BOTH, 1)
        self.route(0, VELOCITY, VCA)
        self.assertTrue(self.route(9, 2, 3), "one at 0: not full")
        self.assertEqual(self.s.route_table()[0], (2, 3, 9))
        self.assertEqual(self.s.settings()[self.BOTH], 0, "a new route has no mark")
        self.s.rest()
        self.assertEqual(self.s.route_table()[0], (2, 3, 9), "and no time running")
        self.assertFalse(self.route(9, 2, 4), "full again")

    def test_an_empty_slot_goes_first(self):
        self.route(0, VELOCITY, VCA)
        self.assertTrue(self.route(9, 2, 3))
        self.assertEqual(self.s.route_table()[:3], [(VELOCITY, VCA, 0), INIT_ROUTES[1], (2, 3, 9)])

    def test_the_panels_velocity_knob_shows_tenths_until_it_moves_the_depth(self):
        self.s.no_routes()
        t = 535 + 990
        self.s.request(0x66, 0, VELOCITY, VCA, t >> 7, t & 0x7F)
        self.s.hold("SHIFT")
        self.s.pot("MASTER_VOL", 0.3)
        self.s.pot("MASTER_VOL", 0.3 + 30 / 4095)
        self.assertShows("VE.A")
        self.s.ms(600)
        self.assertShows(" 53.5", "too small a move for a step")
        self.s.pot("MASTER_VOL", 0.6)
        self.assertRegex(self.s.shows(), r"^ +\d+$", "a step: a whole number")
        self.s.let_go("SHIFT")

    def test_the_panels_velocity_knob_and_mod_assign_agree(self):
        self.s.no_routes()
        for dest in range(16, 32):
            self.route(5, 1, dest)
        self.s.hold("SHIFT")
        self.s.pot("MASTER_VOL", 0.3)
        self.s.pot("MASTER_VOL", 0.4)
        self.assertShows("FULL")
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.route(0, 1, 20)
        self.s.hold("SHIFT")
        self.s.pot("MASTER_VOL", 0.6)
        self.s.pot("MASTER_VOL", 0.7)
        self.assertShows("VE.A", "room now")
        self.assertEqual(self.s.route_table()[4][:2], (VELOCITY, VCA), "in the slot at 0")
        self.s.let_go("SHIFT")
        self.s.ms(1600)
        self.route(0, 1, 21)  # the next one, for MOD ASSIGN
        self.s.press("ASSIGN")
        self.s.press("4")
        self.s.pot("CUTOFF", 0.3)
        self.s.pot("CUTOFF", 0.35)
        self.s.ms(800)
        self.s.turn(1)
        self.assertShows("   2", "no FULL")
        self.assertEqual(self.s.route_table()[5], (VELOCITY, CUTOFF, 2))
        self.s.press("ASSIGN")

    # a save, a load, undo, compare, copy
    def test_a_save_stores_none_at_0_and_the_one_waiting_stays_waiting(self):
        self.s.setting(self.BOTH, 3)
        self.route(0, VELOCITY, VCA)
        self.s.ms(1000)
        self.store_patch(9)
        self.assertEqual(self.record_routes(9)[:6], bytes([0, 0, 0, VELOCITY, CUTOFF, 20]), "slot 0 empty, slot 1 where it was")
        self.assertEqual(self.record_both(9), 2, "and the mark on slot 1 still on slot 1")
        self.assertEqual(self.s.route_table()[0], (VELOCITY, VCA, 0), "in memory it's as it was")
        self.assertEqual(self.s.settings()[self.BOTH], 3)
        self.route(30, VELOCITY, VCA)  # and still has its slot and its mark, if it comes back
        self.assertEqual(self.s.settings()[self.BOTH], 3)
        self.route(0, VELOCITY, VCA)
        self.s.rest()
        self.assertEqual(self.s.route_table()[0], (0, 0, 0))
        self.s.request(0x5C, 0, 8)
        self.assertEqual(self.s.routes(), INIT_ROUTES[1:], "loaded: none at 0")
        self.assertEqual(self.s.settings()[self.BOTH], 2)

    def test_a_load_ends_the_wait(self):
        self.store_patch(9)
        self.route(0)
        self.s.request(0x5C, 0, 8)
        self.assertEqual(self.s.routes(), INIT_ROUTES)
        self.s.rest()
        self.assertEqual(self.s.routes(), INIT_ROUTES, "the slot's route isn't the one that was waiting")

    def test_the_init_sound_ends_the_wait(self):
        self.route(0)
        self.s.request(0x5F)
        self.s.rest()
        self.assertEqual(self.s.routes(), INIT_ROUTES)

    def test_undo_ends_the_wait_and_what_waited_is_gone(self):
        self.store_patch(9)
        self.route(0)
        self.s.request(0x5C, 0, 8)  # the edit, as it was, goes to the undo: without the route at 0
        self.s.hold("SHIFT")
        self.s.press("PERF")
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.routes(), INIT_ROUTES[1:])
        self.assertTrue(self.route(11, 2, 3))
        self.s.hold("SHIFT")
        self.s.press("PERF")
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.routes(), INIT_ROUTES, "redo: the stored one")
        self.s.rest()
        self.assertEqual(self.s.routes(), INIT_ROUTES)

    def test_compare_ends_the_wait_and_what_waited_is_gone(self):
        self.store_patch(9)
        self.route(0)
        self.s.hold("SHIFT")
        self.s.hold("REC")
        self.assertShows("CoNP")
        self.assertEqual(self.s.routes(), INIT_ROUTES, "the stored patch while held")
        self.s.rest()
        self.assertEqual(self.s.routes(), INIT_ROUTES, "with the wait over")
        self.s.let_go("REC")
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.routes(), INIT_ROUTES[1:], "back to the edit, which holds no route at 0")
        self.assertTrue(self.route(11, 2, 3))
        self.s.rest()
        self.assertEqual(self.s.routes(), [(2, 3, 11), INIT_ROUTES[1]])

    def test_a_copy_of_the_layer_takes_none_at_0_and_ends_the_wait_there(self):
        self.route(0, VELOCITY, CUTOFF)
        self.route(0, VELOCITY, VCA, layer=1)
        self.s.hold("SHIFT")
        self.s.press("SEQ2")
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.routes(1), INIT_ROUTES[:1], "the copy: not the one at 0")
        self.assertEqual(self.s.routes(0), [INIT_ROUTES[0], (VELOCITY, CUTOFF, 0)], "the original's still waiting")
        self.s.rest()
        self.assertEqual(self.s.routes(1), INIT_ROUTES[:1], "and nothing was waiting on the copy")
        self.assertEqual(self.s.routes(0), INIT_ROUTES[:1])

    def test_a_record_holding_depth_0_loads_and_stays_until_its_edited(self):
        """Only a hand-made one has any. It plays nothing and no time runs: nothing goes by itself. A save leaves it out."""
        self.store_patch(9)
        self.s.close()
        rewrite_record(self.path, 9, (242, 0))  # store.c: routes at 240, the first route's depth
        self.s = Synth(self.path)
        self.s.ms(1600)
        self.assertEqual(self.s.settings()["patch"], 8)
        wanted = [(VELOCITY, VCA, 0), INIT_ROUTES[1]]
        self.assertEqual(self.s.routes(), wanted)
        self.s.rest()
        self.assertEqual(self.s.routes(), wanted, "still there")
        self.s.request(0x5D, 0, 9)
        self.assertEqual(self.record_routes(10)[:3], bytes(3), "not in the save")
        self.assertEqual(self.s.routes(), wanted)
        self.route(0)
        self.s.rest()
        self.assertEqual(self.s.routes(), INIT_ROUTES[1:], "0 once more: now it's on its way")

    def test_such_a_record_gives_its_slot_up_when_the_table_is_full(self):
        self.store_patch(9)
        self.s.close()
        rewrite_record(self.path, 9, (242, 0))
        self.s = Synth(self.path)
        self.s.ms(1600)
        self.fill()
        self.assertTrue(self.route(9, 2, 3))
        self.assertEqual(self.s.route_table()[0], (2, 3, 9))

    # browsing
    def browse(self):
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")

    def test_browsing_shows_one_at_0_and_copes_when_it_goes(self):
        self.s.no_routes()
        self.route(5, 1, VCA)
        self.route(5, 2, CUTOFF)
        self.route(0, 2, CUTOFF)
        self.browse()
        self.assertShows("nod1")
        self.s.turn(1)
        self.assertShows("nod2")
        self.s.history()
        self.s.ms(800 + 3 * 600)
        self.assertSaw(["FEnV", "CUt", "   0"])
        self.s.rest()  # it went while its row was shown: the list ends a row sooner
        self.s.history()
        self.s.ms(3 * 600)
        self.assertEqual({t for _, t in self.s.history()} - {readable(segments_of(x)) for x in ("nod1", "LFO1", "VCA", "   5")}, set(), "only the one left")
        self.s.turn(1)
        self.assertShows("nod1", "round to itself")
        self.s.press("ASSIGN")
        self.assertShows("VCA", "and it edits the one left")
        self.s.press("ASSIGN")

    def test_browsing_when_the_last_one_goes(self):
        self.s.no_routes()
        self.route(5, 1, VCA)
        self.route(0, 1, VCA)
        self.browse()
        self.s.rest()
        self.assertShows("nonE")
        self.s.turn(1)
        self.assertShows("nonE")
        self.s.turn(-1)
        self.assertShows("nonE")
        self.s.press("ASSIGN")
        self.assertShows("nonE", "nothing to edit")
        self.s.hold("ASSIGN")
        self.s.ms(650)
        self.s.let_go("ASSIGN")
        self.assertShows("P  1")

    def test_mod_assign_at_0_when_it_goes_shows_0_and_a_turn_brings_it_back_unmarked(self):
        self.s.setting(self.BOTH, 2)
        self.s.press("ASSIGN")
        self.s.press("4")
        self.s.pot("CUTOFF", 0.3)
        self.s.pot("CUTOFF", 0.35)
        self.s.ms(800)
        self.s.turn(-10)
        self.s.rest()
        self.assertShows("   0")
        self.assertEqual(self.s.route_table()[1], (0, 0, 0))
        self.s.turn(1)
        self.assertShows("   2")
        self.assertEqual(self.s.route_table()[1], (VELOCITY, CUTOFF, 2))
        self.assertEqual(self.s.settings()[self.BOTH], 0)
        self.s.press("ASSIGN")


class ShiftVelocityRoutes(PanelTest):
    """Shift + M VOL is the edit layer's Velocity -> VCA (VE.A), Shift + FIL ENV AMT its Velocity -> Cutoff (VE.F), the routes
    MOD ASSIGN and the editor's Velocity knobs edit. MANUAL, the Shift table. Knob travel here: VE.A 113.85 per sweep, VE.F 227.7."""

    def setUp(self):
        super().setUp()
        for layer in (0, 1):  # about the knob, not init's depth (InitRoutes has that): a depth it can turn both ways
            self.s.request(0x56, layer, VELOCITY, VCA, (40 + 99) >> 7, (40 + 99) & 0x7F)

    def depth(self, dest, layer=0):
        """The depth read back, None without a route, 0 for one waiting at 0."""
        found = [d for source, to, d in self.s.routes(layer) if source == VELOCITY and to == dest]
        self.assertLessEqual(len(found), 1)
        return found[0] if found else None

    def route(self, layer, dest, depth, source=VELOCITY):
        self.s.request(0x56, layer, source, dest, (depth + 99) >> 7, (depth + 99) & 0x7F)

    def counts(self, pot, *moves):
        """Turns a knob from where it is by these many counts of 4095, one at a time. Under 24 no event comes (POT_HYSTERESIS)."""
        for move in moves:
            self.position = getattr(self, "position", 2048) + move
            self.s.cmd(f"pot {POT[pot]} {self.position}")
            self.s.ms(40)

    def start(self, pot, at=0.3):
        """Shift on, the knob's first reading in: from there on it counts."""
        self.s.hold("SHIFT")
        self.s.pot(pot, at)
        self.position = round(at * 4095)

    def voice(self, velocity, note=60):
        """The gated voice's VCA gain and filter CV in semitones; the note is let go again."""
        self.s.ms(50)
        self.s.note(note, velocity)
        self.s.ms(100)
        voices = self.s.gated()
        self.assertEqual(len(voices), 1)
        cvs = self.s.cvs()
        self.s.note_off(note)
        self.s.ms(1500)
        return (cvs[CV_AMP1 + voices[0]] / 65535 - 0.07) / 0.93, cvs[CV_FIL1 + voices[0]] / CV_PER_SEMITONE

    def test_m_vol_adds_adjusts_and_removes_the_route(self):
        self.s.no_routes()
        self.start("MASTER_VOL")
        self.s.pot("MASTER_VOL", 0.4)
        self.assertShows("VE.A", "the name first")
        self.assertEqual(self.depth(VCA), 11)
        self.s.ms(520)
        self.assertShows("  11", "then the value")
        self.s.pot("MASTER_VOL", 0.5)
        self.assertShows("  22")
        self.assertEqual(self.depth(VCA), 22)
        self.s.pot("MASTER_VOL", 0.0)
        self.assertShows("   0")
        self.assertEqual(self.depth(VCA), 0, "0 keeps it for now")
        self.s.rest()
        self.assertIsNone(self.depth(VCA), "and resting there removes it")
        self.assertEqual(self.s.routes(), [])
        self.s.pot("MASTER_VOL", 0.1)
        self.assertEqual(self.s.shows(), "  11")
        self.assertEqual(self.depth(VCA), 11, "and turning up adds it, by as much as before")
        self.s.pot("MASTER_VOL", 1.0)
        self.assertShows("  99")
        self.assertEqual(self.depth(VCA), 99)
        self.s.let_go("SHIFT")

    def test_fil_env_amt_adds_adjusts_and_removes_the_route_on_either_side(self):
        self.route(0, CUTOFF, 3)
        self.start("FIL_ENV_AMT")
        self.counts("FIL_ENV_AMT", -24)
        self.assertShows("VE.F")
        self.s.ms(520)
        depths, shown = [], []
        for _ in range(7):
            self.counts("FIL_ENV_AMT", -24)  # the knob's smallest move, 1.33 of a step
            depths.append(self.depth(CUTOFF))
            shown.append(self.s.shows())
        self.assertIn(0, depths, "a slow turn stops on 0")
        self.assertEqual(depths, sorted(depths, reverse=True))
        self.assertGreater(depths[0], 0)
        self.assertLess(depths[-1], 0, "and going on takes it negative")
        self.assertEqual(shown[depths.index(0)], "   0")
        self.assertEqual(shown[-1], f"{depths[-1]:4d}", "the sign on the display")
        self.s.pot("FIL_ENV_AMT", 0.0)
        self.s.pot("FIL_ENV_AMT", 1.0)
        self.s.pot("FIL_ENV_AMT", 0.0)
        self.assertShows(" -99")
        self.assertEqual(self.depth(CUTOFF), -99)
        self.s.pot("FIL_ENV_AMT", 1.0)
        self.assertShows("  99", "no plus: a route's depth, as MOD ASSIGN shows it")
        self.assertEqual(self.depth(CUTOFF), 99)
        self.s.let_go("SHIFT")

    def test_what_the_route_says_is_what_plays(self):
        self.s.param(0, "cutoff", 0x2000)
        self.s.ms(400)
        self.start("MASTER_VOL")
        self.s.pot("MASTER_VOL", 0.4)
        self.s.let_go("SHIFT")
        vca = self.depth(VCA)
        self.assertEqual(vca, 51)
        self.assertAlmostEqual(self.voice(64)[0], 1 - (1 - 64 / 127) * vca / 99, delta=0.002)
        self.assertAlmostEqual(self.voice(127)[0], 1, delta=0.002)
        before = self.voice(127)[1]
        self.start("FIL_ENV_AMT")
        self.s.pot("FIL_ENV_AMT", 0.4)
        self.s.let_go("SHIFT")
        cutoff = self.depth(CUTOFF)
        self.assertEqual(cutoff, 42)
        self.assertAlmostEqual(self.voice(127)[1] - before, (42 - 20) / 99 * 96, delta=0.05)
        self.start("FIL_ENV_AMT", 0.9)
        self.s.pot("FIL_ENV_AMT", 0.1)
        self.s.pot("FIL_ENV_AMT", 0.0)
        self.s.let_go("SHIFT")
        self.assertEqual(self.depth(CUTOFF), -99)
        self.assertAlmostEqual(self.voice(127)[1] - before, (-99 - 20) / 99 * 96, delta=0.05, msg="a harder note closes it")

    def test_it_edits_the_edit_layer(self):
        self.s.setting("layers", 1)  # split: from the split note up is layer 2
        self.s.press("SEQ2")
        self.start("MASTER_VOL")
        self.s.pot("MASTER_VOL", 0.5)
        self.start("FIL_ENV_AMT")
        self.s.pot("FIL_ENV_AMT", 0.4)
        self.s.ms(520)
        self.s.let_go("SHIFT")
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.routes(0), KNOB_START, "layer 1's stay")
        self.assertEqual(self.s.routes(1), [(VELOCITY, VCA, 62), (VELOCITY, CUTOFF, 42)])
        self.assertAlmostEqual(self.voice(64, note=72)[0], 1 - (1 - 64 / 127) * 62 / 99, delta=0.002, msg="layer 2's own")
        self.assertAlmostEqual(self.voice(64, note=40)[0], 1 - (1 - 64 / 127) * 40 / 99, delta=0.002, msg="layer 1's own")

    def test_a_link_doesnt_reach_a_route(self):
        """On a linked layer the knobs are layer 1's, but a route is the layer's own: the editor's knob says the same."""
        self.s.setting("layers", 1)
        self.s.setting("link", 127)
        self.s.press("SEQ2")
        self.start("FIL_ENV_AMT")
        self.s.pot("FIL_ENV_AMT", 0.4)
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.routes(0), KNOB_START)
        self.assertEqual(self.depth(CUTOFF, 1), 42)

    def test_a_full_table_says_so_and_nothing_changes(self):
        self.s.no_routes()
        for dest in range(1, 17):
            self.route(0, dest, 5, source=1)
        table = self.s.routes()
        self.assertEqual(len(table), 16)
        self.start("MASTER_VOL")
        self.s.pot("MASTER_VOL", 0.5)
        self.assertShows("FULL")
        self.s.ms(600)
        self.assertShows("FULL", "no value of a route that isn't there comes after it")
        self.assertEqual(self.s.routes(), table)
        self.s.pot("MASTER_VOL", 0.1)  # turned down too: there's nothing to adjust
        self.assertShows("FULL")
        self.assertEqual(self.s.routes(), table)
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.assertShows("P  1")
        self.start("FIL_ENV_AMT")
        self.s.pot("FIL_ENV_AMT", 0.0)
        self.assertShows("FULL")
        self.assertEqual(self.s.routes(), table)
        self.s.let_go("SHIFT")
        self.s.ms(1100)
        self.route(0, 2, 0, source=1)  # room again: what was turned against the full table counts for nothing
        self.start("MASTER_VOL")
        self.s.pot("MASTER_VOL", 0.4)
        self.assertShows("VE.A")
        self.assertEqual(self.depth(VCA), 11)
        self.s.let_go("SHIFT")

    def test_a_full_table_doesnt_stop_a_route_that_is_there(self):
        for dest in range(4, 18):
            self.route(0, dest, 5, source=1)
        self.assertEqual(len(self.s.routes()), 16)
        self.start("MASTER_VOL", 0.6)
        self.s.pot("MASTER_VOL", 0.7)
        self.assertShows("VE.A")
        self.assertEqual(self.depth(VCA), 51)
        self.s.pot("MASTER_VOL", 0.0)
        self.s.rest()
        self.assertEqual(self.depth(VCA), None)
        self.assertEqual(len(self.s.routes()), 15)
        self.s.pot("MASTER_VOL", 0.1)
        self.assertEqual(self.depth(VCA), 11, "its slot is free: it comes back")
        self.s.let_go("SHIFT")

    def test_a_negative_vca_route_stays_until_the_knob_goes_up(self):
        self.route(0, VCA, -40)
        self.start("MASTER_VOL")
        self.counts("MASTER_VOL", -200)
        self.assertShows("VE.A")
        self.s.ms(520)
        self.assertShows(" -40", "the real value")
        self.assertEqual(self.depth(VCA), -40, "a turn down writes nothing")
        self.counts("MASTER_VOL", -200, -400)
        self.assertShows(" -40")
        self.assertEqual(self.depth(VCA), -40)
        self.counts("MASTER_VOL", 24)  # 0.67 of a step, however far it was turned down
        self.assertEqual(self.depth(VCA), -40)
        self.assertShows(" -40")
        self.counts("MASTER_VOL", 24)
        self.assertEqual(self.depth(VCA), 1, "the first whole step replaces it, from 0")
        self.assertShows("   1")
        self.counts("MASTER_VOL", -60)
        self.assertEqual(self.depth(VCA), 0, "and now it's an ordinary route")
        self.s.rest()
        self.assertIsNone(self.depth(VCA))
        self.s.let_go("SHIFT")

    def test_a_negative_cutoff_route_is_an_ordinary_value(self):
        self.route(0, CUTOFF, -40)
        self.start("FIL_ENV_AMT")
        self.s.pot("FIL_ENV_AMT", 0.2)
        self.assertEqual(self.depth(CUTOFF), -62)
        self.s.pot("FIL_ENV_AMT", 0.3)
        self.assertEqual(self.depth(CUTOFF), -40)
        self.s.let_go("SHIFT")

    def test_the_fraction_doesnt_carry_past_the_end(self):
        self.s.no_routes()
        self.start("MASTER_VOL")
        self.counts("MASTER_VOL", -24)  # 0.67 of a step below 0
        self.counts("MASTER_VOL", 24)
        self.assertIsNone(self.depth(VCA))
        self.counts("MASTER_VOL", 24)
        self.assertEqual(self.depth(VCA), 1, "1.33 steps up: the 0.67 below 0 doesn't count against it")
        self.s.let_go("SHIFT")

    def test_the_both_mark_stays_with_the_route_and_goes_with_it(self):
        both = "route-both 1 (0-7)"
        self.s.setting(both, 1)  # slot 0: velocity -> VCA, the init sound's
        self.start("MASTER_VOL", 0.6)
        self.s.pot("MASTER_VOL", 0.7)
        self.assertEqual(self.depth(VCA), 51)
        self.assertEqual(self.s.settings()[both], 1, "adjusting leaves it")
        self.s.pot("MASTER_VOL", 0.0)
        self.assertEqual(self.depth(VCA), 0)
        self.assertEqual(self.s.settings()[both], 1, "at 0 it keeps it")
        self.s.pot("MASTER_VOL", 0.3)
        self.assertEqual(self.depth(VCA), 34)
        self.assertEqual(self.s.settings()[both], 1, "back from 0 it still has it")
        self.s.pot("MASTER_VOL", 0.0)
        self.s.rest()
        self.assertIsNone(self.depth(VCA))
        self.assertEqual(self.s.settings()[both], 0, "the firmware takes it off with the route")
        self.s.pot("MASTER_VOL", 0.3)
        self.assertEqual(self.depth(VCA), 34)
        self.assertEqual(self.s.settings()[both], 0, "a new route has no mark")
        self.s.let_go("SHIFT")

    def test_0_takes_every_copy_off_as_the_editors_knob_does(self):
        self.store_patch(9)
        self.s.close()
        rewrite_record(self.path, 9, (246, VELOCITY), (247, CUTOFF), (248, 30))  # store.c: routes at 240, three bytes each
        self.s = Synth(self.path)
        self.s.ms(1600)
        self.route(0, CUTOFF, 1)  # the first
        self.assertEqual(self.s.routes(0), [(VELOCITY, VCA, 40), (VELOCITY, CUTOFF, 1), (VELOCITY, CUTOFF, 30)])
        self.start("FIL_ENV_AMT")
        self.counts("FIL_ENV_AMT", -25)
        self.assertEqual(self.s.routes(0), [(VELOCITY, VCA, 40), (VELOCITY, CUTOFF, 0), (VELOCITY, CUTOFF, 0)])
        self.s.rest()
        self.assertEqual(self.s.routes(0), [(VELOCITY, VCA, 40)])
        self.s.let_go("SHIFT")

    def test_letting_go_at_once_still_shows_the_value(self):
        for pot, name, value in (("MASTER_VOL", "VE.A", "  29"), ("FIL_ENV_AMT", "VE.F", "  -2")):
            with self.subTest(pot=pot):
                self.start(pot)
                self.s.pot(pot, 0.2)
                self.assertShows(name)
                self.s.let_go("SHIFT")
                self.s.ms(520)
                self.assertShows(value)
                self.s.ms(1600)
                self.assertShows("P  1")

    def test_a_saved_patch_keeps_the_depths(self):
        self.start("MASTER_VOL")
        self.s.pot("MASTER_VOL", 0.4)
        self.start("FIL_ENV_AMT", 0.9)
        self.s.pot("FIL_ENV_AMT", 0.1)
        self.s.ms(520)
        self.s.let_go("SHIFT")
        self.s.let_go("SHIFT")
        self.store_patch(9)
        self.s.press("1")
        self.s.ms(3100)
        self.assertEqual(self.s.routes(), INIT_ROUTES)
        self.s.press("9")
        self.s.ms(3100)
        self.assertEqual(self.s.routes(), [(VELOCITY, VCA, 51), (VELOCITY, CUTOFF, -99)])
        self.power_cycle()
        self.s.ms(1600)
        self.assertEqual(self.s.routes(), [(VELOCITY, VCA, 51), (VELOCITY, CUTOFF, -99)])

    def test_the_rec_save_after_a_turn_has_it(self):
        self.s.press("REC")
        self.start("MASTER_VOL")
        self.s.pot("MASTER_VOL", 0.4)
        self.s.ms(520)
        self.s.let_go("SHIFT")
        self.s.ms(1600)
        self.assertShows("P  1", "the save is still waiting")
        self.s.press("REC")
        self.assertShows("donE")
        self.s.ms(1100)
        self.s.press("2")
        self.s.ms(3100)
        self.assertEqual(self.s.routes(), INIT_ROUTES)
        self.s.press("1")
        self.s.ms(3100)
        self.assertEqual(self.depth(VCA), 51)

    def test_without_shift_the_knobs_are_what_they_were(self):
        self.manual()
        self.s.pot("MASTER_VOL", 0.25)
        self.assertShows("  25", "no dot")
        self.s.ms(300)
        self.assertEqual(self.s.params()["master-vol"], 25)
        self.assertAlmostEqual(self.s.cvs()[35] / 65535, 0.25, delta=0.002)
        self.s.ms(1600)
        self.s.pot("FIL_ENV_AMT", 0.75)
        self.assertNotIn(".", self.s.shows().rstrip()[-1:])
        self.assertEqual(self.s.params()["fil-env-amt"], 74)
        self.assertEqual(self.s.routes(), KNOB_START)
        self.assertEqual(self.s.settings()["pickup"], 1)

    def test_shift_pressed_and_let_go_leaves_m_vol_where_its_knob_is(self):
        self.s.pot("MASTER_VOL", 0.75)
        self.s.ms(300)
        self.s.press("SHIFT")
        self.s.ms(1200)
        self.s.pot("MASTER_VOL", 0.25)
        self.assertShows("  25", "no dot: nothing moved under Shift")
        self.s.ms(300)
        self.assertAlmostEqual(self.s.cvs()[35] / 65535, 0.25, delta=0.002)

    def test_the_level_stays_while_m_vol_edits_the_route(self):
        self.s.pot("MASTER_VOL", 0.5)
        self.s.ms(300)
        level = self.s.cvs()[35]
        self.start("MASTER_VOL", 0.3)
        self.s.pot("MASTER_VOL", 1.0)
        self.s.ms(300)
        self.assertEqual(self.s.cvs()[35], level, "under Shift")
        self.s.let_go("SHIFT")
        self.s.ms(300)
        self.assertEqual(self.s.cvs()[35], level, "letting go moves nothing")
        self.assertEqual(self.s.params()["master-vol"], 50)
        self.s.pot("MASTER_VOL", 0.8)
        self.assertShows("  50.", "it waits like any knob, with a dot")
        self.s.ms(300)
        self.assertEqual(self.s.cvs()[35], level)
        self.s.pot("MASTER_VOL", 0.3)  # past it
        self.s.ms(300)
        self.assertAlmostEqual(self.s.cvs()[35] / 65535, 0.3, delta=0.002, msg="and takes over from there on")
        self.s.pot("MASTER_VOL", 0.25)
        self.assertShows("  25", "no dot any more")

    def test_a_load_doesnt_let_a_waiting_m_vol_jump(self):
        self.store_patch(3)
        self.s.pot("MASTER_VOL", 0.5)
        self.s.ms(300)
        level = self.s.cvs()[35]
        self.start("MASTER_VOL", 0.3)
        self.s.pot("MASTER_VOL", 1.0)
        self.s.let_go("SHIFT")
        self.s.midi(0xC0, 2)
        self.s.ms(300)
        self.assertEqual(self.s.cvs()[35], level)

    def test_a_waiting_m_vol_isnt_frozen_by_mod_assign(self):
        """M VOL is the one knob MOD ASSIGN leaves alone."""
        self.s.pot("MASTER_VOL", 0.5)
        self.s.ms(300)
        level = self.s.cvs()[35]
        self.start("MASTER_VOL", 0.3)
        self.s.pot("MASTER_VOL", 1.0)
        self.s.let_go("SHIFT")
        self.s.press("ASSIGN")
        self.s.pot("MASTER_VOL", 0.8)
        self.s.ms(300)
        self.assertEqual(self.s.cvs()[35], level)
        self.s.pot("MASTER_VOL", 0.3)
        self.s.ms(300)
        self.assertAlmostEqual(self.s.cvs()[35] / 65535, 0.3, delta=0.002)

    def test_pickup_off_lets_m_vol_jump_when_it_moves(self):
        self.s.setting("pickup", 0)
        self.s.pot("MASTER_VOL", 0.5)
        self.s.ms(300)
        level = self.s.cvs()[35]
        self.start("MASTER_VOL", 0.3)
        self.s.pot("MASTER_VOL", 1.0)
        self.s.let_go("SHIFT")
        self.s.ms(300)
        self.assertEqual(self.s.cvs()[35], level, "letting go moves nothing with pickup off too")
        self.s.pot("MASTER_VOL", 0.9)
        self.s.ms(300)
        self.assertAlmostEqual(self.s.cvs()[35] / 65535, 0.9, delta=0.002)

    def test_with_pickup_off_letting_go_during_the_knobs_glide_moves_nothing(self):
        """A reading behind the muxes glides for a scan. What's left of that glide when Shift goes up isn't the knob moving, and
        on M VOL it would jump the volume to the knob."""
        for wait in (0, 1, 2, 5, 10, 20, 40):
            with self.subTest(wait=wait):
                self.s.setting("pickup", 0)
                self.s.pot("MASTER_VOL", 0.5)
                self.s.ms(300)
                level = self.s.cvs()[35]
                self.start("MASTER_VOL", 0.3)
                self.s.pot("MASTER_VOL", 1.0)
                self.s.ms(wait)
                self.s.let_go("SHIFT")
                self.s.ms(300)
                self.assertEqual(self.s.cvs()[35], level)

    def test_fil_env_amt_picks_up_after_shift_as_any_knob(self):
        self.manual()
        self.s.pot("FIL_ENV_AMT", 0.75)
        before = self.s.params()["fil-env-amt"]
        self.start("FIL_ENV_AMT", 0.6)
        self.s.pot("FIL_ENV_AMT", 0.2)
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.params()["fil-env-amt"], before, "the turn didn't touch the knob's own value")
        self.s.pot("FIL_ENV_AMT", 0.3)
        self.assertTrue(self.s.shows().endswith("."), "waiting, with a dot")
        self.assertEqual(self.s.params()["fil-env-amt"], before)
        self.s.pot("FIL_ENV_AMT", 0.9)
        self.assertEqual(self.s.params()["fil-env-amt"], 89, "past it: taken over")

    def test_pickup_is_on_pmod_fil_env_now_and_not_on_fil_env_amt(self):
        amount = self.s.params()["pmod-fil-env"]
        self.s.hold("SHIFT")
        self.s.pot("FIL_ENV_AMT", 0.9)
        self.s.pot("FIL_ENV_AMT", 0.1)
        self.assertShows("VE.F")
        self.assertEqual(self.s.settings()["pickup"], 1, "the old knob doesn't touch it")
        routes = self.s.routes()
        self.s.pot("PMOD_FIL_ENV", 0.6)
        self.s.pot("PMOD_FIL_ENV", 0.4)  # less than a quarter turn
        self.assertEqual(self.s.settings()["pickup"], 1)
        self.s.pot("PMOD_FIL_ENV", 0.3)
        self.assertEqual(self.s.settings()["pickup"], 0, "a quarter turn down")
        self.s.ms(600)
        self.assertShows("OFF")
        self.s.pot("PMOD_FIL_ENV", 0.7)
        self.assertEqual(self.s.settings()["pickup"], 1, "a quarter turn up")
        self.assertShows("on")
        self.s.pot("PMOD_FIL_ENV", 0.2)
        self.s.let_go("SHIFT")
        self.assertEqual(self.s.params()["pmod-fil-env"], amount, "the poly-mod value stays")
        self.assertEqual(self.s.routes(), routes, "and it isn't a route")
        self.s.ms(1100)
        self.power_cycle()
        self.assertEqual(self.s.settings()["pickup"], 0, "a system setting, where it was stored")

    # A quarter turn is 1024 counts, the first move that makes a whole step of P.UP
    def test_turning_past_an_end_of_pup_doesnt_change_the_quarter_turn(self):
        self.start("PMOD_FIL_ENV", 0.4)
        self.counts("PMOD_FIL_ENV", 820)  # up while on: 0.8 of a step that has nowhere to go
        self.assertEqual(self.s.settings()["pickup"], 1)
        self.counts("PMOD_FIL_ENV", -1000)
        self.assertEqual(self.s.settings()["pickup"], 1, "under a quarter turn down")
        self.counts("PMOD_FIL_ENV", -24)
        self.assertEqual(self.s.settings()["pickup"], 0, "a quarter turn down")
        self.counts("PMOD_FIL_ENV", -820)  # down while off, the same the other way
        self.counts("PMOD_FIL_ENV", 1000)
        self.assertEqual(self.s.settings()["pickup"], 0, "under a quarter turn up")
        self.counts("PMOD_FIL_ENV", 24)
        self.assertEqual(self.s.settings()["pickup"], 1, "a quarter turn up")
        self.s.let_go("SHIFT")

    def test_a_new_press_of_shift_starts_pups_quarter_turn_again(self):
        self.start("PMOD_FIL_ENV", 0.7)
        self.counts("PMOD_FIL_ENV", -1000)
        self.assertEqual(self.s.settings()["pickup"], 1)
        self.s.let_go("SHIFT")
        self.start("PMOD_FIL_ENV", 0.4)
        self.counts("PMOD_FIL_ENV", -100)
        self.assertEqual(self.s.settings()["pickup"], 1, "what the last press turned doesn't count")
        self.counts("PMOD_FIL_ENV", -924)
        self.assertEqual(self.s.settings()["pickup"], 0, "this press's own quarter turn does")
        self.s.let_go("SHIFT")

    def test_m_vol_at_the_top_comes_down_without_shift_and_the_route_stays(self):
        """MANUAL, under the Shift table: the knob waits, so the depth can go up without passing 0 and losing the mark."""
        both = "route-both 1 (0-7)"
        self.s.pot("MASTER_VOL", 1.0)
        self.s.ms(300)
        level = self.s.cvs()[35]
        self.s.setting(both, 1)
        self.start("MASTER_VOL", 1.0)
        self.s.pot("MASTER_VOL", 0.95)  # the first reading
        self.s.pot("MASTER_VOL", 0.9)
        depth = self.depth(VCA)
        self.assertLess(depth, 40)
        self.s.let_go("SHIFT")
        self.s.pot("MASTER_VOL", 0.5)
        self.s.pot("MASTER_VOL", 0.3)
        self.s.ms(300)
        self.assertEqual(self.s.cvs()[35], level, "the level stays while the knob comes down")
        self.s.hold("SHIFT")
        self.s.pot("MASTER_VOL", 0.35)  # the first reading
        self.s.pot("MASTER_VOL", 0.8)
        self.s.let_go("SHIFT")
        self.s.ms(300)
        self.assertGreater(self.depth(VCA), 40, "above what it could reach from the top")
        self.assertEqual(self.s.settings()[both], 1, "and the mark is still there")
        self.assertEqual(self.s.cvs()[35], level)

    def test_the_words_on_the_page_and_in_the_manual(self):
        page = source("tools", "web", "index.html")
        sheet = page[page.index('id="tab-device"'):page.index('id="tab-matrix"')]
        for where in (sheet, source("MANUAL.md")):
            for text in ("VE.A", "VE.F", "P.UP"):
                self.assertIn(text, where)
        for where in (page, source("MANUAL.md"), source("docs", "how-it-works.html"), source("README.md")):
            self.assertNotIn("Shift + FIL ENV AMT, ", where)
            self.assertNotIn("(Shift + FIL ENV AMT)", where)
            self.assertNotIn("Shift + FIL ENV AMT\n", where)
            self.assertIn("Shift + PMOD FIL ENV", where)
        self.assertIn("Shift + M VOL", velocity_about(page, "DEST_VCA"))
        self.assertIn("Shift + FIL ENV AMT", velocity_about(page, "DEST_CUTOFF"))


def velocity_about(page, dest):
    """The title of a faceplate Velocity knob."""
    return re.search(r'velocityKnob\(' + dest + r', "[^"]*", -?\d+, "([^"]*)"\)', page).group(1)


class PresetBundle(unittest.TestCase):
    """presets/lib/bundle.py fails the build on a preset the pages or the firmware couldn't take."""

    def bundle(self, *presets, folder=None):
        root = tempfile.mkdtemp(prefix="pro800bundle")
        self.addCleanup(shutil.rmtree, root, True)
        out = os.path.join(root, "out.json")
        if folder is None:
            folder = os.path.join(root, "presets")
            os.mkdir(folder)
            with open(os.path.join(root, "VERSION"), "w") as f:
                f.write("0.0.0\n")
            for i, p in enumerate(presets):
                with open(os.path.join(folder, f"{i:02}.json"), "w", encoding="utf-8") as f:
                    json.dump(p, f)
        run = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "presets", "lib", "bundle.py"), out, folder], capture_output=True, text=True, encoding="utf-8")
        return run, out

    def test_every_preset_we_ship_passes(self):
        run, out = self.bundle(folder=os.path.join(ROOT, "presets"))
        self.assertEqual(run.returncode, 0, run.stderr)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["presets"]), 70)

    def test_edges_pass(self):
        run, _ = self.bundle({"slot": 1, "name": "A"}, {"slot": 128, "name": "1234567890123456"}, {"slot": 2, "name": "a b~"})
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_a_bad_one_fails(self):
        for bad, says in (({"slot": 0, "name": "x"}, "slot"), ({"slot": 129, "name": "x"}, "slot"), ({"slot": "1", "name": "x"}, "slot"), ({"slot": True, "name": "x"}, "slot"),
                          ({"name": "x"}, "slot"), ({"slot": 1}, "name"), ({"slot": 1, "name": ""}, "name"), ({"slot": 1, "name": 5}, "name"),
                          ({"slot": 1, "name": "Café"}, "ASCII"), ({"slot": 1, "name": "tab\there"}, "ASCII"), ({"slot": 1, "name": "x" * 17}, "16"),
                          ({"slot": 1, "name": " left"}, "space"), ({"slot": 1, "name": "right "}, "space"), ([], "not a preset")):
            with self.subTest(preset=bad):
                run, out = self.bundle(bad)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn(says, run.stderr)
                self.assertFalse(os.path.exists(out), "no bank is written")

    def test_two_in_one_slot_fail(self):
        run, _ = self.bundle({"slot": 5, "name": "a"}, {"slot": 5, "name": "b"})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("taken", run.stderr)


if __name__ == "__main__":
    build()
    unittest.main(verbosity=1)
