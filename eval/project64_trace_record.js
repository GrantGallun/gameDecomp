// Project64 4.x ES5 API. The runner prefixes `var traceJob = {...};`.
// Read-only recording of whole calls to ONE function: entry and exit registers,
// every RAM and device read/write, and each callee's entry arguments and return
// values. No game register or memory writes.
//
// Hooks are armed only between the function's entry and its return, so the
// game runs at normal interpreter speed outside the recorded window.
var job = traceJob, recorded = 0, seen = 0, active = null, ids = [];
var RAM = new AddressRange(0x80000000, 0x807FFFFF);
var DEVICE = new AddressRange(0xA0000000, 0xBFFFFFFF);
var BODY = new AddressRange(job.entry, job.end - 1);
var JAL = 0x0C000000, JAL_MASK = 0xFC000000;
var JALR = 0x00000009, JALR_MASK = 0xFC00003F;

function status(stage, extra) {
    var row = {schema_version: 1, stage: stage, recorded: recorded, seen: seen};
    for (var k in (extra || {})) row[k] = extra[k];
    fs.writefile(job.outputRoot + "trace-status.json", JSON.stringify(row));
}

function regs() {
    var lo = [], hi = [], i;
    for (i = 0; i < 32; i++) { lo.push(cpu.gpr[i] >>> 0); hi.push(cpu.ugpr[i] >>> 0); }
    return {pc: cpu.pc >>> 0, gpr: lo, ugpr: hi, hi: cpu.hi >>> 0, lo: cpu.lo >>> 0};
}

function disarm() {
    for (var i = 0; i < ids.length; i++) { try { events.remove(ids[i]); } catch (ignored) {} }
    ids = [];
}

function finish(reason) {
    disarm();
    active.reason = reason;
    fs.writefile(job.outputRoot + "call-" + active.index + ".json", JSON.stringify(active));
    active = null;
    recorded++;
    status(recorded >= job.max_calls ? "done" : "recording");
}

function access(kind) {
    return function (e) {
        if (!active) return;
        if (active.events.length >= job.max_events) { active.truncated = true; return; }
        // e.value is numeric for floats too; keep it raw and decode by valueType.
        active.events.push([kind, e.pc >>> 0, e.address >>> 0, e.valueType, e.value,
                            e.valueHi === undefined ? null : e.valueHi]);
    };
}

function once(address, fn) {
    var id = events.onexec(address, function (e) {
        try { events.remove(id); } catch (ignored) {}
        if (active) fn(e);
    });
    ids.push(id);
}

function call(target, site) {
    active.events.push(["c", site, target >>> 0]);
    // Arguments are final only at the callee's first instruction: the jal delay
    // slot commonly sets a0. Return values are read back at the return site.
    once(target >>> 0, function () {
        active.events.push(["e", cpu.pc >>> 0, cpu.gpr[4] >>> 0, cpu.gpr[5] >>> 0,
                            cpu.gpr[6] >>> 0, cpu.gpr[7] >>> 0, cpu.gpr[29] >>> 0]);
    });
    once((site + 8) >>> 0, function () {
        active.events.push(["x", cpu.pc >>> 0, cpu.gpr[2] >>> 0, cpu.gpr[3] >>> 0]);
    });
}

try {
    events.onexec(job.entry, function () {
        if (active || recorded >= job.max_calls) return;
        seen++;
        if ((seen - 1) % job.every !== 0) return;
        var sp0 = cpu.gpr[29] >>> 0, ra0 = cpu.gpr[31] >>> 0;
        active = {schema_version: 1, kind: "project64-call-trace", function: job.function,
                  entry_address: job.entry, end_address: job.end, index: recorded, entry_ordinal: seen,
                  rom_info: pj64.romInfo, entry: regs(), events: [], truncated: false,
                  // The API documents these names but not their numbers; record the
                  // live values so decoding never depends on a guessed enum order.
                  type_ids: {u8: u8, u16: u16, u32: u32, s8: s8, s16: s16, s32: s32,
                             f32: f32, f64: f64, u64: u64}};
        ids.push(events.onread(RAM, access("r")));
        ids.push(events.onwrite(RAM, access("w")));
        ids.push(events.onread(DEVICE, access("R")));
        ids.push(events.onwrite(DEVICE, access("W")));
        ids.push(events.onopcode(BODY, JAL, JAL_MASK, function (c) {
            if (!active) return;
            var op = mem.u32[c.pc] >>> 0;
            call(((c.pc >>> 28) * 0x10000000) + (op & 0x03FFFFFF) * 4, c.pc >>> 0);
        }));
        ids.push(events.onopcode(BODY, JALR, JALR_MASK, function (c) {
            if (!active) return;
            var op = mem.u32[c.pc] >>> 0;
            call(cpu.gpr[(op >>> 21) & 31] >>> 0, c.pc >>> 0);
        }));
        ids.push(events.onexec(ra0, function () {
            // The same return address can be reached by a nested frame; only
            // the frame that entered, with its stack pointer restored, ends it.
            if (!active || (cpu.gpr[29] >>> 0) !== sp0) return;
            active.exit = regs();
            finish("returned");
        }));
        status("recording", {active_entry: seen});
    });
    status("armed", {entry: job.entry});
} catch (error) {
    status("registration_error", {error: String(error)});
}
