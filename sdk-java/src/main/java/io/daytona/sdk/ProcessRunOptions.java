// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Options for the one-shot {@link Process#run(ProcessRunOptions)} workflow.
 *
 * <p>Use these instead of {@link ProcessStartOptions} when the SDK should wait and collect output.
 * Use {@code start} options for a background process you supervise through a handle.
 *
 * <p>A {@code null} wait timeout means no client-side deadline, while {@code 0} is a deadline that
 * has already elapsed and reports a timed-out result immediately. Negative values are rejected.
 */
public class ProcessRunOptions extends ProcessStartOptions {
    private Integer waitTimeoutMs;
    private Consumer<String> onStdout;
    private Consumer<String> onStderr;

    /** @return client wait deadline; inherited timeout controls process execution instead */
    public Integer getWaitTimeoutMs() { return waitTimeoutMs; }
    /** @param waitTimeoutMs client wait deadline; use inherited timeout for daemon-enforced execution timeout @return this instance */
    public ProcessRunOptions setWaitTimeoutMs(Integer waitTimeoutMs) { this.waitTimeoutMs = waitTimeoutMs; return this; }
    /** @return incremental stdout callback; use the result getter when only final output is needed */
    public Consumer<String> getOnStdout() { return onStdout; }
    /** @param onStdout incremental callback during run; omit to collect without streaming @return this instance */
    public ProcessRunOptions setOnStdout(Consumer<String> onStdout) { this.onStdout = onStdout; return this; }
    /** @return incremental stderr callback; use the result getter when only final output is needed */
    public Consumer<String> getOnStderr() { return onStderr; }
    /** @param onStderr incremental callback during run; omit to collect without streaming @return this instance */
    public ProcessRunOptions setOnStderr(Consumer<String> onStderr) { this.onStderr = onStderr; return this; }

    /** {@inheritDoc} */
    @Override public ProcessRunOptions setArgv(List<String> value) { super.setArgv(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setShellCommand(String value) { super.setShellCommand(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setShell(String value) { super.setShell(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setLogin(Boolean value) { super.setLogin(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setName(String value) { super.setName(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setSessionId(String value) { super.setSessionId(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setCwd(String value) { super.setCwd(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setEnv(Map<String, String> value) { super.setEnv(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setUser(String value) { super.setUser(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setStdin(String value) { super.setStdin(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setTimeoutMs(Integer value) { super.setTimeoutMs(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setKind(String value) { super.setKind(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setTerminal(ProcessTerminalOptions value) { super.setTerminal(value); return this; }
    /** {@inheritDoc} */
    @Override public ProcessRunOptions setKeepLogs(String value) { super.setKeepLogs(value); return this; }
}
