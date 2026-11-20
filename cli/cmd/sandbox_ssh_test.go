// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestToolboxSSH_buildsSSHInvocation(t *testing.T) {
	// Given a sandbox name that resolves to a different id and an SSH token.
	const projectKey, token = "project-key-fixture", "sandbox-ssh-token"
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer "+projectKey, r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.EscapedPath() {
		case "/sandboxes/box":
			assert.Equal(t, http.MethodGet, r.Method)
			_, _ = io.WriteString(w, `{"id":"sbx/one","class":"linux","status":"running","toolbox_proxy_url":"https://tb.example","ssh_gateway_host":"ssh.example","ssh_gateway_port":2222}`)
		case "/sandboxes/sbx%2Fone/access-token":
			assert.Equal(t, http.MethodPost, r.Method)
			body, err := io.ReadAll(r.Body)
			assert.NoError(t, err)
			assert.JSONEq(t, `{"operations":["ssh"],"ports":[],"ttl_seconds":300}`, string(body))
			w.WriteHeader(http.StatusCreated)
			_, _ = io.WriteString(w, `{"access_token":"`+token+`"}`)
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL)
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	a, err := app.Load(app.Globals{APIURL: server.URL, APIKey: projectKey, APIKeySet: true, Output: "json"}, &bytes.Buffer{}, &bytes.Buffer{}, nil)
	require.NoError(t, err)
	cmd := newSandboxSSHSubcommand()
	cmd.SetContext(context.WithValue(context.Background(), ctxKey{}, a))

	oldSSHCommand := runSSHCommand
	runSSHCommand = func(_ context.Context, binary string, args []string, _ io.Writer) error {
		require.Contains(t, binary, "ssh")
		// The credential-bearing username lives in a 0600 ssh_config passed
		// with -F, never in argv (visible to every local user via ps).
		require.Len(t, args, 7)
		require.Equal(t, "-F", args[0])
		require.Equal(t, []string{"-L", "8080:localhost:80", "daytona-sandbox", "uname", "-a"}, args[2:])
		for _, a := range args {
			require.NotContains(t, a, token)
			require.NotContains(t, a, projectKey)
		}
		cfg, err := os.ReadFile(args[1])
		require.NoError(t, err)
		require.Contains(t, string(cfg), "HostName ssh.example")
		require.Contains(t, string(cfg), "Port 2222")
		require.Contains(t, string(cfg), `User "sbx/one#`+token+`"`)
		require.NotContains(t, string(cfg), projectKey)
		return nil
	}
	t.Cleanup(func() { runSSHCommand = oldSSHCommand })

	// When connecting by name, then SSH receives only the minted token.
	require.NoError(t, cmd.RunE(cmd, []string{"box", "-L", "8080:localhost:80", "--", "uname", "-a"}))
}

