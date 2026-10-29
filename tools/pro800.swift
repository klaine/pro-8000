// pro800: talks to a Behringer PRO-800 over USB MIDI (macOS, CoreMIDI).
//
// Read-only:
//   pro800 ports                          list MIDI ports
//   pro800 version                        firmware version
//   pro800 listen [seconds]               print everything the synth sends (default 20 s)
//   pro800 backup <dir> [first] [last]    dump system settings and patches as .syx (stock firmware only)
//   pro800 play <note> [options]          play a note, optionally recording and measuring it
//       --channel N (1-16 or "all"; default the synth's MIDI channel, else 6)  --hold S (default 2)  --velocity V (default 100)
//       --record [audio device]  (default "Ableton Move Audio")  --wav <file>
//   pro800 record [seconds] [--window S] [--device D] [--wav file]
//                                         record the synth's audio, print level and pitch per window
//
// Changes the synth (only when you mean it):
//   pro800 update-mode                    reboot into the bootloader's update mode
//   pro800 flash <file.syx> --yes [--rate B/s] [--erase-wait S] [--dry-run]
//                                         stream a firmware update to the bootloader
//
// Options for every command: --port <name> (default "PRO 800").
import AVFoundation
import AudioToolbox
import CoreAudio
import CoreMIDI
import Foundation

let helpText = """
Read-only:
  pro800 ports                          list MIDI ports
  pro800 version                        firmware version
  pro800 listen [seconds]               print everything the synth sends (default 20 s)
  pro800 backup <dir> [first] [last]    dump system settings and patches as .syx (stock firmware only)
  pro800 play <note> [options]          play a note, optionally recording and measuring it
      --channel N (1-16 or "all"; default the synth's MIDI channel, else 6)  --hold S (default 2)  --velocity V (default 100)
      --record [audio device]  (default "Ableton Move Audio")  --wav <file>
  pro800 record [seconds] [--window S] [--device D] [--wav file]
                                        record the synth's audio, print level and pitch per window

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
  pro800 sh-waits <settle µs> <sample µs>  the S&H waits for channels written alone (RAM only; 0 0 = default)
  pro800 dac-heal                       set the DAC80501's gain wrong on purpose and check the firmware's re-send
                                        puts it right (0.8.5+; silent)
                                        sweep DAC80501 buses and S&H waits, checked by osc A1's pitch (0.7.4+;
                                        silent; changes nothing stored)
  pro800 panel [seconds]                print every pot, switch, button and encoder change live
  pro800 state                          print every pot (0-99) and switch as the firmware reads them
  pro800 set <setting> <value>          lfo-sync / lfo2-sync free|clock, lfo-trigger / lfo2-trigger
                                        free|first|every|voice, layers off|split|stack|channel|8ch|mpe, split <note>,
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
                                        midi-channel all|1-16 (the channel it listens on; 0.9.0)
  pro800 settings                       print every setting
  pro800 params [layer]                 print a layer's parameters (0-99), Env 3 / 4 and LFO 2 included
  pro800 param <layer> <name> <0-99>    set one (its knob then picks it up)
  pro800 layer 1|2                      choose the layer the per-layer knobs edit
  pro800 route <layer> <src> <dest> <depth>  set a mod route (-99..99, 0 removes); pro800 routes lists them
  pro800 timing                         engine, CV refresh and main-loop pass times
  pro800 cc <controller> <value>        send a control change (e.g. 1 = mod wheel)
  pro800 bend <0-16383>                 send pitch bend (8192 = centre); --channel N for both
  pro800 eeprom <file> [start] [length] read the patch EEPROM (read-only; default all 128 kB)
  pro800 patches [--all]                list the patch slots (ours, stock, empty)
  pro800 patch load|save|clear <slot>   load a patch, save the current sound, or empty a slot (0-199, A00-B99;
                                        each patch has its own sequence, saved and cleared with it)
  pro800 seq                            print the loaded patch's step sequence (firmware 0.7.0+)
  pro800 seq set key=value…             its header: on, length, step (division), clock, bpm, swing, once, keys, arp,
                                        lanes, smooth (e.g. seq set on=1 length=16 step=1/16 clock=1 lanes=1 smooth=A)
  pro800 seq step <n> <note|.> <gate|tie> [v<vel>] [p<prob>] [x<ratchets>] [a<v>]…  one step (a-d: lanes, -99..99)
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
  pro800 install <file.syx> --yes       update-mode + flash + wait until the new firmware answers

Options for every command: --port <name> (default "PRO 800").
"""

let sysexHeader: [UInt8] = [0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00]

func fail(_ message: String) -> Never {
    fflush(stdout)
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

func hex(_ bytes: [UInt8]) -> String { bytes.map { String(format: "%02X", $0) }.joined(separator: " ") }

// MARK: - Arguments

var args = Array(CommandLine.arguments.dropFirst())

func option(_ name: String) -> String? {
    guard let i = args.firstIndex(of: name) else { return nil }
    guard i + 1 < args.count, !args[i + 1].hasPrefix("--") else { args.remove(at: i); return "" }
    let value = args[i + 1]
    args.removeSubrange(i...(i + 1))
    return value
}

func flag(_ name: String) -> Bool {
    guard let i = args.firstIndex(of: name) else { return false }
    args.remove(at: i)
    return true
}

let portName = option("--port") ?? "PRO 800"

// MARK: - MIDI

func endpointName(_ e: MIDIEndpointRef) -> String {
    var s: Unmanaged<CFString>?
    MIDIObjectGetStringProperty(e, kMIDIPropertyDisplayName, &s)
    return (s?.takeRetainedValue() as String?) ?? ""
}

final class Midi {
    private var client = MIDIClientRef()
    private var inPort = MIDIPortRef()
    private var outPort = MIDIPortRef()
    private var dest: MIDIEndpointRef = 0
    private let lock = NSLock()
    private var partial = [UInt8]()
    private var sysex = [[UInt8]]()
    private let arrived = DispatchSemaphore(value: 0)
    // non-SysEx messages, for `listen`; realtime bytes are dropped
    var onShortMessage: (([UInt8]) -> Void)?

    convenience init(port: String) {
        guard let _ = Midi.endpoints(port) else { fail("MIDI port \"\(port)\" not found (see `pro800 ports`)") }
        self.init(existing: port)!
    }

    static func endpoints(_ port: String) -> (MIDIEndpointRef, MIDIEndpointRef)? {
        var src: MIDIEndpointRef = 0, dest: MIDIEndpointRef = 0
        for i in 0..<MIDIGetNumberOfSources() where endpointName(MIDIGetSource(i)) == port { src = MIDIGetSource(i) }
        for i in 0..<MIDIGetNumberOfDestinations() where endpointName(MIDIGetDestination(i)) == port { dest = MIDIGetDestination(i) }
        return src != 0 && dest != 0 ? (src, dest) : nil
    }

    // nil if the port isn't there (it can vanish while a device re-enumerates)
    init?(existing port: String) {
        guard let (src, dest) = Midi.endpoints(port) else { return nil }
        self.dest = dest
        MIDIClientCreate("pro800" as CFString, nil, nil, &client)
        MIDIOutputPortCreate(client, "out" as CFString, &outPort)
        // weak: CoreMIDI can still deliver to a port after its Midi object is gone
        MIDIInputPortCreateWithBlock(client, "in" as CFString, &inPort) { [weak self] list, _ in
            guard let self else { return }
            var p = list.pointee.packet
            for _ in 0..<list.pointee.numPackets {
                let bytes = withUnsafeBytes(of: p.data) { Array($0.prefix(Int(p.length))) }
                self.receive(bytes)
                p = MIDIPacketNext(&p).pointee
            }
        }
        MIDIPortConnectSource(inPort, src, nil)
    }

    deinit { MIDIClientDispose(client) }

    // One message can span several packets, so SysEx is assembled here.
    private func receive(_ bytes: [UInt8]) {
        lock.lock()
        defer { lock.unlock() }
        var short = [UInt8]()
        for b in bytes {
            if b >= 0xF8 { continue }
            if b == 0xF0 { partial = [b]; continue }
            if !partial.isEmpty {
                partial.append(b)
                if b == 0xF7 { sysex.append(partial); partial = []; arrived.signal() }
                continue
            }
            if b & 0x80 != 0, !short.isEmpty { onShortMessage?(short); short = [] }
            short.append(b)
        }
        if !short.isEmpty { onShortMessage?(short) }
    }

    func send(_ bytes: [UInt8]) {
        let size = bytes.count + 1024
        let raw = UnsafeMutableRawPointer.allocate(byteCount: size, alignment: 8)
        defer { raw.deallocate() }
        let list = raw.bindMemory(to: MIDIPacketList.self, capacity: 1)
        let packet = MIDIPacketListInit(list)
        _ = MIDIPacketListAdd(list, size, packet, 0, bytes.count, bytes)
        let status = MIDISend(outPort, dest, list)
        if status != 0 { fail("MIDISend failed: \(status)") }
    }

    // Sends at a CoreMIDI timestamp (host time), so CoreMIDI schedules it.
    func send(_ bytes: [UInt8], at time: MIDITimeStamp) {
        var list = MIDIPacketList()
        let packet = MIDIPacketListInit(&list)
        _ = MIDIPacketListAdd(&list, MemoryLayout<MIDIPacketList>.size, packet, time, bytes.count, bytes)
        MIDISend(outPort, dest, &list)
    }

    func takeSysex() -> [[UInt8]] {
        lock.lock(); defer { lock.unlock() }
        let all = sysex
        sysex.removeAll()
        return all
    }

    // Sends header + data + F7 and waits for a PRO-800 reply whose data starts with `expect`.
    // An empty patch slot answers with a bare F0 F7, returned as is.
    func request(_ data: [UInt8], expect: [UInt8], timeout: Double = 1.5) -> [UInt8]? {
        _ = takeSysex()
        send(sysexHeader + data + [0xF7])
        let deadline = Date().addingTimeInterval(timeout)
        var seen = [[UInt8]]()
        while Date() < deadline {
            _ = arrived.wait(timeout: .now() + 0.05)
            seen += takeSysex()
            if seen.contains([0xF0, 0xF7]) { return [0xF0, 0xF7] }
            for m in seen where m.count > sysexHeader.count && m.starts(with: sysexHeader) {
                let body = m[sysexHeader.count...]
                if body.starts(with: expect) || body.starts(with: [0x01, 0x00, 0x01]) { return m }
            }
        }
        return nil
    }
}

// 7-bit → 8-bit: each group starts with a byte holding the MSBs of the next 7 (bit 0 → first byte).
func unpack(_ packed: ArraySlice<UInt8>) -> [UInt8] {
    var out = [UInt8]()
    var i = packed.startIndex
    while i < packed.endIndex {
        let msbs = packed[i]
        i += 1
        for bit in 0..<7 where i < packed.endIndex {
            out.append(packed[i] | (((msbs >> bit) & 1) << 7))
            i += 1
        }
    }
    return out
}

func patchLabel(_ n: Int) -> String { "P\(n + 1)" }

// Patch name: offset 150 of the unpacked data, up to 16 bytes, 0-terminated.
func patchName(_ data: [UInt8]) -> String {
    guard data.count > 150 else { return "" }
    var name = ""
    for c in data[150...].prefix(16) {
        if c == 0 { break }
        name.append(c >= 0x20 && c < 0x7F ? Character(UnicodeScalar(c)) : "?")
    }
    return name
}

// MARK: - Commands

func ports() {
    print("MIDI sources:")
    for i in 0..<MIDIGetNumberOfSources() { print("  \(endpointName(MIDIGetSource(i)))") }
    print("MIDI destinations:")
    for i in 0..<MIDIGetNumberOfDestinations() { print("  \(endpointName(MIDIGetDestination(i)))") }
}

func version() {
    let midi = Midi(port: portName)
    guard let reply = midi.request([0x08, 0x00], expect: [0x09, 0x00]) else { fail("no reply to the version request") }
    let v = reply.dropFirst(sysexHeader.count + 2).dropLast()
    print("firmware \(v.map(String.init).joined(separator: "."))")
}

func listen() {
    let seconds = Double(args.first ?? "20") ?? 20
    let midi = Midi(port: portName)
    var count = 0
    midi.onShortMessage = { print(hex($0)); count += 1; fflush(stdout) }
    print("listening on \"\(portName)\" for \(Int(seconds)) s (clock and active sensing hidden)")
    let end = Date().addingTimeInterval(seconds)
    while Date() < end {
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))
        for m in midi.takeSysex() { print(hex(m)); count += 1 }
        fflush(stdout)
    }
    print("\(count) messages")
}

