# Releasing xsd-former to PyPI

Publishing is automated by [`.github/workflows/release.yml`](../.github/workflows/release.yml)
via PyPI **Trusted Publishing** (OIDC): CI mints a short-lived, scoped token at
publish time, so there is no PyPI API token stored in the repo.

## One-time PyPI setup

Before the first release, register the trusted publisher on PyPI (needs a PyPI
account with the project, or pre-register the pending publisher for a
not-yet-existing project):

- Project: `xsd-former` (the distribution name; the import package stays `xsdformer`)
- Owner: `populationgenomics`
- Repository: `xsd-former`
- Workflow filename: `release.yml`
- Environment: `pypi`

Then create a GitHub environment named `pypi` on the repo (Settings →
Environments) so the workflow's `publish` job can request the OIDC token.

## Cut a release

1. Bump `version` in `pyproject.toml` and merge it to `main`.
2. Publish a GitHub Release whose tag is `v<version>` (e.g. `v1.0.1`).

The `release` workflow then checks the tag matches the `pyproject.toml`
version (a published version is irreversible), builds the sdist + wheel with
`uv build`, and publishes them. The `publish` job runs in the `pypi`
environment and holds only `id-token: write`.
