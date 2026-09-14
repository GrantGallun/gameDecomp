"""Valid allocation/list states from target loads/stores, including boundaries."""
from solver.mips_differential import TestCase, PLAYER_BASE


def cases():
    rows = []
    # Global halfword counters; pool word; node next +4, priority +14,
    # active +22 are observed target memory operations.
    def add(kind, priority, values, exhausted=False, empty_pool=False):
        writes = [(f'gFreeCallbackTaskType{i}Count', 2, 0 if exhausted else 2) for i in range(7)]
        writes += [('gFreeCallbackTaskCount', 2, 0 if empty_pool else 1),
                   ('gFreeCallbackTaskPool', 4, PLAYER_BASE),
                   ('gCallbackTaskActiveListSentinel+0x4', 4, PLAYER_BASE + 256 if values else 0)]
        memory = [(22, 2, 0xABCD)]
        for i, value in enumerate(values):
            offset = 256 + i * 32
            memory += [(offset, 4, 0), (offset + 4, 4, PLAYER_BASE + offset + 32 if i + 1 < len(values) else 0),
                       (offset + 14, 2, value)]
        rows.append(TestCase(f'callback-{len(rows)}-type{kind}-priority{priority}-list{len(values)}',
            73000 + len(rows), tuple(memory), tuple(writes),
            (('a0', 0x12345678), ('a1', kind), ('a2', priority))))
    for kind in range(7):
        for values in ((), (0,), (100,), (100, 50, 0)):
            add(kind, 50, values)
        add(kind, 0, (), exhausted=True)
        add(kind, 0, (), empty_pool=True)
    for priority in (-1, 0, 1, 32767, 32768, 65535, 65536):
        for values in ((), (0,), (65535,), (65535, 32768, 0)):
            add(0, priority, values)
    for kind in (7, 255, 256, 257, 0x10006, 0xFFFF):
        add(kind, 50, (100, 0))
    return tuple(rows)
