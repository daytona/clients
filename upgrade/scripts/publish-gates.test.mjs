/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const workflow = readFileSync(new URL('../../.github/workflows/sdk_publish.yaml', import.meta.url), 'utf8');
const testUpgrade = readFileSync(new URL('../../scripts/test-upgrade.sh', import.meta.url), 'utf8');

function fixture(context) {
  const root = mkdtempSync(join(tmpdir(), 'upgrade-build-'));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'bin'));
  return root;
}

function executable(path, body) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `#!/bin/bash\nset -euo pipefail\n${body}\n`);
  chmodSync(path, 0o755);
}

function step(name) {
  const block = workflow.split(`      - name: ${name}\n`)[1]?.split('\n      - ')[0];
  assert.ok(block, `missing step ${name}`);
  // The run block ends at the first line indented less than its body (a comment before the next step).
  const lines = block.split('        run: |\n')[1].split('\n');
  const end = lines.findIndex((line) => line.trim() !== '' && !line.startsWith(' '.repeat(10)));
  return lines.slice(0, end === -1 ? undefined : end).map((line) => line.slice(10)).join('\n');
}

for (const tag of ['alpha', 'beta', 'next', 'latest', '', 'stable', 'latest\ninjected=value']) {
  test(`validates the npm dist-tag when value is ${JSON.stringify(tag)}`, (context) => {
    // Given: one packed artifact and a publisher that records whether it ran.
    const root = fixture(context);
    mkdirSync(join(root, 'upgrade-pack'));
    writeFileSync(join(root, 'upgrade-pack/tool.tgz'), '');
    executable(join(root, 'bin/npm'), 'echo published > "$PUBLISH_LOG"');
    const log = join(root, 'published');
    const valid = ['alpha', 'beta', 'next', 'latest'].includes(tag);
    // When: running the credential-bearing publish step with the supplied dist-tag.
    const result = spawnSync('bash', ['-c', step('Publish the migration tool to npm (OIDC)')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        NPM_DIST_TAG: tag, NPM_VERSION: '2.0.0-alpha.8', RESUME: 'false', PUBLISH_LOG: log },
    });
    // Then: only an allowed dist-tag reaches npm.
    assert.equal(result.status, valid ? 0 : 1, result.stderr);
    if (valid) assert.equal(readFileSync(log, 'utf8'), 'published\n');
    else assert.throws(() => readFileSync(log), { code: 'ENOENT' });
  });
}

for (const [code, published] of [['200', false], ['404', true], ['503', false]]) {
  test(`resumes the migration tool npm publish when the registry answers ${code}`, (context) => {
    // Given: a resumed run and a registry that has, lacks, or cannot answer for the version.
    const root = fixture(context);
    mkdirSync(join(root, 'upgrade-pack'));
    writeFileSync(join(root, 'upgrade-pack/tool.tgz'), '');
    executable(join(root, 'bin/npm'), 'echo published > "$PUBLISH_LOG"');
    executable(join(root, 'bin/curl'), `printf ${code}`);
    const log = join(root, 'published');
    // When
    const result = spawnSync('bash', ['-c', step('Publish the migration tool to npm (OIDC)')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        NPM_DIST_TAG: 'alpha', NPM_VERSION: '2.0.0-alpha.8', RESUME: 'true', PUBLISH_LOG: log },
    });
    // Then: only an absent version publishes; an unknown answer fails closed.
    assert.equal(result.status, code === '503' ? 1 : 0, result.stderr);
    if (published) assert.equal(readFileSync(log, 'utf8'), 'published\n');
    else assert.throws(() => readFileSync(log), { code: 'ENOENT' });
  });
}

