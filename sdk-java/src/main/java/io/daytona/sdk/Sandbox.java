// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.daytona.api.client.api.SandboxApi;
import io.daytona.api.client.model.BuildInfo;
import io.daytona.api.client.model.CreateSandboxSnapshot;
import io.daytona.api.client.model.ForkSandbox;
import io.daytona.analytics.api.client.model.ModelsMetricPoint;
import io.daytona.api.client.model.MetricDataPoint;
import io.daytona.api.client.model.MetricSeries;
import io.daytona.api.client.model.MetricsResponse;
import io.daytona.api.client.model.SandboxLabels;
import io.daytona.api.client.model.SandboxListItem;
import io.daytona.api.client.model.SandboxVolume;
import io.daytona.api.client.model.ToolboxProxyUrl;
import io.daytona.api.client.model.UpdateSandboxNetworkSettings;
import io.daytona.api.client.model.UpdateSandboxSecrets;
import io.daytona.sdk.exception.DaytonaException;
import io.daytona.sdk.exception.DaytonaNotFoundException;
import io.daytona.sdk.exception.DaytonaTimeoutException;
import io.daytona.sdk.internal.EventSubscriptionManager;
import io.daytona.sdk.model.SandboxMetrics;
import io.daytona.toolbox.client.api.SystemApi;
import io.daytona.toolbox.client.model.SystemMetrics;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Represents a Daytona Sandbox instance.
 *
 * <p>Exposes lifecycle controls and operation facades for process execution, file-system access,
 * and Git. State changes are streamed over WebSocket by default with polling as a safety net,
 * or observed by polling only when the deprecated polling mode is enabled.
 */
public class Sandbox {
    private static final Map<String, String> SANDBOX_METRIC_FIELD_BY_NAME = Map.of(
            "daytona.sandbox.cpu.utilization", "cpuUsedPct",
            "daytona.sandbox.cpu.limit", "cpuCount",
            "daytona.sandbox.memory.usage", "memUsed",
            "daytona.sandbox.memory.limit", "memTotal",
            "daytona.sandbox.memory.cache", "memCache",
            "daytona.sandbox.filesystem.usage", "diskUsed",
            "daytona.sandbox.filesystem.total", "diskTotal");

    private static final List<String> SANDBOX_METRIC_NAMES =
            List.copyOf(SANDBOX_METRIC_FIELD_BY_NAME.keySet());

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private static final long POLL_ONLY_INTERVAL_MILLIS = 250;
    private static final long POLL_STREAMING_INTERVAL_MILLIS = 1000;
    private static final Set<String> STARTED_STATES = Collections.singleton("started");
    private static final Set<String> STOPPED_STATES = new HashSet<>(Arrays.asList("stopped", "destroyed"));
    private static final Set<String> DESTROYED_STATES = Collections.singleton("destroyed");
    private static final Set<String> PAUSED_STATES = Collections.singleton("paused");
    private static final Set<String> ERROR_STATES = new HashSet<>(Arrays.asList("error", "build_failed"));
    private static final Set<String> RESIZE_TARGET_STATES;
    private static final Set<String> SNAPSHOT_TARGET_STATES;
    private static final Set<String> PAUSE_TARGET_STATES;
    static {
        Set<String> allStates = new HashSet<>();
        for (io.daytona.api.client.model.SandboxState s : io.daytona.api.client.model.SandboxState.values()) {
            allStates.add(s.getValue());
        }
        allStates.removeAll(ERROR_STATES);

        Set<String> resizeStates = new HashSet<>(allStates);
        resizeStates.remove("resizing");
        RESIZE_TARGET_STATES = Collections.unmodifiableSet(resizeStates);

        Set<String> snapshotStates = new HashSet<>(allStates);
        snapshotStates.remove("snapshotting");
        SNAPSHOT_TARGET_STATES = Collections.unmodifiableSet(snapshotStates);

        Set<String> pauseStates = new HashSet<>(allStates);
        pauseStates.remove("pausing");
        PAUSE_TARGET_STATES = Collections.unmodifiableSet(pauseStates);
    }

    private static final ScheduledExecutorService POLL_SCHEDULER = Executors.newScheduledThreadPool(2, r -> {
        Thread t = new Thread(r, "daytona-sandbox-poller");
        t.setDaemon(true);
        return t;
    });

    // Unbounded cached pool for blocking HTTP refreshes — prevents starvation
    // on the fixed-size POLL_SCHEDULER when 3+ sandboxes poll concurrently.
    private static final ExecutorService REFRESH_EXECUTOR = Executors.newCachedThreadPool(r -> {
        Thread t = new Thread(r, "daytona-sandbox-refresh");
        t.setDaemon(true);
        return t;
    });

    private final SandboxApi sandboxApi;
    private final DaytonaConfig config;
    private final io.daytona.toolbox.client.ApiClient toolboxApiClient;
    private final io.daytona.toolbox.client.api.InfoApi infoApi;
    private final io.daytona.toolbox.client.api.ServerApi serverApi;
    private final SystemApi systemApi;
    private final String apiKey;
    private final java.util.function.Supplier<String> analyticsApiUrlProvider;
    private final EventSubscriptionManager subscriptionManager;
    private final Object subscriptionLock = new Object();
    private String subId;
    private final CopyOnWriteArrayList<StateWaiter> stateWaiters = new CopyOnWriteArrayList<>();

    // Fields shared by both io.daytona.api.client.model.Sandbox and SandboxListItem.
    private String id;
    private String name;
    private String organizationId;
    private String snapshot;
    private String user;
    private Map<String, String> labels;
    private Boolean isPublic;
    private String target;
    private int cpu;
    private int gpu;
    private int memory;
    private int disk;
    private volatile String state;
    private String errorReason;
    private Boolean recoverable;
    private String backupState;
    private Integer autoStopInterval;
    private Integer autoPauseInterval;
    private Integer autoArchiveInterval;
    private Integer autoDeleteInterval;
    private String createdAt;
    private String updatedAt;
    private String lastActivityAt;
    private String autoDestroyAt;
    private String toolboxProxyUrl;

    // Fields only present on the full Sandbox DTO; not populated by Daytona.list() —
    // call refreshData() on each item to populate.
    private Map<String, String> env;
    private Boolean networkBlockAll;
    private String networkAllowList;
    private String domainAllowList;
    private List<SandboxVolume> volumes;
    private BuildInfo buildInfo;
    private String backupCreatedAt;

