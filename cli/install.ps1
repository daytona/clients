# Install the daytona CLI on Windows.
#
#   irm https://github.com/daytona/clients/releases/download/v2.0.0-alpha.3/install.ps1 | iex
#
# Downloads the release binary for this architecture from GitHub Releases,
# verifies it against the release's SHA256SUMS, and installs daytona.exe.
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

if (-not $Version) {
	# Newest tag of the CLI's own 2.x line, not the newest release of any kind
	# (the repo also carries V0 stable and SDK releases).
	$releases = @(Invoke-RestMethod "https://api.github.com/repos/$Repo/releases?per_page=100" -UseBasicParsing)
	$cli = @($releases | Where-Object { $_.tag_name -like "v2.*" })
	$Version = if ($cli.Count -gt 0) { $cli[0].tag_name } else { $null }
	if (-not $Version) { throw "could not determine the newest 2.x CLI release; set DAYTONA_VERSION" }
}
$Version = $Version.TrimStart("v")

$base = "https://github.com/$Repo/releases/download/v$Version"
$asset = "daytona-windows-$arch.exe"
$tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("daytona-" + [guid]::NewGuid()))
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
  Move-Item -Force (Join-Path $tmp $asset) (Join-Path $InstallDir "daytona.exe")
  Write-Host "installed $InstallDir\daytona.exe"
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if (($userPath -split ";") -notcontains $InstallDir) {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$InstallDir", "User")
    Write-Host "added $InstallDir to your user PATH (open a new terminal)"
  }
  & (Join-Path $InstallDir "daytona.exe") version
  if ($LASTEXITCODE -ne 0) {
    throw "installed binary failed verification with exit code $LASTEXITCODE"
  }
} finally {
  Remove-Item -Recurse -Force $tmp
}
