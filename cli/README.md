# daytona — the Daytona CLI

Sandboxes, images, snapshots, and your organization, from the terminal. Built
for people at a keyboard and for coding agents driving a shell: interactive at
a terminal, strict and scriptable when piped.

> Alpha. The 2.x line targets the Daytona V2 platform. Versions are
> `2.0.0-alpha.N` until launch.

## Install

macOS / Linux (Homebrew, alpha channel):

```sh
brew install daytonaio/cli/daytona-alpha
```

macOS / Linux (script — verifies the release checksum):

```sh
curl -fsSL https://github.com/daytona/clients/releases/download/v2.0.0-alpha.3/install.sh | sh
```

Windows (PowerShell):

```powershell
irm https://github.com/daytona/clients/releases/download/v2.0.0-alpha.3/install.ps1 | iex
```

These URLs fetch the installer from a known 2.x release; the installer selects
the newest 2.x release unless `DAYTONA_VERSION` is set. The repository's
`releases/latest` URL does not select the 2.x CLI. Use the release
assets above to install the 2.x CLI.

Or download the binary from the
[releases](https://github.com/daytona/clients/releases) and put it on your
`PATH`: `daytona-linux-<amd64|arm64>`, `daytona-darwin-<amd64|arm64>`, or
`daytona-windows-<amd64|arm64>.exe`; `SHA256SUMS` lists every asset.

## Sign in

```sh
daytona login               # opens your browser
daytona login --device      # force the device flow (automatic off-TTY and inside SSH sessions)
daytona login --browser     # force the browser flow inside SSH (forward the printed port with ssh -L)
daytona login --token dtn_… # a project API key
```

The CLI connects to the Daytona API (`https://api.daytona.com`), the same
default as the SDKs.

A browser or device login selects your personal organization and its default
project the first time and remembers your last choice afterwards. Switch with
`daytona org use <name>` and `daytona project use <name>`; override for one
command with `--org` / `--project`.

Sandbox commands run on credentials derived from your session (see Credentials
below). `daytona logout` removes the local session and attempts to revoke this
profile's login-minted project keys.

## Use

```sh
daytona image create py312 --ref python:3.12
daytona sandbox create --image py312 --cpu 2 --memory 2048   # CPU in cores (1 core = 2 vCPUs)
daytona sandbox create                   # no --image: boots the platform default image
daytona image list --platform true       # the platform images every project can boot
daytona sandbox list
daytona sandbox search python --status running
daytona sandbox search --created-start-at 2026-10-01T00:00:00Z --created-end-at 2026-11-01T00:00:00Z
daytona sandbox exec <id> -- python -c 'print("hello")'
daytona sandbox docker status <id> --wait
daytona sandbox ssh <id>
daytona sandbox cp ./data.csv <id>:/work/data.csv
daytona volume create shared --type bucket
daytona sandbox create --image py312 --volume shared:/models:ro
daytona snapshot create <id> --name saved-state
daytona sandbox create-from-snapshot saved-state --name resumed   # resume a memory snapshot
daytona image capture <id> --name py312-after-setup
daytona image promote img_123 keep-this   # name an anonymous image (makes it permanent)
daytona org limits                     # sandbox limits and current usage
daytona billing usage                  # current open billing period
daytona billing invoices --unpaid
daytona billing plan list
daytona billing portal-session --open
daytona sandbox delete <id>
```

`daytona --help` lists every command group; `daytona docs --full` prints the
whole reference on one page.

`daytona region list` prints the region catalog as a tree, each NAME indented
under the region it is inside, with the columns NAME, ID, PRICE MULTIPLIER and
AVAILABLE. PRICE MULTIPLIER is what
a create naming the region is billed at, relative to `earth`. It is read for
the selected organization after `daytona login` with `read:billing`; with an
API key or without that permission the table shows `-` and JSON/YAML output
carries `"price_multiplier": null`. `--region` keeps its name on create,
search and catalog commands. On a sandbox create, omitting it falls back to the
project's default, then the organization's, then `earth`; `earth` means
anywhere and may land in any geography. A known unavailable region returns
`REGION_UNAVAILABLE` (409). Image creates have no `earth` fallback: omitting
`--region` uses the project's default region, then the organization's, then the
platform's default region. They also require a narrower region than `earth` or
a broad area and return 400 when the selected region is too broad.

`daytona version` always prints the CLI's own version. It also reads
`GET /config` from the API the CLI is pointed at (the default API unless
`--api-url`, `DAYTONA_V2_API_URL` or `DAYTONA_API_URL` says otherwise) and
prints that API's version, build SHA, and build time, with a warning when the
CLI and API versions are more than one minor version apart. If the API cannot be reached within two seconds, the
command still exits 0 and prints the CLI version plus a short diagnostic note.

`daytona sandbox docker status <id>` reads the sandbox's Docker Engine state
(`ready`, `starting`, `failed`, `not_present`, `disabled`, `unmanaged`,
`deferred`, `unsupported`), who owns it, the optional reason, and the process
whose logs hold Docker's output. Add `--wait` to poll until Docker is `ready`;
if `--timeout` is not set, the command uses a default wait budget of 60s.

### macOS sandboxes

A macOS sandbox runs on a dedicated physical Mac your organization reserves.
A reservation buys one physical Mac of a named model. Set sandbox sizes at create.

Find regions and their regional API endpoints with `daytona region list`
(`GET /regions`). Use `daytona mac-model list --region '<region>'` to discover
available Mac models and current prices. Replace `<region>` and `<model>`
below with values from these catalogs.

```sh
daytona region list
daytona mac-model list
# Free Macs and prices per model in a region:
daytona mac-model list --region '<region>'
daytona reservation create --region '<region>' --model '<model>'
# Several Macs of one model in one request; --dry-run prints the price first:
daytona reservation create --region '<region>' --model '<model>' --count 3 --dry-run
daytona reservation create --region '<region>' --model '<model>' --count 3 --idempotency-key nightly-macs
# Use the model's default sandbox size:
daytona sandbox create-macos --region '<region>' --image base-macos
# Or set this sandbox's CPU, memory, and disk sizes:
daytona sandbox create-macos --region '<region>' --image base-macos --reservation RESERVATION_ID --cores 10 --memory 16384 --disk 163840 --persistent
# Pay for the next period; a retry with the same key never pays twice:
daytona reservation renew RESERVATION_ID --idempotency-key renew-RESERVATION_ID-2
daytona reservation update RESERVATION_ID --auto-renew=false
```

`mac-model list` shows each model's `MAX_SANDBOXES` and `DEFAULT_SIZE`.
Each omitted `--cores`, `--memory`, or `--disk` uses half of the reserved Mac
on a model that runs two sandboxes, or all of it on a model that runs one.
Defaults round down, and disk rounds down to a whole GiB. For `--disk`, the
total is the model's sandbox disk room (`SANDBOX_DISK_MIB` in `mac-model list`),
not the Mac's whole SSD. Sandbox sizes do not change the reservation.
The size is fixed at creation, and `daytona
sandbox get` shows the associated reservation as `reservation_id`. `--disk` is the
whole sandbox disk in MiB, macOS included, at least 81920. Without `--model`, a
reservation gets the region's default model; an unavailable model is never
upgraded.

`reservation create --count N` reserves N Macs, all or nothing; add
`--min-count M` to accept as few as M when fewer are free. `reservation create`
and `reservation renew` send a fresh `Idempotency-Key` per invocation unless
`--idempotency-key` is given; pass your own to retry safely.

`sandbox create-macos` with no `--reservation`, or with `--reservation new`,
may buy a 24-hour Mac reservation billed to your organization. These commands
and `reservation create` ask for confirmation at a terminal; off-terminal,
pass `--yes`, set `DAYTONA_YES=1`, or run `daytona config set confirm false`.
Using an existing reservation ID or `reservation create --dry-run` does not
prompt.

Stop and pause need a persistent sandbox (`--persistent`). Stop keeps the disk on the same Mac, and start and resume use that Mac. Delete erases the disk. At reservation end, a persistent sandbox's disk is saved as an anonymous image before the Mac is erased, and an ephemeral sandbox's disk is erased unsaved; a lost Mac saves persistent disks if it comes back within 7 days. If a Mac restarts before it is declared lost, a running persistent sandbox comes back stopped with its disk, a running ephemeral sandbox is lost, and stopped and paused sandboxes are unchanged. Create a sandbox from an image to continue on another Mac.

## Scripts and agents

When stdout is not a terminal, or with `--output json`, the CLI never prompts:

- `--output json` (or `yaml`) on every command that returns data; payload on
  stdout, diagnostics on stderr.
- Destructive commands (`delete`, `remove`, `revoke`, `leave`) prompt
  at a terminal; off-terminal they refuse unless one of `--yes`,
  `DAYTONA_YES=1` or `daytona config set confirm false` says yes — and refuse
  before anything reaches the server.
- Server-paginated lists (`sandbox`, `snapshot`, `image`, `secret`,
  `registry`, `audit`, `reservation`, `sandbox search`, and billing `balances`,
  `usage-periods`, `payment-methods`, `charges`, and `invoices`) return one page; `--limit`,
  `--cursor <token>`, `--all`
  page through them. `sandbox list` and `image list` query each distinct API
  base URL in the region catalog once for fresh listings and regional cursors,
   unless `--region` narrows them. Saved cursors resume unfinished pages.
   Results merge regional cursors and de-duplicate resources by ID. Every other list (`region`, `cpu-type`,
  `mac-model`, `org`, `member`, `project`, `key`,
  `service-account`, …) is returned in full by the server and takes no
  paging flags. A sandbox or image list that could not reach every region
  prints what it has and exits **7**.
- Sandbox search time filters use half-open ranges: `--created-start-at` /
  `--created-end-at`, `--updated-start-at` / `--updated-end-at`, and
  `--last-activity-start-at` / `--last-activity-end-at`. Start bounds are
  inclusive; end bounds are exclusive. Values are RFC3339 timestamps.
- Billing amounts are integer cents plus a currency. One-time top-ups use
  `--amount-cents`; automatic top-up and low-balance settings use
  `--threshold-cents` (and automatic top-up also uses `--target-cents`).
- Long operations wait for completion by default; `--no-wait` returns the
  accepted resource immediately. `--timeout 10m` bounds the wait (the server
  keeps working).
- `--wait <seconds>` on every `sandbox create*` command sends
  the server-side hold budget; `--create-ttl` is the deadline (seconds) for the sandbox
  to reach `running` — queued OR booting — before the server deletes it
  (`-1` = server max).
- Complex requests take `--file body.json` (or `--file -` for stdin); explicit
  flags override fields from the file.
- Bucket volumes are created with `daytona volume create NAME --type bucket`; add
  `--bucket`, `--endpoint`, `--region`, `--prefix`, `--path-style`,
  `--access-key-secret`, and `--secret-key-secret` for a customer bucket.
- `sandbox create --volume` accepts `VOLUME:MOUNT_PATH`,
  `VOLUME:MOUNT_PATH:ro`, or `VOLUME:MOUNT_PATH:SUBPATH[:ro]`.
  `sandbox create-macos --volume` takes `VOLUME:MOUNT_PATH[:SUBPATH]`:
  a macOS sandbox mounts disk volumes read-write only. Use the `volumes`
  array in `--file` JSON for the same options; an explicit `--volume`
  replaces that array.
- `sandbox create --allow-domain api.example.com --allow-cidr 10.1.0.0/16`
  (or `sandbox create-macos` with the same flags) sets a `network_policy`: a domain list implies default-deny (allowed
  domains are reachable on HTTP/HTTPS only) and `--allow-cidr` opens addresses
  on any port. `--block-all` with `--allow-cidr` selects the CIDR-only mode,
  which is **not a DNS exfiltration boundary**: DNS A lookups for any name are
   still sent to DNS resolvers and only answers inside the allowed CIDRs come
  back, so a sandbox can carry data out in the names it looks up. When lookups
  must be contained, put the names that must resolve in `--allow-domain`, or
  set `"allow_domains": []` in a `--file` body to resolve no tenant name (a
  domain list, even an empty one, also denies TCP 80/443 to the allowed CIDRs
  unless the name is listed). `--block-all` without an allow list
   blocks all outbound traffic and DNS lookups to user-specified domains.
   `--allow-cidr` without `--block-all` or `--allow-domain` is a
  usage error, because the resulting policy would be open.

Exit codes: `0` ok · `1` failure · `2` usage · `3` authentication · `4`
authorization · `5` not found · `6` conflict · `7` partial result · `8` wait
timed out · `130` interrupted.

## Credentials

The CLI sends whatever credential you give it and lets the server decide what
it may do. Precedence: `--api-key` > `DAYTONA_API_KEY` > the active profile.
An explicit credential is the only one used for that command — the CLI never
falls back to your saved login.

After `daytona login`, sandbox, image, secret, and registry commands
use automatically refreshed, project-scoped credentials. Platform tokens expire
after five minutes; automatically minted project API keys expire after 24 hours.
The CLI uses project API keys for `exec`, `cp`, `logs`, `port` and `ssh`, and as
a fallback when platform tokens are unavailable. Automatically minted keys stay
on Daytona V2. A replacement attempts to revoke its superseded key;
static credentials' exchanged keys use the same 24-hour expiry and replacement
policy. Your supplied static credential is never revoked automatically.

Browser/device relogin clears cached login keys and platform tokens before
publishing the new session, then attempts to revoke the old login's keys with
its old credential at its saved API, within a three-second total budget.
This also applies when switching accounts or API URLs.

`daytona logout` removes this profile's saved tokens and key cache first, then
attempts to revoke its cached login-minted project keys at the profile's saved
API. Revocation has a three-second total budget, including any token refresh;
local logout completes even when the server is unreachable. Keys that could
not be revoked can remain valid until their expiry. Logout ignores API URL and
`--api-key` overrides and does not end the identity provider's session or revoke
manually supplied credentials, other profiles' keys, or keys from other APIs.
Static-credential exchanges are not revoked on logout: their source credential is
separate from the login being signed out and may not be stored. Keys no longer
tracked after replacement with `login --token` are not revoked by logout.
`daytona profile delete` performs the same revocation for the deleted profile.

A cached platform token is sent only to the API that issued it. With
`--api-url` (or `DAYTONA_V2_API_URL` / `DAYTONA_API_URL`) pointing at another
API, the CLI exchanges a new token there when that API issues platform tokens,
and otherwise uses a project API key as described above.

Environment: `DAYTONA_API_KEY`, `DAYTONA_PROFILE`,
`DAYTONA_ORG`, `DAYTONA_PROJECT`, `DAYTONA_REGION`, `DAYTONA_YES`,
`DAYTONA_CONFIG_DIR`, `NO_COLOR`.

`DAYTONA_V2_API_URL` (or `DAYTONA_API_URL`, or `--api-url`) points the CLI
at another API; for test environments only. A login saves the URL in its
profile. Unset these variables to use the default Daytona API.

Configuration lives in `~/.config/daytona/` (`$XDG_CONFIG_HOME/daytona`), in
files readable only by you.

## Develop

```sh
go build ./... && go test ./...
golangci-lint run ./...
./hack/gen.sh      # regenerate wire types from ../contract/*.json
./hack/build.sh    # six release binaries + SHA256SUMS into dist/
go test ./cmd -run TestPublicSurface -update   # regenerate api-surface/daytona.api.txt
```

`api-surface/daytona.api.txt` lists the CLI's public surface: every command
path with its aliases, positional arguments, the argument counts it accepts
and the flag groups cobra enforces on it (mutually exclusive, one required,
required together); every flag with its type, shorthand and default and its
hidden, persistent and deprecated state and whether cobra marks it required;
and the environment variables the CLI reads. A requirement the command checks
itself (such as `image create` needing `--ref` unless `--file` supplies the
image) is not in the snapshot. `go test` fails when it is stale;
regenerate it after changing any of them and commit the result.

Wire types are generated from the public `contract/` OpenAPI artifacts and
must not be edited by hand.
