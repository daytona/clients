// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.api.SnapshotsApi;
import io.daytona.api.client.model.CreateBuildInfo;
import io.daytona.api.client.model.CreateSnapshot;
import io.daytona.api.client.model.SandboxClass;
import io.daytona.sdk.exception.DaytonaException;
import io.daytona.sdk.exception.DaytonaNotFoundException;
import io.daytona.sdk.model.PaginatedSnapshots;
import io.daytona.sdk.model.Snapshot;
import okhttp3.OkHttpClient;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.regex.Pattern;

/**
 * Service for managing Daytona Snapshots.
 *
 * <p>Provides operations to create, list, retrieve, and delete snapshots.
 */
public class SnapshotService {
    /**
     * Matches RFC 4122 UUIDs (versions 1-5) and the nil UUID — the same set the
     * Daytona API recognizes as snapshot IDs. Anything else is treated as a name.
     *
     * <p>Note: {@link java.util.UUID#fromString(String)} is intentionally NOT used
     * for validation because it accepts non-canonical forms (e.g. {@code 1-1-1-1-1}).
     */
    private static final Pattern SNAPSHOT_ID_PATTERN = Pattern.compile(
            "(?i)^(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|00000000-0000-0000-0000-000000000000)$");

    private final SnapshotsApi snapshotsApi;
    private final OkHttpClient httpClient;
    private final String apiKey;

    SnapshotService(SnapshotsApi snapshotsApi, OkHttpClient httpClient, String apiKey) {
        this.snapshotsApi = snapshotsApi;
        this.httpClient = httpClient;
        this.apiKey = apiKey;
    }

    /**
     * Creates a snapshot from an existing image reference.
     *
     * @param name snapshot name
     * @param imageName source image name or tag
     * @return created {@link Snapshot}
     * @throws io.daytona.sdk.exception.DaytonaException if the API request fails
     */
    public Snapshot create(String name, String imageName) {
        return create(name, imageName, null);
    }

    /**
     * Creates a snapshot from an existing image reference with a target sandbox class.
     *
     * @param name snapshot name
     * @param imageName source image name or tag
     * @param sandboxClass target sandbox class; {@code null} for default
     * @return created {@link Snapshot}
     * @throws io.daytona.sdk.exception.DaytonaException if the API request fails
     */
    public Snapshot create(String name, String imageName, SandboxClass sandboxClass) {
        return create(name, imageName, sandboxClass, null);
    }

    /**
     * Creates a snapshot from an existing image reference, available in the given regions.
     *
     * @param name snapshot name
     * @param imageName source image name or tag
     * @param sandboxClass target sandbox class; {@code null} for default
     * @param regionIds IDs of the regions where the snapshot will be available; {@code null} or empty
     *     for the organization default region. Duplicates are ignored and the order carries no meaning —
     *     the server selects the region that performs the initial build or pull. Requesting more than one
     *     region requires the multi-region snapshots feature to be enabled for the organization, is not
     *     supported for GPU snapshots, and is only possible between regions that share an internal registry.
     * @return created {@link Snapshot}
     * @throws io.daytona.sdk.exception.DaytonaException if the API request fails
     */
    public Snapshot create(String name, String imageName, SandboxClass sandboxClass, List<String> regionIds) {
        CreateSnapshot req = new CreateSnapshot().name(name).imageName(imageName);
        if (sandboxClass != null) {
            req.setSandboxClass(sandboxClass);
        }
        if (regionIds != null && !regionIds.isEmpty()) {
            req.setRegionIds(regionIds);
        }
        io.daytona.api.client.model.SnapshotDto snapshotDto = ExceptionMapper.callMain(
                () -> snapshotsApi.createSnapshot(req, null)
        );
        return toSnapshot(snapshotDto);
    }

    /**
     * Creates a snapshot from a declarative {@link Image} with optional build log streaming.
     *
     * @param name snapshot name
     * @param image declarative image definition
     * @param onLogs callback for build log lines; {@code null} to skip streaming
     * @return created {@link Snapshot} in active or error state
     * @throws DaytonaException if the API request fails or the build fails
     */
    public Snapshot create(String name, Image image, Consumer<String> onLogs) {
        return create(name, image, null, null, onLogs);
    }

