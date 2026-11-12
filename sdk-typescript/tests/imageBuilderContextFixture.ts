/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { chmod, mkdir, symlink, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { ImageBuilder } from '../src/v2/Image.js'

type ContextStep =
  | readonly ['file' | 'dir', string, string]
  | readonly ['requirements' | 'workdir' | 'user', string]
  | readonly ['run' | 'cmd' | 'entrypoint' | 'pip', readonly string[]]
  | readonly ['env', Record<string, string>]

export interface ContextCase {
  readonly name: string
  readonly files: Readonly<Record<string, string>>
  readonly symlinks?: Readonly<Record<string, string>>
  readonly source: { readonly registry?: string; readonly dockerfile?: string; readonly debianSlim?: string | null }
  readonly steps: readonly ContextStep[]
  readonly dockerfile: string
  readonly entries: readonly string[] | null
  readonly contents?: Readonly<Record<string, string>>
  readonly sha256?: string
  readonly error: string
}

interface FixtureTree {
  readonly files: Readonly<Record<string, string>>
  readonly symlinks?: Readonly<Record<string, string>>
  readonly fifos?: readonly string[]
  readonly ignoreLineLength?: number
}

export async function writeTree(root: string, testCase: FixtureTree): Promise<void> {
  for (const [name, contents] of Object.entries(testCase.files)) {
    const path = join(root, name)
    const parent = name.endsWith('/') ? path : dirname(path)
    await mkdir(parent, { recursive: true })
    for (let directory = parent; directory !== root; directory = dirname(directory)) await chmod(directory, 0o755)
    if (!name.endsWith('/')) {
      await writeFile(path, contents)
      await chmod(path, 0o644)
    }
  }
  for (const [name, target] of Object.entries(testCase.symlinks ?? {})) {
    const path = join(root, name)
    await mkdir(dirname(path), { recursive: true })
    await symlink(target, path)
  }
  for (const name of testCase.fifos ?? []) {
    await mkdir(dirname(join(root, name)), { recursive: true })
    execFileSync('mkfifo', [join(root, name)])
  }
  if (testCase.ignoreLineLength !== undefined) {
    await writeFile(join(root, '.dockerignore'), 'a'.repeat(testCase.ignoreLineLength))
  }
}

export function contextBuilder(root: string, testCase: Pick<ContextCase, 'source' | 'steps'>): ImageBuilder {
  let builder = 'debianSlim' in testCase.source
    ? testCase.source.debianSlim == null ? ImageBuilder.debianSlim() : ImageBuilder.debianSlim(testCase.source.debianSlim)
    : testCase.source.registry !== undefined ? ImageBuilder.fromRegistry(testCase.source.registry) : ImageBuilder.fromDockerfile(testCase.source.dockerfile ?? '')
  for (const step of testCase.steps) {
    switch (step[0]) {
      case 'file': builder = builder.addLocalFile(step[1].trim() ? join(root, step[1]) : step[1], step[2]); break
      case 'dir': builder = builder.addLocalDir(step[1].trim() ? join(root, step[1]) : step[1], step[2]); break
      case 'requirements': builder = builder.pipInstallFromRequirements(step[1].trim() ? join(root, step[1]) : step[1]); break
      case 'run': builder = builder.runCommands(...step[1]); break
      case 'workdir': builder = builder.workdir(step[1]); break
      case 'user': builder = builder.user(step[1]); break
      case 'cmd': builder = builder.cmd(...step[1]); break
      case 'entrypoint': builder = builder.entrypoint(...step[1]); break
      case 'pip': builder = builder.pipInstall(...step[1]); break
      case 'env': builder = builder.env(step[1]); break
      default: throw new TypeError(`unknown fixture operation: ${step satisfies never}`)
    }
  }
  return builder
}
