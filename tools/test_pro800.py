#!/usr/bin/env python3
# Tests tools/pro800.py against a simulated synth: `python tools/test_pro800.py`. No MIDI port is opened. The replies
# below are built the way src/main.c and src/synth.c build them, the requests are checked byte for byte, and the name
# tables are compared with tools/pro800.swift's.
import contextlib
import glob
import io
import json
import itertools
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import pro800  # noqa: E402

HEADER = pro800.SYSEX_HEADER


def no_child_process(*command, **options):
    raise AssertionError(f"a test would have run the real tool, on the real port: {command}")


pro800.run_self = no_child_process  # a test that needs it patches in its own


def request_bytes(*data):
    return HEADER + bytes(data) + b"\xF7"


# what fault_provoke() leaves: a precise bus fault at the unmapped address, escalated to a hard fault
CRASH = dict(streak=1, exception=3, pc=0x08012345, lr=0x0801ABCD, exc_return=0xFFFFFFE9, cfsr=0x00008200, hfsr=0x40000000,
             mmfar=0xFEDCBA98, bfar=0x30000000)
CRASH_TEXT = ("last crash: HardFault (exception 3)\n"
              "  pc         0x08012345\n"
              "  lr         0x0801ABCD\n"
              "  EXC_RETURN 0xFFFFFFE9\n"
              "  CFSR       0x00008200 PRECISERR BFARVALID\n"
              "  HFSR       0x40000000 FORCED\n"
              "  MMFAR      0xFEDCBA98 (not valid)\n"
              "  BFAR       0x30000000\n"
              "  streak     1\n")


class FakeSynth:
    """Our firmware's SysEx, as far as the tool's commands need it."""

    def __init__(self, firmware=(0, 9, 3)):
        self.firmware = firmware
        self.sent = []
        self.receive = None
        self.settings = [0] * 99
        self.undo = [1, *b"Pad"]
        self.init_theirs = 1  # 5F 1: Perf's sound, the user's init patch (0.10.0)
        self.chords = [[0] * 8, [0] * 8]  # 68 / 69 (0.10.0)
        self.cc_ids, self.cc_nibbles = [127] * 128, [0] * 128  # 65 / 43 / 42 (0.10.0)
        self.cc_ids[74] = 24
        self.captured = (3, 0)  # 6A's reply: the notes each layer took (127: left alone)
        self.has_via = True
        self.vias = [[0] * 16, [0] * 16]
        self.fines = [[0] * 16, [0] * 16]
        self.buttons = [0] * 16
        self.steps = {}
        self.params = [[0] * 36, [0] * 36]
        self.pots = [0] * 26
        self.switches = [1] * 24
        self.routes = [[(0, 0, 0)] * 16, [(0, 0, 0)] * 16]
        self.sequence = bytearray([8, 0, 12, 0, 120, 0, 0, 0, 64, 0])  # length lanes division bpm(2) flags swing smooth max arp
        self.seq_steps = [[64, 100, 50, 1, 0, 64, 64, 64, 64] for _ in range(64)]
        self.slots = {}
        self.eeprom = bytearray((i * 7) & 0xFF for i in range(0x20000))
        self.note_hz = None
        self.silent = False  # stock firmware: answers nothing of ours
        self.start_messages = []
        self.panel_events = []
        self.crash = None  # the record of src/fault.h: a dict, or None
        self.crashes = True  # takes the crash request (an older firmware ignores it)
        self.format_count = (1, 0)
        self.format_failed = 0
        self.cut = None  # (reply prefix, bytes kept): replies shorter than they should be

    def open(self, source, destination, receive):
        self.receive = receive
        self.later(self.start_messages)
        return self

    def later(self, messages):  # as the real thing: while the command is waiting
        def deliver():
            for message in messages:
                self.receive(message)
        threading.Timer(0.03, deliver).start()

    def close(self):
        pass

    def reply(self, *body):
        if self.cut and bytes(body[:len(self.cut[0])]) == self.cut[0]:
            body = body[:self.cut[1]]
        self.receive(HEADER + bytes(body) + b"\xF7")

    def ack(self, command):
        self.reply(0x7F, command)

    def send(self, data):
        self.sent.append(bytes(data))
        if data.startswith(HEADER) and not self.silent:
            self.handle(data[len(HEADER):-1])

    def handle(self, d):
        c = d[0]
        if c == 0x08:
            self.reply(0x09, 0x00, *self.firmware)
        elif c == 0x54:
            values = []
            for v in self.settings:
                values += [v >> 7 & 0x7F, v & 0x7F]
            self.reply(0x54, len(self.settings), *values)
        elif c == 0x4C:
            self.reply(0x4C, *self.buttons)
        elif c == 0x4D:
            count, bits = self.steps.get((d[1], d[2]), (16, 0))
            self.reply(0x4D, d[1], d[2], count, bits >> 14 & 0x7F, bits >> 7 & 0x7F, bits & 0x7F)
        elif c == 0x53:
            values = []
            for v in self.params[d[1]]:
                values += [v >> 7, v & 0x7F]
            self.reply(0x53, d[1], len(self.params[d[1]]), *values)
        elif c == 0x7C:
            values = []
            for v in self.pots:
                values += [v >> 7, v & 0x7F]
            self.reply(0x7C, *values, *self.switches)
        elif c == 0x55:
            self.engine_probe()
        elif c == 0x48:
            self.reply(0x48, *self.sequence)
        elif c == 0x46:
            out = []
            for s in self.seq_steps[d[1]:d[1] + d[2]]:
                out += s
            self.reply(0x46, d[1], d[2], *out)
        elif c == 0x5E:
            kind, name = self.slots.get(d[1] << 7 | d[2], (0, b""))
            self.reply(0x5E, d[1], d[2], kind, *name)
        elif c == 0x57:
            out = []
            for source, dest, depth in self.routes[d[1]]:
                out += [source, dest, (depth + 99) >> 7, (depth + 99) & 0x7F]
            self.reply(0x57, d[1], *out)
        elif c == 0x58:
            out = []
            for us in (100, 200, 300, 400, 500, 600, 1000, 800):
                out += [us >> 14 & 0x7F, us >> 7 & 0x7F, us & 0x7F]
            self.reply(0x58, *out)
        elif c == 0x77:
            out = []
            for p in range(14):
                centihertz = 0 if d[1] == 3 else 2750 * (p + 1)
                out += [centihertz >> shift & 0x7F for shift in (21, 14, 7, 0)]
            self.reply(0x77, d[1], 14, *out)
        elif c == 0x79:
            address, n = d[1] << 14 | d[2] << 7 | d[3], d[4]
            out = [0x01, 0x02]  # status register 0x12
            for b in self.eeprom[address:address + n]:
                out += [b >> 4, b & 0x0F]
            self.reply(*d, *out)
        elif c == 0x5A:
            address, n = d[1] << 14 | d[2] << 7 | d[3], d[4]
            self.eeprom[address:address + n] = bytes(d[5 + 2 * i] << 4 | d[6 + 2 * i] for i in range(n))
            self.ack(0x5A)
        elif c == 0x50:
            self.reply(0x50, d[1], 1, *b"Factory") if d[1] == 0 else self.reply(0x50, d[1], 0)
        elif c == 0x51:
            self.reply(0x51, 0)
        elif c == 0x5B:
            self.reply(0x5B, *self.format_count, self.format_failed)
        elif c == 0x75:
            self.reply(0x76, 24)
        elif c == 0x72:
            self.measurement()
        elif c == 0x78:
            self.note_hz = 440 * 2 ** ((d[3] - 69) / 12)
            self.ack(0x78)
        elif c == 0x6C:
            self.reply(0x7F, 0x6C, 1)
        elif c == 0x7A:
            self.ack(0x7A)
            if d[1] == 1:
                self.later([HEADER + bytes(event) + b"\xF7" for event in self.panel_events])
        elif c == 0x7D:
            self.settings[d[1]] = d[2] << 7 | d[3]
            self.ack(0x7D)
        elif c in (0x03,):
            pass
        elif c == 0x49:
            self.reply(0x49, *self.fault_body())
        elif c == 0x63:
            if self.has_via:
                self.reply(0x63, d[1], *[b for v in self.vias[d[1]] for b in (v >> 7, v & 0x7F)])
        elif c == 0x62:
            self.ack(0x62)
        elif c == 0x68:
            self.reply(0x68, *self.chords[0], *self.chords[1])
        elif c == 0x65:
            q = d[1]
            self.reply(0x65, q, *[b for cc in range(32 * q, 32 * q + 32) for b in (self.cc_ids[cc], self.cc_nibbles[cc])])
        elif c == 0x43 and len(d) == 4:
            self.cc_ids[d[1]], self.cc_nibbles[d[1]] = d[2], d[3]
            self.ack(0x43)
        elif c == 0x42:
            self.cc_ids, self.cc_nibbles = [127] * 128, [0] * 128
            self.cc_ids[74] = 24
            self.ack(0x42)
        elif c == 0x69 and len(d) == 10:
            self.chords[d[1]] = list(d[2:10])
            self.ack(0x69)
        elif c == 0x6A:
            self.reply(0x6A, *self.captured)
        elif c == 0x67:
            if self.has_via:
                self.reply(0x67, d[1], *[f + 9 for f in self.fines[d[1]]])
        elif c == 0x66:
            if self.has_via:
                self.ack(0x66)
        elif c == 0x60:
            self.reply(0x60, *self.undo)
        elif c == 0x5F and len(d) == 2:
            self.reply(0x5F, self.init_theirs)
        elif c == 0x4A:
            if self.crashes and d[1:3] == b"\x43\x52":
                streak = self.crash["streak"] + 1 if self.crash else 1
                self.crash = dict(CRASH, streak=streak)
        else:
            self.ack(c)  # the commands that only acknowledge

    def fault_body(self):  # as src/main.c builds 49's reply
        if self.crash is None:
            return bytes(3 + 5 * 7)
        c = self.crash
        out = [1, min(c["streak"], 0x7F), c["exception"]]
        for name in ("pc", "lr", "exc_return", "cfsr", "hfsr", "mmfar", "bfar"):
            out += [c[name] >> shift & 0x7F for shift in (28, 21, 14, 7, 0)]
        return bytes(out)

    def engine_probe(self):
        out = [2, 60, 64, 0, 0, 0, 0, 0, 0, 0]  # held keys per layer: count, four notes
        for v in range(8):
            out += [1 if v == 0 else 0, 1 if v == 0 else 0, 3 if v == 1 else 0]  # gate, amp stage, filter stage
        out += [2, 0, 1]  # keytrack setting, switch seen + 1, switch now
        out += [0b011 if s == 0 else 0b100 if s == 1 else 0 for s in range(16)]  # override | value << 1 | panel << 2
        for l in range(4):
            out += [25, l, 100] + [50] * 7  # LFO: shared phase, cycles done, 8 voices
        self.reply(0x55, *out)

    def measurement(self):
        if self.note_hz is None:
            self.reply(0x73, 0)
            return
        khz, periods = 168000, 20
        cycles = round(periods * khz * 1000 / self.note_hz)
        self.reply(0x73, 1, periods >> 7, periods & 0x7F, *[cycles >> shift & 0x7F for shift in (28, 21, 14, 7, 0)],
                   *[khz >> shift & 0x7F for shift in (14, 7, 0)])


def run(synth, *argv, port_list=None):
    """Runs one command in-process; returns (output, exit code)."""
    names = port_list or (["PRO 800"], ["PRO 800"])
    out = io.StringIO()
    pro800.args = list(argv)
    command = pro800.args.pop(0)
    code = 0
    with mock.patch.object(pro800, "port_names", lambda: names), mock.patch.object(pro800, "open_transport", synth.open):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                pro800.COMMANDS[command]()
            except SystemExit as e:
                code = e.code or 0
            finally:
                pro800.close_midi()
    return out.getvalue(), code


class SwiftParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "pro800.swift"), encoding="utf-8") as f:
            cls.swift = f.read()

    def table(self, name):
        found = re.search(r"\b" + name + r"(?:: \[String\])? = \[(.*?)\]\n?", self.swift, re.S)
        self.assertIsNotNone(found, name)
        return re.findall(r'"([^"]*)"', found.group(1))

    def test_name_tables(self):
        for swift_name, table in (
                ("switchNames", pro800.SWITCH_NAMES), ("potNames", pro800.POT_NAMES), ("switchLabels", pro800.SWITCH_LABELS),
                ("shapeNames", pro800.SHAPE_NAMES), ("lfo2DestNames", pro800.LFO2_DEST_NAMES), ("cycleNames", pro800.CYCLE_NAMES),
                ("linkNames", pro800.LINK_NAMES), ("curveNames", pro800.CURVE_NAMES), ("scaleNames", pro800.SCALE_NAMES),
                ("rootNames", pro800.ROOT_NAMES), ("divisionNames", pro800.DIVISION_NAMES), ("paramNames", pro800.PARAM_NAMES),
                ("sourceNames", pro800.SOURCE_NAMES), ("destNames", pro800.DEST_NAMES), ("arpModeNames", pro800.ARP_MODE_NAMES),
                ("gateNames", pro800.GATE_NAMES), ("layerModes", pro800.LAYER_MODES),
                ("triggerModes", pro800.TRIGGER_MODES), ("voiceModes", pro800.VOICE_MODES), ("priorities", pro800.PRIORITIES)):
            self.assertEqual(self.table(swift_name), table, swift_name)

    def test_tables_inside_functions(self):
        with open(os.path.join(HERE, "pro800.py"), encoding="utf-8") as f:
            python = f.read()
        for name in ("stages", "kinds"):
            ours = re.search(name + r" = \[(.*?)\]", python).group(1)
            self.assertEqual(self.table(name), re.findall(r'"([^"]*)"', ours), name)

    def test_buttons(self):
        found = re.search(r"let buttonNames: \[Int: String\] = \[(.*?)\]\n", self.swift, re.S).group(1)
        self.assertEqual({int(k): v for k, v in re.findall(r'([0-9]+): "([a-z-]+)"', found)}, pro800.BUTTON_NAMES)

    def test_fault_tables(self):
        def pairs(name):
            found = re.search(r"let " + name + r": .*? = \[(.*?)\]\n", self.swift, re.S).group(1)
            return [(int(k), v) for k, v in re.findall(r'\(([0-9]+), "([A-Z]+)"\)', found)]

        self.assertEqual(pairs("cfsrBits"), pro800.CFSR_BITS)
        self.assertEqual(pairs("hfsrBits"), pro800.HFSR_BITS)
        names = re.search(r"let exceptionNames: .*? = \[(.*?)\]\n", self.swift, re.S).group(1)
        self.assertEqual({int(k): v for k, v in re.findall(r'([0-9]+): "([A-Za-z]+)"', names)}, pro800.EXCEPTION_NAMES)

    def test_setting_names_match_in_order(self):
        block = re.search(r"let settingNames: \[String\] = (.*?\"morph\"\])", self.swift, re.S).group(1)
        names = []
        for envelope, literal in re.findall(r'perEnvelope\("([^"]+)"\)|"([^"]+)"', block):
            names += pro800.per_envelope(envelope) if envelope else [literal]
        self.assertEqual(names, pro800.SETTING_NAMES)

    def test_setting_names(self):
        self.assertEqual(len(pro800.SETTING_NAMES), 108)
        self.assertEqual(pro800.SETTING_NAMES.index("midi-channel"), 77)
        self.assertEqual(pro800.SETTING_NAMES.index("env-count 2 env4"), 74)
        self.assertEqual(pro800.SETTING_NAMES.index("lfo-restart"), 86)
        self.assertEqual(pro800.SETTING_NAMES[91:], ["unison-voices", "arp-range", "arp-extend", "arp-chaos", "init-patch",
                                                     "midi-socket", "notes-out", "out-channel", "clock-out", "knob-cc", "cc-out-a",
                                                     "cc-out-b", "pc-out", "env-select", "preset-mode", "patch", "morph"])

    def test_lfo_note_times_read_the_same(self):
        """Swift can't run here: the same formula, version and condition as pro800.py's, read off the source."""
        self.assertIn('let step = min(Int(Double(v) / 99 * 15 + 0.5), 15)', self.swift)
        self.assertIn('return v == 0 ? "off" : "\\(v) (\\(step == 0 ? "off" : divisionNames[15 - step]))"', self.swift)
        self.assertIn('return Array(reply.dropFirst(sysexHeader.count + 2).dropLast()).lexicographicallyPrecedes([0, 9, 4]) == false', self.swift)
        self.assertIn('let v = reply.dropFirst(sysexHeader.count + 2).dropLast()', self.swift, "where `version` reads it from")
        self.assertIn('case 32...35: text = notes && synced[(i - 32) / 2] ? lfoNoteText(v)', self.swift)
        self.assertIn('let synced = [0, 5].map { count > $0 && (Int(b[1 + 2 * $0]) << 7 | Int(b[2 + 2 * $0])) == 1 }', self.swift)
        self.assertIn('let notes = synced.contains(true) && lfoNoteTimes(midi)', self.swift)

    def test_commands_match_both_ways(self):
        commands = set(re.findall(r'^case "([a-z0-9-]+)":', self.swift, re.M))
        self.assertTrue(commands)
        self.assertEqual(commands - set(pro800.COMMANDS), set())
        self.assertEqual(set(pro800.COMMANDS) - commands, {"port-present"})  # install's child process; the Swift tool asks CoreMIDI

    def swift_help(self):
        return re.search(r'let helpText = """\n(.*?)\n"""', self.swift, re.S).group(1)

    def test_help_lists_every_command(self):
        for name, text, known in (("Python", pro800.HELP_TEXT, set(pro800.COMMANDS) - {"port-present", "record"}),
                                  ("Swift", self.swift_help(), set(pro800.COMMANDS) - {"port-present"})):
            listed = set(re.findall(r"\bpro800 ([a-z0-9-]+)", text)) & set(pro800.COMMANDS)
            self.assertEqual(listed, known, name)

    def test_help_says_the_same(self):
        audio = ("record", "--record", "play <note>", "Run as", "level and pitch")
        lines = lambda text: [line for line in text.splitlines() if line.strip() and not any(word in line for word in audio)]
        self.assertEqual(lines(pro800.HELP_TEXT), lines(self.swift_help()))

    def test_the_same_messages(self):
        for text in ("the bootloader did not accept the header; nothing was written", "the bootloader did not confirm the update",
                     "the bootloader refused the update", "takes a fresh header", "A stock firmware .syx also brings it back",
                     "hold buttons 0 and 7", "short reply (", "the stock firmware does not do this", "half old, half new",
                     "The image carries the tuning tables", "no fault recorded", "holds no record of this crash",
                     "the same record as before the crash",
                     "the synth is in its bootloader", "Restart the MIDI service or replug", "BEHRINGER PRO 800", "characters dropped",
                     "--channel must be 1-16", "notes 0-127", "the file is cut short", "only a header", "pro800 update-mode --yes",
                     "--erase-wait can't be negative", "the data blocks aren't in order",
                     "If the synth still plays, it wasn't in its bootloader and nothing changed. If it is there, it stays there ",
                     "\\nThe synth stays in its bootloader "):
            with open(os.path.join(HERE, "pro800.py"), encoding="utf-8") as f:
                self.assertIn(text, f.read(), "pro800.py")
            self.assertIn(text, self.swift, "pro800.swift")

    def test_swift_says_stuck_in_the_bootloader_only_after_the_accept(self):
        flash = self.swift[self.swift.index("func flash()"):self.swift.index("let usbFirmware")]
        self.assertEqual(flash.count("failNote = flashRecovery"), 1)
        self.assertLess(flash.index("accepted = true"), flash.index("failNote = flashRecovery"))
        self.assertLess(flash.index("failNote = flashRecovery"), flash.index("failNote = flashNotStarted"), "inside takeReplies")
        self.assertLess(flash.index("failNote = flashNotStarted"), flash.index("midi.send(m)"))

    def test_swift_takes_an_empty_reply_only_for_backup(self):
        self.assertEqual(self.swift.count("allowEmpty: true"), 2)
        backup = self.swift[self.swift.index("func backup()"):self.swift.index("// MARK: - play / record")]
        self.assertEqual(backup.count("allowEmpty: true"), 2)

    def test_swift_parsers_go_through_the_helper(self):
        # only the channel lookup, which must not fail on a firmware that can't say, reads a reply by hand
        self.assertEqual(len(re.findall(r"Array\((?:reply|r|h)\[\(?sysexHeader", self.swift)), 1)

    def test_swift_usage_is_current(self):
        self.assertNotIn("A00", self.swift)
        self.assertNotIn("0-199", self.swift)

    def test_help_mentions_what_the_preset_installer_greps_for(self):
        self.assertIn("seq set", pro800.HELP_TEXT)
        self.assertIn("set morph", pro800.HELP_TEXT)