    /**
     * Creates a snapshot from a declarative {@link Image} with resources and optional build log streaming.
     *
     * @param name snapshot name
     * @param image declarative image definition
     * @param resources CPU/GPU/memory/disk resources; {@code null} for defaults
     * @param onLogs callback for build log lines; {@code null} to skip streaming
     * @return created {@link Snapshot} in active or error state
     * @throws DaytonaException if the API request fails or the build fails
     */
    public Snapshot create(String name, Image image, io.daytona.sdk.model.Resources resources, Consumer<String> onLogs) {
        return create(name, image, resources, null, onLogs);
    }

    /**
     * Creates a snapshot from a declarative {@link Image} with resources, sandbox class, and optional build log streaming.
     *
     * @param name snapshot name
     * @param image declarative image definition
     * @param resources CPU/GPU/memory/disk resources; {@code null} for defaults
     * @param sandboxClass target sandbox class; {@code null} for default
     * @param onLogs callback for build log lines; {@code null} to skip streaming
     * @return created {@link Snapshot} in active or error state
     * @throws DaytonaException if the API request fails or the build fails
     */
    public Snapshot create(String name, Image image, io.daytona.sdk.model.Resources resources, SandboxClass sandboxClass, Consumer<String> onLogs) {
        return create(name, image, resources, sandboxClass, null, onLogs);
    }

    /**
     * Creates a snapshot from a declarative {@link Image}, available in the given regions, with optional build log streaming.
     *
     * @param name snapshot name
     * @param image declarative image definition
     * @param resources CPU/GPU/memory/disk resources; {@code null} for defaults
     * @param sandboxClass target sandbox class; {@code null} for default
     * @param regionIds IDs of the regions where the snapshot will be available; {@code null} or empty
     *     for the organization default region. Duplicates are ignored and the order carries no meaning —
     *     the server selects the region that performs the initial build or pull. Requesting more than one
     *     region requires the multi-region snapshots feature to be enabled for the organization, is not
     *     supported for GPU snapshots, and is only possible between regions that share an internal registry.
     * @param onLogs callback for build log lines; {@code null} to skip streaming
     * @return created {@link Snapshot} in active or error state
     * @throws DaytonaException if the API request fails or the build fails
     */
    public Snapshot create(String name, Image image, io.daytona.sdk.model.Resources resources, SandboxClass sandboxClass, List<String> regionIds, Consumer<String> onLogs) {
        CreateSnapshot req = new CreateSnapshot().name(name)
                .buildInfo(new CreateBuildInfo().dockerfileContent(image.getDockerfile()))
                .entrypoint(null);

        if (resources != null) {
            if (resources.getCpu() != null) req.setCpu(resources.getCpu());
            if (resources.getGpu() != null) req.setGpu(resources.getGpu());
            if (resources.getGpuType() != null) req.setGpuType(resources.getGpuType());
            if (resources.getMemory() != null) req.setMemory(resources.getMemory());
            if (resources.getDisk() != null) req.setDisk(resources.getDisk());
        }

        if (sandboxClass != null) {
            req.setSandboxClass(sandboxClass);
        }

        if (regionIds != null && !regionIds.isEmpty()) {
            req.setRegionIds(regionIds);
        }

        final io.daytona.api.client.model.SnapshotDto[] ref = { ExceptionMapper.callMain(
                () -> snapshotsApi.createSnapshot(req, null)
        )};

        if (ref[0] == null) {
            throw new DaytonaException("Failed to create snapshot — no response from API");
        }

        List<String> terminalStates = Arrays.asList("active", "error", "build_failed");
        final String snapshotId = ref[0].getId();
        final String snapshotName = ref[0].getName();

        if (onLogs != null) {
            onLogs.accept("Creating snapshot " + snapshotName + " (" + stateString(ref[0]) + ")");
        }

        boolean logStreamStarted = false;
        while (!terminalStates.contains(stateString(ref[0]))) {
            if (onLogs != null && !logStreamStarted && !"pending".equals(stateString(ref[0]))) {
                logStreamStarted = true;
                io.daytona.api.client.model.Url logsUrl = ExceptionMapper.callMain(
                        () -> snapshotsApi.getSnapshotBuildLogsUrl(snapshotId, null));
                new BuildLogStreamer(httpClient, apiKey).streamLogs(
                        logsUrl.getUrl(), onLogs,
                        () -> terminalStates.contains(stateString(ref[0])));
            }
            try { Thread.sleep(1000); } catch (InterruptedException e) { Thread.currentThread().interrupt(); break; }
            ref[0] = ExceptionMapper.callMain(() -> snapshotsApi.getSnapshot(snapshotName, null));
        }

        if (onLogs != null && "active".equals(stateString(ref[0]))) {
            onLogs.accept("Created snapshot " + snapshotName + " (" + stateString(ref[0]) + ")");
        }

        if ("error".equals(stateString(ref[0])) || "build_failed".equals(stateString(ref[0]))) {
            throw new DaytonaException("Snapshot build failed: " + snapshotName + " (" + stateString(ref[0]) + ")");
        }

        return toSnapshot(ref[0]);
    }

