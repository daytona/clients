// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

type builderFixture struct {
	Name       string
	Source     map[string]json.RawMessage
	Steps      [][]json.RawMessage
	Dockerfile string
	Request    struct {
		Dockerfile string
		Source     *struct{ Ref string }
	}
	Files    map[string]string
	Symlinks map[string]string
	Entries  []string
	Contents map[string]string
	SHA256   string
	Error    string
}

func builderFixtures(t *testing.T, name string, value any) {
	t.Helper()
	content, err := os.ReadFile("../sdk-python/tests/fixtures/" + name)
	if errors.Is(err, os.ErrNotExist) {
		t.Skip("shared Python fixtures are not included in the Go module")
	}
	require.NoError(t, err)
	require.NoError(t, json.Unmarshal(content, value))
}

func builderFixtureString(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	var text string
	require.NoError(t, json.Unmarshal(raw, &text))
	// Go strings represent invalid Unicode with invalid bytes, not surrogates.
	// Restore that input class after encoding/json replaces the shared fixture's
	// unpaired surrogate; valid literal replacement characters remain valid.
	if bytes.Contains(raw, []byte(`\ud800`)) {
		text = strings.ReplaceAll(text, "\ufffd", "\xff")
	}
	return text
}

func builderFromFixture(t *testing.T, tc builderFixture, root string) *ImageBuilder {
	t.Helper()
	var builder *ImageBuilder
	switch {
	case tc.Source["registry"] != nil:
		builder = NewImageBuilder(builderFixtureString(t, tc.Source["registry"]))
	case tc.Source["dockerfile"] != nil:
		builder = ImageFromDockerfile(builderFixtureString(t, tc.Source["dockerfile"]))
	default:
		builder = DebianSlim(builderFixtureString(t, tc.Source["debianSlim"]))
	}
	for _, step := range tc.Steps {
		builder = builderFixtureStep(t, builder, step, root)
	}
	return builder
}

func builderFixtureStep(t *testing.T, b *ImageBuilder, step []json.RawMessage, root string) *ImageBuilder {
	t.Helper()
	op := builderFixtureString(t, step[0])
	switch op {
	case "run", "pip", "cmd", "entrypoint", "env-name":
		var rawArgs []json.RawMessage
		require.NoError(t, json.Unmarshal(step[1], &rawArgs))
		args := make([]string, len(rawArgs))
		for i, raw := range rawArgs {
			args[i] = builderFixtureString(t, raw)
		}
		switch op {
		case "run":
			return b.RunCommands(args...)
		case "pip":
			return b.PipInstall(args...)
		case "cmd":
			return b.Cmd(args...)
		case "entrypoint":
			return b.Entrypoint(args...)
		case "env-name":
			return b.Env(args[0], args[1])
		}
	case "env":
		return builderFixtureEnv(t, b, step[1])
	case "env-map":
		t.Skip("Go statically rejects non-map arguments to EnvVars")
	case "workdir":
		return b.Workdir(builderFixtureString(t, step[1]))
	case "user":
		return b.User(builderFixtureString(t, step[1]))
	case "file", "dir", "requirements":
		local := builderFixtureString(t, step[1])
		if builderTrim(local) != "" {
			local = filepath.Join(root, local)
		}
		if op == "requirements" {
			return b.PipInstallFromRequirements(local)
		}
		remote := builderFixtureString(t, step[2])
		if op == "dir" {
			return b.AddLocalDir(local, remote)
		}
		return b.AddLocalFile(local, remote)
	}
	t.Fatalf("unsupported fixture operation: %s", op)
	return nil
}

func builderFixtureEnv(t *testing.T, b *ImageBuilder, raw json.RawMessage) *ImageBuilder {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(raw))
	_, err := decoder.Token()
	require.NoError(t, err)
	var keys []string
	values := make(map[string]string)
	normalized := make(map[string]bool)
	collision := false
	for decoder.More() {
		token, tokenErr := decoder.Token()
		require.NoError(t, tokenErr)
		key, ok := token.(string)
		require.True(t, ok)
		var value json.RawMessage
		require.NoError(t, decoder.Decode(&value))
		keys = append(keys, key)
		values[key] = builderFixtureString(t, value)
		if normalized[builderTrim(key)] {
			collision = true
		}
		normalized[builderTrim(key)] = true
	}
	if collision || len(keys) == 0 {
		return b.EnvVars(values)
	}
	// JSON objects in these cross-language fixtures have insertion order. Go
	// callers preserve that order with Env calls; EnvVars sorts unordered maps.
	for _, key := range keys {
		b = b.Env(key, values[key])
	}
	return b
}

func builderFixtureTree(t *testing.T, tc builderFixture) string {
	t.Helper()
	root := t.TempDir()
	for name, content := range tc.Files {
		filename := filepath.Join(root, name)
		parent := filepath.Dir(filename)
		if strings.HasSuffix(name, "/") {
			parent = filename
		}
		require.NoError(t, os.MkdirAll(parent, 0o755))
		for dir := parent; dir != root; dir = filepath.Dir(dir) {
			require.NoError(t, os.Chmod(dir, 0o755))
		}
		if !strings.HasSuffix(name, "/") {
			require.NoError(t, os.WriteFile(filename, []byte(content), 0o600))
			require.NoError(t, os.Chmod(filename, 0o644))
		}
	}
	for name, target := range tc.Symlinks {
		filename := filepath.Join(root, name)
		require.NoError(t, os.MkdirAll(filepath.Dir(filename), 0o755))
		require.NoError(t, os.Symlink(target, filename))
	}
	return root
}
