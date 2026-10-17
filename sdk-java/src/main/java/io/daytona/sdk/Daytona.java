// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.sdk.exception.DaytonaException;
import io.daytona.sdk.model.CreateSandboxFromImageParams;
import io.daytona.sdk.model.CreateSandboxFromSnapshotParams;
import io.daytona.sdk.model.ListSandboxesQuery;

import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.logging.Logger;
import java.util.stream.Stream;

/**
 * Root Daytona client wrapper for the Daytona 1.x SDK.
 *
 * <p>Use {@code daytona.v1()} for Daytona 2.x-compatible call sites. Deprecated root resource methods
 * forward to the same {@link DaytonaV1} instance and emit a runtime warning once per process.
 *
 * @see DaytonaV1
 * @see DaytonaConfig
 * @see Sandbox
 */
public class Daytona implements AutoCloseable {
    public static final String V1_API_URL = DaytonaV1.V1_API_URL;

    /**
     * Deprecated: use {@link DaytonaV1#CODE_TOOLBOX_LANGUAGE_LABEL} instead. The root form is
     * removed in SDK 2.x.
     *
     * @deprecated Use {@link DaytonaV1#CODE_TOOLBOX_LANGUAGE_LABEL} instead. The root form is
     *     removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public static final String CODE_TOOLBOX_LANGUAGE_LABEL = DaytonaV1.CODE_TOOLBOX_LANGUAGE_LABEL;

    private static final Logger LOG = Logger.getLogger(Daytona.class.getName());
    private static final AtomicBoolean ROOT_NAMESPACE_WARNING_EMITTED = new AtomicBoolean(false);

    private final DaytonaV1 v1;

    /**
     * Creates a root client using environment variables.
     *
     * <p>Reads {@code DAYTONA_API_KEY}, {@code DAYTONA_API_URL}, and the deprecated
     * {@code DAYTONA_TARGET}.
     *
     * @throws DaytonaException if required authentication is missing
     */
    public Daytona() {
        this(new DaytonaV1());
    }

    /**
     * Creates a root client with explicit configuration.
     *
     * @param config SDK configuration containing API key and endpoint settings
     * @throws DaytonaException if configuration is invalid or missing credentials
     */
    public Daytona(DaytonaConfig config) {
        this(new DaytonaV1(config));
    }

    Daytona(DaytonaV1 v1) {
        if (v1 == null) {
            throw new IllegalArgumentException("v1 client is required");
        }
        this.v1 = v1;
    }

    /**
     * Returns the cached v1 client implementation.
     *
     * @return cached v1 client instance
     */
    public DaytonaV1 v1() {
        return v1;
    }

    /**
     * Creates a Sandbox with default parameters and timeout.
     *
     * @return created and started {@link Sandbox}
     * @throws DaytonaException if creation or startup fails
     * @deprecated Use {@code daytona.v1().create()} instead. The root form is removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public Sandbox create() {
        warnRootNamespaceUsage("create()", "v1().create()");
        return v1.create();
    }

    /**
     * Creates a Sandbox from snapshot-oriented parameters using default timeout.
     *
     * @param params snapshot creation parameters
     * @return created and started {@link Sandbox}
     * @throws DaytonaException if creation or startup fails
     * @deprecated Use {@code daytona.v1().create(params)} instead. The root form is removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public Sandbox create(CreateSandboxFromSnapshotParams params) {
        warnRootNamespaceUsage("create(CreateSandboxFromSnapshotParams)", "v1().create(params)");
        return v1.create(params);
    }

    /**
     * Creates a Sandbox from image-oriented parameters using default timeout.
     *
     * @param params image creation parameters
     * @return created and started {@link Sandbox}
     * @throws DaytonaException if creation or startup fails
     * @deprecated Use {@code daytona.v1().create(params)} instead. The root form is removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public Sandbox create(CreateSandboxFromImageParams params) {
        warnRootNamespaceUsage("create(CreateSandboxFromImageParams)", "v1().create(params)");
        return v1.create(params);
    }

    /**
     * Creates a Sandbox from snapshot parameters.
     *
     * @param params snapshot creation parameters including env vars, labels, and lifecycle options
     * @param timeoutSeconds maximum seconds to wait for the Sandbox to reach {@code started}
     * @return created and started {@link Sandbox}
     * @throws DaytonaException if creation fails or the Sandbox does not start in time
     * @deprecated Use {@code daytona.v1().create(params, timeoutSeconds)} instead. The root form
     *     is removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public Sandbox create(CreateSandboxFromSnapshotParams params, long timeoutSeconds) {
        warnRootNamespaceUsage("create(CreateSandboxFromSnapshotParams, long)", "v1().create(params, timeoutSeconds)");
        return v1.create(params, timeoutSeconds);
    }

    /**
     * Creates a Sandbox from image parameters.
     *
     * @param params image creation parameters including image source and optional resources
     * @param timeoutSeconds maximum seconds to wait for the Sandbox to reach {@code started}
     * @return created and started {@link Sandbox}
     * @throws DaytonaException if creation fails or the Sandbox does not start in time
     * @deprecated Use {@code daytona.v1().create(params, timeoutSeconds)} instead. The root form
     *     is removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public Sandbox create(CreateSandboxFromImageParams params, long timeoutSeconds) {
        warnRootNamespaceUsage("create(CreateSandboxFromImageParams, long)", "v1().create(params, timeoutSeconds)");
        return v1.create(params, timeoutSeconds);
    }

    /**
     * Creates a new Sandbox from a declarative image with build log streaming.
     *
     * @param params creation parameters including the image definition
     * @param timeoutSeconds maximum seconds to wait for the Sandbox to reach {@code started}
     * @param onSnapshotCreateLogs callback for build log lines; {@code null} to skip streaming
     * @return created and started {@link Sandbox}
     * @throws DaytonaException if creation fails or the Sandbox does not start in time
     * @deprecated Use {@code daytona.v1().create(params, timeoutSeconds, onSnapshotCreateLogs)}
     *     instead. The root form is removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public Sandbox create(CreateSandboxFromImageParams params, long timeoutSeconds, Consumer<String> onSnapshotCreateLogs) {
        warnRootNamespaceUsage(
                "create(CreateSandboxFromImageParams, long, Consumer)",
                "v1().create(params, timeoutSeconds, onSnapshotCreateLogs)");
        return v1.create(params, timeoutSeconds, onSnapshotCreateLogs);
    }

    /**
     * Retrieves a Sandbox by ID or name.
     *
     * @param sandboxIdOrName Sandbox identifier or name
     * @return resolved {@link Sandbox}
     * @throws DaytonaException if the Sandbox is not found or request fails
     * @deprecated Use {@code daytona.v1().get(sandboxIdOrName)} instead. The root form is removed
     *     in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public Sandbox get(String sandboxIdOrName) {
        warnRootNamespaceUsage("get(String)", "v1().get(sandboxIdOrName)");
        return v1.get(sandboxIdOrName);
    }

    /**
     * Iterates over all Sandboxes (no filter, default sort).
     *
     * @return iterable over Sandboxes
     * @deprecated Use {@code daytona.v1().list()} instead. The root form is removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public Iterable<Sandbox> list() {
        warnRootNamespaceUsage("list()", "v1().list()");
        return v1.list();
    }

    /**
     * Iterates over Sandboxes matching the given query.
     *
     * @param query optional filters, sorting, and per-page size
     * @return iterable over Sandboxes
     * @deprecated Use {@code daytona.v1().list(query)} instead. The root form is removed in SDK
     *     2.x.
     */
    @Deprecated(since = "1.0")
    public Iterable<Sandbox> list(ListSandboxesQuery query) {
        warnRootNamespaceUsage("list(ListSandboxesQuery)", "v1().list(query)");
        return v1.list(query);
    }

