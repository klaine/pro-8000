// pro800: talks to a Behringer PRO-800 over USB MIDI (macOS, CoreMIDI).
//
// Read-only:
//   pro800 ports                          list MIDI ports
//   pro800 version                        firmware version
//   pro800 listen [seconds]               print everything the synth sends (default 20 s)
//   pro800 backup <dir> [first] [last]    dump system settings and patches 0-399 as .syx
//   pro800 play <note> [options]          play a note, optionally recording and measuring it
//       --channel N (1-16 or "all", default 6)  --hold S (default 2)  --velocity V (default 100)
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
  pro800 backup <dir> [first] [last]    dump system settings and patches 0-399 as .syx
  pro800 play <note> [options]          play a note, optionally recording and measuring it
      --channel N (1-16 or "all", default 6)  --hold S (default 2)  --velocity V (default 100)
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
  pro800 check [notes...]               play notes through the tables on every oscillator, print cents off

Changes the synth (only when you mean it):
  pro800 update-mode                    reboot into the bootloader's update mode
  pro800 flash <file.syx> --yes [--rate B/s] [--erase-wait S] [--dry-run]
                                        stream a firmware update to the bootloader
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

func patchLabel(_ n: Int) -> String { "\(["A", "B", "C", "D"][n / 100])\(String(format: "%02d", n % 100))" }

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
    let channelArg = option("--channel") ?? "6"
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

// Plays notes through the tables on every oscillator and measures them with TUNE_CPR; prints the error in cents.
func check() {
    let notes = args.isEmpty ? [36, 48, 60, 72, 84, 96] : args.compactMap { Int($0) }
    let midi = Midi(port: portName)
    func ack(_ data: [UInt8]) { probeAck(midi, data) }
    func setCV(_ name: String, _ value: Int) {
        ack([0x70, UInt8(cvNames.firstIndex(of: name)!), UInt8(value >> 14), UInt8((value >> 7) & 0x7F), UInt8(value & 0x7F)])
    }
    ack([0x74, 1])
    setCV("master", 0); setCV("res", 0); setCV("pwa", 0xFFFF); setCV("pwb", 0)
    for v in 1...8 { setCV("amp\(v)", 0); setCV("fil\(v)", 0xFFFF) }
    print("cents off per note: " + notes.map { String(format: "%6d", $0) }.joined())
    var worst = 0.0
    for osc in 0...1 {
        ack([0x71, 0, osc == 0 ? 1 : 0]); ack([0x71, 3, osc == 1 ? 1 : 0])
        setCV("vola", osc == 0 ? 0xFFFF : 0); setCV("volb", osc == 1 ? 0xFFFF : 0)
        for v in 1...8 {
            setCV("amp\(v)", 0xFFFF)
            var line = "osc \(osc == 0 ? "A" : "B")\(v)             "
            for note in notes {
                ack([0x78, UInt8(v - 1), UInt8(osc), UInt8(note)])
                let expected = 440 * pow(2, Double(note - 69) / 12)
                let periods = min(255, max(6, Int(expected * 0.08)))
                pump(0.03)
                // the comparator now and then reads an octave high below ~50 Hz: retry big errors, keep the best
                var best: Double?
                for _ in 0..<3 {
                    if let (hz, _) = measureOnce(midi, periods: periods, timeoutMs: 2047) {
                        let c = 1200 * log2(hz / expected)
                        if best == nil || abs(c) < abs(best!) { best = c }
                        if abs(c) < 50 { break }
                    }
                }
                if let cents = best {
                    worst = max(worst, abs(cents))
                    line += String(format: "%+6.1f", cents)
                } else {
                    line += "     -"
                }
            }
            print(line)
            setCV("amp\(v)", 0)
        }
    }
    ack([0x74, 0])
    print(String(format: "worst: %.1f cents", worst))
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
case "update-mode": updateMode()
case "flash": flash()
case "install": install()
default:
    print(helpText)
    if command != "help" && command != "-h" && command != "--help" { exit(1) }
}
