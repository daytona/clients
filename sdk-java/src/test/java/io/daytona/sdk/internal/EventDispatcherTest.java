// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.internal;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.HttpUrl;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.BiConsumer;

import static org.assertj.core.api.Assertions.assertThat;

class EventDispatcherTest {

    private static final List<String> STATE_EVENTS = List.of("sandbox.state.updated");
    private static final long WAIT_MILLIS = 5000;
    private static final long QUIET_MILLIS = 400;
    private static final BiConsumer<String, JsonNode> NOOP = (event, data) -> { };

    private FakeEventGateway gateway;
    private EventDispatcher dispatcher;

    @BeforeEach
    void setUp() throws Exception {
        gateway = new FakeEventGateway();
        dispatcher = new EventDispatcher(gateway.apiUrl(), "test-token", null, "sdk-java", "1.2.3");
    }

    @AfterEach
    void tearDown() throws Exception {
        dispatcher.shutdown();
        gateway.close();
    }

    @Test
    void constructionOpensNoConnection() throws Exception {
        Thread.sleep(QUIET_MILLIS);

        assertThat(gateway.requestCount()).isZero();
        assertThat(dispatcher.isConnected()).isFalse();
    }

    @Test
    void firstSubscribeConnectsInExplicitModeAndSubscribesTheId() throws Exception {
        dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);

