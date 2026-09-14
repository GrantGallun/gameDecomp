import struct

from solver import byte_certificate as cert


def elf(text=b"\x03\xe0\x00\x08\x00\x00\x00\x00", *, rodata=b"", symbol="gValue"):
    names = b"\0.text\0.rodata\0.rel.text\0.symtab\0.strtab\0.shstrtab\0"
    strings = b"\0" + symbol.encode() + b"\0"
    rows = [(0,) * 10]
    payload = bytearray(b"\0" * 52)
    for name, kind, flags, data, link, info, align, entsize in (
        (".text", 1, 6, text, 0, 0, 16, 0),
        (".rodata", 1, 2, rodata, 0, 0, 4, 0),
        (".rel.text", 9, 0, struct.pack(">II", 0, (1 << 8) | 2), 4, 1, 4, 8),
        (".symtab", 2, 0, b"\0" * 16 + struct.pack(">IIIBBH", 1, 0, 0, 16, 0, 0), 5, 1, 4, 16),
        (".strtab", 3, 0, strings, 0, 0, 1, 0),
        (".shstrtab", 3, 0, names, 0, 0, 1, 0),
    ):
        rows.append((names.index(name.encode()), kind, flags, 0, len(payload),
                     len(data), link, info, align, entsize))
        payload.extend(data)
    shoff = len(payload)
    for row in rows:
        payload.extend(struct.pack(">IIIIIIIIII", *row))
    payload[:16] = b"\x7fELF\x01\x02\x01" + b"\0" * 9
    struct.pack_into(">HHIIIIIHHHHHH", payload, 16,
                     1, 8, 1, 0, 0, shoff, 0, 52, 0, 0, 40, len(rows), 6)
    return bytes(payload)


def test_certificate_checks_padding_data_and_symbol_identity(tmp_path):
    target, candidate = tmp_path / "target.o", tmp_path / "candidate.o"
    target.write_bytes(elf())
    candidate.write_bytes(elf())
    assert cert.certify(target, candidate, source="C")["exact"]
    for changed in (elf(text=b"\x03\xe0\x00\x08"), elf(rodata=b"abcd"),
                    elf(symbol="wrongGlobal"), b"bad ELF"):
        candidate.write_bytes(changed)
        receipt = cert.certify(target, candidate, source="C")
        assert not receipt["exact"]
        assert not receipt["whole_rom_verified"]


def test_missing_object_is_unverified(tmp_path):
    assert cert.certify(tmp_path / "missing", tmp_path / "missing2", source="")["status"] == "unverified"


def test_independent_relocation_pairs_commute_but_pairing_is_preserved():
    a, b, call = ("external", "a", 1, 0), ("external", "b", 1, 0), ("external", "call", 1, 0)
    pair_a = [(0, 5, a), (16, 6, a)]
    pair_b = [(4, 5, b), (20, 6, b)]
    jal = [(8, 4, call)]
    target = pair_a + jal + pair_b
    assert cert.independent_relocation_groups(target) == cert.independent_relocation_groups(pair_b + pair_a + jal)
    # Even the same symbol/entry multiset cannot authorize different pairs.
    same_symbol = [(0, 5, a), (16, 6, a), (4, 5, a), (20, 6, a)]
    crossed = [(0, 5, a), (20, 6, a), (4, 5, a), (16, 6, a)]
    assert cert.independent_relocation_groups(same_symbol) != cert.independent_relocation_groups(crossed)
    for unsupported in ([(0, 5, a)], [(0, 6, a)], [(0, 5, a), (4, 5, a), (8, 6, a)],
                        [(0, 5, a), (4, 6, b)], pair_a + [(0, 4, call)], [(2, 4, call)],
                        [(0, 7, a)], [(0, 2, ("section", ".text", 0, 0, 0))]):
        assert cert.independent_relocation_groups(unsupported) is None


def test_relocation_group_comparison_never_ignores_bytes_or_symbols():
    a = ("external", "a", 1, 0)
    call = ("external", "call", 1, 0)
    rels = [(0, 5, a), (4, 6, a), (8, 4, call)]
    left = {".text": {"sha256": "same-bytes", "size": 16, "relocations": rels}}
    right = {".text": {**left[".text"], "relocations": [rels[2], *rels[:2]]}}
    assert cert.sections_equivalent(left, right)
    for field, changed in (("sha256", "different"), ("size", 32),
            ("relocations", [rels[2], rels[0], (4, 6, ("external", "wrong", 1, 0))])):
        bad = {".text": {**right[".text"], field: changed}}
        assert not cert.sections_equivalent(left, bad)


def test_workspace_requires_bytes_not_just_exact_banner(tmp_path, monkeypatch):
    from solver import workspace
    monkeypatch.setattr(workspace, "_candidate_compile_source", lambda repo, code: code)
    monkeypatch.setattr(workspace, "sh", lambda *a, **kw: (
        0, "Score: 100.0%\nVerified exact match: yes\n"))
    (tmp_path / "target.o").write_bytes(elf())
    attempt = workspace.score(tmp_path, tmp_path, "probe", "int f(void){return 0;}")
    assert attempt.compiled and attempt.score == 100 and not attempt.exact
    (tmp_path / "probe.o").write_bytes(elf())
    attempt = workspace.score(tmp_path, tmp_path, "probe", "int f(void){return 0;}")
    assert attempt.exact and attempt.verification["status"] == "object_sections_exact"
    assert (tmp_path / "probe.verification.json").is_file()


def test_failed_build_cannot_reuse_stale_score_banner(tmp_path, monkeypatch):
    from solver import workspace
    monkeypatch.setattr(workspace, "_candidate_compile_source", lambda repo, code: code)
    monkeypatch.setattr(workspace, "sh", lambda *a, **kw: (
        1, "Score: 100.0%\nVerified exact match: yes\n"))
    (tmp_path / "target.o").write_bytes(elf())
    (tmp_path / "probe.o").write_bytes(elf())
    assert not workspace.score(tmp_path, tmp_path, "probe", "C").compiled
