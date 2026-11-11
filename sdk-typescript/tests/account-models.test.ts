/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { clientAt } from './helpers/client'
import { startFakeApi } from './networkPolicyTestServer'

describe('account response models', () => {
  it('maps the API key when the server returns snake_case fields', async () => {
    // Given
    const api = await startFakeApi([{ status: 200, body: {
      id: 'key-id', name: 'automation', project_id: 'project-id', project_permissions: ['read:sandboxes'],
      created_at: '2026-01-02T03:04:05Z', created_by: 'user-id', expires_at: '2027-01-01T00:00:00Z',
      last_used_at: '2026-02-03T00:00:00Z', revoked_at: null, key_prefix: 'prefix', key_suffix: 'suffix',
      service_account_id: 'service-id', service_account_name: 'builder',
      context: { org_id: 'org-id', org_name: 'organization', project_name: 'project' }, extra: 'ignored',
    } }])
    const client = clientAt(api.url)
    try {
      // When
      const key = await client.v2.currentApiKey()
      // Then
      expect(key).toEqual({
        id: 'key-id', name: 'automation', projectId: 'project-id', projectPermissions: ['read:sandboxes'],
        createdAt: '2026-01-02T03:04:05Z', createdBy: 'user-id', expiresAt: '2027-01-01T00:00:00Z',
        lastUsedAt: '2026-02-03T00:00:00Z', revokedAt: undefined, keyPrefix: 'prefix', keySuffix: 'suffix',
        serviceAccountId: 'service-id', serviceAccountName: 'builder',
        context: { orgId: 'org-id', orgName: 'organization', projectName: 'project' },
      })
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('maps budget dates and amounts when a budget is returned', async () => {
    // Given
    const api = await startFakeApi([{ status: 200, body: {
      monthly_limit_cents: 1200, period_start_date: '2026-11-01', spent_cents: 340,
      created_at: '2026-10-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
      evaluated_at: '2026-11-02T00:00:00Z', exceeded_at: null, stop_at: null,
    } }])
    const client = clientAt(api.url)
    try {
      // When
      const budget = await client.v2.getProjectBudget()
      // Then
      expect(budget).toEqual({ monthlyLimitCents: 1200, periodStartDate: '2026-11-01', spentCents: 340,
        createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-11-01T00:00:00Z',
        evaluatedAt: '2026-11-02T00:00:00Z', exceededAt: undefined, stopAt: undefined })
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('maps nested limits when organization usage is returned', async () => {
    // Given
    const usage = { limit: 10, usage: 2 }
    const api = await startFakeApi([{ status: 200, body: {
      sandboxes: { concurrent: usage, total: { limit: null, usage: 4 } }, image_builds: { concurrent: usage },
      storage: { volumes: usage, images: usage, snapshots: usage }, secrets_per_project: usage,
      api_keys_per_project: usage, api_keys_per_organization: usage,
      sandbox_resources: { max_cores: 4.5, max_memory_mib: 4608 },
      sandbox_timeout: { max_seconds: 86400, ceiling_seconds: 604800 },
      members: { limit: 10, usage: 2, pending_invitations: 3 },
    } }])
    const client = clientAt(api.url)
    try {
      // When
      const limits = await client.v2.getLimits({ organizationId: 'org-id' })
      // Then
      expect(limits).toEqual({
        sandboxes: { concurrent: usage, total: { limit: undefined, usage: 4 } }, imageBuilds: { concurrent: usage },
        storage: { volumes: usage, images: usage, snapshots: usage }, secretsPerProject: usage,
        apiKeysPerProject: usage, apiKeysPerOrganization: usage,
        sandboxResources: { maxCores: 4.5, maxMemoryMib: 4608 },
        members: { limit: 10, usage: 2, pendingInvitations: 3 },
      })
    } finally {
      await client.close()
      await api.close()
    }
  })

  it('maps action groups when audit actions are returned', async () => {
    // Given
    const api = await startFakeApi([{ status: 200, body: { targets: [{ target_type: 'sandbox', actions: ['create', 'stop'] }] } }])
    const client = clientAt(api.url)
    try {
      // When
      const actions = await client.v2.listAuditActions()
      // Then
      expect(actions).toEqual({ targets: [{ targetType: 'sandbox', actions: ['create', 'stop'] }] })
    } finally {
      await client.close()
      await api.close()
    }
  })
})