func TestToolboxSSH_redactsTheCredentialFromSSHStderr(t *testing.T) {
	const token = "sandbox-ssh-token-fixture"
	// Held bytes must wait for the next write however slow the machine is.
	oldHold := secretRedactorHold
	secretRedactorHold = time.Hour
	t.Cleanup(func() { secretRedactorHold = oldHold })
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/sandboxes/sbx/access-token" {
			w.WriteHeader(http.StatusCreated)
			_, _ = io.WriteString(w, `{"access_token":"`+token+`"}`)
			return
		}
		assert.Equal(t, "/sandboxes/sbx", r.URL.Path)
		_, _ = w.Write([]byte(`{"id":"sbx","class":"linux","status":"running","toolbox_proxy_url":"https://tb.example","ssh_gateway_host":"ssh.example","ssh_gateway_port":2222}`))
	}))
	defer server.Close()
	a, err := app.Load(app.Globals{APIURL: server.URL, APIKey: "project-key-fixture", APIKeySet: true, Output: "json"}, &bytes.Buffer{}, &bytes.Buffer{}, nil)
	require.NoError(t, err)
	cmd := newSandboxSSHSubcommand()
	cmd.SetContext(context.WithValue(context.Background(), ctxKey{}, a))
	read, write, err := os.Pipe()
	require.NoError(t, err)
	t.Cleanup(func() { _ = read.Close(); _ = write.Close() })
	oldStderr, oldSSHCommand := os.Stderr, runSSHCommand
	os.Stderr = write
	t.Cleanup(func() { os.Stderr, runSSHCommand = oldStderr, oldSSHCommand })
	// Given an ssh client that names the credential-bearing username on
	// stderr, the credential split across two writes.
	runSSHCommand = func(_ context.Context, _ string, _ []string, stderr io.Writer) error {
		_, _ = io.WriteString(stderr, "sbx#"+token[:10])
		_, _ = io.WriteString(stderr, token[10:]+"@ssh.example: Permission denied (publickey).\n")
		_, _ = io.WriteString(stderr, "tail "+token[:3])
		return nil
	}
	// When ssh runs, its stderr reaches the user without the credential.
	require.NoError(t, cmd.RunE(cmd, []string{"sbx"}))
	require.NoError(t, write.Close())
	got, err := io.ReadAll(read)
	require.NoError(t, err)
	require.Equal(t, "sbx#<redacted>@ssh.example: Permission denied (publickey).\ntail "+token[:3], string(got))
}

func TestSecretRedactor_replacesEverySecretAndPassesOtherBytes(t *testing.T) {
	old := secretRedactorHold
	secretRedactorHold = time.Hour
	t.Cleanup(func() { secretRedactorHold = old })
	var out bytes.Buffer
	r := newSecretRedactor(&out, "s3cr3t")
	for _, chunk := range []string{"a s3", "cr3t b ", "s3cr3ts3cr", "3t", " s3c", "x"} {
		n, err := r.Write([]byte(chunk))
		require.NoError(t, err)
		require.Equal(t, len(chunk), n)
	}
	require.NoError(t, r.Flush())
	require.Equal(t, "a <redacted> b <redacted><redacted> s3cx", out.String())

	out.Reset()
	none := newSecretRedactor(&out, "")
	_, err := none.Write([]byte("plain"))
	require.NoError(t, err)
	require.Equal(t, "plain", out.String())
}

// lockedBuffer is a bytes.Buffer safe for the redactor's flush timer.
type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func TestSecretRedactor_writesHeldBytesWhenNothingFollows(t *testing.T) {
	// Given a prompt that ends with the first byte of the secret.
	old := secretRedactorHold
	secretRedactorHold = 500 * time.Millisecond
	t.Cleanup(func() { secretRedactorHold = old })
	var out lockedBuffer
	r := newSecretRedactor(&out, "s3cr3t")
	_, err := r.Write([]byte("password for s"))
	require.NoError(t, err)
	require.Equal(t, "password for ", out.String())
	// Then it reaches the user once nothing follows, without a Flush.
	require.Eventually(t, func() bool { return out.String() == "password for s" }, 5*time.Second, 10*time.Millisecond)
}

func TestSecretRedactor_aStaleFlushDoesNotReleaseALaterPrefix(t *testing.T) {
	old := secretRedactorHold
	secretRedactorHold = time.Hour
	t.Cleanup(func() { secretRedactorHold = old })
	var out bytes.Buffer
	r := newSecretRedactor(&out, "s3cr3t")
	// Given a flush armed by one write that fires after a later write.
	_, err := r.Write([]byte("a s"))
	require.NoError(t, err)
	stale := r.generation
	_, err = r.Write([]byte("3cr"))
	require.NoError(t, err)
	require.NoError(t, r.flushIf(stale))
	// Then the later held prefix stays held, and the secret is still caught.
	_, err = r.Write([]byte("3t b"))
	require.NoError(t, err)
	require.NoError(t, r.Flush())
	require.Equal(t, "a <redacted> b", out.String())
}