func backup() {
    guard let dir = args.first else { fail("usage: pro800 backup <dir> [first] [last]") }
    let first = args.count > 1 ? Int(args[1]) ?? 0 : 0
    let last = args.count > 2 ? Int(args[2]) ?? 399 : 399
    guard 0 <= first, first <= last, last <= 399 else { fail("patch range must be within 0-399") }
    let out = URL(fileURLWithPath: dir)
    try! FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
    func save(_ m: [UInt8], _ name: String) { try! Data(m).write(to: out.appendingPathComponent(name)) }

    let midi = Midi(port: portName)
    var log = ""
    func note(_ s: String) { print(s); log += s + "\n"; fflush(stdout) }

    guard let ver = midi.request([0x08, 0x00], expect: [0x09, 0x00]) else { fail("no reply to the version request") }
    note("firmware \(ver.dropFirst(sysexHeader.count + 2).dropLast().map(String.init).joined(separator: "."))")
    save(ver, "version.syx")

    var all = [UInt8]()
    if let sys = midi.request([0x77, 0x7E, 0x03], expect: [0x78, 0x7E, 0x03]) {
        save(sys, "system.syx")
        all += sys
        note("system settings: \(sys.count) bytes")
    } else {
        note("system settings: NO REPLY")
    }

    var saved = 0, empty = 0, failed = [String]()
    for n in first...last {
        let lo = UInt8(n & 0x7F), hi = UInt8(n >> 7)
        var reply = midi.request([0x77, lo, hi], expect: [0x78, lo, hi])
        if reply == nil { reply = midi.request([0x77, lo, hi], expect: [0x78, lo, hi], timeout: 3) }
        let label = patchLabel(n)
        if reply == [0xF0, 0xF7] { empty += 1; note("\(label): empty"); continue }
        guard let r = reply, r.count > sysexHeader.count + 3, r[sysexHeader.count] == 0x78 else {
            failed.append(label)
            note("\(label): \(reply.map { "unexpected " + hex($0) } ?? "NO REPLY")")
            continue
        }
        save(r, String(format: "patch-%03d-%@.syx", n, label))
        all += r
        saved += 1
        let data = unpack(r[(sysexHeader.count + 3)..<(r.count - 1)])
        let format = data.count > 4 ? String(format: "0x%02X", data[4]) : "?"
        note("\(label): \(r.count) bytes, format \(format), name \"\(patchName(data))\"")
    }
    save(all, "pro800-all.syx")
    note("done: \(saved) patches saved, \(empty) empty slots, \(failed.count) failed \(failed)")
    try! log.write(to: out.appendingPathComponent("dump-log.txt"), atomically: true, encoding: .utf8)
    if !failed.isEmpty { exit(1) }
}

// MARK: - play / record

func audioDevice(named name: String) -> AudioDeviceID? {
    var size: UInt32 = 0
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size)
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids)
    for id in ids {
        var nameAddr = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName, mScope: kAudioObjectPropertyScopeGlobal,
                                                  mElement: kAudioObjectPropertyElementMain)
        var cf: Unmanaged<CFString>?
        var n = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        AudioObjectGetPropertyData(id, &nameAddr, 0, nil, &n, &cf)
        if (cf?.takeRetainedValue() as String?) == name { return id }
    }
    return nil
}

func rmsDB(_ a: ArraySlice<Float>) -> Float {
    guard !a.isEmpty else { return -120 }
    let r = sqrt(a.reduce(0) { $0 + $1 * $1 } / Float(a.count))
    return 20 * log10(max(r, 1e-6))
}

// Autocorrelation with parabolic interpolation around the best lag, so high notes are measured to a few cents.
func estimatePitch(_ x: [Float], sampleRate: Float) -> Float? {
    let n = min(x.count, 16384)
    guard n > 4096 else { return nil }
    let minLag = max(2, Int(sampleRate / 10000)), maxLag = Int(sampleRate / 25)
    var corr = [Float](repeating: 0, count: maxLag + 2)
    for lag in minLag...(maxLag + 1) {
        var s: Float = 0
        for i in 0..<(n - lag) { s += x[i] * x[i + lag] }
        corr[lag] = s
    }
    // the first peak above 90% of the global maximum, to avoid locking onto a multiple of the period
    let top = corr[minLag...maxLag].max() ?? 0
    guard top > 0 else { return nil }
    var lag = minLag + 1
    while lag < maxLag, !(corr[lag] >= 0.9 * top && corr[lag] >= corr[lag - 1] && corr[lag] >= corr[lag + 1]) { lag += 1 }
    let a = corr[lag - 1], b = corr[lag], c = corr[lag + 1]
    let denom = a - 2 * b + c
    let shift = denom == 0 ? 0 : 0.5 * (a - c) / denom
    return sampleRate / (Float(lag) + shift)
}

let defaultAudioDevice = "Ableton Move Audio"

// Records the first input channel of an audio device, optionally also to a WAV file.
final class Recorder {
    private let engine = AVAudioEngine()
    private let lock = NSLock()
    private var samples = [Float]()
    let sampleRate: Float

    init(device name: String, wav: String?) {
        guard var device = audioDevice(named: name) else { fail("audio device \"\(name)\" not found") }
        let input = engine.inputNode
        AudioUnitSetProperty(input.audioUnit!, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &device,
                             UInt32(MemoryLayout<AudioDeviceID>.size))
        let format = input.outputFormat(forBus: 0)
        sampleRate = Float(format.sampleRate)
        let file = wav.map { try! AVAudioFile(forWriting: URL(fileURLWithPath: $0), settings: format.settings) }
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [unowned self] buf, _ in
            try? file?.write(from: buf)
            self.lock.lock()
            self.samples.append(contentsOf: UnsafeBufferPointer(start: buf.floatChannelData![0], count: Int(buf.frameLength)))
            self.lock.unlock()
        }
    }

    func start() { try! engine.start() }

    func stop() -> [Float] {
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        lock.lock(); defer { lock.unlock() }
        return samples
    }
}

func play() {
    guard let noteArg = args.first, let note = UInt8(noteArg), note < 128 else {
        fail("usage: pro800 play <note 0-127> [--channel N|all] [--hold S] [--velocity V] [--record [device]] [--wav file]")
    }
    let channelArg = option("--channel") ?? "\(synthChannel() ?? 6)"
    let hold = Double(option("--hold") ?? "2") ?? 2
    let velocity = UInt8(option("--velocity") ?? "100") ?? 100
    let record = option("--record")
    let wav = option("--wav")
    let channels: [UInt8]
    if channelArg == "all" {
        channels = Array(0...15)
    } else if let c = Int(channelArg), (1...16).contains(c) {
        channels = [UInt8(c - 1)]
    } else {
        fail("--channel must be 1-16 or all")
    }

    let midi = Midi(port: portName)
    let recorder = record.map { Recorder(device: $0.isEmpty ? defaultAudioDevice : $0, wav: wav) }
    recorder?.start()
    if recorder != nil { Thread.sleep(forTimeInterval: 0.5) }

    for c in channels { midi.send([0x90 | c, note, velocity]) }
    Thread.sleep(forTimeInterval: hold)
    for c in channels { midi.send([0x80 | c, note, 0]) }

    guard let recorder else { return }
    Thread.sleep(forTimeInterval: 0.5)
    let x = recorder.stop()
    let sampleRate = recorder.sampleRate
    let sr = Int(sampleRate)
    let before = x[0..<min(x.count, sr * 4 / 10)]
    let heldStart = min(x.count, sr / 2 + sr / 4), heldEnd = min(x.count, sr / 2 + Int(hold * Double(sr)) - sr / 10)
    let held = heldStart < heldEnd ? x[heldStart..<heldEnd] : []
    print(String(format: "level: noise floor %.1f dBFS, note %.1f dBFS, peak %.1f dBFS",
                 rmsDB(before), rmsDB(held), 20 * log10(max(x.map(abs).max() ?? 0, 1e-6))))
    let expected = 440 * pow(2, (Float(note) - 69) / 12)
    if rmsDB(held) - rmsDB(before) < 10 {
        print("no note detected (less than 10 dB above the noise floor)")
    } else if let f = estimatePitch(Array(held), sampleRate: sampleRate) {
        print(String(format: "pitch: %.2f Hz, expected %.2f Hz (A440), %+.1f cents", f, expected, 1200 * log2(f / expected)))
    }
}

// Records for a while and prints level and pitch per window, to measure what the synth plays on its own.
func record() {
    let seconds = Double(args.first ?? "20") ?? 20
    let window = Double(option("--window") ?? "0.5") ?? 0.5
    let device = option("--device") ?? defaultAudioDevice
    let wav = option("--wav")
    guard window >= 0.1 else { fail("--window must be at least 0.1 s") }
    let recorder = Recorder(device: device, wav: wav)
    recorder.start()
    print("recording \(Int(seconds)) s from \"\(device)\"")
    Thread.sleep(forTimeInterval: seconds)
    let x = recorder.stop()
    let n = Int(window * Double(recorder.sampleRate))
    print("  time    level     pitch")
    var start = 0
    while start + n <= x.count {
        let w = Array(x[start..<(start + n)])
        let db = rmsDB(w[...])
        var pitch = ""
        if db > -45, let f = estimatePitch(w, sampleRate: recorder.sampleRate) {
            let midi = 69 + 12 * log2(f / 440)
            pitch = String(format: "%8.2f Hz  (MIDI %.2f)", f, midi)
        }
        print(String(format: "%6.2f  %6.1f dB  %@", Double(start) / Double(recorder.sampleRate), db, pitch))
        start += n
    }
}

// MARK: - Probe (our firmware only)

let cvNames: [String] = (1...8).map { "osca\($0)" } + (1...8).map { "oscb\($0)" } + (1...8).map { "fil\($0)" }
    + (1...8).map { "amp\($0)" } + ["pmod", "vola", "volb", "master", "noise", "extfil", "pwa", "pwb", "res"]
let gateNames = ["sawa", "tria", "sync", "sawb", "trib", "pmodfa", "pmodfil"]

func parseValue(_ text: String) -> Int? {
    text.lowercased().hasPrefix("0x") ? Int(text.dropFirst(2), radix: 16) : Int(text)
}

func probeAck(_ midi: Midi, _ data: [UInt8]) {
    guard let reply = midi.request(data, expect: [0x7F, data[0]]), reply.count > sysexHeader.count,
          reply[sysexHeader.count] == 0x7F else {
        fail("no acknowledgement (is our firmware 0.0.9 or later running?)")
    }
}

func direct() {
    guard let mode = args.first, mode == "on" || mode == "off" else { fail("usage: pro800 direct on|off") }
    probeAck(Midi(port: portName), [0x74, mode == "on" ? 1 : 0])
    print("direct mode \(mode)")
}

// Sets CVs: pro800 cv <name> <value> [<name> <value> ...]
func cv() {
    guard args.count >= 2, args.count % 2 == 0 else {
        fail("usage: pro800 cv <name> <value 0-65535 or 0x...> ...\nnames: \(cvNames.joined(separator: " "))")
    }
    let midi = Midi(port: portName)
    for i in stride(from: 0, to: args.count, by: 2) {
        guard let index = cvNames.firstIndex(of: args[i].lowercased()) else { fail("unknown CV \(args[i])") }
        guard let value = parseValue(args[i + 1]), (0...0xFFFF).contains(value) else { fail("bad value \(args[i + 1])") }
        probeAck(midi, [0x70, UInt8(index), UInt8(value >> 14), UInt8((value >> 7) & 0x7F), UInt8(value & 0x7F)])
    }
}

// Sets gates: pro800 gate <name> 0|1 [...]
func gate() {
    guard args.count >= 2, args.count % 2 == 0 else {
        fail("usage: pro800 gate <name> 0|1 ...\nnames: \(gateNames.joined(separator: " "))")
    }
    let midi = Midi(port: portName)
    for i in stride(from: 0, to: args.count, by: 2) {
        guard let index = gateNames.firstIndex(of: args[i].lowercased()) else { fail("unknown gate \(args[i])") }
        probeAck(midi, [0x71, UInt8(index), args[i + 1] == "0" ? 0 : 1])
    }
}

