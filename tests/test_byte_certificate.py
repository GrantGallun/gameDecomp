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


def _words(*immediates):
    """Text bytes: one 4-byte word per entry, the low halfword holding the given immediate."""
    return b"".join(b"\x3c\x0e" + struct.pack(">H", imm) for imm in immediates)


def test_same_addend_pairing_accepts_crossed_pairs_of_one_symbol():
    # osCreateMesgQueue (reloc-pairing-20260924): two lui/%lo pairs of one symbol, identical immediates;
    # target lists (HI@4, LO@8), (HI@0, LO@12); IDO lists (HI@0, LO@8), (HI@4, LO@12). Same linked bytes.
    a = ("external", "__osThreadTail", 1, 0)
    text = _words(0, 0, 0, 0)
    target = [(4, 5, a), (8, 6, a), (0, 5, a), (12, 6, a)]
    ido = [(0, 5, a), (8, 6, a), (4, 5, a), (12, 6, a)]
    assert cert.independent_relocation_groups(target) != cert.independent_relocation_groups(ido)   # strict: differ
    assert cert.same_addend_pairing_groups(target, text) == cert.same_addend_pairing_groups(ido, text)
    left = {".text": {"sha256": "same", "size": 16, "relocations": target}}
    right = {".text": {"sha256": "same", "size": 16, "relocations": ido}}
    assert cert.pairing_equivalent(left, right, {".text": text}, {".text": text})


def test_same_addend_pairing_declines_different_addends_symbols_or_bytes():
    a, b = ("external", "a", 1, 0), ("external", "b", 1, 0)
    crossed = [(0, 5, a), (12, 6, a), (4, 5, a), (8, 6, a)]
    # `a+4` and `a+8`: the LO16 immediates differ, so pairing decides the linked value -> strict (None)
    assert cert.same_addend_pairing_groups(crossed, _words(0, 0, 4, 8)) is None
    # different symbols are never merged
    mixed = [(0, 5, a), (8, 6, a), (4, 5, b), (12, 6, b)]
    other = [(0, 5, b), (8, 6, b), (4, 5, a), (12, 6, a)]
    text = _words(0, 0, 0, 0)
    assert cert.same_addend_pairing_groups(mixed, text) != cert.same_addend_pairing_groups(other, text)
    # different bytes are never excused by relocation normalization
    target = [(4, 5, a), (8, 6, a), (0, 5, a), (12, 6, a)]
    ido = [(0, 5, a), (8, 6, a), (4, 5, a), (12, 6, a)]
    left = {".text": {"sha256": "one", "size": 16, "relocations": target}}
    right = {".text": {"sha256": "two", "size": 16, "relocations": ido}}
    assert not cert.pairing_equivalent(left, right, {".text": text}, {".text": text})


def _hi_lo_text(lo_opcode=0x31):
    """lui at,0 ; lwc1 $f4,0(at): HI16 at 0, LO16 at 4 (opcode in the top 6 bits of the LO16 word)."""
    return b"\x3c\x01\x00\x00" + bytes([lo_opcode << 2, 0x24, 0x00, 0x00])


def test_rodata_references_compare_by_the_bytes_they_read():
    text = _hi_lo_text()
    late = ("section", ".late_rodata", 0, 0, 0)
    ro = ("section", ".rodata", 0, 0, 0)
    target = [(0, 5, late), (4, 6, late)]
    cand = [(0, 5, ro), (4, 6, ro)]
    left = {".text": {"sha256": "same", "size": 8, "relocations": target}}
    right = {".text": {"sha256": "same", "size": 8, "relocations": cand}}
    four_thirds, minus = struct.pack(">f", 4 / 3), struct.pack(">f", -4 / 3)
    ok_left = {".text": text, ".late_rodata": four_thirds + b"\0" * 12}
    assert cert.rodata_equivalent(left, right, ok_left, {".text": text, ".rodata": four_thirds})
    # the sign-flipped literal (initControllerPakFileDeleteFlow's draft) is REJECTED: values are compared now
    assert not cert.rodata_equivalent(left, right, ok_left, {".text": text, ".rodata": minus})
    # unresolvable reference (section too short) declines
    assert not cert.rodata_equivalent(left, right, ok_left, {".text": text, ".rodata": b"\0\0"})
    # non-rodata differences still decide: a different external symbol is never excused
    bad = {".text": {"sha256": "same", "size": 8, "relocations": [(0, 5, ("external", "x", 1, 0)),
                                                                   (4, 6, ("external", "x", 1, 0))]}}
    assert not cert.rodata_equivalent(left, bad, ok_left, {".text": text})


def test_candidate_rodata_pool_matches_target_late_rodata_only_byte_for_byte():
    text = _hi_lo_text()
    late = ("section", ".late_rodata", 0, 1, 0)
    ro = ("section", ".rodata", 0, 0, 0)
    pool = struct.pack(">f", 4 / 3) + bytes(12)
    left = {".text": {"sha256": "same", "size": 8, "relocations": [(0, 5, late), (4, 6, late)]}}
    right = {".text": {"sha256": "same", "size": 8, "relocations": [(0, 5, ro), (4, 6, ro)]},
             ".rodata": {"sha256": "pool", "size": 16, "relocations": []}}
    target_raw = {".text": text, ".late_rodata": pool}
    assert cert.rodata_equivalent(left, right, target_raw, {".text": text, ".rodata": pool})
    wrong = struct.pack(">f", -4 / 3) + bytes(12)
    assert not cert.rodata_equivalent(left, right, target_raw, {".text": text, ".rodata": wrong})
    padded = pool + b"\x00\x00\x00\x01"
    assert not cert.rodata_equivalent(left, right, target_raw, {".text": text, ".rodata": padded})