    /**
     * Lists snapshots with pagination.
     *
     * <pre>{@code
     * try (Daytona daytona = new Daytona()) {
     *     PaginatedSnapshots page = daytona.snapshot().list(2, 10);
     *     System.out.printf("Page %d of %d (%d snapshots total)%n",
     *             page.getPage(), page.getTotalPages(), page.getTotal());
     *     for (var snapshot : page.getItems()) {
     *         System.out.println(snapshot.getName() + " (" + snapshot.getImageName() + ")");
     *     }
     * }
     * }</pre>
     *
     * @param page page number starting from 1; defaults to 1 when {@code null}
     * @param limit maximum number of items per page; defaults to 10 when {@code null}
     * @return paginated snapshot result
     * @throws io.daytona.sdk.exception.DaytonaException if the API request fails
     */
    public PaginatedSnapshots list(Integer page, Integer limit) {
        return list(page, limit, null);
    }

    /**
     * Lists snapshots with pagination, optionally filtered by source sandbox.
     *
     * @param page page number starting from 1; defaults to 1 when {@code null}
     * @param limit maximum number of items per page; defaults to 10 when {@code null}
     * @param sourceSandboxId filter by the ID of the sandbox the snapshot was created from; ignored when {@code null}
     * @return paginated snapshot result
     * @throws io.daytona.sdk.exception.DaytonaException if the API request fails
     */
    public PaginatedSnapshots list(Integer page, Integer limit, String sourceSandboxId) {
        int p = page == null ? 1 : page;
        int l = limit == null ? 10 : limit;
        io.daytona.api.client.model.PaginatedSnapshots result = ExceptionMapper.callMain(
                () -> snapshotsApi.getAllSnapshots(null, BigDecimal.valueOf(p), BigDecimal.valueOf(l), null, sourceSandboxId, null, null)
        );

        PaginatedSnapshots output = new PaginatedSnapshots();
        List<Snapshot> items = new ArrayList<Snapshot>();
        if (result != null && result.getItems() != null) {
            for (io.daytona.api.client.model.SnapshotDto snapshot : result.getItems()) {
                items.add(toSnapshot(snapshot));
            }
        }
        output.setItems(items);
        output.setTotal(result != null && result.getTotal() != null ? result.getTotal().intValue() : 0);
        output.setPage(result != null && result.getPage() != null ? result.getPage().intValue() : 0);
        output.setTotalPages(result != null && result.getTotalPages() != null ? result.getTotalPages().intValue() : 0);
        return output;
    }

    /**
     * Retrieves a snapshot by name or ID.
     *
     * @param nameOrId snapshot name or identifier
     * @return matching {@link Snapshot}
     * @throws io.daytona.sdk.exception.DaytonaException if no snapshot is found or request fails
     */
    public Snapshot get(String nameOrId) {
        io.daytona.api.client.model.SnapshotDto snapshotDto = ExceptionMapper.callMain(() -> snapshotsApi.getSnapshot(nameOrId, null));
        return toSnapshot(snapshotDto);
    }