// One TUNE_CPR measurement in the synth: (Hz, periods counted), or nil without a usable signal.
func measureOnce(_ midi: Midi, periods: Int, timeoutMs: Int, rising: Bool = false) -> (Double, Int)? {
    let data: [UInt8] = [0x72, UInt8(periods >> 7), UInt8(periods & 0x7F), UInt8(timeoutMs >> 7), UInt8(timeoutMs & 0x7F),
                         rising ? 1 : 0]
    guard let reply = midi.request(data, expect: [0x73], timeout: Double(timeoutMs) / 1000 + 2) else {
        fail("no reply (is our firmware 0.1.3 or later running?)")
    }
    let b = Array(reply[(sysexHeader.count + 1)...])
    guard b.count >= 11, b[0] == 1 else { return nil }
    let counted = Int(b[1]) << 7 | Int(b[2])
    let cycles = b[3...7].reduce(0) { $0 << 7 | Int($1) }, khz = b[8...10].reduce(0) { $0 << 7 | Int($1) }
    return (Double(counted) * Double(khz) * 1000 / Double(cycles), counted)
}

// Times TUNE_CPR in the synth: pro800 measure [periods] [--timeout ms] [--repeat n] [--rising]
func measure() {
    let timeout = Int(option("--timeout") ?? "1000") ?? 1000
    let repeatCount = Int(option("--repeat") ?? "1") ?? 1
    let rising = flag("--rising")
    let periods = Int(args.first ?? "16") ?? 16
    guard (1...255).contains(periods), (1...16383).contains(timeout) else { fail("periods 1-255, timeout 1-16383 ms") }
    let midi = Midi(port: portName)
    for _ in 0..<repeatCount {
        if let (hz, counted) = measureOnce(midi, periods: periods, timeoutMs: timeout, rising: rising) {
            print(String(format: "%.4f Hz  (%d periods)", hz, counted))
        } else {
            print("no signal on TUNE_CPR within \(timeout) ms")
        }
    }
}

// Runs autotune in the synth (about 25 s).
func tune() {
    let midi = Midi(port: portName)
    print("tuning (about 25 s, silent)...")
    guard let reply = midi.request([0x75], expect: [0x76], timeout: 120) else { fail("no reply (firmware 0.1.1 or later?)") }
    print("done: \(reply[sysexHeader.count + 1]) of 24 rows measured")
}

func tuningRows(_ midi: Midi) -> [[Double]] {
    (0..<24).map { row in
        guard let reply = midi.request([0x77, UInt8(row)], expect: [0x77, UInt8(row)], timeout: 2) else { fail("no reply for row \(row)") }
        let b = Array(reply[(sysexHeader.count + 3)..<(reply.count - 1)])
        return stride(from: 0, to: b.count - 3, by: 4).map { i in Double(b[i...(i + 3)].reduce(0) { $0 << 7 | Int($1) }) / 100 }
    }
}

// Prints the tuning tables: measured Hz per CV point (0x2000 ... 0xF000).
func tuning() {
    let rows = tuningRows(Midi(port: portName))
    let names = (1...8).map { "osc A\($0)" } + (1...8).map { "osc B\($0)" } + (1...8).map { "filter\($0)" }
    print("row       " + (0..<14).map { String(format: "%8@", String(format: "%04X", 0x2000 + 0x1000 * $0)) }.joined())
    for (r, row) in rows.enumerated() {
        print(names[r].padding(toLength: 10, withPad: " ", startingAt: 0) + row.map { $0 == 0 ? "       -" : String(format: "%8.1f", $0) }.joined())
    }
}

// Plays notes through the tables on every oscillator and filter and measures them with TUNE_CPR; prints the error
// in cents. pro800 check [notes...] [--osc | --filters]. Filters are measured as autotune measures them,
// self-oscillating at full resonance with the mixer off (filters from firmware 0.8.4).
func check() {
    let onlyOsc = flag("--osc"), onlyFilters = flag("--filters")
    let given = args.compactMap { Int($0) }
    let oscNotes = given.isEmpty ? [36, 48, 60, 72, 84, 96] : given
    let filterNotes = given.isEmpty ? [48, 60, 72, 84, 96, 108, 120] : given
    let midi = Midi(port: portName)
    func ack(_ data: [UInt8]) { probeAck(midi, data) }
    func setCV(_ name: String, _ value: Int) {
        ack([0x70, UInt8(cvNames.firstIndex(of: name)!), UInt8(value >> 14), UInt8((value >> 7) & 0x7F), UInt8(value & 0x7F)])
    }
    // sets the source (0 osc A, 1 osc B, 2 filter) of voice v to the note through the tables; measures; cents off
    func cents(_ v: Int, _ source: Int, _ note: Int) -> Double? {
        let data: [UInt8] = [0x78, UInt8(v - 1), UInt8(source), UInt8(note)]
        guard let r = midi.request(data, expect: [0x7F, 0x78], timeout: 2), r[sysexHeader.count] == 0x7F else {
            fail(source == 2 ? "no acknowledgement: checking filters needs firmware 0.8.4 or later (or pass --osc)"
                             : "no acknowledgement (is our firmware running?)")
        }
        let expected = 440 * pow(2, Double(note - 69) / 12)
        let periods = min(255, max(6, Int(expected * 0.08)))
        pump(source == 2 ? 0.06 : 0.03)
        // the comparator now and then reads an octave high below ~50 Hz: retry big errors, keep the best
        var best: Double?
        for _ in 0..<3 {
            if let (hz, _) = measureOnce(midi, periods: periods, timeoutMs: 2047) {
                let c = 1200 * log2(hz / expected)
                if best == nil || abs(c) < abs(best!) { best = c }
                if abs(c) < 50 { break }
            }
        }
        return best
    }
    var worst = [0.0, 0.0]
    func row(_ name: String, _ v: Int, _ source: Int, _ notes: [Int]) {
        setCV("amp\(v)", 0xFFFF)
        var line = name.padding(toLength: 20, withPad: " ", startingAt: 0)
        for note in notes {
            if let c = cents(v, source, note) {
                worst[source / 2] = max(worst[source / 2], abs(c))
                line += String(format: "%+6.1f", c)
            } else {
                line += "     -"
            }
        }
        print(line)
        setCV("amp\(v)", 0)
    }
    ack([0x74, 1])
    setCV("master", 0); setCV("res", 0); setCV("pwa", 0xFFFF); setCV("pwb", 0)
    for v in 1...8 { setCV("amp\(v)", 0); setCV("fil\(v)", 0xFFFF) }
    if !onlyFilters {
        print("cents off per note: " + oscNotes.map { String(format: "%6d", $0) }.joined())
        for osc in 0...1 {
            ack([0x71, 0, osc == 0 ? 1 : 0]); ack([0x71, 3, osc == 1 ? 1 : 0])
            setCV("vola", osc == 0 ? 0xFFFF : 0); setCV("volb", osc == 1 ? 0xFFFF : 0)
            for v in 1...8 { row("osc \(osc == 0 ? "A" : "B")\(v)", v, osc, oscNotes) }
        }
    }
    if !onlyOsc {
        ack([0x71, 0, 0]); ack([0x71, 3, 0]); setCV("vola", 0); setCV("volb", 0); setCV("res", 0xFFFF)
        if !onlyFilters { print("") }
        print("filters, self-oscillating at full resonance")
        print("cents off per note: " + filterNotes.map { String(format: "%6d", $0) }.joined())
        for v in 1...8 {
            row("filter \(v)", v, 2, filterNotes)
            setCV("fil\(v)", 0xFFFF)
        }
        setCV("res", 0)
    }
    ack([0x74, 0])
    if !onlyFilters { print(String(format: "worst oscillator: %.1f cents", worst[0])) }
    if !onlyOsc { print(String(format: "worst filter: %.1f cents", worst[1])) }
}

// Sweeps the CV path's speed (firmware 0.7.4): DAC80501 buses and S&H waits, each checked by oscillator A1's pitch
// through the tuning comparator against the safe default. pro800 dac-test [--bus-only | --timing-only]
// Everything is a probe: the synth goes back to the default at the end, and on any reboot.
// Checks the DAC80501 setup re-send (0.8.5): in direct mode, osc A1 at CV 0x8000 through TUNE_CPR, then with the re-send
// off and the gain at reference / 2 × 1 (half the CV range: about 92 Hz instead of 343), then with the re-send on
// again. pro800 dac-heal
func dacHeal() {
    let midi = Midi(port: portName)
    func ack(_ data: [UInt8]) {
        guard let r = midi.request(data, expect: [0x7F, data[0]], timeout: 2), r[sysexHeader.count] == 0x7F else {
            fail("no acknowledgement (firmware 0.8.5 or later?)")
        }
    }
    func setCV(_ name: String, _ value: Int) {
        probeAck(midi, [0x70, UInt8(cvNames.firstIndex(of: name)!), UInt8(value >> 14), UInt8((value >> 7) & 0x7F), UInt8(value & 0x7F)])
    }
    func hz() -> Double? {
        pump(0.05)
        var readings: [Double] = []
        for _ in 0..<3 { if let (f, _) = measureOnce(midi, periods: 16, timeoutMs: 1500) { readings.append(f) } }
        return readings.isEmpty ? nil : readings.sorted()[readings.count / 2]
    }
    func show(_ label: String) { print(label + (hz().map { String(format: "%8.1f Hz", $0) } ?? "  no signal")) }
    defer { ack([0x6E, 1]); probeAck(midi, [0x74, 0]) }
    probeAck(midi, [0x74, 1])
    setCV("master", 0); setCV("res", 0); setCV("pwa", 0xFFFF); setCV("pwb", 0); setCV("volb", 0); setCV("vola", 0xFFFF)
    for v in 1...8 { setCV("amp\(v)", 0); setCV("fil\(v)", 0xFFFF) }
    probeAck(midi, [0x71, 0, 1]); probeAck(midi, [0x71, 3, 0])
    setCV("osca1", 0x8000); setCV("amp1", 0xFFFF)
    show("osc A1 at 0x8000, as set up:          ")
    ack([0x6E, 0])
    ack([0x6F, 0x04, 0, 0x02, 0x00]) // gain register: reference / 2, buffer gain 1
    show("gain wrong, re-send off:              ")
    show("still wrong a moment later:           ")
    ack([0x6E, 1])
    pump(0.3)
    show("re-send on again, 0.3 s later:        ")
    setCV("amp1", 0)
}

// pro800 sh-waits <settle µs> <sample µs>: the S&H waits for channels written on their own (RAM only, default
// bus; 0 0 restores the defaults). For measuring the VCA path through the Move, which dac-test can't judge.
func shWaits() {
    guard args.count == 2, let settle = Double(args[0]), let sample = Double(args[1]), settle >= 0, sample >= 0,
          settle <= 12.7, sample <= 12.7 else { fail("usage: pro800 sh-waits <settle µs 0-12.7> <sample µs 0-12.7>") }
    let midi = Midi(port: portName)
    let data: [UInt8] = [0x6C, 0, 0, 168 >> 7, 168 & 0x7F, 168 >> 7, 168 & 0x7F, 0, 0, UInt8(settle * 10 + 0.5), UInt8(sample * 10 + 0.5)]
    guard let r = midi.request(data, expect: [0x7F, 0x6C], timeout: 2), r[sysexHeader.count + 2] == 1 else { fail("rejected") }
    print("S&H waits: settle \(settle) µs, sample \(sample) µs (0 = default)")
}

