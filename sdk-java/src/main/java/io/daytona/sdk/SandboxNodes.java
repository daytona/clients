// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.api.SandboxApi;
import io.daytona.api.client.model.AddGangNodes;
import io.daytona.api.client.model.GangMembershipOperation;
import io.daytona.api.client.model.GangNodeResources;
import io.daytona.api.client.model.RemoveGangNodes;
import io.daytona.api.client.model.ReplaceGangNode;
import io.daytona.sdk.exception.DaytonaException;
import io.daytona.sdk.exception.DaytonaTimeoutException;
import io.daytona.sdk.exception.DaytonaNodeOperationException;
import io.daytona.sdk.model.ExecuteResponse;
import io.daytona.sdk.model.NodeExecuteOptions;
import io.daytona.sdk.model.NodeResources;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.AbstractList;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.BiConsumer;

/** Read-only, live rank-ordered members, including the head at index zero. */
public final class SandboxNodes extends AbstractList<SandboxNode> {
    static final int GPUS_PER_GANG_NODE = 8;
    private static final String RDMA_CHECK = "sh -lc 'ldconfig -p 2>/dev/null | grep -q libibverbs.so.1 && find /usr/lib /lib -name \"libmlx5-rdmav*.so\" -print -quit 2>/dev/null | grep -q .'";
    private static final String RDMA_WARNING = "WARNING: rdma-core userspace (libibverbs.so.1 and an mlx5 provider) is missing; NCCL may fall back to sockets.";
    private final Sandbox parent;
    private volatile List<SandboxNode> members = Collections.emptyList();
    private final NodeProcess process = new NodeProcess();
    private final NodeFileSystem fs = new NodeFileSystem();

    SandboxNodes(Sandbox parent) { this.parent = parent; }
    @Override
    public SandboxNode get(int rank) { return members.get(rank); }
    @Override
    public int size() { return members.size(); }
    public NodeProcess getProcess() { return process; }
    public NodeFileSystem getFs() { return fs; }

    synchronized void updateProxy(String proxy) {
        for (SandboxNode node : members) node.updateProxy(proxy);
    }

    synchronized void update(List<io.daytona.api.client.model.SandboxNode> raw) {
        if (raw == null || raw.isEmpty()) {
            if (!members.isEmpty()) { updateProxy(parent.getToolboxProxyUrl()); return; }
            int count = Math.max(1, parent.getNodeCount());
            raw = Collections.singletonList(new io.daytona.api.client.model.SandboxNode()
                    .id(parent.getId()).rank(0).incarnation(1)
                    .state(io.daytona.api.client.model.SandboxState.fromValue(parent.getState()))
                    .cpu(parent.getCpu() / count).gpu(parent.getGpu() / count)
                    .memory(parent.getMemory() / count).disk(parent.getDisk() / count));
        }
        Map<String, SandboxNode> existing = new HashMap<>();
        for (SandboxNode node : members) existing.put(node.getId(), node);
        Set<String> seen = new HashSet<>();
        for (int rank = 0; rank < raw.size(); rank++) {
            io.daytona.api.client.model.SandboxNode node = raw.get(rank);
            if (node == null || node.getRank() == null || node.getRank() != rank ||
                    node.getId() == null || node.getId().isEmpty() || ".".equals(node.getId()) ||
                    "..".equals(node.getId()) || !seen.add(node.getId()) ||
                    (rank == 0 && !parent.getId().equals(node.getId())) ||
                    (existing.containsKey(node.getId()) && existing.get(node.getId()).getRank() != rank)) {
                throw new DaytonaException("Invalid sandbox node ordering or identity");
            }
        }
        List<SandboxNode> updated = new ArrayList<>();
        for (io.daytona.api.client.model.SandboxNode data : raw) {
            SandboxNode node = existing.get(data.getId());
            if (node == null) node = new SandboxNode(parent, data);
            else node.update(data, parent.getToolboxProxyUrl());
            updated.add(node);
        }
        members = Collections.unmodifiableList(updated);
    }