    /** Process execution interface for this Sandbox. */
    public final Process process;
    /** File-system operations interface for this Sandbox. */
    public final FileSystem fs;
    /** Git operations interface for this Sandbox. */
    public final Git git;
    /** Computer use (desktop automation) interface for this Sandbox. */
    public final ComputerUse computerUse;
    /** Stateful code interpreter for this Sandbox (Python). */
    public final CodeInterpreter codeInterpreter;

    Sandbox(SandboxApi sandboxApi, DaytonaConfig config, io.daytona.api.client.model.Sandbox data,
            java.util.function.Supplier<String> analyticsApiUrlProvider,
            EventSubscriptionManager subscriptionManager) {
        this.sandboxApi = sandboxApi;
        this.config = config;
        this.apiKey = config.getApiKey();
        this.analyticsApiUrlProvider = analyticsApiUrlProvider;
        this.subscriptionManager = subscriptionManager;
        populateFromDTO(data);
        this.toolboxApiClient = buildToolboxApiClient(sandboxApi, config);
        this.infoApi = new io.daytona.toolbox.client.api.InfoApi(toolboxApiClient);
        this.serverApi = new io.daytona.toolbox.client.api.ServerApi(toolboxApiClient);
        this.systemApi = new SystemApi(toolboxApiClient);
        this.process = new Process(new io.daytona.toolbox.client.api.ProcessApi(toolboxApiClient), this);
        this.fs = new FileSystem(new io.daytona.toolbox.client.api.FileSystemApi(toolboxApiClient));
        this.git = new Git(new io.daytona.toolbox.client.api.GitApi(toolboxApiClient));
        this.computerUse = new ComputerUse(new io.daytona.toolbox.client.api.ComputerUseApi(toolboxApiClient));
        this.codeInterpreter = new CodeInterpreter(new io.daytona.toolbox.client.api.InterpreterApi(toolboxApiClient), this);
        ensureSubscribed();
    }

    Sandbox(SandboxApi sandboxApi, DaytonaConfig config, SandboxListItem data,
            java.util.function.Supplier<String> analyticsApiUrlProvider,
            EventSubscriptionManager subscriptionManager) {
        this.sandboxApi = sandboxApi;
        this.config = config;
        this.apiKey = config.getApiKey();
        this.analyticsApiUrlProvider = analyticsApiUrlProvider;
        this.subscriptionManager = subscriptionManager;
        populateFromDTO(data);
        this.toolboxApiClient = buildToolboxApiClient(sandboxApi, config);
        this.infoApi = new io.daytona.toolbox.client.api.InfoApi(toolboxApiClient);
        this.serverApi = new io.daytona.toolbox.client.api.ServerApi(toolboxApiClient);
        this.systemApi = new SystemApi(toolboxApiClient);
        this.process = new Process(new io.daytona.toolbox.client.api.ProcessApi(toolboxApiClient), this);
        this.fs = new FileSystem(new io.daytona.toolbox.client.api.FileSystemApi(toolboxApiClient));
        this.git = new Git(new io.daytona.toolbox.client.api.GitApi(toolboxApiClient));
        this.computerUse = new ComputerUse(new io.daytona.toolbox.client.api.ComputerUseApi(toolboxApiClient));
        this.codeInterpreter = new CodeInterpreter(new io.daytona.toolbox.client.api.InterpreterApi(toolboxApiClient), this);
        ensureSubscribed();
    }

    /**
     * Builds the toolbox HTTP client, resolving the proxy URL if missing and attaching auth + SDK headers.
     * Requires {@code this.id} and {@code this.toolboxProxyUrl} to be populated.
     */
    private io.daytona.toolbox.client.ApiClient buildToolboxApiClient(SandboxApi sandboxApi, DaytonaConfig config) {
        String proxyBase = this.toolboxProxyUrl;
        if (proxyBase == null || proxyBase.isEmpty()) {
            ToolboxProxyUrl proxy = ExceptionMapper.callMain(() -> sandboxApi.getToolboxProxyUrl(this.id, null));
            proxyBase = proxy == null ? "" : proxy.getUrl();
        }

        String toolboxBase = trimTrailingSlash(proxyBase) + "/" + this.id;
        io.daytona.toolbox.client.ApiClient client = new io.daytona.toolbox.client.ApiClient();
        client.setBasePath(toolboxBase);
        String sdkVersion = Daytona.class.getPackage().getImplementationVersion();
        if (sdkVersion == null) sdkVersion = "dev";
        client.addDefaultHeader("Authorization", "Bearer " + config.getApiKey());
        client.addDefaultHeader("X-Daytona-Source", "sdk-java");
        client.addDefaultHeader("X-Daytona-SDK-Version", sdkVersion);
        client.setUserAgent("sdk-java/" + sdkVersion);
        return client;
    }

    /**
     * Creates an LSP server instance for the specified language and project.
     *
     * @param languageId language server to start (e.g. "typescript", "python", "go")
     * @param pathToProject absolute path to the project root inside the sandbox
     * @return a new {@link LspServer} configured for the given language
     */
    public LspServer createLspServer(String languageId, String pathToProject) {
        ensureSubscribed();
        return new LspServer(new io.daytona.toolbox.client.api.LspApi(toolboxApiClient));
    }

    String getLanguage() {
        String lang = "python";
        if (labels != null && labels.containsKey(Daytona.CODE_TOOLBOX_LANGUAGE_LABEL)) {
            lang = labels.get(Daytona.CODE_TOOLBOX_LANGUAGE_LABEL);
        }
        return lang;
    }

    /**
     * Starts this Sandbox with default timeout.
     *
     * @throws DaytonaException if the Sandbox fails to start
     */
    public void start() {
        ensureSubscribed();
        start(60);
    }

    /**
     * Starts this Sandbox and waits for readiness.
     *
     * @param timeoutSeconds maximum seconds to wait; {@code 0} disables timeout
     * @throws DaytonaException if start fails or times out
     */
    public void start(long timeoutSeconds) {
        if (timeoutSeconds < 0) {
            throw new DaytonaException("Timeout must be non-negative");
        }
        ensureSubscribed();
        long startTime = System.currentTimeMillis();
        io.daytona.api.client.model.Sandbox response = ExceptionMapper.callMain(() -> sandboxApi.startSandbox(id, null));
        if (response != null) {
            populateFromDTO(response);
        }
        long elapsed = (System.currentTimeMillis() - startTime) / 1000;
        long remaining = timeoutSeconds > 0 ? Math.max(1, timeoutSeconds - elapsed) : 0;
        waitUntilStarted(remaining);
    }

    /**
     * Stops this Sandbox with default timeout.
     *
     * @throws DaytonaException if the Sandbox fails to stop
     */
    public void stop() {
        ensureSubscribed();
        stop(60);
    }