func dacTest() {
    let midi = Midi(port: portName)
    func ack(_ data: [UInt8]) { probeAck(midi, data) }
    func setCV(_ name: String, _ value: Int) {
        ack([0x70, UInt8(cvNames.firstIndex(of: name)!), UInt8(value >> 14), UInt8((value >> 7) & 0x7F), UInt8(value & 0x7F)])
    }
    struct Bus { var bus = 0, mode = 0, high = 168, low = 168, speed = 0, sync = 0 }
    func configure(_ b: Bus, settle: Int = 0, sample: Int = 0) -> Bool {
        let data: [UInt8] = [0x6C, UInt8(b.bus), UInt8(b.mode), UInt8(b.high >> 7), UInt8(b.high & 0x7F), UInt8(b.low >> 7),
                             UInt8(b.low & 0x7F), UInt8(b.speed), UInt8(b.sync), UInt8(settle), UInt8(sample)]
        guard let r = midi.request(data, expect: [0x7F, 0x6C], timeout: 2) else { fail("no reply (firmware 0.7.4 or later?)") }
        return r[sysexHeader.count + 2] == 1
    }
    // the bus's time for one 24-bit frame plus the SYNC waits, in µs
    func frameUs(_ b: Bus) -> Double {
        b.bus == 1 ? 24 / (42.0 / pow(2, Double(b.high + 1))) + 2 * Double(b.sync)
                   : 24 * Double(b.high + b.low) / 168 + 2 * Double(b.sync)
    }
    func describe(_ b: Bus) -> String {
        b.bus == 1 ? String(format: "SPI mode %d, %5.2f MHz, GPIO speed %d", b.mode, 42 / pow(2, Double(b.high + 1)), b.speed)
                   : String(format: "bit-bang mode %d, %5.2f MHz (%d/%d cycles), GPIO speed %d", b.mode,
                            168 / Double(b.high + b.low), b.high, b.low, b.speed)
    }
    func hz(_ cv: Int) -> Double? {
        setCV("osca1", cv)
        pump(0.05)
        var readings: [Double] = []
        for _ in 0..<3 { if let (f, _) = measureOnce(midi, periods: 32, timeoutMs: 1500) { readings.append(f) } }
        return readings.isEmpty ? nil : readings.sorted()[readings.count / 2]
    }
    let points = [0x5000, 0x8000, 0xB000]
    defer { _ = configure(Bus()); ack([0x74, 0]) }

    ack([0x74, 1])
    setCV("master", 0); setCV("res", 0); setCV("pwa", 0xFFFF); setCV("pwb", 0); setCV("volb", 0); setCV("vola", 0xFFFF)
    for v in 1...8 { setCV("amp\(v)", 0); setCV("fil\(v)", 0xFFFF) }
    setCV("amp1", 0xFFFF)
    ack([0x71, 0, 1]); ack([0x71, 3, 0])
    _ = configure(Bus())
    let reference = points.map { hz($0) }
    guard reference.allSatisfy({ $0 != nil }) else { fail("no signal from oscillator A1 on the default bus") }
    print("default bus: " + zip(points, reference).map { String(format: "%04X %.2f Hz", $0, $1!) }.joined(separator: ", "))

    var best = Bus()
    if !flag("--timing-only") {
        var buses: [Bus] = []
        if flag("--clock") {
            // the edge of the working range, asymmetric clocks (one slew-limited edge would allow a short other
            // half), and no SYNC wait: bit-bang only
            for mode in 0...1 { for speed in [0, 3] {
                for (h, l) in [(154, 154), (140, 140), (126, 126), (168, 112), (168, 84), (168, 56), (168, 28), (168, 14),
                               (112, 168), (84, 168), (56, 168), (28, 168), (14, 168), (140, 112), (112, 140)] {
                    buses.append(Bus(bus: 0, mode: mode, high: h, low: l, speed: speed))
                }
            } }
            buses.append(Bus(bus: 0, mode: 0, high: 168, low: 168, speed: 0, sync: 0))
        } else {
            for mode in 0...1 { for speed in [0, 3] { for h in [168, 112, 84, 56, 42, 28, 21, 14, 8] {
                buses.append(Bus(bus: 0, mode: mode, high: h, low: h, speed: speed))
            } } }
            for mode in 0...3 { for div in (1...7).reversed() { buses.append(Bus(bus: 1, mode: mode, high: div, low: 0, speed: 2)) } }
        }
        print("\nbus                                                  frame   worst error")
        for b in buses {
            guard configure(b) else { print(describe(b).padding(toLength: 52, withPad: " ", startingAt: 0) + "  rejected"); continue }
            var worst = 0.0, silent = false
            for (i, cv) in points.enumerated() {
                guard let f = hz(cv) else { silent = true; break }
                worst = max(worst, abs(1200 * log2(f / reference[i]!)))
            }
            let ok = !silent && worst < 3
            print(describe(b).padding(toLength: 52, withPad: " ", startingAt: 0) + String(format: " %5.1f µs  ", frameUs(b)) +
                  (silent ? "no signal" : String(format: "%7.1f cents %@", worst, ok ? "ok" : "BAD")))
            if ok && frameUs(b) < frameUs(best) { best = b }
        }
        _ = configure(Bus())
        print("\nfastest good bus: \(describe(best)), \(String(format: "%.1f", frameUs(best))) µs a frame (default \(String(format: "%.1f", frameUs(Bus()))) µs)")
    }
    if !flag("--bus-only") {
        // The DAC80501's previous write in the refresh order is FIL8: a settle time that's too short lets FIL8's
        // value leak into OSC1A's pitch, so measure A1 with FIL8 at both ends.
        print("\nS&H waits on \(describe(best))")
        print("settle  sample   per channel   A1 shift with FIL8 0 → max   A1 vs default")
        for (settle, sample) in [(60, 120), (40, 80), (30, 60), (20, 40), (20, 20), (10, 20), (10, 10), (5, 10)] {
            guard configure(best, settle: settle, sample: sample) else { continue }
            setCV("fil8", 0)
            let low = hz(0x8000)
            setCV("fil8", 0xFFFF)
            let high = hz(0x8000)
            let line = String(format: "%4.1f µs %5.1f µs  %7.1f µs  ", Double(settle) / 10, Double(sample) / 10,
                              frameUs(best) + Double(settle + sample) / 10)
            if let l = low, let h = high {
                print(line + String(format: "%15.2f cents %18.2f cents", 1200 * log2(h / l), 1200 * log2(h / reference[1]!)))
            } else {
                print(line + "no signal")
            }
        }
    }
}

// Live panel events: pro800 panel [seconds]
let switchNames = ["OSC1_SAW", "OSC1_TRI", "OSC1_SQ", "OSC2_SAW", "OSC2_TRI", "OSC2_SQ", "PW_A_B", "F_A_B", "FREQ_A",
                   "LFO_SHAP", "LFO_FILTER", "FILTER", "UNISON", "HALF", "FULL", "SYNC_SEL", "MIDI_CH_0", "MIDI_CH_1",
                   "MIDI_CH_2", "MIDI_CH_3", "BACKPANEL_PRESENCE", "PEDAL_DT", "SYNC_IN_A", "SYNC_IN_B"]

func potLabel(_ i: Int) -> String {
    i < 24 ? "pot \(["U22/VMUX1", "U21/VMUX2", "U42/VMUX3"][i / 8]).Y\(i % 8) (#\(i))" : (i == 24 ? "pot CUTOFF (#24)" : "pot NOISE (#25)")
}

var panelMidi: Midi?
func panelOff() {
    if let midi = panelMidi { _ = midi.request([0x7A, 0], expect: [0x7F, 0x7A], timeout: 1) }
}

