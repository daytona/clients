/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The release-channel half of sdk_publish.yaml: the gate's derivation, the tap
// check, the CLI release and the Homebrew update, each run from the workflow
// text against fake gh / curl binaries (no network, nothing published).

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '../..');
const workflow = readFileSync(join(repoRoot, '.github/workflows/sdk_publish.yaml'), 'utf8');

// The run script of the step whose `name:` line is exactly `name`.
function step(name) {
  const block = workflow.split(new RegExp(`\\n {6}(?:- )? {0,2}name: ${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\n`))[1]?.split('\n      - ')[0];
  assert.ok(block, `missing step ${name}`);
  const lines = block.split('        run: |\n')[1].split('\n');
  const end = lines.findIndex((line) => line.trim() !== '' && !line.startsWith(' '.repeat(10)));
  return lines.slice(0, end === -1 ? undefined : end).map((line) => line.slice(10)).join('\n');
}

function fixture(context) {
  const root = mkdtempSync(join(tmpdir(), 'publish-channels-'));
  // A tool on the host may still write into a fixture's .git while it is
  // removed; a leftover temporary directory is not a test failure.
  context.after(() => {
    try {
      rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    } catch (error) {
      if (error.code !== 'ENOTEMPTY') throw error;
    }
  });
  mkdirSync(join(root, 'bin'));
  return root;
}

function executable(path, body) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `#!/bin/bash\nset -euo pipefail\n${body}\n`);
  chmodSync(path, 0o755);
}

