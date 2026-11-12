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

// tags: git tags on an earlier commit (the tags an SDK-only run records).
function gateFixture(context, stableReleases, tags = []) {
  const root = fixture(context);
  const git = (...args) => {
    const result = spawnSync('git', ['-C', root, ...args], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout.trim();
  };
  git('init', '-q', '-b', 'main');
  if (tags.length) {
    git('-c', 'user.email=a@b', '-c', 'user.name=x', 'commit', '-q', '--allow-empty', '-m', 'earlier');
    for (const tag of tags) git('tag', tag);
  }
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

for (const version of ['v2.0.0-beta.1', 'v0.232.0', 'v2.0.0-dev.1', 'v2.0.0-rc.1', '2.0.0-rc.1']) {
  test(`gate refuses ${version}`, (context) => {
    const { root, sha } = gateFixture(context, []);
    const result = run(root, gateScript, { VERSION_INPUT: version, SOURCE_SHA: sha, GITHUB_OUTPUT: join(root, 'output'),
      PUBLISH_PYPI: 'true', PUBLISH_NPM: 'true', PUBLISH_CLI: 'true', PUBLISH_UPGRADE: 'true' });
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.equal(readFileSync(join(root, 'output'), 'utf8'), '');
  });
}

for (const [title, releases, publishCli, status, message = /must publish the CLI too/] of [
  ['the first stable release without the CLI', ['v0.231.0', 'v0.230.0'], 'false', 1],
  ['the first stable release with the CLI', ['v0.231.0'], 'true', 0],
  ['a stable 2.x release without the CLI when only a later major is stable', ['v0.231.0', 'v3.0.0'], 'false', 1],
  ['a stable version below the highest stable 2.x release', ['v0.240.0', 'v2.1.0', 'v2.0.0'], 'true', 1, /below the highest stable 2\.x release v2\.1\.0/],
  ['a stable version below the highest, numeric order', ['v2.0.10'], 'true', 1, /below the highest stable 2\.x release v2\.0\.10/],
  ['an SDK-only stable version below the highest stable 2.x release', ['v2.1.0', 'v2.0.0'], 'false', 1, /below the highest stable 2\.x release v2\.1\.0/],
  ['the highest stable 2.x release itself (a resume)', ['v2.0.1', 'v2.0.0'], 'true', 0],
  ['a stable version above the highest stable 2.x release', ['v2.0.0', 'v0.240.0'], 'false', 0],
  ['a stable version above a higher 0.x release', ['v0.300.0', 'v2.0.0'], 'true', 0],
  ['a later stable release without the CLI', ['v0.231.0', 'v2.0.0'], 'false', 0],
  ['a stable release when the releases cannot be read', null, 'false', 1],
]) {
  test(`gate: ${title}`, (context) => {
    // Given: the repository's stable GitHub Releases.
    const { root, sha } = gateFixture(context, releases);
    // When: an SDK-only (or full) stable run reaches the gate.
    const result = run(root, gateScript, { VERSION_INPUT: 'v2.0.1', SOURCE_SHA: sha, GITHUB_OUTPUT: join(root, 'output'),
      PUBLISH_PYPI: 'true', PUBLISH_NPM: 'true', PUBLISH_CLI: publishCli, PUBLISH_UPGRADE: 'true' });
    // Then: the handover of the default channels from 0.x needs the CLI
    // release, and no stable version below the highest stable 2.x publishes.
    assert.equal(result.status, status, result.stdout + result.stderr);
    if (status === 1 && releases) assert.match(result.stdout, message);
  });
}

for (const [title, tags, version, status] of [
  ['a stable version below an SDK-only stable release (a tag, no GitHub Release)', ['v2.1.0'], 'v2.0.5', 1],
  ['a stable version above an SDK-only stable release', ['v2.1.0'], 'v2.1.1', 0],
  ['pre-release tags do not count', ['v2.2.0-alpha.1', 'v2.1.0-rc.1'], 'v2.0.5', 0],
]) {
  test(`gate: ${title}`, (context) => {
    // Given: a stable 2.x GitHub Release, and tags an SDK-only run recorded.
    const { root, sha } = gateFixture(context, ['v2.0.0'], tags);
    // When
    const result = run(root, gateScript, { VERSION_INPUT: version, SOURCE_SHA: sha, GITHUB_OUTPUT: join(root, 'output'),
      PUBLISH_PYPI: 'true', PUBLISH_NPM: 'true', PUBLISH_CLI: 'true', PUBLISH_UPGRADE: 'true' });
    // Then
    assert.equal(result.status, status, result.stdout + result.stderr);
    if (status === 1) assert.match(result.stdout, /below the highest stable 2\.x release v2\.1\.0/);
  });
}

test('gate: an SDK-only stable tag does not stand in for the first stable 2.x CLI release', (context) => {
  // No stable 2.x GitHub Release, only a tag: an SDK-only run is still refused.
  const { root, sha } = gateFixture(context, ['v0.231.0'], ['v2.0.0']);
  const result = run(root, gateScript, { VERSION_INPUT: 'v2.0.1', SOURCE_SHA: sha, GITHUB_OUTPUT: join(root, 'output'),
    PUBLISH_PYPI: 'true', PUBLISH_NPM: 'true', PUBLISH_CLI: 'false', PUBLISH_UPGRADE: 'true' });
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stdout, /must publish the CLI too/);
});

test('gate: no input skips the stable-backport refusal', () => {
  // The refusal runs in every mode: the gate step reads no force_republish
  // (or any other) input that could turn it off.
  const env = workflow.split('        name: Derive registry versions and the release channel (branch rule + gate)\n')[1].split('        run: |')[0];
  assert.doesNotMatch(env, /force_republish|FORCE/);
});

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
    assert.ok(create.includes(`brew install daytonaio/cli/${channelEnv[channel].FORMULA}\``), create);
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
    assert.ok(result.stdout.includes(`is a ${kind}, not the`), result.stdout);
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
    assert.ok(log.split('\n').find((line) => line.startsWith('release create')).includes(` --latest=${latest} `));
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

// An existing release under the tag: its assets live in $ROOT/published.
function resumeGh(root, { kind = 'prerelease', stable = [] } = {}) {
  writeFileSync(join(root, 'stable'), stable.map((t) => `${t}\n`).join(''));
  executable(join(root, 'bin/gh'), [
    'echo "$*" >> "$GH_LOG"',
    'pub="$ROOT/published"',
    'case "$*" in',
    `  "release view "*"--json isPrerelease,isDraft"*) echo ${kind} ;;`,
    '  "release view "*"--json assets"*) ls "$pub" ;;',
    '  "release view "*) ;;',
    '  "release list "*) cat "$ROOT/stable" ;;',
    '  "release download "*"--pattern SHA256SUMS --output "*) [ -f "$pub/SHA256SUMS" ] || exit 1; cp "$pub/SHA256SUMS" "${*: -1}" ;;',
    '  "release download "*"--dir "*) mkdir -p "${*: -1}"; cp "$pub"/* "${*: -1}/" ;;',
    '  "release upload "*) shift 3; for f in "$@"; do case "$f" in --*|daytona/clients) ;; *) cp "$f" "$pub/" ;; esac; done ;;',
    '  "release edit "*) ;;',
    '  *) echo "unexpected gh $*" >&2; exit 1 ;;',
    'esac',
  ].join('\n'));
}

// cli-dist with real checksums, and the release as published so far.
function resumeFixture(context, publish) {
  const root = fixture(context);
  mkdirSync(join(root, 'cli-dist'));
  writeFileSync(join(root, 'cli-dist/daytona-linux-amd64'), 'linux binary');
  writeFileSync(join(root, 'cli-dist/install.sh'), 'installer');
  const sums = spawnSync('sha256sum', ['daytona-linux-amd64', 'install.sh'], { cwd: join(root, 'cli-dist'), encoding: 'utf8' });
  writeFileSync(join(root, 'cli-dist/SHA256SUMS'), sums.stdout);
  mkdirSync(join(root, 'published'));
  publish(root);
  return root;
}

const copyAsset = (root, name) => writeFileSync(join(root, 'published', name), readFileSync(join(root, 'cli-dist', name)));

for (const [title, publish, options, status, expect] of [
  ['a complete identical release resumes without uploading', (root) => ['daytona-linux-amd64', 'install.sh', 'SHA256SUMS'].forEach((a) => copyAsset(root, a)), {}, 0,
    { uploads: [], message: /verified against this build's checksums/ }],
  ['identical checksums with a missing asset uploads only that asset', (root) => ['daytona-linux-amd64', 'SHA256SUMS'].forEach((a) => copyAsset(root, a)), {}, 0,
    { uploads: ['release upload v2.0.0-rc.2 cli-dist/install.sh --repo daytona/clients'] }],
  ['a release without SHA256SUMS gets every asset again', (root) => copyAsset(root, 'install.sh'), {}, 0,
    { uploads: ['release upload v2.0.0-rc.2 cli-dist/SHA256SUMS cli-dist/daytona-linux-amd64 cli-dist/install.sh --repo daytona/clients --clobber'] }],
  ['different checksums fail without uploading', (root) => { writeFileSync(join(root, 'published/SHA256SUMS'), 'other'); }, {}, 1,
    { uploads: [], message: /already exists with DIFFERENT content/ }],
  ['a published asset that does not match the checksums fails', (root) => { ['install.sh', 'SHA256SUMS'].forEach((a) => copyAsset(root, a)); writeFileSync(join(root, 'published/daytona-linux-amd64'), 'tampered'); }, {}, 1,
    { uploads: [] }],
]) {
  test(`CLI release resume: ${title}`, (context) => {
    // Given: a pre-release already under the tag, published as far as `publish` got.
    const root = resumeFixture(context, publish);
    resumeGh(root, options);
    // When
    const result = run(root, cliRelease, { CLI_VERSION: '2.0.0-rc.2', SOURCE_SHA: 'abc', ROOT: root, RUNNER_TEMP: root, GITHUB_WORKSPACE: root, GH_LOG: join(root, 'gh.log'), ...channelEnv.rc });
    // Then
    assert.equal(result.status, status, result.stdout + result.stderr);
    const log = readFileSync(join(root, 'gh.log'), 'utf8').split('\n');
    assert.deepEqual(log.filter((line) => line.startsWith('release upload')), expect.uploads);
    assert.ok(!log.some((line) => line.startsWith('release create')), 'created a second release');
    if (expect.message) assert.match(result.stdout, expect.message);
  });
}

for (const [stable, latest] of [[['v0.231.0'], true], [['v2.1.0', 'v0.240.0'], false]]) {
  test(`CLI release resume: a stable release is ${latest ? '' : 'not '}marked Latest again`, (context) => {
    // Given: the complete stable release, and the repository's other stable releases.
    const root = resumeFixture(context, (r) => ['daytona-linux-amd64', 'install.sh', 'SHA256SUMS'].forEach((a) => copyAsset(r, a)));
    resumeGh(root, { kind: 'stable', stable });
    // When
    const result = run(root, cliRelease, { CLI_VERSION: '2.0.1', SOURCE_SHA: 'abc', ROOT: root, RUNNER_TEMP: root, GITHUB_WORKSPACE: root, GH_LOG: join(root, 'gh.log'), ...channelEnv.stable });
    // Then
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.equal(readFileSync(join(root, 'gh.log'), 'utf8').includes('release edit v2.0.1 --repo daytona/clients --latest'), latest);
  });
}

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
// before: the formula's version when the step starts. formulaVersion: the
// version, or the list of versions, successive reads see after that (the last
// one repeats). watchExit: the tap run's result. runsReadable: whether the
// token can list the tap's runs.
// v0Running / v0ReleaseRunning: the counts of in-progress release/v0
// sdk_publish.yaml / release.yaml runs successive checks see.
// urlRepo: the repository the formula's url lines download from;
// beforeUrlRepo: the one they name before the tap run.
function homebrewGh(root, { v0Running = ['0'], v0ReleaseRunning = ['0'], before = '0.231.0', formulaVersion, watchExit = 0, runsReadable = true, clockStep = 100, urlRepo = 'daytona/clients', beforeUrlRepo = urlRepo }) {
  writeFileSync(join(root, 'v0-running'), v0Running.join('\n'));
  writeFileSync(join(root, 'v0-release-running'), v0ReleaseRunning.join('\n'));
  writeFileSync(join(root, 'formula-versions'), [`${before} ${beforeUrlRepo}`].concat([].concat(formulaVersion ?? []).map((v) => `${v} ${urlRepo}`)).map((l) => `${l}\n`).join(''));
  executable(join(root, 'bin/formula'), [
    'read -r v repo < <(head -n 1 "$ROOT/formula-versions")',
    'if [ "$(wc -l < "$ROOT/formula-versions")" -gt 1 ]; then sed -i 1d "$ROOT/formula-versions"; fi',
    'u="    url \\"https://github.com/${repo}/releases/download/v#{version}/daytona-"',
    'printf \'class Daytona < Formula\\n  desc "Daytona CLI"\\n  version "%s"\\n  if OS.mac?\\n%sdarwin-arm64"\\n  else\\n%slinux-amd64"\\n  end\\nend\\n\' "$v" "$u" "$u" | base64 -w0',
  ].join('\n'));
  // A clock that moves clockStep seconds per read, so deadlines pass without waiting.
  writeFileSync(join(root, 'clock'), '0');
  executable(join(root, 'bin/date'), `if [ "\${1:-}" = +%s ]; then c=$(cat "$ROOT/clock"); echo $(( c + ${clockStep} )) > "$ROOT/clock"; echo "$c"; else /bin/date "$@"; fi`);
  executable(join(root, 'bin/gh'), [
    'echo "$*" >> "$GH_LOG"',
    'if [[ "$*" == "run list --repo daytona/clients"* ]]; then',
    '  q="$ROOT/v0-running"; [[ "$*" != *"--workflow release.yaml "* ]] || q="$ROOT/v0-release-running"',
    '  [[ "$*" == *"--branch release/v0 --status in_progress"* ]] || { echo "unexpected gh $*" >&2; exit 1; }',
    '  head -n 1 "$q"; if [ "$(wc -l < "$q")" -gt 0 ]; then sed -i 1d "$q"; fi; exit 0',
    'fi',
    'case "$*" in',
    // Only the channel's tap workflow and formula are answered.
    '  "run list --repo daytonaio/homebrew-cli --workflow $TAP_WORKFLOW "*--json\\ createdAt*) echo 2026-01-01T00:00:00Z ;;',
    '  "run list --repo daytonaio/homebrew-cli --workflow $TAP_WORKFLOW "*--json*) echo 42 ;;',
    `  "run list --repo daytonaio/homebrew-cli --workflow $TAP_WORKFLOW "*) ${runsReadable ? '' : 'echo "HTTP 403" >&2; exit 1'} ;;`,
    '  "api repos/daytonaio/homebrew-cli/dispatches"*) ;;',
    `  "run watch 42"*) exit ${watchExit} ;;`,
    '  "api repos/daytonaio/homebrew-cli/contents/$FORMULA.rb "*) formula ;;',
    '  *) echo "unexpected gh $*" >&2; exit 1 ;;',
    'esac',
  ].join('\n'));
  executable(join(root, 'bin/sleep'), 'exit 0');
}

const homebrewStep = step('Point the ${{ needs.gate.outputs.homebrew_formula }} Homebrew formula at the release');
const homebrewEnv = {
  stable: { CHANNEL: 'stable', FORMULA: 'daytona', EVENT: 'update-version', TAP_WORKFLOW: 'brew_publish.yaml' },
  rc: { CHANNEL: 'rc', FORMULA: 'daytona-rc', EVENT: 'update-version-rc', TAP_WORKFLOW: 'brew_publish_rc.yaml' },
  alpha: { CHANNEL: 'alpha', FORMULA: 'daytona-alpha', EVENT: 'update-version-alpha', TAP_WORKFLOW: 'brew_publish_alpha.yaml' },
};

for (const [title, channel, options, status, waits, message, version = '2.0.0', dispatched = true] of [
  ['stable waits for running release/v0 publishes, then dispatches', 'stable', { v0Running: ['1', '1', '0'], formulaVersion: '2.0.0' }, 0, 2],
  ['stable waits for running release/v0 releases too', 'stable', { v0ReleaseRunning: ['1', '0'], formulaVersion: '2.0.0' }, 0, 1],
  ['stable fails when the formula was moved after the update', 'stable', { formulaVersion: '0.232.0' }, 1, 0, /is at '0\.232\.0', not 2\.0\.0/],
  ['an alpha does not wait for release/v0', 'alpha', { v0Running: ['1'], before: '2.0.0-alpha.8', formulaVersion: '2.0.0' }, 0, 0],
  ['an rc goes to daytona-rc and does not wait for release/v0', 'rc', { v0Running: ['1'], before: '0.127.0', formulaVersion: '2.0.0' }, 0, 0],
  ['a failed tap run fails the step', 'alpha', { formulaVersion: '2.0.0', watchExit: 1 }, 1, 0],
  ['a formula read that lags the tap run is retried', 'alpha', { formulaVersion: ['2.0.0-alpha.8', '2.0.0'], clockStep: 15 }, 0, 0],
  ['unreadable tap runs: the formula naming the version passes', 'alpha', { formulaVersion: ['2.0.0-alpha.8', '2.0.0-alpha.8', '2.0.0'], runsReadable: false }, 0, 0, /waiting up to 15 minutes/],
  ['unreadable tap runs: a formula that never moves fails', 'alpha', { formulaVersion: '2.0.0-alpha.8', runsReadable: false }, 1, 0, /is at '2\.0\.0-alpha\.8', not 2\.0\.0/],
  // A formula only moves forward.
  ['a stable release below the formula leaves it alone (the gate refuses one first)', 'stable', { before: '2.1.0' }, 0, 0, /daytona\.rb is at 2\.1\.0, above 2\.0\.1/, '2.0.1', false],
  ['a stable release above the formula moves it', 'stable', { before: '2.0.1', formulaVersion: '2.1.0' }, 0, 0, undefined, '2.1.0'],
  ['an older rc leaves a higher rc formula alone', 'rc', { before: '2.1.0-rc.1' }, 0, 0, /above 2\.0\.1-rc\.3/, '2.0.1-rc.3', false],
  ['an rc leaves a formula that names a stable release of that version alone', 'rc', { before: '2.1.0' }, 0, 0, /above 2\.1\.0-rc\.1/, '2.1.0-rc.1', false],
  ['an older alpha leaves a higher alpha formula alone', 'alpha', { before: '2.1.0-alpha.10' }, 0, 0, /above 2\.1\.0-alpha\.9/, '2.1.0-alpha.9', false],
  ['a higher alpha counter moves the formula (numeric, not text, order)', 'alpha', { before: '2.1.0-alpha.9', formulaVersion: '2.1.0-alpha.10' }, 0, 0, undefined, '2.1.0-alpha.10'],
  ['the same version (a resume) is read back, not dispatched', 'stable', { before: '2.0.0', formulaVersion: '2.0.0' }, 0, 0, /already names 2\.0\.0/, '2.0.0', false],
  ['the first 2.x rc: a daytona-rc formula from another repository, rewritten by the tap run, passes', 'rc', { before: '0.127.0', beforeUrlRepo: 'daytonaio/daytona', formulaVersion: '2.0.0' }, 0, 0],
  ['a formula that downloads from another repository fails the read-back', 'rc', { before: '0.127.0', formulaVersion: '2.0.0', urlRepo: 'daytonaio/daytona' }, 1, 0, /does not download it from daytona\/clients's release: +url "https:\/\/github\.com\/daytonaio\/daytona/],
  ['a formula naming an unknown version fails before dispatching', 'stable', { before: '2.0.0-beta.1' }, 1, 0, /not a release version/, '2.0.0', false],
]) {
  test(`Homebrew: ${title}`, (context) => {
    // Given
    const root = fixture(context);
    homebrewGh(root, options);
    // When
    const result = run(root, homebrewStep, { CLI_VERSION: version, ROOT: root, GH_LOG: join(root, 'gh.log'), REPO_TOKEN: 'read-token', ...homebrewEnv[channel] });
    // Then: the dispatch names the channel's event, after release/v0 settled,
    // and the step passes only once the formula names the version; a formula
    // at or above the version is never dispatched to.
    assert.equal(result.status, status, result.stdout + result.stderr);
    const log = readFileSync(join(root, 'gh.log'), 'utf8').split('\n');
    const dispatch = log.findIndex((line) => line.startsWith('api repos/daytonaio/homebrew-cli/dispatches'));
    if (message) assert.match(result.stdout, message);
    const formulaReads = log.filter((line) => line.startsWith(`api repos/daytonaio/homebrew-cli/contents/${homebrewEnv[channel].FORMULA}.rb `)).length;
    if (!dispatched) {
      assert.equal(dispatch, -1, 'dispatched');
      // A resume (same version) reads the formula back after the first read;
      // a higher formula is read once.
      if (status === 0) assert.equal(formulaReads, options.before === version ? 2 : 1);
      return;
    }
    assert.ok(dispatch >= 0, 'no dispatch');
    assert.ok(log[dispatch].includes(`event_type=${homebrewEnv[channel].EVENT} `), log[dispatch]);
    assert.ok(log[dispatch].endsWith(`client_payload[version]=v${version}`), log[dispatch]);
    // Each check asks for both release/v0 workflows.
    const v0Checks = log.slice(0, dispatch).filter((line) => line.startsWith('run list --repo daytona/clients')).length;
    assert.equal(v0Checks, channel === 'stable' ? 2 * (waits + 1) : 0);
    // The formula is read from the channel's own file, before the dispatch and after it.
    assert.ok(log.slice(0, dispatch).some((line) => line.startsWith(`api repos/daytonaio/homebrew-cli/contents/${homebrewEnv[channel].FORMULA}.rb `)), 'formula not read before dispatching');
    if (status === 0) assert.ok(formulaReads >= 2, 'formula not read back');
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
  ['when a 200 transfer did not complete', '200!18 {"alpha":"2.0.0-alpha.8"}', '2.0.1', 1],
  ['when latest is a pre-release of the same version', '200 {"latest":"2.0.0-alpha.8"}', '2.0.0', 0],
  ['when latest is a pre-release of a higher version', '200 {"latest":"2.1.0-rc.1"}', '2.0.1', 0],
  ['above a latest that sorts higher as text', '200 {"latest":"2.0.9"}', '2.0.10', 0],
  ['below a latest that sorts lower as text', '200 {"latest":"2.0.10"}', '2.0.9', 1],
]) {
  test(`registry check: a stable version ${title}`, (context) => {
    // Given: npm answers the dist-tags request with a status and a body.
    const root = fixture(context);
    // "<status>[!<curl exit>] <body>": a curl exit code means the transfer
    // failed after the status line arrived.
    const [head, body] = [answer.slice(0, answer.indexOf(' ')), answer.slice(answer.indexOf(' ') + 1)];
    const [code, curlExit = '0'] = head.split('!');
    executable(join(root, 'bin/curl'), [
      'out=""; url="${*: -1}"',
      'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac; done',
      `case "$url" in *dist-tags) printf '%s' '${body}' > "$out"; printf ${code}; exit ${curlExit} ;; *) printf 404 ;; esac`,
    ].join('\n'));
    // When
    const result = run(root, step('Refuse a version the registry already has (unless force_republish)'), {
      NPM_VERSION: version, PYPI_VERSION: '', CLI_VERSION: '', UPGRADE_NPM: version, UPGRADE_PYPI: '',
      NPM_DIST_TAG: 'latest', FORCE: 'true', GH_TOKEN: '' });
    // Then: even force_republish never moves `latest` to a lower version.
    assert.equal(result.status, status, result.stdout + result.stderr);
    if (status === 1 && code === '200' && curlExit === '0') assert.match(result.stdout, /is below the package's npm latest/);
    if (curlExit !== '0') assert.match(result.stdout, /the transfer failed/);
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
    'read -r head versions latest < <(head -n 1 "$ROOT/answers")',
    'code="${head%%!*}"; curl_exit=0; [ "$head" = "$code" ] || curl_exit="${head#*!}"',
    'if [ "$(wc -l < "$ROOT/answers")" -gt 1 ]; then sed -i 1d "$ROOT/answers"; fi',
    'node -e \'const [v, l] = process.argv.slice(1); console.log(JSON.stringify({ "dist-tags": { latest: l }, versions: Object.fromEntries(v.split(",").filter(Boolean).map((x) => [x, {}])) }))\' "${versions:-}" "${latest:-}" > "$out"',
    'printf %s "$code"; exit "$curl_exit"',
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
  ['check: a 200 transfer that did not complete', 'check', '2.0.1', ['200!18 2.0.0,2.1.0 2.1.0'], 1, /HTTP 000/],
  ['verify: retries a transfer that did not complete', 'verify', '2.0.0', ['200!18 0.231.0,2.0.0 0.231.0', '200 0.231.0,2.0.0 2.0.0'], 0],
  ['verify: latest is the highest stable version', 'verify', '2.0.0', ['200 0.231.0,2.0.0 2.0.0'], 0],
  ['verify: waits until the version is visible', 'verify', '2.0.0', ['503', '200 0.231.0 0.231.0', '200 0.231.0,2.0.0 2.0.0'], 0],
  ['verify: latest moved back by another publish', 'verify', '2.0.1', ['200 2.0.0,2.0.1,2.1.0 2.0.1'], 1, /npm dist-tag add @daytona\/sdk@2\.1\.0 latest/],
  ['verify: the version never appears', 'verify', '2.0.0', ['200 0.231.0 0.231.0'], 1, /not visible on the registry yet/],
  ['verify: the registry never answers', 'verify', '2.0.0', ['503'], 1, /could not read @daytona\/sdk from the npm registry \(last answer: HTTP 503\)/],
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

// ── publish: GitHub's Latest stays on the highest stable release ─────────────

const latestStep = step("Keep GitHub's Latest on the highest stable release");

for (const [title, releases, latest] of [
  ['re-pins Latest to the highest stable release, numerically', ['v2.0.10 stable', 'v2.0.9 stable', 'v0.240.0 stable', 'v2.1.0 prerelease', 'v2.2.0 draft'], 'v2.0.10'],
  ['a later 0.x release does not take Latest from 2.x', ['v0.300.0 stable', 'v2.0.0 stable'], 'v2.0.0'],
]) {
  test(`Latest: ${title}`, (context) => {
    // Given: releases of every kind.
    const root = fixture(context);
    releaseGh(root, { releases });
    // When
    const result = run(root, latestStep, { ROOT: root, GH_LOG: join(root, 'gh.log') });
    // Then: only the highest published stable release is marked Latest.
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const edits = readFileSync(join(root, 'gh.log'), 'utf8').split('\n').filter((line) => line.startsWith('release edit'));
    assert.deepEqual(edits, [`release edit ${latest} --repo daytona/clients --latest`]);
  });
}

test('Latest: fails when the releases cannot be listed', (context) => {
  const root = fixture(context);
  releaseGh(root, { releases: null });
  const result = run(root, latestStep, { ROOT: root, GH_LOG: join(root, 'gh.log') });
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.doesNotMatch(readFileSync(join(root, 'gh.log'), 'utf8'), /release edit/);
});

test('Latest is re-pinned after the Homebrew update, for stable releases only', () => {
  const at = (name) => workflow.indexOf(`      - name: ${name}`);
  assert.ok(at("Keep GitHub's Latest on the highest stable release") > at('Point the ${{ needs.gate.outputs.homebrew_formula }} Homebrew formula at the release'));
  const condition = workflow.split("      - name: Keep GitHub's Latest on the highest stable release\n")[1].split('\n').find((line) => line.trim().startsWith('if:'));
  assert.ok(condition.includes("needs.gate.outputs.channel == 'stable'"), condition);
});

// ── concurrency: every stable version shares one group ───────────────────────

// Evaluates the workflow's concurrency.group expression for a version input.
// It may use only inputs.version, string literals, contains / startsWith /
// format, && and || (GitHub's operators return an operand, like JavaScript's).
function concurrencyGroup(version) {
  const line = workflow.split('\nconcurrency:\n')[1].split('\n').find((l) => l.trimStart().startsWith('group:'));
  const expression = /^\s*group: \$\{\{ (.*) \}\}$/.exec(line)?.[1];
  assert.ok(expression, `unexpected concurrency group line: ${line}`);
  const tokens = expression.replace(/'[^']*'/g, '').replace(/\b(contains|startsWith|format)\(|inputs\.version|&&|\|\||[(),\s]/g, '');
  assert.equal(tokens, '', `unsupported expression: ${expression}`);
  const ctx = {
    v: version,
    contains: (s, x) => s.toLowerCase().includes(x.toLowerCase()),
    startsWith: (s, x) => s.toLowerCase().startsWith(x.toLowerCase()),
    format: (f, ...args) => f.replace(/\{(\d+)\}/g, (_, i) => args[Number(i)]),
  };
  const js = expression.replace(/\b(contains|startsWith|format)\(/g, 'ctx.$1(').replace(/inputs\.version/g, 'ctx.v');
  return new Function('ctx', `return (${js});`)(ctx);
}

test('every stable version shares one concurrency group; pre-releases queue per version', () => {
  // Given / When
  const stable = ['v2.0.0', '2.0.1', 'v2.1.0'].map(concurrencyGroup);
  const rc = ['v2.0.0-rc.2', '2.0.0-rc.2'].map(concurrencyGroup);
  // Then: stable runs serialize with each other, never with a pre-release.
  assert.equal(new Set(stable).size, 1, stable.join(', '));
  assert.ok(stable[0]);
  assert.deepEqual(rc, ['sdk-publish-v2.0.0-rc.2', 'sdk-publish-v2.0.0-rc.2']);
  assert.notEqual(concurrencyGroup('v2.0.0-alpha.9'), rc[0]);
  assert.ok(!rc.includes(stable[0]));
});

for (const [title, latestPin, expected] of [
  ['a failed Latest pin shows in the CLI row', 'failure', 'RELEASE PUBLISHED, LATEST PIN FAILED'],
  ['a pinned Latest leaves the CLI row published', 'success', ':white_check_mark: published'],
  ['a pre-release (no Latest pin) leaves the CLI row published', 'skipped', ':white_check_mark: published'],
]) {
  test(`summary: ${title}`, (context) => {
    // Given: the CLI release and the Homebrew update succeeded.
    const root = fixture(context);
    const summary = join(root, 'summary.md');
    // When
    const result = run(root, step('Write run summary'), {
      GITHUB_STEP_SUMMARY: summary, VERSION: '2.0.0', VERSION_INPUT: 'v2.0.0', CHANNEL: 'stable', HOMEBREW_FORMULA: 'daytona',
      GATE_RESULT: 'success', REGISTRY_RESULT: 'success', PUBLISH_RESULT: latestPin === 'failure' ? 'failure' : 'success', TAG_RESULT: 'success',
      CLI_VERSION: '2.0.0', CLI_BUILD: 'success', CLI_PUBLISH: 'success', HOMEBREW_PUBLISH: 'success', LATEST_PIN: latestPin,
      PYPI_VERSION: '', NPM_VERSION: '', UPGRADE_NPM: '', UPGRADE_PYPI: '', PUBLISH_UPGRADE: 'false', FORCE: 'false',
      PY_BUILD: 'skipped', NPM_BUILD: 'skipped', UPGRADE_BUILD: 'skipped', SOURCE_REF: 'main', SOURCE_SHA: 'a'.repeat(40),
      WORKFLOW_REF: 'main', WORKFLOW_SHA: 'b'.repeat(40), ACTOR: 'someone', REPO: 'daytona/clients', SERVER: 'https://github.com' });
    // Then
    assert.equal(result.status, latestPin === 'failure' ? 1 : 0, result.stdout + result.stderr);
    const row = readFileSync(summary, 'utf8').split('\n').find((line) => line.startsWith('| CLI `daytona` |'));
    assert.ok(row.includes(expected), row);
  });
}

// ── prepare-release: only the versions the publish workflow takes ──────────

const prepareStep = (() => {
  const prepare = readFileSync(join(repoRoot, '.github/workflows/prepare-release.yaml'), 'utf8');
  const lines = prepare.split('      - name: Validate the version for this line\n')[1].split('        run: |\n')[1].split('\n');
  const end = lines.findIndex((line) => line.trim() !== '' && !line.startsWith(' '.repeat(10)));
  return lines.slice(0, end === -1 ? undefined : end).map((line) => line.slice(10)).join('\n');
})();

for (const [version, tags, status, message] of [
  ['v2.0.0-rc.1', [], 1, /2\.0\.0-rc\.1 is taken/],
  ['v2.0.0-rc.2', [], 0],
  ['v2.0.0-beta.1', [], 1],
  ['v2.0.0', ['v2.0.0-alpha.8'], 0],
  ['v2.0.5', ['v2.1.0', 'v2.0.0'], 1, /not above the highest stable 2\.x release v2\.1\.0/],
  ['v2.1.1', ['v2.1.0', 'v0.300.0'], 0],
]) {
  test(`prepare-release: ${version} with tags ${tags.join(', ') || 'none'}`, (context) => {
    // Given: a checkout with the release helpers and the tags so far.
    const root = fixture(context);
    const git = (...args) => assert.equal(spawnSync('git', ['-C', root, ...args]).status, 0);
    git('init', '-q');
    git('-c', 'user.email=a@b', '-c', 'user.name=x', 'commit', '-q', '--allow-empty', '-m', 'x');
    for (const tag of tags) git('tag', tag);
    mkdirSync(join(root, 'scripts'));
    for (const script of ['sdk-version.sh', 'release-channel.sh']) {
      writeFileSync(join(root, 'scripts', script), readFileSync(join(repoRoot, 'scripts', script)));
      chmodSync(join(root, 'scripts', script), 0o755);
    }
    writeFileSync(join(root, 'output'), '');
    // When
    const result = run(root, prepareStep, { VERSION_INPUT: version, GITHUB_REF: 'refs/heads/main', GITHUB_OUTPUT: join(root, 'output') });
    // Then: prepare-release takes what the publish gate takes.
    assert.equal(result.status, status, result.stdout + result.stderr);
    if (message) assert.match(result.stdout, message);
  });
}
