// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

// releaseIndex is a fake GitHub release list, newest published first, served
// in pages of pageSize like the releases API. GitHub marks a release as a
// pre-release when its tag has a suffix; prerelease and draft name the
// releases that are marked otherwise.
type releaseIndex struct {
	tags       []string
	pageSize   int
	prerelease []string
	draft      []string
	reordered  bool     // flags before tag_name, as another API version might send them
	raw        []string // page bodies served as they are, instead of tags
}

// githubRelease keeps the field order of GitHub's release objects; the
// nested author and assets carry decoy fields of the same names.
type githubRelease struct {
	URL             string         `json:"url"`
	Author          map[string]any `json:"author"`
	TagName         string         `json:"tag_name"`
	TargetCommitish string         `json:"target_commitish"`
	Name            string         `json:"name"`
	Draft           bool           `json:"draft"`
	Immutable       bool           `json:"immutable"`
	Prerelease      bool           `json:"prerelease"`
	CreatedAt       string         `json:"created_at"`
	Assets          []any          `json:"assets"`
	Body            string         `json:"body"`
	MentionsCount   *int           `json:"mentions_count"` // GitHub's last field: a number, here also null
}

type reorderedRelease struct {
	Prerelease bool           `json:"prerelease"`
	Draft      bool           `json:"draft"`
	Body       string         `json:"body"`
	Author     map[string]any `json:"author"`
	TagName    string         `json:"tag_name"`
	// Ends the object with a number or null, so the next object's first
	// field (a flag) follows a value that no closing token ends.
	MentionsCount *int `json:"mentions_count"`
}

