// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package errors

// ErrCursorExpired identifies a retained-log cursor evicted from the process ledger.
// Callers replaying logs should resume from the first available cursor reported by
// the daemon instead of retrying the expired cursor.
var ErrCursorExpired = &DaytonaError{Source: SourceDaemon, Code: "CURSOR_EXPIRED"}