    /**
     * Stops this Sandbox and waits until fully stopped.
     *
     * @param timeoutSeconds maximum seconds to wait; {@code 0} disables timeout
     * @throws DaytonaException if stop fails or times out
     */
    public void stop(long timeoutSeconds) {
        if (timeoutSeconds < 0) {
            throw new DaytonaException("Timeout must be non-negative");
        }
        ensureSubscribed();
        long startTime = System.currentTimeMillis();
        ExceptionMapper.callMain(() -> sandboxApi.stopSandbox(id, null, null));
        refreshData();
        long elapsed = (System.currentTimeMillis() - startTime) / 1000;
        long remaining = timeoutSeconds > 0 ? Math.max(1, timeoutSeconds - elapsed) : 0;
        waitUntilStopped(remaining);
    }

    /**
     * Waits until Sandbox reaches {@code stopped} (or {@code destroyed}) state.
     *
     * @param timeoutSeconds maximum seconds to wait; {@code 0} disables timeout
     * @throws DaytonaException if timeout is invalid, state becomes error, or timeout expires
     */
    public void waitUntilStopped(long timeoutSeconds) {
        ensureSubscribed();
        if (timeoutSeconds < 0) {
            throw new DaytonaException("Timeout must be non-negative");
        }
        if (STOPPED_STATES.contains(state)) {
            return;
        }
        waitForState(STOPPED_STATES, ERROR_STATES, timeoutSeconds, true);
    }

    /**
     * Deletes this Sandbox.
     *
     * <p>Fires the delete API call and returns immediately without waiting for the
     * Sandbox to reach the {@code destroyed} state. Use {@link #delete(long, boolean)}
     * with {@code wait=true} to block until destruction completes.
     *
     * @throws DaytonaException if the delete API call fails
     */
    public void delete() {
        delete(60);
    }

    /**
     * Deletes this Sandbox.
     *
     * <p>Fires the delete API call and returns immediately. Use
     * {@link #delete(long, boolean)} with {@code wait=true} to block until destroyed.
     *
     * @param timeoutSeconds timeout for the HTTP request (and for waiting when
     *                       {@code wait} is true in {@link #delete(long, boolean)})
     * @throws DaytonaException if the delete API call fails
     */
    public void delete(long timeoutSeconds) {
        delete(timeoutSeconds, false);
    }

    /**
     * Deletes this Sandbox, optionally waiting for it to reach the {@code destroyed} state.
     *
     * @param timeoutSeconds maximum seconds to wait when {@code wait} is true;
     *                       {@code 0} disables timeout. Ignored when {@code wait} is false.
     * @param wait if {@code true}, block until the Sandbox is destroyed
     * @throws DaytonaException if deletion fails or times out
     */
    public void delete(long timeoutSeconds, boolean wait) {
        if (timeoutSeconds < 0) {
            throw new DaytonaException("Timeout must be non-negative");
        }
        ensureSubscribed();
        long startTime = System.currentTimeMillis();
        ExceptionMapper.callMain(() -> sandboxApi.deleteSandbox(id, null));

        try {
            if (wait) {
                refreshDataSafe();
                if (!"destroyed".equalsIgnoreCase(state)) {
                    long elapsed = (System.currentTimeMillis() - startTime) / 1000;
                    long remaining = timeoutSeconds > 0 ? Math.max(1, timeoutSeconds - elapsed) : 0;
                    waitForState(DESTROYED_STATES, ERROR_STATES, remaining, true);
                }
            }
        } finally {
            synchronized (subscriptionLock) {
                unsubscribeFromEventsLocked();
            }
        }
    }

    /**
     * Replaces Sandbox labels.
     *
     * @param labels label map to apply
     * @return updated labels
     * @throws DaytonaException if label update fails
     */
    public Map<String, String> setLabels(Map<String, String> labels) {
        ensureSubscribed();
        ExceptionMapper.callMain(() -> {
            okhttp3.Call call = sandboxApi.replaceLabelsCall(id, new SandboxLabels().labels(labels), null, null);
            sandboxApi.getApiClient().execute(call, null);
            return null;
        });
        refreshData();
        return this.labels;
    }

    /**
     * Sets Sandbox auto-stop interval.
     *
     * @param minutes idle minutes before automatic stop
     * @throws DaytonaException if the update fails
     */
    public void setAutostopInterval(int minutes) {
        ensureSubscribed();
        io.daytona.api.client.model.Sandbox response = ExceptionMapper.callMain(() -> sandboxApi.setAutostopInterval(id, BigDecimal.valueOf(minutes), null));
        if (response != null) {
            populateFromDTO(response);
        }
    }

    /**
     * Sets Sandbox auto-pause interval.
     *
     * @param minutes idle minutes before automatic pause (0 means disabled)
     * @throws DaytonaException if the update fails
     */
    public void setAutoPauseInterval(int minutes) {
        io.daytona.api.client.model.Sandbox response = ExceptionMapper.callMain(() -> sandboxApi.setAutoPauseInterval(id, BigDecimal.valueOf(minutes), null));
        if (response != null) {
            populateFromDTO(response);
        }
    }

    /**
     * Sets Sandbox auto-archive interval.
     *
     * @param minutes minutes in stopped state before automatic archive
     * @throws DaytonaException if the update fails
     */
    public void setAutoArchiveInterval(int minutes) {
        ensureSubscribed();
        io.daytona.api.client.model.Sandbox response = ExceptionMapper.callMain(() -> sandboxApi.setAutoArchiveInterval(id, BigDecimal.valueOf(minutes), null));
        if (response != null) {
            populateFromDTO(response);
        }
    }

    /**
     * Sets Sandbox auto-delete interval.
     *
     * @param minutes minutes before automatic deletion after stop
     * @throws DaytonaException if the update fails
     */
    public void setAutoDeleteInterval(int minutes) {
        ensureSubscribed();
        io.daytona.api.client.model.Sandbox response = ExceptionMapper.callMain(() -> sandboxApi.setAutoDeleteInterval(id, BigDecimal.valueOf(minutes), null));
        if (response != null) {
            populateFromDTO(response);
        }
    }

    /**
     * Sets Sandbox TTL (time to live) in minutes. Set to 0 to disable the TTL.
     * The deadline is computed server-side; call {@link #refreshData()} to read the updated
     * {@code autoDestroyAt}.
     *
     * @param ttlMinutes minutes until the Sandbox is destroyed, or 0 to disable
     * @throws IllegalArgumentException if ttlMinutes is negative
     * @throws DaytonaException if the update fails
     */
    public void setTtl(int ttlMinutes) {
        if (ttlMinutes < 0) {
            throw new IllegalArgumentException("ttlMinutes must be a non-negative integer");
        }
        ExceptionMapper.callMain(() -> sandboxApi.setTtl(id, BigDecimal.valueOf(ttlMinutes), null));
    }

