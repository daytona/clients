// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.api.SandboxApi;
import io.daytona.api.client.model.CreateBuildInfo;
import io.daytona.api.client.model.CreateSandbox;
import io.daytona.api.client.model.ListSandboxesResponse;
import io.daytona.api.client.model.SandboxState;
import io.daytona.api.client.model.Url;
import io.daytona.sdk.exception.DaytonaAuthenticationException;
import io.daytona.sdk.exception.DaytonaBadRequestException;
import io.daytona.sdk.exception.DaytonaConflictException;
import io.daytona.sdk.exception.DaytonaException;
import io.daytona.sdk.exception.DaytonaForbiddenException;
import io.daytona.sdk.exception.DaytonaNotFoundException;
import io.daytona.sdk.exception.DaytonaRateLimitException;
import io.daytona.sdk.exception.DaytonaServerException;
import io.daytona.sdk.exception.DaytonaValidationException;
import io.daytona.sdk.internal.EventDispatcher;
import io.daytona.sdk.internal.EventSubscriptionManager;
import io.daytona.sdk.model.CreateSandboxFromImageParams;
import io.daytona.sdk.model.CreateSandboxFromSnapshotParams;
import io.daytona.sdk.model.ListSandboxesQuery;
import io.daytona.sdk.model.Resources;
import io.daytona.sdk.model.VolumeMount;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DaytonaTest {

    @Mock
    private SandboxApi sandboxApi;

    private Daytona daytona;

    @BeforeEach
    void setUp() {
        daytona = new Daytona(TestSupport.config());
        TestSupport.setField(daytona, "sandboxApi", sandboxApi);
    }

    @Test
    void constructorValidatesConfiguration() {
        assertThatThrownBy(() -> new Daytona((DaytonaConfig) null))
                .isInstanceOf(DaytonaException.class)
                .hasMessage("Authentication required: set DAYTONA_API_KEY environment variable or pass apiKey in DaytonaConfig");

        assertThatThrownBy(() -> new Daytona(new DaytonaConfig.Builder().apiKey("").build()))
                .isInstanceOf(DaytonaException.class)
                .hasMessage("Authentication required: set DAYTONA_API_KEY environment variable or pass apiKey in DaytonaConfig");
    }

    @Test
    void constructorConfiguresUnderlyingApiClient() {
        io.daytona.api.client.ApiClient apiClient = TestSupport.getField(daytona, "apiClient", io.daytona.api.client.ApiClient.class);

        assertThat(apiClient.getBasePath()).isEqualTo("https://example.com/api");
        assertThat(apiClient.getAuthentications()).containsKey("oauth2");
    }

    @Test
    void constructorDefaultsToEventStreaming() {
        EventDispatcher dispatcher = TestSupport.getField(daytona, "eventDispatcher", EventDispatcher.class);
        EventSubscriptionManager manager = TestSupport.getField(daytona, "subscriptionManager", EventSubscriptionManager.class);

        assertThat(dispatcher).isNotNull();
        assertThat(TestSupport.getField(manager, "dispatcher", EventDispatcher.class)).isSameAs(dispatcher);
    }

    @SuppressWarnings("deprecation")
    @Test
    void constructorSkipsEventDispatcherWhenDeprecatedPollingEnabled() {
        try (Daytona pollingDaytona = new Daytona(new DaytonaConfig.Builder()
                .apiKey("test-key")
                .apiUrl("https://example.com/api/")
                .target("eu")
                .useDeprecatedPolling(true)
                .build())) {
            EventDispatcher dispatcher = TestSupport.getField(pollingDaytona, "eventDispatcher", EventDispatcher.class);
            EventSubscriptionManager manager = TestSupport.getField(pollingDaytona, "subscriptionManager", EventSubscriptionManager.class);

            assertThat(dispatcher).isNull();
            assertThat(TestSupport.getField(manager, "dispatcher", EventDispatcher.class)).isNull();
        }
    }

    @Test
    void constructorSkipsEventDispatcherWhenEnvironmentEnablesDeprecatedPolling() throws Exception {
        Map<String, String> env = new HashMap<String, String>();
        env.put("DAYTONA_API_KEY", "env-key");
        env.put("DAYTONA_USE_DEPRECATED_POLLING", "true");

        TestSupport.withEnvironment(env, () -> {
            try (Daytona pollingDaytona = new Daytona()) {
                EventDispatcher dispatcher = TestSupport.getField(pollingDaytona, "eventDispatcher", EventDispatcher.class);
                EventSubscriptionManager manager = TestSupport.getField(pollingDaytona, "subscriptionManager", EventSubscriptionManager.class);

                assertThat(dispatcher).isNull();
                assertThat(TestSupport.getField(manager, "dispatcher", EventDispatcher.class)).isNull();
            }
        });
    }

    @SuppressWarnings("deprecation")
    @Test
    void constructorUsesExplicitConfigOverEnvironmentForDeprecatedPolling() throws Exception {
        Map<String, String> env = new HashMap<String, String>();
        env.put("DAYTONA_USE_DEPRECATED_POLLING", "true");

        TestSupport.withEnvironment(env, () -> {
            try (Daytona streamingDaytona = new Daytona(new DaytonaConfig.Builder()
                    .apiKey("test-key")
                    .apiUrl("https://example.com/api/")
                    .target("eu")
                    .useDeprecatedPolling(false)
                    .build())) {
                EventDispatcher dispatcher = TestSupport.getField(streamingDaytona, "eventDispatcher", EventDispatcher.class);
                EventSubscriptionManager manager = TestSupport.getField(streamingDaytona, "subscriptionManager", EventSubscriptionManager.class);

                assertThat(dispatcher).isNotNull();
                assertThat(TestSupport.getField(manager, "dispatcher", EventDispatcher.class)).isSameAs(dispatcher);
            }
        });
    }

    @Test
    void createUsesDefaultSnapshotParamsAndWaitsUntilStarted() {
        when(sandboxApi.createSandbox(any(), isNull())).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTING));
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));

        Sandbox sandbox = daytona.create();

        assertThat(sandbox.getId()).isEqualTo("sb-1");
        ArgumentCaptor<CreateSandbox> captor = ArgumentCaptor.forClass(CreateSandbox.class);
        org.mockito.Mockito.verify(sandboxApi).createSandbox(captor.capture(), isNull());
        assertThat(captor.getValue().getLabels()).containsEntry(Daytona.CODE_TOOLBOX_LANGUAGE_LABEL, "python");
        assertThat(captor.getValue().getTarget()).isEqualTo("eu");
    }

    @Test
    void createFromImageStringBuildsDockerfile() {
        when(sandboxApi.createSandbox(any(), isNull())).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));

        CreateSandboxFromImageParams params = new CreateSandboxFromImageParams();
        params.setImage("python:3.12-slim");
        daytona.create(params, 1);

        ArgumentCaptor<CreateSandbox> captor = ArgumentCaptor.forClass(CreateSandbox.class);
        org.mockito.Mockito.verify(sandboxApi).createSandbox(captor.capture(), isNull());
        assertThat(captor.getValue().getBuildInfo().getDockerfileContent()).isEqualTo("FROM python:3.12-slim\n");
    }

    @Test
    void createFromImageObjectAddsResources() {
        when(sandboxApi.createSandbox(any(), isNull())).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));

        CreateSandboxFromImageParams params = new CreateSandboxFromImageParams();
        params.setImage(Image.base("python:3.12").runCommands("echo hi"));
        Resources resources = new Resources();
        resources.setCpu(2);
        resources.setGpu(1);
        resources.setMemory(4);
        resources.setDisk(8);
        params.setResources(resources);
        daytona.create(params, 1);

        ArgumentCaptor<CreateSandbox> captor = ArgumentCaptor.forClass(CreateSandbox.class);
        org.mockito.Mockito.verify(sandboxApi, org.mockito.Mockito.times(1)).createSandbox(captor.capture(), isNull());
        assertThat(captor.getValue().getBuildInfo().getDockerfileContent()).contains("RUN echo hi\n");
        assertThat(captor.getValue().getCpu()).isEqualTo(2);
        assertThat(captor.getValue().getGpu()).isEqualTo(1);
        assertThat(captor.getValue().getMemory()).isEqualTo(4);
        assertThat(captor.getValue().getDisk()).isEqualTo(8);
    }

    @Test
    void createFromSnapshotCopiesAllCommonFieldsAndNormalizesLanguage() {
        when(sandboxApi.createSandbox(any(), isNull())).thenReturn(TestSupport.mainSandbox("sb-9", SandboxState.STARTED));

        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        params.setName("sandbox-name");
        params.setUser("custom-user");
        params.setLanguage("typescript");
        params.setEnvVars(Collections.singletonMap("A", "1"));
        params.setLabels(Collections.singletonMap("team", "sdk"));
        params.setPublic(true);
        params.setAutoStopInterval(7);
        params.setAutoArchiveInterval(8);
        params.setAutoDeleteInterval(9);
        params.setNetworkBlockAll(true);
        params.setSnapshot("snap-1");
        VolumeMount mount = new VolumeMount();
        mount.setVolumeId("vol-1");
        mount.setMountPath("/workspace");
        params.setVolumes(Collections.singletonList(mount));

        daytona.create(params, 1);

        ArgumentCaptor<CreateSandbox> captor = ArgumentCaptor.forClass(CreateSandbox.class);
        org.mockito.Mockito.verify(sandboxApi).createSandbox(captor.capture(), isNull());
        CreateSandbox body = captor.getValue();
        assertThat(body.getName()).isEqualTo("sandbox-name");
        assertThat(body.getUser()).isEqualTo("custom-user");
        assertThat(body.getEnv()).containsEntry("A", "1");
        assertThat(body.getLabels())
                .containsEntry("team", "sdk")
                .containsEntry(Daytona.CODE_TOOLBOX_LANGUAGE_LABEL, "typescript");
        assertThat(body.getPublic()).isTrue();
        assertThat(body.getAutoStopInterval()).isEqualTo(7);
        assertThat(body.getAutoArchiveInterval()).isEqualTo(8);
        assertThat(body.getAutoDeleteInterval()).isEqualTo(9);
        assertThat(body.getNetworkBlockAll()).isTrue();
        assertThat(body.getSnapshot()).isEqualTo("snap-1");
        assertThat(body.getVolumes()).singleElement().satisfies(volume -> {
            assertThat(volume.getVolumeId()).isEqualTo("vol-1");
            assertThat(volume.getMountPath()).isEqualTo("/workspace");
        });
    }

    @Test
    void createFromSnapshotSerializesSecretsAsSingleEntryMaps() {
        when(sandboxApi.createSandbox(any(), isNull())).thenReturn(TestSupport.mainSandbox("sb-sec", SandboxState.STARTED));

        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        Map<String, String> secrets = new java.util.LinkedHashMap<>();
        secrets.put("ANTHROPIC_API_KEY", "anthropic-prod");
        secrets.put("OPENAI_API_KEY", "openai-prod");
        params.setSecrets(secrets);

        daytona.create(params, 1);

        ArgumentCaptor<CreateSandbox> captor = ArgumentCaptor.forClass(CreateSandbox.class);
        org.mockito.Mockito.verify(sandboxApi).createSandbox(captor.capture(), isNull());
        List<Map<String, String>> serialized = captor.getValue().getSecrets();
        assertThat(serialized).containsExactly(
                Collections.singletonMap("ANTHROPIC_API_KEY", "anthropic-prod"),
                Collections.singletonMap("OPENAI_API_KEY", "openai-prod"));
    }

    @Test
    void createFromSnapshotWiresAutoPauseInterval() {
        when(sandboxApi.createSandbox(any(), isNull())).thenReturn(TestSupport.mainSandbox("sb-ap", SandboxState.STARTED));

        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        params.setAutoPauseInterval(15);

        daytona.create(params, 1);

        ArgumentCaptor<CreateSandbox> captor = ArgumentCaptor.forClass(CreateSandbox.class);
        org.mockito.Mockito.verify(sandboxApi).createSandbox(captor.capture(), isNull());
        assertThat(captor.getValue().getAutoPauseInterval()).isEqualTo(15);
        assertThat(captor.getValue().getAutoStopInterval()).isNull();
    }

    @Test
    void createRejectsNegativeAutoPauseInterval() {
        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        params.setAutoPauseInterval(-1);

        assertThatThrownBy(() -> daytona.create(params, 1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("autoPauseInterval must be a non-negative integer");
    }

    @Test
    void createRejectsEphemeralWithAutoPause() {
        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        params.setAutoDeleteInterval(0);
        params.setAutoPauseInterval(60);

        assertThatThrownBy(() -> daytona.create(params, 1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Ephemeral sandboxes cannot have auto-pause enabled");
    }

    @Test
    void createRejectsMutuallyExclusiveAutoStopAndAutoPause() {
        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        params.setAutoStopInterval(10);
        params.setAutoPauseInterval(20);

        assertThatThrownBy(() -> daytona.create(params, 1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("autoStopInterval and autoPauseInterval are mutually exclusive");
    }

    @Test
    void createAllowsAutoStopAndAutoPauseWhenOneIsZero() {
        when(sandboxApi.createSandbox(any(), isNull())).thenReturn(TestSupport.mainSandbox("sb-z", SandboxState.STARTED));

        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        params.setAutoStopInterval(10);
        params.setAutoPauseInterval(0);

        daytona.create(params, 1);

        ArgumentCaptor<CreateSandbox> captor = ArgumentCaptor.forClass(CreateSandbox.class);
        org.mockito.Mockito.verify(sandboxApi).createSandbox(captor.capture(), isNull());
        assertThat(captor.getValue().getAutoStopInterval()).isEqualTo(10);
        assertThat(captor.getValue().getAutoPauseInterval()).isEqualTo(0);
    }

    @Test
    void createFromSnapshotRejectsUnsupportedLanguage() {
        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        params.setLanguage("ruby");

        assertThatThrownBy(() -> daytona.create(params, 1))
                .isInstanceOf(DaytonaException.class)
                .hasMessageContaining("Invalid code-toolbox-language: ruby");
    }

    @Test
    void createFromSnapshotRejectsNegativeTtlMinutes() {
        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        params.setTtlMinutes(-1);

        assertThatThrownBy(() -> daytona.create(params, 1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("ttlMinutes must be a non-negative integer");
    }

    @Test
    void createFromSnapshotWiresTtlMinutesToBody() {
        when(sandboxApi.createSandbox(any(), isNull())).thenReturn(TestSupport.mainSandbox("sb-ttl", SandboxState.STARTED));

        CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
        params.setTtlMinutes(30);

        daytona.create(params, 1);

        ArgumentCaptor<CreateSandbox> captor = ArgumentCaptor.forClass(CreateSandbox.class);
        org.mockito.Mockito.verify(sandboxApi).createSandbox(captor.capture(), isNull());
        assertThat(captor.getValue().getTtlMinutes()).isEqualTo(30);
    }

    @Test
    void createFromImageWithoutImageLeavesBuildInfoUnset() {
        when(sandboxApi.createSandbox(any(), isNull())).thenReturn(TestSupport.mainSandbox("sb-10", SandboxState.STARTED));
        when(sandboxApi.getSandbox("sb-10", null, null)).thenReturn(TestSupport.mainSandbox("sb-10", SandboxState.STARTED));

        CreateSandboxFromImageParams params = new CreateSandboxFromImageParams();
        params.setImage("");

        daytona.create(params, 1);

        ArgumentCaptor<CreateSandbox> captor = ArgumentCaptor.forClass(CreateSandbox.class);
        org.mockito.Mockito.verify(sandboxApi).createSandbox(captor.capture(), isNull());
        assertThat(captor.getValue().getBuildInfo()).isNull();
        assertThat(captor.getValue().getLabels()).containsEntry(Daytona.CODE_TOOLBOX_LANGUAGE_LABEL, "python");
    }

    @Test
    void createFromImageStreamsBuildLogsForPendingBuildSandboxes() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().setBody("build-line-1\nbuild-line-2\n"));
            io.daytona.api.client.model.Sandbox created = TestSupport.mainSandbox("sb-build", SandboxState.PENDING_BUILD);
            io.daytona.api.client.model.Sandbox starting = TestSupport.mainSandbox("sb-build", SandboxState.STARTING);
            io.daytona.api.client.model.Sandbox started = TestSupport.mainSandbox("sb-build", SandboxState.STARTED);
            when(sandboxApi.createSandbox(any(), isNull())).thenReturn(created);
            when(sandboxApi.getSandbox("sb-build", null, null)).thenReturn(starting, started, started);
            when(sandboxApi.getBuildLogsUrl("sb-build", null)).thenReturn(new Url().url(server.url("/logs").toString()));

            List<String> lines = new ArrayList<String>();
            Sandbox sandbox = daytona.create(new CreateSandboxFromImageParams(), 2, lines::add);

            assertThat(sandbox.getId()).isEqualTo("sb-build");
            assertThat(lines).contains("build-line-1");
            assertThat(server.takeRequest().getPath()).isEqualTo("/logs?follow=true");
        }
    }

    @Test
    void getWrapsSandboxModel() {
        when(sandboxApi.getSandbox("sandbox-1", null, null)).thenReturn(TestSupport.mainSandbox("sandbox-1", SandboxState.STARTED));

        Sandbox sandbox = daytona.get("sandbox-1");

        assertThat(sandbox.getId()).isEqualTo("sandbox-1");
        assertThat(sandbox.getState()).isEqualTo("started");
    }

    @Test
    void listIteratorYieldsSandboxesAndForwardsLabelFilter() {
        ListSandboxesResponse response = new ListSandboxesResponse();
        response.setItems(Collections.singletonList(TestSupport.mainSandboxListItem("sb-1", SandboxState.STARTED)));
        response.setNextCursor(null);
        doReturn(response).when(sandboxApi).listSandboxes(
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any());

        ListSandboxesQuery query = new ListSandboxesQuery();
        query.setLabels(Collections.singletonMap("team", "sdk"));
        query.setLimit(5);

        List<Sandbox> collected = new ArrayList<>();
        for (Sandbox sandbox : daytona.list(query)) {
            collected.add(sandbox);
        }

        assertThat(collected).singleElement().satisfies(sandbox -> assertThat(sandbox.getId()).isEqualTo("sb-1"));

        org.mockito.Mockito.verify(sandboxApi).listSandboxes(
                isNull(),                                  // org header
                isNull(),                                  // cursor (first page)
                eq(BigDecimal.valueOf(5)),                 // limit
                isNull(),                                  // id
                isNull(),                                  // name
                eq("{\"team\":\"sdk\"}"),                  // labels JSON
                isNull(),                                  // includeErroredDeleted
                isNull(), isNull(), isNull(), isNull(),    // states, snapshots, regionIds, sandboxClasses
                isNull(), isNull(),                        // minCpu, maxCpu
                isNull(), isNull(),                        // minMemoryGiB, maxMemoryGiB
                isNull(), isNull(),                        // minDiskGiB, maxDiskGiB
                isNull(), isNull(),                        // isPublic, isRecoverable
                isNull(), isNull(),                        // createdAtAfter, createdAtBefore
                isNull(), isNull(),                        // lastEventAfter, lastEventBefore
                isNull(), isNull());                       // sort, order
    }

    @Test
    void listWithNoQueryUsesAllNullFilters() {
        ListSandboxesResponse response = new ListSandboxesResponse();
        response.setItems(Collections.<io.daytona.api.client.model.SandboxListItem>emptyList());
        response.setNextCursor(null);
        doReturn(response).when(sandboxApi).listSandboxes(
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any());

        Iterator<Sandbox> iter = daytona.list().iterator();
        // Drive the iterator so the API call actually happens.
        assertThat(iter.hasNext()).isFalse();

        org.mockito.Mockito.verify(sandboxApi).listSandboxes(
                isNull(), isNull(), isNull(), isNull(), isNull(), isNull(), isNull(),
                isNull(), isNull(), isNull(), isNull(), isNull(), isNull(), isNull(),
                isNull(), isNull(), isNull(), isNull(), isNull(), isNull(),
                isNull(), isNull(), isNull(), isNull(), isNull());
    }

    @Test
    void listReturnsEmptyIteratorWhenApiReturnsNullItems() {
        ListSandboxesResponse response = new ListSandboxesResponse();
        // items is null on the wire — SDK must treat it as empty.
        response.setItems(null);
        response.setNextCursor(null);
        doReturn(response).when(sandboxApi).listSandboxes(
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any());

        Iterator<Sandbox> iter = daytona.list(new ListSandboxesQuery()).iterator();

        assertThat(iter.hasNext()).isFalse();
    }

    @Test
    void listPaginatesAcrossMultiplePages() {
        ListSandboxesResponse page1 = new ListSandboxesResponse();
        page1.setItems(java.util.Arrays.asList(
                TestSupport.mainSandboxListItem("sb-1", SandboxState.STARTED),
                TestSupport.mainSandboxListItem("sb-2", SandboxState.STARTED)
        ));
        page1.setNextCursor("cursor-2");

        ListSandboxesResponse page2 = new ListSandboxesResponse();
        page2.setItems(Collections.singletonList(TestSupport.mainSandboxListItem("sb-3", SandboxState.STARTED)));
        page2.setNextCursor(null);

        doReturn(page1, page2).when(sandboxApi).listSandboxes(
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any());

        List<String> ids = new ArrayList<>();
        for (Sandbox sandbox : daytona.list()) {
            ids.add(sandbox.getId());
        }

        assertThat(ids).containsExactly("sb-1", "sb-2", "sb-3");
        org.mockito.Mockito.verify(sandboxApi, org.mockito.Mockito.times(2)).listSandboxes(
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any());
    }

    @ParameterizedTest
    @MethodSource("mappedApiExceptions")
    void getMapsApiErrors(int status, Class<? extends RuntimeException> type) {
        when(sandboxApi.getSandbox(anyString(), isNull(), isNull()))
                .thenThrow(new io.daytona.api.client.ApiException(status, "boom", null, "{\"message\":\"mapped\"}"));

        assertThatThrownBy(() -> daytona.get("sandbox-1"))
                .isInstanceOf(type)
                .hasMessage("mapped");
    }

    @Test
    void closeHandlesNullHttpClientCacheAndUtilityHelpers() {
        assertThat(Daytona.urlEncodePathSegment("a b/c")).isEqualTo("a+b%2Fc".replace("+", "%20"));
        assertThat(Daytona.urlEncodeQuery("a b")).isEqualTo("a+b");
        assertThat(Daytona.castStringMap(Collections.singletonMap(1, null))).containsEntry("1", "");
        Daytona.shutdownHttpClient(null);

        io.daytona.api.client.ApiClient apiClient = TestSupport.getField(daytona, "apiClient", io.daytona.api.client.ApiClient.class);
        daytona.close();
        assertThat(apiClient.getHttpClient().dispatcher().executorService().isShutdown()).isTrue();
    }

    @Test
    void closeShutsDownHttpClient() {
        io.daytona.api.client.ApiClient apiClient = TestSupport.getField(daytona, "apiClient", io.daytona.api.client.ApiClient.class);

        daytona.close();

        assertThat(apiClient.getHttpClient().dispatcher().executorService().isShutdown()).isTrue();
    }

    private static Stream<Arguments> mappedApiExceptions() {
        return Stream.of(
                Arguments.of(400, DaytonaBadRequestException.class),
                Arguments.of(401, DaytonaAuthenticationException.class),
                Arguments.of(403, DaytonaForbiddenException.class),
                Arguments.of(404, DaytonaNotFoundException.class),
                Arguments.of(409, DaytonaConflictException.class),
                Arguments.of(422, DaytonaValidationException.class),
                Arguments.of(429, DaytonaRateLimitException.class),
                Arguments.of(500, DaytonaServerException.class)
        );
    }
}
