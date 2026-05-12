# CLAUDE.md

Guidance for Claude Code working in this repository.

## Before pushing

CI runs `dagger call all`, which includes both `ruff check` AND
`ruff format --check`. Running only one locally is not sufficient.

Quick path (no Dagger needed): inside `custom_components/area_lighting/`,
run

```sh
uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto
```

CI-equivalent path (slower, requires Docker):

```sh
dagger call all
```

A `pre-commit` hook in `hooks/pre-commit` runs `dagger call lint`
automatically, but only after a one-time
`git config core.hooksPath hooks`. If the hook isn't installed in this
clone, the local commands above are the safety net.

## Commit subjects

Every commit subject must start with `(Major)`, `(Minor)`, or `(Patch)`
(enforced by `hooks/commit-msg` when hooks are enabled, and required by
the `tag:auto` CI job that computes the next version). For example:

- `(Patch) area_lighting: fix motion timer on HA reload`
- `(Minor) ci: auto-tag main branch`

Avoid the literal string `skip ci` anywhere in subject or body — GitLab
treats it as a pipeline suppressor and `tag:auto` won't run.

No em dashes in commit messages: use commas, colons, or parentheses.

## Versioning

The `tag:auto` CI job on `main` reads commit subjects, computes the next
version, writes `release: bump version to X.Y.Z` as its own commit, and
tags. **Do not manually edit `pyproject.toml` / `manifest.json` /
`uv.lock` versions** in content commits: it makes the bot's bump commit
redundant and leaves the lock out of sync if forgotten.

If you do need to manually align `uv.lock`, run `uv lock` (which updates
only the local-virtual-package entry) and commit it separately.

## Test layout

See `custom_components/area_lighting/tests/README.md` for the unit vs.
integration split. Integration tests use
`pytest-homeassistant-custom-component` fixtures from
`tests/integration/conftest.py` (`hass`, `helper_entities`,
`network_room_config`, `service_calls`). When a test needs more than one
area, define a multi-area config fixture inline in the test file rather
than expanding `conftest.py`.

## See also

- `CONTRIBUTING.md` — Dagger setup, hook installation, versioning policy,
  manual tag creation
- `README.md` — user-facing description and HACS install