    /**
     * Updates outbound network policy on the runner (block all, restore access, or CIDR allow list).
     *
     * @param settings request body; at least one of networkBlockAll or networkAllowList must be set
     * @throws DaytonaException if the update fails
     */
    public void updateNetworkSettings(UpdateSandboxNetworkSettings settings) {
        io.daytona.api.client.model.Sandbox response = ExceptionMapper.callMain(() -> sandboxApi.updateNetworkSettings(id, settings, null));
        if (response != null) {
            populateFromDTO(response);
        }
    }

    /**
     * Replaces the set of vault secrets mounted in this Sandbox.
     *
     * <p>Each key is an environment variable name and each value is the name of an existing
     * organization Secret. Pass an empty map to detach all secrets. Attached, detached, and
     * rotated secrets take effect for outbound requests within seconds. New environment
     * variables are only visible to processes spawned after the update; a Sandbox created
     * without secrets must be restarted for newly attached secrets to work.
     *
     * @param secrets map of environment variable name to organization Secret name
     * @throws DaytonaException if the update fails
     */
    public void updateSecrets(Map<String, String> secrets) {
        List<Map<String, String>> wireList = new ArrayList<Map<String, String>>();
        for (Map.Entry<String, String> entry : secrets.entrySet()) {
            wireList.add(Collections.singletonMap(entry.getKey(), entry.getValue()));
        }
        io.daytona.api.client.model.Sandbox response = ExceptionMapper.callMain(
                () -> sandboxApi.updateSandboxSecrets(id, new UpdateSandboxSecrets().secrets(wireList), null));
        if (response != null) {
            populateFromDTO(response);
        }
    }

    /**
     * Returns home directory path for Sandbox user.
     *
     * @return absolute home directory path
     * @throws DaytonaException if the request fails
     */
    public String getUserHomeDir() {
        ensureSubscribed();
        io.daytona.toolbox.client.model.UserHomeDirResponse value = ExceptionMapper.callToolbox(() -> infoApi.getUserHomeDir());
        return value == null ? "" : asString(value.getDir());
    }

    /**
     * Gets the most recent resource usage sample directly from the sandbox daemon.
     *
     * <p>Unlike {@link #getMetrics}, which returns aggregated historical samples, this returns
     * the single current reading without going through the telemetry backend.
     *
     * @return the current resource usage sample for the sandbox
     * @throws DaytonaException if the request fails
     */
    public SandboxMetrics getMetricsLatest() {
        return sandboxMetricsFromSystemMetrics(ExceptionMapper.callToolbox(() -> systemApi.getSystemMetrics()));
    }

    /**
     * Gets historical time-series resource usage metrics for the sandbox.
     *
     * <p>When the deployment runs a dedicated Analytics API, metrics are fetched from it directly;
     * otherwise they are fetched through the control-plane telemetry proxy. A {@code null} start
     * defaults to the sandbox creation time; a {@code null} end defaults to the current time.
     * Samples are returned ordered ascending by timestamp.
     *
     * @param start start of the time range, or {@code null} for the sandbox creation time
     * @param end end of the time range, or {@code null} for the current time
     * @return time-ordered usage samples over the requested range
     * @throws DaytonaException if the request fails
     */
    public List<SandboxMetrics> getMetrics(OffsetDateTime start, OffsetDateTime end) {
        OffsetDateTime to = end != null ? end : OffsetDateTime.now();
        OffsetDateTime from = start != null ? start
                : (createdAt != null ? OffsetDateTime.parse(createdAt) : to);

        String analyticsApiUrl = analyticsApiUrlProvider.get();

        if (analyticsApiUrl != null && !analyticsApiUrl.isEmpty()) {
            List<ModelsMetricPoint> points = ExceptionMapper.callMain(
                    () -> buildAnalyticsTelemetryApi(analyticsApiUrl)
                            .organizationOrganizationIdSandboxSandboxIdTelemetryMetricsGet(
                                    organizationId, id, from.toString(), to.toString(),
                                    String.join(",", SANDBOX_METRIC_NAMES)));
            return pivotSandboxMetricPoints(points);
        }

        MetricsResponse response = ExceptionMapper.callMain(
                () -> sandboxApi.getSandboxMetrics(id, from, to, null, SANDBOX_METRIC_NAMES));
        return pivotSandboxMetrics(response.getSeries());
    }

    private io.daytona.analytics.api.client.api.TelemetryApi buildAnalyticsTelemetryApi(String analyticsApiUrl) {
        io.daytona.analytics.api.client.ApiClient client = new io.daytona.analytics.api.client.ApiClient();
        client.setBasePath(trimTrailingSlash(analyticsApiUrl));
        client.setApiKey(apiKey);
        client.setApiKeyPrefix("Bearer");
        return new io.daytona.analytics.api.client.api.TelemetryApi(client);
    }

    private void addMetricPoint(Map<String, Map<String, Double>> buckets, String name, String timestamp, Number value) {
        String field = SANDBOX_METRIC_FIELD_BY_NAME.get(name);
        if (field == null || timestamp == null || value == null) {
            return;
        }
        buckets.computeIfAbsent(timestamp, k -> new HashMap<>()).put(field, value.doubleValue());
    }

    private List<SandboxMetrics> pivotSandboxMetrics(List<MetricSeries> series) {
        Map<String, Map<String, Double>> buckets = new TreeMap<>();
        if (series != null) {
            for (MetricSeries s : series) {
                if (s.getDataPoints() == null) {
                    continue;
                }
                for (MetricDataPoint point : s.getDataPoints()) {
                    addMetricPoint(buckets, s.getMetricName(), point.getTimestamp(), point.getValue());
                }
            }
        }
        return buildSandboxMetricsFromBuckets(buckets);
    }

    private List<SandboxMetrics> pivotSandboxMetricPoints(List<ModelsMetricPoint> points) {
        Map<String, Map<String, Double>> buckets = new TreeMap<>();
        if (points != null) {
            for (ModelsMetricPoint p : points) {
                addMetricPoint(buckets, p.getMetricName(), p.getTimestamp(), p.getValue());
            }
        }
        return buildSandboxMetricsFromBuckets(buckets);
    }