class ReadOnly(unittest.TestCase):
    def setUp(self):
        self.synth = FakeSynth()

    def test_ports(self):
        out, code = run(self.synth, "ports", port_list=(["PRO 800", "Other"], ["GS", "PRO 800"]))
        self.assertEqual(code, 0)
        self.assertEqual(out, "MIDI sources:\n  PRO 800\n  Other\nMIDI destinations:\n  GS\n  PRO 800\n")

    def test_version(self):
        self.assertEqual(run(self.synth, "version"), ("firmware 0.9.3\n", 0))
        self.assertEqual(self.synth.sent, [request_bytes(0x08, 0x00)])

    def test_port_names(self):
        decorated = (["Move", "PRO 800:PRO 800 MIDI 1 28:0"], ["PRO 800:PRO 800 MIDI 1 28:0"])
        second_synth = (["2- PRO 800"], ["2- PRO 800"])
        with mock.patch.object(pro800.sys, "platform", "win32"):
            self.assertEqual(pro800.find_port(["Move", "PRO 800"], "PRO 800"), 1)
            self.assertEqual(run(self.synth, "version", port_list=second_synth)[1], 1)  # never another synth
        with mock.patch.object(pro800.sys, "platform", "linux"):
            self.assertEqual(run(self.synth, "version", port_list=decorated)[1], 0)
            self.assertEqual(run(self.synth, "version", port_list=(["PRO 800 a", "PRO 800 b"], ["PRO 800 a"]))[1], 1)

    def test_missing_port(self):
        out, code = run(self.synth, "version", port_list=(["Move"], ["Move"]))
        self.assertEqual(code, 1)
        self.assertIn('MIDI port "PRO 800" not found', out)

    def test_no_reply(self):
        self.synth.silent = True
        with mock.patch.object(pro800.Midi, "request", lambda *a, **k: None):
            out, code = run(self.synth, "settings")
        self.assertEqual(code, 1)
        self.assertIn("no reply (firmware 0.6.0 or later?)", out)

    def test_settings(self):
        s = self.synth.settings
        s[0], s[2], s[4], s[13], s[25], s[26] = 1, 2, 2, 5, 12, 25
        s[38], s[43], s[48], s[50], s[66], s[67] = 104, 120, 1 | 2 << 2, 99, 3, 7
        s[77], s[83], s[86], s[97], s[98] = 5, 65, 1, 127, 1
        self.synth.buttons[0], self.synth.buttons[15] = 1, 1
        self.synth.steps[(1, 1)] = (4, 0b0101)
        out, code = run(self.synth, "settings")
        self.assertEqual(code, 0)
        for line in ("lfo-sync             clock", "layers               stack", "keytrack             full",
                     "lfo2-dests 1         pitch,pw", "bend                 ±12 semitones", "detune               25 cents",
                     "macro 1 1            +5", "bpm                  120 BPM", "env-delay 1 amp      99 (10.000 s)",
                     "env-curve 1          amp linear, filter swell, env3 analog, env4 analog",
                     "layer-voices         3 + 5", "env-count 1 amp      7 cycles", "midi-channel         5",
                     "link                 amp-env,filter", "patch                P128", "morph                P1",
                     "buttons              a-saw sync", "steps 2 2            x.x."):
            self.assertIn(line + "\n", out)

    def test_settings_of_0_10_0_with_the_init_patch(self):
        self.synth.settings = [0] * 100
        s = self.synth.settings
        s[95], s[96], s[98], s[99] = 120, 3, 4, 0
        out, code = run(self.synth, "settings")
        self.assertEqual(code, 0)
        for line in ("init-patch           P120", "env-select           env 3 / env 4 (Shift)", "patch                P5", "morph                none"):
            self.assertIn(line + "\n", out)
        s[95] = 0
        out, _ = run(self.synth, "settings")
        self.assertIn("init-patch           none (the built-in sound)\n", out)

    def test_settings_of_0_10_0_with_midi_out(self):
        self.synth.settings = [0] * 108
        s = self.synth.settings
        s[96], s[97], s[98], s[99], s[101], s[103], s[104], s[106] = 2, 2, 5, 1, 20, 1, 3, 4
        out, code = run(self.synth, "settings")
        self.assertEqual(code, 0)
        for line in ("midi-socket          merge", "notes-out            played", "out-channel          5", "clock-out            on",
                     "knob-cc              off", "cc-out-a             CC 20", "cc-out-b             CC 13 (default)", "pc-out               on",
                     "env-select           env 3 / env 4 (Shift)", "patch                P5"):
            self.assertIn(line + "\n", out)
        for argv, sent in ((("set", "midi-socket", "out"), [0x7D, 96, 0, 1]), (("set", "notes-out", "keys"), [0x7D, 97, 0, 1]),
                           (("set", "out-channel", "follow"), [0x7D, 98, 0, 0]), (("set", "out-channel", "16"), [0x7D, 98, 0, 16]),
                           (("set", "clock-out", "on"), [0x7D, 99, 0, 1]), (("set", "knob-cc", "off"), [0x7D, 100, 0, 0]),
                           (("set", "cc-out-b", "default"), [0x7D, 102, 0, 0]), (("set", "pc-out", "on"), [0x7D, 103, 0, 1])):
            with self.subTest(argv=argv):
                synth = FakeSynth()
                synth.settings = [0] * 108
                run(synth, *argv)
                self.assertEqual(synth.sent[-1], request_bytes(*sent))
        for cc in ("1", "64", "120", "0", "128"):  # a CC with its own meaning (the firmware refuses it too), or none
            with self.subTest(cc=cc):
                synth = FakeSynth()
                synth.settings = [0] * 108
                out, code = run(synth, "set", "cc-out-a", cc)
                self.assertNotEqual(code, 0, out)
                self.assertEqual(synth.sent, [], "nothing sent")
        synth = FakeSynth()
        synth.settings = [0] * 108
        run(synth, "set", "cc-out-a", "20")
        self.assertEqual(synth.sent[-1], request_bytes(0x7D, 101, 0, 20))

    def test_settings_of_an_older_firmware(self):
        self.synth.settings = self.synth.settings[:82]
        self.synth.settings[-1] = 2  # read-only ones are the reply's last four whatever the count
        self.synth.settings[-2] = 6
        out, _ = run(self.synth, "settings")
        self.assertIn("morph                P2\n", out)
        self.assertIn("patch                P7\n", out)
        self.assertNotIn("pickup", out)

    def test_a_synced_lfos_delay_and_fade_are_note_values(self):
        synth = FakeSynth(firmware=(0, 9, 4))
        s = synth.settings
        s[0], s[32], s[33], s[34], s[35] = 1, 60, 2, 60, 0  # LFO 1 on the clock, LFO 2 free
        out, code = run(synth, "settings")
        self.assertEqual(code, 0)
        for line in ("lfo-delay            60 (1/4)", "lfo-fade             2 (off)", "lfo2-delay           60 (0.58 s)", "lfo2-fade            off"):
            self.assertIn(line + "\n", out)
        s[0], s[5], s[35] = 0, 1, 99  # the other way round
        out, _ = run(synth, "settings")
        for line in ("lfo-delay            60 (0.58 s)", "lfo-fade             2 (0.02 s)", "lfo2-delay           60 (1/4)", "lfo2-fade            99 (8bar)"):
            self.assertIn(line + "\n", out)

    def test_the_note_values_steps(self):
        synth = FakeSynth(firmware=(0, 9, 4))
        synth.settings[0] = 1
        for value, text in ((0, "off"), (3, "3 (off)"), (4, "4 (1/32)"), (56, "56 (1/4T)"), (57, "57 (1/4)"), (62, "62 (1/4)"), (63, "63 (1/4.)"), (99, "99 (8bar)")):
            with self.subTest(value=value):
                synth.settings[32] = value
                self.assertIn(f"lfo-delay            {text}\n", run(synth, "settings")[0])

    def test_firmware_before_0_9_4_still_acts_in_seconds(self):
        for firmware in ((0, 9, 3), (0, 8, 9)):
            synth = FakeSynth(firmware=firmware)
            synth.settings[0], synth.settings[32] = 1, 60
            self.assertIn("lfo-delay            60 (0.58 s)\n", run(synth, "settings")[0])
            self.assertIn(request_bytes(0x08, 0x00), synth.sent)

    def test_later_firmware_keeps_the_note_values(self):
        for firmware in ((0, 9, 5), (1, 0, 0)):
            synth = FakeSynth(firmware=firmware)
            synth.settings[5], synth.settings[34] = 1, 60
            self.assertIn("lfo2-delay           60 (1/4)\n", run(synth, "settings")[0])

    def test_with_no_lfo_synced_the_version_isnt_asked(self):
        self.synth.settings[32] = 60
        self.assertIn("lfo-delay            60 (0.58 s)\n", run(self.synth, "settings")[0])
        self.assertNotIn(request_bytes(0x08, 0x00), self.synth.sent)

    def test_the_settings_of_a_firmware_with_too_few_for_the_sync(self):
        self.synth.settings = [1, 0, 0]
        self.assertEqual(run(self.synth, "settings")[1], 0)

    def test_params(self):
        self.synth.params[1][24] = 16383
        self.synth.params[1][0] = 8192
        out, _ = run(self.synth, "params", "2")
        self.assertIn("layer 2:\n", out)
        self.assertIn("amp-rel       50", out)
        self.assertIn("cutoff        99", out)
        self.assertNotIn("layer 1:", out)

    def test_state(self):
        self.synth.pots[24] = 4095
        self.synth.pots[0] = 2048
        self.synth.switches[0] = self.synth.switches[12] = self.synth.switches[14] = 0
        out, _ = run(self.synth, "state")
        self.assertIn("CUTOFF         99\n", out)
        self.assertIn("AMP REL        49\n", out)
        self.assertIn("on: Osc A Saw, Unison\n", out)
        self.assertIn("keyboard tracking: Full\n", out)

    def test_engine(self):
        out, _ = run(self.synth, "engine")
        self.assertIn("layer 1 held keys: 2 (60 64)\n", out)
        self.assertIn("layer 2 held keys: 0\n", out)
        self.assertIn("voice 1: gate 1, amp attack, filter idle\n", out)
        self.assertIn("voice 2: gate 0, amp idle, filter release\n", out)
        self.assertIn("keytrack full, switch last seen unknown, now half\n", out)
        self.assertIn("switch OSC1_SAW: patch holds it, 1, panel 0\n", out)
        self.assertIn("switch OSC1_TRI: follows the panel, panel 1\n", out)
        self.assertIn("LFO 1 layer 1: shared 25%, cycles done 0; voices stopped 50% 50% 50% 50% 50% 50% 50%\n", out)
        self.assertIn("LFO 2 layer 2: shared 25%, cycles done 3;", out)

    def test_seq(self):
        q = self.synth.sequence
        q[0], q[1], q[5] = 9, 2, 1 | 1 << 5  # 9 steps, one lane + one chord lane, on
        self.synth.seq_steps[0] = [69, 100, 100, 1 | 3 << 1, 0b01100, 127, 67, 64, 64]  # tie, holds 3, 4 in 8 × 2
        self.synth.seq_steps[1] = [64, 100, 50, 0, 0, 1, 64, 64, 64]  # a rest
        out, _ = run(self.synth, "seq")
        lines = out.splitlines()
        self.assertEqual(lines[0], "on, 9 steps of 1/16, 120 BPM, swing 0 %, loops, keys of both layers, 1 lane, 2-note chords (max 64 steps)")
        self.assertEqual(lines[1], "note   +5   .   0   0   0   0   0   0   0")
        self.assertEqual(lines[2], "n2     +3   .   .   .   .   .   .   .   .")
        self.assertEqual(lines[3], "gate  tie   .  50  50  50  50  50  50  50")
        self.assertTrue(lines[4].startswith("hold    3   .   ."))
        self.assertTrue(lines[5].startswith("prob  4x2   .   ."))
        self.assertTrue(lines[6].startswith("seq A +99 -99  +0"))
        # two requests: steps 1-8, then 9-16
        self.assertIn(request_bytes(0x46, 8, 8), self.synth.sent)

    def test_patches(self):
        self.synth.slots = {0: (1, b"Lead"), 5: (2, b""), 127: (3, b"")}
        out, _ = run(self.synth, "patches")
        self.assertEqual(out, "P1  ours    Lead\nP6  STOCK   \nP128  corrupt \n1 ours, 1 stock, 125 empty, 1 corrupt\n")
        self.assertEqual(len(self.synth.sent), 128)
        self.assertEqual(self.synth.sent[127], request_bytes(0x5E, 0, 127))
        self.assertEqual(run(self.synth, "patches", "--all")[0].count("\n"), 129)

    def test_routes(self):
        self.synth.routes[0][1] = (1, 3, -50)
        self.synth.routes[1][9] = (27, 45, 99)
        self.synth.settings[87] = 0b10  # layer 1, slot 1 plays on both layers
        out, _ = run(self.synth, "routes")
        self.assertEqual(out, "layer 1: \n  lfo → cutoff: -50 (both layers)\nlayer 2: \n  macro2 → macro2: 99\n")

    def test_routes_marks_one_at_0(self):
        self.synth.routes[0][1] = (1, 3, 0)
        self.synth.settings[87] = 0b10
        out, _ = run(self.synth, "routes")
        self.assertEqual(out, "layer 1: \n  lfo → cutoff: 0 (both layers) (plays nothing; 5 s at 0 removes it)\nlayer 2: no routes\n")
        swift = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pro800.swift"), encoding="utf-8").read()
        self.assertIn('" (plays nothing; 5 s at 0 removes it)"', swift, "the same in both tools")

    def test_routes_with_none_says_so(self):
        out, _ = run(self.synth, "routes")
        self.assertEqual(out, "layer 1: no routes\nlayer 2: no routes\n")

    def test_timing(self):
        with mock.patch.object(pro800.time, "sleep"):
            out, _ = run(self.synth, "timing")
        self.assertEqual(out, "engine 100 µs, CV refresh 200 µs (of which the four ticks 800 µs), main-loop pass 300 µs (longest in 2 s: 400 µs)\n"
                              "panel poll 500 µs, display 600 µs (last pass)\n"
                              "1000 passes in the last second: 1000 µs per pass on average, modulation 1000 times a second\n")

    def test_tuning(self):
        out, _ = run(self.synth, "tuning")
        lines = out.splitlines()
        self.assertEqual(lines[0], "row           2000    3000    4000    5000    6000    7000    8000    9000    A000    B000    C000    D000    E000    F000")
        self.assertTrue(lines[1].startswith("osc A1        27.5    55.0    82.5"))
        self.assertTrue(lines[4].startswith("osc A4           -       -"))
        self.assertTrue(lines[24].startswith("filter8 "))
        self.assertEqual(len(lines), 25)

    def test_check(self):
        out, code = run(self.synth, "check", "60", "--osc")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"osc A1                [+-]0\.0\n")
        self.assertRegex(out, r"osc B8                [+-]0\.0\n")
        self.assertIn("worst oscillator: 0.0 cents\n", out)
        self.assertNotIn("filter", out)
        self.assertEqual(self.synth.sent[0], request_bytes(0x74, 1))
        self.assertEqual(self.synth.sent[-1], request_bytes(0x74, 0))

    def test_check_retries_an_octave_high_reading(self):
        readings = itertools.cycle([(523.2511, 20), (261.6256, 20)])  # C5 read for C4, then right
        with mock.patch.object(pro800, "measure_once", lambda *a: next(readings)):
            out, _ = run(self.synth, "check", "60", "--filters")
        self.assertRegex(out, r"filter 1              [+-]0\.0\n")
        self.assertIn("worst filter: 0.0 cents", out)

    def test_check_turns_direct_mode_off_when_it_fails(self):
        class Refuses(FakeSynth):
            def handle(self, d):
                if d[0] == 0x78:
                    self.reply(0x01, 0x00, 0x01)
                else:
                    super().handle(d)

        synth = Refuses()
        out, code = run(synth, "check", "60", "--osc")
        self.assertEqual(code, 1)
        self.assertIn("no acknowledgement", out)
        self.assertEqual(synth.sent[0], request_bytes(0x74, 1))
        self.assertEqual(synth.sent[-1], request_bytes(0x74, 0))

    def test_check_turns_direct_mode_off_on_ctrl_c(self):
        synth = FakeSynth()
        with mock.patch.object(pro800, "measure_once", side_effect=KeyboardInterrupt):
            pro800.args = ["60", "--osc"]
            with mock.patch.object(pro800, "port_names", lambda: (["PRO 800"], ["PRO 800"])), \
                    mock.patch.object(pro800, "open_transport", synth.open), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    pro800.check()
                pro800.close_midi()
        self.assertEqual(synth.sent[-1], request_bytes(0x74, 0))

    def test_panel(self):
        self.synth.panel_events = [(0x60, 24, 0x1F, 0x7F), (0x61, 12, 1), (0x62, 2, 0, 5), (0x63, 0, 65), (0x64, 7, 1), (0x64, 7, 0), (0x65, 1, 1)]
        out, code = run(self.synth, "panel", "0.1")
        self.assertEqual(code, 0)
        for text in ("pot CUTOFF (#24)  4095", "switch UNISON = 1", "keys byte 2 = 00000101", "encoder +", "button #7 pressed",
                     "button #7 released", "65 01 01"):
            self.assertIn(text, out)
        self.assertEqual(self.synth.sent[0], request_bytes(0x7A, 1))
        self.assertEqual(self.synth.sent[-1], request_bytes(0x7A, 0))

    def test_panel_goes_off_on_ctrl_c(self):
        with mock.patch.object(pro800.time, "sleep", side_effect=KeyboardInterrupt):
            pro800.args = ["5"]
            with mock.patch.object(pro800, "port_names", lambda: (["PRO 800"], ["PRO 800"])), \
                    mock.patch.object(pro800, "open_transport", self.synth.open), contextlib.redirect_stdout(io.StringIO()):
                pro800.panel()
                pro800.connection = None
        self.assertEqual(self.synth.sent[-1], request_bytes(0x7A, 0))

    def test_listen(self):
        self.synth.start_messages = [bytes([0xB0, 0x01, 0x40]), bytes([0xF8]), bytes([0x90, 0x3C, 0x64]), bytes([0xF8, 0xFE])]
        out, _ = run(self.synth, "listen", "0.15")
        self.assertIn('listening on "PRO 800" for 0 s', out)
        self.assertIn("B0 01 40\n90 3C 64\n", out)
        self.assertIn("2 messages\n", out)
        self.assertEqual(run(FakeSynth(), "listen", "0")[0].splitlines()[-1], "0 messages")  # 0 means 0, not the default

    def test_sysex_split_over_buffers(self):
        self.synth.start_messages = [b"\xF0\x01\x02", b"\x03\xF7", b"\xF0\x04\xF7\xF0\x05\xF7"]
        out, _ = run(self.synth, "listen", "0.15")
        self.assertIn("F0 01 02 03 F7\nF0 04 F7\nF0 05 F7\n", out)

    def test_eeprom_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "image.bin")
            out, code = run(self.synth, "eeprom", path, "0x100", "100")
            self.assertEqual(code, 0)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), bytes(self.synth.eeprom[0x100:0x164]))
        self.assertIn("status register 0x12", out)
        self.assertEqual(self.synth.sent[0], request_bytes(0x79, 0, 2, 0, 48))
        self.assertEqual(self.synth.sent[2], request_bytes(0x79, 0, 2, 96, 4))

    def test_factory_list(self):
        out, _ = run(self.synth, "factory")
        self.assertTrue(out.startswith(" 0  Factory\n 1  (none)\n"))
        self.assertEqual(out.count("\n"), 100)

    def test_sysex_pipe(self):
        with mock.patch.object(sys, "stdin", io.StringIO("08 00; 09\n\n7D 00 00 01; 7f 7d\n")):
            out, _ = run(self.synth, "sysex")
        self.assertEqual(out, "09 00 00 09 03\nnone\n7f 7d\n")


