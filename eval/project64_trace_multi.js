// Project64 4.x ES5 API. The runner prefixes `var traceJob = {...};`.
// Read-only recording of whole calls to MANY functions in one session. Same
// per-call schema as project64_trace_record.js ("project64-call-trace").
//
// Windows nest: while A is being recorded, an entry to B starts B's own frame,
// and B's accesses land in both frames (for A they are callee effects). Memory
// hooks are armed only while at least one frame is open. A frame that exceeds
// max_events or max_window_ms is abandoned, never written truncated; a function
// abandoned `max_abandons` times is disabled so it cannot stall the session.
var job = traceJob, started = Date.now();
var RAM = new AddressRange(0x80000000, 0x807FFFFF);
var DEVICE = new AddressRange(0xA0000000, 0xBFFFFFFF);
var JAL = 0x0C000000, JAL_MASK = 0xFC000000;
var JALR = 0x00000009, JALR_MASK = 0xFC00003F;
var TYPE_IDS = {u8: u8, u16: u16, u32: u32, s8: s8, s16: s16, s32: s32, f32: f32, f64: f64, u64: u64};
var frames = [], shared = [], state = {}, lastWrite = 0;

function status(stage, extra) {
    var row = {schema_version: 1, stage: stage, elapsed_ms: Date.now() - started, open_frames: frames.length,
               functions: state};
    for (var k in (extra || {})) row[k] = extra[k];
    fs.writefile(job.outputRoot + "trace-status.json", JSON.stringify(row));
}

function regs() {
    var lo = [], hi = [], i;
    for (i = 0; i < 32; i++) { lo.push(cpu.gpr[i] >>> 0); hi.push(cpu.ugpr[i] >>> 0); }
    return {pc: cpu.pc >>> 0, gpr: lo, ugpr: hi, hi: cpu.hi >>> 0, lo: cpu.lo >>> 0};
}

function removeAll(ids) {
    for (var i = 0; i < ids.length; i++) { try { events.remove(ids[i]); } catch (ignored) {} }
}

function access(kind) {
    return function (e) {
        for (var i = 0; i < frames.length; i++) {
            var f = frames[i];
            if (f.events.length >= job.max_events) { f.overflow = true; continue; }
            f.events.push([kind, e.pc >>> 0, e.address >>> 0, e.valueType, e.value,
                           e.valueHi === undefined ? null : e.valueHi]);
        }
    };
}

function armShared() {
    shared.push(events.onread(RAM, access("r")));
    shared.push(events.onwrite(RAM, access("w")));
    shared.push(events.onread(DEVICE, access("R")));
    shared.push(events.onwrite(DEVICE, access("W")));
}

function close(frame, outcome) {
    removeAll(frame.ids);
    var index = frames.indexOf(frame);
    if (index >= 0) frames.splice(index, 1);
    if (!frames.length) { removeAll(shared); shared = []; }
    var s = state[frame.fn.name];
    if (outcome === "returned" && !frame.overflow) {
        frame.record.exit = regs();
        frame.record.reason = "returned";
        frame.record.events = frame.events;
        fs.writefile(job.outputRoot + "call-" + frame.fn.name + "-" + s.recorded + ".json",
                     JSON.stringify(frame.record));
        s.recorded++;
    } else {
        s.abandoned++;
        s.last_abandon = frame.overflow ? "max_events" : outcome;
        if (s.abandoned >= job.max_abandons) s.disabled = true;
    }
}

function once(frame, address, fn) {
    var id = events.onexec(address >>> 0, function () {
        try { events.remove(id); } catch (ignored) {}
        if (frames.indexOf(frame) >= 0) fn();
    });
    frame.ids.push(id);
}

function call(frame, target, site) {
    frame.events.push(["c", site, target >>> 0]);
    once(frame, target, function () {
        frame.events.push(["e", cpu.pc >>> 0, cpu.gpr[4] >>> 0, cpu.gpr[5] >>> 0,
                           cpu.gpr[6] >>> 0, cpu.gpr[7] >>> 0, cpu.gpr[29] >>> 0]);
    });
    once(frame, site + 8, function () {
        frame.events.push(["x", cpu.pc >>> 0, cpu.gpr[2] >>> 0, cpu.gpr[3] >>> 0]);
    });
}

function open(fn) {
    var s = state[fn.name];
    var sp0 = cpu.gpr[29] >>> 0, ra0 = cpu.gpr[31] >>> 0;
    var frame = {fn: fn, ids: [], events: [], overflow: false, opened: Date.now(),
                 record: {schema_version: 1, kind: "project64-call-trace", function: fn.name,
                          entry_address: fn.entry, end_address: fn.end, index: s.recorded,
                          entry_ordinal: s.seen, rom_info: pj64.romInfo, entry: regs(),
                          truncated: false, type_ids: TYPE_IDS, session: "multi"}};
    if (!frames.length) armShared();
    frames.push(frame);
    var body = new AddressRange(fn.entry, fn.end - 1);
    frame.ids.push(events.onopcode(body, JAL, JAL_MASK, function (c) {
        var op = mem.u32[c.pc] >>> 0;
        call(frame, ((c.pc >>> 28) * 0x10000000) + (op & 0x03FFFFFF) * 4, c.pc >>> 0);
    }));
    frame.ids.push(events.onopcode(body, JALR, JALR_MASK, function (c) {
        var op = mem.u32[c.pc] >>> 0;
        call(frame, cpu.gpr[(op >>> 21) & 31] >>> 0, c.pc >>> 0);
    }));
    frame.ids.push(events.onexec(ra0, function () {
        // Only the frame that entered, with its stack pointer restored, ends it.
        if ((cpu.gpr[29] >>> 0) === sp0) close(frame, "returned");
    }));
}

function finished() {
    for (var name in state) {
        if (!state[name].disabled && state[name].recorded < job.max_calls) return false;
    }
    return true;
}

try {
    for (var i = 0; i < job.functions.length; i++) {
        (function (fn) {
            state[fn.name] = {recorded: 0, seen: 0, abandoned: 0, disabled: false};
            events.onexec(fn.entry, function () {
                var s = state[fn.name];
                if (s.disabled || s.recorded >= job.max_calls) return;
                for (var j = 0; j < frames.length; j++) {
                    if (frames[j].fn === fn) return;          // recursion stays inside the open frame
                }
                if (frames.length >= job.max_depth) return;
                s.seen++;
                if ((s.seen - 1) % fn.every !== 0) return;
                open(fn);
            });
        })(job.functions[i]);
    }
    setInterval(function () {
        var now = Date.now();
        for (var k = frames.length - 1; k >= 0; k--) {
            if (now - frames[k].opened > job.max_window_ms || frames[k].overflow) close(frames[k], "window_limit");
        }
        if (finished()) { status("done"); return; }
        if (now - lastWrite > 5000) { lastWrite = now; status("recording"); }
    }, 1000);
    status("armed");
} catch (error) {
    status("registration_error", {error: String(error)});
}