    private List<SandboxMetrics> buildSandboxMetricsFromBuckets(Map<String, Map<String, Double>> buckets) {
        List<SandboxMetrics> result = new ArrayList<>();
        for (Map.Entry<String, Map<String, Double>> entry : buckets.entrySet()) {
            Map<String, Double> v = entry.getValue();
            result.add(new SandboxMetrics(
                    (int) v.getOrDefault("cpuCount", 0.0).doubleValue(),
                    v.getOrDefault("cpuUsedPct", 0.0),
                    (long) v.getOrDefault("diskTotal", 0.0).doubleValue(),
                    (long) v.getOrDefault("diskUsed", 0.0).doubleValue(),
                    (long) v.getOrDefault("memTotal", 0.0).doubleValue(),
                    (long) v.getOrDefault("memUsed", 0.0).doubleValue(),
                    (long) v.getOrDefault("memCache", 0.0).doubleValue(),
                    OffsetDateTime.parse(entry.getKey())));
        }
        return result;
    }

    private SandboxMetrics sandboxMetricsFromSystemMetrics(SystemMetrics m) {
        OffsetDateTime ts = m.getTimestamp() != null ? OffsetDateTime.parse(m.getTimestamp()) : OffsetDateTime.now();
        return new SandboxMetrics(
                m.getCpuCount() != null ? m.getCpuCount() : 0,
                m.getCpuUsedPct() != null ? m.getCpuUsedPct() : 0.0,
                m.getDiskTotal() != null ? m.getDiskTotal() : 0L,
                m.getDiskUsed() != null ? m.getDiskUsed() : 0L,
                m.getMemTotal() != null ? m.getMemTotal() : 0L,
                m.getMemUsed() != null ? m.getMemUsed() : 0L,
                m.getMemCache() != null ? m.getMemCache() : 0L,
                ts);
    }

    /**
     * Returns current working directory path.
     *
     * @return absolute working directory path
     * @throws DaytonaException if the request fails
     */
    public String getWorkDir() {
        ensureSubscribed();
        io.daytona.toolbox.client.model.WorkDirResponse value = ExceptionMapper.callToolbox(() -> infoApi.getWorkDir());
        return value == null ? "" : asString(value.getDir());
    }

    /**
     * Updates the Sandbox daemon's process environment.
     *
     * <p>Newly spawned processes, sessions, and PTYs inherit the change; already-running
     * processes keep their environment.
     *
     * @param env environment variables to set in the daemon's process environment
     * @throws DaytonaException if the update fails
     */
    public void updateEnv(Map<String, String> env) {
        updateEnv(env, null);
    }

    /**
     * Updates the Sandbox daemon's process environment.
     *
     * <p>Newly spawned processes, sessions, and PTYs inherit the change; already-running
     * processes keep their environment.
     *
     * @param env environment variables to set in the daemon's process environment; {@code null} to set none
     * @param unset environment variable names to remove; {@code null} to remove none
     * @throws DaytonaException if the update fails
     */
    public void updateEnv(Map<String, String> env, List<String> unset) {
        io.daytona.toolbox.client.model.UpdateEnvRequest request = new io.daytona.toolbox.client.model.UpdateEnvRequest();
        if (env != null) request.set(env);
        if (unset != null) request.unset(unset);
        // The daemon responds with a status message, not the resulting environment.
        ExceptionMapper.callToolbox(() -> serverApi.updateEnv(request));
    }

    /**
     * Waits until Sandbox reaches {@code started} state.
     *
     * @param timeoutSeconds maximum seconds to wait; {@code 0} disables timeout
     * @throws DaytonaException if timeout is invalid, state becomes failure, or timeout expires
     */
    public void waitUntilStarted(long timeoutSeconds) {
        ensureSubscribed();
        if (timeoutSeconds < 0) {
            throw new DaytonaException("Timeout must be non-negative");
        }
        if ("started".equalsIgnoreCase(state)) {
            return;
        }
        waitForState(STARTED_STATES, ERROR_STATES, timeoutSeconds, false);
    }

    /**
     * Waits for a resize operation to complete.
     *
     * @param timeoutSeconds maximum seconds to wait; {@code 0} disables timeout
     * @throws DaytonaException if resize times out or fails
     */
    public void waitForResizeComplete(long timeoutSeconds) {
        ensureSubscribed();
        if (timeoutSeconds < 0) {
            throw new DaytonaException("Timeout must be non-negative");
        }
        if (!"resizing".equalsIgnoreCase(state)) {
            return;
        }
        waitForState(RESIZE_TARGET_STATES, ERROR_STATES, timeoutSeconds, false);
    }

    /**
     * Refreshes local Sandbox fields from latest API state. After refresh, all fields
     * — including those not returned by {@link Daytona#list} — are populated.
     *
     * @throws DaytonaException if refresh fails
     */
    public void refreshData() {
        ensureSubscribed();
        io.daytona.api.client.model.Sandbox data = ExceptionMapper.callMain(() -> sandboxApi.getSandbox(id, null, null));
        if (data != null) {
            populateFromDTO(data);
        }
    }

    /**
     * Copies fields from the full {@link io.daytona.api.client.model.Sandbox} DTO onto this instance.
     *
     * <p>Populates every field, including those not returned by the list endpoint (env,
     * networkBlockAll, networkAllowList, volumes, buildInfo, backupCreatedAt).
     */
    private void populateFromDTO(io.daytona.api.client.model.Sandbox d) {
        if (d == null) {
            return;
        }
        populateCommonFields(
                d.getId(), d.getName(), d.getOrganizationId(), d.getSnapshot(), d.getUser(),
                d.getLabels(), d.getPublic(), d.getTarget(),
                d.getCpu(), d.getGpu(), d.getMemory(), d.getDisk(),
                d.getErrorReason(), d.getRecoverable(),
                d.getBackupState() == null ? null : d.getBackupState().getValue(),
                d.getAutoStopInterval(), d.getAutoPauseInterval(), d.getAutoArchiveInterval(), d.getAutoDeleteInterval(),
                d.getCreatedAt(), d.getUpdatedAt(), d.getLastActivityAt(), d.getAutoDestroyAt(),
                d.getToolboxProxyUrl()
        );

        // Fields only present on the full Sandbox DTO.
        this.env = d.getEnv() == null ? new HashMap<String, String>() : new HashMap<String, String>(d.getEnv());
        this.networkBlockAll = d.getNetworkBlockAll();
        this.networkAllowList = d.getNetworkAllowList();
        this.domainAllowList = d.getDomainAllowList();
        this.volumes = d.getVolumes() == null ? null : Collections.unmodifiableList(d.getVolumes());
        this.buildInfo = d.getBuildInfo();
        this.backupCreatedAt = d.getBackupCreatedAt();

        applyState(d.getState() == null ? "" : d.getState().getValue());
    }