function run(root, script, env) {
  return spawnSync('bash', ['-c', script], {
    cwd: root, encoding: 'utf8',
    env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`, GITHUB_REPOSITORY: 'daytona/clients', ...env },
  });
}

const outputs = (file) => Object.fromEntries(readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map((line) => line.split(/=(.*)/s).slice(0, 2)));

// ── gate: versions and channel ────────────────────────────────────────────────

function gateFixture(context, stableReleases) {
  const root = fixture(context);
  const git = (...args) => {
    const result = spawnSync('git', ['-C', root, ...args], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout.trim();
  };
  git('init', '-q', '-b', 'main');
  git('-c', 'user.email=a@b', '-c', 'user.name=x', 'commit', '-q', '--allow-empty', '-m', 'release');
  git('remote', 'add', 'origin', root);
  mkdirSync(join(root, '.publish-tools/scripts'), { recursive: true });
  for (const script of ['sdk-version.sh', 'release-channel.sh']) {
    writeFileSync(join(root, '.publish-tools/scripts', script), readFileSync(join(repoRoot, 'scripts', script)));
    chmodSync(join(root, '.publish-tools/scripts', script), 0o755);
  }
  // gh release list: the stable releases, or a failure when null.
  executable(join(root, 'bin/gh'), stableReleases === null ? 'exit 1' : `printf '%b' ${JSON.stringify(stableReleases.map((t) => `${t}\n`).join(''))}`);
  writeFileSync(join(root, 'output'), '');
  return { root, sha: git('rev-parse', 'HEAD') };
}

const gateScript = step('Derive registry versions and the release channel (branch rule + gate)');

for (const [version, expected] of [
  ['v2.0.0-alpha.9', { channel: 'alpha', prerelease: 'true', npm_dist_tag: 'alpha', homebrew_formula: 'daytona-alpha', homebrew_event: 'update-version-alpha', homebrew_workflow: 'brew_publish_alpha.yaml', pypi: '2.0.0a9' }],
  ['v2.0.0-rc.2', { channel: 'rc', prerelease: 'true', npm_dist_tag: 'next', homebrew_formula: 'daytona-rc', homebrew_event: 'update-version-rc', homebrew_workflow: 'brew_publish_rc.yaml', pypi: '2.0.0rc2' }],
  ['v2.0.0', { channel: 'stable', prerelease: 'false', npm_dist_tag: 'latest', homebrew_formula: 'daytona', homebrew_event: 'update-version', homebrew_workflow: 'brew_publish.yaml', pypi: '2.0.0' }],
]) {
  test(`gate derives the ${expected.channel} channel from ${version}`, (context) => {
    // Given: a source commit on main.
    const { root, sha } = gateFixture(context, []);
    // When
    const result = run(root, gateScript, { VERSION_INPUT: version, SOURCE_SHA: sha, GITHUB_OUTPUT: join(root, 'output'),
      PUBLISH_PYPI: 'true', PUBLISH_NPM: 'true', PUBLISH_CLI: 'true', PUBLISH_UPGRADE: 'true' });
    // Then: every channel output reaches the later jobs.
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const out = outputs(join(root, 'output'));
    for (const [key, value] of Object.entries(expected)) assert.equal(out[key], value, key);
  });
}

for (const version of ['v2.0.0-beta.1', 'v0.232.0', 'v2.0.0-dev.1']) {
  test(`gate refuses ${version}`, (context) => {
    const { root, sha } = gateFixture(context, []);
    const result = run(root, gateScript, { VERSION_INPUT: version, SOURCE_SHA: sha, GITHUB_OUTPUT: join(root, 'output'),
      PUBLISH_PYPI: 'true', PUBLISH_NPM: 'true', PUBLISH_CLI: 'true', PUBLISH_UPGRADE: 'true' });
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.equal(readFileSync(join(root, 'output'), 'utf8'), '');
  });
}

for (const [title, releases, publishCli, status] of [
  ['the first stable release without the CLI', ['v0.231.0', 'v0.230.0'], 'false', 1],
  ['the first stable release with the CLI', ['v0.231.0'], 'true', 0],
  ['a later stable release without the CLI', ['v0.231.0', 'v2.0.0'], 'false', 0],
  ['a stable release when the releases cannot be read', null, 'false', 1],
]) {
  test(`gate: ${title}`, (context) => {
    // Given: the repository's stable GitHub Releases.
    const { root, sha } = gateFixture(context, releases);
    // When: an SDK-only (or full) stable run reaches the gate.
    const result = run(root, gateScript, { VERSION_INPUT: 'v2.0.1', SOURCE_SHA: sha, GITHUB_OUTPUT: join(root, 'output'),
      PUBLISH_PYPI: 'true', PUBLISH_NPM: 'true', PUBLISH_CLI: publishCli, PUBLISH_UPGRADE: 'true' });
    // Then: the handover of the default channels from 0.x needs the CLI release.
    assert.equal(result.status, status, result.stdout + result.stderr);
    if (status === 1 && releases) assert.match(result.stdout, /must publish the CLI too/);
  });
}

// ── gate: the Homebrew tap takes this channel ────────────────────────────────

const tapWorkflows = {
  good: [
    'name: Update brew with new versions',
    'run-name: Update brew daytona to ${{ github.event.client_payload.version }}',
    'on:', '  repository_dispatch:', '    types: [update-version]',
    'jobs:', '  update-brew:', '    steps:', '      - run: bash scripts/update_sha.sh "$VERSION" daytona.rb',
  ].join('\n'),
  // The tap's stable workflow before it named its runs.
  untitled: [
    'name: Update brew with new versions',
    'on:', '  repository_dispatch:', '    types: [update-version]',
    'jobs:', '  update-brew:', '    steps:', '      - run: bash scripts/update_sha.sh $version',
    '        with:', '          file_pattern: "daytona.rb"',
  ].join('\n'),
  // The version is in the title but not at its end: the publish job would
  // never find the run.
  versionNotLast: [
    'run-name: Update ${{ github.event.client_payload.version }} brew daytona',
    'on:', '  repository_dispatch:', '    types: [update-version]', '      - run: bash scripts/update_sha.sh "$VERSION" daytona.rb',
  ].join('\n'),
  quotedRunName: [
    "run-name: 'Update brew daytona to ${{ github.event.client_payload.version }}'",
    'on:', '  repository_dispatch:', '    types: [update-version]', '      - run: bash scripts/update_sha.sh "$VERSION" daytona.rb',
  ].join('\n'),
  wrongEvent: [
    'run-name: Update brew to ${{ github.event.client_payload.version }}',
    'on:', '  repository_dispatch:', '    types: [update-version-alpha]', '      - run: bash scripts/update_sha.sh "$VERSION" daytona.rb',
  ].join('\n'),
};

for (const [title, files, status, message] of [
  ['a tap that takes the channel', { 'daytona.rb': 'class Daytona', '.github/workflows/brew_publish.yaml': tapWorkflows.good }, 0],
  ['a tap workflow without a run-name', { 'daytona.rb': 'class Daytona', '.github/workflows/brew_publish.yaml': tapWorkflows.untitled }, 1, /lacks a run-name/],
  ['a run-name with the version not at its end', { 'daytona.rb': 'class Daytona', '.github/workflows/brew_publish.yaml': tapWorkflows.versionNotLast }, 1, /lacks a run-name/],
  ['a quoted run-name ending in the version', { 'daytona.rb': 'class Daytona', '.github/workflows/brew_publish.yaml': tapWorkflows.quotedRunName }, 0],
  ['a tap workflow on another event', { 'daytona.rb': 'class Daytona', '.github/workflows/brew_publish.yaml': tapWorkflows.wrongEvent }, 1, /lacks a repository_dispatch trigger on update-version/],
  ['a tap without the formula', { '.github/workflows/brew_publish.yaml': tapWorkflows.good }, 1, /has no daytona.rb formula/],
  ['a tap without the workflow', { 'daytona.rb': 'class Daytona' }, 1, /has no .github\/workflows\/brew_publish.yaml/],
]) {
  test(`tap check: ${title}`, (context) => {
    // Given: the tap's files, served by the contents API.
    const root = fixture(context);
    for (const [path, content] of Object.entries(files)) {
      mkdirSync(dirname(join(root, 'tap', path)), { recursive: true });
      writeFileSync(join(root, 'tap', path), content);
    }
    executable(join(root, 'bin/gh'), [
      'path="${2#repos/daytonaio/homebrew-cli/contents/}"',
      '[ -f "$TAP/$path" ] || exit 1',
      'base64 -w0 "$TAP/$path"',
    ].join('\n'));
    // When
    const result = run(root, step('Check the Homebrew tap takes this channel'), {
      TAP: join(root, 'tap'), FORMULA: 'daytona', EVENT: 'update-version', TAP_WORKFLOW: 'brew_publish.yaml' });
    // Then
    assert.equal(result.status, status, result.stdout + result.stderr);
    if (message) assert.match(result.stdout, message);
  });
}

// ── publish: the CLI's GitHub Release ────────────────────────────────────────

// A gh that knows which releases exist (and of which kind) and logs every
// call. `release list` honors --exclude-drafts / --exclude-pre-releases, and
// fails when `releases` is null.
function releaseGh(root, { existing = null, stable = [], releases = stable.map((tag) => `${tag} stable`) } = {}) {
  writeFileSync(join(root, 'releases'), releases === null ? '' : releases.map((r) => `${r}\n`).join(''));
  executable(join(root, 'bin/gh'), [
    'echo "$*" >> "$GH_LOG"',
    'case "$1 $2" in',
    `  "release view") ${existing ? `if [[ "$*" == *--json* ]]; then echo ${existing}; fi; exit 0` : 'exit 1'} ;;`,
    `  "release list") ${releases === null ? 'echo "HTTP 502" >&2; exit 1' : 'while read -r tag kind; do'
      + ' if [[ "$kind" == draft && "$*" == *--exclude-drafts* ]] || [[ "$kind" == prerelease && "$*" == *--exclude-pre-releases* ]]; then continue; fi;'
      + ' echo "$tag"; done < "$ROOT/releases"'} ;;`,
    '  "release create"|"release edit") ;;',
    '  *) echo "unexpected gh $*" >&2; exit 1 ;;',
    'esac',
  ].join('\n'));
}

function cliFixture(context) {
  const root = fixture(context);
  mkdirSync(join(root, 'cli-dist'));
  writeFileSync(join(root, 'cli-dist/daytona-linux-amd64'), 'bin');
  writeFileSync(join(root, 'cli-dist/SHA256SUMS'), 'sums');
  return root;
}

const cliRelease = step('Create (or confirm) the daytona CLI ${{ needs.gate.outputs.cli }} release');
const channelEnv = {
  alpha: { CHANNEL: 'alpha', PRERELEASE: 'true', FORMULA: 'daytona-alpha' },
  rc: { CHANNEL: 'rc', PRERELEASE: 'true', FORMULA: 'daytona-rc' },
  stable: { CHANNEL: 'stable', PRERELEASE: 'false', FORMULA: 'daytona' },
};

for (const [title, version, channel, stable, flags, notFlags] of [
  ['an alpha is a pre-release, never Latest', '2.1.0-alpha.1', 'alpha', ['v2.0.0'], ['--prerelease', '--latest=false'], []],
  ['an rc is a pre-release, never Latest', '2.1.0-rc.2', 'rc', ['v2.0.0'], ['--prerelease', '--latest=false'], []],
  ['the first stable release becomes Latest over 0.x', '2.0.0', 'stable', ['v0.231.0', 'v0.230.0'], ['--latest=true'], ['--prerelease']],
  ['a backport below the highest stable release does not become Latest', '2.0.1', 'stable', ['v2.1.0', 'v2.0.0', 'v0.240.0'], ['--latest=false'], ['--prerelease']],
]) {
  test(`CLI release: ${title}`, (context) => {
    // Given: no release under the tag yet, and the repository's stable releases.
    const root = cliFixture(context);
    releaseGh(root, { stable });
    // When
    const result = run(root, cliRelease, { CLI_VERSION: version, SOURCE_SHA: 'abc', ROOT: root, GH_LOG: join(root, 'gh.log'), ...channelEnv[channel] });
    // Then: the release is created with the channel's kind and notes.
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const create = readFileSync(join(root, 'gh.log'), 'utf8').split('\n').find((line) => line.startsWith('release create'));
    assert.ok(create, 'no release created');
    for (const flag of flags) assert.ok(create.split(' ').includes(flag), `${flag} missing: ${create}`);
    for (const flag of notFlags) assert.ok(!create.split(' ').includes(flag), `${flag} present: ${create}`);
    assert.match(create, new RegExp(`brew install daytonaio/cli/${channelEnv[channel].FORMULA}\``));
  });
}

