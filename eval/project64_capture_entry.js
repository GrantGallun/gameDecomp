// Project64 6f7612b ES5 API. The runner prefixes a JSON-only captureJob.
// No game register/memory writes. Halt only a naturally reached selected entry.
var requested = false, finished = false, eventsSeen = [];
function status(value) {
    fs.writefile(captureJob.outputRoot + "bridge-status.json", JSON.stringify(value));
}
function hexblock(address, size) {
    var bytes = mem.getblock(address, size), parts = [];
    for (var i = 0; i < size; i++) parts.push(("0" + bytes[i].toString(16)).slice(-2));
    return parts.join("");
}
function snapshot() {
    var lower = [], upper = [], memory = [], i, row;
    for (i = 0; i < 32; i++) {
        lower.push(cpu.gpr[i] >>> 0);
        upper.push(cpu.ugpr[i] >>> 0);
    }
    for (i = 0; i < captureJob.plan.ram.length; i++) {
        row = captureJob.plan.ram[i];
        memory.push({name: row.name, kind: row.kind, address: row.address,
            size: row.size, hex: hexblock(row.address, row.size)});
    }
    return {paused: debug.paused, pc: cpu.pc >>> 0,
        hi: cpu.hi >>> 0, lo: cpu.lo >>> 0, uhi: cpu.uhi >>> 0, ulo: cpu.ulo >>> 0,
        gpr: lower, ugpr: upper, memory: memory,
        code_hex: hexblock(captureJob.plan.entry, captureJob.plan.code_size)};
}
status({stage: "script_started"});
try {
    events.onexec(captureJob.plan.entry, function(e) {
        if (requested || finished) return;
        if (captureJob.selector === "a0_nonzero" && (cpu.gpr[4] >>> 0) === 0) return;
        requested = true;
        status({stage: "entry_hook", pc: e.pc >>> 0});
        debug.breakhere(true);
    });
    events.onstatechange(function(e) {
        if (eventsSeen.length < 256) eventsSeen.push(e.state);
        if (!requested || finished || e.state !== EMU_DEBUG_PAUSED) return;
        finished = true;
        try {
            var first = snapshot(), second = snapshot();
            fs.writefile(captureJob.outputRoot + "project64-entry-raw.json", JSON.stringify({
                schema_version: 1, kind: "project64-debug-paused-export",
                producer_revision: "6f7612b", entry: captureJob.plan.entry,
                rom_info: pj64.romInfo, events: eventsSeen, samples: [first, second]}));
            status({stage: "captured", pc: first.pc, paused: first.paused});
        } catch (error) {
            status({stage: "capture_error", error: String(error)});
        }
    });
    status({stage: "armed", entry: captureJob.plan.entry});
} catch (error) {
    status({stage: "registration_error", error: String(error)});
}
