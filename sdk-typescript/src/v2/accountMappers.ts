/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as api from './gen/api-types.js'
import type { AuditActionList, OrganizationLimits, ProjectApiKey, ProjectBudget, UsageLimit } from './accountTypes.js'
import { arrayOf, checkResponse, nested } from './responseFields.js'
import type { ResponseFields } from './responseFields.js'

// The contract fields each account mapper reads, leaves and array items
// included: a response that breaks them is the SDK's BadRequestError naming
// the field, never a raw TypeError.
const AUDIT_ACTIONS: ResponseFields = { targets: arrayOf(nested({ target_type: 'string', actions: arrayOf('string') })) }
const API_KEY: ResponseFields = {
  id: 'string', name: 'string', project_id: 'string', project_permissions: arrayOf('string'), created_at: 'string',
  created_by: 'string?', expires_at: 'string?', last_used_at: 'string?', revoked_at: 'string?', key_prefix: 'string?',
  key_suffix: 'string?', service_account_id: 'string?', service_account_name: 'string?',
  context: nested({ org_id: 'string', org_name: 'string', project_name: 'string' }),
}
const PROJECT_BUDGET: ResponseFields = {
  monthly_limit_cents: 'integer?', period_start_date: 'string?', spent_cents: 'integer?', created_at: 'string?',
  updated_at: 'string?', evaluated_at: 'string?', exceeded_at: 'string?', stop_at: 'string?',
}
const USAGE: ResponseFields = { limit: 'integer?', usage: 'integer' }
const ORGANIZATION_LIMITS: ResponseFields = {
  sandboxes: nested({ concurrent: nested(USAGE), total: nested(USAGE) }),
  image_builds: nested({ concurrent: nested(USAGE) }),
  storage: nested({ volumes: nested(USAGE), images: nested(USAGE), snapshots: nested(USAGE) }),
  secrets_per_project: nested(USAGE), api_keys_per_project: nested(USAGE), api_keys_per_organization: nested(USAGE),
  sandbox_resources: nested({ max_cores: 'number', max_memory_mib: 'integer' }, true),
  members: nested({ ...USAGE, pending_invitations: 'integer' }, true),
}

export function auditActionList(value: api.AuditActionList): AuditActionList {
  const data = checkResponse<api.AuditActionList>(value, AUDIT_ACTIONS, 'audit actions')
  return { targets: data.targets.map((group) => ({ targetType: group.target_type, actions: [...group.actions] })) }
}

export function projectApiKey(value: api.ProjectApiKeyWithContext): ProjectApiKey {
  const data = checkResponse<api.ProjectApiKeyWithContext>(value, API_KEY, 'API key')
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

export function projectBudget(value: api.ProjectBudget): ProjectBudget {
  const data = checkResponse<api.ProjectBudget>(value, PROJECT_BUDGET, 'project budget')
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

export function organizationLimits(value: api.OrganizationLimits): OrganizationLimits {
  const data = checkResponse<api.OrganizationLimits>(value, ORGANIZATION_LIMITS, 'organization limits')
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
