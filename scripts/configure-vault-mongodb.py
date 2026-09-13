#!/usr/bin/env python3
"""Import MongoDB credentials into Vault without printing or persisting their values."""
import argparse
import base64
import json
from pathlib import Path
import ssl
import subprocess
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-credentials', type=Path, default=Path('../vault/private/init.json'))
    parser.add_argument('--ca', type=Path, default=Path('../vault/ca.crt'))
    parser.add_argument('--context', default='default')
    parser.add_argument('--namespace', default='chart-analyzer-optimized')
    parser.add_argument('--service-account', default='chart-analyzer-optimized')
    parser.add_argument('--role', default='chart-analyzer-optimized-mongodb')
    parser.add_argument('--path', default='chart-analyzer-optimized/mongodb')
    parser.add_argument('--port', type=int, default=18444)
    args = parser.parse_args()
    # Never interpolate unchecked path/policy names into ACL syntax.
    import re
    for value in (args.path, args.role):
        if not re.fullmatch(r'[a-zA-Z0-9_/-]+', value):
            parser.error('Vault path and role must use letters, numbers, underscores, hyphens or slashes')
    token = json.loads(args.admin_credentials.read_text())['root_token']
    context = ssl.create_default_context(cafile=str(args.ca))
    process = subprocess.Popen(['kubectl', '--context', args.context, '-n', 'vault',
        'port-forward', '--address', '127.0.0.1', 'pod/vault-0', f'{args.port}:8200'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def api(path, data=None, method=None):
        request = urllib.request.Request(f'https://127.0.0.1:{args.port}/v1/{path}',
            data=None if data is None else json.dumps(data).encode(), method=method,
            headers={'X-Vault-Token': token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, context=context, timeout=15) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            if error.code == 404 and data is None:
                return None
            raise RuntimeError(f'Vault request failed: HTTP {error.code} at {path}') from None

    try:
        for attempt in range(30):
            if process.poll() is not None:
                raise RuntimeError('Vault port-forward exited')
            try:
                api('sys/health')
                break
            except urllib.error.URLError:
                time.sleep(1)
        else:
            raise RuntimeError('Vault endpoint did not become available')
        source = json.loads(subprocess.check_output(['kubectl', '--context', args.context,
            '-n', 'mongodb', 'get', 'secret', 'mongodb', '-o', 'json']))['data']
        credentials = {'username': base64.b64decode(source['mongodb-root-username']).decode(),
                       'password': base64.b64decode(source['mongodb-root-password']).decode()}
        if not all(credentials.values()):
            raise RuntimeError('MongoDB credentials must not be empty')
        current = api(f'secret/data/{args.path}')
        if current is None or current['data']['data'] != credentials:
            version = current['data']['metadata']['version'] if current else 0
            api(f'secret/data/{args.path}', {'data': credentials, 'options': {'cas': version}})
            print('Imported MongoDB credentials into the dedicated Vault KV path.')
        else:
            print('Vault credentials already match the MongoDB source Secret.')
        policy = f'path "secret/data/{args.path}" {{ capabilities = ["read"] }}'
        # VSO renews/revokes only its own token; no access to other tokens or secrets.
        policy += '\npath "auth/token/lookup-self" { capabilities = ["read"] }'
        policy += '\npath "auth/token/renew-self" { capabilities = ["update"] }'
        policy += '\npath "auth/token/revoke-self" { capabilities = ["update"] }' 
        api(f'sys/policies/acl/{args.role}', {'policy': policy}, 'PUT')
        api(f'auth/kubernetes/role/{args.role}', {
            'bound_service_account_names': [args.service_account],
            'bound_service_account_namespaces': [args.namespace],
            'audience': 'vault', 'token_policies': [args.role],
            'token_no_default_policy': True, 'token_ttl': '1h', 'token_max_ttl': '1h'})
        print('Configured a read-only Vault policy and namespace/service-account-bound role.')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == '__main__':
    main()