func panel() {
    let seconds = Double(args.first ?? "120") ?? 120
    let midi = Midi(port: portName)
    panelMidi = midi
    probeAck(midi, [0x7A, 1])
    signal(SIGINT) { _ in panelOff(); exit(0) }
    print("panel report on for \(Int(seconds)) s: move knobs, press buttons (Ctrl-C to stop)")
    let start = Date(), end = start.addingTimeInterval(seconds)
    while Date() < end {
        pump(0.05)
        for m in midi.takeSysex() where m.count > sysexHeader.count + 2 && m.starts(with: sysexHeader) {
            let b = Array(m[sysexHeader.count..<(m.count - 1)])
            print(String(format: "%6.1f  ", Date().timeIntervalSince(start)), terminator: "")
            switch b[0] {
            case 0x60 where b.count >= 4: print("\(potLabel(Int(b[1])))  \(Int(b[2]) << 7 | Int(b[3]))")
            case 0x61 where b.count >= 3:
                print("switch \(Int(b[1]) < switchNames.count ? switchNames[Int(b[1])] : "#\(b[1])") = \(b[2])")
            case 0x62 where b.count >= 4:
                let v = Int(b[2]) << 7 | Int(b[3])
                print("keys byte \(b[1]) = " + String(v, radix: 2).leftPad(8))
            case 0x63 where b.count >= 3: print("encoder \(Int(b[2]) - 64 > 0 ? "+" : "-")")
            case 0x64 where b.count >= 3: print("button #\(b[1]) \(b[2] == 1 ? "pressed" : "released")")
            default: print(hex(b))
            }
            fflush(stdout)
        }
    }
    panelOff()
}

// Writes HT16K33 display RAM bytes (report mode keeps the firmware off the display): pro800 leds <addr> <value> ...
func leds() {
    guard args.count >= 2, args.count % 2 == 0 else { fail("usage: pro800 leds <address 0-15> <value 0-255 or 0x..> ...") }
    let midi = Midi(port: portName)
    probeAck(midi, [0x7A, 1])
    for i in stride(from: 0, to: args.count, by: 2) {
        guard let a = parseValue(args[i]), (0..<16).contains(a), let v = parseValue(args[i + 1]), (0..<256).contains(v) else {
            fail("bad address/value \(args[i]) \(args[i + 1])")
        }
        probeAck(midi, [0x7B, UInt8(a), UInt8(v >> 7), UInt8(v & 0x7F)])
    }
}

extension String {
    func leftPad(_ n: Int) -> String { String(repeating: "0", count: max(0, n - count)) + self }
}

let potNames = ["AMP REL", "AMP SUS", "AMP DEC", "AMP ATT", "FIL REL", "FIL SUS", "FIL DEC", "FIL ATT", "RESONANCE",
                "FIL ENV AMT", "FREQ A", "FREQ B", "FINE", "A PW", "B PW", "A LEVEL", "GLIDE", "LFO FREQ", "LFO AMOUNT",
                "M TUNE", "M VOL", "B LEVEL", "PMOD OSC B", "PMOD FIL ENV", "CUTOFF", "NOISE"]
let switchLabels = ["Osc A Saw", "Osc A Tri", "Osc A Square", "Osc B Saw", "Osc B Tri", "Osc B Square", "LFO → PW",
                    "LFO → Freq", "Poly-mod → Freq A", "LFO Shape", "LFO → Filter", "Poly-mod → Filter", "Unison",
                    "Keytrack HALF line", "Keytrack FULL line", "Sync", "MIDI_CH_0", "MIDI_CH_1", "MIDI_CH_2", "MIDI_CH_3",
                    "BACKPANEL_PRESENCE", "PEDAL_DT", "SYNC_IN_A", "SYNC_IN_B"]

// Prints every pot (0-99) and switch as the firmware reads them.
func state() {
    let midi = Midi(port: portName)
    guard let reply = midi.request([0x7C], expect: [0x7C], timeout: 2) else { fail("no reply (firmware 0.2.0 or later?)") }
    let b = Array(reply[(sysexHeader.count + 1)...])
    guard b.count >= 26 * 2 + 24 else { fail("short reply") }
    for i in 0..<26 {
        let v = Int(b[2 * i]) << 7 | Int(b[2 * i + 1])
        print(potNames[i].padding(toLength: 14, withPad: " ", startingAt: 0) + String(format: "%3d", v * 99 / 4095))
    }
    // latching buttons read 1 when latched down, which is off on this panel
    let on = (0..<16).filter { b[52 + $0] == 0 && $0 != 13 && $0 != 14 }.map { switchLabels[$0] }
    print("on: " + (on.isEmpty ? "none" : on.joined(separator: ", ")))
    let keytrack = b[52 + 14] == 0 ? "Full" : b[52 + 13] == 0 ? "Half" : "Off"
    print("keyboard tracking: \(keytrack)")
}

let shapeNames = ["tri", "square", "ramp-up", "ramp-down", "sine", "sh", "drift", "steps", "noise"]
let lfo2DestNames = ["pitch", "filter", "pw"]
let layerModes = ["off", "split", "stack", "channel", "8ch", "mpe"], triggerModes = ["free", "first", "every", "voice"]
let cycleNames = ["amp", "filter", "env3", "env4"]
func perEnvelope(_ name: String) -> [String] { (1...2).flatMap { l in cycleNames.map { "\(name) \(l) \($0)" } } }
let settingNames: [String] = ["lfo-sync", "lfo-trigger", "layers", "split", "keytrack", "lfo2-sync", "lfo2-trigger", "edit-layer",
                    "lfo", "shape 1 1", "shape 1 2", "shape 2 1", "shape 2 2", "lfo2-dests 1", "lfo2-dests 2",
                    "cycle 1", "cycle 2", "cycle-sync 1 amp", "cycle-sync 1 filter", "cycle-sync 1 env3",
                    "cycle-sync 1 env4", "cycle-sync 2 amp", "cycle-sync 2 filter", "cycle-sync 2 env3",
                    "cycle-sync 2 env4", "bend", "detune", "voice 1", "voice 2", "drift", "scale", "root",
                    "lfo-delay", "lfo-fade", "lfo2-delay", "lfo2-fade", "cc-a", "cc-b", "macro 1 1", "macro 1 2", "macro 2 1",
                   "macro 2 2", "clock", "bpm", "env-up 1", "env-up 2", "dahdsr 1", "dahdsr 2", "env-curve 1", "env-curve 2"]
    + perEnvelope("env-delay") + perEnvelope("env-hold") + ["layer-voices"] + perEnvelope("env-count")
    + ["env-tempo 1", "env-tempo 2", "midi-channel", "env-select", "preset-mode", "patch", "morph"]
let curveNames = ["analog", "linear", "swell"]
let scaleNames = ["off", "major", "minor", "dorian", "phrygian", "lydian", "mixolydian", "harmonic-minor", "pentatonic-major",
                  "pentatonic-minor", "blues", "whole-tone", "chromatic"]
let rootNames = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
let voiceModes = ["poly", "mono", "legato"], priorities = ["last", "low", "high"]
let divisionNames = ["8bar", "4bar", "2bar", "1bar", "1/2", "1/4.", "1/4", "1/4T", "1/8.", "1/8", "1/8T", "1/16.", "1/16",
                     "1/16T", "1/32"]
// the latching buttons (switch_t indices) the engine reports with 4C and takes with 4B
let buttonNames: [Int: String] = [0: "a-saw", 1: "a-tri", 2: "a-square", 3: "b-saw", 4: "b-tri", 5: "b-square",
                                  6: "lfo-pw", 7: "lfo-freq", 8: "pmod-freq-a", 10: "lfo-filter", 11: "pmod-filter",
                                  12: "unison", 15: "sync"]

func lfo2DestText(_ bits: Int) -> String {
    let on = lfo2DestNames.indices.filter { bits >> $0 & 1 != 0 }.map { lfo2DestNames[$0] }
    return on.isEmpty ? "none" : on.joined(separator: ",")
}

// Settings: pro800 set <setting> <value> (see the help)
func setSetting() {
    let table: [String: (UInt8, [String])] = ["lfo-sync": (0, ["free", "clock"]), "lfo-trigger": (1, triggerModes),
                                               "layers": (2, layerModes), "keytrack": (4, ["off", "half", "full"]),
                                               "lfo2-sync": (5, ["free", "clock"]), "lfo2-trigger": (6, triggerModes),
                                               "lfo": (8, ["1", "2"])]
    let usage = "usage: pro800 set lfo-sync|lfo2-sync free|clock | lfo-trigger|lfo2-trigger free|first|every|voice | " +
        "layers off|split|stack|channel|8ch|mpe | split <note 0-127> | layer-voices auto|<layer 1's voices 1-7> | keytrack off|half|full | lfo 1|2 | " +
        "lfo-delay|lfo-fade <lfo 1|2> <0-99> | cc-a|cc-b <controller 1-127> | macro <layer 1|2> <1|2> <offset -99..99> | " +
        "clock midi|internal|sync-in | bpm <30-300> | midi-channel all|<1-16> | " +
        "shape <layer 1|2> <lfo 1|2> \(shapeNames.joined(separator: "|")) | lfo2-dests <layer 1|2> none|pitch,filter,pw | " +
        "cycle <layer 1|2> none|amp,filter,env3,env4 | steps <layer 1|2> <lfo 1|2> <x.x.xx.. (1-16 steps)> | " +
        "cycle-sync <layer 1|2> amp|filter|env3|env4 free|\(divisionNames.joined(separator: "|")) | " +
        "env-up|dahdsr|env-tempo <layer 1|2> none|amp,filter,env3,env4 | env-curve <layer 1|2> amp|filter|env3|env4 " +
        "\(curveNames.joined(separator: "|")) | env-delay|env-hold <layer 1|2> amp|filter|env3|env4 <0-99> | " +
        "env-count <layer 1|2> amp|filter|env3|env4 loop|<cycles 1-99> | " +
        "button \(buttonNames.values.sorted().joined(separator: "|")) on|off"
    var id: UInt8, v: Int
    if args.count == 2, args[0] == "morph" { // the morph target: a slot, or none (SysEx 44)
        let t = args[1] == "none" ? 0 : (slotNumber(args[1]).map { $0 + 1 } ?? -1)
        guard t >= 0 else { fail("usage: pro800 set morph <slot 1-128|P1-P128|none>") }
        probeAck(Midi(port: portName), [0x44, UInt8(t >> 7), UInt8(t & 0x7F)])
        print("morph target: " + (t == 0 ? "none" : patchLabel(t - 1)))
        return
    }
    if args.count == 2, args[0] == "split", let note = Int(args[1]), (0...127).contains(note) {
        (id, v) = (3, note)
    } else if args.count == 2, args[0] == "bend", let st = Int(args[1]), (1...12).contains(st) {
        (id, v) = (25, st)
    } else if args.count == 2, args[0] == "detune", let ct = Int(args[1]), (0...50).contains(ct) {
        (id, v) = (26, ct)
    } else if args.count == 2, args[0] == "drift", let a = Int(args[1]), (0...99).contains(a) {
        (id, v) = (29, a)
    } else if args.count == 2, args[0] == "scale", let i = scaleNames.firstIndex(of: args[1]) {
        (id, v) = (30, i)
    } else if args.count == 2, args[0] == "root", let i = rootNames.firstIndex(of: args[1].uppercased()) {
        (id, v) = (31, i)
    } else if args.count == 3, ["lfo-delay", "lfo-fade"].contains(args[0]), let lfo = Int(args[1]), (1...2).contains(lfo),
              let a = Int(args[2]), (0...99).contains(a) {
        (id, v) = (UInt8(32 + 2 * (lfo - 1) + (args[0] == "lfo-fade" ? 1 : 0)), a)
    } else if args.count == 2, ["cc-a", "cc-b"].contains(args[0]), let num = Int(args[1]), (1...127).contains(num) {
        (id, v) = (args[0] == "cc-a" ? 36 : 37, num)
    } else if args.count == 4, args[0] == "macro", let layer = Int(args[1]), (1...2).contains(layer), let m = Int(args[2]),
              (1...2).contains(m), let off = Int(args[3]), (-99...99).contains(off) {
        (id, v) = (UInt8(38 + 2 * (layer - 1) + (m - 1)), off + 99)
    } else if args.count == 2, args[0] == "clock", let i = ["midi", "internal", "sync-in"].firstIndex(of: args[1]) {
        (id, v) = (42, i)
    } else if args.count == 2, args[0] == "layer-voices", let n = args[1] == "auto" ? 0 : Int(args[1]), (0...7).contains(n) {
        (id, v) = (66, n)
    } else if args.count == 2, args[0] == "midi-channel", let c = args[1] == "all" ? 0 : Int(args[1]), (0...16).contains(c) {
        (id, v) = (77, c)
    } else if args.count == 2, args[0] == "bpm", let b = Int(args[1]), (30...300).contains(b) {
        (id, v) = (43, b)
    } else if args.count == 4, args[0] == "voice", let layer = Int(args[1]), (1...2).contains(layer),
              let m = voiceModes.firstIndex(of: args[2]), let p = priorities.firstIndex(of: args[3]) {
        (id, v) = (UInt8(27 + layer - 1), m | p << 2)
    } else if args.count == 2, let (i, values) = table[args[0]], let index = values.firstIndex(of: args[1]) {
        (id, v) = (i, index)
    } else if args.count == 4, args[0] == "shape", let layer = Int(args[1]), (1...2).contains(layer),
              let lfo = Int(args[2]), (1...2).contains(lfo), let shape = shapeNames.firstIndex(of: args[3]) {
        (id, v) = (UInt8(9 + (layer - 1) * 2 + lfo - 1), shape)
    } else if args.count == 3, args[0] == "lfo2-dests", let layer = Int(args[1]), (1...2).contains(layer) {
        var bits = 0
        for name in args[2].split(separator: ",") where name != "none" {
            guard let i = lfo2DestNames.firstIndex(of: String(name)) else { fail(usage) }
            bits |= 1 << i
        }
        (id, v) = (UInt8(13 + layer - 1), bits)
    } else if args.count == 4, args[0] == "steps", let layer = Int(args[1]), (1...2).contains(layer),
              let lfo = Int(args[2]), (1...2).contains(lfo) {
        // x / 1 = on, . / 0 / - = off; the length is the step count (1-16)
        let steps = Array(args[3])
        guard (1...16).contains(steps.count), steps.allSatisfy({ "x1.0-".contains($0) }) else { fail(usage) }
        let bits = steps.indices.reduce(0) { $0 | ("x1".contains(steps[$1]) ? 1 << $1 : 0) }
        probeAck(Midi(port: portName), [0x4E, UInt8(layer - 1), UInt8(lfo - 1), UInt8(steps.count),
                                        UInt8(bits >> 14 & 0x7F), UInt8(bits >> 7 & 0x7F), UInt8(bits & 0x7F)])
        print("layer \(layer) LFO \(lfo) steps: " + String(steps.indices.map { bits >> $0 & 1 != 0 ? "x" : "." }))
        return
    } else if args.count == 4, args[0] == "cycle-sync", let layer = Int(args[1]), (1...2).contains(layer),
              let env = cycleNames.firstIndex(of: args[2]), args[3] == "free" || divisionNames.contains(args[3]) {
        (id, v) = (UInt8(17 + (layer - 1) * 4 + env), args[3] == "free" ? 0 : divisionNames.firstIndex(of: args[3])! + 1)
    } else if args.count == 3, args[0] == "button", let i = buttonNames.first(where: { $0.value == args[1] })?.key,
              args[2] == "on" || args[2] == "off" {
        probeAck(Midi(port: portName), [0x4B, UInt8(i), args[2] == "on" ? 1 : 0])
        print("\(args[1]) \(args[2])")
        return
    } else if args.count == 3, ["cycle", "env-up", "dahdsr", "env-tempo"].contains(args[0]), let layer = Int(args[1]), (1...2).contains(layer) {
        var bits = 0
        for name in args[2].split(separator: ",") where name != "none" {
            guard let i = cycleNames.firstIndex(of: String(name)) else { fail(usage) }
            bits |= 1 << i
        }
        let first = ["cycle": 15, "env-up": 44, "dahdsr": 46, "env-tempo": 75][args[0]]!
        (id, v) = (UInt8(first + layer - 1), bits)
    } else if args.count == 4, args[0] == "env-curve", let layer = Int(args[1]), (1...2).contains(layer),
              let env = cycleNames.firstIndex(of: args[2]), let curve = curveNames.firstIndex(of: args[3]) {
        // the other envelopes' curves stay: read them first
        guard let reply = Midi(port: portName).request([0x54], expect: [0x54], timeout: 2) else { fail("no reply") }
        let b = Array(reply[(sysexHeader.count + 1)..<(reply.count - 1)]), i = 48 + layer - 1
        guard Int(b[0]) > i else { fail("the firmware has no envelope curves (0.9.0 or later?)") }
        let bits = Int(b[1 + 2 * i]) << 7 | Int(b[2 + 2 * i])
        (id, v) = (UInt8(i), bits & ~(3 << (2 * env)) | curve << (2 * env))
    } else if args.count == 4, ["env-delay", "env-hold"].contains(args[0]), let layer = Int(args[1]), (1...2).contains(layer),
              let env = cycleNames.firstIndex(of: args[2]), let a = Int(args[3]), (0...99).contains(a) {
        (id, v) = (UInt8((args[0] == "env-delay" ? 50 : 58) + (layer - 1) * 4 + env), a)
    } else if args.count == 4, args[0] == "env-count", let layer = Int(args[1]), (1...2).contains(layer),
              let env = cycleNames.firstIndex(of: args[2]), let n = args[3] == "loop" ? 0 : Int(args[3]), (0...99).contains(n) {
        (id, v) = (UInt8(67 + (layer - 1) * 4 + env), n)
    } else {
        fail(usage)
    }
    probeAck(Midi(port: portName), [0x7D, id, UInt8(v >> 7), UInt8(v & 0x7F)])
    print("\(args.joined(separator: " "))")
}

// The MIDI channel the synth listens on (1-16), or nil when it takes every channel or can't say (stock firmware, or
// ours before the setting): what play, cc and bend send on without --channel
func synthChannel() -> Int? {
    guard let reply = Midi(port: portName).request([0x54], expect: [0x54], timeout: 1) else { return nil }
    let b = Array(reply[(sysexHeader.count + 1)..<(reply.count - 1)]), id = 77
    guard b.count > 0, Int(b[0]) - 4 > id, b.count > 2 + 2 * id else { return nil }
    let v = Int(b[1 + 2 * id]) << 7 | Int(b[2 + 2 * id])
    return (1...16).contains(v) ? v : nil
}

// pro800 settings: every setting, as the firmware has them now
func settings() {
    guard let reply = Midi(port: portName).request([0x54], expect: [0x54], timeout: 2) else {
        fail("no reply (firmware 0.6.0 or later?)")
    }
    let b = Array(reply[(sysexHeader.count + 1)..<(reply.count - 1)])
    let count = Int(b[0]), readOnly = 4 // env-select, preset-mode, patch, morph: always the reply's last four
    for r in 0..<count {
        // the settable ones from the start, as far as both know them; the read-only ones from the end
        let i = r >= count - readOnly ? settingNames.count - (count - r) : r
        guard r >= count - readOnly || i < settingNames.count - readOnly else { continue }
        let v = Int(b[1 + 2 * r]) << 7 | Int(b[2 + 2 * r])
        var text = "\(v)"
        switch i {
        case 0, 5: text = ["free", "clock"][safe: v] ?? text
        case 1, 6: text = triggerModes[safe: v] ?? text
        case 2: text = layerModes[safe: v] ?? text
        case 4: text = ["off", "half", "full"][safe: v] ?? text
        case 7, 8: text = "\(v + 1)"
        case 9...12: text = shapeNames[safe: v] ?? text
        case 13, 14: text = lfo2DestText(v)
        case 15, 16, 44...47, 75, 76:
            let on = cycleNames.indices.filter { v >> $0 & 1 != 0 }.map { cycleNames[$0] }
            text = on.isEmpty ? "none" : on.joined(separator: ",")
        case 48, 49: text = cycleNames.indices.map { "\(cycleNames[$0]) \(curveNames[safe: v >> (2 * $0) & 3] ?? "?")" }.joined(separator: ", ")
        case 50...65: text = String(format: "%d (%.3f s)", v, 10 * (pow(2, Double(v) / 99 * 12) - 1) / 4095)
        case 17...24: text = v == 0 ? "free" : divisionNames[safe: v - 1] ?? text
        case 67...74: text = v == 0 ? "loop (endless)" : "\(v) cycles"
        case 25: text = "±\(v) semitones"
        case 26: text = "\(v) cents"
        case 27, 28: text = "\(voiceModes[safe: v & 3] ?? "?"), \(priorities[safe: v >> 2 & 3] ?? "?") note priority"
        case 30: text = scaleNames[safe: v] ?? text
        case 31: text = rootNames[safe: v] ?? text
        case 32...35: text = v == 0 ? "off" : String(format: "%d (%.2f s)", v, 0.02 * pow(2, Double(v) * 8 / 99))
        case 36, 37: text = "CC \(v)"
        case 38...41: text = String(format: "%+d", v - 99)
        case 42: text = ["midi", "internal", "sync-in"][safe: v] ?? text
        case 43: text = "\(v) BPM"
        case 66: text = v == 0 ? "automatic" : "\(v) + \(8 - v)"
        case 77: text = v == 0 ? "all" : "\(v)"
        case 78: text = ["filter and amp", "env 3", "env 4", "env 3 / env 4 (Shift)"][safe: v] ?? text
        case 80: text = slotName(v)
        case 81: text = v == 0 ? "none" : slotName(v - 1)
        default: break
        }
        print("\(settingNames[i].padding(toLength: 20, withPad: " ", startingAt: 0)) \(text)")
    }
    let midi = Midi(port: portName)
    if let r = midi.request([0x4C], expect: [0x4C]) {
        let d = Array(r[(sysexHeader.count + 1)..<(r.count - 1)])
        let on = buttonNames.keys.sorted().filter { $0 < d.count && d[$0] != 0 }.map { buttonNames[$0]! }
        print("buttons".padding(toLength: 21, withPad: " ", startingAt: 0) + (on.isEmpty ? "none" : on.joined(separator: " ")))
    }
    for layer in 0..<2 {
        for lfo in 0..<2 {
            guard let r = midi.request([0x4D, UInt8(layer), UInt8(lfo)], expect: [0x4D, UInt8(layer), UInt8(lfo)]) else { continue }
            let d = Array(r[sysexHeader.count...])
            let count = Int(d[3]), bits = Int(d[4]) << 14 | Int(d[5]) << 7 | Int(d[6])
            print("steps \(layer + 1) \(lfo + 1)".padding(toLength: 21, withPad: " ", startingAt: 0) +
                  String((0..<count).map { bits >> $0 & 1 != 0 ? "x" : "." }))
        }
    }
}

extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}

