/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.120.0"}}';
const commands = ['RUN', 'ONBUILD RUN'] as const;

it.each(commands.flatMap(command => [true, false].map(closed => ({ command, closed }))))(
  'scans all remaining instructions when any $command heredoc is unterminated, closed=$closed', async ({ command, closed }) => {
    // Given a terminated first body and a potentially unterminated second body.
    const docker = `${command} <<FIRST <<SECOND\nENV DAYTONA_API_URL=https://first/api\nFIRST\nARG DAYTONA_SERVER_URL=https://second/api\n${closed ? 'SECOND\n' : ''}ENV DAYTONA_API_URL=https://later/api\n`;
    const root = temporaryProject({ 'package.json': manifest, Dockerfile: docker });
    // When scanning the Dockerfile through the CLI.
    const result = await invoke(root, ['--json']);
    // Then malformed instructions cannot hide either body, but valid bodies stay ignored.
    expect(result.code).toBe(2);
    const items: readonly { readonly kind: string; readonly line: number }[] = JSON.parse(result.output).items;
    expect(items.filter(item => item.kind === 'custom-api-url').map(item => item.line))
      .toEqual(closed ? [6] : [2, 4, 5]);
    expect(items.some(item => item.kind === 'manual-dependency')).toBe(false);
  },
);

it.each(commands.flatMap(command => ['<<EOF', '<< EOF', '<<"EOF"', '<<-EOF'].map(marker => ({ command, marker }))))(
  'ignores closed heredoc payloads when $command uses $marker', async ({ command, marker }) => {
    // Given
    const docker = `${command} ${marker}\nENV DAYTONA_API_URL=https://self/api\nEOF\n`;
    const root = temporaryProject({ 'package.json': manifest, Dockerfile: docker });
    // When
    const result = await invoke(root, ['--json', '--strict']);
    // Then
    expect(result.code).toBe(0);
    expect(JSON.parse(result.output).items).toEqual([]);
    expect(contents(root)['Dockerfile']).toBe(docker);
  },
);

it.each(commands.flatMap(command => ['ENV', 'ARG'].flatMap(setter => [true, false].map(closed => ({ command, setter, closed })))))(
  'reports later $setter when $command heredoc closed=$closed', async ({ command, setter, closed }) => {
    // Given
    const docker = `${command} <<EOF\nexample\n${closed ? 'EOF\n' : ''}${setter} DAYTONA_API_URL=https://self/api\n`;
    const root = temporaryProject({ 'package.json': manifest, Dockerfile: docker });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output).items.map((item: { readonly kind: string }) => item.kind))
      .toEqual(['env-api-url', 'custom-api-url']);
    expect(contents(root)).toEqual(before);
  },
);
