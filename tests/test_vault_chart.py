"""Render the chart to check the operator/app wiring without using credentials."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / 'charts/chart-analyzer'
pytestmark = pytest.mark.skipif(shutil.which('helm') is None, reason='helm required')


def render(*args):
    output = subprocess.check_output(['helm', 'template', 'trial', str(CHART),
        '--namespace', 'trial', *args], text=True)
    return [doc for doc in yaml.safe_load_all(output) if doc]


def test_vault_tls_auth_and_secret_wiring():
    docs = render('--set', 'vault.mongodb.destinationSecret=custom-mongodb')
    connection = next(d for d in docs if d['kind'] == 'VaultConnection')
    auth = next(d for d in docs if d['kind'] == 'VaultAuth')
    static = next(d for d in docs if d['kind'] == 'VaultStaticSecret')
    deployment = next(d for d in docs if d['kind'] == 'Deployment')
    account = next(d for d in docs if d['kind'] == 'ServiceAccount')
    assert connection['spec']['skipTLSVerify'] is False
    assert auth['spec']['vaultConnectionRef'] == connection['metadata']['name']
    assert auth['spec']['kubernetes']['serviceAccount'] == account['metadata']['name']
    assert static['spec']['vaultAuthRef'] == auth['metadata']['name']
    assert static['spec']['destination']['name'] == 'custom-mongodb'
    assert static['spec']['destination']['transformation']['excludeRaw'] is True
    assert '.Secrets.password' in static['spec']['destination']['transformation']['templates']['uri']['text']
    env = deployment['spec']['template']['spec']['containers'][0]['env']
    mongo = [v for v in env if v['name'] == 'CHART_ANALYZER_MONGODB_URI']
    assert len(mongo) == 1
    assert mongo[0]['valueFrom']['secretKeyRef'] == {'name':'custom-mongodb','key':'uri'}
    assert static['spec']['rolloutRestartTargets'][0]['name'] == deployment['metadata']['name']


def test_vault_can_be_disabled_and_existing_env_is_retained():
    docs = render('--set', 'vault.enabled=false')
    assert not any(d['kind'].startswith('Vault') for d in docs)
    deployment = next(d for d in docs if d['kind'] == 'Deployment')
    env = deployment['spec']['template']['spec']['containers'][0]['env']
    assert env[0]['valueFrom']['secretKeyRef']['name'] == 'chart-analyzer-optimized-mongodb'


def test_extra_environment_variables_survive_vault_wiring():
    docs = render('--set', 'env[0].name=EXAMPLE', '--set-string', 'env[0].value=hello')
    deployment = next(d for d in docs if d['kind'] == 'Deployment')
    assert {'name':'EXAMPLE','value':'hello'} in deployment['spec']['template']['spec']['containers'][0]['env']
