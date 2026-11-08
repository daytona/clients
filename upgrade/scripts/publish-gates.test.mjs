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

const workflow = readFileSync(new URL('../../.github/workflows/sdk_publish.yaml', import.meta.url), 'utf8');
const testUpgrade = readFileSync(new URL('../../scripts/test-upgrade.sh', import.meta.url), 'utf8');

function fixture(context) {
  const root = mkdtempSync(join(tmpdir(), 'upgrade-build-'));
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

// A real tarball holding package/package.json, as `npm pack` writes it.
function packTool(root, name, version) {
  mkdirSync(join(root, 'tool/package'), { recursive: true });
  writeFileSync(join(root, 'tool/package/package.json'), JSON.stringify({ name, version }));
  const packed = spawnSync('tar', ['-czf', join(root, 'upgrade-pack/tool.tgz'), 'package'], { cwd: join(root, 'tool'), encoding: 'utf8' });
  assert.equal(packed.status, 0, packed.stderr);
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
    packTool(root, '@daytona/upgrade', '2.0.0-alpha.8');
    executable(join(root, 'bin/npm'), 'echo published > "$PUBLISH_LOG"');
    const log = join(root, 'published');
    const valid = ['alpha', 'beta', 'next', 'latest'].includes(tag);
    // When: running the credential-bearing publish step with the supplied dist-tag.
    const result = spawnSync('bash', ['-c', step('Publish the migration tool to npm (OIDC)')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        NPM_DIST_TAG: tag, NPM_VERSION: '2.0.0-alpha.8', RESUME: 'false', PUBLISH_LOG: log,
        // The npm latest guard has its own tests (publish-channels.test.mjs).
        NPM_LATEST_GUARD: 'echo "guard $*" >> "$GUARD_LOG"', GUARD_LOG: join(root, 'guard') },
    });
    // Then: only an allowed dist-tag reaches npm.
    assert.equal(result.status, valid ? 0 : 1, result.stderr);
    if (valid) assert.equal(readFileSync(log, 'utf8'), 'published\n');
    else assert.throws(() => readFileSync(log), { code: 'ENOENT' });
    // A publish under `latest` is checked before and verified after.
    if (tag === 'latest') assert.equal(readFileSync(join(root, 'guard'), 'utf8'), 'guard check @daytona/upgrade 2.0.0-alpha.8\nguard verify @daytona/upgrade 2.0.0-alpha.8\n');
    else assert.throws(() => readFileSync(join(root, 'guard')), { code: 'ENOENT' });
  });
}

for (const [code, published] of [['200', false], ['404', true], ['503', false]]) {
  test(`resumes the migration tool npm publish when the registry answers ${code}`, (context) => {
    // Given: a resumed run and a registry that has, lacks, or cannot answer for the version.
    const root = fixture(context);
    mkdirSync(join(root, 'upgrade-pack'));
    packTool(root, '@daytona/upgrade', '2.0.0-alpha.8');
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
    const result = spawnSync('bash', ['-c', step('Build and pack the npm migration tool')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        SOURCE_TARBALL: join(root, 'artifact.tgz'), RUNNER_TEMP: root, NPM_VERSION: '1.2.3' },
    });
    // Then: only the artifact with the tag's version is publishable.
    assert.equal(result.status, version === '1.2.3' ? 0 : 1, result.stderr);
  });
}

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

for (const [code, published] of [['200', true], ['404', false]]) {
  test(`waits for the SDK on its registry before the migration tool publishes (HTTP ${code})`, (context) => {
    // Given: a registry that answers the SDK version's URL with a fixed status.
    const root = fixture(context);
    executable(join(root, 'bin/curl'), `printf ${code}`);
    executable(join(root, 'bin/sleep'), 'exit 0');
    executable(join(root, 'bin/date'), 'if [ "$1" = +%s ]; then echo $(( $(cat "$CLOCK") )); echo $(( $(cat "$CLOCK") + 100 )) > "$CLOCK"; else echo 1; fi');
    writeFileSync(join(root, 'clock'), '0');
    // When
    const result = spawnSync('bash', ['-c', step('Wait until @daytona/sdk ${{ needs.gate.outputs.upgrade_npm }} is on npm')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        URL: 'https://registry.npmjs.org/@daytona%2fsdk/2.0.0-alpha.8', CLOCK: join(root, 'clock') },
    });
    // Then: a visible SDK lets the tool publish; an invisible one fails after the deadline.
    assert.equal(result.status, published ? 0 : 1, result.stdout + result.stderr);
  });
}

