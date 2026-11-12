// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"fmt"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
)

// versionOutput is the CLI-owned `version -o json` shape: the API fields are
// omitted when the API could not be read (or did not say).
type versionOutput struct {
	Version     string `json:"version"`
	Go          string `json:"go"`
	OS          string `json:"os"`
	Arch        string `json:"arch"`
	APIVersion  string `json:"api_version,omitempty"`
	APIBuildSHA string `json:"api_build_sha,omitempty"`
	APIBuiltAt  string `json:"api_built_at,omitempty"`
	APIURL      string `json:"api_url,omitempty"`
	SkewWarning string `json:"skew_warning,omitempty"`
}

type apiVersionInfo struct {
	Version     string
	BuildSHA    string
	BuiltAt     string
	SkewWarning string
}

var versionFetchTimeout = 2 * time.Second

func init() {
	Register(func() *cobra.Command {
		return &cobra.Command{
			Use:         "version",
			Short:       "Print the CLI version and, when reachable, the connected API build identity",
			GroupID:     GroupOther,
			Args:        cobra.NoArgs,
			Annotations: map[string]string{annotationRunsWithoutConfig: "true"},
			RunE: func(cmd *cobra.Command, _ []string) error {
				a := FromContext(cmd.Context())
				if a == nil {
					return printLocalVersion(cmd, LoadError(cmd.Context()))
				}
				v := versionOutput{
					Version: app.Version,
					Go:      runtime.Version(),
					OS:      runtime.GOOS,
					Arch:    runtime.GOARCH,
					APIURL:  a.APIURL(),
				}

				if info, err := fetchAPIVersionInfo(cmd.Context(), a); err != nil {
					if errors.Is(err, context.Canceled) || cerr.ExitCode(err) == cerr.ExitInterrupted {
						return err
					}
					a.Info("Connected API unavailable at %s: %v", v.APIURL, err)
				} else {
					v.APIVersion = info.Version
					v.APIBuildSHA = info.BuildSHA
					v.APIBuiltAt = info.BuiltAt
					v.SkewWarning = info.SkewWarning
				}

				return a.Printer().Raw(renderVersionText(v), v)
			},
		}
	})
}

// printLocalVersion reports the CLI's own version when the config could not
// be loaded, so `daytona version` still works against a broken config.yaml.
func printLocalVersion(cmd *cobra.Command, loadErr error) error {
	if err := cerr.FromContext(cmd.Context()); err != nil {
		return err
	}
	format, err := app.ResolveFormat(cmd.Flag("output").Value.String(), app.WriterIsTerminal(cmd.OutOrStdout()))
	if err != nil {
		return err
	}
	if quiet, _ := cmd.Flags().GetBool("quiet"); !quiet && loadErr != nil {
		fmt.Fprintf(cmd.ErrOrStderr(), "warning: %s; the connected API was not queried\n", output.Sanitize(loadErr.Error()))
	}
	v := versionOutput{Version: app.Version, Go: runtime.Version(), OS: runtime.GOOS, Arch: runtime.GOARCH}
	return output.Printer{W: cmd.OutOrStdout(), Format: format}.Raw(renderVersionText(v), v)
}

func fetchAPIVersionInfo(ctx context.Context, a *app.App) (apiVersionInfo, error) {
	client := a.AnonClient()
	client.HTTP.Timeout = versionFetchTimeout
	config, err := auth.FetchServerConfig(ctx, client)
	if err != nil {
		return apiVersionInfo{}, fmt.Errorf("read /config: %w", err)
	}

	info := apiVersionInfo{
		Version:     strings.TrimSpace(config.Version),
		BuildSHA:    strings.TrimSpace(config.BuildSha),
		SkewWarning: versionSkewWarning(app.Version, config.Version),
	}
	if config.BuiltAt != nil {
		info.BuiltAt = config.BuiltAt.UTC().Format(time.RFC3339)
	}
	return info, nil
}

func renderVersionText(v versionOutput) string {
	lines := []string{fmt.Sprintf("daytona %s (%s/%s)", v.Version, v.OS, v.Arch)}
	if v.APIVersion == "" {
		return strings.Join(lines, "\n")
	}
	builtAt := v.APIBuiltAt
	if builtAt == "" {
		builtAt = "unknown"
	}
	lines = append(lines,
		fmt.Sprintf("Connected API: %s", v.APIURL),
		fmt.Sprintf("  Version: %s", v.APIVersion),
		fmt.Sprintf("  Build SHA: %s", v.APIBuildSHA),
		fmt.Sprintf("  Built At: %s", builtAt),
	)
	if v.SkewWarning != "" {
		lines = append(lines, "Warning: "+v.SkewWarning)
	}
	return strings.Join(lines, "\n")
}

func versionSkewWarning(cliVersion, apiVersion string) string {
	if isUnknownVersion(cliVersion) || isUnknownVersion(apiVersion) {
		return ""
	}
	cliMajor, cliMinor, ok := parseMajorMinor(cliVersion)
	if !ok {
		return ""
	}
	apiMajor, apiMinor, ok := parseMajorMinor(apiVersion)
	if !ok {
		return ""
	}
	if cliMajor != apiMajor {
		return fmt.Sprintf("CLI version %s and API version %s are on different major versions", cliVersion, apiVersion)
	}
	if absInt(cliMinor-apiMinor) > 1 {
		return fmt.Sprintf("CLI version %s and API version %s differ by more than one minor version", cliVersion, apiVersion)
	}
	return ""
}

func parseMajorMinor(version string) (int, int, bool) {
	trimmed := strings.TrimPrefix(strings.TrimSpace(version), "v")
	if trimmed == "" {
		return 0, 0, false
	}
	core, _, _ := strings.Cut(trimmed, "-")
	parts := strings.Split(core, ".")
	if len(parts) != 3 {
		return 0, 0, false
	}
	major, err := strconv.Atoi(parts[0])
	if err != nil {
		return 0, 0, false
	}
	minor, err := strconv.Atoi(parts[1])
	if err != nil {
		return 0, 0, false
	}
	if _, err := strconv.Atoi(parts[2]); err != nil {
		return 0, 0, false
	}
	return major, minor, true
}

func isUnknownVersion(version string) bool {
	trimmed := strings.TrimSpace(version)
	return trimmed == "" || trimmed == "unknown" || trimmed == "0.0.0-dev"
}

func absInt(v int) int {
	if v < 0 {
		return -v
	}
	return v
}
