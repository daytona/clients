/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { selectRegion } from './region.js'
import { hasLoneSurrogate } from './pathSegment.js'
import { deepFrozen } from './frozen.js'
import type * as api from './gen/api-types.js'
import { ImageRetention as IMAGE_RETENTIONS, SnapshotClass as SNAPSHOT_CLASSES, SnapshotKind as SNAPSHOT_KINDS } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { badRequest, isPlainRecord } from './options.js'
import { hasSpace, isBlank, strip } from './pyText.js'
import type { ArtifactRetention, CpuArch, ImageOrigin, ImageSource, ImageStatus, SnapshotClass, SnapshotKind } from './types.js'
import type { ImageModel } from './imageModel.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import { arrayOf, checkResponse } from './responseFields.js'
import type { ResponseFields } from './responseFields.js'

// The contract fields an Image / Snapshot maps; a field the contract requires
// must be present, as in Python.
const IMAGE: ResponseFields = {
  id: 'string', anonymous: 'boolean', class: 'string', created_at: 'string', updated_at: 'string', origin: 'string',
  region_id: 'string', retention: 'string', status: 'string', format_version: 'integer', format_outdated: 'boolean',
  region_ids: arrayOf('string'), platform: 'boolean', default: 'boolean', deprecated: 'boolean', name: 'string?',
  bytes: 'integer?', disk_mib: 'integer?', error: 'string?', expires_at: 'string?', layer_count: 'integer?', source: 'object?',
}
const SNAPSHOT: ResponseFields = {
  id: 'string', name: 'string', class: 'string', kind: 'string', status: 'string', region_id: 'string',
  region_ids: arrayOf('string'), created_at: 'string', updated_at: 'string', bytes: 'integer?', memory_bytes: 'integer?',
  error: 'string?', sandbox_id: 'string?',
}

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

// One rule in both SDKs: every value must be a string that is not blank by
// Python's whitespace set, and text rendered raw into a Dockerfile line (the
// registry ref, the Dockerfile, commands, packages, env keys, the workdir) is
// stripped with that set; an env value, rendered as a JSON string, is kept as
// given. Python applies the same rule to those. `user()`, `cmd()` and
// `entrypoint()` are TypeScript-only for now: the user is stripped, and the
// cmd / entrypoint arguments, rendered as JSON strings, are kept as given.
function assertText(value: unknown, label: string): string {
  if (typeof value !== 'string' || isBlank(value)) throw badRequest(`${label} must be a non-empty string`)
  // A Dockerfile is UTF-8 text: a lone surrogate has no UTF-8 form, and the two
  // SDKs would render it differently. Python refuses it the same way.
  if (hasLoneSurrogate(value)) throw badRequest(`${label} must not contain a lone surrogate`)
  return value
}

function rawText(value: unknown, label: string): string {
  return strip(assertText(value, label))
}

