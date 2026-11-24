// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.internal;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class EventSubscriptionManagerTest {

    @Test
    void isConnectedIsFalseWithoutDispatcher() {
        EventSubscriptionManager manager = new EventSubscriptionManager(null);
        try {
            assertThat(manager.isConnected()).isFalse();
        } finally {
            manager.shutdown();
        }
    }

    @Test
    void isConnectedIsFalseAfterShutdownWhileDispatcherIsStillConnected() throws Exception {
        try (FakeEventGateway gateway = new FakeEventGateway()) {
            EventDispatcher dispatcher = new EventDispatcher(gateway.apiUrl(), "test-token");
            EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
            try {
                assertThat(manager.subscribe("sb-1", (event, data) -> { }, List.of("sandbox.state.updated"))).isNotNull();
                assertThat(gateway.takeEmitted(5000)).isNotNull();
                assertThat(manager.isConnected()).isTrue();

                manager.shutdown();

                // The dispatcher only disconnects after its linger delay.
                assertThat(dispatcher.isConnected()).isTrue();
                assertThat(manager.isConnected()).isFalse();
            } finally {
                manager.shutdown();
                dispatcher.shutdown();
            }
        }
    }
}
