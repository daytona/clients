/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as api from './gen/api-types.js'
import { badRequest, isPlainRecord } from './options.js'
import { strip } from './pyText.js'
import { deepFrozen } from './frozen.js'
import { assertTextList, dockerfileEscape, endsWithContinuation, envName, envValue, lineText, rawText, renderStep, shellQuote } from './builderText.js'
import type { BuilderSource, BuilderStep } from './builderText.js'
import { localSource } from './builderLocalSource.js'
import type { LocalSource } from './builderLocalSource.js'

// The builder's local sources stay off its public surface: the SDK reads them
// through localSourcesOf(), which the package entry points do not export.
const LOCAL_SOURCES = new WeakMap<ImageBuilder, readonly LocalSource[]>()

/** The local files a builder copies, in COPY order. @internal */
export function localSourcesOf(builder: ImageBuilder): readonly LocalSource[] {
  return LOCAL_SOURCES.get(builder) ?? []
}

export class ImageBuilder {
  private constructor(
    private readonly source: BuilderSource,
    private readonly steps: readonly BuilderStep[] = [],
    localSources: readonly LocalSource[] = [],
  ) {
    // Checked when the first step is added: a base ending in a line
    // continuation would swallow it (and every later step) silently.
    if (steps.length > 0 && source.type === 'dockerfile' && endsWithContinuation(source.dockerfile)) {
      throw badRequest('ImageBuilder.fromDockerfile dockerfile ends with a line continuation; a step added after it would be joined to its last instruction')
    }
    deepFrozen(source)
    deepFrozen(steps)
    LOCAL_SOURCES.set(this, deepFrozen(localSources))
    Object.freeze(this)
  }

  public static fromRegistry(ref: string): ImageBuilder {
    return new ImageBuilder({ type: 'registry', ref: lineText(ref, 'ImageBuilder.fromRegistry ref') })
  }

  public static fromDockerfile(dockerfile: string): ImageBuilder {
    return new ImageBuilder({ type: 'dockerfile', dockerfile: rawText(dockerfile, 'ImageBuilder.fromDockerfile dockerfile') })
  }

  public static debianSlim(pythonVersion?: string): ImageBuilder {
    if (pythonVersion !== undefined && typeof pythonVersion !== 'string') throw badRequest('ImageBuilder.debianSlim pythonVersion must be a string')
    const version = pythonVersion === undefined ? '3.13' : strip(pythonVersion)
    if (!['3.10', '3.11', '3.12', '3.13', '3.14'].includes(version)) {
      throw badRequest(`ImageBuilder.debianSlim pythonVersion must be one of 3.10, 3.11, 3.12, 3.13, 3.14 (got ${JSON.stringify(version)})`)
    }
    return ImageBuilder.fromRegistry(`python:${version}-slim-bookworm`).runCommands(
      'apt-get update',
      'apt-get install -y gcc gfortran build-essential',
      'pip install --upgrade pip',
      "echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections",
    )
  }

  public runCommands(...commands: readonly string[]): ImageBuilder {
    return this.append({ type: 'run', commands: assertTextList(commands, 'ImageBuilder.runCommands commands').map((command) => lineText(command, 'ImageBuilder.runCommands command')) })
  }

  public pipInstall(...packages: readonly string[]): ImageBuilder {
    const resolved = assertTextList(packages, 'ImageBuilder.pipInstall packages').map((pkg) => lineText(pkg, 'ImageBuilder.pipInstall package'))
    return this.runCommands(`python -m pip install ${resolved.map(shellQuote).join(' ')}`)
  }