    /**
     * Copies fields from a {@link SandboxListItem} DTO onto this instance.
     *
     * <p>The list endpoint omits env, networkBlockAll, networkAllowList, volumes, buildInfo, and
     * backupCreatedAt; those fields remain {@code null} until {@link #refreshData()} is called.
     */
    private void populateFromDTO(SandboxListItem d) {
        if (d == null) {
            return;
        }
        populateCommonFields(
                d.getId(), d.getName(), d.getOrganizationId(), d.getSnapshot(), d.getUser(),
                d.getLabels(), d.getPublic(), d.getTarget(),
                d.getCpu(), d.getGpu(), d.getMemory(), d.getDisk(),
                d.getErrorReason(), d.getRecoverable(),
                d.getBackupState() == null ? null : d.getBackupState().getValue(),
                d.getAutoStopInterval(), d.getAutoPauseInterval(), d.getAutoArchiveInterval(), d.getAutoDeleteInterval(),
                d.getCreatedAt(), d.getUpdatedAt(), d.getLastActivityAt(), d.getAutoDestroyAt(),
                d.getToolboxProxyUrl()
        );

        applyState(d.getState() == null ? "" : d.getState().getValue());
    }

    // Shared population logic for fields present on both Sandbox and SandboxListItem DTOs.
    // Takes already-extracted values (rather than the DTO itself) so the two type-safe overloads
    // above can each call it without referencing the other DTO's enum types.
    private void populateCommonFields(
            String id, String name, String organizationId, String snapshot, String user,
            Map<String, String> labels, Boolean isPublic, String target,
            BigDecimal cpu, BigDecimal gpu, BigDecimal memory, BigDecimal disk,
            String errorReason, Boolean recoverable, String backupState,
            BigDecimal autoStopInterval, BigDecimal autoPauseInterval, BigDecimal autoArchiveInterval, BigDecimal autoDeleteInterval,
            String createdAt, String updatedAt, String lastActivityAt, String autoDestroyAt,
            String toolboxProxyUrl) {
        this.id = asString(id);
        this.name = asString(name);
        this.organizationId = asString(organizationId);
        this.snapshot = snapshot;
        this.user = asString(user);
        this.labels = labels == null ? new HashMap<String, String>() : new HashMap<String, String>(labels);
        this.isPublic = isPublic;
        this.target = asString(target);
        this.cpu = cpu == null ? 0 : cpu.intValue();
        this.gpu = gpu == null ? 0 : gpu.intValue();
        this.memory = memory == null ? 0 : memory.intValue();
        this.disk = disk == null ? 0 : disk.intValue();
        this.errorReason = errorReason;
        this.recoverable = recoverable;
        this.backupState = backupState;
        this.autoStopInterval = autoStopInterval == null ? null : autoStopInterval.intValue();
        this.autoPauseInterval = autoPauseInterval == null ? null : autoPauseInterval.intValue();
        this.autoArchiveInterval = autoArchiveInterval == null ? null : autoArchiveInterval.intValue();
        this.autoDeleteInterval = autoDeleteInterval == null ? null : autoDeleteInterval.intValue();
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
        this.lastActivityAt = lastActivityAt;
        String newProxyUrl = asString(toolboxProxyUrl);
        if (!newProxyUrl.isEmpty() && !newProxyUrl.equals(this.toolboxProxyUrl) && this.toolboxApiClient != null) {
            this.toolboxApiClient.setBasePath(trimTrailingSlash(newProxyUrl) + "/" + this.id);
        }
        this.toolboxProxyUrl = newProxyUrl;
        this.autoDestroyAt = autoDestroyAt;
    }

    private void applyState(String newState) {
        if (newState == null || newState.equals(this.state)) {
            return;
        }
        this.state = newState;
        for (StateWaiter waiter : stateWaiters) {
            waiter.onStateChanged(newState);
        }
    }

    private void ensureSubscribed() {
        synchronized (subscriptionLock) {
            if (subId != null) {
                if (subscriptionManager.refresh(subId)) {
                    return;
                }
                subId = null;
            }

            subId = subscriptionManager.subscribe(id, this::handleEvent,
                    Arrays.asList("sandbox.state.updated", "sandbox.created"));
        }
    }

    private void handleEvent(String eventName, JsonNode data) {
        if (data == null || !data.isObject()) {
            return;
        }

        if ("sandbox.created".equals(eventName)) {
            JsonNode sandboxNode = data.has("sandbox") ? data.get("sandbox") : data;
            if (sandboxNode != null) {
                updateFromJsonEvent(sandboxNode);
            }
            return;
        }

        JsonNode stateNode = data.path("newState");
        if (!stateNode.isTextual()) {
            JsonNode sandboxNode = data.has("sandbox") ? data.get("sandbox") : data;
            if (sandboxNode != null) {
                stateNode = sandboxNode.path("state");
            }
        }

        if (stateNode.isTextual()) {
            applyState(stateNode.asText());
        }
    }

    private void unsubscribeFromEventsLocked() {
        if (subId == null) {
            subId = null;
            return;
        }

        subscriptionManager.unsubscribe(subId);
        subId = null;
    }

    private void waitForState(Set<String> targetStates, Set<String> errorStates,
                              long timeoutSeconds, boolean safeRefresh) {
        ensureSubscribed();
        boolean subscribed = hasActiveSubscription();
        StateWaiter waiter = new StateWaiter(targetStates, errorStates);
        stateWaiters.add(waiter);

        try {
            String current = state;
            if (current != null && targetStates.contains(current)) {
                return;
            }

            long pollIntervalMillis = subscribed ? POLL_STREAMING_INTERVAL_MILLIS : POLL_ONLY_INTERVAL_MILLIS;
            AtomicBoolean refreshInFlight = new AtomicBoolean(false);
            ScheduledFuture<?> pollFuture = POLL_SCHEDULER.scheduleAtFixedRate(() -> {
                if (waiter.isResolved()) {
                    return;
                }
                if (refreshInFlight.compareAndSet(false, true)) {
                    REFRESH_EXECUTOR.submit(() -> {
                        try {
                            runPollingRefresh(waiter, safeRefresh);
                        } finally {
                            refreshInFlight.set(false);
                        }
                    });
                }
            }, 0, pollIntervalMillis, TimeUnit.MILLISECONDS);

            try {
                boolean completed;
                if (timeoutSeconds > 0) {
                    completed = waiter.latch.await(timeoutSeconds, TimeUnit.SECONDS);
                } else {
                    waiter.latch.await();
                    completed = true;
                }

                if (!completed) {
                    pollFuture.cancel(false);
                    try {
                        if (safeRefresh) {
                            refreshDataSafe();
                        } else {
                            refreshData();
                        }
                    } catch (Exception ignored) {
                    }
                    // applyState no-ops when the state is unchanged, so
                    // explicitly evaluate for a persistent error state.
                    current = this.state;
                    if (current != null) {
                        waiter.onStateChanged(current);
                    }
                    if (waiter.isResolved()) {
                        waiter.throwIfError();
                        return;
                    }
                    throw new DaytonaTimeoutException("Sandbox " + id + " did not reach target state within " + timeoutSeconds + " seconds");
                }
                waiter.throwIfError();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new DaytonaException("Interrupted while waiting for sandbox state change", e);
            } finally {
                pollFuture.cancel(false);
            }
        } finally {
            stateWaiters.remove(waiter);
        }
    }

