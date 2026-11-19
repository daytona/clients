// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Go's released SandboxAPI interface and exported struct layouts are part of its source API.
// Generate membership operations separately and keep their metadata outside legacy structs.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

const [input, output] = process.argv.slice(2)
const spec = JSON.parse(readFileSync(input, 'utf8'))
const schemas = spec.components.schemas

for (const route of [
  '/sandbox/{sandboxIdOrName}/membership-operations/{operationId}',
  '/sandbox/{sandboxIdOrName}/nodes',
  '/sandbox/{sandboxIdOrName}/nodes/remove',
  '/sandbox/{sandboxIdOrName}/nodes/{rank}/replace',
]) {
  for (const operation of Object.values(spec.paths[route])) {
    operation.tags = ['Sandbox Membership']
  }
}

const membershipFields = [
  'degraded',
  'headSandboxId',
  'incarnation',
  'nodeCount',
  'nodeRank',
  'nodes',
  'topologyGeneration',
]
schemas.SandboxMembershipDetails = {
  type: 'object',
  properties: Object.fromEntries(membershipFields.map((field) => [field, schemas.Sandbox.properties[field]])),
}

for (const [model, fields] of Object.entries({
  CreateSandbox: ['cpuPerNode', 'memoryPerNode', 'diskPerNode'],
  RegionQuota: ['maxNodesPerSandbox'],
  RunnerHealthcheck: ['fabric'],
  Sandbox: membershipFields,
  UpdateSandboxStateDto: ['fabricFenced', 'incarnation'],
})) {
  for (const field of fields) delete schemas[model].properties[field]
}

for (const model of ['RunnerFabricHealth', 'RunnerFabricRail', 'SandboxGangMemberFailedWebhook']) {
  delete schemas[model]
}

const actions = schemas.AuditTargetScenario.properties.actions.items
actions.enum = actions.enum.filter((action) => !['add_nodes', 'remove_nodes', 'replace_node'].includes(action))
schemas.JobType.enum = schemas.JobType.enum.filter((type) => type !== 'UPDATE_SANDBOX_FABRIC')
schemas.WebhookEvent.enum = schemas.WebhookEvent.enum.filter((name) => name !== 'sandbox.gang-member-failed')
schemas.WebhookEvent['x-enum-varnames'] = schemas.WebhookEvent['x-enum-varnames'].filter(
  (name) => name !== 'SANDBOX_GANG_MEMBER_FAILED',
)

mkdirSync(dirname(output), { recursive: true })
writeFileSync(output, JSON.stringify(spec))
