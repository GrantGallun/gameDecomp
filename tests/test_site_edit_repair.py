"""Campaign wiring of solver.site_edits: the profile fires on the residuals it was measured on, and only there."""
from pathlib import Path

import pytest

from solver import repair_queue as queue


def _node(**faults):
    base = {'structural': 0, 'layout': 0, 'offset': 0, 'width': 0, 'relocation': 0,
            'register_allocation': 0, 'ordering': 0, 'immediate': 0}
    base.update(faults)
    return {'status': 'pending', 'source': '/tmp/x.c', 'source_sha256': 'abc', 'attempt_id': 1, 'jobs': [],
            'residual': {'compiled': True, 'frontend': {'passed': True}, 'faults': base}}


def test_fires_on_a_small_compiled_residual():
    profile = queue.site_edit_profile(_node(immediate=2))
    assert profile and profile['site_edits'] and profile['model'] is False
    assert profile['name'] == 'site_edits@' + queue.site_edit_digest()


def test_layout_is_not_double_counted():
    # `layout` duplicates offset + width; counting it would push 7 real faults over the bound.
    assert queue.site_edit_profile(_node(offset=6, layout=6, register_allocation=6)) is not None


def test_declines_large_residuals_uncompiled_and_revisited():
    assert queue.site_edit_profile(_node(structural=13)) is None
    node = _node(immediate=2)
    node['residual']['frontend'] = {'passed': False}
    assert queue.site_edit_profile(node) is None
    node = _node(immediate=2)
    node['jobs'] = [{'profile': 'site_edits@' + queue.site_edit_digest()}]
    assert queue.site_edit_profile(node) is None


def test_revision_tracks_the_generator(monkeypatch):
    original = Path.read_bytes
    target = Path('solver/site_edits.py').resolve()
    monkeypatch.setattr(queue, '_SITE_EDIT_DIGEST', None)
    before = queue.site_edit_digest()
    monkeypatch.setattr(Path, 'read_bytes',
                        lambda p: original(p) + b'\n# new operator\n' if p.resolve() == target else original(p))
    monkeypatch.setattr(queue, '_SITE_EDIT_DIGEST', None)
    assert queue.site_edit_digest() != before


def test_dispatch_refuses_a_stale_revision(tmp_path, monkeypatch):
    from eval import completion_campaign
    source = tmp_path / 'f.c'
    source.write_text('void f(void) {}\n')
    import hashlib
    node = {'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='site-edit generator changed'):
        completion_campaign.execute(repo=tmp_path, db=tmp_path / 'db', function='f', node=node,
                                    profile={'name': 'site_edits@old', 'site_edits': True,
                                             'site_edit_revision': 'old'}, config={}, out=tmp_path / 'o.json')
