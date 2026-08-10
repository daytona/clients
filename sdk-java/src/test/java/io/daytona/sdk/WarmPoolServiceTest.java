// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.api.WarmPoolsApi;
import io.daytona.api.client.model.CreateWarmPool;
import io.daytona.api.client.model.UpdateWarmPool;
import io.daytona.sdk.exception.DaytonaNotFoundException;
import io.daytona.sdk.exception.DaytonaServerException;
import io.daytona.sdk.model.WarmPool;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.Arrays;
import java.util.Collections;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class WarmPoolServiceTest {

    @Mock
    private WarmPoolsApi warmPoolsApi;

    private WarmPoolService service;

    @BeforeEach
    void setUp() {
        service = new WarmPoolService(warmPoolsApi);
    }

    @Test
    void createMapsResponse() {
        when(warmPoolsApi.createWarmPool(any(), isNull())).thenReturn(warmPoolDto("wp-1", "snap", 5, 3));

        WarmPool warmPool = service.create("snap", 5, null);

        assertThat(warmPool.getId()).isEqualTo("wp-1");
        assertThat(warmPool.getSnapshot()).isEqualTo("snap");
        assertThat(warmPool.getPool()).isEqualTo(5);
        assertThat(warmPool.getCurrentSize()).isEqualTo(3);

        ArgumentCaptor<CreateWarmPool> captor = ArgumentCaptor.forClass(CreateWarmPool.class);
        verify(warmPoolsApi).createWarmPool(captor.capture(), isNull());
        assertThat(captor.getValue().getSnapshot()).isEqualTo("snap");
        assertThat(captor.getValue().getPool()).isEqualTo(BigDecimal.valueOf(5));
        assertThat(captor.getValue().getTarget()).isNull();
    }

    @Test
    void createPassesTargetWhenGiven() {
        when(warmPoolsApi.createWarmPool(any(), isNull())).thenReturn(warmPoolDto("wp-1", "snap", 5, 0));

        service.create("snap", 5, "eu");

        ArgumentCaptor<CreateWarmPool> captor = ArgumentCaptor.forClass(CreateWarmPool.class);
        verify(warmPoolsApi).createWarmPool(captor.capture(), isNull());
        assertThat(captor.getValue().getTarget()).isEqualTo("eu");
    }

    @Test
    void listMapsAllItems() {
        when(warmPoolsApi.listWarmPools(isNull())).thenReturn(Arrays.asList(
                warmPoolDto("wp-1", "snap-a", 5, 5),
                warmPoolDto("wp-2", "snap-b", 3, 1)
        ));

        assertThat(service.list())
                .extracting(WarmPool::getSnapshot)
                .containsExactly("snap-a", "snap-b");
    }

    @Test
    void listReturnsEmptyListWhenApiReturnsNull() {
        when(warmPoolsApi.listWarmPools(isNull())).thenReturn(null);

        assertThat(service.list()).isEqualTo(Collections.<WarmPool>emptyList());
    }

    @Test
    void updateMapsResponse() {
        when(warmPoolsApi.updateWarmPool(eq("wp-1"), any(), isNull())).thenReturn(warmPoolDto("wp-1", "snap", 10, 3));

        WarmPool warmPool = service.update("wp-1", 10);

        assertThat(warmPool.getPool()).isEqualTo(10);

        ArgumentCaptor<UpdateWarmPool> captor = ArgumentCaptor.forClass(UpdateWarmPool.class);
        verify(warmPoolsApi).updateWarmPool(eq("wp-1"), captor.capture(), isNull());
        assertThat(captor.getValue().getPool()).isEqualTo(BigDecimal.valueOf(10));
    }

    @Test
    void deleteDelegatesToApi() {
        service.delete("wp-1");

        verify(warmPoolsApi).deleteWarmPool("wp-1", null);
    }

    @ParameterizedTest
    @MethodSource("mappedMainApiExceptions")
    void updateMapsApiErrors(int status, Class<? extends RuntimeException> type) {
        when(warmPoolsApi.updateWarmPool(eq("wp-1"), any(), isNull()))
                .thenThrow(new io.daytona.api.client.ApiException(status, "boom", null, "{\"message\":\"mapped\"}"));

        assertThatThrownBy(() -> service.update("wp-1", 1))
                .isInstanceOf(type)
                .hasMessage("mapped");
    }

    private static Stream<Arguments> mappedMainApiExceptions() {
        return Stream.of(
                Arguments.of(404, DaytonaNotFoundException.class),
                Arguments.of(500, DaytonaServerException.class)
        );
    }

    private static io.daytona.api.client.model.WarmPool warmPoolDto(String id, String snapshot, int pool, int currentSize) {
        io.daytona.api.client.model.WarmPool dto = new io.daytona.api.client.model.WarmPool();
        dto.setId(id);
        dto.setSnapshot(snapshot);
        dto.setTarget("us");
        dto.setPool(BigDecimal.valueOf(pool));
        dto.setCurrentSize(BigDecimal.valueOf(currentSize));
        return dto;
    }
}
