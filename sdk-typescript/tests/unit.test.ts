/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createHash } from 'node:crypto'
import { readFileSync, readdirSync } from 'node:fs'
import * as nodeFsPromises from 'node:fs/promises'
import { access, mkdtemp, readFile, readlink, readdir, rm, symlink, writeFile } from 'node:fs/promises'
import { join, relative, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { createServer, type Server, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'
import * as otelApi from '@opentelemetry/api'
import { trace } from '@opentelemetry/api'
import { InMemorySpanExporter, SimpleSpanProcessor, TracerProvider as SdkTracerProvider } from '@opentelemetry/sdk-trace'
import * as ts from 'typescript'
import { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import * as publicSdk from '../src/v2/index'
import type { components as ApiComponents } from '../src/v2/gen/api'
import type { components as ToolboxComponents } from '../src/v2/gen/toolbox'
import { Daytona, NAME_LABEL, hasSearchFilters, idempotencyKeyForName, searchQueryParams, searchTimestamp } from '../src/v2/Daytona'
import { LIST_VALUE_LIST, SINGLE_VALUE_SEARCH, searchConsumerCalls } from './search-options.consumer'
import { Image as ImageHandle, ImageBuilder, Snapshot } from '../src/v2/Image'
import { ImagesService } from '../src/v2/Images'
import { ComputerUseService, type ComputerUseTransport } from '../src/v2/ComputerUse'
import {
  mapComputerUseLifecycleResponse,
  mapComputerUseRestartResponse,
  mapComputerUseStatusResponse,
} from '../src/v2/ComputerUseMappers'
import { FileSystemService, FilesystemEventType } from '../src/v2/FileSystem'
import { GitService } from '../src/v2/Git'
import { InterpreterService } from '../src/v2/Interpreter'
import { LspService } from '../src/v2/Lsp'
import { PortsService } from '../src/v2/Ports'
import { SecretService } from '../src/v2/Secret'
import { VolumeService } from '../src/v2/Volume'
import { ProcessHandle, ProcessService, PtyService, TIMED_OUT_LOG_PAGE_LIMIT, WAIT_READ_SLACK_MS, startProcess } from '../src/v2/Process'
import { Sandbox } from '../src/v2/Sandbox'
import type { SandboxConnectOptionsFromCreate } from '../src/v2/Sandbox'
import { DOCKER_WAIT_DEADLINE_GRACE_MS, DockerService } from '../src/v2/Docker'
import {
  API_CODE_EXTRAS,
  API_ERROR_CODES,
  CODE_STATUS,
  DAEMON_CODE_EXTRAS,
  DAEMON_ERROR_CODES,
  GENERIC_ERROR_CODES,
  PROXY_CODE_EXTRAS,
  PROXY_ERROR_CODES,
  ROUTER_CODE_EXTRAS,
  ROUTER_ERROR_CODES,
  type ErrorCode,
} from '../src/v2/codes'
import type {
  ComputerUseDesktopFailure,
  ComputerUseDesktopFailureCode,
  ComputerUseDesktopState,
  AuditActionList,
  ProjectApiKeyWithContext,
  ProjectBudget,
  OrganizationLimits,
  ComputerUseRestartResponse,
  ListEndpointsOptions,
  ListSecretsOptions,
  ListVolumesOptions,
  Volume,
  SandboxInfo,
  ProcessDto,
  SandboxState,
  SandboxWaitState,
  WaitForStateOptions,
  ComputerUseStatusResponse,
  ProcessHandleJSON,
  ProcessState,
} from '../src/v2/index'
import {
  AddonUnavailableError,
  ConflictError,
  FeatureNotEnabledError,
  DesktopNotReadyError,
  NotSupportedError,
  ServiceUnavailableError,
  TransferCancelledError,
} from '../src/v2/index'
import {
  AtomicUploadUnsupportedError,
  BadRequestError,
  BillingDisabledError,
  ConnectionError,
  CODE_ERROR_CLASS,
  AuthenticationError,
  DaytonaError,
  createAxiosDaytonaError,
  createDaytonaError,
  CursorExpiredError,
  errorClassFromStatusCode,
  errorFromFetchResponse,
  errorFromEnvelopeBody,
  errorOptionsFromEnvelope,
  InternalServerError,
  ImageBuildFailedError,
  ImageClassMismatchError,
  ImageNameReservedError,
  ImageNameTakenError,
  ImageNotFoundError,
  ImageNotReadyError,
  ImageFormatOutdatedError,
  IdleOutOfRangeError,
  KernelUnavailableError,
  LimitExceededError,
  NameConflictError,
  NotSupportedForClassError,
  NotFoundError,
  OrgSuspendedError,
  PayloadTooLargeError,
  ProjectBudgetExceededError,
  RateLimitError,
  RateLimitedError,
  SessionNotFoundError,
  SnapshotClassMismatchError,
  SnapshotNotReadyError,
  SnapshotNotFoundError,
  statusForCode,
  ClientTimeoutError,
  UnsupportedOperationError,
  SandboxCreateConnectionError,
  SandboxCreateRequestTimeoutError,
  SandboxCreateTimeoutError,
  SandboxTerminalStateError,
  SandboxNotRunningError,
  RequestTimeoutError,
  SearchNotConfiguredError,
  SearchUnavailableError,
} from '../src/v2/errors/DaytonaError'
import { routeTemplate, HttpTransport, sandboxState, sseMessages, gbToMib, mibToGb, boundedRequestTimeout, parseSandboxLifecycle, lifecycleBody, assertLifecycleClassSupport, authHeaders, LIFECYCLE_MAX_SECS, createPlatformTokenProvider } from '../src/v2/http'
import { WaitHub, targetPredicate, type WebSocketFactory } from '../src/v2/events'
import { WAIT_FOR_STATE_MIN_INTERVAL_MS } from '../src/v2/waitConstants'
import { WAIT_TRANSPORT_RETRY_LIMIT } from '../src/v2/ProcessWire'
import { SDK_VERSION } from '../src/version'
import { DISABLED } from '../src/v2/types'
import type { CreateSandboxOptions, LifecyclePolicyOptions, Resources, SandboxHandleJSON, SearchResult, SandboxDto } from '../src/v2/types'
import type { GitConfigScope, GitRemote } from '../src/v2'
import { lazySandbox } from './lazySandbox'

const SIGNED_FILE_TEST_SIGNATURE = 'v1_c2VydmVyLW1pbnRlZC1zaWduYXR1cmU'
const FIXED_TEST_EPOCH_MS = 1_725_984_000_000

function sandboxJwt(exp: number, aud = 'test-audience'): string {
  const header = Buffer.from(JSON.stringify({ alg: 'ES256', typ: 'at+jwt' })).toString('base64url')
  const payload = Buffer.from(JSON.stringify({ exp, aud })).toString('base64url')
  return `${header}.${payload}.signature`
}

type CreateFixtureOptions = Omit<CreateSandboxOptions, 'waitSeconds'> & {
  readonly image?: string | ImageBuilder
  readonly snapshot?: string
  readonly waitSeconds?: number
  readonly timeout?: number
  readonly lifecycle?: LifecyclePolicyOptions | null
}

const Image = Object.assign(ImageHandle, {
  base(image: string): string {
    if (image.trim() === '') throw new Error('non-empty image name or registry reference')
    return image
  },
  fromDockerfile(dockerfile: string): ImageBuilder {
    if (dockerfile.trim() === '') throw new Error('non-empty Dockerfile')
    return ImageBuilder.fromDockerfile(dockerfile)
  },
})


function createFixture(client: Daytona, options: CreateFixtureOptions = {}): Promise<Sandbox> {
  const { image, snapshot, timeout, resources, lifecycle, ...rest } = options
  const waitSeconds = timeout ?? options.waitSeconds
  const createOptions = lifecycle === undefined ? rest : { ...rest, lifecyclePolicy: lifecycle }
  const baseOptions = waitSeconds === undefined ? createOptions : { ...createOptions, waitSeconds }
  if (snapshot !== undefined) {
    return client.createFromSnapshot(snapshot, baseOptions)
  }
  if (image instanceof ImageBuilder) return client.createFromBuild(image, { ...baseOptions, resources })
  if (typeof image === 'string') {
    return client.create(image, { ...baseOptions, resources })
  }
  throw new TypeError('daytona.create() only accepts an image name or img_… id')
}

function newClient(): Daytona {
  return new Daytona({ apiKey: 'key' })
}

function withApiUrl<T>(apiUrl: string, callback: () => T): T {
  const previous = process.env.DAYTONA_V2_API_URL
  process.env.DAYTONA_V2_API_URL = apiUrl
  try {
    return callback()
  } finally {
    if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = previous
  }
}

function sandboxDetail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'sbx',
    name: 'sandbox',
    status: 'running',
    class: 'linux',
    cpu: 1,
    cores_max: 1,
    memory_mib: 1024,
    disk_mib: 1024,
    persistent: false,
    toolbox_proxy_url: 'http://toolbox',
    image: { id: 'img_123', name: 'base' },
    snapshot: null,
    ...overrides,
  }
}

class HubTestWebSocket {
  readyState = 1

  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null

  readonly sent: string[] = []
  private closed = false

  send(data: string): void {
    if (!this.closed) this.sent.push(data)
  }

  close(code = 1000): void {
    if (this.closed) return
    this.closed = true
    this.readyState = 3
    this.onclose?.({ code, reason: '', wasClean: code === 1000 } as CloseEvent)
  }

  inject(frame: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent)
  }
}

function imageResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'img_123',
    name: 'base',
    anonymous: false,
    region_ids: ['region-a'],
    class: 'linux',
    created_at: '2026-09-30T00:00:00Z',
    updated_at: '2026-09-30T02:00:00Z',
    origin: 'registry',
    region_id: 'region-a',
    retention: 'permanent',
    status: 'ready',
    ...overrides,
  }
}

function snapshotResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'snap_123',
    name: 'after-setup',
    class: 'linux',
    kind: 'full',
    region_ids: ['region-a'],
    region_id: 'region-a',
    status: 'ready',
    created_at: '2026-09-30T00:00:00Z',
    updated_at: '2026-09-30T02:00:00Z',
    ...overrides,
  }
}

type HttpMethod = 'DELETE' | 'GET' | 'PATCH' | 'POST' | 'PUT'

interface OpenApiSpec {
  readonly paths: Record<string, Partial<Record<Lowercase<HttpMethod>, unknown>>>
}

interface ContractOperation {
  readonly contract: 'api' | 'toolbox'
  readonly method: HttpMethod
  readonly path: string
}

interface RouteLiteralUse {
  readonly file: string
  readonly line: number
  readonly method: HttpMethod
  readonly path: string
  readonly raw: string
}

interface LocalFileWriteContext {
  write(buffer: Uint8Array): Promise<{ readonly bytesWritten: number }>
}

type RequestInitWithBodyFactory = RequestInit & {
  readonly bodyFactory?: () => BodyInit | Promise<BodyInit>
}

const HTTP_METHODS = {
  delete: 'DELETE',
  fetch: 'GET',
  get: 'GET',
  getBytes: 'GET',
  patch: 'PATCH',
  post: 'POST',
  postRaw: 'POST',
  put: 'PUT',
} as const satisfies Record<string, HttpMethod>

const SDK_TOOLBOX_ROUTE_ALLOWLIST = {
  'GET /info': 'No public SDK method exposes this metadata endpoint.',
  'GET /sessions/{id}': 'Session handles expose operations without a separate metadata lookup.',
  'GET /version': 'No public SDK method exposes this version endpoint.',
} as const satisfies Record<string, string>

const CONTRACT_ROUTE_PREFIXES = [
  '/computeruse',
  '/docker',
  '/events',
  '/files',
  '/git',
  '/images',
  '/info',
  '/lsp',
  '/mac-catalog',
  '/mac-models',
  '/port',
  '/processes',
  '/reservations',
  '/secrets',
  '/sessions',
  '/sandboxes',
  '/snapshots',
  '/system',
  '/version',
  '/volumes',
] as const

function readContractOperations(contract: 'api' | 'toolbox'): readonly ContractOperation[] {
  const path = resolve(__dirname, `../../contract/${contract}-openapi.json`)
  const spec = JSON.parse(readFileSync(path, 'utf8')) as OpenApiSpec
  return Object.entries(spec.paths).flatMap(([routePath, operations]) =>
    Object.keys(operations).flatMap((method) => {
      const verb = method.toUpperCase()
      if (!isHttpMethod(verb)) return []
      return [{ contract, method: verb, path: routePath }]
    }),
  )
}

function isHttpMethod(value: string): value is HttpMethod {
  return value === 'DELETE' || value === 'GET' || value === 'PATCH' || value === 'POST' || value === 'PUT'
}

function collectSdkRouteLiterals(): readonly RouteLiteralUse[] {
  const sourceRoot = resolve(__dirname, '../src')
  return listTypeScriptSourceFiles(sourceRoot).flatMap((filePath) => {
      if (filePath.includes('/src/v2/gen/')) return []
    // http.ts IS the transport: its `this.http.get(assertRelativeRequestPath(path))`
    // forwards a caller's path, it does not name a route.
    if (filePath.endsWith('/src/v2/http.ts')) return []
    const sourceFile = ts.createSourceFile(filePath, readFileSync(filePath, 'utf8'), ts.ScriptTarget.Latest, true)
    return collectRoutesFromSourceFile(sourceFile, filePath)
  })
}

function listTypeScriptSourceFiles(root: string): readonly string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const filePath = join(root, entry.name)
    if (entry.isDirectory()) return listTypeScriptSourceFiles(filePath)
    return entry.isFile() && filePath.endsWith('.ts') ? [filePath] : []
  })
}

function collectRoutesFromSourceFile(sourceFile: ts.SourceFile, filePath: string): readonly RouteLiteralUse[] {
  const routes: RouteLiteralUse[] = []

  const visit = (node: ts.Node, scope: Map<string, ts.Expression>): void => {
    const nextScope = createsScope(node) ? new Map(scope) : scope
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      nextScope.set(node.name.text, node.initializer)
    }

    if (ts.isCallExpression(node)) {
      const httpRouteUses = collectHttpRouteUses(node, scope, sourceFile, filePath)
      if (httpRouteUses.length > 0) routes.push(...httpRouteUses)
    }

    if (ts.isNewExpression(node)) {
      const webSocketRouteUses = collectWebSocketRouteUses(node, scope, sourceFile, filePath)
      if (webSocketRouteUses.length > 0) routes.push(...webSocketRouteUses)
    }

    ts.forEachChild(node, (child) => visit(child, nextScope))
  }

  visit(sourceFile, new Map())
  return routes
}

function createsScope(node: ts.Node): boolean {
  return ts.isSourceFile(node) || ts.isBlock(node) || ts.isFunctionLike(node)
}

function collectHttpRouteUses(
  node: ts.CallExpression,
  scope: ReadonlyMap<string, ts.Expression>,
  sourceFile: ts.SourceFile,
  filePath: string,
): readonly RouteLiteralUse[] {
  // Build-log helpers forward discovery paths; the dynamic proxy URL is not
  // a hard-coded API route. Collect the literal at every forwarding call site.
  const forwarding = ts.isIdentifier(node.expression) && ['followBuildLogs', 'readBuildLogs', 'discovery'].includes(node.expression.text)
  if (!forwarding && !ts.isPropertyAccessExpression(node.expression)) return []
  const method = forwarding ? 'GET' : httpMethodForCall(node, scope)
  const arg = node.arguments[forwarding ? 1 : 0]
  if (!method || !arg) return []
  if (ts.isIdentifier(arg) && arg.text === 'path') {
    let parent: ts.Node | undefined = node.parent
    while (parent && !ts.isFunctionDeclaration(parent) && !ts.isMethodDeclaration(parent)) parent = parent.parent
    if (parent && ((ts.isFunctionDeclaration(parent) && parent.name && ['followBuildLogs', 'readBuildLogs', 'discovery'].includes(parent.name.text)) || (ts.isMethodDeclaration(parent) && parent.name.getText(sourceFile) === 'createSandbox'))) return []
  }
  const variants = routeVariants(arg, scope, sourceFile)
  const paths = variants.map((raw) => normalizeSdkRoute(raw)).filter((path): path is string => path !== undefined)
  if ((forwarding || (ts.isPropertyAccessExpression(node.expression) && isTransportCall(node.expression))) && paths.length === 0) {
    // A route the collector cannot resolve is a route the drift test never
    // checks. Fail here, at the call site, rather than let coverage rot
    // silently — teach `routeVariants`/`knownRouteHelperVariants` the new
    // shape instead of weakening this check.
    const where = `${relative(resolve(__dirname, '..'), filePath)}:${sourceFile.getLineAndCharacterOfPosition(arg.getStart(sourceFile)).line + 1}`
    throw new Error(`SDK route drift: cannot resolve the route of ${arg.getText(sourceFile)} at ${where} (${method}); raw variants: ${JSON.stringify(variants)}`)
  }
  return paths
    .map((path) => ({
      file: relative(resolve(__dirname, '..'), filePath),
      line: sourceFile.getLineAndCharacterOfPosition(arg.getStart(sourceFile)).line + 1,
      method,
      path,
      raw: arg.getText(sourceFile),
    }))
}

/**
 * `transport.get(...)`, `this.transport.post(...)`, `this.api.get(...)`,
 * `this.requireApiForPresignedUrls().post(...)` — the receivers that perform
 * HTTP. Other objects with a `.get`/`.delete` (Maps, headers) are not routes.
 */
function isTransportCall(expression: ts.PropertyAccessExpression): boolean {
  const receiver = unwrapRouteExpression(expression.expression)
  const receiverText = receiver.getText()
  return /(^|\.)(transport|api|toolbox|http|client)$/.test(receiverText) || /^this\.require[A-Za-z]*\(\)$/.test(receiverText)
}

function httpMethodFor(name: string): HttpMethod | undefined {
  return Object.prototype.hasOwnProperty.call(HTTP_METHODS, name)
    ? HTTP_METHODS[name as keyof typeof HTTP_METHODS]
    : undefined
}

function httpMethodForCall(
  node: ts.CallExpression,
  scope: ReadonlyMap<string, ts.Expression>,
): HttpMethod | undefined {
  if (!ts.isPropertyAccessExpression(node.expression)) return undefined
  const defaultMethod = httpMethodFor(node.expression.name.text)
  if (node.expression.name.text !== 'fetch') {
    return defaultMethod
  }
  const rawInit = node.arguments[1]
  const init = rawInit && ts.isIdentifier(rawInit)
    ? scope.get(rawInit.text) ?? rawInit
    : rawInit
  if (!init || !ts.isObjectLiteralExpression(init)) {
    return defaultMethod
  }
  const methodProperty = init.properties.find((property) =>
    ts.isPropertyAssignment(property)
      && identifierText(property.name) === 'method'
      && (ts.isStringLiteral(property.initializer) || ts.isNoSubstitutionTemplateLiteral(property.initializer)),
  )
  if (!methodProperty || !ts.isPropertyAssignment(methodProperty)) {
    return defaultMethod
  }
  const initializer = methodProperty.initializer
  if (!ts.isStringLiteral(initializer) && !ts.isNoSubstitutionTemplateLiteral(initializer)) {
    return defaultMethod
  }
  const value = initializer.text.toUpperCase()
  return isHttpMethod(value) ? value : defaultMethod
}

function identifierText(name: ts.PropertyName): string | undefined {
  if (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNoSubstitutionTemplateLiteral(name)) {
    return name.text
  }
  return undefined
}

function collectWebSocketRouteUses(
  node: ts.NewExpression,
  scope: ReadonlyMap<string, ts.Expression>,
  sourceFile: ts.SourceFile,
  filePath: string,
): readonly RouteLiteralUse[] {
  if (!ts.isIdentifier(node.expression) || node.expression.text !== 'URL') return []
  const arg = node.arguments?.[0]
  if (!arg) return []
  return routeVariants(arg, scope, sourceFile)
    .map((raw) => normalizeSdkRoute(raw))
    .filter((path): path is string => path !== undefined)
    .map((path) => ({
      file: relative(resolve(__dirname, '..'), filePath),
      line: sourceFile.getLineAndCharacterOfPosition(arg.getStart(sourceFile)).line + 1,
      method: 'GET',
      path,
      raw: arg.getText(sourceFile),
    }))
}

/**
 * `await x`, `x as T`, `x!`, `(x)` and `x satisfies T` do not change which
 * route `x` names. Every resolver below looks through them first, so a
 * `const path = await this.portPath()` resolves to the call.
 */
function unwrapRouteExpression(expression: ts.Expression): ts.Expression {
  let current = expression
  for (;;) {
    if (ts.isAwaitExpression(current) || ts.isParenthesizedExpression(current) || ts.isNonNullExpression(current)) {
      current = current.expression
    } else if (ts.isAsExpression(current) || ts.isSatisfiesExpression(current) || ts.isTypeAssertionExpression(current)) {
      current = current.expression
    } else {
      return current
    }
  }
}

function routeVariants(
  rawExpression: ts.Expression,
  scope: ReadonlyMap<string, ts.Expression>,
  sourceFile: ts.SourceFile,
  seen: ReadonlySet<string> = new Set(),
): readonly string[] {
  const expression = unwrapRouteExpression(rawExpression)
  if (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression)) {
    return [expression.text]
  }
  if (ts.isTemplateExpression(expression)) {
    return combineRouteParts([
      [expression.head.text],
      ...expression.templateSpans.flatMap((span) => {
        const variants = routeExpressionValue(span.expression, scope, sourceFile, seen)
        return [variants, [span.literal.text]]
      }),
    ])
  }
  if (ts.isIdentifier(expression)) {
    const key = `${sourceFile.fileName}:${expression.text}`
    if (seen.has(key)) return []
    const resolved = scope.get(expression.text)
    return resolved ? routeVariants(resolved, scope, sourceFile, new Set([...seen, key])) : []
  }
  if (ts.isConditionalExpression(expression)) {
    return uniqueStrings([...routeVariants(expression.whenTrue, scope, sourceFile, seen), ...routeVariants(expression.whenFalse, scope, sourceFile, seen)])
  }
  if (ts.isBinaryExpression(expression) && expression.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    return combineVariants(routeVariants(expression.left, scope, sourceFile, seen), routeVariants(expression.right, scope, sourceFile, seen))
  }
  if (ts.isCallExpression(expression)) {
    const helperVariants = knownRouteHelperVariants(expression)
    if (helperVariants.length > 0) return helperVariants
  }
  return []
}

function routeExpressionValue(
  rawExpression: ts.Expression,
  scope: ReadonlyMap<string, ts.Expression>,
  sourceFile: ts.SourceFile,
  seen: ReadonlySet<string>,
): readonly string[] {
  const expression = unwrapRouteExpression(rawExpression)
  if (ts.isCallExpression(expression) && ts.isIdentifier(expression.expression) && expression.expression.text === 'encodeQuery') {
    return ['{query}']
  }
  if (ts.isIdentifier(expression)) {
    const resolved = scope.get(expression.text)
    if (resolved) {
      const resolvedHelperVariants = knownRouteHelperVariants(unwrapRouteExpression(resolved))
      if (resolvedHelperVariants.length > 0) return resolvedHelperVariants
    }
  }
  const helperVariants = knownRouteHelperVariants(expression)
  if (helperVariants.length > 0) return helperVariants
  const variants = routeVariants(expression, scope, sourceFile, seen)
  return variants.length > 0 ? variants : ['{param}']
}

function knownRouteHelperVariants(rawExpression: ts.Expression): readonly string[] {
  const expression = unwrapRouteExpression(rawExpression)
  if (!ts.isCallExpression(expression)) return []
  if (ts.isPropertyAccessExpression(expression.expression) && expression.expression.name.text === 'portsPath') {
    return ['/sandboxes/{param}/ports']
  }
  if (ts.isPropertyAccessExpression(expression.expression) && expression.expression.name.text === 'portPath') {
    return ['/sandboxes/{param}/ports/{param}']
  }
  return []
}

function combineRouteParts(parts: readonly (readonly string[])[]): readonly string[] {
  return parts.reduce<readonly string[]>((acc, next) => combineVariants(acc, next), [''])
}

function combineVariants(left: readonly string[], right: readonly string[]): readonly string[] {
  return uniqueStrings(left.flatMap((lhs) => right.map((rhs) => `${lhs}${rhs}`)))
}

function uniqueStrings(values: readonly string[]): readonly string[] {
  return [...new Set(values)]
}

function normalizeSdkRoute(raw: string): string | undefined {
  const strippedQuery = raw.split('?')[0] ?? raw
  const prefix = CONTRACT_ROUTE_PREFIXES
    .map((candidate) => ({ candidate, index: strippedQuery.indexOf(candidate) }))
    .filter((item) => item.index >= 0)
    .sort((left, right) => left.index - right.index)[0]
  const path = prefix ? strippedQuery.slice(prefix.index) : strippedQuery
  if (!path.startsWith('/')) return undefined
  const segments = path.split('/').map((segment, index) => {
    if (index === 0 || segment === '' || segment === '{param}') return segment
    if (segment.includes('{query}')) return segment.split('{query}')[0] ?? ''
    if (segment.includes('{param}')) {
      const literalPrefix = segment.split('{param}')[0] ?? ''
      return literalPrefix || '{param}'
    }
    return segment
  })
  const normalized = segments.join('/').replace(/\/+$|\{query\}.*$/g, '')
  return normalized || '/'
}

function routeMatchesSpec(routePath: string, specPath: string): boolean {
  const routeSegments = pathSegments(routePath)
  const specSegments = pathSegments(specPath)
  if (routeSegments.length !== specSegments.length) return false
  return routeSegments.every((segment, index) => {
    const specSegment = specSegments[index]
    if (specSegment === undefined) return false
    if (segment === '{param}') return isSpecParam(specSegment)
    if (isSpecParam(specSegment)) return true
    return segment === specSegment
  })
}

/** `routeMatchesSpec` without the wildcard: every literal segment must be
 * equal, and only a route `{param}` may stand in for a spec parameter. */
function routeMatchesSpecExactly(routePath: string, specPath: string): boolean {
  const routeSegments = pathSegments(routePath)
  const specSegments = pathSegments(specPath)
  if (routeSegments.length !== specSegments.length) return false
  return routeSegments.every((segment, index) => {
    const specSegment = specSegments[index]
    if (specSegment === undefined) return false
    if (segment === '{param}') return isSpecParam(specSegment)
    return segment === specSegment
  })
}

function pathSegments(path: string): readonly string[] {
  return path.split('/').filter((segment) => segment.length > 0)
}

function isSpecParam(segment: string): boolean {
  return /^\{[^/]+\}$/.test(segment)
}

function nearestSpecPaths(method: HttpMethod, routePath: string, operations: readonly ContractOperation[]): readonly string[] {
  const routeSegments = pathSegments(routePath)
  return operations
    .filter((operation) => operation.method === method)
    .map((operation) => ({
      path: operation.path,
      score: similarityScore(routeSegments, pathSegments(operation.path)),
    }))
    .sort((left, right) => right.score - left.score || left.path.localeCompare(right.path))
    .slice(0, 3)
    .map((item) => item.path)
}

function similarityScore(routeSegments: readonly string[], specSegments: readonly string[]): number {
  const sharedLiterals = routeSegments.reduce((count, segment, index) => {
    const other = specSegments[index]
    if (other === undefined) return count
    return !isSpecParam(segment) && !isSpecParam(other) && segment === other ? count + 3 : count
  }, 0)
  return sharedLiterals - Math.abs(routeSegments.length - specSegments.length)
}

beforeEach(() => {
  jest.useFakeTimers({ now: FIXED_TEST_EPOCH_MS })
})

afterEach(() => {
  jest.restoreAllMocks()
  delete (globalThis as typeof globalThis & { __DAYTONA_OTEL_IMPORT__?: () => Promise<never> }).__DAYTONA_OTEL_IMPORT__
  trace.disable()
  jest.useRealTimers()
})

describe('V2 model', () => {
  it('withApiUrl overrides and restores DAYTONA_V2_API_URL', () => {
    const previous = process.env.DAYTONA_V2_API_URL
    process.env.DAYTONA_V2_API_URL = 'https://ambient-v2.example'
    try {
      const client = withApiUrl('https://api.example.com', newClient)
      expect(clientInstance(client).defaults.baseURL).toBe('https://api.example.com')
      expect(process.env.DAYTONA_V2_API_URL).toBe('https://ambient-v2.example')
    } finally {
      if (previous === undefined) delete process.env.DAYTONA_V2_API_URL
      else process.env.DAYTONA_V2_API_URL = previous
    }
  })

  it('exports git remote and config scope types from the package root', () => {
    const remote: GitRemote = { name: 'origin', url: 'https://example.invalid/repo.git' }
    const scope: GitConfigScope = 'local'
    const waitState: SandboxWaitState = 'started'
    const waitForStateOptions: WaitForStateOptions = { timeout: 1 }

    expect(remote).toEqual({ name: 'origin', url: 'https://example.invalid/repo.git' })
    expect(scope).toBe('local')
    expect(waitState).toBe('started')
    expect(waitForStateOptions.timeout).toBe(1)
  })

  it('exports the list pagination option types from the package root', () => {
    // The gate here is the COMPILE, not the `toEqual`s: each literal is typed
    // by the method's own parameter and `satisfies` the root export, so this
    // fails the moment one of the public types stops being published from `index.ts`
    // or stops being the type `listEndpoints` / `secret.list`
    // actually takes. A TypeScript consumer cannot name those arguments without
    // them if the root exports are incomplete.
    const endpoints: Parameters<PortsService['listEndpoints']>[0] = {
      cursor: 'c1',
      limit: 10,
    } satisfies ListEndpointsOptions
    const secrets: Parameters<SecretService['list']>[0] = {
      cursor: 'c2',
      limit: 20,
    } satisfies ListSecretsOptions
    const volumes: Parameters<VolumeService['list']>[0] = {
      cursor: 'c3',
      limit: 30,
    } satisfies ListVolumesOptions

    expect(endpoints).toEqual({ cursor: 'c1', limit: 10 })
    expect(secrets).toEqual({ cursor: 'c2', limit: 20 })
    expect(volumes).toEqual({ cursor: 'c3', limit: 30 })
  })

  it('exports FeatureNotEnabledError and the camel-case Volume type from the v2 namespace', () => {
    const volume: Volume = {
      id: 'vol-1',
      name: 'data',
      type: 'disk',
      rawType: 'disk',
      sizeGib: 10,
      status: 'ready',
      rawStatus: 'ready',
      createdAt: '2026-10-14T00:00:00Z',
      updatedAt: '2026-10-14T00:00:00Z',
    }

    expect(FeatureNotEnabledError).toBe(publicSdk.FeatureNotEnabledError)
    expect(volume.sizeGib).toBe(10)
  })

  it('sends a read-only volume mount as read_only and omits the flag when it is unset', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })

    await client.create('python:3.12', {
      volumes: [
        { volumeId: 'bucket-vol', mountPath: '/mnt/models', subpath: 'v1/', readOnly: true },
        { volumeId: 'disk-vol', mountPath: '/data' },
        { volumeId: 'rw-bucket', mountPath: '/mnt/rw', readOnly: false },
      ],
    })

    // JSON drops undefined properties, matching the request sent over HTTP.
    expect(JSON.parse(JSON.stringify(post.mock.calls[0]?.[1]))).toMatchObject({
      image: 'python:3.12',
      wait_seconds: 60,
      idempotency_key: expect.any(String),
      volumes: [
        { volume: 'bucket-vol', mount_path: '/mnt/models', subpath: 'v1/', read_only: true },
        { volume: 'disk-vol', mount_path: '/data' },
        { volume: 'rw-bucket', mount_path: '/mnt/rw' },
      ],
    })
  })

  it('createFromBuild forwards volume mounts to the sandbox create', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockImplementation(async (path: string) => {
      if (path === '/images') return { data: { id: 'img_vol', name: null, region: 'region-a', status: 'pending' } }
      if (path === '/sandboxes') return { data: { id: 'sbx', status: 'running', wait_cap_seconds: 3 } }
      throw new Error(`unexpected post ${path}`)
    })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: imageResponse({ id: 'img_vol', anonymous: true, name: null }) })

    await client.createFromBuild(ImageBuilder.fromDockerfile('FROM python:3.12'), { volumes: [{ volumeId: 'vol-1', mountPath: '/mnt/data', subpath: 'proj' }] })

    expect(JSON.parse(JSON.stringify(post.mock.calls.find(([path]) => path === '/sandboxes')?.[1]))).toMatchObject({
      image: 'img_vol',
      volumes: [{ volume: 'vol-1', mount_path: '/mnt/data', subpath: 'proj' }],
    })
  })

  it('maps image create resource units', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', name: 'n', status: 'running', class: 'linux', memory_mib: 2048, disk_mib: 3072 } })
    const sandbox = await client.create('python:3.12', { resources: { cpu: 1.5, memory: 2, disk: 3 } })
    expect(sandbox.id).toBe('sbx')
    expect(post.mock.calls[0]?.[0]).toBe('/sandboxes')
    expect(post.mock.calls[0]?.[1]).toMatchObject({ cpu: 1.5, memory_mib: 2048, disk_mib: 3072 })
    expect(post.mock.calls[0]?.[2]).toMatchObject({ headers: { 'Idempotency-Key': expect.any(String) } })
  })

  it('uses the create response toolbox_proxy_url without a sandbox detail fetch', async () => {
    const originalFetch = globalThis.fetch
    const seen: string[] = []
    globalThis.fetch = jest.fn(async (input: string | URL | Request) => {
      seen.push(String(input))
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    }) as never

    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({
      data: { id: 'sbx', name: 'n', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' },
    })
    const get = jest.spyOn(clientInstance(client), 'get')

    try {
      const sandbox = await client.create('python:3.12')
      expect(toolboxInstance(sandbox).getUri({ url: '/version' })).toBe('https://proxy.example/sandbox-api/sbx/version')
      expect(post).toHaveBeenCalledTimes(1)
      expect(get).not.toHaveBeenCalled()
      expect(seen).toEqual([])
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('a create handle returned before any refresh invents no memory, disk or labels', async () => {
    // Built from a partial DTO, the handle reported memory 1, disk 1 and
    // labels {} as known fields, and toJSON() persisted them.
    const client = new Daytona({ apiKey: 'key' })
    jest.spyOn(clientInstance(client), 'post').mockResolvedValue({
      data: { id: 'sbx', name: 'n', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' },
    })
    const get = jest.spyOn(clientInstance(client), 'get')

    const sandbox = await client.create('python:3.12', { labels: { team: 'a' }, memory: 8, waitSeconds: 0 })

    expect(get).not.toHaveBeenCalled()
    expect(sandbox.memory).toBeUndefined()
    expect(sandbox.disk).toBeUndefined()
    expect(sandbox.toolboxProxyUrl).toBe('https://proxy.example/sandbox-api/sbx')
    const json = sandbox.toJSON()
    expect(json).not.toHaveProperty('memory')
    expect(json).not.toHaveProperty('disk')
    expect(json).not.toHaveProperty('labels')
    // no toolbox URL is serialized; the live handle keeps it.
    expect(json).not.toHaveProperty('toolboxProxyUrl')
    expect(json).not.toHaveProperty('toolboxUrl')
  })


  test('API envelopes surface their message', async () => {
    const response = new Response(JSON.stringify({ code: 'CONFLICT', message: 'conflict: fork is unavailable for this sandbox', source: 'DAYTONA_API' }), { status: 409 })
    const err = await errorFromFetchResponse(response)
    expect(err.message).toContain('fork is unavailable')
  })

  test('waitForState rejects NaN and non-finite timeouts before any request', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx')
    await expect(sandbox.waitForState(['started'], { timeout: Number.NaN })).rejects.toThrow('timeout must be a non-negative finite number')
    await expect(sandbox.waitForState(['started'], { timeout: Number.POSITIVE_INFINITY })).rejects.toThrow('timeout must be a non-negative finite number')
  })

  it('maps snapshot creates to the snapshot source field', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })
    await client.createFromSnapshot('snap')
    expect(post.mock.calls[0]?.[0]).toBe('/sandboxes/from-snapshot')
    expect(post.mock.calls[0]?.[1]).toMatchObject({ snapshot: 'snap' })
  })

  it('does not forward an unrecognized create option', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', status: 'running', wait_cap_seconds: 3 } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })
    // Untyped input may contain unknown options, which must not reach the wire.
    await client.create('py312', { unknownOption: 4096 } as unknown as CreateSandboxOptions)
    expect(post.mock.calls[0]?.[1]).not.toHaveProperty('unknown_option')
  })

  it('leaves the remaining source forms unchanged on the wire', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })

    await client.create('python:3.12')
    expect(post.mock.calls[0]?.[1]).toMatchObject({ image: 'python:3.12', wait_seconds: 60, idempotency_key: expect.any(String) })

    await client.create('node:20')
    expect(post.mock.calls[1]?.[1]).toMatchObject({ image: 'node:20', wait_seconds: 60, idempotency_key: expect.any(String) })

    await client.createFromSnapshot('snap')
    expect(post.mock.calls[2]?.[1]).toMatchObject({ snapshot: 'snap', wait_seconds: 60, idempotency_key: expect.any(String) })
  })

  it('discovers sandbox build logs before reading text', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const body = 'state: running\nPreparing build\nBuild completed\n'
    const get = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ url: 'https://logs.example/build-logs/sandboxes/sbx' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(body))
    const sandbox = lazySandbox(client, 'sbx')

    try {
      await expect(sandbox.buildLogs()).resolves.toBe(body)
      expect(get.mock.calls.map(call => call[0])).toEqual(['http://api.invalid/sandboxes/sbx/build-logs-url', 'https://logs.example/build-logs/sandboxes/sbx?follow=false&format=text'])
    } finally { get.mockRestore() }
  })

  it('reads the unified error envelope off a build-log HTTP error', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ url: 'https://logs.example/build-logs/sandboxes/sbx' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 'NOT_FOUND', message: 'the region is not known', source: 'DAYTONA_ROUTER', trace_id: '4bf92f3577b34da6a3ce929d0e0e4736' }), { status: 404 }))
    const sandbox = lazySandbox(client, 'sbx')
    try {
      const error = await sandbox.buildLogs().then(() => { throw new Error('expected rejection') }, (e: unknown) => e as DaytonaError)
      expect(error).toBeInstanceOf(NotFoundError)
      expect(error.statusCode).toBe(404)
      expect(error.code).toBe('NOT_FOUND')
      expect(error.source).toBe('DAYTONA_ROUTER')
      expect(error.message).toBe('the region is not known')
      expect(error.traceId).toBe('4bf92f3577b34da6a3ce929d0e0e4736')
    } finally { get.mockRestore() }
  })

  it('reads an error message and code on a build-log HTTP failure', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ url: 'https://logs.example/build-logs/sandboxes/sbx' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: 'service is unavailable', code: 'SERVICE_UNAVAILABLE' }), { status: 503 }))
    const sandbox = lazySandbox(client, 'sbx')
    try {
      const error = await sandbox.buildLogs().then(() => { throw new Error('expected rejection') }, (e: unknown) => e as DaytonaError)
      expect(error).toBeInstanceOf(ServiceUnavailableError)
      expect(error.statusCode).toBe(503)
      expect(error.code).toBe('SERVICE_UNAVAILABLE')
      expect(error.message).toBe('service is unavailable')
    } finally { get.mockRestore() }
  })

  it('falls back to the build-log status message when the error body has no prose', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ url: 'https://logs.example/build-logs/sandboxes/sbx' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 'INTERNAL_SERVER_ERROR' }), { status: 500 }))
    const sandbox = lazySandbox(client, 'sbx')
    try {
      const error = await sandbox.buildLogs().then(() => { throw new Error('expected rejection') }, (e: unknown) => e as DaytonaError)
      expect(error.statusCode).toBe(500)
      expect(error.code).toBe('INTERNAL_SERVER_ERROR')
      expect(error.message).toBe('build log HTTP 500')
    } finally { get.mockRestore() }
  })

  it.each([
    ['empty message', { message: '', code: 'INTERNAL_SERVER_ERROR' }],
    ['empty error', { error: '', code: 'INTERNAL_SERVER_ERROR' }],
    ['numeric message', { message: 7, code: 'INTERNAL_SERVER_ERROR' }],
  ])('keeps the build-log status fallback when prose is %s (parity: Python build_logs)', async (_label, bodyObj) => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ url: 'https://logs.example/build-logs/sandboxes/sbx' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(bodyObj), { status: 500 }))
    const sandbox = lazySandbox(client, 'sbx')
    try {
      const error = await sandbox.buildLogs().then(() => { throw new Error('expected rejection') }, (e: unknown) => e as DaytonaError)
      expect(error.statusCode).toBe(500)
      expect(error.code).toBe('INTERNAL_SERVER_ERROR')
      expect(error.message).toBe('build log HTTP 500')
    } finally { get.mockRestore() }
  })

  it('prefers a non-empty error over an empty message on a build-log error (parity: Python)', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ url: 'https://logs.example/build-logs/sandboxes/sbx' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: '', error: 'the real diagnostic text', code: 'NOT_FOUND' }), { status: 404 }))
    const sandbox = lazySandbox(client, 'sbx')
    try {
      const error = await sandbox.buildLogs().then(() => { throw new Error('expected rejection') }, (e: unknown) => e as DaytonaError)
      expect(error.code).toBe('NOT_FOUND')
      expect(error.message).toBe('the real diagnostic text')
    } finally { get.mockRestore() }
  })

  it('surfaces region on UNKNOWN_REGION and drops a non-string region (parity: Python)', () => {
    const ok = errorFromEnvelopeBody('{"code":"UNKNOWN_REGION","message":"nope","source":"DAYTONA_ROUTER","region":"narnia"}', 400, 'build log HTTP 400')
    expect(ok).toBeInstanceOf(BadRequestError)
    expect(ok.region).toBe('narnia')
    const nonString = errorFromEnvelopeBody('{"code":"UNKNOWN_REGION","message":"nope","source":"DAYTONA_ROUTER","region":5}', 400, 'build log HTTP 400')
    expect(nonString.region).toBeUndefined()
  })

  it('surfaces the DAYTONA_ROUTER per-code extras on the build-log error', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ url: 'https://logs.example/build-logs/sandboxes/sbx' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 'REGION_MISMATCH', message: 'two regions', source: 'DAYTONA_ROUTER', stated: 'region-a', actual: 'region-b' }), { status: 400 }))
    const sandbox = lazySandbox(client, 'sbx')
    try {
      const error = await sandbox.buildLogs().then(() => { throw new Error('expected rejection') }, (e: unknown) => e as DaytonaError)
      expect(error).toBeInstanceOf(BadRequestError)
      expect(error.code).toBe('REGION_MISMATCH')
    expect(error.stated).toBe('region-a')
    expect(error.actual).toBe('region-b')
    } finally { get.mockRestore() }
  })

  it('maps burst caps to cores_max, memory_max_mib and disk_max_mib', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })
    await client.create('python:3.12', { resources: { cpu: 0.5, cpuMax: 4, memory: 1, memoryMax: 0.25, diskMax: 20 } })
    expect(post.mock.calls[0]?.[1]).toMatchObject({ cores_max: 4, memory_max_mib: 256, disk_max_mib: 20480 })
    // Fractional GiB limits convert to integer MiB values.
    await client.create('python:3.12', { resources: { diskMax: 0.5 } })
    expect(post.mock.calls[1]?.[1]).toMatchObject({ disk_max_mib: 512 })
  })

  it('sends every resource limit when creating a Linux sandbox', async () => {
    // A fixed resource shape sets each limit equal to its requested amount.
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })
    await client.create('python:3.12', { resources: { cpu: 2, cpuMax: 2, memory: 4, memoryMax: 4, disk: 10, diskMax: 10 } })
    expect(post.mock.calls[0]?.[0]).toBe('/sandboxes')
    expect(post.mock.calls[0]?.[1]).toMatchObject({ cpu: 2, cores_max: 2, memory_mib: 4096, memory_max_mib: 4096, disk_mib: 10240, disk_max_mib: 10240 })
  })
  it('forwards labels when creating a sandbox', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValueOnce({ data: { id: 'linux' } })
    jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { id: 'linux', status: 'running' } })

    await createFixture(client, { image: Image.base('python:3.12'), labels: { team: 'sdk' } })
    expect(post.mock.calls[0]?.[1]).toMatchObject({ labels: { team: 'sdk' } })

  })

  it('rejects invalid sources before posting', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
    await expect(client.create('   ')).rejects.toThrow(new BadRequestError('image must be a non-empty string'))
    await expect(client.createFromSnapshot('')).rejects.toThrow(new BadRequestError('snapshot must be a non-empty string'))
    await expect(client.createFromSnapshot('   ')).rejects.toThrow(new BadRequestError('snapshot must be a non-empty string'))
    await expect(client.createFromSnapshot(5 as never)).rejects.toThrow(new TypeError('snapshot must be a non-empty snapshot name or snap_… id'))
    expect(() => Image.fromDockerfile('   ')).toThrow('non-empty Dockerfile')
    expect(post).not.toHaveBeenCalled()
  })

  it('creates from the platform default when no image is passed', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValueOnce({ data: { id: 'bare', status: 'running', wait_cap_seconds: 3, image: { id: 'img_default', name: 'daytona-base' } } })
      .mockResolvedValueOnce({ data: { id: 'opts', status: 'running', wait_cap_seconds: 3 } })
      .mockResolvedValueOnce({ data: { id: 'undef', status: 'running', wait_cap_seconds: 3 } })
      .mockResolvedValueOnce({ data: { id: 'named', status: 'running', wait_cap_seconds: 3 } })

    const bare = await client.create()
    const withOptions = await client.create({ name: 'example-user', labels: { team: 'sdk' } })
    const maybe: string | undefined = undefined as string | undefined
    await client.create(maybe, { cpu: 2 })
    await client.create('python:3.12', { name: 'img' })

    expect(post.mock.calls.map((call) => call[0])).toEqual(['/sandboxes', '/sandboxes', '/sandboxes', '/sandboxes'])
    for (const index of [0, 1, 2]) expect(post.mock.calls[index]?.[1]).not.toHaveProperty('image')
    expect(post.mock.calls[1]?.[1]).toMatchObject({ name: 'example-user', labels: { team: 'sdk', name: 'example-user' } })
    expect(post.mock.calls[2]?.[1]).toMatchObject({ cpu: 2 })
    expect(post.mock.calls[3]?.[1]).toMatchObject({ image: 'python:3.12', name: 'img' })
    expect(bare.image).toEqual({ id: 'img_default', name: 'daytona-base' })
    // Without an image in the response, the image remains unknown.
    expect(withOptions.image).toBeNull()
  })

  it('prefers the response image on every create, falling back to the input when it is absent', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const echoed = { id: 'img_py', name: 'python:3.12' }
    jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValueOnce({ data: { id: 'named', status: 'running', wait_cap_seconds: 3, image: echoed } })
      .mockResolvedValueOnce({ data: { id: 'by-id', status: 'running', wait_cap_seconds: 3, image: echoed } })
      .mockResolvedValueOnce({ data: { id: 'bare', status: 'running', wait_cap_seconds: 3, image: { id: 'img_default', name: 'daytona-base' } } })
      .mockResolvedValueOnce({ data: { id: 'mac', status: 'running', wait_cap_seconds: 3, image: { id: 'img_mac', name: 'base-macos' } } })
      // Create responses without an `image` field.
      .mockResolvedValueOnce({ data: { id: 'missing-image-named', status: 'running', wait_cap_seconds: 3 } })
      .mockResolvedValueOnce({ data: { id: 'missing-image-id', status: 'running', wait_cap_seconds: 3 } })
      .mockResolvedValueOnce({ data: { id: 'missing-image-bare', status: 'running', wait_cap_seconds: 3 } })

    const named = await client.create('python:3.12')
    const byId = await client.create('img_py')
    const bare = await client.create()
    const macos = await client.createMacos('base-macos')
    const withoutImageNamed = await client.create('python:3.12')
    const withoutImageId = await client.create('img_py')
    const withoutImageBare = await client.create()

    // The response's {id, name} is canonical: a named create resolves to its img_ id.
    expect(named.image).toEqual(echoed)
    expect(byId.image).toEqual(echoed)
    expect(bare.image).toEqual({ id: 'img_default', name: 'daytona-base' })
    expect(macos.image).toEqual({ id: 'img_mac', name: 'base-macos' })
    // No echo: fall back to the input-derived ref (an img_ id only; a name stays unknown).
    expect(withoutImageNamed.image).toBeNull()
    expect(withoutImageId.image).toEqual({ id: 'img_py', name: null })
    expect(withoutImageBare.image).toBeNull()
  })

  it('keeps rejecting blank and non-string image sources before posting', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
    await expect(client.create('')).rejects.toBeInstanceOf(BadRequestError)
    await expect(client.create('   ', { name: 'x' })).rejects.toThrow('image must be a non-empty string')
    await expect(client.create(ImageBuilder.fromRegistry('python:3.12') as unknown as string)).rejects.toThrow('createFromBuild')
    await expect(client.create(new ImageHandle(imageResponse() as never) as unknown as string)).rejects.toThrow('daytona.create() only accepts an image name or img_… id')
    await expect(client.create(42 as unknown as string)).rejects.toThrow('daytona.create() only accepts an image name or img_… id')
    await expect(client.create({ name: 'a' } as unknown as string, { name: 'b' })).rejects.toThrow('create(options) or create(image, options)')
    expect(post).not.toHaveBeenCalled()
  })

  it('rejects object sources create({ image }) and create({ snapshot }) before posting', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
    const message = 'daytona.create() takes the image as its first argument: create(image, options), not create({ image })'
    // Through a variable, so TypeScript's excess-property check does not fire.
    const imageObject = { image: 'python:3.12', cpu: 2 }
    const snapshotObject = { snapshot: 'snap_abc', name: 'x' }
    await expect(client.create(imageObject)).rejects.toThrow(message)
    await expect(client.create(snapshotObject)).rejects.toThrow(message)
    await expect(client.create({ image: undefined } as CreateSandboxOptions)).rejects.toThrow(message)
    expect(post).not.toHaveBeenCalled()
  })

  it('type-checks an optional image argument', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValue({ data: { id: 'opt', status: 'running', wait_cap_seconds: 3 } })
    const maybe = (process.env.DAYTONA_TEST_UNSET_IMAGE ?? undefined) as string | undefined
    await client.create(undefined)
    await client.create(maybe)
    await client.create(maybe, { name: 'opt' })
    expect(post).toHaveBeenCalledTimes(3)
    for (const call of post.mock.calls) expect(call[1]).not.toHaveProperty('image')
  })

  it('lists platform images and parses the platform fields', async () => {
    const client = newClient()
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { images: [imageResponse({ id: 'img_base', name: 'daytona-base', platform: true, default: true, deprecated: false })], next_cursor: null } })
      .mockResolvedValueOnce({ data: { images: [imageResponse()], next_cursor: null } })

    const platform = await client.images.list({ platform: 'true' })
    const withoutPlatformFields = await client.images.list({ platform: 'any', name: 'daytona-base' })

    expect(get.mock.calls[0]?.[1]).toMatchObject({ params: { platform: 'true' } })
    expect(get.mock.calls[1]?.[1]).toMatchObject({ params: { platform: 'any', name: 'daytona-base' } })
    expect(platform.items[0]).toMatchObject({ platform: true, default: true, deprecated: false })
    // Absent platform fields default to false.
    expect(withoutPlatformFields.items[0]).toMatchObject({ platform: false, default: false, deprecated: false })
  })

  it('parses disk_mib of a macOS snapshot image', async () => {
    const client = newClient()
    jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { images: [imageResponse({ id: 'img_mac', class: 'macos', origin: 'macos', disk_mib: 61440 }), imageResponse({ disk_mib: null })], next_cursor: null } })
      .mockResolvedValueOnce({ data: { images: [imageResponse()], next_cursor: null } })

    const page = await client.images.list({ anonymous: true })
    const withoutDiskField = await client.images.list()

    expect(page.items.map((image) => image.diskMib)).toEqual([61440, null])
    // An absent disk field reads as null.
    expect(withoutDiskField.items[0]?.diskMib).toBeNull()
  })


  it('omits the lifecycle key entirely when nothing is set', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValueOnce({ data: { id: 'linux' } })
      .mockResolvedValueOnce({ data: { id: 'bare' } })
      .mockResolvedValueOnce({ data: { id: 'null-lifecycle' } })
    jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { id: 'linux', status: 'running' } })
      .mockResolvedValueOnce({ data: { id: 'bare', status: 'running' } })
      .mockResolvedValueOnce({ data: { id: 'null-lifecycle', status: 'running' } })

    await createFixture(client, { image: Image.base('python:3.12'), lifecycle: {} })
    await createFixture(client, { image: Image.base('python:3.12') })
    await createFixture(client, { image: Image.base('python:3.12'), lifecycle: null })

    for (const call of post.mock.calls) expect((call[1] as Record<string, unknown> | undefined)?.lifecycle_policy).toBeUndefined()
  })

  it('leaves onTimeout to the server class default', async () => {
    // `onTimeout` is optional: the server fills the class default, so the SDK
    // never invents one — and `onTimeout` alone may ride on the default timeout.
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })
    await createFixture(client, { image: Image.base('py'), lifecycle: { timeoutSeconds: 300 } })
    expect((post.mock.calls[0]?.[1] as { lifecycle_policy: unknown }).lifecycle_policy).toEqual({ timeout_seconds: 300 })
  })

  it('sends timeoutSeconds verbatim without unit conversion', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })

    await createFixture(client, { image: Image.base('py'), lifecycle: { timeoutSeconds: 3600 } })

    expect((post.mock.calls[0]?.[1] as { lifecycle_policy: unknown }).lifecycle_policy).toEqual({ timeout_seconds: 3600 })
  })

  it.each([
    ['linux create does not accept: lifecyclePolicy.onTimeout=stop', { image: Image.base('py'), lifecycle: { timeoutSeconds: 60, onTimeout: 'stop' } }],
    ['linux create does not accept: lifecyclePolicy.onTimeout=pause', { image: Image.base('py'), lifecycle: { timeoutSeconds: 60, onTimeout: 'pause' } }],
    [
      'linux create does not accept: lifecyclePolicy.onIdleTimeout=stop',
      { image: Image.base('py'), lifecycle: { idleExpirySeconds: 900, onIdleTimeout: 'stop' } },
    ],
  ] as const)('rejects lifecycle actions the class forbids: %s', async (message, options) => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
    await expect(createFixture(client, options)).rejects.toThrow(message)
    expect(post).not.toHaveBeenCalled()
  })

  it('deletes a Linux snapshot restore when disposed', async () => {
    const client = new Daytona({ apiKey: 'key' })
    jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', name: 'sbx', status: 'running', class: 'linux' } })
    const del = jest.spyOn(clientInstance(client), 'delete').mockResolvedValue({ data: {} })
    const sandbox = await client.createFromSnapshot('snap', { waitSeconds: 0 })
    await sandbox[Symbol.asyncDispose]()
    expect(del).toHaveBeenCalledTimes(1)
  })

  it('keeps a persistent macOS sandbox when disposed', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'mac', class: 'macos', persistent: true, status: 'running' }, api, undefined, 'key')
    const del = jest.spyOn(api, 'delete')

    await sandbox[Symbol.asyncDispose]()

    expect(del).not.toHaveBeenCalled()
  })

  it('accepts the delete action on Linux creates', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })
    const variants: CreateFixtureOptions[] = [
      { image: Image.base('py') },
    ]
    const lifecycle: LifecyclePolicyOptions = { timeoutSeconds: 120, onTimeout: 'delete', idleExpirySeconds: 60, onIdleTimeout: 'delete' }
    for (const variant of variants) {
      await createFixture(client, { ...variant, lifecycle })
    }
    for (const call of post.mock.calls) {
      expect(call[1]).toHaveProperty('lifecycle_policy', { timeout_seconds: 120, on_timeout: 'delete', idle_expiry_seconds: 60, on_idle_timeout: 'delete' })
    }
  })


  it('rejects a disabled or zero timeout', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
    for (const value of [DISABLED, 0, -2]) {
      await expect(createFixture(client, { image: Image.base('py'), lifecycle: { timeoutSeconds: value } }))
        .rejects.toThrow('lifecyclePolicy.timeoutSeconds must be at least 1; the timeout cannot be disabled')
    }
    expect(post).not.toHaveBeenCalled()
    post.mockResolvedValueOnce({ data: { id: 'one' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValueOnce({ data: { id: 'one', status: 'running' } })
    await createFixture(client, { image: Image.base('py'), lifecycle: { timeoutSeconds: 1 } })
    expect((post.mock.calls[0]?.[1] as { lifecycle_policy: unknown }).lifecycle_policy).toEqual({ timeout_seconds: 1 })
  })

  it('accepts DISABLED only on idleExpirySeconds', async () => {
    expect(DISABLED).toBe(-1)
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })

    await createFixture(client, { image: Image.base('py'), lifecycle: { idleExpirySeconds: DISABLED } })
    expect((post.mock.calls[0]?.[1] as { lifecycle_policy: unknown }).lifecycle_policy).toEqual({ idle_expiry_seconds: -1 })
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { idleExpirySeconds: 0, onIdleTimeout: 'delete' } }))
      .rejects.toThrow('lifecyclePolicy.idleExpirySeconds: 0 is not a valid budget')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { idleExpirySeconds: -2 } }))
      .rejects.toThrow('lifecyclePolicy.idleExpirySeconds must be >= -1: use -1 to disable')
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('requires onIdleTimeout when arming the idle timeout at create', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { idleExpirySeconds: 900 } }))
      .rejects.toThrow('lifecyclePolicy.idleExpirySeconds requires lifecyclePolicy.onIdleTimeout')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { onIdleTimeout: 'stop' } }))
      .rejects.toThrow('lifecyclePolicy.onIdleTimeout requires lifecyclePolicy.idleExpirySeconds')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { idleExpirySeconds: DISABLED, onIdleTimeout: 'stop' } }))
      .rejects.toThrow('lifecyclePolicy.onIdleTimeout requires lifecyclePolicy.idleExpirySeconds')
    expect(post).not.toHaveBeenCalled()
  })

  it('rejects non-integer lifecycle durations and bad actions', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { timeoutSeconds: 1.5 } }))
      .rejects.toThrow('lifecyclePolicy.timeoutSeconds must be an integer number of seconds')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { idleExpirySeconds: true } } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy.idleExpirySeconds must be an integer number of seconds')
    for (const action of ['hibernate', 'STOP', 1]) {
      await expect(createFixture(client, { image: Image.base('py'), lifecycle: { timeoutSeconds: 60, onTimeout: action } } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy.onTimeout must be one of: stop, pause, delete')
    }
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: 900 } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy must be an object')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: [] } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy must be an object')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: [{ timeoutSeconds: 60 }] } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy must be an object')
    expect(post).not.toHaveBeenCalled()
  })

  it('rejects unknown lifecycle fields instead of dropping them', async () => {
    // Misspelled fields must fail validation rather than silently disappear.
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
    // SAFETY: deliberately misspelled lifecycle input exercises runtime validation for untyped callers.
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { timeoutSec: 900 } } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy contains unknown fields: timeoutSec')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { idleExpirySeconds: 60, nope: 1 } } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy contains unknown fields: nope')
    // A snake_case knob is the wire/Python spelling, unknown on the TS surface.
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { timeout_seconds: 60 } } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy contains unknown fields: timeout_seconds')
    // Alternative spellings are not accepted as aliases.
    // SAFETY: deliberately unknown lifecycle input exercises runtime validation for untyped callers.
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { stopAfterIdleSecs: 900 } } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy contains unknown fields: stopAfterIdleSecs')
    await expect(createFixture(client, { image: Image.base('py'), lifecycle: { deleteInSecs: 60 } } as unknown as CreateFixtureOptions))
      .rejects.toThrow('lifecyclePolicy contains unknown fields: deleteInSecs')
    expect(post).not.toHaveBeenCalled()
  })

  it('never creates a policy-free sandbox from a misspelled only key', async () => {
    // The single-key typo is the dangerous one: projecting it away omits
    // `lifecycle` entirely and creates the sandbox with NO policy at all.
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
    for (const lifecycle of [{ timeoutSec: 900 }, { onIdle: 'stop' }, { unknown_option: true }]) {
      // SAFETY: deliberately misspelled lifecycle input exercises runtime validation for untyped callers.
      await expect(createFixture(client, { image: Image.base('py'), lifecycle } as unknown as CreateFixtureOptions))
        .rejects.toThrow('lifecyclePolicy contains unknown fields')
    }
    expect(post).not.toHaveBeenCalled()
  })

  it('types every lifecycle rejection as BadRequestError with source SDK (parity: test_core.py)', () => {
    // Python's twin raises BadRequestError via create_error(…, "SDK") at every
    // site; a caller's `instanceof BadRequestError` must catch the same inputs
    // here. One probe per rejection site in lifecycleBody +
    // assertLifecycleClassSupport.
    type Loose = Parameters<typeof lifecycleBody>[0]
    const rejections: Array<() => unknown> = [
      () => lifecycleBody([] as unknown as Loose),
      () => lifecycleBody({ nope: 1 } as unknown as Loose),
      () => lifecycleBody({ timeoutSeconds: 1.5 }),
      () => lifecycleBody({ timeoutSeconds: 0 }),
    () => lifecycleBody({ idleExpirySeconds: 0, onIdleTimeout: 'delete' }),
    () => lifecycleBody({ idleExpirySeconds: -2 }),
      () => lifecycleBody({ timeoutSeconds: 2147483648 }),
      () => lifecycleBody({ timeoutSeconds: 60, onTimeout: 'hibernate' } as unknown as Loose),
    () => lifecycleBody({ idleExpirySeconds: DISABLED, onIdleTimeout: 'stop' }),
    () => lifecycleBody({ idleExpirySeconds: 900 }, { creating: true }),
      () => lifecycleBody({ onIdleTimeout: 'stop' }, { creating: true }),
      () => assertLifecycleClassSupport({ onTimeout: 'pause' }, { sandboxClass: 'linux', persistent: false }),
    ]
    for (const run of rejections) {
      let caught: unknown
      try {
        run()
      } catch (error) {
        caught = error
      }
      expect(caught).toBeInstanceOf(BadRequestError)
      expect((caught as publicSdk.DaytonaError).source).toBe('SDK')
    }
  })

  it('treats known lifecycle fields carrying undefined as omitted', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })

    // A known field carrying undefined is the documented "omitted" case
    // (unchanged on PATCH, disabled at create) — not an unknown field.
    await createFixture(client, {
      image: Image.base('py'),
      lifecycle: { timeoutSeconds: 60, onTimeout: undefined, idleExpirySeconds: undefined, onIdleTimeout: undefined },
    })
    expect(post.mock.calls[0]?.[1]).toHaveProperty('lifecycle_policy', { timeout_seconds: 60 })
  })

  it('patches only the passed lifecycle keys for a persistent macOS sandbox', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const patch = jest.spyOn(api, 'patch')
      .mockResolvedValueOnce({
        data: { id: 'sbx', lifecycle_policy: {
          timeout_seconds: 300,
          on_timeout: 'stop',
          timeout_at: '2026-08-28T10:00:00Z',
          idle_expiry_seconds: 900,
          on_idle_timeout: 'stop',
          last_activity_at: '2026-08-27T09:00:00Z',
          last_activity_source: 'toolbox',
        } },
      })

    const sandbox = new Sandbox({ id: 'sbx', status: 'running', class: 'macos', persistent: true }, api, undefined, 'key')
    const lifecycle = await sandbox.updateLifecyclePolicy({ idleExpirySeconds: 900, onIdleTimeout: 'stop' })
    expect(patch.mock.calls[0]?.[0]).toBe('/sandboxes/sbx/lifecycle-policy')
    expect(patch.mock.calls[0]?.[1]).toEqual({ idle_expiry_seconds: 900, on_idle_timeout: 'stop' })
    expect(lifecycle).toEqual({
      timeoutSeconds: 300,
      onTimeout: 'stop',
      timeoutAt: '2026-08-28T10:00:00Z',
      idleExpirySeconds: 900,
      onIdleTimeout: 'stop',
      lastActivityAt: '2026-08-27T09:00:00Z',
      lastActivitySource: 'toolbox',
    })
    expect(sandbox.lifecyclePolicy).toEqual(lifecycle)
  })

  it('updateLifecyclePolicy distinguishes omitted, DISABLED, and concrete values', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const patch = jest.spyOn(api, 'patch')
      .mockResolvedValueOnce({ data: { id: 'omitted', lifecycle_policy: { timeout_seconds: 60 } } })
      .mockResolvedValueOnce({ data: { id: 'disabled', lifecycle_policy: { idle_expiry_seconds: null, on_idle_timeout: null } } })
      .mockResolvedValueOnce({ data: { id: 'value', lifecycle_policy: { idle_expiry_seconds: 3600, on_idle_timeout: 'delete' } } })

    const omitted = new Sandbox({ id: 'omitted', status: 'running' }, api, undefined, 'key')
    await omitted.updateLifecyclePolicy({ timeoutSeconds: 60 })
    expect(patch.mock.calls[0]?.[1]).toEqual({ timeout_seconds: 60 })

    const disabled = new Sandbox({ id: 'disabled', status: 'running' }, api, undefined, 'key')
    await disabled.updateLifecyclePolicy({ idleExpirySeconds: DISABLED })
    expect(patch.mock.calls[1]?.[1]).toEqual({ idle_expiry_seconds: DISABLED })

    const value = new Sandbox({ id: 'value', status: 'running' }, api, undefined, 'key')
    await value.updateLifecyclePolicy({ idleExpirySeconds: 3600, onIdleTimeout: 'delete' })
    expect(patch.mock.calls[2]?.[1]).toEqual({ idle_expiry_seconds: 3600, on_idle_timeout: 'delete' })
  })

  it('rejects automatic pause for macOS before sending a request', async () => {
    // Given
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const patch = jest.spyOn(api, 'patch')
    const sandbox = new Sandbox({ id: 'sbx', status: 'running', class: 'macos', persistent: true }, api, undefined, 'key')
    // When / Then
    await expect(sandbox.updateLifecyclePolicy({ onTimeout: 'pause' })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(patch).not.toHaveBeenCalled()
  })

  it('updateLifecyclePolicy leaves pair coherence to the merged state', async () => {
    // PATCH may change either half of an idle policy independently; the API
    // validates the complete resulting policy.
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const patch = jest.spyOn(api, 'patch')
      .mockResolvedValueOnce({ data: { id: 'sbx', lifecycle_policy: { idle_expiry_seconds: 1800, on_idle_timeout: 'stop' } } })
      .mockResolvedValueOnce({ data: { id: 'sbx', lifecycle_policy: { idle_expiry_seconds: 1800, on_idle_timeout: 'delete' } } })
    const sandbox = new Sandbox({ id: 'sbx', status: 'running', class: 'macos', persistent: true }, api, undefined, 'key')
    await sandbox.updateLifecyclePolicy({ idleExpirySeconds: 1800 })
    await sandbox.updateLifecyclePolicy({ onIdleTimeout: 'delete' })
    expect(patch.mock.calls.map((call) => call[1])).toEqual([{ idle_expiry_seconds: 1800 }, { on_idle_timeout: 'delete' }])
  })

  it('mirrors the server ceiling rule before posting', async () => {
    const client = new Daytona({ apiKey: 'key' })
    for (const field of ['timeoutSeconds', 'idleExpirySeconds'] as const) {
      await expect(createFixture(client, { image: Image.base('py'), lifecycle: { [field]: 2147483648, onIdleTimeout: 'delete' } }))
        .rejects.toThrow(`lifecyclePolicy.${field} must not exceed 2147483647 seconds`)
    }
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValueOnce({ data: { id: 'ok' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValueOnce({ data: { id: 'ok', status: 'running' } })
    await createFixture(client, { image: Image.base('py'), lifecycle: { timeoutSeconds: 2147483647 } })
    expect((post.mock.calls[0]?.[1] as { lifecycle_policy: unknown }).lifecycle_policy).toEqual({ timeout_seconds: 2147483647 })
  })

  it('never rejects the off positions as a class violation', async () => {
    // Disabling the idle timeout is valid for every supported class.
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValueOnce({ data: { id: 'linux' } })
    jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { id: 'linux', status: 'running' } })
    const off: LifecyclePolicyOptions = { idleExpirySeconds: DISABLED }
    await createFixture(client, { image: Image.base('py'), lifecycle: off })
    for (const call of post.mock.calls) {
      expect(call[1]).toHaveProperty('lifecycle_policy', { idle_expiry_seconds: -1 })
    }
  })

  it('validates updateLifecyclePolicy before patching', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const patch = jest.spyOn(api, 'patch')

    const linux = new Sandbox({ id: 'linux', status: 'running', class: 'linux' }, api, undefined, 'key')
    await expect(linux.updateLifecyclePolicy({ onIdleTimeout: 'pause' })).rejects.toThrow('lifecyclePolicy.onIdleTimeout=pause')
    await expect(linux.updateLifecyclePolicy({ onTimeout: 'stop' })).rejects.toThrow('lifecyclePolicy.onTimeout=stop')

    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    await expect(sandbox.updateLifecyclePolicy({})).rejects.toThrow('updateLifecyclePolicy requires at least one lifecycle field')
    await expect(sandbox.updateLifecyclePolicy({ timeoutSeconds: DISABLED })).rejects.toThrow('the timeout cannot be disabled')
    await expect(sandbox.updateLifecyclePolicy({ idleExpirySeconds: 0 })).rejects.toThrow('lifecyclePolicy.idleExpirySeconds: 0 is not a valid budget')
    await expect(sandbox.updateLifecyclePolicy({ idleExpirySeconds: DISABLED, onIdleTimeout: 'stop' })).rejects.toThrow('lifecyclePolicy.onIdleTimeout requires lifecyclePolicy.idleExpirySeconds')
    expect(patch).not.toHaveBeenCalled()
  })

    // Resetting the idle timer posts to the sandbox's reset endpoint.
  it('resets the idle timer and caches the echoed lifecycle', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({
        data: {
          lifecycle_policy: {
            timeout_seconds: 300,
            on_timeout: 'delete',
            timeout_at: '2026-08-28T10:00:00Z',
            idle_expiry_seconds: 900,
            on_idle_timeout: 'delete',
            idle_timeout_at: '2026-08-27T09:15:00Z',
            last_activity_at: '2026-08-27T09:00:00Z',
            last_activity_source: 'api',
          },
          id: 'sbx',
        },
      })
      .mockResolvedValueOnce({ data: { lifecycle_policy: { last_activity_at: '2026-08-27T09:00:02Z', last_activity_source: 'toolbox' }, id: 'mac' } })

    const linux = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const lifecycle = await linux.resetIdleTimer()
    expect(post.mock.calls[0]?.[0]).toBe('/sandboxes/sbx/reset-idle-timer')
    expect(post.mock.calls[0]?.[1]).toBeUndefined()
    expect(lifecycle).toEqual({
      timeoutSeconds: 300,
      onTimeout: 'delete',
      timeoutAt: '2026-08-28T10:00:00Z',
      idleExpirySeconds: 900,
      onIdleTimeout: 'delete',
      idleTimeoutAt: '2026-08-27T09:15:00Z',
      lastActivityAt: '2026-08-27T09:00:00Z',
      lastActivitySource: 'api',
    })
    // The echo IS the full post-reset lifecycle — the cache takes it whole,
    // exactly like updateLifecyclePolicy (a bare handle included).
    expect(linux.lifecyclePolicy).toEqual(lifecycle)

    // The response may report newer concurrent activity and its source.
    // An unarmed idle policy has no idle deadline.
    const mac = new Sandbox({ id: 'mac', status: 'running', class: 'macos' }, api, undefined, 'key')
    const macLifecycle = await mac.resetIdleTimer()
    expect(macLifecycle.lastActivitySource).toBe('toolbox')
    expect(macLifecycle.idleTimeoutAt).toBeUndefined()
    expect(post.mock.calls[1]?.[0]).toBe('/sandboxes/mac/reset-idle-timer')
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('does not roll the cached lifecycle backwards on an out-of-order reset echo', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { lifecycle_policy: { last_activity_at: '2026-08-27T08:00:00Z', last_activity_source: 'ssh' }, id: 'sbx' } })

    // Cached clock is NEWER than the echo — the shape a slow response from an
    // earlier concurrent reset arrives in.
    const sandbox = new Sandbox({
      id: 'sbx',
      status: 'running',
      lifecycle_policy: {
        idle_expiry_seconds: 900,
        on_idle_timeout: 'stop',
        last_activity_at: '2026-08-27T09:00:00Z',
        last_activity_source: 'toolbox',
      },
    }, api, undefined, 'key')
    // The caller still gets the echo verbatim…
    await expect(sandbox.resetIdleTimer()).resolves.toMatchObject({ lastActivityAt: '2026-08-27T08:00:00Z', lastActivitySource: 'ssh' })
    // …but the cached policy keeps the newer clock.
    expect(sandbox.lifecyclePolicy?.lastActivityAt).toBe('2026-08-27T09:00:00Z')
    expect(sandbox.lifecyclePolicy?.lastActivitySource).toBe('toolbox')
  })

  it('breaks sub-millisecond reset-echo ties without regressing the cache', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    jest.spyOn(api, 'post')
      // Older than the cache by 660 microseconds — invisible to Date.parse.
      .mockResolvedValueOnce({ data: { lifecycle_policy: { last_activity_at: '2026-08-27T09:00:00.308100Z', last_activity_source: 'ssh' }, id: 'sbx' } })
      // Newer by 500 microseconds — also invisible to Date.parse.
      .mockResolvedValueOnce({ data: { lifecycle_policy: { last_activity_at: '2026-08-27T09:00:00.309260Z', last_activity_source: 'api' }, id: 'sbx' } })

    const sandbox = new Sandbox({
      id: 'sbx',
      status: 'running',
      lifecycle_policy: { last_activity_at: '2026-08-27T09:00:00.308760Z', last_activity_source: 'toolbox' },
    }, api, undefined, 'key')

    await sandbox.resetIdleTimer()
    expect(sandbox.lifecyclePolicy?.lastActivityAt).toBe('2026-08-27T09:00:00.308760Z')

    await sandbox.resetIdleTimer()
    expect(sandbox.lifecyclePolicy?.lastActivityAt).toBe('2026-08-27T09:00:00.309260Z')
  })

  it('breaks a sub-millisecond tie across mixed-precision renderings', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { lifecycle_policy: { last_activity_at: '2026-08-27T09:00:00.000500Z', last_activity_source: 'api' }, id: 'sbx' } })
      .mockResolvedValueOnce({ data: { lifecycle_policy: { last_activity_at: '2026-08-27T09:00:00Z', last_activity_source: 'ssh' }, id: 'older' } })

    // Second-precision cache, micros-precision echo: the same Date.parse
    // millisecond, but the echo is 500 microseconds NEWER. Lexicographically
    // '.' < 'Z', so a raw string tiebreak would reject this advance.
    const advancing = new Sandbox({
      id: 'sbx',
      status: 'running',
      lifecycle_policy: { last_activity_at: '2026-08-27T09:00:00Z', last_activity_source: 'toolbox' },
    }, api, undefined, 'key')
    await advancing.resetIdleTimer()
    expect(advancing.lifecyclePolicy?.lastActivityAt).toBe('2026-08-27T09:00:00.000500Z')
    expect(advancing.lifecyclePolicy?.lastActivitySource).toBe('api')

    // The mirror image: the echo is 500 microseconds OLDER at the same
    // millisecond, so the cache keeps its clock.
    const regressing = new Sandbox({
      id: 'older',
      status: 'running',
      lifecycle_policy: { last_activity_at: '2026-08-27T09:00:00.000500Z', last_activity_source: 'toolbox' },
    }, api, undefined, 'key')
    await expect(regressing.resetIdleTimer()).resolves.toMatchObject({ lastActivityAt: '2026-08-27T09:00:00Z' })
    expect(regressing.lifecyclePolicy?.lastActivityAt).toBe('2026-08-27T09:00:00.000500Z')
    expect(regressing.lifecyclePolicy?.lastActivitySource).toBe('toolbox')
  })

  it('keeps a valid cached clock when the reset echo carries no parseable one', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { lifecycle_policy: { last_activity_at: null, last_activity_source: 'ssh' }, id: 'sbx' } })
      .mockResolvedValueOnce({ data: { lifecycle_policy: { last_activity_source: 'ssh' }, id: 'sbx' } })
      .mockResolvedValueOnce({ data: { lifecycle_policy: { last_activity_at: 'not-a-timestamp', last_activity_source: 'ssh' }, id: 'sbx' } })

    const sandbox = new Sandbox({
      id: 'sbx',
      status: 'running',
      lifecycle_policy: { idle_expiry_seconds: 900, on_idle_timeout: 'delete', last_activity_at: '2026-08-27T09:00:00Z', last_activity_source: 'toolbox' },
    }, api, undefined, 'key')

    // Null, absent and unparseable timestamps must all preserve the cache.
    for (let attempt = 0; attempt < 3; attempt++) {
      await expect(sandbox.resetIdleTimer()).resolves.toMatchObject({ lastActivitySource: 'ssh' })
      expect(sandbox.lifecyclePolicy?.lastActivityAt).toBe('2026-08-27T09:00:00Z')
      expect(sandbox.lifecyclePolicy?.lastActivitySource).toBe('toolbox')
    }
  })

  it('accepts a clockless reset echo into an empty cache', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { lifecycle_policy: { idle_expiry_seconds: 900, on_idle_timeout: 'delete' }, id: 'sbx' } })

    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const fresh = await sandbox.resetIdleTimer()
    expect(fresh.lastActivityAt).toBeUndefined()
    expect(sandbox.lifecyclePolicy).toEqual(fresh)
  })

  it('parses idle_timeout_at wherever a lifecycle object appears', () => {
    expect(parseSandboxLifecycle({
      idle_expiry_seconds: 900,
      on_idle_timeout: 'stop',
      idle_timeout_at: '2026-08-27T09:15:00Z',
    })).toMatchObject({ idleTimeoutAt: '2026-08-27T09:15:00Z' })
    // Null and absent timestamps both read back as undefined.
    expect(parseSandboxLifecycle({ idle_timeout_at: null })).toMatchObject({ idleTimeoutAt: undefined })
    expect(parseSandboxLifecycle({})).toMatchObject({ idleTimeoutAt: undefined })
  })

  it('rejects unknown fields on the updateLifecyclePolicy PATCH path too', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const patch = jest.spyOn(api, 'patch').mockResolvedValue({ data: { id: 'sbx', lifecycle_policy: { timeout_seconds: 900 } } })
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')

    // Untyped JS callers reach this path too, and a dropped typo here would
    // read as "updateLifecyclePolicy requires at least one lifecycle field" at best and
    // as a silently ignored policy change at worst.
    await expect(sandbox.updateLifecyclePolicy({ timeoutSec: 900 } as unknown as LifecyclePolicyOptions))
      .rejects.toThrow('lifecyclePolicy contains unknown fields: timeoutSec')
    await expect(sandbox.updateLifecyclePolicy({ timeoutSeconds: 900, unknown_option: true } as unknown as LifecyclePolicyOptions))
      .rejects.toThrow('lifecyclePolicy contains unknown fields: unknown_option')
    expect(patch).not.toHaveBeenCalled()

    // Omitted stays omitted: the guard inspects key NAMES, so it cannot smuggle
    // unset fields onto a PATCH body and overwrite the current policy.
    await sandbox.updateLifecyclePolicy({ timeoutSeconds: 900, onTimeout: undefined })
    expect(patch.mock.calls[0]?.[1]).toEqual({ timeout_seconds: 900 })
  })

  it('exposes the read-side lifecycle from sandbox metadata', () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({
      id: 'sbx',
      status: 'running',
      lifecycle_policy: {
        timeout_seconds: 300,
        on_timeout: 'delete',
        timeout_at: '2026-08-28T10:00:00Z',
        idle_expiry_seconds: null,
        on_idle_timeout: null,
        last_activity_at: '2026-08-27T09:00:00Z',
        last_activity_source: 'toolbox',
      },
    }, api, undefined, 'key')
    expect(sandbox.lifecyclePolicy).toEqual({
      timeoutSeconds: 300,
      onTimeout: 'delete',
      timeoutAt: '2026-08-28T10:00:00Z',
      idleExpirySeconds: undefined,
      onIdleTimeout: undefined,
      lastActivityAt: '2026-08-27T09:00:00Z',
      lastActivitySource: 'toolbox',
    })
    expect(new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key').lifecyclePolicy).toBeUndefined()
  })

  it('passes an unknown lifecycle action through as its wire string', () => {
    // A newer server may add an action; the read side must not fail or rename it.
    expect(parseSandboxLifecycle({ timeout_seconds: 60, on_timeout: 'hibernate' }).onTimeout).toBe('hibernate')
  })

  it('maps extended sandbox metadata from Sandbox and serializes it to SandboxInfo', () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = new Sandbox({
      id: 'sbx',
      class: 'macos',
      name: 'train',
      image: { id: 'img_py', name: 'python:3.12' },
      status: 'running',
      cpu: 0.5,
      cores_max: 1.5,
      memory_mib: 3072,
      memory_max_mib: 5120,
      disk_mib: 4096,
      disk_max_mib: 8192,
      region_id: 'region-a',
      persistent: true,
      spot: true,
      spot_evicted_at: '2026-10-01T10:00:00Z',
      lifecycle_policy: {
        timeout_seconds: 3600,
        on_timeout: 'stop',
        timeout_at: '2026-10-02T10:00:00Z',
        idle_expiry_seconds: 600,
        on_idle_timeout: 'stop',
        last_activity_at: '2026-10-01T09:00:00Z',
        last_activity_source: 'preview',
      },
      forked_from: 'sbx-parent',
      user: 'dev',
      error: 'failed once',
      labels: { team: 'sdk', env: 'ci' },
    }, clientInstance(client), 'http://toolbox/{sandboxId}', 'key')

    expect(sandbox.labels).toEqual({ team: 'sdk', env: 'ci' })
    expect(sandbox.cpu).toBe(0.5)
    expect(sandbox.cpuMax).toBe(1.5)
    expect(sandbox.memory).toBe(3)
    expect(sandbox.memoryMax).toBe(5)
    expect(sandbox.disk).toBe(4)
    expect(sandbox.diskMax).toBe(8)
    expect(sandbox.region).toBe('region-a')
    expect(sandbox.persistent).toBe(true)
    expect(sandbox.spot).toBe(true)
    expect(sandbox.spotEvictedAt).toBe('2026-10-01T10:00:00Z')
    expect(sandbox.forkedFrom).toBe('sbx-parent')
    expect(sandbox.user).toBe('dev')
    expect(sandbox.error).toBe('failed once')

    const info = sandbox.toJSON() as SandboxInfo
    expect(info.labels).toEqual({ team: 'sdk', env: 'ci' })
    expect(info.memoryMax).toBe(5)
    expect(info.lifecyclePolicy).toEqual({ timeoutSeconds: 3600, onTimeout: 'stop', timeoutAt: '2026-10-02T10:00:00Z', idleExpirySeconds: 600, onIdleTimeout: 'stop', lastActivityAt: '2026-10-01T09:00:00Z', lastActivitySource: 'preview' })
  })

  it('openExclusiveStagingFile retries a fresh name on EEXIST and reports only the path it created', async () => {
  const { openExclusiveStagingFile } = await import('../src/v2/filesystemTransfers')
    const attempted: string[] = []
    const fakeHandle = { chmod: async () => undefined, write: async () => ({ bytesWritten: 0 }), close: async () => undefined } as unknown as Awaited<ReturnType<typeof nodeFsPromises.open>>
    const fs = {
      open: async (p: unknown, flags?: unknown, mode?: unknown) => {
        attempted.push(String(p))
        expect(flags).toBe('wx')
        expect(mode).toBe(0o600)
        if (attempted.length < 3) throw Object.assign(new Error('EEXIST'), { code: 'EEXIST' })
        return fakeHandle
      },
    }
    const staged = await openExclusiveStagingFile(fs, '/tmp/x/out.bin')
    expect(attempted).toHaveLength(3)
    expect(new Set(attempted).size).toBe(3)
    expect(staged.partPath).toBe(attempted[2])
    expect(staged.handle).toBe(fakeHandle)
    // A non-EEXIST failure propagates untouched (nothing to clean up).
    await expect(openExclusiveStagingFile({ open: async () => { throw Object.assign(new Error('EACCES'), { code: 'EACCES' }) } }, '/tmp/x/out.bin')).rejects.toMatchObject({ code: 'EACCES' })
  })

  it('rejects non-record labels with BadRequestError before any request', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const put = jest.spyOn(clientInstance(client), 'put')
    await expect(lazySandbox(client, 'sbx').setLabels(['bad'] as never)).rejects.toBeInstanceOf(BadRequestError)
    expect(put).not.toHaveBeenCalled()
  })

  it('sets labels without fetching metadata and loads macOS persistence before stopping', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'lazy')
    const api = clientInstance(client)
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'lazy', status: 'stopped', class: 'macos', persistent: true } })
    const put = jest.spyOn(api, 'put').mockResolvedValue({ data: { labels: { team: 'sdk' } } })
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: {} })

    await sandbox.setLabels({ team: 'sdk' })
    expect(get).not.toHaveBeenCalled()
    expect(put.mock.calls[0]?.[0]).toBe('/sandboxes/lazy/labels')

    await sandbox.stop()
    expect(post.mock.calls[0]?.[0]).toBe('/sandboxes/lazy/stop')
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('replaces labels through the sandbox labels route and updates local state', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'mac')
    sandbox.sandboxClass = 'macos'
    const put = jest.spyOn(clientInstance(client), 'put').mockResolvedValue({ data: { labels: { team: 'sdk' } } })

    await expect(sandbox.setLabels({ team: 'sdk' })).resolves.toEqual({ team: 'sdk' })
    expect(put.mock.calls[0]).toEqual([
      '/sandboxes/mac/labels',
      { labels: { team: 'sdk' } },
      expect.objectContaining({ daytona: { routeTemplate: '/sandboxes/{id}/labels', sandboxId: 'mac' } }),
    ])
    expect(sandbox.labels).toEqual({ team: 'sdk' })
  })

  it('preserves __proto__ labels and env keys through request and response maps', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' })
    sandbox.sandboxClass = 'linux'
    const labelPayload = Object.create(null) as Record<string, string>
    labelPayload['__proto__'] = 'label-value'
    const labelResponse = Object.create(null) as Record<string, string>
    labelResponse['__proto__'] = 'label-value'
    const envPayload = Object.create(null) as Record<string, string>
    envPayload['__proto__'] = 'env-value'
    const mintPayload = { access_token: 'proto-mint-jwt', token_type: 'Bearer', expires_at: new Date(Date.now() + 3_600_000).toISOString(), operations: [], ports: null }
    jest.spyOn(clientInstance(client), 'post').mockResolvedValueOnce({ data: mintPayload })
    const put = jest.spyOn(clientInstance(client), 'put').mockResolvedValue({ data: { labels: labelResponse } })
    const post = jest.spyOn(toolboxInstance(sandbox), 'post').mockResolvedValue({ data: undefined })

    const labels = await sandbox.setLabels(labelPayload)
    await sandbox.updateEnv(envPayload)
    const sentLabels = put.mock.calls[0]?.[1] as { labels: Record<string, string> } | undefined
    const sentEnv = post.mock.calls[0]?.[1] as { env: Record<string, string> } | undefined

    expect(Object.prototype.hasOwnProperty.call(sentLabels?.labels, '__proto__')).toBe(true)
    expect(sentLabels?.labels['__proto__']).toBe('label-value')
    expect(Object.prototype.hasOwnProperty.call(labels, '__proto__')).toBe(true)
    expect(labels['__proto__']).toBe('label-value')
    expect(Object.prototype.hasOwnProperty.call(sentEnv?.env, '__proto__')).toBe(true)
    expect(sentEnv?.env['__proto__']).toBe('env-value')
  })

  it('reads entrypoint logs as plain text from the sandbox detail route', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx')
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: 'boot ok\n' })

    await expect(sandbox.entrypointLogs()).resolves.toBe('boot ok\n')
    expect(get.mock.calls[0]).toEqual([
      '/sandboxes/sbx/logs',
      expect.objectContaining({ responseType: 'text', daytona: { routeTemplate: '/sandboxes/{id}/logs', sandboxId: 'sbx' } }),
    ])
  })

  it('updates future sandbox env via the toolbox route', async () => {
    const sandbox = new Sandbox('sbx', undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' })
    const post = jest.spyOn(toolboxInstance(sandbox), 'post').mockResolvedValue({ data: undefined })

    await sandbox.updateEnv({ HELLO: 'world' }, { unset: ['OLD'] })

    expect(post.mock.calls[0]).toEqual(['/env', { env: { HELLO: 'world' }, unset: ['OLD'] }, undefined])
  })

  it('rejects invalid updateEnv keys before any request', async () => {
    const sandbox = new Sandbox('sbx', undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' })
    const post = jest.spyOn(toolboxInstance(sandbox), 'post')

    await expect(sandbox.updateEnv({ '': 'x' })).rejects.toBeInstanceOf(BadRequestError)
    await expect(sandbox.updateEnv({ 'BAD=KEY': 'x' })).rejects.toBeInstanceOf(BadRequestError)
    await expect(sandbox.updateEnv({ 'BAD\0KEY': 'x' })).rejects.toBeInstanceOf(BadRequestError)
    await expect(sandbox.updateEnv({ GOOD: 'bad\0value' })).rejects.toBeInstanceOf(BadRequestError)
    await expect(sandbox.updateEnv({}, { unset: ['BAD\0KEY'] })).rejects.toBeInstanceOf(BadRequestError)
    expect(post).not.toHaveBeenCalled()
  })

  it.each([
    ['', 'env key is not a valid variable name'],
    ['BAD=KEY', 'env key is not a valid variable name'],
    ['BAD\nKEY', 'env key is not a valid variable name'],
    ['BAD\rKEY', 'env key is not a valid variable name'],
  ])('refuses env key %p that no environment can hold', async (key, message) => {
    const sandbox = new Sandbox('sbx', undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' })
    const post = jest.spyOn(toolboxInstance(sandbox), 'post')

    await expect(sandbox.updateEnv({ [key]: 'x' })).rejects.toMatchObject({ message, code: 'BAD_REQUEST' })
    expect(post).not.toHaveBeenCalled()
  })

  it('leaves the reserved-name policy to the toolbox API', async () => {
    const sandbox = new Sandbox('sbx', undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' })
    const post = jest.spyOn(toolboxInstance(sandbox), 'post').mockResolvedValue({ data: undefined })

    await sandbox.updateEnv({ APP_DEBUG: '1' })
    expect(post.mock.calls[0]?.[1]).toMatchObject({ env: { APP_DEBUG: '1' } })
    post.mockClear()

    await sandbox.updateEnv({ DAYTONA_EXAMPLE_SETTING: 'example-value' })
    const request = post.mock.calls[0]?.[1] as { env: Record<string, string>; unset: readonly string[] } | undefined

    expect(post.mock.calls[0]?.[0]).toBe('/env')
    expect(request?.env.DAYTONA_EXAMPLE_SETTING).toBe('example-value')
    expect(request?.unset).toEqual([])
  })

  it('rejects multiple sandbox sources at compile time', () => {
    // @ts-expect-error snapshot and image are mutually exclusive on CreateSandboxOptions
    const invalidSources: CreateSandboxOptions = { image: Image.base('python:3.12'), snapshot: 'snap' }
    expect(invalidSources).toBeDefined()
  })

  it('wires image operations to their API routes', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'img', name: 'python', status: 'pending' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: imageResponse({ id: 'img', name: 'python' }) })
    await client.images.build(ImageBuilder.fromRegistry('python:3.12'), { name: 'python' })
    expect(post.mock.calls[0]?.[0]).toBe('/images')
    expect(post.mock.calls[0]?.[1]).toMatchObject({ name: 'python', source: { type: 'registry', ref: 'python:3.12' } })
  })

  it('serializes sandbox and process handles without network', () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, { sandboxId: 'sbx' }, { toolboxUrl: 'http://sandbox.local' })
    const handle = sandbox.process.connect({ sandboxId: 'sbx', processId: 'proc' })
    expect(sandbox.toJSON()).toEqual({ sandboxId: 'sbx' })
    expect(handle.toJSON()).toEqual({ sandboxId: 'sbx', processId: 'proc' })
  })

  it('preserves partial SandboxInfo field presence until a full Sandbox refresh lands', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, { sandboxId: 'sbx', name: 'partial' })
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({
      data: {
        id: 'sbx',
        name: 'partial',
        image: { id: 'img_py', name: 'python:3.12' },
        labels: undefined,
        memory_mib: 1024,
        disk_mib: 2048,
        status: 'running',
        region: 'region-a',
        persistent: false,
        spot: false,
        toolbox_proxy_url: 'http://region-toolbox',
      },
    })

    expect(sandbox.toJSON()).toEqual({ sandboxId: 'sbx', name: 'partial' })

    await sandbox.refreshData()

    expect(get).toHaveBeenCalledWith('/sandboxes/sbx', undefined)
    expect(sandbox.toJSON()).toEqual(expect.objectContaining({
      sandboxId: 'sbx',
      name: 'partial',
      image: { id: 'img_py', name: 'python:3.12' },
      labels: {},
      state: 'started',
    }))
  })

  it('omits sandbox accessToken from toJSON and JSON.stringify output', () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, { sandboxId: 'sbx' }, {
      toolboxUrl: 'https://proxy.example/sandbox-api/sbx',
      accessToken: 'sandbox-secret-token',
    })

    const serialized = sandbox.toJSON()

    expect(serialized).not.toHaveProperty('accessToken')
    expect(JSON.stringify(sandbox)).not.toContain('sandbox-secret-token')
  })

  it.each([
    ['refreshData', (s: Sandbox) => s.refreshData()],
    ['createAccessToken', (s: Sandbox) => s.createAccessToken()],
    ['start', (s: Sandbox) => s.start()],
    ['stop', (s: Sandbox) => s.stop()],
    ['pause', (s: Sandbox) => s.pause()],
    ['resume', (s: Sandbox) => s.resume()],
    ['fork', (s: Sandbox) => s.fork()],
    ['delete', (s: Sandbox) => s.delete()],
    ['getNetworkPolicy', (s: Sandbox) => s.getNetworkPolicy()],
    ['updateNetworkPolicy', (s: Sandbox) => s.updateNetworkPolicy({ blockAll: true }, { ifMatch: 'v1' })],
    ['resetNetworkPolicy', (s: Sandbox) => s.resetNetworkPolicy({ ifMatch: 'v1' })],
    ['updateLifecyclePolicy', (s: Sandbox) => s.updateLifecyclePolicy({ timeoutSeconds: 60 })],
    ['resetIdleTimer', (s: Sandbox) => s.resetIdleTimer()],
    ['snapshotFilesystem', (s: Sandbox) => s.snapshotFilesystem()],
    ['createSnapshot', (s: Sandbox) => s.createSnapshot('snap')],
    ['setLabels', (s: Sandbox) => s.setLabels({ team: 'sdk' })],
    ['entrypointLogs', (s: Sandbox) => s.entrypointLogs()],
    ['buildLogs', (s: Sandbox) => s.buildLogs()],
    ['followBuildLogs', (s: Sandbox) => s.followBuildLogs({ onLogs: () => undefined })],
  ] as const)('%s on a Sandbox built without an API client fails with UNSUPPORTED_OPERATION', async (_name, call) => {
    const sandbox = new Sandbox('sbx', undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' })
    sandbox.sandboxClass = 'macos'
    sandbox.persistent = true
    await expect(call(sandbox)).rejects.toMatchObject({
      message: 'API is not available for this sandbox',
      code: 'UNSUPPORTED_OPERATION',
      statusCode: 400,
    })
  })

  it.each([
    ['SandboxInfo toolboxProxyUrl', { sandboxId: 'sbx', toolboxProxyUrl: 'http://evil.invalid/sandbox-api/sbx' }],
    ['SandboxInfo toolboxUrl', { sandboxId: 'sbx', toolboxUrl: 'http://evil.invalid/sandbox-api/sbx' }],
    ['SandboxDto toolbox_proxy_url', { id: 'sbx', status: 'running', toolbox_proxy_url: 'http://evil.invalid/sandbox-api' }],
    ['SandboxDto toolboxProxyUrl', { id: 'sbx', status: 'running', toolboxProxyUrl: 'http://evil.invalid/sandbox-api' }],
  ] as const)('the exported Sandbox constructor ignores a toolbox URL inside its input (%s)', (_inputKind, input) => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = new Sandbox(input as unknown as SandboxInfo, clientInstance(client), undefined, 'key')
    expect(sandbox.toolboxProxyUrl).toBeUndefined()
    expect(() => sandbox.fs).toThrow('sandbox handle has no toolbox URL')
  })

  it('the exported Sandbox constructor honors an explicit toolboxUrl option', () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = new Sandbox({ sandboxId: 'sbx', toolboxProxyUrl: 'http://evil.invalid/sandbox-api/sbx' }, clientInstance(client), undefined, 'key', undefined, { toolboxUrl: 'http://trusted.invalid/sandbox-api/sbx' })
    expect(sandbox.toolboxProxyUrl).toBe('http://trusted.invalid/sandbox-api/sbx')
  })

  it('a stored handle is not accepted as get() options (compile-time check)', () => {
    // Compile-only: the guard is the @ts-expect-error line, which ts-jest and
    // `npm run typecheck` fail on if SandboxInfo ever shares a field with
    // SandboxConnectOptions again. The function is never called.
    const rejectedAtCompileTime = (client: Daytona, stored: SandboxHandleJSON) => {
      // @ts-expect-error a stored handle is not get() options
      void client.get(stored.sandboxId, stored)
    }
    void rejectedAtCompileTime
  })

  it('has no public sandbox() constructor: a sandbox comes from create/get/list', () => {
    const client = new Daytona({ apiKey: 'key' })
    expect('sandbox' in client).toBe(false)
    expect(Object.getOwnPropertyNames(Daytona.prototype)).not.toContain('sandbox')
  })

  it('defers missing toolbox URL until toolbox services are used', () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, { sandboxId: 'sbx' })
    expect(sandbox.id).toBe('sbx')
    expect(() => sandbox.process).toThrow('sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolboxUrl)')
  })

  it('ignores DAYTONA_TOOLBOX_URL for a metadata-less handle', () => {
    const previous = process.env.DAYTONA_TOOLBOX_URL
    process.env.DAYTONA_TOOLBOX_URL = 'http://ignored.invalid/sandbox-api'
    try {
      const client = new Daytona({ apiKey: 'key' })
      expect(() => lazySandbox(client, 'sbx').process).toThrow('sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolboxUrl)')
    } finally {
      if (previous === undefined) delete process.env.DAYTONA_TOOLBOX_URL
      else process.env.DAYTONA_TOOLBOX_URL = previous
    }
  })

  it('refreshes lazy sandbox toolbox transports from sandbox metadata', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, { sandboxId: 'sbx' })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running', toolbox_proxy_url: 'http://region-toolbox' } })
    await sandbox.refreshData()
    expect(sandbox.toolboxProxyUrl).toBe('http://region-toolbox/sbx')
    expect(sandbox.process.connect('p').toJSON()).toEqual({ sandboxId: 'sbx', processId: 'p' })
  })

  it('hydrates imageBuildId from Sandbox payloads and serializes it to SandboxInfo', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, { sandboxId: 'sbx' })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({
      data: { id: 'sbx', status: 'running', toolbox_proxy_url: 'http://region-toolbox', image_build_id: 'build-123' },
    })

    await sandbox.refreshData()

    expect(sandbox.imageBuildId).toBe('build-123')
    expect(sandbox.toJSON().imageBuildId).toBe('build-123')
  })

  it('preserves direct scoped toolbox URLs across refreshes', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, { sandboxId: 'sbx' }, { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running', toolbox_proxy_url: 'http://region-toolbox' } })
    await sandbox.refreshData()
    expect(sandbox.toolboxProxyUrl).toBe('http://toolbox.invalid/sandbox-api/sbx')
  })

  it('fetches a missing list item toolbox_proxy_url exactly once before building the handle', async () => {
    const originalFetch = globalThis.fetch
    const seen: string[] = []
    globalThis.fetch = jest.fn(async (input: string | URL | Request) => {
      seen.push(String(input))
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    }) as never

    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { sandboxes: [{ id: 'sbx', status: 'running' }] } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' } })

    try {
      const page = await client.list({ limit: 1 })
      expect(page.items).toHaveLength(1)
      const [sandbox] = page.items
      if (!sandbox) throw new Error('expected one sandbox')
      expect(toolboxInstance(sandbox).getUri({ url: '/version' })).toBe('https://proxy.example/sandbox-api/sbx/version')
      expect(get).toHaveBeenCalledTimes(2)
      expect(get.mock.calls[1]?.[0]).toBe('/sandboxes/sbx')
      expect(seen).toEqual([])
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('reads sandbox metrics from the toolbox transport', async () => {
    const sandbox = new Sandbox('sbx', undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' })
    const toolboxTransport = Reflect.get(sandbox, 'toolboxTransport') as HttpTransport
    const get = jest.spyOn(toolboxTransport, 'get').mockResolvedValue({
      timestamp: '2026-08-23T00:00:00Z',
      cpuCount: 4,
      cpuUtilization: 0.25,
      memoryUsageBytes: 10,
      memoryTotalBytes: 20,
      diskUsageBytes: 30,
      diskTotalBytes: 40,
    })

    await expect(sandbox.metrics()).resolves.toMatchObject({ cpuCount: 4, cpuUtilization: 0.25 })
    expect(get).toHaveBeenCalledWith('/system/metrics')
  })

  it('reads sandbox metrics from the toolbox transport', async () => {
    const sandbox = new Sandbox('sbx', undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox/sbx' })
    const toolboxTransport = Reflect.get(sandbox, 'toolboxTransport') as HttpTransport
    const get = jest.spyOn(toolboxTransport, 'get').mockResolvedValue({
      timestamp: '2026-08-23T00:00:00Z',
      cpuCount: 4,
      cpuUtilization: 0.25,
      memoryUsageBytes: 10,
      memoryTotalBytes: 20,
      diskUsageBytes: 30,
      diskTotalBytes: 40,
    })

    await expect(sandbox.metrics()).resolves.toMatchObject({ cpuCount: 4, cpuUtilization: 0.25 })
    expect(get).toHaveBeenCalledWith('/system/metrics')
  })

  it('resumes paused sandboxes when starting them', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = new Sandbox({ id: 'sbx', status: 'paused', class: 'macos', persistent: true }, clientInstance(client), 'http://toolbox/{sandboxId}', 'key')
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'paused', class: 'macos', persistent: true } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'running', class: 'macos', persistent: true } })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValueOnce({ data: {} })
    await sandbox.start({ timeout: 1 })
    expect(get).toHaveBeenCalledTimes(2)
    expect(post.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/sbx/resume'])
  })

  it('preserves an unrelated resume conflict without attempting start', async () => {
    // Given a paused sandbox whose resume request conflicts with another operation.
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'paused', class: 'macos', persistent: true }, api, undefined, 'key')
    jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', status: 'paused', class: 'macos', persistent: true } })
    const error = new DaytonaError('another operation is in progress', { statusCode: 409, code: 'CONFLICT' })
    const post = jest.spyOn(api, 'post').mockRejectedValue(error)

    // When starting requires a resume, Then the original error remains observable.
    await expect(sandbox.start({ timeout: 1 })).rejects.toBe(error)
    expect(post.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/sbx/resume'])
  })

  it('captures Linux and macOS snapshots and quotes the sandbox id', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { id: 'linux', status: 'pending' } })
      .mockResolvedValueOnce({ data: { id: 'mac', status: 'pending' } })
    jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: snapshotResponse({ id: 'linux' }) })
      .mockResolvedValueOnce({ data: snapshotResponse({ id: 'mac', class: 'macos' }) })

    const linux = new Sandbox({ id: "sbx!'()*", status: 'running' }, api, undefined, 'key')
    const mac = new Sandbox({ id: 'mac', status: 'running', class: 'macos' }, api, undefined, 'key')

    await expect(linux.createSnapshot('snap')).resolves.toBeInstanceOf(Snapshot)
    await expect(mac.createSnapshot('snap')).resolves.toBeInstanceOf(Snapshot)

    expect(post.mock.calls).toEqual([
      ["/sandboxes/sbx%21%27%28%29%2A/snapshots", { name: 'snap' }],
      ['/sandboxes/mac/snapshots', { name: 'snap' }],
    ])
  })

  it('rejects negative start and resume timeouts before posting lifecycle requests', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = new Sandbox({ id: 'sbx', status: 'stopped' }, clientInstance(client), 'http://toolbox/{sandboxId}', 'key')
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: {} })
    await expect(sandbox.start({ timeout: -1 })).rejects.toThrow('timeout must be a non-negative finite number')
    await expect(sandbox.resume({ timeout: -1 })).rejects.toThrow('timeout must be a non-negative finite number')
    expect(post).not.toHaveBeenCalled()
  })

  it.each([
    ['linux', 'stop'],
    ['linux', 'pause'],
    ['linux', 'resume'],
    ['macos', 'stop'],
    ['macos', 'pause'],
    ['macos', 'resume'],
  ] as const)('rejects %s %s when unsupported by its configuration before posting', async (sandboxClass, operation) => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', class: sandboxClass, persistent: false, status: 'running' }, api, undefined, 'key')
    const post = jest.spyOn(api, 'post')

    await expect(sandbox[operation]()).rejects.toMatchObject({ code: 'UNSUPPORTED_OPERATION' })

    expect(post).not.toHaveBeenCalled()
  })

  it('does not post start for existing startup transitions', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, clientInstance(client), 'http://toolbox/{sandboxId}', 'key')
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'booting' } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'running' } })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: {} })
    await sandbox.start({ timeout: 1 })
    expect(get).toHaveBeenCalledTimes(2)
    expect(post).not.toHaveBeenCalled()
  })

  it('lists paginated preview ports and upserts exposure by port', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    jest.spyOn(api, 'put').mockResolvedValue({ data: { id: 'ep', port: 8080, public: false, url: 'https://p' } })
    jest.spyOn(api, 'get').mockResolvedValue({ data: { ports: [{ id: 'ep', port: 8080, public: false, url: 'https://p' }], next_cursor: 'next' } })
    await expect(sandbox.ports.listEndpoints()).resolves.toEqual({ items: [{ port: 8080, public: false, url: 'https://p', createdAt: undefined }], nextCursor: 'next' })
    await expect(sandbox.ports.expose(8080)).resolves.toMatchObject({ port: 8080 })
    expect(api.put).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080', { public: false })
  })

  it('marks waitForState timeout errors as SDK-originated', async () => {
    jest.useFakeTimers()
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, clientInstance(client), 'http://toolbox/{sandboxId}', 'key')
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'booting' } })
    try {
      const waiting = expect(sandbox.waitForState(['started'], { timeout: 1 })).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT', source: 'SDK' })
      await jest.advanceTimersByTimeAsync(1)
      await waiting
    } finally {
      jest.useRealTimers()
    }
  })

  it('wait() raises RequestTimeoutError on timeout', async () => {
    jest.useFakeTimers()
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, clientInstance(client), 'http://toolbox/{sandboxId}', 'key')
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'booting' } })
    try {
      const waiting = sandbox.wait({ timeout: 0.001 }).catch((error: unknown) => error)
      await jest.advanceTimersByTimeAsync(1)
      const error = await waiting
      expect(error).toBeInstanceOf(RequestTimeoutError)
      expect(error).toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    } finally {
      jest.useRealTimers()
    }
  })

  it.each([
    ['failed', 'error'],
    ['deleted', 'destroyed'],
    ['deleting', 'destroying'],
  ])('waitForState raises with the observed default terminal state: %s', async (status, state) => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, clientInstance(client), 'http://toolbox/{sandboxId}', 'key')
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status } })

    const failure = sandbox.waitForState(['started'])
    await expect(failure).rejects.toBeInstanceOf(publicSdk.SandboxTerminalStateError)
    await expect(failure).rejects.toBeInstanceOf(publicSdk.ConflictError)
    await expect(failure).rejects.toMatchObject({ statusCode: 409, code: 'CONFLICT', source: 'SDK', state })
  })

  it('waitForState uses API long-poll slices', async () => {
    const client = new Daytona({ apiKey: 'key', requestTimeoutSeconds: 5 })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key')
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'booting' } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'running' } })

    const waiting = sandbox.waitForState(['started'], { timeout: 60_000 })
    await jest.advanceTimersByTimeAsync(200)
    await expect(waiting).resolves.toBeUndefined()

    expect(get.mock.calls[0]).toEqual(['/sandboxes/sbx', { timeout: 35_000, params: { wait: 'running', timeout_ms: 30000 } }])
    expect(get.mock.calls[1]).toEqual(['/sandboxes/sbx', { timeout: 35_000, params: { wait: 'running', timeout_ms: 30000 } }])
  })

  it('rounds waitForState sub-millisecond budgets up before sending timeout_ms', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key')
    const get = jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'sbx', status: 'running' } })

    await sandbox.waitForState(['started'], { timeout: 0.5 })

    expect(get.mock.calls[0]).toEqual(['/sandboxes/sbx', { timeout: 5001, params: { wait: 'running', timeout_ms: 1 } }])
  })

  it('waitForState re-arms a failed probe poll at the full early-probe delay', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const ticketApi = {
      post: jest.fn().mockResolvedValue({ data: { ticket: 't0k3n', expires_in_ms: 30_000, subprotocol: 'daytona.events.v1' } }),
      get: jest.fn(),
      defaults: { timeout: 30_000 },
    } as unknown as AxiosInstance
    const ws = new HubTestWebSocket()
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, () => ws as unknown as WebSocket, ticketApi, { earlyProbeMs: 40, noFrameTimeoutMs: 5_000 })
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    const get = jest.spyOn(api, 'get').mockRejectedValue(new ServiceUnavailableError('busy', { statusCode: 503, code: 'SERVICE_UNAVAILABLE', source: 'DAYTONA_API' }))
    try {
      const waiting = sandbox.waitForState(['started'], { timeout: 130 }).catch((error: unknown) => error)
      await jest.advanceTimersByTimeAsync(130)
      expect(await waiting).toBeInstanceOf(RequestTimeoutError)
      expect(get.mock.calls.length).toBeLessThanOrEqual(3)
    } finally {
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
      jest.useRealTimers()
    }
  })

  it('waitForState short-budget probes still require socket silence before polling', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const ticketApi = {
      post: jest.fn().mockResolvedValue({ data: { ticket: 't0k3n', expires_in_ms: 30_000, subprotocol: 'daytona.events.v1' } }),
      get: jest.fn(),
      defaults: { timeout: 30_000 },
    } as unknown as AxiosInstance
    const ws = new HubTestWebSocket()
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, () => ws as unknown as WebSocket, ticketApi, { earlyProbeMs: 80, noFrameTimeoutMs: 5_000 })
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: sandboxDetail({ id: 'sbx', status: 'running' }) })
    try {
      const waiting = sandbox.waitForState(['started'], { timeout: 100 })
      await jest.advanceTimersByTimeAsync(20)
      ws.inject({ type: 'snapshot', id: 'sbx', state: 'pending', version: 1, error: null })
      await jest.advanceTimersByTimeAsync(30)
      expect(get).toHaveBeenCalledTimes(0)
      const finished = waiting.catch(() => undefined)
      await hub.detach()
      await finished
    } finally {
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
      jest.useRealTimers()
    }
  })

  it('waitForState resolves within budget when a probe keeps returning not-ready', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const randomSpy = jest.spyOn(Math, 'random').mockReturnValue(0.5)
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const ticketApi = {
      post: jest.fn().mockResolvedValue({ data: { ticket: 't0k3n', expires_in_ms: 30_000, subprotocol: 'daytona.events.v1' } }),
      get: jest.fn(),
      defaults: { timeout: 30_000 },
    } as unknown as AxiosInstance
    const ws = new HubTestWebSocket()
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, () => ws as unknown as WebSocket, ticketApi, { earlyProbeMs: 1_000, noFrameTimeoutMs: 5_000 })
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    const probeAt: number[] = []
    const startedAt = Date.now()
    const get = jest.spyOn(api, 'get')
      .mockImplementation(async () => {
        probeAt.push(Date.now())
        return { data: sandboxDetail({ id: 'sbx', status: probeAt.at(-1)! - startedAt >= 2_300 ? 'running' : 'booting' }) }
      })
    try {
      const waiting = sandbox.waitForState(['started', 'stopped'], { timeout: 2_600 })
      await jest.advanceTimersByTimeAsync(2_600)
      await expect(waiting).resolves.toBeUndefined()
      expect(probeAt.slice(1).every((at, index) => at - probeAt[index]! >= WAIT_FOR_STATE_MIN_INTERVAL_MS)).toBe(true)
    } finally {
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
      randomSpy.mockRestore()
      jest.useRealTimers()
    }
  })

  it('waitForState timeout above the Node timer cap re-arms until the real deadline', async () => {
    jest.useFakeTimers({ doNotFake: ['setImmediate', 'nextTick', 'queueMicrotask'] })
    const api = {
      post: jest.fn(),
      get: jest.fn(),
      defaults: { timeout: 30_000 },
    } as unknown as AxiosInstance
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    const registerWaiter = (hub as unknown as {
      registerWaiter: (
        sandboxId: string,
        predicate: ReturnType<typeof targetPredicate>,
        deadline: number | undefined,
        observe?: ((outcome: unknown) => void) | undefined,
        sandboxPollFn?: (() => Promise<unknown>) | undefined,
      ) => { promise: Promise<unknown> }
    }).registerWaiter.bind(hub)
    let settled = false
    try {
      const waiting = registerWaiter(
        'sbx',
        targetPredicate(
          new Set<SandboxState>(['started']),
          new Set<SandboxState>(['destroying', 'destroyed', 'error']),
        ),
        Date.now() + 2_147_483_647 + 5_000,
      ).promise.catch((error: unknown) => error).finally(() => {
        settled = true
      })
      await jest.advanceTimersByTimeAsync(2_147_483_647)
      await Promise.resolve()
      expect(settled).toBe(false)
      await jest.advanceTimersByTimeAsync(5_000)
      expect(await waiting).toBeInstanceOf(RequestTimeoutError)
    } finally {
      jest.useRealTimers()
    }
  })

  it('waitForState polls plainly when several targets are given', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, undefined, 'key')
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'booting' } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'stopped' } })
    const waiting = sandbox.waitForState(['started', 'stopped'], { timeout: 10_000 })
    await jest.advanceTimersByTimeAsync(200)
    await expect(waiting).resolves.toBeUndefined()
    expect(sandbox.state).toBe('stopped')
    // No server long-poll for a multi-target wait: plain GETs, no wait/timeout_ms.
    for (const call of get.mock.calls) expect(call[1]?.params).toBeUndefined()
  })

  it('waitForState never trusts a stale cached state (resume after a foreign pause)', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running', class: 'macos', persistent: true }, api, undefined, 'key')
    expect(sandbox.state).toBe('started') // stale: another client paused it since
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: {} })
    // The class is already known from the constructor response, so every GET below is
    // the waiter's. A returned-on-cache bug would show as 0 waits.
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'assigned', class: 'macos', persistent: true } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'running', class: 'macos', persistent: true } })
    const resumed = sandbox.resume({ timeout: 10_000 })
    await jest.advanceTimersByTimeAsync(200)
    await expect(resumed).resolves.toBeUndefined()
    expect(post).toHaveBeenCalledTimes(1)
    const waits = get.mock.calls.filter((call) => (call[1] as { params?: { wait?: string } } | undefined)?.params?.wait === 'running')
    expect(waits).toHaveLength(2) // refreshed before judging; never returned on the cached 'started'
  })

  it('waitForState treats destroying as progress only when waiting for destroyed', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'deleting' } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'deleted' } })
    const waiting = sandbox.waitForState(['destroyed'], { timeout: 10_000 })
    await jest.advanceTimersByTimeAsync(200)
    await expect(waiting).resolves.toBeUndefined()
    expect(sandbox.state).toBe('destroyed')

    const other = new Sandbox({ id: 'sbx2', status: 'running' }, api, undefined, 'key')
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'sbx2', status: 'deleting' } })
    await expect(other.waitForState(['started'], { timeout: 10_000 })).rejects.toMatchObject({ state: 'destroying' })
  })

  it('waitForState holds an exact long poll for destroyed and names a failed deletion', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'deleting' } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'failed', error: 'sandbox deletion did not finish' } })
    const waiting = sandbox.waitForState(['destroyed'], { timeout: 10_000 }).then(() => undefined, (e: unknown) => e)
    await jest.advanceTimersByTimeAsync(200)
    const error = await waiting
    expect(error).toBeInstanceOf(publicSdk.SandboxTerminalStateError)
    expect(error).toMatchObject({ state: 'error', reason: 'sandbox deletion did not finish' })
    expect((error as Error).message).toBe('sandbox sbx reached terminal state error: sandbox deletion did not finish')
    expect(sandbox.errorReason).toBe('sandbox deletion did not finish')
    // `wait=deleted` is already met by `deleting`, so every poll would answer
    // at once while the deletion finishes; the exact form holds it.
    expect(get).toHaveBeenCalledTimes(2)
    for (const call of get.mock.calls) {
      const params = (call[1] as { params?: Record<string, unknown> } | undefined)?.params
      expect(params).toMatchObject({ wait_exact: 'deleted' })
      expect(params).not.toHaveProperty('wait')
    }
  })

  it('waitForState keeps other targets on the loose long poll', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, undefined, 'key')
    const get = jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'sbx', status: 'running' } })
    await sandbox.waitForState(['started'], { timeout: 10_000 })
    const params = (get.mock.calls[0]?.[1] as { params?: Record<string, unknown> } | undefined)?.params
    expect(params).toMatchObject({ wait: 'running' })
    expect(params).not.toHaveProperty('wait_exact')
  })

  it('names the server reason after a hub rejection, unless the follow-up read moved on', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    const sandbox = new Sandbox({ id: 'sbx', status: 'deleting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'failed', error: 'sandbox deletion did not finish' } })
      // A late finish turned the row deleted between the frame and the read:
      // the read's error describes a different state, so it is not attached.
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'deleted', error: 'stale' } })
    jest.spyOn(hub, 'wait').mockRejectedValue(new publicSdk.SandboxTerminalStateError('sbx', 'error', { rawState: 'failed' }))
    try {
      const named = await sandbox.waitForState(['destroyed'], { timeout: 1_000 }).then(() => undefined, (e: unknown) => e)
      expect(named).toBeInstanceOf(publicSdk.SandboxTerminalStateError)
      expect(named).toMatchObject({ state: 'error', rawState: 'failed', reason: 'sandbox deletion did not finish' })

      const unnamed = await sandbox.waitForState(['started'], { timeout: 1_000 }).then(() => undefined, (e: unknown) => e)
      expect(unnamed).toBeInstanceOf(publicSdk.SandboxTerminalStateError)
      expect((unnamed as { reason?: string }).reason).toBeUndefined()
      expect((unnamed as Error).message).toBe('sandbox sbx reached terminal state error')
    } finally {
      get.mockRestore()
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('waitForState keeps the loose long poll for destroyed when another terminal state is named', async () => {
    // The exact form answers only on deleted, failed and lost: a wait that also
    // rejects on stopped must keep the loose form to see stopped at once.
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const get = jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'sbx', status: 'stopped' } })
    await expect(sandbox.waitForState(['destroyed'], { terminalStates: ['stopped', 'error'], timeout: 10_000 })).rejects.toMatchObject({ state: 'stopped' })
    const params = (get.mock.calls[0]?.[1] as { params?: Record<string, unknown> } | undefined)?.params
    expect(params).toMatchObject({ wait: 'deleted' })
    expect(params).not.toHaveProperty('wait_exact')
  })

  it('keeps a lost reason off a failed hub rejection', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    const sandbox = new Sandbox({ id: 'sbx', status: 'deleting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    // `failed` and `lost` both read as public `error`: the read's explanation
    // belongs to `lost`, not to the `failed` the hub reported.
    const get = jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'sbx', status: 'lost', error: 'the host stopped responding' } })
    jest.spyOn(hub, 'wait').mockRejectedValue(new publicSdk.SandboxTerminalStateError('sbx', 'error', { rawState: 'failed' }))
    try {
      const error = await sandbox.waitForState(['destroyed'], { timeout: 1_000 }).then(() => undefined, (e: unknown) => e)
      expect(error).toBeInstanceOf(publicSdk.SandboxTerminalStateError)
      expect(error).toMatchObject({ rawState: 'failed' })
      expect((error as { reason?: string }).reason).toBeUndefined()
      expect((error as Error).message).toBe('sandbox sbx reached terminal state error')
    } finally {
      get.mockRestore()
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('never attaches a reason it did not just read after a hub rejection', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    const sandbox = new Sandbox({ id: 'sbx', status: 'deleting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    // The follow-up read fails; the handle still holds an explanation from an
    // earlier read, which says nothing about this outcome.
    sandbox.errorReason = 'an earlier start failure'
    const get = jest.spyOn(api, 'get').mockRejectedValue(new ServiceUnavailableError('busy', { statusCode: 503, code: 'SERVICE_UNAVAILABLE', source: 'DAYTONA_API' }))
    jest.spyOn(hub, 'wait').mockRejectedValue(new publicSdk.SandboxTerminalStateError('sbx', 'error', { rawState: 'failed' }))
    try {
      const error = await sandbox.waitForState(['destroyed'], { timeout: 1_000 }).then(() => undefined, (e: unknown) => e)
      expect(error).toBeInstanceOf(publicSdk.SandboxTerminalStateError)
      expect(get).toHaveBeenCalledTimes(1)
      expect((error as { reason?: string }).reason).toBeUndefined()
      expect((error as Error).message).toBe('sandbox sbx reached terminal state error')
    } finally {
      get.mockRestore()
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('SandboxTerminalStateError without a reason keeps its message', () => {
    const error = new publicSdk.SandboxTerminalStateError('sbx', 'error', { rawState: 'failed', reason: '' })
    expect(error.message).toBe('sandbox sbx reached terminal state error')
    expect(error.reason).toBeUndefined()
  })

  it('updates the cached state before rethrowing a hub terminal-state rejection', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    jest.spyOn(hub, 'wait').mockRejectedValue(new publicSdk.SandboxTerminalStateError('sbx', 'error'))
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)

    try {
      await expect(sandbox.waitForState(['started'], { timeout: 1_000 })).rejects.toBeInstanceOf(publicSdk.SandboxTerminalStateError)
      expect(sandbox.state).toBe('error')
    } finally {
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('raises RequestTimeoutError when the hub poll fallback exhausts the caller budget', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    jest.spyOn(hub, 'wait').mockImplementation(async (_sandboxId, _predicate, options) => {
      await options?.sandboxPollFn?.()
      throw new RequestTimeoutError('sandbox failed to reach the requested state before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    })
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    const now = jest.spyOn(Date, 'now')
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', status: 'booting' } })

    try {
      now.mockReturnValueOnce(1_000)
      now.mockReturnValueOnce(1_000)
      now.mockReturnValueOnce(1_002)
      await expect(sandbox.waitForState(['started'], { timeout: 1 })).rejects.toBeInstanceOf(RequestTimeoutError)
      expect(get).toHaveBeenCalledTimes(1)
    } finally {
      get.mockRestore()
      now.mockRestore()
    }
  })

  it('fills in the handle after a wait the socket decided, and not after a polled one', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    // A socket frame carries only the state; create/start/resume hand back
    // this handle, so the toolbox URL, resources and error reason must come
    // from one GET, as on the polling path.
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running', memory_mib: 2048 } })
    const wait = jest.spyOn(hub, 'wait')
    try {
      wait.mockResolvedValueOnce({ state: 'started', rawState: 'running' })
      await sandbox.waitForState(['started'], { timeout: 1_000 })
      expect(get).toHaveBeenCalledTimes(1)
      expect(sandbox.memory).toBe(2)

      // Decided by the fallback poll: that poll already did the GET.
      wait.mockImplementationOnce(async (_id, _predicate, options) => options!.sandboxPollFn!())
      get.mockClear()
      await sandbox.waitForState(['started'], { timeout: 1_000 })
      expect(get).toHaveBeenCalledTimes(1)

      // The refresh shares the caller's budget.
      wait.mockResolvedValueOnce({ state: 'started', rawState: 'running' })
      get.mockClear()
      await sandbox.waitForState(['started'], { timeout: 1_000 })
      const config = get.mock.calls[0]?.[1] as { timeout?: number } | undefined
      expect(config?.timeout).toBeLessThanOrEqual(1_000)

      // A destroyed sandbox has nothing to fill in: no GET (it would 404).
      wait.mockResolvedValueOnce({ state: 'destroyed', rawState: 'deleted' })
      get.mockClear()
      await sandbox.waitForState(['destroyed'], { timeout: 1_000 })
      expect(get).not.toHaveBeenCalled()
    } finally {
      get.mockRestore()
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('fills in the error reason when the socket reports a terminal state', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx', status: 'failed', error: 'image pull failed' } })
    jest.spyOn(hub, 'wait').mockRejectedValue(new publicSdk.SandboxTerminalStateError('sbx', 'error'))
    try {
      await expect(sandbox.waitForState(['started'], { timeout: 1_000 })).rejects.toBeInstanceOf(publicSdk.SandboxTerminalStateError)
      expect(sandbox.errorReason).toBe('image pull failed')
    } finally {
      get.mockRestore()
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('bounds the terminal-outcome refresh by the wait budget, and skips it for destroyed', async () => {
    // Real timers: the stalled GET is released only by its own timeout.
    jest.useRealTimers()
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    // A GET that hangs unless it carries a timeout.
    const get = jest.spyOn(api, 'get').mockImplementation(async (_url, config) => {
      const ms = (config as { timeout?: number } | undefined)?.timeout
      return await new Promise((_, reject) => {
        if (ms !== undefined && ms > 0) setTimeout(() => reject(new RequestTimeoutError('timed out', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })), ms)
      })
    })
    const wait = jest.spyOn(hub, 'wait')
    try {
      wait.mockRejectedValueOnce(new publicSdk.SandboxTerminalStateError('sbx', 'error'))
      const started = Date.now()
      await expect(sandbox.waitForState(['started'], { timeout: 200 })).rejects.toBeInstanceOf(publicSdk.SandboxTerminalStateError)
      expect(Date.now() - started).toBeLessThan(2_000)

      wait.mockRejectedValueOnce(new publicSdk.SandboxTerminalStateError('sbx', 'destroyed'))
      get.mockClear()
      await expect(sandbox.waitForState(['started'], { timeout: 200 })).rejects.toBeInstanceOf(publicSdk.SandboxTerminalStateError)
      expect(get).not.toHaveBeenCalled()
    } finally {
      get.mockRestore()
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('updates the cached state before rethrowing a timed-out hub wait', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    jest.spyOn(hub, 'wait').mockImplementation(async (_sandboxId, _predicate, options) => {
      options?.onObserved?.({ state: 'stopped', rawState: 'stopped' })
      throw new RequestTimeoutError('sandbox failed to reach the requested state before timeout', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    })
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, undefined, undefined, hub)
    sandbox.rawState = 'running'

    try {
      await expect(sandbox.waitForState(['started'], { timeout: 1_000 })).rejects.toBeInstanceOf(RequestTimeoutError)
      expect(sandbox.state).toBe('stopped')
      expect(sandbox.rawState).toBe('stopped')
    } finally {
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('normalizes a local target to no region hint for bare UUID waits', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    delete process.env['DAYTONA_SANDBOX_EVENTS']
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const hub = new WaitHub('http://api.invalid', 'key', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, api)
    const wait = jest.spyOn(hub, 'wait').mockResolvedValue({ state: 'started', rawState: 'running' })
    const sandbox = new Sandbox({ id: '550e8400-e29b-41d4-a716-446655440000', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', 'local', {}, undefined, undefined, hub)
    jest.spyOn(api, 'get').mockResolvedValue({ data: { id: '550e8400-e29b-41d4-a716-446655440000', status: 'running' } })

    try {
      await sandbox.waitForState(['started'], { timeout: 1_000 })

      expect(wait).toHaveBeenCalledWith(
        '550e8400-e29b-41d4-a716-446655440000',
        expect.any(Object),
        expect.objectContaining({ regionHint: undefined }),
      )
    } finally {
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('process.start/run/exec accept the argv object form like Python', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p', kind: 'exec', state: 'terminal', exitCode: 0, logs: { frames: [], eof: true } })
    const service = new ProcessService(transport.object, 'sbx')
    await service.start({ argv: ['python', '-c', 'print(1)'] })
    expect(transport.post.mock.calls[0]?.[1]).toMatchObject({ argv: ['python', '-c', 'print(1)'] })
    expect(transport.post.mock.calls[0]?.[1]).not.toHaveProperty('shellCommand')
    await service.run({ argv: ['echo', 'a b'] })
    expect(transport.post.mock.calls[1]?.[1]).toMatchObject({ argv: ['echo', 'a b'] })
    await service.run({ cmd: 'echo hi' })
    expect(transport.post.mock.calls[2]?.[1]).toMatchObject({ shellCommand: 'echo hi' })
    await service.run('echo str')
    expect(transport.post.mock.calls[3]?.[1]).toMatchObject({ shellCommand: 'echo str' })
  })

  it('waitForState rejects an empty target list before polling', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const get = jest.spyOn(api, 'get')
    await expect(lazySandbox(client, 'sbx').waitForState([], { timeout: 1_000 })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(get).not.toHaveBeenCalled()
  })

  it('waitForState backs off when the API answers a long-poll early', async () => {
    jest.useFakeTimers()
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, undefined, 'key')
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'booting' } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'booting' } })
      .mockResolvedValueOnce({ data: { id: 'sbx', status: 'running' } })
    try {
      const waiting = sandbox.waitForState(['started'], { timeout: 10_000 })
      // Two instant answers: without the back-off floor the third refresh would
      // already have fired here.
      await jest.advanceTimersByTimeAsync(0)
      expect(get).toHaveBeenCalledTimes(1)
      await jest.advanceTimersByTimeAsync(199)
      expect(get).toHaveBeenCalledTimes(1)
      await jest.advanceTimersByTimeAsync(1)
      expect(get).toHaveBeenCalledTimes(2)
      await jest.advanceTimersByTimeAsync(200)
      expect(get).toHaveBeenCalledTimes(3)
      await expect(waiting).resolves.toBeUndefined()
      for (const call of get.mock.calls) expect(call).toEqual(['/sandboxes/sbx', { timeout: expect.any(Number), params: { wait: 'running', timeout_ms: expect.any(Number) } }])
    } finally {
      jest.useRealTimers()
    }
  })

  it('waitForState strictly encodes sandbox ids in long-poll refreshes', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: "sbx!'()*", status: 'booting' }, api, undefined, 'key')
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { id: "sbx!'()*", status: 'running' } })

    await expect(sandbox.waitForState(['started'], { timeout: 1_000 })).resolves.toBeUndefined()
    expect(get.mock.calls[0]?.[0]).toBe('/sandboxes/sbx%21%27%28%29%2A')
  })

  it('exports the preview warning skip header constants from the v2 namespace only', async () => {
    const ports = await import('../src/v2/Ports')
    const root = await import('../src/index')
    expect(publicSdk.PREVIEW_WARNING_SKIP_HEADER).toBe('X-Daytona-Skip-Preview-Warning')
    expect(publicSdk.PREVIEW_WARNING_SKIP_HEADER_VALUE).toBe('true')
    expect(ports.PREVIEW_WARNING_SKIP_HEADER).toBe(publicSdk.PREVIEW_WARNING_SKIP_HEADER)
    expect(ports.PREVIEW_WARNING_SKIP_HEADER_VALUE).toBe(publicSdk.PREVIEW_WARNING_SKIP_HEADER_VALUE)
    expect('PREVIEW_WARNING_SKIP_HEADER' in root).toBe(false)
    expect('PREVIEW_WARNING_SKIP_HEADER_VALUE' in root).toBe(false)
  })

  it('mints a ports-scoped sandbox token for private preview links when a key is present', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'ep', port: 8080, public: false, url: 'https://p' } })
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { url: 'https://p', token: 'preview-token' } })
      .mockResolvedValueOnce({
        data: { access_token: 'scoped-jwt', token_type: 'Bearer', expires_at: '2027-01-01T00:00:00Z', operations: [], ports: [8080] },
      })

    // URL unchanged; the token is the ports-scoped sandbox token.
    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: 'scoped-jwt', expiresAt: '2027-01-01T00:00:00Z' })
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080/preview-url')
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/access-token', { operations: [], ports: [8080] })
  })

  it('previewLink mints a port-scoped token even when the route token has JWT claims', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const routeToken = sandboxJwt(1_900_000_000)
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { port: 8080, public: false, url: 'https://p' } })
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { url: 'https://p', token: routeToken } })
      .mockResolvedValueOnce({
        data: { access_token: 'scoped-jwt', token_type: 'Bearer', expires_at: '2027-01-01T00:00:00Z', operations: [], ports: [8080] },
      })

    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: 'scoped-jwt', expiresAt: '2027-01-01T00:00:00Z' })
    expect(post.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/sbx/ports/8080/preview-url', '/sandboxes/sbx/access-token'])
  })

  it.each([
    [1_900_000_000.5, '2030-03-17T17:46:40.500Z'],
    [1_900_000_000.1239, '2030-03-17T17:46:40.123Z'],
  ])('previewLink floors a fractional exp %p to whole milliseconds like the Python SDK', async (exp, want) => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, undefined)
    const routeToken = sandboxJwt(exp)
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { port: 8080, public: false, url: 'https://p' } })
    jest.spyOn(api, 'post').mockResolvedValueOnce({ data: { url: 'https://p', token: routeToken } })

    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: routeToken, expiresAt: want })
  })

  it('previewLink mints for a malformed JWT-like route token without raising', async () => {
    // A malformed route token does not prevent minting a port-scoped token.
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const routeToken = 'eyJhbGciOiJFUzI1NiIsInR5cCI6ImF0K2p3dCJ9.bm90LWpzb24.signature'
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { port: 8080, public: false, url: 'https://p' } })
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { url: 'https://p', token: routeToken } })
      .mockResolvedValueOnce({
        data: { access_token: 'scoped-jwt', token_type: 'Bearer', expires_at: '2027-01-01T00:00:00Z', operations: [], ports: [8080] },
      })

    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: 'scoped-jwt', expiresAt: '2027-01-01T00:00:00Z' })
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/access-token', { operations: [], ports: [8080] })
  })

  it('previewLink returns a route token with an unrepresentable expiry when minting is unavailable', async () => {
    // The expiry is unknown but the token is still reusable (no mint).
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, undefined)
    const routeToken = sandboxJwt(1e13)
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { port: 8080, public: false, url: 'https://p' } })
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { url: 'https://p', token: routeToken } })
      .mockImplementation(() => {
        throw new Error('unexpected mint')
      })

    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: routeToken })
    expect(post.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/sbx/ports/8080/preview-url'])
  })

  it('previewLink prefers a minted token over a route token with standard JWT claims', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { port: 8080, public: false, url: 'https://p' } })
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { url: 'https://p', token: sandboxJwt(1_900_000_000, 'unrelated-application') } })
      .mockResolvedValueOnce({
        data: { access_token: 'scoped-jwt', token_type: 'Bearer', expires_at: '2027-01-01T00:00:00Z', operations: [], ports: [8080] },
      })

    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: 'scoped-jwt', expiresAt: '2027-01-01T00:00:00Z' })
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/access-token', { operations: [], ports: [8080] })
  })

  it('previewLink raises a typed error on a malformed preview-url body', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { port: 8080, public: false, url: 'https://p' } })
    jest.spyOn(api, 'post').mockResolvedValueOnce({ data: { url: 'https://p', token: null } })

    const caught = await sandbox.ports.previewLink(8080).catch((error: unknown) => error)
    expect(caught).toBeInstanceOf(BadRequestError)
    expect(caught).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  })

  it('previewLink falls back to the route token when the port-scoped mint fails transiently', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'ep', port: 8080, public: false, url: 'https://p' } })
    // The preview-url route already answered with a usable token; a transient
    // mint failure must not discard it and fail the whole call.
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { url: 'https://p', token: 'preview-token' } })
      .mockRejectedValueOnce(new DaytonaError('boom', { statusCode: 500 }))

    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: 'preview-token' })
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/access-token', { operations: [], ports: [8080] })
  })

  it('passes the route token through for a caller-supplied-token configuration', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key', undefined, { accessToken: 'caller-token' })
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'ep', port: 8080, public: false, url: 'https://p' } })
    const post = jest.spyOn(api, 'post').mockResolvedValueOnce({ data: { url: 'https://p', token: 'preview-token' } })

    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: 'preview-token' })
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('previewLink returns no token expiry for public endpoints', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const get = jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { port: 8080, public: true, url: 'https://p' } })
    const post = jest.spyOn(api, 'post')

    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: '' })
    expect(get).toHaveBeenCalledTimes(1)
    expect(post).not.toHaveBeenCalled()
  })

  it('creates a private preview endpoint before fetching its token link', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    jest.spyOn(api, 'get').mockRejectedValueOnce(new NotFoundError('missing', { statusCode: 404, code: 'NOT_FOUND' }))
    jest.spyOn(api, 'put').mockResolvedValueOnce({ data: { id: 'ep', port: 8080, public: false, url: 'https://p' } })
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { url: 'https://p', token: 'preview-token' } })
      .mockResolvedValueOnce({
        data: { access_token: 'scoped-jwt', token_type: 'Bearer', expires_at: '2027-01-01T00:00:00Z', operations: [], ports: [8080] },
      })

    await expect(sandbox.ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: 'scoped-jwt', expiresAt: '2027-01-01T00:00:00Z' })
    expect(api.put).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080', { public: false })
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080/preview-url')
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/access-token', { operations: [], ports: [8080] })
  })

  it('mints signed preview links with an explicit TTL query', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: { url: 'https://8080-sdeadbeef.preview', token: 'sdeadbeef', expires_at: '2026-08-28T00:05:00Z' } })

    await expect(sandbox.ports.signedPreviewLink(8080, { expiresInSeconds: 300 })).resolves.toEqual({ url: 'https://8080-sdeadbeef.preview', token: 'sdeadbeef', expiresAt: '2026-08-28T00:05:00Z' })
  expect(post).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080/signed-preview-urls', { expires_in_seconds: 300 })
    expect('getSignedPreviewLink' in sandbox).toBe(false)
  })

  it('omits the TTL query when minting signed preview links with the server default', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: { url: 'https://8080-sdeadbeef.preview', token: 'sdeadbeef', expires_at: '2026-08-28T00:01:00Z' } })

    await expect(sandbox.ports.signedPreviewLink(8080)).resolves.toEqual({ url: 'https://8080-sdeadbeef.preview', token: 'sdeadbeef', expiresAt: '2026-08-28T00:01:00Z' })
  expect(post).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080/signed-preview-urls', {})
  })

  it('rejects non-positive signed preview TTLs before posting', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const post = jest.spyOn(api, 'post')

    await expect(sandbox.ports.signedPreviewLink(8080, { expiresInSeconds: 0 })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    await expect(sandbox.ports.signedPreviewLink(8080, { expiresInSeconds: -1 })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(post).not.toHaveBeenCalled()
  })

  it('expires signed preview links with a URL-encoded token path segment', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: "sbx!'()*", status: 'running' }, api, undefined, 'key')
    const post = jest.spyOn(api, 'post').mockResolvedValue({ status: 204, data: undefined })

    await expect(sandbox.ports.expireSignedPreviewLink(8080, 'tok/en')).resolves.toBeUndefined()
    expect(post).toHaveBeenCalledWith(
      "/sandboxes/sbx%21%27%28%29%2A/ports/8080/signed-preview-urls/revoke",
      { token: 'tok/en' },
    )
    expect('expireSignedPreviewLink' in sandbox).toBe(false)
  })

  it('accepts toolbox_proxy_url and next_cursor response fields', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { sandboxes: [{ id: 'sbx', status: 'running', toolbox_proxy_url: 'http://toolbox' }], next_cursor: 'next' } })
    const page = await client.list()
    expect(get).toHaveBeenCalledTimes(1)
    expect(page.nextCursor).toBe('next')
    expect(page.items[0]?.toolboxProxyUrl).toBe('http://toolbox/sbx')
  })

  it.each([
    ['list', (client: Daytona) => client.list()],
    ['filtered list (search route)', (client: Daytona) => client.list({ name: 'sbx' })],
    ['search', (client: Daytona) => client.search({ status: ['running'] })],
  ])('%s handles reach each response\'s regional toolbox without a refresh', async (_label, read) => {
    const metrics = { timestamp: '2026-10-08T00:00:00Z', cpuCount: 1, cpuUtilization: 0, memoryUsageBytes: 0, memoryTotalBytes: 0, diskUsageBytes: 0, diskTotalBytes: 0 }
    const hits: string[] = []
    const region = async (name: string): Promise<[Server, string]> => {
      const server = createServer((req, res) => {
        hits.push(`${name} ${req.method} ${req.url} ${req.headers.authorization}`)
        res.setHeader('content-type', 'application/json')
        res.end(JSON.stringify(metrics))
      })
      await new Promise<void>((done) => server.listen(0, '127.0.0.1', done))
      return [server, `http://127.0.0.1:${(server.address() as AddressInfo).port}/sandbox-api`]
    }
    const [firstServer, firstUrl] = await region('region-a')
    const [secondServer, secondUrl] = await region('region-b')
    try {
      const client = new Daytona({ apiKey: 'key' })
      const api = clientInstance(client)
      const get = jest.spyOn(api, 'get').mockResolvedValue({
        data: {
          sandboxes: [
            { id: 'sbx-a', status: 'running', region: 'region-a', toolbox_proxy_url: firstUrl },
            { id: 'sbx-b', status: 'running', region: 'region-b', toolbox_proxy_url: secondUrl },
          ],
          next_cursor: null,
        },
      })
      jest.spyOn(api, 'post').mockResolvedValue({
        data: { access_token: 'jwt', token_type: 'Bearer', expires_at: '2099-01-01T00:00:00Z', operations: [], ports: [] },
      })
      const page = await read(client)
      for (const sandbox of page.items) await sandbox.metrics()
      expect(get).toHaveBeenCalledTimes(1)
      expect(hits).toEqual([
        'region-a GET /sandbox-api/sbx-a/system/metrics Bearer jwt',
        'region-b GET /sandbox-api/sbx-b/system/metrics Bearer jwt',
      ])
    } finally {
      firstServer.close()
      secondServer.close()
    }
  })

  it('returns one sandbox page and iter follows cursors explicitly', async () => {
    const client = new Daytona({ apiKey: 'key' })
    let call = 0
    jest.spyOn(clientInstance(client), 'get').mockImplementation(async () => {
      call += 1
      return { data: { sandboxes: [{ id: `sbx-${call}`, status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }], next_cursor: `cursor-${call}` } }
    })
    const page = await client.list()
    expect(call).toBe(1)
    expect(page.items).toHaveLength(1)
    expect(page.nextCursor).toBe('cursor-1')

    const iteratorClient = new Daytona({ apiKey: 'key' })
    const iteratorGet = jest.spyOn(clientInstance(iteratorClient), 'get')
    iteratorGet
      .mockResolvedValueOnce({ data: { sandboxes: [{ id: 'sbx-1', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }], next_cursor: 'cursor-1' } })
      .mockResolvedValueOnce({ data: { sandboxes: [{ id: 'sbx-2', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }], next_cursor: 'cursor-2' } })
      .mockResolvedValueOnce({ data: { sandboxes: [{ id: 'sbx-3', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }], next_cursor: 'cursor-2' } })
    const all = []
    for await (const sandbox of iteratorClient.iter()) all.push(sandbox.id)
    expect(all).toEqual(['sbx-1', 'sbx-2', 'sbx-3'])
  })

  it('reads the sandbox class and image from metadata', () => {
    const sandbox = new Sandbox(
      { id: 'mac', status: 'running', class: 'macos', image: { id: 'img_mac', name: 'mac-base' } },
      undefined,
      undefined,
      'key',
    )
    expect(sandbox.image).toEqual({ id: 'img_mac', name: 'mac-base' })
    expect(sandbox.sandboxClass).toBe('macos')
  })

  it('maps statuses, units, and code-specific errors', () => {
    expect(sandboxState('running')).toBe('started')
    expect(sandboxState('pausing')).toBe('pausing')
    expect(sandboxState('paused')).toBe('paused')
    expect(gbToMib(1.5)).toBe(1536)
    expect(mibToGb(1536)).toBe(2)
    expect(createDaytonaError('conflict', 409, 'NAME_CONFLICT', 'DAYTONA_DAEMON')).toBeInstanceOf(NameConflictError)
    expect(createDaytonaError('expired', 409, 'CURSOR_EXPIRED', 'DAYTONA_DAEMON')).toBeInstanceOf(CursorExpiredError)
    expect(createDaytonaError('unsupported', 400, 'UNSUPPORTED_OPERATION', 'DAYTONA_DAEMON')).toBeInstanceOf(UnsupportedOperationError)
  })

  it('never resolves a dispatch class through Object.prototype keys', () => {
    const viaPrototype = createDaytonaError('bad', 500, 'constructor', 'DAYTONA_API')
    expect(viaPrototype).toBeInstanceOf(InternalServerError)
    expect(createDaytonaError('bad', 400, 'hasOwnProperty', 'toString')).toBeInstanceOf(BadRequestError)
  })
})

describe('list fallback detail reads', () => {
  it('keeps the page when one fallback detail read fails, and caps concurrent reads', async () => {
    jest.useRealTimers()
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const ids = Array.from({ length: 20 }, (_, i) => `s${i}`)
    let inFlight = 0
    let peak = 0
    jest.spyOn(api, 'get').mockImplementation(async (url: string) => {
      if (url === '/sandboxes') return { data: { sandboxes: ids.map((id) => ({ id, status: 'running' })), next_cursor: null } }
      const id = url.split('/').pop() ?? ''
      inFlight++
      peak = Math.max(peak, inFlight)
      await new Promise((resolve) => setTimeout(resolve, 5))
      inFlight--
      if (id === 's3') throw new Error('404 unknown sandbox')
      return { data: { id, status: 'running', toolbox_proxy_url: 'https://p.example/sandbox-api' } }
    })

    const page = await client.list()

    expect(page.items.map((s) => s.id)).toEqual(ids)
    expect(page.items[3].toolboxProxyUrl).toBeUndefined()
    expect(page.items[4].toolboxProxyUrl).toBe('https://p.example/sandbox-api/s4')
    expect(() => page.items[3].process).toThrow('sandbox handle has no toolbox URL')
    expect(peak).toBeGreaterThan(1)
    expect(peak).toBeLessThanOrEqual(8)
  })
})

describe('events hub identity under JWT auth', () => {
  it('gives each JWT its own hub, and shares only an identical JWT', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const mk = (jwtToken: string) => new Daytona({ jwtToken, projectId: 'project-1' })
    const a = mk('user-a-jwt')
    const b = mk('user-b-jwt')
    const a2 = mk('user-a-jwt')
    try {
      const hub = (d: Daytona) => (d as unknown as { acquireHub(): unknown }).acquireHub()
      const ha = hub(a)
      expect(ha).toBeDefined()
      expect(hub(b)).not.toBe(ha)
      expect(hub(a2)).toBe(ha)
      const key = (a as unknown as { hubIdentityKey: string }).hubIdentityKey
      expect(key).toContain('jwt:project-1:')
      expect(key).not.toContain('user-a-jwt')
    } finally {
      await Promise.all([a.close(), b.close(), a2.close()])
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })
})

describe('recurring early probe', () => {
  it('resolves via probe when upgrade is black-holed and sandbox becomes ready', async () => {
    jest.useRealTimers()
    const PROBE_MS = 50
    const READY_AFTER_PROBES = 5
    let probeCount = 0
    const pollFn = jest.fn(async () => ({
      state: (probeCount++ >= READY_AFTER_PROBES ? 'started' : 'creating') as import('../src/v2/types').SandboxState,
      rawState: probeCount > READY_AFTER_PROBES ? 'running' : 'creating',
    }))
    const hub = new WaitHub(
      'http://api.invalid',
      'test-key',
      { enabled: true },
      (() => new Promise(() => {})) as unknown as WebSocketFactory,
      undefined,
      { earlyProbeMs: PROBE_MS, openTimeoutMs: 30_000, noFrameTimeoutMs: 70_000 },
    )
    hub.attach()
    try {
      const result = await hub.wait('sb-probe-1', targetPredicate(new Set<SandboxState>(['started']), new Set<SandboxState>(['destroyed', 'error'])), {
        timeout: 10_000,
        sandboxPollFn: pollFn,
      })
      expect(result.state).toBe('started')
      expect(pollFn).toHaveBeenCalledTimes(READY_AFTER_PROBES + 1)
    } finally {
      await hub.detach()
    }
  }, 5000)
})

describe('client identity headers', () => {
  it('committed src/version.ts matches package.json (drift guard — no pretest re-stamp)', () => {
    const pkg = JSON.parse(readFileSync(resolve(__dirname, '../package.json'), 'utf8')) as { version: string }
    expect(SDK_VERSION).toBe(pkg.version)
  })

  it('sends the Daytona SDK identity headers outside browsers', () => {
    expect(authHeaders('k')).toEqual({
      Authorization: 'Bearer k',
      'X-Daytona-Source': 'sdk-typescript',
      'X-Daytona-SDK-Version': SDK_VERSION,
      'User-Agent': `sdk-typescript/${SDK_VERSION}`,
    })
    expect(authHeaders()).not.toHaveProperty('Authorization')
  })

  it('keeps X-Daytona-* but drops the browser-forbidden User-Agent in a browser-like global', () => {
    const g = globalThis as { window?: unknown }
    g.window = {}
    try {
      expect(authHeaders('k')).toEqual({
        Authorization: 'Bearer k',
        'X-Daytona-Source': 'sdk-typescript',
        'X-Daytona-SDK-Version': SDK_VERSION,
      })
    } finally {
      delete g.window
    }
  })
})

describe('creation shape', () => {
  it('creates a linux sandbox from an image name', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', name: 'sandbox', status: 'running', wait_cap_seconds: 3 } })

    const sandbox = await client.create('python:3.12', { cpu: 2, memory: 4, createTimeoutSeconds: 90, waitSeconds: 60 })

    expect(sandbox.id).toBe('sbx')
    expect(post).toHaveBeenCalledWith('/sandboxes', expect.objectContaining({ image: 'python:3.12', cpu: 2, memory_mib: 4096, create_timeout_seconds: 90, wait_seconds: 60 }), expect.any(Object))
  })

  it('sets rawState from the create response when create already returns a running sandbox', async () => {
    const client = newClient()
    jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', name: 'sandbox', status: 'running', wait_cap_seconds: 3 } })

    const sandbox = await client.create('python:3.12', { waitSeconds: 60 })

    expect(sandbox.state).toBe('started')
    expect(sandbox.rawState).toBe('running')
  })

  it('creates from snapshot on the snapshot route with shared fields only', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', name: 'sandbox', status: 'queued', wait_cap_seconds: 3 } })
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: sandboxDetail({ status: 'queued', image: null, snapshot: { id: 'snap_123', name: 'after-setup' } }) })

    const sandbox = await client.createFromSnapshot('after-setup', { name: 'restore', waitSeconds: 0, labels: { role: 'worker' } })

    expect(sandbox.id).toBe('sbx')
    // `name` stamps the `name` label next to the caller's own.
    expect(post).toHaveBeenCalledWith('/sandboxes/from-snapshot', expect.objectContaining({ snapshot: 'after-setup', name: 'restore', labels: { role: 'worker', name: 'restore' }, wait_seconds: 0 }), expect.any(Object))
    expect(get).not.toHaveBeenCalled()
  })
  it('throws a runtime TypeError when create receives a non-string source', async () => {
    const client = newClient()
    const create = Reflect.get(client, 'create') as (image: unknown) => Promise<unknown>

    await expect(create.call(client, ImageBuilder.fromRegistry('python:3.12'))).rejects.toThrow(/createFromBuild/)
  })

  it('wait=0 resolves after the create response and wait=60 long-polls the remainder', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValueOnce({ data: { id: 'queued', name: 'queued', status: 'queued', wait_cap_seconds: 3 } })
      .mockResolvedValueOnce({ data: { id: 'waiting', name: 'waiting', status: 'queued', wait_cap_seconds: 3 } })
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: sandboxDetail({ id: 'waiting', status: 'running' }) })

    await client.create('python:3.12', { waitSeconds: 0 })
    await client.create('python:3.12', { waitSeconds: 60 })

    expect(post.mock.calls[0]?.[1]).toMatchObject({ wait_seconds: 0 })
    expect(post.mock.calls[1]?.[1]).toMatchObject({ wait_seconds: 60 })
    expect(get).toHaveBeenCalledWith('/sandboxes/waiting', { timeout: 35000, params: { wait: 'running', timeout_ms: 30000 } })
  })

  it('a create-returned sandbox waits via the socket hub and only refreshes once after the socket decision', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const client = newClient()
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockResolvedValue({
      data: { id: 'sbx', name: 'sandbox', status: 'booting', wait_cap_seconds: 0 },
        headers: { 'x-daytona-cell': 'route-a' },
    })
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: sandboxDetail({ id: 'sbx', status: 'running', region_id: 'region-a' }) })
    try {
      const hub = (client as unknown as { acquireHub(): WaitHub | undefined }).acquireHub()
      expect(hub).toBeDefined()
      const wait = jest.spyOn(hub!, 'wait').mockImplementation(async () => {
        expect(get).toHaveBeenCalledTimes(0)
        return { state: 'started', rawState: 'running' }
      })

      const sandbox = await client.create('python:3.12', { waitSeconds: 0 })
      await sandbox.waitForState(['started'], { timeout: 1_000 })

      expect(post).toHaveBeenCalledTimes(1)
      expect(wait).toHaveBeenCalledTimes(1)
      expect(get).toHaveBeenCalledTimes(1)
      expect(get).toHaveBeenCalledWith('/sandboxes/sbx', expect.objectContaining({ timeout: expect.any(Number) }))
    } finally {
      await client.close()
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('createFromBuild fails before sandbox create when the image build fails', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockImplementation(async (path: string) => {
      if (path === '/images') return { data: { id: 'img_failed', name: null, region: 'region-a', status: 'pending' } }
      throw new Error(`unexpected post ${path}`)
    })
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: imageResponse({ id: 'img_failed', status: 'failed', error: 'boom', anonymous: true, name: null }) })

    await expect(client.createFromBuild(ImageBuilder.fromRegistry('python:3.12').runCommands('echo hi'))).rejects.toMatchObject({ buildError: 'boom', imageId: 'img_failed', statusCode: 409, code: 'IMAGE_BUILD_FAILED', source: 'SDK' })
    await expect(client.createFromBuild(ImageBuilder.fromRegistry('python:3.12').runCommands('echo hi'))).rejects.toBeInstanceOf(ImageBuildFailedError)
    expect(post.mock.calls.filter(([path]) => path === '/sandboxes')).toHaveLength(0)
    expect(get.mock.calls.every(([path]) => String(path).startsWith('/images/'))).toBe(true)
  })

  it('createFromBuild passes the remaining wait budget to create()', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockImplementation(async (path: string) => {
      if (path === '/images') return { data: { id: 'img_wait', name: null, region: 'region-a', status: 'pending' } }
      if (path === '/sandboxes') return { data: { id: 'sbx', status: 'running', wait_cap_seconds: 3 } }
      throw new Error(`unexpected post ${path}`)
    })
    jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: imageResponse({ id: 'img_wait', status: 'pending', anonymous: true, name: null }) })
      .mockResolvedValueOnce({ data: imageResponse({ id: 'img_wait', anonymous: true, name: null }) })
    const now = jest.spyOn(Date, 'now')
    try {
      now.mockReturnValueOnce(0)
      now.mockReturnValueOnce(0)
      now.mockReturnValueOnce(3_200)
      now.mockReturnValueOnce(3_200)
      await client.createFromBuild(ImageBuilder.fromDockerfile('FROM python:3.12'), { waitSeconds: 5 })
    } finally {
      now.mockRestore()
    }

    expect(post.mock.calls.find(([path]) => path === '/sandboxes')?.[1]).toMatchObject({ image: 'img_wait', wait_seconds: 2 })
  })

  it('createFromBuild times out while the build is still pending and never posts /sandboxes', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockImplementation(async (path: string) => {
      if (path === '/images') return { data: { id: 'img_wait', name: null, region: 'region-a', status: 'pending' } }
      if (path === '/sandboxes') return { data: { id: 'sbx', status: 'running', wait_cap_seconds: 3 } }
      throw new Error(`unexpected post ${path}`)
    })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: imageResponse({ id: 'img_wait', status: 'pending', anonymous: true, name: null }) })
    const now = jest.spyOn(Date, 'now')
    try {
      now.mockReturnValueOnce(0)
      now.mockReturnValueOnce(0)
      now.mockReturnValueOnce(6_000)
      const failure = client.createFromBuild(ImageBuilder.fromDockerfile('FROM python:3.12'), { waitSeconds: 5 })
      await expect(failure).rejects.toBeInstanceOf(RequestTimeoutError)
      await expect(failure).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    } finally {
      now.mockRestore()
    }

    expect(post.mock.calls.filter(([path]) => path === '/sandboxes')).toHaveLength(0)
  })

  // Name-derived idempotency keys. The SAME vectors are pinned in
  // sdk-python/tests/test_core.py (NAME_KEY_VECTORS): the two SDKs must
  // derive byte-identical keys, or a Python and a TypeScript caller creating
  // the same end user's sandbox would get two.
  const sha256 = (value: string): string => createHash('sha256').update(value, 'utf8').digest('hex')
  const NAME_KEY_VECTORS: Array<[string, string]> = [
    ['example-user', 'name:example-user'],
    ['Example 2 (test)', 'name:Example 2 (test)'],
    ['a'.repeat(251), 'name:' + 'a'.repeat(251)],
    // One over the 256-character key cap: hashed.
    ['a'.repeat(252), 'name:sha256:' + sha256('a'.repeat(252))],
    // Non-ASCII: hashed over the UTF-8 bytes.
    ['météo', 'name:sha256:54d1ed6448e2d6fe64cf3356d89435a041992b4f761a692d4f33fd8e8869ad74'],
    // Surrounding whitespace would be trimmed by the API / stripped by HTTP: hashed.
    [' example-user', 'name:sha256:dc734153d169d526ca4e514011da05c95fa28ad25d9ba57483c612638e46a78c'],
    ['example-user\t', 'name:sha256:' + sha256('example-user\t')],
    // A control character is not printable ASCII: hashed.
    ['example\x01user', 'name:sha256:' + sha256('example\x01user')],
  ]

  it.each(NAME_KEY_VECTORS)('idempotencyKeyForName parity vectors: %j', async (name, expected) => {
    const key = await idempotencyKeyForName(name)
    expect(key).toBe(expected)
    // Every derived key is one the API accepts verbatim.
    expect(key).toBe(key.trim())
    expect(key.length).toBeLessThanOrEqual(256)
    expect(/^[\x20-\x7e]*$/.test(key)).toBe(true)
  })

  it('idempotencyKeyForName rejects non-strings and exports the label name', async () => {
    await expect(idempotencyKeyForName(42 as unknown as string)).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(NAME_LABEL).toBe('name')
    expect(publicSdk.idempotencyKeyForName).toBe(idempotencyKeyForName)
    expect(publicSdk.NAME_LABEL).toBe(NAME_LABEL)
  })

  it('a named create stamps the name label and derives the key', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', status: 'running', wait_cap_seconds: 3 } })

    await client.create('py312', { name: 'example-user', labels: { team: 'cli' } })

    const [path, body, config] = post.mock.calls[0] as [string, Record<string, unknown>, { headers: Record<string, string> }]
    expect(path).toBe('/sandboxes')
    expect(body.name).toBe('example-user')
    expect(body.labels).toEqual({ team: 'cli', name: 'example-user' })
    expect(body.idempotency_key).toBe('name:example-user')
    expect(config.headers['Idempotency-Key']).toBe('name:example-user')
  })

  it.each([
    ['createFromSnapshot', 'snap', '/sandboxes/from-snapshot'],
  ] as const)('%s stamps the name label and derives the key', async (method, arg, path) => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', status: 'running', wait_cap_seconds: 3 } })

    await (client[method] as (source: string, options: { name: string }) => Promise<unknown>)(arg, { name: 'example-user' })

    const [calledPath, body, config] = post.mock.calls[0] as [string, Record<string, unknown>, { headers: Record<string, string> }]
    expect(calledPath).toBe(path)
    expect(body.labels).toEqual({ name: 'example-user' })
    expect(config.headers['Idempotency-Key']).toBe('name:example-user')
  })

  it('an explicit idempotencyKey wins over the name, and a blank name derives nothing', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', status: 'running', wait_cap_seconds: 3 } })

    await client.create('py312', { name: 'example-user', idempotencyKey: 'my-key' })
    let [, body, config] = post.mock.calls[0] as [string, Record<string, unknown>, { headers: Record<string, string> }]
    expect(config.headers['Idempotency-Key']).toBe('my-key')
    expect(body.labels).toEqual({ name: 'example-user' }) // the label is stamped regardless

    // A blank name is no name (the API generates one): no label, random key.
    await client.create('py312', { name: '   ' })
    ;[, body, config] = post.mock.calls[1] as [string, Record<string, unknown>, { headers: Record<string, string> }]
    expect(body.labels).toBeUndefined()
    // A random per-call key (its exact shape depends on the runtime's Web
    // Crypto — Node 18 under jest has no randomUUID), never a derived one.
    const blankKey = config.headers['Idempotency-Key']
    expect(blankKey).toBeTruthy()
    expect(blankKey.startsWith('name:')).toBe(false)

    await client.create('py312')
    const [, , third] = post.mock.calls[2] as [string, Record<string, unknown>, { headers: Record<string, string> }]
    expect(third.headers['Idempotency-Key'].startsWith('name:')).toBe(false)
    expect(third.headers['Idempotency-Key']).not.toBe(blankKey)
  })

  it('a named create refuses a contradicting name label', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', status: 'running', wait_cap_seconds: 3 } })

    await expect(client.create('py312', { name: 'example-user', labels: { name: 'other' } })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(post).not.toHaveBeenCalled()

    // The same spelling in both places is not a contradiction.
    await client.create('py312', { name: 'example-user', labels: { name: 'example-user' } })
    expect((post.mock.calls[0]?.[1] as Record<string, unknown>).labels).toEqual({ name: 'example-user' })
  })

  it('findByName uses exact-name search and returns one live sandbox', async () => {
    const client = newClient()
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { sandboxes: [sandboxDetail({ id: 'sbx-new', name: 'example-user' })], total: 1, total_relation: 'eq' } })

    const found = await client.findByName('example-user')

    expect(found.map((sandbox) => sandbox.id)).toEqual(['sbx-new'])
    expect(found[0]).toBeInstanceOf(Sandbox)
    // Each result carries the events hub; a hub-less handle would long-poll
    // every later wait, silently.
    for (const sandbox of found) expect((sandbox as unknown as { _hub?: unknown })._hub).toBeDefined()
    expect(get.mock.calls[0]).toEqual(['/sandboxes/search', { params: { labels: JSON.stringify({ name: 'example-user' }), limit: 100 } }])
  })

  it('findByName uses the same region selector as named creates', async () => {
    // The lookup sends the same `x-daytona-region` selector as create*().
    const client = withApiUrl('http://api.invalid', () => new Daytona({ apiKey: 'key' }))
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx', status: 'running', wait_cap_seconds: 3 } })
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { sandboxes: [sandboxDetail({ id: 'sbx', name: 'example-user' })], total: 1, total_relation: 'eq' } })

    await client.create('py312', { name: 'example-user', region: 'eu' })
    await client.findByName('example-user', { region: 'eu' })
    await client.findByName('example-user', { region: 'us' })
    await client.findByName('example-user', { region: 'local' })

    expect((post.mock.calls[0]?.[2] as { headers: Record<string, string> }).headers['x-daytona-region']).toBe('eu')
    expect(get.mock.calls[0]?.[1]).toMatchObject({ headers: { 'x-daytona-region': 'eu' } })
    expect(get.mock.calls[1]?.[1]).toMatchObject({ headers: { 'x-daytona-region': 'us' } })
    expect(get.mock.calls[2]?.[1]).not.toHaveProperty('headers') // local uses the default region
  })

  it('findByName surfaces a deployment without search as SearchNotConfiguredError, not not-found', async () => {
    const client = newClient()
    jest.spyOn(clientInstance(client), 'get')
      .mockRejectedValueOnce(createDaytonaError('sandbox search is not configured', 501, 'SEARCH_NOT_CONFIGURED', 'DAYTONA_API'))

    await expect(client.findByName('example-user')).rejects.toBeInstanceOf(SearchNotConfiguredError)
  })

  it('findByName returns every live match across pages, and [] when none', async () => {
    const client = newClient()
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { sandboxes: [], total: 0, total_relation: 'eq' } })
      .mockResolvedValueOnce({ data: { sandboxes: [sandboxDetail({ id: 'a' }), sandboxDetail({ id: 'gone', status: 'deleted' })], next_cursor: 'c1', total: 3, total_relation: 'eq' } })
      .mockResolvedValueOnce({ data: { sandboxes: [sandboxDetail({ id: 'b' })], total: 3, total_relation: 'eq' } })

    expect(await client.findByName('nobody')).toEqual([])
    expect((await client.findByName('duplicate')).map((sandbox) => sandbox.id)).toEqual(['a', 'b'])
    await expect(client.findByName('  ')).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(get.mock.calls[2]?.[1]).toMatchObject({ params: { cursor: 'c1' } })
    expect(get).toHaveBeenCalledTimes(3)
  })

  it('createFromBuild applies region to both the image build and the sandbox, and rejects target', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockImplementation(async (path: string) => {
      if (path === '/images') return { data: { id: 'img_eu', name: null, region: 'region-a', status: 'pending' } }
      if (path === '/sandboxes') return { data: { id: 'sbx-eu', status: 'running', wait_cap_seconds: 3 } }
      throw new Error(`unexpected post ${path}`)
    })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: imageResponse({ id: 'img_eu', status: 'ready', anonymous: true, name: null, region: 'region-a' }) })

    await client.createFromBuild(ImageBuilder.fromDockerfile('FROM python:3.12'), { region: 'region-a', idleExpirySeconds: 3600, cpuArch: 'arm64' })
    // Build the image in the sandbox's requested region; build-only options
    // must not reach the sandbox create body.
    const buildBody = post.mock.calls.find(([path]) => path === '/images')?.[1] as Record<string, unknown>
    const createBody = post.mock.calls.find(([path]) => path === '/sandboxes')?.[1] as Record<string, unknown>
    expect(buildBody.region_id).toBe('region-a')
    expect(buildBody.idle_expiry_seconds).toBe(3600)
    expect(buildBody.cpu_arch).toBe('arm64')
    expect(buildBody).not.toHaveProperty('name')
    expect(createBody.region_id).toBe('region-a')
    expect(createBody).not.toHaveProperty('idle_expiry_seconds')
    await expect(client.createFromBuild(ImageBuilder.fromDockerfile('FROM x'), { target: 'region-a' } as never)).rejects.toThrow(/region/)
  })

  it('maps image methods to the images surface', async () => {
    const client = newClient()
    const post = jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValueOnce({ data: { id: 'img_123', status: 'pending' } })
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: imageResponse({ anonymous: true, name: null, status: 'pending' }) })
      .mockResolvedValueOnce({ data: { images: [imageResponse()], next_cursor: 'next' } })
      .mockResolvedValueOnce({ data: imageResponse({ id: 'img_999', name: 'keep-this' }) })
    const patch = jest.spyOn(clientInstance(client), 'patch').mockResolvedValue({ data: imageResponse({ name: 'keep-this' }) })
    const del = jest.spyOn(clientInstance(client), 'delete').mockResolvedValue({ data: undefined })

    const built = await client.images.build(ImageBuilder.fromRegistry('python:3.12').pipInstall('numpy'))
    const listed = await client.images.list({ anonymous: true, sandboxId: 'sbx' })
    const got = await client.images.get('img_999', { wait: 'ready', timeout: 15 })
    const promoted = await client.images.promote('img_123', 'keep-this')
    await client.images.delete('img_123')

    expect(built).toBeInstanceOf(ImageHandle)
    expect(post.mock.calls[0]?.[0]).toBe('/images')
    expect(post.mock.calls[0]?.[1]).toMatchObject({ dockerfile: expect.stringContaining('FROM python:3.12') })
    expect(get.mock.calls[1]).toEqual(['/images', { params: { cursor: undefined, limit: undefined, name: undefined, anonymous: true, sandbox_id: 'sbx' } }])
    expect(get.mock.calls[2]).toEqual(['/images/img_999', { params: { wait: 'ready', timeout_ms: 15000 }, timeout: 20000 }])
    expect(patch).toHaveBeenCalledWith('/images/img_123', { name: 'keep-this' })
    expect(del).toHaveBeenCalledWith('/images/img_123')
    expect(listed.items[0]).toBeInstanceOf(ImageHandle)
    expect(got).toBeInstanceOf(ImageHandle)
    expect(promoted.name).toBe('keep-this')
  })

  it('keeps direct ImagesService(api) construction working for followBuildLogs', async () => {
    const originalFetch = globalThis.fetch
    const client = newClient()
    globalThis.fetch = jest.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url === 'http://api.invalid/images/img_123/build-logs-url') {
        return new Response(JSON.stringify({ url: 'https://logs.example/build-logs/images/img_123' }), {
          headers: { 'content-type': 'application/json' },
        })
      }
      if (url === 'https://logs.example/build-logs/images/img_123?follow=true&format=sse') {
        return new Response('event: done\ndata: {"status":"ready","reason":"terminal","output_complete":true}\n\n', {
          headers: { 'content-type': 'text/event-stream' },
        })
      }
      throw new Error(`unexpected fetch ${url}`)
    }) as never

    try {
      const service = new ImagesService(clientInstance(client))
      await expect(service.followBuildLogs('img_123', { onLogs: () => {} })).resolves.toMatchObject({
        status: 'ready',
      })
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('maps snapshot methods to the snapshots surface', async () => {
    const client = newClient()
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { snapshots: [snapshotResponse()], next_cursor: 'next' } })
      .mockResolvedValueOnce({ data: snapshotResponse({ id: 'snap_999' }) })
    const del = jest.spyOn(clientInstance(client), 'delete').mockResolvedValue({ data: undefined })

    const page = await client.snapshots.list({ name: 'after-setup', sandboxId: 'sbx' })
    const detail = await client.snapshots.get('snap_999', { wait: 'ready', timeout: 15 })
    await client.snapshots.delete('snap_999')

    expect(page.items[0]).toBeInstanceOf(Snapshot)
    expect(get.mock.calls[0]).toEqual(['/snapshots', { params: { cursor: undefined, limit: undefined, name: 'after-setup', sandbox_id: 'sbx' } }])
    expect(get.mock.calls[1]).toEqual(['/snapshots/snap_999', { params: { wait: 'ready', timeout_ms: 15000 }, timeout: 20000 }])
    expect(del).toHaveBeenCalledWith('/snapshots/snap_999')
    expect(detail).toBeInstanceOf(Snapshot)
  })

  it('creates image and snapshot artifacts from a sandbox', async () => {
    const api = clientInstance(newClient())
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { id: 'img_456', status: 'pending' } })
      .mockResolvedValueOnce({ data: { id: 'snap_456', status: 'pending' } })
    jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: imageResponse({ id: 'img_456', anonymous: true, name: null, retention: 'idle' }) })
      .mockResolvedValueOnce({ data: snapshotResponse({ id: 'snap_456', name: 'after-setup' }) })
    const sandbox = new Sandbox(sandboxDetail() as never, api, 'http://toolbox/{sandboxId}', 'key')

    const image = await sandbox.snapshotFilesystem({ idleExpirySeconds: 86400 })
    const snapshot = await sandbox.createSnapshot('after-setup')

    expect(post.mock.calls[0]).toEqual(['/sandboxes/sbx/images', { name: undefined, idle_expiry_seconds: 86400 }])
    expect(post.mock.calls[1]).toEqual(['/sandboxes/sbx/snapshots', { name: 'after-setup' }])
    expect(image).toBeInstanceOf(ImageHandle)
    expect(snapshot).toBeInstanceOf(Snapshot)
  })

  it('parses image and snapshot refs from sandbox dto payloads', () => {
    const sandbox = new Sandbox(sandboxDetail({ image: { id: 'img_999', name: 'kept' }, snapshot: { id: 'snap_999', name: 'saved' } }) as never, undefined, undefined, undefined)

    expect(sandbox.image).toEqual({ id: 'img_999', name: 'kept' })
    expect(sandbox.snapshot).toEqual({ id: 'snap_999', name: 'saved' })
  })

  it('ignores bare image and snapshot strings in SandboxInfo', () => {
    const client = newClient()
    const sandbox = lazySandbox(client, { sandboxId: 'sbx', image: 'python:3.12', snapshot: 'after-setup' } as unknown as SandboxInfo)

    expect(sandbox.image).toBeNull()
    expect(sandbox.snapshot).toBeNull()
  })

  it('maps creation-shape error codes to typed errors', () => {
    expect(createDaytonaError('missing', 404, 'IMAGE_NOT_FOUND', 'DAYTONA_API')).toBeInstanceOf(ImageNotFoundError)
    expect(createDaytonaError('not ready', 409, 'IMAGE_NOT_READY', 'DAYTONA_API')).toBeInstanceOf(ImageNotReadyError)
    expect(createDaytonaError('outdated', 409, 'IMAGE_FORMAT_OUTDATED', 'DAYTONA_API')).toBeInstanceOf(ImageFormatOutdatedError)
    expect(createDaytonaError('failed', 409, 'IMAGE_BUILD_FAILED', 'DAYTONA_API')).toBeInstanceOf(ImageBuildFailedError)
    expect(createDaytonaError('mismatch', 409, 'IMAGE_CLASS_MISMATCH', 'DAYTONA_API')).toBeInstanceOf(ImageClassMismatchError)
    expect(createDaytonaError('taken', 409, 'IMAGE_NAME_TAKEN', 'DAYTONA_API')).toBeInstanceOf(ImageNameTakenError)
    expect(createDaytonaError('reserved', 400, 'IMAGE_NAME_RESERVED', 'DAYTONA_API')).toBeInstanceOf(ImageNameReservedError)
    expect(createDaytonaError('idle', 400, 'IDLE_OUT_OF_RANGE', 'DAYTONA_API', { max: 604800 })).toBeInstanceOf(IdleOutOfRangeError)
    expect(createDaytonaError('ttl', 400, 'CREATE_TTL_OUT_OF_RANGE', 'DAYTONA_API', { max: 86400 })).toMatchObject({ max: 86400 })
    expect(createDaytonaError('snap missing', 404, 'SNAPSHOT_NOT_FOUND', 'DAYTONA_API')).toBeInstanceOf(SnapshotNotFoundError)
    expect(createDaytonaError('snap not ready', 409, 'SNAPSHOT_NOT_READY', 'DAYTONA_API')).toBeInstanceOf(SnapshotNotReadyError)
    expect(createDaytonaError('snap mismatch', 409, 'SNAPSHOT_CLASS_MISMATCH', 'DAYTONA_API')).toBeInstanceOf(SnapshotClassMismatchError)
    expect(createDaytonaError('unsupported', 409, 'NOT_SUPPORTED_FOR_CLASS', 'DAYTONA_API')).toBeInstanceOf(NotSupportedForClassError)
  })

  it('maps every public SandboxState from SandboxInfo and back to toJSON()', () => {
    const client = newClient()
    const states = [
      'queued',
      'creating',
      'starting',
      'started',
      'pausing',
      'stopping',
      'stopped',
      'paused',
      'destroying',
      'destroyed',
      'error',
      'unknown',
    ] as const satisfies readonly SandboxState[]

    for (const state of states) {
      const sandbox = lazySandbox(client, { sandboxId: `sbx-${state}`, state })
      expect(sandbox.state).toBe(state)
      expect(sandbox.toJSON().state).toBe(state)
    }
  })
})

describe('toolbox service request shaping', () => {
  it('waits for fs watch start before resolving and delivers CRLF-framed events sequentially', async () => {
    const transport = transportStub()
    const stream = controlledSseResponse()
    transport.fetch.mockImplementation(async (_path, init) => {
      const signal = init?.signal
      if (!(signal instanceof AbortSignal)) throw new Error('expected AbortSignal')
      signal.addEventListener('abort', () => {
        stream.close()
      }, { once: true })
      return stream.response
    })
    const fs = new FileSystemService(transport.object)
    const seen: string[] = []
    let releaseFirstEvent: (() => void) | undefined
    const firstEventDone = new Promise<void>((resolve) => {
      releaseFirstEvent = resolve
    })

    const pendingHandle = fs.watchDir('/workspace', async (event) => {
      seen.push(`start:${event.name}`)
      if (event.name === 'a.txt') {
        await firstEventDone
      }
      seen.push(`end:${event.name}`)
    }, {
      recursive: true,
    })

    let resolved = false
    void pendingHandle.then(() => {
      resolved = true
    })
    await Promise.resolve()
    expect(resolved).toBe(false)

    stream.push('event: start\r\ndata: {}\r\n\r\n')
    const handle = await pendingHandle
    expect(transport.fetch.mock.calls[0]).toEqual([
      '/files/watch?path=%2Fworkspace&recursive=true',
      { headers: { Accept: 'text/event-stream' }, signal: expect.any(AbortSignal) },
    ])

    stream.push('event: fs\r\ndata: {"type":"create","name":"a.txt","isDir":false,"timestamp":1}\r\n\r\n')
    stream.push('event: fs\r\ndata: {"type":"write","name":"b.txt","isDir":false,"timestamp":2}\r\n\r\n')
    await flushAsync()
    expect(seen).toEqual(['start:a.txt'])

    releaseFirstEvent?.()
    await flushAsync()
    expect(seen).toEqual(['start:a.txt', 'end:a.txt', 'start:b.txt', 'end:b.txt'])

    await handle.stop()
  })

  it('routes fs watch overflow and stop through their dedicated callbacks', async () => {
    const transport = transportStub()
    const stream = controlledSseResponse()
    transport.fetch.mockImplementation(async (_path, init) => {
      const signal = init?.signal
      if (!(signal instanceof AbortSignal)) throw new Error('expected AbortSignal')
      signal.addEventListener('abort', () => {
        stream.close()
      }, { once: true })
      return stream.response
    })
    const fs = new FileSystemService(transport.object)
    const onOverflow = jest.fn()
    const onExit = jest.fn()
    const events: Array<{ readonly type: FilesystemEventType; readonly name: string }> = []

    const pendingHandle = fs.watchDir('/workspace', (event) => {
      events.push({ type: event.type, name: event.name })
    }, {
      onOverflow,
      onExit,
    })

    stream.push('event: start\ndata: {}\n\n')
    const handle = await pendingHandle
    stream.push('event: fs\ndata: {"type":"rename","name":"old.txt","isDir":false,"timestamp":3}\n\n')
    stream.push('event: overflow\ndata: {"dropped":true}\n\n')
    await flushAsync()

    expect(events).toEqual([{ type: FilesystemEventType.RENAME, name: 'old.txt' }])
    expect(onOverflow).toHaveBeenCalledTimes(1)

    await handle.stop()
    const signal = transport.fetch.mock.calls[0]?.[1]?.signal
    expect(signal).toBeInstanceOf(AbortSignal)
    expect((signal as AbortSignal).aborted).toBe(true)
    await expect(handle.waitForExit()).resolves.toBeUndefined()
    expect(handle.exitError).toBeUndefined()
    expect(onExit).toHaveBeenCalledTimes(1)
    expect(onExit).toHaveBeenCalledWith(undefined)
  })

  it('surfaces unexpected fs watch termination through onExit, waitForExit, and exitError', async () => {
    const transport = transportStub()
    const stream = controlledSseResponse()
    transport.fetch.mockResolvedValue(stream.response)
    const fs = new FileSystemService(transport.object)
    const onExit = jest.fn()

    const pendingHandle = fs.watchDir('/workspace', () => undefined, { onExit })

    stream.push('event: start\ndata: {}\n\n')
    const handle = await pendingHandle
    const exitPromise = handle.waitForExit()

    stream.close()

    await expect(exitPromise).resolves.toMatchObject({
      message: 'filesystem watch stream ended unexpectedly',
    })
    expect(handle.exitError).toMatchObject({
      message: 'filesystem watch stream ended unexpectedly',
    })
    expect(onExit).toHaveBeenCalledTimes(1)
    expect(onExit).toHaveBeenLastCalledWith(expect.objectContaining({
      message: 'filesystem watch stream ended unexpectedly',
    }))
  })

  it('lets stop() resolve when called from inside onEvent and notifies exit once', async () => {
    const transport = transportStub()
    const stream = controlledSseResponse()
    transport.fetch.mockImplementation(async (_path, init) => {
      const signal = init?.signal
      if (!(signal instanceof AbortSignal)) throw new Error('expected AbortSignal')
      signal.addEventListener('abort', () => {
        stream.close()
      }, { once: true })
      return stream.response
    })
    const fs = new FileSystemService(transport.object)
    const onExit = jest.fn()
    let handle: { stop(): Promise<void> } | undefined
    let callbackResolved = false

    const pendingHandle = fs.watchDir('/workspace', async () => {
      if (!handle) throw new Error('watch handle unavailable inside callback')
      await handle.stop()
      callbackResolved = true
    }, {
      onExit,
    })

    stream.push('event: start\ndata: {}\n\n')
    handle = await pendingHandle
    stream.push('event: fs\ndata: {"type":"create","name":"a.txt","isDir":false,"timestamp":1}\n\n')

    await flushAsync()
    await flushAsync()

    expect(callbackResolved).toBe(true)
    expect(onExit).toHaveBeenCalledTimes(1)
    expect(onExit).toHaveBeenCalledWith(undefined)
  })

  it('rejects fs watch setup errors before returning a handle', async () => {
    const transport = transportStub()
    transport.fetch.mockRejectedValue(new DaytonaError('missing path', { statusCode: 404, code: 'FILE_NOT_FOUND' }))
    const fs = new FileSystemService(transport.object)

    await expect(fs.watchDir('/missing', () => undefined)).rejects.toMatchObject({
      statusCode: 404,
      code: 'FILE_NOT_FOUND',
      message: 'missing path',
    })
  })

  it('shapes bulk filesystem writes as indexed multipart form data', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ files: [{ path: '/tmp/a.txt', bytes: 3 }, { path: '/tmp/b.txt', bytes: 2 }], errors: ['file[2]: missing .path metadata'] })
    const fs = new FileSystemService(transport.object)

    const result = await fs.writeMany({
      '/tmp/a.txt': new Uint8Array([65, 65, 65]),
      '/tmp/b.txt': new Uint8Array([66, 66]),
    })

    expect(result).toEqual({ files: [{ path: '/tmp/a.txt', bytes: 3 }, { path: '/tmp/b.txt', bytes: 2 }], errors: ['file[2]: missing .path metadata'] })
    expect(transport.post.mock.calls[0]?.[0]).toBe('/files/bulk-upload')
    expect(transport.post.mock.calls[0]?.[2]).toEqual({ timeout: 0, daytona: { transfer: true } })

    const form = transport.post.mock.calls[0]?.[1]
    expect(form).toBeInstanceOf(FormData)
    if (!(form instanceof FormData)) throw new Error('expected FormData body')

    const names: string[] = []
    form.forEach((_value, name) => {
      names.push(name)
    })
    expect(names).toEqual([
      'files[0].path',
      'files[0].file',
      'files[1].path',
      'files[1].file',
    ])
    expect(form.get('files[0].path')).toBe('/tmp/a.txt')
    expect(form.get('files[1].path')).toBe('/tmp/b.txt')

    // Blob, not File: FormData file entries are Blob subclasses carrying a
    // `name` on every runtime.
    const firstFile = form.get('files[0].file')
    const secondFile = form.get('files[1].file')
    expect(firstFile).toBeInstanceOf(Blob)
    expect(secondFile).toBeInstanceOf(Blob)
    if (!(firstFile instanceof Blob) || !(secondFile instanceof Blob)) throw new Error('expected blob parts')
    expect((firstFile as Blob & { name?: string }).name).toBe('a.txt')
    expect((secondFile as Blob & { name?: string }).name).toBe('b.txt')
    expect(Array.from(new Uint8Array(await firstFile.arrayBuffer()))).toEqual([65, 65, 65])
    expect(Array.from(new Uint8Array(await secondFile.arrayBuffer()))).toEqual([66, 66])
  })

  it('forwards atomic query parameters on write and writeMany', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)

    await expect(fs.writeBytes('/tmp/a.txt', 'hello' as unknown as Uint8Array)).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(transport.post).not.toHaveBeenCalled()
    await fs.writeText('/tmp/a.txt', 'hello', { atomic: true })
    await fs.writeMany({ '/tmp/a.txt': new Uint8Array([1, 2, 3]) }, { atomic: true })

    expect(transport.post.mock.calls[0]?.[2]).toEqual({ params: { path: '/tmp/a.txt', atomic: true }, timeout: 0, daytona: { transfer: true } })
    expect(transport.post.mock.calls[1]?.[2]).toEqual({ params: { atomic: true }, timeout: 0, daytona: { transfer: true } })
  })

  it('streams uploadFile with progress callbacks and atomic query parameters', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-upload-'))
    const localPath = join(tempRoot, 'three-chunks.bin')
    const totalBytes = 160_000
    await writeFile(localPath, Buffer.alloc(totalBytes, 0x61))
    const progress: number[] = []

    transport.fetch.mockImplementation(async (path: string, init?: RequestInit) => {
      expect(path).toBe('/files/upload?path=%2Fremote%2Fthree-chunks.bin&atomic=true')
      expect(init?.method).toBe('POST')
      const uploadInit = init as RequestInitWithBodyFactory | undefined
      const body = uploadInit?.body ?? await uploadInit?.bodyFactory?.()
      expect(body).toBeInstanceOf(ReadableStream)
      const bodyBytes = await readStreamBytes(body as ReadableStream<Uint8Array>)
      const multipartBody = new TextDecoder().decode(bodyBytes)
      expect(bodyBytes.byteLength).toBeGreaterThan(totalBytes)
      expect(multipartBody).toContain('Content-Disposition: form-data; name="file"; filename="three-chunks.bin"')
      expect(multipartBody).not.toContain('Content-Disposition: form-data; name="path"')
      expect(multipartBody).not.toContain('/remote/three-chunks.bin')
      return new Response(null, { status: 204 })
    })

    try {
      await fs.uploadFile(localPath, '/remote/three-chunks.bin', {
        atomic: true,
        onProgress: (bytes, total) => {
          progress.push(bytes)
          expect(total).toBe(totalBytes)
        },
      })
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(progress.length).toBeGreaterThanOrEqual(3)
    expect(progress.at(-1)).toBe(totalBytes)
  })

  it('throws a typed SDK error when the source file shrinks after stat', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-upload-shrunk-'))
    const localPath = join(tempRoot, 'shrunk.bin')
    await writeFile(localPath, Buffer.from('abcdefgh'))
    const progress: Array<readonly [number, number | undefined]> = []

    transport.fetch.mockImplementation(async (_path: string, init?: RequestInit) => {
      await nodeFsPromises.truncate(localPath, 3)
      const uploadInit = init as RequestInitWithBodyFactory | undefined
      const body = (uploadInit?.body ?? await uploadInit?.bodyFactory?.()) as ReadableStream<Uint8Array>
      await readStreamBytes(body)
      return new Response(null, { status: 204 })
    })

    try {
      await expect(fs.uploadFile(localPath, '/remote/shrunk.bin', {
        onProgress: (bytes, total) => {
          progress.push([bytes, total])
        },
      })).rejects.toMatchObject({
        constructor: BadRequestError,
        message: 'source file changed size during upload',
        statusCode: 400,
        code: 'BAD_REQUEST',
        source: 'SDK',
      })
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(progress).toEqual([[3, 8]])
  })

  it('caps streamed uploadFile bytes to the stat snapshot when the source grows mid-upload', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-upload-grown-'))
    const localPath = join(tempRoot, 'grown.bin')
    await writeFile(localPath, Buffer.from('abcdefgh'))
    const progress: Array<readonly [number, number | undefined]> = []
    let uploadedBody = ''

    transport.fetch.mockImplementation(async (_path: string, init?: RequestInit) => {
      await writeFile(localPath, Buffer.from('ijkl'), { flag: 'a' })
      const uploadInit = init as RequestInitWithBodyFactory | undefined
      const body = (uploadInit?.body ?? await uploadInit?.bodyFactory?.()) as ReadableStream<Uint8Array>
      uploadedBody = new TextDecoder().decode(await readStreamBytes(body))
      return new Response(null, { status: 204 })
    })

    try {
      await fs.uploadFile(localPath, '/remote/grown.bin', {
        onProgress: (bytes, total) => {
          progress.push([bytes, total])
        },
      })
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(progress).toEqual([[8, 8]])
    expect(uploadedBody).toContain('abcdefgh')
    expect(uploadedBody).not.toContain('ijkl')
  })

  it('omits atomic=false from plain uploadFile URLs and keeps atomic=true when requested', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-upload-query-'))
    const localPath = join(tempRoot, 'query.bin')
    await writeFile(localPath, Buffer.alloc(8, 0x63))

    transport.fetch
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    try {
      await fs.uploadFile(localPath, '/remote/default.bin')
      await fs.uploadFile(localPath, '/remote/atomic.bin', { atomic: true })
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(transport.fetch.mock.calls[0]?.[0]).toBe('/files/upload?path=%2Fremote%2Fdefault.bin')
    expect(transport.fetch.mock.calls[1]?.[0]).toBe('/files/upload?path=%2Fremote%2Fatomic.bin&atomic=true')
  })

  it('cancels uploadFile mid-stream with a typed cancellation error', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-upload-cancel-'))
    const localPath = join(tempRoot, 'cancel.bin')
    const totalBytes = 160_000
    await writeFile(localPath, Buffer.alloc(totalBytes, 0x62))
    const controller = new AbortController()
    const progress: number[] = []

    transport.fetch.mockImplementation(async (_path: string, init?: RequestInit) => {
      expect(init?.signal).toBe(controller.signal)
      const uploadInit = init as RequestInitWithBodyFactory | undefined
      const body = (uploadInit?.body ?? await uploadInit?.bodyFactory?.()) as ReadableStream<Uint8Array>
      const reader = body.getReader()
      for (;;) {
        const next = await reader.read()
        if (next.done) break
        if (next.value.byteLength > 1024) {
          controller.abort()
        }
      }
      return new Response(null, { status: 204 })
    })

    try {
      await expect(fs.uploadFile(localPath, '/remote/cancel.bin', {
        signal: controller.signal,
        onProgress: (bytes) => {
          progress.push(bytes)
        },
      })).rejects.toBeInstanceOf(TransferCancelledError)
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(controller.signal.aborted).toBe(true)
    expect(progress.length).toBeGreaterThanOrEqual(1)
    expect(progress.at(-1)).toBeLessThan(totalBytes)
  })

  it('rejects uploadFile before opening the stream when the signal aborts before body creation', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-upload-preabort-'))
    const localPath = join(tempRoot, 'preabort.bin')
    await writeFile(localPath, Buffer.from('abcdef'))
    const controller = new AbortController()
    const mutableNodeFs = require('node:fs') as typeof import('node:fs')
    const originalCreateReadStream = mutableNodeFs.createReadStream
    let createReadStreamCalls = 0
    mutableNodeFs.createReadStream = ((...args: Parameters<typeof originalCreateReadStream>) => {
      createReadStreamCalls += 1
      return originalCreateReadStream(...args)
    }) as typeof originalCreateReadStream

    transport.fetch.mockImplementation(async (_path: string, init?: RequestInit) => {
      controller.abort(new DOMException('cancelled', 'AbortError'))
      const uploadInit = init as RequestInitWithBodyFactory | undefined
      await expect(uploadInit?.bodyFactory?.()).rejects.toBeInstanceOf(TransferCancelledError)
      throw new TransferCancelledError('file transfer cancelled')
    })

    try {
      await expect(fs.uploadFile(localPath, '/remote/preabort.bin', { signal: controller.signal })).rejects.toBeInstanceOf(TransferCancelledError)
    } finally {
      mutableNodeFs.createReadStream = originalCreateReadStream
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(createReadStreamCalls).toBe(0)
  })

  it('swallows uploadFile progress callback errors and still completes the transfer', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-upload-progress-'))
    const localPath = join(tempRoot, 'progress.bin')
    await writeFile(localPath, Buffer.from('abcdef'))
    const progress: Array<readonly [number, number | undefined]> = []

    transport.fetch.mockImplementation(async (_path: string, init?: RequestInit) => {
      const uploadInit = init as RequestInitWithBodyFactory | undefined
      const body = (uploadInit?.body ?? await uploadInit?.bodyFactory?.()) as ReadableStream<Uint8Array>
      await readStreamBytes(body)
      return new Response(null, { status: 204 })
    })

    try {
      await expect(fs.uploadFile(localPath, '/remote/progress.bin', {
        onProgress: (bytes, total) => {
          progress.push([bytes, total])
          throw new Error('progress failed')
        },
      })).resolves.toBeUndefined()
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(progress.at(-1)).toEqual([6, 6])
  })

  it('downloads files to disk with progress, renames on success, and removes the .part file', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-'))
    const targetWithLength = join(tempRoot, 'with-length.txt')
    const targetWithoutLength = join(tempRoot, 'without-length.txt')
    const first = new TextEncoder().encode('hello ')
    const second = new TextEncoder().encode('world')
    const totalBytes = first.byteLength + second.byteLength
    const withLengthProgress: Array<readonly [number, number | undefined]> = []
    const withoutLengthProgress: Array<readonly [number, number | undefined]> = []

    transport.fetch
      .mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(first)
          controller.enqueue(second)
          controller.close()
        },
      }), { status: 200, headers: { 'content-length': String(totalBytes) } }))
      .mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(first)
          controller.enqueue(second)
          controller.close()
        },
      }), { status: 200 }))

    try {
      await fs.downloadFile('/remote/with-length.txt', targetWithLength, {
        onProgress: (bytes, total) => {
          withLengthProgress.push([bytes, total])
        },
      })
      await fs.downloadFile('/remote/without-length.txt', targetWithoutLength, {
        onProgress: (bytes, total) => {
          withoutLengthProgress.push([bytes, total])
        },
      })

      expect(await readFile(targetWithLength, 'utf8')).toBe('hello world')
      expect(await readFile(targetWithoutLength, 'utf8')).toBe('hello world')
      expect((await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))).toEqual([])
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(withLengthProgress.at(-1)).toEqual([totalBytes, totalBytes])
    expect(withoutLengthProgress.at(-1)).toEqual([totalBytes, undefined])
  })

  it('preserves an existing destination mode when downloadFile replaces it', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-mode-'))
    const target = join(tempRoot, 'target.txt')
    await writeFile(target, 'keep me')
    await nodeFsPromises.chmod(target, 0o600)
    transport.fetch.mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('next body'))
        controller.close()
      },
    }), { status: 200, headers: { 'content-length': '9' } }))

    try {
      await fs.downloadFile('/remote/target.txt', target)
      expect(await readFile(target, 'utf8')).toBe('next body')
      expect((await nodeFsPromises.stat(target)).mode & 0o777).toBe(0o600)
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('keeps the staging file private while streaming and applies the default creation mode on rename', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-default-mode-'))
    const target = join(tempRoot, 'target.txt')
    const payload = new TextEncoder().encode('next body')
    let firstProgress: (() => void) | undefined
    const progressStarted = new Promise<void>((resolve) => {
      firstProgress = resolve
    })
    let releaseBody: (() => void) | undefined
    transport.fetch.mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(payload)
        await new Promise<void>((resolve) => {
          releaseBody = resolve
        })
        controller.close()
      },
    }), { status: 200, headers: { 'content-length': String(payload.byteLength) } }))

    try {
      const pending = fs.downloadFile('/remote/target.txt', target, {
        onProgress: () => {
          firstProgress?.()
        },
      })
      await progressStarted
      const [stagingEntry] = (await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))
      expect(stagingEntry).toBeDefined()
      if (stagingEntry === undefined) throw new Error('expected staging file')
      const stagingPath = join(tempRoot, stagingEntry)
      expect((await nodeFsPromises.stat(stagingPath)).mode & 0o777).toBe(0o600)

      releaseBody?.()
      await pending

      expect(await readFile(target, 'utf8')).toBe('next body')
      expect((await nodeFsPromises.stat(target)).mode & 0o777).toBe(0o666 & ~process.umask())
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('removes the partial file when downloadFile is cancelled or the stream fails', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-failure-'))
    const cancelTarget = join(tempRoot, 'cancel.txt')
    const errorTarget = join(tempRoot, 'error.txt')
    const chunk = new TextEncoder().encode('chunk')

    transport.fetch
      .mockImplementationOnce(async (_path: string, init?: RequestInit) => {
        const signal = init?.signal
        if (!(signal instanceof AbortSignal)) throw new Error('expected AbortSignal')
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(chunk)
            signal.addEventListener('abort', () => {
              controller.error(new DOMException('aborted', 'AbortError'))
            }, { once: true })
          },
        }), { status: 200, headers: { 'content-length': '10' } })
      })
      .mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(chunk)
          controller.error(new Error('stream failed'))
        },
      }), { status: 200, headers: { 'content-length': '10' } }))

    const controller = new AbortController()

    try {
      const pendingCancel = fs.downloadFile('/remote/cancel.txt', cancelTarget, { signal: controller.signal })
      await flushMicrotasks()
      controller.abort()
      await expect(pendingCancel).rejects.toBeInstanceOf(TransferCancelledError)
      await expect(access(cancelTarget)).rejects.toThrow()
      expect((await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))).toEqual([])

      await expect(fs.downloadFile('/remote/error.txt', errorTarget)).rejects.toThrow('stream failed')
      await expect(access(errorTarget)).rejects.toThrow()
      expect((await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))).toEqual([])
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('rejects a bodyless download before opening a staging file', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-empty-body-'))
    const target = join(tempRoot, 'target.txt')
    await writeFile(target, 'keep me')
    transport.fetch.mockResolvedValue(new Response(null, { status: 200, headers: { 'content-length': '0' } }))

    try {
      await expect(fs.downloadFile('/remote/target.txt', target)).rejects.toMatchObject({ constructor: BadRequestError, message: 'downloadFile() expected a response body', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
      expect(await readFile(target, 'utf8')).toBe('keep me')
      expect((await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))).toEqual([])
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('rejects a truncated download before renaming the staged file', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-truncated-'))
    const target = join(tempRoot, 'target.txt')
    await writeFile(target, 'keep me')
    transport.fetch.mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('short'))
        controller.close()
      },
    }), { status: 200, headers: { 'content-length': '10' } }))

    try {
      // A truncated transfer, as when the socket drops mid-body (Python raises the same).
      await expect(fs.downloadFile('/remote/target.txt', target)).rejects.toMatchObject({ constructor: ConnectionError, message: 'downloadFile() ended before the declared Content-Length was received', source: 'SDK' })
      expect(await readFile(target, 'utf8')).toBe('keep me')
      expect((await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))).toEqual([])
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('does not rename the staged download after a final progress callback aborts', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-final-abort-'))
    const target = join(tempRoot, 'cancel-at-end.txt')
    const chunk = new TextEncoder().encode('chunk')
    const controller = new AbortController()
    transport.fetch.mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
      start(streamController) {
        streamController.enqueue(chunk)
        streamController.close()
      },
    }), { status: 200, headers: { 'content-length': String(chunk.byteLength) } }))

    try {
      await expect(fs.downloadFile('/remote/cancel-at-end.txt', target, {
        signal: controller.signal,
        onProgress: (bytes, total) => {
          if (bytes === total) controller.abort()
        },
      })).rejects.toBeInstanceOf(TransferCancelledError)
      await expect(access(target)).rejects.toThrow()
      expect((await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))).toEqual([])
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('retries partial local writes until the full chunk is staged', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-short-write-'))
    const target = join(tempRoot, 'short-write.txt')
    const bytes = new TextEncoder().encode('hello world')
    const progress: Array<readonly [number, number | undefined]> = []
    const sampleHandle = await nodeFsPromises.open(join(tempRoot, 'sample.txt'), 'w')
    const fileHandlePrototype = Object.getPrototypeOf(sampleHandle) as {
      write: (buffer: Uint8Array) => Promise<{ readonly bytesWritten: number }>
    }
    const originalWrite = fileHandlePrototype.write
    await sampleHandle.close()
    await rm(join(tempRoot, 'sample.txt'), { force: true })
    const writeSpy = jest.spyOn(fileHandlePrototype, 'write')
      .mockImplementationOnce(async function (this: LocalFileWriteContext, buffer: Uint8Array) {
        const bytesWritten = Math.floor(buffer.byteLength / 2)
        await originalWrite.call(this, buffer.subarray(0, bytesWritten))
        return { bytesWritten }
      })
    transport.fetch.mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
      start(streamController) {
        streamController.enqueue(bytes)
        streamController.close()
      },
    }), { status: 200, headers: { 'content-length': String(bytes.byteLength) } }))

    try {
      await fs.downloadFile('/remote/short-write.txt', target, {
        onProgress: (written, total) => {
          progress.push([written, total])
        },
      })
      expect(writeSpy).toHaveBeenCalledTimes(2)
      expect(await readFile(target, 'utf8')).toBe('hello world')
      expect(progress).toEqual([
        [Math.floor(bytes.byteLength / 2), bytes.byteLength],
        [bytes.byteLength, bytes.byteLength],
      ])
    } finally {
      writeSpy.mockRestore()
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('leaves a pre-existing .part symlink untouched and uses per-download temp files', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-temp-'))
    const target = join(tempRoot, 'file.txt')
    const existingPartPath = `${target}.part`
    const sentinel = join(tempRoot, 'sentinel.txt')
    const first = new TextEncoder().encode('first')
    const second = new TextEncoder().encode('second')
    let releaseFirst: (() => void) | undefined
    let releaseSecond: (() => void) | undefined

    await writeFile(sentinel, 'sentinel')
    await symlink(sentinel, existingPartPath)

    transport.fetch
      .mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
        async start(controller) {
          controller.enqueue(first)
          await new Promise<void>((resolve) => {
            releaseFirst = resolve
          })
          controller.close()
        },
      }), { status: 200, headers: { 'content-length': String(first.byteLength) } }))
      .mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
        async start(controller) {
          controller.enqueue(second)
          await new Promise<void>((resolve) => {
            releaseSecond = resolve
          })
          controller.close()
        },
      }), { status: 200, headers: { 'content-length': String(second.byteLength) } }))

    try {
      const pendingA = fs.downloadFile('/remote/a.txt', target)
      const pendingB = fs.downloadFile('/remote/b.txt', target)
      await flushMicrotasks()
      releaseFirst?.()
      releaseSecond?.()

      await expect(Promise.all([pendingA, pendingB])).resolves.toEqual([undefined, undefined])
      expect(await readFile(sentinel, 'utf8')).toBe('sentinel')
      expect(await readlink(existingPartPath)).toBe(sentinel)
      expect(['first', 'second']).toContain(await readFile(target, 'utf8'))
      expect((await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))).toEqual([])
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('swallows downloadFile progress callback errors and still commits the file', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-progress-'))
    const target = join(tempRoot, 'target.txt')
    const cancel = jest.fn().mockResolvedValue(undefined)
    const releaseLock = jest.fn()
    const read = jest.fn()
      .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode('chunk') })
      .mockResolvedValueOnce({ done: true, value: undefined })
    transport.fetch.mockResolvedValue({
      headers: new Headers({ 'content-length': '5' }),
      body: { getReader: () => ({ read, cancel, releaseLock }) },
    } as unknown as Response)

    try {
      await expect(fs.downloadFile('/remote/file.txt', target, {
        onProgress: () => {
          throw new Error('progress failed')
        },
      })).resolves.toBeUndefined()
      expect(await readFile(target, 'utf8')).toBe('chunk')
      expect(cancel).not.toHaveBeenCalled()
      expect(releaseLock).toHaveBeenCalledTimes(1)
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('cancels the download body when reader setup fails after the staging file opens', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-reader-setup-'))
    const target = join(tempRoot, 'target.txt')
    const cancel = jest.fn().mockResolvedValue(undefined)
    transport.fetch.mockResolvedValue({
      headers: new Headers({ 'content-length': '5' }),
      body: {
        cancel,
        getReader: () => {
          throw new Error('reader setup failed')
        },
      },
    } as unknown as Response)

    try {
      await expect(fs.downloadFile('/remote/target.txt', target)).rejects.toThrow('reader setup failed')
      expect(cancel).toHaveBeenCalledTimes(1)
      expect((await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))).toEqual([])
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('removes the staged file when applying the final download mode fails', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-chmod-failure-'))
    const target = join(tempRoot, 'target.txt')
    const payload = new TextEncoder().encode('next body')
    const sampleHandle = await nodeFsPromises.open(join(tempRoot, 'sample.txt'), 'w')
    const fileHandlePrototype = Object.getPrototypeOf(sampleHandle) as {
      chmod: (mode: number) => Promise<void>
    }
    await sampleHandle.close()
    await rm(join(tempRoot, 'sample.txt'), { force: true })
    const chmodSpy = jest.spyOn(fileHandlePrototype, 'chmod').mockRejectedValueOnce(new Error('chmod failed'))
    transport.fetch.mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(payload)
        controller.close()
      },
    }), { status: 200, headers: { 'content-length': String(payload.byteLength) } }))

    try {
      await expect(fs.downloadFile('/remote/target.txt', target)).rejects.toThrow('chmod failed')
      await expect(access(target)).rejects.toThrow()
      expect((await readdir(tempRoot)).filter((entry) => entry.startsWith('.daytona-dl-'))).toEqual([])
    } finally {
      chmodSpy.mockRestore()
      await rm(tempRoot, { recursive: true, force: true })
    }
  })

  it('keeps bulk read payload bytes that look like the multipart boundary', async () => {
    const transport = transportStub()
    const boundary = 'sdk-boundary-123'
    const payload = Buffer.concat([
      Buffer.from(`before\r\n--${boundary}X\r\nafter\r\n--${boundary}`, 'utf8'),
      Buffer.from([0x00]),
      Buffer.from('tail', 'utf8'),
    ])
    transport.postRaw.mockResolvedValue({
      data: multipartFixture([
        {
          headers: ['Content-Disposition: form-data; name="file"; filename="bin.dat"', 'Content-Type: application/octet-stream'],
          body: new Uint8Array(payload),
        },
      ], boundary),
      status: 200,
      headers: { 'content-type': `multipart/form-data; boundary=${boundary}` },
    })
    const fs = new FileSystemService(transport.object)

    const result = await fs.readMany(['bin.dat'])

    expect(result.errors).toEqual([])
    expect(Buffer.from(result.files['bin.dat'] ?? new Uint8Array()).equals(payload)).toBe(true)
  })

  it('parses bulk filesystem reads using the response boundary header', async () => {
    const transport = transportStub()
    const boundary = 'sdk-boundary-123'
    transport.postRaw.mockResolvedValue({
      data: multipartFixture([
        {
          headers: [
            'Content-Disposition: form-data; name="file"; filename="dir/?.txt"; filename*=utf-8\'\'dir%2F%C3%BC.txt',
            'Content-Type: application/octet-stream',
          ],
          body: new TextEncoder().encode('hello\r\n--DAYTONA-FILE-BOUNDARY\r\nworld'),
        },
        {
          headers: [
            'Content-Disposition: form-data; name="error"',
            'Content-Type: application/json',
          ],
          body: new TextEncoder().encode('{"path":"/nope.txt","code":"FILE_NOT_FOUND","message":"file not found: /nope.txt","source":"DAYTONA_DAEMON"}'),
        },
      ], boundary),
      status: 200,
      headers: { 'content-type': `multipart/form-data; boundary=${boundary}` },
    })
    const fs = new FileSystemService(transport.object)

    const result = await fs.readMany(['dir/ü.txt', '/nope.txt'])

    expect(transport.postRaw.mock.calls[0]).toEqual([
      '/files/bulk-download',
      { paths: ['dir/ü.txt', '/nope.txt'] },
      { responseType: 'arraybuffer', timeout: 0, daytona: { transfer: true } },
    ])
    const downloaded = result.files['dir/ü.txt']
    expect(downloaded).toBeDefined()
    if (!downloaded) throw new Error('expected bulk read file payload')
    expect(new TextDecoder().decode(downloaded)).toBe('hello\r\n--DAYTONA-FILE-BOUNDARY\r\nworld')
    expect(result.errors).toEqual([
      {
        path: '/nope.txt',
        code: 'FILE_NOT_FOUND',
        message: 'file not found: /nope.txt',
        source: 'DAYTONA_DAEMON',
      },
    ])
  })

  it('raises the typed envelope error when a bulk read answers non-200', async () => {
    const transport = transportStub()
    transport.postRaw.mockResolvedValue({
      status: 400,
      data: new TextEncoder().encode(
        '{"message":"paths are required","source":"DAYTONA_DAEMON","code":"BAD_REQUEST"}',
      ).buffer,
      headers: { 'content-type': 'application/json' },
    })
    const fs = new FileSystemService(transport.object)

    await expect(fs.readMany([])).rejects.toMatchObject({
      statusCode: 400,
      message: 'paths are required',
      code: 'BAD_REQUEST',
    })
  })

  it('shapes process start, list, handle control, and wait results', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'proc' })
    transport.get
      .mockResolvedValueOnce({ reason: 'signaled', signal: 'SIGTERM' })
      .mockResolvedValueOnce([{ id: 'proc', kind: 'exec', state: 'running' }])
    const process = new ProcessService(transport.object, 'sbx')

    const handle = await process.start('echo hi', { name: 'job', env: { A: 'B' }, executionTimeout: 5 })
    expect(transport.post.mock.calls[0]).toEqual(['/processes', { shellCommand: 'echo hi', name: 'job', env: { A: 'B' }, timeoutMs: 5 }])

    await handle.kill({ signal: 'TERM', escalateAfter: 100, escalateTo: 'KILL' })
    expect(transport.post.mock.calls[1]).toEqual(['/processes/proc/signals', { signal: 'SIGTERM', escalateAfterMs: 100, escalateTo: 'SIGKILL' }])
    await handle.writeStdin(new Uint8Array([0, 255]))
    expect(transport.post.mock.calls[2]).toEqual(['/processes/proc/stdin', { data: 'AP8=', encoding: 'base64' }])
    await handle.closeStdin()
    expect(transport.post.mock.calls[3]).toEqual(['/processes/proc/stdin', { data: '', eof: true }])
    await expect(handle.resize(120, 40)).rejects.toBeInstanceOf(UnsupportedOperationError)
    await handle.delete()
    expect(transport.delete.mock.calls[0]?.[0]).toBe('/processes/proc')

    const now = jest.spyOn(Date, 'now').mockReturnValue(1_000)
    try {
      await expect(handle.wait({ timeout: 25 })).resolves.toEqual({ reason: 'signaled', rawReason: 'signaled', signal: 'TERM' })
      await expect(process.list({ state: 'running', kind: 'exec', sessionId: 's', name: 'n', pid: 42 })).resolves.toEqual({
        items: [{ id: 'proc', kind: 'exec', rawKind: 'exec', state: 'running', rawState: 'running' }],
      })
      expect(transport.get.mock.calls[0]?.[0]).toBe('/processes/proc/wait?timeoutMs=25')
      expect(transport.get.mock.calls[0]?.[1]).toEqual({ timeout: 25 + WAIT_READ_SLACK_MS })
      expect(transport.get.mock.calls[1]?.[0]).toBe('/processes?state=running&kind=exec&sessionId=s&name=n&pid=42')
    } finally {
      now.mockRestore()
    }
  })

  it('lists and deletes PTY processes through PtyService helpers', async () => {
    const transport = transportStub()
    transport.get.mockResolvedValueOnce({ items: [{ id: 'pty-1', kind: 'pty', state: 'running' }], nextCursor: 'p' })
    transport.post.mockResolvedValueOnce({ signal: 'ok' })
    transport.delete.mockResolvedValueOnce(undefined)
    const process = new ProcessService(transport.object, 'sbx')
    const pty = new PtyService(process)
    const handle = pty.connect('pty-1')

    await expect(process.list({ kind: 'pty' })).resolves.toEqual({
      items: [{ id: 'pty-1', kind: 'pty', rawKind: 'pty', state: 'running', rawState: 'running', executionTimeout: undefined }],
      nextCursor: 'p',
    })
    await expect(handle.kill()).resolves.toBeUndefined()
    await expect(handle.delete()).resolves.toBeUndefined()

    expect(transport.get.mock.calls[0]?.[0]).toBe('/processes?kind=pty')
    expect(transport.post.mock.calls[0]).toEqual(['/processes/pty-1/signals', { signal: undefined, escalateAfterMs: undefined, escalateTo: undefined }])
    expect(transport.delete.mock.calls[0]).toEqual(['/processes/pty-1'])
  })

  it('rejects terminal geometry on non-pty process creation before posting', async () => {
    const transport = transportStub()

    await expect(new ProcessService(transport.object, 'sbx').start('echo hi', { terminal: { cols: 80, rows: 24 } })).rejects.toMatchObject({
      code: 'UNSUPPORTED_OPERATION',
      source: 'SDK',
    })
    expect(transport.post).not.toHaveBeenCalled()
  })

  it('treats a literal wait timeout of zero as an unbounded toolbox poll', async () => {
    jest.useFakeTimers()
    try {
      const transport = transportStub()
      transport.get.mockImplementation(async () => {
        await new Promise((resolve) => setTimeout(resolve, 5))
        return { reason: 'exited', exitCode: 0 }
      })
      const handle = new ProcessService(transport.object, 'sbx').connect('proc')

      const waitPromise = handle.wait({ timeout: 0 })
      await jest.advanceTimersByTimeAsync(5)

      await expect(waitPromise).resolves.toEqual({ reason: 'exited', rawReason: 'exited', exitCode: 0 })
      expect(transport.get.mock.calls[0]).toEqual(['/processes/proc/wait?timeoutMs=0', { timeout: 0 }])
    } finally {
      jest.useRealTimers()
    }
  })

  it('blocks stdin on known code handles and lets exec or unknown handles call the wire', async () => {
    const codeTransport = transportStub()
    const codeHandle = new ProcessHandle(codeTransport.object, 'sbx', 'code-proc', 'code')

    await expect(codeHandle.writeStdin('print(1)')).rejects.toBeInstanceOf(UnsupportedOperationError)
    await expect(codeHandle.closeStdin()).rejects.toBeInstanceOf(UnsupportedOperationError)
    expect(codeTransport.post).not.toHaveBeenCalled()

    const execTransport = transportStub()
    const execHandle = new ProcessHandle(execTransport.object, 'sbx', 'exec-proc', 'exec')

    await execHandle.writeStdin('hello')
    expect(execTransport.post).toHaveBeenCalledWith('/processes/exec-proc/stdin', { data: 'hello' })

    const unknownTransport = transportStub()
    const unknownHandle = new ProcessService(unknownTransport.object, 'sbx').connect('unknown-proc')

    await unknownHandle.closeStdin()
    expect(unknownTransport.post).toHaveBeenCalledWith('/processes/unknown-proc/stdin', { data: '', eof: true })
  })

  it('returns timed_out when a computed wait budget reaches zero instead of forwarding zero', async () => {
    const transport = transportStub()
    const now = jest.spyOn(Date, 'now')
    try {
      now.mockReturnValueOnce(0)
      transport.get.mockResolvedValueOnce({ reason: 'timed_out' })
      now.mockReturnValueOnce(0)
      now.mockReturnValueOnce(1)
      now.mockReturnValueOnce(1)
      const handle = new ProcessService(transport.object, 'sbx').connect('proc')

      await expect(handle.wait({ timeout: 1 })).resolves.toEqual({ reason: 'timed_out' })
      expect(transport.get.mock.calls).toEqual([['/processes/proc/wait?timeoutMs=1', { timeout: 1 + WAIT_READ_SLACK_MS }]])
    } finally {
      now.mockRestore()
    }
  })

  it('parses process log pages and CRLF-framed SSE streams without a live websocket', async () => {
    const transport = transportStub()
    transport.get
      .mockResolvedValueOnce([{ cursor: 'a', channel: 'stdout', data: 'text-output', timestamp: 41 }])
      .mockResolvedValueOnce({ frames: [{ cursor: 'b', channel: 'stderr', data: 'err', timestamp: 42 }], nextCursor: 'b', truncatedHead: true, firstAvailableCursor: 'a' })
    transport.fetch.mockResolvedValue(sseResponse([
      'id: c1\r\nevent: log\r\ndata: {"channel":"stdout","data":"hi"}\r\n\r\n',
      'id: c2\r\nevent: state\r\ndata: {"reason":"exited","exitCode":7}\r\n\r\n',
      'id: c3\r\nevent: warning\r\ndata: {"message":"trimmed","firstAvailableCursor":"c0"}\r\n\r\n',
      'id: c4\r\nevent: error\r\ndata: {"message":"boom"}\r\n\r\n',
      'id: c5\r\nevent: eof\r\ndata: {}\r\n\r\n',
    ]))
    const handle = new ProcessService(transport.object, 'sbx').connect('proc')

    await expect(handle.logs()).resolves.toEqual({ items: [{ cursor: 'a', channel: 'stdout', data: 'text-output', timestamp: 41 }] })
    await expect(handle.logs({ cursor: 'a', limit: 1, encoding: 'base64' })).resolves.toMatchObject({ items: [{ cursor: 'b', channel: 'stderr', data: 'err', timestamp: 42 }], nextCursor: 'b', truncatedHead: true, firstAvailableCursor: 'a' })

    const events = []
    for await (const event of handle.streamLogs({ cursor: 'a', limit: 5 })) events.push(event)
    expect(transport.fetch.mock.calls[0]).toEqual(['/processes/proc/logs?cursor=a&limit=5&follow=true', { headers: { Accept: 'text/event-stream', 'Last-Event-ID': 'a' }, signal: undefined }])
    expect(events).toMatchObject([
      { type: 'log', cursor: 'c1', frame: { channel: 'stdout', data: 'hi' } },
      { type: 'state', cursor: 'c2', result: { reason: 'exited', exitCode: 7 } },
      { type: 'warning', cursor: 'c3', message: 'trimmed', firstAvailableCursor: 'c0' },
      { type: 'error', cursor: 'c4' },
      { type: 'eof', cursor: 'c5' },
    ])
  })

  it('parses CR-only framed SSE streams', async () => {
    const fixture = [
      'event: start\rdata: {}\r\r',
      'event: fs\rdata: {"type":"create","name":"a.txt","isDir":false,"timestamp":1}\r\r',
    ]

    const messages = []
    for await (const message of sseMessages(sseResponse(fixture))) messages.push(message)

    expect(messages).toEqual([
      { event: 'start', id: undefined, data: '{}' },
      { event: 'fs', id: undefined, data: '{"type":"create","name":"a.txt","isDir":false,"timestamp":1}' },
    ])
  })

  it('cancels the SSE reader when a consumer stops early', async () => {
    const cancel = jest.fn(async () => undefined)
    const releaseLock = jest.fn()
    const reader = {
      read: jest.fn().mockResolvedValue({
        done: false,
        value: new TextEncoder().encode('id: c1\r\nevent: log\r\ndata: {"channel":"stdout","data":"hi"}\r\n\r\n'),
      }),
      cancel,
      releaseLock,
    }

    const iterator = sseMessages({
      body: { getReader: () => reader },
    } as unknown as Response)[Symbol.asyncIterator]()

    await expect(iterator.next()).resolves.toEqual({
      done: false,
      value: { event: 'log', id: 'c1', data: '{"channel":"stdout","data":"hi"}' },
    })
    const close = iterator.return?.bind(iterator)
    expect(close).toBeDefined()
    if (!close) throw new Error('expected async iterator return() hook')
    await expect(close(undefined)).resolves.toEqual({ done: true, value: undefined })
    expect(cancel).toHaveBeenCalledTimes(1)
    expect(releaseLock).toHaveBeenCalledTimes(1)
  })

  it('cancels the download response body when opening the local destination fails', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-download-open-fail-'))
    const target = join(tempRoot, 'missing', 'target.txt')
    const cancel = jest.fn().mockResolvedValue(undefined)
    transport.fetch.mockResolvedValue({
      headers: new Headers(),
      body: { cancel },
    } as unknown as Response)

    try {
      await expect(fs.downloadFile('/remote/file.txt', target)).rejects.toThrow()
    } finally {
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('runs interpreter code with client-side timeout interrupt and explicit session contexts', async () => {
    const transport = transportStub()
    transport.post
      .mockResolvedValueOnce({ id: 'sess' })
      .mockResolvedValueOnce({ id: 'ctx-proc' })
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce({ id: 'one-shot' })
    transport.get
      .mockResolvedValueOnce({ reason: 'timed_out' })
      .mockResolvedValueOnce({ id: 'ctx-proc', kind: 'code', state: 'running', createdAt: '2026-01-01T00:00:00Z', cwd: '/w', stdin: 'closed', keepLogs: 'until_cleanup', truncatedHead: false })
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 130 })
      .mockResolvedValueOnce({ frames: [{ cursor: '1', channel: 'stdout', data: 'after interrupt' }], nextCursor: '1' })
      .mockResolvedValueOnce({ frames: [], nextCursor: '1' })
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [{ cursor: '2', channel: 'stdout', data: 'a' }, { cursor: '3', channel: 'stderr', data: 'warn' }, { cursor: '4', channel: 'stdout', data: 'c' }], nextCursor: '4' })
      .mockResolvedValueOnce({ frames: [], nextCursor: '2' })
    const process = new ProcessService(transport.object, 'sbx')
    const interpreter = new InterpreterService(transport.object, process)

    const context = await interpreter.createContext({ id: 'sess', language: 'python', cwd: '/w', env: { A: 'B' } })
    expect(transport.post.mock.calls[0]).toEqual(['/sessions', { id: 'sess', kernel: 'python', cwd: '/w', env: { A: 'B' } }])

    const now = jest.spyOn(Date, 'now')
      .mockReturnValueOnce(0)
      .mockReturnValueOnce(0)
      .mockReturnValueOnce(0)
      .mockReturnValueOnce(0)
      .mockReturnValueOnce(0)
      .mockReturnValueOnce(10)
      .mockReturnValueOnce(10)
      .mockReturnValueOnce(10)
    let interrupted
    try {
      interrupted = await context.run('print(1)', { timeout: 10 })
    } finally {
      now.mockRestore()
    }
    expect(transport.post.mock.calls[1]).toEqual(['/processes', { code: 'print(1)', kind: 'code', sessionId: 'sess', language: 'python' }, { timeout: 10 }])
    expect(transport.post.mock.calls[2]).toEqual(['/processes/ctx-proc/signals', { signal: 'SIGINT', escalateAfterMs: undefined, escalateTo: undefined }])
    expect(interrupted).toMatchObject({ process: { processId: 'ctx-proc' }, logs: { stdout: 'after interrupt', stderr: '' } })

    const oneShot = await interpreter.run('console.error("warn")', { language: 'typescript', sessionId: 'sess' })
    expect(transport.post.mock.calls[3]).toEqual(['/processes', { code: 'console.error("warn")', kind: 'code', sessionId: 'sess', language: 'typescript' }, undefined])
    expect(oneShot).toMatchObject({ process: { processId: 'one-shot', exitCode: 0 }, logs: { stdout: 'ac', stderr: 'warn' } })

    expect(interpreter.connectContext('sess', 'python').id).toBe('sess')
    await interpreter.deleteContext(context)
    expect(transport.delete.mock.calls[0]).toEqual(['/sessions/sess'])
  })

  it('rebinds explicit interpreter contexts from another client onto the current transport', async () => {
    const foreignTransport = transportStub()
    const localTransport = transportStub()
    localTransport.post.mockResolvedValueOnce({ id: 'proc' })
    localTransport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [], nextCursor: undefined })
    const foreignContext = new InterpreterService(foreignTransport.object, new ProcessService(foreignTransport.object, 'sbx')).connectContext('ctx', 'python')
    const interpreter = new InterpreterService(localTransport.object, new ProcessService(localTransport.object, 'sbx'))

    await expect(interpreter.run('print(1)', { context: foreignContext })).resolves.toMatchObject({ process: { processId: 'proc', exitCode: 0 } })
    expect(localTransport.post.mock.calls[0]).toEqual(['/processes', { code: 'print(1)', kind: 'code', sessionId: 'ctx', language: 'python' }])
    expect(foreignTransport.post).not.toHaveBeenCalled()
  })

  it('rejects env on explicit interpreter contexts before posting a code process', async () => {
    const transport = transportStub()
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))

    await expect(interpreter.run('print(1)', { sessionId: 'sess', env: { A: 'B' } })).rejects.toMatchObject({
      code: 'BAD_REQUEST',
      source: 'SDK',
    })
    expect(transport.post).not.toHaveBeenCalled()
  })

  it('recomputes the interpreter wait budget after process creation', async () => {
    jest.useFakeTimers()
    try {
      const transport = transportStub()
      transport.post.mockImplementationOnce(async () => {
        jest.advanceTimersByTime(5)
        return { id: 'proc' }
      })
      transport.get
        .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
        .mockResolvedValueOnce({ frames: [{ cursor: '1', channel: 'stdout', data: 'ok' }], nextCursor: '1' })
        .mockResolvedValueOnce({ frames: [], nextCursor: '1' })
      const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))

      await expect(interpreter.run('print(1)', { timeout: 10 })).resolves.toMatchObject({ logs: { stdout: 'ok' } })
      expect(transport.post.mock.calls[0]).toEqual(['/processes', { code: 'print(1)', kind: 'code', language: 'python', cwd: undefined, env: undefined }, { timeout: 10 }])
      expect(transport.get.mock.calls[0]).toEqual(['/processes/proc/wait?timeoutMs=5', { timeout: 5 + WAIT_READ_SLACK_MS }])
    } finally {
      jest.useRealTimers()
    }
  })

  it('does not turn an expired interpreter budget into an unbounded toolbox wait', async () => {
    jest.useFakeTimers()
    try {
      const transport = transportStub()
      transport.post
        .mockImplementationOnce(async () => {
          jest.advanceTimersByTime(10)
          return { id: 'proc' }
        })
        .mockResolvedValueOnce(undefined)
      transport.get
        .mockResolvedValueOnce({ reason: 'exited', exitCode: 130 })
        .mockResolvedValueOnce({ frames: [], nextCursor: undefined })
      const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))

      await expect(interpreter.run('print(1)', { timeout: 10 })).resolves.toMatchObject({ process: { processId: 'proc', reason: 'exited' } })
      expect(transport.get.mock.calls.map((call) => call[0])).not.toContain('/processes/proc/wait?timeoutMs=0')
      expect(transport.post.mock.calls[1]).toEqual(['/processes/proc/signals', { signal: 'SIGINT', escalateAfterMs: undefined, escalateTo: undefined }])
    } finally {
      jest.useRealTimers()
    }
  })

  it('omits sessionId on context-free interpreter runs to select the default context', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValueOnce({ id: 'one' }).mockResolvedValueOnce({ id: 'two' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [{ cursor: '1', channel: 'stdout', data: 'first' }], nextCursor: '1' })
      .mockResolvedValueOnce({ frames: [], nextCursor: '1' })
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [{ cursor: '2', channel: 'stdout', data: 'second' }], nextCursor: '2' })
      .mockResolvedValueOnce({ frames: [], nextCursor: '2' })
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))

    await expect(interpreter.run('x = 5', { language: 'python' })).resolves.toMatchObject({ logs: { stdout: 'first' } })
    await expect(interpreter.run('print(x)', { language: 'python' })).resolves.toMatchObject({ logs: { stdout: 'second' } })

    expect(transport.post.mock.calls).toEqual([
      ['/processes', { code: 'x = 5', kind: 'code', language: 'python' }],
      ['/processes', { code: 'print(x)', kind: 'code', language: 'python' }],
    ])
  })

  it('serializes interpreter contexts as identity-only handles and reconnects them cleanly', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValueOnce({ id: 'ctx' }).mockResolvedValueOnce({ id: 'proc' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [], nextCursor: undefined })
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))

    const context = await interpreter.createContext({ id: 'ctx', language: 'python' })
    const serialized = JSON.parse(JSON.stringify(context)) as {
      sandboxId: string
      contextId: string
      language: string
    }

    expect(context.toJSON()).toEqual({ sandboxId: 'sbx', contextId: 'ctx', language: 'python' })
    expect(serialized).toEqual({ sandboxId: 'sbx', contextId: 'ctx', language: 'python' })
    expect({ ...context }).toEqual({ sandboxId: 'sbx', id: 'ctx', language: 'python' })

    const rebound = interpreter.connectContext(serialized)
    await expect(rebound.run('print(1)')).resolves.toMatchObject({ process: { processId: 'proc', exitCode: 0 } })
    expect(transport.post.mock.calls[1]).toEqual(['/processes', { code: 'print(1)', kind: 'code', sessionId: 'ctx', language: 'python' }])
  })

  it('serializes concurrent explicit-context runs before submitting the next process', async () => {
    const transport = transportStub()
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    const context = interpreter.connectContext('ctx', 'python')
    let releaseFirstWait: (() => void) | undefined
    let firstSubmitted: (() => void) | undefined
    const firstSubmittedPromise = new Promise<void>((resolve) => {
      firstSubmitted = resolve
    })
    transport.post
      .mockResolvedValueOnce({ id: 'p1' })
      .mockResolvedValueOnce({ id: 'p2' })
    transport.get.mockImplementation(async (path: string) => {
      if (path === '/processes/p1/wait?timeoutMs=0') {
        firstSubmitted?.()
        return new Promise((resolve) => {
          releaseFirstWait = () => resolve({ reason: 'exited', exitCode: 0 })
        })
      }
      if (path === '/processes/p1/logs') return { frames: [], nextCursor: undefined }
      if (path === '/processes/p2/wait?timeoutMs=0') return { reason: 'exited', exitCode: 0 }
      if (path === '/processes/p2/logs') return { frames: [], nextCursor: undefined }
      throw new Error(`unexpected GET ${path}`)
    })

    const first = context.run('print(1)')
    await firstSubmittedPromise
    const second = context.run('print(2)')
    await Promise.resolve()

    expect(transport.post.mock.calls).toEqual([
      ['/processes', { code: 'print(1)', kind: 'code', sessionId: 'ctx', language: 'python' }],
    ])

    releaseFirstWait?.()

    await expect(first).resolves.toMatchObject({ process: { processId: 'p1', exitCode: 0 } })
    await expect(second).resolves.toMatchObject({ process: { processId: 'p2', exitCode: 0 } })
    expect(transport.post.mock.calls).toEqual([
      ['/processes', { code: 'print(1)', kind: 'code', sessionId: 'ctx', language: 'python' }],
      ['/processes', { code: 'print(2)', kind: 'code', sessionId: 'ctx', language: 'python' }],
    ])
  })

  it('serializes concurrent context-free runs before submitting the next process', async () => {
    const transport = transportStub()
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    let releaseFirstWait: (() => void) | undefined
    let firstSubmitted: (() => void) | undefined
    const firstSubmittedPromise = new Promise<void>((resolve) => {
      firstSubmitted = resolve
    })
    transport.post
      .mockResolvedValueOnce({ id: 'p1' })
      .mockResolvedValueOnce({ id: 'p2' })
    transport.get.mockImplementation(async (path: string) => {
      if (path === '/processes/p1/wait?timeoutMs=0') {
        firstSubmitted?.()
        return new Promise((resolve) => {
          releaseFirstWait = () => resolve({ reason: 'exited', exitCode: 0 })
        })
      }
      if (path === '/processes/p1/logs') return { frames: [], nextCursor: undefined }
      if (path === '/processes/p2/wait?timeoutMs=0') return { reason: 'exited', exitCode: 0 }
      if (path === '/processes/p2/logs') return { frames: [], nextCursor: undefined }
      throw new Error(`unexpected GET ${path}`)
    })

    const first = interpreter.run('print(1)', { language: 'python' })
    await firstSubmittedPromise
    const second = interpreter.run('print(2)', { language: 'python' })
    await Promise.resolve()

    expect(transport.post.mock.calls).toEqual([
      ['/processes', { code: 'print(1)', kind: 'code', language: 'python' }],
    ])

    releaseFirstWait?.()

    await expect(first).resolves.toMatchObject({ process: { processId: 'p1', exitCode: 0 } })
    await expect(second).resolves.toMatchObject({ process: { processId: 'p2', exitCode: 0 } })
    expect(transport.post.mock.calls).toEqual([
      ['/processes', { code: 'print(1)', kind: 'code', language: 'python' }],
      ['/processes', { code: 'print(2)', kind: 'code', language: 'python' }],
    ])
  })

  it('returns timed_out for explicit-context runs that expire while queued without submitting them', async () => {
    const transport = transportStub()
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    const context = interpreter.connectContext('ctx', 'python')
    let releaseFirstWait: (() => void) | undefined
    let firstSubmitted: (() => void) | undefined
    const firstSubmittedPromise = new Promise<void>((resolve) => {
      firstSubmitted = resolve
    })
    transport.post.mockResolvedValueOnce({ id: 'p1' })
    transport.get.mockImplementation(async (path: string) => {
      if (path === '/processes/p1/wait?timeoutMs=0') {
        firstSubmitted?.()
        return new Promise((resolve) => {
          releaseFirstWait = () => resolve({ reason: 'exited', exitCode: 0 })
        })
      }
      if (path === '/processes/p1/logs') return { frames: [], nextCursor: undefined }
      throw new Error(`unexpected GET ${path}`)
    })

    const first = context.run('print(1)')
    await firstSubmittedPromise
    const queued = context.run('print(2)', { timeout: 1 })
    await jest.advanceTimersByTimeAsync(1)

    await expect(queued).resolves.toMatchObject({
      process: { processId: '', reason: 'timed_out' },
      results: [],
      logs: { stdout: '', stderr: '' },
    })
    expect(transport.post).toHaveBeenCalledTimes(1)

    releaseFirstWait?.()
    await expect(first).resolves.toMatchObject({ process: { processId: 'p1', exitCode: 0 } })
    expect(transport.post).toHaveBeenCalledTimes(1)
  })

  it('rejects bare-id interpreter reconnects without a language before any wire call', () => {
    const transport = transportStub()
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))

    expect(() => interpreter.connectContext('sess' as unknown as never)).toThrow(
      'language is required when reconnecting an interpreter context by id',
    )
    expect(transport.post).not.toHaveBeenCalled()
    expect(transport.get).not.toHaveBeenCalled()
  })

  it('evicts interpreter queues on close and deleteContext', async () => {
    const transport = transportStub()
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    transport.post
      .mockResolvedValueOnce({ id: 'ctx' })
      .mockResolvedValueOnce({ id: 'ctx-2' })

    const context = await interpreter.createContext({ id: 'ctx', language: 'python' })
    expect(interpreter['contextQueues'].size).toBe(1)

    await context.close()
    expect(interpreter['contextQueues'].size).toBe(0)

    await interpreter.createContext({ id: 'ctx-2', language: 'python' })
    expect(interpreter['contextQueues'].size).toBe(1)

    await interpreter.deleteContext('ctx-2')
    expect(interpreter['contextQueues'].size).toBe(0)
  })

  it('keeps interpreter queues after a failed close so reconnects stay serialized on the original queue', async () => {
    const transport = transportStub()
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    const context = interpreter.connectContext('ctx', 'python')
    let releaseFirstWait: (() => void) | undefined
    let firstSubmitted: (() => void) | undefined
    const firstSubmittedPromise = new Promise<void>((resolve) => {
      firstSubmitted = resolve
    })
    transport.post
      .mockResolvedValueOnce({ id: 'p1' })
      .mockResolvedValueOnce({ id: 'p2' })
    transport.get.mockImplementation(async (path: string) => {
      if (path === '/processes/p1/wait?timeoutMs=0') {
        firstSubmitted?.()
        return new Promise((resolve) => {
          releaseFirstWait = () => resolve({ reason: 'exited', exitCode: 0 })
        })
      }
      if (path === '/processes/p1/logs') return { frames: [], nextCursor: undefined }
      if (path === '/processes/p2/wait?timeoutMs=0') return { reason: 'exited', exitCode: 0 }
      if (path === '/processes/p2/logs') return { frames: [], nextCursor: undefined }
      throw new Error(`unexpected GET ${path}`)
    })
    transport.delete.mockRejectedValueOnce(new DaytonaError('still alive', { statusCode: 503, code: 'SERVICE_UNAVAILABLE', source: 'DAYTONA_DAEMON' }))

    const first = context.run('print(1)')
    await firstSubmittedPromise

    await expect(context.close()).rejects.toMatchObject({
      statusCode: 503,
      code: 'SERVICE_UNAVAILABLE',
      source: 'DAYTONA_DAEMON',
    })

    const rebound = interpreter.connectContext('ctx', 'python')
    const second = rebound.run('print(2)')
    await Promise.resolve()

    expect(interpreter['contextQueues'].size).toBe(1)
    expect(transport.post.mock.calls).toEqual([
      ['/processes', { code: 'print(1)', kind: 'code', sessionId: 'ctx', language: 'python' }],
    ])
    expect(transport.delete.mock.calls).toEqual([['/sessions/ctx']])

    releaseFirstWait?.()

    await expect(first).resolves.toMatchObject({ process: { processId: 'p1', exitCode: 0 } })
    await expect(second).resolves.toMatchObject({ process: { processId: 'p2', exitCode: 0 } })
    expect(transport.post.mock.calls).toEqual([
      ['/processes', { code: 'print(1)', kind: 'code', sessionId: 'ctx', language: 'python' }],
      ['/processes', { code: 'print(2)', kind: 'code', sessionId: 'ctx', language: 'python' }],
    ])
  })

  it('keeps interpreter queues until queued runs drain after deleteContext succeeds', async () => {
    const transport = transportStub()
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    const context = interpreter.connectContext('ctx', 'python')
    let releaseFirstWait: (() => void) | undefined
    let firstSubmitted: (() => void) | undefined
    const firstSubmittedPromise = new Promise<void>((resolve) => {
      firstSubmitted = resolve
    })
    transport.post
      .mockResolvedValueOnce({ id: 'p1' })
      .mockResolvedValueOnce({ id: 'p2' })
    transport.get.mockImplementation(async (path: string) => {
      if (path === '/processes/p1/wait?timeoutMs=0') {
        firstSubmitted?.()
        return new Promise((resolve) => {
          releaseFirstWait = () => resolve({ reason: 'exited', exitCode: 0 })
        })
      }
      if (path === '/processes/p1/logs') return { frames: [], nextCursor: undefined }
      if (path === '/processes/p2/wait?timeoutMs=0') return { reason: 'exited', exitCode: 0 }
      if (path === '/processes/p2/logs') return { frames: [], nextCursor: undefined }
      throw new Error(`unexpected GET ${path}`)
    })

    const first = context.run('print(1)')
    await firstSubmittedPromise
    const second = context.run('print(2)')
    await Promise.resolve()

    await expect(interpreter.deleteContext('ctx')).resolves.toBeUndefined()
    expect(interpreter['contextQueues'].has('ctx')).toBe(true)
    expect(transport.post.mock.calls).toEqual([
      ['/processes', { code: 'print(1)', kind: 'code', sessionId: 'ctx', language: 'python' }],
    ])
    expect(transport.delete.mock.calls).toEqual([['/sessions/ctx']])

    releaseFirstWait?.()

    await expect(first).resolves.toMatchObject({ process: { processId: 'p1', exitCode: 0 } })
    await expect(second).resolves.toMatchObject({ process: { processId: 'p2', exitCode: 0 } })
    expect(interpreter['contextQueues'].has('ctx')).toBe(false)
    expect(transport.post.mock.calls).toEqual([
      ['/processes', { code: 'print(1)', kind: 'code', sessionId: 'ctx', language: 'python' }],
      ['/processes', { code: 'print(2)', kind: 'code', sessionId: 'ctx', language: 'python' }],
    ])
  })

  it('does not leak one interpreter queue per isolated run', async () => {
    const transport = transportStub()
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    let processIndex = 0
    transport.post.mockImplementation(async (path: string, body: unknown) => {
      if (path === '/sessions') {
        return { id: (body as { readonly id: string }).id }
      }
      if (path === '/processes') {
        processIndex += 1
        return { id: `proc-${processIndex}` }
      }
      throw new Error(`unexpected POST ${path}`)
    })
    transport.get.mockImplementation(async (path: string) => {
      if (path.endsWith('/wait?timeoutMs=0')) return { reason: 'exited', exitCode: 0 }
      if (path.endsWith('/logs')) return { frames: [], nextCursor: undefined }
      throw new Error(`unexpected GET ${path}`)
    })
    transport.delete.mockResolvedValue(undefined)

    await interpreter.run('print(1)', { language: 'python', isolated: true })
    await interpreter.run('print(2)', { language: 'python', isolated: true })
    await interpreter.run('print(3)', { language: 'python', isolated: true })

    expect(interpreter['contextQueues'].size).toBe(0)
    expect(transport.delete).toHaveBeenCalledTimes(3)
  })

  it('spends the isolated-run caller budget on the setup request before starting code', async () => {
    jest.useFakeTimers()
    try {
      const transport = transportStub()
      transport.post
        .mockImplementationOnce(async () => {
          jest.advanceTimersByTime(5)
          return { id: 'isolated-python' }
        })
      transport.delete.mockResolvedValue(undefined)
      const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))

      await expect(interpreter.run('print(1)', { language: 'python', isolated: true, timeout: 2 }))
        .rejects.toMatchObject({ code: 'REQUEST_TIMEOUT', source: 'SDK' })

      expect(transport.post.mock.calls[0]).toEqual(['/sessions', { id: expect.any(String), kernel: 'python', cwd: undefined, env: undefined }, { timeout: 2 }])
      expect(transport.post).toHaveBeenCalledTimes(1)
    } finally {
      jest.useRealTimers()
    }
  })

  it('deletes an isolated context by id even when setup times out before createContext returns', async () => {
    const transport = transportStub()
    transport.post.mockRejectedValueOnce(new ClientTimeoutError('timed out', { statusCode: 408, code: 'CLIENT_TIMEOUT', source: 'SDK' }))
    transport.delete.mockResolvedValue(undefined)
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))

    await expect(interpreter.run('print(1)', { language: 'python', isolated: true })).rejects.toBeInstanceOf(ClientTimeoutError)

    const createPayload = transport.post.mock.calls[0]?.[1]
    const createdContextId = typeof createPayload === 'object' && createPayload !== null && 'id' in createPayload
      ? String(createPayload.id)
      : undefined
    expect(createdContextId).toMatch(/^isolated-python-.+/)
    expect(transport.delete.mock.calls[0]).toEqual([`/sessions/${createdContextId}`])
  })

  it('rejects foreign interpreter contexts before sandbox.runCode reaches the wire', async () => {
    const localTransport = transportStub()
    const foreignTransport = transportStub()
    foreignTransport.post.mockResolvedValueOnce({ id: 'ctx' })

    const localSandbox = new Sandbox('local', undefined, undefined, 'key')
    Reflect.set(localSandbox, 'toolboxTransport', localTransport.object)
    const foreignSandbox = new Sandbox('foreign', undefined, undefined, 'key')
    Reflect.set(foreignSandbox, 'toolboxTransport', foreignTransport.object)

    const foreignContext = await foreignSandbox.interpreter.createContext({ id: 'ctx', language: 'python' })
    await expect(localSandbox.runCode('print(1)', { context: foreignContext })).rejects.toMatchObject({
      statusCode: 400,
      code: 'BAD_REQUEST',
      source: 'SDK',
    })

    expect(localTransport.post).not.toHaveBeenCalled()
    expect(foreignTransport.post.mock.calls).toEqual([
      ['/sessions', { id: 'ctx', kernel: 'python', cwd: undefined, env: undefined }],
    ])
  })

  it('rejects foreign interpreter contexts before deleteContext reaches the wire', async () => {
    const localTransport = transportStub()
    const foreignTransport = transportStub()
    foreignTransport.post.mockResolvedValueOnce({ id: 'ctx' })

    const localSandbox = new Sandbox('local', undefined, undefined, 'key')
    Reflect.set(localSandbox, 'toolboxTransport', localTransport.object)
    const foreignSandbox = new Sandbox('foreign', undefined, undefined, 'key')
    Reflect.set(foreignSandbox, 'toolboxTransport', foreignTransport.object)

    const foreignContext = await foreignSandbox.interpreter.createContext({ id: 'ctx', language: 'python' })
    await expect(localSandbox.interpreter.deleteContext(foreignContext)).rejects.toMatchObject({
      statusCode: 400,
      code: 'BAD_REQUEST',
      source: 'SDK',
    })

    expect(localTransport.delete).not.toHaveBeenCalled()
    expect(foreignTransport.post.mock.calls).toEqual([
      ['/sessions', { id: 'ctx', kernel: 'python', cwd: undefined, env: undefined }],
    ])
  })

  it('propagates SESSION_NOT_FOUND from an explicit context without recreating or retrying', async () => {
    const transport = transportStub()
    transport.post
      .mockResolvedValueOnce({ id: 'ctx' })
      .mockRejectedValueOnce(createDaytonaError('missing', 404, 'SESSION_NOT_FOUND', 'DAYTONA_DAEMON'))
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    const context = await interpreter.createContext({ id: 'ctx', language: 'python' })

    const run = context.run('print(1)')
    await expect(run).rejects.toBeInstanceOf(SessionNotFoundError)
    await expect(run).rejects.toMatchObject({ statusCode: 404, code: 'SESSION_NOT_FOUND', source: 'DAYTONA_DAEMON' })
    expect(transport.post.mock.calls).toEqual([
      ['/sessions', { id: 'ctx', kernel: 'python', cwd: undefined, env: undefined }],
      ['/processes', { code: 'print(1)', kind: 'code', sessionId: 'ctx', language: 'python' }],
    ])
  })

  it('isolated runs spend the caller budget on context creation and expire instead of running fresh', async () => {
    const transport = transportStub()
    transport.post.mockImplementationOnce(async (_path, body) => {
      jest.advanceTimersByTime(5_000) // POST /sessions took 5s of a 2s budget
      return { id: String((body as { id: string }).id) }
    })
    transport.delete.mockResolvedValueOnce({})
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    await expect(interpreter.run('print(1)', { language: 'python', isolated: true, timeout: 2_000 })).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT', source: 'SDK' })
    // Only the session create (and its best-effort delete) were issued — no execution.
    expect(transport.post).toHaveBeenCalledTimes(1)
    expect(transport.delete).toHaveBeenCalledTimes(1)
    await expect(interpreter.run('print(1)', { language: 'python', isolated: true, timeout: -1 })).rejects.toMatchObject({ code: 'BAD_REQUEST' })
    expect(transport.post).toHaveBeenCalledTimes(1)
  })

  it('uses an isolated explicit context and deletes it best-effort afterwards', async () => {
    const transport = transportStub()
    transport.post
      .mockImplementationOnce(async (_path, body) => ({ id: String((body as { id: string }).id) }))
      .mockResolvedValueOnce({ id: 'proc' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [], nextCursor: undefined })
    transport.delete.mockRejectedValueOnce(new DaytonaError('gone', { statusCode: 404, code: 'SESSION_NOT_FOUND' }))
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    await expect(interpreter.run('print(1)', { language: 'python', isolated: true })).resolves.toMatchObject({ process: { processId: 'proc', exitCode: 0 } })

    const createCall = transport.post.mock.calls[0]
    const runCall = transport.post.mock.calls[1]
    const deleteCall = transport.delete.mock.calls[0]
    const createPayload = createCall?.[1]
    const createdContextId = typeof createPayload === 'object' && createPayload !== null && 'id' in createPayload
      ? String(createPayload.id)
      : undefined

    expect(createdContextId).toMatch(/^isolated-python-.+/)
    expect(runCall).toEqual([
      '/processes',
      { code: 'print(1)', kind: 'code', sessionId: createdContextId, language: 'python' },
    ])
    expect(deleteCall).toEqual([`/sessions/${createdContextId}`])
    expect(interpreter['contextQueues'].size).toBe(0)
  })

  it('keeps interpreter client deadlines across toolbox long-poll caps', async () => {
    const now = jest.spyOn(Date, 'now').mockReturnValue(0)
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p' })
    transport.get
      .mockResolvedValueOnce({ reason: 'timed_out' })
      .mockResolvedValueOnce({ id: 'p', kind: 'code', state: 'running', createdAt: '2026-01-01T00:00:00Z', cwd: '/w', stdin: 'closed', keepLogs: 'until_cleanup', truncatedHead: false })
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [], nextCursor: undefined })
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    const context = interpreter.connectContext('ctx', 'python')
    try {
      await expect(context.run('1', { timeout: 600_001 })).resolves.toMatchObject({ process: { exitCode: 0 } })
      expect(transport.get.mock.calls[0]?.[0]).toBe('/processes/p/wait?timeoutMs=600001')
    } finally {
      now.mockRestore()
    }
  })

  it('surfaces the terminal-race result when the process exits during the wait loop', async () => {
    // The suite clock is frozen, so the 10ms budget never spends: a toolbox
    // `timed_out` chunk is re-polled inside `wait` and the exit that lands on
    // the next chunk is returned without any interrupt.
    const transport = transportStub()
    transport.post.mockResolvedValueOnce({ id: 'p' })
    transport.get
      .mockResolvedValueOnce({ reason: 'timed_out' })
      .mockResolvedValueOnce({ id: 'p', kind: 'code', state: 'running', createdAt: '2026-01-01T00:00:00Z', cwd: '/w', stdin: 'closed', keepLogs: 'until_cleanup', truncatedHead: false })
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [], nextCursor: undefined })
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    const context = interpreter.connectContext('ctx', 'python')
    await expect(context.run('1', { timeout: 10 })).resolves.toMatchObject({ process: { processId: 'p', reason: 'exited', exitCode: 0 } })
    expect(transport.post).toHaveBeenCalledTimes(1)
    expect(transport.get.mock.calls.map((call) => call[0])).toEqual([
      '/processes/p/wait?timeoutMs=10',
      '/processes/p',
      '/processes/p/wait?timeoutMs=10',
      '/processes/p/logs',
    ])
  })

  it('keeps the recovered terminal result when the retry wait settles before another interrupt', async () => {
    // Budget genuinely expires (clock advances past it during the first wait),
    // the interrupt is refused while the execution is still queued, and the
    // short retry wait observes the real exit — which wins over the timeout.
    const transport = transportStub()
    transport.post
      .mockResolvedValueOnce({ id: 'p' })
      .mockRejectedValueOnce(new DaytonaError('queued', { statusCode: 400, code: 'UNSUPPORTED_OPERATION' }))
    transport.get
      .mockImplementationOnce(async () => {
        jest.advanceTimersByTime(10)
        return { reason: 'timed_out' }
      })
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 130 })
      .mockResolvedValueOnce({ frames: [], nextCursor: undefined })
    const interpreter = new InterpreterService(transport.object, new ProcessService(transport.object, 'sbx'))
    const context = interpreter.connectContext('ctx', 'python')
    await expect(context.run('1', { timeout: 10 })).resolves.toMatchObject({ process: { reason: 'exited', exitCode: 130 } })
    expect(transport.post).toHaveBeenCalledTimes(2)
    expect(transport.post.mock.calls[1]?.[0]).toBe('/processes/p/signals')
    expect(transport.get.mock.calls.map((call) => call[0])).toEqual([
      '/processes/p/wait?timeoutMs=10',
      '/processes/p/wait?timeoutMs=250',
      '/processes/p/logs',
    ])
  })

  it('merges object-form process options and cleans up PTYs when websocket attach fails', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p', kind: 'pty' })
    transport.delete.mockResolvedValue(undefined)
    await new ProcessService(transport.object, 'sbx').start('echo hi', { executionTimeout: 5, keepLogs: 'none' })
    expect(transport.post.mock.calls[0]?.[1]).toMatchObject({ shellCommand: 'echo hi', timeoutMs: 5, keepLogs: 'none' })

    const attachSpy = jest.spyOn(ProcessHandle.prototype, 'attach').mockRejectedValueOnce(new Error('ws failed'))
    try {
      const pty = new PtyService(new ProcessService(transport.object, 'sbx'))
      await expect(pty.attach()).rejects.toThrow('ws failed')
      expect(transport.delete.mock.calls[0]?.[0]).toBe('/processes/p')
    } finally {
      attachSpy.mockRestore()
    }
  })

  it('uses the exchanged platform token in websocket attach URLs under jwt auth', async () => {
    const originalFetch = globalThis.fetch
    const originalWebSocket = Reflect.get(globalThis, 'WebSocket') as unknown
    const urls: string[] = []
    class FakeWebSocket {
      public binaryType = 'arraybuffer'
      public readyState = 1
      private readonly listeners: Record<string, Array<() => void>> = { open: [], close: [] }
      constructor(url: string) {
        urls.push(url)
      }
      public send(): void {}
      public close(): void {
        for (const listener of this.listeners.close) listener()
      }
      public addEventListener(type: 'open' | 'close' | 'error' | 'message', listener: (() => void) | ((event: { data: unknown }) => void)): void {
        if (type === 'open') {
          const openListener = listener as () => void
          this.listeners.open.push(openListener)
          openListener()
          return
        }
        if (type === 'close') this.listeners.close.push(listener as () => void)
      }
    }
    Reflect.set(globalThis, 'WebSocket', FakeWebSocket)
    globalThis.fetch = jest.fn(async (input: string | URL | Request) => {
      expect(String(input)).toBe('http://api.invalid/auth/token')
      return new Response(JSON.stringify({ access_token: 'platform-1', expires_in: 300 }), {
        headers: { 'content-type': 'application/json' },
      })
    }) as never

    try {
      const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
      const duplex = await lazySandbox(client, 'sbx', { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' }).process.connect('proc').attach()
      duplex.close()
    } finally {
      if (originalWebSocket === undefined) Reflect.deleteProperty(globalThis, 'WebSocket')
      else Reflect.set(globalThis, 'WebSocket', originalWebSocket)
      globalThis.fetch = originalFetch
    }

    expect(urls).toEqual(['ws://toolbox.invalid/sandbox-api/sbx/processes/proc/attach?daytona_token=platform-1'])
  })

  it('forces replayable log retention for process.run even when keepLogs none is requested', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [{ cursor: 'c1', channel: 'stdout', data: 'ok' }], nextCursor: 'c1' })
      .mockResolvedValueOnce({ frames: [], nextCursor: 'c1' })
    const result = await new ProcessService(transport.object, 'sbx').run('echo hi', { keepLogs: 'none' })
    expect(transport.post.mock.calls[0]?.[1]).toMatchObject({ shellCommand: 'echo hi', keepLogs: 'on_exit_ttl' })
    expect(result.output).toBe('ok')
  })

  it('settles process.run through the inline toolbox wait/log path', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p', state: 'terminal', reason: 'exited', exitCode: 0, logs: { frames: [{ cursor: 'c1', channel: 'stdout', data: 'ok' }], nextCursor: 'c1', eof: true } })
    const result = await new ProcessService(transport.object, 'sbx').run('echo ok', { timeout: 1234 })
    expect(transport.post.mock.calls[0]?.[0]).toBe('/processes?waitMs=1234&logs=text')
    expect(transport.get).not.toHaveBeenCalled()
    expect(result).toMatchObject({ reason: 'exited', exitCode: 0, stdout: 'ok', output: 'ok' })
  })

  it('collects multi-page process logs after waiting', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [{ cursor: 'c1', channel: 'stdout', data: 'whole ' }], nextCursor: 'c1' })
      .mockResolvedValueOnce({ frames: [{ cursor: 'c2', channel: 'stdout', data: 'output' }], nextCursor: 'c2' })
      .mockResolvedValueOnce({ frames: [], nextCursor: 'c2' })
    const result = await new ProcessService(transport.object, 'sbx').run('echo whole output')
    expect(result.output).toBe('whole output')
  })

  it('falls back to a split wait when the inline wait window elapses, on the remaining budget', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p', state: 'running' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [{ cursor: 'c1', channel: 'stdout', data: 'late' }], nextCursor: 'c1' })
      .mockResolvedValueOnce({ frames: [], nextCursor: 'c1' })
    const result = await new ProcessService(transport.object, 'sbx').run('sleep 1', { timeout: 5000 })
    const waitUrl = String(transport.get.mock.calls[0]?.[0])
    expect(waitUrl).toMatch(/^\/processes\/p\/wait\?timeoutMs=/)
    const asked = Number(new URLSearchParams(waitUrl.split('?')[1]).get('timeoutMs'))
    expect(asked).toBeGreaterThan(0)
    expect(asked).toBeLessThanOrEqual(5000)
    expect(result).toMatchObject({ reason: 'exited', exitCode: 0, stdout: 'late' })
  })

  it('returns timed_out instead of throwing when the inline wait budget expires during a reply', async () => {
    const transport = transportStub()
    transport.post.mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1))
      return { id: 'p', state: 'running' }
    })
    transport.get
      .mockResolvedValueOnce({ frames: [{ cursor: 'c1', channel: 'stdout', data: 'partial' }], nextCursor: 'c1' })
      .mockResolvedValueOnce({ frames: [], nextCursor: 'c1' })

    const runPromise = new ProcessService(transport.object, 'sbx').run('sleep 1', { timeout: 1 })
    await jest.advanceTimersByTimeAsync(1)

    await expect(runPromise).resolves.toMatchObject({ reason: 'timed_out', stdout: 'partial', output: 'partial' })
    expect(transport.get.mock.calls).toEqual([
      ['/processes/p/logs'],
      ['/processes/p/logs?cursor=c1'],
    ])
  })

  it('normalizes fetch header objects before adding auth headers', async () => {
    const originalFetch = globalThis.fetch
    const seen: RequestInit[] = []
    globalThis.fetch = jest.fn((_url: string | URL | Request, init?: RequestInit) => {
      seen.push(init ?? {})
      return Promise.resolve(new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }))
    }) as never
    try {
      await new HttpTransport('http://toolbox/sbx', 'key').fetch('/events', { headers: new Headers([['X-Custom', 'yes']]) })
      expect(seen[0]?.headers).toMatchObject({ Authorization: 'Bearer key', 'x-custom': 'yes' })
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('applies the transport request timeout to fetch connection setup', async () => {
    jest.useFakeTimers()
    const originalFetch = globalThis.fetch
    globalThis.fetch = jest.fn((_url: string | URL | Request, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })) as never
    try {
      const pending = expect(new HttpTransport('http://toolbox/sbx', 'key', 5).fetch('/events')).rejects.toBeInstanceOf(ClientTimeoutError)
      await jest.advanceTimersByTimeAsync(5)
      await pending
    } finally {
      globalThis.fetch = originalFetch
      jest.useRealTimers()
    }
  })

  it('keeps fetch unbounded when the transport request timeout is zero', async () => {
    const originalFetch = globalThis.fetch
    let requestSignal: AbortSignal | undefined
    globalThis.fetch = jest.fn((_url: string | URL | Request, init?: RequestInit) => {
      requestSignal = init?.signal ?? undefined
      return Promise.resolve(new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }))
    }) as never
    try {
      await expect(new HttpTransport('http://toolbox/sbx', 'key', 0).fetch('/events')).resolves.toBeInstanceOf(Response)
      expect(requestSignal?.aborted).toBe(false)
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('preserves caller aborts on fetch instead of rewriting them as timeouts', async () => {
    const originalFetch = globalThis.fetch
    const callerAbort = new DOMException('caller cancelled', 'AbortError')
    globalThis.fetch = jest.fn((_url: string | URL | Request, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true })
    })) as never
    try {
      const controller = new AbortController()
      const pending = new HttpTransport('http://toolbox/sbx', 'key', 50).fetch('/events', { signal: controller.signal })
      controller.abort(callerAbort)
      await expect(pending).rejects.toBe(callerAbort)
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('drops the caller abort relay once the streamed body is consumed', async () => {
    const originalFetch = globalThis.fetch
    const controller = new AbortController()
    const removeSpy = jest.spyOn(controller.signal, 'removeEventListener')
    globalThis.fetch = jest.fn(async () => new Response(new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode('data: x\n\n'))
        c.close()
      },
    }), { status: 200 }))
    try {
      const response = await new HttpTransport('http://toolbox/sbx', 'key', 50).fetch('/events', { signal: controller.signal })
      expect(removeSpy).not.toHaveBeenCalled()
      await response.text()
      expect(removeSpy).toHaveBeenCalledWith('abort', expect.any(Function))
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('keeps the caller abort relay connected after fetch headers arrive', async () => {
    const originalFetch = globalThis.fetch
    let requestSignal: AbortSignal | undefined
    globalThis.fetch = jest.fn((_url: string | URL | Request, init?: RequestInit) => {
      requestSignal = init?.signal ?? undefined
      return Promise.resolve(new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }))
    }) as never
    try {
      const controller = new AbortController()
      await new HttpTransport('http://toolbox/sbx', 'key', 50).fetch('/events', { signal: controller.signal })
      expect(requestSignal?.aborted).toBe(false)
      controller.abort(new DOMException('stop streaming', 'AbortError'))
      expect(requestSignal?.aborted).toBe(true)
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('cancels the rejected 401 response body before retrying with a refreshed token', async () => {
    const originalFetch = globalThis.fetch
    const authProvider = {
      authorizationHeader: jest.fn()
        .mockResolvedValueOnce('Bearer old-token')
        .mockResolvedValueOnce('Bearer new-token'),
    }
    const cancel = jest.fn().mockResolvedValue(undefined)
    let callCount = 0
    globalThis.fetch = jest.fn(async () => {
      callCount += 1
      if (callCount === 1) {
        return {
          ok: false,
          status: 401,
          body: { cancel },
          headers: new Headers({ 'content-type': 'application/json' }),
          json: async () => ({ code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }),
          text: async () => JSON.stringify({ code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }),
        } as unknown as Response
      }
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    }) as never
    try {
      await new HttpTransport('http://toolbox/sbx', undefined, 50, { authProvider }).fetch('/events')
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(authProvider.authorizationHeader).toHaveBeenNthCalledWith(2, expect.objectContaining({ forceRefresh: true, rejectedAuthorization: 'Bearer old-token', signal: expect.any(AbortSignal) }))
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('swallows rejected body cancellation before retrying an authProvider 401 refresh', async () => {
    const originalFetch = globalThis.fetch
    const authProvider = {
      authorizationHeader: jest.fn()
        .mockResolvedValueOnce('Bearer old-token')
        .mockResolvedValueOnce('Bearer new-token'),
    }
    const cancel = jest.fn().mockRejectedValue(new Error('cancel failed'))
    let callCount = 0
    globalThis.fetch = jest.fn(async () => {
      callCount += 1
      if (callCount === 1) {
        return {
          ok: false,
          status: 401,
          body: { cancel },
          headers: new Headers({ 'content-type': 'application/json' }),
          json: async () => ({ code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }),
          text: async () => JSON.stringify({ code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }),
        } as unknown as Response
      }
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    }) as never
    try {
      await expect(new HttpTransport('http://toolbox/sbx', undefined, 50, { authProvider }).fetch('/events')).resolves.toBeInstanceOf(Response)
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(authProvider.authorizationHeader).toHaveBeenNthCalledWith(2, expect.objectContaining({ forceRefresh: true, rejectedAuthorization: 'Bearer old-token', signal: expect.any(AbortSignal) }))
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('replays a streamed upload with a fresh body after jwt token refresh without regressing progress', async () => {
    const originalFetch = globalThis.fetch
    const tempRoot = await mkdtemp(join(tmpdir(), 'daytona-sdk-upload-refresh-'))
    const localPath = join(tempRoot, 'refresh.txt')
    const chunkA = new TextEncoder().encode('streamed ')
    const chunkB = new TextEncoder().encode('upload body')
    const fileBytes = new Uint8Array(chunkA.byteLength + chunkB.byteLength)
    fileBytes.set(chunkA, 0)
    fileBytes.set(chunkB, chunkA.byteLength)
    await writeFile(localPath, Buffer.from(fileBytes))
    const attempts: Uint8Array[] = []
    const progress: Array<readonly [number, number | undefined]> = []
    let uploadCalls = 0
    const mutableNodeFs = require('node:fs') as typeof import('node:fs')
    const originalCreateReadStream = mutableNodeFs.createReadStream
    let createReadStreamCalls = 0
    mutableNodeFs.createReadStream = ((..._args: Parameters<typeof originalCreateReadStream>) => {
      createReadStreamCalls += 1
      return {
        async *[Symbol.asyncIterator](): AsyncIterator<Uint8Array> {
          yield chunkA
          yield chunkB
        },
        destroy() {},
      }
    }) as typeof originalCreateReadStream
    const authProvider = createPlatformTokenProvider({
      baseURL: 'http://api.invalid',
      jwtToken: 'jwt-token',
      projectId: 'project-1',
    })

    globalThis.fetch = jest.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url === 'http://api.invalid/auth/token') {
        return new Response(JSON.stringify({
          access_token: uploadCalls === 0 ? 'toolbox-token-1' : 'toolbox-token-2',
          expires_in: 3600,
        }), { status: 200, headers: { 'content-type': 'application/json' } })
      }
      if (url === 'http://toolbox.invalid/files/upload?path=%2Fremote%2Frefresh.txt') {
        const body = init?.body
        if (!(body instanceof ReadableStream)) throw new Error('expected ReadableStream upload body')
        attempts.push(await readStreamBytes(body))
        uploadCalls += 1
        return uploadCalls === 1
          ? new Response(JSON.stringify({ code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }), { status: 401, headers: { 'content-type': 'application/json' } })
          : new Response(null, { status: 200 })
      }
      throw new Error(`unexpected fetch ${url}`)
    }) as never

    try {
      const transport = new HttpTransport('http://toolbox.invalid', undefined, 50, { authProvider })
      const fs = new FileSystemService(transport)
      await fs.uploadFile(localPath, '/remote/refresh.txt', {
        onProgress: (bytes, total) => {
          progress.push([bytes, total])
        },
      })
    } finally {
      mutableNodeFs.createReadStream = originalCreateReadStream
      globalThis.fetch = originalFetch
      await rm(tempRoot, { recursive: true, force: true })
    }

    expect(uploadCalls).toBe(2)
    expect(attempts).toHaveLength(2)
    expect(createReadStreamCalls).toBe(2)
    expect(attempts[0]).toEqual(attempts[1])
    expect(new TextDecoder().decode(attempts[1])).toContain('streamed upload body')
    expect(progress).toEqual([
      [chunkA.byteLength, fileBytes.byteLength],
      [fileBytes.byteLength, fileBytes.byteLength],
    ])
  })

  it('retries one jwt 401 even when fetch checkStatus is false', async () => {
    const originalFetch = globalThis.fetch
    const authProvider = {
      authorizationHeader: jest.fn()
        .mockResolvedValueOnce('Bearer old-token')
        .mockResolvedValueOnce('Bearer new-token'),
    }
    const seenAuth: string[] = []
    let attempts = 0
    globalThis.fetch = jest.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      attempts += 1
      seenAuth.push(new Headers(init?.headers).get('authorization') ?? '')
      if (attempts === 1) {
        return new Response(JSON.stringify({ code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        })
      }
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    }) as never

    try {
      const response = await new HttpTransport('http://toolbox.invalid', undefined, 50, { authProvider }).fetch('/events', {}, false)
      expect(response.status).toBe(200)
    } finally {
      globalThis.fetch = originalFetch
    }

    expect(attempts).toBe(2)
    expect(seenAuth).toEqual(['Bearer old-token', 'Bearer new-token'])
  })

  it('surfaces a typed auth error instead of ConnectionError for a non-replayable stream without a body factory', async () => {
    const originalFetch = globalThis.fetch
    let uploadCalls = 0
    const authProvider = createPlatformTokenProvider({
      baseURL: 'http://api.invalid',
      jwtToken: 'jwt-token',
      projectId: 'project-1',
    })
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('single shot body'))
        controller.close()
      },
    })

    globalThis.fetch = jest.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url === 'http://api.invalid/auth/token') {
        return new Response(JSON.stringify({
          access_token: uploadCalls === 0 ? 'toolbox-token-1' : 'toolbox-token-2',
          expires_in: 3600,
        }), { status: 200, headers: { 'content-type': 'application/json' } })
      }
      if (url === 'http://toolbox.invalid/events') {
        const requestBody = init?.body
        if (!(requestBody instanceof ReadableStream)) throw new Error('expected ReadableStream body')
        uploadCalls += 1
        await readStreamBytes(requestBody)
        return new Response(JSON.stringify({ code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        })
      }
      throw new Error(`unexpected fetch ${url}`)
    }) as never

    try {
      const transport = new HttpTransport('http://toolbox.invalid', undefined, 50, { authProvider })
      const error = await transport.fetch('/events', { method: 'POST', body }).catch((caught: unknown) => caught)
      expect(error).toBeInstanceOf(AuthenticationError)
      expect(error).not.toBeInstanceOf(ConnectionError)
    } finally {
      globalThis.fetch = originalFetch
    }

    expect(uploadCalls).toBe(1)
  })

  it('detaches the caller abort relay when fetch setup fails before sending the request', async () => {
    const controller = new AbortController()
    const removeSpy = jest.spyOn(controller.signal, 'removeEventListener')
    const authError = new AuthenticationError('token exchange failed', { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' })
    const authProvider = {
      authorizationHeader: jest.fn().mockRejectedValue(authError),
    }

    await expect(new HttpTransport('http://toolbox/sbx', undefined, 50, { authProvider }).fetch('/events', { signal: controller.signal }))
      .rejects.toBe(authError)
    expect(removeSpy).toHaveBeenCalledWith('abort', expect.any(Function))
  })

  it('rethrows AuthenticationError unchanged when axios auth setup fails before dispatch', async () => {
    const authError = new AuthenticationError('token exchange failed', { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' })
    const authProvider = {
      authorizationHeader: jest.fn().mockRejectedValue(authError),
    }

    await expect(new HttpTransport('http://toolbox/sbx', undefined, 50, { authProvider }).get('/events'))
      .rejects.toBe(authError)
    expect(authProvider.authorizationHeader).toHaveBeenCalledTimes(1)
  })

  it('axios interceptor sends auth bearer when both auth and authProvider are set — authProvider never called', async () => {
    const auth = {
      bearer: jest.fn().mockResolvedValue('sbx-tok'),
      onUnauthorized: jest.fn().mockResolvedValue(undefined),
    }
    const authProvider = { authorizationHeader: jest.fn().mockResolvedValue('Bearer proj-tok') }
    const transport = new HttpTransport('http://toolbox/sbx', undefined, 5000, { auth, authProvider })

    const wireAuthorizations: Array<string | undefined> = []
    transport.http.defaults.adapter = async (config) => {
      wireAuthorizations.push(String((config.headers as Record<string, unknown>).Authorization ?? ''))
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    }

    await transport.get('/path')
    await transport.post('/path', {})

    expect(wireAuthorizations).toEqual(['Bearer sbx-tok', 'Bearer sbx-tok'])
    expect(authProvider.authorizationHeader).not.toHaveBeenCalled()
  })

  it('keeps the request meta a caller set when it arms the deadline for a sandbox token', async () => {
    const auth = { bearer: jest.fn().mockResolvedValue('sbx-tok'), onUnauthorized: jest.fn().mockResolvedValue(undefined) }
    const transport = new HttpTransport('http://toolbox/sbx', undefined, 5000, { auth })
    const seen: unknown[] = []
    transport.http.defaults.adapter = async (config) => {
      seen.push((config as { daytona?: unknown }).daytona)
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    }

    await transport.get('/path', { daytona: { routeTemplate: '/custom/{id}', sandboxId: 'sbx', retryOnUnauthorized: false } } as never)

    expect(seen).toEqual([expect.objectContaining({
      routeTemplate: '/custom/{id}',
      sandboxId: 'sbx',
      retryOnUnauthorized: false,
      deadlineAt: expect.any(Number),
    })])
  })

  it('fetch keeps a caller Authorization header when the SDK auth source yields no bearer', async () => {
    const auth = { bearer: jest.fn().mockResolvedValue(undefined), onUnauthorized: jest.fn().mockResolvedValue(undefined) }
    const seen: Array<string | null> = []
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (_input: string | URL | Request, init?: RequestInit) => {
      seen.push(new Headers(init?.headers).get('authorization'))
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    })
    try {
      const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', undefined, 5000, { auth })
      await transport.fetch('/version', { headers: { Authorization: 'Bearer caller-tok' } })
      auth.bearer.mockResolvedValue('sbx-tok')
      await transport.fetch('/version', { headers: { authorization: 'Bearer caller-tok' } })
      expect(seen).toEqual(['Bearer caller-tok', 'Bearer sbx-tok'])
    } finally {
      fetch.mockRestore()
    }
  })

  it('fetch retries a persistent 401 at most once when both auth and authProvider are set', async () => {
    let minted = 0
    const auth = {
      bearer: jest.fn().mockResolvedValue('sbx-tok'),
      onUnauthorized: jest.fn(async () => `fresh-${++minted}`),
    }
    const authProvider = { authorizationHeader: jest.fn().mockResolvedValue('Bearer proj-tok') }
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{"message":"unauthorized"}', {
      status: 401,
      headers: { 'content-type': 'application/json' },
    }))
    try {
      const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', undefined, 5000, { auth, authProvider })
      await expect(transport.fetch('/version')).rejects.toBeInstanceOf(DaytonaError)
      expect(fetch).toHaveBeenCalledTimes(2)
      expect(auth.onUnauthorized).toHaveBeenCalledTimes(1)
    } finally {
      fetch.mockRestore()
    }
  })

  it('axios interceptor 401 retry goes through auth.onUnauthorized — authProvider never invoked for the retry', async () => {
    const auth = {
      bearer: jest.fn().mockResolvedValue('sbx-tok'),
      onUnauthorized: jest.fn().mockResolvedValue('fresh-sbx-tok'),
    }
    const authProvider = { authorizationHeader: jest.fn().mockResolvedValue('Bearer proj-tok') }
    const transport = new HttpTransport('http://toolbox/sbx', undefined, 5000, { auth, authProvider })

    const wireAuthorizations: Array<string | undefined> = []
    let callCount = 0
    transport.http.defaults.adapter = async (config) => {
      callCount += 1
      wireAuthorizations.push(String((config.headers as Record<string, unknown>).Authorization ?? ''))
      if (callCount === 1) {
        const response = { data: { code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }, status: 401, statusText: 'Unauthorized', headers: {}, config }
        throw new AxiosError('Request failed with status code 401', 'ERR_BAD_REQUEST', config, null, response)
      }
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    }

    await transport.get('/path')

    expect(wireAuthorizations).toEqual(['Bearer sbx-tok', 'Bearer fresh-sbx-tok'])
    expect(auth.onUnauthorized).toHaveBeenCalledWith('sbx-tok')
    expect(authProvider.authorizationHeader).not.toHaveBeenCalled()
  })

  it('axios interceptor applies authProvider when auth is absent — API transport case', async () => {
    const authProvider = { authorizationHeader: jest.fn().mockResolvedValue('Bearer proj-tok') }
    const transport = new HttpTransport('http://api.invalid', undefined, 5000, { authProvider })

    const wireAuthorizations: Array<string | undefined> = []
    transport.http.defaults.adapter = async (config) => {
      wireAuthorizations.push(String((config.headers as Record<string, unknown>).Authorization ?? ''))
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    }

    await transport.get('/path')

    expect(wireAuthorizations).toEqual(['Bearer proj-tok'])
    expect(authProvider.authorizationHeader).toHaveBeenCalledTimes(1)
  })

  it('rejects absolute request URLs and still allows relative transport paths', async () => {
    const transport = new HttpTransport('http://toolbox/sbx', 'key')
    const bytes = new Uint8Array([1, 2, 3]).buffer
    const get = jest.spyOn(transport.http, 'get')
      .mockResolvedValueOnce({ data: { ok: true } } as never)
      .mockResolvedValueOnce({ data: bytes } as never)

    await expect(transport.get('/ok')).resolves.toEqual({ ok: true })
    await expect(transport.getBytes('/bytes')).resolves.toBe(bytes)
    await expect(transport.get('https://evil.invalid/steal')).rejects.toThrow('relative SDK path')
    await expect(transport.getBytes('https://evil.invalid/steal')).rejects.toThrow('relative SDK path')
    await expect(transport.fetch('//evil.invalid/steal')).rejects.toThrow('relative SDK path')

    expect(get).toHaveBeenCalledTimes(2)
    expect(get).toHaveBeenNthCalledWith(1, '/ok', undefined)
    expect(get).toHaveBeenNthCalledWith(2, '/bytes', { responseType: 'arraybuffer', timeout: 0, daytona: { transfer: true } })
  })

  it('omits PTY shellCommand for default-shell PTYs', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p' })
    await new PtyService(new ProcessService(transport.object, 'sbx')).start()
    expect(transport.post.mock.calls[0]?.[1]).toMatchObject({ kind: 'pty' })
    expect(transport.post.mock.calls[0]?.[1]).not.toHaveProperty('shellCommand')
  })

  it('omits timeoutMs from code process creation', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p' })
    await startProcess(transport.object, 'sbx', { code: 'print(1)', kind: 'code', language: 'python', executionTimeout: 100 })
    expect(transport.post.mock.calls[0]?.[1]).not.toHaveProperty('timeoutMs')
  })

  it('collects every log page and includes PTY frames in stdout', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [{ cursor: 'c1', channel: 'stdout', data: 'a' }], nextCursor: 'c1' })
      .mockResolvedValueOnce({ frames: [{ cursor: 'c2', channel: 'stderr', data: 'b' }, { cursor: 'c3', channel: 'stdout', data: 'c' }], nextCursor: 'c3' })
      .mockResolvedValueOnce({ frames: [], nextCursor: 'c2' })
    const result = await new ProcessService(transport.object, 'sbx').run('echo hi')
    expect(result.stdout).toBe('ac')
    expect(result.stderr).toBe('b')
    expect(result.output).toBe('abc')
  })

  it('keeps waiting across toolbox long-poll caps when no timeout is requested', async () => {
    const transport = transportStub()
    transport.get
      .mockResolvedValueOnce({ reason: 'timed_out' })
      .mockResolvedValueOnce({ id: 'p', kind: 'code', state: 'running', createdAt: '2026-01-01T00:00:00Z', cwd: '/w', stdin: 'closed', keepLogs: 'until_cleanup', truncatedHead: false })
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
    await expect(new ProcessService(transport.object, 'sbx').connect('p').wait()).resolves.toEqual({ reason: 'exited', rawReason: 'exited', exitCode: 0 })
    expect(transport.get.mock.calls.map((call) => call[0])).toEqual(['/processes/p/wait?timeoutMs=0', '/processes/p', '/processes/p/wait?timeoutMs=0'])
  })

  it('raises after repeated record-read failures even when the long-poll succeeds', async () => {
    const transport = transportStub()
    for (let i = 0; i <= WAIT_TRANSPORT_RETRY_LIMIT; i++) {
      transport.get.mockResolvedValueOnce({ reason: 'timed_out' }).mockRejectedValueOnce(new ConnectionError('reset'))
    }
    await expect(new ProcessService(transport.object, 'sbx').connect('p').wait()).rejects.toBeInstanceOf(ConnectionError)
    expect(transport.get).toHaveBeenCalledTimes(2 * (WAIT_TRANSPORT_RETRY_LIMIT + 1))
  })

  it('retries a transient record-read failure', async () => {
    const transport = transportStub()
    transport.get
      .mockResolvedValueOnce({ reason: 'timed_out' })
      .mockRejectedValueOnce(new ConnectionError('reset'))
      .mockResolvedValueOnce({ reason: 'timed_out' })
      .mockResolvedValueOnce({ id: 'p', kind: 'exec', sessionId: 's', state: 'terminal', reason: 'timed_out', createdAt: '2026-01-01T00:00:00Z', cwd: '/w', stdin: 'closed', keepLogs: 'until_cleanup', truncatedHead: false })
    await expect(new ProcessService(transport.object, 'sbx').connect('p').wait()).resolves.toMatchObject({ reason: 'timed_out' })
  })

  it('returns a command its own deadline stopped instead of re-polling forever', async () => {
    // `/wait` answers `timed_out` for a settled timed-out session command too.
    const transport = transportStub()
    transport.get
      .mockResolvedValueOnce({ reason: 'timed_out' })
      .mockResolvedValueOnce({ id: 'p', kind: 'exec', sessionId: 'shell-1', state: 'terminal', reason: 'timed_out', createdAt: '2026-01-01T00:00:00Z', cwd: '/w', stdin: 'closed', keepLogs: 'until_cleanup', truncatedHead: false })
    await expect(new ProcessService(transport.object, 'sbx').connect('p').wait()).resolves.toEqual({ reason: 'timed_out', rawReason: 'timed_out' })
    expect(transport.get).toHaveBeenCalledTimes(2)
  })

  it('connect() drops a persisted kind this SDK release does not list, like Python from_json()', () => {
    const service = new ProcessService(transportStub().object, 'sbx')
    const unlisted = JSON.parse('{"sandboxId":"sbx","processId":"p","kind":"sidecar"}') as ProcessHandleJSON
    expect(service.connect(unlisted).kind).toBeUndefined()
    expect(service.connect({ sandboxId: 'sbx', processId: 'p', kind: 'pty' }).kind).toBe('pty')
  })

  it('treats a null wait reason like an absent reason', async () => {
    const transport = transportStub()
    transport.get.mockResolvedValueOnce({ reason: null, exitCode: null, signal: null })
    await expect(new ProcessService(transport.object, 'sbx').connect('p').wait()).resolves.toEqual({
      reason: 'failed',
      exitCode: undefined,
      signal: undefined,
    })
  })

  it('reads a null process reason as absent, like the Python SDK', async () => {
    const transport = transportStub()
    transport.get.mockResolvedValueOnce({ id: 'p', kind: 'exec', state: 'running', reason: null, stdin: null, keepLogs: null })
    const info = await new ProcessService(transport.object, 'sbx').get('p')
    expect(info.reason).toBeUndefined()
    expect(info.rawReason).toBeUndefined()
    expect(info.stdin).toBeUndefined()
    expect(info.rawStdin).toBeUndefined()
    expect(info.keepLogs).toBeUndefined()
    expect(info.rawKeepLogs).toBeUndefined()
  })

  it('drops malformed wait fields instead of leaking or throwing on them', async () => {
    const transport = transportStub()
    transport.get.mockResolvedValueOnce({ reason: 'lost', exitCode: 'x', signal: 9 })
    await expect(new ProcessService(transport.object, 'sbx').connect('p').wait()).resolves.toEqual({
      reason: 'lost',
      rawReason: 'lost',
      exitCode: undefined,
      signal: undefined,
    })
  })

  it('keeps file delete non-recursive and applies upload mode with chmod', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    await fs.remove('/tmp/dir')
    expect(transport.delete.mock.calls[0]?.[1]).toMatchObject({ params: { path: '/tmp/dir' } })
    expect(transport.delete.mock.calls[0]?.[1].params).not.toMatchObject({ recursive: true })
    await fs.writeText('/tmp/file', 'x', { mode: '0644' })
    expect(transport.post).toHaveBeenCalledWith('/files/upload', expect.any(FormData), { params: { path: '/tmp/file' }, timeout: 0, daytona: { transfer: true } })
    expect(transport.post).toHaveBeenCalledWith('/files/permissions', undefined, { params: { path: '/tmp/file', mode: '0644', owner: undefined, group: undefined } })
  })

  // `POST …/files/sign` answers; the SDK composes its own URL and reads only
  // `signature` / `expires_at` — `url`, `method` and `path` are echoes it ignores.
  function signResponse(expiresAt = '2026-06-12T03:22:47Z', signature = SIGNED_FILE_TEST_SIGNATURE) {
    return { data: { url: 'https://server.example/sandbox-api/ignored/files/download?path=ignored', method: 'GET', path: '/ignored', expires_at: expiresAt, signature } }
  }

  function presignedSandbox(options: { sandboxClass?: 'linux' | 'macos'; id?: string; toolboxUrl?: string } = {}) {
    const client = new Daytona({ apiKey: 'key' })
    const id = options.id ?? 'sbx'
    const sandbox = lazySandbox(client, id, { toolboxUrl: options.toolboxUrl ?? `http://toolbox.invalid/sandbox-api/${id}` })
    sandbox.sandboxClass = options.sandboxClass ?? 'linux'
    const api = clientInstance(client)
    return { sandbox, get: jest.spyOn(api, 'get'), post: jest.spyOn(api, 'post'), now: jest.spyOn(Date, 'now').mockReturnValue(1_781_230_967_750) }
  }

  it('posts to /sandboxes/{sandbox_id}/files/sign and composes the download URL from the SDK toolbox base', async () => {
    const { sandbox, post, now } = presignedSandbox()
    post.mockResolvedValueOnce(signResponse())
    try {
      // The link lives on the handle's toolbox base, never on the server's `url` echo.
      await expect(sandbox.fs.presignedDownloadUrl('/home/user/report.pdf', { expiresInSeconds: 3600 })).resolves.toBe(
        `http://toolbox.invalid/sandbox-api/sbx/files/download?path=%2Fhome%2Fuser%2Freport.pdf&expires=1781234567&signature=${SIGNED_FILE_TEST_SIGNATURE}`,
      )
      // expires_at = now + expiresInSeconds; the path travels raw in the body.
      expect(post).toHaveBeenCalledTimes(1)
      expect(post).toHaveBeenCalledWith('/sandboxes/sbx/files/sign', { method: 'GET', path: '/home/user/report.pdf', expires_at: '2026-06-12T03:22:47.000Z' })
    } finally {
      now.mockRestore()
    }
  })

  it('signs upload URLs with method POST and the upload route, defaulting to one hour', async () => {
    const { sandbox, post, now } = presignedSandbox()
    post.mockResolvedValueOnce(signResponse())
    try {
      await expect(sandbox.fs.presignedUploadUrl('/tmp/incoming/data.bin')).resolves.toBe(
        `http://toolbox.invalid/sandbox-api/sbx/files/upload?path=%2Ftmp%2Fincoming%2Fdata.bin&expires=1781234567&signature=${SIGNED_FILE_TEST_SIGNATURE}`,
      )
      expect(post).toHaveBeenCalledWith('/sandboxes/sbx/files/sign', { method: 'POST', path: '/tmp/incoming/data.bin', expires_at: '2026-06-12T03:22:47.000Z' })
    } finally {
      now.mockRestore()
    }
  })

  it('keeps TypeScript path encoding byte-identical to Python quote(path, safe="") and sends the raw path to the signer', async () => {
    const { sandbox, post, now } = presignedSandbox()
    post.mockResolvedValue(signResponse())
    const vectors = [
      ['/tmp/a b.txt', '%2Ftmp%2Fa%20b.txt'],
      ['/tmp/ü.txt', '%2Ftmp%2F%C3%BC.txt'],
      ['/tmp/a+b.txt', '%2Ftmp%2Fa%2Bb.txt'],
      ['/tmp/~tilde.txt', '%2Ftmp%2F~tilde.txt'],
      ["/tmp/!'()*.txt", '%2Ftmp%2F%21%27%28%29%2A.txt'],
      ["/tmp/it's (a) file!*.txt", '%2Ftmp%2Fit%27s%20%28a%29%20file%21%2A.txt'],
      ['/tmp/a/b.txt', '%2Ftmp%2Fa%2Fb.txt'],
      ['/tmp/a:b.txt', '%2Ftmp%2Fa%3Ab.txt'],
      ['/tmp/a&b.txt', '%2Ftmp%2Fa%26b.txt'],
      ['/tmp/a%b.txt', '%2Ftmp%2Fa%25b.txt'],
    ] as const
    try {
      for (const [path, expected] of vectors) {
        const url = await sandbox.fs.presignedDownloadUrl(path)
        expect(url).toBe(`http://toolbox.invalid/sandbox-api/sbx/files/download?path=${expected}&expires=1781234567&signature=${SIGNED_FILE_TEST_SIGNATURE}`)
        expect(post).toHaveBeenLastCalledWith('/sandboxes/sbx/files/sign', { method: 'GET', path, expires_at: '2026-06-12T03:22:47.000Z' })
      }
    } finally {
      now.mockRestore()
    }
  })

  it('echoes the server expires and signature into the URL', async () => {
    // The signature covers the server's `expires`; a link must carry the value
    // that was signed, not the one the SDK asked for.
    const { sandbox, post, now } = presignedSandbox()
    post.mockResolvedValueOnce(signResponse('2026-06-12T03:22:50Z', 'v1_other'))
    try {
      await expect(sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')).resolves.toBe(
        'http://toolbox.invalid/sandbox-api/sbx/files/download?path=%2Ftmp%2Fdemo.txt&expires=1781234570&signature=v1_other',
      )
    } finally {
      now.mockRestore()
    }
  })

  it('mints every presigned URL with its own request and keeps no client-side cache', async () => {
    const { sandbox, get, post, now } = presignedSandbox()
    post.mockResolvedValue(signResponse())
    try {
      await sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')
      await sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')
      await sandbox.fs.presignedUploadUrl('/tmp/demo.txt')
      expect(post).toHaveBeenCalledTimes(3)
      expect(post.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/sbx/files/sign', '/sandboxes/sbx/files/sign', '/sandboxes/sbx/files/sign'])
      expect(get).not.toHaveBeenCalled()
    } finally {
      now.mockRestore()
    }
  })

  it('signs macOS sandbox file URLs', async () => {
    const { sandbox, post, now } = presignedSandbox({ sandboxClass: 'macos', id: 'mac' })
    post.mockResolvedValueOnce(signResponse())
    try {
      await expect(sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')).resolves.toContain('http://toolbox.invalid/sandbox-api/mac/files/download?path=%2Ftmp%2Fdemo.txt')
      expect(post).toHaveBeenCalledWith('/sandboxes/mac/files/sign', { method: 'GET', path: '/tmp/demo.txt', expires_at: '2026-06-12T03:22:47.000Z' })
    } finally {
      now.mockRestore()
    }
  })

  it.each([0, -1, 604_801, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1])(
    'rejects an out-of-range presigned URL expiry (%p) before any request',
    async (expiresInSeconds) => {
      expect(publicSdk.SIGNED_FILE_MAX_TTL_SECONDS).toBe(604_800)
      const { sandbox, get, post, now } = presignedSandbox()
      try {
        for (const mint of [sandbox.fs.presignedDownloadUrl.bind(sandbox.fs), sandbox.fs.presignedUploadUrl.bind(sandbox.fs)]) {
          const pending = mint('/tmp/demo.txt', { expiresInSeconds })
          await expect(pending).rejects.toBeInstanceOf(publicSdk.BadRequestError)
          await expect(pending).rejects.toMatchObject({
            message: 'expiresInSeconds must be an integer between 1 and 604800 (7 days)',
            statusCode: 400,
            code: 'BAD_REQUEST',
            source: 'SDK',
          })
        }
        expect(get).not.toHaveBeenCalled()
        expect(post).not.toHaveBeenCalled()
      } finally {
        now.mockRestore()
      }
    },
  )

  it.each([1, 604_800])('accepts the presigned URL TTL boundary %p', async (expiresInSeconds) => {
    const { sandbox, post, now } = presignedSandbox()
    post.mockResolvedValueOnce(signResponse())
    try {
      await sandbox.fs.presignedDownloadUrl('/tmp/demo.txt', { expiresInSeconds })
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/files/sign', { method: 'GET', path: '/tmp/demo.txt', expires_at: new Date((1_781_230_967 + expiresInSeconds) * 1000).toISOString() })
    } finally {
      now.mockRestore()
    }
  })

  it('surfaces the server 400 and 403 from files/sign as the typed errors', async () => {
    const { sandbox, post, now } = presignedSandbox()
    // What the axios interceptor produces for each status; the SDK must pass it through unchanged.
    post
      .mockRejectedValueOnce(createDaytonaError('expires is too far in the future', 400, 'BAD_REQUEST', 'DAYTONA_API'))
      .mockRejectedValueOnce(createDaytonaError('missing permission write:sandboxes', 403, 'FORBIDDEN', 'DAYTONA_API'))
    try {
      const badRequest = sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')
      await expect(badRequest).rejects.toBeInstanceOf(publicSdk.BadRequestError)
      await expect(badRequest).rejects.toMatchObject({ message: 'expires is too far in the future', statusCode: 400 })
      const forbidden = sandbox.fs.presignedUploadUrl('/tmp/demo.txt')
      await expect(forbidden).rejects.toBeInstanceOf(publicSdk.ForbiddenError)
      await expect(forbidden).rejects.toMatchObject({ message: 'missing permission write:sandboxes', statusCode: 403 })
      expect(post).toHaveBeenCalledTimes(2)
    } finally {
      now.mockRestore()
    }
  })

  // Parity matrix with the Python suite (`test_presigned_urls_reject_a_malformed_sign_payload`):
  // a body that breaks the contract is BadRequestError naming the field, with the same messages.
  it.each([
    [null, 'signed file URL response is not a JSON object'],
    [[], 'signed file URL response is not a JSON object'],
    ['str', 'signed file URL response is not a JSON object'],
    [{}, 'signed file URL response signature must be a non-empty string'],
    [{ signature: '', expires_at: '2026-06-12T03:22:47Z' }, 'signed file URL response signature must be a non-empty string'],
    [{ signature: 7, expires_at: '2026-06-12T03:22:47Z' }, 'signed file URL response signature must be a non-empty string'],
    [{ signature: 'v1_ok' }, 'signed file URL response expires_at must be an RFC 3339 timestamp'],
    [{ signature: 'v1_ok', expires_at: 1_781_234_567 }, 'signed file URL response expires_at must be an RFC 3339 timestamp'],
    [{ signature: 'v1_ok', expires_at: 'invalid' }, 'signed file URL response expires_at must be an RFC 3339 timestamp'],
    [{ signature: 'v1_ok', expires_at: true }, 'signed file URL response expires_at must be an RFC 3339 timestamp'],
  ])('reports a malformed files/sign payload (%p) as a BadRequestError naming the field', async (data, message) => {
    const { sandbox, post, now } = presignedSandbox()
    // JSON `null` is a valid axios body; it must surface as the SDK's error, not a TypeError.
    post.mockResolvedValueOnce({ data } as never)
    try {
      const pending = sandbox.fs.presignedDownloadUrl('/f.txt')
      await expect(pending).rejects.toMatchObject({ constructor: BadRequestError, message, statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    } finally {
      now.mockRestore()
    }
  })

  // `POST …/signing-key/rotate` answers 204 with no body.
  // The key never reaches the SDK, so the method returns void.
  it('rotates the signing key through POST /sandboxes/{sandbox_id}/signing-key/rotate with no body and resolves void', async () => {
    const { sandbox, post, now } = presignedSandbox()
    post.mockResolvedValueOnce({ status: 204, data: undefined } as never)
    try {
      await expect(sandbox.rotateSigningKey()).resolves.toBeUndefined()
      expect(post).toHaveBeenCalledTimes(1)
      expect(post).toHaveBeenCalledWith('/sandboxes/sbx/signing-key/rotate')
      expect(post.mock.calls[0]).toHaveLength(1)
    } finally {
      now.mockRestore()
    }
  })

  it('rotates macOS signing keys', async () => {
    const { sandbox, post, now } = presignedSandbox({ sandboxClass: 'macos', id: 'mac' })
    post.mockResolvedValueOnce({ status: 204, data: undefined } as never)
    try {
      await sandbox.rotateSigningKey()
      expect(post).toHaveBeenCalledWith('/sandboxes/mac/signing-key/rotate')
    } finally {
      now.mockRestore()
    }
  })

  it('accepts the 204 empty rotation response', async () => {
    const { sandbox, post, now } = presignedSandbox()
    post.mockResolvedValueOnce({ status: 204, data: undefined } as never)
    try {
      await expect(sandbox.rotateSigningKey()).resolves.toBeUndefined()
    } finally {
      now.mockRestore()
    }
  })

  it('surfaces the server 403 and 404 from signing-key/rotate as the typed errors', async () => {
    const { sandbox, post, now } = presignedSandbox()
    post
      .mockRejectedValueOnce(createDaytonaError('missing permission write:sandboxes', 403, 'FORBIDDEN', 'DAYTONA_API'))
      .mockRejectedValueOnce(createDaytonaError('sandbox not found', 404, 'NOT_FOUND', 'DAYTONA_API'))
    try {
      const forbidden = sandbox.rotateSigningKey()
      await expect(forbidden).rejects.toBeInstanceOf(publicSdk.ForbiddenError)
      await expect(forbidden).rejects.toMatchObject({ message: 'missing permission write:sandboxes', statusCode: 403 })
      const notFound = sandbox.rotateSigningKey()
      await expect(notFound).rejects.toBeInstanceOf(publicSdk.NotFoundError)
      await expect(notFound).rejects.toMatchObject({ message: 'sandbox not found', statusCode: 404 })
      expect(post).toHaveBeenCalledTimes(2)
    } finally {
      now.mockRestore()
    }
  })

  it('treats a create-response toolboxProxyUrl as proxied regardless of its shape', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, { sandboxId: 'sbx', toolboxProxyUrl: 'http://toolbox.invalid/sbx' })
    sandbox.sandboxClass = 'linux'
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValueOnce(signResponse())
    const now = jest.spyOn(Date, 'now').mockReturnValue(1_781_230_967_000)
    try {
      await expect(sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')).resolves.toBe(
        `http://toolbox.invalid/sbx/files/download?path=%2Ftmp%2Fdemo.txt&expires=1781234567&signature=${SIGNED_FILE_TEST_SIGNATURE}`,
      )
      expect(post).toHaveBeenCalledWith('/sandboxes/sbx/files/sign', { method: 'GET', path: '/tmp/demo.txt', expires_at: '2026-06-12T03:22:47.000Z' })
    } finally {
      now.mockRestore()
    }
  })

  it('rejects signing requests when the explicit toolbox override cannot support them', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: 'http://toolbox.invalid' })
    const api = clientInstance(client)
    const get = jest.spyOn(api, 'get')
    const post = jest.spyOn(api, 'post')

    await expect(sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')).rejects.toMatchObject({ statusCode: 400, code: 'UNSUPPORTED_OPERATION' })
    await expect(sandbox.fs.presignedUploadUrl('/tmp/demo.txt')).rejects.toMatchObject({ statusCode: 400, code: 'UNSUPPORTED_OPERATION' })
    await expect(sandbox.rotateSigningKey()).rejects.toMatchObject({ statusCode: 400, code: 'UNSUPPORTED_OPERATION' })

    expect(get).not.toHaveBeenCalled()
    expect(post).not.toHaveBeenCalled()
  })

  it('accepts a sandbox-id suffix beneath an arbitrary URL prefix for presigned URLs', async () => {
    const { sandbox, post, now } = presignedSandbox({ toolboxUrl: 'http://127.0.0.1:9000/sandbox-api/sbx' })
    post.mockResolvedValueOnce(signResponse())
    try {
      await expect(sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')).resolves.toBe(
        `http://127.0.0.1:9000/sandbox-api/sbx/files/download?path=%2Ftmp%2Fdemo.txt&expires=1781234567&signature=${SIGNED_FILE_TEST_SIGNATURE}`,
      )
      expect(post).toHaveBeenCalledWith('/sandboxes/sbx/files/sign', { method: 'GET', path: '/tmp/demo.txt', expires_at: '2026-06-12T03:22:47.000Z' })
    } finally {
      now.mockRestore()
    }
  })

  // Both spellings are documented in README.md; a template copied from a
  // Python integration must work here too.
  it.each(['{sandboxId}', '{sandbox_id}'])('allows presigned URLs from a per-sandbox proxy-scoped toolbox template override (%s)', async (placeholder) => {
    const { sandbox, post, now } = presignedSandbox({ toolboxUrl: `https://tb.example.com/sandbox-api/${placeholder}` })
    post.mockResolvedValueOnce(signResponse())
    try {
      expect(sandbox.toolboxProxyUrl).toBe('https://tb.example.com/sandbox-api/sbx')
      await expect(sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')).resolves.toBe(
        `https://tb.example.com/sandbox-api/sbx/files/download?path=%2Ftmp%2Fdemo.txt&expires=1781234567&signature=${SIGNED_FILE_TEST_SIGNATURE}`,
      )
      expect(post).toHaveBeenCalledWith('/sandboxes/sbx/files/sign', { method: 'GET', path: '/tmp/demo.txt', expires_at: '2026-06-12T03:22:47.000Z' })
    } finally {
      now.mockRestore()
    }
  })

  it('classifies a schemeless proxy-scoped override instead of throwing a TypeError', async () => {
    // Parity with Python's urlsplit: `host/sandbox-api/<id>` is sandbox-scoped; WHATWG
    // URL cannot parse it and must not leak a raw TypeError from fork() or minting.
    const { sandbox, post, now } = presignedSandbox({ id: 'abc', toolboxUrl: 'proxy.example/sandbox-api/abc' })
    post.mockResolvedValueOnce(signResponse())
    try {
      await expect(sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')).resolves.toBe(
        `proxy.example/sandbox-api/abc/files/download?path=%2Ftmp%2Fdemo.txt&expires=1781234567&signature=${SIGNED_FILE_TEST_SIGNATURE}`,
      )
    } finally {
      now.mockRestore()
    }
  })

  it.each([
    ['seg A/B', 'seg%20A%2FB'],
    // !'()* are the characters encodeURIComponent leaves bare and Python's
    // quote(safe='') encodes; both SDKs must agree byte-for-byte.
    ["it's (a) file!*", 'it%27s%20%28a%29%20file%21%2A'],
  ])('percent-encodes the sandbox id into a toolbox template like Python (%s)', (sandboxId, encoded) => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, sandboxId, { toolboxUrl: 'https://tb.example.com/sandbox-api/{sandboxId}' })
    expect(sandbox.toolboxProxyUrl).toBe(`https://tb.example.com/sandbox-api/${encoded}`)
  })

  it.each([
    'https://{sandboxId}.example.com/sandbox-api',
    'https://tb.example.com:{sandbox_id}/sandbox-api',
    'https://tb.example.com/sandbox-api?id={sandboxId}',
    'https://tb.example.com/sandbox-api#{sandbox_id}',
    // A '/' inside the query must not be mistaken for the path start.
    'https://tb.example.com?x=/sandbox-api/{sandboxId}',
    'https://tb.example.com#/sandbox-api/{sandbox_id}',
  ])('rejects a toolbox template with the placeholder outside the path (%s)', (template) => {
    // The placeholder is a path segment; anywhere else (host, port, query,
    // fragment) is a shape no proxy serves and the docs never promised.
    const client = new Daytona({ apiKey: 'key' })
    expect(() => lazySandbox(client, 'sbx', { toolboxUrl: template })).toThrow('only supported in the path')
  })

  it('takes fork metadata from its detail and its toolbox from the fork response', async () => {
    const originalFetch = globalThis.fetch
    const seen: string[] = []
    globalThis.fetch = jest.fn(async (input: string | URL | Request) => {
      seen.push(String(input))
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    }) as never

    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: 'http://10.0.0.7:2280' })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    jest.spyOn(api, 'post').mockResolvedValueOnce({ data: { id: 'forked', toolbox_proxy_url: 'https://proxy.example/sandbox-api' } })
    const get = jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'forked', name: 'the-fork', status: 'running' } })

    try {
      const forked = await sandbox.fork()
      expect(toolboxInstance(forked).getUri({ url: '/version' })).toBe('https://proxy.example/sandbox-api/forked/version')
      expect(forked.name).toBe('the-fork')
      expect(get).toHaveBeenCalledTimes(1)
      expect(seen).toEqual([])
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('does not give a fork the parent explicit toolbox override', async () => {
    // Inheriting the parent's URL would send fork requests to the wrong sandbox.
    // The fork instead uses the toolbox URL returned by the API.
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: 'http://10.0.0.7:2280' })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockResolvedValueOnce({ data: { id: 'forked', toolbox_proxy_url: 'https://proxy.example' } })
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'forked', status: 'pending' } })

    const forked = await sandbox.fork()

    expect(forked.toolboxProxyUrl).toBe('https://proxy.example/forked')
    expect(forked.toolboxProxyUrl).not.toBe(sandbox.toolboxProxyUrl)
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/fork', {})
  })

  it('sends memory on fork only when given', async () => {
    // memory=false asks for a disk-only fork; an omitted
    // option leaves the server default (a memory fork) in charge.
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx')
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { id: 'a' } })
      .mockResolvedValueOnce({ data: { id: 'b' } })
    jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { id: 'a', status: 'stopped' } })
      .mockResolvedValueOnce({ data: { id: 'b', status: 'paused' } })

    expect((await sandbox.fork({ name: 'disk', memory: false })).id).toBe('a')
    expect((await sandbox.fork()).id).toBe('b')
    expect(post).toHaveBeenNthCalledWith(1, '/sandboxes/sbx/fork', { name: 'disk', memory: false })
    expect(post).toHaveBeenNthCalledWith(2, '/sandboxes/sbx/fork', {})
  })

  it('fork({ timeout }) waits for the state the fork settles in, by source state', async () => {
    jest.useRealTimers()
    // A paused or stopped source's fork rests; waiting for `started` would
    // time out on a fork that was created and is usable.
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx')
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    jest.spyOn(api, 'post').mockResolvedValue({ data: { id: 'f' } })
    const get = jest.spyOn(api, 'get')
    // source refresh (stopped), fork DTO, then the fork's state polls
    get.mockResolvedValueOnce({ data: { id: 'sbx', status: 'stopped', class: 'linux' } })
      .mockResolvedValueOnce({ data: { id: 'f', status: 'pending' } })
      .mockResolvedValue({ data: { id: 'f', status: 'stopped' } })
    const rested = await sandbox.fork({ memory: false, timeout: 5_000 })
    expect(rested.state).toBe('stopped')

    get.mockReset()
    get.mockResolvedValueOnce({ data: { id: 'sbx', status: 'running', class: 'linux' } })
      .mockResolvedValueOnce({ data: { id: 'f', status: 'pending' } })
      .mockResolvedValue({ data: { id: 'f', status: 'running' } })
    const running = await sandbox.fork({ timeout: 5_000 })
    expect(running.state).toBe('started')
  })

  it('macOS fork({ timeout }) settles on started or stopped whatever the source showed before the POST', async () => {
    jest.useRealTimers()
    // The source may stop after a previous read; the fork's returned state
    // determines whether the wait is complete.
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx')
    sandbox.sandboxClass = 'macos'
    const api = clientInstance(client)
    jest.spyOn(api, 'post').mockResolvedValue({ data: { id: 'f' } })
    const get = jest.spyOn(api, 'get')
    get.mockResolvedValueOnce({ data: { id: 'f', status: 'pending' } })
      .mockResolvedValue({ data: { id: 'f', status: 'stopped' } })
    expect((await sandbox.fork({ timeout: 5_000 })).state).toBe('stopped')
    expect(get).not.toHaveBeenCalledWith('/sandboxes/sbx')

    get.mockReset()
    get.mockResolvedValueOnce({ data: { id: 'f', status: 'pending' } })
      .mockResolvedValue({ data: { id: 'f', status: 'running' } })
    expect((await sandbox.fork({ timeout: 5_000 })).state).toBe('started')
  })

  it('gives a fork no toolbox rather than the parent URL when the response carries none', async () => {
    // With no response URL or client base, toolbox calls must fail rather than
    // silently target the parent's sandbox.
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: 'http://10.0.0.7:2280' })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    jest.spyOn(api, 'post').mockResolvedValueOnce({ data: { id: 'forked' } })
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'forked', status: 'pending' } })

    const forked = await sandbox.fork()

    expect(forked.toolboxProxyUrl).toBeUndefined()
    expect(() => forked.fs).toThrow('sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolboxUrl)')
  })

  it('mints a fresh credential for forked toolbox requests instead of inheriting the parent per-sandbox token', async () => {
    const originalFetch = globalThis.fetch
    const seen: RequestInit[] = []
    globalThis.fetch = jest.fn((_url: string | URL | Request, init?: RequestInit) => {
      seen.push(init ?? {})
      return Promise.resolve(new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }))
    }) as never

    const client = new Daytona({
      apiKey: 'project-key',
    })
    const sandbox = lazySandbox(client, 'sbx', { accessToken: 'parent-sandbox-token' })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'forked', status: 'running' } })
    jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { id: 'forked', toolbox_proxy_url: 'https://proxy.region-1.example.com' } })
      // The fork handle is key-configured (no caller token), so its first
      // toolbox call lazily mints its OWN sandbox token — never the parent's.
      .mockResolvedValueOnce({
        data: { access_token: 'forked-sandbox-jwt', token_type: 'Bearer', expires_at: '2027-01-01T00:00:00Z', operations: ['toolbox'], ports: null },
      })

    try {
      const forked = await sandbox.fork()
      const toolboxTransport = Reflect.get(forked, 'toolboxTransport') as HttpTransport

      await toolboxTransport.fetch('/version')

      expect(api.post).toHaveBeenLastCalledWith('/sandboxes/forked/access-token', {})
      expect(seen[0]?.headers).toMatchObject({ Authorization: 'Bearer forked-sandbox-jwt' })
      expect(seen[0]?.headers).not.toMatchObject({ Authorization: 'Bearer parent-sandbox-token' })
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('forwards JWT-backed httpOptions into forked toolbox requests', async () => {
    const originalFetch = globalThis.fetch
    const seen: RequestInit[] = []
    globalThis.fetch = jest.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input) === 'http://api.invalid/auth/token') {
        return Promise.resolve(new Response(JSON.stringify({ access_token: 'platform-1', expires_in: 300, token_type: 'Bearer' }), {
          headers: { 'content-type': 'application/json' },
        }))
      }
      seen.push(init ?? {})
      return Promise.resolve(new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }))
    }) as never

    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const sandbox = lazySandbox(client, 'sbx')
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    jest.spyOn(api, 'post').mockResolvedValueOnce({ data: { id: 'forked', toolbox_proxy_url: 'https://proxy.example/sandbox-api' } })
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'forked', status: 'running' } })

    try {
      const forked = await sandbox.fork()
      await (Reflect.get(forked, 'toolboxTransport') as HttpTransport).fetch('/version')
      expect(seen[0]?.headers).toMatchObject({ Authorization: 'Bearer platform-1' })
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('requires git commit identity before posting', async () => {
    const transport = transportStub()
    await expect(new GitService(transport.object).commit('/repo', 'msg', {} as never)).rejects.toThrow('author and email')
  })

  it('raises BadRequestError for invalid git timeouts', async () => {
    const transport = transportStub()
    const git = new GitService(transport.object)

    await expect(git.pull('/repo', { timeout: Number.NaN })).rejects.toBeInstanceOf(BadRequestError)
    await expect(git.pull('/repo', { timeout: Number.NaN })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
  })

  it('rejects invalid clone depth before posting', async () => {
    const transport = transportStub()
    const git = new GitService(transport.object)

    await expect(git.clone('https://example.invalid/repo.git', { path: '/repo', depth: 0 })).rejects.toBeInstanceOf(BadRequestError)
    expect(transport.post).not.toHaveBeenCalled()
  })

  it('shapes filesystem query params and list-compatible responses', async () => {
    const transport = transportStub()
    const fs = new FileSystemService(transport.object)
    transport.get
      .mockResolvedValueOnce(new TextEncoder().encode('hello').buffer)
      .mockResolvedValueOnce([{ name: 'a', path: '/tmp/a' }])
      .mockResolvedValueOnce({ name: 'a', path: '/tmp/a', isDir: false })
      .mockResolvedValueOnce({ items: [{ path: '/tmp/a' }], nextCursor: 'n' })
      .mockResolvedValueOnce([{ name: 'b', path: '/tmp/b' }])
      .mockResolvedValueOnce({ files: ['/tmp/a'] })
    transport.post.mockResolvedValueOnce(undefined).mockResolvedValueOnce(undefined).mockResolvedValueOnce([{ file: '/tmp/a', replacements: 1 }])

    await expect(fs.readText('/tmp/a')).resolves.toBe('hello')
    expect(transport.get.mock.calls[0]).toEqual(['/files/download', { params: { path: '/tmp/a' }, responseType: 'arraybuffer', timeout: 0, daytona: { transfer: true } }])
    await expect(fs.list('/tmp', { depth: 2 })).resolves.toEqual({ items: [{ name: 'a', path: '/tmp/a' }] })
    expect(transport.get.mock.calls[1]).toEqual(['/files', { params: { path: '/tmp', depth: 2 } }])
    await expect(fs.stat('/tmp/a')).resolves.toMatchObject({ path: '/tmp/a' })
    await fs.mkdir('/tmp/new', { mode: '0700' })
    expect(transport.post.mock.calls[0]).toEqual(['/files/folder', undefined, { params: { path: '/tmp/new', mode: '0700' } }])
    await fs.move('/tmp/a', '/tmp/b')
    expect(transport.post.mock.calls[1]).toEqual(['/files/move', undefined, { params: { source: '/tmp/a', destination: '/tmp/b' } }])
    await expect(fs.grep('/tmp', 'needle')).resolves.toEqual({ items: [{ path: '/tmp/a' }], nextCursor: 'n' })
    await expect(fs.list('/tmp/plain')).resolves.toEqual({ items: [{ name: 'b', path: '/tmp/b' }] })
    expect(transport.get.mock.calls[4]).toEqual(['/files', { params: { path: '/tmp/plain' } }])
    await expect(fs.glob('/tmp', '*.txt')).resolves.toEqual({ items: ['/tmp/a'] })
    await expect(fs.replace(['/tmp/a'], 'old', 'new')).resolves.toEqual({ items: [{ file: '/tmp/a', replacements: 1 }] })
  })

  it('shapes git namespace routes and list response variants', async () => {
    const transport = transportStub()
    const git = new GitService(transport.object)
    transport.get
      .mockResolvedValueOnce({ currentBranch: 'main', files: [] })
      .mockResolvedValueOnce(['main', 'dev'])
      .mockResolvedValueOnce({ items: [{ hash: 'h', message: 'm' }] })
    transport.post.mockResolvedValueOnce(undefined).mockResolvedValueOnce({ hash: 'h' })

    await git.clone('https://example.invalid/repo.git', {
      path: '/repo',
      branch: 'main',
      commitId: 'abc',
      depth: 7,
      insecureSkipTls: true,
      username: 'u',
      password: 'p',
      timeout: 12_500,
    })
    expect(transport.post.mock.calls[0]).toEqual([
      '/git/clone',
      {
        url: 'https://example.invalid/repo.git',
        path: '/repo',
        branch: 'main',
        commit_id: 'abc',
        depth: 7,
        insecureSkipTls: true,
        username: 'u',
        password: 'p',
      },
      { timeout: 12_500 },
    ])
    await expect(git.status('/repo')).resolves.toMatchObject({ currentBranch: 'main' })
    expect(transport.get.mock.calls[0]).toEqual(['/git/status', { params: { path: '/repo' } }])
    await git.add('/repo', ['README.md'])
    await git.reset('/repo', { mode: 'mixed', target: 'HEAD~1', files: ['README.md'] })
    await git.restore('/repo', ['README.md'], { staged: true, worktree: false, source: 'HEAD' })
    await git.commit('/repo', 'msg', { author: 'A', email: 'a@example.invalid' })
    await expect(git.commit('/repo', 'msg', { author: '   ', email: 'a@example.invalid' })).rejects.toThrow('author and email')
    await git.pull('/repo', { username: 'u', branch: 'main', remote: 'origin', timeout: 3_000 })
    await git.push('/repo', { password: 'p', branch: 'release', remote: 'origin', setUpstream: true, timeout: 7_000 })
    await expect(git.listBranches('/repo')).resolves.toEqual({ items: ['main', 'dev'] })
    await git.createBranch('/repo', 'feature')
    await git.deleteBranch('/repo', 'feature')
    await git.checkoutBranch('/repo', 'main')
    await expect(git.history('/repo')).resolves.toEqual({ items: [{ hash: 'h', message: 'm' }] })
    expect(transport.post.mock.calls.slice(1)).toEqual([
      ['/git/add', { path: '/repo', files: ['README.md'] }],
      ['/git/reset', { path: '/repo', mode: 'mixed', target: 'HEAD~1', files: ['README.md'] }],
      ['/git/restore', { path: '/repo', files: ['README.md'], staged: true, worktree: false, source: 'HEAD' }],
      ['/git/commit', { path: '/repo', message: 'msg', author: 'A', email: 'a@example.invalid' }],
      ['/git/pull', { path: '/repo', username: 'u', password: undefined, branch: 'main', remote: 'origin' }, { timeout: 3_000 }],
      ['/git/push', { path: '/repo', username: undefined, password: 'p', branch: 'release', remote: 'origin', setUpstream: true }, { timeout: 7_000 }],
      ['/git/branches', { path: '/repo', name: 'feature' }],
      ['/git/checkout', { path: '/repo', branch: 'main' }],
    ])
    expect(transport.delete.mock.calls[0]).toEqual(['/git/branches', { data: { path: '/repo', name: 'feature' } }])
  })

  it('threads git commit allowEmpty only when enabled', async () => {
    const transport = transportStub()
    const git = new GitService(transport.object)

    await git.commit('/repo', 'msg', { author: 'A', email: 'a@example.invalid' })
    await git.commit('/repo', 'msg', { author: 'A', email: 'a@example.invalid', allowEmpty: true })

    expect(transport.post.mock.calls).toEqual([
      ['/git/commit', { path: '/repo', message: 'msg', author: 'A', email: 'a@example.invalid', allow_empty: undefined }],
      ['/git/commit', { path: '/repo', message: 'msg', author: 'A', email: 'a@example.invalid', allow_empty: true }],
    ])
  })

  it('shapes git init remotes and config routes', async () => {
    const transport = transportStub()
    const git = new GitService(transport.object)
    transport.get
      .mockResolvedValueOnce({ remotes: [{ name: 'origin', url: 'https://example.invalid/repo.git' }] })
      .mockResolvedValueOnce({ value: 'Ada' })
      .mockResolvedValueOnce({ value: null })
    transport.post
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce(undefined)

    await git.init('/repo', { bare: true, initialBranch: 'trunk' })
    await expect(git.listRemotes('/repo')).resolves.toEqual({ items: [{ name: 'origin', url: 'https://example.invalid/repo.git' }] })
    await git.addRemote('/repo', 'origin', 'https://example.invalid/repo.git')
    await expect(git.getConfig('user.name', { path: '/repo', scope: 'local' })).resolves.toBe('Ada')
    await git.setConfig('user.name', 'Ada', { path: '/repo', scope: 'local' })
    await expect(git.getConfig('demo.missing')).resolves.toBeNull()

    expect(transport.post.mock.calls).toEqual([
      ['/git/init', { path: '/repo', bare: true, initial_branch: 'trunk' }],
      ['/git/remotes', { path: '/repo', name: 'origin', url: 'https://example.invalid/repo.git' }],
      ['/git/config', { key: 'user.name', value: 'Ada', path: '/repo', scope: 'local' }],
    ])
    expect(transport.get.mock.calls).toEqual([
      ['/git/remotes', { params: { path: '/repo' } }],
      ['/git/config', { params: { key: 'user.name', path: '/repo', scope: 'local' } }],
      ['/git/config', { params: { key: 'demo.missing', scope: 'global' } }],
    ])
  })

  it('uses a 30-minute default deadline for remote git operations', async () => {
    const transport = transportStub()
    const git = new GitService(transport.object)

    await git.clone('https://example.invalid/repo.git', { path: '/repo' })
    await git.pull('/repo')
    await git.push('/repo')

    expect(transport.post.mock.calls).toEqual([
      ['/git/clone', { url: 'https://example.invalid/repo.git', path: '/repo', branch: undefined, commit_id: undefined, depth: undefined, insecureSkipTls: undefined, username: undefined, password: undefined }, { timeout: 1_800_000 }],
      ['/git/pull', { path: '/repo', username: undefined, password: undefined, branch: undefined, remote: undefined }, { timeout: 1_800_000 }],
      ['/git/push', { path: '/repo', username: undefined, password: undefined, branch: undefined, remote: undefined, setUpstream: undefined }, { timeout: 1_800_000 }],
    ])
  })

  it('binds transport request timeouts at client construction', () => {
    const client = new Daytona({ apiKey: 'key', requestTimeoutSeconds: 0.123 })
    expect(clientInstance(client).defaults.timeout).toBe(123)
  })

  it('matches Python auth validation: missing credentials auth error, both supplied validation error, jwt without project validation error', () => {
    expect(() => new Daytona()).toThrow(AuthenticationError)
    expect(() => new Daytona({ apiKey: 'key', jwtToken: 'jwt', projectId: 'project-1' })).toThrow('exactly one of apiKey or jwtToken')
    expect(() => new Daytona({ jwtToken: 'jwt' })).toThrow('projectId is required when jwtToken is set')
  })

  it('lets an explicit credential win over the other credential in the environment', () => {
    const saved = { key: process.env.DAYTONA_API_KEY, jwt: process.env.DAYTONA_JWT_TOKEN }
    process.env.DAYTONA_API_KEY = 'env-key'
    process.env.DAYTONA_JWT_TOKEN = 'env-jwt'
    try {
      expect(() => new Daytona({ jwtToken: 'jwt', projectId: 'p' })).not.toThrow()
      expect(() => new Daytona({ apiKey: 'key' })).not.toThrow()
      expect(() => new Daytona({ apiKey: 'key', jwtToken: 'jwt', projectId: 'p' })).toThrow('exactly one')
      expect(() => new Daytona()).toThrow('exactly one')
    } finally {
      if (saved.key === undefined) delete process.env.DAYTONA_API_KEY
      else process.env.DAYTONA_API_KEY = saved.key
      if (saved.jwt === undefined) delete process.env.DAYTONA_JWT_TOKEN
      else process.env.DAYTONA_JWT_TOKEN = saved.jwt
    }
  })

  it('treats explicit empty credentials and projectId as invalid instead of falling back to env', () => {
    const previousJwt = process.env.DAYTONA_JWT_TOKEN
    const previousProject = process.env.DAYTONA_PROJECT_ID
    const previousKey = process.env.DAYTONA_API_KEY
    try {
      process.env.DAYTONA_JWT_TOKEN = 'env-jwt'
      process.env.DAYTONA_PROJECT_ID = 'env-project'
      process.env.DAYTONA_API_KEY = 'env-key'
      expect(() => new Daytona({ apiKey: 'key', jwtToken: '' })).toThrow('jwtToken must be a non-empty string when provided')
      expect(() => new Daytona({ jwtToken: '' })).toThrow('jwtToken must be a non-empty string when provided')
      expect(() => new Daytona({ apiKey: '' })).toThrow('apiKey must be a non-empty string when provided')
      expect(() => new Daytona({ jwtToken: 'jwt-token', projectId: '' })).toThrow('projectId must be a non-empty string when provided')
    } finally {
      if (previousJwt === undefined) delete process.env.DAYTONA_JWT_TOKEN
      else process.env.DAYTONA_JWT_TOKEN = previousJwt
      if (previousKey === undefined) delete process.env.DAYTONA_API_KEY
      else process.env.DAYTONA_API_KEY = previousKey
      if (previousProject === undefined) delete process.env.DAYTONA_PROJECT_ID
      else process.env.DAYTONA_PROJECT_ID = previousProject
    }
  })

  it('uses a caller-provided otelApi module without resolving the peer itself', async () => {
    const otel = otelApi
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    jest.useRealTimers()
    ;(globalThis as typeof globalThis & { __DAYTONA_OTEL_IMPORT__?: () => Promise<never> }).__DAYTONA_OTEL_IMPORT__ = async () => {
      throw new Error('Cannot find module @opentelemetry/api')
    }
    try {
      const client = new Daytona({ apiKey: 'key', otelEnabled: true, otelApi: otel })
      let headers = new Headers()
      clientInstance(client).defaults.adapter = async (config) => {
        headers = responseHeaders(config)
        return { data: { id: 'current-key' }, status: 200, statusText: 'OK', headers: {}, config }
      }
      await client.currentApiKey()
      expect(headers.get('traceparent') ?? '').not.toMatch(/^00-0{32}-/)
      await provider.forceFlush()
      expect(exporter.getFinishedSpans().map((span) => span.name)).toContain('daytona.get /api-keys/current')
    } finally {
      trace.disable()
      delete (globalThis as typeof globalThis & { __DAYTONA_OTEL_IMPORT__?: () => Promise<never> }).__DAYTONA_OTEL_IMPORT__
    }
  })

  it('normalizes DAYTONA_OTEL_ENABLED like Python', async () => {
    const previous = process.env.DAYTONA_OTEL_ENABLED
    ;(globalThis as typeof globalThis & { __DAYTONA_OTEL_IMPORT__?: () => Promise<unknown> }).__DAYTONA_OTEL_IMPORT__ = async () => await import('@opentelemetry/api')
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    try {
      process.env.DAYTONA_OTEL_ENABLED = '  TRUE  '
      const enabled = new Daytona({ apiKey: 'key' })
      let enabledHeaders = new Headers()
      clientInstance(enabled).defaults.adapter = async (config) => {
        enabledHeaders = responseHeaders(config)
        return { data: { id: 'current-key' }, status: 200, statusText: 'OK', headers: {}, config }
      }
      await enabled.currentApiKey()
      // A real (non-zero) trace id proves the instrumentation path ran — the
      // noop tracer would still yield a truthy all-zero traceparent.
      const traceparent = enabledHeaders.get('traceparent') ?? ''
      expect(traceparent).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$/)
      expect(traceparent).not.toMatch(/^00-0{32}-/)
      // The span may already be exporting when forceFlush starts; the
      // in-memory exporter completes through a setTimeout, which the suite's
      // fake timers only fire when advanced.
      const flushed = provider.forceFlush()
      await jest.advanceTimersByTimeAsync(0)
      await flushed
      expect(exporter.getFinishedSpans().map((span) => span.name)).toContain('daytona.get /api-keys/current')

      process.env.DAYTONA_OTEL_ENABLED = 'yes'
      const disabled = new Daytona({ apiKey: 'key' })
      let disabledHeaders = new Headers()
      clientInstance(disabled).defaults.adapter = async (config) => {
        disabledHeaders = responseHeaders(config)
        return { data: { id: 'current-key' }, status: 200, statusText: 'OK', headers: {}, config }
      }
      await disabled.currentApiKey()
      expect(disabledHeaders.get('traceparent')).toBeNull()
    } finally {
      trace.disable()
      if (previous === undefined) delete process.env.DAYTONA_OTEL_ENABLED
      else process.env.DAYTONA_OTEL_ENABLED = previous
    }
  })

  it('shares one token exchange across concurrent api and toolbox requests', async () => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: 'http://toolbox.invalid/sandbox-api/sbx' })
    const apiAuth: string[] = []
    const toolboxAuth: string[] = []
    clientInstance(client).defaults.adapter = async (config) => {
      apiAuth.push(responseHeaders(config).get('authorization') ?? '')
      return { data: { id: 'current-key' }, status: 200, statusText: 'OK', headers: {}, config }
    }
    toolboxInstance(sandbox).defaults.adapter = async (config) => {
      toolboxAuth.push(responseHeaders(config).get('authorization') ?? '')
      return { data: { currentBranch: 'main' }, status: 200, statusText: 'OK', headers: {}, config }
    }

    let resolveExchange: ((response: Response) => void) | undefined
    const exchangeResponse = new Promise<Response>((resolve) => { resolveExchange = resolve })
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      expect(String(input)).toBe('http://api.invalid/auth/token')
      return exchangeResponse
    })

    const pending = Promise.all([client.currentApiKey(), sandbox.git.status('/repo')])
    await flushMicrotasks()
    expect(fetch).toHaveBeenCalledTimes(1)
    resolveExchange?.(new Response(JSON.stringify({ access_token: 'platform-1', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), {
      headers: { 'content-type': 'application/json' },
    }))

    await expect(pending).resolves.toEqual([{ id: 'current-key' }, { currentBranch: 'main' }])
    expect(apiAuth).toEqual(['Bearer platform-1'])
    expect(toolboxAuth).toEqual(['Bearer platform-1'])
  })

  it('refreshes jwt exchanges when fewer than 30 seconds remain', async () => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const auths: string[] = []
    clientInstance(client).defaults.adapter = async (config) => {
      auths.push(responseHeaders(config).get('authorization') ?? '')
      return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config }
    }
    const fetch = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'platform-1', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'platform-2', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))

      await client.listAuditActions()
    jest.advanceTimersByTime(269_000) // 31 s left: still cached
      await client.listAuditActions()
    jest.advanceTimersByTime(2_000) // 29 s left: refresh
    await client.currentApiKey()

    expect(fetch).toHaveBeenCalledTimes(2)
    expect(auths).toEqual(['Bearer platform-1', 'Bearer platform-1', 'Bearer platform-2'])
  })

  it('short platform-token TTLs refresh at half the lifetime instead of on every request', async () => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    clientInstance(client).defaults.adapter = async (config) => ({ data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config })
    const fetch = jest.spyOn(globalThis, 'fetch')
      .mockImplementation(async () => new Response(JSON.stringify({ access_token: 'platform', expires_in: 10, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))
    for (let i = 0; i < 5; i += 1) await client.listAuditActions()
    jest.advanceTimersByTime(4_000)
    await client.listAuditActions()
    expect(fetch).toHaveBeenCalledTimes(1)
    jest.advanceTimersByTime(2_000) // past half of 10 s
    await client.listAuditActions()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  // The Python SDK's test_core.py asserts the same messages for the same bodies.
  it.each([
    ['not json', 'token exchange response is not a JSON object'],
    ['[]', 'token exchange response is not a JSON object'],
    ['', 'token exchange response is not a JSON object'],
    ['{"expires_in": 300}', 'token exchange response access_token must be a non-empty string'],
    ['{"access_token": "", "expires_in": 300}', 'token exchange response access_token must be a non-empty string'],
    ['{"access_token": "t"}', 'token exchange response expires_in must be a positive integer'],
    ['{"access_token": "t", "expires_in": 0}', 'token exchange response expires_in must be a positive integer'],
    ['{"access_token": "t", "expires_in": -1}', 'token exchange response expires_in must be a positive integer'],
    ['{"access_token": "t", "expires_in": "soon"}', 'token exchange response expires_in must be a positive integer'],
    ['{"access_token": "t", "expires_in": 9007199254740992}', 'token exchange response expires_in must be a positive integer'],
    ['{"access_token": "t", "expires_in": 1.5}', 'token exchange response expires_in must be a positive integer'],
    ['{"access_token": "t", "expires_in": 60.5}', 'token exchange response expires_in must be a positive integer'],
    ['{"access_token": "t", "expires_in": true}', 'token exchange response expires_in must be a positive integer'],
    ['{"access_token": "t", "expires_in": 9007199254740992.0}', 'token exchange response expires_in must be a positive integer'],
  ])('names what is wrong with the token exchange body %p', async (body, message) => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    clientInstance(client).defaults.adapter = async (config) => ({ data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config })
    jest.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(body, { headers: { 'content-type': 'application/json' } }))
    await expect(client.listAuditActions()).rejects.toMatchObject({ constructor: BadRequestError, message, statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  })

  // JSON.parse cannot tell 60.0 from 60; Python's test_core.py takes the same values.
  it.each(['60', '60.0', '1e2'])('takes an integral expires_in written as %s', async (raw) => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const auths: string[] = []
    clientInstance(client).defaults.adapter = async (config) => {
      auths.push(responseHeaders(config).get('authorization') ?? '')
      return { data: { items: [] }, status: 200, statusText: 'OK', headers: {}, config }
    }
    jest.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(`{"access_token": "platform", "expires_in": ${raw}}`, { headers: { 'content-type': 'application/json' } }))
    await client.listAuditActions()
    expect(auths).toEqual(['Bearer platform'])
  })

  it('reads only the token exchange fields it uses (no token_type / issued_token_type needed, as in Python)', async () => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const auths: string[] = []
    clientInstance(client).defaults.adapter = async (config) => {
      auths.push(responseHeaders(config).get('authorization') ?? '')
      return { data: { items: [] }, status: 200, statusText: 'OK', headers: {}, config }
    }
    jest.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ access_token: 'platform', expires_in: 9007199254740991 }), { headers: { 'content-type': 'application/json' } }))
    await client.listAuditActions()
    expect(auths).toEqual(['Bearer platform'])
  })

  it.each([0, -1])('rejects a token exchange whose expires_in is %p', async (expiresIn) => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    clientInstance(client).defaults.adapter = async (config) => ({ data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config })
    jest.spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ access_token: 'platform', expires_in: expiresIn, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))
    // A broken exchange response is the SDK's BadRequestError, as in Python.
    await expect(client.listAuditActions()).rejects.toMatchObject({ constructor: BadRequestError, message: 'token exchange response expires_in must be a positive integer', statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  })

  it('re-exchanges once after a 401 and retries with the new token', async () => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const auths: string[] = []
    let attempts = 0
    clientInstance(client).defaults.adapter = async (config) => {
      attempts += 1
      auths.push(responseHeaders(config).get('authorization') ?? '')
      if (attempts === 1) {
        return Promise.reject(rawAxiosError(401, { code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }, config))
      }
      return { data: { targets: [] }, status: 200, statusText: 'OK', headers: {}, config }
    }
    const fetch = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'platform-1', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'platform-2', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))

    await expect(client.listAuditActions()).resolves.toEqual({ targets: [] })
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(auths).toEqual(['Bearer platform-1', 'Bearer platform-2'])
  })

  it('maps a request deadline that fires during the token exchange to ClientTimeoutError', async () => {
    jest.useRealTimers()
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (input: string | URL | Request) => {
      if (String(input).endsWith('/auth/token')) return new Promise<Response>(() => undefined) // hangs
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    })
    try {
      const provider = createPlatformTokenProvider({ baseURL: 'http://api.invalid', jwtToken: 'jwt', projectId: 'p' })
      const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', undefined, 50, { authProvider: provider })
      await expect(transport.fetch('/version')).rejects.toBeInstanceOf(ClientTimeoutError)
    } finally {
      fetch.mockRestore()
    }
  })

  it('treats a blank DAYTONA_JWT_TOKEN env var as absent when apiKey auth is configured', async () => {
    const previousJwt = process.env.DAYTONA_JWT_TOKEN
    const previousProject = process.env.DAYTONA_PROJECT_ID
    const fetch = jest.spyOn(globalThis, 'fetch')
    try {
      process.env.DAYTONA_JWT_TOKEN = '   '
      process.env.DAYTONA_PROJECT_ID = 'env-project'
      const client = new Daytona({ apiKey: 'key' })
      let authorization: string | null = null
      clientInstance(client).defaults.adapter = async (config) => {
        authorization = responseHeaders(config).get('authorization')
        return { data: { id: 'current-key' }, status: 200, statusText: 'OK', headers: {}, config }
      }

      await expect(client.currentApiKey()).resolves.toEqual({ id: 'current-key' })
      expect(authorization).toBe('Bearer key')
      expect(fetch).not.toHaveBeenCalled()
    } finally {
      fetch.mockRestore()
      if (previousJwt === undefined) delete process.env.DAYTONA_JWT_TOKEN
      else process.env.DAYTONA_JWT_TOKEN = previousJwt
      if (previousProject === undefined) delete process.env.DAYTONA_PROJECT_ID
      else process.env.DAYTONA_PROJECT_ID = previousProject
    }
  })

  it('times out while waiting for a slow bodyFactory before fetch starts', async () => {
    const originalFetch = globalThis.fetch
    const fetch = jest.fn(async () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }))
    globalThis.fetch = fetch as never
    try {
      const transport = new HttpTransport('http://toolbox.invalid', 'key', 50)
      const pending = transport.fetch('/events', {
        method: 'POST',
        bodyFactory: async () => await new Promise<BodyInit>((resolve) => {
          setTimeout(() => resolve('late body'), 100)
        }),
      }).catch((error: unknown) => error)
      await jest.advanceTimersByTimeAsync(50)
      expect(await pending).toBeInstanceOf(ClientTimeoutError)
      expect(fetch).not.toHaveBeenCalled()
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('never sends two credentials: a caller lowercase authorization header is replaced by the SDK bearer', async () => {
    jest.useRealTimers()
    let seen: Headers | undefined
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (_input: string | URL | Request, init?: RequestInit) => {
      seen = new Headers(init?.headers)
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    })
    try {
      const auth = { bearer: async () => 'sbx-tok', onUnauthorized: async () => undefined }
      const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', undefined, undefined, { auth })
      await transport.fetch('/version', { headers: { authorization: 'Bearer stale-caller-token' } })
      expect(seen?.get('authorization')).toBe('Bearer sbx-tok')
    } finally {
      fetch.mockRestore()
    }
  })

  it('routes a 401 accepted by a caller validateStatus through the refresh/retry path', async () => {
    jest.useRealTimers()
    const tokens = ['sbx-1', 'sbx-2']
    const auth = { bearer: async () => tokens[0]!, onUnauthorized: async () => tokens[1] }
    const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', undefined, undefined, { auth })
    const seenAuth: string[] = []
    let calls = 0
    transport.http.defaults.adapter = async (config) => {
      calls += 1
      seenAuth.push(String(config.headers?.Authorization ?? config.headers?.authorization))
      const status = calls === 1 ? 401 : 200
      return { data: status === 401 ? { code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_DAEMON' } : { ok: true }, status, statusText: String(status), headers: {}, config }
    }
    const result = await transport.get<{ ok: boolean }>('/version', { validateStatus: () => true })
    expect(result).toEqual({ ok: true })
    expect(seenAuth).toEqual(['Bearer sbx-1', 'Bearer sbx-2'])
  })

  it('keeps the forced-refresh bearer on the axios 401 retry even if the provider still answers the stale token', async () => {
    jest.useRealTimers()
    // Provider: plain lookups keep returning the stale token; only the forced
    // refresh yields the fresh one. The retry must carry the fresh token.
    const authProvider = {
      authorizationHeader: jest.fn(async (options?: { forceRefresh?: boolean }) =>
        options?.forceRefresh ? 'Bearer proj-fresh' : 'Bearer proj-stale'),
    }
    const transport = new HttpTransport('http://api.invalid', undefined, undefined, { authProvider })
    const seen: string[] = []
    let calls = 0
    transport.http.defaults.adapter = async (config) => {
      calls += 1
      seen.push(String(config.headers?.Authorization))
      if (calls === 1) {
        throw new AxiosError('Request failed with status code 401', AxiosError.ERR_BAD_REQUEST, config, undefined, { data: { code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }, status: 401, statusText: '401', headers: {}, config })
      }
      return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config }
    }
    await transport.get('/sandboxes')
    expect(seen).toEqual(['Bearer proj-stale', 'Bearer proj-fresh'])
  })

  it('does not replay a one-shot stream body on the axios 401 retry but still rotates the token', async () => {
    jest.useRealTimers()
    const authProvider = {
      authorizationHeader: jest.fn(async (options?: { forceRefresh?: boolean }) =>
        options?.forceRefresh ? 'Bearer proj-fresh' : 'Bearer proj-stale'),
    }
    const transport = new HttpTransport('http://api.invalid', undefined, undefined, { authProvider })
    let calls = 0
    transport.http.defaults.adapter = async (config) => {
      calls += 1
      throw new AxiosError('Request failed with status code 401', AxiosError.ERR_BAD_REQUEST, config, undefined, { data: { code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }, status: 401, statusText: '401', headers: {}, config })
    }
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.close() } })
    await expect(transport.http.post('/upload', body)).rejects.toMatchObject({ statusCode: 401 })
    expect(calls).toBe(1)
    expect(authProvider.authorizationHeader).toHaveBeenLastCalledWith(expect.objectContaining({ forceRefresh: true, rejectedAuthorization: 'Bearer proj-stale' }))
  })

  it('lets a caller abort while a shared token exchange is still pending', async () => {
    jest.useRealTimers()
    let resolveExchange: ((value: Response) => void) | undefined
    const exchange = new Promise<Response>((resolve) => { resolveExchange = resolve })
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (input: string | URL | Request) => {
      if (String(input).endsWith('/auth/token')) return exchange
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    })
    try {
      const provider = createPlatformTokenProvider({ baseURL: 'http://api.invalid', jwtToken: 'jwt', projectId: 'p' })
      const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', undefined, undefined, { authProvider: provider })
      const controller = new AbortController()
      const pending = transport.fetch('/version', { signal: controller.signal })
      controller.abort(new DOMException('caller cancelled', 'AbortError'))
      await expect(pending).rejects.toBeInstanceOf(DOMException)
      // The shared exchange is still alive for other callers: resolving it
      // now lets a second request succeed with the exchanged token.
      resolveExchange?.(new Response(JSON.stringify({ access_token: 'platform-1', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))
      expect(await provider.authorizationHeader()).toBe('Bearer platform-1')
      expect(fetch.mock.calls.filter(([input]) => String(input).endsWith('/auth/token'))).toHaveLength(1)
    } finally {
      fetch.mockRestore()
    }
  })

  it('rethrows a custom abort reason from a pending token exchange instead of a ConnectionError', async () => {
    class CallerCancelled extends Error {}
    const exchange = new Promise<Response>(() => {})
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (input: string | URL | Request) => {
      if (String(input).endsWith('/auth/token')) return exchange
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    })
    try {
      const provider = createPlatformTokenProvider({ baseURL: 'http://api.invalid', jwtToken: 'jwt', projectId: 'p' })
      const controller = new AbortController()
      const pending = provider.authorizationHeader({ signal: controller.signal })
      controller.abort(new CallerCancelled('caller cancelled'))
      await expect(pending).rejects.toBeInstanceOf(CallerCancelled)
    } finally {
      fetch.mockRestore()
    }
  })

  it('lets an axios caller abort while the initial authProvider exchange is pending', async () => {
    const controller = new AbortController()
    let authStarted: (() => void) | undefined
    const started = new Promise<void>((resolve) => { authStarted = resolve })
    const adapter = jest.fn(async (config: InternalAxiosRequestConfig) => ({
      data: { ok: true },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }))
    const authProvider = {
      authorizationHeader: jest.fn((options?: { signal?: AbortSignal }) => new Promise<string>((_resolve, reject) => {
        authStarted?.()
        options?.signal?.addEventListener('abort', () => {
          reject(options.signal?.reason ?? new DOMException('aborted', 'AbortError'))
        }, { once: true })
      })),
    }
    const transport = new HttpTransport('http://api.invalid', undefined, undefined, { authProvider })
    transport.http.defaults.adapter = adapter

    const pending = transport.get('/sandboxes', { signal: controller.signal }).catch((error: unknown) => error)
    await started
    controller.abort(new DOMException('caller cancelled', 'AbortError'))

    await expect(pending).resolves.toMatchObject({ name: 'AbortError', message: 'caller cancelled' })
    expect(adapter).not.toHaveBeenCalled()
    expect(authProvider.authorizationHeader).toHaveBeenCalledWith(expect.objectContaining({ signal: controller.signal }))
  })

  it('times out an axios authProvider wait before the adapter starts', async () => {
    const adapter = jest.fn(async (config: InternalAxiosRequestConfig) => ({
      data: { ok: true },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }))
    const authProvider = {
      authorizationHeader: jest.fn((options?: { signal?: AbortSignal }) => new Promise<string>((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => {
          reject(options.signal?.reason ?? new DOMException('aborted', 'AbortError'))
        }, { once: true })
      })),
    }
    const transport = new HttpTransport('http://api.invalid', undefined, undefined, { authProvider })
    transport.http.defaults.adapter = adapter

    const pending = transport.get('/sandboxes', { timeout: 25 }).catch((error: unknown) => error)
    await jest.advanceTimersByTimeAsync(25)

    await expect(pending).resolves.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    expect(adapter).not.toHaveBeenCalled()
  })

  it('lets a prompt axios authProvider resolve within the request timeout', async () => {
    const authProvider = { authorizationHeader: jest.fn().mockResolvedValue('Bearer proj-tok') }
    const transport = new HttpTransport('http://api.invalid', undefined, 25, { authProvider })
    const adapter = jest.fn(async (config: InternalAxiosRequestConfig) => ({
      data: { ok: true },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }))
    transport.http.defaults.adapter = adapter

    await expect(transport.get('/sandboxes')).resolves.toEqual({ ok: true })
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(responseHeaders(adapter.mock.calls[0]?.[0] as InternalAxiosRequestConfig).get('authorization')).toBe('Bearer proj-tok')
  })

  it('lets an axios caller abort while a forced-refresh authProvider exchange is pending', async () => {
    const controller = new AbortController()
    let forcedRefreshStarted: (() => void) | undefined
    const started = new Promise<void>((resolve) => { forcedRefreshStarted = resolve })
    const authProvider = {
      authorizationHeader: jest.fn((options?: { forceRefresh?: boolean; rejectedAuthorization?: string; signal?: AbortSignal }) => {
        if (options?.forceRefresh) {
          return new Promise<string>((_resolve, reject) => {
            forcedRefreshStarted?.()
            options.signal?.addEventListener('abort', () => {
              reject(options.signal?.reason ?? new DOMException('aborted', 'AbortError'))
            }, { once: true })
          })
        }
        return Promise.resolve('Bearer stale-token')
      }),
    }
    const transport = new HttpTransport('http://api.invalid', undefined, undefined, { authProvider })
    const adapter = jest.fn(async (config: InternalAxiosRequestConfig) => {
      throw rawAxiosError(401, { code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }, config)
    })
    transport.http.defaults.adapter = adapter

    const pending = transport.get('/sandboxes', { signal: controller.signal }).catch((error: unknown) => error)
    await started
    controller.abort(new DOMException('caller cancelled', 'AbortError'))

    await expect(pending).resolves.toMatchObject({ name: 'AbortError', message: 'caller cancelled' })
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(authProvider.authorizationHeader).toHaveBeenNthCalledWith(2, expect.objectContaining({
      forceRefresh: true,
      rejectedAuthorization: 'Bearer stale-token',
      signal: controller.signal,
    }))
  })

  it('reuses the already-refreshed token for a late 401 instead of exchanging again', async () => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const auths: string[] = []
    let attempts = 0
    clientInstance(client).defaults.adapter = async (config) => {
      attempts += 1
      const auth = responseHeaders(config).get('authorization') ?? ''
      auths.push(auth)
      // The first two concurrent requests both carry platform-1 and both get a
      // 401; the first retry exchanges to platform-2, the second must reuse it.
      if (auth === 'Bearer platform-1') {
        return Promise.reject(rawAxiosError(401, { code: 'UNAUTHORIZED', message: 'expired', source: 'DAYTONA_API' }, config))
      }
      return { data: { targets: [] }, status: 200, statusText: 'OK', headers: {}, config }
    }
    const fetch = jest.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'platform-1', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'platform-2', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValue(new Response(JSON.stringify({ access_token: 'platform-3-should-not-happen', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } }))

    await Promise.all([client.listAuditActions(), client.listAuditActions()])
    // exactly two exchanges: the initial one and the single forced refresh
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(attempts).toBe(4)
    expect(auths.filter((a) => a === 'Bearer platform-2')).toHaveLength(2)
    expect(auths.some((a) => a.includes('platform-3'))).toBe(false)
  })

  it('shares one agent pair across concurrent first requests with the same pool size', async () => {
    const client = new Daytona({ apiKey: 'key', connectionPoolMaxsize: 5 })
    const agents: unknown[] = []
    clientInstance(client).defaults.adapter = async (config) => {
      agents.push(config.httpAgent)
      return { data: { targets: [] }, status: 200, statusText: 'OK', headers: {}, config }
    }
    await Promise.all([client.listAuditActions(), client.listAuditActions(), client.listAuditActions()])
    expect(agents).toHaveLength(3)
    expect(new Set(agents).size).toBe(1)
  })

  it('adds keep-alive node agents when connectionPoolMaxsize is set', async () => {
    const client = new Daytona({ apiKey: 'key', connectionPoolMaxsize: 7 })
    let httpAgent: unknown
    let httpsAgent: unknown
    clientInstance(client).defaults.adapter = async (config) => {
      httpAgent = config.httpAgent
      httpsAgent = config.httpsAgent
      return { data: { id: 'current-key' }, status: 200, statusText: 'OK', headers: {}, config }
    }

    await client.currentApiKey()
    expect(httpAgent).toBeDefined()
    expect(httpsAgent).toBeDefined()
    expect(Reflect.get(httpAgent as object, 'maxSockets')).toBe(7)
    expect(Reflect.get(httpsAgent as object, 'maxSockets')).toBe(7)
  })

  it('creates spans and injects trace headers only when otel is enabled', async () => {
    const provider = new SdkTracerProvider()
    ;(globalThis as typeof globalThis & { __DAYTONA_OTEL_IMPORT__?: () => Promise<unknown> }).__DAYTONA_OTEL_IMPORT__ = async () => await import('@opentelemetry/api')
    trace.setGlobalTracerProvider(provider)

    const enabled = new Daytona({ apiKey: 'key', otelEnabled: true })
    const sandbox = lazySandbox(enabled, 'sbx')
    let enabledHeaders = new Headers()
    clientInstance(enabled).defaults.adapter = async (config) => {
      enabledHeaders = responseHeaders(config)
      return { data: 'boot ok\n', status: 200, statusText: 'OK', headers: {}, config }
    }

    await sandbox.entrypointLogs()
    expect(enabledHeaders.get('traceparent')).toBeTruthy()

    const disabled = new Daytona({ apiKey: 'key', otelEnabled: false })
    let disabledHeaders = new Headers()
    clientInstance(disabled).defaults.adapter = async (config) => {
      disabledHeaders = responseHeaders(config)
      return { data: 'boot ok\n', status: 200, statusText: 'OK', headers: {}, config }
    }

    await lazySandbox(disabled, 'sbx').entrypointLogs()
    expect(disabledHeaders.get('traceparent')).toBeNull()
  })

  it('uses the static /sandboxes/search route template for search spans', async () => {
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    ;(globalThis as typeof globalThis & { __DAYTONA_OTEL_IMPORT__?: () => Promise<unknown> }).__DAYTONA_OTEL_IMPORT__ = async () => await import('@opentelemetry/api')
    trace.setGlobalTracerProvider(provider)

    const client = new Daytona({ apiKey: 'key', otelEnabled: true })
    clientInstance(client).defaults.adapter = async (config) => ({
      data: { sandboxes: [], next_cursor: null, total: 0, total_relation: 'eq' },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    })

    await client.search({ q: 'hello' })

    const spans = exporter.getFinishedSpans()
    expect(spans[0]?.name).toBe('daytona.get /sandboxes/search')
    expect(spans[0]?.attributes['url.template']).toBe('/sandboxes/search')
    expect(spans[0]?.instrumentationScope.version).toBe(SDK_VERSION)
  })

  it('ignores an opaque base prefix and templates operation ids in fetch spans', async () => {
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    ;(globalThis as typeof globalThis & { __DAYTONA_OTEL_IMPORT__?: () => Promise<unknown> }).__DAYTONA_OTEL_IMPORT__ = async () => await import('@opentelemetry/api')
    trace.setGlobalTracerProvider(provider)
    const originalFetch = globalThis.fetch
    globalThis.fetch = jest.fn(async () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })) as never

    try {
      const transport = new HttpTransport('https://sandbox.example/sandbox-api/sbx', 'key', undefined, { otelEnabled: true, sandboxId: 'sbx' })
      // Streamed spans finish when the body settles, so read the bodies.
      await (await transport.fetch('/processes/proc/logs')).text()
      await (await transport.fetch('/port/3999/in-use')).text()
      const spans = exporter.getFinishedSpans()
      expect(spans[0]?.name).toBe('daytona.get /processes/{id}/logs')
      expect(spans[0]?.attributes['url.template']).toBe('/processes/{id}/logs')
      expect(spans[1]?.name).toBe('daytona.get /port/{port}/in-use')
      expect(spans[1]?.attributes['url.template']).toBe('/port/{port}/in-use')
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('finishes aborted fetch spans with ERROR status', async () => {
    jest.useRealTimers()
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    const originalFetch = globalThis.fetch
    globalThis.fetch = jest.fn((_url: string | URL | Request, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      if (init?.signal?.aborted) {
        reject(init.signal.reason)
        return
      }
      init?.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true })
    })) as never

    try {
      const controller = new AbortController()
      const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', 'key', undefined, { otelEnabled: true, otelApi })
      const pending = transport.fetch('/processes/proc/logs', { signal: controller.signal })
      controller.abort(new DOMException('caller cancelled', 'AbortError'))
      await expect(pending).rejects.toBeInstanceOf(DOMException)
      await provider.forceFlush()
      const spans = exporter.getFinishedSpans()
      expect(spans).toHaveLength(1)
      expect(spans[0]?.status.code).toBe(otelApi.SpanStatusCode.ERROR)
    } finally {
      globalThis.fetch = originalFetch
      trace.disable()
    }
  })

  it('keeps a streamed fetch span open until the body settles and marks read failures ERROR', async () => {
    jest.useRealTimers()
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    const originalFetch = globalThis.fetch
    // Error only on the SECOND pull, i.e. after the caller started reading,
    // so headers (and the first chunk) arrive with the span still open.
    let pulls = 0
    const failing = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulls += 1
        if (pulls === 1) {
          controller.enqueue(new TextEncoder().encode('partial '))
          return
        }
          controller.error(new Error('server reset mid-body'))
      },
    })
    globalThis.fetch = jest.fn(async () => new Response(failing, { status: 200 })) as never
    try {
      const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', 'key', undefined, { otelEnabled: true, otelApi })
      const response = await transport.fetch('/processes/proc/logs')
      // Headers arrived with 200: the span must NOT be finished yet.
      await provider.forceFlush()
      expect(exporter.getFinishedSpans()).toHaveLength(0)
    await expect(response.text()).rejects.toThrow('server reset mid-body')
      await provider.forceFlush()
      const spans = exporter.getFinishedSpans()
      expect(spans).toHaveLength(1)
      expect(spans[0]?.status.code).toBe(otelApi.SpanStatusCode.ERROR)
    } finally {
      globalThis.fetch = originalFetch
      trace.disable()
    }
  })

  it('marks a 4xx span ERROR even when the caller disabled checkStatus', async () => {
    jest.useRealTimers()
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    const originalFetch = globalThis.fetch
    globalThis.fetch = jest.fn(async () => new Response('{"code":"NOT_FOUND"}', { status: 404 })) as never
    try {
      const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', 'key', undefined, { otelEnabled: true, otelApi })
      const response = await transport.fetch('/processes/proc/logs', {}, false)
      expect(response.status).toBe(404)
      await response.text()
      await provider.forceFlush()
      const spans = exporter.getFinishedSpans()
      expect(spans).toHaveLength(1)
      expect(spans[0]?.status.code).toBe(otelApi.SpanStatusCode.ERROR)
      expect(spans[0]?.attributes['http.response.status_code']).toBe(404)
    } finally {
      globalThis.fetch = originalFetch
      trace.disable()
    }
  })

  it('ignores an opaque API base prefix while retaining the sandbox id attribute', async () => {
    jest.useRealTimers()
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    try {
      const client = withApiUrl('https://api.example/api/v2', () => new Daytona({ apiKey: 'key', otelEnabled: true, otelApi }))
      clientInstance(client).defaults.adapter = async (config) => ({
        data: { id: 'sbx', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      })

      await lazySandbox(client, 'sbx').refreshData()
      await provider.forceFlush()

      const spans = exporter.getFinishedSpans()
      expect(spans).toHaveLength(1)
      expect(spans[0]?.name).toBe('daytona.get /sandboxes/{id}')
      expect(spans[0]?.attributes['daytona.sandbox_id']).toBe('sbx')
    } finally {
      trace.disable()
    }
  })

  it('finishes a streamed fetch span OK once the body is fully read', async () => {
    jest.useRealTimers()
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    const originalFetch = globalThis.fetch
    globalThis.fetch = jest.fn(async () => new Response('whole body', { status: 200 })) as never
    try {
      const transport = new HttpTransport('https://proxy.example/sandbox-api/sbx', 'key', undefined, { otelEnabled: true, otelApi })
      const response = await transport.fetch('/processes/proc/logs')
      expect(await response.text()).toBe('whole body')
      await provider.forceFlush()
      const spans = exporter.getFinishedSpans()
      expect(spans).toHaveLength(1)
      expect(spans[0]?.status.code).not.toBe(otelApi.SpanStatusCode.ERROR)
    } finally {
      globalThis.fetch = originalFetch
      trace.disable()
    }
  })

  it('ignores an opaque base prefix and templates operation ids in axios spans', async () => {
    jest.useRealTimers()
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    try {
      const transport = new HttpTransport('https://sandbox.example/sandbox-api/sbx', 'key', undefined, { otelEnabled: true, otelApi })
      transport.http.defaults.adapter = async (config) => ({
        data: 'boot ok\n',
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      })

      await transport.get('/processes/proc/logs')
      await provider.forceFlush()

      const spans = exporter.getFinishedSpans()
      expect(spans[0]?.name).toBe('daytona.get /processes/{id}/logs')
      expect(spans[0]?.attributes['url.template']).toBe('/processes/{id}/logs')
    } finally {
      trace.disable()
    }
  })

  it('templates the port segment even though ports is an id-bearing collection', async () => {
    jest.useRealTimers()
    const otel = otelApi
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    try {
      const client = new Daytona({ apiKey: 'key', otelEnabled: true, otelApi: otel })
      clientInstance(client).defaults.adapter = async (config) => ({
        data: config.url?.includes('/preview-url')
          ? { url: 'https://p.invalid', token: 't' }
          : { port: 8080, public: false, url: 'https://p.invalid' },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      })
      await lazySandbox(client, 'sbx-1').ports.previewLink(8080)
      await provider.forceFlush()
      const names = exporter.getFinishedSpans().map((span) => span.name)
      expect(names).toContain('daytona.post /sandboxes/{id}/ports/{port}/preview-url')
    } finally {
      trace.disable()
    }
  })

  it('finishes auth-token spans on network failures', async () => {
    jest.useRealTimers()
    const exporter = new InMemorySpanExporter()
    const provider = new SdkTracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
    trace.setGlobalTracerProvider(provider)
    const originalFetch = globalThis.fetch
    let attempts = 0
    globalThis.fetch = jest.fn(async (input: string | URL | Request) => {
      attempts += 1
      if (String(input) === 'http://api.invalid/auth/token') {
        throw new TypeError('network down')
      }
      throw new Error(`unexpected fetch ${String(input)}`)
    }) as never

    try {
      const authProvider = createPlatformTokenProvider({
        baseURL: 'http://api.invalid',
        jwtToken: 'jwt-token',
        projectId: 'project-1',
        otelEnabled: true,
        otelApi,
      })
      await expect(authProvider.authorizationHeader()).rejects.toBeInstanceOf(ConnectionError)
      await provider.forceFlush()
      const spans = exporter.getFinishedSpans()
      expect(attempts).toBe(1)
      expect(spans).toHaveLength(1)
      expect(spans[0]?.name).toBe('daytona.post /auth/token')
      expect(spans[0]?.status.code).toBe(otelApi.SpanStatusCode.ERROR)
    } finally {
      globalThis.fetch = originalFetch
      trace.disable()
    }
  })

  it('templates every id-bearing collection the Python SDK templates', () => {
    // Mirror of python tests for `_route_template`; the set lives in
    // ID_COLLECTION_SEGMENTS / STATIC_COLLECTION_SEGMENTS and must stay in lockstep.
    const cases: ReadonlyArray<readonly [string, string]> = [
      ['/computeruse/recordings/rec-1', '/computeruse/recordings/{id}'],
      ['/computeruse/recordings/rec-1/download', '/computeruse/recordings/{id}/download'],
      ['/computeruse/mouse/click', '/computeruse/mouse/click'],
      ['/computeruse/process/xvfb/logs', '/computeruse/process/{id}/logs'],
      ['/git/branches', '/git/branches'],
      ['/sessions/s-1', '/sessions/{id}'],
      ['/processes/proc-1/logs', '/processes/{id}/logs'],
      ['/port/8080/in-use', '/port/{port}/in-use'],
      ['/sandboxes/sbx/ports/8080/preview-url', '/sandboxes/{id}/ports/{port}/preview-url'],
      ['/sandboxes/sbx/ports/8080/signed-preview-urls', '/sandboxes/{id}/ports/{port}/signed-preview-urls'],
      ['/sandboxes/sbx/ports/8080/signed-preview-urls/revoke', '/sandboxes/{id}/ports/{port}/signed-preview-urls/revoke'],
      ['/secrets/MY_SECRET', '/secrets/{id}'],
      ['/volumes/vol-1', '/volumes/{id}'],
      ['/volumes/vol-1/pull', '/volumes/{id}/pull'],
      ['/images/img-1/build-logs-url', '/images/{id}/build-logs-url'],
      ['/sandboxes/sbx/labels', '/sandboxes/{id}/labels'],
      ['/sandboxes/sbx/logs', '/sandboxes/{id}/logs'],
      ['/sandboxes/search', '/sandboxes/search'],
      ['/sandboxes/from-snapshot', '/sandboxes/from-snapshot'],
      ['/sandboxes/batch-wait', '/sandboxes/batch-wait'],
      ['/sandboxes/macos', '/sandboxes/macos'],
      ['/reservations/bulk', '/reservations/bulk'],
      ['/invitations/accept', '/invitations/accept'],
      ['/invitations/decline', '/invitations/decline'],
      ['/invitations/lookup', '/invitations/lookup'],
      ['/orgs/org-1/invitations/inv-123', '/orgs/{id}/invitations/{id}'],
      ['/api-keys/current', '/api-keys/current'],
      ['/orgs/org-1/api-keys/current', '/orgs/{id}/api-keys/current'],
      ['/orgs/org-1/api-keys/key-1', '/orgs/{id}/api-keys/{id}'],
      ['/orgs/org-1/members/u-1', '/orgs/{id}/members/{id}'],
    ]
    for (const [path, template] of cases) {
      expect(routeTemplate(path)).toBe(template)
    }
  })

  it('raises a clear error when otelEnabled is set but @opentelemetry/api is unavailable', async () => {
    ;(globalThis as typeof globalThis & { __DAYTONA_OTEL_IMPORT__?: () => Promise<never> }).__DAYTONA_OTEL_IMPORT__ = async () => {
      throw new Error('Cannot find module @opentelemetry/api')
    }
    try {
      const client = new Daytona({ apiKey: 'key', otelEnabled: true })
      clientInstance(client).defaults.adapter = async (config) => ({ data: { id: 'current-key' }, status: 200, statusText: 'OK', headers: {}, config })
      await expect(client.currentApiKey()).rejects.toThrow('@opentelemetry/api')
    } finally {
      delete (globalThis as typeof globalThis & { __DAYTONA_OTEL_IMPORT__?: () => Promise<never> }).__DAYTONA_OTEL_IMPORT__
    }
  })
})

  describe('API resource services', () => {
  it('reads the authenticated public audit action matrix', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const response: AuditActionList = {
      targets: [{ target_type: 'sandbox', actions: ['create', 'delete'] }],
    }
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: response })

    await expect(client.listAuditActions()).resolves.toEqual(response)
    expect(get).toHaveBeenCalledWith('/audit/actions')
  })

  it('describes the API key the client authenticates with', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const response: ProjectApiKeyWithContext = {
      id: '0d1c8a6e-4a0a-4f6c-9c58-6a7b2f4b7c11',
      name: 'ci',
      key_prefix: 'dtn_a1b2c3d',
      key_suffix: 'xyz',
      project_id: '6e1a2b3c-4d5e-4f60-8a7b-9c0d1e2f3a4b',
      context: {
        project_name: 'sdk-tests',
        org_id: 'b7a6c5d4-e3f2-4a1b-9c8d-7e6f5a4b3c2d',
        org_name: 'acme',
      },
      project_permissions: ['read:sandboxes'],
      created_at: '2026-09-08T14:00:00Z',
      expires_at: null,
      last_used_at: null,
      service_account_id: null,
      imported_from: null,
      sync_to_v0: false, sync_to_v1: false,
    }
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: response })

    await expect(client.currentApiKey()).resolves.toEqual(response)
    expect(get).toHaveBeenCalledWith('/api-keys/current')
  })

  it('reads the API key project budget', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const response: ProjectBudget = {
      monthly_limit_cents: 50000,
      period_start_date: '2026-10-01',
      spent_cents: 41237,
      evaluated_at: '2026-10-07T09:41:00Z',
      exceeded_at: null,
      stop_at: null,
      created_at: '2026-09-30T08:00:00Z',
      updated_at: '2026-10-05T12:30:00Z',
    }
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: response })

    await expect(client.getProjectBudget()).resolves.toEqual(response)
    expect(get).toHaveBeenCalledWith('/usage/budget')
  })

  it('returns the unset project-budget state', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const unset: ProjectBudget = { monthly_limit_cents: null, period_start_date: null, created_at: null, updated_at: null }
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: unset })

    await expect(client.getProjectBudget()).resolves.toEqual(unset)
  })

  const organizationLimits: OrganizationLimits = {
    sandboxes: {
      concurrent: { limit: 100, usage: 3 },
      total: { limit: 400, usage: 11 },
    },
    sandbox_resources: { max_cores: 4, max_memory_mib: 8192 },
    image_builds: { concurrent: { limit: null, usage: 0 } },
    storage: {
      images: { limit: null, usage: 4 },
      snapshots: { limit: null, usage: 0 },
      volumes: { limit: null, usage: 1 },
    },
    secrets_per_project: { limit: 500, usage: 2 },
    api_keys_per_project: { limit: 100, usage: 1 },
    api_keys_per_organization: { limit: 100, usage: 0 },
  }

  it('reads the API key organization limits, looking the organization up once', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(clientInstance(client), 'get').mockImplementation(async (url: string) =>
      url === '/api-keys/current'
        ? { data: { context: { org_id: 'org-1', org_name: 'Acme', project_name: 'default' } } }
        : { data: organizationLimits },
    )

    await expect(client.getLimits()).resolves.toEqual(organizationLimits)
    await expect(client.getLimits()).resolves.toEqual(organizationLimits)

    expect(get.mock.calls.map(([url]) => url)).toEqual([
      '/api-keys/current',
      '/orgs/org-1/limits',
      '/orgs/org-1/limits',
    ])
  })

  it('reads the named organization limits without a key lookup', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: organizationLimits })

    await client.getLimits({ organizationId: 'org/2' })

    expect(get.mock.calls.map(([url]) => url)).toEqual(['/orgs/org%2F2/limits'])
  })

  it('shares one organization lookup across concurrent first limits reads', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const get = jest.spyOn(clientInstance(client), 'get').mockImplementation(async (url: string) =>
      url === '/api-keys/current'
        ? { data: { context: { org_id: 'org-1', org_name: 'Acme', project_name: 'default' } } }
        : { data: organizationLimits },
    )

    await Promise.all(Array.from({ length: 5 }, () => client.getLimits()))

    const urls = get.mock.calls.map(([url]) => url)
    expect(urls.filter((url) => url === '/api-keys/current')).toHaveLength(1)
    expect(urls.filter((url) => url === '/orgs/org-1/limits')).toHaveLength(5)
  })

  it('retries the organization lookup after a failed one', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const unauthorized = createDaytonaError('unauthorized', 401, 'UNAUTHORIZED', 'DAYTONA_API')
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockRejectedValueOnce(unauthorized)
      .mockResolvedValueOnce({ data: { context: { org_id: 'org-1', org_name: 'Acme', project_name: 'default' } } })
      .mockResolvedValueOnce({ data: organizationLimits })

    await expect(client.getLimits()).rejects.toBe(unauthorized)
    await expect(client.getLimits()).resolves.toEqual(organizationLimits)

    expect(get.mock.calls.map(([url]) => url)).toEqual(['/api-keys/current', '/api-keys/current', '/orgs/org-1/limits'])
  })

  it('requires organizationId for limits under jwtToken authentication', async () => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const get = jest.spyOn(clientInstance(client), 'get')

    await expect(client.getLimits()).rejects.toMatchObject({
      constructor: BadRequestError,
      message: 'organizationId is required with jwtToken authentication',
      statusCode: 400,
      code: 'BAD_REQUEST',
      source: 'SDK',
    })
    await expect(client.getLimits({ organizationId: '' })).rejects.toMatchObject({
      message: 'organizationId must be a non-empty string',
      statusCode: 400,
      code: 'BAD_REQUEST',
      source: 'SDK',
    })
    expect(get).not.toHaveBeenCalled()
  })

  it('propagates non-404 project budget errors', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const forbidden = createDaytonaError('missing read:spending', 403, 'FORBIDDEN', 'DAYTONA_API')
    jest.spyOn(clientInstance(client), 'get').mockRejectedValue(forbidden)

    await expect(client.getProjectBudget()).rejects.toBe(forbidden)
  })

  it('shapes image build/list requests and parses pagination', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValueOnce({ data: { id: 'img-build', name: 'built', status: 'pending' } })
      .mockResolvedValueOnce({ data: { id: 'img-pull', name: 'pulled', status: 'pending' } })
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: imageResponse({ id: 'img-build', name: 'built', region: 'region-a' }) })
      .mockResolvedValueOnce({ data: imageResponse({ id: 'img-pull', name: 'pulled', region: 'region-b' }) })
      .mockResolvedValueOnce({ data: { images: [imageResponse({ id: 'img' })], next_cursor: 'next' } })

    await client.images.build(ImageBuilder.fromDockerfile('FROM scratch\n'), { name: 'built', region: 'us' })
    expect(post.mock.calls[0]?.[0]).toBe('/images')
    expect(post.mock.calls[0]?.[1]).toEqual({ name: 'built', region_id: 'us', dockerfile: 'FROM scratch\n' })

    // A registry image needs no Dockerfile build.
    await client.images.build(ImageBuilder.fromRegistry('python:3.12'), { name: 'pulled', region: 'eu' })
    expect(post.mock.calls[1]).toEqual(['/images', { name: 'pulled', region_id: 'eu', source: { type: 'registry', ref: 'python:3.12' } }])
    // With steps on top it becomes a Dockerfile build again; a step that
    // renders to nothing (`env({})`) does not.
    expect(ImageBuilder.fromRegistry('python:3.12').runCommands('pip install x').toRequestSource()).toEqual({
      dockerfile: 'FROM python:3.12\nRUN pip install x\n',
    })
    expect(ImageBuilder.fromRegistry('python:3.12').env({}).toRequestSource()).toEqual({
      source: { type: 'registry', ref: 'python:3.12' },
    })
    // Both SDKs render the same Dockerfile text.
    expect(
      ImageBuilder.fromRegistry('python:3.12')
        .runCommands('pip install x')
        .pipInstall('numpy', 'pandas>=2.0', "it's")
        .env({ APP_ENV: 'dev mode' })
        .workdir('/workspace')
        .toDockerfile(),
    ).toBe(
      "FROM python:3.12\nRUN pip install x\nRUN python -m pip install numpy 'pandas>=2.0' 'it'\"'\"'s'\n" +
        'ENV APP_ENV="dev mode"\nWORKDIR /workspace\n',
    )
    expect(ImageBuilder.fromDockerfile('FROM python:3.12\n\n').toDockerfile()).toBe('FROM python:3.12\n')
    const listed = await client.images.list({ cursor: 'c', limit: 5 })
    expect(listed.nextCursor).toBe('next')
    expect(listed.items[0]).toBeInstanceOf(ImageHandle)
    expect(get).toHaveBeenCalledWith('/images', { params: { cursor: 'c', limit: 5 } })
  })
  it('creates a missing volume after the HTTP client maps its 404 to NotFoundError', async () => {
    const requests: string[] = []
    const http = createServer((request, response) => {
      requests.push(`${request.method} ${request.url}`)
      if (request.method === 'GET') {
        response.writeHead(404, { 'content-type': 'application/json' })
        response.end(JSON.stringify({ error: 'not found' }))
        return
      }
      response.writeHead(200, { 'content-type': 'application/json' })
      response.end(JSON.stringify({ id: 'created-volume', name: 'missing', type: 'disk', status: 'ready', created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z' }))
    })
    await new Promise<void>(resolve => { http.listen(0, '127.0.0.1', resolve) })
    const address = http.address()
    if (address === null || typeof address === 'string') throw new Error('missing test listener')

    try {
      const client = withApiUrl(`http://127.0.0.1:${address.port}`, () => new Daytona({ apiKey: 'key' }))
      const volumes = client.volume
      await expect(volumes.get('missing', true)).resolves.toEqual({ id: 'created-volume', name: 'missing', type: 'disk', rawType: 'disk', status: 'ready', rawStatus: 'ready', createdAt: '2026-10-14T00:00:00Z', updatedAt: '2026-10-14T00:00:00Z' })
      expect(requests).toEqual(['GET /volumes/missing', 'POST /volumes'])
    } finally {
      await new Promise<void>((resolve, reject) => http.close(error => error ? reject(error) : resolve()))
    }
  })

  it('does not create a volume after an id-only 404', async () => {
    const requests: string[] = []
    const http = createServer((request, response) => {
      requests.push(`${request.method} ${request.url}`)
      response.writeHead(404, { 'content-type': 'application/json' })
      response.end(JSON.stringify({ error: 'not found' }))
    })
    await new Promise<void>(resolve => { http.listen(0, '127.0.0.1', resolve) })
    const address = http.address()
    if (address === null || typeof address === 'string') throw new Error('missing test listener')

    try {
      const client = withApiUrl(`http://127.0.0.1:${address.port}`, () => new Daytona({ apiKey: 'key' }))
      await expect(client.volume.get('00000000-0000-4000-8000-000000000001', true, { idOnly: true })).rejects.toBeInstanceOf(NotFoundError)
      expect(requests).toEqual(['GET /volumes/00000000-0000-4000-8000-000000000001?id_only=true'])
    } finally {
      await new Promise<void>((resolve, reject) => http.close(error => error ? reject(error) : resolve()))
    }
  })

  it('shapes volume routes with encoded ids', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const volumes = client.volume
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { volumes: [{ id: 'vol 1', name: 'listed', type: 'disk', size_gib: 4, status: 'ready', created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z', futureField: true }] } })
      .mockResolvedValueOnce({ data: { id: 'vol 1', name: 'fetched', type: 'disk', size_gib: 8, status: 'ready', created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z', futureField: true } })
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: { id: 'vol 2', name: 'created', type: 'disk', size_gib: 16, status: 'ready', created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z', futureField: true } })
    const del = jest.spyOn(api, 'delete').mockResolvedValue({ data: {} })

    await expect(volumes.list()).resolves.toEqual({
      items: [{ id: 'vol 1', name: 'listed', type: 'disk', rawType: 'disk', sizeGib: 4, status: 'ready', rawStatus: 'ready', createdAt: '2026-10-14T00:00:00Z', updatedAt: '2026-10-14T00:00:00Z' }],
      nextCursor: undefined,
    })
    expect(get.mock.calls[0]).toEqual(['/volumes', { params: { cursor: undefined, limit: undefined } }])
    await expect(volumes.get('vol/name')).resolves.toEqual({ id: 'vol 1', name: 'fetched', type: 'disk', rawType: 'disk', sizeGib: 8, status: 'ready', rawStatus: 'ready', createdAt: '2026-10-14T00:00:00Z', updatedAt: '2026-10-14T00:00:00Z' })
    expect(get.mock.calls[1]?.[0]).toBe('/volumes/vol%2Fname')
    expect(post).not.toHaveBeenCalled()
    const created = await volumes.create('created', 16)
    expect(created).toEqual({ id: 'vol 2', name: 'created', type: 'disk', rawType: 'disk', sizeGib: 16, status: 'ready', rawStatus: 'ready', createdAt: '2026-10-14T00:00:00Z', updatedAt: '2026-10-14T00:00:00Z' })
    await volumes.delete({ ...created, id: 'vol/id' })
    expect(del.mock.calls[0]?.[0]).toBe('/volumes/vol%2Fid')
  })

  it('reports volume deletion acceptance and opts into id-only lookup explicitly', async () => {
    const volumeId = '00000000-0000-4000-8000-000000000001'
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const volumes = client.volume
    const response = { id: volumeId, name: 'named-volume', type: 'disk', status: 'deleting', created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z' }
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: response })
    const del = jest.spyOn(api, 'delete').mockResolvedValue({ data: response })

    await expect(volumes.delete('named-volume')).resolves.toBeUndefined()
    await expect(volumes.get(volumeId, false, { idOnly: true })).resolves.toEqual({ id: volumeId, name: 'named-volume', type: 'disk', rawType: 'disk', status: 'deleting', rawStatus: 'deleting', createdAt: '2026-10-14T00:00:00Z', updatedAt: '2026-10-14T00:00:00Z' })
    await expect(volumes.delete(volumeId, { idOnly: true })).resolves.toBeUndefined()
    expect(del.mock.calls).toEqual([
      ['/volumes/named-volume', undefined],
      [`/volumes/${volumeId}`, { params: { id_only: true } }],
    ])
    expect(get.mock.calls[0]).toEqual([`/volumes/${volumeId}`, { params: { id_only: true } }])
  })

  it('shapes secret routes with encoded ids', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { secrets: [{ name: 'TOKEN' }] } })
      .mockResolvedValueOnce({ data: { name: 'TOKEN', allowed_domains: [], created_at: 'c', updated_at: 'u' } })
    const put = jest.spyOn(api, 'put').mockResolvedValue({ data: {} })
    const del = jest.spyOn(api, 'delete').mockResolvedValue({ data: {} })

    const secretsPage = await client.secret.list()
    expect(secretsPage).toEqual({ items: [{ name: 'TOKEN' }] })
    expect(Object.keys(secretsPage)).toEqual(['items', 'nextCursor'])
    expect(get.mock.calls[0]?.[0]).toBe('/secrets')
    await client.secret.set('API/TOKEN', 'secret', { allowedDomains: ['*.example.com'], description: 'desc', ifNotExists: true })
    expect(put).toHaveBeenCalledWith(
      '/secrets/API%2FTOKEN',
      { value: 'secret', allowed_domains: ['*.example.com'], description: 'desc' },
      { headers: { 'If-None-Match': '*' } },
    )
    await client.secret.delete('API/TOKEN')
    expect(del.mock.calls[0]?.[0]).toBe('/secrets/API%2FTOKEN')
    await expect(client.secret.get('TOKEN')).resolves.toEqual({ name: 'TOKEN', allowed_domains: [], description: null, created_at: 'c', updated_at: 'u' })

    // set with ["*"] sends allowed_domains: ["*"]
    put.mockResolvedValueOnce({ data: {} })
    await client.secret.set('WIDE', 'val', { allowedDomains: ['*'] })
    expect(put).toHaveBeenLastCalledWith('/secrets/WIDE', { value: 'val', allowed_domains: ['*'] }, undefined)

    // @ts-expect-error options is required — allowedDomains must be provided
    const _setRequiresScope = () => client.secret.set('X', 'v')
  })

  it('surfaces secret delete conflicts with the server message', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    jest.spyOn(api, 'delete').mockRejectedValue(
      createDaytonaError(
        'secret is still in use by another resource',
        409,
        'CONFLICT',
        'DAYTONA_API',
      ),
    )

    const failure = client.secret.delete('TOKEN')

    await expect(failure).rejects.toBeInstanceOf(publicSdk.ConflictError)
    await expect(failure).rejects.toMatchObject({
      message: 'secret is still in use by another resource',
      statusCode: 409,
      code: 'CONFLICT',
      source: 'DAYTONA_API',
    })
  })


  it('snapshots.get() and snapshots.delete() address one snapshot', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const response = snapshotResponse({ id: 'snap/1', name: 'after-setup', status: 'ready', memory_bytes: 1073741824 })
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: response })
    const del = jest.spyOn(api, 'delete').mockResolvedValue({ data: {} })

    await expect(client.snapshots.get('snap/1')).resolves.toEqual(new Snapshot(response as never))
    expect(get.mock.calls[0]).toEqual(['/snapshots/snap%2F1', { params: {} }])

    // wait: 'ready' long-polls; the HTTP timeout outlives the server hold.
    await client.snapshots.get('snap/1', { wait: 'ready', timeout: 30 })
    expect(get.mock.calls[1]).toEqual(['/snapshots/snap%2F1', { params: { wait: 'ready', timeout_ms: 30000 }, timeout: 35_000 }])
    // No explicit budget: the server picks the hold, so the request is unbounded.
    await client.snapshots.get('snap/1', { wait: 'ready' })
    expect(get.mock.calls[2]).toEqual(['/snapshots/snap%2F1', { params: { wait: 'ready' }, timeout: 0 }])
    // A budget beyond the server's 60s hold ceiling is capped on the wire (and
    // never becomes an overflowing HTTP timer).
    await client.snapshots.get('snap/1', { wait: 'ready', timeout: 10 * 24 * 60 * 60 * 1000 })
    expect(get.mock.calls[3]).toEqual(['/snapshots/snap%2F1', { params: { wait: 'ready', timeout_ms: 60000 }, timeout: 65_000 }])
    await expect(client.snapshots.get('snap/1', { wait: 'ready', timeout: -1 })).rejects.toMatchObject({ code: 'BAD_REQUEST' })
    await expect(client.snapshots.get('snap/1', { wait: 'ready', timeout: Number.NaN })).rejects.toMatchObject({ code: 'BAD_REQUEST' })
    expect(get).toHaveBeenCalledTimes(4)

    await client.snapshots.delete('snap/1')
    expect(del.mock.calls[0]?.[0]).toBe('/snapshots/snap%2F1')
  })

  it('process.run rejects an invalid timeout before issuing any request', async () => {
    const transport = transportStub()
    const process = new ProcessService(transport.object, 'sbx')
    await expect(process.run('true', { timeout: -1 })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    await expect(process.run('true', { timeout: Number.NaN })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(transport.post).not.toHaveBeenCalled()
  })
  it.each(['region-a', 'region-b', 'region-c', 'region-d'])('creates in region %s with identical retry bodies and no selector on subsequent reads', async (region) => {
    const client = withApiUrl('http://api.invalid', () => new Daytona({ apiKey: 'key' }))
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post')
      .mockRejectedValueOnce(new ClientTimeoutError())
      .mockRejectedValueOnce(new ConnectionError('socket closed'))
      .mockResolvedValueOnce({ data: { id: 'sbx-region' } })
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx-region', status: 'running' } })
    const options = { image: Image.base('python:3.12'), region, name: 'same-action' }
    const uncertain = await createFixture(client, options).catch((error: unknown) => error)
    expect(uncertain).toBeInstanceOf(SandboxCreateRequestTimeoutError)
    const key = (uncertain as SandboxCreateRequestTimeoutError).idempotencyKey
    await expect(createFixture(client, { ...options, idempotencyKey: key })).rejects.toMatchObject({ idempotencyKey: key, source: 'SDK' })
    await createFixture(client, { ...options, idempotencyKey: key })
    expect(post.mock.calls).toHaveLength(3)
    for (const [path, body, config] of post.mock.calls) {
      expect(path).toBe('/sandboxes')
      expect(body).toEqual(post.mock.calls[0]?.[1])
      expect(body).toMatchObject({ region_id: region, name: 'same-action' })
      expect(config?.headers).toEqual({ 'Idempotency-Key': key, 'x-daytona-region': region })
    }
    expect(get.mock.calls[0]?.[0]).toBe('/sandboxes/sbx-region')
    expect(get.mock.calls[0]?.[1]?.headers ?? {}).not.toHaveProperty('x-daytona-region')
    expect(api.defaults.headers.common).not.toHaveProperty('x-daytona-region')
  })

  it('treats omitted regions as local and clears the selector header for region=local', async () => {
    const client = withApiUrl('http://api.invalid', () => new Daytona({ apiKey: 'key' }))
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })
    await client.create('python:3.12', { region: 'region-a' })
    await client.create('python:3.12', { region: 'local' })
    await client.create('python:3.12')
    expect(post.mock.calls[0]?.[1]).toMatchObject({ region_id: 'region-a' })
    expect(post.mock.calls[0]?.[2]?.headers).toHaveProperty('x-daytona-region', 'region-a')
    expect(post.mock.calls[1]?.[1]).toBeDefined()
    expect(post.mock.calls[1]?.[2]?.headers).not.toHaveProperty('x-daytona-region')
    expect((post.mock.calls[2]?.[1] as Record<string, unknown> | undefined)?.region_id).toBeUndefined()
    expect(post.mock.calls[2]?.[2]?.headers).not.toHaveProperty('x-daytona-region')
    expect(clientInstance(client).defaults.headers.common).not.toHaveProperty('x-daytona-region')
  })

  it('preserves an existing sandbox hub when another client starts the handle', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const first = new Daytona({ apiKey: 'key-1' })
    const second = new Daytona({ apiKey: 'key-2' })
    const firstApi = clientInstance(first)
    const firstHub = new WaitHub('http://api.invalid', 'key-1', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, firstApi)
    const secondHub = new WaitHub('http://api.invalid', 'key-2', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, clientInstance(second))
    const waitFirst = jest.spyOn(firstHub, 'wait').mockResolvedValue({ state: 'started', rawState: 'running' })
    const waitSecond = jest.spyOn(secondHub, 'wait').mockRejectedValue(new Error('unexpected second hub wait'))
    const sandbox = new Sandbox(sandboxDetail({ id: 'sbx', status: 'stopped' }) as unknown as SandboxDto, firstApi, 'http://toolbox/{sandboxId}', 'key-1', undefined, {}, undefined, undefined, firstHub)
    ;(second as unknown as { hub?: WaitHub }).hub = secondHub
    jest.spyOn(firstApi, 'get').mockResolvedValue({ data: sandboxDetail({ id: 'sbx', status: 'stopped' }) })
    jest.spyOn(firstApi, 'post').mockResolvedValue({ data: {} })

    try {
      await expect(second.start(sandbox)).resolves.toBeUndefined()

      expect(waitFirst).toHaveBeenCalledTimes(1)
      expect(waitSecond).not.toHaveBeenCalled()
      expect((sandbox as unknown as { _hub?: unknown })._hub).toBe(firstHub)
    } finally {
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('close detaches the shared hub at most once across concurrent close calls', async () => {
    const client = newClient()
    lazySandbox(client, 'sbx')
    const hub = (client as unknown as { hub?: WaitHub }).hub
    expect(hub).toBeDefined()
    if (hub === undefined) throw new Error('expected shared hub')
    const detach = jest.spyOn(hub, 'detach')

    await Promise.all([client.close(), client.close()])

    expect(detach).toHaveBeenCalledTimes(1)
    expect((client as unknown as { hub?: WaitHub }).hub).toBeUndefined()
  })

  it('a closed client does not attach a new hub and waits by polling', async () => {
    const client = new Daytona({ apiKey: 'key' })
    lazySandbox(client, 'seed')
    await client.close()
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })
    const sandbox = lazySandbox(client, 'sbx')

    expect((sandbox as unknown as { _hub?: WaitHub })._hub).toBeUndefined()
    await expect(sandbox.waitForState(['started'], { timeout: 50 })).resolves.toBeUndefined()
    expect(get).toHaveBeenCalled()
  })

  it('resolveWithHub replaces a closed sandbox hub', async () => {
    const savedEnv = process.env['DAYTONA_SANDBOX_EVENTS']
    process.env['DAYTONA_SANDBOX_EVENTS'] = '1'
    const first = new Daytona({ apiKey: 'key-1' })
    const second = new Daytona({ apiKey: 'key-2' })
    const firstApi = clientInstance(first)
    const firstHub = new WaitHub('http://api.invalid', 'key-1', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, firstApi)
    const secondHub = new WaitHub('http://api.invalid', 'key-2', { enabled: true, reconcileIntervalMs: 0 }, async () => { throw new Error('unused') }, clientInstance(second))
    const waitSecond = jest.spyOn(secondHub, 'wait').mockResolvedValue({ state: 'started', rawState: 'running' })
    const sandbox = new Sandbox(sandboxDetail({ id: 'sbx', status: 'stopped' }) as unknown as SandboxDto, firstApi, 'http://toolbox/{sandboxId}', 'key-1', undefined, {}, undefined, undefined, firstHub)
    ;(second as unknown as { hub?: WaitHub }).hub = secondHub
    await firstHub.detach()
    jest.spyOn(firstApi, 'get').mockResolvedValue({ data: sandboxDetail({ id: 'sbx', status: 'stopped' }) })
    jest.spyOn(firstApi, 'post').mockResolvedValue({ data: {} })

    try {
      await expect(second.start(sandbox)).resolves.toBeUndefined()
      expect(waitSecond).toHaveBeenCalledTimes(1)
      expect((sandbox as unknown as { _hub?: unknown })._hub).toBe(secondHub)
    } finally {
      if (savedEnv === undefined) delete process.env['DAYTONA_SANDBOX_EVENTS']
      else process.env['DAYTONA_SANDBOX_EVENTS'] = savedEnv
    }
  })

  it('sends a caller-supplied idempotencyKey verbatim and mints one when omitted', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })

    await createFixture(client, { image: Image.base('python:3.12'), idempotencyKey: 'run-42/attempt-1' })
    await createFixture(client, { image: Image.base('python:3.12') })
    await createFixture(client, { image: Image.base('python:3.12') })

    expect(post.mock.calls[0]?.[2]).toMatchObject({ headers: { 'Idempotency-Key': 'run-42/attempt-1' } })
    expect(post.mock.calls[0]?.[1]).toMatchObject({ idempotency_key: 'run-42/attempt-1' })
    const minted = post.mock.calls.slice(1).map(([, , config]) => config?.headers?.['Idempotency-Key'])
    expect(minted[0]).toEqual(expect.any(String))
    expect(minted[0]).not.toBe(minted[1])
  })

  it('forwards the idempotencyKey verbatim and only type-checks it', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'sbx' } })
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { id: 'sbx', status: 'running' } })

    await expect(createFixture(client, { image: Image.base('python:3.12'), idempotencyKey: 42 as unknown as string })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    expect(post).not.toHaveBeenCalled()

    // Length, blank and character rules are the server's (400), not the SDK's:
    // the key is the create's identity and goes out exactly as given.
    await createFixture(client, { image: Image.base('python:3.12'), idempotencyKey: '  k'.repeat(100) })
    expect(post.mock.calls[0]?.[2]).toMatchObject({ headers: { 'Idempotency-Key': '  k'.repeat(100) } })
  })

  // The create POST timing out is driven through a real server in
  // tests/client-config.test.ts.
  it('surfaces the idempotency key when the create POST loses the connection', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockRejectedValueOnce(new ConnectionError('socket hang up'))
    const get = jest.spyOn(api, 'get')

    const dropped = await createFixture(client, { image: Image.base('python:3.12') }).catch((error: unknown) => error)
    expect(dropped).toBeInstanceOf(SandboxCreateConnectionError)
    expect(dropped).toBeInstanceOf(ConnectionError)
    const mintedKey = (post.mock.calls[0]?.[2] as { headers: Record<string, string> }).headers['Idempotency-Key']
    expect((dropped as SandboxCreateConnectionError).idempotencyKey).toBe(mintedKey)
    expect((dropped as Error).message).toContain('socket hang up')

    // Server-side errors on the POST are not "unknown outcome" and pass through untouched.
    post.mockRejectedValueOnce(new BadRequestError('bad image', { statusCode: 400, code: 'BAD_REQUEST' }))
    await expect(createFixture(client, { image: Image.base('python:3.12') })).rejects.toBeInstanceOf(BadRequestError)
    expect(get).not.toHaveBeenCalled()
  })

  it('replays the same sandbox when create is retried with the surfaced idempotency key', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post')
      .mockRejectedValueOnce(new ClientTimeoutError())
      .mockResolvedValueOnce({ data: { id: 'sbx-first', access_token: null } })
    jest.spyOn(api, 'get').mockResolvedValue({ data: { id: 'sbx-first', status: 'running' } })

    const error = await createFixture(client, { image: Image.base('python:3.12') }).catch((e: unknown) => e as SandboxCreateRequestTimeoutError)
    expect(error).toBeInstanceOf(SandboxCreateRequestTimeoutError)
    const sandbox = await createFixture(client, { image: Image.base('python:3.12'), idempotencyKey: (error as SandboxCreateRequestTimeoutError).idempotencyKey })

    expect(sandbox.id).toBe('sbx-first')
    expect(post.mock.calls[1]?.[1]).toEqual(post.mock.calls[0]?.[1])
    expect(post.mock.calls[0]?.[2]).toMatchObject({ headers: { 'Idempotency-Key': (error as SandboxCreateRequestTimeoutError).idempotencyKey } })
    expect(post.mock.calls[1]?.[2]).toMatchObject({ headers: { 'Idempotency-Key': (error as SandboxCreateRequestTimeoutError).idempotencyKey } })
  })

  it('strictly percent-encodes process ids in every request path (byte parity with Python quote(safe=""))', async () => {
    const raw = "my/proc?x#y!'()*"
    const encoded = 'my%2Fproc%3Fx%23y%21%27%28%29%2A'
    const transport = transportStub()
    transport.get
      .mockResolvedValueOnce({ id: raw, kind: 'exec', state: 'running' })
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [], nextCursor: undefined })
    const service = new ProcessService(transport.object, 'sbx')
    const handle = service.connect(raw)
    await handle.get()
    await handle.wait({ timeout: 1 })
    await handle.logs()
    await handle.writeStdin('x')
    await handle.kill()
    await handle.delete()
    const paths = [...transport.get.mock.calls, ...transport.post.mock.calls, ...transport.delete.mock.calls].map((call) => String(call[0]))
    expect(paths.every((path) => path.startsWith(`/processes/${encoded}`))).toBe(true)
    expect(paths.some((path) => path.includes(raw))).toBe(false)
    expect(paths).toHaveLength(6)
  })

  it('process.run refuses to create PTYs and code processes refuse user', async () => {
    const transport = transportStub()
    const service = new ProcessService(transport.object, 'sbx')
    await expect(service.run('sh', { kind: 'pty' } as never)).rejects.toMatchObject({ code: 'UNSUPPORTED_OPERATION', source: 'SDK' })
    await expect(startProcess(transport.object, 'sbx', { code: 'print(1)', kind: 'code', language: 'python', user: 'root' })).rejects.toMatchObject({ code: 'UNSUPPORTED_OPERATION', source: 'SDK' })
    expect(transport.post).not.toHaveBeenCalled()
  })

  it('normalises executionTimeout to integer millis and rejects invalid values', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'p', kind: 'exec', state: 'running' })
    await startProcess(transport.object, 'sbx', { cmd: 'true', executionTimeout: 1500.4 })
    expect(transport.post.mock.calls[0]?.[1]).toMatchObject({ timeoutMs: 1501 })
    await expect(startProcess(transport.object, 'sbx', { cmd: 'true', executionTimeout: -1 })).rejects.toMatchObject({ code: 'BAD_REQUEST' })
    await expect(startProcess(transport.object, 'sbx', { cmd: 'true', executionTimeout: Number.NaN })).rejects.toMatchObject({ code: 'BAD_REQUEST' })
  })

  it('bounds the log snapshot when run() reports timed_out on a still-running process', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'proc', kind: 'exec', state: 'running' })
    transport.get.mockImplementation(async (path: string) => {
      if (path.includes('/wait')) {
        jest.advanceTimersByTime(10)
        return { reason: 'timed_out' }
      }
      const cursor = Number(new URL(`http://x${path}`).searchParams.get('cursor') ?? '0')
      return { frames: [{ cursor: String(cursor), channel: 'stdout', data: `p${cursor}`, timestamp: 1 }], nextCursor: String(cursor + 1) }
    })
    const result = await new ProcessService(transport.object, 'sbx').run('yes', { timeout: 10 })
    expect(result.reason).toBe('timed_out')
    const logCalls = transport.get.mock.calls.filter((call) => String(call[0]).includes('/logs'))
    expect(logCalls).toHaveLength(TIMED_OUT_LOG_PAGE_LIMIT)
    expect(result.stdout).toBe(Array.from({ length: TIMED_OUT_LOG_PAGE_LIMIT }, (_, i) => `p${i}`).join(''))
  })

  it('pty.delete refuses non-pty processes before sending any signal', async () => {
    const transport = transportStub()
    transport.get.mockResolvedValueOnce({ id: 'p1', kind: 'exec', state: 'running' })
    const pty = new PtyService(new ProcessService(transport.object, 'sbx'))
    await expect(pty.delete('p1')).rejects.toMatchObject({ code: 'UNSUPPORTED_OPERATION', source: 'SDK' })
    expect(transport.post).not.toHaveBeenCalled()
    expect(transport.delete).not.toHaveBeenCalled()
  })

  it('serialises fractional wait budgets as integer milliseconds on the wire', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'proc', kind: 'exec', state: 'running' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [], nextCursor: undefined })
    const process = new ProcessService(transport.object, 'sbx')
    await process.run('true', { timeout: 1500.4 })
    expect(transport.post.mock.calls[0]?.[0]).toBe('/processes?waitMs=1501&logs=text')
  })

  it('boundedRequestTimeout never yields the unbounded sentinel from arithmetic', () => {
    const now = FIXED_TEST_EPOCH_MS
    expect(boundedRequestTimeout(undefined, 30_000)).toBe(0) // no deadline → caller's unbounded
    expect(boundedRequestTimeout(now, 30_000)).toBeUndefined() // spent exactly → caller must raise
    expect(boundedRequestTimeout(now - 1, 30_000)).toBeUndefined() // spent → caller must raise
    expect(boundedRequestTimeout(now + 5_000, 30_000)).toBe(5_000) // remainder below the cap
    expect(boundedRequestTimeout(now + 100_000, 30_000)).toBe(30_000) // capped by requestTimeout
    expect(boundedRequestTimeout(now + 100_000, 0)).toBe(100_000) // requestTimeout 0 = no cap
    expect(boundedRequestTimeout(now + 100_000, undefined)).toBe(100_000)
    expect(boundedRequestTimeout(now + 0.4, 30_000)).toBe(1) // fractional remainder rounds UP, never to 0
  })

  it('keeps process.run timeout=0 as an unbounded caller wait', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'proc', kind: 'exec', state: 'running' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [{ cursor: '1', channel: 'stdout', data: 'out' }], nextCursor: '1' })
      .mockResolvedValueOnce({ frames: [], nextCursor: '1' })
    const process = new ProcessService(transport.object, 'sbx')

    await expect(process.run('echo hi', { timeout: 0 })).resolves.toMatchObject({ reason: 'exited', stdout: 'out' })
    expect(transport.post.mock.calls[0]?.[0]).toBe('/processes?waitMs=0&logs=text')
    expect(transport.post.mock.calls[0]?.[1]).toMatchObject({ shellCommand: 'echo hi', keepLogs: 'on_exit_ttl' })
    expect(transport.post.mock.calls[0]?.[2]).toEqual({ timeout: 0 })
    expect(transport.get.mock.calls[0]).toEqual(['/processes/proc/wait?timeoutMs=0', { timeout: 0 }])
  })

  it('keeps process.run without a timeout unbounded across the awaited exec and terminal wait', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValue({ id: 'proc', kind: 'exec', state: 'running' })
    transport.get
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 0 })
      .mockResolvedValueOnce({ frames: [{ cursor: '1', channel: 'stdout', data: 'out' }], nextCursor: '1' })
      .mockResolvedValueOnce({ frames: [], nextCursor: '1' })
    const process = new ProcessService(transport.object, 'sbx')

    await expect(process.run('echo hi')).resolves.toMatchObject({ reason: 'exited', stdout: 'out' })
    expect(transport.post.mock.calls[0]?.[0]).toBe('/processes?waitMs=0&logs=text')
    expect(transport.post.mock.calls[0]?.[2]).toEqual({ timeout: 0 })
    expect(transport.get.mock.calls[0]).toEqual(['/processes/proc/wait?timeoutMs=0', { timeout: 0 }])
  })

  it('sizes waitForState long-poll slices by the caller budget, not the global requestTimeout', async () => {
    // A long-poll must be allowed to outlive the short per-request default:
    // the slice is min(30s, remaining caller budget) and the HTTP timeout
    // matches the slice, in both SDKs.
    const client = new Daytona({ apiKey: 'key', requestTimeoutSeconds: 0.123 })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'booting' }, api, 'http://toolbox/{sandboxId}', 'key', undefined, {}, 123)
    const get = jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'sbx', status: 'running' } })

    await sandbox.waitForState(['started'], { timeout: 500 })

    expect(get).toHaveBeenCalledWith('/sandboxes/sbx', { timeout: 5_500, params: { wait: 'running', timeout_ms: 500 } })
  })
})

describe('typed error mapping', () => {
  it('preserves rawState on SandboxTerminalStateError', () => {
    const error = new SandboxTerminalStateError('sbx', 'destroyed', { rawState: 'deleted' })
    expect(error.rawState).toBe('deleted')
  })

  it('maps the size-limit codes to typed conflict errors carrying the byte limits', () => {
    const image = createDaytonaError('image exceeds the maximum size of 50 GiB', 409, 'IMAGE_TOO_LARGE', 'DAYTONA_API', errorOptionsFromEnvelope({
      code: 'IMAGE_TOO_LARGE',
      max_bytes: 53687091200,
      bytes: 60000000000,
    }))
    expect(image).toBeInstanceOf(publicSdk.ImageTooLargeError)
    expect(image).toBeInstanceOf(publicSdk.ConflictError)
    expect((image as InstanceType<typeof publicSdk.ImageTooLargeError>).maxBytes).toBe(53687091200)
    expect((image as InstanceType<typeof publicSdk.ImageTooLargeError>).bytes).toBe(60000000000)

    // `bytes` is optional on the wire: an absent or malformed value is undefined, never a guess.
    const snapshot = createDaytonaError('snapshot exceeds the maximum size of 8 GiB', 409, 'SNAPSHOT_TOO_LARGE', 'DAYTONA_API', errorOptionsFromEnvelope({
      code: 'SNAPSHOT_TOO_LARGE',
      max_bytes: 8589934592,
      bytes: 'big',
    }))
    expect(snapshot).toBeInstanceOf(publicSdk.SnapshotTooLargeError)
    expect(snapshot).toBeInstanceOf(publicSdk.ConflictError)
    expect((snapshot as InstanceType<typeof publicSdk.SnapshotTooLargeError>).maxBytes).toBe(8589934592)
    expect((snapshot as InstanceType<typeof publicSdk.SnapshotTooLargeError>).bytes).toBeUndefined()
  })

  it('maps every known code with code and source preserved', () => {
    for (const code of [...DAEMON_ERROR_CODES, ...GENERIC_ERROR_CODES] as ErrorCode[]) {
      const err = createDaytonaError(`message ${code}`, statusForCode(code), code, 'DAYTONA_DAEMON', { firstAvailableCursor: 'c' })
      expect(err).toBeInstanceOf(CODE_ERROR_CLASS[code])
      expect(err.code).toBe(code)
      expect(err.source).toBe('DAYTONA_DAEMON')
      expect(err.firstAvailableCursor).toBe('c')
    }
  })

  it('maps status fallback, axios envelopes, fetch envelopes, and transport timeouts', async () => {
    expect(createDaytonaError(['bad', 'input'] as never, 400).message).toBe('bad,input')
    expect(errorClassFromStatusCode(undefined).name).toBe('DaytonaError')
    expect(errorClassFromStatusCode(418).name).toBe('DaytonaError')
    expect(errorClassFromStatusCode(413).name).toBe('PayloadTooLargeError')
    expect(errorClassFromStatusCode(415).name).toBe('UnsupportedMediaTypeError')
    expect(errorClassFromStatusCode(422).name).toBe('UnprocessableEntityError')
    expect(errorClassFromStatusCode(501).name).toBe('NotSupportedError')
    expect(errorClassFromStatusCode(502).name).toBe('InternalServerError')
    expect(createDaytonaError('not supported', 501)).toBeInstanceOf(InternalServerError)
    expect(createDaytonaError('unsupported', 400, 'UNSUPPORTED_OPERATION')).toBeInstanceOf(BadRequestError)

    const axiosErr = createAxiosDaytonaError({
      message: 'Request failed',
      response: { status: 409, data: { message: 'name exists', code: 'NAME_CONFLICT', source: 'DAYTONA_DAEMON' } },
      isAxiosError: true,
      name: 'AxiosError',
      toJSON: () => ({}),
    } as never)
    expect(axiosErr).toBeInstanceOf(NameConflictError)
    expect(axiosErr.message).toBe('name exists')
    expect(createAxiosDaytonaError({ message: 'Request failed', response: { status: 502, data: 'bad gateway' }, isAxiosError: true, name: 'AxiosError', toJSON: () => ({}) } as never).message).toBe('bad gateway')

    expect(createAxiosDaytonaError({ message: 'timeout of 10ms exceeded', code: 'ECONNABORTED', isAxiosError: true, name: 'AxiosError', toJSON: () => ({}) } as never)).toBeInstanceOf(ClientTimeoutError)
    expect(createAxiosDaytonaError({ message: 'socket hang up', code: 'ECONNRESET', request: {}, isAxiosError: true, name: 'AxiosError', toJSON: () => ({}) } as never).name).toBe('ConnectionError')

    await expect(errorFromFetchResponse(new Response(JSON.stringify({ message: 'old cursor', code: 'CURSOR_EXPIRED', source: 'DAYTONA_DAEMON', firstAvailableCursor: 'c1' }), { status: 409 }))).resolves.toBeInstanceOf(CursorExpiredError)
    await expect(errorFromFetchResponse(new Response('plain text failure', { status: 500, statusText: 'Server Error' }))).resolves.toMatchObject({ message: 'plain text failure', statusCode: 500 })
  })

  it('parses trace and per-code extras with message taking precedence over the error field', () => {
    const err = createAxiosDaytonaError({
      message: 'Request failed',
      response: {
        status: 429,
        data: {
          code: 'RATE_LIMITED',
          message: 'retry this request later',
          source: 'DAYTONA_API',
          trace_id: '0af7651916cd43dd8448eb211c80319c',
          retry_after_seconds: 7,
          feature: 'example_feature',
          entitlement: 'example_entitlement',
          replacement: 'new-value',
          firstAvailableCursor: 'cursor-1',
          error: 'secondary_message_must_be_ignored',
        },
      },
      isAxiosError: true,
      name: 'AxiosError',
      toJSON: () => ({}),
    } as never)

    expect(err).toBeInstanceOf(RateLimitedError)
    expect(err).toMatchObject({
      message: 'retry this request later',
      traceId: '0af7651916cd43dd8448eb211c80319c',
      retryAfterSeconds: 7,
      feature: 'example_feature',
      entitlement: 'example_entitlement',
      replacement: 'new-value',
      firstAvailableCursor: 'cursor-1',
    })
  })

  it('surfaces an error-only body as prose on the fetch path without promoting it to a code', async () => {
    const errorOnly = await errorFromFetchResponse(new Response(JSON.stringify({ error: 'coupon not found' }), { status: 404 }))
    expect(errorOnly).toBeInstanceOf(NotFoundError)
    expect(errorOnly).toMatchObject({ message: 'coupon not found', code: undefined })
  })

  it('dispatches by source and code before generic code and status fallbacks', () => {
    expect(createDaytonaError('suspended', 402, 'ORG_SUSPENDED', 'DAYTONA_API')).toBeInstanceOf(OrgSuspendedError)
    const overBudget = createDaytonaError('over budget', 402, 'PROJECT_BUDGET_EXCEEDED', 'DAYTONA_API')
    expect(overBudget).toBeInstanceOf(ProjectBudgetExceededError)
    expect(overBudget).not.toBeInstanceOf(OrgSuspendedError)
    expect(overBudget.statusCode).toBe(402)
    expect(overBudget.code).toBe('PROJECT_BUDGET_EXCEEDED')
    expect(createDaytonaError('disabled', 501, 'BILLING_DISABLED', 'DAYTONA_API')).toBeInstanceOf(BillingDisabledError)
    expect(createDaytonaError('not running', 409, 'SANDBOX_NOT_RUNNING', 'DAYTONA_PROXY')).toBeInstanceOf(SandboxNotRunningError)
    const wrongSource = createDaytonaError('missing', 404, 'PROCESS_NOT_FOUND', 'DAYTONA_API')
    expect(wrongSource).toBeInstanceOf(NotFoundError)
    expect(wrongSource).not.toBeInstanceOf(publicSdk.ProcessNotFoundError)
    expect(createDaytonaError('down', 503, 'SERVICE_UNAVAILABLE', 'DAYTONA_API')).toBeInstanceOf(ServiceUnavailableError)
    const errorOnly = createAxiosDaytonaError({ message: 'Request failed', response: { status: 400, data: { error: 'Search is unavailable' } }, isAxiosError: true, name: 'AxiosError', toJSON: () => ({}) } as never)
    expect(errorOnly).toMatchObject({ message: 'Search is unavailable', code: undefined })
  })

  it('maps DESKTOP_NOT_READY to DesktopNotReadyError as a conflict', () => {
    expect(DAEMON_ERROR_CODES).toContain('DESKTOP_NOT_READY')
    expect(statusForCode('DESKTOP_NOT_READY' as ErrorCode)).toBe(409)

    const err = createAxiosDaytonaError({
      message: 'Request failed',
      response: {
        status: 409,
        data: {
          message: 'desktop is starting; poll GET /computeruse/status',
          source: 'DAYTONA_DAEMON',
          code: 'DESKTOP_NOT_READY',
        },
      },
      isAxiosError: true,
      name: 'AxiosError',
      toJSON: () => ({}),
    } as never)

    expect(err).toBeInstanceOf(DesktopNotReadyError)
    expect(err).toBeInstanceOf(ConflictError)
    expect(err).toMatchObject({
      statusCode: 409,
      code: 'DESKTOP_NOT_READY',
    })
  })

  it('maps an absent addon to AddonUnavailableError as service unavailable', () => {
    expect(DAEMON_ERROR_CODES).toContain('ADDON_UNAVAILABLE')

    const err = createDaytonaError(
      'computer-use addon is not staged in this sandbox',
      503,
      'ADDON_UNAVAILABLE',
      'DAYTONA_DAEMON',
    )

    expect(err).toBeInstanceOf(AddonUnavailableError)
    expect(err).toBeInstanceOf(ServiceUnavailableError)
    expect(err).toMatchObject({
      statusCode: 503,
      code: 'ADDON_UNAVAILABLE',
    })
  })

  it('maps ATOMIC_UPLOAD_UNSUPPORTED to AtomicUploadUnsupportedError as a conflict', async () => {
    expect(DAEMON_ERROR_CODES).toContain('ATOMIC_UPLOAD_UNSUPPORTED')
    expect(statusForCode('ATOMIC_UPLOAD_UNSUPPORTED' as ErrorCode)).toBe(409)

    const err = await errorFromFetchResponse(new Response(JSON.stringify({
      message: 'atomic upload is not supported on this filesystem',
      source: 'DAYTONA_DAEMON',
      code: 'ATOMIC_UPLOAD_UNSUPPORTED',
    }), { status: 409 }))

    expect(err).toBeInstanceOf(AtomicUploadUnsupportedError)
    expect(err).toBeInstanceOf(ConflictError)
    expect(err).toMatchObject({
      statusCode: 409,
      code: 'ATOMIC_UPLOAD_UNSUPPORTED',
    })
  })

  it('maps a LIMIT_EXCEEDED envelope to LimitExceededError with the limit extras', async () => {
    const fetchErr = await errorFromFetchResponse(
      new Response(
        JSON.stringify({ code: 'LIMIT_EXCEEDED', message: 'organization limit reached: 25 of 25 (max_concurrent_sandboxes)', source: 'DAYTONA_API', limit: 'max_concurrent_sandboxes', current: 25, max: 25, trace_id: 'tr-1' }),
        { status: 429 },
      ),
    )
    expect(fetchErr).toBeInstanceOf(LimitExceededError)
    expect(fetchErr).toBeInstanceOf(RateLimitError)
    expect(fetchErr).toMatchObject({ statusCode: 429, code: 'LIMIT_EXCEEDED', source: 'DAYTONA_API', limit: 'max_concurrent_sandboxes', current: 25, max: 25, traceId: 'tr-1' })

    const axiosErr = createAxiosDaytonaError({
      message: 'Request failed with status code 429',
      response: { status: 429, data: { code: 'LIMIT_EXCEEDED', message: 'organization limit reached', source: 'DAYTONA_API', limit: 'max_concurrent_sandboxes', current: 3, max: 3, trace_id: 'tr-2' } },
      isAxiosError: true,
      name: 'AxiosError',
      toJSON: () => ({}),
    } as never)
    expect(axiosErr).toBeInstanceOf(LimitExceededError)
    expect(axiosErr).toMatchObject({ statusCode: 429, code: 'LIMIT_EXCEEDED', message: 'organization limit reached', limit: 'max_concurrent_sandboxes', current: 3, max: 3 })

    // Malformed extras cost the extra alone, never the classification.
    const tolerant = createDaytonaError('organization limit reached', 429, 'LIMIT_EXCEEDED', 'DAYTONA_API', errorOptionsFromEnvelope({ limit: 42, current: '25', max: 25.5 }))
    expect(tolerant).toBeInstanceOf(LimitExceededError)
    expect((tolerant as LimitExceededError).limit).toBeUndefined()
    expect((tolerant as LimitExceededError).current).toBeUndefined()
    expect((tolerant as LimitExceededError).max).toBeUndefined()

    const bareBody = createDaytonaError('organization limit reached', 429, 'LIMIT_EXCEEDED', 'DAYTONA_API')
    expect(bareBody).toBeInstanceOf(LimitExceededError)
    expect((bareBody as LimitExceededError).limit).toBeUndefined()
  })

  it('leaves every other 429 a RateLimitError and keeps dispatch source-scoped', async () => {
    const bare = createDaytonaError('too many requests', 429)
    expect(bare).toBeInstanceOf(RateLimitError)
    expect(bare).not.toBeInstanceOf(LimitExceededError)

    // An envelope-less lowercase prose body is a message
    // fallback only — never promoted to a code, never refined.
    const prose = await errorFromFetchResponse(
      new Response(JSON.stringify({ error: 'Too many requests' }), { status: 429 }),
    )
    expect(prose).toBeInstanceOf(RateLimitError)
    expect(prose).not.toBeInstanceOf(LimitExceededError)
    expect(prose.code).toBeUndefined()
    expect(prose.message).toBe('Too many requests')

    // LIMIT_EXCEEDED is a DAYTONA_API code: another source falls back to status.
    const foreign = createDaytonaError('limit exceeded', 429, 'LIMIT_EXCEEDED', 'DAYTONA_DAEMON')
    expect(foreign).not.toBeInstanceOf(LimitExceededError)
    expect(foreign).toBeInstanceOf(RateLimitError)
    expect(foreign.code).toBe('LIMIT_EXCEEDED')
  })

  it('reports the transport status, never a body-supplied statusCode', async () => {
    // Mirror of test_error_status_code_is_the_transport_status_never_a_body_field
    // in the Python suite: the same (transport status, body) pairs must
    // classify identically in both SDKs.
    const forgedBody = { code: 'LIMIT_EXCEEDED', statusCode: 429, message: 'organization limit reached', source: 'DAYTONA_API', limit: 'max_concurrent_sandboxes', current: 9, max: 9 }

    const forgedFetch = await errorFromFetchResponse(new Response(JSON.stringify(forgedBody), { status: 500 }))
    expect(forgedFetch.code).toBe('LIMIT_EXCEEDED')
    expect(forgedFetch).toMatchObject({ statusCode: 500, message: 'organization limit reached' })

    const plain = await errorFromFetchResponse(
      new Response(JSON.stringify({ code: 'LIMIT_EXCEEDED', message: 'organization limit reached: 25 of 25 (max_concurrent_sandboxes)', source: 'DAYTONA_API', limit: 'max_concurrent_sandboxes', current: 25, max: 25 }), { status: 429 }),
    )
    expect(plain).toBeInstanceOf(LimitExceededError)
    expect(plain).toMatchObject({ statusCode: 429, code: 'LIMIT_EXCEEDED', limit: 'max_concurrent_sandboxes', current: 25, max: 25 })
  })
})

// The generic codes each source's catalog publishes, pinned per source: the
// shared GENERIC_ERROR_CODES is the union, so a membership check against it
// cannot catch a source DROPPING a generic code it used to emit.
const DAEMON_GENERIC_CODES = [
  'BAD_GATEWAY',
  'BAD_REQUEST',
  'CONFLICT',
  'FORBIDDEN',
  'GONE',
  'INTERNAL_SERVER_ERROR',
  'METHOD_NOT_ALLOWED',
  'NOT_FOUND',
  'PAYLOAD_TOO_LARGE',
  'REQUEST_TIMEOUT',
  'SERVICE_UNAVAILABLE',
  'TOO_MANY_REQUESTS',
  'UNAUTHORIZED',
  'UNPROCESSABLE_ENTITY',
  'UNSUPPORTED_MEDIA_TYPE',
] as const

const PROXY_GENERIC_CODES = [
  'BAD_GATEWAY',
  'BAD_REQUEST',
  'CONFLICT',
  'FORBIDDEN',
  'GATEWAY_TIMEOUT',
  'INTERNAL_SERVER_ERROR',
  'METHOD_NOT_ALLOWED',
  'NOT_FOUND',
  'SERVICE_UNAVAILABLE',
  'UNAUTHORIZED',
] as const

describe('toolbox error-code drift', () => {
  it('matches the toolbox error catalog in the contract artifact', () => {
    // The published artifact under contract/ defines the error vocabulary.
    const path = resolve(__dirname, '../../contract/toolbox-openapi.json')
    const spec = JSON.parse(readFileSync(path, 'utf8')) as {
      components: { schemas: { ErrorCode: { enum: string[] } } }
      'x-daytona-error-codes': Record<string, { status: number; source: string; extras: string[] }>
      'x-daytona-generic-codes': Record<string, { status: number; extras: string[] }>
    }
    const domain = spec['x-daytona-error-codes']
    const generic = spec['x-daytona-generic-codes']
    expect([...spec.components.schemas.ErrorCode.enum].sort()).toEqual([...DAEMON_ERROR_CODES].sort())
    expect(Object.keys(domain).sort()).toEqual([...DAEMON_ERROR_CODES].sort())
    expect(Object.keys(generic).sort()).toEqual([...DAEMON_GENERIC_CODES].sort())
    for (const [code, meta] of [...Object.entries(domain), ...Object.entries(generic)]) {
      expect({ code, status: meta.status }).toEqual({ code, status: CODE_STATUS[code as ErrorCode] })
      expect({ code, extras: meta.extras }).toEqual({ code, extras: [...(DAEMON_CODE_EXTRAS[code as ErrorCode] ?? [])] })
    }
    for (const meta of Object.values(domain)) expect(meta.source).toBe('DAYTONA_DAEMON')
  })

  it('matches the API error catalog in the contract artifact', () => {
    const path = resolve(__dirname, '../../contract/api-openapi.json')
    const spec = JSON.parse(readFileSync(path, 'utf8')) as {
      'x-daytona-error-codes': Record<string, { status: number; source: string; extras: string[] }>
      'x-daytona-generic-codes': Record<string, { status: number; extras: string[] }>
    }
    const domain = spec['x-daytona-error-codes']
    const generic = spec['x-daytona-generic-codes']
    expect(Object.keys(domain).sort()).toEqual([...API_ERROR_CODES].sort())
    // The api's generic table mirrors the full shared set (pinned equality,
    // not membership — see DAEMON_GENERIC_CODES above).
    expect(Object.keys(generic).sort()).toEqual([...GENERIC_ERROR_CODES].sort())
    for (const [code, meta] of [...Object.entries(domain), ...Object.entries(generic)]) {
      expect({ code, status: meta.status }).toEqual({ code, status: CODE_STATUS[code as ErrorCode] })
      expect({ code, extras: meta.extras }).toEqual({ code, extras: [...(API_CODE_EXTRAS[code as ErrorCode] ?? [])] })
    }
    for (const meta of Object.values(domain)) expect(meta.source).toBe('DAYTONA_API')
  })

  it('exposes the per-code extras maps through the codes namespace', () => {
    expect(publicSdk.codes.API_CODE_EXTRAS).toBe(API_CODE_EXTRAS)
    expect(publicSdk.codes.DAEMON_CODE_EXTRAS).toBe(DAEMON_CODE_EXTRAS)
    expect(publicSdk.codes.PROXY_CODE_EXTRAS).toBe(PROXY_CODE_EXTRAS)
    expect(publicSdk.codes.ROUTER_CODE_EXTRAS).toBe(ROUTER_CODE_EXTRAS)
  })

  it('matches the proxy error catalog in the contract artifact', () => {
    const path = resolve(__dirname, '../../contract/proxy-errors.json')
    const spec = JSON.parse(readFileSync(path, 'utf8')) as {
      source: string
      codes: Array<{ code: string; status: number; extras: string[] }>
      generic: Array<{ code: string; status: number; extras: string[] }>
    }
    expect(spec.source).toBe('DAYTONA_PROXY')
    expect(spec.codes.map(({ code }) => code).sort()).toEqual([...PROXY_ERROR_CODES].sort())
    expect(spec.generic.map(({ code }) => code).sort()).toEqual([...PROXY_GENERIC_CODES].sort())
    for (const entry of [...spec.codes, ...spec.generic]) {
      expect({ code: entry.code, status: entry.status }).toEqual({
        code: entry.code,
        status: CODE_STATUS[entry.code as ErrorCode],
      })
      expect({ code: entry.code, extras: entry.extras }).toEqual({
        code: entry.code,
        extras: [...(PROXY_CODE_EXTRAS[entry.code as ErrorCode] ?? [])],
      })
    }
  })

  it('matches the router error catalog in the contract artifact', () => {
    const path = resolve(__dirname, '../../contract/router-errors.json')
    const spec = JSON.parse(readFileSync(path, 'utf8')) as {
      source: string
      codes: Array<{ code: string; status: number; extras: string[] }>
      generic: Array<{ code: string; status: number; extras: string[] }>
    }
    expect(spec.source).toBe('DAYTONA_ROUTER')
    expect(spec.codes.map(({ code }) => code).sort()).toEqual([...ROUTER_ERROR_CODES].sort())
    // The router's generic table mirrors the API's full generic table.
    expect(spec.generic.map(({ code }) => code).sort()).toEqual([...GENERIC_ERROR_CODES].sort())
    for (const entry of [...spec.codes, ...spec.generic]) {
      expect({ code: entry.code, status: entry.status }).toEqual({
        code: entry.code,
        status: CODE_STATUS[entry.code as ErrorCode],
      })
      expect({ code: entry.code, extras: entry.extras }).toEqual({
        code: entry.code,
        extras: [...(ROUTER_CODE_EXTRAS[entry.code as ErrorCode] ?? [])],
      })
    }
  })

  it('dispatches router errors by (source, code)', () => {
    expect(createDaytonaError('two regions', 400, 'REGION_MISMATCH', 'DAYTONA_ROUTER')).toBeInstanceOf(BadRequestError)
    expect(createDaytonaError('region unavailable', 503, 'PLACEMENT_UNAVAILABLE', 'DAYTONA_ROUTER')).toBeInstanceOf(ServiceUnavailableError)
    expect(createDaytonaError('body too large', 413, 'CREATE_BODY_TOO_LARGE', 'DAYTONA_ROUTER')).toBeInstanceOf(PayloadTooLargeError)
  })

  // SDK duration bounds must match the published API contract.
  it('pins the lifecycle bounds to the contract artifact', () => {
    const path = resolve(__dirname, '../../contract/api-openapi.json')
    const spec = JSON.parse(readFileSync(path, 'utf8')) as {
      components: { schemas: { UpdateSandboxLifecyclePolicyRequest: { properties: Record<string, { maximum?: number; description?: string }> } } }
    }
    const props = spec.components.schemas.UpdateSandboxLifecyclePolicyRequest.properties
    expect(props.timeout_seconds.maximum).toBe(LIFECYCLE_MAX_SECS)
    expect(props.idle_expiry_seconds.maximum).toBe(LIFECYCLE_MAX_SECS)

    const floor = /below (\d+) is raised to (\d+)/.exec(props.idle_expiry_seconds.description ?? '')
    expect(floor?.[1]).toBeDefined()
    expect(floor?.[2]).toBe(floor?.[1])
  })

  it('matches the toolbox log-channel vocabulary in the contract artifact', () => {
    const path = resolve(__dirname, '../../contract/toolbox-openapi.json')
    const spec = JSON.parse(readFileSync(path, 'utf8')) as {
      'x-daytona-log-channels': Record<string, { description: string }>
    }
    expect(Object.keys(spec['x-daytona-log-channels']).sort()).toEqual([
      'display',
      'error',
      'execute_result',
      'pty',
      'setup',
      'stderr',
      'stdout',
    ])
  })
})

describe('SDK route drift', () => {
  const operations = [...readContractOperations('api'), ...readContractOperations('toolbox')]

  it('keeps build-log discovery forwarders in the route inventory', () => {
    const routes = collectSdkRouteLiterals().map(use => `${use.method} ${use.path}`)
    expect(routes).toContain('GET /images/{param}/build-logs-url')
    expect(routes).toContain('GET /sandboxes/{param}/build-logs-url')
  })

  it('binds every hard-coded SDK route literal to exactly one OpenAPI operation', () => {
    const mismatches = collectSdkRouteLiterals().flatMap((routeUse) => {
      const candidates = operations.filter((operation) => operation.method === routeUse.method && routeMatchesSpec(routeUse.path, operation.path))
      // A literal route segment takes precedence over a path parameter.
      const exact = candidates.filter((operation) => routeMatchesSpecExactly(routeUse.path, operation.path))
      const matches = exact.length > 0 ? exact : candidates
      if (matches.length === 1) return []
      const nearest = nearestSpecPaths(routeUse.method, routeUse.path, operations)
      return [
        `${routeUse.file}:${routeUse.line} ${routeUse.method} ${routeUse.path} from ${routeUse.raw} matched ${matches.length} operations; nearest: ${nearest.join(', ') || '(none)'}`,
      ]
    })
    if (mismatches.length > 0) {
      throw new Error(`Unbound SDK routes:\n${mismatches.join('\n')}`)
    }
  })

  // Exercise routes assembled through an awaited helper.
  it('resolves routes built from awaited helpers (files/sign, signing-key/rotate)', () => {
    const keys = new Set(collectSdkRouteLiterals().map((use) => `${use.method} ${use.path}`))
    for (const expected of [
      'POST /sandboxes/{param}/files/sign',
      'POST /sandboxes/{param}/signing-key/rotate',
    ]) {
      expect([...keys]).toContain(expected)
    }
  })

  it('covers every toolbox operation or documents why the SDK intentionally skips it', () => {
    const routeUses = collectSdkRouteLiterals()
    const operations = readContractOperations('toolbox')
    const isUsed = (operation: { method: string; path: string }) =>
      routeUses.some((routeUse) => routeUse.method === operation.method && routeMatchesSpec(routeUse.path, operation.path))
    const missing = operations.filter((operation) => !(`${operation.method} ${operation.path}` in SDK_TOOLBOX_ROUTE_ALLOWLIST) && !isUsed(operation))
    if (missing.length > 0) {
      throw new Error(`Toolbox operations missing SDK coverage:\n${missing.map((operation) => `${operation.method} ${operation.path}`).join('\n')}`)
    }
    // An allow-list entry for a route the SDK does call is stale: the list
    // must shrink as coverage grows, or it stops meaning anything.
    const stale = Object.keys(SDK_TOOLBOX_ROUTE_ALLOWLIST).filter((key) => {
      const operation = operations.find((candidate) => `${candidate.method} ${candidate.path}` === key)
      return operation === undefined || isUsed(operation)
    })
    expect(stale).toEqual([])
  })
})

const KNOWN_COMPUTER_USE_DESKTOP_STATES = [
  'not_started',
  'starting',
  'ready',
  'failed',
] as const satisfies readonly ComputerUseDesktopState[]

const KNOWN_COMPUTER_USE_DESKTOP_FAILURE_CODES = [
  'desktop_exited',
  'addon_crashed',
  'start_failed',
  'start_interrupted',
  'session_ended',
  'session_replaced',
  'state_corrupt',
  'state_persistence_failed',
] as const satisfies readonly ComputerUseDesktopFailureCode[]

describe('service response mapping', () => {
  it('keeps explicit nulls and omitted optionals while mapping volume fields', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const payload = { id: 'vol', name: 'vol', type: 'disk', status: 'ready', error: null, used_bytes: null, created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z' }
    jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: payload })
    const volume = await client.volume.get('vol')
    expect(volume).toEqual({ id: 'vol', name: 'vol', type: 'disk', rawType: 'disk', status: 'ready', rawStatus: 'ready', error: null, usedBytes: null, createdAt: '2026-10-14T00:00:00Z', updatedAt: '2026-10-14T00:00:00Z' })
    expect('lastUsedAt' in volume).toBe(false)
  })

  it('maps the image and snapshot update timestamps apart from their create timestamps', async () => {
    const client = new Daytona({ apiKey: 'key' })
    jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: imageResponse({ created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z' }) })
      .mockResolvedValueOnce({ data: snapshotResponse({ created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-08T00:00:00Z' }) })

    const image = await client.images.get('img_123')
    const snapshot = await client.snapshots.get('snap_123')

    expect([image.createdAt, image.updatedAt]).toEqual(['2026-10-01T00:00:00Z', '2026-10-09T00:00:00Z'])
    expect([snapshot.createdAt, snapshot.updatedAt]).toEqual(['2026-10-02T00:00:00Z', '2026-10-08T00:00:00Z'])
  })
})

describe('forward compatibility: a newer server must not break an older SDK', () => {
  it('surfaces unknown enum values verbatim and tolerates unknown fields', async () => {
    const transport = transportStub()
    const dto = {
      id: 'p-new',
      kind: 'exec',
      state: 'hibernating',
      reason: 'preempted',
      createdAt: '2026-08-24T00:00:00Z',
      cwd: '/',
      stdin: 'closed',
      keepLogs: 'until_cleanup',
      truncatedHead: false,
      brandNewField: { nested: [1, 2] },
    }
    transport.get.mockResolvedValue(dto)
    const info = await new ProcessService(transport.object, 'sbx').get('p-new')
    expect(info.kind).toBe('exec')
    expect(info.rawKind).toBe('exec')
    expect(info.state).toBe('unknown')
    expect(info.rawState).toBe('hibernating')
    expect(info.reason).toBe('unknown')
    expect(info.rawReason).toBe('preempted')
    expect(info.stdin).toBe('closed')
    expect(info.rawStdin).toBe('closed')
    expect(info.keepLogs).toBe('until_cleanup')
    expect(info.rawKeepLogs).toBe('until_cleanup')
  })

  it('tolerates process kinds and exit reasons this SDK release does not list', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValueOnce({ id: 'proc', kind: 'service' })
    transport.get
      .mockResolvedValueOnce({ id: 'proc', kind: 'service', state: 'terminal', stdin: 'detached', keepLogs: 'forever' })
      .mockResolvedValueOnce({ reason: 'evicted', exitCode: 3 })
    const process = new ProcessService(transport.object, 'sbx')

    const handle = await process.start('echo hi')
    expect(handle.kind).toBe('exec')
    await expect(handle.get()).resolves.toMatchObject({
      kind: 'unknown',
      rawKind: 'service',
      stdin: 'unknown',
      rawStdin: 'detached',
      keepLogs: 'unknown',
      rawKeepLogs: 'forever',
    })
    await expect(handle.wait()).resolves.toEqual({ reason: 'unknown', rawReason: 'evicted', exitCode: 3, signal: undefined })

    // Generated enums are open at the type level: a value a newer server adds
    // type-checks against today's SDK.
    const architecture: ApiComponents['schemas']['CpuArchitecture'] = 'orbital'
    const code: ToolboxComponents['schemas']['ErrorCode'] = 'NEW_DAEMON_CODE'
    const known: ApiComponents['schemas']['CpuArchitecture'] = 'amd64'
    expect([architecture, code, known]).toEqual(['orbital', 'NEW_DAEMON_CODE', 'amd64'])
  })

  it('keeps public server vocabularies closed with an explicit unknown branch', () => {
    const info: Pick<ProcessDto, 'state' | 'rawState'> = { state: 'unknown', rawState: 'future_state' }
    // @ts-expect-error public output types reject typos and unlisted literals
    const impossibleComparison = info.state === 'started'
    void impossibleComparison

    const state: ProcessState = info.state
    const seen = (() => {
      switch (state) {
        case 'starting':
        case 'provisioning':
        case 'running':
        case 'terminal':
        case 'unknown':
          return state
        default: {
          const _: never = state
          return _
        }
      }
    })()

    expect(seen).toBe('unknown')
  })
})

describe('computeruse vocabulary drift', () => {
  // The desktop lifetime vocabulary comes from the published toolbox contract.
  it('matches the desktop state and failure-code enums in the contract artifact', () => {
    const path = resolve(__dirname, '../../contract/toolbox-openapi.json')
    const spec = JSON.parse(readFileSync(path, 'utf8')) as {
      components: { schemas: Record<string, { enum?: string[] }> }
    }
    const states = spec.components.schemas['ComputerUseDesktopState']?.enum
    const failureCodes = spec.components.schemas['ComputerUseDesktopFailureCode']?.enum
    if (!states || !failureCodes) throw new Error('contract artifact lacks the computer-use desktop enums')

    expect([...new Set(states)].sort()).toEqual([...KNOWN_COMPUTER_USE_DESKTOP_STATES].sort())
    expect([...new Set(failureCodes)].sort()).toEqual([...KNOWN_COMPUTER_USE_DESKTOP_FAILURE_CODES].sort())
  })
})

function rawAxiosError(status: number, body: Record<string, unknown>, config: InternalAxiosRequestConfig) {
  return new AxiosError(
    `Request failed with status code ${status}`,
    undefined,
    config,
    undefined,
    { data: body, status, statusText: String(status), headers: {}, config },
  )
}

describe('sandbox search', () => {
  function searchClient(): { client: Daytona; api: AxiosInstance } {
    const client = new Daytona({ apiKey: 'key' })
    return { client, api: clientInstance(client) }
  }

  function axios4xx(status: number, body: Record<string, unknown>) {
    return createAxiosDaytonaError({
      isAxiosError: true,
      message: `Request failed with status code ${status}`,
      name: 'AxiosError',
      response: { status, data: body },
    } as unknown as import('axios').AxiosError)
  }

  it('encodes every search option on the query string', async () => {
    const { client, api } = searchClient()
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { sandboxes: [], next_cursor: null, total: 0, total_relation: 'eq' } })
    await client.search({
      q: 'build',
      namePrefix: 'ci-',
      idPrefix: 'sbx-',
      status: ['running', 'stopped'],
      sandboxClass: ['linux', 'macos'],
      region: ['region-a', 'region-b'],
      imageRef: 'python:3.12',
      imageRefPrefix: 'python:',
      labels: { team: 'ml', tier: 'gold' },
      spot: false,
      forkedFrom: 'sbx-parent',
      minCores: 0.5,
      maxCores: 8,
      minMemoryMib: 512,
      maxMemoryMib: 16384,
      minDiskMib: 1024,
      maxDiskMib: 65536,
      createdStartAt: new Date('2026-01-01T00:00:00Z'),
      createdEndAt: '2026-02-01T00:00:00Z',
      updatedStartAt: new Date('2026-01-02T03:04:05.000Z'),
      updatedEndAt: new Date('2026-01-03T01:00:00+01:00'),
      lastActivityStartAt: '2026-01-04T00:00:00Z',
      lastActivityEndAt: '2026-01-05T00:00:00Z',
      sort: 'memory_mib',
      order: 'asc',
      limit: 25,
      cursor: 'abc',
    })
    expect(get).toHaveBeenCalledTimes(1)
    expect(get.mock.calls[0]?.[0]).toBe('/sandboxes/search')
    expect(get.mock.calls[0]?.[1]).toEqual({
      params: {
        q: 'build',
        name_prefix: 'ci-',
        id_prefix: 'sbx-',
        status: 'running,stopped',
      class: 'linux,macos',
      region_ids: 'region-a,region-b',
        image_ref: 'python:3.12',
        image_ref_prefix: 'python:',
        labels: '{"team":"ml","tier":"gold"}',
        spot: 'false',
        forked_from: 'sbx-parent',
        min_cores: 0.5,
        max_cores: 8,
        min_memory_mib: 512,
        max_memory_mib: 16384,
        min_disk_mib: 1024,
        max_disk_mib: 65536,
        created_start_at: '2026-01-01T00:00:00.000Z',
        created_end_at: '2026-02-01T00:00:00Z',
        updated_start_at: '2026-01-02T03:04:05.000Z',
        updated_end_at: '2026-01-03T00:00:00.000Z',
        last_activity_start_at: '2026-01-04T00:00:00Z',
        last_activity_end_at: '2026-01-05T00:00:00Z',
        sort: 'memory_mib',
        order: 'asc',
        limit: 25,
        cursor: 'abc',
      },
    })
  })

  it('omits unset options and rejects comma-bearing list entries', () => {
    expect(searchQueryParams({})).toEqual({})
    expect(searchQueryParams({ status: ['running'], limit: 5 })).toEqual({ status: 'running', limit: 5 })
    expect(searchTimestamp('2026-01-01T00:00:00+02:00')).toBe('2026-01-01T00:00:00+02:00')
    expect(() => searchQueryParams({ region: ['a,b'] })).toThrow(BadRequestError)
    // An empty list / object is not "no filter": the server would
    // read the empty parameter as absent and answer an unfiltered search.
    expect(() => searchQueryParams({ status: [] })).toThrow('status must name at least one value')
    expect(() => searchQueryParams({ labels: {} })).toThrow('labels must name at least one pair')
    // JS `null` is absent, like Python's `None` — never a raw TypeError.
    expect(hasSearchFilters({ status: null as unknown as undefined })).toBe(false)
    expect(() => searchQueryParams({ status: null as unknown as undefined, spot: true })).not.toThrow()
    expect(hasSearchFilters({})).toBe(false)
    expect(hasSearchFilters({ spot: false })).toBe(true)
  expect(hasSearchFilters({ cursor: 'c', limit: 1, id: 'x', name: 'y' } as import('../src/v2/types').ListSandboxesOptions)).toBe(false)
  })

  it('parses search results and tolerates unknown response fields', async () => {
    const { client, api } = searchClient()
    jest.spyOn(api, 'get').mockResolvedValue({
      data: {
        sandboxes: [{
          id: 'sbx-1',
          name: 'ci-1',
          status: 'running',
          toolbox_proxy_url: 'https://proxy.example/sandbox-api',
        region_id: 'region-a',
          updated_at: '2026-01-01T00:00:00Z',
          last_activity_at: null,
          started_at: '2026-01-01T00:00:00Z',
          stopped_at: null,
          forked_from: null,
          cursor: 'page-cursor',
          unknown_field: { nested: true },
        }],
        next_cursor: 'page-2',
        total: 10000,
        total_relation: 'gte',
        future_top_level: 1,
      },
    })
    const page: SearchResult<Sandbox> = await client.search({ status: ['running'] })
    expect(page.items.map((sandbox) => sandbox.id)).toEqual(['sbx-1'])
    expect(page.items[0]?.name).toBe('ci-1')
    expect(page.items[0]?.state).toBe('started')
    expect(page.items[0]?.target).toBe('region-a')
    expect(page.nextCursor).toBe('page-2')
    expect(page.total).toBe(10000)
    expect(page.totalRelation).toBe('gte')
  })

  it('defaults the summary fields when the body omits them', async () => {
    const { client, api } = searchClient()
    jest.spyOn(api, 'get').mockResolvedValue({ data: { sandboxes: [{ id: 'sbx', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }], next_cursor: null } })
    const page = await client.search()
    expect(page.nextCursor).toBeUndefined()
    expect(page.total).toBe(0)
    expect(page.totalRelation).toBe('eq')
  })

  // Response-shape parity fixture shared with the Python SDK:
  // the search contract is a `sandboxes` array, so an `items` array, a non-array
  // `sandboxes`, a missing `sandboxes`, a bare array body and a non-object item are the
  // SDK's own BadRequestError (never an empty page); unknown extra fields are
  // still tolerated.
  it('rejects a search body without a sandboxes array exactly like the Python SDK', async () => {
    const { client, api } = searchClient()
    const get = jest.spyOn(api, 'get')
    for (const data of [{ items: [{ id: 'sbx', status: 'running' }] }, { sandboxes: 'sbx' }, { sandboxes: null }, {}, { sandboxes: { id: 'sbx' } }]) {
      get.mockResolvedValue({ data })
      await expect(client.search({ q: 'build' })).rejects.toThrow('search response is missing the sandboxes array')
      await expect(client.search({ q: 'build' })).rejects.toBeInstanceOf(BadRequestError)
      // `list()` with a search filter shares the parser and the strictness.
      await expect(client.list({ status: ['running'] })).rejects.toBeInstanceOf(BadRequestError)
    }
    for (const data of [[{ id: 'sbx' }], 'sandboxes', null, { sandboxes: [{ id: 'sbx' }, 'sbx-2'] }]) {
      get.mockResolvedValue({ data })
      await expect(client.search()).rejects.toThrow('expected JSON object')
      await expect(client.search()).rejects.toBeInstanceOf(BadRequestError)
    }
    const error = await client.search().catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(BadRequestError)
    expect((error as BadRequestError).source).toBe('SDK')

    get.mockResolvedValue({ data: { sandboxes: [{ id: 'sbx', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api', unknown_field: 1 }], future_top_level: true } })
    expect((await client.search({ q: 'build' })).items.map((sandbox) => sandbox.id)).toEqual(['sbx'])
    // The cursor is the contract's snake_case `next_cursor`; a non-string is no cursor.
    get.mockResolvedValue({ data: { sandboxes: [], next_cursor: 'page-2' } })
    expect((await client.search()).nextCursor).toBe('page-2')
    get.mockResolvedValue({ data: { sandboxes: [], next_cursor: 7 } })
    expect((await client.search()).nextCursor).toBeUndefined()
  })

  // Timestamp-bound parity fixture — the SAME values are pinned in
  // sdk-python/tests/test_core.py::test_search_timestamp_rejects_non_timestamp_values_matches_typescript:
  // a non-Date/non-string bound is the SDK's own BadRequestError and no request
  // goes out. An invalid `Date` is TS-only (a Python `datetime` cannot be NaN):
  // it must surface as BadRequestError, never the `RangeError` from `toISOString()`.
  it('rejects an invalid Date and non-timestamp bounds with BadRequestError, never RangeError', async () => {
    const { client, api } = searchClient()
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { sandboxes: [] } })
    const message = 'timestamp bounds must be a valid Date or an RFC 3339 string'
    for (const value of [new Date('not a date'), new Date(NaN), 1700000000, 1700000000.5, ['2026-01-01T00:00:00Z'], { iso: '2026-01-01T00:00:00Z' }, true]) {
      const bound = value as unknown as Date
      expect(() => searchTimestamp(bound)).not.toThrow(RangeError)
      expect(() => searchTimestamp(bound)).toThrow(BadRequestError)
      expect(() => searchTimestamp(bound)).toThrow(message)
      expect(() => searchQueryParams({ createdStartAt: bound })).toThrow(BadRequestError)
      const error = await client.search({ lastActivityEndAt: bound }).catch((caught: unknown) => caught)
      expect(error).toBeInstanceOf(BadRequestError)
      expect((error as BadRequestError).message).toBe(message)
      expect((error as BadRequestError).source).toBe('SDK')
    }
    expect(get).not.toHaveBeenCalled()
    // Valid bounds still go out: a Date is normalized to UTC `Z`.
    await client.search({ createdStartAt: new Date('2026-01-01T02:00:00+02:00'), createdEndAt: '2026-02-01T00:00:00Z' })
    expect(get).toHaveBeenCalledTimes(1)
    expect(get.mock.calls[0]?.[1]).toEqual({ params: { created_start_at: '2026-01-01T00:00:00.000Z', created_end_at: '2026-02-01T00:00:00Z' } })
  })

  it('types the search-item timestamps as the contract emits them', async () => {
    // Compile-time pin: `created_at` is always
    // set on a search item, `updated_at` and the other event timestamps and
    // `forked_from` are nullable. This assignment fails `npm run typecheck` if
    // SandboxDto drifts back to a non-null `updated_at` or loses `created_at`.
    const item: SandboxDto = {
      id: 'sbx-1',
      status: 'running',
      toolbox_proxy_url: 'https://proxy.example/sandbox-api',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: null,
      last_activity_at: null,
      started_at: null,
      stopped_at: null,
      forked_from: null,
      cursor: 'page-cursor',
    }
    const { client, api } = searchClient()
    jest.spyOn(api, 'get').mockResolvedValue({ data: { sandboxes: [item] } })
    const page = await client.search()
    expect(page.items[0]?.id).toBe('sbx-1')
    expect(page.items[0]?.state).toBe('started')
  })

  // Input-boundary parity fixture — the SAME values are pinned in
  // sdk-python/tests/test_core.py::test_search_input_boundary_matches_typescript:
  // (a) a bare string for a list filter is one entry, (b) a non-object
  // `labels` and a non-string list entry are the SDK's own BadRequestError with
  // no request sent, (c) unknown response fields are tolerated.
  it('handles input boundaries and unknown response fields like the Python SDK', async () => {
    const { client, api } = searchClient()
    const get = jest.spyOn(api, 'get').mockResolvedValue({
      data: { sandboxes: [], next_cursor: null, total: 0, total_relation: 'eq', future_server_field: { version: 2 } },
    })

    // (a) a single value is one entry — accepted by the types, not only at runtime.
    expect(searchQueryParams({ status: 'running', sandboxClass: 'linux', region: 'local' })).toEqual({ status: 'running', class: 'linux', region_ids: 'local' })
    expect(searchQueryParams({ region: ['region-a', 'region-b'] })).toEqual({ region_ids: 'region-a,region-b' })

    // (b) client-side rejections, none of them reach the transport.
    for (const labels of ['team=ml', ['team=ml'], 42]) {
      const options = { labels: labels as unknown as Record<string, string> }
      expect(() => searchQueryParams(options)).toThrow(BadRequestError)
      expect(() => searchQueryParams(options)).toThrow('labels must be an object')
      await expect(client.search(options)).rejects.toBeInstanceOf(BadRequestError)
    }
    // JS `null` is absent (Python's `None`): no parameter, no request-time error.
    expect(searchQueryParams({ labels: null as unknown as Record<string, string> })).toEqual({})
    for (const status of [['running', 7], ['running', ''], ['a,b']]) {
      // Deliberately invalid entries a JS caller could pass; the cast only exists to reach the runtime guard.
      const options = { status: status as unknown as readonly string[] }
      expect(() => searchQueryParams(options)).toThrow(BadRequestError)
      expect(() => searchQueryParams(options)).toThrow('status entries must be non-empty strings without commas')
      await expect(client.search(options)).rejects.toBeInstanceOf(BadRequestError)
    }
    expect(get).not.toHaveBeenCalled()

    const page = await client.search({ status: 'running' })
    expect(get.mock.calls[0]?.[1]).toEqual({ params: { status: 'running' } })
    expect(page.total).toBe(0)
  })

  it('accepts a single value or a list for status/sandboxClass/region in the published types', async () => {
    // The fixture module is a strict consumer: it compiles under
    // tsconfig.tests.json (`npm run typecheck`) and ts-jest without a single
    // cast, so `{ status: 'running' }` can never regress to a TS2322 again.
    const { client, api } = searchClient()
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { sandboxes: [], total: 0 } })
    await searchConsumerCalls(client)
    expect(get.mock.calls.map((call) => call[1])).toEqual([
      { params: { status: 'running', class: 'linux', region_ids: 'local' } },
      { params: { status: 'running,stopped' } },
      { params: { class: 'linux,macos', region_ids: 'region-a,region-b' } },
    ])
    expect(searchQueryParams(SINGLE_VALUE_SEARCH)).toEqual({ status: 'running', class: 'linux', region_ids: 'local' })
    expect(searchQueryParams(LIST_VALUE_LIST)).toEqual({ status: 'running,stopped' })
    expect(hasSearchFilters(LIST_VALUE_LIST)).toBe(true)
  })

  it('routes list() with any search filter to a single /sandboxes/search call and never walks /sandboxes', async () => {
    const { client, api } = searchClient()
    const get = jest.spyOn(api, 'get').mockImplementation(async (path: string) => {
      if (path !== '/sandboxes/search') throw new Error(`unexpected GET ${path}`)
      return {
        data: {
          sandboxes: [{ id: 'sbx-a', name: 'ci-a', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }],
          next_cursor: 'more',
          total: 42,
          total_relation: 'eq',
        },
      }
    })
    const page = await client.list({ id: 'sbx', name: 'ci', limit: 5, status: ['running'], labels: { team: 'ml' } })
    expect(get).toHaveBeenCalledTimes(1)
    expect(get.mock.calls[0]?.[0]).toBe('/sandboxes/search')
    expect(get.mock.calls[0]?.[1]).toEqual({ params: { id_prefix: 'sbx', name_prefix: 'ci', limit: 5, status: 'running', labels: '{"team":"ml"}' } })
    expect(page.items.map((sandbox) => sandbox.id)).toEqual(['sbx-a'])
    expect(page.nextCursor).toBe('more')
    expect(page.total).toBe(42)
    expect(page.totalRelation).toBe('eq')
  })

  it('forwards the list() cursor to /sandboxes/search when a search filter is present', async () => {
    const { client, api } = searchClient()
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { sandboxes: [], total: 0 } })
    await client.list({ cursor: 'c1', sort: 'name', order: 'asc' })
    expect(get.mock.calls[0]?.[0]).toBe('/sandboxes/search')
    expect(get.mock.calls[0]?.[1]).toEqual({ params: { cursor: 'c1', sort: 'name', order: 'asc' } })
  })

  it('returns one list page without filters and uses search for prefix filters', async () => {
    // list() returns one page; iter() follows subsequent pages explicitly.
    const { client, api } = searchClient()
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { sandboxes: [{ id: 'sbx-1', name: 'ci-1', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }, { id: 'other', name: 'x', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }], next_cursor: 'p2' } })
      .mockResolvedValueOnce({ data: { sandboxes: [{ id: 'sbx-2', name: 'ci-2', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }] } })
      .mockResolvedValueOnce({ data: { sandboxes: [{ id: 'sbx-1', name: 'ci-1', status: 'running', toolbox_proxy_url: 'https://proxy.example/sandbox-api' }], total: 1, total_relation: 'eq' } })
    const page = await client.list()
    expect(get).toHaveBeenCalledTimes(1)
    expect(get.mock.calls[0]).toEqual(['/sandboxes', { params: { cursor: undefined, limit: undefined } }])
    expect(page.items.map((sandbox) => sandbox.id)).toEqual(['sbx-1', 'other'])
    expect(page.nextCursor).toBe('p2')
    expect(page.total).toBeUndefined()
    expect(page.totalRelation).toBeUndefined()

    // iter() is how you walk: it follows the cursor page by page.
    const rest: string[] = []
    for await (const sandbox of client.iter({ cursor: 'p2' })) rest.push(sandbox.id)
    expect(rest).toEqual(['sbx-2'])
    expect(get.mock.calls[1]).toEqual(['/sandboxes', { params: { cursor: 'p2', limit: undefined } }])

    // `GET /sandboxes` has no id/name filters: a prefix applied client-side to one page
    // would silently miss later pages, so a prefix uses the search endpoint
    // (as id_prefix / name_prefix) like every other filter.
    const filtered = await client.list({ id: 'sbx', name: 'ci' })
    expect(get.mock.calls[2]?.[0]).toBe('/sandboxes/search')
    expect(get.mock.calls[2]?.[1]).toEqual({ params: { id_prefix: 'sbx', name_prefix: 'ci' } })
    expect(filtered.items.map((sandbox) => sandbox.id)).toEqual(['sbx-1'])
    expect(filtered.total).toBe(1)

    // An explicit null (reachable from untyped JS) is "no filter", exactly like
    // Python's `id=None`: it uses the list endpoint, not search.
    get.mockResolvedValueOnce({ data: { sandboxes: [], next_cursor: undefined } })
    await client.list({ id: null, name: null } as unknown as Parameters<typeof client.list>[0])
    expect(get.mock.calls[3]?.[0]).toBe('/sandboxes')
  })

  it.each([
    [501, 'SEARCH_NOT_CONFIGURED', SearchNotConfiguredError],
    [503, 'SEARCH_UNAVAILABLE', SearchUnavailableError],
  ])('maps %i %s to the status-level error class', async (status, code, expected) => {
    const { client, api } = searchClient()
    jest.spyOn(api, 'get').mockRejectedValue(axios4xx(status, { code, message: code, source: 'DAYTONA_API', trace_id: '0af7651916cd43dd8448eb211c80319c' }))
    await expect(client.search({ q: 'x' })).rejects.toBeInstanceOf(expected)
    await expect(client.list({ status: ['running'] })).rejects.toBeInstanceOf(expected)
    const error = await client.search({ q: 'x' }).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(DaytonaError)
    expect((error as DaytonaError).statusCode).toBe(status)
    expect((error as DaytonaError).code).toBe(code)
    expect((error as DaytonaError).message).toBe(code)
    expect(error instanceof InternalServerError).toBe(status === 501)
  })

  it('surfaces a mismatched cursor as a BadRequestError', async () => {
    const { client, api } = searchClient()
    jest.spyOn(api, 'get').mockRejectedValue(axios4xx(400, { code: 'BAD_REQUEST', message: 'cursor does not match this query', source: 'DAYTONA_API', trace_id: '0af7651916cd43dd8448eb211c80319c' }))
    await expect(client.search({ cursor: 'stale', sort: 'name' })).rejects.toThrow('cursor does not match this query')
    await expect(client.search({ cursor: 'stale', sort: 'name' })).rejects.toBeInstanceOf(BadRequestError)
    const error = await client.search({ cursor: 'stale', sort: 'name' }).catch((caught: unknown) => caught)
    expect((error as DaytonaError).code).toBe('BAD_REQUEST')
  })
})

describe('macOS sandboxes and reservations', () => {
  it('sends disk volumes on a macOS create', async () => {
    const client = macClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'mac', status: 'running', wait_cap_seconds: 3 } })
    await client.createMacos('base-macos', { volumes: [{ volumeId: 'data', mountPath: '/Users/daytona/data', subpath: 'proj' }] })
    expect(JSON.parse(JSON.stringify(post.mock.calls[0]?.[1])).volumes).toEqual([{ volume: 'data', mount_path: '/Users/daytona/data', subpath: 'proj' }])
  })
  const macResponse = { id: 'mac', status: 'running', class: 'macos', persistent: true }

  function macClient(): Daytona {
    return new Daytona({ apiKey: 'key' })
  }

  it('createMacos posts to /sandboxes/macos with the CreateMacosSandboxRequest body', async () => {
    const client = macClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'mac', status: 'running', wait_cap_seconds: 3 } })

    const sandbox = await client.createMacos('base-macos', { region: 'mac-region' })
    expect(post.mock.calls[0]?.[0]).toBe('/sandboxes/macos')
    expect(post.mock.calls[0]?.[1]).toMatchObject({ image: 'base-macos', region_id: 'mac-region', wait_seconds: 60 })
    expect(post.mock.calls[0]?.[2]).toMatchObject({ headers: { 'Idempotency-Key': expect.any(String) } })
    expect(sandbox.sandboxClass).toBe('macos')

    await client.createMacos('sequoia-xcode', {
      name: 'mac-1',
      persistent: true,
      reservation: 'new',
      autoRenew: false,
      ports: [{ port: 8080 }],
      env: { A: 'B' },
      labels: { team: 'sdk' },
      secrets: ['API_KEY'],
      idempotencyKey: 'mac-key',
      region: 'mac-region',
      createTimeoutSeconds: 600,
    })
    expect(post.mock.calls[1]?.[0]).toBe('/sandboxes/macos')
    expect(post.mock.calls[1]?.[1]).toMatchObject({
      name: 'mac-1',
      image: 'sequoia-xcode',
      persistent: true,
      ports: [{ port: 8080, proto: 'tcp' }],
      env: { A: 'B' },
      labels: { team: 'sdk', name: 'mac-1' },
      secrets: ['API_KEY'],
      region_id: 'mac-region',
      reservation: 'new',
      auto_renew: false,
      wait_seconds: 60,
      create_timeout_seconds: 600,
    })
    expect(post.mock.calls[1]?.[2]).toMatchObject({ headers: { 'Idempotency-Key': 'mac-key' } })

    // A reservation id places the sandbox on exactly that lease; a per-create target routes it.
    await client.createMacos('base-macos', { reservation: 'mac-region:res-1', region: 'region-a' })
    expect(post.mock.calls[2]?.[1]).toMatchObject({ region_id: 'region-a', reservation: 'mac-region:res-1' })
    expect(post.mock.calls[2]?.[2]).toMatchObject({ headers: { 'x-daytona-region': 'region-a' } })
  })

  /** A client on the default API: no URL in the env. */
  function defaultApiClient(): Daytona {
    const saved = process.env.DAYTONA_V2_API_URL
    delete process.env.DAYTONA_V2_API_URL
    try {
      return macClient()
    } finally {
      if (saved !== undefined) process.env.DAYTONA_V2_API_URL = saved
    }
  }

  it.each([undefined, 'region-a'])('createMacos on the default API throws the API region conflict unchanged (region %j)', async (region) => {
    const client = defaultApiClient()
    const notServed = new publicSdk.ConflictError("Region 'region-a' is not available at this API URL", { statusCode: 409, code: 'CONFLICT', source: 'DAYTONA_API' })
    const post = jest.spyOn(clientInstance(client), 'post').mockRejectedValueOnce(notServed)
    const get = jest.spyOn(clientInstance(client), 'get')
    await expect(client.createMacos('base-macos', { region })).rejects.toBe(notServed)
    expect(post).toHaveBeenCalledTimes(1)
    expect(get).not.toHaveBeenCalled()
  })

  it('hands back the reservation a running macOS create claimed, with no second read', async () => {
    const client = macClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'mac', status: 'running', wait_cap_seconds: 3, reservation_id: 'mac-region:res-1' } })
    const get = jest.spyOn(clientInstance(client), 'get')

    const sandbox = await client.createMacos('base-macos')
    expect(post).toHaveBeenCalledTimes(1)
    expect(get).not.toHaveBeenCalled()
    expect(sandbox.reservationId).toBe('mac-region:res-1')
  })


  it('sends a network policy on a macOS create', async () => {
    const client = macClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'mac', status: 'running', wait_cap_seconds: 3 } })
    await client.createMacos('base-macos', { networkPolicy: { blockAll: true, allowCidrs: ['93.184.215.14/32'] } })
    const body = JSON.parse(JSON.stringify(post.mock.calls[0]?.[1])) as Record<string, unknown>
    expect(body.network_policy).toEqual({ block_all: true, allow_cidrs: ['93.184.215.14/32'] })
    await client.createMacos('base-macos')
    const plain = JSON.parse(JSON.stringify(post.mock.calls[1]?.[1])) as Record<string, unknown>
    expect(plain).not.toHaveProperty('network_policy')
  })

  it('refuses what a macOS create does not take before posting', async () => {
    const client = macClient()
    const post = jest.spyOn(clientInstance(client), 'post')
    await expect(client.createMacos('base-macos', { autoRenew: true })).rejects.toThrow('macos create takes autoRenew only with reservation: "new"')
    await expect(client.createMacos('base-macos', { reservation: 'mac-region:res-1', autoRenew: true })).rejects.toThrow('macos create takes autoRenew only with reservation: "new"')
    expect(post).not.toHaveBeenCalled()
  })

  it('sends supplied macOS size axes exactly and omits absent ones', async () => {
    const client = macClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: { id: 'mac', status: 'running', wait_cap_seconds: 3 } })

    await client.createMacos('base-macos')
    // macOS accepts the persistent option alongside a reservation.
    await client.createMacos('base-macos', { reservation: 'mac-region:res-1', cpu: 10, memory: 16, persistent: true })
    await client.createMacos('base-macos', { memory: 4.5 })
    await client.createMacos('base-macos', { cpu: 4 })
    await client.createMacos('base-macos', { disk: 115 })
    await client.createMacos('base-macos', { disk: 80.5 })

    const bodies = post.mock.calls.map((call) => JSON.parse(JSON.stringify(call[1])) as Record<string, unknown>)
    expect(bodies[0]).not.toHaveProperty('cores')
    expect(bodies[0]).not.toHaveProperty('memory_mib')
    expect(bodies[0]).not.toHaveProperty('disk_mib')
    expect(bodies[1]).toMatchObject({ reservation: 'mac-region:res-1', cores: 10, memory_mib: 16384, persistent: true })
    expect(bodies[2]).toMatchObject({ memory_mib: 4608 })
    expect(bodies[2]).not.toHaveProperty('cores')
    expect(bodies[3]).toMatchObject({ cores: 4 })
    expect(bodies[3]).not.toHaveProperty('memory_mib')
    expect(bodies[4]).toMatchObject({ disk_mib: 117760 })
    expect(bodies[4]).not.toHaveProperty('memory_mib')
    expect(bodies[5]).toMatchObject({ disk_mib: 82432 })
    expect(bodies.map((body) => body.cpu ?? body.memory ?? body.disk)).toEqual([undefined, undefined, undefined, undefined, undefined, undefined])
  })

  it.each([
    ['cpu', { cpu: 3 }, 'cpu must be an integer between 4 and 2147483647'],
    ['fractional cpu', { cpu: 4.5 }, 'cpu must be an integer between 4 and 2147483647'],
    ['cpu above int32', { cpu: 2147483648 }, 'cpu must be an integer between 4 and 2147483647'],
    ['NaN cpu', { cpu: Number.NaN }, 'cpu must be an integer between 4 and 2147483647'],
    ['memory below 4 GiB', { memory: 3.999 }, 'memory must be a finite GiB value that converts to an integer MiB value between 4096 and 2147483647'],
    ['memory with a fractional MiB', { memory: 4.0001 }, 'memory must be a finite GiB value that converts to an integer MiB value between 4096 and 2147483647'],
    ['infinite memory', { memory: Number.POSITIVE_INFINITY }, 'memory must be a finite GiB value that converts to an integer MiB value between 4096 and 2147483647'],
    ['memory above int32 MiB', { memory: 2097152 }, 'memory must be a finite GiB value that converts to an integer MiB value between 4096 and 2147483647'],
    ['disk below 80 GiB', { disk: 79.999 }, 'disk must be a finite GiB value that converts to an integer MiB value between 81920 and 2147483647'],
    ['disk with a fractional MiB', { disk: 80.0001 }, 'disk must be a finite GiB value that converts to an integer MiB value between 81920 and 2147483647'],
    ['NaN disk', { disk: Number.NaN }, 'disk must be a finite GiB value that converts to an integer MiB value between 81920 and 2147483647'],
    ['disk above int32 MiB', { disk: 2097152 }, 'disk must be a finite GiB value that converts to an integer MiB value between 81920 and 2147483647'],
  ])('refuses an invalid macOS size (%s) before posting', async (_case, size, message) => {
    const client = macClient()
    const post = jest.spyOn(clientInstance(client), 'post')
    const error = await client.createMacos('base-macos', size).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(DaytonaError)
    expect(error).toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message })
    expect(post).not.toHaveBeenCalled()
  })

  it('reports a macOS size in exact GiB and keeps the reservation id through serialization', () => {
    const client = macClient()
    const api = clientInstance(client)
    const mac = new Sandbox({ ...macResponse, cpu: 7, cores_max: 7, memory_mib: 4608, memory_max_mib: 4608, disk_mib: 117760, disk_max_mib: 117760, reservation_id: 'mac-region:res-1' }, api, undefined, 'key')
    expect(mac.memory).toBe(4.5)
    expect(mac.memoryMax).toBe(4.5)
    expect(mac.disk).toBe(115)
    expect(mac.diskMax).toBe(115)
    expect(new Sandbox({ ...macResponse, disk_mib: 82432 }, api, undefined, 'key').disk).toBe(80.5)
    expect(mac.reservationId).toBe('mac-region:res-1')
    const json = mac.toJSON()
    expect(json).toMatchObject({ memory: 4.5, memoryMax: 4.5, disk: 115, diskMax: 115, reservationId: 'mac-region:res-1' })

    const unclaimed = new Sandbox({ ...macResponse, status: 'pending', memory_mib: 0, disk_mib: 0, reservation_id: null }, api, undefined, 'key')
    expect(unclaimed.memory).toBe(0)
    expect(unclaimed.disk).toBe(0)
    expect(unclaimed.reservationId).toBeUndefined()
    // Linux keeps whole GiB, rounded up.
    const linux = new Sandbox({ id: 'lin', status: 'running', class: 'linux', memory_mib: 4608, disk_mib: 1536 }, api, undefined, 'key')
    expect(linux.memory).toBe(5)
    expect(linux.disk).toBe(2)
  })

  it('refreshes the claimed lease id and size from the sandbox detail', async () => {
    const client = macClient()
    const sandbox = lazySandbox(client, 'mac')
    jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { ...macResponse, status: 'pending', cpu: 0, cores_max: 0, memory_mib: 0, reservation_id: null } })
      .mockResolvedValueOnce({ data: { ...macResponse, cpu: 10, cores_max: 10, memory_mib: 16384, reservation_id: 'mac-region:res-2' } })
    await sandbox.refreshData()
    expect(sandbox.reservationId).toBeUndefined()
    await sandbox.refreshData()
    expect(sandbox.reservationId).toBe('mac-region:res-2')
    expect(sandbox.cpu).toBe(10)
    expect(sandbox.memory).toBe(16)
  })

  it('surfaces coded macOS create failures with their code', async () => {
    const client = macClient()
    const post = jest.spyOn(clientInstance(client), 'post')
    for (const [status, code] of [[409, 'NO_ACTIVE_RESERVATION'], [409, 'AUTO_RESERVE_LIMIT_REACHED'], [409, 'MAC_DISK_FULL'], [404, 'RESERVATION_NOT_FOUND'], [402, 'INSUFFICIENT_BALANCE']] as const) {
      post.mockRejectedValueOnce(createAxiosDaytonaError({
        isAxiosError: true,
        message: `Request failed with status code ${status}`,
        name: 'AxiosError',
        response: { status, data: { code, message: `refused: ${code}`, source: 'DAYTONA_API' } },
      } as unknown as AxiosError))
      const error = await client.createMacos('base-macos').catch((caught: unknown) => caught)
      expect(error).toMatchObject({ statusCode: status, code, message: `refused: ${code}`, source: 'DAYTONA_API' })
      if (status === 409) expect(error).toBeInstanceOf(ConflictError)
    }
  })

  it('reads the macOS sandbox class from metadata', async () => {
    const client = macClient()
    const sandbox = lazySandbox(client, 'mac')
    // `class` alone decides the class; a response without it reads as linux.
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { id: 'mac', status: 'stopped', class: 'macos', persistent: true } })
      .mockResolvedValueOnce({ data: { id: 'mac', status: 'running', class: 'macos', persistent: true } })
      .mockResolvedValueOnce({ data: { id: 'mac', status: 'running', class: 'macos', persistent: true } })
      .mockRejectedValueOnce(new NotFoundError('gone', { statusCode: 404, code: 'NOT_FOUND' }))
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: {} })
    const del = jest.spyOn(clientInstance(client), 'delete').mockResolvedValue({ data: {} })

    await sandbox.start({ timeout: 1 })
    expect(sandbox.sandboxClass).toBe('macos')
    await sandbox.stop()
    await sandbox.delete()

    expect(post.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/mac/start', '/sandboxes/mac/stop'])
    expect(del.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/mac'])
    expect(get.mock.calls[0]?.[0]).toBe('/sandboxes/mac')
  })

  it('forks a macOS sandbox, disk only by default', async () => {
    const client = macClient()
    const sandbox = lazySandbox(client, 'mac')
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { id: 'mac', status: 'stopped', class: 'macos' } })
      .mockResolvedValueOnce({ data: { id: 'mac-fork', status: 'pending', class: 'macos' } })
    const post = jest.spyOn(clientInstance(client), 'post')
      .mockResolvedValue({ data: { forked_from: 'mac', status: 'pending', toolbox_proxy_url: 'https://proxy.example', id: 'mac-fork' }, headers: {} })

    const fork = await sandbox.fork({ name: 'copy' })

    expect(fork.id).toBe('mac-fork')
    expect(post.mock.calls).toEqual([['/sandboxes/mac/fork', { name: 'copy' }]])
    expect(get.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/mac', '/sandboxes/mac-fork'])
  })

  it('pauses and resumes macOS sandboxes, waiting through pausing and assigned', async () => {
    const client = macClient()
    const api = clientInstance(client)
    const sandbox = new Sandbox({ ...macResponse, persistent: true }, api, undefined, 'key')
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { status: 'pausing', id: 'mac' } })
      .mockResolvedValueOnce({ data: { status: 'assigned', id: 'mac' } })
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { ...macResponse, status: 'pausing' } })
      .mockResolvedValueOnce({ data: { ...macResponse, status: 'paused' } })
      .mockResolvedValueOnce({ data: { ...macResponse, status: 'assigned' } })
      .mockResolvedValueOnce({ data: { ...macResponse, status: 'running', mac_boot_mode: 'future-mode' } })

    const paused = sandbox.pause({ timeout: 10_000 })
    await jest.advanceTimersByTimeAsync(200)
    await expect(paused).resolves.toBeUndefined()
    expect(sandbox.state).toBe('paused')
    expect(sandbox.macBootMode).toBeNull()

    const resumed = sandbox.resume({ timeout: 10_000 })
    await jest.advanceTimersByTimeAsync(200)
    await expect(resumed).resolves.toBeUndefined()
    expect(sandbox.state).toBe('started')
    expect(sandbox.macBootMode).toBe('future-mode')

    expect(post.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/mac/pause', '/sandboxes/mac/resume'])
    const waits = get.mock.calls.map((call) => (call[1] as { params?: { wait?: string } } | undefined)?.params?.wait)
    expect(waits).toEqual(['paused', 'paused', 'running', 'running'])
  })

  it('resumes a paused macOS sandbox from start()', async () => {
    const client = macClient()
    const api = clientInstance(client)
    const sandbox = lazySandbox(client, 'mac')
    jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { ...macResponse, status: 'paused' } })
      .mockResolvedValueOnce({ data: { ...macResponse, status: 'running', mac_boot_mode: 'future-mode' } })
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: { status: 'assigned', id: 'mac' } })
    await sandbox.start({ timeout: 10_000 })
    expect(post.mock.calls.map((call) => call[0])).toEqual(['/sandboxes/mac/resume'])
    expect(sandbox.macBootMode).toBe('future-mode')
  })

  it('fails a macOS pause that returns to running and a resume that ends stopped', async () => {
    const client = macClient()
    const api = clientInstance(client)
    const sandbox = new Sandbox({ ...macResponse, persistent: true }, api, undefined, 'key')
    jest.spyOn(api, 'post').mockResolvedValue({ data: {} })
    jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { ...macResponse, status: 'running', error: 'Disk is full' } })
      .mockResolvedValueOnce({ data: { ...macResponse, status: 'stopped' } })

    await expect(sandbox.pause({ timeout: 10_000 })).rejects.toMatchObject({ name: 'SandboxTerminalStateError', state: 'started' })
    expect(sandbox.errorReason).toBe('Disk is full')
    await expect(sandbox.resume({ timeout: 10_000 })).rejects.toMatchObject({ name: 'SandboxTerminalStateError', state: 'stopped' })
  })

  it('reads an arbitrary mac_boot_mode from the response', () => {
    const client = macClient()
    const api = clientInstance(client)
    const resumed = new Sandbox({ ...macResponse, mac_boot_mode: 'future-mode', error: 'Resume details' }, api, undefined, 'key')
    expect(resumed.macBootMode).toBe('future-mode')
    expect(resumed.errorReason).toBe('Resume details')
    expect(new Sandbox({ ...macResponse, mac_boot_mode: null }, api, undefined, 'key').macBootMode).toBeNull()
    expect(new Sandbox({ id: 'lin', status: 'running', class: 'linux' }, api, undefined, 'key').macBootMode).toBeNull()
  })

  it('refuses class-gated calls for a sandbox class it does not know instead of assuming linux', async () => {
    const client = macClient()
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post')
    const patch = jest.spyOn(api, 'patch')
    const unknown = new Sandbox({ id: 'odd', status: 'running', class: 'future-class' }, api, undefined, 'key')
    expect(unknown.sandboxClass).toBeUndefined()
    await expect(unknown.pause()).rejects.toThrow('unsupported sandbox class future-class')
    await expect(unknown.updateLifecyclePolicy({ onIdleTimeout: 'delete' })).rejects.toBeInstanceOf(UnsupportedOperationError)
    await expect(unknown.fork()).rejects.toThrow('unsupported sandbox class future-class')
    expect(post).not.toHaveBeenCalled()
    expect(patch).not.toHaveBeenCalled()
  })

  it('allows class-independent calls for a sandbox class it does not know', async () => {
    const client = macClient()
    const api = clientInstance(client)
    const odd = { id: 'odd', status: 'running', class: 'future-class' }
    const snapshotPayload = { id: 'snap', name: 'snap', class: 'future-class', kind: 'full', region_id: 'region-a', region_ids: [], status: 'pending', created_at: '2026-09-22T00:00:00Z' }
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: odd })
    get.mockResolvedValueOnce({ data: odd }).mockResolvedValueOnce({ data: snapshotPayload })
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { id: 'snap', status: 'pending' } })
      .mockResolvedValueOnce({ data: { signature: SIGNED_FILE_TEST_SIGNATURE, expires_at: '2026-06-12T03:22:47Z' } })
      .mockResolvedValueOnce({ status: 204, data: undefined })
      .mockResolvedValueOnce({ data: {} })
    const sandbox = lazySandbox(client, 'odd', { toolboxUrl: 'http://toolbox.invalid/sandbox-api/odd' })

    // Already started: start() returns after its refresh, whatever the class.
    await expect(sandbox.start()).resolves.toBeUndefined()
    expect(sandbox.sandboxClass).toBeUndefined()
    // Snapshot and signed-URL routes do not depend on the class.
    await expect(sandbox.createSnapshot('snap')).resolves.toMatchObject({ id: 'snap', class: 'future-class' })
    await expect(sandbox.fs.presignedDownloadUrl('/tmp/demo.txt')).resolves.toContain('/files/download?path=%2Ftmp%2Fdemo.txt')
    await expect(sandbox.rotateSigningKey()).resolves.toBeUndefined()
    // A stopped sandbox uses the same start route.
    get.mockResolvedValueOnce({ data: { ...odd, status: 'stopped' } })
    await expect(sandbox.start({ timeout: 1000 })).resolves.toBeUndefined()
    expect(get.mock.calls.map((call) => call[0])).toContain('/snapshots/snap')
    expect(post.mock.calls.map((call) => call[0])).toEqual([
      '/sandboxes/odd/snapshots',
      '/sandboxes/odd/files/sign',
      '/sandboxes/odd/signing-key/rotate',
      '/sandboxes/odd/start',
    ])
    // Resuming a paused one depends on the class, so it is still refused.
    get.mockResolvedValueOnce({ data: { ...odd, status: 'paused' } })
    await expect(sandbox.start()).rejects.toThrow('unsupported sandbox class future-class')
    expect(post).toHaveBeenCalledTimes(4)
  })

  it('drives the reservations namespace over /reservations', async () => {
    const client = macClient()
    const api = clientInstance(client)
    const reservation = {
      id: 'res-1',
      org_id: 'org',
      project_id: 'proj',
      region_id: 'mac-region',
      status: 'active',
      sandboxes_per_mac: 2,
      model: 'm4-10c-16g',
      capacity: { cores: 10, memory_mib: 16384, disk_mib: 235520 },
      resources_used: { cores: 5, memory_mib: 8192, disk_mib: 117760 },
      period_n: 1,
      period_seconds: 86_400,
      period_started_at: '2026-09-22T00:00:00Z',
      period_end: '2026-09-23T00:00:00Z',
      pending_mac_started_at: '2026-09-21T23:59:00Z',
      created_at: '2026-09-22T00:00:00Z',
      slots_used: 1,
      renewal_pending: false,
      renewal_error: null,
      auto_renew: true,
      origin: 'explicit',
      rawOrigin: 'explicit',
    }
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: reservation })
    const get = jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { reservations: [reservation] } })
      .mockResolvedValueOnce({ data: reservation })

    const created = await client.reservations.create({ region: 'mac-region', model: 'm4pro-12c-24g', idempotencyKey: 'res-key', autoRenew: false })
    const expectedReservation = {
      id: 'res-1', orgId: reservation.org_id, projectId: reservation.project_id,
      region: reservation.region_id, status: 'active', rawStatus: 'active',
      sandboxesPerMac: 2,
      model: reservation.model, capacity: { cores: 10, memoryMib: 16384, diskMib: 235520 },
      resourcesUsed: { cores: 5, memoryMib: 8192, diskMib: 117760 }, periodN: 1,
      periodSeconds: 86_400, periodStartedAt: '2026-09-22T00:00:00Z',
      periodEnd: '2026-09-23T00:00:00Z', pendingMacStartedAt: '2026-09-21T23:59:00Z',
      createdAt: '2026-09-22T00:00:00Z', slotsUsed: 1, renewalPending: false,
      renewalError: null, autoRenew: true, origin: 'explicit', rawOrigin: 'explicit',
    }
    expect(created).toEqual(expectedReservation)
    expect(created.capacity?.cores).toBe(10)
    expect(created.resourcesUsed.memoryMib).toBe(8192)
    expect(post.mock.calls[0]).toEqual(['/reservations', { region_id: 'mac-region', model: 'm4pro-12c-24g', auto_renew: false }, { headers: { 'Idempotency-Key': 'res-key' } }])

    // Without a caller key every request still carries one, fresh per call.
    await client.reservations.create({ region: 'mac-region' })
    await client.reservations.create({ region: 'mac-region' })
    expect(post.mock.calls[1]?.[1]).toEqual({ region_id: 'mac-region' })
    expect(post.mock.calls[1]?.[2]).toEqual({ headers: { 'Idempotency-Key': expect.stringMatching(/\S/) } })
    expect(post.mock.calls[1]?.[2]).not.toEqual(post.mock.calls[2]?.[2])

    await expect(client.reservations.list()).resolves.toEqual({ items: [expectedReservation], nextCursor: undefined })
    await expect(client.reservations.get('res-1')).resolves.toEqual(expectedReservation)

    expect(get.mock.calls.map((call) => call[0])).toEqual(['/reservations', '/reservations/res-1'])
  })

  it('carries Mac capacity and omitted-size defaults', async () => {
    const client = macClient()
    const api = clientInstance(client)
    const reservation = {
      id: 'res-1',
      status: 'active',
      origin: 'explicit',
      capacity: { cores: 10, memory_mib: 16384, disk_mib: 235520 },
      resources_used: { cores: 5, memory_mib: 8192, disk_mib: 117760 },
    }
    const model = { id: 'm4-10c-16g', name: 'Apple M4', cores: 10, memory_mib: 16384, disk_mib: 244198, sandbox_disk_mib: 235520, max_sandboxes: 2, default_size: { cores: 5, memory_mib: 8192, disk_mib: 117760 } }
    const tinyModel = { id: 'tiny-2c-4g', name: 'Apple Tiny', cores: 2, memory_mib: 4096, disk_mib: 65536, sandbox_disk_mib: 40960, max_sandboxes: 0, default_size: null }
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: reservation }).mockResolvedValueOnce({ data: { mac_models: [model, tinyModel] } })
    const fetched = await client.reservations.get('res-1')
    expect(fetched.capacity?.diskMib).toBe(235520)
    expect(fetched.resourcesUsed.diskMib).toBe(117760)
    const [listed, tiny] = await client.macModels.list()
    expect(listed?.sandbox_disk_mib).toBe(235520)
    expect(listed?.max_sandboxes).toBe(2)
    expect(listed?.default_size).toEqual({ cores: 5, memory_mib: 8192, disk_mib: 117760 })
    expect(tiny?.max_sandboxes).toBe(0)
    expect(tiny?.default_size).toBeNull()
  })

  it('renews with the caller key, or a fresh key per call', async () => {
    const client = macClient()
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: lease('mac-region:res 1') })
    await client.reservations.renew('mac-region:res 1', { idempotencyKey: 'renew-1' })
    await client.reservations.renew('mac-region:res 1', { idempotencyKey: 'renew-1' })
    await client.reservations.renew('mac-region:res 1')
    await client.reservations.renew('mac-region:res 1')
    expect(post.mock.calls[0]).toEqual(['/reservations/mac-region%3Ares%201/renew', undefined, { headers: { 'Idempotency-Key': 'renew-1' } }])
    expect(post.mock.calls[1]).toEqual(post.mock.calls[0])
    expect(post.mock.calls[2]?.[2]).toEqual({ headers: { 'Idempotency-Key': expect.stringMatching(/\S/) } })
    expect(post.mock.calls[2]?.[2]).not.toEqual(post.mock.calls[3]?.[2])
    await expect(client.reservations.renew('res-1', { idempotencyKey: 7 as unknown as string })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'idempotencyKey must be a string' })
    expect(post).toHaveBeenCalledTimes(4)
  })

  it('reserves several Macs with POST /reservations/bulk', async () => {
    const client = macClient()
    const batch = { requested: 3, reserved: 2, reservations: ['res-1', 'res-2'].map((id) => ({ ...lease(id), status: 'pending_charge', origin: 'explicit' })) }
    const post = jest.spyOn(clientInstance(client), 'post').mockResolvedValue({ data: batch })

    const bought = await client.reservations.createBulk({ region: 'mac-region', count: 3, minCount: 2, model: 'm4pro-12c-24g', autoRenew: true, idempotencyKey: 'nightly-macs' })
    expect(bought).toMatchObject({ requested: 3, reserved: 2, reservations: [
      { id: 'res-1', status: 'pending_charge', origin: 'explicit' },
      { id: 'res-2', status: 'pending_charge', origin: 'explicit' },
    ] })
    expect(bought.reserved).toBe(2)
    for (const reserved of bought.reservations) {
      expect(reserved).toMatchObject({ orgId: 'org', projectId: 'proj', autoRenew: true, sandboxesPerMac: 2, slotsUsed: 0, periodN: 1, periodSeconds: 86_400, renewalPending: false, createdAt: '2026-09-22T00:00:00Z' })
      expect(reserved.resourcesUsed).toEqual({ cores: 0, memoryMib: 0, diskMib: 0 })
      for (const wire of ['region_id', 'org_id', 'project_id', 'auto_renew', 'sandboxes_per_mac', 'slots_used', 'resources_used', 'period_n', 'period_seconds', 'renewal_pending', 'created_at']) {
        expect(reserved).not.toHaveProperty(wire)
      }
    }
    expect(post.mock.calls[0]).toEqual(['/reservations/bulk', { region_id: 'mac-region', count: 3, min_count: 2, model: 'm4pro-12c-24g', auto_renew: true }, { headers: { 'Idempotency-Key': 'nightly-macs' } }])

    // Omitted options stay off the body; every request still carries a key, fresh per call.
    await client.reservations.createBulk({ region: 'mac-region', count: 3 })
    await client.reservations.createBulk({ region: 'mac-region', count: 3 })
    expect(post.mock.calls[1]?.[1]).toEqual({ region_id: 'mac-region', count: 3 })
    expect(post.mock.calls[1]?.[2]).toEqual({ headers: { 'Idempotency-Key': expect.stringMatching(/\S/) } })
    expect(post.mock.calls[1]?.[2]).not.toEqual(post.mock.calls[2]?.[2])
  })

  it('reads free Macs and prices from GET /mac-catalog', async () => {
    const client = macClient()
    const catalog = {
      regions: [{
        id: 'mac-region',
        default_model: 'm4-10c-16g',
        models: [{
          model: { id: 'm4-10c-16g', name: 'Apple M4', cores: 10, memory_mib: 16384, disk_mib: 244198, sandbox_disk_mib: 235520, max_sandboxes: 2, default_size: { cores: 5, memory_mib: 8192, disk_mib: 117760 } },
          available: 3,
          price: { cents_per_hour: 135.12, period_cents: 3243, currency: 'usd', period_seconds: 86_400 },
        }],
      }],
    }
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: catalog })
    await expect(client.macModels.catalog({ region: 'mac-region' })).resolves.toEqual(catalog)
    await client.macModels.catalog()
    expect(get.mock.calls).toEqual([['/mac-catalog', { params: { region_ids: 'mac-region' } }], ['/mac-catalog', undefined]])
  })

  it('lists the Mac model registry from GET /mac-models', async () => {
    const client = macClient()
    const models = [
      { id: 'm4-10c-16g', name: 'Apple M4', cores: 10, memory_mib: 16384, disk_mib: 244198 },
      { id: 'm4pro-12c-24g', name: 'Apple M4 Pro', cores: 12, memory_mib: 24576, disk_mib: 488386 },
    ]
    const get = jest.spyOn(clientInstance(client), 'get').mockResolvedValue({ data: { mac_models: models } })
    expect(client.macModels).toBeInstanceOf(publicSdk.MacModelsService)
    await expect(client.macModels.list()).resolves.toEqual(models)
    expect(get.mock.calls).toEqual([['/mac-models']])
  })

  function lease(id: string) {
    return {
      id,
      org_id: 'org',
      project_id: 'proj',
      region_id: 'mac-region',
      status: 'active',
      origin: 'auto',
      auto_renew: true,
      sandboxes_per_mac: 2,
      slots_used: 0,
      resources_used: { cores: 0, memory_mib: 0, disk_mib: 0 },
      period_n: 1,
      period_seconds: 86_400,
      renewal_pending: false,
      created_at: '2026-09-22T00:00:00Z',
    }
  }

  it('list() is one cursor page of /reservations and iter() walks the rest', async () => {
    const client = macClient()
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { reservations: [lease('res-1')], next_cursor: 'p2' } })
      .mockResolvedValueOnce({ data: { reservations: [lease('res-2')], next_cursor: null } })

    const page = await client.reservations.list({ limit: 1 })

    expect(page).toMatchObject({ items: [{ id: 'res-1' }], nextCursor: 'p2' })
    expect(get.mock.calls[0]).toEqual(['/reservations', { params: { limit: 1 } }])

    const walked: string[] = []
    for await (const reservation of client.reservations.iter({ cursor: 'p2' })) walked.push(reservation.id)

    expect(walked).toEqual(['res-2'])
    expect(get.mock.calls[1]).toEqual(['/reservations', { params: { cursor: 'p2' } }])
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('iter() stops instead of looping when the server repeats a cursor', async () => {
    const client = macClient()
    const get = jest.spyOn(clientInstance(client), 'get')
      .mockResolvedValueOnce({ data: { reservations: [lease('res-1')], next_cursor: 'p2' } })
      .mockResolvedValueOnce({ data: { reservations: [lease('res-2')], next_cursor: 'p2' } })

    const walked: string[] = []
    for await (const reservation of client.reservations.iter()) walked.push(reservation.id)

    expect(walked).toEqual(['res-1', 'res-2'])
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('turns auto-renewal on and off with PATCH /reservations/{id}', async () => {
    const client = macClient()
    const api = clientInstance(client)
    const patch = jest.spyOn(api, 'patch').mockResolvedValue({ data: { ...lease('mac-region:res 1'), auto_renew: false, renewal_error: null } })
    await expect(client.reservations.update('mac-region:res 1', { autoRenew: false })).resolves.toMatchObject({ origin: 'auto', autoRenew: false })
    expect(patch.mock.calls).toEqual([['/reservations/mac-region%3Ares%201', { auto_renew: false }]])
  })
})

describe('sandbox access tokens', () => {
  const TOOLBOX = 'http://toolbox.invalid/sandbox-api/sbx'

  function mintPayload(token: string, options: { expiresInMs?: number; operations?: string[]; ports?: number[] | null } = {}) {
    return {
      access_token: token,
      token_type: 'Bearer',
      expires_at: new Date(Date.now() + (options.expiresInMs ?? 3_600_000)).toISOString(),
      operations: options.operations ?? ['toolbox', 'ssh', 'desktop', 'sandbox:stop', 'sandbox:pause'],
      ports: options.ports ?? null,
    }
  }

  function toolboxTransportOf(sandbox: Sandbox): HttpTransport {
    const transport = Reflect.get(sandbox, 'toolboxTransport') as HttpTransport | undefined
    if (!transport) throw new Error('sandbox has no toolbox transport')
    return transport
  }

  function authorizationOf(call: unknown[]): unknown {
    const config = call[1] as { headers?: Record<string, unknown> } | undefined
    return config?.headers?.Authorization
  }

  it('createAccessToken round-trips operations/ports/ttl without a metadata GET on a lazy handle', async () => {
    const base = '/sandboxes'
    const client = new Daytona({ apiKey: 'key' })
    // A LAZY handle: no sandboxClass preset — the mint must not fetch the sandbox
    // to learn it (Python parity).
    const sandbox = lazySandbox(client, 'sbx')
    const api = clientInstance(client)
    const get = jest.spyOn(api, 'get')
    const post = jest.spyOn(api, 'post').mockResolvedValueOnce({
      data: mintPayload('scoped-jwt', { operations: ['toolbox:fs', 'ssh'], ports: [3000] }),
    })

    const minted = await sandbox.createAccessToken({ operations: ['toolbox:fs', 'ssh'], ports: [3000], ttlSeconds: 900 })

    expect(minted.accessToken).toBe('scoped-jwt')
    expect(minted.tokenType).toBe('Bearer')
    expect(minted.operations).toEqual(['toolbox:fs', 'ssh'])
    expect(minted.ports).toEqual([3000])
    expect(post).toHaveBeenCalledWith(`${base}/sbx/access-token`, { operations: ['toolbox:fs', 'ssh'], ports: [3000], ttl_seconds: 900 })
    expect(get).not.toHaveBeenCalled()
  })

  // The Python SDK's test_sandbox_tokens.py asserts the same messages for the same bodies.
  it.each([
    [null, 'access token response is not a JSON object'],
    [[], 'access token response is not a JSON object'],
    [{ expires_at: '2030-01-01T00:00:00Z' }, 'access token response access_token must be a non-empty string'],
    [{ access_token: '', expires_at: '2030-01-01T00:00:00Z' }, 'access token response access_token must be a non-empty string'],
    [{ access_token: 'jwt' }, 'access token response expires_at must be a string'],
    [{ access_token: 'jwt', expires_at: 5 }, 'access token response expires_at must be a string'],
  ])('createAccessToken names what breaks the response contract (%p)', async (data, message) => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx')
    jest.spyOn(clientInstance(client), 'post').mockResolvedValueOnce({ data })
    await expect(sandbox.createAccessToken()).rejects.toMatchObject({ constructor: BadRequestError, message, statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  })

  it('createAccessToken sends an empty body for the full default mint and maps null ports to undefined', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx')
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockResolvedValueOnce({ data: mintPayload('full-jwt') })

    const minted = await sandbox.createAccessToken()

    expect(minted.ports).toBeUndefined() // null on the wire = all ports
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/access-token', {})
  })

  it('createAccessToken rejects invalid ports and ttl before any request', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx')
    sandbox.sandboxClass = 'linux'
    await expect(sandbox.createAccessToken({ ports: [0] })).rejects.toThrow('port must be an integer')
    await expect(sandbox.createAccessToken({ ttlSeconds: 0 })).rejects.toThrow('ttlSeconds must be a positive integer')
  })

  it('mints once under concurrency when the held token is inside the 60 s expiry lead', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX })
    sandbox.sandboxClass = 'linux'
    sandbox.accessToken = 'old-jwt'
    sandbox.accessTokenExpiresAt = new Date(Date.now() + 30_000).toISOString()
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockResolvedValueOnce({ data: mintPayload('fresh-jwt') })
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get').mockResolvedValue({ data: { ports: [] } })

    await Promise.all([sandbox.ports.list(), sandbox.ports.list()])

    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/access-token', {})
    expect(get.mock.calls.map(authorizationOf)).toEqual(['Bearer fresh-jwt', 'Bearer fresh-jwt'])
    expect(sandbox.accessToken).toBe('fresh-jwt')
  })

  it('concurrent callers share one failed mint attempt', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    // The shared in-flight promise must make exactly ONE attempt and fail
    // every joiner with it — never N sequential attempts (Python parity).
    const post = jest.spyOn(api, 'post').mockRejectedValue(new DaytonaError('boom', { statusCode: 500 }))
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get')

    const results = await Promise.allSettled(Array.from({ length: 8 }, () => sandbox.ports.list()))

    expect(results.every((r) => r.status === 'rejected')).toBe(true)
    expect(post).toHaveBeenCalledTimes(1)
    expect(get).not.toHaveBeenCalled()
  })

  it('rides the still-unexpired token when the proactive refresh fails transiently', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX })
    sandbox.sandboxClass = 'linux'
    // A failed proactive refresh must not discard a token before it expires.
    sandbox.accessToken = 'old-jwt'
    sandbox.accessTokenExpiresAt = new Date(Date.now() + 30_000).toISOString()
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockRejectedValue(new DaytonaError('boom', { statusCode: 500 }))
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get').mockResolvedValue({ data: { ports: [] } })

    const listed = await sandbox.ports.list()
    expect(listed.items).toEqual([])
    expect(get.mock.calls.map(authorizationOf)).toEqual(['Bearer old-jwt'])
    // Not latched as unsupported: the next call retries the mint.
    await sandbox.ports.list()
    expect(post).toHaveBeenCalledTimes(2)

    // An EXPIRED token cannot be ridden — the mint error surfaces.
    sandbox.accessTokenExpiresAt = new Date(Date.now() - 1_000).toISOString()
    await expect(sandbox.ports.list()).rejects.toMatchObject({ statusCode: 500 })
  })

  it('file I/O carries the minted sandbox token on every op, not the static credential', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    jest.spyOn(api, 'post').mockResolvedValue({ data: mintPayload('minted-jwt') })
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get').mockResolvedValue({ data: new TextEncoder().encode('hello').buffer })
    const post = jest.spyOn(transport.http, 'post').mockResolvedValue({
      data: { files: [], errors: [] },
      status: 200,
      headers: {},
    })

    await expect(sandbox.fs.readText('/tmp/a')).resolves.toBe('hello')
    await sandbox.fs.writeText('/tmp/a', 'x')
    await sandbox.fs.writeMany({ '/tmp/b': new Uint8Array([66]) })
    // readMany interprets non-2xx bodies itself; the request must still be
    // the wrapped one (and the 400 arrives as the typed envelope error).
    post.mockResolvedValueOnce({
      data: new TextEncoder().encode('{"message":"nope","source":"DAYTONA_DAEMON","code":"BAD_REQUEST"}').buffer,
      status: 400,
      headers: { 'content-type': 'application/json' },
    })
    await expect(sandbox.fs.readMany(['/tmp/c'])).rejects.toMatchObject({ statusCode: 400 })

    const postAuth = (call: unknown[]) => (call[2] as { headers?: Record<string, unknown> } | undefined)?.headers?.Authorization
    expect(get.mock.calls.map((call) => [call[0], authorizationOf(call)])).toEqual([
      ['/files/download', 'Bearer minted-jwt'],
    ])
    expect(post.mock.calls.map((call) => [call[0], postAuth(call)])).toEqual([
      ['/files/upload', 'Bearer minted-jwt'],
      ['/files/bulk-upload', 'Bearer minted-jwt'],
      ['/files/bulk-download', 'Bearer minted-jwt'],
    ])
  })

  it('file reads re-mint exactly once and retry on a toolbox 401', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX, accessToken: 'dtnv_secret', accessTokenFromCreate: true } as SandboxConnectOptionsFromCreate)
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const mint = jest.spyOn(api, 'post').mockResolvedValueOnce({ data: mintPayload('fresh-jwt') })
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get')
      .mockRejectedValueOnce(new DaytonaError('unauthorized', { statusCode: 401 }))
      .mockResolvedValueOnce({ data: new TextEncoder().encode('hello').buffer })

    await expect(sandbox.fs.readText('/tmp/a')).resolves.toBe('hello')
    expect(mint).toHaveBeenCalledTimes(1)
    expect(get.mock.calls.map(authorizationOf)).toEqual(['Bearer dtnv_secret', 'Bearer fresh-jwt'])
  })

  it('re-mints exactly once and retries exactly once on a toolbox 401', async () => {
    const client = new Daytona({ apiKey: 'key' })
    // An opaque create-response token has no known expiry; a 401 triggers
    // one refresh and one retry with the new token.
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX, accessToken: 'dtnv_secret', accessTokenFromCreate: true } as SandboxConnectOptionsFromCreate)
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockResolvedValueOnce({ data: mintPayload('fresh-jwt') })
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get')
      .mockRejectedValueOnce(new DaytonaError('unauthorized', { statusCode: 401 }))
      .mockResolvedValueOnce({ data: { ports: [3000] } })

    const listed = await sandbox.ports.list()

    expect(listed.items).toEqual([3000])
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/sandboxes/sbx/access-token', {})
    expect(get.mock.calls.map(authorizationOf)).toEqual(['Bearer dtnv_secret', 'Bearer fresh-jwt'])
    expect(sandbox.accessToken).toBe('fresh-jwt')
  })

  it('surfaces the second 401 after exactly one re-mint', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX, accessToken: 'dtnv_secret', accessTokenFromCreate: true } as SandboxConnectOptionsFromCreate)
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockResolvedValueOnce({ data: mintPayload('fresh-jwt') })
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get').mockRejectedValue(new DaytonaError('unauthorized', { statusCode: 401 }))

    await expect(sandbox.ports.list()).rejects.toMatchObject({ statusCode: 401 })
    expect(post).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('surfaces a mint 403 during the 401 retry instead of the toolbox 401', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX, accessToken: 'dtnv_secret', accessTokenFromCreate: true } as SandboxConnectOptionsFromCreate)
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    // The mint is FORBIDDEN (the credential cannot mint for this sandbox):
    // that is the actionable error, not the toolbox 401 the retry started
    // from. The unsupported-route fallback (404/405/501) is a kept, different path.
    const post = jest.spyOn(api, 'post').mockRejectedValue(new DaytonaError('forbidden', { statusCode: 403 }))
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get').mockRejectedValue(new DaytonaError('unauthorized', { statusCode: 401 }))

    await expect(sandbox.ports.list()).rejects.toMatchObject({ statusCode: 403 })
    expect(post).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('refreshData never gives a caller-supplied handle a minting toolbox transport', async () => {
    const client = new Daytona({ apiKey: 'key' })
    // No toolbox at construction; refreshData rebuilds the transport from the
    // API's toolbox_proxy_url with the caller token, without a provider.
    const sandbox = lazySandbox(client, 'sbx', { accessToken: 'caller-token' })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: { id: 'sbx', status: 'running', toolbox_proxy_url: 'http://toolbox2.invalid/sandbox-api' } })
    const post = jest.spyOn(api, 'post')

    await sandbox.refreshData()

    const transport = toolboxTransportOf(sandbox)
    expect(transport.token).toBe('caller-token')
    const get = jest.spyOn(transport.http, 'get').mockRejectedValue(new DaytonaError('unauthorized', { statusCode: 401 }))

    // The caller token still rides as-is: the 401 surfaces, nothing is minted.
    await expect(sandbox.ports.list()).rejects.toMatchObject({ statusCode: 401 })
    expect(post).not.toHaveBeenCalled()
    expect(get).toHaveBeenCalledTimes(1)
    expect(sandbox.accessToken).toBe('caller-token')
  })

  it('lazily mints on first toolbox use for a key-configured handle', async () => {
    const client = new Daytona({ apiKey: 'key' })
    // A get()/list()-shaped handle has no token and a configured project key.
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockResolvedValueOnce({ data: mintPayload('lazy-jwt') })
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get').mockResolvedValue({ data: { ports: [] } })

    await sandbox.ports.list()
    await sandbox.ports.list()

    // One mint serves both calls; the toolbox receives only the minted token.
    expect(post).toHaveBeenCalledTimes(1)
    expect(get.mock.calls.map(authorizationOf)).toEqual(['Bearer lazy-jwt', 'Bearer lazy-jwt'])
    expect(sandbox.accessToken).toBe('lazy-jwt')
  })

  it('never replaces a caller-supplied token: the 401 surfaces and nothing is minted', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX, accessToken: 'caller-token' })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post')
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get').mockRejectedValue(new DaytonaError('unauthorized', { statusCode: 401 }))

    await expect(sandbox.ports.list()).rejects.toMatchObject({ statusCode: 401 })
    expect(post).not.toHaveBeenCalled()
    expect(get).toHaveBeenCalledTimes(1)
    expect(transport.token).toBe('caller-token')
    expect(sandbox.accessToken).toBe('caller-token')
  })

  it('uses the create-response opaque token as-is until a 401', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX, accessToken: 'dtnv_x', accessTokenFromCreate: true } as SandboxConnectOptionsFromCreate)
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post')
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get').mockResolvedValue({ data: { ports: [] } })

    await sandbox.ports.list()

    // Unknown expiry, no 401: no proactive mint, the create token rides as-is.
    expect(post).not.toHaveBeenCalled()
    expect(get.mock.calls.map(authorizationOf)).toEqual(['Bearer dtnv_x'])
  })

  it('falls back to the project key when the deployment has no mint routes', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX })
    sandbox.sandboxClass = 'linux'
    const api = clientInstance(client)
    const post = jest.spyOn(api, 'post').mockRejectedValue(new DaytonaError('not found', { statusCode: 404 }))
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get').mockResolvedValue({ data: { ports: [] } })

    await sandbox.ports.list()
    await sandbox.ports.list()

    // After one unsupported mint attempt, subsequent calls use the project key.
    expect(post).toHaveBeenCalledTimes(1)
    expect(get.mock.calls.map(authorizationOf)).toEqual(['Bearer key', 'Bearer key'])
  })

  it('wires the create-response token and expiry into the handle as refreshable', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const expiresAt = new Date(Date.now() + 3_600_000).toISOString()
    jest.spyOn(api, 'post').mockResolvedValueOnce({ data: { id: 'sbx', status: 'running', access_token: 'create-jwt', access_token_expires_at: expiresAt } })

    const sandbox = await client.createFromSnapshot('snap', { waitSeconds: 0 })

    expect(sandbox.accessToken).toBe('create-jwt')
    expect(sandbox.accessTokenExpiresAt).toBe(expiresAt)
    expect(Reflect.get(sandbox, 'accessTokenCallerSupplied')).toBe(false)
    expect(Reflect.get(sandbox, 'sandboxTokenMintable')).toBe(true)
  })

  it('jwt client: creation token is mintable; 401 triggers one re-mint through API client, retry succeeds', async () => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const expiresAt = new Date(Date.now() + 3_600_000).toISOString()
    const sandbox = lazySandbox(client, 'sbx', {
      toolboxUrl: TOOLBOX,
      accessToken: 'creation-tok',
      accessTokenFromCreate: true,
      accessTokenExpiresAt: expiresAt,
    } as SandboxConnectOptionsFromCreate)

    expect(Reflect.get(sandbox, 'sandboxTokenMintable')).toBe(true)
    expect(Reflect.get(sandbox, 'accessTokenCallerSupplied')).toBe(false)

    const api = clientInstance(client)
    const mint = jest.spyOn(api, 'post').mockResolvedValueOnce({ data: mintPayload('fresh-sandbox-tok') })
    const transport = toolboxTransportOf(sandbox)
    const get = jest.spyOn(transport.http, 'get')
      .mockRejectedValueOnce(new DaytonaError('unauthorized', { statusCode: 401 }))
      .mockResolvedValueOnce({ data: { ports: [] } })

    const listed = await sandbox.ports.list()

    expect(listed.items).toEqual([])
    expect(mint).toHaveBeenCalledWith('/sandboxes/sbx/access-token', {})
    expect(get.mock.calls.map(authorizationOf)).toEqual(['Bearer creation-tok', 'Bearer fresh-sandbox-tok'])
    expect(sandbox.accessToken).toBe('fresh-sandbox-tok')
  })

  it('jwt client without mint routes: an expired creation token falls back to the project token, and a 401 retries with it', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (input: string | URL | Request) => {
      if (String(input).endsWith('/auth/token')) {
        return new Response(JSON.stringify({ access_token: 'project-tok', expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } })
      }
      throw new Error(`unexpected fetch ${String(input)}`)
    })
    try {
      const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
      const api = clientInstance(client)
      const mint = jest.spyOn(api, 'post').mockRejectedValue(new DaytonaError('not found', { statusCode: 404 }))

      const expired = lazySandbox(client, 'sbx', {
        toolboxUrl: TOOLBOX,
        accessToken: 'creation-tok',
        accessTokenFromCreate: true,
        accessTokenExpiresAt: new Date(Date.now() - 1_000).toISOString(),
      } as SandboxConnectOptionsFromCreate)
      const expiredGet = jest.spyOn(toolboxTransportOf(expired).http, 'get').mockResolvedValue({ data: { ports: [] } })
      await expired.ports.list()
      expect(expiredGet.mock.calls.map(authorizationOf)).toEqual(['Bearer project-tok'])

      const rejected = lazySandbox(client, 'sbx2', {
        toolboxUrl: TOOLBOX,
        accessToken: 'opaque-creation-tok',
        accessTokenFromCreate: true,
      } as SandboxConnectOptionsFromCreate)
      const rejectedGet = jest.spyOn(toolboxTransportOf(rejected).http, 'get')
        .mockRejectedValueOnce(new DaytonaError('unauthorized', { statusCode: 401 }))
        .mockResolvedValue({ data: { ports: [] } })
      await rejected.ports.list()
      await rejected.ports.list()
      // The rejected token is dropped: the next call does not repeat the 401.
      expect(rejectedGet.mock.calls.map(authorizationOf)).toEqual(['Bearer opaque-creation-tok', 'Bearer project-tok', 'Bearer project-tok'])
      expect(mint).toHaveBeenCalledTimes(2)
    } finally {
      fetch.mockRestore()
    }
  })

  it('jwt client without mint routes: a 401 on the cached project token re-exchanges it', async () => {
    let exchanges = 0
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(async (input: string | URL | Request) => {
      if (String(input).endsWith('/auth/token')) {
        exchanges += 1
        return new Response(JSON.stringify({ access_token: `P${exchanges}`, expires_in: 300, token_type: 'Bearer', issued_token_type: 'urn:ietf:params:oauth:token-type:access_token' }), { headers: { 'content-type': 'application/json' } })
      }
      throw new Error(`unexpected fetch ${String(input)}`)
    })
    try {
      const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
      jest.spyOn(clientInstance(client), 'post').mockRejectedValue(new DaytonaError('not found', { statusCode: 404 }))
      const sandbox = lazySandbox(client, 'sbx', {
        toolboxUrl: TOOLBOX,
        accessToken: 'expired-creation-tok',
        accessTokenFromCreate: true,
        accessTokenExpiresAt: new Date(Date.now() - 1_000).toISOString(),
      } as SandboxConnectOptionsFromCreate)
      const get = jest.spyOn(toolboxTransportOf(sandbox).http, 'get')
        .mockRejectedValueOnce(new DaytonaError('unauthorized', { statusCode: 401 }))
        .mockResolvedValueOnce({ data: { ports: [] } })

      await sandbox.ports.list()

      expect(get.mock.calls.map(authorizationOf)).toEqual(['Bearer P1', 'Bearer P2'])
      expect(exchanges).toBe(2)
    } finally {
      fetch.mockRestore()
    }
  })

  it('jwt client: explicit caller-supplied token — 401 surfaces, nothing minted', async () => {
    const client = new Daytona({ jwtToken: 'jwt-token', projectId: 'project-1' })
    const sandbox = lazySandbox(client, 'sbx', { toolboxUrl: TOOLBOX, accessToken: 'caller-tok' })

    expect(Reflect.get(sandbox, 'accessTokenCallerSupplied')).toBe(true)
    expect(Reflect.get(sandbox, 'sandboxTokenMintable')).toBe(false)

    const api = clientInstance(client)
    const mint = jest.spyOn(api, 'post')
    const transport = toolboxTransportOf(sandbox)
    jest.spyOn(transport.http, 'get').mockRejectedValue(new DaytonaError('unauthorized', { statusCode: 401 }))

    await expect(sandbox.ports.list()).rejects.toMatchObject({ statusCode: 401 })
    expect(mint).not.toHaveBeenCalled()
  })
})

function clientInstance(client: Daytona): AxiosInstance {
  return Reflect.get(client, 'api') as AxiosInstance
}

function toolboxInstance(sandbox: Sandbox): AxiosInstance {
  const transport = Reflect.get(sandbox, 'toolboxTransport') as HttpTransport
  return transport.http
}

function responseHeaders(config: { headers?: unknown }): Headers {
  const headers = new Headers()
  const raw = config.headers
  if (raw === undefined || raw === null) return headers
  if (typeof raw === 'object' && 'toJSON' in raw && typeof (raw as { toJSON?: unknown }).toJSON === 'function') {
    const json = (raw as { toJSON: () => Record<string, unknown> }).toJSON()
    for (const [key, value] of Object.entries(json)) {
      if (typeof value === 'string') headers.set(key, value)
    }
    return headers
  }
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === 'string') headers.set(key, value)
  }
  return headers
}

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
}

async function readStreamBytes(stream: ReadableStream<Uint8Array>): Promise<Uint8Array> {
  const reader = stream.getReader()
  const chunks: Uint8Array[] = []
  let totalLength = 0
  try {
    for (;;) {
      const next = await reader.read()
      if (next.done) break
      chunks.push(next.value)
      totalLength += next.value.byteLength
    }
  } finally {
    reader.releaseLock()
  }
  const combined = new Uint8Array(totalLength)
  let offset = 0
  for (const chunk of chunks) {
    combined.set(chunk, offset)
    offset += chunk.byteLength
  }
  return combined
}

function transportStub() {
  const http = { post: jest.fn().mockResolvedValue({ data: {} }), get: jest.fn() }
  const object = {
    baseUrl: 'http://toolbox/sbx',
    token: 'key',
    http,
    get: jest.fn(),
    getBytes: jest.fn(),
    post: jest.fn().mockResolvedValue({}),
    postRaw: jest.fn(),
    delete: jest.fn().mockResolvedValue({}),
    fetch: jest.fn(),
    bearerToken: jest.fn().mockResolvedValue('key'),
    withBaseUrl: jest.fn(),
  }
  const computerUseObject: ComputerUseTransport = {
    get: <T>(path: string, config?: { params?: Record<string, unknown> }) =>
      (config === undefined ? object.get(path) : object.get(path, config)) as Promise<T>,
    post: <T>(path: string, body?: unknown) =>
      (body === undefined ? object.post(path) : object.post(path, body)) as Promise<T>,
    delete: <T>(path: string) => object.delete(path) as Promise<T>,
    getBytes: (path: string) => object.getBytes(path) as Promise<ArrayBuffer>,
  }
  return {
    object: object as never,
    computerUseObject,
    get: object.get,
    getBytes: object.getBytes,
    post: object.post,
    postRaw: object.postRaw,
    delete: object.delete,
    fetch: object.fetch,
    http,
  }
}

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  }))
}

function controlledSseResponse(): { readonly response: Response; readonly push: (chunk: string) => void; readonly close: () => void } {
  const encoder = new TextEncoder()
  let controller: ReadableStreamDefaultController<Uint8Array> | undefined
  const response = new Response(new ReadableStream<Uint8Array>({
    start(nextController) {
      controller = nextController
    },
  }))
  return {
    response,
    push(chunk: string) {
      controller?.enqueue(encoder.encode(chunk))
    },
    close() {
      controller?.close()
    },
  }
}

async function flushAsync(): Promise<void> {
  await jest.advanceTimersByTimeAsync(0)
}

async function collectAsyncValues<T>(values: AsyncIterable<T>): Promise<T[]> {
  const items: T[] = []
  for await (const value of values) {
    items.push(value)
  }
  return items
}

function multipartFixture(
  parts: ReadonlyArray<{ readonly headers: ReadonlyArray<string>; readonly body: Uint8Array }>,
  boundary = 'DAYTONA-FILE-BOUNDARY',
): ArrayBuffer {
  const chunks: Buffer[] = []
  for (const part of parts) {
    chunks.push(Buffer.from(`--${boundary}\r\n`, 'utf8'))
    chunks.push(Buffer.from(`${part.headers.join('\r\n')}\r\n\r\n`, 'utf8'))
    chunks.push(Buffer.from(part.body))
    chunks.push(Buffer.from('\r\n', 'utf8'))
  }
  chunks.push(Buffer.from(`--${boundary}--\r\n`, 'utf8'))
  const buffer = Buffer.concat(chunks)
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength)
}

describe('toolbox endpoint e2e', () => {
  const toolboxUrl = process.env.DAYTONA_SDKTS_TOOLBOX_URL
  let toolboxAvailable = false

  beforeEach(() => {
    jest.useRealTimers()
  })

  beforeAll(async () => {
    if (toolboxUrl === undefined) return
    toolboxAvailable = await fetch(`${toolboxUrl}/version`).then((response) => response.ok).catch(() => false)
  })

  it('runs process start/wait/log replay', async () => {
    if (!toolboxAvailable || toolboxUrl === undefined) return
    const sandbox = directSandbox(toolboxUrl)
    const result = await sandbox.process.run('printf sdkts-process')
    expect(result.reason).toBe('exited')
    expect(result.output).toContain('sdkts-process')
    const handle = sandbox.process.connect({ sandboxId: 'sdkts', processId: result.processId })
    const replay = await handle.logs({ cursor: 'start' })
    expect(replay.items.map((item) => item.data).join('')).toContain('sdkts-process')
  })

  it('performs filesystem operations', async () => {
    if (!toolboxAvailable || toolboxUrl === undefined) return
    const sandbox = directSandbox(toolboxUrl)
    const path = `/tmp/sdkts-${Date.now()}.txt`
    await sandbox.fs.writeText(path, 'sdkts-fs')
    await expect(sandbox.fs.readText(path)).resolves.toBe('sdkts-fs')
    await sandbox.fs.remove(path)
  })

  it('performs git status against a local repo', async () => {
    if (!toolboxAvailable || toolboxUrl === undefined) return
    const sandbox = directSandbox(toolboxUrl)
    const repo = `/tmp/sdkts-repo-${Date.now()}`
    try {
      await sandbox.process.run(`rm -rf ${repo} && mkdir -p ${repo} && cd ${repo} && git init && git config user.email sdkts@example.invalid && git config user.name sdkts && printf hi > README.md && git add README.md`)
      const status = await sandbox.git.status(repo)
      expect(status.currentBranch).toBeTruthy()
    } finally {
      await sandbox.fs.remove(repo, { recursive: true }).catch(() => undefined)
    }
  })

  it('wires computer-use requests, queries, and binary downloads', async () => {
    const transport = transportStub()
    transport.get.mockResolvedValueOnce({ status: 'active', desktop: 'ready' })
    transport.get.mockResolvedValueOnce({ processName: 'wm', running: true })
    transport.get.mockResolvedValueOnce({ screenshot: 'abc', cursorPosition: { x: 1, y: 2 }, sizeBytes: 3 })
    transport.get.mockResolvedValueOnce({ displays: [{ id: 0, x: 0, y: 0, width: 1920, height: 1080, isActive: true }] })
    transport.get.mockResolvedValueOnce({ windows: [{ id: 14680067, title: 'Main', x: 0, y: 0, width: 10, height: 10, isActive: true }] })
    transport.get.mockResolvedValueOnce({ root: { id: '1', role: 'window', name: 'Main', bounds: { x: 0, y: 0, width: 10, height: 10 }, states: ['focused'] }, truncated: false })
    transport.getBytes.mockResolvedValueOnce(new Uint8Array([1, 2, 3]).buffer)
    const computerUse = new ComputerUseService(transport.computerUseObject)

    await expect(computerUse.status()).resolves.toEqual({ status: 'active', desktop: 'ready' })
    await expect(computerUse.service.status('wm')).resolves.toEqual({ processName: 'wm', running: true })
    await expect(computerUse.screen.screenshot({ showCursor: true })).resolves.toEqual({ screenshot: 'abc', cursorPosition: { x: 1, y: 2 }, sizeBytes: 3 })
    await expect(computerUse.display.info()).resolves.toEqual({ displays: [{ id: 0, x: 0, y: 0, width: 1920, height: 1080, isActive: true }] })
    await expect(computerUse.display.windows()).resolves.toEqual({ windows: [{ id: 14680067, title: 'Main', x: 0, y: 0, width: 10, height: 10, isActive: true }] })
    await expect(computerUse.a11y.tree({ scope: 'pid', pid: 44, maxDepth: 2 })).resolves.toMatchObject({ truncated: false })
    await expect(computerUse.recording.download('rec-1')).resolves.toEqual(new Uint8Array([1, 2, 3]))

    expect(transport.get.mock.calls[1]?.[0]).toBe('/computeruse/process/wm/status')
    expect(transport.get.mock.calls[2]?.[0]).toBe('/computeruse/screenshot')
    expect(transport.get.mock.calls[2]?.[1]).toMatchObject({ params: { show_cursor: 'true' } })
    expect(transport.get.mock.calls[3]?.[0]).toBe('/computeruse/display/info')
    expect(transport.get.mock.calls[4]?.[0]).toBe('/computeruse/display/windows')
    expect(transport.get.mock.calls[5]?.[0]).toBe('/computeruse/a11y/tree')
    expect(transport.get.mock.calls[5]?.[1]).toMatchObject({ params: { scope: 'pid', pid: 44, maxDepth: 2 } })
    expect(transport.getBytes).toHaveBeenCalledWith('/computeruse/recordings/rec-1/download')
  })

  it('routes screen.screenshot() by region and compression options', async () => {
    const transport = transportStub()
    transport.get.mockResolvedValue({ screenshot: 'abc' })
    const screen = new ComputerUseService(transport.computerUseObject).screen
    const region = { x: 1, y: 2, width: 30, height: 40 }

    await screen.screenshot()
    await screen.screenshot({ region, showCursor: false })
    await screen.screenshot({ format: 'jpeg', quality: 80, scale: 0.5 })
    await screen.screenshot({ region, quality: 60 })

    expect(transport.get.mock.calls).toEqual([
      ['/computeruse/screenshot', { params: {} }],
      ['/computeruse/screenshot/region', { params: { x: '1', y: '2', width: '30', height: '40', show_cursor: 'false' } }],
      ['/computeruse/screenshot/compressed', { params: { format: 'jpeg', quality: '80', scale: '0.5' } }],
      ['/computeruse/screenshot/region/compressed', { params: { x: '1', y: '2', width: '30', height: '40', quality: '60' } }],
    ])
  })

  it('shapes port and lsp requests and unwraps their payloads', async () => {
    const transport = transportStub()
    transport.get.mockResolvedValueOnce({ ports: [3000, 8080] }).mockResolvedValueOnce({ isInUse: true })
    const portsApi = {
      get: jest.fn()
        .mockResolvedValueOnce({ data: { id: 'ep', port: 8080, public: false, url: 'https://p', created_at: 'now' } }),
      post: jest.fn()
        .mockResolvedValueOnce({ data: { url: 'https://p', token: 'preview-token' } })
        .mockResolvedValueOnce({ data: { url: 'https://signed', token: 'signed-token', expires_at: 'later' } })
        .mockResolvedValueOnce({ data: undefined }),
      put: jest.fn().mockResolvedValue({ data: { id: 'ep', port: 8080, public: true, url: 'https://p', created_at: 'now' } }),
      delete: jest.fn().mockResolvedValue({ data: {} }),
    }
    const ports = new PortsService(transport.object, portsApi as never, 'sbx')

    await expect(ports.list()).resolves.toEqual({ items: [3000, 8080] })
    await expect(ports.isInUse(3999)).resolves.toBe(true)
    await expect(ports.isInUse(39.5)).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    await expect(ports.previewLink(8080)).resolves.toEqual({ url: 'https://p', token: 'preview-token' })
    await expect(ports.signedPreviewLink(8080, { expiresInSeconds: 300 })).resolves.toEqual({ url: 'https://signed', token: 'signed-token', expiresAt: 'later' })
    await expect(ports.setPublic(8080, { public: true })).resolves.toEqual({ port: 8080, public: true, url: 'https://p', createdAt: 'now' })
    await expect(ports.expireSignedPreviewLink(8080, 'tok/en')).resolves.toBeUndefined()
    await ports.unexpose(8080)
    expect(transport.get.mock.calls[0]).toEqual(['/port'])
    expect(transport.get.mock.calls[1]).toEqual(['/port/3999/in-use'])
    expect(portsApi.get).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080')
    expect(portsApi.post).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080/preview-url')
  expect(portsApi.post).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080/signed-preview-urls', { expires_in_seconds: 300 })
    expect(portsApi.put).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080', { public: true })
    expect(portsApi.post).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080/signed-preview-urls/revoke', { token: 'tok/en' })
    expect(portsApi.delete).toHaveBeenCalledWith('/sandboxes/sbx/ports/8080')

    const lspTransport = transportStub()
    lspTransport.post
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce({ isIncomplete: false, items: [{ label: 'append' }] })
      .mockResolvedValueOnce(undefined)
    lspTransport.get
      .mockResolvedValueOnce([{ name: 'main', kind: 12, location: { uri: 'file:///p/a.py', range: { start: { line: 0, character: 0 }, end: { line: 0, character: 4 } } } }])
      .mockResolvedValueOnce([])
    const lsp = new LspService(lspTransport.object)
    const server = await lsp.start('python', '/p')

    await server.openDocument('file:///p/a.py')
    await server.closeDocument('file:///p/a.py')
    await expect(server.completions('file:///p/a.py', { line: 2, character: 5 })).resolves.toEqual({ isIncomplete: false, items: [{ label: 'append' }] })
    await expect(server.completions('file:///p/a.py', { line: -1, character: 5 })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    await expect(server.documentSymbols('file:///p/a.py')).resolves.toHaveLength(1)
    await expect(server.workspaceSymbols('main')).resolves.toEqual([])
    await server[Symbol.asyncDispose]()
    expect(lspTransport.post.mock.calls[0]).toEqual(['/lsp/start', { languageId: 'python', pathToProject: '/p' }])
    expect(lspTransport.post.mock.calls[1]).toEqual(['/lsp/did-open', { languageId: 'python', pathToProject: '/p', uri: 'file:///p/a.py' }])
    expect(lspTransport.post.mock.calls[2]).toEqual(['/lsp/did-close', { languageId: 'python', pathToProject: '/p', uri: 'file:///p/a.py' }])
    expect(lspTransport.post.mock.calls[3]).toEqual(['/lsp/completions', { languageId: 'python', pathToProject: '/p', uri: 'file:///p/a.py', position: { line: 2, character: 5 } }])
    expect(lspTransport.post.mock.calls[4]).toEqual(['/lsp/stop', { languageId: 'python', pathToProject: '/p' }])
    expect(lspTransport.get.mock.calls[0]?.[0]).toBe('/lsp/document-symbols?languageId=python&pathToProject=%2Fp&uri=file%3A%2F%2F%2Fp%2Fa.py')
    expect(lspTransport.get.mock.calls[1]?.[0]).toBe('/lsp/workspacesymbols?query=main&languageId=python&pathToProject=%2Fp')
  })

  it('runs python through the interpreter (kind=code)', async () => {
    if (!toolboxAvailable || toolboxUrl === undefined) return
    const sandbox = directSandbox(toolboxUrl)
    let result
    try {
      result = await sandbox.interpreter.run('print(40 + 2)', { language: 'python', timeout: 180_000 })
    } catch (error) {
      // Interpreter availability depends on the selected sandbox image.
      if (error instanceof KernelUnavailableError) return
      throw error
    }
    expect(result.process.reason).toBe('exited')
    expect(result.process.exitCode).toBe(0)
    expect(result.logs.stdout).toContain('42')

    // Implicit-session state persists across one-shot runs.
    await sandbox.interpreter.run('x = 11', { language: 'python', timeout: 60_000 })
    const second = await sandbox.interpreter.run('print(x * 4)', { language: 'python', timeout: 60_000 })
    expect(second.logs.stdout).toContain('44')

    const failed = await sandbox.interpreter.run('1/0', { language: 'python', timeout: 60_000 })
    expect(failed.process.exitCode).toBe(1)
    expect(failed.error?.name).toBe('ZeroDivisionError')
  }, 240_000)

  it('creates snapshots through the sandbox snapshots route', async () => {
    const client = new Daytona({ apiKey: 'key' })
    const api = clientInstance(client)
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, api, undefined, 'key')
    const post = jest.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { id: 'snap-full', status: 'pending' } })
    jest.spyOn(api, 'get').mockResolvedValueOnce({ data: snapshotResponse({ id: 'snap-full', name: 'full' }) })

    const snapshot = await sandbox.createSnapshot('full')

    expect(snapshot).toBeInstanceOf(Snapshot)
    expect(snapshot.id).toBe('snap-full')
    expect(post.mock.calls).toEqual([['/sandboxes/sbx/snapshots', { name: 'full' }]])
  })
})

function directSandbox(toolboxUrl: string): Sandbox {
  const client = new Daytona({ apiKey: 'key' })
  return lazySandbox(client, 'sdkts', { toolboxUrl })
}

function expectSdkProtocolError(action: () => unknown, message: string): void {
  let thrown: unknown
  try {
    action()
  } catch (error) {
    thrown = error
  }

  expect(thrown).toBeInstanceOf(InternalServerError)
  expect(thrown).toMatchObject({
    message,
    statusCode: 500,
    source: 'SDK',
  })
}

describe('computer-use namespace', () => {
  it('rejects impossible desktop lifetime objects at compile time', () => {
    const impossibleCause: ComputerUseDesktopFailure = {
      code: 'start_failed',
      rawCode: 'start_failed',
      message: 'not actually failed',
    }
    // @ts-expect-error failed desktop states require a cause
    const failedWithoutCause: ComputerUseStatusResponse = {
      status: 'inactive',
      desktop: 'failed',
    }
    // @ts-expect-error active status is legal only with the ready desktop state
    const activeNonReady: ComputerUseStatusResponse = {
      status: 'active',
      desktop: 'starting',
    }
    // @ts-expect-error non-failed desktop states cannot carry a cause
    const causeOnNonFailed: ComputerUseStatusResponse = {
      status: 'inactive',
      desktop: 'not_started',
      cause: impossibleCause,
    }
    // @ts-expect-error the unknown variant keeps the wire values on raw siblings
    const unknownWithoutRaw: ComputerUseStatusResponse = {
      status: 'unknown',
      desktop: 'unknown',
    }

    expect([failedWithoutCause, activeNonReady, causeOnNonFailed, unknownWithoutRaw]).toHaveLength(4)
  })

  it('forces an exhaustive desktop lifetime switch to handle the unknown pair', () => {
    const describeStatus = (response: ComputerUseStatusResponse): string => {
      switch (response.desktop) {
        case 'not_started':
        case 'starting':
          return response.desktop
        case 'ready':
          return 'ready'
        case 'failed':
          return `failed: ${response.cause.code}`
        case 'unknown':
          return `unknown: ${response.rawStatus}/${response.rawDesktop}`
        default: {
          const exhaustive: never = response
          throw new Error(`unhandled desktop lifetime variant: ${JSON.stringify(exhaustive)}`)
        }
      }
    }

    expect(describeStatus({ status: 'active', desktop: 'ready' })).toBe('ready')
    expect(
      describeStatus({
        status: 'unknown',
        desktop: 'unknown',
        rawStatus: 'inactive',
        rawDesktop: 'rebooting',
      }),
    ).toBe('unknown: inactive/rebooting')
  })

  it('exports the desktop lifetime types with unknown failure sentinels plus raw codes', () => {
    const states: ComputerUseDesktopState[] = [...KNOWN_COMPUTER_USE_DESKTOP_STATES]
    const failureCodes: ComputerUseDesktopFailureCode[] = [
      ...KNOWN_COMPUTER_USE_DESKTOP_FAILURE_CODES,
      'unknown',
    ]
    const cause: ComputerUseDesktopFailure = {
      code: 'unknown',
      rawCode: 'future_failure',
      message: 'a future addon supplied this diagnostic',
    }
    const status: ComputerUseStatusResponse = {
      status: 'inactive',
      desktop: 'failed',
      cause,
    }
    const restart: ComputerUseRestartResponse = {
      message: 'computer use restarted',
      status: 'active',
      desktop: 'ready',
    }

    expect(states).toEqual(KNOWN_COMPUTER_USE_DESKTOP_STATES)
    expect(failureCodes).toEqual([
      ...KNOWN_COMPUTER_USE_DESKTOP_FAILURE_CODES,
      'unknown',
    ])
    expect(status).toEqual({
      status: 'inactive',
      desktop: 'failed',
      cause: {
        code: 'unknown',
        rawCode: 'future_failure',
        message: 'a future addon supplied this diagnostic',
      },
    })
    expect(restart).toEqual({
      message: 'computer use restarted',
      status: 'active',
      desktop: 'ready',
    })
  })

  it('maps each legal desktop lifetime branch and preserves future fields', () => {
    expect(
      mapComputerUseStatusResponse({
        status: 'inactive',
        desktop: 'not_started',
        futureStatusField: 'kept',
      }),
    ).toEqual({
      status: 'inactive',
      desktop: 'not_started',
      extraFields: {
        futureStatusField: 'kept',
      },
    })

    expect(
      mapComputerUseStatusResponse({
        status: 'active',
        desktop: 'ready',
      }),
    ).toEqual({
      status: 'active',
      desktop: 'ready',
    })

    expect(
      mapComputerUseStatusResponse({
        status: 'inactive',
        desktop: 'failed',
        cause: {
          code: 'future_display_failure',
          message: 'display backend changed underneath the desktop',
          futureCauseField: 42,
        },
      }),
    ).toEqual({
      status: 'inactive',
      desktop: 'failed',
      cause: {
        code: 'unknown',
        rawCode: 'future_display_failure',
        message: 'display backend changed underneath the desktop',
        extraFields: {
          futureCauseField: 42,
        },
      },
    })
  })

  it('maps a valid desktop restart response and preserves future fields', () => {
    expect(
      mapComputerUseRestartResponse({
        message: 'computer use restarted',
        status: 'active',
        desktop: 'ready',
        futureRestartField: 42,
      }),
    ).toEqual({
      message: 'computer use restarted',
      status: 'active',
      desktop: 'ready',
      extraFields: {
        futureRestartField: 42,
      },
    })
  })

  it('rejects non-object status payloads and non-string required fields', () => {
    const expectedError =
      'Invalid computer-use status response: expected string status and desktop, where inactive/not_started-or-starting and active/ready carry no cause, inactive/failed carries a cause, and any cause is a plain object containing string code and message'
    const invalidPayloads: unknown[] = [
      undefined,
      null,
      true,
      42,
      'inactive',
      [],
      new Date(),
      {},
      { status: 'inactive' },
      { desktop: 'not_started' },
      { status: 1, desktop: 'not_started' },
      { status: 'inactive', desktop: 1 },
    ]

    for (const payload of invalidPayloads) {
      expectSdkProtocolError(() => mapComputerUseStatusResponse(payload), expectedError)
    }
  })

  it('rejects null and malformed failed causes with one stable error', () => {
    const expectedError =
      'Invalid computer-use status response: expected string status and desktop, where inactive/not_started-or-starting and active/ready carry no cause, inactive/failed carries a cause, and any cause is a plain object containing string code and message'
    const invalidCauses: unknown[] = [
      null,
      [],
      42,
      new Date(),
      {},
      { code: 'start_failed' },
      { message: 'desktop failed' },
      { code: 42, message: 'desktop failed' },
      { code: 'start_failed', message: 42 },
    ]

    for (const cause of invalidCauses) {
      expectSdkProtocolError(
        () =>
          mapComputerUseStatusResponse({
            status: 'inactive',
            desktop: 'failed',
            cause,
          }),
        expectedError,
      )
    }
  })

  it('rejects malformed desktop restart payloads with one stable error', () => {
    const expectedError =
      'Invalid computer-use restart response: expected a plain object with string message, status="active", and desktop="ready"'
    const invalidPayloads: unknown[] = [
      undefined,
      null,
      42,
      [],
      new Date(),
      {},
      { message: 42, status: 'active', desktop: 'ready' },
      { message: 'computer use restarted', status: 42, desktop: 'ready' },
      { message: 'computer use restarted', status: 'active', desktop: 42 },
      { message: 'computer use restarted', status: 'inactive', desktop: 'ready' },
      { message: 'computer use restarted', status: 'active', desktop: 'starting' },
    ]

    for (const payload of invalidPayloads) {
      expectSdkProtocolError(() => mapComputerUseRestartResponse(payload), expectedError)
    }
  })

  it('rejects a cause that contradicts a recognized desktop lifetime pair', () => {
    const expectedInvariant =
      'Invalid computer-use status response: expected string status and desktop, where inactive/not_started-or-starting and active/ready carry no cause, inactive/failed carries a cause, and any cause is a plain object containing string code and message'

    expectSdkProtocolError(
      () =>
        mapComputerUseStatusResponse({
          status: 'inactive',
          desktop: 'failed',
        }),
      expectedInvariant,
    )

    expectSdkProtocolError(
      () =>
        mapComputerUseStatusResponse({
          status: 'inactive',
          desktop: 'not_started',
          cause: {
            code: 'start_failed',
            message: 'not actually failed',
          },
        }),
      expectedInvariant,
    )

    expectSdkProtocolError(
      () =>
        mapComputerUseStatusResponse({
          status: 'active',
          desktop: 'ready',
          cause: null,
        }),
      expectedInvariant,
    )
  })

  it('reads an unrecognized desktop lifetime pair as unknown with the raw values kept', () => {
    // Given an unrecognized status value.
    expect(mapComputerUseStatusResponse({ status: 'future_status', desktop: 'ready' })).toEqual({
      status: 'unknown',
      desktop: 'unknown',
      rawStatus: 'future_status',
      rawDesktop: 'ready',
    })

    // Given an unrecognized desktop value.
    expect(
      mapComputerUseStatusResponse({ status: 'inactive', desktop: 'rebooting', futureField: 7 }),
    ).toEqual({
      status: 'unknown',
      desktop: 'unknown',
      rawStatus: 'inactive',
      rawDesktop: 'rebooting',
      extraFields: { futureField: 7 },
    })

    // Given two listed values in a combination the contract never produced.
    expect(mapComputerUseStatusResponse({ status: 'active', desktop: 'starting' })).toEqual({
      status: 'unknown',
      desktop: 'unknown',
      rawStatus: 'active',
      rawDesktop: 'starting',
    })

    expect(mapComputerUseStatusResponse({ status: 'inactive', desktop: 'ready' })).toEqual({
      status: 'unknown',
      desktop: 'unknown',
      rawStatus: 'inactive',
      rawDesktop: 'ready',
    })

    // Given a failure attached to the unrecognized state, it survives.
    expect(
      mapComputerUseStatusResponse({
        status: 'active',
        desktop: 'failed',
        cause: { code: 'future_display_failure', message: 'display backend changed' },
      }),
    ).toEqual({
      status: 'unknown',
      desktop: 'unknown',
      rawStatus: 'active',
      rawDesktop: 'failed',
      cause: {
        code: 'unknown',
        rawCode: 'future_display_failure',
        message: 'display backend changed',
      },
    })

    // Given an explicit null cause, it reads as absent (parity with Python).
    expect(
      mapComputerUseStatusResponse({ status: 'inactive', desktop: 'rebooting', cause: null }),
    ).toEqual({
      status: 'unknown',
      desktop: 'unknown',
      rawStatus: 'inactive',
      rawDesktop: 'rebooting',
    })
  })

  it('still rejects a malformed cause on an unrecognized desktop lifetime pair', () => {
    const expectedInvariant =
      'Invalid computer-use status response: expected string status and desktop, where inactive/not_started-or-starting and active/ready carry no cause, inactive/failed carries a cause, and any cause is a plain object containing string code and message'

    for (const cause of [[], 42, 'crashed', {}, { code: 42, message: 'x' }]) {
      expectSdkProtocolError(
        () => mapComputerUseStatusResponse({ status: 'inactive', desktop: 'rebooting', cause }),
        expectedInvariant,
      )
    }
  })

  it('maps all desktop states and preserves an unknown failure code as unknown plus rawCode', async () => {
    const transport = transportStub()
    const computerUse = new ComputerUseService(transport.computerUseObject)
    const responses = [
      { status: 'inactive', desktop: 'not_started' },
      { status: 'inactive', desktop: 'starting' },
      { status: 'active', desktop: 'ready' },
      {
        status: 'inactive',
        desktop: 'failed',
        cause: {
          code: 'future_display_failure',
          message: 'display backend changed underneath the desktop',
        },
      },
    ] as const

    for (const response of responses) {
      transport.get.mockResolvedValueOnce(response)
      if (response.desktop === 'failed') {
        await expect(computerUse.status()).resolves.toEqual({
          status: 'inactive',
          desktop: 'failed',
          cause: {
            code: 'unknown',
            rawCode: 'future_display_failure',
            message: 'display backend changed underneath the desktop',
          },
        })
      } else {
        await expect(computerUse.status()).resolves.toEqual(response)
      }
    }

    expect(transport.get.mock.calls).toEqual([
      ['/computeruse/status'],
      ['/computeruse/status'],
      ['/computeruse/status'],
      ['/computeruse/status'],
    ])
  })

  it('posts the desktop restart route and maps its ready response', async () => {
    const transport = transportStub()
    const computerUse = new ComputerUseService(transport.computerUseObject)
    const response = {
      message: 'computer use restarted',
      status: 'active',
      desktop: 'ready',
    } as const
    transport.post.mockResolvedValueOnce(response)

    await expect(computerUse.restart()).resolves.toEqual(response)
    expect(transport.post.mock.calls).toEqual([['/computeruse/restart']])
  })

  it('maps the full computer-use process-status field set and keeps future additions', () => {
    expect(
      mapComputerUseLifecycleResponse({
        message: 'computer use started',
        status: {
          xvfb: {
            Running: true,
            Priority: 1,
            AutoRestart: false,
            Pid: 42,
            Failed: true,
            RestartAttempts: 3,
            RestartBackoffMs: 1500,
            Status: 'failed',
            ExtraFutureField: 'kept',
          },
        },
      }),
    ).toEqual({
      message: 'computer use started',
      status: {
          xvfb: {
            running: true,
            priority: 1,
            autoRestart: false,
            pid: 42,
            failed: true,
            restartAttempts: 3,
            restartBackoffMs: 1500,
            status: 'failed',
            extraFields: { extraFutureField: 'kept' },
          },
        },
      })
  })

  it('posts grouped computer-use controls with exact wire field names', async () => {
    const transport = transportStub()
    transport.post.mockResolvedValueOnce({}).mockResolvedValueOnce({}).mockResolvedValueOnce({ matches: [], truncated: false })
    const computerUse = new ComputerUseService(transport.computerUseObject)

    await computerUse.mouse.click({ x: 10, y: 20, button: 'left', double: true })
    await computerUse.keyboard.key({ key: 'K', modifiers: ['ctrl', 'shift'] })
    await computerUse.a11y.find({ scope: 'all', role: 'button', name: 'Save', nameMatch: 'substring', states: ['enabled'], limit: 5 })
    await computerUse.recording.start({ label: 'demo' })

    expect(transport.post.mock.calls[0]).toEqual(['/computeruse/mouse/click', { x: 10, y: 20, button: 'left', double: true }])
    expect(transport.post.mock.calls[1]).toEqual(['/computeruse/keyboard/key', { key: 'K', modifiers: ['ctrl', 'shift'] }])
    expect(transport.post.mock.calls[2]).toEqual(['/computeruse/a11y/find', { scope: 'all', role: 'button', name: 'Save', nameMatch: 'substring', states: ['enabled'], limit: 5 }])
    expect(transport.post.mock.calls[3]).toEqual(['/computeruse/recordings/start', { label: 'demo' }])
  })

  it('returns valueTruncated as a boolean on a11y tree and find nodes, children included', async () => {
    const transport = transportStub()
    const bounds = { x: 0, y: 0, width: 10, height: 10 }
    const cut = { id: 'cut', role: 'textField', name: 'Body', value: 'abcd', valueTruncated: true, bounds, states: [] }
    const leaf = { id: 'leaf', role: 'staticText', name: 'Save', bounds, states: [] }
    const plain = { id: 'plain', role: 'button', name: 'Save', bounds, states: [], children: [leaf] }
    transport.get.mockResolvedValueOnce({ root: { id: 'root', role: 'window', name: 'Main', bounds, states: [], children: [cut, plain] }, truncated: false })
    transport.post.mockResolvedValueOnce({ matches: [cut, plain], truncated: true })
    const computerUse = new ComputerUseService(transport.computerUseObject)

    const mappedCut = { ...cut, valueTruncated: true }
    const mappedPlain = { ...plain, valueTruncated: false, children: [{ ...leaf, valueTruncated: false }] }
    await expect(computerUse.a11y.tree({ scope: 'focused' })).resolves.toStrictEqual({
      root: { id: 'root', role: 'window', name: 'Main', bounds, states: [], valueTruncated: false, children: [mappedCut, mappedPlain] },
      truncated: false,
    })
    await expect(computerUse.a11y.find({ scope: 'all' })).resolves.toStrictEqual({ matches: [mappedCut, mappedPlain], truncated: true })
  })
})

describe('sandbox.docker', () => {
  const status = (state: string, extra: Record<string, unknown> = {}) => ({ state, owner: 'daemon', ...extra })

  it('is a toolbox namespace on the sandbox handle', () => {
    const sandbox = new Sandbox({ id: 'sbx', status: 'running' }, undefined, undefined, 'key', undefined, { toolboxUrl: 'http://toolbox/sbx' })
    expect(sandbox.docker).toBeInstanceOf(DockerService)
    expect(sandbox.docker).toBe(sandbox.docker)
    const bare = new Sandbox({ id: 'sbx', status: 'running' }, undefined, undefined, 'key')
    expect(() => bare.docker).toThrow('sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolboxUrl)')
  })

  it('status() reads GET /docker/status without waiting', async () => {
    const transport = transportStub()
    transport.get.mockResolvedValueOnce(status('failed', { reason: 'service failed', processId: 'p-1' }))
    const docker = new DockerService(transport.object)
    await expect(docker.status()).resolves.toEqual({ state: 'failed', rawState: 'failed', owner: 'daemon', rawOwner: 'daemon', reason: 'service failed', processId: 'p-1' })
    expect(transport.get.mock.calls[0]).toEqual(['/docker/status'])
  })

  it('waitUntilReady() long-polls through starting and returns the ready status', async () => {
    const transport = transportStub()
    transport.get
      .mockImplementationOnce(async () => { jest.setSystemTime(Date.now() + 1_000); return status('starting', { processId: 'p-1' }) })
      .mockResolvedValueOnce(status('ready', { processId: 'p-1' }))
    const docker = new DockerService(transport.object)
    await expect(docker.waitUntilReady({ timeout: 10_000 })).resolves.toEqual({ state: 'ready', rawState: 'ready', owner: 'daemon', rawOwner: 'daemon', processId: 'p-1', reason: undefined })
    expect(transport.get.mock.calls).toEqual([
      ['/docker/status?waitMs=1000', { timeout: 1_000 + WAIT_READ_SLACK_MS }],
      ['/docker/status?waitMs=1000', { timeout: 1_000 + WAIT_READ_SLACK_MS }],
    ])
  })

  it('waitUntilReady() spaces deferred replies and keeps polling', async () => {
    const transport = transportStub()
    transport.get
      .mockResolvedValueOnce(status('deferred', { owner: 'none', reason: 'deferred' }))
      .mockResolvedValueOnce(status('ready'))
    const docker = new DockerService(transport.object)
    const waiting = docker.waitUntilReady()
    await jest.advanceTimersByTimeAsync(499)
    expect(transport.get).toHaveBeenCalledTimes(1)
    await jest.advanceTimersByTimeAsync(1)
    await expect(waiting).resolves.toMatchObject({ state: 'ready' })
    expect(transport.get).toHaveBeenCalledTimes(2)
  })

  it.each(['failed', 'not_present', 'disabled', 'unmanaged', 'unsupported'])('waitUntilReady() raises DockerNotReadyError on terminal state %s', async (state) => {
    const transport = transportStub()
    transport.get.mockResolvedValue(status(state, { reason: 'why' }))
    const failure = new DockerService(transport.object).waitUntilReady({ timeout: 5_000 })
    await expect(failure).rejects.toBeInstanceOf(publicSdk.DockerNotReadyError)
    await expect(failure).rejects.toBeInstanceOf(publicSdk.ConflictError)
    await expect(failure).rejects.toMatchObject({ statusCode: 409, code: 'CONFLICT', source: 'SDK', status: { state, reason: 'why' } })
    expect(transport.get).toHaveBeenCalledTimes(1)
  })

  it('formats Docker error messages with the raw state for an unlisted value', () => {
    const status = { state: 'unknown', rawState: 'paused', owner: 'daemon', rawOwner: 'daemon', reason: 'why' } satisfies publicSdk.DockerStatus
    expect(new publicSdk.DockerNotReadyError(status).message).toBe('docker is paused: why')
    expect(new publicSdk.DockerWaitTimeoutError(status).message).toBe('docker did not become ready within the requested timeout (last state: paused)')
  })

  it('waitUntilReady() raises DockerWaitTimeoutError with the last status when the budget runs out', async () => {
    const transport = transportStub()
    transport.get.mockImplementation(async () => { jest.setSystemTime(Date.now() + 1_000); return status('starting') })
    const failure = new DockerService(transport.object).waitUntilReady({ timeout: 1_500 })
    await expect(failure).rejects.toBeInstanceOf(publicSdk.DockerWaitTimeoutError)
    await expect(failure).rejects.toBeInstanceOf(RequestTimeoutError)
    await expect(failure).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK', status: { state: 'starting' } })
    // Second poll holds only for what is left of the budget.
    expect(transport.get.mock.calls.map((call) => call[0])).toEqual(['/docker/status?waitMs=1000', '/docker/status?waitMs=500'])
  })

  it('waitUntilReady() keeps polling an unknown deferred reason until ready', async () => {
    const transport = transportStub()
    transport.get
      .mockResolvedValueOnce(status('deferred', { owner: 'none', reason: 'waiting-for-resource' }))
      .mockResolvedValueOnce(status('ready'))
    const waiting = new DockerService(transport.object).waitUntilReady({ timeout: 5_000 })
    await jest.advanceTimersByTimeAsync(499)
    expect(transport.get).toHaveBeenCalledTimes(1)
    await jest.advanceTimersByTimeAsync(1)
    await expect(waiting).resolves.toMatchObject({ state: 'ready' })
    expect(transport.get).toHaveBeenCalledTimes(2)
  })

  it('waitUntilReady() caps each request timeout at the deadline plus a small grace', async () => {
    const transport = transportStub()
    transport.get.mockImplementation(async () => { jest.setSystemTime(Date.now() + 1_000); return status('starting') })
    const failure = new DockerService(transport.object).waitUntilReady({ timeout: 1_500 })
    await expect(failure).rejects.toBeInstanceOf(publicSdk.DockerWaitTimeoutError)
    expect(transport.get.mock.calls).toEqual([
      ['/docker/status?waitMs=1000', { timeout: 1_500 + DOCKER_WAIT_DEADLINE_GRACE_MS }],
      ['/docker/status?waitMs=500', { timeout: 500 + DOCKER_WAIT_DEADLINE_GRACE_MS }],
    ])
  })

  it('waitUntilReady() maps a transport timeout at the deadline to DockerWaitTimeoutError with the last status', async () => {
    const transport = transportStub()
    transport.get
      .mockImplementationOnce(async () => { jest.setSystemTime(Date.now() + 1_000); return status('starting') })
      .mockImplementationOnce(async () => { jest.setSystemTime(Date.now() + 750); throw new publicSdk.ClientTimeoutError() })
    const failure = new DockerService(transport.object).waitUntilReady({ timeout: 1_500 })
    await expect(failure).rejects.toBeInstanceOf(publicSdk.DockerWaitTimeoutError)
    await expect(failure).rejects.toMatchObject({ status: { state: 'starting' } })
  })

  it('waitUntilReady() rethrows a transport timeout that happens before the deadline', async () => {
    const transport = transportStub()
    const stalled = new publicSdk.ClientTimeoutError()
    transport.get.mockImplementationOnce(async () => { jest.setSystemTime(Date.now() + 6_000); throw stalled })
    await expect(new DockerService(transport.object).waitUntilReady({ timeout: 60_000 })).rejects.toBe(stalled)
  })

  it('waitUntilReady() against a stalling server ends near the deadline with DockerWaitTimeoutError', async () => {
    jest.useRealTimers()
    const pending: ServerResponse[] = []
    const server: Server = createServer((_req, res) => { pending.push(res) })
    await new Promise<void>((resolveListen) => server.listen(0, '127.0.0.1', resolveListen))
    try {
      const { port } = server.address() as AddressInfo
      const docker = new DockerService(new HttpTransport(`http://127.0.0.1:${port}`, 'key'))
      const started = Date.now()
      const failure = docker.waitUntilReady({ timeout: 1_000 })
      await expect(failure).rejects.toBeInstanceOf(publicSdk.DockerWaitTimeoutError)
      await expect(failure).rejects.toMatchObject({ statusCode: 408, code: 'REQUEST_TIMEOUT', status: undefined })
      const overshoot = Date.now() - started - 1_000
      expect(overshoot).toBeGreaterThanOrEqual(0)
      expect(overshoot).toBeLessThanOrEqual(500)
    } finally {
      for (const res of pending) res.destroy()
      server.closeAllConnections()
      await new Promise<void>((resolveClose) => server.close(() => resolveClose()))
    }
  })

  it('waitUntilReady() validates its timeout', async () => {
    const docker = new DockerService(transportStub().object)
    await expect(docker.waitUntilReady({ timeout: -1 })).rejects.toMatchObject({ code: 'BAD_REQUEST', source: 'SDK' })
    await expect(docker.waitUntilReady({ timeout: Number.NaN })).rejects.toThrow('timeout must be a non-negative finite number')
  })
})
