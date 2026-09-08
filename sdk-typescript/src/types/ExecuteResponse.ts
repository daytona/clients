/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Chart } from './Charts'

/**
 * Artifacts from the command execution.
 *
 * @interface
 * @property stdout - Standard output from the command, same as `result` in `ExecuteResponse`
 * @property charts - List of chart metadata from matplotlib
 */
export interface ExecutionArtifacts {
  stdout: string
  charts?: Chart[]
}

/**
 * Response from the command execution.
 *
 * @interface
 * @property exitCode - The exit code from the command execution
 * @property result - Combined stdout and stderr from the command execution (interleaved)
 * @property stdout - Standard output only; undefined when the sandbox daemon predates split streams
 * @property stderr - Standard error only; undefined when the sandbox daemon predates split streams
 * @property artifacts - Artifacts from the command execution
 */
export interface ExecuteResponse {
  exitCode: number
  result: string
  stdout?: string
  stderr?: string
  artifacts?: ExecutionArtifacts
}
