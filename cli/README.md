# daytona — the Daytona CLI

Sandboxes, images, snapshots, and your organization, from the terminal. Built
for people at a keyboard and for coding agents driving a shell: interactive at
a terminal, strict and scriptable when piped.

> Alpha. The 2.x line targets the Daytona V2 platform. Versions are
> `2.0.0-alpha.N` until launch.

## Install

macOS / Linux (script — verifies the release checksum):

```sh
curl -fsSL https://raw.githubusercontent.com/daytona/clients/main/cli/install.sh | sh
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/daytona/clients/main/cli/install.ps1 | iex
```

The installer installs the highest stable 2.x release. Until `2.0.0` is
out there is none, so it installs the highest pre-release and names it; from
`2.0.0` on the default is the highest stable release, and `--channel alpha`
or `rc` opts into pre-releases. Pick a channel or an exact version explicitly:

```sh
curl -fsSL https://raw.githubusercontent.com/daytona/clients/main/cli/install.sh | sh -s -- --channel alpha
curl -fsSL https://raw.githubusercontent.com/daytona/clients/main/cli/install.sh | sh -s -- --version 2.0.0
```

On Windows set `$env:DAYTONA_CHANNEL` (`stable`, `rc` or `alpha`) or
`$env:DAYTONA_VERSION` before running the installer. The `rc` channel
installs the highest release candidate or stable release, `alpha` the highest
alpha, release candidate or stable release. The install directory is
`--dir` for `install.sh` and `$env:DAYTONA_INSTALL_DIR` for `install.ps1`
(`DAYTONA_INSTALL_DIR` works for both).

Homebrew (macOS / Linux):

| Formula | Installs |
|---|---|
| `daytonaio/cli/daytona` | stable 2.x releases as `daytona` (until `2.0.0` is out, the 0.x CLI) |
| `daytonaio/cli/daytona-rc` | 2.x release candidates as `daytona`, from the first 2.x rc on |
| `daytonaio/cli/daytona-alpha` | 2.x alpha releases as `daytona` |
| `daytonaio/cli/daytona@0` | the 0.x CLI as `daytona-v0`, beside any of the above |

```sh
brew install daytonaio/cli/daytona-alpha   # before 2.0.0
brew install daytonaio/cli/daytona         # from 2.0.0
```

The first three formulae install the same `daytona` binary, so install one of
them.

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

`login --profile NAME` creates a named connection; `profile use NAME` selects
one. Names use 1–64 ASCII letters, digits, `.`, `_` or `-`, except `.` and
`..`. Older saved names still load and can be refreshed with `login`. An `active_profile` in the configuration that names a missing profile stops the commands that use it (exit 5), like an unknown `--profile`. Deleting the active profile explicitly
selects `default` when it exists and announces that on stderr; otherwise no
profile is active, and the CLI asks you to select one or log in.

## Use

```sh
daytona image create py312 --ref python:3.12
daytona sandbox create --image py312 --cpu 2 --memory 2048   # CPU in cores (1 core = 2 vCPUs)
daytona sandbox create                   # no --image: boots the platform default image
daytona sandbox create --image py312 --lifecycle-timeout 2h --on-lifecycle-timeout delete
daytona image list --platform true       # the platform images every project can boot
daytona sandbox list
daytona sandbox search python --status running
daytona sandbox search --created-start-at 2026-10-01T00:00:00Z --created-end-at 2026-11-01T00:00:00Z
daytona sandbox exec <id> -- python -c 'print("hello")'
daytona sandbox docker status <id> --wait
daytona sandbox ssh <id>
daytona sandbox cp ./data.csv <id>:/work/data.csv
daytona sandbox port preview-url <id> 8080 --signed --ttl 10m
daytona volume create shared --type bucket
daytona sandbox create --image py312 --volume shared:/models:ro
daytona snapshot create <id> --name saved-state
daytona sandbox create-from-snapshot saved-state --name resumed   # resume a memory snapshot
daytona image build my-app --context .  # honours .dockerignore
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
whole reference on one page. Use `daytona docs --dir DIR` for separate Markdown
files instead; `--full` and `--dir` cannot be combined.

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
`--api-url` or `DAYTONA_V2_API_URL` says otherwise) and
prints that API's version, build SHA, and build time, with a warning when the
CLI and API versions are more than one minor version apart. If the API cannot be reached within two seconds, the
command still exits 0 and prints the CLI version plus a short diagnostic note.

`daytona sandbox docker status <id>` reads the sandbox's Docker Engine state
(`ready`, `starting`, `failed`, `not_present`, `disabled`, `unmanaged`,
`deferred`, `unsupported`), who owns it, the optional reason, and the process
whose logs hold Docker's output. Add `--wait` to poll until Docker is `ready`;
`--timeout` bounds that wait (default `60s`).

`daytona image build <name> --context DIR` uploads the directory without what
its ignore file excludes: `<Dockerfile>.dockerignore` next to the Dockerfile
when it exists, otherwise `.dockerignore` at the context root. What is
uploaded matches what `docker build` (BuildKit) uploads: the ignore file is
read as Docker reads it (`#` comment lines, `*`, `?`, `**`, character
classes, `!` exceptions), patterns are evaluated in order and the last one
that matches decides, and a pattern that matched a directory applies to
everything inside it. So `!config` after `**/*.pem` uploads `config/` but
not `config/server.pem`. An excluded directory is walked only when a `!`
pattern could re-include something inside it; an unreadable excluded
directory is skipped. As with `docker build`, a pattern with a syntax error
(`[a-`) fails the build up front, and one that cannot be compiled into a
matcher (`foo\q`) fails it only when a path reaches it (exit 2, naming the
ignore file). The Dockerfile and the ignore file are always uploaded,
even when a pattern matches them. The 1 GiB context limit counts what is
uploaded. Before any request, a directory context must contain the selected
Dockerfile (`Dockerfile` by default, or `--dockerfile` inside the context).
Plain tar contexts are checked for that entry too; compressed or unrecognized
archives are checked by the server. A `--context` tar file is uploaded as is,
and `sandbox cp` never filters. `image create --ref` refuses empty or blank
references locally.

