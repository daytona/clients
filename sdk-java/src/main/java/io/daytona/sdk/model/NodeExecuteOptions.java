// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import java.util.function.BiConsumer;

/** Execution options for all current nodes; callbacks receive rank and chunk separately. */
public final class NodeExecuteOptions {
    private long timeoutSeconds;
    private BiConsumer<Integer, String> onStdout;
    private BiConsumer<Integer, String> onStderr;

    /** Zero disables the execution limit. Setup and cleanup each have a ten-second request bound. */
    public long getTimeoutSeconds() { return timeoutSeconds; }
    public void setTimeoutSeconds(long value) {
        if (value < 0) throw new io.daytona.sdk.exception.DaytonaException("Timeout must be non-negative");
        timeoutSeconds = value;
    }
    public BiConsumer<Integer, String> getOnStdout() { return onStdout; }
    public void setOnStdout(BiConsumer<Integer, String> value) { onStdout = value; }
    public BiConsumer<Integer, String> getOnStderr() { return onStderr; }
    public void setOnStderr(BiConsumer<Integer, String> value) { onStderr = value; }
}
