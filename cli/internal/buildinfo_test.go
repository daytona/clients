// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package internal

import (
	"path/filepath"
	"testing"
)

func TestAdoptInvokedName(t *testing.T) {
	tests := []struct {
		built, arg0           string
		wantName, wantFormula string
	}{
		{"daytona-v0", filepath.Join("usr", "local", "bin", "daytona"), "daytona", "daytonaio/cli/daytona"},
		{"daytona-v0", "daytona.exe", "daytona", "daytonaio/cli/daytona"},
		{"daytona-v0", "Daytona.EXE", "daytona", "daytonaio/cli/daytona"},
		{"daytona-v0", filepath.Join("bin", "daytona-v0"), "daytona-v0", "daytonaio/cli/daytona@0"},
		{"daytona-v0", "daytona-v0-linux-amd64", "daytona-v0", "daytonaio/cli/daytona@0"},
		{"daytona-v0", "daytona-linux-amd64", "daytona-v0", "daytonaio/cli/daytona@0"},
		{"daytona", "daytona-v0", "daytona", "daytonaio/cli/daytona"},
	}
	for _, tt := range tests {
		t.Run(tt.built+"/"+tt.arg0, func(t *testing.T) {
			savedName, savedFormula := BinaryName, HomebrewFormula
			t.Cleanup(func() { BinaryName, HomebrewFormula = savedName, savedFormula })

			BinaryName = tt.built
			HomebrewFormula = "daytonaio/cli/daytona"
			if tt.built == "daytona-v0" {
				HomebrewFormula = "daytonaio/cli/daytona@0"
			}

			adoptInvokedName(tt.arg0)
			if BinaryName != tt.wantName || HomebrewFormula != tt.wantFormula {
				t.Errorf("got (%q, %q), want (%q, %q)", BinaryName, HomebrewFormula, tt.wantName, tt.wantFormula)
			}
		})
	}
}
