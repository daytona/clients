/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as api from './gen/api-types.js'
import type { AuditActionList, OrganizationLimits, ProjectApiKey, ProjectBudget, UsageLimit } from './accountTypes.js'
import { badRequest } from './options.js'

// The malformed-response rule: a response missing a structure the mapper
// reads is the SDK's BadRequestError naming the field, never a raw TypeError.
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function objectAt(value: unknown, path: readonly string[], what: string): Record<string, unknown> {
  let current: unknown = value
  for (const key of path) current = typeof current === 'object' && current !== null ? Reflect.get(current, key) : undefined
  if (!isObject(current)) throw badRequest(path.length === 0 ? `${what} response is not a JSON object` : `${what} response is missing the ${path.join('.')} object`)
  return current
}

function arrayAt(value: unknown, path: readonly string[], what: string): unknown[] {
  const parent = objectAt(value, path.slice(0, -1), what)
  const array = parent[path[path.length - 1]]
  if (!Array.isArray(array)) throw badRequest(`${what} response is missing the ${path.join('.')} array`)
  return array
}

export function auditActionList(data: api.AuditActionList): AuditActionList {
  arrayAt(data, ['targets'], 'audit actions').forEach((_group, index) => arrayAt(data, ['targets', String(index), 'actions'], 'audit actions'))
  return { targets: data.targets.map((group) => ({ targetType: group.target_type, actions: [...group.actions] })) }
}

export function projectApiKey(data: api.ProjectApiKeyWithContext): ProjectApiKey {
  arrayAt(data, ['project_permissions'], 'API key')
  objectAt(data, ['context'], 'API key')
  return {
    id: data.id,
    name: data.name,
    projectId: data.project_id,
    projectPermissions: [...data.project_permissions],
    createdAt: data.created_at,
    createdBy: data.created_by ?? undefined,
    expiresAt: data.expires_at ?? undefined,
    lastUsedAt: data.last_used_at ?? undefined,
    revokedAt: data.revoked_at ?? undefined,
    keyPrefix: data.key_prefix ?? undefined,
    keySuffix: data.key_suffix ?? undefined,
    serviceAccountId: data.service_account_id ?? undefined,
    serviceAccountName: data.service_account_name ?? undefined,
    context: { orgId: data.context.org_id, orgName: data.context.org_name, projectName: data.context.project_name },
  }
}

export function projectBudget(data: api.ProjectBudget): ProjectBudget {
  objectAt(data, [], 'project budget')
  return {
    monthlyLimitCents: data.monthly_limit_cents ?? undefined,
    periodStartDate: data.period_start_date ?? undefined,
    spentCents: data.spent_cents ?? undefined,
    createdAt: data.created_at ?? undefined,
    updatedAt: data.updated_at ?? undefined,
    evaluatedAt: data.evaluated_at ?? undefined,
    exceededAt: data.exceeded_at ?? undefined,
    stopAt: data.stop_at ?? undefined,
  }
}

function usageLimit(data: { readonly limit: number | null; readonly usage: number }): UsageLimit {
  return { limit: data.limit ?? undefined, usage: data.usage }
}

const REQUIRED_LIMITS: readonly (readonly string[])[] = [
  ['sandboxes', 'concurrent'], ['sandboxes', 'total'], ['image_builds', 'concurrent'],
  ['storage', 'volumes'], ['storage', 'images'], ['storage', 'snapshots'],
  ['secrets_per_project'], ['api_keys_per_project'], ['api_keys_per_organization'],
]

export function organizationLimits(data: api.OrganizationLimits): OrganizationLimits {
  for (const path of REQUIRED_LIMITS) objectAt(data, path, 'organization limits')
  for (const optional of ['sandbox_resources', 'members']) {
    const value: unknown = Reflect.get(data, optional)
    if (value !== undefined && !isObject(value)) throw badRequest(`organization limits response has a ${optional} that is not an object`)
  }
  return {
    sandboxes: { concurrent: usageLimit(data.sandboxes.concurrent), total: usageLimit(data.sandboxes.total) },
    imageBuilds: { concurrent: usageLimit(data.image_builds.concurrent) },
    storage: { volumes: usageLimit(data.storage.volumes), images: usageLimit(data.storage.images), snapshots: usageLimit(data.storage.snapshots) },
    secretsPerProject: usageLimit(data.secrets_per_project),
    apiKeysPerProject: usageLimit(data.api_keys_per_project),
    apiKeysPerOrganization: usageLimit(data.api_keys_per_organization),
    sandboxResources: data.sandbox_resources === undefined ? undefined : {
      maxCores: data.sandbox_resources.max_cores,
      maxMemoryMib: data.sandbox_resources.max_memory_mib,
    },
    members: data.members === undefined ? undefined : {
      ...usageLimit(data.members),
      pendingInvitations: data.members.pending_invitations,
    },
  }
}
