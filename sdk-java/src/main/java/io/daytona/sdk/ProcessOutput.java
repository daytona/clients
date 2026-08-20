// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

/**
 * Snapshot of collected output and exit metadata.
 *
 * <p>Use this after {@link ProcessHandle#waitFor(Integer)} for supervised work. Use
 * {@link ProcessRunResult} when waiting and collection should happen in one call.
 */
public class ProcessOutput {
    private final String stdout;
    private final String stderr;
    private final Integer exitCode;
    private final String signal;
    private final String reason;

    ProcessOutput(String stdout, String stderr, Integer exitCode, String signal, String reason) {
        this.stdout = stdout;
        this.stderr = stderr;
        this.exitCode = exitCode;
        this.signal = signal;
        this.reason = reason;
    }

    /** @return collected stdout; use {@link #getStderr()} for the error channel */
    public String getStdout() { return stdout; }
    /** @return collected stderr; use {@link #getStdout()} for the normal-output channel */
    public String getStderr() { return stderr; }
    /** @return exit code, or {@code null} before a normal exit or when terminated by signal */
    public Integer getExitCode() { return exitCode; }
    /** @return terminating signal, or {@code null}; use {@link #getExitCode()} for normal exits */
    public String getSignal() { return signal; }
    /** @return terminal reason, or {@code null}; use code/signal getters for concrete status */
    public String getReason() { return reason; }
}