for (const [channel, kind] of [['stable', 'prerelease'], ['alpha', 'stable'], ['rc', 'draft']]) {
  test(`CLI release: refuses to resume a ${kind} for a ${channel} version`, (context) => {
    // Given: a release under the tag that this channel would not have created.
    const root = cliFixture(context);
    releaseGh(root, { existing: kind });
    // When
    const result = run(root, cliRelease, { CLI_VERSION: '2.0.0', SOURCE_SHA: 'abc', ROOT: root, GH_LOG: join(root, 'gh.log'), ...channelEnv[channel] });
    // Then: nothing is uploaded and Homebrew is not reached.
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stdout, new RegExp(`is a ${kind}, not the`));
    assert.doesNotMatch(readFileSync(join(root, 'gh.log'), 'utf8'), /release (create|upload|edit)/);
  });
}

for (const [title, releases, latest] of [
  ['a higher draft and pre-release do not take Latest from a stable release', ['v2.2.0 draft', 'v2.1.0 prerelease', 'v2.0.0 stable', 'v0.231.0 stable'], 'true'],
  ['a higher stable release keeps Latest', ['v2.2.0 draft', 'v2.1.0 stable', 'v2.0.0 stable'], 'false'],
]) {
  test(`CLI release: ${title}`, (context) => {
    // Given: releases of every kind under the tags around this one.
    const root = cliFixture(context);
    releaseGh(root, { releases });
    // When
    const result = run(root, cliRelease, { CLI_VERSION: '2.0.1', SOURCE_SHA: 'abc', ROOT: root, GH_LOG: join(root, 'gh.log'), ...channelEnv.stable });
    // Then: only published stable releases decide.
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const log = readFileSync(join(root, 'gh.log'), 'utf8');
    assert.match(log, /release list .*--exclude-drafts --exclude-pre-releases/);
    assert.match(log.split('\n').find((line) => line.startsWith('release create')), new RegExp(` --latest=${latest} `));
  });
}

