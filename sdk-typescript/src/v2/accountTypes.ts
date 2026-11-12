/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

export interface AuditActionGroup {
  readonly targetType: string
  readonly actions: readonly string[]
}

export interface AuditActionList {
  readonly targets: readonly AuditActionGroup[]
}

export interface ProjectApiKeyContext {
  readonly orgId: string
  readonly orgName: string
  readonly projectName: string
}

export interface ProjectApiKey {
  readonly id: string
  readonly name: string
  readonly projectId: string
  readonly projectPermissions: readonly string[]
  readonly createdAt: string
  readonly createdBy?: string
  readonly expiresAt?: string
  readonly lastUsedAt?: string
  readonly revokedAt?: string
  readonly keyPrefix?: string
  readonly keySuffix?: string
  readonly serviceAccountId?: string
  readonly serviceAccountName?: string
  readonly context: ProjectApiKeyContext
}

export interface ProjectBudget {
  readonly monthlyLimitCents?: number
  readonly periodStartDate?: string
  readonly spentCents?: number
  readonly createdAt?: string
  readonly updatedAt?: string
  readonly evaluatedAt?: string
  readonly exceededAt?: string
  readonly stopAt?: string
}

export interface UsageLimit {
  readonly limit?: number
  readonly usage: number
}

export interface SandboxLimits {
  readonly concurrent: UsageLimit
  readonly total: UsageLimit
}

export interface ImageBuildLimits {
  readonly concurrent: UsageLimit
}

export interface StorageLimits {
  readonly volumes: UsageLimit
  readonly images: UsageLimit
  readonly snapshots: UsageLimit
}

export interface SandboxResourceLimits {
  readonly maxCores: number
  readonly maxMemoryMib: number
}

export interface MemberLimits extends UsageLimit {
  readonly pendingInvitations: number
}

export interface OrganizationLimits {
  readonly sandboxes: SandboxLimits
  readonly imageBuilds: ImageBuildLimits
  readonly storage: StorageLimits
  readonly secretsPerProject: UsageLimit
  readonly apiKeysPerProject: UsageLimit
  readonly apiKeysPerOrganization: UsageLimit
  readonly sandboxResources?: SandboxResourceLimits
  /** Mac reservations the organization holds: pending, active or ending. Renewals do not count again. */
  readonly macReservations?: UsageLimit
  readonly members?: MemberLimits
}