`daytona image logs <id-or-name>` accepts an image name or id. Without
`--follow` it reads at most 64 MiB of logs; `--follow` streams larger ones. Image build/import
failures include the recorded build error and its code when available. A timeout
on `image create`, `build`, `capture` or `snapshot create` includes the pending
resource id and its `get` command; the operation keeps running on the server.
`image list --platform` accepts only `true`, `false` or `any`.

`sandbox exec <id> -- COMMAND [ARGS...]` copies stdout and stderr as raw bytes,
including binary output. `--timeout` only stops the CLI waiting; the process
continues. `--execution-timeout DURATION` sets the remote execution deadline
(greater than zero, rounded up to milliseconds). Interrupting the CLI with
Ctrl-C or SIGTERM instead forwards INT to the process, escalating to KILL
after one second, best effort. `--tty` requires terminal stdin and stdout,
replays output from startup, defaults an unavailable or zero terminal size to
80×24, and forwards terminal resizes on Unix.

`sandbox ssh [CLI FLAGS] <id> [SSH OPTIONS] [-- COMMAND [ARGS...]]` applies CLI
flags only before the sandbox id; arguments after it are forwarded to OpenSSH.
Gateway host keys are kept per host and port under the CLI configuration
directory, never in your personal `known_hosts`. New keys are accepted without
a prompt; subsequent key changes are refused. This is trust on first use:
an attacker intercepting the first connection could supply the trusted key.
SSH options you forward after the sandbox id (for example
`-o StrictHostKeyChecking=no` or `-o UserKnownHostsFile=…`) override this
policy.

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
daytona reservation renew RESERVATION_ID --idempotency-key renew-RESERVATION_ID-2 --yes
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