for (const version of ['1.2.3', '1.2.4']) {
  test(`checks npm artifact version when tarball contains ${version}`, (context) => {
    // Given: a real tarball whose metadata either matches or differs from the release tag.
    const root = fixture(context);
    mkdirSync(join(root, 'package/dist'), { recursive: true });
    writeFileSync(join(root, 'package/package.json'), JSON.stringify({ version }));
    writeFileSync(join(root, 'package/dist/mapping.json'), '{}');
    const packed = spawnSync('tar', ['-czf', join(root, 'artifact.tgz'), 'package'], { cwd: root, encoding: 'utf8' });
    assert.equal(packed.status, 0, packed.stderr);
    executable(join(root, 'bin/npm'), 'if [ "$1" = pack ]; then cp "$SOURCE_TARBALL" "$RUNNER_TEMP/upgrade-npm/artifact.tgz"; fi');
    // When: executing the publish job's actual packaging and artifact checks.
    const result = spawnSync('bash', ['-c', step('Build, test and pack the npm migration tool')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        SOURCE_TARBALL: join(root, 'artifact.tgz'), RUNNER_TEMP: root, NPM_VERSION: '1.2.3' },
    });
    // Then: only the artifact with the tag's version is publishable.
    assert.equal(result.status, version === '1.2.3' ? 0 : 1, result.stderr);
  });
}

test('blocks packaging when the v2 surface gate fails in the mapping step', (context) => {
  // Given: a stale mapping for which the surface command fails.
  const root = fixture(context);
  executable(join(root, 'bin/node'), 'case "$1" in */check-v2-surface.mjs) exit 23 ;; esac');
  executable(join(root, 'bin/npm'), 'exit 0');
  // When: running the build job's pre-packaging mapping checks.
  const result = spawnSync('bash', ['-c', step('Mapping copies, guide and surfaces are current')], {
    cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}` },
  });
  // Then: the failed surface gate propagates, rather than allowing packaging.
  assert.equal(result.status, 23, result.stderr);
});

for (const state of ['installed', 'incomplete', 'absent']) {
  test(`preserves SDK dependencies when node_modules is ${state}`, (context) => {
    // Given: a developer checkout with a complete, partial or missing SDK installation.
    const installed = state === 'installed';
    const root = fixture(context);
    mkdirSync(join(root, 'sdk-typescript'), { recursive: true });
    if (state !== 'absent') mkdirSync(join(root, 'sdk-typescript/node_modules'));
    if (installed) {
      mkdirSync(join(root, 'sdk-typescript/node_modules/typescript'));
      writeFileSync(join(root, 'sdk-typescript/node_modules/typescript/package.json'), '{}');
    }
    executable(join(root, 'bin/node'), 'exit 0');
    executable(join(root, 'bin/npm'), 'printf "%s\\n" "$*" >> "$INSTALL_LOG"');
    const recipe = testUpgrade.slice(testUpgrade.indexOf('if [ ! -f sdk-typescript/node_modules'), testUpgrade.indexOf('\nfi\n') + 3);
    // When: executing the real SDK-install guard from scripts/test-upgrade.sh.
    const log = join(root, 'npm.log');
    const result = spawnSync('bash', ['-c', recipe], { cwd: root, encoding: 'utf8',
      env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`, INSTALL_LOG: log } });
    // Then: a clean install runs only when the compiler package is missing.
    assert.equal(result.status, 0, result.stderr);
    if (installed) assert.throws(() => readFileSync(log), { code: 'ENOENT' });
    else assert.equal(readFileSync(log, 'utf8'), 'ci --no-audit --no-fund\n');
  });
}

