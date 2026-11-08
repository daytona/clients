// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/getkin/kin-openapi/routers"
)

// fakeAPI is a scripted Daytona API for command tests.
type fakeAPI struct {
	t        *testing.T
	srv      *httptest.Server
	routes   map[string]func(w http.ResponseWriter, r *http.Request)
	mu       sync.Mutex
	requests []recorded
}

type recorded struct {
	Method, Path, Query, Auth string
	Body                      map[string]any
}

var (
	// /test-tools is a synthetic URL base, NOT an API route.
	toolboxFixturePrefix = regexp.MustCompile(`^/test-tools/[^/]+`)
	contractAllowlist    = map[string]string{
		// External OIDC IdP fixtures are intentionally outside the API and toolbox specs.
		"GET /idp/.well-known/openid-configuration": "external OIDC discovery fixture",
		"POST /idp/device":                          "external OIDC device authorization fixture",
		"POST /idp/token":                           "external OIDC token fixture",
		// Standard RFC 8414 authorization-server discovery, separate from the API contract.
		"GET /.well-known/oauth-authorization-server": "RFC 8414 authorization-server metadata",
	}
	uuidRe = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
)

type contractTarget struct {
	route      *routers.Route
	pathParams map[string]string
	request    *http.Request
}

func newFakeAPI(t *testing.T) *fakeAPI {
	f := &fakeAPI{t: t, routes: map[string]func(http.ResponseWriter, *http.Request){}}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var rawBody []byte
		rec := recorded{Method: r.Method, Path: r.URL.Path, Query: r.URL.RawQuery, Auth: r.Header.Get("Authorization")}
		if r.Body != nil {
			rawBody, _ = io.ReadAll(r.Body)
			r.Body = io.NopCloser(bytes.NewReader(rawBody))
		}
		if strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
			var b map[string]any
			_ = json.Unmarshal(rawBody, &b)
			rec.Body = b
		}
		f.mu.Lock()
		f.requests = append(f.requests, rec)
		f.mu.Unlock()
		if h, ok := f.routes[r.Method+" "+r.URL.Path]; ok {
			validateRequestAgainstContract(t, r, rawBody)
			r.Body = io.NopCloser(bytes.NewReader(rawBody))
			rw := &recordingWriter{ResponseWriter: w}
			h(rw, r)
			validateAgainstContractRequest(t, r, rw.status, rw.buf.Bytes(), rw.Header().Get("Content-Type"))
			return
		}
		w.WriteHeader(404)
		_, _ = w.Write([]byte(`{"code":"NOT_FOUND","message":"not found: ` + r.Method + " " + r.URL.Path + `","source":"DAYTONA_API"}`))
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func prepareContractTarget(t *testing.T, req *http.Request, body []byte) (*contractTarget, []string) {
	t.Helper()
	if os.Getenv("DAYTONA_TEST_SKIP_CONTRACT") != "" {
		return nil, nil
	}
	if _, ok := contractAllowlist[req.Method+" "+req.URL.Path]; ok {
		return nil, nil
	}
	apiRouter, toolboxRouter := loadContracts(t)
	contractPath := req.URL.EscapedPath()
	if contractPath == "" {
		contractPath = req.URL.Path
	}
	router := apiRouter
	if prefix := toolboxFixturePrefix.FindString(contractPath); prefix != "" {
		contractPath = strings.TrimPrefix(contractPath, prefix)
		if contractPath == "" {
			contractPath = "/"
		}
		router = toolboxRouter
	}
	contractReq, _ := http.NewRequestWithContext(req.Context(), req.Method, "http://contract"+contractPath, bytes.NewReader(body))
	contractReq.URL.RawQuery = req.URL.RawQuery
	contractReq.Header = req.Header.Clone()
	route, pathParams, err := router.FindRoute(contractReq)
	if err != nil {
		return nil, []string{"route is not in the API/toolbox OpenAPI contracts; add a named allowlist entry only for deliberate external fixtures: " + req.Method + " " + req.URL.Path}
	}
	return &contractTarget{route: route, pathParams: pathParams, request: contractReq}, nil
}

