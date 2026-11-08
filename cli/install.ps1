# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0

# Install the daytona CLI on Windows.
#
#   irm https://raw.githubusercontent.com/daytona/clients/main/cli/install.ps1 | iex
#   $env:DAYTONA_CHANNEL = "rc"; irm … | iex              # highest release candidate or stable release
#   $env:DAYTONA_VERSION = "2.1.0-alpha.2"; irm … | iex   # an exact version
#
# Without DAYTONA_VERSION it installs the highest stable 2.x release (vX.Y.Z),
# by version, not by publication date. Pre-releases only come with a channel
# (DAYTONA_CHANNEL): `rc` is the highest release candidate or stable release,
# `alpha` the highest alpha, release candidate or stable release. Until the
# first stable 2.x release exists, the default installs the highest
# pre-release and says so; DAYTONA_CHANNEL=stable never does.
#
# Downloads the release binary for this architecture from GitHub Releases,
# verifies it against the release's SHA256SUMS, and installs daytona.exe.
#
# `irm | iex` runs this text in the caller's session: the body is a script
# block so its variables and $ErrorActionPreference stay out of that session.
& {
$ErrorActionPreference = "Stop"

$Repo = if ($env:DAYTONA_RELEASE_REPO) { $env:DAYTONA_RELEASE_REPO } else { "daytona/clients" }
$Version = $env:DAYTONA_VERSION
$Channel = $env:DAYTONA_CHANNEL
if ($Channel -and $Channel -cnotin @("stable", "rc", "alpha")) { throw "unknown DAYTONA_CHANNEL: $Channel (stable, rc or alpha)" }
if ($Version -and $Channel) { throw "set either DAYTONA_VERSION or DAYTONA_CHANNEL, not both" }
$InstallDir = if ($env:DAYTONA_INSTALL_DIR) { $env:DAYTONA_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Daytona\bin" }

$rawArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$arch = switch ($rawArch) {
  "AMD64" { "amd64" }
  "ARM64" { "arm64" }
  default { throw "unsupported architecture: $rawArch" }
}

if (-not $Version) {
  # Every published release of the repository (it also carries the 0.x line).
  # GitHub lists drafts only to maintainers.
  $tags = @()
  for ($page = 1; ; $page++) {
    # Invoke-RestMethod writes a JSON array as ONE object: the inner
    # parentheses enumerate it, so $releases holds one entry per release.
    $releases = @((Invoke-RestMethod "https://api.github.com/repos/$Repo/releases?per_page=100&page=$page" -UseBasicParsing))
    if ($releases.Count -eq 0) { break }
    # At most 50 pages (5,000 releases) are read; a longer list is an error,
    # never a truncated one.
    if ($page -gt 50) { throw "$Repo has more than 5000 releases, more than this installer reads; set DAYTONA_VERSION" }
    # Drafts never count, and a stable-looking tag GitHub marks as a
    # pre-release is not stable.
    $tags += @($releases | Where-Object { -not $_.draft -and -not ($_.prerelease -and "$($_.tag_name)" -notlike "*-*") } | ForEach-Object { $_.tag_name })
  }
  # 2.x CLI tags only, ordered by version: alpha (0) < rc (1) < stable (2).
  $n = "(0|[1-9][0-9]*)"
  $ranked = @(foreach ($tag in $tags) {
    if ("$tag" -cmatch "^v2\.$n\.$n(-(alpha|rc)\.$n)?$") {
      $stage = switch ($Matches[4]) { "alpha" { 0 } "rc" { 1 } default { 2 } }
      $num = if ($Matches[5]) { [int64]$Matches[5] } else { 0 }
      [pscustomobject]@{ Tag = $tag; Minor = [int64]$Matches[1]; Patch = [int64]$Matches[2]; Stage = $stage; Num = $num }
    }
  }) | Sort-Object Minor, Patch, Stage, Num
  $min = switch ($Channel) { "rc" { 1 } "alpha" { 0 } default { 2 } }
  $Version = @($ranked | Where-Object { $_.Stage -ge $min }) | Select-Object -Last 1 -ExpandProperty Tag
  if (-not $Version -and -not $Channel) {
    $Version = @($ranked) | Select-Object -Last 1 -ExpandProperty Tag
    if ($Version) { Write-Host "note: no stable 2.x release yet, so installing the highest pre-release, $Version; from 2.0.0 on the default is the highest stable release (DAYTONA_CHANNEL=alpha or rc opts into pre-releases)" }
  }
  if (-not $Version) {
    $wanted = if ($Channel) { $Channel } else { "stable" }
    throw "found no $wanted 2.x CLI release of $Repo; set DAYTONA_CHANNEL=alpha or DAYTONA_VERSION"
  }
}
$Version = $Version.TrimStart("v")

$base = "https://github.com/$Repo/releases/download/v$Version"
$asset = "daytona-windows-$arch.exe"
$tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("daytona-" + [guid]::NewGuid()))
$staged = $null
try {
  Write-Host "downloading daytona $Version (windows/$arch)"
	Invoke-WebRequest "$base/$asset" -UseBasicParsing -OutFile (Join-Path $tmp $asset)
	Invoke-WebRequest "$base/SHA256SUMS" -UseBasicParsing -OutFile (Join-Path $tmp "SHA256SUMS")
  $line = Get-Content (Join-Path $tmp "SHA256SUMS") | Where-Object { $_ -match " $([regex]::Escape($asset))$" }
  if (-not $line) { throw "SHA256SUMS has no entry for $asset" }
  $expected = ($line -split "\s+")[0].ToLower()
  $actual = (Get-FileHash (Join-Path $tmp $asset) -Algorithm SHA256).Hash.ToLower()
  if ($expected -ne $actual) { throw "checksum mismatch for $asset" }

  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  $target = Join-Path $InstallDir "daytona.exe"
  # Check that the new executable runs before it replaces the installed one.
  # .NET file calls take literal paths (no PowerShell wildcards), and
  # File.Replace swaps the files in one step on NTFS, so a failure cannot
  # leave the install directory without a daytona.exe.
  $staged = Join-Path $InstallDir (".daytona.new." + [guid]::NewGuid() + ".exe")
  [IO.File]::Move((Join-Path $tmp $asset), $staged)
  & $staged version
  if ($LASTEXITCODE -ne 0) {
    throw "the downloaded daytona $Version failed verification with exit code $LASTEXITCODE; $target was not changed"
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