// registry-check: the tool never ships without its SDK, and a version is published once.
for (const [title, inputs, codes, expected] of [
  ['tool-only, SDK absent', { NPM_VERSION: '', PYPI_VERSION: '' }, { sdk: '404', tool: '404' }, 1],
  ['tool-only, SDK published', { NPM_VERSION: '', PYPI_VERSION: '' }, { sdk: '200', tool: '404' }, 0],
  ['tool-only with force_republish, SDK absent', { NPM_VERSION: '', PYPI_VERSION: '', FORCE: 'true' }, { sdk: '404', tool: '404' }, 1],
  ['tool-only, SDK registry unavailable', { NPM_VERSION: '', PYPI_VERSION: '' }, { sdk: '503', tool: '404' }, 1],
  ['tool already published', {}, { sdk: '404', tool: '200' }, 1],
  ['fresh version', {}, { sdk: '404', tool: '404' }, 0],
  ['force_republish, tool already published', { FORCE: 'true' }, { sdk: '200', tool: '200' }, 0],
]) {
  test(`registry check: ${title}`, (context) => {
    // Given: a registry that answers per package.
    const root = fixture(context);
    executable(join(root, 'bin/curl'), [
      'url="${*: -1}"',
      'case "$url" in',
      '  *daytona%2fupgrade*|*daytona-upgrade*) printf "$TOOL_CODE" ;;',
      '  *) printf "$SDK_CODE" ;;',
      'esac',
    ].join('\n'));
    // When
    const result = spawnSync('bash', ['-c', step('Refuse a version the registry already has (unless force_republish)')], {
      encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        NPM_VERSION: '2.0.0-alpha.8', PYPI_VERSION: '2.0.0a8', CLI_VERSION: '', FORCE: 'false',
        UPGRADE_NPM: '2.0.0-alpha.8', UPGRADE_PYPI: '2.0.0a8', GH_TOKEN: '', SDK_CODE: codes.sdk, TOOL_CODE: codes.tool, ...inputs },
    });
    // Then
    assert.equal(result.status, expected, result.stdout + result.stderr);
  });
}

test('tests both tools before stamping the release version', () => {
  // Given: the build steps of the release run.
  const npmStep = step('Build, test and pack the npm migration tool');
  const pythonStep = step('Test, then build the PyPI migration tool');
  // Then: a stamped version changes the SDK release the tool writes, so the
  // suites (which expect the development range) run on the unstamped tree.
  assert.ok(npmStep.indexOf('npm test') >= 0 && npmStep.indexOf('npm test') < npmStep.indexOf('npm version'), npmStep);
  assert.ok(pythonStep.indexOf('uv run pytest') >= 0 && pythonStep.indexOf('uv run pytest') < pythonStep.indexOf('PYPI_VERSION"]'), pythonStep);
});

test('the release build gets the raised test timeout', () => {
  // Given: the npm script the release step runs, and the vitest config.
  const manifest = JSON.parse(readFileSync(new URL('../typescript/package.json', import.meta.url), 'utf8'));
  const config = readFileSync(new URL('../typescript/vitest.config.ts', import.meta.url), 'utf8');
  // Then: no command-line timeout overrides the config's release value, and
  // the release step sets the switch that raises it.
  assert.doesNotMatch(manifest.scripts.test, /--(test|hook)Timeout/);
  assert.match(config, /testTimeout: release \? 300_000/);
  assert.match(step('Build, test and pack the npm migration tool'), /npm test/);
  assert.match(workflow, /DAYTONA_UPGRADE_SKIP_WALL_CLOCK: '1'/);
});

test('the pin bump writes a changelog fragment the changelog check accepts', (context) => {
  // Given: the fragment path the pin bump workflow builds for a release tag.
  const bump = readFileSync(new URL('../../.github/workflows/v0-pin-bump.yaml', import.meta.url), 'utf8');
  const templates = [...bump.matchAll(/"(changelog\.d\/v0-pin-\$\{TAG[^"]*\.changed\.md)"/g)].map((match) => match[1]);
  assert.equal(templates.length, 2, 'prepare and open-pr build the fragment path');
  assert.equal(templates[0], templates[1]);
  const root = fixture(context);
  const built = spawnSync('bash', ['-c', `TAG=v0.231.0; printf %s "${templates[0]}"`], { encoding: 'utf8' }).stdout;
  mkdirSync(join(root, 'changelog.d'));
  writeFileSync(join(root, built), '**Fragment** — text.\n');
  // When
  const result = spawnSync('python3', [fileURLToPath(new URL('../../scripts/changelog/assemble.py', import.meta.url)), 'check', '--dir', join(root, 'changelog.d')], { encoding: 'utf8' });
  // Then
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