        RecordedRequest handshake = gateway.takeHandshake(WAIT_MILLIS);
        assertThat(handshake).isNotNull();
        HttpUrl url = handshake.getRequestUrl();
        assertThat(url.encodedPath()).isEqualTo("/api/socket.io/");
        assertThat(url.queryParameter("subscriptionMode")).isEqualTo("explicit");
        assertThat(url.queryParameter("source")).isEqualTo("sdk-java");
        assertThat(url.queryParameter("sdkVersion")).isEqualTo("1.2.3");

        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
    }

    @Test
    void sendsSubscribeOnFirstListenerAndUnsubscribeOnLastWhileConnected() throws Exception {
        Runnable first = dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        assertThat(dispatcher.isConnected()).isTrue();

        Runnable second = dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        Runnable other = dispatcher.subscribe("sb-2", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-2"));
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();

        first.run();
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();

        second.run();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "unsubscribe", Set.of("sb-1"));

        other.run();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "unsubscribe", Set.of("sb-2"));
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
    }

    @Test
    void whileDisconnectedOnlyTracksIdsThenSendsTheWholeSetInChunksOnConnect() throws Exception {
        gateway.holdConnects();

        Set<String> expected = new HashSet<>();
        Runnable removed = null;
        for (int i = 0; i < 250; i++) {
            String id = "sb-" + i;
            Runnable unsubscribe = dispatcher.subscribe(id, NOOP, STATE_EVENTS);
            if (i == 7) {
                removed = unsubscribe;
            } else {
                expected.add(id);
            }
        }
        removed.run();

        assertThat(gateway.takeHandshake(WAIT_MILLIS)).isNotNull();
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
        gateway.resumeAccepting();

        List<Integer> chunkSizes = new ArrayList<>();
        List<String> received = new ArrayList<>();
        while (received.size() < expected.size()) {
            FakeEventGateway.Emitted message = gateway.takeEmitted(WAIT_MILLIS);
            assertThat(message).isNotNull();
            assertThat(message.event).isEqualTo("subscribe");
            List<String> ids = ids(message);
            chunkSizes.add(ids.size());
            received.addAll(ids);
        }

        assertThat(received).containsExactlyInAnyOrderElementsOf(expected);
        assertThat(chunkSizes).containsExactly(100, 100, 49);
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
    }

    @Test
    void resendsTheWholeSetAfterReconnectWithoutBufferedDuplicates() throws Exception {
        dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        dispatcher.subscribe("sb-2", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-2"));

        gateway.holdConnects();
        gateway.dropConnection();
        waitUntil(() -> !dispatcher.isConnected());

        Runnable removed = dispatcher.subscribe("sb-3", NOOP, STATE_EVENTS);
        dispatcher.subscribe("sb-4", NOOP, STATE_EVENTS);
        removed.run();
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
        gateway.resumeAccepting();

        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1", "sb-2", "sb-4"));
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
    }

    @Test
    void connectionEpochAdvancesOnConnectAndOnEveryReconnect() throws Exception {
        EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
        assertThat(manager.connectionEpoch()).isZero();
        manager.subscribe("sb-1", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        long afterConnect = manager.connectionEpoch();
        assertThat(afterConnect).isPositive();

        gateway.holdConnects();
        gateway.dropConnection();
        waitUntil(() -> !dispatcher.isConnected());
        assertThat(manager.connectionEpoch()).isEqualTo(afterConnect);
        gateway.resumeAccepting();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));

        assertThat(manager.connectionEpoch()).isGreaterThan(afterConnect);
        assertThat(new EventSubscriptionManager(null).connectionEpoch()).isZero();
        manager.shutdown();
    }

    @Test
    void servesIdsUpToTheCapAndSendsNothingForTheRest() throws Exception {
        EventDispatcher capped = cappedDispatcher();
        EventSubscriptionManager manager = new EventSubscriptionManager(capped);
        try {
            manager.subscribe("sb-1", NOOP, STATE_EVENTS);
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
            manager.subscribe("sb-2", NOOP, STATE_EVENTS);
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-2"));
            manager.subscribe("sb-3", NOOP, STATE_EVENTS);

            assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
            assertThat(capped.isServed("sb-3")).isFalse();
            assertThat(manager.isEventBacked("sb-1")).isTrue();
            assertThat(manager.isEventBacked("sb-3")).isFalse();
        } finally {
            manager.shutdown();
            capped.shutdown();
        }
    }

    @Test
    void freesASlotOnTheLastListenerWithoutPromotingAnUnservedId() throws Exception {
        EventDispatcher capped = cappedDispatcher();
        try {
            Runnable releaseFirst = capped.subscribe("sb-1", NOOP, STATE_EVENTS);
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
            capped.subscribe("sb-2", NOOP, STATE_EVENTS);
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-2"));
            Runnable releaseThird = capped.subscribe("sb-3", NOOP, STATE_EVENTS);
            assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();

            releaseFirst.run();
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "unsubscribe", Set.of("sb-1"));
            assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
            assertThat(capped.isServed("sb-3")).isFalse();

            capped.subscribe("sb-4", NOOP, STATE_EVENTS);
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-4"));

            // sb-3 was never served, so its last listener sends nothing.
            releaseThird.run();
            assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
        } finally {
            capped.shutdown();
        }
    }

    @Test
    void resendsExactlyTheServedIdsOnReconnect() throws Exception {
        EventDispatcher capped = cappedDispatcher();
        try {
            capped.subscribe("sb-1", NOOP, STATE_EVENTS);
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
            capped.subscribe("sb-2", NOOP, STATE_EVENTS);
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-2"));
            capped.subscribe("sb-3", NOOP, STATE_EVENTS);
            assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();

            gateway.dropConnection();

            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1", "sb-2"));
            assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
        } finally {
            capped.shutdown();
        }
    }

    @Test
    void isNotEventBackedWhileDisconnectedOrAfterShutdown() throws Exception {
        EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
        gateway.holdConnects();
        manager.subscribe("sb-1", NOOP, STATE_EVENTS);
        assertThat(manager.isEventBacked("sb-1")).isFalse();

        gateway.resumeAccepting();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        assertThat(manager.isEventBacked("sb-1")).isTrue();

        manager.shutdown();
        assertThat(manager.isEventBacked("sb-1")).isFalse();
        assertThat(new EventSubscriptionManager(null).isEventBacked("sb-1")).isFalse();
    }

    private EventDispatcher cappedDispatcher() {
        return new EventDispatcher(gateway.apiUrl(), "test-token", null, "sdk-java", "1.2.3", 2);
    }

    @Test
    void dispatchesSlimStatePayloadOnlyToMatchingId() throws Exception {
        BlockingQueue<JsonNode> received = new LinkedBlockingQueue<>();
        dispatcher.subscribe("sb-1", (event, data) -> received.add(data), STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));

        gateway.sendEvent("sandbox.state.updated",
                "{\"sandbox\":{\"id\":\"sb-other\",\"state\":\"started\"},\"oldState\":\"starting\",\"newState\":\"started\"}");
        gateway.sendEvent("sandbox.state.updated",
                "{\"sandbox\":{\"id\":\"sb-1\",\"state\":\"started\"},\"oldState\":\"starting\",\"newState\":\"started\"}");

        JsonNode data = received.poll(WAIT_MILLIS, TimeUnit.MILLISECONDS);
        assertThat(data).isNotNull();
        assertThat(data.path("sandbox").path("id").asText()).isEqualTo("sb-1");
        assertThat(data.path("newState").asText()).isEqualTo("started");
        assertThat(received.poll(QUIET_MILLIS, TimeUnit.MILLISECONDS)).isNull();
    }

    private static void assertEmitted(FakeEventGateway.Emitted message, String event, Set<String> ids) {
        assertThat(message).as("expected %s %s", event, ids).isNotNull();
        assertThat(message.event).isEqualTo(event);
        // Exact multiset: a duplicated id on the wire must fail.
        assertThat(ids(message)).containsExactlyInAnyOrderElementsOf(ids);
    }

    private static List<String> ids(FakeEventGateway.Emitted message) {
        List<String> ids = new ArrayList<>();
        for (JsonNode id : message.payload.path("sandboxIds")) {
            ids.add(id.asText());
        }
        return ids;
    }

    private static void waitUntil(java.util.function.BooleanSupplier condition) throws InterruptedException {
        long deadline = System.currentTimeMillis() + WAIT_MILLIS;
        while (!condition.getAsBoolean()) {
            assertThat(System.currentTimeMillis()).as("condition not met in time").isLessThan(deadline);
            Thread.sleep(10);
        }
    }
}
