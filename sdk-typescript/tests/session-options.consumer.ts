/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Compile-only checks: the public session handle is the only shell-session
// command entrypoint. These fail if an unsupported option becomes accepted.
import type {
  CreateSessionOptions, ProcessHandle, ProcessListOptions, ProcessStartOptions,
  RunOptions, Sandbox, SessionJSON, SessionRunOptions, SessionStartOptions,
} from '../src/v2/index'

type Assert<T extends true> = T
type Absent<T, K extends PropertyKey> = Extract<keyof T, K> extends never ? true : false
type _StartHasNoSession = Assert<Absent<ProcessStartOptions, 'sessionId'>>
type _RunHasNoSession = Assert<Absent<RunOptions, 'sessionId'>>
type _ExecHasNoSession = Assert<Absent<NonNullable<Parameters<Sandbox['exec']>[1]>, 'sessionId'>>
type _SessionRunSettings = Assert<Absent<SessionRunOptions, 'cwd' | 'env' | 'shell' | 'login' | 'user' | 'argv' | 'cmd' | 'sessionId'>>
type _SessionStartSettings = Assert<Absent<SessionStartOptions, 'cwd' | 'env' | 'shell' | 'login' | 'user' | 'argv' | 'cmd' | 'sessionId' | 'timeout'>>
type _SessionCreateSettings = Assert<Absent<CreateSessionOptions, 'user' | 'keepLogs' | 'executionTimeout'>>
type _RemovalVerb = Assert<Absent<ProcessHandle, 'cleanup'>>

export const sessionList: ProcessListOptions = { kind: 'session', sessionId: 'session-1' }
export const sessionJSON: SessionJSON = { sandboxId: 'sandbox-1', sessionId: 'session-1' }
