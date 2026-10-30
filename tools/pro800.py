#!/usr/bin/env python3
# pro800: talks to a Behringer PRO-800 over USB MIDI. The same commands as tools/pro800.swift, for Windows, where
# Swift and CoreMIDI aren't there.
#
# Windows: WinMM through ctypes, so nothing to build or install, only Python 3.8+. macOS and Linux: python-rtmidi
# (`pip install python-rtmidi`); on macOS tools/pro800.swift is still the tested tool.
#
# Not here: `record` and `play --record` (audio from the Move's input; macOS tool only).
#
# Commands that talk to the synth share one MIDI connection per process. `install` runs the MIDI steps as child
# processes, so that each finds the ports afresh while the synth re-enumerates.
import atexit
import datetime
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

HELP_TEXT = """\
Run as `python tools/pro800.py <command>`.

Read-only:
  pro800 ports                          list MIDI ports
  pro800 version                        firmware version
  pro800 listen [seconds]               print everything the synth sends (default 20 s)
  pro800 backup <dir> [first] [last]    dump system settings and patches as .syx (stock firmware only)
  pro800 play <note> [options]          play a note
      --channel N (1-16 or "all"; default the synth's MIDI channel, else 6)  --hold S (default 2)  --velocity V (default 100)

Probe (our firmware 0.0.9+):
  pro800 direct on|off                  stop/start the voice engine so CVs and gates stay as set
  pro800 cv <name> <value> ...          set CVs (osca1-8 oscb1-8 fil1-8 amp1-8 pmod vola volb master
                                        noise extfil pwa pwb res), values 0-65535 or 0x...
  pro800 gate <name> 0|1 ...            set gates (sawa tria sync sawb trib pmodfa pmodfil)
  pro800 measure [periods] [--timeout ms] [--repeat n] [--rising]
                                        time TUNE_CPR (the tuning comparator) in the synth
  pro800 tune                           run autotune in the synth (about 25 s) and save it
  pro800 tuning                         print the tuning tables (Hz per CV point)
  pro800 check [notes...] [--osc|--filters]
                                        play notes through the tables on every oscillator and filter (filters
                                        self-oscillating, 0.8.4+), print cents off
  pro800 dac-test [--bus-only|--timing-only|--clock]
                                        sweep DAC80501 buses and S&H waits, checked by osc A1's pitch (0.7.4+;
                                        silent; changes nothing stored)
  pro800 sh-waits <settle µs> <sample µs>  the S&H waits for channels written alone (RAM only; 0 0 = default)
  pro800 dac-heal                       set the DAC80501's gain wrong on purpose and check the firmware's re-send
                                        puts it right (0.8.5+; silent)
  pro800 panel [seconds]                print every pot, switch, button and encoder change live
  pro800 state                          print every pot (0-99) and switch as the firmware reads them
  pro800 set <setting> <value>          lfo-sync / lfo2-sync free|clock, lfo-trigger / lfo2-trigger
                                        free|first|every|voice|one-shot|one-shot-voice, lfo-cycles / lfo2-cycles <1-99>
                                        (how many cycles a one shot runs), lfo-restart steps|keys (what restarts the LFOs with a sequence on), layers off|split|stack|channel|8ch|mpe, split <note>,
                                        keytrack off|half|full, lfo 1|2 (the LFO the knobs edit),
                                        bend <1-12> (semitones), detune <0-50> (unison, cents), drift <0-99>,
                                        scale off|major|minor|dorian|…|chromatic, root C..B,
                                        voice <layer> poly|mono|legato last|low|high,
                                        shape <layer> <lfo> tri|square|ramp-up|ramp-down|sine|sh|drift,
                                        lfo2-dests <layer> none|pitch,filter,pw,
                                        cycle <layer> none|amp,filter,env3,env4 (cycling envelopes),
                                        steps <layer> <lfo> x.x.xx.. (the step shape's pattern, 1-16),
                                        cycle-sync <layer> <env> free|1/16|… (a cycle's length from the clock),
                                        button a-saw|…|sync on|off (the panel's latching buttons),
                                        midi-channel all|1-16 (the channel it listens on; 0.9.1),
                                        pickup on|off (off: a knob that moves takes over at once; 0.9.3)
  pro800 settings                       print every setting
  pro800 params [layer]                 print a layer's parameters (0-99), Env 3 / 4 and LFO 2 included
  pro800 param <layer> <name> <0-99>    set one (its knob then picks it up)
  pro800 layer 1|2                      choose the layer the per-layer knobs edit
  pro800 route <layer> <src> <dest> <depth>  set a mod route (-99..99, 0 removes); pro800 routes lists them
  pro800 route-both <layer> <src> <dest> on|off  the route plays on the other layer's voices too, in the two-layer modes
  pro800 timing                         engine, CV refresh and main-loop pass times
  pro800 sysex                          for scripts: SysEx requests from stdin ("<hex> [; <reply prefix hex>]" per line),
                                        replies on stdout as hex; over one MIDI connection
  pro800 engine                         the engine's state: held keys, voice gates and envelope stages, keytrack,
                                        switch overrides (read-only probe, 0.9.1+)
  pro800 cc <controller> <value>        send a control change (e.g. 1 = mod wheel)
  pro800 bend <0-16383>                 send pitch bend (8192 = centre); --channel N for both
  pro800 eeprom <file> [start] [length] read the patch EEPROM (read-only; default all 128 kB)
  pro800 patches [--all]                list the patch slots (ours, stock, empty)
  pro800 patch load|save|clear <slot>   load a patch, save the current sound, or empty a slot (0-127, P1-P128;
                                        each patch has its own sequence, saved and cleared with it)
  pro800 seq                            print the loaded patch's step sequence (firmware 0.7.0+)
  pro800 seq set key=value…             its header: on, length, step (division), clock, bpm, swing, once, keys, arp,
                                        lanes, chord (0-2 extra notes per step), smooth (e.g. seq set on=1 length=16 step=1/16 clock=1 lanes=1 smooth=A)
  pro800 seq step <n> <note|.> <gate|tie> [v<vel>] [p<prob>] [x<ratchets>] [a<v>]… [n2<v>] [n3<v>] [h<v>]  one step (a-d: lanes, -99..99; n2, n3: chord notes, semitones from the step's note; h: notes held past the gate, 1 own + 2 Note 2 + 4 Note 3)
  pro800 set morph <slot|none>          the morph target patch
  pro800 patch init                     load the basic sound (osc A saw), as PERF does
  pro800 patch rename <slot> <name>     rename a patch (up to 16 characters)
  pro800 factory                        list the stock factory bank (100 patches, kept in the EEPROM)
  pro800 factory import <f> <slot> [--force]  copy factory patch f (0-99) into a slot (empty unless --force)
  pro800 clock <bpm> [seconds]          send MIDI clock (Start, then 24 per quarter note)
  pro800 leds <address> <value> ...     write display RAM bytes (HT16K33, addresses 0-15)

Changes the synth (only when you mean it):
  pro800 update-mode                    reboot into the bootloader's update mode
  pro800 flash <file.syx> --yes [--rate B/s] [--erase-wait S] [--dry-run]
                                        stream a firmware update to the bootloader
  pro800 format --yes                   wipe every patch slot and mark the EEPROM as ours (format 2), in place
  pro800 eeprom-write <file> --yes      write a whole EEPROM image and verify it (e.g. back to stock)
  pro800 install <file.syx> --yes       update-mode + flash + check the synth runs the file's version

Options for every command: --port <name> (default "PRO 800").
"""

SYSEX_HEADER = bytes([0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00])
HEADER_SIZE = len(SYSEX_HEADER)
EMPTY_SLOT = bytes([0xF0, 0xF7])
EMPTY_GRACE = 0.1


def fail(message):
    sys.stdout.flush()
    print(message, file=sys.stderr)
    sys.exit(1)


def show(text="", end="\n"):
    print(text, end=end, flush=True)


def hexdump(data):
    return " ".join(f"{b:02X}" for b in data)


def pad(text, width):
    return text[:width].ljust(width)


def at(items, i, default=None):
    return items[i] if 0 <= i < len(items) else default


def to_int(text):
    return int(text) if text is not None and re.fullmatch(r"[+-]?[0-9]+", text) else None


def to_float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def int_in(text, low, high):
    v = to_int(text)
    return v if v is not None and low <= v <= high else None


def index_of(items, item):
    return items.index(item) if item in items else None


def parse_value(text):
    if text.lower().startswith("0x"):
        try:
            return int(text[2:], 16)
        except ValueError:
            return None
    return to_int(text)


def rounded(x):  # half away from zero
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def bits7(data):  # big-endian, 7 bits a byte
    value = 0
    for b in data:
        value = value << 7 | b
    return value


# MARK: - Arguments

args = sys.argv[1:]


def option(name):
    if name not in args:
        return None
    i = args.index(name)
    if i + 1 >= len(args) or args[i + 1].startswith("--"):
        del args[i]
        return ""
    value = args[i + 1]
    del args[i:i + 2]
    return value


def flag(name):
    if name not in args:
        return False
    args.remove(name)
    return True


port_name = "PRO 800"

# MARK: - MIDI backends: port_names() -> (sources, destinations), open_transport(source, destination, receive)