test('publishes the migration tool only after the SDK it pins, and before the CLI', () => {
  // Given: the publish job's step order.
  const at = (name) => {
    const index = workflow.indexOf(`      - name: ${name}`);
    assert.ok(index >= 0, `missing step ${name}`);
    return index;
  };
  // Then
  assert.ok(at('Publish daytona $') < at('Wait until daytona') && at('Wait until daytona') < at('Publish daytona-upgrade'));
  assert.ok(at('Publish @daytona/sdk') < at('Wait until @daytona/sdk') && at('Wait until @daytona/sdk') < at('Publish the migration tool to npm (OIDC)'));
  assert.ok(at('Publish the migration tool to npm (OIDC)') < at('Download the CLI release assets'));
});

for (const [name, version, published] of [['@daytona/upgrade', '2.0.0-alpha.8', true], ['@daytona/sdk', '2.0.0-alpha.8', false], ['@daytona/upgrade', '2.0.0-alpha.7', false]]) {
  test(`publishes the migration tool tarball only when it is the tool at this version (${name}@${version})`, (context) => {
    // Given: a tarball whose package identity may not match the run.
    const root = fixture(context);
    mkdirSync(join(root, 'upgrade-pack'));
    packTool(root, name, version);
    executable(join(root, 'bin/npm'), 'echo published > "$PUBLISH_LOG"');
    const log = join(root, 'published');
    // When
    const result = spawnSync('bash', ['-c', step('Publish the migration tool to npm (OIDC)')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        NPM_DIST_TAG: 'alpha', NPM_VERSION: '2.0.0-alpha.8', RESUME: 'false', PUBLISH_LOG: log },
    });
    // Then
    assert.equal(result.status, published ? 0 : 1, result.stdout + result.stderr);
    if (published) assert.equal(readFileSync(log, 'utf8'), 'published\n');
    else assert.throws(() => readFileSync(log), { code: 'ENOENT' });
  });
}

for (const [files, valid] of [[['daytona_upgrade-2.0.0a8-py3-none-any.whl', 'daytona_upgrade-2.0.0a8.tar.gz'], true],
  [['daytona_upgrade-2.0.0a8-py3-none-any.whl', 'daytona_upgrade-2.0.0a8.tar.gz', 'daytona-2.0.0a8-py3-none-any.whl'], false]]) {
  test(`checks the migration tool's PyPI distributions (${files.length} files)`, (context) => {
    // Given
    const root = fixture(context);
    mkdirSync(join(root, 'upgrade-pypi'));
    for (const file of files) writeFileSync(join(root, 'upgrade-pypi', file), '');
    // When
    const result = spawnSync('bash', ['-c', step("The migration tool's PyPI distributions are exactly the tool's")], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PYPI_VERSION: '2.0.0a8' },
    });
    // Then: an SDK distribution never rides along under the tool's publisher.
    assert.equal(result.status, valid ? 0 : 1, result.stdout + result.stderr);
  });
}

for (const [tagged, expected] of [[false, 1], [true, 0]]) {
  test(`a tool-only run needs the SDKs' release tag on its source commit (tagged: ${tagged})`, (context) => {
    // Given: a repository whose source commit may carry the release tag.
    const root = fixture(context);
    const git = (...args) => spawnSync('git', ['-C', root, ...args], { encoding: 'utf8' });
    git('init', '-q');
    git('-c', 'user.email=a@b', '-c', 'user.name=x', 'commit', '-q', '--allow-empty', '-m', 'release');
    const sha = git('rev-parse', 'HEAD').stdout.trim();
    if (tagged) git('tag', 'v2.0.0-alpha.8');
    mkdirSync(join(root, '.publish-tools/scripts'), { recursive: true });
    writeFileSync(join(root, '.publish-tools/scripts/sdk-version.sh'),
      '#!/bin/bash\nprintf "version=2.0.0-alpha.8\\nnpm=2.0.0-alpha.8\\ncli=2.0.0-alpha.8\\npypi=2.0.0a8\\n"\n');
    chmodSync(join(root, '.publish-tools/scripts/sdk-version.sh'), 0o755);
    writeFileSync(join(root, '.publish-tools/scripts/release-channel.sh'), readFileSync(new URL('../../scripts/release-channel.sh', import.meta.url)));
    chmodSync(join(root, '.publish-tools/scripts/release-channel.sh'), 0o755);
    const output = join(root, 'output');
    writeFileSync(output, '');
    // When: the gate derives versions for a tool-only run.
    // The gate's step starts with its id, so it is cut out by its name line.
    const block = workflow.split('        name: Derive registry versions and the release channel (branch rule + gate)\n')[1].split('\n      - ')[0];
    const lines = block.split('        run: |\n')[1].split('\n');
    const end = lines.findIndex((line) => line.trim() !== '' && !line.startsWith(' '.repeat(10)));
    const script = lines.slice(0, end === -1 ? undefined : end).map((line) => line.slice(10)).join('\n');
    const result = spawnSync('bash', ['-c', script], {
      cwd: root, encoding: 'utf8', env: { ...process.env, VERSION_INPUT: 'v2.0.0-alpha.8', SOURCE_SHA: sha, GITHUB_OUTPUT: output,
        PUBLISH_PYPI: 'false', PUBLISH_NPM: 'false', PUBLISH_CLI: 'false', PUBLISH_UPGRADE: 'true' },
    });
    // Then: without the tag, the run cannot prove the SDKs came from this commit,
    // and the error names the fixes, including unticking publish_upgrade.
    assert.equal(result.status, expected, result.stdout + result.stderr);
    if (expected === 1) assert.match(result.stdout, /untick publish_upgrade/);
  });
}

