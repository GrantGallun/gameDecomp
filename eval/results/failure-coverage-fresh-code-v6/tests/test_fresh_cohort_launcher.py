import importlib
import pytest


launcher = importlib.import_module('eval.experiments.campaign-gap-audit.fresh_run_v1')


def test_versioned_launcher_preserves_defaults_and_allows_no_model():
    assert vars(launcher.options([])) == {'version': 1, 'model_calls': 3,'per_stratum':1,'max_work_items':40}
    assert vars(launcher.options(['--version', '2', '--model-calls', '0'])) == {
        'version': 2, 'model_calls': 0,'per_stratum':1,'max_work_items':40}


@pytest.mark.parametrize('args', [['--version', '0'], ['--version', '-1'], ['--model-calls', '-1']])
def test_versioned_launcher_rejects_invalid_budgets(args):
    with pytest.raises(SystemExit):
        launcher.options(args)
