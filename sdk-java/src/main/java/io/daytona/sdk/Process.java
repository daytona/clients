// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.daytona.sdk.model.*;
import io.daytona.sdk.exception.DaytonaException;
import io.daytona.toolbox.client.api.ProcessApi;
import io.daytona.toolbox.client.model.CodeRunRequest;
import io.daytona.toolbox.client.model.CodeRunResponse;
import io.daytona.toolbox.client.model.CreateSessionRequest;
import io.daytona.toolbox.client.model.ExecuteRequest;
import io.daytona.toolbox.client.model.PtyCreateRequest;
import io.daytona.toolbox.client.model.PtyCreateResponse;
import io.daytona.toolbox.client.model.PtyListResponse;
import io.daytona.toolbox.client.model.PtyResizeRequest;
import io.daytona.toolbox.client.model.PtySessionInfo;
import io.daytona.toolbox.client.model.SessionSendInputRequest;
import io.daytona.toolbox.client.model.CreateProcessRequest;
import io.daytona.toolbox.client.model.KillProcessRequest;
import io.daytona.toolbox.client.model.ProcessKeepLogs;
import io.daytona.toolbox.client.model.ProcessKind;
import io.daytona.toolbox.client.model.ProcessLogFrame;
import io.daytona.toolbox.client.model.ProcessLogPage;
import io.daytona.toolbox.client.model.ProcessResult;
import io.daytona.toolbox.client.model.ProcessShellSelector;
import io.daytona.toolbox.client.model.ProcessStdinMode;
import io.daytona.toolbox.client.model.ProcessTerminalReason;
import io.daytona.toolbox.client.model.ProcessTerminalSize;
import io.daytona.toolbox.client.model.ResizeProcessRequest;
import io.daytona.toolbox.client.model.ProcessStdinRequest;
import okhttp3.Call;
import okhttp3.HttpUrl;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

import java.io.ByteArrayOutputStream;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * Process and session execution interface for a Sandbox.
 *
 * <p>Supports single-command execution, code execution, long-running sessions, and PTY terminal
 * sessions.
 */
public class Process {
    private static final int LOG_STREAM_NONE = 0;
    private static final int LOG_STREAM_STDOUT = 1;
    private static final int LOG_STREAM_STDERR = 2;
    private static final byte STDOUT_PREFIX_BYTE = 0x01;
    private static final byte STDERR_PREFIX_BYTE = 0x02;
    private static final int PREFIX_REPEAT_COUNT = 3;
    private static final long PTY_CONNECTION_TIMEOUT_SECONDS = 10;
    private static final String PTY_ENVS_SUBPROTOCOL_PREFIX = "X-Daytona-Pty-Envs~";
    // Capability token advertised on PTY WebSocket connects so the daemon sends the
    // "exited" control message; clients that don't send it only get the close frame.
    private static final String PTY_EXIT_CONTROL_SUBPROTOCOL = "X-Daytona-Pty-Exit-Control";
    private static final int MAX_LOG_REPLAY_PAGES = 10_000;
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final ProcessApi processApi;
    private final Sandbox sandbox;

    Process(ProcessApi processApi, Sandbox sandbox) {
        this.processApi = processApi;
        this.sandbox = sandbox;
    }

    /**
     * Starts a managed process in the background and returns the handle you supervise.
     *
     * <p>Use this instead of {@link #run(ProcessRunOptions)} when you need to stream logs, write
     * stdin, send signals, or reconnect later with {@link #connect(String)}. Logs default to
     * retention until {@link ProcessHandle#cleanup()}.
     * @param options process creation options
     * @return reconnectable process handle
     */
    public ProcessHandle start(ProcessStartOptions options) {
        ProcessStartOptions opts = options == null ? new ProcessStartOptions() : options;
        validateStartOptions(opts);
        io.daytona.toolbox.client.model.Process record =
                ExceptionMapper.callToolbox(() -> processApi.createProcess(toCreateRequest(opts)));
        return new ProcessHandle(record.getId(), this);
    }

    /**
     * Runs a one-shot process, waits for it, and returns collected stdout and stderr.
     *
     * <p>Use this instead of {@link #start(ProcessStartOptions)} when the caller only needs the
     * completed output. Its logs default to short-TTL retention after exit; use {@code start} when
     * the process must remain reconnectable or explicitly supervised.
     * @param options run options
     * @return collected process result, including partial output on wait timeout
     */
    public ProcessRunResult run(ProcessRunOptions options) {
        ProcessRunOptions opts = options == null ? new ProcessRunOptions() : options;
        if (opts.getWaitTimeoutMs() != null && opts.getWaitTimeoutMs() < 0) {
            throw new IllegalArgumentException("waitTimeoutMs must be non-negative");
        }
        if (opts.getKeepLogs() == null) opts.setKeepLogs("on_exit_ttl");
        ProcessHandle handle = start(opts);
        ProcessOutputCollector collector = new ProcessOutputCollector(opts.getOnStdout(), opts.getOnStderr());
        ProcessResult result;
        boolean timedOut = false;

        if (opts.getOnStdout() != null || opts.getOnStderr() != null) {
            timedOut = streamLogs(handle.getId(), "start", "base64", opts.getWaitTimeoutMs(), event -> {
                if ("log".equals(event.getType())) collector.accept(event.getFrame());
            });
            result = waitResult(handle, timedOut ? 1 : null);
        } else {
            result = waitResult(handle, opts.getWaitTimeoutMs());
            timedOut = ProcessHandle.timedOut(result);
            ProcessOutputCollector.Collected collected = collectLogs(handle, null, null);
            ProcessOutput output = handle.finishRunOutput(collected, result);
            return new ProcessRunResult(handle.getId(), handle, output, timedOut);
        }

        ProcessOutput output = handle.finishRunOutput(collector.finish(), result);
        return new ProcessRunResult(handle.getId(), handle, output, timedOut || ProcessHandle.timedOut(result));
    }