function assertTextList(values: readonly string[], label: string): readonly string[] {
  if (!Array.isArray(values) || values.length === 0) throw badRequest(`${label} requires at least one value`)
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

/**
 * Text rendered raw into one Dockerfile line, stripped: a line break would end
 * the instruction and start another, so no control character (C0 or DEL) is
 * allowed. Python refuses the same values.
 */
function lineText(value: unknown, label: string): string {
  const text = rawText(value, label)
  if (/[\x00-\x1f\x7f]/.test(text)) throw badRequest(`${label} ${JSON.stringify(text)} must not contain a control character`)
  return text
}

/** `shlex.quote` (Python's rule, byte for byte): safe words pass through, everything else is single-quoted with `'` → `'"'"'`. */
function shellQuote(value: string): string {
  if (value === '') return "''"
  if (/^[A-Za-z0-9@%+=:,./_-]+$/.test(value)) return value
  return `'${value.replace(/'/g, `'"'"'`)}'`
}

/**
 * An env name, stripped: it renders as `ENV name=value`, so `=`, whitespace
 * (Python's set) or a control character (C0 or DEL) would change the
 * instruction, or add one. Python refuses the same names.
 */
function envName(value: string, label: string): string {
  const name = rawText(value, label)
  if (/[=\x00-\x1f\x7f]/.test(name) || hasSpace(name)) {
    throw badRequest(`${label} ${JSON.stringify(name)} must not contain '=', whitespace or a control character`)
  }
  return name
}

export class ImageBuilder {
  private constructor(
    private readonly source: BuilderSource,
    private readonly steps: readonly BuilderStep[] = [],
  ) {}

  public static fromRegistry(ref: string): ImageBuilder {
    return new ImageBuilder({ type: 'registry', ref: lineText(ref, 'ImageBuilder.fromRegistry ref') })
  }

  public static fromDockerfile(dockerfile: string): ImageBuilder {
    return new ImageBuilder({ type: 'dockerfile', dockerfile: rawText(dockerfile, 'ImageBuilder.fromDockerfile dockerfile') })
  }

  public runCommands(...commands: readonly string[]): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'run', commands: assertTextList(commands, 'ImageBuilder.runCommands commands').map((command) => lineText(command, 'ImageBuilder.runCommands command')) }])
  }

  public pipInstall(...packages: readonly string[]): ImageBuilder {
    const resolved = assertTextList(packages, 'ImageBuilder.pipInstall packages').map((pkg) => lineText(pkg, 'ImageBuilder.pipInstall package'))
    // POSIX-quoted like Python's `shlex.quote`: a version specifier such as
    // `numpy>=1.26` is one shell word.
    return this.runCommands(`python -m pip install ${resolved.map(shellQuote).join(' ')}`)
  }

  public env(name: string, value: string): ImageBuilder
  public env(values: Record<string, string>): ImageBuilder
  public env(nameOrValues: string | Record<string, string>, value?: string): ImageBuilder {
    const given: unknown = nameOrValues
    // A plain object is the counterpart of Python's mapping; a Map, a Date or a
    // class instance would read as empty and drop the step, so it is refused.
    if (typeof given !== 'string' && !isPlainRecord(given)) {
      throw badRequest('ImageBuilder.env takes a name and a value, or a mapping of names to values')
    }
    const pairs: ReadonlyArray<readonly [string, string]> = typeof nameOrValues === 'string'
      ? [[envName(nameOrValues, 'ImageBuilder.env name'), assertText(value, 'ImageBuilder.env value')]]
      : Object.entries(nameOrValues).map(([key, entryValue]) => [envName(key, 'ImageBuilder.env key'), assertText(entryValue, 'ImageBuilder.env value')] as const)
    // Keys are stripped: two that collide afterwards would silently keep one.
    const seen = new Set<string>()
    for (const [key] of pairs) {
      if (seen.has(key)) throw badRequest(`ImageBuilder.env key ${JSON.stringify(key)} appears more than once after stripping`)
      seen.add(key)
    }
    const entries = Object.fromEntries(pairs)
    return new ImageBuilder(this.source, [...this.steps, { type: 'env', entries }])
  }

  public workdir(path: string): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'workdir', path: lineText(path, 'ImageBuilder.workdir path') }])
  }

  public user(name: string): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'user', name: lineText(name, 'ImageBuilder.user name') }])
  }

  public cmd(...args: readonly string[]): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'cmd', args: assertTextList(args, 'ImageBuilder.cmd args') }])
  }

  public entrypoint(...args: readonly string[]): ImageBuilder {
    return new ImageBuilder(this.source, [...this.steps, { type: 'entrypoint', args: assertTextList(args, 'ImageBuilder.entrypoint args') }])
  }

  public toDockerfile(): string {
    // Python's whitespace set, as Python's `rstrip()` (JavaScript's `trimEnd()` also strips U+FEFF).
    const base = this.source.type === 'registry' ? `FROM ${this.source.ref}` : strip(this.source.dockerfile)
    const renderedSteps = this.steps.map(renderStep).filter((step) => step !== '')
    return strip([base, ...renderedSteps].join('\n')) + '\n'
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

  /** @internal */
  constructor(token: Internal, image: api.ImageListItem)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, image: api.ImageListItem) {
    assertInternal(token, 'Image')
    checkResponse<api.ImageListItem>(image, IMAGE, 'image')
    this.id = image.id
    this.name = image.name ?? null
    this.anonymous = image.anonymous
    this.availableIn = Object.freeze([...image.region_ids])
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
    this.source = image.source === undefined || image.source === null ? undefined : deepFrozen(imageSource(image.source))
    this.status = image.status
    this.formatVersion = image.format_version
    this.formatOutdated = image.format_outdated
    this.platform = image.platform
    this.default = image.default
    this.deprecated = image.deprecated
    this.diskMib = image.disk_mib ?? null
    // An SDK-owned data model with no live state: frozen at run time.
    Object.freeze(this)
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

  /** @internal */
  constructor(token: Internal, snapshot: api.Snapshot)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, snapshot: api.Snapshot) {
    assertInternal(token, 'Snapshot')
    checkResponse<api.Snapshot>(snapshot, SNAPSHOT, 'snapshot')
    this.id = snapshot.id
    this.name = snapshot.name
    this.rawSandboxClass = snapshot.class
    this.sandboxClass = knownOrUnknown(snapshot.class, SNAPSHOT_CLASSES)
    this.rawKind = snapshot.kind
    this.kind = knownOrUnknown(snapshot.kind, SNAPSHOT_KINDS)
    this.availableIn = Object.freeze([...snapshot.region_ids])
    this.region = snapshot.region_id
    this.status = snapshot.status
    this.error = snapshot.error ?? undefined
    this.createdAt = snapshot.created_at
    this.updatedAt = snapshot.updated_at
    this.bytes = snapshot.bytes ?? undefined
    this.memoryBytes = snapshot.memory_bytes ?? undefined
    this.sandboxId = snapshot.sandbox_id ?? undefined
    Object.freeze(this)
  }
}