    /**
     * Streams all Sandboxes (no filter, default sort).
     *
     * @return stream of Sandboxes
     * @deprecated Use {@code daytona.v1().listStream()} instead. The root form is removed in SDK
     *     2.x.
     */
    @Deprecated(since = "1.0")
    public Stream<Sandbox> listStream() {
        warnRootNamespaceUsage("listStream()", "v1().listStream()");
        return v1.listStream();
    }

    /**
     * Streams Sandboxes matching the given query.
     *
     * @param query optional filters, sorting, and per-page size
     * @return stream of Sandboxes
     * @deprecated Use {@code daytona.v1().listStream(query)} instead. The root form is removed in
     *     SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public Stream<Sandbox> listStream(ListSandboxesQuery query) {
        warnRootNamespaceUsage("listStream(ListSandboxesQuery)", "v1().listStream(query)");
        return v1.listStream(query);
    }

    /**
     * Returns Snapshot management service.
     *
     * @return snapshot service instance
     * @deprecated Use {@code daytona.v1().snapshot()} instead. The root form is removed in SDK
     *     2.x.
     */
    @Deprecated(since = "1.0")
    public SnapshotService snapshot() {
        warnRootNamespaceUsage("snapshot()", "v1().snapshot()");
        return v1.snapshot();
    }

    /**
     * Returns Volume management service.
     *
     * @return volume service instance
     * @deprecated Use {@code daytona.v1().volume()} instead. The root form is removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public VolumeService volume() {
        warnRootNamespaceUsage("volume()", "v1().volume()");
        return v1.volume();
    }

    /**
     * Returns Secret management service.
     *
     * @return secret service instance
     * @deprecated Use {@code daytona.v1().secret()} instead. The root form is removed in SDK 2.x.
     */
    @Deprecated(since = "1.0")
    public SecretService secret() {
        warnRootNamespaceUsage("secret()", "v1().secret()");
        return v1.secret();
    }

    /**
     * Returns Warm Pool management service.
     *
     * @return warm pool service instance
     * @deprecated Use {@code daytona.v1().warmPool()} instead. The root form is removed in SDK
     *     2.x.
     */
    @Deprecated(since = "1.0")
    public WarmPoolService warmPool() {
        warnRootNamespaceUsage("warmPool()", "v1().warmPool()");
        return v1.warmPool();
    }

    /**
     * Closes this client and releases underlying HTTP resources.
     */
    @Override
    public void close() {
        v1.close();
    }

    static String urlEncodePathSegment(String value) {
        return DaytonaV1.urlEncodePathSegment(value);
    }

    static String urlEncodeQuery(String value) {
        return DaytonaV1.urlEncodeQuery(value);
    }

    static Map<String, String> castStringMap(Map<?, ?> source) {
        return DaytonaV1.castStringMap(source);
    }

    static void shutdownHttpClient(okhttp3.OkHttpClient client) {
        DaytonaV1.shutdownHttpClient(client);
    }

    static void resetRootNamespaceWarningForTests() {
        ROOT_NAMESPACE_WARNING_EMITTED.set(false);
    }

    private static void warnRootNamespaceUsage(String member, String replacement) {
        if (ROOT_NAMESPACE_WARNING_EMITTED.compareAndSet(false, true)) {
            LOG.warning("Daytona." + member + " is deprecated. Use Daytona." + replacement
                    + " or DaytonaV1 instead. The root form is removed in SDK 2.x.");
        }
    }
}