    private int generation(NodeDeadline deadline) {
        SandboxApi api = parent.nodeSandboxApi();
        io.daytona.api.client.model.Sandbox response = deadline.main(api.getApiClient(),
                () -> api.getSandboxCall(parent.getId(), null, null, null),
                io.daytona.api.client.model.Sandbox.class);
        parent.applyNodeResponse(response);
        if (parent.getTopologyGeneration() == null) {
            throw new DaytonaException("Sandbox does not expose a topology generation; elastic node operations are unavailable");
        }
        deadline.check();
        return parent.getTopologyGeneration();
    }

    private GangMembershipOperation waitForOperation(GangMembershipOperation operation, NodeDeadline deadline) {
        SandboxApi api = parent.nodeSandboxApi();
        deadline.check();
        while (operation.getStatus() != GangMembershipOperation.StatusEnum.COMPLETED &&
                operation.getStatus() != GangMembershipOperation.StatusEnum.FAILED) {
            deadline.pause(1000);
            String id = operation.getId();
            operation = deadline.main(api.getApiClient(),
                    () -> api.getGangMembershipOperationCall(parent.getId(), id, null, null),
                    GangMembershipOperation.class);
        }
        if (operation.getStatus() == GangMembershipOperation.StatusEnum.FAILED) {
            throw new DaytonaException(operation.getErrorReason() == null ? "Node membership operation failed" : operation.getErrorReason());
        }
        parent.applyNodeResponse(deadline.main(api.getApiClient(),
                () -> api.getSandboxCall(parent.getId(), null, null, null),
                io.daytona.api.client.model.Sandbox.class));
        deadline.check();
        return operation;
    }

    /** Append rank-ordered nodes; a timeout ends the wait, not the server-side operation. */
    public GangMembershipOperation add(List<NodeResources> nodes) { return add(nodes, 600); }
    public GangMembershipOperation add(List<NodeResources> nodes, long timeoutSeconds) {
        if (nodes == null || nodes.isEmpty()) throw new DaytonaException("At least one node resource specification is required");
        List<GangNodeResources> resources = new ArrayList<>();
        for (NodeResources node : nodes) {
            if (node == null) throw new DaytonaException("Node resources must not be null");
            resources.add(new GangNodeResources().cpu(node.getCpu()).memory(node.getMemory()).disk(node.getDisk()));
        }
        NodeDeadline deadline = new NodeDeadline(timeoutSeconds);
        AddGangNodes request = new AddGangNodes().expectedGeneration(generation(deadline)).nodes(resources);
        SandboxApi api = parent.nodeSandboxApi();
        return waitForOperation(deadline.main(api.getApiClient(),
                () -> api.addSandboxNodesCall(parent.getId(), request, null, null), GangMembershipOperation.class), deadline);
    }

    /** Release the highest count ranks. Zero timeout disables the overall wait limit. */
    public GangMembershipOperation release(int count) { return release(count, 600); }
    public GangMembershipOperation release(int count, long timeoutSeconds) {
        if (count < 1) throw new DaytonaException("Node release count must be positive");
        NodeDeadline deadline = new NodeDeadline(timeoutSeconds);
        RemoveGangNodes request = new RemoveGangNodes().expectedGeneration(generation(deadline)).count(count);
        SandboxApi api = parent.nodeSandboxApi();
        return waitForOperation(deadline.main(api.getApiClient(),
                () -> api.removeSandboxNodesCall(parent.getId(), request, null, null), GangMembershipOperation.class), deadline);
    }

    /** Replace a degraded logical rank and refresh retained node handles after completion. */
    public GangMembershipOperation replace(int rank) { return replace(rank, 600); }
    public GangMembershipOperation replace(int rank, long timeoutSeconds) {
        if (rank < 0) throw new DaytonaException("Node rank must be non-negative");
        NodeDeadline deadline = new NodeDeadline(timeoutSeconds);
        ReplaceGangNode request = new ReplaceGangNode().expectedGeneration(generation(deadline));
        SandboxApi api = parent.nodeSandboxApi();
        return waitForOperation(deadline.main(api.getApiClient(),
                () -> api.replaceSandboxNodeCall(parent.getId(), Integer.toString(rank), request, null, null),
                GangMembershipOperation.class), deadline);
    }