// write stores the index as page-N.json files in dir.
func (r releaseIndex) write(t *testing.T, dir string) {
	t.Helper()
	for i, body := range r.raw {
		require.NoError(t, os.WriteFile(filepath.Join(dir, fmt.Sprintf("page-%d.json", i+1)), []byte(body), 0o644))
	}
	size := r.pageSize
	if size == 0 {
		size = 100
	}
	for page := 0; page*size < len(r.tags); page++ {
		end := min((page+1)*size, len(r.tags))
		var releases []any
		for i, tag := range r.tags[page*size : end] {
			// A numeric mentions_count, and null on every third release.
			var mentions *int
			if i%3 != 2 {
				n := i + 3
				mentions = &n
			}
			draft, prerelease := slices.Contains(r.draft, tag), strings.Contains(tag, "-") || slices.Contains(r.prerelease, tag)
			author := map[string]any{"login": "release-bot", "tag_name": "v9.9.9", "draft": false, "prerelease": false}
			body := `Install: "tag_name": "v9.9.9", "prerelease": false, "draft": false {[\`
			if r.reordered {
				releases = append(releases, reorderedRelease{Prerelease: prerelease, Draft: draft, Body: body, Author: author, TagName: tag, MentionsCount: mentions})
				continue
			}
			releases = append(releases, githubRelease{
				URL: "https://api.github.com/repos/daytona/clients/releases/1", Author: author, TagName: tag, TargetCommitish: "main",
				Name: "daytona CLI " + tag, Draft: draft, Prerelease: prerelease, CreatedAt: "2026-11-01T00:00:00Z",
				Assets: []any{map[string]any{"name": "daytona-linux-amd64", "label": "", "draft": true}}, Body: body,
				MentionsCount: mentions,
			})
		}
		body, err := json.Marshal(releases)
		require.NoError(t, err)
		require.NoError(t, os.WriteFile(filepath.Join(dir, fmt.Sprintf("page-%d.json", page+1)), body, 0o644))
	}
}

// pages is the number of non-empty pages GitHub would serve.
func (r releaseIndex) pages() int {
	size := r.pageSize
	if size == 0 {
		size = 100
	}
	return (len(r.tags) + size - 1) / size
}

var (
	// Before 2.0.0: 0.x stable releases and 2.x alphas, an SDK tag, a beta.
	preGAIndex = releaseIndex{tags: []string{
		"v0.231.0", "v2.0.0-alpha.8", "v0.230.0", "v2.0.0-alpha.7", "python-sdk-1.4.0", "v2.0.0-beta.1", "v2.0.0-alpha.12x",
	}}
	// After 2.0.0, newest published first: an alpha of the next minor and a
	// backport published after a higher stable release, above 0.x releases.
	postGAIndex = releaseIndex{tags: []string{
		"v2.2.0-alpha.1", "v2.0.1", "v0.240.0", "v2.1.0", "v2.1.0-rc.1", "v2.0.0", "v2.0.0-rc.2", "v2.0.0-alpha.12", "v2.0.0-alpha.9",
	}}
	// Numeric, not lexical, ordering.
	numericIndex = releaseIndex{tags: []string{"v2.9.0", "v2.10.0-rc.1", "v2.10.0-alpha.10", "v2.10.0-alpha.9"}}
	// GitHub marks v2.3.0 as a pre-release, and v2.4.0 is a draft.
	flaggedIndex = releaseIndex{tags: []string{"v2.4.0", "v2.3.0", "v2.2.0", "v2.3.0-rc.1"}, prerelease: []string{"v2.3.0"}, draft: []string{"v2.4.0"}}
	// The same, with the flags before the tag in each release object.
	reorderedIndex = releaseIndex{tags: flaggedIndex.tags, prerelease: flaggedIndex.prerelease, draft: flaggedIndex.draft, reordered: true}
	// The 2.x release sits on the second page.
	pagedIndex = releaseIndex{tags: append(repeatTags("v0.%d.0", 100), "v2.0.0", "v2.0.0-alpha.8"), pageSize: 100}
)

func repeatTags(format string, n int) []string {
	tags := make([]string, n)
	for i := range tags {
		tags[i] = fmt.Sprintf(format, 300-i)
	}
	return tags
}

type selectCase struct {
	name    string
	index   releaseIndex
	channel string
	want    string // the version installed; empty = the installer fails
	note    bool   // the "no stable 2.x release yet" note is printed
}

var selectCases = []selectCase{
	{name: "pre-GA default installs the highest pre-release with a note", index: preGAIndex, want: "2.0.0-alpha.8", note: true},
	{name: "pre-GA stable channel finds nothing", index: preGAIndex, channel: "stable"},
	{name: "pre-GA rc channel finds nothing", index: preGAIndex, channel: "rc"},
	{name: "pre-GA alpha channel", index: preGAIndex, channel: "alpha", want: "2.0.0-alpha.8"},
	{name: "post-GA default is the highest stable", index: postGAIndex, want: "2.1.0"},
	{name: "post-GA stable channel", index: postGAIndex, channel: "stable", want: "2.1.0"},
	{name: "post-GA rc channel takes a stable release above every rc", index: postGAIndex, channel: "rc", want: "2.1.0"},
	{name: "post-GA alpha channel", index: postGAIndex, channel: "alpha", want: "2.2.0-alpha.1"},
	{name: "numeric ordering, stable", index: numericIndex, want: "2.9.0"},
	{name: "numeric ordering, rc", index: numericIndex, channel: "rc", want: "2.10.0-rc.1"},
	{name: "numeric ordering, alpha below rc", index: numericIndex, channel: "alpha", want: "2.10.0-rc.1"},
	{name: "second page", index: pagedIndex, want: "2.0.0"},
	{name: "a stable tag marked as a pre-release is not stable", index: flaggedIndex, want: "2.2.0"},
	{name: "drafts never count", index: flaggedIndex, channel: "alpha", want: "2.3.0-rc.1"},
	{name: "flags in any field order", index: reorderedIndex, want: "2.2.0"},
	{name: "flags in any field order, alpha", index: reorderedIndex, channel: "alpha", want: "2.3.0-rc.1"},
}

const fakeBinary = "#!/bin/sh\necho installed\n"

// fakeReleaseCurl is a curl that serves the release index pages from
// $MOCK_INDEX and the linux/amd64 binary of any version, logging each URL.
func fakeReleaseCurl(t *testing.T, dir string) {
	t.Helper()
	writeMockTool(t, filepath.Join(dir, "uname"), "#!/bin/sh\nif [ \"$1\" = -s ]; then echo Linux; else echo x86_64; fi\n")
	writeMockTool(t, filepath.Join(dir, "curl"), fmt.Sprintf(`#!/bin/sh
set -eu
out=''
url=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf '%%s\n' "$url" >> "$MOCK_LOG"
case "$url" in
  https://raw.githubusercontent.com/daytona/clients/main/cli/install.sh) cat "$MOCK_INSTALL_SH" ;;
  'https://api.github.com/repos/daytona/clients/releases?per_page=100&page='*)
    page="${url##*page=}"
    if [ -f "$MOCK_INDEX/page-$page.json" ]; then cat "$MOCK_INDEX/page-$page.json"; else printf '[]'; fi ;;
  https://github.com/daytona/clients/releases/download/v*/daytona-linux-amd64)
    printf '#!/bin/sh\necho installed\n' > "$out" ;;
  https://github.com/daytona/clients/releases/download/v*/SHA256SUMS)
    printf '%%s  daytona-linux-amd64\n' '%x' > "$out" ;;
  *) echo "unexpected download: $url" >&2; exit 1 ;;
esac
`, sha256.Sum256([]byte(fakeBinary))))
}

// runInstallSh runs command (sh -c) against the fake release index and
// returns the output and the version whose binary was downloaded.
func runInstallSh(t *testing.T, command string, index releaseIndex, env ...string) (string, string, error) {
	t.Helper()
	out, got, _, err := runInstallShRequests(t, command, index, env...)
	return out, got, err
}

// runInstallShRequests is runInstallSh that also returns the number of
// release-list requests.
func runInstallShRequests(t *testing.T, command string, index releaseIndex, env ...string) (string, string, int, error) {
	t.Helper()
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh not on PATH")
	}
	mock, indexDir, binDir := t.TempDir(), t.TempDir(), t.TempDir()
	index.write(t, indexDir)
	fakeReleaseCurl(t, mock)
	script, err := filepath.Abs("install.sh")
	require.NoError(t, err)
	log := filepath.Join(t.TempDir(), "curl.log")
	cmd := exec.Command("sh", "-c", command)
	cmd.Env = append(os.Environ(),
		"DAYTONA_VERSION=", "DAYTONA_CHANNEL=", "DAYTONA_RELEASE_REPO=",
		"DAYTONA_INSTALL_DIR="+binDir,
		"MOCK_INDEX="+indexDir, "MOCK_LOG="+log, "MOCK_INSTALL_SH="+script,
		"PATH="+mock+":"+os.Getenv("PATH"),
	)
	cmd.Env = append(cmd.Env, env...)
	out, runErr := cmd.CombinedOutput()
	return string(out), downloadedVersion(t, log, "daytona-linux-amd64"), releaseListRequests(t, log), runErr
}

// releaseListRequests counts the release-list requests in a curl log.
func releaseListRequests(t *testing.T, log string) int {
	t.Helper()
	body, err := os.ReadFile(log)
	if os.IsNotExist(err) {
		return 0
	}
	require.NoError(t, err)
	return strings.Count(string(body), "https://api.github.com/repos/daytona/clients/releases?per_page=100&page=")
}

func downloadedVersion(t *testing.T, log, asset string) string {
	t.Helper()
	body, err := os.ReadFile(log)
	if os.IsNotExist(err) {
		return ""
	}
	require.NoError(t, err)
	for line := range strings.Lines(string(body)) {
		line = strings.TrimSpace(line)
		if rest, ok := strings.CutPrefix(line, "https://github.com/daytona/clients/releases/download/v"); ok && strings.HasSuffix(rest, "/"+asset) {
			return strings.TrimSuffix(rest, "/"+asset)
		}
	}
	return ""
}

func TestInstallSh_selectsTheReleaseOfItsChannel(t *testing.T) {
	for _, tc := range selectCases {
		t.Run(tc.name, func(t *testing.T) {
			// Given a release index, and a channel or none.
			args := ""
			if tc.channel != "" {
				args = " --channel " + tc.channel
			}

			// When the installer runs without a version.
			out, got, requests, err := runInstallShRequests(t, "sh install.sh"+args, tc.index)

			// Then it installs the expected release, or fails naming the
			// channel, reading each page once and stopping at the first empty one.
			require.Equal(t, tc.want, got, out)
			require.Equal(t, tc.index.pages()+1, requests, "release-list requests")
			if tc.want == "" {
				require.Error(t, err, out)
				require.Contains(t, out, "found no "+tc.channel+" 2.x CLI release")
				return
			}
			require.NoError(t, err, out)
			require.Equal(t, tc.note, strings.Contains(out, "no stable 2.x release yet"), out)
			if tc.note {
				require.Contains(t, out, "installing the highest pre-release, v"+tc.want+";")
				require.Contains(t, out, "from 2.0.0 on the default is the highest stable release (--channel alpha or rc opts into pre-releases)")
			}
		})
	}
}

func TestInstallSh_channelFromTheEnvironment(t *testing.T) {
	out, got, err := runInstallSh(t, "sh install.sh", postGAIndex, "DAYTONA_CHANNEL=alpha")

	require.NoError(t, err, out)
	require.Equal(t, "2.2.0-alpha.1", got)
}

func TestInstallSh_exactVersionNeedsNoIndex(t *testing.T) {
	out, got, err := runInstallSh(t, "sh install.sh --version v2.0.0-rc.2", releaseIndex{})

	require.NoError(t, err, out)
	require.Equal(t, "2.0.0-rc.2", got)
	require.NotContains(t, out, "unexpected download")
}

func TestInstallSh_rejectsUsageErrors(t *testing.T) {
	for _, tc := range []struct{ args, env, message string }{
		{args: "--channel beta", message: "unknown channel: beta"},
		{args: "--channel", message: "--channel requires a value"},
		{args: "--channel alpha --version 2.0.0", message: "either a version or a channel"},
		{env: "DAYTONA_CHANNEL=nightly", message: "unknown channel: nightly"},
	} {
		t.Run(tc.args+tc.env, func(t *testing.T) {
			var env []string
			if tc.env != "" {
				env = append(env, tc.env)
			}
			out, got, err := runInstallSh(t, "sh install.sh "+tc.args, postGAIndex, env...)

			var exit *exec.ExitError
			require.ErrorAs(t, err, &exit, out)
			require.Equal(t, 2, exit.ExitCode(), out)
			require.Contains(t, out, tc.message)
			require.Empty(t, got)
		})
	}
}

// A release object that ends with a number (GitHub's mentions_count) before
// one whose flags come first: the second, higher release must not be lost.
var trailingNumberIndex = releaseIndex{raw: []string{
	`[{"url":"a","tag_name":"v2.1.0","draft":false,"prerelease":false,"mentions_count":3},` +
		`{"draft":false,"prerelease":false,"tag_name":"v2.2.0","url":"b"},` +
		`{"url":"c","tag_name":"v2.0.0","draft":false,"prerelease":false,"mentions_count":null},` +
		`{"prerelease":false,"tag_name":"v2.1.5","draft":false}]`,
}}

func TestInstallSh_readsAReleaseAfterOneEndingInANumber(t *testing.T) {
	out, got, requests, err := runInstallShRequests(t, "sh install.sh", trailingNumberIndex)

	require.NoError(t, err, out)
	require.Equal(t, "2.2.0", got)
	require.Equal(t, 2, requests)
}

// A page whose only release has an empty tag is read (that release is no 2.x
// release), not mistaken for one that could not be read; a tag with blanks
// does not shift the draft and prerelease fields.
func TestInstallSh_readsAPageWhoseReleaseHasNoUsableTag(t *testing.T) {
	index := releaseIndex{raw: []string{
		`[{"tag_name":"v2.1.0","draft":false,"prerelease":false},{"tag_name":"v2.3.0 x","draft":false,"prerelease":false}]`,
		`[{"prerelease":true,"draft":false,"tag_name":"","mentions_count":0}]`,
	}}

	out, got, requests, err := runInstallShRequests(t, "sh install.sh", index)

	require.NoError(t, err, out)
	require.Equal(t, "2.1.0", got)
	require.Equal(t, 3, requests)
}

func TestInstallPs1_readsAReleaseAfterOneEndingInANumber(t *testing.T) {
	out, got, requests, err := runInstallPs1Requests(t, `Get-Content -Raw $env:MOCK_INSTALL_PS1 | Invoke-Expression`, trailingNumberIndex)

	require.NoError(t, err, out)
	require.NotContains(t, out, "INSTALL_FAILED")
	require.Equal(t, "2.2.0", got)
	require.Equal(t, 2, requests)
}

func TestInstallSh_failsOnAPageItCannotRead(t *testing.T) {
	// Given a first page of releases whose objects lack the fields the
	// installer reads, which must not pass for the end of the list.
	index := releaseIndex{raw: []string{`[{"tag_name":"v2.1.0"},{"name":"v2.0.0"}]`, `[{"tag_name":"v2.0.0","draft":false,"prerelease":false}]`}}

	out, got, err := runInstallSh(t, "sh install.sh", index)

	require.Error(t, err, out)
	require.Contains(t, out, "could not read the release list of daytona/clients (page 1)")
	require.Empty(t, got)
}

func TestInstallSh_anEmptyPageWithWhitespaceEndsTheList(t *testing.T) {
	index := releaseIndex{raw: []string{`[{"tag_name":"v2.0.0","draft":false,"prerelease":false}]`, " [\n ]\n"}}

	out, got, requests, err := runInstallShRequests(t, "sh install.sh", index)

	require.NoError(t, err, out)
	require.Equal(t, "2.0.0", got)
	require.Equal(t, 2, requests)
}

// 50 pages are read; one release per page stands in for 100.
var (
	fiftyPages     = releaseIndex{tags: append([]string{"v2.0.1"}, repeatTags("v0.%d.0", 49)...), pageSize: 1}
	fiftyOnePages  = releaseIndex{tags: append([]string{"v2.0.1"}, repeatTags("v0.%d.0", 50)...), pageSize: 1}
	tooManyMessage = "daytona/clients has more than 5000 releases, more than this installer reads"
)

func TestInstallSh_readsUpToFiftyPagesAndFailsBeyond(t *testing.T) {
	out, got, requests, err := runInstallShRequests(t, "sh install.sh", fiftyPages)
	require.NoError(t, err, out)
	require.Equal(t, "2.0.1", got)
	require.Equal(t, 51, requests, "50 pages and the empty one after them")

	out, got, requests, err = runInstallShRequests(t, "sh install.sh", fiftyOnePages)
	require.Error(t, err, out)
	require.Contains(t, out, tooManyMessage+"; pass --version X.Y.Z")
	require.Empty(t, got)
	require.Equal(t, 51, requests)
}

func TestInstallPs1_readsUpToFiftyPagesAndFailsBeyond(t *testing.T) {
	const command = `Get-Content -Raw $env:MOCK_INSTALL_PS1 | Invoke-Expression`
	out, got, requests, err := runInstallPs1Requests(t, command, fiftyPages)
	require.NoError(t, err, out)
	require.NotContains(t, out, "INSTALL_FAILED")
	require.Equal(t, "2.0.1", got)
	require.Equal(t, 51, requests, "50 pages and the empty one after them")

	out, got, requests, err = runInstallPs1Requests(t, command, fiftyOnePages)
	require.NoError(t, err, out)
	require.Contains(t, out, "INSTALL_FAILED: "+tooManyMessage+"; set DAYTONA_VERSION")
	require.Empty(t, got)
	require.Equal(t, 51, requests)
}

func TestInstallSh_failsWhenTheReleasesCannotBeListed(t *testing.T) {
	out, got, err := runInstallSh(t, "sh install.sh", postGAIndex, "DAYTONA_RELEASE_REPO=daytona/unreachable")

	require.Error(t, err, out)
	require.Contains(t, out, "could not list the releases of daytona/unreachable")
	require.Empty(t, got)
}

// runInstallPs1 runs command in pwsh and returns the output and the version
// downloaded (see runInstallPs1Requests).
func runInstallPs1(t *testing.T, command string, index releaseIndex, env ...string) (string, string, error) {
	t.Helper()
	out, got, _, err := runInstallPs1Requests(t, command, index, env...)
	return out, got, err
}

// runInstallPs1Requests runs command in pwsh. The release list is served by a
// local HTTP server to the real Invoke-RestMethod (a wrapper only rewrites the
// GitHub API host), so the script sees the cmdlet's own output shape: a JSON
// array arrives as ONE object. Invoke-WebRequest serves the release assets,
// and Invoke-RestMethod of the README's installer URL returns install.ps1. It
// returns the output, the version downloaded and the number of release-list
// requests.
func runInstallPs1Requests(t *testing.T, command string, index releaseIndex, env ...string) (string, string, int, error) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("the fake release binary is a shell script")
	}
	pwsh, err := exec.LookPath("pwsh")
	if err != nil {
		t.Skip("pwsh not on PATH")
	}
	indexDir, installDir := t.TempDir(), t.TempDir()
	index.write(t, indexDir)
	var requests atomic.Int32
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/repos/daytona/clients/releases" || r.URL.Query().Get("per_page") != "100" {
			http.NotFound(w, r)
			return
		}
		requests.Add(1)
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		body, err := os.ReadFile(filepath.Join(indexDir, "page-"+r.URL.Query().Get("page")+".json"))
		if os.IsNotExist(err) {
			body = []byte("[]")
		}
		_, _ = w.Write(body)
	}))
	t.Cleanup(api.Close)
	script, err := filepath.Abs("install.ps1")
	require.NoError(t, err)
	log := filepath.Join(t.TempDir(), "requests.log")
	driver := `$ErrorActionPreference = "Continue"
function Invoke-RestMethod {
  param([Parameter(Position = 0)] $Uri, [switch] $UseBasicParsing)
  Add-Content $env:MOCK_LOG $Uri
  if ($Uri -eq "https://raw.githubusercontent.com/daytona/clients/main/cli/install.ps1") { return Get-Content -Raw $env:MOCK_INSTALL_PS1 }
  if ($Uri -notlike "https://api.github.com/repos/daytona/clients/releases?per_page=100&page=*") { throw "unexpected request: $Uri" }
  Microsoft.PowerShell.Utility\Invoke-RestMethod ($Uri -replace '^https://api\.github\.com', $env:MOCK_API) -UseBasicParsing
}
function Invoke-WebRequest {
  param([Parameter(Position = 0)] $Uri, [switch] $UseBasicParsing, $OutFile)
  Add-Content $env:MOCK_LOG $Uri
  $name = Split-Path $Uri -Leaf
  if ($name -eq "SHA256SUMS") { Set-Content -NoNewline -LiteralPath $OutFile -Value $env:MOCK_SUMS; return }
  Set-Content -NoNewline -LiteralPath $OutFile -Value $env:MOCK_BINARY
  & chmod +x $OutFile
}
try { ` + command + ` } catch { Write-Host "INSTALL_FAILED: $_" }
`
	cmd := exec.Command(pwsh, "-NoProfile", "-NonInteractive", "-Command", driver)
	cmd.Env = append(os.Environ(),
		"DAYTONA_VERSION=", "DAYTONA_CHANNEL=", "DAYTONA_RELEASE_REPO=",
		"DAYTONA_INSTALL_DIR="+installDir,
		"PROCESSOR_ARCHITECTURE=AMD64", "PROCESSOR_ARCHITEW6432=",
		"TEMP="+t.TempDir(),
		"MOCK_API="+api.URL, "MOCK_LOG="+log, "MOCK_INSTALL_PS1="+script,
		"MOCK_BINARY="+fakeBinary,
		fmt.Sprintf("MOCK_SUMS=%x  daytona-windows-amd64.exe\n", sha256.Sum256([]byte(fakeBinary))),
	)
	cmd.Env = append(cmd.Env, env...)
	out, runErr := cmd.CombinedOutput()
	return string(out), downloadedVersion(t, log, "daytona-windows-amd64.exe"), int(requests.Load()), runErr
}

func TestInstallPs1_selectsTheReleaseOfItsChannel(t *testing.T) {
	for _, tc := range selectCases {
		t.Run(tc.name, func(t *testing.T) {
			// Given a release index, and DAYTONA_CHANNEL or none.
			var env []string
			if tc.channel != "" {
				env = append(env, "DAYTONA_CHANNEL="+tc.channel)
			}

			// When the installer runs without DAYTONA_VERSION, as `irm | iex` does.
			out, got, requests, err := runInstallPs1Requests(t, `Get-Content -Raw $env:MOCK_INSTALL_PS1 | Invoke-Expression`, tc.index, env...)

			// Then it picks the same release as install.sh, reading each page
			// once and stopping at the first empty one.
			require.NoError(t, err, out)
			require.Equal(t, tc.want, got, out)
			require.Equal(t, tc.index.pages()+1, requests, "release-list requests")
			if tc.want == "" {
				require.Contains(t, out, "INSTALL_FAILED: found no "+map[bool]string{true: tc.channel, false: "stable"}[tc.channel != ""]+" 2.x CLI release")
				return
			}
			require.NotContains(t, out, "INSTALL_FAILED", out)
			require.Equal(t, tc.note, strings.Contains(out, "no stable 2.x release yet"), out)
			if tc.note {
				require.Contains(t, out, "installing the highest pre-release, v"+tc.want+";")
				require.Contains(t, out, "from 2.0.0 on the default is the highest stable release (DAYTONA_CHANNEL=alpha or rc opts into pre-releases)")
			}
		})
	}
}

func TestInstallPs1_rejectsUsageErrors(t *testing.T) {
	for _, tc := range []struct {
		env     []string
		message string
	}{
		{env: []string{"DAYTONA_CHANNEL=beta"}, message: "unknown DAYTONA_CHANNEL: beta"},
		{env: []string{"DAYTONA_CHANNEL=alpha", "DAYTONA_VERSION=2.0.0"}, message: "set either DAYTONA_VERSION or DAYTONA_CHANNEL"},
	} {
		t.Run(tc.message, func(t *testing.T) {
			out, got, err := runInstallPs1(t, `Get-Content -Raw $env:MOCK_INSTALL_PS1 | Invoke-Expression`, postGAIndex, tc.env...)

			require.NoError(t, err, out)
			require.Contains(t, out, "INSTALL_FAILED: "+tc.message)
			require.Empty(t, got)
		})
	}
}