Every `sandbox create*` sends an `Idempotency-Key` too, as the SDKs do:
`--idempotency-key` (or the `--file` body's `idempotency_key`), else one
derived from `--name` (the same key the SDKs derive, so creating the same name
again within a regional API answers with the sandbox the first create made),
else a fresh one per invocation. When a create fails without an answer (a
network error, a 5xx, a timeout), the error's hint names the key to retry with.
A named create also stamps the name as the label `name`, which the SDKs'
`find_by_name` matches; a `--label name=…` that disagrees with `--name` is a
usage error, and so is a blank `--name` (omit it for a generated name).
`sandbox create-macos` checks its sizes (`--cores` at least 4, `--memory` at
least 4096, `--disk` at least 81920), `--auto-renew` (only with `--reservation
new`) and its volumes (at most 8, read-write, not with `--persistent`) before
asking to buy anything.

`sandbox create-macos` with no `--reservation`, or with `--reservation new`,
may buy a 24-hour Mac reservation billed to your organization. These commands,
`reservation create`, `reservation renew`, and `reservation update
--auto-renew=true` ask for confirmation at a
terminal; off a terminal, pass `--yes` or set `DAYTONA_YES=1`. Using an
existing reservation ID or `reservation create --dry-run` does not prompt.
`reservation update --auto-renew=false` needs no confirmation.

Stop and pause need a persistent sandbox (`--persistent`). Stop keeps the disk on the same Mac, and start and resume use that Mac. Delete erases the disk. At reservation end, a persistent sandbox's disk is saved as an anonymous image before the Mac is erased, and an ephemeral sandbox's disk is erased unsaved; a lost Mac saves persistent disks if it comes back within 7 days. If a Mac restarts before it is declared lost, a running persistent sandbox comes back stopped with its disk, a running ephemeral sandbox is lost, and stopped and paused sandboxes are unchanged. Create a sandbox from an image to continue on another Mac.

## Command reference

`daytona docs --full` prints every command, flag and default. The tree:

| Group | Commands |
|---|---|
| `login`, `logout`, `whoami` | sign in (`--device`, `--browser`, `--token`), sign out, show the credential and the selected organization and project |
| `profile` | `list`, `use`, `delete` (`login --profile NAME` creates one) |
| `config` | `get`, `set`, `path` (the only setting is `confirm`) |
| `sandbox` | `create`, `create-from-snapshot`, `create-macos`, `list`, `search`, `get`, `wait`, `start`, `stop`, `pause`, `resume`, `fork`, `delete`, `update`, `logs`, `usage`, `exec`, `ssh`, `cp` |
| `sandbox port` | `list`, `preview-url` (and `preview-url expire`) |
| `sandbox process` | `list`, `get`, `wait`, `signal`, `kill`, `logs`, `attach` |
| `sandbox docker` | `status` |
| `image` | `create`, `build`, `capture`, `promote`, `list`, `get`, `delete`, `logs` |
| `snapshot` | `create`, `list`, `get`, `delete` |
| `volume` | `create`, `list`, `get`, `delete` |
| `reservation` | `create`, `get`, `list`, `renew`, `update` |
| `region`, `cpu-type`, `mac-model` | `list` |
| `org` | `list`, `get`, `limits`, `use`, `create`, `update`, `delete`, `leave` |
| `project` | `list`, `get`, `use`, `create`, `update`, `delete`; `budget get`, `budget set`, `budget remove`; `member list`, `member add`, `member remove`; `service-account list`, `service-account add`, `service-account remove` |
| `member` | `list`, `update`, `remove` |
| `invite` | `create`, `list`, `revoke`, `get`, `accept`, `decline` |
| `role` | `list` |
| `key`, `org-key` | `mint`, `list`, `revoke` |
| `service-account` | `create`, `list`, `get`, `update`, `delete` |
| `secret` | `set`, `list`, `get`, `delete` |
| `registry` | `set`, `list`, `get`, `delete` |
| `billing` | `wallet`, `balances`, `usage`, `usage-periods`, `payment-methods`, `charges`, `invoices`, `invoice pdf`, `invoice payment-session`, `top-up`, `portal-session`, `payment-method-session`, `automatic-top-up get/set/delete`, `balance-notification get/set/delete`, `coupon redeem`, `plan get/list/set`, `contact get/set`, `profile` |
| `audit` | `list` |
| `version`, `docs`, `completion` | the CLI's and the API's versions, the reference, shell completion scripts |

Flags every command takes: `--api-url`, `--api-key`, `--profile`, `--org`,
`--project`, `-o/--output`, `-y/--yes`, `-q/--quiet`, `--debug`.
`--file/-f`, `--no-wait`, `--timeout` and `--region` exist only on the
commands that use them; any other command rejects them as unknown flags
(exit 2). `--region` is on `sandbox create*`, `sandbox list`, `sandbox
search`, `image list`, `image create`, `image build`, `reservation create`,
`cpu-type list` and `mac-model list`. A renamed flag's old spelling is
refused with a hint that names the new one.

## Scripts and agents

When stdout is not a terminal, or with `--output json` or `yaml`, the CLI
never prompts:

- The default output is a table at a terminal and JSON otherwise;
  `--output` always wins. Payload goes to stdout, diagnostics to stderr.
  Format names are case-sensitive: `table`, `json`, `yaml`, and `yml` (an
  alias for `yaml`). Other spellings are refused (exit 2).
- Destructive and billable commands (`delete`, `remove`, `revoke`, `leave`,
  billing setting deletes, `billing plan set`, `billing automatic-top-up
  set`, Mac purchases, `reservation renew`, `reservation update
  --auto-renew=true`) prompt at a
  terminal; in non-interactive mode (no terminal, `-o json|yaml`, or `CI`
  set) they refuse unless `--yes` or `DAYTONA_YES=1` is given, before
  anything reaches the server. `daytona config set confirm false`
  turns the terminal prompts off; it never consents off a terminal.
- Every list returns one page and prints the list envelope. `--limit` (1 to
  200, default 100; the billing lists have their own: `billing balances`,
  `charges` and `invoices` 1 to 100, default 25; `billing payment-methods` 1
  to 100, default 20; `billing usage-periods` 1 to 60, default 12),
  `--cursor <token>` and `--all` page through it. `sandbox list` and `image list` query each distinct API
  base URL in the region catalog once for fresh listings and regional cursors,
  unless `--region` narrows them. With `--region R` they ask every API that
  serves R or a region inside it and print only the items in R or a region
  inside it (`region_id` for sandboxes; `region_id` or `region_ids` for
  images); the list API has no region filter and regions can share an API,
  so a page can then hold fewer than `--limit` items while the cursor still
  advances over each API's page. Saved cursors resume
  unfinished pages.
  Results merge regional cursors and de-duplicate resources by ID. The
  catalogs (`region`, `cpu-type`, `mac-model`, `role`, `billing plan`) and the
  in-sandbox lists (`sandbox port list`, `sandbox process list`) are returned
  whole and take no paging flags. An unknown `--region` on `sandbox list` or
  `image list` is a usage error (exit 2) before any list request. When some
  regions fail, the list prints the others' items with `"incomplete": true`
  and one `errors` entry per failed region, and exits **7**; when every region
  fails, the command fails with the first region's exit code, `code`,
  `source` and `trace_id` (for example exit 3 for a refused credential),
  names every region's failure in the message, and prints no list.
- Sandbox search time filters use half-open ranges: `--created-start-at` /
  `--created-end-at`, `--updated-start-at` / `--updated-end-at`, and
  `--last-activity-start-at` / `--last-activity-end-at`. Start bounds are
  inclusive; end bounds are exclusive. Values are RFC3339 timestamps.
- Sandbox search accepts repeatable `--label KEY=VALUE`, merged with
  `--labels '{"key":"value"}'`; conflicting values across the two flags
  are a usage error (exit 2). `--region` is checked against the region
  catalog: an unknown region is a usage error (exit 2), and a parent expands
  to itself and its children in the search API's `region_ids` filter.
- Billing amounts are integer cents plus a currency. One-time top-ups use
  `--amount-cents`; automatic top-up and low-balance settings use
  `--threshold-cents` (and automatic top-up also uses `--target-cents`).
- `secret set` takes the value from `--value`, `--from-file` or `--stdin`
  (one trailing newline is stripped from a file or stdin). An empty value is
  refused: the stored value cannot be read back, so an unset variable or an
  empty file would otherwise erase it silently. `--allowed-domain` rejects
  empty entries, including in comma-separated lists. Omit it to preserve an
  existing scope; use `--allowed-domain '*'` to remove domain restrictions
  (the value can then reach any verified HTTPS host). An empty scope is not
  supported by the API; delete the secret to remove it entirely.
- Repeatable list flags accumulate: `--role a --role b,c` is `a,b,c` (also
  `--permission`, `--allow-domain`, …). `--label` and `--env` take one
  `KEY=VALUE` each and keep commas inside the value; repeat them for more.
- Long operations wait for completion by default; `--no-wait` returns the
  accepted resource immediately. `sandbox create*`, `start`, `stop`, `pause`,
  `resume`, `fork` and `delete` print the sandbox as `sandbox get` does in both
  modes. With `--no-wait` the sandbox is read once right after the request.
  If a delete has already removed the sandbox, the API's `{"id", "status"}` is
  printed instead; if a create's read fails, `{"id", "name", "status"}` from the
  create's answer is printed with a warning on stderr. No command
  prints a sandbox access token.
- `sandbox wait --for` takes a settled status: `running`, `stopped`, `paused`
  or `deleted`, or the SDK state names `started` and `destroyed`; anything else
  is a usage error (exit 2) before any request. `sandbox start` on a running
  sandbox changes nothing, and on a paused one resumes it. `--timeout` bounds the CLI's own wait (default
  `10m`; the server keeps working) and exits **8** when it passes. A
  `--timeout` with nothing to wait for is refused (exit 2): with `--no-wait`,
  on `image logs` / `sandbox process logs` without `--follow`, and on
  `sandbox docker status` without `--wait`.