    /**
     * Gets a validated handle for an existing process ID.
     *
     * <p>Use this for ordinary lookup; {@link #connect(String)} is an equivalent alias with
     * identical behavior.
     * @param id process identifier
     * @return validated process handle
     */
    public ProcessHandle get(String id) {
        String processId = requireIdentifier(id, "id");
        getRecord(processId);
        return new ProcessHandle(processId, this);
    }

    /**
     * Reconnects to an existing process by ID.
     *
     * <p>This is an alias of {@link #get(String)}. Use either method when a
     * previous {@link #start(ProcessStartOptions)} handle is no longer in memory.
     * @param id process identifier
     * @return validated process handle
     */
    public ProcessHandle connect(String id) { return get(id); }

    /**
     * Lists process records matching optional server-side filters.
     *
     * <p>Use this overload instead of {@link #list()} when narrowing by state, kind, session, name,
     * or PID.
     * @param filter optional process filters
     * @return matching process records
     */
    public List<io.daytona.toolbox.client.model.Process> list(ProcessListFilter filter) {
        ProcessListFilter value = filter == null ? new ProcessListFilter() : filter;
        List<io.daytona.toolbox.client.model.Process> records = ExceptionMapper.callToolbox(() ->
                processApi.listProcesses(value.getState(), value.getKind(), value.getSessionId(),
                        value.getName(), value.getPid()));
        return records == null ? Collections.emptyList() : records;
    }

    /**
     * Lists all process records.
     *
     * <p>Use {@link #list(ProcessListFilter)} when only a subset is needed.
     * @return all process records
     */
    public List<io.daytona.toolbox.client.model.Process> list() { return list(null); }

    io.daytona.toolbox.client.model.Process getRecord(String id) {
        return ExceptionMapper.callToolbox(() -> processApi.getProcess(id));
    }

    ProcessLogPage logs(String id, String cursor, Integer limit, String encoding) {
        return ExceptionMapper.callToolbox(() -> processApi.readProcessLogs(id, cursor, limit, encoding, null));
    }

    void stdin(String id, String data) {
        ExceptionMapper.runToolbox(() -> processApi.sendProcessStdin(id, new ProcessStdinRequest().data(data)));
    }

    void stdinEof(String id) {
        ExceptionMapper.runToolbox(() -> processApi.sendProcessStdin(id, new ProcessStdinRequest().eof(true)));
    }

    void kill(String id, ProcessKillOptions options) {
        ProcessKillOptions value = options == null ? new ProcessKillOptions() : options;
        KillProcessRequest request = new KillProcessRequest().signal(value.getSignal());
        if (value.getEscalateAfterMs() != null) request.escalateAfterMs(value.getEscalateAfterMs());
        if (value.getEscalateTo() != null) request.escalateTo(value.getEscalateTo());
        ExceptionMapper.runToolbox(() -> processApi.signalProcess(id, request));
    }

    void resize(String id, int cols, int rows) {
        if (cols <= 0 || rows <= 0) throw new IllegalArgumentException("cols and rows must be positive");
        ExceptionMapper.callToolbox(() -> processApi.resizeProcess(id,
                new ResizeProcessRequest().cols(cols).rows(rows)));
    }

    ProcessResult waitFor(String id, Integer timeoutMs) {
        if (timeoutMs != null && timeoutMs < 0) throw new IllegalArgumentException("timeoutMs must be non-negative");
        return ExceptionMapper.callToolbox(() -> processApi.waitForProcess(id, timeoutMs));
    }

    void cleanup(String id) {
        ExceptionMapper.runToolbox(() -> processApi.cleanupProcess(id));
    }

    /**
     * Replays every retained log page for a process.
     *
     * <p>The daemon retains logs under a byte cap, so this collects the retained suffix of the
     * output rather than the full history: once the cap is reached the oldest frames are evicted
     * and each page reports {@code truncatedHead} together with the surviving range's
     * {@code firstAvailableCursor}. Callers that must detect or recover from eviction should page
     * with {@link ProcessHandle#logs(String, Integer, String)} and inspect those fields, or follow
     * {@link ProcessHandle#streamLogs(String, ProcessLogListener)} from the start of the process so
     * no frame is evicted before it is read.
     */
    ProcessOutputCollector.Collected collectLogs(ProcessHandle handle, Consumer<String> onStdout,
                                                 Consumer<String> onStderr) {
        ProcessOutputCollector collector = new ProcessOutputCollector(onStdout, onStderr);
        String cursor = "start";
        boolean drained = false;
        for (int pageNumber = 0; pageNumber < MAX_LOG_REPLAY_PAGES; pageNumber++) {
            ProcessLogPage page = handle.logs(cursor, 1000, "base64");
            List<ProcessLogFrame> frames = page == null ? null : page.getFrames();
            if (frames != null) frames.forEach(collector::accept);
            if (page == null || Boolean.TRUE.equals(page.getEof()) || frames == null || frames.isEmpty()) {
                drained = true;
                break;
            }
            if (page.getNextCursor() == null || page.getNextCursor().equals(cursor)) {
                drained = true;
                break;
            }
            cursor = page.getNextCursor();
        }
        if (!drained) {
            throw new DaytonaException("Process log replay exceeded " + MAX_LOG_REPLAY_PAGES
                    + " pages before reaching the end of the log; stream the logs or page them"
                    + " explicitly for logs this large");
        }
        return collector.finish();
    }

