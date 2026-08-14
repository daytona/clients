// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.toolbox.client.model.ProcessLogPage;
import io.daytona.toolbox.client.model.ProcessResult;
import io.daytona.toolbox.client.model.ProcessTerminalReason;

/**
 * Supervision handle for a managed process started with {@link Process#start(ProcessStartOptions)}.
 *
 * <p>Use a handle when you need lifecycle control, retained-log replay, stdin, or PTY attachment.
 * For a one-shot command with collected output, use {@link Process#run(ProcessRunOptions)} instead.
 * A lost handle can be recreated by process ID with {@link Process#connect(String)}.
 */
public final class ProcessHandle {
    private final String id;
    private final Process process;

    ProcessHandle(String id, Process process) {
        this.id = id;
        this.process = process;
    }

    /** @return identifier to persist for later {@link Process#connect(String)} calls */
    public String getId() { return id; }

    /**
     * Fetches current process metadata without replaying logs.
     * @return current process record; use {@link #output()} when collected output is also needed
     */
    public io.daytona.toolbox.client.model.Process getRecord() { return process.getRecord(id); }

    /**
     * Reads the first retained-log page using server defaults.
     * @return first page; use {@link #logs(String, Integer, String)} to page or resume explicitly
     */
    public ProcessLogPage logs() { return logs(null, null, null); }

    /**
     * Reads one retained-log page for replay or resumable polling.
     *
     * <p>Pass each page's next cursor into the following call. If the server reports
     * {@code CURSOR_EXPIRED}, restart from {@code "start"} for full retained replay or omit the
     * cursor to continue from the server's current retention boundary. Use {@link #streamLogs}
     * instead when replay must transition directly into live events.
     * @param cursor resume cursor, {@code "start"} for retained history, or {@code null}
     * @param limit maximum frames, or {@code null}
     * @param encoding text or base64, or {@code null}
     * @return retained-log page
     */
    public ProcessLogPage logs(String cursor, Integer limit, String encoding) {
        return process.logs(id, cursor, limit, encoding);
    }

    /**
     * Replays retained logs from a cursor, then follows live SSE events until EOF.
     *
     * <p>Use this instead of paged {@link #logs} for continuous supervision. On
     * {@code CURSOR_EXPIRED}, reconnect with {@code "start"} or no cursor according to whether
     * replay or only currently retained/live data is desired.
     * @param cursor resume cursor, {@code "start"} for full retained replay, or {@code null}
     * @param listener ordered event listener
     */
    public void streamLogs(String cursor, ProcessLogListener listener) {
        process.streamLogs(id, cursor, "base64", null, listener);
    }

    /**
     * Writes UTF-8 input to a process started with piped stdin.
     * @param data input data; use {@link #stdinEof()} when no further input will be sent
     */
    public void stdin(String data) { process.stdin(id, data); }

    /** Closes piped stdin; use {@link #stdin(String)} while additional input remains. */
    public void stdinEof() { process.stdinEof(id); }

    /**
     * Sends one signal without escalation configuration.
     * @param signal signal name, or {@code null} for SIGTERM; use {@link #kill(ProcessKillOptions)}
     *               when escalation is required
     */
    public void kill(String signal) { process.kill(id, new ProcessKillOptions().setSignal(signal == null ? "SIGTERM" : signal)); }

    /**
     * Sends a signal with optional timed escalation.
     * @param options signal and escalation options; use {@link #kill(String)} for one signal
     */
    public void kill(ProcessKillOptions options) { process.kill(id, options); }

    /**
     * Resizes a running PTY process; this method is invalid for exec and code processes.
     * @param cols terminal columns
     * @param rows terminal rows; use {@link #attachTerminal()} when interactive PTY I/O is needed
     */
    public void resize(int cols, int rows) { process.resize(id, cols, rows); }

    /**
     * Waits for terminal metadata but does not collect retained logs.
     *
     * <p>Call {@link #output()} after this method when both completion and collected output are
     * required; use {@link Process#run(ProcessRunOptions)} to perform that pairing automatically.
     * @param timeoutMs wait deadline in milliseconds, or {@code null}
     * @return terminal result
     */
    public ProcessResult waitFor(Integer timeoutMs) { return process.waitFor(id, timeoutMs); }

    /**
     * Replays retained logs and combines them with current exit metadata without waiting.
     *
     * <p>Pair this with {@link #waitFor(Integer)} for a background process, or use
     * {@link Process#run(ProcessRunOptions)} when waiting and collection should be one operation.
     * @return retained output so far plus current exit metadata
     */
    public ProcessOutput output() {
        io.daytona.toolbox.client.model.Process record = getRecord();
        ProcessOutputCollector.Collected logs = process.collectLogs(this, null, null);
        String reason = record.getReason() == null ? null : record.getReason().getValue();
        return new ProcessOutput(logs.stdout, logs.stderr, record.getExitCode(), record.getSignal(), reason);
    }

    /**
     * Releases the record and retained logs of a terminal process.
     *
     * <p>Use this after {@link #waitFor(Integer)} and {@link #output()} when a process started with
     * {@code start} no longer needs to be reconnected. Do not use it while later replay is needed.
     * @throws io.daytona.sdk.exception.DaytonaConflictException if the process is still running
     */
    public void cleanup() { process.cleanup(id); }

    /**
     * Attaches an interactive WebSocket to a PTY process; exec and code processes are rejected.
     * @return attached PTY handle; use {@link #streamLogs} for non-interactive replay/live output
     */
    public PtyHandle attachTerminal() { return process.attachTerminal(id); }

    ProcessOutput finishRunOutput(ProcessOutputCollector.Collected collected, ProcessResult result) {
        String reason = result == null || result.getReason() == null ? null : result.getReason().getValue();
        return new ProcessOutput(collected.stdout, collected.stderr,
                result == null ? null : result.getExitCode(),
                result == null ? null : result.getSignal(), reason);
    }

    static boolean timedOut(ProcessResult result) {
        return result != null && result.getReason() == ProcessTerminalReason.ReasonTimedOut;
    }
}
