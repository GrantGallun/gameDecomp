import copy

import pytest

from eval import fast_campaign


def test_reasoned_medium_amendment_preserves_lane_identity_budgets_and_other_profiles():
    profile = {'name': 'reasoned_alternative', 'think': 'high', 'model': True,
        'evidence_key': 'bound', 'deterministic_budget': 0, 'lane': 'frontend',
        'brief': 'test a different hypothesis'}
    original = copy.deepcopy(profile)
    changed = fast_campaign.effort_profile(profile, 'medium')
    assert profile == original
    assert changed == {**profile, 'think': 'medium', 'requested_think': 'high', 'effort_policy': 'medium'}
    assert fast_campaign.effort_profile(profile)['think'] == 'high'
    for other in ({**profile, 'name': 'semantic_alternative'},
                  {**profile, 'think': 'low'},
                  {'name': 'local_rewrites', 'model': False, 'deterministic_budget': 8},
                  {'name': 'schema_patch', 'model': True}):
        actual = fast_campaign.effort_profile(other, 'medium')
        assert {k: v for k, v in actual.items() if k not in {'requested_think', 'effort_policy'}} == other
        assert actual['requested_think'] == other.get('think', 'low')


@pytest.mark.parametrize('legacy_has_options', [False, True])
def test_legacy_resume_cannot_silently_enable_reasoned_medium(legacy_has_options):
    options = {'dispatch': 'pipeline', 'workers': 3, 'model_parallel': 1,
        'model_workers': 2, 'tasks_per_worker': 1, 'reasoned_effort': 'profile'}
    state = {'runtime_options': {k: v for k, v in options.items() if k != 'reasoned_effort'}} if legacy_has_options else {}
    original = copy.deepcopy(state)
    with pytest.raises(ValueError, match='amendment'):
        fast_campaign.bind_runtime_options(state, {**options, 'reasoned_effort': 'medium'})
    assert state == original
    fast_campaign.bind_runtime_options(state, options)
    assert state['runtime_options']['reasoned_effort'] == 'profile'
    # Only an explicitly amended checkpoint admits the opt-in setting.
    state['runtime_options']['reasoned_effort'] = 'medium'
    fast_campaign.bind_runtime_options(state, {**options, 'reasoned_effort': 'medium'})
    with pytest.raises(ValueError, match='amendment'):
        fast_campaign.bind_runtime_options(state, options)


@pytest.mark.parametrize('legacy_has_options', [False, True])
def test_integration_requires_explicit_runtime_amendment(legacy_has_options):
    options = {'dispatch': 'pipeline', 'workers': 3, 'model_parallel': 1,
               'model_workers': 2, 'tasks_per_worker': 1, 'reasoned_effort': 'profile'}
    state = {'runtime_options': dict(options)} if legacy_has_options else {}
    with pytest.raises(ValueError, match='amendment'):
        fast_campaign.bind_runtime_options(state, {**options, 'integrate': True})
    fast_campaign.bind_runtime_options(state, {**options, 'integrate': False})
    state['runtime_options']['integrate'] = True
    fast_campaign.bind_runtime_options(state, {**options, 'integrate': True})
