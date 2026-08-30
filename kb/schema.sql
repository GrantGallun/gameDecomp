-- Autodecomp knowledge base.
--
-- Two tiers, and the separation is the whole point of the design:
--   evidence  - mechanically derived from the binary. Never wrong. Never written
--               by a model. Immutable once extracted.
--   inference - claims about types and names. Retractable. Every row must cite
--               evidence via inference_support.
--
-- See DESIGN.md. Invariants 3, 4 and 5 are enforced here and in kb/tms.py.

PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------- provenance

-- One row per extraction run, so every fact can be traced to the exact binary
-- and toolchain that produced it.
CREATE TABLE IF NOT EXISTS extraction (
    id            INTEGER PRIMARY KEY,
    target        TEXT    NOT NULL,      -- 'sbk1'
    rom_sha1      TEXT    NOT NULL,      -- ground truth the evidence came from
    elf_path      TEXT    NOT NULL,
    tool_versions TEXT    NOT NULL,      -- JSON
    created_at    INTEGER NOT NULL
);


-- ---------------------------------------------------------------- structure

CREATE TABLE IF NOT EXISTS tus (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE, -- 'src/race/items/race_item_effects.c'
    object_path TEXT,                    -- 'build/src/race/items/race_item_effects.o'
    start_addr  INTEGER,
    end_addr    INTEGER,
    compiler    TEXT,
    flags       TEXT
);

CREATE TABLE IF NOT EXISTS functions (
    addr        INTEGER PRIMARY KEY,     -- vram
    name        TEXT    NOT NULL,
    tu_id       INTEGER REFERENCES tus(id),
    size        INTEGER,                 -- bytes
    insn_count  INTEGER,
    is_leaf     INTEGER,                 -- 1 if it makes no calls
    state       TEXT    NOT NULL DEFAULT 'asm',   -- asm | attempted | matched
    best_score  REAL    DEFAULT NULL,    -- asm-differ score; lower is better, 0 = match
    attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS fn_tu    ON functions(tu_id);
CREATE INDEX IF NOT EXISTS fn_state ON functions(state);
CREATE INDEX IF NOT EXISTS fn_leaf  ON functions(is_leaf);


-- ---------------------------------------------------------------- evidence

-- Facts about the binary. A 'lbu' at offset 0x24 means width 1, unsigned, int
-- class. That is observation, not interpretation.
--
-- Nullability is meaningful here. `signed` is NULL for stores because a store
-- carries no signedness information -- recording 0 would be a false claim.
-- Invariant 5: unknown must be representable.
CREATE TABLE IF NOT EXISTS evidence (
    id          INTEGER PRIMARY KEY,
    extraction_id INTEGER NOT NULL REFERENCES extraction(id),
    kind        TEXT    NOT NULL,   -- mem_access | call
    addr        INTEGER NOT NULL,   -- instruction vram
    func_addr   INTEGER REFERENCES functions(addr),
    op          TEXT    NOT NULL,   -- mnemonic

    -- mem_access only
    base        TEXT,               -- param0..param3 | stack | global:0x8004dc6c | unknown
    base_reg    TEXT,               -- raw register name, always recorded
    offset      INTEGER,
    width       INTEGER,            -- 1 | 2 | 4 | 8
    signed      INTEGER,            -- 1 | 0 | NULL (NULL = not determinable, e.g. stores)
    class       TEXT,               -- int | float
    access      TEXT,               -- full | partial  (lwl/lwr/swl/swr are partial)
    is_load     INTEGER,

    -- call only
    target_addr INTEGER,

    UNIQUE(addr, kind)
);

-- The contradiction sweep groups by (base, offset); this index is its hot path.
CREATE INDEX IF NOT EXISTS ev_base_off ON evidence(base, offset);
CREATE INDEX IF NOT EXISTS ev_func     ON evidence(func_addr);
CREATE INDEX IF NOT EXISTS ev_kind     ON evidence(kind);
CREATE INDEX IF NOT EXISTS ev_target   ON evidence(target_addr);


-- ---------------------------------------------------------------- inference

CREATE TABLE IF NOT EXISTS inference (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL,   -- field | signature | symbol_name | tu_assign | struct_size
    subject     TEXT    NOT NULL,   -- 'struct:Actor@0x24' | 'func:0x8004dc6c'
    value       TEXT    NOT NULL,   -- JSON
    confidence  REAL,
    origin      TEXT    NOT NULL,   -- human | model | miner
    status      TEXT    NOT NULL DEFAULT 'active',  -- active | retracted
    created_at  INTEGER NOT NULL,
    retracted_at        INTEGER,
    retraction_reason   TEXT
);

CREATE INDEX IF NOT EXISTS inf_subject ON inference(subject, status);
CREATE INDEX IF NOT EXISTS inf_status  ON inference(status);

-- Justification. Invariant 4: no citation, no commit. Enforced in kb/tms.py,
-- which refuses to insert an inference without at least one support row --
-- except for origin='human', where the provenance is the pre-existing repo.
CREATE TABLE IF NOT EXISTS inference_support (
    inference_id INTEGER NOT NULL REFERENCES inference(id) ON DELETE CASCADE,
    evidence_id  INTEGER NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (inference_id, evidence_id)
);

-- Claims built on other claims. Retraction walks this transitively.
CREATE TABLE IF NOT EXISTS inference_depends (
    inference_id  INTEGER NOT NULL REFERENCES inference(id) ON DELETE CASCADE,
    depends_on_id INTEGER NOT NULL REFERENCES inference(id),
    PRIMARY KEY (inference_id, depends_on_id)
);

-- THE critical table: how retraction knows what to rebuild. Without it the
-- system cannot detect its own poisoning.
CREATE TABLE IF NOT EXISTS func_deps (
    func_addr    INTEGER NOT NULL REFERENCES functions(addr),
    inference_id INTEGER NOT NULL REFERENCES inference(id),
    PRIMARY KEY (func_addr, inference_id)
);

CREATE INDEX IF NOT EXISTS fd_inf ON func_deps(inference_id);


-- ---------------------------------------------------------------- sweep

CREATE TABLE IF NOT EXISTS contradictions (
    id       INTEGER PRIMARY KEY,
    subject  TEXT    NOT NULL,     -- 'param0@0x24'
    kind     TEXT    NOT NULL,     -- width | signedness | class | overlap | oversize
    detail   TEXT    NOT NULL,     -- JSON: the conflicting evidence ids and values
    status   TEXT    NOT NULL DEFAULT 'open',   -- open | resolved | dismissed
    found_at INTEGER NOT NULL,
    UNIQUE(subject, kind)
);


-- ---------------------------------------------------------------- trajectory

-- Every attempt, including failures and regressions. Debugging record now,
-- training set later. See TRAINING.md -- never prune this table, and never
-- overwrite a row: the (n -> n+1) pairing is what makes it refinement data.
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY,
    func_addr     INTEGER NOT NULL REFERENCES functions(addr),
    iteration     INTEGER NOT NULL,
    source_code   TEXT    NOT NULL,
    prompt_context TEXT,            -- exact context, or hash + rebuild recipe
    compiled      INTEGER NOT NULL,
    compiler_stderr TEXT,
    score         REAL,             -- asm-differ similarity, 0..100; not proof
    exact         INTEGER,          -- verifier verdict; NULL = historical unknown
    diff_summary  TEXT,             -- full instruction diff, not just the number
    strategy      TEXT,
    model         TEXT,
    sampling      TEXT,             -- JSON
    wall_ms       INTEGER,
    token_cost    INTEGER,
    created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS att_func ON attempts(func_addr, iteration);
