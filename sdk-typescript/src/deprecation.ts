/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const ROOT_NAMESPACE_WARNING_CODE = 'DAYTONA_ROOT_NAMESPACE'
const TARGET_WARNING_CODE = 'DAYTONA_CONFIG_TARGET'

let didWarnRootNamespace = false
let didWarnTargetConfig = false

function emitDeprecationWarning(message: string, code: string): void {
  const nodeProcess = typeof process === 'undefined' ? undefined : process
  if (nodeProcess && typeof nodeProcess.emitWarning === 'function') {
    nodeProcess.emitWarning(message, {
      type: 'DeprecationWarning',
      code,
    })
    return
  }

  console.warn(`DeprecationWarning: ${message}`)
}

export function warnRootNamespaceDeprecation(member: string, replacement = `daytona.v1.${member}`): void {
  if (didWarnRootNamespace) {
    return
  }

  didWarnRootNamespace = true
  emitDeprecationWarning(
    `Use \`${replacement}\` instead. The root form is removed in SDK 2.x.`,
    ROOT_NAMESPACE_WARNING_CODE,
  )
}

export function warnTargetDeprecation(): void {
  if (didWarnTargetConfig) {
    return
  }

  didWarnTargetConfig = true
  emitDeprecationWarning(
    'DaytonaConfig.target (and DAYTONA_TARGET) is deprecated and will be removed in a future version. Pass target per sandbox create call or regionId per snapshot create call instead.',
    TARGET_WARNING_CODE,
  )
}

export function resetRootNamespaceDeprecationWarningForTests(): void {
  didWarnRootNamespace = false
}

export function resetTargetDeprecationWarningForTests(): void {
  didWarnTargetConfig = false
}