class Stock(unittest.TestCase):
    """The stock firmware: 77 lo hi, a bare F0 F7 for an empty slot, here with another program's F0 F7 arriving late."""

    class Firmware(FakeSynth):
        def __init__(self):
            super().__init__((1, 4, 6))
            self.late = []

        def handle(self, d):
            for message in self.late:
                self.receive(message)
            self.late = []
            if d[0] == 0x08:
                self.reply(0x09, 0x00, 1, 4, 6)
            elif d[:3] == bytes([0x77, 0x7E, 0x03]):
                self.reply(0x78, 0x7E, 0x03, 1, 2, 3)
                self.late.append(pro800.EMPTY_SLOT)  # arrives during the next request
            elif d[0] == 0x77 and d[1] == 0 and d[2] == 0:
                data = bytearray(200)
                data[4] = 0x6F
                data[150:154] = b"Lead"
                packed = []
                for i in range(0, len(data), 7):
                    group = data[i:i + 7]
                    packed.append(sum((b >> 7) << k for k, b in enumerate(group)))
                    packed += [b & 0x7F for b in group]
                self.reply(0x78, 0, 0, *packed)
            elif d[0] == 0x77:
                self.receive(pro800.EMPTY_SLOT)

    def test_backup(self):
        synth = self.Firmware()
        with tempfile.TemporaryDirectory() as folder:
            out, code = run(synth, "backup", folder, "0", "2")
            self.assertEqual(code, 0)
            self.assertEqual(sorted(os.listdir(folder)), ["dump-log.txt", "patch-000-P1.syx", "pro800-all.syx", "system.syx", "version.syx"])
            with open(os.path.join(folder, "dump-log.txt"), encoding="utf-8") as f:
                self.assertEqual(f.read(), out)
        self.assertIn("firmware 1.4.6\n", out)
        self.assertIn("system settings: 15 bytes\n", out)
        self.assertIn('P1: 241 bytes, format 0x6F, name "Lead"\n', out)  # not "empty", for the late F0 F7
        self.assertIn("P2: empty\nP3: empty\n", out)
        self.assertIn("done: 1 patches saved, 2 empty slots, 0 failed []\n", out)

    def test_version_ignores_a_stray_empty_reply(self):
        synth = self.Firmware()
        synth.late.append(pro800.EMPTY_SLOT)
        self.assertEqual(run(synth, "version"), ("firmware 1.4.6\n", 0))


class ShortReplies(unittest.TestCase):
    CASES = [
        (("settings",), b"\x54", 3),  # says 96 settings, holds one
        (("settings",), b"\x54", 1),  # not even the count
        (("params", "1"), b"\x53", 4),
        (("params", "1"), b"\x53", 2),
        (("patches",), b"\x5E", 3),  # no kind
        (("factory", "import", "5", "P128"), b"\x51", 1),
        (("factory",), b"\x50", 2),
        (("format", "--yes"), b"\x5B", 3),
        (("tune",), b"\x76", 1),
        (("sh-waits", "2", "4"), b"\x7F\x6C", 2),
        (("routes",), b"\x54", 10),  # the both-layers masks
        (("route-both", "1", "lfo", "cutoff", "on"), b"\x54", 10),
        (("set", "env-curve", "1", "amp", "linear"), b"\x54", 3),
        (("seq", "set", "on=1"), b"\x48", 6),
        (("seq",), b"\x48", 6),
        (("seq",), b"\x46", 5),
        (("seq", "step", "1", "0", "50", "n2+7"), b"\x48", 3),
        (("state",), b"\x7C", 10),
        (("engine",), b"\x55", 10),
        (("timing",), b"\x58", 4),
        (("fault",), b"\x49", 20),
        (("fault-test", "--yes"), b"\x49", 20),
    ]

    def test_each_parser_says_so(self):
        for argv, prefix, kept in self.CASES:
            with self.subTest(argv=argv, kept=kept):
                synth = FakeSynth()
                synth.routes[0][9] = (1, 3, 50)
                synth.cut = (prefix, kept)
                out, code = run(synth, *argv)  # an IndexError here would be the bug
                self.assertEqual(code, 1, out)
                self.assertRegex(out, r"short reply \([0-9]+ bytes\)\n")

    def test_nothing_is_sent_on_the_strength_of_a_short_reply(self):
        for argv in (("set", "env-curve", "1", "amp", "linear"), ("seq", "set", "on=1")):
            with self.subTest(argv=argv):
                synth = FakeSynth()
                synth.cut = (b"\x54" if argv[0] == "set" else b"\x48", 3)
                run(synth, *argv)
                self.assertEqual(len(synth.sent), 1, synth.sent)

    def test_a_slot_kind_this_tool_does_not_know(self):
        synth = FakeSynth()
        synth.slots = {3: (9, b"")}
        out, code = run(synth, "patches")
        self.assertEqual(code, 0, out)
        self.assertIn("P4  corrupt", out)
        self.assertIn("127 empty, 1 corrupt", out)

    def test_seq_set_on_a_firmware_without_the_arp_byte(self):
        synth = FakeSynth()
        del synth.sequence[9]
        out, code = run(synth, "seq", "set", "swing=5")
        self.assertEqual(code, 0, out)
        self.assertEqual(synth.sent[-1], request_bytes(0x47, 8, 0, 12, 0, 120, 0, 5, 0, 0))  # the F7 isn't taken for the arp
        synth.sent.clear()
        self.assertNotIn("arp", run(synth, "seq")[0])

    def test_seq_set_keeps_the_arp(self):
        synth = FakeSynth()
        synth.sequence[9] = 3
        run(synth, "seq", "set", "swing=5")
        self.assertEqual(synth.sent[-1], request_bytes(0x47, 8, 0, 12, 0, 120, 0, 5, 0, 3))

    def test_a_stray_index_error_is_one_line(self):
        def boom():
            return b""[3]

        pro800.args = ["boom"]
        err = io.BytesIO()
        with mock.patch.dict(pro800.COMMANDS, boom=boom), mock.patch.object(sys, "stdout", io.TextIOWrapper(io.BytesIO())), \
                mock.patch.object(sys, "stderr", io.TextIOWrapper(err)), mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO())), \
                mock.patch.object(pro800.atexit, "register"):
            with self.assertRaises(SystemExit) as caught:
                pro800.main()
            sys.stderr.flush()
            self.assertEqual(caught.exception.code, 1)
            self.assertEqual(err.getvalue().decode().count("\n"), 1)
            self.assertIn("shorter than this command expects", err.getvalue().decode())