    /**
     * Deletes a snapshot by ID or name.
     *
     * <p>Snapshot names may themselves be UUID-formatted, so a UUID-shaped input is first
     * tried directly against the ID-only delete endpoint (1 call) and only resolved through
     * the ID-or-name {@code GET /snapshots} lookup on a 404. Non-UUID input is resolved first (2 calls);
     * a UUID-formatted name costs 3 calls in the worst case.
     *
     * @param nameOrId snapshot identifier or name
     * @throws io.daytona.sdk.exception.DaytonaException if deletion fails
     */
    public void delete(String nameOrId) {
        callWithResolvedId(nameOrId, id -> {
            ExceptionMapper.runMain(() -> snapshotsApi.removeSnapshot(id, null));
            return null;
        });
    }

    /**
     * Deletes a snapshot using its already-known identifier.
     *
     * <p>Issues a single delete call — no resolution is performed.
     *
     * @param snapshot snapshot to delete; its {@link Snapshot#getId() id} is used verbatim
     * @throws io.daytona.sdk.exception.DaytonaException if deletion fails
     */
    public void delete(Snapshot snapshot) {
        ExceptionMapper.runMain(() -> snapshotsApi.removeSnapshot(snapshot.getId(), null));
    }

    /**
     * Activates a snapshot by ID or name.
     *
     * <p>Snapshot names may themselves be UUID-formatted, so a UUID-shaped input is first
     * tried directly against the ID-only activate endpoint (1 call) and only resolved through
     * the ID-or-name {@code GET /snapshots} lookup on a 404. Non-UUID input is resolved first (2 calls);
     * a UUID-formatted name costs 3 calls in the worst case.
     *
     * @param nameOrId snapshot identifier or name
     * @return the activated {@link Snapshot}
     * @throws io.daytona.sdk.exception.DaytonaException if activation fails or no snapshot is found
     */
    public Snapshot activate(String nameOrId) {
        io.daytona.api.client.model.SnapshotDto snapshotDto = callWithResolvedId(
                nameOrId,
                id -> ExceptionMapper.callMain(() -> snapshotsApi.activateSnapshot(id, null)));
        return toSnapshot(snapshotDto);
    }

    /**
     * Activates a snapshot using its already-known identifier.
     *
     * <p>Issues a single activate call — no resolution is performed.
     *
     * @param snapshot snapshot to activate; its {@link Snapshot#getId() id} is used verbatim
     * @return the activated {@link Snapshot}
     * @throws io.daytona.sdk.exception.DaytonaException if activation fails
     */
    public Snapshot activate(Snapshot snapshot) {
        io.daytona.api.client.model.SnapshotDto snapshotDto = ExceptionMapper.callMain(
                () -> snapshotsApi.activateSnapshot(snapshot.getId(), null));
        return toSnapshot(snapshotDto);
    }

    /**
     * Invokes an ID-based Snapshot operation, resolving {@code nameOrId} with as few API
     * calls as possible.
     *
     * <p>A UUID-shaped input is optimistically tried as an ID (1 call). If the API responds
     * 404, the input may still be a UUID-formatted name; the method falls back to
     * {@link #get(String) get} to resolve the real ID and retries the operation. Any 404
     * from the final call — or from {@code get()} — propagates to the caller.
     *
     * @param nameOrId snapshot identifier or name
     * @param operation ID-only operation to invoke
     * @param <T> operation return type
     */
    private <T> T callWithResolvedId(String nameOrId, Function<String, T> operation) {
        if (isSnapshotId(nameOrId)) {
            try {
                return operation.apply(nameOrId);
            } catch (DaytonaNotFoundException e) {
                // Not an existing ID — may still be a UUID-formatted name; fall through to resolution.
            }
        }
        Snapshot resolved = get(nameOrId);
        return operation.apply(resolved.getId());
    }

    private static boolean isSnapshotId(String value) {
        return value != null && SNAPSHOT_ID_PATTERN.matcher(value).matches();
    }

    private String stateString(io.daytona.api.client.model.SnapshotDto dto) {
        return dto.getState() == null ? "" : dto.getState().getValue();
    }

    private Snapshot toSnapshot(io.daytona.api.client.model.SnapshotDto source) {
        Snapshot snapshot = new Snapshot();
        if (source != null) {
            snapshot.setId(source.getId());
            snapshot.setName(source.getName());
            snapshot.setImageName(source.getImageName());
            snapshot.setState(source.getState() == null ? null : source.getState().getValue());
            snapshot.setRegionIds(source.getRegionIds());
        }
        return snapshot;
    }
}
