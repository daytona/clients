/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as api from './gen/api-types.js'
import { ImageRetention as IMAGE_RETENTIONS, SnapshotClass as SNAPSHOT_CLASSES, SnapshotKind as SNAPSHOT_KINDS } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { badRequest } from './options.js'
import type { ArtifactRetention, CpuArch, ImageOrigin, ImageSource, ImageStatus, SnapshotClass, SnapshotKind } from './types.js'
import type { ImageModel } from './imageModel.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'

type BuilderSource =
  | { readonly type: 'registry'; readonly ref: string }
  | { readonly type: 'dockerfile'; readonly dockerfile: string }

type BuilderStep =
  | { readonly type: 'run'; readonly commands: readonly string[] }
  | { readonly type: 'env'; readonly entries: Readonly<Record<string, string>> }
  | { readonly type: 'workdir'; readonly path: string }
  | { readonly type: 'user'; readonly name: string }
  | { readonly type: 'cmd'; readonly args: readonly string[] }
  | { readonly type: 'entrypoint'; readonly args: readonly string[] }

function assertText(value: string, label: string): string {
  if (typeof value !== 'string' || value.trim() === '') throw badRequest(`${label} must be a non-empty string`)
  return value
}

function assertTextList(values: readonly string[], label: string): readonly string[] {
  if (values.length === 0) throw badRequest(`${label} requires at least one value`)
  values.forEach((value) => { assertText(value, label) })
  return values
}

function renderJsonArray(values: readonly string[]): string {
  return `[${values.map((value) => JSON.stringify(value)).join(', ')}]`
}

function renderStep(step: BuilderStep): string {
  switch (step.type) {
    case 'run':
      return step.commands.map((command) => `RUN ${command}`).join('\n')
    case 'env':
      return Object.entries(step.entries).map(([key, value]) => `ENV ${key}=${JSON.stringify(value)}`).join('\n')
    case 'workdir':
      return `WORKDIR ${step.path}`
    case 'user':
      return `USER ${step.name}`
    case 'cmd':
      return `CMD ${renderJsonArray(step.args)}`
    case 'entrypoint':
      return `ENTRYPOINT ${renderJsonArray(step.args)}`
  }
}

function normalizeStepText(value: string, label: string): string {
  return assertText(value, label).trim()
}

/** `shlex.quote` (Python's rule, byte for byte): safe words pass through, everything else is single-quoted with `'` → `'"'"'`. */
function shellQuote(value: string): string {
  if (value === '') return "''"
  if (/^[A-Za-z0-9@%+=:,./_-]+$/.test(value)) return value
  return `'${value.replace(/'/g, `'"'"'`)}'`
}

export class ImageBuilder {
  private constructor(
    private readonly source: BuilderSource,
    private readonly steps: readonly BuilderStep[] = [],
  ) {}

  public static fromRegistry(ref: string): ImageBuilder {
    return new ImageBuilder({ type: 'registry', ref: assertText(ref, 'ImageBuilder.fromRegistry ref') })
  }

  public static fromDockerfile(dockerfile: string): ImageBuilder {
    return new ImageBuilder({ type: 'dockerfile', dockerfile: assertText(dockerfile, 'ImageBuilder.fromDockerfile dockerfile') })
  }