test('CLI release: a stable release is not created when the releases cannot be listed', (context) => {
  const root = cliFixture(context);
  releaseGh(root, { releases: null });
  const result = run(root, cliRelease, { CLI_VERSION: '2.0.0', SOURCE_SHA: 'abc', ROOT: root, GH_LOG: join(root, 'gh.log'), ...channelEnv.stable });
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stdout, /could not list the releases/);
  assert.doesNotMatch(readFileSync(join(root, 'gh.log'), 'utf8'), /release (create|upload|edit)/);
});

test('CLI release: an unknown channel fails before anything is published', (context) => {
  const root = cliFixture(context);
  releaseGh(root);
  const result = run(root, cliRelease, { CLI_VERSION: '2.0.0', SOURCE_SHA: 'abc', GH_LOG: join(root, 'gh.log'), CHANNEL: 'beta', PRERELEASE: 'true', FORMULA: 'daytona-beta' });
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.ok(!existsSync(join(root, 'gh.log')));
});

// ── publish: the Homebrew formula ────────────────────────────────────────────

// A gh for the Homebrew step: release/v0 publish runs in progress (one count
// per call, the last repeating), the tap's runs, and the formula's contents.
// formulaVersion: the formula's version, or a list of the versions successive
// reads see (the last one repeats). watchExit: the tap run's result.
// runsReadable: whether the token can list the tap's runs.
function homebrewGh(root, { v0Running = ['0'], formulaVersion, watchExit = 0, runsReadable = true }) {
  writeFileSync(join(root, 'v0-running'), v0Running.join('\n'));
  writeFileSync(join(root, 'formula-versions'), [].concat(formulaVersion).join('\n'));
  executable(join(root, 'bin/formula'), [
    'v=$(head -n 1 "$ROOT/formula-versions")',
    'if [ "$(wc -l < "$ROOT/formula-versions")" -gt 0 ]; then sed -i 1d "$ROOT/formula-versions"; fi',
    'printf \'class Daytona < Formula\\n  desc "Daytona CLI"\\n  version "%s"\\nend\\n\' "$v" | base64 -w0',
  ].join('\n'));
  // A clock that moves 100 s per read, so deadlines pass without waiting.
  writeFileSync(join(root, 'clock'), '0');
  executable(join(root, 'bin/date'), 'if [ "${1:-}" = +%s ]; then c=$(cat "$ROOT/clock"); echo $(( c + 100 )) > "$ROOT/clock"; echo "$c"; else /bin/date "$@"; fi');
  executable(join(root, 'bin/gh'), [
    'echo "$*" >> "$GH_LOG"',
    'if [[ "$*" == "run list --repo daytona/clients"* ]]; then',
    '  head -n 1 "$ROOT/v0-running"; if [ "$(wc -l < "$ROOT/v0-running")" -gt 0 ]; then sed -i 1d "$ROOT/v0-running"; fi; exit 0',
    'fi',
    'case "$*" in',
    '  "run list --repo daytonaio/homebrew-cli"*--json\\ createdAt*) echo 2026-01-01T00:00:00Z ;;',
    '  "run list --repo daytonaio/homebrew-cli"*--json*) echo 42 ;;',
    `  "run list --repo daytonaio/homebrew-cli"*) ${runsReadable ? '' : 'echo "HTTP 403" >&2; exit 1'} ;;`,
    '  "api repos/daytonaio/homebrew-cli/dispatches"*) ;;',
    `  "run watch 42"*) exit ${watchExit} ;;`,
    '  "api repos/daytonaio/homebrew-cli/contents/"*) formula ;;',
    '  *) echo "unexpected gh $*" >&2; exit 1 ;;',
    'esac',
  ].join('\n'));
  executable(join(root, 'bin/sleep'), 'exit 0');
}