func truncate(b []byte, n int) string {
	if len(b) <= n {
		return string(b)
	}
	return string(b[:n]) + "…"
}

type recordingWriter struct {
	http.ResponseWriter
	status int
	buf    bytes.Buffer
}

func (w *recordingWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

func (w *recordingWriter) Write(p []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	w.buf.Write(p)
	return w.ResponseWriter.Write(p)
}

func (f *fakeAPI) on(method, path string, status int, body any) {
	f.routes[method+" "+path] = func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		switch b := body.(type) {
		case string:
			_, _ = w.Write([]byte(b))
		default:
			_ = json.NewEncoder(w).Encode(b)
		}
	}
}

// handle registers a raw handler; JSON bodies it writes are contract-checked
// like .on() fixtures (set another Content-Type to opt a binary body out).
func (f *fakeAPI) handle(method, path string, h func(w http.ResponseWriter, r *http.Request)) {
	f.routes[method+" "+path] = func(w http.ResponseWriter, r *http.Request) {
		if w.Header().Get("Content-Type") == "" {
			w.Header().Set("Content-Type", "application/json")
		}
		h(w, r)
	}
}

// run executes the CLI with an isolated config dir. Returns stdout, stderr,
// exit code.
func run(t *testing.T, cfgDir string, env map[string]string, args ...string) (string, string, int) {
	t.Helper()
	var out, errb bytes.Buffer
	for _, key := range []string{"DAYTONA_API_URL", "DAYTONA_V2_API_URL", "DAYTONA_API_KEY", "DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT", "DAYTONA_REGION", "DAYTONA_OIDC_CLIENT_ID", "DAYTONA_YES"} {
		t.Setenv(key, "")
	}
	for k, v := range env {
		t.Setenv(k, v)
	}
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir)
	t.Setenv("CI", "1") // non-interactive in tests
	root := NewRoot(&out, &errb)
	root.SetArgs(args)
	err := root.Execute()
	code := 0
	if err != nil {
		printError(&errb, err)
		code = cerr.ExitCode(err)
	}
	return out.String(), errb.String(), code
}

// runNoEnv is run() without t.Setenv (which cannot be called from a goroutine):
// the caller sets DAYTONA_CONFIG_DIR / CI once on the test goroutine.
func runNoEnv(t *testing.T, args ...string) (string, string, int) {
	t.Helper()
	var out, errb bytes.Buffer
	root := NewRoot(&out, &errb)
	root.SetArgs(args)
	err := root.Execute()
	code := 0
	if err != nil {
		printError(&errb, err)
		code = cerr.ExitCode(err)
	}
	return out.String(), errb.String(), code
}

func cfgDir(t *testing.T) string {
	d := filepath.Join(t.TempDir(), "cfg")
	if err := os.MkdirAll(d, 0o700); err != nil {
		t.Fatal(err)
	}
	return d
}

func mustJSONMap(t *testing.T, s string) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(s), &m); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, s)
	}
	return m
}

func mustJSON(t *testing.T, s string) map[string]any {
	return mustJSONMap(t, s)
}

func has(s, sub string) bool { return strings.Contains(s, sub) }

// findRequest returns the LAST recorded request with the given method and
// path, failing the test when none was made — assertions on request order
// are brittle now that a non-UUID --project triggers a resolution lookup.
func findRequest(t *testing.T, f *fakeAPI, method, path string) recorded {
	t.Helper()
	for i := len(f.requests) - 1; i >= 0; i-- {
		if f.requests[i].Method == method && f.requests[i].Path == path {
			return f.requests[i]
		}
	}
	t.Fatalf("no %s %s request recorded; got %+v", method, path, f.requests)
	return recorded{}
}