  public runCommands(...commands: readonly string[]): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'run', commands: assertTextList(commands, 'ImageBuilder.runCommands commands').map((command) => normalizeStepText(command, 'ImageBuilder.runCommands command')) }])
  }

  public pipInstall(...packages: readonly string[]): ImageBuilder {
    const resolved = assertTextList(packages, 'ImageBuilder.pipInstall packages').map((pkg) => normalizeStepText(pkg, 'ImageBuilder.pipInstall package'))
    // POSIX-quoted like Python's `shlex.quote`: a version specifier such as
    // `numpy>=1.26` is one shell word.
    return this.runCommands(`python -m pip install ${resolved.map(shellQuote).join(' ')}`)
  }

  public env(name: string, value: string): ImageBuilder
  public env(values: Record<string, string>): ImageBuilder
  public env(nameOrValues: string | Record<string, string>, value?: string): ImageBuilder {
    const entries = typeof nameOrValues === 'string'
      ? { [assertText(nameOrValues, 'ImageBuilder.env name')]: assertText(value ?? '', 'ImageBuilder.env value') }
      : Object.fromEntries(Object.entries(nameOrValues).map(([key, entryValue]) => [assertText(key, 'ImageBuilder.env key'), assertText(entryValue, 'ImageBuilder.env value')]))
    return new ImageBuilder(this.source, [...this.steps, { type: 'env', entries }])
  }

  public workdir(path: string): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'workdir', path: assertText(path, 'ImageBuilder.workdir path') }])
  }

  public user(name: string): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'user', name: assertText(name, 'ImageBuilder.user name') }])
  }

  public cmd(...args: readonly string[]): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'cmd', args: assertTextList(args, 'ImageBuilder.cmd args') }])
  }

  public entrypoint(...args: readonly string[]): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'entrypoint', args: assertTextList(args, 'ImageBuilder.entrypoint args') }])
  }

  public toDockerfile(): string {
    const base = this.source.type === 'registry' ? `FROM ${this.source.ref}` : this.source.dockerfile.trimEnd()
    const renderedSteps = this.steps.map(renderStep).filter((step) => step !== '')
    return [base, ...renderedSteps].join('\n').trimEnd() + '\n'
  }

  /**
   * The `POST /images` source this builder describes (parity with Python's
   * `ImageBuilder._request_source`): a bare registry ref with no steps
   * is a registry import (`source: {type: 'registry', ref}`) rather than a
   * Dockerfile build. This avoids an unnecessary build for a plain ref.
   * @internal
   */
  public toRequestSource(): { source: NonNullable<api.CreateImageRequest["source"]> } | { dockerfile: string } {
    // Decided on RENDERED steps: `env({})` is a step that renders to nothing,
    // and a Dockerfile that is only `FROM ref` is still an import.
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

export class Image implements ImageModel {
  public readonly id: string
  public readonly name: string | null
  public readonly anonymous: boolean
  public readonly availableIn: readonly string[]
  public readonly bytes?: number
  public readonly sandboxClass: string
  public readonly createdAt: string
  public readonly updatedAt: string
  public readonly error?: string
  public readonly expiresAt?: string
  public readonly layerCount?: number
  public readonly origin: ImageOrigin
  public readonly region: string
  public readonly retention: ArtifactRetention
  public readonly rawRetention: string
  public readonly source?: ImageSource
  public readonly status: ImageStatus
  public readonly formatVersion?: number
  public readonly formatOutdated: boolean
  public readonly platform: boolean
  public readonly default: boolean
  public readonly deprecated: boolean
  public readonly diskMib: number | null

  constructor(token: Internal, image: api.ImageListItem) {
    assertInternal(token, 'Image')
    this.id = image.id
    this.name = image.name ?? null
    this.anonymous = image.anonymous
    this.availableIn = [...image.region_ids]
    this.bytes = image.bytes ?? undefined
    this.sandboxClass = image.class
    this.createdAt = image.created_at
    this.updatedAt = image.updated_at
    this.error = image.error ?? undefined
    this.expiresAt = image.expires_at ?? undefined
    this.layerCount = image.layer_count ?? undefined
    this.origin = image.origin
    this.region = image.region_id
    this.rawRetention = image.retention
    this.retention = knownOrUnknown(image.retention, IMAGE_RETENTIONS)
    this.source = image.source === undefined || image.source === null ? undefined : imageSource(image.source)
    this.status = image.status
    this.formatVersion = image.format_version ?? undefined
    this.formatOutdated = image.format_outdated ?? false
    this.platform = image.platform === true
    this.default = image.default === true
    this.deprecated = image.deprecated === true
    this.diskMib = image.disk_mib ?? null
  }
}

export class Snapshot {
  public readonly id: string
  public readonly name: string
  public readonly sandboxClass: SnapshotClass
  public readonly rawSandboxClass: string
  public readonly kind: SnapshotKind
  public readonly rawKind: string
  public readonly availableIn: readonly string[]
  public readonly region: string
  public readonly status: string
  public readonly error?: string
  public readonly createdAt: string
  public readonly updatedAt: string
  public readonly bytes?: number
  public readonly memoryBytes?: number
  public readonly sandboxId?: string

  constructor(token: Internal, snapshot: api.Snapshot) {
    assertInternal(token, 'Snapshot')
    this.id = snapshot.id
    this.name = snapshot.name
    this.rawSandboxClass = snapshot.class
    this.sandboxClass = knownOrUnknown(snapshot.class, SNAPSHOT_CLASSES)
    this.rawKind = snapshot.kind
    this.kind = knownOrUnknown(snapshot.kind, SNAPSHOT_KINDS)
    this.availableIn = [...snapshot.region_ids]
    this.region = snapshot.region_id
    this.status = snapshot.status
    this.error = snapshot.error ?? undefined
    this.createdAt = snapshot.created_at
    this.updatedAt = snapshot.updated_at
    this.bytes = snapshot.bytes ?? undefined
    this.memoryBytes = snapshot.memory_bytes ?? undefined
    this.sandboxId = snapshot.sandbox_id ?? undefined
  }
}

function imageSource(source: NonNullable<api.ImageListItem['source']>): ImageSource {
  switch (source.type) {
    case 'registry':
      return { type: source.type, ref: source.ref }
    case 'dockerfile':
      return { type: source.type, contextObject: source.context_object, dockerfile: source.dockerfile }
    case 'named':
    case 'manifest':
      return { type: source.type, name: source.name }
  }
}

export function artifactRefOf(ref: api.ImageReference | api.SnapshotReference | null | undefined): { readonly id: string; readonly name: string | null } | null {
  if (!ref) return null
  return Object.freeze({ id: ref.id, name: ref.name ?? null })
}

export function buildImageRequest(builder: ImageBuilder, options: {
  readonly name?: string
  readonly idleExpirySeconds?: number
  readonly region?: string
  readonly cpuArch?: CpuArch
} = {}): api.CreateImageRequest {
  return {
    ...(options.name === undefined ? {} : { name: options.name }),
    ...(options.idleExpirySeconds === undefined ? {} : { idle_expiry_seconds: options.idleExpirySeconds }),
    ...(options.region === undefined ? {} : { region_id: options.region }),
    ...(options.cpuArch === undefined ? {} : { cpu_arch: options.cpuArch }),
    ...builder.toRequestSource(),
  }
}
