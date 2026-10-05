# Local web merge generator

The new `local_web_merge` family proposes a merge only for two plain,
uninitialized, compatible pointer locals declared at function scope when every
use of one is inside the braced `if` arm and every use of the other is inside
the corresponding braced `else` arm. It declines mismatched types, qualifiers
that change lifetime or mutability, arrays, shadowed names, address escapes,
member accesses, and syntax it cannot safely associate with the two arms. The
stream caps the family at 12 candidates per function. Compiler scoring and the
byte certificate remain the acceptance gates.

`tests/test_local_web_merge.py` covers a firing example, regular-stream wiring,
and decline cases. The focused command passed on Windows:

```text
python -m pytest tests/test_local_web_merge.py tests/test_regalloc_mutations.py -q
52 passed
```

`score_generated.py` is prepared to read only retained attempt 108368 from the
private campaign database, require the generator output hash recorded by the
protocol, and ordinary-score a source-bound parent and child in a separate
private database. The private WSL environment was unavailable from this agent
(`wsl.exe --list --verbose` returned `Wsl/EnumerateDistros/Service/E_ACCESSDENIED`),
so no ordinary scoring receipt is claimed here. Run the script in the configured
WSL environment to produce `result.json`.
