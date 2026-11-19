// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.ApiClient;
import io.daytona.api.client.api.SandboxApi;
import io.daytona.api.client.model.SandboxState;
import io.daytona.sdk.exception.DaytonaException;
import io.daytona.sdk.exception.DaytonaTimeoutException;
import io.daytona.sdk.exception.DaytonaNodeOperationException;
import io.daytona.sdk.internal.EventSubscriptionManager;
import io.daytona.sdk.model.NodeExecuteOptions;
import io.daytona.sdk.model.NodeResources;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import okhttp3.mockwebserver.Dispatcher;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class SandboxNodesTest {
    private io.daytona.api.client.model.Sandbox dto(String proxy, int count, int generation) {
        io.daytona.api.client.model.Sandbox data = TestSupport.mainSandbox("head", SandboxState.STARTED);
        data.setToolboxProxyUrl(proxy);
        data.setNodeCount(count);
        data.setTopologyGeneration(generation);
        data.setGpu(count * 8);
        List<io.daytona.api.client.model.SandboxNode> nodes = new ArrayList<>();
        for (int rank = 0; rank < count; rank++) {
            nodes.add(new io.daytona.api.client.model.SandboxNode().id(rank == 0 ? "head" : "worker-" + rank)
                    .rank(rank).incarnation(1).cpu(8).gpu(8).memory(64).disk(100).state(SandboxState.STARTED));
        }
        data.setNodes(nodes);
        return data;
    }

    private Sandbox sandbox(SandboxApi api, io.daytona.api.client.model.Sandbox data) {
        return new Sandbox(api, TestSupport.config(), data, () -> null, mock(EventSubscriptionManager.class));
    }

    @Test void sparseGangWaitsForMemberDetails() {
        SandboxApi api = mock(SandboxApi.class);
        io.daytona.api.client.model.Sandbox sparse = dto("http://localhost:1/toolbox", 2, 1);
        sparse.setNodes(null);
        Sandbox sandbox = sandbox(api, sparse);
        SandboxNodes nodes = sandbox.getNodes();
        assertThat(sandbox.getNodeCount()).isEqualTo(2);
        assertThat(nodes).isEmpty();
        assertThatThrownBy(() -> nodes.getProcess().executeCommand("hostname")).isInstanceOf(DaytonaException.class)
                .hasMessageContaining("membership is unavailable");
        assertThatThrownBy(() -> nodes.getFs().uploadFile(new byte[]{1}, "/file")).isInstanceOf(DaytonaException.class)
                .hasMessageContaining("membership is unavailable");

        when(api.getSandbox("head", null, null)).thenReturn(dto("http://localhost:1/toolbox", 2, 1));
        sandbox.refreshData();
        assertThat(nodes).extracting(SandboxNode::getId).containsExactly("head", "worker-1");
    }

    @Test void refreshUpdatesSingleHeadAndKeepsItsResolvedProxy() {
        SandboxApi api = mock(SandboxApi.class);
        io.daytona.api.client.model.Sandbox first = dto("http://localhost:1/toolbox", 1, 1);
        first.setNodes(null);
        first.setCpu(2);
        first.setState(SandboxState.STOPPED);
        Sandbox sandbox = sandbox(api, first);
        SandboxNode head = sandbox.getNodes().get(0);
        io.daytona.api.client.model.Sandbox next = dto("", 1, 1);
        next.setNodes(null);
        next.setCpu(4);
        when(api.getSandbox("head", null, null)).thenReturn(next);

        sandbox.refreshData();

        assertThat(sandbox.getNodes().get(0)).isSameAs(head);
        assertThat(head.getCpu()).isEqualTo(4);
        assertThat(head.getState()).isEqualTo("started");
        assertThat(head.toolboxClient().getBasePath()).isEqualTo("http://localhost:1/toolbox/head");
    }

    @Test void retainsNodeAndServiceIdentityWhenProxyChangesWithoutNodes() {
        Sandbox sandbox = sandbox(mock(SandboxApi.class), dto("http://localhost:1/toolbox", 2, 4));
        SandboxNode worker = sandbox.getNodes().get(1);
        Process process = worker.getProcess();
        io.daytona.api.client.model.Sandbox changed = dto("http://localhost:2/new", 2, 4);
        changed.setNodes(null);
        sandbox.applyNodeResponse(changed);
        assertThat(sandbox.getNodes().get(1)).isSameAs(worker);
        assertThat(worker.getProcess()).isSameAs(process);
        assertThat(worker.toolboxClient().getBasePath()).isEqualTo("http://localhost:2/new/worker-1");
    }

    @Test void rejectsMalformedIdentityBeforeUpdatingExistingMembers() {
        Sandbox sandbox = sandbox(mock(SandboxApi.class), dto("http://localhost:1/toolbox", 2, 4));
        SandboxNode worker = sandbox.getNodes().get(1);
        io.daytona.api.client.model.Sandbox bad = dto("http://localhost:1/toolbox", 2, 4);
        bad.getNodes().get(1).setId("..");
        assertThatThrownBy(() -> sandbox.applyNodeResponse(bad)).isInstanceOf(DaytonaException.class);
        assertThat(sandbox.getNodes().get(1)).isSameAs(worker);
    }

    @Test void membershipUsesCachedGenerationAndUpdatesRetainedHandles() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.start();
            String proxy = server.url("/toolbox").toString();
            ApiClient client = mainClient(server.url("/").toString());
            SandboxApi api = new SandboxApi(client);
            Sandbox sandbox = sandbox(api, dto(proxy, 2, 1));
            SandboxNode worker = sandbox.getNodes().get(1);
            server.enqueue(new MockResponse().setHeader("Content-Type", "application/json").setBody("{\"id\":\"op\",\"sandboxId\":\"head\",\"type\":\"ADD\",\"status\":\"COMPLETED\",\"phase\":\"COMPLETED\",\"expectedGeneration\":1,\"targetGeneration\":2,\"policyRevision\":1,\"createdAt\":\"2026-01-01T00:00:00Z\",\"updatedAt\":\"2026-01-01T00:00:00Z\"}"));
            server.enqueue(new MockResponse().setHeader("Content-Type", "application/json").setBody(dto(proxy, 3, 2).toJson()));
            sandbox.getNodes().add(Collections.singletonList(new NodeResources(8, 64, 100)), 3);
            RecordedRequest post = server.takeRequest();
            assertThat(post.getMethod()).isEqualTo("POST");
            assertThat(post.getBody().readUtf8()).contains("\"expectedGeneration\":1");
            assertThat(sandbox.getNodes()).hasSize(3);
            assertThat(sandbox.getTopologyGeneration()).isEqualTo(2);
            assertThat(sandbox.getNodes().get(1)).isSameAs(worker);
        }
    }

    @Test void acceptedReleaseSurvivesAReadFailure() throws Exception {
        for (String status : Arrays.asList("COMPLETED", "RUNNING")) {
            try (MockWebServer server = new MockWebServer()) {
                String proxy = server.url("/toolbox").toString();
                Sandbox sandbox = sandbox(new SandboxApi(mainClient(server.url("/").toString())), dto(proxy, 4, 4));
                server.enqueue(json("{\"id\":\"op-1\",\"sandboxId\":\"head\",\"type\":\"REMOVE\",\"status\":\"" + status
                        + "\",\"phase\":\"FENCE\",\"expectedGeneration\":4,\"targetGeneration\":5,\"policyRevision\":1,"
                        + "\"createdAt\":\"2026-01-01T00:00:00Z\",\"updatedAt\":\"2026-01-01T00:00:00Z\"}"));
                server.enqueue(json("{}").setResponseCode(503));
                if ("COMPLETED".equals(status)) {
                    assertThat(sandbox.getNodes().release(1, 5).getId()).isEqualTo("op-1");
                } else {
                    assertThatThrownBy(() -> sandbox.getNodes().release(1, 5))
                            .isInstanceOfSatisfying(DaytonaException.class, e -> assertThat(e.getOperationId()).isEqualTo("op-1"));
                }
            }
        }
    }

    @Test void membershipTimeoutIncludesSubmission() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.start();
            server.enqueue(new MockResponse().setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.NO_RESPONSE));
            Sandbox sandbox = sandbox(new SandboxApi(mainClient(server.url("/").toString())),
                    dto(server.url("/toolbox").toString(), 2, 1));
            assertThatThrownBy(() -> sandbox.getNodes().release(1, 1)).isInstanceOf(DaytonaTimeoutException.class);
            assertThat(server.getRequestCount()).isEqualTo(1);
        }
    }

    @Test void staleReleaseReturnsConflictWithoutRefreshing() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            Sandbox sandbox = sandbox(new SandboxApi(mainClient(server.url("/").toString())),
                    dto(server.url("/toolbox").toString(), 3, 1));
            server.enqueue(json("{\"message\":\"Topology changed\"}").setResponseCode(409));
            assertThatThrownBy(() -> sandbox.getNodes().release(1, 5))
                    .isInstanceOf(io.daytona.sdk.exception.DaytonaConflictException.class);
            RecordedRequest request = server.takeRequest();
            assertThat(request.getMethod()).isEqualTo("POST");
            assertThat(request.getBody().readUtf8()).contains("\"expectedGeneration\":1");
            assertThat(server.getRequestCount()).isEqualTo(1);
        }
    }

    @Test void acceptedReleaseTimeoutRetainsOperationId() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            Sandbox sandbox = sandbox(new SandboxApi(mainClient(server.url("/").toString())),
                    dto(server.url("/toolbox").toString(), 3, 1));
            server.enqueue(json("{\"id\":\"op-1\",\"sandboxId\":\"head\",\"type\":\"REMOVE\",\"status\":\"RUNNING\",\"phase\":\"FENCE\",\"expectedGeneration\":1,\"targetGeneration\":2,\"policyRevision\":1,\"createdAt\":\"2026-01-01T00:00:00Z\",\"updatedAt\":\"2026-01-01T00:00:00Z\"}"));
            assertThatThrownBy(() -> sandbox.getNodes().release(1, 1))
                    .isInstanceOfSatisfying(DaytonaTimeoutException.class, e -> assertThat(e.getOperationId()).isEqualTo("op-1"));
        }
    }

    @Test void executionTimeoutCleansEveryPrivateSessionIncludingIdleWebsocket() throws Exception {
        for (boolean stream : Arrays.asList(false, true)) {
            try (MockWebServer server = new MockWebServer()) {
                AtomicInteger deletes = new AtomicInteger();
                server.setDispatcher(new Dispatcher() {
                    @Override public MockResponse dispatch(RecordedRequest request) {
                        if ("DELETE".equals(request.getMethod())) { deletes.incrementAndGet(); return json("{}"); }
                        String path = request.getPath();
                        if (path.endsWith("/execute")) return json("{\"exitCode\":0,\"result\":\"\"}");
                        if (path.contains("/logs?follow=true")) return new MockResponse().withWebSocketUpgrade(new okhttp3.WebSocketListener() {});
                        if (path.contains("/exec")) return json("{\"cmdId\":\"cmd\"}");
                        if (path.contains("/command/")) return json("{\"id\":\"cmd\",\"command\":\"sleep 100\"}");
                        return json("{\"sessionId\":\"session\",\"commands\":[]}");
                    }
                });
                server.start();
                Sandbox sandbox = sandbox(mock(SandboxApi.class), dto(server.url("/toolbox").toString(), 2, 1));
                NodeExecuteOptions options = new NodeExecuteOptions();
                options.setTimeoutSeconds(1);
                if (stream) options.setOnStdout((rank, chunk) -> fail("Idle stream produced output"));
                assertThatThrownBy(() -> sandbox.getNodes().getProcess().executeCommand("sleep 100", options))
                        .isInstanceOfSatisfying(DaytonaNodeOperationException.class,
                                error -> assertThat(error.getFailures()).containsKeys(0, 1));
                assertThat(deletes).hasValue(2);
            }
        }
    }

    @Test void interruptedRdmaProbeIsRetriedForTheSameIncarnation() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            SandboxNode worker = sandbox(mock(SandboxApi.class), dto(server.url("/toolbox").toString(), 2, 1)).getNodes().get(1);
            try {
                Thread.currentThread().interrupt();
                worker.checkRdma();
            } finally {
                Thread.interrupted();
            }
            assertThat(worker.getWarnings()).isEmpty();
            server.enqueue(json("{\"exitCode\":0,\"result\":\"\"}"));
            worker.checkRdma();
            assertThat(server.getRequestCount()).isEqualTo(1);
            assertThat(worker.getWarnings()).isEmpty();
        }
    }

    @Test void cleanupFailurePreservesSuccessfulCommandsAndAddsWarnings() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            AtomicInteger probes = new AtomicInteger();
            java.util.concurrent.atomic.AtomicBoolean failWorker = new java.util.concurrent.atomic.AtomicBoolean();
            server.setDispatcher(new Dispatcher() {
                @Override public MockResponse dispatch(RecordedRequest request) {
                    String path = request.getPath();
                    if ("DELETE".equals(request.getMethod())) return json("{}").setResponseCode(500);
                    if (path.endsWith("/execute")) { probes.incrementAndGet(); return json("{\"exitCode\":0,\"result\":\"\"}"); }
                    if (failWorker.get() && path.contains("/worker-1/") && path.endsWith("/exec")) return json("{}").setResponseCode(500);
                    if (path.contains("/exec")) return json("{\"cmdId\":\"cmd\"}");
                    if (path.endsWith("/logs")) return json("{\"output\":\"ok\",\"stdout\":\"ok\",\"stderr\":\"\"}");
                    if (path.contains("/command/")) return json("{\"id\":\"cmd\",\"command\":\"echo ok\",\"exitCode\":0}");
                    return json("{\"sessionId\":\"session\",\"commands\":[]}");
                }
            });
            server.start();
            Sandbox sandbox = sandbox(mock(SandboxApi.class), dto(server.url("/toolbox").toString(), 2, 1));
            assertThat(sandbox.getNodes().getProcess().executeCommand("echo ok")).hasSize(2);
            failWorker.set(true);
            assertThatThrownBy(() -> sandbox.getNodes().getProcess().executeCommand("echo ok"))
                    .isInstanceOfSatisfying(DaytonaNodeOperationException.class, error -> {
                        assertThat(error.getFailures()).containsOnlyKeys(1);
                        assertThat(error.getResults()).containsOnlyKeys(0);
                    });
            assertThat(probes).hasValue(2);
            for (SandboxNode node : sandbox.getNodes()) assertThat(node.getWarnings()).anyMatch(w -> w.contains("cleanup failed"));
        }
    }

    private static ApiClient mainClient(String base) throws Exception {
        ApiClient client = new ApiClient().setBasePath(base);
        java.lang.reflect.Field field = ApiClient.class.getDeclaredField("authentications");
        field.setAccessible(true);
        java.util.Map<String, io.daytona.api.client.auth.Authentication> auths = new java.util.HashMap<>(client.getAuthentications());
        auths.put("oauth2", (q, h, c, p, m, u) -> {});
        field.set(client, Collections.unmodifiableMap(auths));
        return client;
    }

    @Test void interruptAbortsInflightRequestsAndCleansEverySession() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            java.util.concurrent.CountDownLatch blocked = new java.util.concurrent.CountDownLatch(2);
            AtomicInteger deletes = new AtomicInteger();
            server.setDispatcher(new Dispatcher() {
                @Override public MockResponse dispatch(RecordedRequest request) {
                    String path = request.getPath();
                    if ("DELETE".equals(request.getMethod())) { deletes.incrementAndGet(); return json("{}"); }
                    if (path.endsWith("/execute")) return json("{\"exitCode\":0,\"result\":\"\"}");
                    if (path.endsWith("/exec")) return json("{\"cmdId\":\"cmd\"}");
                    if (path.contains("/command/")) {
                        blocked.countDown();
                        return new MockResponse().setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.NO_RESPONSE);
                    }
                    return json("{\"sessionId\":\"session\",\"commands\":[]}");
                }
            });
            server.start();
            Sandbox sandbox = sandbox(mock(SandboxApi.class), dto(server.url("/toolbox").toString(), 2, 1));
            java.util.concurrent.atomic.AtomicReference<Throwable> failure = new java.util.concurrent.atomic.AtomicReference<>();
            Thread caller = new Thread(() -> {
                try { sandbox.getNodes().getProcess().executeCommand("sleep 100"); }
                catch (Throwable e) { failure.set(e); }
            });
            caller.start();
            try {
                assertThat(blocked.await(3, java.util.concurrent.TimeUnit.SECONDS)).isTrue();
                caller.interrupt();
                caller.join(3000);
                assertThat(caller.isAlive()).isFalse();
                assertThat(failure.get()).isInstanceOf(DaytonaTimeoutException.class);
                assertThat(deletes).hasValue(2);
            } finally {
                caller.interrupt();
                caller.join(3000);
            }
        }
    }

    @Test void previewAndSshUseWorkerId() {
        SandboxApi api = mock(SandboxApi.class);
        SandboxNode worker = sandbox(api, dto("http://localhost:1/toolbox", 2, 1)).getNodes().get(1);
        worker.getPreviewLink(8080);
        worker.createSshAccess(60);
        worker.revokeSshAccess("token");
        verify(api).getPortPreviewUrl("worker-1", java.math.BigDecimal.valueOf(8080), null);
        verify(api).createSshAccess("worker-1", null, java.math.BigDecimal.valueOf(60));
        verify(api).revokeSshAccess("worker-1", null, "token");
    }

    @Test void directWorkerRetainsPhysicalRank() {
        io.daytona.api.client.model.Sandbox data = dto("http://localhost:1/toolbox", 2, 4);
        data.setId("worker-1");
        data.setHeadSandboxId("head");
        data.setNodeRank(1);
        data.setNodes(Collections.singletonList(data.getNodes().get(1)));
        data.getNodes().get(0).incarnation(3).fabricHealthy(true);
        SandboxApi api = mock(SandboxApi.class);
        Sandbox sandbox = sandbox(api, data);
        SandboxNode worker = sandbox.getNodes().get(0);

        data.setNodes(null);
        data.setCpu(12);
        data.setState(SandboxState.STOPPED);
        when(api.getSandbox("worker-1", null, null)).thenReturn(data);
        sandbox.refreshData();

        assertThat(sandbox.getNodes().get(0)).isSameAs(worker);
        assertThat(worker.getRank()).isEqualTo(1);
        assertThat(worker.getId()).isEqualTo("worker-1");
        assertThat(worker.getCpu()).isEqualTo(12);
        assertThat(worker.getState()).isEqualTo("stopped");
        assertThat(worker.getIncarnation()).isEqualTo(3);
        assertThat(worker.getFabricHealthy()).isTrue();

        data.setIncarnation(4);
        sandbox.refreshData();
        assertThat(worker.getIncarnation()).isEqualTo(4);
        assertThat(worker.getFabricHealthy()).isNull();
    }

    @Test void pathUploadStreamsToEachRank(@org.junit.jupiter.api.io.TempDir java.nio.file.Path temp) throws Exception {
        java.nio.file.Path source = java.nio.file.Files.writeString(temp.resolve("sample"), "same input");
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(json("{}"));
            server.enqueue(json("{}"));
            Sandbox sandbox = sandbox(mock(SandboxApi.class), dto(server.url("/toolbox").toString(), 2, 1));
            sandbox.getNodes().getFs().uploadFile(source, "/sample");
            for (int i = 0; i < 2; i++) {
                RecordedRequest request = server.takeRequest();
                assertThat(request.getPath()).endsWith("/files/bulk-upload");
                assertThat(request.getHeader("Transfer-Encoding")).isEqualTo("chunked");
                assertThat(request.getBody().readUtf8()).contains("same input", "/sample");
            }
        }
    }

    @Test void uploadCollectsEveryFailedRank() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.setDispatcher(new Dispatcher() {
                @Override public MockResponse dispatch(RecordedRequest request) { return json("{}").setResponseCode(403); }
            });
            server.start();
            Sandbox sandbox = sandbox(mock(SandboxApi.class), dto(server.url("/toolbox").toString(), 2, 1));
            assertThatThrownBy(() -> sandbox.getNodes().getFs().uploadFile(new byte[]{1, 2}, "/tmp/file"))
                    .isInstanceOfSatisfying(DaytonaNodeOperationException.class,
                            e -> assertThat(e.getFailures()).containsKeys(0, 1));
        }
    }

    @Test void streamingSuccessTagsRanksAndKeepsAdvisoryWarnings() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            AtomicInteger deletes = new AtomicInteger();
            server.setDispatcher(new Dispatcher() {
                @Override public MockResponse dispatch(RecordedRequest request) {
                    String path = request.getPath();
                    if ("DELETE".equals(request.getMethod())) { deletes.incrementAndGet(); return json("{}"); }
                    if (path.endsWith("/execute")) return json("{\"exitCode\":1,\"result\":\"missing RDMA\"}");
                    if (path.contains("/logs?follow=true")) {
                        return new MockResponse().withWebSocketUpgrade(new okhttp3.WebSocketListener() {
                            @Override public void onOpen(okhttp3.WebSocket socket, okhttp3.Response response) {
                                socket.send(okio.ByteString.of((byte) 1, (byte) 1, (byte) 1, (byte) 'o', (byte) 'k'));
                                socket.close(1000, "done");
                            }
                        });
                    }
                    if (path.endsWith("/exec")) return json("{\"cmdId\":\"cmd\"}");
                    if (path.endsWith("/logs")) return json("{\"output\":\"ok\",\"stdout\":\"ok\",\"stderr\":\"\"}");
                    if (path.contains("/command/")) return json("{\"id\":\"cmd\",\"command\":\"echo ok\",\"exitCode\":0}");
                    return json("{\"sessionId\":\"session\",\"commands\":[]}");
                }
            });
            server.start();
            Sandbox sandbox = sandbox(mock(SandboxApi.class), dto(server.url("/toolbox").toString(), 2, 1));
            List<String> chunks = new ArrayList<>();
            NodeExecuteOptions options = new NodeExecuteOptions();
            options.setTimeoutSeconds(3);
            options.setOnStdout((rank, chunk) -> chunks.add(rank + ":" + chunk));
            java.util.Map<Integer, io.daytona.sdk.model.ExecuteResponse> results =
                    sandbox.getNodes().getProcess().executeCommand("echo ok", options);
            assertThat(chunks).containsExactlyInAnyOrder("0:ok", "1:ok");
            assertThat(deletes).hasValue(2);
            assertThat(results.values()).allSatisfy(result -> assertThat(result.getResult()).isEqualTo("ok"));
            for (SandboxNode node : sandbox.getNodes()) assertThat(node.getWarnings()).anyMatch(w -> w.contains("WARNING:"));
        }
    }

    private static MockResponse json(String body) {
        return new MockResponse().setHeader("Content-Type", "application/json").setBody(body);
    }
}
