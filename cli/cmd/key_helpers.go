// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	openapi_types "github.com/oapi-codegen/runtime/types"
	"github.com/spf13/pflag"
)

func splitCSV(value string, flagName string) ([]string, error) {
	parts := make([]string, 0)
	for _, raw := range strings.Split(value, ",") {
		item := strings.TrimSpace(raw)
		if item != "" {
			parts = append(parts, item)
		}
	}
	if len(parts) == 0 {
		return nil, cerr.Usage("%s is required", flagName)
	}
	return parts, nil
}

func parseOptionalExpiry(value string) (*time.Time, error) {
	if strings.TrimSpace(value) == "" {
		return nil, nil
	}
	if d, err := time.ParseDuration(value); err == nil {
		if d <= 0 {
			return nil, cerr.Usage("--expires must be greater than zero")
		}
		if d%time.Second != 0 {
			return nil, cerr.Usage("--expires duration must be a whole number of seconds")
		}
		t := time.Now().Add(d)
		return &t, nil
	}
	t, err := time.Parse(time.RFC3339, value)
	if err != nil {
		return nil, cerr.Usage("--expires must be a duration or RFC3339 timestamp")
	}
	if !t.After(time.Now()) {
		return nil, cerr.Usage("--expires must be in the future")
	}
	return &t, nil
}

func sortedKVFromMap(m map[string]any) [][2]string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	kv := make([][2]string, 0, len(keys))
	for _, k := range keys {
		kv = append(kv, [2]string{k, toString(m[k])})
	}
	return kv
}

func stringPtr(value string) *string {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	v := value
	return &v
}

func uuidStrings(values []openapi_types.UUID) []string {
	out := make([]string, 0, len(values))
	for _, value := range values {
		out = append(out, value.String())
	}
	return out
}

func mustOneOf(count int, names ...string) error {
	if count == 1 {
		return nil
	}
	return cerr.Usage("exactly one of %s is required", strings.Join(names, ", "))
}

func formatTokenOnce(token string) string {
	// Server-controlled text at a terminal sink: sanitized like every other.
	return fmt.Sprintf("Token (shown once): %s", output.SanitizeLine(token))
}

// csvListFlag is a repeatable comma-separated list flag stored as one CSV
// string: `--role a --role b,c` accumulates to "a,b,c" (splitCSV parses it).
type csvListFlag struct {
	target *string
	set    bool
}

func (f *csvListFlag) String() string { return *f.target }

func (f *csvListFlag) Type() string { return "strings" }

func (f *csvListFlag) Set(value string) error {
	if f.set && *f.target != "" {
		*f.target += "," + value
	} else {
		*f.target = value
	}
	f.set = true
	return nil
}

// addCSVListFlag registers a repeatable comma-separated list flag.
func addCSVListFlag(fs *pflag.FlagSet, target *string, name, usage string) {
	fs.Var(&csvListFlag{target: target}, name, usage)
}
