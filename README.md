# roost-cc/.github

Org-level GitHub configuration for the [Roost](https://roost.cc) organization.

The README that profiles the org on github.com/roost-cc lives at
[`profile/README.md`](profile/README.md). The rest of this repository is
shared org infrastructure: reusable workflows, templates, etc.

## Reusable CI workflow

`.github/workflows/ci.yml` is a reusable workflow that provides the standard
Roost CI baseline (pre-commit re-scan, lint, typecheck, test, PR-title check)
for any repo in the org. Consumer repos call it from their own
`.github/workflows/ci.yml` with a few lines of YAML — they do not copy-paste
the workflow itself.

### Calling the workflow

Minimal usage:

```yaml
# .github/workflows/ci.yml in a consumer repo
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  ci:
    uses: roost-cc/.github/.github/workflows/ci.yml@v1
    with:
      language: python
```

The double `.github/.github/` is intentional: the first segment is the repo
name (`roost-cc/.github`), the second is the in-repo workflows directory.

### Inputs

| Input               | Type    | Required | Default                                                    | Description                                                                                    |
| ------------------- | ------- | -------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `language`          | string  | yes      | —                                                          | One of `python`, `node`, `go`.                                                                 |
| `version`           | string  | no       | per-language default (`python=3.12`, `node=20`, `go=1.22`) | Toolchain version.                                                                             |
| `working-directory` | string  | no       | `.`                                                        | Directory the lint / typecheck / test commands run in.                                         |
| `run-tests`         | boolean | no       | `true`                                                     | Set to `false` for repos that don't have tests yet. The `coverage-report` job is also skipped. |

### Jobs

The reusable workflow defines four jobs:

1. **`precommit`** — installs `pre-commit` and runs
   `pre-commit run --all-files --show-diff-on-failure`. If the consumer repo
   has no `.pre-commit-config.yaml`, the job logs a notice and skips (so
   legacy repos that haven't yet adopted shared-config from S-379 don't hard
   fail). This re-scan catches commits pushed with `--no-verify` or by
   contributors who never installed the hooks.
2. **`lint-typecheck-test`** — language-conditional. See per-language
   contracts below.
3. **`pr-title`** — only runs on `pull_request` events. Validates the PR
   title against Conventional Commits via
   `amannn/action-semantic-pull-request`. Has `pull-requests: read` only —
   never write.
4. **`coverage-report`** — downloads the coverage artifact uploaded by the
   `lint-typecheck-test` job and posts a presence summary. **No coverage
   threshold is enforced in v1.** Threshold enforcement is per-repo and
   ships with S-385.

### Per-language contracts

#### Python (`language: python`)

The workflow runs:

- `ruff check .`
- `ruff format --check .`
- `mypy .`
- `pytest --cov --cov-report=xml`

The consumer repo must have a `pyproject.toml` declaring `ruff`, `mypy`,
and `pytest` as dev dependencies. If a `uv.lock` is present the workflow
uses `uv sync --all-extras --dev` and runs commands via `uv run`;
otherwise it falls back to `pip install -e '.[dev]'`. Coverage is emitted
to `coverage.xml`.

#### Node (`language: node`)

The workflow runs:

- `npm ci`
- `npm run lint` _(skipped with a notice if the script is absent)_
- `npm run typecheck` _(skipped with a notice if absent)_
- `npm test -- --coverage` _(skipped with a notice if absent)_

The consumer repo must have a committed `package-lock.json` (`npm ci`
requires one). Missing `lint` / `typecheck` / `test` scripts are tolerated
— each is checked with `jq -e '.scripts.<name>'` first. Coverage tooling
is the consumer's choice (Jest, c8, vitest, etc.) — emit to
`coverage/lcov.info` to be picked up by the artifact upload.

#### Go (`language: go`)

The workflow runs:

- `go vet ./...`
- `staticcheck ./...` (installed at job time via `go install
honnef.co/go/tools/cmd/staticcheck@latest`)
- `go test -race -coverprofile=coverage.out ./...`

### Action SHA pinning

Every third-party action reference in the workflow is pinned to a 40-char
commit SHA with the human-readable tag in a trailing comment, e.g.
`actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2`. SHAs
are kept fresh by Renovate (`renovate.json` in this repo).

### Versioning policy

Consumers pin via the moving major-version tag `@v1`. The contract:

- `v1.0.0`, `v1.1.0`, … — immutable point releases. Cut on each
  backwards-compatible change.
- `v1` — moving pointer that is force-updated to point at the latest
  `v1.x.y` after each release. **This is what consumers should pin to.**
- A breaking input change (e.g. removing or renaming an input, changing a
  default in a way consumer repos can't ignore) → cut a new major (`v2`).
  Existing pins on `@v1` keep working unchanged.

Tagging procedure (post-merge to `main`):

```sh
git tag -s v1.0.0 -m "Reusable CI workflow v1.0.0"
git tag -f v1
git push origin v1.0.0
git push origin v1 --force-with-lease
```

(Subsequent `vX.Y.Z` releases follow the same shape; only `vX` is ever
force-pushed.)

### Self-test

`.github/workflows/ci-selftest.yml` calls the reusable workflow against
minimal projects under `selftest/{python,node,go}/` to prove the contract
end-to-end on every PR that touches `ci.yml`. `actionlint` lints the
workflow file as a gating step.
