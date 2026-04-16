package main

import (
	"context"

	"dagger/area-lighting/internal/dagger"

	"golang.org/x/sync/errgroup"
)

// New creates an AreaLighting CI module.
func New(
	// Project source directory.
	// +defaultPath="."
	// +ignore=["dagger","*.gen.go",".git",".venv","__pycache__",".pytest_cache",".mypy_cache",".ruff_cache"]
	source *dagger.Directory,
) *AreaLighting {
	return &AreaLighting{Source: source}
}

// AreaLighting provides CI functions for the area_lighting HA component.
type AreaLighting struct {
	Source *dagger.Directory
}

// base returns a Python 3.13 container with uv and dev dependencies installed.
func (m *AreaLighting) base() *dagger.Container {
	return dag.Container().
		From("ghcr.io/astral-sh/uv:python3.13-bookworm").
		WithMountedCache("/root/.cache/uv", dag.CacheVolume("uv-cache")).
		WithDirectory("/src", m.Source).
		WithWorkdir("/src").
		WithExec([]string{"uv", "sync", "--extra", "dev"})
}

// Lint runs ruff check and ruff format --check.
func (m *AreaLighting) Lint(ctx context.Context) (string, error) {
	return m.base().
		WithExec([]string{"uv", "run", "ruff", "check", "."}).
		WithExec([]string{"uv", "run", "ruff", "format", "--check", "."}).
		Stdout(ctx)
}

// Typecheck runs mypy on the component source.
func (m *AreaLighting) Typecheck(ctx context.Context) (string, error) {
	return m.base().
		WithExec([]string{
			"uv", "run", "mypy", "custom_components/area_lighting",
			"--ignore-missing-imports",
		}).
		Stdout(ctx)
}

// Test runs the full pytest suite.
func (m *AreaLighting) Test(ctx context.Context) (string, error) {
	return m.base().
		WithExec([]string{"uv", "run", "pytest", "-v", "--tb=short"}).
		Stdout(ctx)
}

// TestLatest runs tests against the newest pytest-homeassistant-custom-component
// (and therefore the latest HA core). Used for nightly CI.
func (m *AreaLighting) TestLatest(ctx context.Context) (string, error) {
	return dag.Container().
		From("ghcr.io/astral-sh/uv:python3.13-bookworm").
		WithMountedCache("/root/.cache/uv", dag.CacheVolume("uv-cache")).
		WithDirectory("/src", m.Source).
		WithWorkdir("/src").
		WithExec([]string{
			"uv", "lock", "--upgrade-package", "pytest-homeassistant-custom-component",
		}).
		WithExec([]string{"uv", "sync", "--extra", "dev"}).
		WithExec([]string{"uv", "run", "pytest", "-v", "--tb=short"}).
		Stdout(ctx)
}

// All runs lint, typecheck, test, and the versioning-helper tests
// concurrently.
func (m *AreaLighting) All(
	ctx context.Context,
	// +defaultPath="./dagger/versioning"
	versioningSource *dagger.Directory,
) error {
	eg, ctx := errgroup.WithContext(ctx)
	eg.Go(func() error { _, err := m.Lint(ctx); return err })
	eg.Go(func() error { _, err := m.Typecheck(ctx); return err })
	eg.Go(func() error { _, err := m.Test(ctx); return err })
	eg.Go(func() error { _, err := m.TestVersioning(ctx, versioningSource); return err })
	return eg.Wait()
}