for (const [name, version, valid] of [['@daytona/sdk', '2.0.0-alpha.8', true], ['@daytona/upgrade', '2.0.0-alpha.8', false], ['@daytona/sdk', '2.0.0-alpha.7', false]]) {
  test(`publishes the SDK tarball only when it is the SDK at this version (${name}@${version})`, (context) => {
    // Given: a tarball whose package identity may not match the run.
    const root = fixture(context);
    mkdirSync(join(root, 'pack/package'), { recursive: true });
    writeFileSync(join(root, 'pack/package/package.json'), JSON.stringify({ name, version }));
    const packed = spawnSync('tar', ['-czf', join(root, 'pack/sdk.tgz'), 'package'], { cwd: join(root, 'pack'), encoding: 'utf8' });
    assert.equal(packed.status, 0, packed.stderr);
    rmSync(join(root, 'pack/package'), { recursive: true });
    executable(join(root, 'bin/npm'), 'echo published > "$PUBLISH_LOG"');
    const log = join(root, 'published');
    // When
    const result = spawnSync('bash', ['-c', step('Publish @daytona/sdk ${{ needs.gate.outputs.npm }} to npm (OIDC)')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        NPM_DIST_TAG: 'alpha', NPM_VERSION: '2.0.0-alpha.8', RESUME: 'false', PUBLISH_LOG: log },
    });
    // Then: the tool's package never goes up under this step.
    assert.equal(result.status, valid ? 0 : 1, result.stdout + result.stderr);
    if (valid) assert.equal(readFileSync(log, 'utf8'), 'published\n');
    else assert.throws(() => readFileSync(log), { code: 'ENOENT' });
  });
}

for (const [files, valid] of [[['daytona-2.0.0a8-py3-none-any.whl', 'daytona-2.0.0a8.tar.gz'], true],
  [['daytona-2.0.0a8-py3-none-any.whl', 'daytona-2.0.0a8.tar.gz', 'daytona_upgrade-2.0.0a8-py3-none-any.whl'], false],
  [['daytona-2.0.0a7-py3-none-any.whl', 'daytona-2.0.0a7.tar.gz'], false]]) {
  test(`checks the SDK's PyPI distributions (${files.join(', ')})`, (context) => {
    // Given
    const root = fixture(context);
    mkdirSync(join(root, 'pypi-dist'));
    for (const file of files) writeFileSync(join(root, 'pypi-dist', file), '');
    // When
    const result = spawnSync('bash', ['-c', step("The PyPI distributions are exactly the SDK's")], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PYPI_VERSION: '2.0.0a8' },
    });
    // Then: a tool distribution never rides along under the SDK's upload.
    assert.equal(result.status, valid ? 0 : 1, result.stdout + result.stderr);
  });
}

for (const [lockstep, expected] of [[true, 0], [false, 1]]) {
  test(`refuses to publish a migration tool that predates lockstep (lockstep: ${lockstep})`, (context) => {
    // Given: a source tree with the tool, with or without its lockstep target module.
    const root = fixture(context);
    for (const directory of ['sdk-python', 'sdk-typescript', 'upgrade/mapping', 'upgrade/typescript/src', 'upgrade/python/src/daytona_upgrade']) mkdirSync(join(root, directory), { recursive: true });
    writeFileSync(join(root, 'upgrade/mapping/mapping.json'), '{}');
    if (lockstep) {
      writeFileSync(join(root, 'upgrade/typescript/src/sdk-target.ts'), '');
      writeFileSync(join(root, 'upgrade/python/src/daytona_upgrade/sdk_target.py'), '');
    }
    // When
    const result = spawnSync('bash', ['-c', step('Require the sdk-python/ and sdk-typescript/ layout')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PUBLISH_UPGRADE: 'true' },
    });
    // Then: an older tool would write its own fixed range, not this release.
    assert.equal(result.status, expected, result.stdout + result.stderr);
  });
}