const homebrewStep = step('Point the ${{ needs.gate.outputs.homebrew_formula }} Homebrew formula at the release');
const homebrewEnv = {
  stable: { CHANNEL: 'stable', FORMULA: 'daytona', EVENT: 'update-version', TAP_WORKFLOW: 'brew_publish.yaml' },
  alpha: { CHANNEL: 'alpha', FORMULA: 'daytona-alpha', EVENT: 'update-version-alpha', TAP_WORKFLOW: 'brew_publish_alpha.yaml' },
};

for (const [title, channel, options, status, waits, message] of [
  ['stable waits for running release/v0 publishes, then dispatches', 'stable', { v0Running: ['1', '1', '0'], formulaVersion: '2.0.0' }, 0, 2],
  ['stable fails when the formula was moved after the update', 'stable', { formulaVersion: '0.232.0' }, 1, 0, /is at '0\.232\.0', not 2\.0\.0/],
  ['an alpha does not wait for release/v0', 'alpha', { v0Running: ['1'], formulaVersion: '2.0.0' }, 0, 0],
  ['a failed tap run fails the step', 'alpha', { formulaVersion: '2.0.0', watchExit: 1 }, 1, 0],
  ['unreadable tap runs: the formula naming the version passes', 'alpha', { formulaVersion: ['2.0.0-alpha.8', '2.0.0-alpha.8', '2.0.0'], runsReadable: false }, 0, 0, /waiting up to 15 minutes/],
  ['unreadable tap runs: a formula that never moves fails', 'alpha', { formulaVersion: '2.0.0-alpha.8', runsReadable: false }, 1, 0, /is at '2\.0\.0-alpha\.8', not 2\.0\.0/],
]) {
  test(`Homebrew: ${title}`, (context) => {
    // Given
    const root = fixture(context);
    homebrewGh(root, options);
    // When
    const result = run(root, homebrewStep, { CLI_VERSION: '2.0.0', ROOT: root, GH_LOG: join(root, 'gh.log'), REPO_TOKEN: 'read-token', ...homebrewEnv[channel] });
    // Then: the dispatch names the channel's event, after release/v0 settled,
    // and the step passes only once the formula names the version.
    assert.equal(result.status, status, result.stdout + result.stderr);
    const log = readFileSync(join(root, 'gh.log'), 'utf8').split('\n');
    const dispatch = log.findIndex((line) => line.startsWith('api repos/daytonaio/homebrew-cli/dispatches'));
    assert.ok(dispatch >= 0, 'no dispatch');
    assert.match(log[dispatch], new RegExp(`event_type=${homebrewEnv[channel].EVENT} `));
    assert.match(log[dispatch], /client_payload\[version\]=v2\.0\.0$/);
    const v0Checks = log.slice(0, dispatch).filter((line) => line.startsWith('run list --repo daytona/clients')).length;
    assert.equal(v0Checks, channel === 'stable' ? waits + 1 : 0);
    if (message) assert.match(result.stdout, message);
  });
}