    boolean streamLogs(String id, String cursor, String encoding, Integer timeoutMs,
                       ProcessLogListener listener) {
        HttpUrl base = HttpUrl.parse(sandbox.getToolboxApiClient().getBasePath());
        if (base == null) throw new DaytonaException("Toolbox base URL is not available");
        HttpUrl.Builder url = base.newBuilder().addPathSegment("processes").addPathSegment(id)
                .addPathSegment("logs").addQueryParameter("follow", "true");
        if (cursor != null) url.addQueryParameter("cursor", cursor);
        if (encoding != null) url.addQueryParameter("encoding", encoding);
        Request request = new Request.Builder().url(url.build())
                .header("Authorization", "Bearer " + sandbox.getApiKey())
                .header("Accept", "text/event-stream").build();
        // A zero deadline has already elapsed, and OkHttp reads a zero timeout as "no timeout",
        // so it is honored here instead of being handed to the call.
        if (timeoutMs != null && timeoutMs <= 0) return true;
        Call call = sandbox.getToolboxApiClient().getHttpClient().newCall(request);
        call.timeout().timeout(timeoutMs == null ? 0 : timeoutMs, TimeUnit.MILLISECONDS);

        try (Response response = call.execute()) {
            if (!response.isSuccessful()) {
                ResponseBody body = response.body();
                String text = body == null ? "" : body.string();
                throw ExceptionMapper.map(response.code(), text, null);
            }
            ResponseBody body = response.body();
            if (body == null) throw new DaytonaException("Process log stream returned an empty response");
            readSse(body, listener);
            return false;
        } catch (java.io.InterruptedIOException e) {
            if (timeoutMs != null) return true;
            Thread.currentThread().interrupt();
            throw new DaytonaException("Process log stream was interrupted", e);
        } catch (IOException e) {
            throw new DaytonaException("Failed to stream process logs", e);
        }
    }

    PtyHandle attachTerminal(String id) {
        io.daytona.toolbox.client.model.Process record = getRecord(id);
        if (record.getKind() != ProcessKind.KindPty) {
            throw new IllegalStateException("attachTerminal requires a pty process");
        }
        String wsUrl = buildWsUrl(sandbox.getToolboxApiClient().getBasePath(),
                "/processes/" + URLEncoder.encode(id, StandardCharsets.UTF_8) + "/attach");
        Request request = new Request.Builder().url(wsUrl)
                .header("Authorization", "Bearer " + sandbox.getApiKey())
                .header("Sec-WebSocket-Protocol", PTY_EXIT_CONTROL_SUBPROTOCOL).build();
        PtyHandle handle = new PtyHandle(sandbox.getToolboxApiClient().getHttpClient(), request, id,
                this::resize, processId -> kill(processId, new ProcessKillOptions()), null);
        try {
            handle.waitForConnection(PTY_CONNECTION_TIMEOUT_SECONDS);
            return handle;
        } catch (RuntimeException e) {
            handle.disconnect();
            throw e;
        }
    }

    private ProcessResult waitResult(ProcessHandle handle, Integer timeoutMs) {
        try {
            return handle.waitFor(timeoutMs);
        } catch (io.daytona.sdk.exception.DaytonaTimeoutException e) {
            return new ProcessResult().reason(ProcessTerminalReason.ReasonTimedOut);
        }
    }