    private <T> Map<Integer, T> parallel(List<SandboxNode> snapshot, java.util.function.Function<SandboxNode, T> action,
                                        Map<Integer, ExecuteResponse> partialResults) {
        if (snapshot.isEmpty()) return Collections.emptyMap();
        ExecutorService pool = Executors.newFixedThreadPool(snapshot.size(), task -> {
            Thread thread = new Thread(task, "daytona-node-operation");
            thread.setDaemon(true);
            return thread;
        });
        Map<Integer, Future<T>> futures = new TreeMap<>();
        Map<Integer, T> results = new TreeMap<>();
        Map<Integer, Throwable> failures = new TreeMap<>();
        boolean interrupted = false;
        try {
            for (SandboxNode node : snapshot) futures.put(node.getRank(), pool.submit(() -> action.apply(node)));
            for (Map.Entry<Integer, Future<T>> entry : futures.entrySet()) {
                try {
                    T result = entry.getValue().get();
                    results.put(entry.getKey(), result);
                    if (result instanceof ExecuteResponse) partialResults.put(entry.getKey(), (ExecuteResponse) result);
                } catch (ExecutionException e) {
                    failures.put(entry.getKey(), e.getCause());
                }
            }
        } catch (InterruptedException e) {
            interrupted = true;
            futures.values().forEach(future -> future.cancel(true));
            throw new DaytonaTimeoutException("Parallel sandbox node operation interrupted");
        } finally {
            pool.shutdownNow();
            // Each task owns a session and performs a bounded cleanup even after interruption.
            try {
                pool.awaitTermination(11, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                interrupted = true;
            }
            if (interrupted) Thread.currentThread().interrupt();
        }
        if (!failures.isEmpty()) throw new DaytonaNodeOperationException(failures, partialResults);
        return Collections.unmodifiableMap(results);
    }

    /** Parallel process execution over a membership snapshot. */
    public final class NodeProcess {
        public Map<Integer, ExecuteResponse> executeCommand(String command) {
            return executeCommand(command, new NodeExecuteOptions());
        }

        public Map<Integer, ExecuteResponse> executeCommand(String command, NodeExecuteOptions options) {
            if (options == null) throw new DaytonaException("Node execution options must not be null");
            long timeout = options.getTimeoutSeconds();
            new NodeDeadline(timeout); // Validate before starting any work.
            List<SandboxNode> snapshot = members;
            Object callbackLock = new Object();
            AtomicBoolean active = new AtomicBoolean(true);
            BiConsumer<Integer, String> stdout = options.getOnStdout();
            BiConsumer<Integer, String> stderr = options.getOnStderr();
            BiConsumer<Integer, String> emitOut = (rank, chunk) -> {
                synchronized (callbackLock) { if (active.get() && stdout != null) stdout.accept(rank, chunk); }
            };
            BiConsumer<Integer, String> emitErr = (rank, chunk) -> {
                synchronized (callbackLock) { if (active.get() && stderr != null) stderr.accept(rank, chunk); }
            };
            try {
                return parallel(snapshot, node -> execute(node, command, timeout,
                        stdout == null ? null : emitOut, stderr == null ? null : emitErr, snapshot.size() > 1), new TreeMap<>());
            } finally {
                synchronized (callbackLock) { active.set(false); }
            }
        }

        private ExecuteResponse execute(SandboxNode node, String command, long timeout,
                BiConsumer<Integer, String> stdout, BiConsumer<Integer, String> stderr, boolean checkRdma) {
            String warning = "";
            if (checkRdma) {
                try {
                    io.daytona.toolbox.client.model.ExecuteResponse probe = new NodeDeadline(5).toolbox(node.toolboxClient(),
                            () -> node.processApi().executeCommandCall(new io.daytona.toolbox.client.model.ExecuteRequest().command(RDMA_CHECK).timeout(5), null),
                            io.daytona.toolbox.client.model.ExecuteResponse.class);
                    if (probe.getExitCode() != 0) warning = RDMA_WARNING;
                } catch (RuntimeException e) {
                    warning = "WARNING: Could not check RDMA userspace; continuing without verification.";
                }
                if (!warning.isEmpty()) {
                    node.warn(warning);
                    if (stderr != null) stderr.accept(node.getRank(), warning + "\n");
                }
            }
            io.daytona.toolbox.client.api.ProcessApi api = node.processApi();
            io.daytona.toolbox.client.ApiClient client = node.toolboxClient();
            String session = "daytona-nodes-exec-" + UUID.randomUUID();
            RuntimeException failure = null;
            try {
                NodeDeadline setup = new NodeDeadline(10);
                setup.toolbox(client, () -> api.createSessionCall(
                        new io.daytona.toolbox.client.model.CreateSessionRequest().sessionId(session), null),
                        io.daytona.toolbox.client.model.Session.class);
                NodeDeadline execution = new NodeDeadline(timeout);
                io.daytona.toolbox.client.model.SessionExecuteResponse started = execution.toolbox(client,
                        () -> api.sessionExecuteCommandCall(session,
                                new io.daytona.toolbox.client.model.SessionExecuteRequest().command(command).runAsync(true), null),
                        io.daytona.toolbox.client.model.SessionExecuteResponse.class);
                String commandId = started.getCmdId();
                if (commandId == null || commandId.isEmpty()) throw new DaytonaException("Session execution returned no command ID");
                if (stdout != null || stderr != null) {
                    node.getProcess().getSessionCommandLogs(session, commandId,
                            chunk -> { execution.check(); if (stdout != null) stdout.accept(node.getRank(), chunk); },
                            chunk -> { execution.check(); if (stderr != null) stderr.accept(node.getRank(), chunk); },
                            execution.remainingMillis());
                }
                io.daytona.toolbox.client.model.Command completed;
                do {
                    completed = execution.toolbox(client, () -> api.getSessionCommandCall(session, commandId, null),
                            io.daytona.toolbox.client.model.Command.class);
                    if (completed.getExitCode() == null) execution.pause(100);
                } while (completed.getExitCode() == null);
                io.daytona.toolbox.client.model.SessionCommandLogsResponse logs = execution.toolbox(client,
                        () -> api.getSessionCommandLogsCall(session, commandId, null, null),
                        io.daytona.toolbox.client.model.SessionCommandLogsResponse.class);
                String out = logs.getStdout() == null ? "" : logs.getStdout();
                String resultText = logs.getOutput() == null ? out + (logs.getStderr() == null ? "" : logs.getStderr()) : logs.getOutput();
                if (!warning.isEmpty()) { out = warning + "\n" + out; resultText = warning + "\n" + resultText; }
                ExecuteResponse result = new ExecuteResponse();
                result.setExitCode(completed.getExitCode());
                result.setResult(resultText);
                result.setArtifacts(new io.daytona.toolbox.client.model.CodeRunArtifacts());
                return result;
            } catch (RuntimeException e) {
                failure = e;
                throw e;
            } finally {
                boolean interrupted = Thread.interrupted();
                try {
                    new NodeDeadline(10).toolbox(client, () -> api.deleteSessionCall(session, null), Void.class);
                } catch (RuntimeException cleanup) {
                    if (failure != null) failure.addSuppressed(cleanup);
                    else node.warn("Session " + session + " cleanup failed: " + cleanup.getMessage());
                } finally {
                    if (interrupted) Thread.currentThread().interrupt();
                }
            }
        }
    }

    /** Upload the same bytes to each node; successful uploads are not rolled back. */
    public final class NodeFileSystem {
        public void uploadFile(byte[] content, String destination) {
            if (content == null) throw new DaytonaException("Upload content must not be null");
            parallel(members, node -> { node.getFs().uploadFile(content, destination); return null; }, new TreeMap<>());
        }
        public void uploadFile(Path source, String destination) {
            try { uploadFile(Files.readAllBytes(source), destination); }
            catch (java.io.IOException e) { throw new DaytonaException("Failed to read upload file", e); }
        }
    }
}