// The source is an exact-discriminator union: each variant's fields are required.
const SOURCE_FIELDS: Readonly<Record<string, ResponseFields>> = {
  registry: { ref: 'string' },
  dockerfile: { context_object: 'string', dockerfile: 'string' },
  named: { name: 'string' },
  manifest: { name: 'string' },
}

function imageSource(source: NonNullable<api.ImageListItem['source']>): ImageSource {
  const type: unknown = Reflect.get(source, 'type')
  const fields = typeof type === 'string' && Object.prototype.hasOwnProperty.call(SOURCE_FIELDS, type) ? SOURCE_FIELDS[type] : undefined
  if (fields === undefined) throw badRequest('image response has a source.type that is not a known source type')
  checkResponse(source, fields, 'image source')
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

/** @internal */
export function artifactRefOf(ref: api.ImageReference | api.SnapshotReference | null | undefined): { readonly id: string; readonly name: string | null } | null {
  if (!ref) return null
  return Object.freeze({ id: ref.id, name: ref.name ?? null })
}

/**
 * The image options of a build, pull or capture, each checked before any
 * request, as in Python; `null` / `undefined` are omitted.
 * @internal
 */
export function imageOptionsBody(options: { readonly name?: unknown; readonly idleExpirySeconds?: unknown; readonly region?: unknown; readonly cpuArch?: unknown }): { name?: string; idle_expiry_seconds?: number; region_id?: string; cpu_arch?: string } {
  const body: { name?: string; idle_expiry_seconds?: number; region_id?: string; cpu_arch?: string } = {}
  for (const [key, wire] of [['name', 'name'], ['region', 'region_id'], ['cpuArch', 'cpu_arch']] as const) {
    const value: unknown = options[key]
    if (key === 'region') {
      // The region rule of create: blank or local selects the default region
      // (no region_id); any other value follows the header rule and is sent as given.
      const selected = selectRegion(value)
      if (selected !== undefined) body.region_id = selected
      continue
    }
    if (value === undefined || value === null) continue
    if (typeof value !== 'string') throw badRequest(`${key} must be a string`)
    // A blank name names nothing: refused, as promote() refuses it.
    if (key === 'name' && isBlank(value)) throw badRequest('name must be a non-empty string')
    // The contract's architectures only: SDK inputs are closed.
    if (key === 'cpuArch' && value !== 'amd64' && value !== 'arm64') throw badRequest('cpuArch must be "amd64" or "arm64"')
    body[wire] = value
  }
  const idle: unknown = options.idleExpirySeconds
  if (idle !== undefined && idle !== null) {
    // The contract: -1 (the maximum) or 1 and up, an int64 both SDKs carry
    // exactly; 0 and anything below -1 are refused, and the server holds the maximum.
    if (typeof idle !== 'number' || !Number.isSafeInteger(idle) || !(idle === -1 || idle >= 1)) {
      throw badRequest('idleExpirySeconds must be -1 (the maximum) or a positive integer number of seconds')
    }
    body.idle_expiry_seconds = idle
  }
  return body
}

/** @internal */
export function buildImageRequest(builder: ImageBuilder, options: {
  readonly name?: string
  readonly idleExpirySeconds?: number
  readonly region?: string
  readonly cpuArch?: CpuArch
} = {}): api.CreateImageRequest {
  return {
    ...imageOptionsBody(options),
    ...builder.toRequestSource(),
  }
}
