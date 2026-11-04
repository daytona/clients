// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package main

import (
	"crypto/sha256"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReadmeInstall_installsNewestV2_whenCommandIsCopied(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("install script requires sh")
	}
	// Given the executable shell command published in the README, and a newer
	// v2 release below a v0 release in GitHub's newest-first release list.
	readme, err := os.ReadFile("README.md")
	require.NoError(t, err)
	var command string
	for line := range strings.Lines(string(readme)) {
		if strings.HasPrefix(line, "curl ") && strings.Contains(line, "/install.sh") {
			command = strings.TrimSpace(line)
			break
		}
	}
	require.NotEmpty(t, command)
	root := t.TempDir()
	binDir := filepath.Join(root, "bin")
	mockDir := filepath.Join(root, "mock")
	require.NoError(t, os.Mkdir(mockDir, 0o755))
	asset := "#!/bin/sh\necho newest-v2\n"
	sum := fmt.Sprintf("%x", sha256.Sum256([]byte(asset)))
	writeMockTool(t, filepath.Join(mockDir, "uname"), `#!/bin/sh
if [ "$1" = "-s" ]; then echo Linux; else echo x86_64; fi
`)
	writeMockTool(t, filepath.Join(mockDir, "curl"), fmt.Sprintf(`#!/bin/sh
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
case "$url" in
  *github.com/daytona/clients/releases/download/v2.*/install.sh) cat install.sh ;;
  *api.github.com/repos/daytona/clients/releases?per_page=100)
    echo '[{"tag_name":"v0.999.0"},{"tag_name":"v2.0.0-alpha.9"}]' ;;
  */v2.0.0-alpha.9/daytona-linux-amd64)
    printf '#!/bin/sh\necho newest-v2\n' > "$out" ;;
  */v2.0.0-alpha.9/SHA256SUMS)
    printf '%%s  daytona-linux-amd64\n' '%s' > "$out" ;;
  *) echo "unexpected download: $url" >&2; exit 1 ;;
esac
`, sum))
	cmd := exec.Command("sh", "-c", command)
	cmd.Env = append(os.Environ(),
		"DAYTONA_VERSION=",
		"DAYTONA_RELEASE_REPO=daytona/clients",
		"DAYTONA_INSTALL_DIR="+binDir,
		"PATH="+mockDir+":"+os.Getenv("PATH"),
	)

	// When a user copies the command without selecting a binary version.
	out, err := cmd.CombinedOutput()

	// Then the installed executable comes from the newest v2 release, not the
	// release that supplies the bootstrap script or GitHub's latest v0 release.
	require.NoError(t, err, string(out))
	installed, err := os.ReadFile(filepath.Join(binDir, "daytona"))
	require.NoError(t, err)
	require.Equal(t, asset, string(installed))
}