    private void refreshDataSafe() {
        try {
            refreshData();
        } catch (DaytonaNotFoundException e) {
            applyState("destroyed");
        }
    }

    private void runPollingRefresh(StateWaiter waiter, boolean safeRefresh) {
        try {
            if (safeRefresh) {
                refreshDataSafe();
            } else {
                refreshData();
            }
            // applyState no-ops when the state is unchanged, so explicitly
            // notify the waiter after each successful refresh.
            String current = this.state;
            if (current != null) {
                waiter.onStateChanged(current);
            }
        } catch (Exception e) {
            waiter.onPollError(e instanceof RuntimeException ? (RuntimeException) e : new DaytonaException(e.getMessage(), e));
        }
    }

    private boolean hasActiveSubscription() {
        synchronized (subscriptionLock) {
            return subId != null;
        }
    }

    private void updateFromJsonEvent(JsonNode node) {
        try {
            io.daytona.api.client.model.Sandbox model = OBJECT_MAPPER.treeToValue(node, io.daytona.api.client.model.Sandbox.class);
            if (model != null) {
                populateFromDTO(model);
            }
        } catch (Exception e) {
            JsonNode stateNode = node.path("state");
            if (stateNode.isTextual()) {
                applyState(stateNode.asText());
            }
        }
    }

    private static class StateWaiter {
        final Set<String> targetStates;
        final Set<String> errorStates;
        final AtomicReference<String> resolvedState = new AtomicReference<>();
        final AtomicReference<RuntimeException> pollError = new AtomicReference<>();
        final CountDownLatch latch = new CountDownLatch(1);

        StateWaiter(Set<String> targetStates, Set<String> errorStates) {
            this.targetStates = targetStates;
            this.errorStates = errorStates;
        }

        void onStateChanged(String newState) {
            if (targetStates.contains(newState) || errorStates.contains(newState)) {
                if (resolvedState.compareAndSet(null, newState)) {
                    latch.countDown();
                }
            }
        }

        boolean isResolved() {
            return resolvedState.get() != null || pollError.get() != null;
        }

        void onPollError(RuntimeException error) {
            if (pollError.compareAndSet(null, error)) {
                latch.countDown();
            }
        }

        void throwIfError() {
            RuntimeException refreshFailure = pollError.get();
            if (refreshFailure != null) {
                throw refreshFailure;
            }

            String resolved = resolvedState.get();
            if (resolved != null && errorStates.contains(resolved)) {
                throw new DaytonaException("Sandbox entered error state: " + resolved);
            }
        }
    }

