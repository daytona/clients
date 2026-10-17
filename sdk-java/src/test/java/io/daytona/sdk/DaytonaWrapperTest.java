// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import java.util.logging.Logger;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

class DaytonaWrapperTest {
    @AfterEach
    void resetWarnings() {
        Daytona.resetRootNamespaceWarningForTests();
    }

    @Test
    void v1ReturnsSameDelegateInstance() {
        DaytonaV1 delegate = Mockito.mock(DaytonaV1.class);

        Daytona daytona = new Daytona(delegate);

        assertThat(daytona.v1()).isSameAs(delegate);
    }

    @SuppressWarnings("deprecation")
    @Test
    void deprecatedRootCallsWarnOnceAndForwardToSameDelegate() {
        DaytonaV1 delegate = Mockito.mock(DaytonaV1.class);
        Sandbox sandbox = Mockito.mock(Sandbox.class);
        SnapshotService snapshotService = Mockito.mock(SnapshotService.class);
        when(delegate.create()).thenReturn(sandbox);
        when(delegate.snapshot()).thenReturn(snapshotService);

        Daytona daytona = new Daytona(delegate);

        TestSupport.withLogCapture(Logger.getLogger(Daytona.class.getName()), records -> {
            Daytona.resetRootNamespaceWarningForTests();

            assertThat(daytona.create()).isSameAs(sandbox);
            assertThat(daytona.snapshot()).isSameAs(snapshotService);
            assertThat(daytona.snapshot()).isSameAs(snapshotService);

            assertThat(records).hasSize(1);
            assertThat(records.get(0).getMessage()).contains("Daytona.create()").contains("v1().create()");
            Daytona.resetRootNamespaceWarningForTests();
        });
    }

    @SuppressWarnings("deprecation")
    @Test
    void everyDeprecatedRootOverloadForwardsItsArguments() {
        DaytonaV1 delegate = Mockito.mock(DaytonaV1.class);
        Daytona daytona = new Daytona(delegate);
        io.daytona.sdk.model.CreateSandboxFromSnapshotParams snapshotParams =
                new io.daytona.sdk.model.CreateSandboxFromSnapshotParams();
        io.daytona.sdk.model.CreateSandboxFromImageParams imageParams =
                new io.daytona.sdk.model.CreateSandboxFromImageParams();
        io.daytona.sdk.model.ListSandboxesQuery query = new io.daytona.sdk.model.ListSandboxesQuery();
        java.util.function.Consumer<String> onLogs = line -> { };

        TestSupport.withLogCapture(Logger.getLogger(Daytona.class.getName()), records -> {
            daytona.create();
            daytona.create(snapshotParams);
            daytona.create(imageParams);
            daytona.create(snapshotParams, 11L);
            daytona.create(imageParams, 12L);
            daytona.create(imageParams, 13L, onLogs);
            daytona.get("sandbox-1");
            daytona.list();
            daytona.list(query);
            daytona.listStream();
            daytona.listStream(query);
            daytona.snapshot();
            daytona.volume();
            daytona.secret();
            daytona.warmPool();
            Daytona.resetRootNamespaceWarningForTests();
        });

        Mockito.verify(delegate).create();
        Mockito.verify(delegate).create(snapshotParams);
        Mockito.verify(delegate).create(imageParams);
        Mockito.verify(delegate).create(snapshotParams, 11L);
        Mockito.verify(delegate).create(imageParams, 12L);
        Mockito.verify(delegate).create(imageParams, 13L, onLogs);
        Mockito.verify(delegate).get("sandbox-1");
        Mockito.verify(delegate).list();
        Mockito.verify(delegate).list(query);
        Mockito.verify(delegate).listStream();
        Mockito.verify(delegate).listStream(query);
        Mockito.verify(delegate).snapshot();
        Mockito.verify(delegate).volume();
        Mockito.verify(delegate).secret();
        Mockito.verify(delegate).warmPool();
        Mockito.verifyNoMoreInteractions(delegate);
    }

    @Test
    void closeForwardsToDelegateWithoutWarning() {
        DaytonaV1 delegate = Mockito.mock(DaytonaV1.class);

        TestSupport.withLogCapture(Logger.getLogger(Daytona.class.getName()), records -> {
            Daytona.resetRootNamespaceWarningForTests();
            new Daytona(delegate).close();
            assertThat(records).isEmpty();
        });

        Mockito.verify(delegate).close();
        Mockito.verifyNoMoreInteractions(delegate);
    }

    @Test
    void rootWrapperExposesEveryPublicV1MethodWithSameSignature() {
        Set<String> wrapperMethods = Arrays.stream(Daytona.class.getMethods())
                .map(DaytonaWrapperTest::methodSignature)
                .collect(Collectors.toSet());

        List<String> missing = Arrays.stream(DaytonaV1.class.getMethods())
                .filter(method -> Modifier.isPublic(method.getModifiers()))
                .map(DaytonaWrapperTest::methodSignature)
                .filter(signature -> !wrapperMethods.contains(signature))
                .sorted(Comparator.naturalOrder())
                .collect(Collectors.toList());

        assertThat(missing).isEmpty();
    }

    private static String methodSignature(Method method) {
        // Generic parameter and return types, so e.g. Consumer<String> vs Consumer<Object> counts as drift.
        return method.getGenericReturnType().getTypeName() + " " + method.getName() + "("
                + Arrays.stream(method.getGenericParameterTypes())
                        .map(java.lang.reflect.Type::getTypeName)
                        .collect(Collectors.joining(","))
                + ")";
    }
}