def short_message_size(status):
    if status >= 0xF4 and status != 0xF7 or status == 0xF0:
        return 1
    return 2 if status & 0xE0 == 0xC0 or status in (0xF1, 0xF3) else 3


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    winmm = ctypes.WinDLL("winmm")
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    cfgmgr32 = ctypes.WinDLL("cfgmgr32")

    HANDLE = ctypes.c_void_p
    CALLBACK_THREAD = 0x20000
    MM_MIM_DATA, MM_MIM_LONGDATA = 0x3C3, 0x3C4
    WM_QUIT = 0x12
    MMSYSERR_ALLOCATED = 4
    MIDIERR_STILLPLAYING = 65
    CM_GETIDLIST_FILTER_ENUMERATOR, CM_GETIDLIST_FILTER_PRESENT = 0x1, 0x100
    CR_BUFFER_SMALL = 0x1A

    class MidiInCaps(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwSupport", wintypes.DWORD)]

    class MidiOutCaps(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("wTechnology", wintypes.WORD), ("wVoices", wintypes.WORD),
                    ("wNotes", wintypes.WORD), ("wChannelMask", wintypes.WORD), ("dwSupport", wintypes.DWORD)]

    class MidiHdr(ctypes.Structure):
        _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                    ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t), ("dwFlags", wintypes.DWORD),
                    ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t), ("dwOffset", wintypes.DWORD),
                    ("dwReserved", ctypes.c_size_t * 8)]

    class Msg(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", ctypes.c_size_t),
                    ("lParam", ctypes.c_ssize_t), ("time", wintypes.DWORD), ("pt", wintypes.POINT)]

    winmm.midiInGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(MidiInCaps), wintypes.UINT]
    winmm.midiOutGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(MidiOutCaps), wintypes.UINT]
    winmm.midiInOpen.argtypes = [ctypes.POINTER(HANDLE), wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    winmm.midiOutOpen.argtypes = [ctypes.POINTER(HANDLE), wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    for name in ("midiInPrepareHeader", "midiInUnprepareHeader", "midiInAddBuffer", "midiOutPrepareHeader",
                 "midiOutUnprepareHeader", "midiOutLongMsg"):
        getattr(winmm, name).argtypes = [HANDLE, ctypes.POINTER(MidiHdr), wintypes.UINT]
    for name in ("midiInStart", "midiInStop", "midiInReset", "midiInClose", "midiOutClose"):
        getattr(winmm, name).argtypes = [HANDLE]
    winmm.midiOutShortMsg.argtypes = [HANDLE, wintypes.DWORD]
    winmm.midiOutGetErrorTextW.argtypes = [wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    user32.GetMessageW.argtypes = [ctypes.POINTER(Msg), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.argtypes = [ctypes.POINTER(Msg), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    cfgmgr32.CM_Get_Device_ID_List_SizeW.argtypes = [ctypes.POINTER(wintypes.ULONG), wintypes.LPCWSTR, wintypes.ULONG]
    cfgmgr32.CM_Get_Device_ID_ListW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.ULONG, wintypes.ULONG]

    def port_names():
        sources, destinations = [], []
        for i in range(winmm.midiInGetNumDevs()):
            caps = MidiInCaps()
            if winmm.midiInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                sources.append(caps.szPname)
            else:
                sources.append("")  # keeps the list's indices the device numbers
        for i in range(winmm.midiOutGetNumDevs()):
            caps = MidiOutCaps()
            if winmm.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                destinations.append(caps.szPname)
            else:
                destinations.append("")
        return sources, destinations

    def midi_error(code):
        text = ctypes.create_unicode_buffer(256)
        winmm.midiOutGetErrorTextW(code, text, 256)
        return f"{text.value} ({code})"

    def checked(what, code):
        if code == MMSYSERR_ALLOCATED:
            fail(f"can't open the MIDI port: another program has it (close it, or install Windows MIDI Services, "
                 f"which lets programs share a port)")
        if code != 0:
            fail(f"{what} failed: {midi_error(code)}")

    class WinMM:
        # Input arrives as messages to a thread of ours (CALLBACK_THREAD), not a callback: WinMM's callbacks mustn't
        # call WinMM, and the thread can give each buffer straight back.
        BUFFERS, BUFFER_SIZE = 16, 4096

        def __init__(self, source, destination, receive):
            self.receive = receive
            self.closing = False
            self.thread_id = 0
            ready = threading.Event()
            self.thread = threading.Thread(target=self.input_loop, args=(ready,), daemon=True)
            self.thread.start()
            ready.wait()
            self.input = HANDLE()
            checked("midiInOpen", winmm.midiInOpen(ctypes.byref(self.input), source, self.thread_id, 0, CALLBACK_THREAD))
            self.buffers = [ctypes.create_string_buffer(self.BUFFER_SIZE) for _ in range(self.BUFFERS)]
            self.headers = [MidiHdr() for _ in range(self.BUFFERS)]
            for buffer, header in zip(self.buffers, self.headers):
                header.lpData = ctypes.cast(buffer, ctypes.c_void_p)
                header.dwBufferLength = self.BUFFER_SIZE
                checked("midiInPrepareHeader", winmm.midiInPrepareHeader(self.input, header, ctypes.sizeof(header)))
                checked("midiInAddBuffer", winmm.midiInAddBuffer(self.input, header, ctypes.sizeof(header)))
            checked("midiInStart", winmm.midiInStart(self.input))
            self.output = HANDLE()
            checked("midiOutOpen", winmm.midiOutOpen(ctypes.byref(self.output), destination, 0, 0, 0))

        def input_loop(self, ready):
            msg = Msg()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # creates this thread's message queue
            self.thread_id = kernel32.GetCurrentThreadId()
            ready.set()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == MM_MIM_DATA:
                    self.receive((msg.lParam & 0xFFFFFF).to_bytes(3, "little")[:short_message_size(msg.lParam & 0xFF)])
                elif msg.message == MM_MIM_LONGDATA:
                    header = ctypes.cast(msg.lParam, ctypes.POINTER(MidiHdr))
                    if header.contents.dwBytesRecorded:
                        self.receive(ctypes.string_at(header.contents.lpData, header.contents.dwBytesRecorded))
                    if not self.closing:
                        winmm.midiInAddBuffer(self.input, header, ctypes.sizeof(MidiHdr))

        def send(self, data):
            if data[0] != 0xF0:
                checked("midiOutShortMsg", winmm.midiOutShortMsg(self.output, int.from_bytes(data, "little")))
                return
            buffer = ctypes.create_string_buffer(data, len(data))
            header = MidiHdr()
            header.lpData = ctypes.cast(buffer, ctypes.c_void_p)
            header.dwBufferLength = header.dwBytesRecorded = len(data)
            checked("midiOutPrepareHeader", winmm.midiOutPrepareHeader(self.output, header, ctypes.sizeof(header)))
            checked("midiOutLongMsg", winmm.midiOutLongMsg(self.output, header, ctypes.sizeof(header)))
            while winmm.midiOutUnprepareHeader(self.output, header, ctypes.sizeof(header)) == MIDIERR_STILLPLAYING:
                time.sleep(0.001)

        def close(self):
            self.closing = True
            winmm.midiInStop(self.input)
            winmm.midiInReset(self.input)
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.thread.join(1)
            for header in self.headers:
                winmm.midiInUnprepareHeader(self.input, header, ctypes.sizeof(header))
            winmm.midiInClose(self.input)
            winmm.midiOutClose(self.output)

    def open_transport(source, destination, receive):
        return WinMM(source, destination, receive)
else:
    def load_rtmidi():
        try:
            import rtmidi
        except ImportError:
            fail("needs python-rtmidi on this system: pip install python-rtmidi")
        return rtmidi

    def port_names():
        rtmidi = load_rtmidi()
        return rtmidi.MidiIn().get_ports(), rtmidi.MidiOut().get_ports()

    class RtMidi:
        def __init__(self, source, destination, receive):
            rtmidi = load_rtmidi()
            self.input = rtmidi.MidiIn()
            self.input.ignore_types(sysex=False, timing=True, active_sense=True)
            self.input.set_callback(lambda event, _: receive(bytes(event[0])))
            self.input.open_port(source)
            self.output = rtmidi.MidiOut()
            self.output.open_port(destination)

        def send(self, data):
            self.output.send_message(data)

        def close(self):
            self.input.close_port()
            self.output.close_port()

    def open_transport(source, destination, receive):
        return RtMidi(source, destination, receive)


def find_port(names, port):
    # the exact name, else a part of it: Windows numbers a second device ("2- PRO 800"), ALSA adds to the name
    if port in names:
        return names.index(port)
    for i, name in enumerate(names):
        if port and port.lower() in name.lower():
            return i
    return None


class Midi:
    def __init__(self, port):
        self.lock = threading.Lock()
        self.partial = bytearray()
        self.sysex = []
        self.arrived = threading.Event()
        self.on_short_message = None  # non-SysEx messages, for `listen`; realtime bytes are dropped
        sources, destinations = port_names()
        source, destination = find_port(sources, port), find_port(destinations, port)
        if source is None or destination is None:
            fail(f'MIDI port "{port}" not found (see `pro800 ports`)')
        self.transport = open_transport(source, destination, self.receive)

    def close(self):
        time.sleep(0.05)  # what was just sent is still on its way
        self.transport.close()

    # One message can span several buffers, so SysEx is assembled here.
    def receive(self, data):
        with self.lock:
            short = bytearray()
            for b in data:
                if b >= 0xF8:
                    continue
                if b == 0xF0:
                    self.partial = bytearray([b])
                    continue
                if self.partial:
                    self.partial.append(b)
                    if b == 0xF7:
                        self.sysex.append(bytes(self.partial))
                        self.partial = bytearray()
                        self.arrived.set()
                    continue
                if b & 0x80 and short:
                    if self.on_short_message:
                        self.on_short_message(bytes(short))
                    short = bytearray()
                short.append(b)
            if short and self.on_short_message:
                self.on_short_message(bytes(short))

    def send(self, data):
        self.transport.send(bytes(data))

    def take_sysex(self):
        with self.lock:
            taken, self.sysex = self.sysex, []
            return taken

    # Sends header + data + F7 and waits for a PRO-800 reply whose data starts with `expect`. An empty patch slot
    # answers with a bare F0 F7, returned as is with allow_empty (only `backup` asks for the stock firmware's slots).
    # The stock firmware also sends one after the system settings, which can arrive after the next request went out,
    # so a real reply gets EMPTY_GRACE seconds to follow it.
    def request(self, data, expect, timeout=1.5, allow_empty=False):
        expect = bytes(expect)
        self.take_sysex()
        self.send(SYSEX_HEADER + bytes(data) + b"\xF7")
        deadline = time.monotonic() + timeout
        seen = []
        empty_at = None
        while time.monotonic() < deadline:
            self.arrived.wait(0.02)
            self.arrived.clear()
            seen += self.take_sysex()
            for m in seen:
                if len(m) > HEADER_SIZE and m.startswith(SYSEX_HEADER):
                    body = m[HEADER_SIZE:]
                    if body.startswith(expect) or body.startswith(b"\x01\x00\x01"):
                        return m
            if allow_empty and EMPTY_SLOT in seen:
                empty_at = empty_at or time.monotonic()
                if time.monotonic() - empty_at >= EMPTY_GRACE:
                    return EMPTY_SLOT
        return EMPTY_SLOT if allow_empty and EMPTY_SLOT in seen else None


connection = None


def midi():  # the process's one connection: a port may not open twice (WinMM without Windows MIDI Services)
    global connection
    if connection is None:
        connection = Midi(port_name)
    return connection


def close_midi():
    global connection
    if connection is not None:
        connection.close()
        connection = None


# 7-bit → 8-bit: each group starts with a byte holding the MSBs of the next 7 (bit 0 → first byte).
def unpack(packed):
    out = bytearray()
    i = 0
    while i < len(packed):
        msbs = packed[i]
        i += 1
        for bit in range(7):
            if i < len(packed):
                out.append(packed[i] | ((msbs >> bit) & 1) << 7)
                i += 1
    return bytes(out)


def patch_label(n):
    return f"P{n + 1}"


# Patch name: offset 150 of the unpacked data, up to 16 bytes, 0-terminated.
def patch_name(data):
    if len(data) <= 150:
        return ""
    name = ""
    for c in data[150:166]:
        if c == 0:
            break
        name += chr(c) if 0x20 <= c < 0x7F else "?"
    return name


# MARK: - Commands


def ports():
    sources, destinations = port_names()
    show("MIDI sources:")
    for name in sources:
        show(f"  {name}")
    show("MIDI destinations:")
    for name in destinations:
        show(f"  {name}")


def version_text(reply):
    return ".".join(str(b) for b in reply[HEADER_SIZE + 2:-1])


def version():
    reply = midi().request([0x08, 0x00], expect=[0x09, 0x00])
    if reply is None:
        fail("no reply to the version request")
    show(f"firmware {version_text(reply)}")


def listen():
    seconds = to_float(args[0] if args else "20") or 20
    m = midi()
    count = [0]

    def short(message):
        show(hexdump(message))
        count[0] += 1

    m.on_short_message = short
    show(f'listening on "{port_name}" for {int(seconds)} s (clock and active sensing hidden)')
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(0.1)
        for message in m.take_sysex():
            show(hexdump(message))
            count[0] += 1
    show(f"{count[0]} messages")


def backup():
    if not args:
        fail("usage: pro800 backup <dir> [first] [last]")
    first = (to_int(args[1]) if to_int(args[1]) is not None else 0) if len(args) > 1 else 0
    last = (to_int(args[2]) if to_int(args[2]) is not None else 399) if len(args) > 2 else 399
    if not 0 <= first <= last <= 399:
        fail("patch range must be within 0-399")
    out = args[0]
    os.makedirs(out, exist_ok=True)

    def save(data, name):
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)

    m = midi()
    log = []

    def note(text):
        show(text)
        log.append(text)

    ver = m.request([0x08, 0x00], expect=[0x09, 0x00])
    if ver is None:
        fail("no reply to the version request")
    note(f"firmware {version_text(ver)}")
    save(ver, "version.syx")

    everything = bytearray()
    system = m.request([0x77, 0x7E, 0x03], expect=[0x78, 0x7E, 0x03])
    if system is not None:
        save(system, "system.syx")
        everything += system
        note(f"system settings: {len(system)} bytes")
    else:
        note("system settings: NO REPLY")

    saved = empty = 0
    failed = []
    for n in range(first, last + 1):
        lo, hi = n & 0x7F, n >> 7
        reply = m.request([0x77, lo, hi], expect=[0x78, lo, hi], allow_empty=True)
        if reply is None:
            reply = m.request([0x77, lo, hi], expect=[0x78, lo, hi], timeout=3, allow_empty=True)
        label = patch_label(n)
        if reply == EMPTY_SLOT:
            empty += 1
            note(f"{label}: empty")
            continue
        if reply is None or len(reply) <= HEADER_SIZE + 3 or reply[HEADER_SIZE] != 0x78:
            failed.append(label)
            note(f"{label}: " + (f"unexpected {hexdump(reply)}" if reply is not None else "NO REPLY"))
            continue
        save(reply, f"patch-{n:03d}-{label}.syx")
        everything += reply
        saved += 1
        data = unpack(reply[HEADER_SIZE + 3:-1])
        fmt = f"0x{data[4]:02X}" if len(data) > 4 else "?"
        note(f'{label}: {len(reply)} bytes, format {fmt}, name "{patch_name(data)}"')
    save(bytes(everything), "pro800-all.syx")
    note(f"done: {saved} patches saved, {empty} empty slots, {len(failed)} failed {failed}")
    with open(os.path.join(out, "dump-log.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log) + "\n")
    if failed:
        sys.exit(1)


# MARK: - play


def no_audio():
    fail("recording the synth's audio is only in the macOS tool (tools/pro800.swift)")


def play():
    if option("--record") is not None or option("--wav") is not None:
        no_audio()
    channel_arg = option("--channel")
    hold = to_float(option("--hold"))
    hold = 2.0 if hold is None else hold
    velocity = int_in(option("--velocity"), 0, 127)
    velocity = 100 if velocity is None else velocity
    note = int_in(args[0], 0, 127) if args else None
    if note is None:
        fail("usage: pro800 play <note 0-127> [--channel N|all] [--hold S] [--velocity V]")
    if channel_arg is None:
        channel_arg = str(synth_channel() or 6)
    if channel_arg == "all":
        channels = list(range(16))
    elif int_in(channel_arg, 1, 16) is not None:
        channels = [int(channel_arg) - 1]
    else:
        fail("--channel must be 1-16 or all")
    m = midi()
    for c in channels:
        m.send([0x90 | c, note, velocity])
    time.sleep(hold)
    for c in channels:
        m.send([0x80 | c, note, 0])


# MARK: - Probe (our firmware only)

CV_NAMES = ([f"osca{i}" for i in range(1, 9)] + [f"oscb{i}" for i in range(1, 9)] + [f"fil{i}" for i in range(1, 9)]
            + [f"amp{i}" for i in range(1, 9)] + ["pmod", "vola", "volb", "master", "noise", "extfil", "pwa", "pwb", "res"])
GATE_NAMES = ["sawa", "tria", "sync", "sawb", "trib", "pmodfa", "pmodfil"]


def probe_ack(data):
    reply = midi().request(data, expect=[0x7F, data[0]])
    if reply is None or len(reply) <= HEADER_SIZE or reply[HEADER_SIZE] != 0x7F:
        fail("no acknowledgement (is our firmware 0.0.9 or later running?)")


def set_cv(name, value):
    probe_ack([0x70, CV_NAMES.index(name), value >> 14, (value >> 7) & 0x7F, value & 0x7F])


def direct():
    mode = args[0] if args else None
    if mode not in ("on", "off"):
        fail("usage: pro800 direct on|off")
    probe_ack([0x74, 1 if mode == "on" else 0])
    show(f"direct mode {mode}")


# Sets CVs: pro800 cv <name> <value> [<name> <value> ...]
def cv():
    if len(args) < 2 or len(args) % 2:
        fail(f"usage: pro800 cv <name> <value 0-65535 or 0x...> ...\nnames: {' '.join(CV_NAMES)}")
    for i in range(0, len(args), 2):
        name = args[i].lower()
        if name not in CV_NAMES:
            fail(f"unknown CV {args[i]}")
        value = parse_value(args[i + 1])
        if value is None or not 0 <= value <= 0xFFFF:
            fail(f"bad value {args[i + 1]}")
        set_cv(name, value)


# Sets gates: pro800 gate <name> 0|1 [...]
def gate():
    if len(args) < 2 or len(args) % 2:
        fail(f"usage: pro800 gate <name> 0|1 ...\nnames: {' '.join(GATE_NAMES)}")
    for i in range(0, len(args), 2):
        name = args[i].lower()
        if name not in GATE_NAMES:
            fail(f"unknown gate {args[i]}")
        probe_ack([0x71, GATE_NAMES.index(name), 0 if args[i + 1] == "0" else 1])


# One TUNE_CPR measurement in the synth: (Hz, periods counted), or None without a usable signal.
def measure_once(periods, timeout_ms, rising=False):
    data = [0x72, periods >> 7, periods & 0x7F, timeout_ms >> 7, timeout_ms & 0x7F, 1 if rising else 0]
    reply = midi().request(data, expect=[0x73], timeout=timeout_ms / 1000 + 2)
    if reply is None:
        fail("no reply (is our firmware 0.1.3 or later running?)")
    b = reply[HEADER_SIZE + 1:]
    if len(b) < 11 or b[0] != 1:
        return None
    counted = b[1] << 7 | b[2]
    cycles, khz = bits7(b[3:8]), bits7(b[8:11])
    return counted * khz * 1000 / cycles, counted


# Times TUNE_CPR in the synth: pro800 measure [periods] [--timeout ms] [--repeat n] [--rising]
def measure():
    timeout = to_int(option("--timeout"))
    timeout = 1000 if timeout is None else timeout
    repeat = to_int(option("--repeat"))
    repeat = 1 if repeat is None else repeat
    rising = flag("--rising")
    periods = to_int(args[0]) if args else 16
    periods = 16 if periods is None else periods
    if not 1 <= periods <= 255 or not 1 <= timeout <= 16383:
        fail("periods 1-255, timeout 1-16383 ms")
    for _ in range(repeat):
        result = measure_once(periods, timeout, rising)
        if result:
            show(f"{result[0]:.4f} Hz  ({result[1]} periods)")
        else:
            show(f"no signal on TUNE_CPR within {timeout} ms")


# Runs autotune in the synth (about 25 s).
def tune():
    show("tuning (about 25 s, silent)...")
    reply = midi().request([0x75], expect=[0x76], timeout=120)
    if reply is None:
        fail("no reply (firmware 0.1.1 or later?)")
    show(f"done: {reply[HEADER_SIZE + 1]} of 24 rows measured")


def tuning_rows():
    rows = []
    for row in range(24):
        reply = midi().request([0x77, row], expect=[0x77, row], timeout=2)
        if reply is None:
            fail(f"no reply for row {row}")
        b = reply[HEADER_SIZE + 3:-1]
        rows.append([bits7(b[i:i + 4]) / 100 for i in range(0, len(b) - 3, 4)])
    return rows


# Prints the tuning tables: measured Hz per CV point (0x2000 ... 0xF000).
def tuning():
    rows = tuning_rows()
    names = [f"osc A{i}" for i in range(1, 9)] + [f"osc B{i}" for i in range(1, 9)] + [f"filter{i}" for i in range(1, 9)]
    show("row       " + "".join(f"{0x2000 + 0x1000 * i:04X}".rjust(8) for i in range(14)))
    for r, row in enumerate(rows):
        show(pad(names[r], 10) + "".join("       -" if v == 0 else f"{v:8.1f}" for v in row))


# Plays notes through the tables on every oscillator and filter and measures them with TUNE_CPR; prints the error
# in cents. pro800 check [notes...] [--osc | --filters]. Filters are measured as autotune measures them,
# self-oscillating at full resonance with the mixer off (filters from firmware 0.8.4).
def check():
    only_osc, only_filters = flag("--osc"), flag("--filters")
    given = [to_int(a) for a in args if to_int(a) is not None]
    osc_notes = given or [36, 48, 60, 72, 84, 96]
    filter_notes = given or [48, 60, 72, 84, 96, 108, 120]
    ack = probe_ack
    m = midi()

    # sets the source (0 osc A, 1 osc B, 2 filter) of voice v to the note through the tables; measures; cents off
    def cents(v, source, note):
        r = m.request([0x78, v - 1, source, note], expect=[0x7F, 0x78], timeout=2)
        if r is None or r[HEADER_SIZE] != 0x7F:
            fail("no acknowledgement: checking filters needs firmware 0.8.4 or later (or pass --osc)" if source == 2
                 else "no acknowledgement (is our firmware running?)")
        expected = 440 * 2 ** ((note - 69) / 12)
        periods = min(255, max(6, int(expected * 0.08)))
        time.sleep(0.06 if source == 2 else 0.03)
        # the comparator now and then reads an octave high below ~50 Hz: retry big errors, keep the best
        best = None
        for _ in range(3):
            result = measure_once(periods, 2047)
            if result:
                c = 1200 * math.log2(result[0] / expected)
                if best is None or abs(c) < abs(best):
                    best = c
                if abs(c) < 50:
                    break
        return best

    worst = [0.0, 0.0]

    def row(name, v, source, notes):
        set_cv(f"amp{v}", 0xFFFF)
        line = pad(name, 20)
        for note in notes:
            c = cents(v, source, note)
            if c is not None:
                worst[source // 2] = max(worst[source // 2], abs(c))
                line += f"{c:+6.1f}"
            else:
                line += "     -"
        show(line)
        set_cv(f"amp{v}", 0)

    ack([0x74, 1])
    set_cv("master", 0)
    set_cv("res", 0)
    set_cv("pwa", 0xFFFF)
    set_cv("pwb", 0)
    for v in range(1, 9):
        set_cv(f"amp{v}", 0)
        set_cv(f"fil{v}", 0xFFFF)
    if not only_filters:
        show("cents off per note: " + "".join(f"{n:6d}" for n in osc_notes))
        for osc in range(2):
            ack([0x71, 0, 1 if osc == 0 else 0])
            ack([0x71, 3, 1 if osc == 1 else 0])
            set_cv("vola", 0xFFFF if osc == 0 else 0)
            set_cv("volb", 0xFFFF if osc == 1 else 0)
            for v in range(1, 9):
                row(f"osc {'A' if osc == 0 else 'B'}{v}", v, osc, osc_notes)
    if not only_osc:
        ack([0x71, 0, 0])
        ack([0x71, 3, 0])
        set_cv("vola", 0)
        set_cv("volb", 0)
        set_cv("res", 0xFFFF)
        if not only_filters:
            show("")
        show("filters, self-oscillating at full resonance")
        show("cents off per note: " + "".join(f"{n:6d}" for n in filter_notes))
        for v in range(1, 9):
            row(f"filter {v}", v, 2, filter_notes)
            set_cv(f"fil{v}", 0xFFFF)
        set_cv("res", 0)
    ack([0x74, 0])
    if not only_filters:
        show(f"worst oscillator: {worst[0]:.1f} cents")
    if not only_osc:
        show(f"worst filter: {worst[1]:.1f} cents")


# The median of three readings of TUNE_CPR, or None
def median_hz(periods):
    time.sleep(0.05)
    readings = []
    for _ in range(3):
        result = measure_once(periods, 1500)
        if result:
            readings.append(result[0])
    return sorted(readings)[len(readings) // 2] if readings else None


# Checks the DAC80501 setup re-send (0.8.5): in direct mode, osc A1 at CV 0x8000 through TUNE_CPR, then with the re-send
# off and the gain at reference / 2 × 1 (half the CV range: about 92 Hz instead of 343), then with the re-send on
# again. pro800 dac-heal
def dac_heal():
    m = midi()

    def ack(data):
        r = m.request(data, expect=[0x7F, data[0]], timeout=2)
        if r is None or r[HEADER_SIZE] != 0x7F:
            fail("no acknowledgement (firmware 0.8.5 or later?)")

    def report(label):
        hz = median_hz(16)
        show(label + (f"{hz:8.1f} Hz" if hz is not None else "  no signal"))

    try:
        probe_ack([0x74, 1])
        set_cv("master", 0)
        set_cv("res", 0)
        set_cv("pwa", 0xFFFF)
        set_cv("pwb", 0)
        set_cv("volb", 0)
        set_cv("vola", 0xFFFF)
        for v in range(1, 9):
            set_cv(f"amp{v}", 0)
            set_cv(f"fil{v}", 0xFFFF)
        probe_ack([0x71, 0, 1])
        probe_ack([0x71, 3, 0])
        set_cv("osca1", 0x8000)
        set_cv("amp1", 0xFFFF)
        report("osc A1 at 0x8000, as set up:          ")
        ack([0x6E, 0])
        ack([0x6F, 0x04, 0, 0x02, 0x00])  # gain register: reference / 2, buffer gain 1
        report("gain wrong, re-send off:              ")
        report("still wrong a moment later:           ")
        ack([0x6E, 1])
        time.sleep(0.3)
        report("re-send on again, 0.3 s later:        ")
        set_cv("amp1", 0)
    finally:
        ack([0x6E, 1])
        probe_ack([0x74, 0])


# pro800 sh-waits <settle µs> <sample µs>: the S&H waits for channels written on their own (RAM only, default
# bus; 0 0 restores the defaults). For measuring the VCA path through the Move, which dac-test can't judge.
def sh_waits():
    settle, sample = (to_float(args[0]), to_float(args[1])) if len(args) == 2 else (None, None)
    if settle is None or sample is None or not 0 <= settle <= 12.7 or not 0 <= sample <= 12.7:
        fail("usage: pro800 sh-waits <settle µs 0-12.7> <sample µs 0-12.7>")
    data = [0x6C, 0, 0, 168 >> 7, 168 & 0x7F, 168 >> 7, 168 & 0x7F, 0, 0, int(settle * 10 + 0.5), int(sample * 10 + 0.5)]
    r = midi().request(data, expect=[0x7F, 0x6C], timeout=2)
    if r is None or r[HEADER_SIZE + 2] != 1:
        fail("rejected")
    show(f"S&H waits: settle {settle} µs, sample {sample} µs (0 = default)")


class Bus:
    def __init__(self, bus=0, mode=0, high=168, low=168, speed=0, sync=0):
        self.bus, self.mode, self.high, self.low, self.speed, self.sync = bus, mode, high, low, speed, sync

    # the bus's time for one 24-bit frame plus the SYNC waits, in µs
    def frame_us(self):
        if self.bus == 1:
            return 24 / (42.0 / 2 ** (self.high + 1)) + 2 * self.sync
        return 24 * (self.high + self.low) / 168 + 2 * self.sync

    def describe(self):
        if self.bus == 1:
            return f"SPI mode {self.mode}, {42 / 2 ** (self.high + 1):5.2f} MHz, GPIO speed {self.speed}"
        return (f"bit-bang mode {self.mode}, {168 / (self.high + self.low):5.2f} MHz ({self.high}/{self.low} cycles), "
                f"GPIO speed {self.speed}")


# Sweeps the CV path's speed (firmware 0.7.4): DAC80501 buses and S&H waits, each checked by oscillator A1's pitch
# through the tuning comparator against the safe default. pro800 dac-test [--bus-only | --timing-only]
# Everything is a probe: the synth goes back to the default at the end, and on any reboot.
def dac_test():
    m = midi()
    ack = probe_ack

    def configure(b, settle=0, sample=0):
        data = [0x6C, b.bus, b.mode, b.high >> 7, b.high & 0x7F, b.low >> 7, b.low & 0x7F, b.speed, b.sync, settle, sample]
        r = m.request(data, expect=[0x7F, 0x6C], timeout=2)
        if r is None:
            fail("no reply (firmware 0.7.4 or later?)")
        return r[HEADER_SIZE + 2] == 1

    def hz(cv_value):
        set_cv("osca1", cv_value)
        return median_hz(32)

    points = [0x5000, 0x8000, 0xB000]
    try:
        ack([0x74, 1])
        set_cv("master", 0)
        set_cv("res", 0)
        set_cv("pwa", 0xFFFF)
        set_cv("pwb", 0)
        set_cv("volb", 0)
        set_cv("vola", 0xFFFF)
        for v in range(1, 9):
            set_cv(f"amp{v}", 0)
            set_cv(f"fil{v}", 0xFFFF)
        set_cv("amp1", 0xFFFF)
        ack([0x71, 0, 1])
        ack([0x71, 3, 0])
        configure(Bus())
        reference = [hz(p) for p in points]
        if None in reference:
            fail("no signal from oscillator A1 on the default bus")
        show("default bus: " + ", ".join(f"{p:04X} {r:.2f} Hz" for p, r in zip(points, reference)))

        best = Bus()
        if not flag("--timing-only"):
            buses = []
            if flag("--clock"):
                # the edge of the working range, asymmetric clocks (one slew-limited edge would allow a short other
                # half), and no SYNC wait: bit-bang only
                for mode in range(2):
                    for speed in (0, 3):
                        for h, l in ((154, 154), (140, 140), (126, 126), (168, 112), (168, 84), (168, 56), (168, 28), (168, 14),
                                     (112, 168), (84, 168), (56, 168), (28, 168), (14, 168), (140, 112), (112, 140)):
                            buses.append(Bus(0, mode, h, l, speed))
                buses.append(Bus(0, 0, 168, 168, 0, 0))
            else:
                for mode in range(2):
                    for speed in (0, 3):
                        for h in (168, 112, 84, 56, 42, 28, 21, 14, 8):
                            buses.append(Bus(0, mode, h, h, speed))
                for mode in range(4):
                    for div in range(7, 0, -1):
                        buses.append(Bus(1, mode, div, 0, 2))
            show("\nbus                                                  frame   worst error")
            for b in buses:
                if not configure(b):
                    show(pad(b.describe(), 52) + "  rejected")
                    continue
                worst, silent = 0.0, False
                for i, cv_value in enumerate(points):
                    f = hz(cv_value)
                    if f is None:
                        silent = True
                        break
                    worst = max(worst, abs(1200 * math.log2(f / reference[i])))
                ok = not silent and worst < 3
                show(pad(b.describe(), 52) + f" {b.frame_us():5.1f} µs  "
                     + ("no signal" if silent else f"{worst:7.1f} cents {'ok' if ok else 'BAD'}"))
                if ok and b.frame_us() < best.frame_us():
                    best = b
            configure(Bus())
            show(f"\nfastest good bus: {best.describe()}, {best.frame_us():.1f} µs a frame (default {Bus().frame_us():.1f} µs)")
        if not flag("--bus-only"):
            # The DAC80501's previous write in the refresh order is FIL8: a settle time that's too short lets FIL8's
            # value leak into OSC1A's pitch, so measure A1 with FIL8 at both ends.
            show(f"\nS&H waits on {best.describe()}")
            show("settle  sample   per channel   A1 shift with FIL8 0 → max   A1 vs default")
            for settle, sample in ((60, 120), (40, 80), (30, 60), (20, 40), (20, 20), (10, 20), (10, 10), (5, 10)):
                if not configure(best, settle, sample):
                    continue
                set_cv("fil8", 0)
                low = hz(0x8000)
                set_cv("fil8", 0xFFFF)
                high = hz(0x8000)
                line = f"{settle / 10:4.1f} µs {sample / 10:5.1f} µs  {best.frame_us() + (settle + sample) / 10:7.1f} µs  "
                if low is not None and high is not None:
                    show(line + f"{1200 * math.log2(high / low):15.2f} cents {1200 * math.log2(high / reference[1]):18.2f} cents")
                else:
                    show(line + "no signal")
    finally:
        configure(Bus())
        ack([0x74, 0])


# Live panel events: pro800 panel [seconds]
SWITCH_NAMES = ["OSC1_SAW", "OSC1_TRI", "OSC1_SQ", "OSC2_SAW", "OSC2_TRI", "OSC2_SQ", "PW_A_B", "F_A_B", "FREQ_A",
                "LFO_SHAP", "LFO_FILTER", "FILTER", "UNISON", "HALF", "FULL", "SYNC_SEL", "MIDI_CH_0", "MIDI_CH_1",
                "MIDI_CH_2", "MIDI_CH_3", "BACKPANEL_PRESENCE", "PEDAL_DT", "SYNC_IN_A", "SYNC_IN_B"]


def pot_label(i):
    if i < 24:
        return f"pot {['U22/VMUX1', 'U21/VMUX2', 'U42/VMUX3'][i // 8]}.Y{i % 8} (#{i})"
    return "pot CUTOFF (#24)" if i == 24 else "pot NOISE (#25)"


def panel():
    seconds = to_float(args[0] if args else "120") or 120
    m = midi()
    probe_ack([0x7A, 1])
    show(f"panel report on for {int(seconds)} s: move knobs, press buttons (Ctrl-C to stop)")
    start = time.monotonic()
    end = start + seconds
    try:
        while time.monotonic() < end:
            time.sleep(0.05)
            for message in m.take_sysex():
                if len(message) <= HEADER_SIZE + 2 or not message.startswith(SYSEX_HEADER):
                    continue
                b = message[HEADER_SIZE:-1]
                kind = b[0]
                text = hexdump(b)
                if kind == 0x60 and len(b) >= 4:
                    text = f"{pot_label(b[1])}  {b[2] << 7 | b[3]}"
                elif kind == 0x61 and len(b) >= 3:
                    text = f"switch {SWITCH_NAMES[b[1]] if b[1] < len(SWITCH_NAMES) else f'#{b[1]}'} = {b[2]}"
                elif kind == 0x62 and len(b) >= 4:
                    text = f"keys byte {b[1]} = {format(b[2] << 7 | b[3], 'b').zfill(8)}"
                elif kind == 0x63 and len(b) >= 3:
                    text = f"encoder {'+' if b[2] - 64 > 0 else '-'}"
                elif kind == 0x64 and len(b) >= 3:
                    text = f"button #{b[1]} {'pressed' if b[2] == 1 else 'released'}"
                show(f"{time.monotonic() - start:6.1f}  {text}")
    except KeyboardInterrupt:
        pass
    finally:
        m.request([0x7A, 0], expect=[0x7F, 0x7A], timeout=1)


# Writes HT16K33 display RAM bytes (report mode keeps the firmware off the display): pro800 leds <addr> <value> ...
def leds():
    if len(args) < 2 or len(args) % 2:
        fail("usage: pro800 leds <address 0-15> <value 0-255 or 0x..> ...")
    probe_ack([0x7A, 1])
    for i in range(0, len(args), 2):
        a, v = parse_value(args[i]), parse_value(args[i + 1])
        if a is None or not 0 <= a < 16 or v is None or not 0 <= v < 256:
            fail(f"bad address/value {args[i]} {args[i + 1]}")
        probe_ack([0x7B, a, v >> 7, v & 0x7F])


POT_NAMES = ["AMP REL", "AMP SUS", "AMP DEC", "AMP ATT", "FIL REL", "FIL SUS", "FIL DEC", "FIL ATT", "RESONANCE",
             "FIL ENV AMT", "FREQ A", "FREQ B", "FINE", "A PW", "B PW", "A LEVEL", "GLIDE", "LFO FREQ", "LFO AMOUNT",
             "M TUNE", "M VOL", "B LEVEL", "PMOD OSC B", "PMOD FIL ENV", "CUTOFF", "NOISE"]
SWITCH_LABELS = ["Osc A Saw", "Osc A Tri", "Osc A Square", "Osc B Saw", "Osc B Tri", "Osc B Square", "LFO → PW",
                 "LFO → Freq", "Poly-mod → Freq A", "LFO Shape", "LFO → Filter", "Poly-mod → Filter", "Unison",
                 "Keytrack HALF line", "Keytrack FULL line", "Sync", "MIDI_CH_0", "MIDI_CH_1", "MIDI_CH_2", "MIDI_CH_3",
                 "BACKPANEL_PRESENCE", "PEDAL_DT", "SYNC_IN_A", "SYNC_IN_B"]


# Prints every pot (0-99) and switch as the firmware reads them.
def state():
    reply = midi().request([0x7C], expect=[0x7C], timeout=2)
    if reply is None:
        fail("no reply (firmware 0.2.0 or later?)")
    b = reply[HEADER_SIZE + 1:]
    if len(b) < 26 * 2 + 24:
        fail("short reply")
    for i in range(26):
        v = b[2 * i] << 7 | b[2 * i + 1]
        show(pad(POT_NAMES[i], 14) + f"{v * 99 // 4095:3d}")
    # latching buttons read 1 when latched down, which is off on this panel
    on = [SWITCH_LABELS[i] for i in range(16) if b[52 + i] == 0 and i not in (13, 14)]
    show("on: " + (", ".join(on) if on else "none"))
    keytrack = "Full" if b[52 + 14] == 0 else "Half" if b[52 + 13] == 0 else "Off"
    show(f"keyboard tracking: {keytrack}")


SHAPE_NAMES = ["tri", "square", "ramp-up", "ramp-down", "sine", "sh", "drift", "steps", "noise"]
LFO2_DEST_NAMES = ["pitch", "filter", "pw"]
LAYER_MODES = ["off", "split", "stack", "channel", "8ch", "mpe"]
TRIGGER_MODES = ["free", "first", "every", "voice", "one-shot", "one-shot-voice"]
CYCLE_NAMES = ["amp", "filter", "env3", "env4"]


def per_envelope(name):
    return [f"{name} {layer} {env}" for layer in (1, 2) for env in CYCLE_NAMES]


SETTING_NAMES = (["lfo-sync", "lfo-trigger", "layers", "split", "keytrack", "lfo2-sync", "lfo2-trigger", "edit-layer",
                  "lfo", "shape 1 1", "shape 1 2", "shape 2 1", "shape 2 2", "lfo2-dests 1", "lfo2-dests 2",
                  "cycle 1", "cycle 2", "cycle-sync 1 amp", "cycle-sync 1 filter", "cycle-sync 1 env3",
                  "cycle-sync 1 env4", "cycle-sync 2 amp", "cycle-sync 2 filter", "cycle-sync 2 env3",
                  "cycle-sync 2 env4", "bend", "detune", "voice 1", "voice 2", "drift", "scale", "root",
                  "lfo-delay", "lfo-fade", "lfo2-delay", "lfo2-fade", "cc-a", "cc-b", "macro 1 1", "macro 1 2", "macro 2 1",
                  "macro 2 2", "clock", "bpm", "env-up 1", "env-up 2", "dahdsr 1", "dahdsr 2", "env-curve 1", "env-curve 2"]
                 + per_envelope("env-delay") + per_envelope("env-hold") + ["layer-voices"] + per_envelope("env-count")
                 + ["env-tempo 1", "env-tempo 2", "midi-channel",
                    "osc-tune 1 a", "osc-tune 1 b", "osc-tune 2 a", "osc-tune 2 b", "pickup", "link", "lfo-cycles", "lfo2-cycles",
                    "lfo-restart", "route-both 1 (0-7)", "route-both 1 (8-15)", "route-both 2 (0-7)", "route-both 2 (8-15)",
                    "env-select", "preset-mode", "patch", "morph"])
LINK_NAMES = ["amp-env", "filter-env", "env3", "env4", "lfo1", "lfo2", "filter"]
CURVE_NAMES = ["analog", "linear", "swell"]
SCALE_NAMES = ["off", "major", "minor", "dorian", "phrygian", "lydian", "mixolydian", "harmonic-minor", "pentatonic-major",
               "pentatonic-minor", "blues", "whole-tone", "chromatic"]
ROOT_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
VOICE_MODES = ["poly", "mono", "legato"]
PRIORITIES = ["last", "low", "high"]
DIVISION_NAMES = ["8bar", "4bar", "2bar", "1bar", "1/2", "1/4.", "1/4", "1/4T", "1/8.", "1/8", "1/8T", "1/16.", "1/16",
                  "1/16T", "1/32"]
# the latching buttons (switch_t indices) the engine reports with 4C and takes with 4B
BUTTON_NAMES = {0: "a-saw", 1: "a-tri", 2: "a-square", 3: "b-saw", 4: "b-tri", 5: "b-square", 6: "lfo-pw", 7: "lfo-freq",
                8: "pmod-freq-a", 10: "lfo-filter", 11: "pmod-filter", 12: "unison", 15: "sync"}


def names_on(names, bits):
    on = [name for i, name in enumerate(names) if bits >> i & 1]
    return ",".join(on) if on else "none"


def names_to_bits(names, text, usage):
    bits = 0
    for name in text.split(","):
        if name and name != "none":
            if name not in names:
                fail(usage)
            bits |= 1 << names.index(name)
    return bits


# Settings: pro800 set <setting> <value> (see the help)
def set_setting():
    table = {"lfo-sync": (0, ["free", "clock"]), "lfo-trigger": (1, TRIGGER_MODES), "layers": (2, LAYER_MODES),
             "keytrack": (4, ["off", "half", "full"]), "lfo2-sync": (5, ["free", "clock"]), "lfo2-trigger": (6, TRIGGER_MODES),
             "lfo": (8, ["1", "2"]), "lfo-restart": (86, ["steps", "keys"])}
    usage = (f"usage: pro800 set lfo-sync|lfo2-sync free|clock | lfo-trigger|lfo2-trigger {'|'.join(TRIGGER_MODES)} | lfo-cycles|lfo2-cycles <1-99> | lfo-restart steps|keys | "
             "layers off|split|stack|channel|8ch|mpe | split <note 0-127> | layer-voices auto|<layer 1's voices 1-7> | keytrack off|half|full | lfo 1|2 | "
             "lfo-delay|lfo-fade <lfo 1|2> <0-99> | cc-a|cc-b <controller 1-127> | macro <layer 1|2> <1|2> <offset -99..99> | "
             "clock midi|internal|sync-in | bpm <30-300> | midi-channel all|<1-16> | pickup on|off | "
             f"shape <layer 1|2> <lfo 1|2> {'|'.join(SHAPE_NAMES)} | lfo2-dests <layer 1|2> none|pitch,filter,pw | "
             "cycle <layer 1|2> none|amp,filter,env3,env4 | steps <layer 1|2> <lfo 1|2> <x.x.xx.. (1-16 steps)> | "
             f"cycle-sync <layer 1|2> amp|filter|env3|env4 free|{'|'.join(DIVISION_NAMES)} | "
             "env-up|dahdsr|env-tempo <layer 1|2> none|amp,filter,env3,env4 | env-curve <layer 1|2> amp|filter|env3|env4 "
             f"{'|'.join(CURVE_NAMES)} | env-delay|env-hold <layer 1|2> amp|filter|env3|env4 <0-99> | "
             "env-count <layer 1|2> amp|filter|env3|env4 loop|<cycles 1-99> | "
             "osc-tune <layer 1|2> <a|b> semitones|octave|free|fixed | "
             f"link none|{','.join(LINK_NAMES)} | "
             f"button {'|'.join(sorted(BUTTON_NAMES.values()))} on|off")
    n = len(args)
    name = args[0] if args else None
    layer = int_in(args[1], 1, 2) if n >= 2 else None
    env = index_of(CYCLE_NAMES, args[2]) if n >= 3 else None
    setting = None
    if n == 2 and name == "morph":  # the morph target: a slot, or none (SysEx 44)
        slot = slot_number(args[1])
        target = 0 if args[1] == "none" else slot + 1 if slot is not None else -1
        if target < 0:
            fail("usage: pro800 set morph <slot 1-128|P1-P128|none>")
        probe_ack([0x44, target >> 7, target & 0x7F])
        show("morph target: " + ("none" if target == 0 else patch_label(target - 1)))
        return
    if n == 2 and name in ("lfo-cycles", "lfo2-cycles") and (v := int_in(args[1], 1, 99)) is not None:
        setting = (84 if name == "lfo-cycles" else 85, v)
    elif n == 2 and name == "split" and (v := int_in(args[1], 0, 127)) is not None:
        setting = (3, v)
    elif n == 2 and name == "bend" and (v := int_in(args[1], 1, 12)) is not None:
        setting = (25, v)
    elif n == 2 and name == "detune" and (v := int_in(args[1], 0, 50)) is not None:
        setting = (26, v)
    elif n == 2 and name == "drift" and (v := int_in(args[1], 0, 99)) is not None:
        setting = (29, v)
    elif n == 2 and name == "scale" and args[1] in SCALE_NAMES:
        setting = (30, SCALE_NAMES.index(args[1]))
    elif n == 2 and name == "root" and args[1].upper() in ROOT_NAMES:
        setting = (31, ROOT_NAMES.index(args[1].upper()))
    elif (n == 3 and name in ("lfo-delay", "lfo-fade") and (lfo := int_in(args[1], 1, 2)) is not None
          and (v := int_in(args[2], 0, 99)) is not None):
        setting = (32 + 2 * (lfo - 1) + (1 if name == "lfo-fade" else 0), v)
    elif n == 2 and name in ("cc-a", "cc-b") and (v := int_in(args[1], 1, 127)) is not None:
        setting = (36 if name == "cc-a" else 37, v)
    elif (n == 4 and name == "macro" and layer is not None and (m := int_in(args[2], 1, 2)) is not None
          and (v := int_in(args[3], -99, 99)) is not None):
        setting = (38 + 2 * (layer - 1) + (m - 1), v + 99)
    elif n == 2 and name == "clock" and args[1] in ("midi", "internal", "sync-in"):
        setting = (42, ["midi", "internal", "sync-in"].index(args[1]))
    elif n == 2 and name == "layer-voices" and (v := 0 if args[1] == "auto" else int_in(args[1], 0, 7)) is not None:
        setting = (66, v)
    elif n == 2 and name == "midi-channel" and (v := 0 if args[1] == "all" else int_in(args[1], 0, 16)) is not None:
        setting = (77, v)
    elif n == 2 and name == "pickup" and args[1] in ("off", "on"):
        setting = (82, ["off", "on"].index(args[1]))
    elif (n == 4 and name == "osc-tune" and layer is not None and args[2] in ("a", "b")
          and args[3] in ("semitones", "octave", "free", "fixed")):
        setting = (78 + (layer - 1) * 2 + ["a", "b"].index(args[2]), ["semitones", "octave", "free", "fixed"].index(args[3]))
    elif n == 2 and name == "bpm" and (v := int_in(args[1], 30, 300)) is not None:
        setting = (43, v)
    elif n == 4 and name == "voice" and layer is not None and args[2] in VOICE_MODES and args[3] in PRIORITIES:
        setting = (27 + layer - 1, VOICE_MODES.index(args[2]) | PRIORITIES.index(args[3]) << 2)
    elif n == 2 and name in table and args[1] in table[name][1]:
        setting = (table[name][0], table[name][1].index(args[1]))
    elif (n == 4 and name == "shape" and layer is not None and (lfo := int_in(args[2], 1, 2)) is not None
          and args[3] in SHAPE_NAMES):
        setting = (9 + (layer - 1) * 2 + lfo - 1, SHAPE_NAMES.index(args[3]))
    elif n == 3 and name == "lfo2-dests" and layer is not None:
        setting = (13 + layer - 1, names_to_bits(LFO2_DEST_NAMES, args[2], usage))
    elif n == 4 and name == "steps" and layer is not None and (lfo := int_in(args[2], 1, 2)) is not None:
        # x / 1 = on, . / 0 / - = off; the length is the step count (1-16)
        steps = args[3]
        if not 1 <= len(steps) <= 16 or any(c not in "x1.0-" for c in steps):
            fail(usage)
        bits = sum(1 << i for i, c in enumerate(steps) if c in "x1")
        probe_ack([0x4E, layer - 1, lfo - 1, len(steps), bits >> 14 & 0x7F, bits >> 7 & 0x7F, bits & 0x7F])
        show(f"layer {layer} LFO {lfo} steps: " + "".join("x" if bits >> i & 1 else "." for i in range(len(steps))))
        return
    elif (n == 4 and name == "cycle-sync" and layer is not None and env is not None
          and (args[3] == "free" or args[3] in DIVISION_NAMES)):
        setting = (17 + (layer - 1) * 4 + env, 0 if args[3] == "free" else DIVISION_NAMES.index(args[3]) + 1)
    elif (n == 3 and name == "button" and args[2] in ("on", "off")
          and (i := next((k for k, b in BUTTON_NAMES.items() if b == args[1]), None)) is not None):
        probe_ack([0x4B, i, 1 if args[2] == "on" else 0])
        show(f"{args[1]} {args[2]}")
        return
    elif n == 2 and name == "link":
        setting = (83, names_to_bits(LINK_NAMES, args[1], usage))
    elif n == 3 and name in ("cycle", "env-up", "dahdsr", "env-tempo") and layer is not None:
        first = {"cycle": 15, "env-up": 44, "dahdsr": 46, "env-tempo": 75}[name]
        setting = (first + layer - 1, names_to_bits(CYCLE_NAMES, args[2], usage))
    elif n == 4 and name == "env-curve" and layer is not None and env is not None and args[3] in CURVE_NAMES:
        # the other envelopes' curves stay: read them first
        reply = midi().request([0x54], expect=[0x54], timeout=2)
        if reply is None:
            fail("no reply")
        b = reply[HEADER_SIZE + 1:-1]
        i = 48 + layer - 1
        if b[0] <= i:
            fail("the firmware has no envelope curves (0.9.0 or later?)")
        bits = b[1 + 2 * i] << 7 | b[2 + 2 * i]
        setting = (i, bits & ~(3 << (2 * env)) | CURVE_NAMES.index(args[3]) << (2 * env))
    elif (n == 4 and name in ("env-delay", "env-hold") and layer is not None and env is not None
          and (v := int_in(args[3], 0, 99)) is not None):
        setting = ((50 if name == "env-delay" else 58) + (layer - 1) * 4 + env, v)
    elif (n == 4 and name == "env-count" and layer is not None and env is not None
          and (v := 0 if args[3] == "loop" else int_in(args[3], 0, 99)) is not None):
        setting = (67 + (layer - 1) * 4 + env, v)
    else:
        fail(usage)
    probe_ack([0x7D, setting[0], setting[1] >> 7, setting[1] & 0x7F])
    show(" ".join(args))


# The MIDI channel the synth listens on (1-16), or None when it takes every channel or can't say (stock firmware, or
# ours before the setting): what play, cc and bend send on without --channel
def synth_channel():
    reply = midi().request([0x54], expect=[0x54], timeout=1)
    if reply is None:
        return None
    b = reply[HEADER_SIZE + 1:-1]
    setting = 77
    if len(b) == 0 or b[0] - 4 <= setting or len(b) <= 2 + 2 * setting:
        return None
    v = b[1 + 2 * setting] << 7 | b[2 + 2 * setting]
    return v if 1 <= v <= 16 else None


def setting_text(i, v):
    if i in (0, 5):
        return at(["free", "clock"], v)
    if i in (1, 6):
        return at(TRIGGER_MODES, v)
    if i == 2:
        return at(LAYER_MODES, v)
    if i == 4:
        return at(["off", "half", "full"], v)
    if i in (7, 8):
        return str(v + 1)
    if 9 <= i <= 12:
        return at(SHAPE_NAMES, v)
    if i in (13, 14):
        return names_on(LFO2_DEST_NAMES, v)
    if i in (15, 16, 44, 45, 46, 47, 75, 76):
        return names_on(CYCLE_NAMES, v)
    if i in (48, 49):
        return ", ".join(f"{CYCLE_NAMES[e]} {at(CURVE_NAMES, v >> (2 * e) & 3, '?')}" for e in range(4))
    if 50 <= i <= 65:
        return f"{v} ({10 * (2 ** (v / 99 * 12) - 1) / 4095:.3f} s)"
    if 17 <= i <= 24:
        return "free" if v == 0 else at(DIVISION_NAMES, v - 1)
    if 67 <= i <= 74:
        return "loop (endless)" if v == 0 else f"{v} cycles"
    if i == 25:
        return f"±{v} semitones"
    if i == 26:
        return f"{v} cents"
    if i in (27, 28):
        return f"{at(VOICE_MODES, v & 3, '?')}, {at(PRIORITIES, v >> 2 & 3, '?')} note priority"
    if i == 30:
        return at(SCALE_NAMES, v)
    if i == 31:
        return at(ROOT_NAMES, v)
    if 32 <= i <= 35:
        return "off" if v == 0 else f"{v} ({0.02 * 2 ** (v * 8 / 99):.2f} s)"
    if i in (36, 37):
        return f"CC {v}"
    if 38 <= i <= 41:
        return f"{v - 99:+d}"
    if i == 42:
        return at(["midi", "internal", "sync-in"], v)
    if i == 43:
        return f"{v} BPM"
    if i == 66:
        return "automatic" if v == 0 else f"{v} + {8 - v}"
    if i == 77:
        return "all" if v == 0 else str(v)
    if 78 <= i <= 81:
        return at(["semitones", "octave", "free", "fixed"], v)
    if i == 82:
        return "off (a knob jumps)" if v == 0 else "on"
    if i == 83:
        return names_on(LINK_NAMES, v)
    if i in (84, 85):
        return "1 cycle (one shot)" if v == 1 else f"{v} cycles (one shot)"
    if i == 86:
        return "steps (a sequence's notes restart the LFOs)" if v == 0 else "keys (the keys restart them, not a sequence's steps)"
    if i == 91:
        return at(["filter and amp", "env 3", "env 4", "env 3 / env 4 (Shift)"], v)
    if i == 93:
        return slot_name(v)
    if i == 94:
        return "none" if v == 0 else slot_name(v - 1)
    return None


# pro800 settings: every setting, as the firmware has them now
def settings():
    m = midi()
    reply = m.request([0x54], expect=[0x54], timeout=2)
    if reply is None:
        fail("no reply (firmware 0.6.0 or later?)")
    b = reply[HEADER_SIZE + 1:-1]
    count, read_only = b[0], 4  # env-select, preset-mode, patch, morph: always the reply's last four
    for r in range(count):
        # the settable ones from the start, as far as both know them; the read-only ones from the end
        i = len(SETTING_NAMES) - (count - r) if r >= count - read_only else r
        if r < count - read_only and i >= len(SETTING_NAMES) - read_only:
            continue
        v = b[1 + 2 * r] << 7 | b[2 + 2 * r]
        text = setting_text(i, v)
        show(f"{pad(SETTING_NAMES[i], 20)} {v if text is None else text}")
    r = m.request([0x4C], expect=[0x4C])
    if r is not None:
        d = r[HEADER_SIZE + 1:-1]
        on = [BUTTON_NAMES[k] for k in sorted(BUTTON_NAMES) if k < len(d) and d[k] != 0]
        show(pad("buttons", 21) + (" ".join(on) if on else "none"))
    for layer in range(2):
        for lfo in range(2):
            r = m.request([0x4D, layer, lfo], expect=[0x4D, layer, lfo])
            if r is None:
                continue
            d = r[HEADER_SIZE:]
            bits = d[4] << 14 | d[5] << 7 | d[6]
            show(pad(f"steps {layer + 1} {lfo + 1}", 21) + "".join("x" if bits >> i & 1 else "." for i in range(d[3])))


# Parameter names, in the firmware's order (the knobs by hardware position, then Env 3, Env 4, LFO 2); macro1 is the
# M TUNE knob (0.9.0)
PARAM_NAMES = ["amp-rel", "amp-sus", "amp-dec", "amp-att", "fil-rel", "fil-sus", "fil-dec", "fil-att",
               "resonance", "fil-env-amt", "freq-a", "freq-b", "fine", "a-pw", "b-pw", "a-level",
               "glide", "lfo-freq", "lfo-amount", "macro1", "master-vol", "b-level", "pmod-osc-b", "pmod-fil-env",
               "cutoff", "noise",
               "env3-att", "env3-dec", "env3-sus", "env3-rel", "env4-att", "env4-dec", "env4-sus", "env4-rel",
               "lfo2-rate", "lfo2-amount"]


# pro800 params [layer]: a layer's parameters, 0-99
def params():
    layers = [1, 2] if not args else [int(a) for a in args if to_int(a) in (1, 2)]
    for layer in layers:
        reply = midi().request([0x53, layer - 1], expect=[0x53, layer - 1], timeout=2)
        if reply is None:
            fail("no reply (firmware 0.6.0 or later?)")
        b = reply[HEADER_SIZE + 2:-1]
        show(f"layer {layer}:")
        line = ""
        for i in range(min(b[0], len(PARAM_NAMES))):
            v = b[1 + 2 * i] << 7 | b[2 + 2 * i]
            line += f"{pad(PARAM_NAMES[i], 13)}{(v * 99 + 8191) // 16383:3d}   "
            if i % 4 == 3:
                show("  " + line)
                line = ""
        if line:
            show("  " + line)


# pro800 param <layer> <name> <0-99>
def param():
    layer = int_in(args[0], 1, 2) if len(args) == 3 else None
    value = to_float(args[2]) if len(args) == 3 else None
    if layer is None or args[1] not in PARAM_NAMES or value is None or not 0 <= value <= 99:
        fail("usage: pro800 param <layer 1|2> <name> <0-99>\nnames: " + " ".join(PARAM_NAMES))
    v = int(value / 99 * 16383 + 0.5)
    probe_ack([0x52, layer - 1, PARAM_NAMES.index(args[1]), v >> 7, v & 0x7F])
    show(f"layer {layer} {args[1]} = {args[2]}")


ARP_MODE_NAMES = ["off", "up", "down", "updown", "random", "played"]


# pro800 seq set key=value…: the sequence's header, read first, the given keys changed (SysEx 47)
def seq_set():
    h = midi().request([0x48], expect=[0x48], timeout=2)
    if h is None:
        fail("no reply (firmware 0.7.0 or later?)")
    d = h[HEADER_SIZE + 1:]
    length, division, bpm, flags = d[0], d[2], d[3] << 7 | d[4], d[5]
    chord = flags >> 5 & 3
    lanes = d[1] - chord  # automation lanes; the header counts the chord lanes too
    swing, smooth, arp = d[6], d[7], d[9]
    usage = ("usage: pro800 seq set on=0|1 length=1-64 step=<division> clock=0|1 bpm=30-255 swing=0-75 once=0|1 "
             f"keys=both|1|2 arp={'|'.join(ARP_MODE_NAMES)} lanes=0-4 chord=0-2 smooth=none|A..D (lanes + chord: at most 4)")

    def number(text, low, high):
        v = int_in(text, low, high)
        if v is None:
            fail(usage)
        return v

    for kv in args[1:]:
        if "=" not in kv:
            fail(usage)
        key, value = kv.split("=", 1)
        if key in ("on", "clock", "once"):
            mask = {"on": 1, "clock": 2, "once": 4}[key]
            flags = flags | mask if number(value, 0, 1) == 1 else flags & ~mask
        elif key == "length":
            length = number(value, 1, 64)
        elif key == "step":
            if value not in DIVISION_NAMES:
                fail(usage)
            division = DIVISION_NAMES.index(value)
        elif key == "bpm":
            bpm = number(value, 30, 255)
        elif key == "swing":
            swing = number(value, 0, 75)
        elif key == "keys":
            if value not in ("both", "1", "2"):
                fail(usage)
            flags = flags & ~(3 << 3) | ["both", "1", "2"].index(value) << 3
        elif key == "arp":
            if value not in ARP_MODE_NAMES:
                fail(usage)
            arp = ARP_MODE_NAMES.index(value)
        elif key == "lanes":
            lanes = number(value, 0, 4)
        elif key == "chord":
            chord = number(value, 0, 2)
        elif key == "smooth":
            smooth = 0
            for ch in value.upper():
                if ch not in "NOE":
                    if ch not in "ABCD":
                        fail(usage)
                    smooth |= 1 << "ABCD".index(ch)
        else:
            fail(usage)
    if lanes + chord > 4:
        fail(usage)
    flags = flags & ~(3 << 5) | chord << 5
    probe_ack([0x47, length, lanes + chord, division, bpm >> 7, bpm & 0x7F, flags & 0x7F, swing, smooth, arp])
    show(f"sequence: {'on' if flags & 1 else 'off'}, {length} steps of {DIVISION_NAMES[division]}, "
         + ("MIDI clock" if flags & 2 else f"{bpm} BPM") + f", {lanes} lanes" + (f", {chord + 1}-note chords" if chord > 0 else ""))


# pro800 seq step <n> <note|.> <gate|tie> [v<vel>] [p<prob 1-8>] [x<ratchets 1-4>] [a<-99..99>] … [d<..>]
# [n2<+-semitones>] [n3<..>] [h<1-7>] (SysEx 45; n2 and n3 are the chord notes, from the step's note, 0 = none; h: the
# notes that hold past the gate into the next step, 1 own note + 2 Note 2 + 4 Note 3)
def seq_step():
    usage = "usage: pro800 seq step <1-64> <+offset|-offset|.> <gate 1-99|tie> [v0-127] [p1-8] [x1-4] [a-99..99] [b..] [c..] [d..] [n2-63..63] [n3..] [h1-7]"
    a = args[1:]
    n = int_in(a[0], 1, 64) if len(a) >= 2 else None
    if n is None:
        fail(usage)
    on, note, gate_value, velocity, probability, ratchets = 1, 64, 50, 0, 8, 1
    lanes = [64, 64, 64, 64]
    options, holds = 2, 0
    if a[1] == ".":
        on = 0
        if len(a) >= 3 and (a[2] == "tie" or to_int(a[2]) is not None):  # a rest may still carry its gate
            options = 3
    else:
        off = int_in(a[1], -64, 63)
        if off is None or len(a) < 3:
            fail(usage)
        note = 64 + off
        if a[2] == "tie":
            gate_value = 100
        else:
            gate_value = int_in(a[2], 1, 99)
            if gate_value is None:
                fail(usage)
        options = 3
    for opt in a[options:]:
        if opt.startswith(("n2", "n3")):  # a chord note: its lane depends on the header
            v = int_in(opt[2:], -63, 63)
            if v is None:
                fail(usage)
            h = midi().request([0x48], expect=[0x48], timeout=2)
            if h is None:
                fail("no reply")
            d = h[HEADER_SIZE + 1:]
            chord = d[5] >> 5 & 3
            which = 0 if opt.startswith("n2") else 1
            if which >= chord:
                fail(f"the sequence has {chord} chord lane(s): seq set chord={which + 1} first")
            lanes[d[1] - chord + which] = 64 + v
            continue
        first, v = opt[:1], to_int(opt[1:])
        if v is None:
            fail(usage)
        if first == "v" and 0 <= v <= 127:
            velocity = v
        elif first == "p" and 1 <= v <= 8:
            probability = v
        elif first == "x" and 1 <= v <= 4:
            ratchets = v
        elif first == "h" and 0 <= v <= 7:
            holds = v
        elif first in "abcd" and -99 <= v <= 99:
            lanes["abcd".index(first)] = 64 + rounded(v * 63 / 99)
        else:
            fail(usage)
    extra = (8 - probability) | (ratchets - 1) << 3
    probe_ack([0x45, n - 1, 1, note, velocity, gate_value, on | holds << 1, extra] + lanes)


# pro800 seq: the loaded patch's step sequence
def show_sequence():
    h = midi().request([0x48], expect=[0x48], timeout=2)
    if h is None:
        fail("no reply (firmware 0.7.0 or later?)")
    d = h[HEADER_SIZE + 1:]
    length, flags = d[0], d[5]
    chord = flags >> 5 & 3
    lanes = d[1] - chord
    arp = "" if len(d) <= 10 or d[9] == 0 else ", arp " + at(["off", "up", "down", "up-down", "random", "played"], d[9], "?")
    show(f"{'on' if flags & 1 else 'off'}, {length} steps of {at(DIVISION_NAMES, d[2], '?')}, "
         + ("MIDI clock" if flags & 2 else f"{d[3] << 7 | d[4]} BPM") + f", swing {d[6]} %, "
         + ("once per key" if flags & 4 else "loops") + ", keys of " + ["both layers", "layer 1", "layer 2", "?"][flags >> 3 & 3]
         + f", {lanes} lane{'' if lanes == 1 else 's'}" + (f", {chord + 1}-note chords" if chord > 0 else "")
         + f" (max {d[8]} steps)" + arp)
    notes, gates, extras, hold_row = [], [], [], []
    lane_rows = [[] for _ in range(lanes)]
    chord_rows = [[] for _ in range(chord)]
    for first in range(0, length, 8):
        r = midi().request([0x46, first, 8], expect=[0x46, first, 8], timeout=2)
        if r is None:
            fail("no reply")
        x = r[HEADER_SIZE + 3:]
        for k in range(8):
            if first + k >= length:
                break
            s = x[9 * k:9 * k + 9]  # note, velocity, gate, on, extra, lanes A-D
            offset = s[0] - 64
            notes.append("  ." if s[3] == 0 else f"{offset:+3d}".replace("+0", " 0"))
            gates.append("  ." if s[3] == 0 else "tie" if s[2] >= 100 else f"{s[2]:3d}")
            hold_row.append("  ." if s[3] >> 1 == 0 else f"{s[3] >> 1:3d}")
            probability, ratchets = 8 - (s[4] & 7), (s[4] >> 3 & 3) + 1
            extras.append("  ." if probability == 8 and ratchets == 1 else f" x{ratchets}" if ratchets > 1 and probability == 8
                          else f"{probability}x{ratchets}" if ratchets > 1 else f" {probability}8")
            for l in range(lanes):
                lane_rows[l].append(f"{rounded((s[5 + l] - 64) / 63 * 99):+3d}")
            for c in range(chord):
                v = s[5 + lanes + c] - 64
                chord_rows[c].append("  ." if v == 0 or s[3] == 0 else f"{v:+3d}")
    show("note  " + " ".join(notes))
    for c in range(chord):
        show(f"n{c + 2}    " + " ".join(chord_rows[c]))
    show("gate  " + " ".join(gates))
    if any(x != "  ." for x in hold_row):
        show("hold  " + " ".join(hold_row) + "   (1 own note, 2 Note 2, 4 Note 3)")
    if any(x != "  ." for x in extras):
        show("prob  " + " ".join(extras) + "   (n8 = n in 8, xN = ratchets)")
    for l in range(lanes):
        show(f"seq {'ABCD'[l]} " + " ".join(lane_rows[l]))


def seq():
    {"set": seq_set, "step": seq_step}.get(args[0] if args else None, show_sequence)()


# pro800 factory [import <f> <slot> [--force]]
def factory():
    m = midi()
    if args and args[0] == "import":
        force = flag("--force")
        f = int_in(args[1], 0, 99) if len(args) >= 3 else None
        n = slot_number(args[2]) if len(args) >= 3 else None
        if f is None or n is None:
            fail("usage: pro800 factory import <0-99> <slot> [--force]")
        data = [0x51, f, n >> 7, n & 0x7F] + ([1] if force else [])
        reply = m.request(data, expect=[0x51], timeout=3)
        if reply is None:
            fail("no reply (firmware 0.6.0 or later?)")
        status = reply[HEADER_SIZE + 1]
        show(at([f"copied factory {f} to {slot_name(n)}", f"{slot_name(n)} isn't empty (--force to replace it)",
                 f"factory patch {f} isn't there", "write error"], status, f"status {status}"))
        return
    for f in range(100):
        reply = m.request([0x50, f], expect=[0x50, f], timeout=2)
        if reply is None:
            fail("no reply (firmware 0.6.0 or later?)")
        b = reply[HEADER_SIZE + 2:-1]
        show(f"{f:2d}  " + (b[1:].decode("utf-8", "replace") if b[:1] == b"\x01" else "(none)"))


# The layer the per-layer knobs edit: pro800 layer 1|2
def layer_command():
    n = int_in(args[0], 1, 2) if args else None
    if n is None:
        fail("usage: pro800 layer 1|2")
    probe_ack([0x7E, n - 1])
    show(f"editing layer {n}")


def wait_until(moment):
    while True:
        left = moment - time.perf_counter()
        if left <= 0:
            return
        time.sleep(left - 0.002 if left > 0.003 else 0)


# Sends MIDI clock: Start, then 24 timing clocks per quarter note. The macOS tool schedules them with CoreMIDI
# timestamps; here they go out when due, so with the jitter of a sleeping process (about a millisecond).
def clock_out():
    bpm = to_float(args[0]) if args else None
    if bpm is None or not 20 <= bpm <= 300:
        fail("usage: pro800 clock <bpm 20-300> [seconds]")
    seconds = to_float(args[1]) if len(args) > 1 else 10
    seconds = 10 if seconds is None else seconds
    m = midi()
    start = time.perf_counter() + 0.05
    tick = 60.0 / bpm / 24
    show(f"clock at {bpm:.1f} BPM for {seconds:.0f} s")
    wait_until(start)
    m.send([0xFA])
    for sent in range(int(seconds / tick)):
        wait_until(start + sent * tick)
        m.send([0xF8])
    m.send([0xFC])
    time.sleep(0.05)


# Reads the patch EEPROM (read-only): pro800 eeprom <file> [start] [length]
def eeprom():
    if not args:
        fail("usage: pro800 eeprom <file> [start] [length]")
    path = args[0]
    start = (parse_value(args[1]) or 0) if len(args) > 1 else 0
    length = (parse_value(args[2]) if parse_value(args[2]) is not None else 0x20000) if len(args) > 2 else 0x20000
    m = midi()
    data = bytearray()
    address = start
    status = -1
    began = time.monotonic()
    while address < start + length:
        n = min(48, start + length - address)
        req = [0x79, address >> 14 & 0x7F, address >> 7 & 0x7F, address & 0x7F, n]
        reply = m.request(req, expect=req, timeout=2)
        if reply is None:
            fail(f"no reply at 0x{address:x} (firmware 0.3.2 or later?)")
        b = reply[HEADER_SIZE + 5:-1]
        if len(b) != 2 + 2 * n:
            fail(f"short reply at 0x{address:x}")
        status = b[0] << 4 | b[1]
        for i in range(n):
            data.append(b[2 + 2 * i] << 4 | b[3 + 2 * i])
        address += n
        if (address - start) % 0x2000 < 48:
            show(f"\r{address - start} / {length} bytes", end="")
    with open(path, "wb") as f:
        f.write(data)
    show(f"\r{len(data)} bytes in {time.monotonic() - began:.1f} s → {path} (status register 0x{status:02X})")


def slot_number(text):
    t = text.upper()
    n = int_in(t, 0, 127)
    if n is not None:
        return n
    if t.startswith("P"):
        p = int_in(t[1:], 1, 128)
        if p is not None:
            return p - 1  # "P7": patch 7 = slot 6
    return None


def slot_name(n):
    return patch_label(n)


# Writes a whole EEPROM image and reads it back: pro800 eeprom-write <file> --yes (e.g. the stock image)
def eeprom_write():
    global args
    confirmed = flag("--yes")
    if not confirmed or not args:
        fail("usage: pro800 eeprom-write <file> --yes")
    path = args[0]
    try:
        with open(path, "rb") as f:
            image = f.read()
    except OSError:
        image = b""
    if len(image) != 0x20000:
        fail(f"{path} isn't a 128 kB image")
    m = midi()
    address = 0
    while address < len(image):
        n = min(48, len(image) - address)
        data = [0x5A, address >> 14 & 0x7F, address >> 7 & 0x7F, address & 0x7F, n]
        for b in image[address:address + n]:
            data += [b >> 4, b & 0x0F]
        if m.request(data, expect=[0x7F, 0x5A], timeout=2) is None:
            fail(f"write failed at 0x{address:x}")
        address += n
        if address % 0x2000 < 48:
            show(f"\rwritten {address} / {len(image)}", end="")
    show("\nverifying")
    check_path = os.path.join(tempfile.gettempdir(), "pro800-verify.bin")
    args = [check_path]
    eeprom()
    with open(check_path, "rb") as f:
        back = f.read()
    show(f"verified: the EEPROM matches {path}; power-cycle the synth" if back == image else "MISMATCH after writing")


# Empties every slot: pro800 format --yes
def format_patches():
    if not flag("--yes"):
        fail("usage: pro800 format --yes (wipes every patch slot and marks the EEPROM as ours)")
    show("formatting...")
    reply = midi().request([0x5B], expect=[0x5B], timeout=60)
    if reply is None:
        fail("no reply (firmware 0.8.0 or later?)")
    b = reply[HEADER_SIZE + 1:]
    show(f"{b[1]} empty slots" if b[2] == 0 else "format failed")


# pro800 patch load|save|clear <slot>  (slot 0-127, or P1-P128)
def patch_command():
    if len(args) >= 3 and args[0] == "rename" and (n := slot_number(args[1])) is not None:
        name = bytes(b for b in " ".join(args[2:]).encode("utf-8") if 0x20 <= b < 0x7F)[:16]
        probe_ack([0x59, n >> 7, n & 0x7F] + list(name))
        show(f"renamed {slot_name(n)} to {name.decode()}")
        return
    if args == ["init"]:  # the basic sound PERF loads
        if midi().request([0x5F], expect=[0x7F, 0x5F], timeout=3) is None:
            fail("no reply")
        show("loaded the init sound")
        return
    n = slot_number(args[1]) if len(args) == 2 and args[0] in ("load", "save", "clear") else None
    if n is None:
        fail("usage: pro800 patch load|save|clear <slot 0-127 or P1-P128>  or  pro800 patch init")
    if args[0] == "clear":  # an empty slot is 256 zero bytes
        probe_ack([0x4F, n >> 7, n & 0x7F])
        show(f"cleared {slot_name(n)}")
        return
    cmd = 0x5C if args[0] == "load" else 0x5D
    if midi().request([cmd, n >> 7, n & 0x7F], expect=[0x7F, cmd], timeout=3) is None:
        fail(f"{args[0]} {slot_name(n)} failed (empty slot?)")
    show(f"{'loaded' if args[0] == 'load' else 'saved'} {slot_name(n)}")


# Lists the patch slots: pro800 patches [--all]
def patches():
    show_all = flag("--all")
    kinds = ["empty", "ours", "STOCK", "corrupt"]
    counts = [0, 0, 0, 0]
    for n in range(128):
        reply = midi().request([0x5E, n >> 7, n & 0x7F], expect=[0x5E, n >> 7, n & 0x7F], timeout=2)
        if reply is None:
            fail("no reply (firmware 0.4.0 or later?)")
        b = reply[HEADER_SIZE + 3:-1]
        kind = b[0]
        counts[kind] += 1
        if kind != 0 or show_all:
            show(f"{slot_name(n)}  {pad(kinds[kind], 7)} {b[1:].decode('utf-8', 'replace')}")
    show(f"{counts[1]} ours, {counts[2]} stock, {counts[0]} empty, {counts[3]} corrupt")


SOURCE_NAMES = ["", "lfo", "filter-env", "amp-env", "velocity", "mod-wheel", "aftertouch", "env3", "env4", "random", "note",
                "lfo2", "seq-a", "seq-b", "seq-c", "seq-d", "lfo-uni", "lfo2-uni", "gate", "timbre",
                "bend", "pedal", "cc-a", "cc-b", "voice", "fixed", "macro1", "macro2"]
DEST_NAMES = ["pitch-a", "pitch-b", "pitch", "cutoff", "filter-env-amount", "pmod-env", "lfo-amount", "lfo-to-pitch",
              "lfo-to-filter", "vca", "a-level", "b-level", "noise", "resonance", "a-pw", "b-pw", "pmod-osc-b", "lfo-to-pw",
              "lfo-rate", "lfo2-amount", "lfo2-rate", "lfo2-to-pitch", "lfo2-to-filter", "lfo2-to-pw", "morph",
              "amp-att", "amp-dec", "amp-sus", "amp-rel", "fil-att", "fil-dec", "fil-sus", "fil-rel",
              "env3-att", "env3-dec", "env3-sus", "env3-rel", "env4-att", "env4-dec", "env4-sus", "env4-rel",
              "glide", "detune", "volume", "macro1", "macro2"]


def source_and_dest():  # None unless args[1] and args[2] name a source and a destination
    if len(args) != 4 or not args[1] or args[1] not in SOURCE_NAMES or args[2] not in DEST_NAMES:
        return None
    return SOURCE_NAMES.index(args[1]), DEST_NAMES.index(args[2])


# pro800 route <layer 1|2> <source> <dest> <depth -99..99>  (0 removes)
def route():
    names = source_and_dest()
    layer = int_in(args[0], 1, 2) if len(args) == 4 else None
    depth = int_in(args[3], -99, 99) if len(args) == 4 else None
    if names is None or layer is None or depth is None:
        fail("usage: pro800 route <layer 1|2> <source> <destination> <depth -99..99>\nsources: "
             + " ".join(SOURCE_NAMES[1:]) + "\ndestinations: " + " ".join(DEST_NAMES))
    d = depth + 99
    probe_ack([0x56, layer - 1, names[0], names[1], d >> 7, d & 0x7F])
    show(f"layer {layer}: {args[1]} → {args[2]} = {depth}")


# pro800 route-both <layer 1|2> <source> <dest> on|off: the route plays on the other layer too (settings 87-90: a bit per slot)
def route_both():
    names = source_and_dest()
    layer = int_in(args[0], 1, 2) if len(args) == 4 else None
    if names is None or layer is None or args[3] not in ("on", "off"):
        fail("usage: pro800 route-both <layer 1|2> <source> <destination> on|off")
    reply = midi().request([0x57, layer - 1], expect=[0x57, layer - 1], timeout=2)
    if reply is None:
        fail("no reply")
    b = reply[HEADER_SIZE + 2:-1]
    slot = next((i // 4 for i in range(0, len(b) - 3, 4) if b[i] == names[0] and b[i + 1] == names[1]), None)
    if slot is None:
        fail(f"layer {layer} has no route {args[1]} → {args[2]}")
    masks = route_both_masks()
    if masks is None:
        fail("this firmware hasn't got both-layers routes (0.9.3)")
    setting = 87 + 2 * (layer - 1) + slot // 8
    part, bit = masks[layer - 1] >> (8 * (slot // 8)) & 0xFF, 1 << (slot % 8)
    v = part | bit if args[3] == "on" else part & ~bit
    probe_ack([0x7D, setting, v >> 7, v & 0x7F])
    show(f"layer {layer}: {args[1]} → {args[2]} on both layers {args[3]}")


# Each layer's both-layers routes, a bit per slot; None from firmware without them (fewer settings than 0.9.3's 95)
def route_both_masks():
    reply = midi().request([0x54], expect=[0x54], timeout=2)
    if reply is None:
        return None
    b = reply[HEADER_SIZE + 1:-1]
    if b[0] < 95:
        return None
    value = lambda i: b[1 + 2 * i] << 7 | b[2 + 2 * i]
    return [value(87) | value(88) << 8, value(89) | value(90) << 8]


# pro800 routes: both layers' mod routes
def routes():
    both = route_both_masks() or [0, 0]
    for layer in range(2):
        reply = midi().request([0x57, layer], expect=[0x57, layer], timeout=2)
        if reply is None:
            fail("no reply (firmware 0.5.0 or later?)")
        b = reply[HEADER_SIZE + 2:-1]
        lines = []
        for i in range(0, len(b) - 3, 4):
            if b[i] != 0 and b[i] < len(SOURCE_NAMES) and b[i + 1] < len(DEST_NAMES):
                lines.append(f"  {SOURCE_NAMES[b[i]]} → {DEST_NAMES[b[i + 1]]}: {(b[i + 2] << 7 | b[i + 3]) - 99}"
                             + (" (both layers)" if both[layer] >> (i // 4) & 1 else ""))
        show(f"layer {layer + 1}: " + ("\n" + "\n".join(lines) if lines else "no routes"))


# pro800 sysex: for scripts. Requests from stdin, one per line ("<request hex> [; <reply prefix hex>]", the bytes after
# the PRO-800 header; the prefix defaults to the request's command byte), each reply on stdout as hex from the command
# byte on, without F7, or "none"; all over one MIDI connection. Sends whatever it's given: read-only only if the
# requests are.
def sysex_pipe():
    def hex_bytes(text):
        out = []
        for word in text.split():
            try:
                out.append(int(word, 16))
            except ValueError:
                pass
        return [b for b in out if 0 <= b <= 255]

    m = midi()
    for line in sys.stdin:
        parts = [hex_bytes(part) for part in line.split(";", 1)]
        if not parts[0]:
            show("none")
            continue
        expect = parts[1] if len(parts) > 1 and parts[1] else parts[0][:1]
        reply = m.request(parts[0], expect=expect, timeout=5)
        if reply is not None and len(reply) > HEADER_SIZE + 1:
            show(" ".join(f"{b:02x}" for b in reply[HEADER_SIZE:-1]))
        else:
            show("none")


# pro800 engine: the engine-state probe (55), read-only
def engine():
    reply = midi().request([0x55], expect=[0x55], timeout=2)
    if reply is None:
        fail("no reply (needs firmware 0.9.1 or later)")
    b = reply[HEADER_SIZE + 1:-1]
    if len(b) < 53:
        fail(f"short reply ({len(b)} bytes)")
    stages = ["idle", "attack", "decay", "release", "hold", "rest", "delay"]
    for l in range(2):
        count = b[5 * l]
        notes = " ".join(str(n) for n in b[5 * l + 1:5 * l + 1 + min(count, 4)])
        show(f"layer {l + 1} held keys: {count}" + (f" ({notes})" if notes else ""))
    for v in range(8):
        g, a, f = b[10 + 3 * v], b[11 + 3 * v], b[12 + 3 * v]
        show(f"voice {v + 1}: gate {g}, amp {at(stages, a, a)}, filter {at(stages, f, f)}")
    kt = ["off", "half", "full"]
    show(f"keytrack {at(kt, b[34], '?')}, switch last seen {'unknown' if b[35] == 0 else at(kt, b[35] - 1, '?')}, now {at(kt, b[36], '?')}")
    for s in range(16):
        x = b[37 + s]
        show(f"switch {at(SWITCH_NAMES, s, s)}: " + (f"patch holds it, {x >> 1 & 1}" if x & 1 else "follows the panel")
             + f", panel {x >> 2 & 1}")
    if len(b) < 93:  # the LFOs' phases: 0.9.3
        return
    phase = lambda p: "stopped" if p >= 100 else f"{p}%"
    for l in range(4):
        start = 53 + 10 * l
        show(f"LFO {l // 2 + 1} layer {l % 2 + 1}: shared {phase(b[start])}, cycles done {b[start + 1]}; voices "
             + " ".join(phase(p) for p in b[start + 2:start + 10]))


# pro800 timing: how long the engine, the CV refresh and a main-loop pass take
def timing():
    m = midi()
    m.request([0x58], expect=[0x58], timeout=2)  # resets the longest pass
    time.sleep(2)
    reply = m.request([0x58], expect=[0x58], timeout=2)
    if reply is None:
        fail("no reply (firmware 0.5.0 or later?)")
    b = reply[HEADER_SIZE + 1:]
    v = [b[3 * i] << 14 | b[3 * i + 1] << 7 | b[3 * i + 2] for i in range(min(8, (len(b) - 1) // 3))]
    show(f"engine {v[0]} µs, CV refresh {v[1]} µs" + (f" (of which the four ticks {v[7]} µs)" if len(v) >= 8 else "")
         + f", main-loop pass {v[2]} µs (longest in 2 s: {v[3]} µs)")
    if len(v) >= 6:
        show(f"panel poll {v[4]} µs, display {v[5]} µs (last pass)")
    if len(v) >= 7 and v[6] > 0:
        show(f"{v[6]} passes in the last second: {1000000 // v[6]} µs per pass on average, modulation {v[6]} times a second")


def channel_option(default):
    return ((to_int(option("--channel") or "") or synth_channel() or default) - 1) & 0x0F


# pro800 cc <controller> <value> [--channel N]
def cc():
    channel = channel_option(1)
    num = int_in(args[0], 0, 127) if len(args) == 2 else None
    value = int_in(args[1], 0, 127) if len(args) == 2 else None
    if num is None or value is None:
        fail("usage: pro800 cc <controller 0-127> <value 0-127> [--channel N]")
    midi().send([0xB0 | channel, num, value])


# pro800 bend <0-16383> [--channel N]: pitch bend (8192 = centre)
def bend():
    channel = channel_option(1)
    value = int_in(args[0], 0, 16383) if len(args) == 1 else None
    if value is None:
        fail("usage: pro800 bend <0-16383> [--channel N]  (8192 = centre)")
    midi().send([0xE0 | channel, value & 0x7F, value >> 7])


# MARK: - Bootloader


def update_mode():
    midi().send(SYSEX_HEADER + bytes([0x03, 0x30, 0xF7]))
    show("sent the update-mode command; the PRO-800 restarts into its bootloader")


# Splits a .syx file into messages and checks it is a PRO-800 update made by PRO800_update_gen.
def update_messages(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        fail(f"can't read {path}")
    messages = []
    current = bytearray()
    for b in data:
        if b == 0xF0:
            if current:
                fail(f"{path}: F0 inside a message")
            current = bytearray([b])
        elif not current:
            fail(f"{path}: data outside a SysEx message")
        else:
            current.append(b)
            if b == 0xF7:
                messages.append(bytes(current))
                current = bytearray()
            elif b & 0x80:
                fail(f"{path}: byte {b:02X} inside SysEx")
    if current:
        fail(f"{path}: last message has no F7")
    update_header = SYSEX_HEADER + b"\x0A"
    if not messages or not messages[0].startswith(update_header):
        fail(f"{path}: doesn't start with a PRO-800 update header ({hexdump(update_header)})")
    if not all(m.startswith(SYSEX_HEADER + b"\x0B") for m in messages[1:]):
        fail(f"{path}: expected only update data messages (0B) after the header")
    return messages


def flash():
    rate = to_float(option("--rate"))
    rate = 3000.0 if rate is None else rate  # bytes/s; the bootloader takes up to 9640
    erase_wait = to_float(option("--erase-wait"))
    erase_wait = 3.0 if erase_wait is None else erase_wait
    confirmed = flag("--yes")
    dry_run = flag("--dry-run")
    require_ok = flag("--require-ok")  # for `install`: exit 1 unless the bootloader confirms
    if not args:
        fail("usage: pro800 flash <file.syx> --yes [--rate B/s] [--erase-wait S] [--dry-run]")
    path = args[0]
    if not 0 < rate <= 9000:
        fail("--rate must be 1-9000 bytes/s")

    messages = update_messages(path)
    total = sum(len(m) for m in messages)
    seconds = total / rate + erase_wait
    show(f"{path}: {len(messages)} messages, {total} bytes, about {math.ceil(seconds)} s at {int(rate)} B/s")
    if dry_run:
        return
    if not confirmed:
        fail("not sent. The PRO-800 must already be in update mode (hold buttons 0 and 7 at power-up, or run\n"
             "`pro800 update-mode`). Add --yes to send. If the update fails, the synth stays in update mode\n"
             "and a stock firmware .syx brings it back.")

    m = midi()
    m.take_sysex()
    last = b""
    for i, message in enumerate(messages):
        m.send(message)
        # the header comes first; give the bootloader time to erase the application flash
        time.sleep(len(message) / rate + (erase_wait if i == 0 else 0))
        for reply in m.take_sysex():
            show(f"\nreply after message {i + 1}: {hexdump(reply)}")
            last = reply
        show(f"\rsent {i + 1}/{len(messages)}", end="")
    time.sleep(1)
    for reply in m.take_sysex():
        show(f"\nreply: {hexdump(reply)}")
        last = reply
    show("\ndone. If the synth doesn't restart by itself, power-cycle it.")
    # the bootloader's OK after the last block
    if require_ok and last != SYSEX_HEADER + bytes([0x01, 0x00, 0x00, 0xF7]):
        sys.exit(1)


# The synth on USB: 1397:125F running a firmware, 1397:1260 in the bootloader. The bootloader answers the version
# request too (with the version of the last update), so only this tells the two apart.
USB_VENDOR, USB_FIRMWARE, USB_BOOTLOADER = 0x1397, 0x125F, 0x1260


def usb_device_present(product):
    if sys.platform == "win32":
        flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
        for _ in range(5):  # the list can grow between the two calls
            size = wintypes.ULONG()
            if cfgmgr32.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), "USB", flags) != 0:
                break
            ids = ctypes.create_unicode_buffer(size.value)
            result = cfgmgr32.CM_Get_Device_ID_ListW("USB", ids, size.value, flags)
            if result == CR_BUFFER_SMALL:
                continue
            wanted = f"USB\\VID_{USB_VENDOR:04X}&PID_{product:04X}"
            return result == 0 and any(i.upper().startswith(wanted) for i in ids[:].split("\0"))
        return False
    if sys.platform == "darwin":
        listing = subprocess.run(["ioreg", "-r", "-c", "IOUSBHostDevice", "-l", "-w0"], capture_output=True, text=True).stdout
        return any(f'"idVendor" = {USB_VENDOR}' in device and f'"idProduct" = {product}' in device
                   for device in listing.split("+-o"))
    fail("install knows the USB devices of Windows and macOS only: run update-mode, then flash")


# Runs this tool as a child process: each finds the ports afresh, where a long-running one may keep a stale list while
# the synth re-enumerates (CoreMIDI does; WinMM's is not documented), and none holds the port between steps.
def run_self(*command, capture=True, timeout=None):
    try:
        return subprocess.run([sys.executable, os.path.abspath(__file__), "--port", port_name, *command],
                              capture_output=capture, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def wait_for(seconds, condition):
    end = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > end:
            return False
        time.sleep(0.25)
    return True


def running_version():
    result = run_self("version", timeout=10)
    if result is None or result.returncode != 0 or not result.stdout.startswith("firmware "):
        return None
    return result.stdout.split()[1]


# update-mode, flash, then wait until the new firmware answers the version request
def install():
    if not flag("--yes") or not args:
        fail("usage: pro800 install <file.syx> --yes (switches to update mode, flashes, waits)")
    path = args[0]
    messages = update_messages(path)  # check the file before touching the synth
    if len(messages[0]) <= 44:
        fail(f"{path}: header too short to hold a version")
    expected = ".".join(str(b) for b in messages[0][42:45])  # the version PRO800_update_gen packed into the header

    if usb_device_present(USB_BOOTLOADER):
        show("already in the bootloader")
    else:
        if not usb_device_present(USB_FIRMWARE):
            fail("no PRO-800 on USB")
        show("update mode requested; waiting for the bootloader")
        for _ in range(3):  # the stock firmware sometimes misses a SysEx message
            run_self("update-mode", timeout=10)
            if wait_for(6, lambda: usb_device_present(USB_BOOTLOADER)):
                break
        else:
            fail("the synth didn't enter the bootloader, so nothing was flashed. Switch it off, hold buttons 0 and 7, switch it on, and run this again.")
    # the port can go and come back right after the device appears: wait until it stays
    steady = [None]

    def port_steady():
        result = run_self("port-present", timeout=10)
        if result is None or result.returncode != 0:
            steady[0] = None
            return False
        if steady[0] is None:
            steady[0] = time.monotonic()
        return time.monotonic() - steady[0] >= 3

    if not wait_for(30, port_steady):
        fail("the bootloader's MIDI port didn't appear")
    if run_self("flash", path, "--yes", "--require-ok", capture=False).returncode != 0:
        fail("the bootloader didn't confirm the update. Quit anything else using MIDI (SynthTribe, a web page in Chrome), switch the synth off and on, and run this again with 0 and 7 held if it comes up on the old firmware.")
    # a first boot after an update also autotunes (about 30 s) before it answers
    show("waiting for the new firmware (it tunes first, about 30 s)", end="")
    for _ in range(75):
        time.sleep(1)
        show(".", end="")
        if not usb_device_present(USB_FIRMWARE) or running_version() is None:
            continue
        # the port can be set up once more shortly after it first appears: answer again after a pause
        time.sleep(3)
        running = running_version()
        if running is None:
            continue
        if running != expected:
            fail(f"\nthe synth runs firmware {running}, not the {expected} in {path}")
        show(f"\nrunning firmware {running}")
        return
    fail("\nthe synth is still in the bootloader. Switch it off and on, and run this again if it stays on the display's boot screen."
         if usb_device_present(USB_BOOTLOADER) else "\nthe firmware didn't answer within 75 s")


# Exit 0 when the port is there, for `install`: pro800 port-present
def port_present():
    sources, destinations = port_names()
    if find_port(sources, port_name) is None or find_port(destinations, port_name) is None:
        sys.exit(1)


# MARK: - Main

COMMANDS = {
    "ports": ports, "version": version, "listen": listen, "backup": backup, "play": play, "record": no_audio,
    "direct": direct, "cv": cv, "gate": gate, "measure": measure, "tune": tune, "tuning": tuning, "check": check,
    "dac-test": dac_test, "sh-waits": sh_waits, "dac-heal": dac_heal, "panel": panel, "state": state, "set": set_setting,
    "settings": settings, "params": params, "param": param, "factory": factory, "seq": seq, "layer": layer_command,
    "eeprom": eeprom, "route": route, "routes": routes, "route-both": route_both, "timing": timing, "engine": engine,
    "sysex": sysex_pipe, "cc": cc, "bend": bend, "eeprom-write": eeprom_write, "format": format_patches,
    "patch": patch_command, "patches": patches, "clock": clock_out, "leds": leds, "update-mode": update_mode,
    "flash": flash, "install": install, "port-present": port_present,
}


def main():
    global port_name
    # pipes would get the Windows code page, which has no → or µ
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    given = option("--port")
    port_name = "PRO 800" if given is None else given
    command = args.pop(0) if args else "help"
    if command not in COMMANDS:
        show(HELP_TEXT, end="")
        if command not in ("help", "-h", "--help"):
            sys.exit(1)
        return
    atexit.register(close_midi)
    try:
        COMMANDS[command]()
    except KeyboardInterrupt:
        sys.exit(130)
    except OSError as error:
        fail(str(error))


if __name__ == "__main__":
    main()
