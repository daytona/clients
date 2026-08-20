// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

/**
 * Collected result from the one-shot {@link Process#run(ProcessRunOptions)} workflow.
 *
 * <p>Use this when waiting and output collection belong in one call. For separately supervised
 * background work, keep a {@link ProcessHandle} from {@link Process#start(ProcessStartOptions)}.
 */
public final class ProcessRunResult extends ProcessOutput {
    private final String id;
    private final ProcessHandle handle;
    private final boolean timedOut;

    ProcessRunResult(String id, ProcessHandle handle, ProcessOutput output, boolean timedOut) {
        super(output.getStdout(), output.getStderr(), output.getExitCode(), output.getSignal(), output.getReason());
        this.id = id;
        this.handle = handle;
        this.timedOut = timedOut;
    }

    /** @return managed process ID for later {@link Process#connect(String)}, if needed */
    public String getId() { return id; }
    /** @return handle for post-run inspection or cleanup; use inherited getters for collected data */
    public ProcessHandle getHandle() { return handle; }
    /** @return whether the wait deadline elapsed; inspect output fields for any partial data */
    public boolean isTimedOut() { return timedOut; }
}
