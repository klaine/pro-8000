#!/usr/bin/env python3
# User-interaction tests: `python tools/test_panel.py [-k name]`. Builds the real firmware for this computer against
# simulated hardware (tools/sim/hw.c, with zig cc, into build/sim/) and uses it the way a person does: knobs, buttons,
# the VALUE encoder and MIDI go in, and every test checks what the display and the LEDs show. No synth is needed.
# A power cycle is a new process on the same EEPROM file. What each case should show comes from MANUAL.md.
import json
import math
import os
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
FIRMWARE = ["main.c", "synth.c", "lfo.c", "seq.c", "store.c", "fastmath.c", "display.c"]
VERSION = open(os.path.join(ROOT, "VERSION")).read().strip()


def build():
    os.makedirs(BUILD, exist_ok=True)
    src = os.path.join(ROOT, "src")
    inputs = [os.path.join(src, f) for f in os.listdir(src)] + [os.path.join(HERE, "sim", f) for f in os.listdir(os.path.join(HERE, "sim"))]
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
CV_AMP1, CV_FIL1 = 24, 16


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

    def params(self, layer=0):
        self.ms(300)  # the knobs glide to a new reading
        reply = self.request(0x53, layer)
        start = reply.index(pro800.SYSEX_HEADER + bytes([0x53, layer]))
        b = reply[start + pro800.HEADER_SIZE + 2:]
        return {pro800.PARAM_NAMES[i]: ((b[1 + 2 * i] << 7 | b[2 + 2 * i]) * 99 + 8191) // 16383 for i in range(b[0])}

    def routes(self, layer=0):
        reply = self.request(0x57, layer)
        start = reply.index(pro800.SYSEX_HEADER + bytes([0x57, layer]))
        d = reply[start + pro800.HEADER_SIZE:]
        return [(d[2 + 4 * r], d[3 + 4 * r], (d[4 + 4 * r] << 7 | d[5 + 4 * r]) - 99) for r in range(16) if d[2 + 4 * r]]

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
        i = pro800.SETTING_NAMES.index(name)
        self.request(0x7D, i, value >> 7, value & 0x7F) if value > 127 else self.request(0x7D, i, value)

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
        self.s.press("PERF")
        self.assertShows("InIt")
        self.s.ms(1500)
        self.assertShows("P  3")
        self.assertEqual(self.s.params()["cutoff"], 99)

    def test_undo_brings_the_sound_back_and_again_redoes(self):
        self.s.request(0x52, 0, 8, 0x60, 0)
        self.store_patch(2)
        resonance = self.s.params()["resonance"]
        self.assertEqual(resonance, 74)
        self.s.press("PERF")
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
        self.s.pot("FIL_ENV_AMT", 0.9)
        self.s.pot("FIL_ENV_AMT", 0.1)
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
                          ("CUTOFF", "nAC1"), ("RESONANCE", "nAC2"), ("A_LEVEL", "CC.A"), ("B_LEVEL", "CC.b"), ("NOISE", "CHnL"), ):
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

    def test_detune_stays_0_to_50(self):
        self.s.hold("SHIFT")
        for f in (0.5, 1.0, 0.0, 1.0, 0.0, 1.0):
            self.s.pot("MASTER_TUNE", f)
        self.s.ms(600)
        self.assertShows("  50")
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

    def test_key_1_again_is_the_other_lfo(self):
        self.begin()
        self.s.press("1")
        self.assertShows("LFO1")
        self.s.press("1")
        self.assertShows("LFO2")
        self.s.press("ASSIGN")

    def test_source_destination_depth(self):
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

    def test_value_scrolls_sources_and_assign_takes_one(self):
        self.begin()
        names = []
        for _ in range(27):
            self.s.turn(1)
            names.append(self.s.shows())
        self.assertEqual(len(set(names)), len(names), f"every source reads differently: {names}")
        for wanted in ("SEqA", "SEqb", "SEqC", "SEqd", "L1.U", "L2.U", "GAtE", "tInb", "bEnd", "PEdL", "CC.A", "CC.b", "VoIC", "FIX", "nAC1", "nAC2"):
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

    def test_depth_zero_removes_the_route(self):
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