// Parameter names, in the firmware's order (the knobs by hardware position, then Env 3, Env 4, LFO 2); macro1 is the
// M TUNE knob (0.9.0)
let paramNames = ["amp-rel", "amp-sus", "amp-dec", "amp-att", "fil-rel", "fil-sus", "fil-dec", "fil-att",
                  "resonance", "fil-env-amt", "freq-a", "freq-b", "fine", "a-pw", "b-pw", "a-level",
                  "glide", "lfo-freq", "lfo-amount", "macro1", "master-vol", "b-level", "pmod-osc-b", "pmod-fil-env",
                  "cutoff", "noise",
                  "env3-att", "env3-dec", "env3-sus", "env3-rel", "env4-att", "env4-dec", "env4-sus", "env4-rel",
                  "lfo2-rate", "lfo2-amount"]

// pro800 params [layer]: a layer's parameters, 0-99
func params() {
    let layers = args.isEmpty ? [1, 2] : args.compactMap { Int($0) }.filter { $0 == 1 || $0 == 2 }
    let midi = Midi(port: portName)
    for layer in layers {
        guard let reply = midi.request([0x53, UInt8(layer - 1)], expect: [0x53, UInt8(layer - 1)], timeout: 2) else {
            fail("no reply (firmware 0.6.0 or later?)")
        }
        let b = Array(reply[(sysexHeader.count + 2)..<(reply.count - 1)])
        print("layer \(layer):")
        var line = ""
        for i in 0..<min(Int(b[0]), paramNames.count) {
            let v = Int(b[1 + 2 * i]) << 7 | Int(b[2 + 2 * i])
            line += "\(paramNames[i].padding(toLength: 13, withPad: " ", startingAt: 0))\(String(format: "%3d", (v * 99 + 8191) / 16383))   "
            if i % 4 == 3 { print("  " + line); line = "" }
        }
        if !line.isEmpty { print("  " + line) }
    }
}

// pro800 param <layer> <name> <0-99>
func param() {
    guard args.count == 3, let layer = Int(args[0]), layer == 1 || layer == 2,
          let index = paramNames.firstIndex(of: args[1]), let value = Double(args[2]), (0...99).contains(value) else {
        fail("usage: pro800 param <layer 1|2> <name> <0-99>\nnames: " + paramNames.joined(separator: " "))
    }
    let v = Int(value / 99 * 16383 + 0.5)
    probeAck(Midi(port: portName), [0x52, UInt8(layer - 1), UInt8(index), UInt8(v >> 7), UInt8(v & 0x7F)])
    print("layer \(layer) \(args[1]) = \(args[2])")
}

// pro800 seq: the loaded patch's step sequence
let arpModeNames = ["off", "up", "down", "updown", "random", "played"]