- Without `--type`, `daytona volume create NAME` lets the server choose the
  volume's type. Bucket volumes are created with `daytona volume create NAME --type bucket`; add
  `--bucket`, `--endpoint`, `--bucket-region`, `--prefix`, `--path-style`,
  `--access-key-secret`, and `--secret-key-secret` for a customer bucket.
  `--size-gib` sizes a disk volume and must be greater than zero.
- `audit list --start-at` and `--end-at` require RFC3339 timestamps and reject
  invalid values locally, before any request.
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

### Durations

Every duration flag takes a Go duration such as `90s`, `30m` or `1h30m`, in
whole seconds where the API stores seconds:

| Flag | Commands | Meaning |
|---|---|---|
| `--timeout` | commands that wait (`sandbox create*`, `start`, `stop`, `pause`, `resume`, `fork`, `delete`, `wait`, `exec`, `process wait`, `process logs --follow`, `docker status --wait`, `snapshot create`, `image create`, `build`, `capture`, `logs --follow`) | how long the CLI waits; never sent to the server |
| `--execution-timeout` | `sandbox exec` | server-side execution deadline, greater than zero and rounded up to milliseconds; omitted means no execution deadline |
| `--lifecycle-timeout` / `--on-lifecycle-timeout ACTION` | `sandbox create*`, `sandbox update` | the sandbox's wall-clock lifetime and the action when it ends |
| `--idle-timeout` / `--on-idle-timeout ACTION` | `sandbox create*`, `sandbox update` | idle time before the action, at least `1m` (the API's minimum); `sandbox update --idle-timeout off` clears it |
| `--server-wait` | `sandbox create*` | how long the server holds the create response (`0s` answers at once; server default `3s`); the CLI still waits for `running` unless `--no-wait`, which neither `--server-wait` nor a `--file` `wait_seconds` combines with |
| `--create-ttl` | `sandbox create*` | how long the sandbox may take to reach `running` before the server deletes it; `max` asks for the server's maximum (server default `1h`) |
| `--ttl` | `sandbox port preview-url --signed` | lifetime of a signed preview URL (default `60s`) |
| `--escalate-after` | `sandbox process signal` | when to send `--escalate-to` |
| `--expires` | `key mint`, `org-key mint` | key lifetime in whole seconds, or an RFC3339 timestamp |

Lifecycle actions follow the sandbox: `delete` on any sandbox, `stop` only on
a persistent macOS sandbox. Pause a sandbox with `daytona sandbox pause`; a
lifecycle policy never pauses. A refused action is a usage error before any
request.

### JSON output

- A command that shows an API resource prints the JSON the API returned, and
  a list prints each item as the API returned it: the API's field names
  (snake_case), its nulls, and fields a newer API adds, without a CLI
  release. A field the API leaves out is absent; the CLI never fills one in.
- Output that comes from inside the sandbox (`sandbox process list`, `get`,
  `wait`, `logs`, `sandbox docker status`) is the toolbox API's JSON with every
  key converted to snake_case (`exit_code`, `next_cursor`, `process_id`,
  `truncated_head`) and signal names without `SIG`.
- A list prints `{"items": [...], "incomplete": false}` plus `next_cursor` when
  another page exists and `errors` (`[{"region", "status", "code", "source",
  "trace_id", "message"}]`, absent fields omitted) when a region failed; `items` is
  always an array. `sandbox search` asks one API and prints
  `{"items": [...], "total": N, "total_relation": "eq" | "gte"}` plus
  `next_cursor` when another page exists (no `incomplete`). `cpu-type list` and `mac-model list` print the same
  envelope with or without `--region` (with `--region`, `mac-model list` items
  are the region's catalog entries: `model`, `available`, `price`).
- `--follow` with `-o json` streams NDJSON, one object per line:
  `{"data": "..."}` chunks for `sandbox logs` and `image logs`, one toolbox log
  frame (`channel`, `cursor`, `data`, `timestamp` in Unix milliseconds) for
  `sandbox process logs`. YAML is refused with `--follow` (exit 2).
  With `sandbox process logs --encoding base64`, JSON/YAML frame data is
  kept as the server sent it: standard base64 or base64url, with or without
  padding. Decoders must accept both alphabets and padding forms. Table output
  decodes frames; stdin sent by the CLI uses the contract's base64url alphabet.
- The CLI's own shapes, for output no single API response holds:
  `{"logs": "<text>"}` (`sandbox logs` and `image logs` without `--follow`);
  `whoami` (`profile`, `api_url`, `credential_source`, `context_resolved`,
  and `org_id`, `project_id`, `org_ref`, `project_ref`, `user`, `org`,
  `api_key` when known); `version` (`version`, `go`, `os`, `arch`, and `api_url`,
  `api_version`, `api_build_sha`, `api_built_at`, `skew_warning` when known);
  device-login events (`verification_uri`, `verification_uri_complete`,
  `user_code`, `expires_in`, only when known), one JSON object on stdout
  before waiting for approval with JSON/YAML output or non-terminal stdout
  (even with `-o table`); `profile list` rows (`credential` omitted when
  empty); `config get` / `config path`; `reservation create
  --dry-run`; `region list` items, which are the API's region objects with
  `price_multiplier` added; and acknowledgements (below). They omit what they
  do not know rather than print empty values.
- A command with no resource to print acknowledges with the identifier under
  the field that names it plus a boolean named after the action, e.g.
  `{"ref": "img1", "deleted": true}`:
  - `ref`: `image`, `snapshot`, `volume delete` (`deleted`): they take an id
    or a name, send it as given in one request, and repeat it (`volume
    delete` prints the API's operation when it returns one);
  - `id`: `org`, `project delete` (`deleted`), `key` /
    `org-key` / `invite revoke` (`revoked`), `service-account delete`
    (`archived`), `project service-account remove` (`removed`), `org leave`
    (`left`);
  - `user_id`: `member remove`, `project member remove` (`removed`);
  - `name`: `secret delete` (`deleted`);
  - `host`: `registry delete` (`deleted`);
  - `project_id`: `project budget remove` (`removed`);
  - `org_id`: `billing automatic-top-up delete`, `billing
    balance-notification delete` (`deleted`).
  `invite decline` prints `{"declined": true}` (the token is not echoed),
  `sandbox port preview-url expire` `{"sandbox_id", "port", "revoked": true}`
  (`port` a number; the token is not echoed, and a blank one is refused), and `sandbox process signal` / `kill` `{"id", "signal"}`.
  With table output `org delete`, `project delete` and `project budget
  remove` print a sentence on stderr instead.
- Raw streams are never JSON: the output of `sandbox exec`, `ssh`,
  `process attach` and `cp`, `--follow` in table mode, `billing invoice pdf`,
  `docs`, `completion`, `help` and `--version`.
- The local configuration commands (`login`, `logout`, `org use`, `project
  use`, `profile use`, `profile delete`, `config set`) print nothing on
  stdout, except the device-login event described above. A successful login
  prints no additional result on stdout.
- `secret set` and `registry set` print the secret or registry credential
  the API stored, like `secret get` / `registry get`. They read the stored
  resource first so omitted metadata keeps its values: they need read as well
  as write access. On `registry set`, an omitted username keeps the existing
  username; a new registry requires `--username`. Every set of a `basic`,
  `ghcr`, `gcr` or `ecr` registry needs `--password-stdin` or `--password`, even
  a description-only update, because the API cannot preserve an omitted
  write-only password; an `ecr_role` registry takes no password. `--kind` must be `basic`, `ghcr`, `gcr`, `ecr` or
  `ecr_role`; omission on update keeps the stored kind.
- The one exception to printing the API's JSON as it came: `secret get`,
  `set` and `list` drop a top-level `value` field, and `registry get`, `set`
  and `list` a top-level `password` field, matched in any case, in every
  output format. These are the fields that carry the credential in
  `secret set` and `registry set`, so the CLI does not print them back even
  when a response returns them.
- With JSON or YAML output (including the default off a terminal) an error
  is one JSON line on stderr:
  `{"error": {"exit_code": 4, "code": "FORBIDDEN", "message": "…", "source": "DAYTONA_API", "trace_id": "…", "hint": "…"}}`.
  `code`, `source` and `trace_id` come from the server when it sent them and
  are omitted otherwise, so a server error is never attributed to the CLI; a
  local error has `source` `CLI`, and a passed-through exit status `PROCESS`
  or `SSH`. A partial list (exit 7) has no `code` or `source` of its own: each
  failed region's `status`, `code`, `source` and `trace_id` are in the list's
  `errors`, and the message names each region with its status and code. Both `code` and `source` are
  open vocabularies. With table output errors are an `error:` line and an
  optional `hint:` line.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | failure: a network error, HTTP 5xx (except 501 and 504), HTTP 429 other than `LIMIT_EXCEEDED`, anything not listed below |
| `2` | usage: wrong number of arguments, unknown command, flag or help topic, an invalid flag value or `--file` body, an invalid API URL, a missing local file, a saved credential used with another API URL, a `--project` other than a project API key's own, a missing `--yes` or selection off a terminal; HTTP 400 and 422 |
| `3` | authentication: no credential, or HTTP 401 |
| `4` | permission denied: HTTP 403 |
| `5` | not found: HTTP 404, or a `--profile` / `DAYTONA_PROFILE`, or the configuration's `active_profile`, that names a profile that does not exist (on the commands that use the selected profile) |
| `6` | conflict: HTTP 409, 412 and 501, and HTTP 429 `LIMIT_EXCEEDED` (an organization limit) |
| `7` | partial result: `sandbox list` / `image list` reached only some regions; the others' items are printed with each failure in `errors` (when no region answers, the first region's failure decides the code) |
| `8` | timed out: `--timeout` passed, a request timed out, or HTTP 408 / 504 |
| `130` | interrupted (Ctrl-C), or a confirmation prompt answered no |
| `143` | terminated (SIGTERM) |

Commands that run something in the sandbox pass its status through, as a
shell does, so any number, including those above, can also come from the
remote side. With JSON or YAML output such an exit still prints the error
object on stderr, whose `source` (`PROCESS` or `SSH`) tells a passed-through
status apart from the CLI's own:

| Command | Exit status |
|---|---|
| `sandbox exec`, `process wait`, `process logs --follow`, `process attach` | the process's exit code; for `sandbox exec`, a command that cannot be started → `127`; a process ended by a signal → `128` + its number for `HUP`, `INT`, `QUIT`, `ILL`, `TRAP`, `ABRT`, `FPE`, `KILL`, `SEGV`, `PIPE`, `ALRM`, `TERM`, `XCPU`, `XFSZ`, `VTALRM`, `PROF` and `WINCH`, whose numbers are the same on Linux and macOS (`SIGINT` → `130`, `SIGKILL` → `137`, `SIGTERM` → `143`); any other signal → `1`; its own timeout → `8`; `lost` / `failed` → its code, else `1` |
| `sandbox ssh` | the `ssh` client's exit status |
| `sandbox cp` | the remote `tar`'s exit status when the in-sandbox side of the copy fails |

### Request bodies (`--file`)

`--file PATH` (or `--file -` for stdin) takes one JSON object; explicit flags
override its fields. Keys name the API's request fields exactly, at any
depth, as the API does: an unknown key, including one that differs from a
field only in case (`Name`, `CORES_MAX`), is refused (exit 2). Keys of map
values such as `env` and `labels` are names of your own and are sent as
given. A field whose value is one of several shapes (`image create`'s
`source`) is checked by its `type`: an unknown `type`, or a key the chosen
shape does not have, is refused too. The bodies use the API's request
fields:

| Command | Body |
|---|---|
| `sandbox create` | `image`, `name`, `cpu`, `memory_mib`, `disk_mib`, `cores_max`, `memory_max_mib`, `disk_max_mib`, `cpu_arch`, `cpu_type`, `user`, `env`, `labels`, `ports`, `volumes`, `secrets`, `spot`, `network_policy`, `lifecycle_policy`, `region_id`, `wait_seconds`, `create_timeout_seconds`, `idempotency_key` |
| `sandbox create-from-snapshot` | `snapshot` (the positional argument wins), `name`, `labels`, `ports`, `lifecycle_policy`, `region_id`, `wait_seconds`, `create_timeout_seconds`, `idempotency_key` |
| `sandbox create-macos` | `image`, `name`, `cores`, `memory_mib`, `disk_mib`, `persistent`, `reservation`, `auto_renew`, `env`, `labels`, `ports`, `volumes`, `secrets`, `network_policy`, `lifecycle_policy`, `region_id`, `wait_seconds`, `create_timeout_seconds`, `idempotency_key` |
| `sandbox update` | `{"labels": {...}, "lifecycle_policy": {...}}` only: `labels` replaces the label set except the label `name`, which is kept unless the new set names it (then it must equal the sandbox name; `--clear-labels` keeps it too), `lifecycle_policy` takes `timeout_seconds`, `on_timeout`, `idle_expiry_seconds` (`-1` clears), `on_idle_timeout`. The CLI sends them as two requests (labels first) |
| `snapshot create` | `name` |
| `image create` | `name` (the positional argument wins), `source` (`{"type": "registry", "ref": "..."}` or `{"type": "named", "name": "..."}`), `region_id`, `cpu_arch`, `idle_expiry_seconds`; a `dockerfile` field is refused (use `image build`) |
| `image build` | the CLI's own descriptor, all strings: `name` (the positional argument wins), `context` (a local directory or tar file), `dockerfile` (a path inside the context), `region_id`, `cpu_arch`; `--dockerfile` and `--context` win even when given empty (then refused) |
| `volume create` | `name` (the positional argument wins), `type`, `size_gib`, `bucket` (`name`, `origin`, `endpoint`, `region`, `prefix`, `path_style`, `credentials`) |
| `reservation create` | `region_id`, `model`, `auto_renew`; with `--count` the bulk request, which adds `count` and `min_count`. `--count` selects the bulk request; a `count` field alone is rejected |

`lifecycle_policy` durations and `wait_seconds` / `create_timeout_seconds` are
integer seconds, as the API stores them.

## Credentials

The CLI sends whatever credential you give it and lets the server decide what
it may do. Precedence: `--api-key` > `DAYTONA_API_KEY` > the active profile.
An explicit credential is the only one used for that command — the CLI never
falls back to your saved login, and an empty `--api-key` is a usage error.
A static credential (`--api-key`, `DAYTONA_API_KEY`, `login --token`) is sent
as-is on every request: the CLI never mints, caches or substitutes another key
for it. A project API key always acts in its own project, so a `--project` /
`DAYTONA_PROJECT` that names another project is refused (exit 2) on every
command that acts in a project (sandbox, image, snapshot, volume, secret,
registry, reservation, region and catalog commands) and on `whoami`, instead
of being ignored; the CLI reads the key's project once per command
(`GET /api-keys/current`) when a project is named.

A saved credential (a login or `login --token`) belongs to the API URL it was
saved with. With `--api-url` or `DAYTONA_V2_API_URL` naming another API, the
CLI refuses to send it there, or to refresh or exchange it there (exit 2):
pass `--api-key` / `DAYTONA_API_KEY` for that API, or sign in to it with
`daytona login --api-url URL --profile NAME`.

After a browser or device `daytona login`, sandbox, image, secret, and registry commands
use automatically refreshed, project-scoped credentials. Platform tokens expire
after five minutes; automatically minted project API keys expire after 24 hours.
The CLI uses project API keys for `exec`, `cp`, `logs`, `port` and `ssh`, and as
a fallback when platform tokens are unavailable. Automatically minted keys stay
on Daytona V2. A replacement attempts to revoke its superseded key.

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
Keys no longer tracked after replacement with `login --token` are not revoked
by logout. `daytona profile delete` performs the same revocation for the
deleted profile.

Cached platform tokens and minted keys are sent only to the API that issued
them, and to the toolbox and build-log endpoints that API names for a sandbox
or image, which can be on other (regional) hosts.

`daytona whoami` reports `org_id` and `project_id` only when they are resolved
IDs; a selection given by name appears in `org_ref` / `project_ref`, and
`context_resolved` is `false` when a name could not be resolved. With a static
credential it asks the API about the key: a refused key fails with exit 3, and
a project API key reports its own organization and project, with the API's
description of the key (no secret) in `api_key`.

## Environment

| Variable | Meaning |
|---|---|
| `DAYTONA_API_KEY` | credential, below `--api-key`; blank counts as unset |
| `DAYTONA_PROFILE` | profile, below `--profile`; it must exist (`profile`, `config`, `version`, `help`, `docs` and completion still run when it does not) |
| `DAYTONA_ORG`, `DAYTONA_PROJECT` | organization and project for this call (ID or name), below `--org` / `--project` |
| `DAYTONA_YES` | `1` or `true` confirms destructive and billable commands, like `--yes` |
| `DAYTONA_V2_API_URL` | another API, for test environments only; below `--api-url`, above the profile's saved URL |
| `DAYTONA_CONFIG_DIR` | configuration directory (default `$XDG_CONFIG_HOME/daytona`, else `~/.config/daytona`) |
| `DAYTONA_OIDC_CLIENT_ID` | the OIDC client a browser or device login uses |
| `CI` | any value makes the CLI non-interactive |
| `NO_COLOR`, `TERM=dumb` | no colour |
| `SSH_CONNECTION`, `SSH_TTY`, `SSH_CLIENT` | any of them makes `login` use the device flow instead of the browser (`--browser` overrides) |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | the HTTP proxy for API requests (Go's standard handling) |

The CLI does not read `DAYTONA_API_URL` (a value set for the 0.x CLI does not
redirect it) or `DAYTONA_REGION` (pass `--region`). A login saves the API URL
it used in its profile, and the CLI keeps using that URL when neither
`--api-url` nor `DAYTONA_V2_API_URL` is set, and its saved credential is never
sent to another URL (see Credentials). To use the default Daytona API, log in
again without a test URL, or use another profile.

Configuration lives in `~/.config/daytona/` (`$XDG_CONFIG_HOME/daytona`), in
files readable only by you.

## Develop

```sh
go build ./... && go test ./...
golangci-lint run ./...
./hack/gen.sh      # regenerate wire types from ../contract/*.json
./hack/build.sh    # six release binaries + SHA256SUMS into dist/
```

Wire types are generated from the public `contract/` OpenAPI artifacts and
must not be edited by hand.
