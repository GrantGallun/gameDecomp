fs.writefile("C:/Code/gameDecomp/eval/results/runtime-capture-20260912/nonzero/script-started.json", "{\"stage\":\"script_started\"}");
// Project64 4.x API, pinned to official development build 6f7612b.
// Observe one genuine entry; never alter N64 registers, RAM, or instructions.
var outputRoot = "C:/Code/gameDecomp/eval/results/runtime-capture-20260912/nonzero/";
var selectedEntry = 0x80043040;
var requested = false;
var finished = false;
var eventsSeen = [];
function status(value) {
    fs.writefile(outputRoot + "bridge-status.json", JSON.stringify(value));
}
function hexblock(address, size) { var bytes = mem.getblock(address, size); var out = []; for (var j = 0; j < size; j++) out.push(("0" + bytes[j].toString(16)).slice(-2)); return out.join(""); }
function snapshot() {
    var lower = [], upper = [];
    for (var i = 0; i < 32; i++) {
        lower.push(cpu.gpr[i] >>> 0);
        upper.push(cpu.ugpr[i] >>> 0);
    }
    return {
        paused: debug.paused,
        pc: cpu.pc >>> 0, hi: cpu.hi >>> 0, lo: cpu.lo >>> 0,
        uhi: cpu.uhi >>> 0, ulo: cpu.ulo >>> 0,
        gpr: lower, ugpr: upper,
        code_hex: hexblock(selectedEntry, 32),
        memory: [{name: "heap_alias_table_window", kind: "persistent", address: 0x80110000,
                  size: 0x20000, hex: hexblock(0x80110000, 0x20000)}]
    };
}
try { events.onexec(selectedEntry, function(e) {
    if (requested || finished || (cpu.gpr[4] >>> 0) === 0) return;
    requested = true;
    status({stage: "entry_hook", pc: e.pc >>> 0});
    debug.breakhere(true);
});
events.onstatechange(function(e) {
    eventsSeen.push(e.state);
    if (requested && !finished && e.state === EMU_DEBUG_PAUSED) {
        finished = true;
        try {
            var first = snapshot();
            var second = snapshot();
            fs.writefile(outputRoot + "project64-entry-raw.json", JSON.stringify({
                schema_version: 1, kind: "project64-debug-paused-export",
                producer_revision: "6f7612b", entry: selectedEntry,
                rom_info: pj64.romInfo, events: eventsSeen,
                samples: [first, second]
            }));
            status({stage: "captured", pc: first.pc, paused: first.paused});
        } catch (error) {
            status({stage: "capture_error", error: String(error)});
        }
    }
});
status({stage: "armed", entry: selectedEntry}); } catch (error) { status({stage: "registration_error", error: String(error), stack: error.stack}); }