// pro800 seq set key=value…: the sequence's header, read first, the given keys changed (SysEx 47)
func seqSet() {
    let midi = Midi(port: portName)
    guard let h = midi.request([0x48], expect: [0x48], timeout: 2) else { fail("no reply (firmware 0.7.0 or later?)") }
    let d = Array(h[(sysexHeader.count + 1)...])
    var length = Int(d[0]), lanes = Int(d[1]), division = Int(d[2]), bpm = Int(d[3]) << 7 | Int(d[4]), flags = Int(d[5])
    var swing = Int(d[6]), smooth = Int(d[7]), arp = Int(d[9])
    let usage = "usage: pro800 seq set on=0|1 length=1-64 step=<division> clock=0|1 bpm=30-255 swing=0-75 once=0|1 " +
                "keys=both|1|2 arp=\(arpModeNames.joined(separator: "|")) lanes=0-4 smooth=none|A..D"
    for kv in args.dropFirst() {
        let parts = kv.split(separator: "=", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { fail(usage) }
        let (key, value) = (parts[0], parts[1])
        func bit(_ mask: Int) { if let v = Int(value), v == 0 || v == 1 { flags = v == 1 ? flags | mask : flags & ~mask } else { fail(usage) } }
        switch key {
        case "on": bit(1)
        case "clock": bit(2)
        case "once": bit(4)
        case "length": guard let v = Int(value), (1...64).contains(v) else { fail(usage) }; length = v
        case "step": guard let i = divisionNames.firstIndex(of: value) else { fail(usage) }; division = i
        case "bpm": guard let v = Int(value), (30...255).contains(v) else { fail(usage) }; bpm = v
        case "swing": guard let v = Int(value), (0...75).contains(v) else { fail(usage) }; swing = v
        case "keys": guard let i = ["both", "1", "2"].firstIndex(of: value) else { fail(usage) }; flags = flags & ~(3 << 3) | i << 3
        case "arp": guard let i = arpModeNames.firstIndex(of: value) else { fail(usage) }; arp = i
        case "lanes": guard let v = Int(value), (0...4).contains(v) else { fail(usage) }; lanes = v
        case "smooth":
            smooth = 0
            for ch in value.uppercased() where ch != "N" && ch != "O" && ch != "E" {
                guard let i = ["A", "B", "C", "D"].firstIndex(of: String(ch)) else { fail(usage) }
                smooth |= 1 << i
            }
        default: fail(usage)
        }
    }
    probeAck(midi, [0x47, UInt8(length), UInt8(lanes), UInt8(division), UInt8(bpm >> 7), UInt8(bpm & 0x7F), UInt8(flags & 0x7F),
                    UInt8(swing), UInt8(smooth), UInt8(arp)])
    print("sequence: \(flags & 1 != 0 ? "on" : "off"), \(length) steps of \(divisionNames[division]), " +
          (flags & 2 != 0 ? "MIDI clock" : "\(bpm) BPM") + ", \(lanes) lanes")
}

// pro800 seq step <n> <note|.> <gate|tie> [v<vel>] [p<prob 1-8>] [x<ratchets 1-4>] [a<-99..99>] … [d<..>] (SysEx 45)
func seqStep() {
    let usage = "usage: pro800 seq step <1-64> <+offset|-offset|.> <gate 1-99|tie> [v0-127] [p1-8] [x1-4] [a-99..99] [b..] [c..] [d..]"
    let a = Array(args.dropFirst())
    guard a.count >= 2, let n = Int(a[0]), (1...64).contains(n) else { fail(usage) }
    var on: UInt8 = 1, note = 64, gate = 50, velocity = 0, probability = 8, ratchets = 1, lanes = [64, 64, 64, 64]
    var options = 2
    if a[1] == "." {
        on = 0
        if a.count >= 3, a[2] == "tie" || Int(a[2]) != nil { options = 3 } // a rest may still carry its gate
    } else {
        guard let off = Int(a[1]), (-64...63).contains(off), a.count >= 3 else { fail(usage) }
        note = 64 + off
        if a[2] == "tie" { gate = 100 } else { guard let g = Int(a[2]), (1...99).contains(g) else { fail(usage) }; gate = g }
        options = 3
    }
    for opt in a.dropFirst(options) {
        guard let first = opt.first, let v = Int(opt.dropFirst()) else { fail(usage) }
        switch first {
        case "v": guard (0...127).contains(v) else { fail(usage) }; velocity = v
        case "p": guard (1...8).contains(v) else { fail(usage) }; probability = v
        case "x": guard (1...4).contains(v) else { fail(usage) }; ratchets = v
        case "a", "b", "c", "d":
            guard (-99...99).contains(v) else { fail(usage) }
            lanes[Int(first.asciiValue! - Character("a").asciiValue!)] = 64 + Int((Double(v) * 63 / 99).rounded())
        default: fail(usage)
        }
    }
    let extra = UInt8((8 - probability) | (ratchets - 1) << 3)
    probeAck(Midi(port: portName), [0x45, UInt8(n - 1), 1, UInt8(note), UInt8(velocity), UInt8(gate), on, extra] + lanes.map { UInt8($0) })
}

func arpText(_ d: [UInt8]) -> String {
    guard d.count > 10, d[9] != 0 else { return "" }
    return ", arp " + (["off", "up", "down", "up-down", "random", "played"][safe: Int(d[9])] ?? "?")
}

func showSequence() {
    let midi = Midi(port: portName)
    guard let h = midi.request([0x48], expect: [0x48], timeout: 2) else { fail("no reply (firmware 0.7.0 or later?)") }
    let d = Array(h[(sysexHeader.count + 1)...])
    let length = Int(d[0]), lanes = Int(d[1]), flags = Int(d[5])
    print("\(flags & 1 != 0 ? "on" : "off"), \(length) steps of \(divisionNames[safe: Int(d[2])] ?? "?"), " +
          (flags & 2 != 0 ? "MIDI clock" : "\(Int(d[3]) << 7 | Int(d[4])) BPM") + ", swing \(d[6]) %, " +
          (flags & 4 != 0 ? "once per key" : "loops") + ", keys of " + ["both layers", "layer 1", "layer 2", "?"][flags >> 3 & 3] +
          ", \(lanes) lane\(lanes == 1 ? "" : "s") (max \(d[8]) steps)" + arpText(d))
    var notes = [String](), gates = [String](), extras = [String](), laneRows = [[String]](repeating: [], count: lanes)
    for first in stride(from: 0, to: length, by: 8) {
        guard let r = midi.request([0x46, UInt8(first), 8], expect: [0x46, UInt8(first), 8], timeout: 2) else { fail("no reply") }
        let x = Array(r[(sysexHeader.count + 3)...])
        for k in 0..<8 where first + k < length {
            let s = Array(x[(9 * k)..<(9 * k + 9)]) // note, velocity, gate, on, extra, lanes A-D
            let offset = Int(s[0]) - 64
            notes.append(s[3] == 0 ? "  ." : String(format: "%+3d", offset).replacingOccurrences(of: "+0", with: " 0"))
            gates.append(s[3] == 0 ? "  ." : s[2] >= 100 ? "tie" : String(format: "%3d", s[2]))
            let probability = 8 - Int(s[4] & 7), ratchets = Int(s[4] >> 3 & 3) + 1
            extras.append(probability == 8 && ratchets == 1 ? "  ." : ratchets > 1 && probability == 8 ? " x\(ratchets)" :
                          ratchets > 1 ? "\(probability)x\(ratchets)" : " \(probability)8")
            for l in 0..<lanes { laneRows[l].append(String(format: "%+3d", Int((Double(Int(s[5 + l]) - 64) / 63 * 99).rounded()))) }
        }
    }
    print("note  " + notes.joined(separator: " "))
    print("gate  " + gates.joined(separator: " "))
    if extras.contains(where: { $0 != "  ." }) { print("prob  " + extras.joined(separator: " ") + "   (n8 = n in 8, xN = ratchets)") }
    for l in 0..<lanes { print("seq \(["A", "B", "C", "D"][l]) " + laneRows[l].joined(separator: " ")) }
}

// pro800 factory [import <f> <slot> [--force]]
func factory() {
    let midi = Midi(port: portName)
    if args.first == "import" {
        guard args.count >= 3, let f = Int(args[1]), (0..<100).contains(f), let n = slotNumber(args[2]) else {
            fail("usage: pro800 factory import <0-99> <slot> [--force]")
        }
        var data: [UInt8] = [0x51, UInt8(f), UInt8(n >> 7), UInt8(n & 0x7F)]
        if flag("--force") { data.append(1) }
        guard let reply = midi.request(data, expect: [0x51], timeout: 3) else { fail("no reply (firmware 0.6.0 or later?)") }
        let status = Int(reply[sysexHeader.count + 1])
        print(["copied factory \(f) to \(slotName(n))", "\(slotName(n)) isn't empty (--force to replace it)",
               "factory patch \(f) isn't there", "write error"][safe: status] ?? "status \(status)")
        return
    }
    for f in 0..<100 {
        guard let reply = midi.request([0x50, UInt8(f)], expect: [0x50, UInt8(f)], timeout: 2) else {
            fail("no reply (firmware 0.6.0 or later?)")
        }
        let b = Array(reply[(sysexHeader.count + 2)..<(reply.count - 1)])
        print(String(format: "%2d  ", f) + (b.first == 1 ? String(decoding: b.dropFirst(), as: UTF8.self) : "(none)"))
    }
}

// The layer the per-layer knobs edit: pro800 layer 1|2
func layer() {
    guard let n = Int(args.first ?? ""), n == 1 || n == 2 else { fail("usage: pro800 layer 1|2") }
    probeAck(Midi(port: portName), [0x7E, UInt8(n - 1)])
    print("editing layer \(n)")
}

// Sends MIDI clock: Start, then 24 timing clocks per quarter note, scheduled with CoreMIDI timestamps.
func clockOut() {
    guard let bpm = Double(args.first ?? ""), bpm >= 20, bpm <= 300 else { fail("usage: pro800 clock <bpm 20-300> [seconds]") }
    let seconds = args.count > 1 ? Double(args[1]) ?? 10 : 10
    let midi = Midi(port: portName)
    var timebase = mach_timebase_info_data_t()
    mach_timebase_info(&timebase)
    func hostTime(_ s: Double) -> UInt64 { UInt64(s * 1e9 * Double(timebase.denom) / Double(timebase.numer)) }
    let start = mach_absolute_time() + hostTime(0.05)
    let tick = 60.0 / bpm / 24
    midi.send([0xFA], at: start)
    let total = Int(seconds / tick)
    var sent = 0
    print(String(format: "clock at %.1f BPM for %.0f s", bpm, seconds))
    while sent < total {
        let horizon = mach_absolute_time() + hostTime(0.1) // keep 100 ms scheduled ahead
        while sent < total && start + hostTime(Double(sent) * tick) < horizon {
            midi.send([0xF8], at: start + hostTime(Double(sent) * tick))
            sent += 1
        }
        pump(0.02)
    }
    pump(0.15)
    midi.send([0xFC], at: 0)
    pump(0.05)
}

// Reads the patch EEPROM (read-only): pro800 eeprom <file> [start] [length]
func eeprom() {
    guard let path = args.first else { fail("usage: pro800 eeprom <file> [start] [length]") }
    let start = args.count > 1 ? parseValue(args[1]) ?? 0 : 0
    let length = args.count > 2 ? parseValue(args[2]) ?? 0x20000 : 0x20000
    let midi = Midi(port: portName)
    var data = [UInt8](), address = start, status = -1
    let began = Date()
    while address < start + length {
        let n = min(48, start + length - address)
        let request: [UInt8] = [0x79, UInt8(address >> 14 & 0x7F), UInt8(address >> 7 & 0x7F), UInt8(address & 0x7F), UInt8(n)]
        guard let reply = midi.request(request, expect: Array(request), timeout: 2) else {
            fail("no reply at 0x\(String(address, radix: 16)) (firmware 0.3.2 or later?)")
        }
        let b = Array(reply[(sysexHeader.count + 5)..<(reply.count - 1)])
        guard b.count == 2 + 2 * n else { fail("short reply at 0x\(String(address, radix: 16))") }
        status = Int(b[0]) << 4 | Int(b[1])
        for i in 0..<n { data.append(b[2 + 2 * i] << 4 | b[3 + 2 * i]) }
        address += n
        if (address - start) % 0x2000 < 48 {
            print("\r\(address - start) / \(length) bytes", terminator: "")
            fflush(stdout)
        }
    }
    try! Data(data).write(to: URL(fileURLWithPath: path))
    print(String(format: "\r%d bytes in %.1f s → %@ (status register 0x%02X)", data.count, Date().timeIntervalSince(began), path, status))
}

func slotNumber(_ text: String) -> Int? {
    let t = text.uppercased()
    if let n = Int(t), (0..<128).contains(n) { return n }
    if let first = t.first, first == "P" || first == "p", let p = Int(t.dropFirst()), (1...128).contains(p) {
        return p - 1 // "P7": patch 7 = slot 6
    }
    return nil
}

func slotName(_ n: Int) -> String { patchLabel(n) }

// Writes a whole EEPROM image and reads it back: pro800 eeprom-write <file> --yes (e.g. the stock image)
func eepromWrite() {
    guard flag("--yes"), let path = args.first else { fail("usage: pro800 eeprom-write <file> --yes") }
    guard let image = FileManager.default.contents(atPath: path), image.count == 0x20000 else { fail("\(path) isn't a 128 kB image") }
    let bytes = [UInt8](image), midi = Midi(port: portName)
    var address = 0
    while address < bytes.count {
        let n = min(48, bytes.count - address)
        var data: [UInt8] = [0x5A, UInt8(address >> 14 & 0x7F), UInt8(address >> 7 & 0x7F), UInt8(address & 0x7F), UInt8(n)]
        for b in bytes[address..<(address + n)] { data += [b >> 4, b & 0x0F] }
        guard midi.request(data, expect: [0x7F, 0x5A], timeout: 2) != nil else { fail("write failed at 0x\(String(address, radix: 16))") }
        address += n
        if address % 0x2000 < 48 { print("\rwritten \(address) / \(bytes.count)", terminator: ""); fflush(stdout) }
    }
    print("\nverifying")
    let check = FileManager.default.temporaryDirectory.appendingPathComponent("pro800-verify.bin").path
    args = [check]
    eeprom()
    let back = FileManager.default.contents(atPath: check)
    print(back == image ? "verified: the EEPROM matches \(path); power-cycle the synth" : "MISMATCH after writing")
}

// Converts every stock patch in place: pro800 import --yes
func formatPatches() {
    guard flag("--yes") else { fail("usage: pro800 format --yes (wipes every patch slot and marks the EEPROM as ours)") }
    let midi = Midi(port: portName)
    print("formatting...")
    guard let reply = midi.request([0x5B], expect: [0x5B], timeout: 60) else { fail("no reply (firmware 0.8.0 or later?)") }
    let b = Array(reply[(sysexHeader.count + 1)...])
    print(b[2] == 0 ? "\(b[1]) empty slots" : "format failed")
}

// pro800 patch load|save|clear <slot>  (slot 0-127, or P1-P128)
func patchCommand() {
    if args.count >= 3, args[0] == "rename", let n = slotNumber(args[1]) {
        let name = Array(args[2...].joined(separator: " ").utf8.filter { $0 >= 0x20 && $0 < 0x7F }.prefix(16))
        probeAck(Midi(port: portName), [0x59, UInt8(n >> 7), UInt8(n & 0x7F)] + name)
        print("renamed \(slotName(n)) to \(String(decoding: name, as: UTF8.self))")
        return
    }
    if args == ["init"] { // the basic sound PERF loads
        guard Midi(port: portName).request([0x5F], expect: [0x7F, 0x5F], timeout: 3) != nil else { fail("no reply") }
        print("loaded the init sound")
        return
    }
    guard args.count == 2, ["load", "save", "clear"].contains(args[0]), let n = slotNumber(args[1]) else {
        fail("usage: pro800 patch load|save|clear <slot 0-199 or A00-B99>  or  pro800 patch init")
    }
    if args[0] == "clear" { // an empty slot is 256 zero bytes
        probeAck(Midi(port: portName), [0x4F, UInt8(n >> 7), UInt8(n & 0x7F)])
        print("cleared \(slotName(n))")
        return
    }
    let cmd: UInt8 = args[0] == "load" ? 0x5C : 0x5D
    guard Midi(port: portName).request([cmd, UInt8(n >> 7), UInt8(n & 0x7F)], expect: [0x7F, cmd], timeout: 3) != nil else {
        fail("\(args[0]) \(slotName(n)) failed (empty slot?)")
    }
    print("\(args[0] == "load" ? "loaded" : "saved") \(slotName(n))")
}

// Lists the patch slots: pro800 patches [--all]
func patches() {
    let all = flag("--all"), midi = Midi(port: portName)
    let kinds = ["empty", "ours", "STOCK", "corrupt"]
    var counts = [0, 0, 0, 0]
    for n in 0..<128 {
        guard let reply = midi.request([0x5E, UInt8(n >> 7), UInt8(n & 0x7F)], expect: [0x5E, UInt8(n >> 7), UInt8(n & 0x7F)], timeout: 2) else {
            fail("no reply (firmware 0.4.0 or later?)")
        }
        let b = Array(reply[(sysexHeader.count + 3)..<(reply.count - 1)])
        let kind = Int(b[0]), name = String(decoding: b.dropFirst(), as: UTF8.self)
        counts[kind] += 1
        if kind != 0 || all { print("\(slotName(n))  \(kinds[kind].padding(toLength: 7, withPad: " ", startingAt: 0)) \(name)") }
    }
    print("\(counts[1]) ours, \(counts[2]) stock, \(counts[0]) empty, \(counts[3]) corrupt")
}

let sourceNames = ["", "lfo", "filter-env", "amp-env", "velocity", "mod-wheel", "aftertouch", "env3", "env4", "random", "note",
                   "lfo2", "seq-a", "seq-b", "seq-c", "seq-d", "lfo-uni", "lfo2-uni", "gate", "timbre",
                   "bend", "pedal", "cc-a", "cc-b", "voice", "fixed", "macro1", "macro2"]
let destNames = ["pitch-a", "pitch-b", "pitch", "cutoff", "filter-env-amount", "pmod-env", "lfo-amount", "lfo-to-pitch",
                 "lfo-to-filter", "vca", "a-level", "b-level", "noise", "resonance", "a-pw", "b-pw", "pmod-osc-b", "lfo-to-pw",
                 "lfo-rate", "lfo2-amount", "lfo2-rate", "lfo2-to-pitch", "lfo2-to-filter", "lfo2-to-pw", "morph",
                 "amp-att", "amp-dec", "amp-sus", "amp-rel", "fil-att", "fil-dec", "fil-sus", "fil-rel",
                 "env3-att", "env3-dec", "env3-sus", "env3-rel", "env4-att", "env4-dec", "env4-sus", "env4-rel",
                 "glide", "detune", "volume", "macro1", "macro2"]

// pro800 route <layer 1|2> <source> <dest> <depth -99..99>  (0 removes)
func route() {
    guard args.count == 4, let layer = Int(args[0]), layer == 1 || layer == 2,
          let src = sourceNames.firstIndex(of: args[1]), src > 0, let dest = destNames.firstIndex(of: args[2]),
          let depth = Int(args[3]), (-99...99).contains(depth) else {
        fail("usage: pro800 route <layer 1|2> <source> <destination> <depth -99..99>\nsources: " +
             sourceNames.dropFirst().joined(separator: " ") + "\ndestinations: " + destNames.joined(separator: " "))
    }
    let d = depth + 99
    probeAck(Midi(port: portName), [0x56, UInt8(layer - 1), UInt8(src), UInt8(dest), UInt8(d >> 7), UInt8(d & 0x7F)])
    print("layer \(layer): \(args[1]) → \(args[2]) = \(depth)")
}

// pro800 routes: both layers' mod routes
func routes() {
    let midi = Midi(port: portName)
    for layer in 0..<2 {
        guard let reply = midi.request([0x57, UInt8(layer)], expect: [0x57, UInt8(layer)], timeout: 2) else {
            fail("no reply (firmware 0.5.0 or later?)")
        }
        let b = Array(reply[(sysexHeader.count + 2)..<(reply.count - 1)])
        var lines = [String]()
        for i in stride(from: 0, to: b.count - 3, by: 4) where b[i] != 0 && Int(b[i]) < sourceNames.count && Int(b[i + 1]) < destNames.count {
            lines.append("  \(sourceNames[Int(b[i])]) → \(destNames[Int(b[i + 1])]): \((Int(b[i + 2]) << 7 | Int(b[i + 3])) - 99)")
        }
        print("layer \(layer + 1): " + (lines.isEmpty ? "no routes" : "\n" + lines.joined(separator: "\n")))
    }
}

// pro800 timing: how long the engine, the CV refresh and a main-loop pass take
func timing() {
    let midi = Midi(port: portName)
    _ = midi.request([0x58], expect: [0x58], timeout: 2) // resets the longest pass
    pump(2)
    guard let reply = midi.request([0x58], expect: [0x58], timeout: 2) else { fail("no reply (firmware 0.5.0 or later?)") }
    let b = Array(reply[(sysexHeader.count + 1)...])
    func value(_ i: Int) -> Int {
        let high = Int(b[3 * i]) << 14, mid = Int(b[3 * i + 1]) << 7
        return high | mid | Int(b[3 * i + 2])
    }
    let v = (0..<min(8, (b.count - 1) / 3)).map(value)
    print("engine \(v[0]) µs, CV refresh \(v[1]) µs" + (v.count >= 8 ? " (of which the four ticks \(v[7]) µs)" : "")
          + ", main-loop pass \(v[2]) µs (longest in 2 s: \(v[3]) µs)")
    if v.count >= 6 { print("panel poll \(v[4]) µs, display \(v[5]) µs (last pass)") }
    if v.count >= 7, v[6] > 0 {
        print("\(v[6]) passes in the last second: \(1_000_000 / v[6]) µs per pass on average, modulation \(v[6]) times a second")
    }
}

// pro800 cc <controller> <value> [--channel N]
func cc() {
    let channel = UInt8((Int(option("--channel") ?? "") ?? synthChannel() ?? 1) - 1) & 0x0F
    guard args.count == 2, let num = Int(args[0]), let value = Int(args[1]), (0...127).contains(num), (0...127).contains(value) else {
        fail("usage: pro800 cc <controller 0-127> <value 0-127> [--channel N]")
    }
    let midi = Midi(port: portName)
    midi.send([0xB0 | channel, UInt8(num), UInt8(value)])
    pump(0.05)
}

// pro800 bend <0-16383> [--channel N]: pitch bend (8192 = centre)
func bend() {
    let channel = UInt8((Int(option("--channel") ?? "") ?? synthChannel() ?? 1) - 1) & 0x0F
    guard args.count == 1, let value = Int(args[0]), (0...16383).contains(value) else {
        fail("usage: pro800 bend <0-16383> [--channel N]  (8192 = centre)")
    }
    let midi = Midi(port: portName)
    midi.send([0xE0 | channel, UInt8(value & 0x7F), UInt8(value >> 7)])
    pump(0.05)
}

// MARK: - Bootloader

func updateMode() {
    let midi = Midi(port: portName)
    midi.send(sysexHeader + [0x03, 0x30, 0xF7])
    print("sent the update-mode command; the PRO-800 restarts into its bootloader")
}

// Splits a .syx file into messages and checks it is a PRO-800 update made by PRO800_update_gen.
func updateMessages(_ path: String) -> [[UInt8]] {
    guard let data = FileManager.default.contents(atPath: path) else { fail("can't read \(path)") }
    var messages = [[UInt8]](), current = [UInt8]()
    for b in data {
        if b == 0xF0 {
            guard current.isEmpty else { fail("\(path): F0 inside a message") }
            current = [b]
        } else if current.isEmpty {
            fail("\(path): data outside a SysEx message")
        } else {
            current.append(b)
            if b == 0xF7 { messages.append(current); current = [] } else if b & 0x80 != 0 { fail("\(path): byte \(hex([b])) inside SysEx") }
        }
    }
    guard current.isEmpty else { fail("\(path): last message has no F7") }
    guard let first = messages.first, first.starts(with: sysexHeader + [0x0A]) else {
        fail("\(path): doesn't start with a PRO-800 update header (\(hex(sysexHeader + [0x0A])))")
    }
    guard messages.dropFirst().allSatisfy({ $0.starts(with: sysexHeader + [0x0B]) }) else {
        fail("\(path): expected only update data messages (0B) after the header")
    }
    return messages
}

func flash() {
    let rate = Double(option("--rate") ?? "3000") ?? 3000 // bytes/s; the bootloader takes up to 9640
    let eraseWait = Double(option("--erase-wait") ?? "3") ?? 3
    let confirmed = flag("--yes")
    let dryRun = flag("--dry-run")
    guard let path = args.first else { fail("usage: pro800 flash <file.syx> --yes [--rate B/s] [--erase-wait S] [--dry-run]") }
    guard rate > 0, rate <= 9000 else { fail("--rate must be 1-9000 bytes/s") }

    let messages = updateMessages(path)
    let total = messages.reduce(0) { $0 + $1.count }
    let seconds = Double(total) / rate + eraseWait
    print("\(path): \(messages.count) messages, \(total) bytes, about \(Int(seconds.rounded(.up))) s at \(Int(rate)) B/s")
    if dryRun { return }
    guard confirmed else {
        fail("""
        not sent. The PRO-800 must already be in update mode (hold buttons 0 and 7 at power-up, or run
        `pro800 update-mode`). Add --yes to send. If the update fails, the synth stays in update mode
        and a stock firmware .syx brings it back.
        """)
    }

    let midi = Midi(port: portName)
    _ = midi.takeSysex()
    for (i, m) in messages.enumerated() {
        midi.send(m)
        // the header comes first; give the bootloader time to erase the application flash
        Thread.sleep(forTimeInterval: Double(m.count) / rate + (i == 0 ? eraseWait : 0))
        for reply in midi.takeSysex() { print("\nreply after message \(i + 1): \(hex(reply))") }
        print("\rsent \(i + 1)/\(messages.count)", terminator: "")
        fflush(stdout)
    }
    Thread.sleep(forTimeInterval: 1)
    for reply in midi.takeSysex() { print("\nreply: \(hex(reply))") }
    print("\ndone. If the synth doesn't restart by itself, power-cycle it.")
}

// Waits while letting CoreMIDI process its notifications: without the run loop, a process that keeps running across
// a re-enumeration keeps sending to the old, dead endpoint.
func pump(_ seconds: Double) {
    RunLoop.main.run(until: Date().addingTimeInterval(seconds))
}

// update-mode, flash, then wait until the new firmware answers the version request
func install() {
    guard flag("--yes") else { fail("usage: pro800 install <file.syx> --yes (switches to update mode, flashes, waits)") }
    guard let path = args.first else { fail("usage: pro800 install <file.syx> --yes") }
    let messages = updateMessages(path) // check the file before touching the synth
    do {
        let midi = Midi(port: portName)
        midi.send(sysexHeader + [0x03, 0x30, 0xF7])
    }
    print("update mode requested; waiting for the bootloader")
    pump(4)
    args = [path, "--yes"]
    _ = messages
    flash()
    // a first boot after an update also autotunes (about 30 s) before it answers
    print("waiting for the new firmware (it tunes first, about 30 s)", terminator: "")
    for _ in 0..<75 {
        pump(1)
        print(".", terminator: ""); fflush(stdout)
        guard let midi = Midi(existing: portName) else { continue }
        if midi.request([0x08, 0x00], expect: [0x09, 0x00], timeout: 1) != nil {
            // macOS sets the port up once more shortly after it first appears: answer again after a pause
            pump(3)
            if let reply = Midi(existing: portName)?.request([0x08, 0x00], expect: [0x09, 0x00], timeout: 2) {
                print("\nrunning firmware \(reply.dropFirst(sysexHeader.count + 2).dropLast().map(String.init).joined(separator: "."))")
                return
            }
        }
    }
    fail("\nthe firmware didn't answer within 75 s")
}

// MARK: - Main

let command = args.isEmpty ? "help" : args.removeFirst()
switch command {
case "ports": ports()
case "version": version()
case "listen": listen()
case "backup": backup()
case "play": play()
case "record": record()
case "direct": direct()
case "cv": cv()
case "gate": gate()
case "measure": measure()
case "tune": tune()
case "tuning": tuning()
case "check": check()
case "dac-test": dacTest()
case "sh-waits": shWaits()
case "dac-heal": dacHeal()
case "panel": panel()
case "state": state()
case "set": setSetting()
case "settings": settings()
case "params": params()
case "param": param()
case "factory": factory()
case "seq": args.first == "set" ? seqSet() : args.first == "step" ? seqStep() : showSequence()
case "layer": layer()
case "eeprom": eeprom()
case "route": route()
case "routes": routes()
case "timing": timing()
case "cc": cc()
case "bend": bend()
case "eeprom-write": eepromWrite()
case "format": formatPatches()
case "patch": patchCommand()
case "patches": patches()
case "clock": clockOut()
case "leds": leds()
case "update-mode": updateMode()
case "flash": flash()
case "install": install()
default:
    print(helpText)
    if command != "help" && command != "-h" && command != "--help" { exit(1) }
}