class MidiOnTheDisplay(PanelTest):
    def test_a_controller_shows_like_its_knob_then_goes_back(self):
        self.s.cc(71, 64)
        self.s.ms(60)
        self.assertShows("  50")
        self.s.ms(1500)
        self.assertShows("P  1")

    def test_cutoff_is_cc_79_in_hz(self):
        self.s.cc(79, 127)
        self.s.ms(60)
        self.assertShows(" 16.7")
        self.assertEqual(self.s.params()["cutoff"], 99)
        self.s.cc(79, 0)
        self.s.ms(60)
        self.assertShows(" 16.4")

    def test_cc_74_is_timbre_and_shows_nothing(self):
        before = self.s.params()["cutoff"]
        self.s.cc(79, 40)
        self.s.ms(1600)
        self.s.history()
        self.s.cc(74, 127)
        self.s.ms(60)
        self.assertEqual(self.s.history(), [])
        self.assertShows("P  1")
        self.assertNotEqual(before, self.s.params()["cutoff"])
        self.assertEqual(self.s.params()["cutoff"], 31)

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

    def test_lfo_2_rate_shows_0_to_99(self):
        self.s.cc(27, 127)
        self.s.ms(60)
        self.assertShows("  99")

    def test_env_3_shows_0_to_99(self):
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

    def test_perf_in_a_save_does_nothing(self):
        self.s.press("REC", "5")
        self.s.press("PERF")
        self.assertShows("P  1", "any other button drops a number being typed")
        self.assertFalse(self.s.settings()["patch"])
        self.s.press("PRESET")

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

    def test_the_edit_layer_gets_the_route(self):
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
        self.assertEqual(self.s.routes(), [])
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
    def test_unison_and_the_waveforms_keep_the_display(self):
        for name in ("A_SAW", "A_TRI", "A_SQUARE", "B_SAW", "B_TRI", "B_SQUARE", "UNISON", "SYNC", "PMOD_FREQ_A", "PMOD_FILTER"):
            with self.subTest(button=name):
                self.s.history()
                self.s.switch(name)
                self.assertEqual([t for _, t in self.s.history()], [], "these only change the sound")
                self.assertShows("P  1")

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

    def test_lfo_routing_on_lfo_1_keeps_the_display(self):
        self.s.history()
        self.s.switch("LFO_FREQ")
        self.assertEqual([t for _, t in self.s.history()], [])


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
        self.s.cc(79, 64, channel=1)
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
        self.s.press("PERF")
        self.s.ms(1600)
        self.assertEqual(self.s.params(0)["cutoff"], 99, "the init sound replaced it")
        self.assertEqual(self.s.routes(0), [])
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
        self.s.press("PERF")
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


def js_block(text, start):
    """A statement or function of the page, from its first line to the line that ends it."""
    i = text.index("\n" + start) + 1
    out = []
    for line in text[i:].split("\n"):
        out.append(line)
        s = re.sub(r"\s*//.*$", "", line.rstrip())
        if start.startswith("function"):
            if s == "}" or (len(out) == 1 and s.endswith("}")):
                break
        elif s.endswith(";"):
            break
    return "\n".join(out)