class StockError(unittest.TestCase):
    """The stock firmware answers what it doesn't know with 01 00 01, and request() hands that over."""

    class Firmware(FakeSynth):
        def handle(self, d):
            self.reply(0x01, 0x00, 0x01)

    def test_is_not_taken_for_success(self):
        for argv in (("patch", "load", "P1"), ("patch", "save", "P1"), ("patch", "clear", "P1"), ("patch", "init"), ("patch", "undo"), ("patch", "perf"),
                     ("direct", "on"), ("settings",), ("params", "1"), ("patches",), ("tune",), ("seq",), ("seq", "set", "on=1"),
                     ("routes",), ("factory",), ("factory", "import", "1", "P1"), ("format", "--yes"), ("state",), ("engine",),
                     ("timing",), ("fault",), ("set", "env-curve", "1", "amp", "linear"), ("tuning",)):
            with self.subTest(argv=argv):
                out, code = run(self.Firmware(), *argv)
                self.assertEqual(code, 1, out)
                self.assertIn("the stock firmware does not do this", out)

    def test_eeprom_write_stops_at_the_first_block(self):
        synth = self.Firmware()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "image.bin")
            with open(path, "wb") as f:
                f.write(bytes(0x20000))
            out, code = run(synth, "eeprom-write", path, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("the stock firmware does not do this", out)
        self.assertNotIn("half old", out)  # it wrote nothing
        self.assertEqual(len(synth.sent), 1)


class Fault(unittest.TestCase):
    def test_none_recorded(self):
        synth = FakeSynth()
        self.assertEqual(run(synth, "fault"), ("no fault recorded\n", 0))
        self.assertEqual(synth.sent, [request_bytes(0x49)])

    def test_a_record(self):
        synth = FakeSynth()
        synth.crash = dict(CRASH)
        self.assertEqual(run(synth, "fault"), (CRASH_TEXT, 0))

    def test_names(self):
        synth = FakeSynth()
        synth.crash = dict(CRASH, exception=16 + 67, pc=0, cfsr=1 << 7 | 1 << 1 | 1 << 25, hfsr=2, streak=3)
        out, _ = run(synth, "fault")
        self.assertIn("last crash: interrupt 67 (exception 83)\n", out)
        self.assertIn("  pc         0x00000000 (no stack frame to read)\n", out)
        self.assertIn("  CFSR       0x02000082 DACCVIOL MMARVALID DIVBYZERO\n", out)
        self.assertIn("  HFSR       0x00000002 VECTTBL\n", out)
        self.assertIn("  MMFAR      0xFEDCBA98\n", out)
        self.assertIn("  BFAR       0x30000000 (not valid)\n", out)
        self.assertIn("  streak     3\n", out)

    def test_an_old_firmware_says_nothing(self):
        synth = FakeSynth()
        synth.silent = True
        out, code = run(synth, "fault")
        self.assertEqual(code, 1)
        self.assertIn("firmware 0.9.4 or later?", out)

    def fault_test(self, synth, answers_after=2, argv=("--yes",), slow=False):
        calls = []

        def child(*command, capture=True, timeout=None):
            calls.append(command)
            if len(calls) <= answers_after:  # still restarting
                return mock.Mock(returncode=1, stdout="") if len(calls) % 2 else None
            if slow:  # over 10 s of running before the port answered: src/fault.h's fault_clear_streak()
                synth.crash["streak"] = 0
            return mock.Mock(returncode=0, stdout=pro800.fault_text(synth.fault_body()) + "\n")

        real_sleep = time.sleep
        with mock.patch.object(pro800, "run_self", child), mock.patch.object(pro800.time, "sleep", lambda s: real_sleep(min(s, 0.001))):
            out, code = run(synth, "fault-test", *argv)
        return out, code, calls

    def test_crashes_and_reads_the_record(self):
        synth = FakeSynth()
        out, code, calls = self.fault_test(synth)
        self.assertEqual(code, 0, out)
        self.assertEqual(synth.sent, [request_bytes(0x49), request_bytes(0x4A, 0x43, 0x52)])
        self.assertEqual(calls, [("fault",)] * 3)
        self.assertIn("crashing the synth on purpose", out)
        self.assertTrue(out.endswith(CRASH_TEXT), out)

    def test_needs_yes(self):
        synth = FakeSynth()
        out, code, calls = self.fault_test(synth, argv=())
        self.assertEqual(code, 1)
        self.assertIn("usage: pro800 fault-test --yes", out)
        self.assertEqual((synth.sent, calls), ([], []))

    def test_fails_without_a_record(self):
        synth = FakeSynth()
        synth.crashes = False
        out, code, _ = self.fault_test(synth)
        self.assertEqual(code, 1)
        self.assertIn("holds no record of this crash", out)

    def test_an_older_crash_is_not_this_one(self):
        synth = FakeSynth()
        synth.crashes = False
        synth.crash = dict(CRASH, streak=0)
        out, code, _ = self.fault_test(synth)
        self.assertEqual(code, 1)
        self.assertIn("the same record as before the crash", out)
        self.assertIn("Switch it off and on", out)

    def test_a_port_slower_than_the_streak_is_still_this_crash(self):
        synth = FakeSynth()
        out, code, _ = self.fault_test(synth, slow=True)
        self.assertEqual(code, 0, out)
        self.assertIn("  BFAR       0x30000000\n", out)
        self.assertIn("  streak     0\n", out)
        self.assertNotIn("no record", out)

    def test_a_slow_port_after_a_different_crash_is_still_this_crash(self):
        synth = FakeSynth()
        synth.crash = dict(CRASH, pc=0x08054321, streak=0)
        out, code, _ = self.fault_test(synth, slow=True)
        self.assertEqual(code, 0, out)
        self.assertIn("  pc         0x08012345\n", out)

    def test_a_slow_port_after_the_same_crash_cant_be_told_apart(self):
        synth = FakeSynth()
        synth.crash = dict(CRASH, streak=0)
        out, code, _ = self.fault_test(synth, slow=True)
        self.assertEqual(code, 1)
        self.assertIn("the same record as before the crash", out)

    def test_the_streak_alone_tells_this_crash_from_the_last(self):
        synth = FakeSynth()
        synth.crash = dict(CRASH, streak=0)
        out, code, _ = self.fault_test(synth)  # 0 before it, 1 now: all else the same
        self.assertEqual(code, 0, out)
        self.assertIn("  streak     1\n", out)

    def test_fails_when_it_never_comes_back(self):
        synth = FakeSynth()
        out, code, calls = self.fault_test(synth, answers_after=1000)
        self.assertEqual(code, 1)
        self.assertIn("didn't answer within 60 s", out)
        self.assertEqual(len(calls), 60)

    def test_will_not_be_the_crash_that_stops_the_synth(self):
        synth = FakeSynth()
        synth.crash = dict(CRASH, streak=2)
        out, code, calls = self.fault_test(synth)
        self.assertEqual(code, 1)
        self.assertIn("Wait 10 s", out)
        self.assertEqual((synth.sent, calls), ([request_bytes(0x49)], []))

    def test_a_second_crash_within_ten_seconds_is_fine(self):
        synth = FakeSynth()
        synth.crash = dict(CRASH, streak=1)
        out, code, _ = self.fault_test(synth)
        self.assertEqual(code, 0, out)
        self.assertIn("  streak     2\n", out)


class Format(unittest.TestCase):
    def test_new_reply(self):
        synth = FakeSynth()
        self.assertEqual(run(synth, "format", "--yes"), ("formatting...\n128 empty slots\n", 0))

    def test_old_reply(self):  # before 0.9.4: 00 80
        synth = FakeSynth()
        synth.format_count = (0, 0x80)
        self.assertEqual(run(synth, "format", "--yes"), ("formatting...\n128 empty slots\n", 0))

    def test_failure(self):
        synth = FakeSynth()
        synth.format_failed = 1
        out, code = run(synth, "format", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("format failed", out)


class Writes(unittest.TestCase):
    def test_requests_byte_for_byte(self):
        cases = [
            (("set", "lfo-sync", "clock"), [0x7D, 0, 0, 1]),
            (("set", "lfo-trigger", "one-shot"), [0x7D, 1, 0, 4]),
            (("set", "layers", "mpe"), [0x7D, 2, 0, 5]),
            (("set", "split", "60"), [0x7D, 3, 0, 60]),
            (("set", "keytrack", "full"), [0x7D, 4, 0, 2]),
            (("set", "lfo2-sync", "clock"), [0x7D, 5, 0, 1]),
            (("set", "lfo2-trigger", "first"), [0x7D, 6, 0, 1]),
            (("set", "lfo", "2"), [0x7D, 8, 0, 1]),
            (("set", "shape", "2", "2", "sine"), [0x7D, 12, 0, 4]),
            (("set", "lfo2-dests", "2", "pitch,pw"), [0x7D, 14, 0, 5]),
            (("set", "cycle", "1", "amp,env4"), [0x7D, 15, 0, 9]),
            (("set", "cycle-sync", "2", "env4", "1/32"), [0x7D, 24, 0, 15]),
            (("set", "bend", "12"), [0x7D, 25, 0, 12]),
            (("set", "detune", "100"), [0x7D, 26, 0, 100]),
            (("set", "unison-voices", "3"), [0x7D, 91, 0, 3]),
            (("set", "arp-range", "3"), [0x7D, 92, 0, 2]),
            (("set", "arp-extend", "ninth"), [0x7D, 93, 0, 4]),
            (("set", "arp-chaos", "40"), [0x7D, 94, 0, 40]),
            (("set", "init-patch", "P120"), [0x7D, 95, 0, 120]),
            (("set", "init-patch", "0"), [0x7D, 95, 0, 1]),
            (("set", "init-patch", "none"), [0x7D, 95, 0, 0]),
            (("set", "voice", "2", "legato", "high"), [0x7D, 28, 0, 10]),
            (("set", "drift", "99"), [0x7D, 29, 0, 99]),
            (("set", "scale", "chromatic"), [0x7D, 30, 0, 12]),
            (("set", "root", "a#"), [0x7D, 31, 0, 10]),
            (("set", "lfo-fade", "2", "10"), [0x7D, 35, 0, 10]),
            (("set", "cc-b", "127"), [0x7D, 37, 0, 127]),
            (("set", "macro", "2", "2", "-99"), [0x7D, 41, 0, 0]),
            (("set", "macro", "1", "1", "99"), [0x7D, 38, 1, 70]),
            (("set", "clock", "sync-in"), [0x7D, 42, 0, 2]),
            (("set", "bpm", "300"), [0x7D, 43, 2, 44]),
            (("set", "env-up", "2", "filter"), [0x7D, 45, 0, 2]),
            (("set", "dahdsr", "1", "none"), [0x7D, 46, 0, 0]),
            (("set", "env-delay", "2", "env3", "7"), [0x7D, 56, 0, 7]),
            (("set", "env-hold", "1", "amp", "9"), [0x7D, 58, 0, 9]),
            (("set", "layer-voices", "auto"), [0x7D, 66, 0, 0]),
            (("set", "env-count", "2", "env4", "loop"), [0x7D, 74, 0, 0]),
            (("set", "env-tempo", "2", "amp"), [0x7D, 76, 0, 1]),
            (("set", "midi-channel", "16"), [0x7D, 77, 0, 16]),
            (("set", "midi-channel", "all"), [0x7D, 77, 0, 0]),
            (("set", "osc-tune", "2", "b", "fixed"), [0x7D, 81, 0, 3]),
            (("set", "pickup", "off"), [0x7D, 82, 0, 0]),
            (("set", "link", "amp-env,filter"), [0x7D, 83, 0, 65]),
            (("set", "lfo2-cycles", "99"), [0x7D, 85, 0, 99]),
            (("set", "lfo-restart", "keys"), [0x7D, 86, 0, 1]),
            (("set", "morph", "P128"), [0x44, 1, 0]),
            (("set", "morph", "none"), [0x44, 0, 0]),
            (("set", "steps", "1", "2", "x.x"), [0x4E, 0, 1, 3, 0, 0, 5]),
            (("set", "button", "sync", "on"), [0x4B, 15, 1]),
            (("param", "2", "cutoff", "99"), [0x52, 1, 24, 0x7F, 0x7F]),
            (("param", "1", "amp-rel", "0"), [0x52, 0, 0, 0, 0]),
            (("layer", "2"), [0x7E, 1]),
            (("route", "1", "lfo", "cutoff", "-99"), [0x56, 0, 1, 3, 0, 0]),
            (("route-move", "2", "lfo", "pitch-a", "env3", "pitch-b", "49"), [0x61, 1, 1, 0, 7, 1, 1, 20]),
            (("route-via", "1", "lfo", "cutoff", "0.25"), [0x62, 0, 1, 3, 0, 25]),
            (("route-via", "2", "lfo", "cutoff", "8"), [0x62, 1, 1, 3, 800 >> 7, 800 & 0x7F]),
            (("route-via", "1", "lfo", "cutoff", "mod-wheel"), [0x62, 0, 1, 3, 1005 >> 7, 1005 & 0x7F]),
            (("route", "2", "macro2", "macro2", "99"), [0x56, 1, 27, 45, 1, 70]),
            (("route", "1", "lfo", "cutoff", "53.5"), [0x66, 0, 1, 3, 1525 >> 7, 1525 & 0x7F]),
            (("route", "1", "lfo", "cutoff", "-0.1"), [0x66, 0, 1, 3, 989 >> 7, 989 & 0x7F]),
            (("route", "1", "lfo", "cutoff", "53.0"), [0x56, 0, 1, 3, 1, 152 & 0x7F]),
            (("route", "2", "velocity", "cutoff", "0"), [0x56, 1, 4, 3, 0, 99]),  # plan.py's lines: takes the init sound's route off
            (("patch", "load", "P7"), [0x5C, 0, 6]),
            (("patch", "save", "P128"), [0x5D, 0, 127]),
            (("patch", "clear", "127"), [0x4F, 0, 127]),
            (("patch", "init"), [0x5F]),
            (("patch", "undo"), [0x60]),
            (("patch", "perf"), [0x5F, 1]),
            (("chord", "1", "0,4,7"), [0x69, 0, 4, 7, 0, 0, 0, 0, 0, 0]),
            (("chord", "2", "3,7,10,14"), [0x69, 1, 3, 7, 10, 14, 0, 0, 0, 0]),
            (("chord", "1", "off"), [0x69, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
            (("chord", "capture", "--yes"), [0x6A, 3]),
            (("chord", "capture", "2", "--yes"), [0x6A, 2]),
            (("patch", "rename", "P2", "Foo"), [0x59, 0, 1, 0x46, 0x6F, 0x6F]),
            (("factory", "import", "5", "P128", "--force"), [0x51, 5, 0, 127, 1]),
            (("format", "--yes"), [0x5B]),
            (("direct", "on"), [0x74, 1]),
            (("cv", "osca1", "0xFFFF"), [0x70, 0, 3, 0x7F, 0x7F]),
            (("cv", "res", "1"), [0x70, 40, 0, 0, 1]),
            (("gate", "sync", "1"), [0x71, 2, 1]),
            (("tune",), [0x75]),
            (("seq", "step", "1", "+5", "tie", "v100", "p4", "x2", "a99"), [0x45, 0, 1, 69, 100, 100, 1, 12, 127, 64, 64, 64]),
            (("seq", "step", "2", ".", "20"), [0x45, 1, 1, 64, 0, 50, 0, 0, 64, 64, 64, 64]),
            (("seq", "step", "3", "-2", "30", "h5", "b-99"), [0x45, 2, 1, 62, 0, 30, 1 | 5 << 1, 0, 64, 1, 64, 64]),
            (("sh-waits", "2", "4"), [0x6C, 0, 0, 1, 40, 1, 40, 0, 0, 20, 40]),
            (("update-mode", "--yes"), [0x03, 0x30]),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                synth = FakeSynth()
                out, code = run(synth, *argv)
                self.assertEqual(code, 0, out)
                self.assertEqual(synth.sent, [request_bytes(*expected)], out)

    def test_nrpn(self):
        ids = {p[1]: p[0] for p in pro800.PARAMS}
        synth = FakeSynth()
        run(synth, "nrpn", "cutoff", "8256", "--layer", "2", "--channel", "1")
        self.assertEqual(synth.sent, [bytes([0xB0, 99, 2]), bytes([0xB0, 98, 24]), bytes([0xB0, 6, 64]), bytes([0xB0, 38, 64])])
        synth = FakeSynth()
        run(synth, "nrpn", "layers", "stack", "--channel", "1")
        self.assertEqual(synth.sent, [bytes([0xB0, 99, 0]), bytes([0xB0, 98, ids["layers"]]), bytes([0xB0, 6, 2])])
        synth = FakeSynth()
        run(synth, "nrpn", "macro 1", "-99", "--layer", "both", "--channel", "1")
        self.assertEqual(synth.sent, [bytes([0xB0, 99, 3]), bytes([0xB0, 98, ids["macro 1"]]), bytes([0xB0, 6, 0]), bytes([0xB0, 38, 1])])
        synth = FakeSynth()
        run(synth, "nrpn", "macro 1", "2", "--channel", "1")
        self.assertEqual(synth.sent[2:], [bytes([0xB0, 6, 65]), bytes([0xB0, 38, 37])], "round(2 x 8191 / 99) + 8192")
        for argv in (("nrpn", "cutoff", "16384"), ("nrpn", "nothing", "1"), ("nrpn", "layers", "6"), ("nrpn", "drift", "5", "--layer", "3"),
                     ("nrpn", "cutoff", "1", "--layer")):
            with self.subTest(argv=argv):
                synth = FakeSynth()
                out, code = run(synth, *argv)
                self.assertNotEqual(code, 0, out)
                self.assertEqual(synth.sent, [])
        out, code = run(FakeSynth(), "nrpn", "list")
        self.assertIn(" 52  layers", out)

    def test_cc_map(self):
        ids = {p[1]: p[0] for p in pro800.PARAMS}
        synth = FakeSynth()
        out, code = run(synth, "cc-map")
        self.assertEqual(code, 0, out)
        self.assertIn("CC  74  cutoff", out)
        for argv, sent in ((("cc-map", "set", "20", "shape 2", "--layer", "1"), [0x43, 20, 84, 1]),
                           (("cc-map", "set", "87", "cutoff", "--fine"), [0x43, 87, 24, 4]),
                           (("cc-map", "set", "74", "none"), [0x43, 74, 127, 0]),
                           (("cc-map", "reset", "--yes"), [0x42])):
            with self.subTest(argv=argv):
                synth = FakeSynth()
                out, code = run(synth, *argv)
                self.assertEqual((code, synth.sent), (0, [request_bytes(*sent)]), out)
        for argv in (("cc-map", "set", "1", "cutoff"), ("cc-map", "set", "20", "nope"), ("cc-map", "set", "20", "layers", "--fine"),
                     ("cc-map", "set", "20", "cutoff", "--layer"), ("cc-map", "reset"), ("cc-map", "nonsense")):
            with self.subTest(argv=argv):
                synth = FakeSynth()
                out, code = run(synth, *argv)
                self.assertNotEqual(code, 0, out)
                self.assertEqual(synth.sent, [])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "map.json")
            synth = FakeSynth()
            synth.cc_ids[20], synth.cc_nibbles[20] = 84, 1
            run(synth, "cc-map", "export", path)
            with open(path, encoding="utf-8") as f:
                j = json.load(f)
            self.assertEqual([e["cc"] for e in j["map"]], [20, 74])
            synth = FakeSynth()
            out, code = run(synth, "cc-map", "import", path, "--yes")
            self.assertEqual(code, 0, out)
            self.assertEqual((synth.cc_ids[20], synth.cc_nibbles[20], synth.cc_ids[74]), (84, 1, 24))
            synth = FakeSynth()
            out, code = run(synth, "cc-map", "import", os.path.join(HERE, "web", "stock-cc-map.json"), "--yes")
            self.assertEqual(code, 0, out)
            self.assertEqual((synth.cc_ids[8], synth.cc_ids[74], synth.cc_ids[71]), (10, ids["button lfo-freq"], 127),
                             "the stock map replaces ours whole: CC 74 is LFO 1 to pitch there, 71 nothing")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"format": "pro8000-cc-map", "version": 1, "map": [{"cc": 1, "param": "cutoff"}]}, f)
            synth = FakeSynth()
            out, code = run(synth, "cc-map", "import", path, "--yes")
            self.assertNotEqual(code, 0)
            self.assertIn("keeps its own meaning", out)
            self.assertFalse([m for m in synth.sent if m[pro800.HEADER_SIZE] == 0x43], "nothing changed")
            for entry, ok in (({"cc": 74.0, "param": "cutoff"}, True), ({"cc": 74, "param": ["cutoff"]}, False),
                              ({"cc": 74, "param": "cutoff", "fine": 1}, False)):
                with self.subTest(entry=entry):
                    with open(path, "w", encoding="utf-8-sig") as f:  # a BOM, as some editors write
                        json.dump({"format": "pro8000-cc-map", "version": 1, "map": [entry]}, f)
                    synth = FakeSynth()
                    out, code = run(synth, "cc-map", "import", path, "--yes")
                    self.assertEqual(code == 0, ok, out)
                    self.assertNotIn("Traceback", out)
        out, _ = run(FakeSynth(), "nrpn", "list", "--csv")
        self.assertTrue(out.startswith("id,name,kind,scope,min,max,values"))
        self.assertIn("52,layers,raw,patch,0,5,off split stack channel 8ch mpe", out)

    def test_the_parameter_list(self):
        """tools/params.json: the generated files are fresh, and its names are the tools' and the presets' names."""
        gen = subprocess.run([sys.executable, os.path.join(HERE, "gen_params.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(gen.returncode, 0, gen.stdout + gen.stderr)
        by_id = {p[0]: p for p in pro800.PARAMS}
        for i, name in enumerate(pro800.PARAM_NAMES):
            if name != "master-vol":
                self.assertEqual((by_id[i][1], by_id[i][2]), (name, "knob"))
        for s, name in pro800.BUTTON_NAMES.items():
            self.assertEqual((by_id[36 + s][1], by_id[36 + s][2]), ("button " + name, "bool"))
        swift = open(os.path.join(HERE, "pro800.swift"), encoding="utf-8").read()
        self.assertEqual(len(re.findall(r'^    \(\d+, "', swift[swift.index("BEGIN PARAMS"):swift.index("END PARAMS")], re.M)), len(pro800.PARAMS))

    def test_chord_prints_and_refuses(self):
        synth = FakeSynth()
        synth.chords[0] = [4, 7, 0, 0, 0, 0, 0, 0]
        synth.buttons[12] = 1
        out, code = run(synth, "chord")
        self.assertEqual(code, 0, out)
        self.assertIn("layer 1: 0 4 7 (from C4: C4 E4 G4)", out)
        self.assertIn("layer 2: no chord", out)
        self.assertIn("Unison is on", out)
        for argv in (("chord", "capture"), ("chord", "1", "0,7,4"), ("chord", "1", "1,2,3,4,5,6,7,8"), ("chord", "3", "0,4,7"),
                     ("chord", "1", "0,128"), ("chord", "1", "x")):
            with self.subTest(argv=argv):
                synth = FakeSynth()
                out, code = run(synth, *argv)
                self.assertNotEqual(code, 0, out)
                self.assertEqual(synth.sent, [], "nothing sent")
        synth = FakeSynth()
        out, _ = run(synth, "chord", "capture", "--yes")
        self.assertIn("layer 1: 3 notes", out)
        self.assertIn("layer 2: fewer than 2 keys held: its chord is cleared", out)
        synth = FakeSynth()
        synth.captured = (2, 127)
        out, _ = run(synth, "chord", "capture", "--yes")
        self.assertIn("layer 2: not captured", out)

    def test_read_first_settings(self):
        synth = FakeSynth()
        synth.settings[49] = 1  # layer 2: amp linear
        run(synth, "set", "env-curve", "2", "filter", "swell")
        self.assertEqual(synth.sent, [request_bytes(0x54), request_bytes(0x7D, 49, 0, 9)])

        synth = FakeSynth()
        run(synth, "seq", "set", "on=1", "length=16", "step=1/16", "bpm=200", "chord=1", "lanes=1", "smooth=AC", "arp=played")
        self.assertEqual(synth.sent[-1], request_bytes(0x47, 16, 2, 12, 1, 72, 0x21, 0, 5, 5))

        synth = FakeSynth()
        synth.sequence[1], synth.sequence[5] = 2, 1 << 5
        run(synth, "seq", "step", "1", "0", "50", "n2+7")
        self.assertEqual(synth.sent[-1], request_bytes(0x45, 0, 1, 64, 0, 50, 1, 0, 64, 71, 64, 64))

        synth = FakeSynth()
        synth.routes[0][9] = (1, 3, 50)
        run(synth, "route-both", "1", "lfo", "cutoff", "on")
        self.assertEqual(synth.sent[-1], request_bytes(0x7D, 88, 0, 2))

    def test_sequence_chord_needs_its_lane(self):
        out, code = run(FakeSynth(), "seq", "step", "1", "0", "50", "n2+7")
        self.assertEqual(code, 1)
        self.assertIn("seq set chord=1 first", out)

    def test_short_messages(self):
        synth = FakeSynth()
        run(synth, "cc", "1", "64", "--channel", "3")
        self.assertEqual(synth.sent, [bytes([0xB2, 1, 64])])
        synth = FakeSynth()
        run(synth, "bend", "16383", "--channel", "1")
        self.assertEqual(synth.sent, [bytes([0xE0, 0x7F, 0x7F])])
        synth = FakeSynth()
        run(synth, "play", "60", "--channel", "2", "--hold", "0", "--velocity", "99")
        self.assertEqual(synth.sent, [bytes([0x91, 60, 99]), bytes([0x81, 60, 64])], "note-off at 64: MIDI's default release")
        synth = FakeSynth()
        run(synth, "play", "60", "--channel", "2", "--hold", "0", "--release", "90")
        self.assertEqual(synth.sent, [bytes([0x91, 60, 100]), bytes([0x81, 60, 90])])
        synth = FakeSynth()
        run(synth, "play", "60", "--channel", "all", "--hold", "0")
        self.assertEqual(len(synth.sent), 32)

    def test_default_channels(self):
        synth = FakeSynth()
        synth.settings[77] = 5
        run(synth, "play", "60", "--hold", "0")
        self.assertEqual(synth.sent[1:], [bytes([0x94, 60, 100]), bytes([0x84, 60, 64])])
        synth = FakeSynth()
        synth.silent = True  # stock: no 54, so the stock firmware's channel 6
        run(synth, "play", "60", "--hold", "0")
        self.assertEqual(synth.sent[1:], [bytes([0x95, 60, 100]), bytes([0x85, 60, 64])])
        synth = FakeSynth()
        run(synth, "cc", "1", "2")  # every channel: channel 1
        self.assertEqual(synth.sent[-1], bytes([0xB0, 1, 2]))

    def test_clock(self):
        synth = FakeSynth()
        run(synth, "clock", "120", "0.1")
        self.assertEqual(synth.sent, [b"\xFA"] + [b"\xF8"] * 4 + [b"\xFC"])

    def test_no_audio(self):
        synth = FakeSynth()
        out, code = run(synth, "play", "60", "--record")
        self.assertEqual(code, 1)
        self.assertIn("macOS tool", out)
        self.assertEqual(synth.sent, [])

    def test_bad_arguments_send_nothing(self):
        for argv in (("set", "nonsense", "1"), ("set", "bend", "13"), ("set", "macro", "3", "1", "0"), ("set", "voice", "1", "poly"),
                     ("param", "1", "nope", "1"), ("param", "3", "cutoff", "1"), ("route", "1", "", "cutoff", "1"),
                     ("route", "1", "lfo", "cutoff", "100"), ("patch", "load", "P129"), ("patch", "save", "128"),
                     ("seq", "step", "65", "0", "50"), ("cc", "128", "0", "--channel", "1"), ("bend", "16384", "--channel", "1"),
                     ("format",), ("eeprom-write", "x"), ("direct", "maybe"), ("cv", "nope", "1"), ("gate", "sawa"),
                     ("cv", "osca1", "0xF_F"), ("param", "1", "cutoff", "1_0"), ("param", "1", "cutoff", "nan"),
                     ("play", "60", "--hold", "-1", "--channel", "1"), ("check", "200"),
                     ("cc", "1", "2", "--channel", "0"), ("cc", "1", "2", "--channel", "17"), ("bend", "0", "--channel", "-1"),
                     ("update-mode",), ("fault-test",)):
            with self.subTest(argv=argv):
                synth = FakeSynth()
                out, code = run(synth, *argv)
                self.assertEqual(code, 1, out)
                self.assertEqual(synth.sent, [], out)

    def test_update_mode_says_how_to_get_back(self):
        out, code = run(FakeSynth(), "update-mode")
        self.assertEqual(code, 1)
        self.assertIn("--yes", out)
        self.assertIn("erases flash sector 4", out)
        self.assertIn("pro800 flash <file.syx> --yes", out)
        out, code = run(FakeSynth(), "update-mode", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("stays there until a firmware is", out)
        self.assertIn("pro800 flash <file.syx> --yes", out)

    def test_rename_warns_about_what_it_dropped(self):
        cases = [(("Foo",), "Foo", None), (("Two", "Words"), "Two Words", None), (("A" * 20,), "A" * 16, 4), (("Füü",), "F", 2),
                 (("A" * 16,), "A" * 16, None)]
        for words, name, dropped in cases:
            with self.subTest(words=words):
                synth = FakeSynth()
                out, code = run(synth, "patch", "rename", "P2", *words)
                self.assertEqual(code, 0, out)
                self.assertEqual(synth.sent, [request_bytes(0x59, 0, 1, *name.encode())])
                if dropped is None:
                    self.assertNotIn("dropped", out)
                else:
                    self.assertIn(f"note: {dropped} characters dropped", out)

    def test_eeprom_write_verifies(self):
        synth = FakeSynth()
        image = bytes((i * 13) & 0xFF for i in range(0x20000))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "image.bin")
            with open(path, "wb") as f:
                f.write(image)
            out, code = run(synth, "eeprom-write", path, "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(bytes(synth.eeprom), image)
        self.assertIn("verified: the EEPROM matches", out)
        self.assertIn("The image carries the tuning tables: if it came from another synth, run a Tune", out)
        self.assertNotIn("half old", out)

    def test_eeprom_write_says_what_a_failed_block_leaves(self):
        class Dies(FakeSynth):
            def handle(self, d):
                if not (d[0] == 0x5A and d[1:4] == bytes([0, 0, 96])):  # the third block
                    super().handle(d)

        synth = Dies()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "image.bin")
            with open(path, "wb") as f:
                f.write(bytes(0x20000))
            out, code = run(synth, "eeprom-write", path, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("write failed at 0x60", out)
        self.assertIn("half old, half new", out)
        self.assertIn("eeprom-write again", out)
        self.assertIn("pro800 format --yes", out)
        self.assertEqual(pro800.fail_note, "")

    def test_seq_set_wants_a_value(self):
        synth = FakeSynth()
        self.assertEqual(run(synth, "seq", "set", "smooth=")[1], 1)
        self.assertEqual(synth.sent, [request_bytes(0x48)])  # only the header's read

    def test_eeprom_write_fails_on_a_mismatch(self):
        class Forgetful(FakeSynth):
            def handle(self, d):
                if d[0] == 0x5A:
                    self.ack(0x5A)
                else:
                    super().handle(d)

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "image.bin")
            with open(path, "wb") as f:
                f.write(bytes(0x20000))
            out, code = run(Forgetful(), "eeprom-write", path, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", out)
        self.assertIn("half old, half new", out)

    def test_eeprom_write_wants_a_whole_image(self):
        synth = FakeSynth()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "short.bin")
            with open(path, "wb") as f:
                f.write(bytes(100))
            out, code = run(synth, "eeprom-write", path, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("isn't a 128 kB image", out)
        self.assertEqual(synth.sent, [])


def update_messages(version=(0, 9, 3), blocks=8, block_size=200, declared=None):
    """As PRO800_update_gen lays one out: the firmware's size in the header, a count in each block."""
    size = (blocks if declared is None else declared) * pro800.UPDATE_BLOCK_BYTES
    header = (HEADER + b"\x0A\x00" + bytes([size & 0x7F, size >> 7 & 0x7F, size >> 14 & 0x7F]) + bytes(11) + b"BEHRINGER PRO 800"
              + b"\x00" + bytes(version) + bytes(10) + b"\xF7")
    assert header[24:41] == b"BEHRINGER PRO 800" and header[42:45] == bytes(version)
    return header, [HEADER + b"\x0B\x00" + bytes([i & 0x7F, i >> 7, 0, 0]) + bytes(block_size) + b"\xF7" for i in range(blocks)]


def write_update(folder, messages):
    path = os.path.join(folder, "update.syx")
    with open(path, "wb") as f:
        f.write(b"".join(messages))
    return path


def update_file(folder, version=(0, 9, 3), blocks=8, block_size=200):
    header, data = update_messages(version, blocks, block_size)
    return write_update(folder, [header] + data), header, data


class Bootloader:
    """Records when each message came and answers as the bootloader does."""

    def __init__(self, ok=True, accepts=True, blocks=8, status_at=None, dies_at=None):
        self.times = []
        self.sent = []
        self.ok = ok  # what it says after the last block: True 01 00 00, False 01 00 07, None nothing
        self.accepts = accepts
        self.blocks = blocks
        self.status_at = status_at or {}  # after that many messages, 01 00 xx
        self.dies_at = dies_at  # that message is what the port refuses
        self.receive = None

    def open(self, source, destination, receive):
        self.receive = receive
        return self

    def close(self):
        pass

    def send(self, data):
        if len(self.sent) + 1 == self.dies_at:
            pro800.fail("midiOutLongMsg failed: Undefined external error. (1)")
        self.times.append(time.perf_counter())
        self.sent.append(bytes(data))
        if len(self.sent) in self.status_at:
            self.receive(HEADER + bytes([0x01, 0x00, self.status_at[len(self.sent)]]) + b"\xF7")
        if data[8] == 0x0A and self.accepts:
            self.receive(HEADER + b"\x03\x31\xF7")
        elif len(self.sent) == 1 + self.blocks and self.ok is not None:
            self.receive(HEADER + (b"\x01\x00\x00" if self.ok else b"\x01\x00\x07") + b"\xF7")


class Flash(unittest.TestCase):
    def setUp(self):  # most files here are small, for speed; UpdateFiles tests the size
        for name, value in (("MIN_UPDATE_BYTES", 0), ("FLASH_ACCEPT_WAIT", 0.1), ("FLASH_CONFIRM_WAIT", 0.1)):
            patcher = mock.patch.object(pro800, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def flash(self, synth, *options, **file_options):
        with tempfile.TemporaryDirectory() as folder:
            path = update_file(folder, **file_options)[0]
            return run(synth, "flash", path, "--yes", "--erase-wait", "0", "--rate", "9000", *options)

    def recovers(self, out):
        for text in ("stays in its bootloader", "takes a fresh header without a power-cycle", "close other MIDI software",
                     "run flash again", "hold buttons 0 and 7", "A stock firmware .syx also brings it back"):
            self.assertIn(text, out)
        self.assertEqual(pro800.fail_note, "")

    def may_be_running(self, out):
        """No 03 31 yet: a running synth ignores the header, so nobody can say it's stuck in its bootloader."""
        self.assertNotIn("The synth stays in its bootloader", out)
        for text in ("If the synth still plays, it wasn't in its bootloader and nothing changed", "it stays there until it gets a firmware",
                     "takes a fresh header without a power-cycle", "hold buttons 0 and 7", "A stock firmware .syx also brings it back"):
            self.assertIn(text, out)
        self.assertEqual(pro800.fail_note, "")

    def test_dry_run_needs_no_synth(self):
        def no_midi(*a):
            raise AssertionError("opened MIDI")

        with tempfile.TemporaryDirectory() as folder:
            path, header, blocks = update_file(folder)
            pro800.args = [path, "--dry-run"]
            out = io.StringIO()
            with mock.patch.object(pro800, "port_names", no_midi), mock.patch.object(pro800, "open_transport", no_midi), \
                    contextlib.redirect_stdout(out):
                pro800.flash()
        self.assertIn(f"9 messages, {len(header) + sum(len(b) for b in blocks)} bytes, about 4 s at 3000 B/s", out.getvalue())

    def test_needs_yes(self):
        synth = Bootloader()
        with tempfile.TemporaryDirectory() as folder:
            out, code = run(synth, "flash", update_file(folder)[0])
        self.assertEqual(code, 1)
        self.assertIn("not sent", out)
        self.assertEqual(synth.sent, [])

    def test_refuses_other_files(self):
        with tempfile.TemporaryDirectory() as folder:
            cases = {"text": b"hello", "no-f7": HEADER + b"\x0A\x00", "not-update": HEADER + b"\x08\x00\xF7",
                     "other-data": HEADER + b"\x0A\xF7" + HEADER + b"\x08\xF7", "high-byte": HEADER + b"\x0A\x80\xF7",
                     "f0-inside": HEADER + b"\x0A" + HEADER + b"\xF7"}
            for name, content in cases.items():
                with self.subTest(name):
                    path = os.path.join(folder, name)
                    with open(path, "wb") as f:
                        f.write(content)
                    synth = Bootloader()
                    out, code = run(synth, "flash", path, "--yes")
                    self.assertEqual(code, 1, out)
                    self.assertEqual(synth.sent, [])
            self.assertEqual(run(Bootloader(), "flash", os.path.join(folder, "missing"), "--yes")[1], 1)

    def test_rate_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = update_file(folder)[0]
            self.assertEqual(run(Bootloader(), "flash", path, "--yes", "--rate", "9001")[1], 1)
            self.assertEqual(run(Bootloader(), "flash", path, "--yes", "--rate", "0")[1], 1)
            synth = Bootloader()
            self.assertEqual(run(synth, "flash", path, "--yes", "--erase-wait", "-1")[1], 1)
            self.assertEqual(synth.sent, [])

    def test_pacing(self):
        rate, erase_wait = 9000, 0.2
        synth = Bootloader()
        with tempfile.TemporaryDirectory() as folder:
            path, header, blocks = update_file(folder)
            out, code = run(synth, "flash", path, "--yes", "--rate", str(rate), "--erase-wait", str(erase_wait))
        self.assertEqual(code, 0)
        self.assertEqual(synth.sent, [header] + blocks)
        gaps = [b - a for a, b in zip(synth.times, synth.times[1:])]
        self.assertGreaterEqual(gaps[0], len(header) / rate + erase_wait)
        for gap, block in zip(gaps[1:], blocks):
            self.assertGreaterEqual(gap, len(block) / rate)
        speed = len(blocks[0]) * (len(synth.times) - 2) / (synth.times[-1] - synth.times[1])
        self.assertLess(speed, 9640)
        self.assertIn("reply after message 1: F0 00 20 32 00 01 24 00 03 31 F7", out)
        self.assertIn("F0 00 20 32 00 01 24 00 01 00 00 F7", out)
        self.assertIn("done.", out)

    def test_confirmed(self):
        self.assertEqual(self.flash(Bootloader(), block_size=4)[1], 0)

    def test_require_ok_is_still_taken(self):
        self.assertEqual(self.flash(Bootloader(), "--require-ok", block_size=4)[1], 0)
        self.assertEqual(self.flash(Bootloader(ok=False), "--require-ok", block_size=4)[1], 1)

    def test_no_header_accepted(self):
        synth = Bootloader(accepts=False)
        out, code = self.flash(synth, block_size=4)
        self.assertEqual(code, 1)
        self.assertIn("the bootloader did not accept the header; nothing was written", out)
        self.assertEqual(len(synth.sent), 1)  # not one block
        self.may_be_running(out)

    def test_the_accept_may_come_late(self):
        class Slow(Bootloader):
            def send(self, data):
                self.times.append(0)
                self.sent.append(bytes(data))
                if data[8] == 0x0A:
                    threading.Timer(0.05, self.receive, [HEADER + b"\x03\x31\xF7"]).start()
                elif len(self.sent) == 9:
                    self.receive(HEADER + b"\x01\x00\x00\xF7")

        self.assertEqual(self.flash(Slow(), block_size=4)[1], 0)

    def test_refused_block_stops_at_once(self):
        synth = Bootloader(status_at={4: 7})
        out, code = self.flash(synth, block_size=4)
        self.assertEqual(code, 1)
        self.assertEqual(len(synth.sent), 4)
        self.assertIn("the bootloader refused the update: 01 00 07 after message 4", out)
        self.assertNotIn("done.", out)
        self.recovers(out)

    def test_refused_header(self):
        synth = Bootloader(accepts=False, status_at={1: 1})  # stock firmware running
        out, code = self.flash(synth, block_size=4)
        self.assertEqual(code, 1)
        self.assertEqual(len(synth.sent), 1)
        self.assertIn("01 00 01", out)
        self.may_be_running(out)

    def test_refused_last_block(self):
        out, code = self.flash(Bootloader(ok=False), block_size=4)
        self.assertEqual(code, 1)
        self.assertIn("01 00 07", out)
        self.recovers(out)

    def test_unconfirmed(self):
        synth = Bootloader(ok=None)
        out, code = self.flash(synth, block_size=4)
        self.assertEqual(code, 1)
        self.assertEqual(len(synth.sent), 9)
        self.assertIn("the bootloader did not confirm the update", out)
        self.assertNotIn("done.", out)
        self.recovers(out)

    def test_an_ok_before_the_last_block_confirms_nothing(self):
        out, code = self.flash(Bootloader(ok=None, status_at={4: 0}), block_size=4)
        self.assertEqual(code, 1)
        self.assertIn("did not confirm", out)

    def test_other_replies_on_the_way_do_no_harm(self):
        class Chatty(Bootloader):
            def send(self, data):
                super().send(data)
                self.receive(HEADER + b"\x09\x00\x00\x00\x00\xF7")

        self.assertEqual(self.flash(Chatty(), block_size=4)[1], 0)

    def test_the_port_dying_midway(self):
        synth = Bootloader(dies_at=5)
        out, code = self.flash(synth, block_size=4)
        self.assertEqual(code, 1)
        self.assertEqual(len(synth.sent), 4)
        self.assertIn("midiOutLongMsg failed", out)
        self.recovers(out)

    def test_the_port_dying_on_the_header(self):
        synth = Bootloader(dies_at=1)
        out, code = self.flash(synth, block_size=4)
        self.assertEqual(code, 1)
        self.assertEqual(synth.sent, [])
        self.assertIn("midiOutLongMsg failed", out)
        self.may_be_running(out)

    def test_the_port_dying_right_after_the_accept(self):
        synth = Bootloader(dies_at=2)
        out, code = self.flash(synth, block_size=4)
        self.assertEqual(code, 1)
        self.assertEqual(len(synth.sent), 1)
        self.assertIn("midiOutLongMsg failed", out)
        self.recovers(out)


class UpdateFiles(unittest.TestCase):
    """What flash and install take, at the real size limit."""

    def refused(self, messages, text):
        synth = Bootloader()
        with tempfile.TemporaryDirectory() as folder:
            out, code = run(synth, "flash", write_update(folder, messages), "--yes")
            dry = run(synth, "flash", write_update(folder, messages), "--dry-run")
        self.assertEqual((code, dry[1]), (1, 1), out)
        self.assertIn(text, out)
        self.assertEqual(synth.sent, [])

    def test_a_whole_file(self):
        header, blocks = update_messages(blocks=48, block_size=240)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(len(pro800.update_messages(write_update(folder, [header] + blocks))), 49)

    def test_header_only(self):
        self.refused([update_messages()[0]], "only a header")

    def test_too_small(self):
        header, blocks = update_messages(blocks=8, block_size=200)
        self.refused([header] + blocks, "too small for a firmware")

    def test_no_name(self):
        header, blocks = update_messages(blocks=48, block_size=240)
        self.refused([header.replace(b"BEHRINGER PRO 800", b"BEHRINGER PRO 600")] + blocks, "the header isn't a PRO-800 update's")

    def test_cut_short_at_a_message(self):
        header, blocks = update_messages(blocks=48, block_size=240, declared=60)
        self.refused([header] + blocks, "48 data blocks where the header's size says 60")

    def test_cut_inside_a_message(self):
        header, blocks = update_messages(blocks=48, block_size=240)
        self.refused([header] + blocks[:-1] + [blocks[-1][:100]], "last message has no F7")

    def test_a_block_missing(self):
        header, blocks = update_messages(blocks=49, block_size=240, declared=48)
        self.refused([header] + blocks[:10] + blocks[11:], "aren't in order")

    def test_blocks_out_of_order(self):
        header, blocks = update_messages(blocks=48, block_size=240)
        blocks[3], blocks[4] = blocks[4], blocks[3]
        self.refused([header] + blocks, "aren't in order")

    def test_real_files_pass(self):
        # the packer's own output and the stock firmware, where this machine has them; git-ignored, so not everywhere
        root = os.path.dirname(HERE)
        found = glob.glob(os.path.join(root, "build", "pro800-*.syx")) + glob.glob(os.path.join(root, "backup", "stock-firmware", "*.syx"))
        if not found:
            self.skipTest("no packed firmware here")
        for path in found:
            with self.subTest(path):
                self.assertGreater(len(pro800.update_messages(path)), 100)


class Install(unittest.TestCase):
    """install's steps, with the USB list and the child processes replaced."""

    def setUp(self):
        patcher = mock.patch.object(pro800, "MIN_UPDATE_BYTES", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    class Machine:
        def __init__(self, mode=pro800.USB_FIRMWARE, enters=True, flash_ok=True, runs="0.9.3", port=True):
            self.mode, self.enters, self.flash_ok, self.runs, self.port = mode, enters, flash_ok, runs, port
            self.commands = []

        def usb(self, product):
            return self.mode == product

        def run_self(self, *command, capture=True, timeout=None):
            self.commands.append(command)
            done = mock.Mock(returncode=0, stdout="")
            if command[0] == "update-mode" and self.enters:
                self.mode = pro800.USB_BOOTLOADER
            elif command[0] == "flash":
                done.returncode = 0 if self.flash_ok else 1
                if self.flash_ok:
                    self.mode = pro800.USB_FIRMWARE
            elif command[0] == "version":
                done.stdout = f"firmware {self.runs}\n"
            elif command[0] == "port-present":
                done.returncode = 0 if self.port else 1
            return done

    def install(self, machine, version=(0, 9, 3)):
        with tempfile.TemporaryDirectory() as folder:
            path = update_file(folder, version)[0]
            out = io.StringIO()
            pro800.args = [path, "--yes"]
            code = 0
            real_sleep = time.sleep
            with mock.patch.object(pro800, "usb_device_present", machine.usb), mock.patch.object(pro800, "run_self", machine.run_self), \
                    mock.patch.object(pro800.time, "sleep", lambda s: real_sleep(min(s, 0.01))), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                try:
                    pro800.install()
                except SystemExit as e:
                    code = e.code or 0
        return out.getvalue(), code

    def names(self, machine):
        steps = []
        for command in machine.commands:
            if not steps or steps[-1] != command[0]:
                steps.append(command[0])
        return steps

    def test_from_a_running_firmware(self):
        machine = self.Machine()
        with mock.patch.object(pro800.time, "monotonic", side_effect=iter(range(0, 100000))):
            out, code = self.install(machine)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.names(machine), ["update-mode", "port-present", "flash", "version"])
        self.assertIn(("update-mode", "--yes"), machine.commands)  # install's own --yes is enough for the user
        self.assertTrue(any(c[0] == "flash" and "--yes" in c for c in machine.commands))
        self.assertIn("running firmware 0.9.3", out)

    def test_from_the_bootloader(self):
        machine = self.Machine(mode=pro800.USB_BOOTLOADER)
        with mock.patch.object(pro800.time, "monotonic", side_effect=iter(range(0, 100000))):
            out, code = self.install(machine)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.names(machine), ["port-present", "flash", "version"])
        self.assertIn("already in the bootloader", out)

    def test_no_synth(self):
        machine = self.Machine(mode=None)
        out, code = self.install(machine)
        self.assertEqual(code, 1)
        self.assertIn("no PRO-800 on USB", out)
        self.assertEqual(machine.commands, [])

    def test_no_flash_without_the_bootloader(self):
        machine = self.Machine(enters=False)
        with mock.patch.object(pro800.time, "monotonic", side_effect=iter(range(0, 100000))):
            out, code = self.install(machine)
        self.assertEqual(code, 1)
        self.assertIn("nothing was flashed", out)
        self.assertEqual([c[0] for c in machine.commands], ["update-mode"] * 3)

    def test_the_bootloaders_port_never_appears(self):
        machine = self.Machine(port=False)
        with mock.patch.object(pro800.time, "monotonic", side_effect=iter(range(0, 100000))):
            out, code = self.install(machine)
        self.assertEqual(code, 1)
        for text in ("the synth is in its bootloader", "nothing was flashed", "Restart the MIDI service or replug", "pro800 flash ", "--yes"):
            self.assertIn(text, out)
        self.assertNotIn("flash", [c[0] for c in machine.commands])

    class Timed(Machine):
        """With a clock, and the port coming and going as port_at() says"""

        def __init__(self, port_at=lambda t: True, **options):
            super().__init__(mode=pro800.USB_BOOTLOADER, **options)
            self.now, self.port_at, self.times = 0.0, port_at, {}

        def run_self(self, *command, capture=True, timeout=None):
            self.times.setdefault(command[0], self.now)
            self.port = self.port_at(self.now)
            return super().run_self(*command, capture=capture, timeout=timeout)

    def timed(self, machine):
        def sleep(seconds):
            machine.now += seconds

        with mock.patch.object(pro800.time, "monotonic", lambda: machine.now):
            with tempfile.TemporaryDirectory() as folder:
                pro800.args = [update_file(folder, (0, 9, 3))[0], "--yes"]
                with mock.patch.object(pro800, "usb_device_present", machine.usb), mock.patch.object(pro800, "run_self", machine.run_self), \
                        mock.patch.object(pro800.time, "sleep", sleep), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    pro800.install()
        return machine.times

    def test_a_page_left_open_has_finished_looking_before_the_flash(self):
        with open(os.path.join(HERE, "web", "index.html"), encoding="utf-8") as f:
            quiet = int(re.search(r"const RECONNECT_QUIET = (\d+);", f.read()).group(1)) / 1000
        with open(os.path.join(HERE, "web", "install.html"), encoding="utf-8") as f:
            page = f.read()
        idle_tab = int(re.search(r"setTimeout\(refresh, (\d+)\)", page).group(1)) / 1000
        self.assertIn('retry(2, () => request([0x79, ...a, 4], [0x79, ...a, 4], 1000))', page)
        self.assertIn('retry(2, () => request([0x77, 0x7E, 0x03], [0x78, 0x7E, 0x03], 1200))', page)
        last_message = idle_tab + 2 * 1.0 + 1.2  # 08, 79 and 79 again, 77 and 77 again: its last goes out a timeout before the end
        times = self.timed(self.Timed())
        wait = times["flash"] - times["port-present"]
        self.assertGreaterEqual(wait, pro800.PORT_STEADY)
        self.assertGreaterEqual(wait - quiet, 4, "the editor, with time for a browser that hears of the port late")
        self.assertGreaterEqual(wait - last_message, 1, "an idle installer tab")
        with open(os.path.join(HERE, "pro800.swift"), encoding="utf-8") as f:
            self.assertIn(f"let portSteady = {pro800.PORT_STEADY}\n", f.read())

    def test_a_port_that_drops_starts_the_wait_again(self):
        times = self.timed(self.Timed(port_at=lambda t: not 2 <= t < 3))
        self.assertGreaterEqual(times["flash"], 3 + pro800.PORT_STEADY)

    def test_says_why_a_step_failed(self):
        self.addCleanup(setattr, pro800, "child_error", "")
        pro800.child_error = "can't open the MIDI port: another program has it"
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            pro800.fail_with_child_error("nothing was flashed")
        self.assertIn("Last error: can't open the MIDI port", err.getvalue())

    def test_refused_flash(self):
        machine = self.Machine(flash_ok=False)
        with mock.patch.object(pro800.time, "monotonic", side_effect=iter(range(0, 100000))):
            out, code = self.install(machine)
        self.assertEqual(code, 1)
        self.assertIn("didn't confirm", out)

    def test_another_version_answers(self):
        machine = self.Machine(runs="0.9.2")
        with mock.patch.object(pro800.time, "monotonic", side_effect=iter(range(0, 100000))):
            out, code = self.install(machine)
        self.assertEqual(code, 1)
        self.assertIn("the synth runs firmware 0.9.2, not the 0.9.3", out)

    def test_bad_file_touches_nothing(self):
        machine = self.Machine()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "x.syx")
            with open(path, "wb") as f:
                f.write(b"nope")
            pro800.args = [path, "--yes"]
            with mock.patch.object(pro800, "usb_device_present", machine.usb), mock.patch.object(pro800, "run_self", machine.run_self), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                pro800.install()
        self.assertEqual(machine.commands, [])


class Helpers(unittest.TestCase):
    def test_short_message_sizes(self):
        self.assertEqual([pro800.short_message_size(s) for s in (0x90, 0xB5, 0xC0, 0xD3, 0xE0, 0xF1, 0xF2, 0xF3, 0xF6, 0xF8, 0xFE)],
                         [3, 3, 2, 2, 3, 2, 3, 2, 1, 1, 1])

    def test_rounding_is_away_from_zero(self):
        self.assertEqual([pro800.rounded(x) for x in (0.5, 1.5, 2.5, -0.5, -1.5, 0.49)], [1, 2, 3, -1, -2, 0])

    def test_unpack(self):
        self.assertEqual(pro800.unpack(bytes([0b0000101, 1, 2, 3, 4, 5, 6, 7, 0, 9])), bytes([0x81, 2, 0x83, 4, 5, 6, 7, 9]))

    def test_slots(self):
        self.assertEqual([pro800.slot_number(t) for t in ("0", "127", "p1", "P128", "128", "P0", "x")], [0, 127, 0, 127, None, None, None])


class ModIds(unittest.TestCase):
    """The mod sources and destinations of src/mod.h against every hand-kept table of them. Ids are append-only and a
    new one has to reach all of these at once; C fills a short initializer with zeros, so main.c wouldn't notice."""

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(HERE)

        def read(*path):
            with open(os.path.join(root, *path), encoding="utf-8") as f:
                return f.read()

        cls.mod_h, cls.main_c = read("src", "mod.h"), read("src", "main.c")
        cls.plan_py, cls.sound_plan, cls.index = read("presets", "lib", "plan.py"), read("tools", "web", "sound-plan.js"), read("tools", "web", "index.html")

    def enum(self, prefix):
        body = re.search(r"typedef enum \{\s*(" + prefix + r"_\w+.*?)" + prefix + r"_COUNT\b", self.mod_h, re.S).group(1)
        body = re.sub(r"//[^\n]*", "", body)
        return re.findall(r"\b(" + prefix + r"_\w+)", body)

    def strings(self, pattern, text):
        found = re.search(pattern + r"\s*\[(.*?)\]", text, re.S)
        self.assertIsNotNone(found, pattern)
        return re.findall(r'"([^"]*)"', found.group(1))

    def test_the_enums_end_where_the_ids_say(self):
        sources, dests = self.enum("SRC"), self.enum("DEST")
        self.assertEqual((sources[0], sources[-1], len(sources)), ("SRC_NONE", "SRC_RELEASE_VELOCITY", 30))
        self.assertEqual((dests[0], dests[-1], len(dests)), ("DEST_PITCH_A", "DEST_CC_OUT_B", 50))

    def c_strings(self, name):
        found = re.search(r"\b" + name + r"\[\w+\]\[\d+\] = \{(.*?)\};", self.main_c, re.S)
        self.assertIsNotNone(found, name)
        return re.findall(r'"([^"]*)"', found.group(1))

    def test_the_firmwares_display_names(self):
        self.assertEqual(len(self.c_strings("source_names")), len(self.enum("SRC")))
        self.assertEqual(len(self.c_strings("dest_names")), len(self.enum("DEST")))

    def test_the_tools_names(self):
        self.assertEqual(len(pro800.SOURCE_NAMES), len(self.enum("SRC")))
        self.assertEqual(pro800.SOURCE_NAMES[0], "")
        self.assertEqual(len(pro800.DEST_NAMES), len(self.enum("DEST")))

    def test_the_preset_plans_names(self):
        self.assertEqual(self.strings(r"\nSOURCES =", self.plan_py), pro800.SOURCE_NAMES[1:])
        self.assertEqual(self.strings(r"\nDESTS =", self.plan_py), pro800.DEST_NAMES)

    def test_the_web_pages_names(self):
        self.assertEqual(self.strings(r"\n  sources:", self.sound_plan), pro800.SOURCE_NAMES[1:])
        self.assertEqual(self.strings(r"\n  dests:", self.sound_plan), pro800.DEST_NAMES)
        self.assertEqual(len(self.strings(r"\nconst SOURCES =", self.index)), len(self.enum("SRC")))
        self.assertEqual(len(self.strings(r"\nconst DESTS =", self.index)), len(self.enum("DEST")))


if __name__ == "__main__":
    unittest.main()
