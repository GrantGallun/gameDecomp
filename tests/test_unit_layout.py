from miner import units


def test_nested_ranges_and_aliases_do_not_manufacture_padding():
    rows = [(0x1000, 0x40, 'outer'), (0x1000, 0x40, 'alias'),
            (0x1010, 4, 'inner'), (0x1040, 8, 'adjacent'),
            (0x1050, 4, 'after_gap')]
    result = units.clusters(rows)
    assert len({result[n] for n in ('outer', 'alias', 'inner', 'adjacent')}) == 1
    assert result['after_gap'] != result['outer']
    assert units.boundaries(rows) == {'adjacent'}
    assert units.clusters(list(reversed(rows))) == result
    assert units.boundaries(list(reversed(rows))) == {'adjacent'}


def test_unknown_extent_remains_a_barrier_without_claiming_padding():
    rows = [(0x1000, 0x10, 'before'), (0x1010, None, 'unknown'),
            (0x1020, 0x10, 'after'), (0x1030, 0x10, 'adjacent')]
    result = units.clusters(rows)
    assert 'unknown' not in result
    assert result['before'] != result['after']
    assert result['after'] == result['adjacent']
    assert units.boundaries(rows) == set()


def test_uncertain_alias_separates_both_sides_of_known_address():
    rows = [(0x1000, 0x10, 'before'), (0x1010, 0, 'unknown_alias'),
            (0x1010, 0x10, 'known_alias'), (0x1020, 0x10, 'after')]
    result = units.clusters(rows)
    assert len(set(result.values())) == 3
    assert units.boundaries(rows) == set()


def test_invalid_extents_are_declined_and_conflicting_names_are_not_guessed():
    rows = [(None, 8, 'unlocated'), (-1, 8, 'negative_address'),
            (0x1000, -8, 'negative_size'), (0x1010, 0, 'zero_size'),
            (0x1020, 8, 'conflict'), (0x1030, 8, 'conflict'),
            (0x1040, 8, 'good'), (0x1040, 8, 'good')]
    assert units.clusters(rows) == {'good': 'unit@0x00001040'}
    assert units.boundaries(rows) == set()


def test_conflicting_name_barriers_cannot_hide_in_an_omitted_row():
    rows = [(0x1000, 0x10, 'before'), (0x1010, 0x10, 'duplicate'),
            (None, 0x10, 'duplicate'), (0x1020, 0x10, 'after')]
    result = units.clusters(rows)
    assert set(result) == {'before', 'after'}
    assert result['before'] != result['after']
    assert not units.boundaries(rows)


def test_regular_padding_and_contiguous_functions_keep_original_labels():
    rows = [(0x1000, 0x10, 'a'), (0x1010, 0x10, 'b'), (0x1030, 4, 'c')]
    assert units.clusters(rows) == {
        'a': 'unit@0x00001000', 'b': 'unit@0x00001000', 'c': 'unit@0x00001030'}
    assert units.boundaries(rows) == {'b'}


def test_invalid_numeric_aliases_decline_independently_of_input_order():
    rows = [(0x1000, 1, 'ambiguous'), (0x1000, True, 'ambiguous'),
            (0x1020, 8, 'good')]
    expected = {'good': 'unit@0x00001020'}
    assert units.clusters(rows) == expected
    assert units.clusters(list(reversed(rows))) == expected
