// Project64 4.x ES5 API. The runner prefixes `var traceJob = {...};`.
// Read-only: counts call targets reached while the game runs untouched. No game
// register or memory writes. A function never counted here cannot be recorded
// by a session that follows the same (input-free) path.
var counts = {}, calls = 0, started = Date.now(), last = 0;
var CODE = new AddressRange(0x80000000, 0x807FFFFF);
var JAL = 0x0C000000, JAL_MASK = 0xFC000000;
var JALR = 0x00000009, JALR_MASK = 0xFC00003F;

function flush(stage) {
    fs.writefile(traceJob.outputRoot + "coverage.json", JSON.stringify({
        schema_version: 1, kind: "project64-call-coverage", stage: stage,
        rom_info: pj64.romInfo, elapsed_ms: Date.now() - started,
        calls: calls, targets: counts}));
}

function count(target) {
    var key = (target >>> 0).toString(16);
    counts[key] = (counts[key] || 0) + 1;
    calls++;
}

try {
    events.onopcode(CODE, JAL, JAL_MASK, function (e) {
        var op = mem.u32[e.pc] >>> 0;
        // Arithmetic, not shifts: JS bitwise ops are signed 32-bit.
        count(((e.pc >>> 28) * 0x10000000) + (op & 0x03FFFFFF) * 4);
    });
    events.onopcode(CODE, JALR, JALR_MASK, function (e) {
        var op = mem.u32[e.pc] >>> 0;
        count(cpu.gpr[(op >>> 21) & 31] >>> 0);
    });
    setInterval(function () {
        if (calls !== last) { last = calls; flush("running"); }
    }, 5000);
    flush("armed");
} catch (error) {
    fs.writefile(traceJob.outputRoot + "coverage.json", JSON.stringify({
        schema_version: 1, kind: "project64-call-coverage", stage: "registration_error",
        error: String(error)}));
}
