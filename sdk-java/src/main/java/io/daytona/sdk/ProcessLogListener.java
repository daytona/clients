// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

/**
 * Receives ordered replay-then-live events from a process log stream.
 *
 * <p>Use this callback with {@link ProcessHandle#streamLogs}; use paged {@code logs} when pull-based
 * replay and explicit cursor storage are preferable.
 */
@FunctionalInterface
public interface ProcessLogListener {
    /** @param event next log, state, or EOF event; persist its cursor for reconnect recovery */
    void onEvent(ProcessLogEvent event);
}
