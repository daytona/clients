// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package sandbox

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/cli/config"
)

func TestCreateCmdExposesKvmFlag(t *testing.T) {
	flag := CreateCmd.Flags().Lookup("kvm")
	if flag == nil {
		t.Fatal("kvm flag not registered")
	}

	want := "Expose KVM (/dev/kvm) inside the sandbox via nested virtualization. linux-vm snapshots only. Requires the sandbox_kvm feature for the organization."
	if got := flag.Usage; got != want {
		t.Fatalf("kvm flag help = %q, want %q", got, want)
	}
}

func TestCreateCmdWiresKvmIntoCreateRequest(t *testing.T) {
	origNetworkBlockAll := networkBlockAllFlag
	origKvm := kvmFlag
	origNetworkAllowList := networkAllowListFlag
	origOut := SandboxCmd.OutOrStdout()
	origErr := SandboxCmd.ErrOrStderr()
	t.Cleanup(func() {
		networkBlockAllFlag = origNetworkBlockAll
		kvmFlag = origKvm
		networkAllowListFlag = origNetworkAllowList
		SandboxCmd.SetArgs(nil)
		SandboxCmd.SetOut(origOut)
		SandboxCmd.SetErr(origErr)
	})

	orgID := "ORG-TEST"
	requests := make(chan apiclient.CreateSandbox, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/sandbox":
			var req apiclient.CreateSandbox
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Fatalf("decode create request: %v", err)
			}
			requests <- req
			w.Header().Set("Content-Type", "application/json")
			if err := json.NewEncoder(w).Encode(apiclient.Sandbox{
				Id:              "sandbox-1",
				OrganizationId:  orgID,
				Name:            "sandbox-1",
				User:            "user",
				Env:             map[string]string{},
				Labels:          map[string]string{},
				Public:          false,
				NetworkBlockAll: true,
				Target:          "us",
				ToolboxProxyUrl: "",
				Kvm:             apiclient.PtrBool(true),
			}); err != nil {
				t.Fatalf("encode sandbox response: %v", err)
			}
		case r.Method == http.MethodGet && r.URL.Path == "/api/sandbox/sandbox-1/ports/22222/preview-url":
			w.Header().Set("Content-Type", "application/json")
			if err := json.NewEncoder(w).Encode(apiclient.PortPreviewUrl{Url: "https://preview.example.test"}); err != nil {
				t.Fatalf("encode preview-url response: %v", err)
			}
		default:
			t.Fatalf("unexpected %s %s", r.Method, r.URL.Path)
		}
	}))
	t.Cleanup(server.Close)

	configDir := t.TempDir()
	t.Setenv("DAYTONA_CONFIG_DIR", configDir)
	t.Setenv(config.DAYTONA_API_URL_ENV_VAR, "")
	t.Setenv(config.DAYTONA_API_KEY_ENV_VAR, "")

	token := "token-123"
	profile := config.Config{
		ActiveProfileId: "test",
		Profiles: []config.Profile{{
			Id:   "test",
			Name: "test",
			Api: config.ServerApi{
				Url: server.URL + "/api",
				Token: &config.Token{
					AccessToken:  token,
					RefreshToken: "refresh",
					ExpiresAt:    time.Now().Add(24 * time.Hour),
				},
			},
			ActiveOrganizationId: &orgID,
		}},
	}
	configBytes, err := json.Marshal(profile)
	if err != nil {
		t.Fatalf("marshal config: %v", err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "config.json"), configBytes, 0600); err != nil {
		t.Fatalf("write config: %v", err)
	}

	SandboxCmd.SetOut(io.Discard)
	SandboxCmd.SetErr(io.Discard)
	SandboxCmd.SetArgs([]string{"create", "--kvm", "--network-block-all", "--network-allow-list", "10.0.0.0/8"})

	if err := SandboxCmd.Execute(); err != nil {
		t.Fatalf("execute create command: %v", err)
	}

	req := <-requests
	if !req.GetKvm() {
		t.Fatal("kvm was not wired into the create request")
	}
	if !req.GetNetworkBlockAll() {
		t.Fatal("networkBlockAll was not wired into the create request")
	}
	if got, want := req.GetNetworkAllowList(), "10.0.0.0/8"; got != want {
		t.Fatalf("networkAllowList = %q, want %q", got, want)
	}
}