// ── registry-check: a stable version never moves npm `latest` back ───────────

for (const [title, answer, version, status] of [
  ['above a 0.x latest', '200 {"latest":"0.231.0","alpha":"2.2.0-alpha.1"}', '2.0.0', 0],
  ['above a lower 2.x latest', '200 {"latest":"2.0.0"}', '2.0.1', 0],
  ['below the current latest', '200 {"latest":"2.1.0","alpha":"2.2.0-alpha.1"}', '2.0.1', 1],
  ['below the current latest, spaced JSON', '200 {"latest": "2.1.0"}', '2.0.1', 1],
  ['equal to the current latest (a resume)', '200 {"latest":"2.0.0"}', '2.0.0', 0],
  ['of a package without a latest tag', '200 {"alpha":"2.0.0-alpha.8"}', '2.0.0', 0],
  ['of a package not on npm yet', '404 {}', '2.0.0', 0],
  ['when the registry cannot answer', '503 -', '2.0.0', 1],
]) {
  test(`registry check: a stable version ${title}`, (context) => {
    // Given: npm answers the dist-tags request with a status and a body.
    const root = fixture(context);
    const [code, body] = [answer.slice(0, 3), answer.slice(4)];
    executable(join(root, 'bin/curl'), [
      'out=""; url="${*: -1}"',
      'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac; done',
      `case "$url" in *dist-tags) printf '%s' '${body}' > "$out"; printf ${code} ;; *) printf 404 ;; esac`,
    ].join('\n'));
    // When
    const result = run(root, step('Refuse a version the registry already has (unless force_republish)'), {
      NPM_VERSION: version, PYPI_VERSION: '', CLI_VERSION: '', UPGRADE_NPM: version, UPGRADE_PYPI: '',
      NPM_DIST_TAG: 'latest', FORCE: 'true', GH_TOKEN: '' });
    // Then: even force_republish never moves `latest` to a lower version.
    assert.equal(result.status, status, result.stdout + result.stderr);
    if (status === 1 && code === '200') assert.match(result.stdout, /is below the package's npm latest/);
    if (code === '503') assert.match(result.stdout, /could not read the npm dist-tags of @daytona\/sdk \(HTTP 503\)/);
  });
}

test('registry check: a pre-release never reads the latest dist-tag', (context) => {
  const root = fixture(context);
  executable(join(root, 'bin/curl'), 'url="${*: -1}"; case "$url" in *dist-tags) exit 7 ;; *) printf 404 ;; esac');
  const result = run(root, step('Refuse a version the registry already has (unless force_republish)'), {
    NPM_VERSION: '2.2.0-alpha.1', PYPI_VERSION: '', CLI_VERSION: '', UPGRADE_NPM: '', UPGRADE_PYPI: '',
    NPM_DIST_TAG: 'alpha', FORCE: 'false', GH_TOKEN: '' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

// ── The legacy package names never publish from this line ────────────────────

test('the 2.x workflow never names the legacy packages', () => {
  // The 0.x line publishes the PyPI alias and the npm mirror; their trusted
  // publishers name this workflow file too, so this line must never build them.
  for (const name of ['daytona-sdk', 'daytona_sdk', '@daytonaio/']) assert.ok(!workflow.includes(name), name);
});

test('every upload step checks the name of what it uploads', () => {
  // Given: each step of the publish job.
  const steps = workflow.slice(workflow.indexOf('\n  publish:\n')).split('\n      - ').slice(1);
  const npm = steps.filter((s) => s.includes('npm publish "./$tgz"'));
  const pypi = steps.filter((s) => s.includes('pypa/gh-action-pypi-publish'));
  // Then: both npm publishes read the tarball's package name first, and every
  // PyPI upload follows a step that admits exactly its own distributions.
  assert.equal(npm.length, 2);
  for (const s of npm) assert.match(s, /manifest\.name !== "@daytona\/(sdk|upgrade)"/);
  assert.equal(pypi.length, 2);
  for (const s of pypi) {
    const dir = s.match(/packages-dir: (\S+)/)[1];
    const check = steps[steps.indexOf(s) - 1];
    assert.match(check, /are exactly the/);
    assert.ok(check.includes(`find ${dir.replace(/\/$/, '')} `), `${dir} is not the checked directory`);
  }
});

for (const [kind, legacy] of [['PyPI', 'daytona_sdk-2.0.0-py3-none-any.whl'], ['npm', '@daytonaio/sdk']]) {
  test(`refuses a legacy ${kind} artifact under the SDK's upload`, (context) => {
    // Given: an artifact carrying the legacy name at this run's version.
    const root = fixture(context);
    let script; let env;
    if (kind === 'PyPI') {
      mkdirSync(join(root, 'pypi-dist'));
      writeFileSync(join(root, 'pypi-dist', legacy), '');
      writeFileSync(join(root, 'pypi-dist', 'daytona_sdk-2.0.0.tar.gz'), '');
      script = step("The PyPI distributions are exactly the SDK's");
      env = { PYPI_VERSION: '2.0.0' };
    } else {
      mkdirSync(join(root, 'pack/package'), { recursive: true });
      writeFileSync(join(root, 'pack/package/package.json'), JSON.stringify({ name: legacy, version: '2.0.0' }));
      assert.equal(spawnSync('tar', ['-czf', 'sdk.tgz', 'package'], { cwd: join(root, 'pack') }).status, 0);
      rmSync(join(root, 'pack/package'), { recursive: true });
      executable(join(root, 'bin/npm'), 'echo published > "$PUBLISH_LOG"');
      script = step('Publish @daytona/sdk ${{ needs.gate.outputs.npm }} to npm (OIDC)');
      env = { NPM_DIST_TAG: 'latest', NPM_VERSION: '2.0.0', RESUME: 'false', PUBLISH_LOG: join(root, 'published') };
    }
    // When
    const result = run(root, script, env);
    // Then: nothing is uploaded.
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.ok(!existsSync(join(root, 'published')));
  });
}

// ── publish: npm `latest` stays on the highest stable version ────────────────

// The job-level NPM_LATEST_GUARD script.
const latestGuard = (() => {
  const lines = workflow.split('      NPM_LATEST_GUARD: |\n')[1].split('\n');
  const end = lines.findIndex((line) => line.trim() !== '' && !line.startsWith(' '.repeat(8)));
  return lines.slice(0, end).map((line) => line.slice(8)).join('\n');
})();

// A registry whose successive answers for the package are `answers`
// ("<status> <versions,…> <latest>"; the last one repeats).
function packumentCurl(root, answers) {
  writeFileSync(join(root, 'answers'), answers.map((a) => `${a}\n`).join(''));
  executable(join(root, 'bin/curl'), [
    'out=""; while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac; done',
    'read -r code versions latest < <(head -n 1 "$ROOT/answers")',
    'if [ "$(wc -l < "$ROOT/answers")" -gt 1 ]; then sed -i 1d "$ROOT/answers"; fi',
    'node -e \'const [v, l] = process.argv.slice(1); console.log(JSON.stringify({ "dist-tags": { latest: l }, versions: Object.fromEntries(v.split(",").filter(Boolean).map((x) => [x, {}])) }))\' "${versions:-}" "${latest:-}" > "$out"',
    'printf %s "$code"',
  ].join('\n'));
  executable(join(root, 'bin/sleep'), 'exit 0');
  writeFileSync(join(root, 'clock'), '0');
  executable(join(root, 'bin/date'), 'if [ "${1:-}" = +%s ]; then c=$(cat "$ROOT/clock"); echo $(( c + 60 )) > "$ROOT/clock"; echo "$c"; else /bin/date "$@"; fi');
}

for (const [title, mode, version, answers, status, message] of [
  ['check: above every stable version', 'check', '2.0.0', ['200 0.231.0,2.0.0-alpha.8,2.0.0-rc.1 0.231.0'], 0],
  ['check: a stable version published since the build', 'check', '2.0.1', ['200 2.0.0,2.1.0 2.1.0'], 1, /@daytona\/sdk@2\.1\.0 is already published/],
  ['check: a higher pre-release does not block', 'check', '2.0.1', ['200 2.0.0,2.1.0-alpha.1 2.0.0'], 0],
  ['check: the same version (a resume)', 'check', '2.0.0', ['200 2.0.0 2.0.0'], 0],
  ['check: a package not on npm yet', 'check', '2.0.0', ['404'], 0, /not on npm yet/],
  ['check: the registry cannot answer', 'check', '2.0.0', ['503'], 1, /HTTP 503/],
  ['verify: latest is the highest stable version', 'verify', '2.0.0', ['200 0.231.0,2.0.0 2.0.0'], 0],
  ['verify: waits until the version is visible', 'verify', '2.0.0', ['503', '200 0.231.0 0.231.0', '200 0.231.0,2.0.0 2.0.0'], 0],
  ['verify: latest moved back by another publish', 'verify', '2.0.1', ['200 2.0.0,2.0.1,2.1.0 2.0.1'], 1, /npm dist-tag add @daytona\/sdk@2\.1\.0 latest/],
  ['verify: the version never appears', 'verify', '2.0.0', ['200 0.231.0 0.231.0'], 1, /not visible on the registry yet/],
]) {
  test(`npm latest guard ${title}`, (context) => {
    // Given: what the registry answers.
    const root = fixture(context);
    packumentCurl(root, answers);
    // When
    const result = spawnSync('bash', ['-c', latestGuard, 'guard', mode, '@daytona/sdk', version], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`, ROOT: root, RUNNER_TEMP: root } });
    // Then
    assert.equal(result.status, status, result.stdout + result.stderr);
    if (message) assert.match(result.stdout, message);
  });
}

test('every npm publish under latest is guarded before and verified after', () => {
  // Given: each step of the publish job that publishes to npm.
  const steps = workflow.slice(workflow.indexOf('\n  publish:\n')).split('\n      - ').slice(1).filter((s) => s.includes('npm publish "./$tgz"'));
  assert.equal(steps.length, 2);
  for (const [s, pkg] of steps.map((s) => [s, s.includes('@daytona%2fupgrade') ? '@daytona/upgrade' : '@daytona/sdk'])) {
    // Then: check right before the publish, verify right after it, and verify
    // when a resume finds the version already published.
    const lines = s.split('\n').map((line) => line.trim());
    const at = lines.findIndex((line) => line.startsWith('npm publish "./$tgz"'));
    assert.equal(lines[at - 1], `if [ "$NPM_DIST_TAG" = latest ]; then bash -c "$NPM_LATEST_GUARD" guard check ${pkg} "$NPM_VERSION"; fi`);
    assert.equal(lines[at + 1], `if [ "$NPM_DIST_TAG" = latest ]; then bash -c "$NPM_LATEST_GUARD" guard verify ${pkg} "$NPM_VERSION"; fi`);
    assert.ok(lines.some((line, i) => line.includes('is already on npm — skipping') && lines[i + 1].includes(`guard verify ${pkg}`)), `resume of ${pkg} not verified`);
  }
});
