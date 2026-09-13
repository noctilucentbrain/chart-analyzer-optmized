# Argo CD deployment

`application.yaml` deploys the Helm chart from the `main` branch of
`git@github.com:noctilucentbrain/chart-analyzer-optimized.git` into namespace
`chart-analyzer-optimized`. The repository root contains the service folder,
so the chart path is `chart-analyzer-optimized/charts/chart-analyzer`.

The Application uses the local registry image
`registry.ibis-silverside.ts.net/chart-analyzer-optimized:0.15.0-optimized.1`.
It starts with manual sync and creates the destination namespace on sync.

Before the first sync:

1. Register this repository in Argo CD with a read-only SSH deploy key authorized
   for this GitHub repository. The existing `k3s-argocd` repository registration
   does not grant access to this separate repository.
2. Build and push the image using `make build` and `make push` from the service folder.
3. Create namespace `chart-analyzer-optimized`, then create Secret
   `chart-analyzer-optimized-mongodb` there with key `uri`. The chart selects
   database `chart_analyzer_optimized`; its credentials must permit access.
   See `../OPTIMIZATION.md` for the secret creation command.

Apply/update the Application from the service folder:

```sh
kubectl --context default apply -f argocd/application.yaml
```

Once the prerequisites are ready, sync `chart-analyzer-optimized` in the Argo CD UI
or run:

```sh
argocd app sync chart-analyzer-optimized
argocd app wait chart-analyzer-optimized --health --timeout 180
```

The service retains its explicit start API: initialize data once and call
`POST /service/start` after deployment/restarts. See `../OPTIMIZATION.md`.
Argo CD deploys the API; it does not initialize data or start ingestion.

Do not also use `make deploy` on this release once Argo CD manages it. Commit chart
changes to Git for subsequent syncs. To release a new image, update the pinned
`image.tag` parameter here and reapply this Application. This standalone Application
is not itself managed by an app-of-apps, so changing this file in Git alone does
not update its live specification.

Reference: https://argo-cd.readthedocs.io/en/stable/user-guide/helm/
