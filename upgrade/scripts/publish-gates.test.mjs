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

const workflow = readFileSync(new URL('../../.github/workflows/upgrade-tool-publish.yaml', import.meta.url), 'utf8');
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
  return block.split('        run: |\n')[1].split('\n').map((line) => line.slice(10)).join('\n');
}

for (const tag of [
  'daytona-upgrade-v0.0.0', 'daytona-upgrade-v1.2.3',
  'daytona-upgrade-v1.2.3-alpha.0', 'daytona-upgrade-v1.2.3-beta.1', 'daytona-upgrade-v1.2.3-rc.2',
  'daytona-upgrade-v01.2.3', 'daytona-upgrade-v1.02.3', 'daytona-upgrade-v1.2.03',
  'daytona-upgrade-v1.2.3-alpha.01', 'daytona-upgrade-v1.2.3-next.1',
  'daytona-upgrade-v1.2.3\ninjected=value', 'injected=value\ndaytona-upgrade-v1.2.3',
  'daytona-upgrade-v1.2.3\n', 'daytona-upgrade-v1.2.3\r',
  'daytona-upgrade-v1.2.3\rinjected=value', '',
]) {
  test(`validates the entire release tag when input is ${JSON.stringify(tag)}`, (context) => {
    // Given: an untrusted dispatch tag and a fresh GitHub output file.
    const root = fixture(context);
    const output = join(root, 'output');
    writeFileSync(output, '');
    const valid = ['daytona-upgrade-v0.0.0', 'daytona-upgrade-v1.2.3',
      'daytona-upgrade-v1.2.3-alpha.0', 'daytona-upgrade-v1.2.3-beta.1', 'daytona-upgrade-v1.2.3-rc.2'].includes(tag);
    // When: the workflow validates the dispatch input.
    const result = spawnSync('bash', ['-c', step('Require a daytona-upgrade release tag')], {
      encoding: 'utf8', env: { ...process.env, TAG: tag, GITHUB_OUTPUT: output },
    });
    // Then: invalid inputs fail without writing any workflow output.
    assert.equal(result.status, valid ? 0 : 1, result.stderr);
    assert.equal(readFileSync(output, 'utf8'), valid ? `version=${tag.slice('daytona-upgrade-v'.length)}\n` : '');
  });
}

for (const ref of ['refs/heads/main', 'refs/heads/release/v2', 'refs/heads/feature', 'refs/heads/release/v0', 'refs/tags/daytona-upgrade-v1.2.3']) {
  test(`checks the dispatch branch when ref is ${ref}`, () => {
    // Given: the ref from which the workflow was dispatched.
    const env = { ...process.env, GITHUB_REF: ref };
    // When: executing the workflow's entry gate.
    const result = spawnSync('bash', ['-c', step('Require dispatch from main')], { encoding: 'utf8', env });
    // Then: only main proceeds; other refs report an explicit workflow error.
    const allowed = ref === 'refs/heads/main';
    assert.equal(result.status, allowed ? 0 : 1, result.stderr);
    if (!allowed) assert.match(result.stdout, /::error::/);
  });
}

for (const tag of ['alpha', 'beta', 'next', 'latest', '', 'stable', 'latest\ninjected=value']) {
  test(`validates the npm dist-tag when value is ${JSON.stringify(tag)}`, (context) => {
    // Given: one packed artifact and a publisher that records whether it ran.
    const root = fixture(context);
    mkdirSync(join(root, 'pack'));
    writeFileSync(join(root, 'pack/tool.tgz'), '');
    executable(join(root, 'bin/npm'), 'echo published > "$PUBLISH_LOG"');
    const log = join(root, 'published');
    const valid = ['alpha', 'beta', 'next', 'latest'].includes(tag);
    // When: running the credential-bearing publish step with the supplied dist-tag.
    const result = spawnSync('bash', ['-c', step('Publish to npm (OIDC)')], {
      cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${root}/bin:${process.env.PATH}`,
        NPM_DIST_TAG: tag, PUBLISH_LOG: log },
    });
    // Then: only an allowed dist-tag reaches npm.
    assert.equal(result.status, valid ? 0 : 1, result.stderr);
    if (valid) assert.equal(readFileSync(log, 'utf8'), 'published\n');
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
    executable(join(root, 'bin/npm'), 'if [ "$1" = pack ]; then cp "$SOURCE_TARBALL" "$RUNNER_TEMP/npm/artifact.tgz"; fi');
    // When: executing the publish job's actual packaging and artifact checks.
    const result = spawnSync('bash', ['-c', step('Build, test and pack the npm package')], {
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
  const result = spawnSync('bash', ['-c', step('Mapping copies and guide are current')], {
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
    mkdirSync(join(root, 'typescript'), { recursive: true });
    if (state !== 'absent') mkdirSync(join(root, 'typescript/node_modules'));
    if (installed) {
      mkdirSync(join(root, 'typescript/node_modules/typescript'));
      writeFileSync(join(root, 'typescript/node_modules/typescript/package.json'), '{}');
    }
    executable(join(root, 'bin/node'), 'exit 0');
    executable(join(root, 'bin/npm'), 'printf "%s\\n" "$*" >> "$INSTALL_LOG"');
    const recipe = testUpgrade.slice(testUpgrade.indexOf('if [ ! -f typescript/node_modules'), testUpgrade.indexOf('\nfi\n') + 3);
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
