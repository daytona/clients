# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0

# Install the Daytona 0.x CLI as `daytona-v0.exe` (Windows), next to the 2.x
# `daytona.exe` CLI.
#
#   irm https://raw.githubusercontent.com/daytona/clients/release/v0/cli/install-v0.ps1 | iex
#   $env:DAYTONA_VERSION = "0.X.Y"; irm … | iex                   # a specific 0.x release
#   $env:DAYTONA_INSTALL_DIR = "C:\tools\daytona"; irm … | iex     # a custom directory
#
# Downloads the daytona-v0-windows-<arch>.exe asset of the newest stable 0.x
# release (or DAYTONA_VERSION), verifies it against the release's SHA256SUMS,
# checks that it runs, and installs it as daytona-v0.exe. Releases from before
# the 0.x line was renamed only carry daytona-windows-<arch>.exe assets and
# cannot be installed with this script.
#
# `irm | iex` runs this text in the caller's session: the body is a script
# block so its variables and $ErrorActionPreference stay out of that session.
& {
$ErrorActionPreference = "Stop"

$Repo = if ($env:DAYTONA_RELEASE_REPO) { $env:DAYTONA_RELEASE_REPO } else { "daytona/clients" }
$Version = $env:DAYTONA_VERSION
$InstallDir = if ($env:DAYTONA_INSTALL_DIR) { $env:DAYTONA_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Daytona\bin" }

$rawArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$arch = switch ($rawArch) {
  "AMD64" { "amd64" }
  "ARM64" { "arm64" }
  default { throw "unsupported architecture: $rawArch" }
}

$n = "(0|[1-9][0-9]*)"
if (-not $Version) {
  # The repository releases the 2.x line too, so "Latest" is not the newest
  # 0.x release: take the highest v0.X.Y tag of a release that is neither a
  # draft nor marked prerelease.
  $ranked = @()
  for ($page = 1; ; $page++) {
    # Invoke-RestMethod writes a JSON array as ONE object: the inner
    # parentheses enumerate it, so $releases holds one entry per release.
    $releases = @((Invoke-RestMethod "https://api.github.com/repos/$Repo/releases?per_page=100&page=$page" -UseBasicParsing))
    if ($releases.Count -eq 0) { break }
    # At most 50 pages (5,000 releases) are read; a longer list is an error,
    # never a truncated one.
    if ($page -gt 50) { throw "$Repo has more than 5000 releases, more than this installer reads; set DAYTONA_VERSION" }
    $ranked += @(foreach ($release in $releases) {
      if (-not $release.draft -and -not $release.prerelease -and "$($release.tag_name)" -cmatch "^v0\.$n\.$n$") {
        [pscustomobject]@{ Tag = $release.tag_name; Minor = [int64]$Matches[1]; Patch = [int64]$Matches[2] }
      }
    })
  }
  $Version = @($ranked | Sort-Object Minor, Patch) | Select-Object -Last 1 -ExpandProperty Tag
  if (-not $Version) { throw "could not determine the newest 0.x release of $Repo; set DAYTONA_VERSION to 0.X.Y" }
}
$Version = $Version.TrimStart("v")
if ($Version -cnotmatch "^0\.$n\.$n$") { throw "this script installs the 0.x CLI only (got $Version)" }

$base = "https://github.com/$Repo/releases/download/v$Version"
$asset = "daytona-v0-windows-$arch.exe"
$tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("daytona-v0-" + [guid]::NewGuid()))
$staged = $null
try {
  Write-Host "downloading daytona-v0 $Version (windows/$arch)"
  try {
    Invoke-WebRequest "$base/$asset" -UseBasicParsing -OutFile (Join-Path $tmp $asset)
  } catch {
    throw "release v$Version has no $asset asset (releases from before the daytona-v0 rename cannot be installed with this script): $_"
  }
  Invoke-WebRequest "$base/SHA256SUMS" -UseBasicParsing -OutFile (Join-Path $tmp "SHA256SUMS")
  $line = Get-Content (Join-Path $tmp "SHA256SUMS") | Where-Object { $_ -match " $([regex]::Escape($asset))$" }
  if (-not $line) { throw "SHA256SUMS has no entry for $asset" }
  $expected = ($line -split "\s+")[0].ToLower()
  $actual = (Get-FileHash (Join-Path $tmp $asset) -Algorithm SHA256).Hash.ToLower()
  if ($expected -ne $actual) { throw "checksum mismatch for $asset" }

  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  $target = Join-Path $InstallDir "daytona-v0.exe"
  # Check that the new executable runs before it replaces the installed one.
  # .NET file calls take literal paths (no PowerShell wildcards), and
  # File.Replace swaps the files in one step on NTFS, so a failure cannot
  # leave the install directory without a daytona-v0.exe.
  $staged = Join-Path $InstallDir (".daytona-v0.new." + [guid]::NewGuid() + ".exe")
  [IO.File]::Move((Join-Path $tmp $asset), $staged)
  & $staged version
  if ($LASTEXITCODE -ne 0) {
    throw "the downloaded daytona-v0 $Version failed verification with exit code $LASTEXITCODE; $target was not changed"
  }
  if ([IO.File]::Exists($target)) {
    [IO.File]::Replace($staged, $target, [NullString]::Value)
  } else {
    [IO.File]::Move($staged, $target)
  }
  $staged = $null
  Write-Host "installed $target"
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if (($userPath -split ";") -notcontains $InstallDir) {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$InstallDir", "User")
    Write-Host "added $InstallDir to your user PATH (open a new terminal)"
  }
} finally {
  Remove-Item -Recurse -Force $tmp
  if ($staged -and [IO.File]::Exists($staged)) { [IO.File]::Delete($staged) }
}
}