  public addLocalFile(localPath: string, remotePath: string): ImageBuilder {
    if (arguments.length > 2) throw badRequest('ImageBuilder.addLocalFile takes a local path and a remote path')
    const source = localSource(localPath, 'file', localSourcesOf(this).length + 1)
    const remote = lineText(remotePath, 'ImageBuilder.addLocalFile remotePath')
    // The appended name is the file's own, not a Dockerfile word: BuildKit
    // runs its shell-word lexer over COPY destinations, so `\\`, `$` and both
    // quotes are escaped.
    const filename = process.getBuiltinModule('node:path').basename(source.localPath).replace(/[\\$'"]/g, (char) => `\\${char}`)
    return this.copy(source, remote.endsWith('/') ? remote + filename : remote)
  }

  public addLocalDir(localPath: string, remotePath: string): ImageBuilder {
    if (arguments.length > 2) throw badRequest('ImageBuilder.addLocalDir takes a local path and a remote path')
    const source = localSource(localPath, 'dir', localSourcesOf(this).length + 1)
    return this.copy(source, lineText(remotePath, 'ImageBuilder.addLocalDir remotePath'))
  }

  public pipInstallFromRequirements(path: string): ImageBuilder {
    // 0.x took pip options here; silently dropping an index URL would install
    // from the default index, so a second argument is refused.
    if (arguments.length > 1) throw badRequest('ImageBuilder.pipInstallFromRequirements takes only the path; put pip options in a runCommands line')
    const source = localSource(path, 'file', localSourcesOf(this).length + 1)
    return this.copy(source, '/.requirements.txt').runCommands('python -m pip install -r /.requirements.txt')
  }

  private copy(source: LocalSource, remotePath: string): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'copy', archivePath: source.archivePath, remotePath }], [...localSourcesOf(this), source])
  }

  private append(step: BuilderStep): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, step], localSourcesOf(this))
  }

  public env(name: string, value: string): ImageBuilder
  public env(values: Record<string, string>): ImageBuilder
  public env(nameOrValues: string | Record<string, string>, value?: string): ImageBuilder {
    const given: unknown = nameOrValues
    // A mapping carries its values: a second argument would be dropped.
    if (typeof given !== 'string' && (!isPlainRecord(given) || value !== undefined)) {
      throw badRequest('ImageBuilder.env takes a name and a value, or a mapping of names to values')
    }
    const pairs: ReadonlyArray<readonly [string, string]> = typeof nameOrValues === 'string'
      ? [[envName(nameOrValues, 'ImageBuilder.env name'), envValue(value, 'ImageBuilder.env value')]]
      : Object.entries(nameOrValues).map(([key, entryValue]) => [envName(key, 'ImageBuilder.env key'), envValue(entryValue, 'ImageBuilder.env value')] as const)
    const seen = new Set<string>()
    for (const [key] of pairs) {
      if (seen.has(key)) throw badRequest(`ImageBuilder.env key ${JSON.stringify(key)} appears more than once after stripping`)
      seen.add(key)
    }
    if (this.source.type === 'dockerfile' && dockerfileEscape(this.source.dockerfile) !== '\\') {
      throw badRequest('ImageBuilder.env cannot keep values literal under a Dockerfile escape directive other than a backslash')
    }
    // Keep Object.entries order: recreating an object would sort integer-like
    // keys again after envName strips them. Python preserves its dict order.
    return this.append({ type: 'env', entries: pairs })
  }

  public workdir(path: string): ImageBuilder {
    return this.append({ type: 'workdir', path: lineText(path, 'ImageBuilder.workdir path') })
  }

  public user(name: string): ImageBuilder {
    return this.append({ type: 'user', name: lineText(name, 'ImageBuilder.user name') })
  }

  public cmd(...args: readonly string[]): ImageBuilder {
    return this.append({ type: 'cmd', args: assertTextList(args, 'ImageBuilder.cmd args') })
  }

  public entrypoint(...args: readonly string[]): ImageBuilder {
    return this.append({ type: 'entrypoint', args: assertTextList(args, 'ImageBuilder.entrypoint args') })
  }

  public toDockerfile(): string {
    const base = this.source.type === 'registry' ? `FROM ${this.source.ref}` : strip(this.source.dockerfile)
    const renderedSteps = this.steps.map(renderStep).filter((step) => step !== '')
    return strip([base, ...renderedSteps].join('\n')) + '\n'
  }

  /** A bare registry ref remains a registry import, rather than a build. @internal */
  public toRequestSource(): { source: NonNullable<api.CreateImageRequest['source']> } | { dockerfile: string } {
    const rendersSteps = this.steps.map(renderStep).some((step) => step !== '')
    if (this.source.type === 'registry' && !rendersSteps) {
      return { source: { type: 'registry', ref: this.source.ref } }
    }
    return { dockerfile: this.toDockerfile() }
  }
}

export function isImageBuilder(value: unknown): value is ImageBuilder {
  return value instanceof ImageBuilder
}
