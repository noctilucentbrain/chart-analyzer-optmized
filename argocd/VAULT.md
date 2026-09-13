# MongoDB credentials from Vault

The Helm chart creates namespace-local `VaultConnection`, `VaultAuth` and
`VaultStaticSecret` resources when `vault.enabled=true` (the default). It also
creates a Secret containing the public Vault CA certificate. TLS verification
remains enabled. Disable this integration with `--set vault.enabled=false` when
using an externally managed MongoDB Secret instead.

The connection points at `https://vault.vault.svc.cluster.local:8200`. Kubernetes
auth uses role `chart-analyzer-optimized-mongodb`, bound to service account
`chart-analyzer-optimized` in namespace `chart-analyzer-optimized`. The role can
read only `secret/data/chart-analyzer-optimized/mongodb` and manage its own token.

The KV v2 record contains `username` and `password`, imported from keys
`mongodb-root-username` and `mongodb-root-password` in Secret `mongodb/mongodb`.
These are the existing MongoDB root credentials requested for this integration;
no new MongoDB user is created. The operator syncs them into Secret
`chart-analyzer-optimized-mongodb`, with an additional URL-encoded `uri` field
for the application's existing `CHART_ANALYZER_MONGODB_URI` environment variable.
The URI uses service `mongodb.mongodb.svc.cluster.local:27017` and `authSource=admin`.
The app continues to select database `chart_analyzer_optimized`.

Refresh is every minute. Vault changes update the destination Secret and request
a Deployment restart so the process reads the new environment value. The app's
existing ingestion loop still requires `/service/start` after a restart.
The original MongoDB Kubernetes Secret is not continuously watched: after
rotating it, rerun the import helper to update Vault. Vault is the operator's
source of truth.

## Bootstrap or re-import

From the service directory:

```sh
python3 scripts/configure-vault-mongodb.py
```

This uses the cluster's existing admin bootstrap file `../vault/private/init.json`
and public CA `../vault/ca.crt` through a verified local TLS tunnel. It imports
credentials in memory, writes only changed KV data with compare-and-set, and
configures the scoped policy/auth role. Credential values are never printed or
saved by the helper. `--admin-credentials` and `--ca` can select alternative paths.
Do not commit the admin bootstrap file or any credential values.

For a different namespace/account/path, update `vault.auth.role`,
`vault.mongodb.path` and the import helper's corresponding command-line arguments.
The public CA in `charts/chart-analyzer/files/vault-ca.crt` must be updated when
changing the Vault CA. Alternatively set `vault.connection.createCASecret=false`
and point `caCertSecretRef` at an existing CA Secret in the app namespace.

The helper configures Vault; Helm/Argo CD manages the Kubernetes resources.
Use Argo CD sync to apply chart changes. Source credentials remain outside Git.

Reference: https://developer.hashicorp.com/vault/docs/deploy/kubernetes/vso/api-reference
