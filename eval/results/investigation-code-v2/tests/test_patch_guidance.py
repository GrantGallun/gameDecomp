import pytest
from solver import modelrepair, patch_guidance


def test_examples_precede_unchanged_original_prompt():
    original = 'TARGET ASSEMBLY\nCURRENT C\nEDITABLE SOURCE SLOTS'
    prompt = patch_guidance.prepend(original)
    assert prompt.endswith(original)
    assert prompt.index('{"slot":"DECLARATIONS"') < prompt.index('TARGET ASSEMBLY')
    assert 'COMPLETE current line' in prompt
    assert 'guessed layouts' in prompt


def test_slots_only_has_no_old_substring_example():
    prompt = patch_guidance.prepend('original', slots_only=True)
    assert '{"old":' not in prompt
    assert 'Old-substring edits are forbidden' in prompt


def test_empty_anchor_and_pragma_still_rejected():
    for edit in ('{"old":"","new":"struct T;"}',
                 '{"slot":"DECLARATIONS","new":"#pragma once\\n"}'):
        with pytest.raises(ValueError):
            modelrepair.parse_proposal(
                '{"kind":"declarations","hypothesis":"test","edits":['+edit+']}',
                source='void f(void) {}')
