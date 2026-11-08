/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type * as api from './gen/api-types.js'
import type { AuditActionList, OrganizationLimits, ProjectApiKey, ProjectBudget, UsageLimit } from './accountTypes.js'

export function auditActionList(data: api.AuditActionList): AuditActionList {
  return { targets: data.targets.map((group) => ({ targetType: group.target_type, actions: [...group.actions] })) }
}

export function projectApiKey(data: api.ProjectApiKeyWithContext): ProjectApiKey {
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

export function organizationLimits(data: api.OrganizationLimits): OrganizationLimits {
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