def page_ranges():
    """What tools/web/index.html works out for routes, rings and the 'where' strings, from its own code in Node."""
    global PAGE_RANGES
    if PAGE_RANGES is not None:
        return PAGE_RANGES
    page = open(os.path.join(ROOT, "tools", "web", "index.html"), encoding="utf-8").read()
    blocks = ["const P = {", "const ENVS = [", "const ENV_KNOB = {};", "for (const [, bit, ...knobs] of ENVS)", "function cycling(",
              "const envIndex", "function cycleSync(", "const envOf", "function cycleShares(", "function cycleShare(", "const knobTime",
              "function envTime(", "const envFlag", "const QUARTERS", "const envTempo", "const noteStep", "const noteName", "const noteSeconds",
              "const DIVISIONS", "function seconds(", "function lfoHz(", "function format(", "const DEST_KNOBS", "const DEST_SCALE",
              "const BIPOLAR_SOURCES", "const FIXED_SOURCE", "const SQUARED_DESTS", "const timeDest", "const rateDest", "const factor",
              "function swingUnit(", "function routeSwing(", "function swingWhere(", "const SOURCES", "const DESTS", "function modRange("]
    script = "let settings = null, params = null, paramsLayer = 0, layerRoutes = null, otherRoutes = [];\nconst feature = () => false;\n"
    script += "\n".join(js_block(page, b) for b in blocks)
    script += """
const out = { unit: {}, swing: {}, where: {}, ring: {}, scale: DEST_SCALE, dests: DESTS.length };
for (let d = 0; d < DESTS.length; d++) {
  out.unit[d] = swingUnit(d)[0];
  for (const s of [25, 4, 1]) for (const dep of [99, 50, 25, 10, -50, -99]) out.swing[`${s},${d},${dep}`] = routeSwing(s, d, dep);
}
params = new Array(36).fill(0.5);
for (const d of [3, 18, 20, 25, 26, 29, 41]) for (const dep of [50, 25, -50]) out.where[`${d},${dep}`] = swingWhere(d, routeSwing(25, d, dep));
for (const [knob, routes] of [[P.CUTOFF, [[25, 3, 50]]], [P.FREQ_A, [[1, 0, 50]]], [P.AMP_ATT, [[25, 25, 50]]], [P.CUTOFF, [[25, 3, 50], [1, 3, 25]]]]) {
  layerRoutes = routes.map(([source, dest, depth]) => ({ source, dest, depth }));
  const r = modRange(knob);
  out.ring[routes.map(x => x.join(",")).join(";")] = { lo: r.lo, hi: r.hi, parts: r.parts.map(p => [p.lo, p.hi]) };
}
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
    synth = open(os.path.join(ROOT, "src", "synth.c")).read()
    c = {name: float(v) for name, v in re.findall(r"#define (\w+)\s+([\d.]+)f", synth)}
    c["TIME_OCTAVES"] = float(re.search(r"fast_exp2\(-?(\d+\.\d+)f \* m\[", synth).group(1))
    c["DETUNE_CENTS"] = float(re.search(r"DEST_DETUNE\] \* (\d+\.\d+)f", synth).group(1))
    c["GLIDE_OCTAVES"] = float(re.search(r"0\.002f \* fast_exp2\(knob \* (\d+\.\d+)f\)", synth).group(1))
    c["LFO_KNOB_OCTAVES"] = float(re.search(r"0\.05f \* fast_exp2\(knob \* (\d+\.\d+)f\)", open(os.path.join(ROOT, "src", "lfo.c")).read()).group(1))
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
        self.assertEqual(unit["41"], c["TIME_OCTAVES"])
        self.assertAlmostEqual(scale["41"], c["TIME_OCTAVES"] / c["GLIDE_OCTAVES"], 6)
        self.assertEqual(unit["42"], c["DETUNE_CENTS"])
        self.assertEqual(self.page["dests"], 46)

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
        at = 53 + 10 * lfo  # synth_probe(): the LFO's shared phase, in hundredths
        p1 = self.s.reply(0x55)[at]
        self.s.ms(ms)
        p2 = self.s.reply(0x55)[at]
        return (p2 - p1) % 100 / 100 / ms * 1000  # Hz

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
        self.assertAlmostEqual(base, 154, delta=4)
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


class EveryName(unittest.TestCase):
    def test_every_text_for_the_display_has_glyphs(self):
        main = open(os.path.join(ROOT, "src", "main.c")).read()
        texts = set(re.findall(r'display_text\("([^"]*)"\)', main))
        for table in re.findall(r"(?:names?|modes|curves|values)\[[^\]]*\](?:\[[^\]]*\])* = \{(.*?)\};", main, re.S):
            texts.update(re.findall(r'"([^"\n]*)"', table))
        lfo = open(os.path.join(ROOT, "src", "lfo.c")).read()
        for table in re.findall(r"names?\[[^\]]*\](?:\[[^\]]*\])* = \{(.*?)\};", lfo, re.S):
            texts.update(re.findall(r'"([^"\n]*)"', table))
        self.assertGreater(len(texts), 80)
        missing = {}
        for text in sorted(texts):
            for c in text:
                if c not in ". " and c.upper() not in GLYPHS:
                    missing.setdefault(text, []).append(c)
        self.assertEqual(missing, {}, "these can't be read on the display")


if __name__ == "__main__":
    build()
    unittest.main(verbosity=1)
