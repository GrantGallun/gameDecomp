"""Campaign wiring of solver.plateau_search: after the site-edit visit, on the same small residuals, once per source."""
import hashlib

import pytest

from solver import repair_queue as queue


def _node(jobs=(), **faults):
    base = {'structural': 0, 'layout': 0, 'offset': 0, 'width': 0, 'relocation': 0,
            'register_allocation': 0, 'ordering': 0, 'immediate': 0}
    base.update(faults)
    return {'status': 'pending', 'source': '/tmp/x.c', 'source_sha256': 'abc', 'attempt_id': 1, 'jobs': list(jobs),
            'residual': {'compiled': True, 'frontend': {'passed': True}, 'faults': base}}


def _visited():
    return [{'profile': 'site_edits@' + queue.site_edit_digest(), 'source_sha256': 'abc'}]


def test_fires_after_the_site_edit_visit_on_a_register_near_miss():
    # updateEndingCreditsCharacterAura shape: register-only residual the site-edit search did not close
    profile = queue.plateau_profile(_node(_visited(), register_allocation=2))
    assert profile and profile['plateau'] and profile['model'] is False and profile['plateau_budget'] == 240
    assert profile['name'] == 'plateau@' + queue.plateau_digest()


def test_next_profile_offers_it_once_the_site_edit_visit_is_done():
    node = _node(_visited(), register_allocation=2)
    node['semantic_validation'] = {'status': 'observed_pass', 'source_sha256': 'abc'}   # the byte lane
    assert queue.next_profile(node, 0, [])['name'] == 'plateau@' + queue.plateau_digest()
    fresh = _node((), register_allocation=2)
    fresh['semantic_validation'] = {'status': 'observed_pass', 'source_sha256': 'abc'}
    assert queue.next_profile(fresh, 0, [])['name'].startswith('site_edits@')


def test_declines_before_site_edits_on_large_residuals_and_when_visited():
    assert queue.plateau_profile(_node((), register_allocation=2)) is None
    assert queue.plateau_profile(_node(_visited(), structural=13)) is None
    node = _node(_visited() + [{'profile': 'plateau@' + queue.plateau_digest(), 'source_sha256': 'abc'}],
                 register_allocation=2)
    assert queue.plateau_profile(node) is None
    node['source_sha256'] = 'def'                                    # a new incumbent earns a new visit
    assert queue.plateau_profile(node) is not None


def test_dispatch_refuses_a_stale_revision(tmp_path):
    from eval import completion_campaign
    source = tmp_path / 'f.c'
    source.write_text('void f(void) {}\n')
    node = {'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='plateau search changed'):
        completion_campaign.execute(repo=tmp_path, db=tmp_path / 'db', function='f', node=node,
                                    profile={'name': 'plateau@old', 'plateau': True, 'plateau_revision': 'old'},
                                    config={}, out=tmp_path / 'o.json')