    private void readSse(ResponseBody body, ProcessLogListener listener) throws IOException {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(body.byteStream(), StandardCharsets.UTF_8))) {
            String event = null;
            StringBuilder data = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isEmpty()) {
                    if (dispatchSse(event, data.toString(), listener)) return;
                    event = null;
                    data.setLength(0);
                } else if (line.startsWith("event:")) {
                    event = line.substring(6).trim();
                } else if (line.startsWith("data:")) {
                    if (data.length() > 0) data.append('\n');
                    data.append(line.substring(5).trim());
                }
            }
            dispatchSse(event, data.toString(), listener);
        }
    }

    private boolean dispatchSse(String event, String data, ProcessLogListener listener) throws IOException {
        if (data == null || data.isEmpty()) return false;
        com.fasterxml.jackson.databind.JsonNode node = OBJECT_MAPPER.readTree(data);
        String type = event;
        if (type == null || type.isEmpty() || "message".equals(type)) {
            if (node.has("channel") || node.has("frame")) type = "log";
            else if (node.has("state") || node.has("process")) type = "state";
            else type = "eof";
        }
        String eventCursor = node.path("cursor").asText(null);
        if ("log".equals(type)) {
            com.fasterxml.jackson.databind.JsonNode frameNode = node.has("frame") ? node.get("frame") : node;
            ProcessLogFrame frame = parseLogFrame(frameNode);
            listener.onEvent(new ProcessLogEvent("log", frame.getCursor(), frame, null));
            return false;
        }
        if ("state".equals(type)) {
            io.daytona.toolbox.client.model.Process record = null;
            com.fasterxml.jackson.databind.JsonNode recordNode = node.get("process");
            if (recordNode != null && recordNode.isObject()) {
                record = parseProcessRecord(recordNode);
            }
            listener.onEvent(new ProcessLogEvent("state", eventCursor, null, record));
            return false;
        }
        if ("eof".equals(type)) {
            listener.onEvent(new ProcessLogEvent("eof", eventCursor, null, null));
            return true;
        }
        return false;
    }

    private ProcessLogFrame parseLogFrame(com.fasterxml.jackson.databind.JsonNode node) {
        String channel = requiredJsonText(node, "channel");
        String frameCursor = requiredJsonText(node, "cursor");
        String timestamp = requiredJsonText(node, "timestamp");
        if (!node.has("seq") || !node.get("seq").canConvertToInt()) {
            throw new DaytonaException("Process log frame seq is required");
        }
        ProcessLogFrame frame = new ProcessLogFrame()
                .channel(io.daytona.toolbox.client.model.ProcessLogChannel.fromValue(channel))
                .cursor(frameCursor).seq(node.get("seq").asInt()).timestamp(timestamp);
        if (node.hasNonNull("data")) frame.data(node.get("data").asText());
        if (node.hasNonNull("encoding")) frame.encoding(node.get("encoding").asText());
        return frame;
    }

    private io.daytona.toolbox.client.model.Process parseProcessRecord(
            com.fasterxml.jackson.databind.JsonNode node) {
        io.daytona.toolbox.client.model.Process record = new io.daytona.toolbox.client.model.Process()
                .id(requiredJsonText(node, "id"))
                .createdAt(requiredJsonText(node, "createdAt"))
                .kind(ProcessKind.fromValue(requiredJsonText(node, "kind")))
                .state(io.daytona.toolbox.client.model.ProcessState.fromValue(requiredJsonText(node, "state")));
        if (node.hasNonNull("exitCode")) record.exitCode(node.get("exitCode").asInt());
        if (node.hasNonNull("signal")) record.signal(node.get("signal").asText());
        if (node.hasNonNull("reason")) record.reason(ProcessTerminalReason.fromValue(node.get("reason").asText()));
        return record;
    }

    private static String requiredJsonText(com.fasterxml.jackson.databind.JsonNode node, String field) {
        String value = node.path(field).asText("");
        if (value.isEmpty()) throw new DaytonaException("Process log event " + field + " is required");
        return value;
    }

    private CreateProcessRequest toCreateRequest(ProcessStartOptions opts) {
        CreateProcessRequest request = new CreateProcessRequest();
        if (opts.getArgv() != null) request.argv(new ArrayList<String>(opts.getArgv()));
        if (opts.getShellCommand() != null) request.shellCommand(opts.getShellCommand());
        if (opts.getShell() != null) request.shell(ProcessShellSelector.fromValue(opts.getShell()));
        if (opts.getLogin() != null) request.login(opts.getLogin());
        if (opts.getName() != null) request.name(opts.getName());
        if (opts.getSessionId() != null) request.sessionId(opts.getSessionId());
        if (opts.getCwd() != null) request.cwd(opts.getCwd());
        if (opts.getEnv() != null) request.env(new java.util.HashMap<String, String>(opts.getEnv()));
        if (opts.getUser() != null) request.user(opts.getUser());
        if (opts.getStdin() != null) request.stdin(ProcessStdinMode.fromValue(opts.getStdin()));
        if (opts.getTimeoutMs() != null) request.timeoutMs(opts.getTimeoutMs());
        if (opts.getKind() != null) request.kind(ProcessKind.fromValue(opts.getKind()));
        if (opts.getKeepLogs() != null) request.keepLogs(ProcessKeepLogs.fromValue(opts.getKeepLogs()));
        if (opts.getTerminal() != null) request.terminal(new ProcessTerminalSize()
                .cols(opts.getTerminal().getCols()).rows(opts.getTerminal().getRows())
                .term(opts.getTerminal().getTerm()));
        return request;
    }

    private void validateStartOptions(ProcessStartOptions opts) {
        if (opts.getTimeoutMs() != null && opts.getTimeoutMs() < 0) {
            throw new IllegalArgumentException("timeoutMs must be non-negative");
        }
        String kind = opts.getKind() == null ? "exec" : opts.getKind();
        requireAllowed(kind, "kind", "exec", "pty", "code");
        if (opts.getShell() != null) requireAllowed(opts.getShell(), "shell", "auto", "sh", "bash", "zsh");
        if (opts.getStdin() != null) requireAllowed(opts.getStdin(), "stdin", "none", "pipe");
        if (opts.getKeepLogs() != null) requireAllowed(opts.getKeepLogs(), "keepLogs", "until_cleanup", "on_exit_ttl", "none");
        boolean hasArgv = opts.getArgv() != null && !opts.getArgv().isEmpty();
        boolean hasShell = opts.getShellCommand() != null && !opts.getShellCommand().trim().isEmpty();
        if (!"pty".equals(kind) && hasArgv == hasShell) {
            throw new IllegalArgumentException("Exactly one of argv or shellCommand is required");
        }
        if (opts.getTerminal() != null) {
            Integer cols = opts.getTerminal().getCols();
            Integer rows = opts.getTerminal().getRows();
            if ((cols != null && cols <= 0) || (rows != null && rows <= 0)) {
                throw new IllegalArgumentException("terminal cols and rows must be positive");
            }
        }
    }

    private static void requireAllowed(String value, String field, String... allowed) {
        Set<String> values = new HashSet<String>(Arrays.asList(allowed));
        if (!values.contains(value)) throw new IllegalArgumentException(field + " must be one of " + values);
    }

    private static String requireIdentifier(String value, String field) {
        if (value == null || value.trim().isEmpty()) throw new IllegalArgumentException(field + " is required");
        return value.trim();
    }

    /**
     * Executes a shell command with default options.
     *
     * @param command command to execute
     * @return execution result
     * @throws DaytonaException if execution fails
     */
    public ExecuteResponse executeCommand(String command) {
        return executeCommand(command, null, null, null);
    }

    /**
     * Alias for {@link #executeCommand(String)}.
     *
     * <p>Use this concise spelling for legacy one-shot command execution; use
     * {@link #start(ProcessStartOptions)} for a supervised process handle.
     * @param command command to execute
     * @return execution result
     */
    public ExecuteResponse exec(String command) {
        return executeCommand(command);
    }

    /**
     * Executes a shell command.
     *
     * @param command command to execute
     * @param cwd working directory, or {@code null} to use sandbox default
     * @param env environment variables to set for the command
     * @param timeout timeout in seconds
     * @return execution result
     * @throws DaytonaException if execution fails
     */
    public ExecuteResponse executeCommand(String command, String cwd, Map<String, String> env, Integer timeout) {
        ExecuteRequest request = new ExecuteRequest().command(command);
        if (cwd != null) {
            request.cwd(cwd);
        }
        if (env != null) {
            request.envs(env);
        }
        if (timeout != null) {
            request.timeout(timeout);
        }
        io.daytona.toolbox.client.model.ExecuteResponse response = ExceptionMapper.callToolbox(() -> processApi.executeCommand(request));
        return toExecuteResponse(response);
    }

    /**
     * Alias for {@link #executeCommand(String, String, Map, Integer)}.
     *
     * <p>Use this concise spelling when the legacy collected command result is
     * sufficient; use {@link #run(ProcessRunOptions)} for the managed-process result model.
     * @param command command to execute
     * @param cwd working directory
     * @param env environment variables
     * @param timeout timeout in seconds
     * @return execution result
     */
    public ExecuteResponse exec(String command, String cwd, Map<String, String> env, Integer timeout) {
        return executeCommand(command, cwd, env, timeout);
    }

    /**
     * Executes source code using Sandbox language tooling.
     *
     * @param code source code to execute
     * @return execution result
     * @throws DaytonaException if execution fails
     */
    public ExecuteResponse codeRun(String code) {
        return codeRun(code, null, null, null);
    }

    public ExecuteResponse codeRun(String code, Map<String, String> env, Integer timeout) {
        return codeRun(code, null, env, timeout);
    }

    public ExecuteResponse codeRun(String code, List<String> argv, Map<String, String> env, Integer timeout) {
        CodeRunRequest request = new CodeRunRequest()
                .code(code == null ? "" : code)
                .language(sandbox.getLanguage());
        if (argv != null && !argv.isEmpty()) {
            request.argv(argv);
        }
        if (env != null) {
            request.envs(env);
        }
        if (timeout != null) {
            request.timeout(timeout);
        }

        CodeRunResponse response = ExceptionMapper.callToolbox(() -> processApi.codeRun(request));
        return toExecuteResponse(response);
    }

    /**
     * Creates a persistent background session.
     *
     * @param sessionId unique session identifier
     * @throws DaytonaException if session creation fails
     */
    public void createSession(String sessionId) {
        ExceptionMapper.runToolbox(() -> processApi.createSession(new CreateSessionRequest().sessionId(sessionId)));
    }

    /**
     * Returns session metadata.
     *
     * @param sessionId session identifier
     * @return session metadata
     * @throws DaytonaException if retrieval fails
     */
    public Session getSession(String sessionId) {
        io.daytona.toolbox.client.model.Session session = ExceptionMapper.callToolbox(() -> processApi.getSession(sessionId));
        return toSession(session);
    }

    /**
     * Returns entrypoint session metadata.
     *
     * @return entrypoint session metadata
     * @throws DaytonaException if retrieval fails
     */
    public Session getEntrypointSession() {
        io.daytona.toolbox.client.model.Session session = ExceptionMapper.callToolbox(processApi::getEntrypointSession);
        return toSession(session);
    }

    /**
     * Executes a command in an existing session.
     *
     * @param sessionId session identifier
     * @param req execution request
     * @return command execution response
     * @throws DaytonaException if execution fails
     */
    public SessionExecuteResponse executeSessionCommand(String sessionId, SessionExecuteRequest req) {
        io.daytona.toolbox.client.model.SessionExecuteRequest request = new io.daytona.toolbox.client.model.SessionExecuteRequest()
                .command(req.getCommand())
                .runAsync(req.getRunAsync());
        io.daytona.toolbox.client.model.SessionExecuteResponse response = ExceptionMapper.callToolbox(() -> processApi.sessionExecuteCommand(sessionId, request));
        return toSessionExecuteResponse(response);
    }

    /**
     * Returns metadata for a command executed in a session.
     *
     * @param sessionId session identifier
     * @param commandId command identifier
     * @return command metadata
     * @throws DaytonaException if retrieval fails
     */
    public Command getSessionCommand(String sessionId, String commandId) {
        io.daytona.toolbox.client.model.Command command = ExceptionMapper.callToolbox(() -> processApi.getSessionCommand(sessionId, commandId));
        return new Command(command);
    }

    /**
     * Returns logs for a command executed in a session.
     *
     * @param sessionId session identifier
     * @param commandId command identifier
     * @return command logs
     * @throws DaytonaException if retrieval fails
     */
    public SessionCommandLogsResponse getSessionCommandLogs(String sessionId, String commandId) {
        return SessionCommandLogsResponse.from(ExceptionMapper.callToolbox(() -> processApi.getSessionCommandLogs(sessionId, commandId, null)));
    }

    /**
     * Streams logs for a command executed in a session via WebSocket.
     *
     * @param sessionId session identifier
     * @param commandId command identifier
     * @param onStdout callback for stdout chunks
     * @param onStderr callback for stderr chunks
     * @throws DaytonaException if streaming fails
     */
    public void getSessionCommandLogs(
            String sessionId,
            String commandId,
            Consumer<String> onStdout,
            Consumer<String> onStderr
    ) {
        String wsUrl = buildWsUrl(sandbox.getToolboxApiClient().getBasePath(),
                "/process/session/" + sessionId + "/command/" + commandId + "/logs?follow=true");
        streamDemuxedLogsViaWebSocket(wsUrl, onStdout, onStderr);
    }

    /**
     * Returns one-shot logs for the sandbox entrypoint session.
     *
     * @return entrypoint logs
     * @throws DaytonaException if retrieval fails
     */
    public SessionCommandLogsResponse getEntrypointLogs() {
        return SessionCommandLogsResponse.from(ExceptionMapper.callToolbox(() -> processApi.getEntrypointLogs(false)));
    }

    /**
     * Streams logs for the sandbox entrypoint session via WebSocket.
     *
     * @param onStdout callback for stdout chunks
     * @param onStderr callback for stderr chunks
     * @throws DaytonaException if streaming fails
     */
    public void getEntrypointLogs(Consumer<String> onStdout, Consumer<String> onStderr) {
        String wsUrl = buildWsUrl(sandbox.getToolboxApiClient().getBasePath(),
                "/process/session/entrypoint/logs?follow=true");
        streamDemuxedLogsViaWebSocket(wsUrl, onStdout, onStderr);
    }

    /**
     * Sends input data to a command executed in a session.
     *
     * @param sessionId session identifier
     * @param commandId command identifier
     * @param data input text to send
     * @throws DaytonaException if sending input fails
     */
    public void sendSessionCommandInput(String sessionId, String commandId, String data) {
        ExceptionMapper.runToolbox(() -> processApi.sendInput(
                sessionId,
                commandId,
                new SessionSendInputRequest().data(data)
        ));
    }

    /**
     * Deletes a session.
     *
     * @param sessionId session identifier
     * @throws DaytonaException if deletion fails
     */
    public void deleteSession(String sessionId) {
        ExceptionMapper.runToolbox(() -> processApi.deleteSession(sessionId));
    }

    /**
     * Lists all sessions in the Sandbox.
     *
     * @return session list
     * @throws DaytonaException if listing fails
     */
    public List<Session> listSessions() {
        List<io.daytona.toolbox.client.model.Session> sessions = ExceptionMapper.callToolbox(processApi::listSessions);
        List<Session> output = new ArrayList<Session>();
        if (sessions != null) {
            for (io.daytona.toolbox.client.model.Session session : sessions) {
                output.add(toSession(session));
            }
        }
        return output;
    }

    /**
     * Creates a PTY terminal session.
     *
     * @param options PTY options, or {@code null} to use defaults
     * @return PTY handle for streaming I/O and lifecycle operations
     * @throws DaytonaException if PTY session creation fails
     */
    public PtyHandle createPty(PtyCreateOptions options) {
        PtyCreateOptions createOptions = options == null ? new PtyCreateOptions() : options;
        String id = createOptions.getId();
        if (id == null || id.isEmpty()) {
            // Generate a client-side id when none is provided (preserves the
            // legacy server-generated/default id flow).
            id = "pty-" + UUID.randomUUID();
        }

        String wsUrl = buildPtyCreateConnectUrl(
                sandbox.getToolboxApiClient().getBasePath(),
                id,
                createOptions.getCols(),
                createOptions.getRows(),
                createOptions.getCwd()
        );
        Request.Builder requestBuilder = new Request.Builder()
                .url(wsUrl)
                .addHeader("Authorization", "Bearer " + sandbox.getApiKey());

        // Advertise the exit-control capability and pass envs as WebSocket subprotocol
        // tokens (kept uniform across SDKs and the daemon, out of the URL/logs). okhttp
        // has no client-side subprotocol API, so set them via Sec-WebSocket-Protocol.
        List<String> subprotocols = new ArrayList<>();
        subprotocols.add(PTY_EXIT_CONTROL_SUBPROTOCOL);
        Map<String, String> envs = createOptions.getEnvs();
        if (envs != null && !envs.isEmpty()) {
            subprotocols.add(PTY_ENVS_SUBPROTOCOL_PREFIX + encodePtyEnvs(envs));
        }
        requestBuilder.addHeader("Sec-WebSocket-Protocol", String.join(", ", subprotocols));
        Request wsRequest = requestBuilder.build();

        PtyHandle handle = new PtyHandle(
                sandbox.getToolboxApiClient().getHttpClient(),
                wsRequest,
                id,
                this::resizePtySession,
                this::killPtySession,
                createOptions.getOnData()
        );
        // The WebSocket connects asynchronously and the daemon registers the
        // session on connect, so wait for the connection before returning (other
        // SDKs do the same) — otherwise an immediate listPtySessions() races it.
        // Close the socket if the handshake never completes so a failed create
        // does not leak the underlying WebSocket connection.
        try {
            handle.waitForConnection(PTY_CONNECTION_TIMEOUT_SECONDS);
        } catch (RuntimeException e) {
            handle.disconnect();
            throw e;
        }
        return handle;
    }

    /**
     * Connects to an existing PTY terminal session.
     *
     * @param sessionId PTY session identifier
     * @return PTY handle for streaming I/O and lifecycle operations
     * @throws DaytonaException if websocket connection setup fails
     */
    public PtyHandle connectPty(String sessionId) {
        return connectPty(sessionId, null);
    }

    /**
     * Connects to an existing PTY terminal session.
     *
     * @param sessionId PTY session identifier
     * @param options PTY options, used for data callback configuration
     * @return PTY handle for streaming I/O and lifecycle operations
     * @throws DaytonaException if websocket connection setup fails
     */
    public PtyHandle connectPty(String sessionId, PtyCreateOptions options) {
        PtyCreateOptions connectOptions = options == null ? new PtyCreateOptions() : options;
        String wsUrl = buildPtyWebSocketUrl(sandbox.getToolboxApiClient().getBasePath(), sessionId);
        Request wsRequest = new Request.Builder()
                .url(wsUrl)
                .addHeader("Authorization", "Bearer " + sandbox.getApiKey())
                .addHeader("Sec-WebSocket-Protocol", PTY_EXIT_CONTROL_SUBPROTOCOL)
                .build();

        PtyHandle handle = new PtyHandle(
                sandbox.getToolboxApiClient().getHttpClient(),
                wsRequest,
                sessionId,
                this::resizePtySession,
                this::killPtySession,
                connectOptions.getOnData()
        );
        try {
            handle.waitForConnection(PTY_CONNECTION_TIMEOUT_SECONDS);
        } catch (RuntimeException e) {
            handle.disconnect();
            throw e;
        }
        return handle;
    }

    /**
     * Lists PTY sessions in the Sandbox.
     *
     * @return PTY session information list
     * @throws DaytonaException if listing fails
     */
    public List<PtySessionInfo> listPtySessions() {
        PtyListResponse response = ExceptionMapper.callToolbox(processApi::listPtySessions);
        return response == null || response.getSessions() == null
                ? new ArrayList<PtySessionInfo>()
                : response.getSessions();
    }

    /**
     * Returns PTY session information.
     *
     * @param sessionId PTY session identifier
     * @return PTY session information
     * @throws DaytonaException if retrieval fails
     */
    public PtySessionInfo getPtySessionInfo(String sessionId) {
        return ExceptionMapper.callToolbox(() -> processApi.getPtySession(sessionId));
    }

    /**
     * Resizes an active PTY session.
     *
     * @param sessionId PTY session identifier
     * @param cols terminal width in columns
     * @param rows terminal height in rows
     * @throws DaytonaException if resize fails
     */
    public void resizePtySession(String sessionId, int cols, int rows) {
        ExceptionMapper.callToolbox(() -> processApi.resizePtySession(
                sessionId,
                new PtyResizeRequest().cols(cols).rows(rows)
        ));
    }

    /**
     * Terminates a PTY session.
     *
     * @param sessionId PTY session identifier
     * @throws DaytonaException if termination fails
     */
    public void killPtySession(String sessionId) {
        ExceptionMapper.callToolbox(() -> processApi.deletePtySession(sessionId));
    }

    private String buildPtyWebSocketUrl(String toolboxBaseUrl, String sessionId) {
        if (toolboxBaseUrl == null || toolboxBaseUrl.isEmpty()) {
            throw new DaytonaException("Toolbox base URL is not available");
        }
        String wsBase = toolboxBaseUrl
                .replaceFirst("^https://", "wss://")
                .replaceFirst("^http://", "ws://");
        return wsBase + "/process/pty/" + sessionId + "/connect";
    }

    private String buildPtyCreateConnectUrl(String toolboxBaseUrl, String id, int cols, int rows, String cwd) {
        if (toolboxBaseUrl == null || toolboxBaseUrl.isEmpty()) {
            throw new DaytonaException("Toolbox base URL is not available");
        }
        String wsBase = toolboxBaseUrl
                .replaceFirst("^https://", "wss://")
                .replaceFirst("^http://", "ws://");
        // URL-encode user-provided values so reserved characters don't corrupt the
        // request; cols/rows are ints and safe to inline.
        StringBuilder url = new StringBuilder(wsBase)
                .append("/process/pty/create-connect?id=")
                .append(URLEncoder.encode(id, StandardCharsets.UTF_8))
                .append("&cols=").append(cols)
                .append("&rows=").append(rows);
        if (cwd != null && !cwd.isEmpty()) {
            url.append("&cwd=").append(URLEncoder.encode(cwd, StandardCharsets.UTF_8));
        }
        return url.toString();
    }

    private String encodePtyEnvs(Map<String, String> envs) {
        try {
            byte[] json = OBJECT_MAPPER.writeValueAsBytes(envs);
            return Base64.getUrlEncoder().withoutPadding().encodeToString(json);
        } catch (com.fasterxml.jackson.core.JsonProcessingException e) {
            throw new DaytonaException("Failed to serialize PTY environment variables", e);
        }
    }

    private void streamDemuxedLogsViaWebSocket(String wsUrl, Consumer<String> onStdout, Consumer<String> onStderr) {
        Request wsRequest = new Request.Builder()
                .url(wsUrl)
                .addHeader("Authorization", "Bearer " + sandbox.getApiKey())
                .build();

        final CountDownLatch doneLatch = new CountDownLatch(1);
        final AtomicReference<RuntimeException> failure = new AtomicReference<>(null);

        sandbox.getToolboxApiClient().getHttpClient().newWebSocket(wsRequest, new WebSocketListener() {
            final ByteArrayOutputStream stdoutBuf = new ByteArrayOutputStream();
            final ByteArrayOutputStream stderrBuf = new ByteArrayOutputStream();
            final ByteArrayOutputStream markerBuf = new ByteArrayOutputStream();
            int streamState = LOG_STREAM_NONE;
            byte markerByte = 0;
            int markerCount = 0;

            @Override
            public void onMessage(WebSocket webSocket, okio.ByteString bytes) {
                demux(bytes.toByteArray());
            }

            @Override
            public void onMessage(WebSocket webSocket, String text) {
                demux(text.getBytes(StandardCharsets.UTF_8));
            }

            @Override
            public void onClosing(WebSocket webSocket, int code, String reason) {
                flush();
                webSocket.close(1000, null);
                doneLatch.countDown();
            }

            @Override
            public void onClosed(WebSocket webSocket, int code, String reason) {
                flush();
                doneLatch.countDown();
            }

            @Override
            public void onFailure(WebSocket webSocket, Throwable t, Response response) {
                flush();
                String msg = (t == null || t.getMessage() == null || t.getMessage().isEmpty())
                        ? "WebSocket failure" : t.getMessage();
                failure.compareAndSet(null, new DaytonaException("Log streaming failed: " + msg, t));
                doneLatch.countDown();
            }

            private void demux(byte[] data) {
                for (byte value : data) {
                    if (value == STDOUT_PREFIX_BYTE || value == STDERR_PREFIX_BYTE) {
                        if (markerCount == 0) {
                            markerByte = value;
                            markerCount = 1;
                            markerBuf.write(value);
                        } else if (markerByte == value) {
                            markerCount++;
                            markerBuf.write(value);
                        } else {
                            drainMarker();
                            markerBuf.write(value);
                            markerByte = value;
                            markerCount = 1;
                        }
                        if (markerCount >= PREFIX_REPEAT_COUNT) {
                            emitBuffer(stdoutBuf, onStdout);
                            emitBuffer(stderrBuf, onStderr);
                            markerBuf.reset();
                            markerCount = 0;
                            streamState = markerByte == STDOUT_PREFIX_BYTE ? LOG_STREAM_STDOUT : LOG_STREAM_STDERR;
                        }
                        continue;
                    }
                    drainMarker();
                    markerCount = 0;
                    appendToStream(value);
                }
            }

            private void drainMarker() {
                if (markerBuf.size() == 0) return;
                for (byte b : markerBuf.toByteArray()) {
                    appendToStream(b);
                }
                markerBuf.reset();
            }

            private void appendToStream(byte value) {
                if (streamState == LOG_STREAM_STDOUT) stdoutBuf.write(value);
                else if (streamState == LOG_STREAM_STDERR) stderrBuf.write(value);
            }

            private void flush() {
                drainMarker();
                emitBuffer(stdoutBuf, onStdout);
                emitBuffer(stderrBuf, onStderr);
            }

            private void emitBuffer(ByteArrayOutputStream buf, Consumer<String> consumer) {
                if (consumer == null || buf.size() == 0) { buf.reset(); return; }
                consumer.accept(new String(buf.toByteArray(), StandardCharsets.UTF_8));
                buf.reset();
            }
        });

        try {
            doneLatch.await();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new DaytonaException("Interrupted while streaming logs", e);
        }
        RuntimeException ex = failure.get();
        if (ex != null) throw ex;
    }

    private String buildWsUrl(String basePath, String path) {
        if (basePath == null || basePath.isEmpty()) {
            throw new DaytonaException("Toolbox base URL is not available");
        }
        String wsBase = basePath
                .replaceFirst("^https://", "wss://")
                .replaceFirst("^http://", "ws://");
        return wsBase + path;
    }

    private ExecuteResponse toExecuteResponse(io.daytona.toolbox.client.model.ExecuteResponse source) {
        return new ExecuteResponse(source);
    }

    private ExecuteResponse toExecuteResponse(CodeRunResponse source) {
        return new ExecuteResponse(source);
    }

    private Session toSession(io.daytona.toolbox.client.model.Session source) {
        return new Session(source);
    }

    private SessionExecuteResponse toSessionExecuteResponse(io.daytona.toolbox.client.model.SessionExecuteResponse source) {
        return new SessionExecuteResponse(source);
    }
}