    private String asString(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static String trimTrailingSlash(String value) {
        if (value == null) {
            return "";
        }
        String output = value;
        while (output.endsWith("/")) {
            output = output.substring(0, output.length() - 1);
        }
        return output;
    }

    /**
     * Forks this Sandbox, creating a new Sandbox with an identical filesystem.
     * Uses default timeout of 60 seconds.
     *
     * @return the forked {@link Sandbox} in started state
     * @throws DaytonaException if the fork operation fails or times out
     */
    public Sandbox experimentalFork() {
        return experimentalFork(null, 60);
    }

    /**
     * Forks this Sandbox, creating a new Sandbox with an identical filesystem.
     * The forked Sandbox is a copy-on-write clone of the original.
     *
     * @param name optional name for the forked Sandbox; {@code null} for auto-generated
     * @param timeoutSeconds maximum seconds to wait for the forked Sandbox to start; {@code 0} disables timeout
     * @return the forked {@link Sandbox} in started state
     * @throws DaytonaException if the fork operation fails or times out
     */
    public Sandbox experimentalFork(String name, long timeoutSeconds) {
        ForkSandbox forkReq = new ForkSandbox();
        if (name != null) {
            forkReq.setName(name);
        }
        io.daytona.api.client.model.Sandbox response = ExceptionMapper.callMain(
            () -> sandboxApi.forkSandbox(id, forkReq, null)
        );
        Sandbox forked = new Sandbox(sandboxApi, config, response, analyticsApiUrlProvider, subscriptionManager);
        forked.waitUntilStarted(timeoutSeconds);
        return forked;
    }

    /**
     * Creates a snapshot from the current state of this Sandbox.
     * Uses default timeout of 60 seconds.
     *
     * @param name name for the new snapshot
     * @throws DaytonaException if the snapshot operation fails
     */
    public void experimentalCreateSnapshot(String name) {
        experimentalCreateSnapshot(name, 60);
    }

    /**
     * Creates a snapshot from the current state of this Sandbox.
     * The Sandbox will temporarily enter a 'snapshotting' state and return to its previous state when complete.
     *
     * @param name name for the new snapshot
     * @param timeoutSeconds reserved timeout parameter for parity with other SDKs
     * @throws DaytonaException if the snapshot operation fails
     */
    public void experimentalCreateSnapshot(String name, long timeoutSeconds) {
        if (timeoutSeconds < 0) {
            throw new DaytonaException("Timeout must be non-negative");
        }
        CreateSandboxSnapshot req = new CreateSandboxSnapshot();
        req.setName(name);
        io.daytona.api.client.model.Sandbox response = ExceptionMapper.callMain(
            () -> sandboxApi.createSandboxSnapshot(id, req, null)
        );
        if (response != null) {
            populateFromDTO(response);
        }
        waitForSnapshotComplete(timeoutSeconds);
    }

    private void waitForSnapshotComplete(long timeoutSeconds) {
        ensureSubscribed();
        if (timeoutSeconds < 0) {
            throw new DaytonaException("Timeout must be non-negative");
        }
        if (!"snapshotting".equalsIgnoreCase(state)) {
            return;
        }
        waitForState(SNAPSHOT_TARGET_STATES, ERROR_STATES, timeoutSeconds, false);
    }

    /**
     * Pauses the Sandbox, freezing all running processes.
     * Uses default timeout of 60 seconds.
     *
     * @throws DaytonaException if the pause operation fails
     */
    public void pause() throws DaytonaException {
        pause(60);
    }

    /**
     * Pauses the Sandbox, freezing all running processes.
     * Completes when the Sandbox has left the {@code pausing} state — any non-error
     * terminal state (paused, stopped, archived, etc.) is accepted.
     *
     * @param timeoutSeconds maximum time to wait in seconds (0 = no timeout)
     * @throws DaytonaException if timeout is negative or the operation fails/times out
     */
    public void pause(long timeoutSeconds) throws DaytonaException {
        if (timeoutSeconds < 0) {
            throw new DaytonaException("Timeout must be a non-negative number");
        }
        ensureSubscribed();
        long startTime = System.currentTimeMillis();
        ExceptionMapper.callMain(() -> sandboxApi.pauseSandbox(id, null));
        refreshData();
        if (PAUSE_TARGET_STATES.contains(state)) {
            return;
        }
        long elapsed = (System.currentTimeMillis() - startTime) / 1000;
        long remaining = timeoutSeconds > 0 ? Math.max(1, timeoutSeconds - elapsed) : 0;
        waitForState(PAUSE_TARGET_STATES, ERROR_STATES, remaining, false);
    }

    /** @return Sandbox ID. */
    public String getId() { return id; }
    /** @return Sandbox name. */
    public String getName() { return name; }
    /** @return organization ID that owns this Sandbox. */
    public String getOrganizationId() { return organizationId; }
    /** @return Daytona snapshot used to create this Sandbox, or {@code null} if none. */
    public String getSnapshot() { return snapshot; }
    /** @return OS user running in the Sandbox. */
    public String getUser() { return user; }
    /** @return custom labels attached to the Sandbox. */
    public Map<String, String> getLabels() { return labels; }
    /** @return whether the Sandbox HTTP preview is publicly accessible. */
    public Boolean getPublic() { return isPublic; }
    /** @return target region/environment where the Sandbox runs. */
    public String getTarget() { return target; }
    /** @return allocated CPU cores. */
    public int getCpu() { return cpu; }
    /** @return allocated GPU units. */
    public int getGpu() { return gpu; }
    /** @return allocated memory in GiB. */
    public int getMemory() { return memory; }
    /** @return allocated disk in GiB. */
    public int getDisk() { return disk; }
    /** @return current lifecycle state (e.g. "started", "stopped"). */
    public String getState() { return state; }
    /** @return error message if the Sandbox is in an error state, or {@code null}. */
    public String getErrorReason() { return errorReason; }
    /** @return whether the Sandbox error is recoverable, or {@code null} if unknown. */
    public Boolean getRecoverable() { return recoverable; }
    /** @return current state of the Sandbox backup as a string, or {@code null}. */
    public String getBackupState() { return backupState; }
    /** @return auto-stop interval in minutes (0 means disabled). */
    public Integer getAutoStopInterval() { return autoStopInterval; }
    /** @return auto-pause interval in minutes (0 means disabled). */
    public Integer getAutoPauseInterval() { return autoPauseInterval; }
    /** @return auto-archive interval in minutes. */
    public Integer getAutoArchiveInterval() { return autoArchiveInterval; }
    /** @return auto-delete interval in minutes (negative means disabled). */
    public Integer getAutoDeleteInterval() { return autoDeleteInterval; }
    /** @return when the Sandbox was created, or {@code null}. */
    public String getCreatedAt() { return createdAt; }
    /** @return when the Sandbox was last updated, or {@code null}. */
    public String getUpdatedAt() { return updatedAt; }
    /** @return when the Sandbox last had activity, or {@code null}. */
    public String getLastActivityAt() { return lastActivityAt; }
    /** @return when the Sandbox expires, or {@code null} if no TTL is set. */
    public String getAutoDestroyAt() { return autoDestroyAt; }
    /** @return toolbox proxy URL. */
    public String getToolboxProxyUrl() { return toolboxProxyUrl; }

    /**
     * Returns Sandbox environment variables.
     *
     * <p>Not returned by {@link Daytona#list}; call {@link #refreshData()} on each item to populate.
     *
     * @return environment map, or {@code null} if not yet populated
     */
    public Map<String, String> getEnv() { return env; }
    /**
     * Returns whether all network access is blocked for this Sandbox.
     *
     * <p>Not returned by {@link Daytona#list}; call {@link #refreshData()} on each item to populate.
     *
     * @return block-all flag, or {@code null} if not yet populated
     */
    public Boolean getNetworkBlockAll() { return networkBlockAll; }
    /**
     * Returns the comma-separated CIDR allow list, if any.
     *
     * <p>Not returned by {@link Daytona#list}; call {@link #refreshData()} on each item to populate.
     *
     * @return allow list, or {@code null}
     */
    public String getNetworkAllowList() { return networkAllowList; }
    /**
     * Returns the comma-separated list of allowed domains, if any.
     *
     * <p>Not returned by {@link Daytona#list}; call {@link #refreshData()} on each item to populate.
     *
     * @return allowed domains, or {@code null}
     */
    public String getDomainAllowList() { return domainAllowList; }
    /**
     * Returns volumes attached to the Sandbox.
     *
     * <p>Not returned by {@link Daytona#list}; call {@link #refreshData()} on each item to populate.
     *
     * @return immutable list of attached volumes, or {@code null} if not yet populated
     */
    public List<SandboxVolume> getVolumes() { return volumes; }
    /**
     * Returns build information if the Sandbox was created from a dynamic build.
     *
     * <p>Not returned by {@link Daytona#list}; call {@link #refreshData()} on each item to populate.
     *
     * @return build info, or {@code null}
     */
    public BuildInfo getBuildInfo() { return buildInfo; }
    /**
     * Returns the creation timestamp of the last backup.
     *
     * <p>Not returned by {@link Daytona#list}; call {@link #refreshData()} on each item to populate.
     *
     * @return backup timestamp, or {@code null}
     */
    public String getBackupCreatedAt() { return backupCreatedAt; }

    /** @return process operations facade. */
    public Process getProcess() { return process; }
    /** @return file-system operations facade. */
    public FileSystem getFs() { return fs; }
    /** @return Git operations facade. */
    public Git getGit() { return git; }
    io.daytona.toolbox.client.ApiClient getToolboxApiClient() { return toolboxApiClient; }
    String getApiKey() { return apiKey; }
}
