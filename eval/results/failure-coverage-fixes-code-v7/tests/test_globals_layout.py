"""Tests for global data-object reconstruction."""

import sqlite3

from miner import globals_layout as gl


def _db(rows):
    """rows: (base, offset, width, signed, func_addr)"""
    c = sqlite3.connect(":memory:")
    c.execute("create table evidence (kind text, base text, offset int,"
              " width int, signed int, func_addr int)")
    c.executemany("insert into evidence values ('mem_access',?,?,?,?,?)", rows)
    return c


def test_addresses_merge_across_functions():
    """The same byte touched by two functions is ONE field, not two."""
    acc = gl.collect(_db([("global:0x80121B50", 0, 4, 1, 100),
                          ("global:0x80121B50", 0, 4, 1, 200)]))
    assert len(acc) == 1
    assert acc[0x80121B50].funcs == {100, 200}


def test_widest_access_wins():
    """A narrower field cannot hold a wider one."""
    acc = gl.collect(_db([("global:0x1000", 0, 2, 1, 1),
                          ("global:0x1000", 0, 4, 1, 2)]))
    assert acc[0x1000].width == 4


def test_base_plus_offset_resolves_to_one_address():
    acc = gl.collect(_db([("global:0x1000", 4, 4, 1, 1),
                          ("global:0x1004", 0, 4, 1, 2)]))
    assert len(acc) == 1 and 0x1004 in acc


def test_nearby_addresses_form_one_object():
    objs = gl.cluster(gl.collect(_db([
        ("global:0x1000", 0, 4, 1, 1), ("global:0x1004", 0, 4, 1, 2),
        ("global:0x1008", 0, 2, 1, 3)])), gap=0x40)
    assert len(objs) == 1
    assert [f.offset for f in objs[0].fields] == [0, 4, 8]


def test_distant_addresses_are_separate_objects():
    objs = gl.cluster(gl.collect(_db([
        ("global:0x1000", 0, 4, 1, 1), ("global:0x1004", 0, 4, 1, 2),
        ("global:0x9000", 0, 4, 1, 3), ("global:0x9004", 0, 4, 1, 4)])),
        gap=0x40)
    assert len(objs) == 2


def test_singleton_objects_are_dropped():
    """One address touched by one function is not evidence of a struct."""
    objs = gl.cluster(gl.collect(_db([("global:0x1000", 0, 4, 1, 1)])),
                      min_fields=2)
    assert objs == []


def test_unobserved_bytes_render_as_unknown_not_padding():
    """Invariant 5: emit unk_, never a guessed field."""
    objs = gl.cluster(gl.collect(_db([
        ("global:0x1000", 0, 4, 1, 1), ("global:0x1010", 0, 4, 1, 2)])),
        gap=0x40)
    out = gl.render(objs[0])
    assert "unk_04" in out and "never observed" in out


def test_render_states_the_base_is_first_observed():
    """The header must not imply the object starts where we first saw it."""
    objs = gl.cluster(gl.collect(_db([
        ("global:0x1000", 0, 4, 1, 1), ("global:0x1004", 0, 4, 1, 2)])))
    out = gl.render(objs[0])
    assert "LOWEST OBSERVED" in out


def test_signedness_is_voted_not_assumed():
    acc = gl.collect(_db([("global:0x1000", 0, 2, 0, 1),
                          ("global:0x1000", 0, 2, 0, 2),
                          ("global:0x1000", 0, 2, 1, 3)]))
    assert acc[0x1000].ctype == "u16"


def test_for_address_finds_the_containing_object():
    objs = gl.cluster(gl.collect(_db([
        ("global:0x1000", 0, 4, 1, 1), ("global:0x1004", 0, 4, 1, 2)])))
    assert gl.for_address(objs, 0x1004) is objs[0]
    assert gl.for_address(objs, 0x9999) is None
