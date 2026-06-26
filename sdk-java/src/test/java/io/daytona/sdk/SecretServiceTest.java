// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.api.SecretApi;
import io.daytona.api.client.model.CreateSecret;
import io.daytona.api.client.model.UpdateSecret;
import io.daytona.sdk.exception.DaytonaConflictException;
import io.daytona.sdk.exception.DaytonaNotFoundException;
import io.daytona.sdk.exception.DaytonaServerException;
import io.daytona.sdk.model.CreateSecretParams;
import io.daytona.sdk.model.Secret;
import io.daytona.sdk.model.UpdateSecretParams;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.OffsetDateTime;
import java.util.Arrays;
import java.util.Collections;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class SecretServiceTest {

    @Mock
    private SecretApi secretApi;

    private SecretService service;

    @BeforeEach
    void setUp() {
        service = new SecretService(secretApi);
    }

    @Test
    void createMapsResponse() {
        when(secretApi.createSecret(any(), isNull())).thenReturn(
                secret("sec-1", "anthropic-prod", "dtn_secret_sec-1", Arrays.asList("api.anthropic.com")));

        CreateSecretParams params = new CreateSecretParams();
        params.setName("anthropic-prod");
        params.setValue("sk-ant-123");
        params.setDescription("prod key");
        params.setHosts(Arrays.asList("api.anthropic.com"));

        Secret result = service.create(params);

        assertThat(result.getId()).isEqualTo("sec-1");
        assertThat(result.getName()).isEqualTo("anthropic-prod");
        assertThat(result.getPlaceholder()).isEqualTo("dtn_secret_sec-1");
        assertThat(result.getHosts()).containsExactly("api.anthropic.com");
    }

    @Test
    void createBuildsRequest() {
        when(secretApi.createSecret(any(), isNull())).thenReturn(
                secret("sec-1", "anthropic-prod", "dtn_secret_sec-1", Collections.<String>emptyList()));

        CreateSecretParams params = new CreateSecretParams();
        params.setName("anthropic-prod");
        params.setValue("sk-ant-123");
        params.setDescription("prod key");
        params.setHosts(Arrays.asList("api.anthropic.com", "*.anthropic.com"));

        service.create(params);

        ArgumentCaptor<CreateSecret> captor = ArgumentCaptor.forClass(CreateSecret.class);
        verify(secretApi).createSecret(captor.capture(), isNull());
        CreateSecret req = captor.getValue();
        assertThat(req.getName()).isEqualTo("anthropic-prod");
        assertThat(req.getValue()).isEqualTo("sk-ant-123");
        assertThat(req.getDescription()).isEqualTo("prod key");
        assertThat(req.getHosts()).containsExactly("api.anthropic.com", "*.anthropic.com");
    }

    @Test
    void createOmitsUnsetOptionalFields() {
        when(secretApi.createSecret(any(), isNull())).thenReturn(
                secret("sec-1", "token", "dtn_secret_sec-1", null));

        CreateSecretParams params = new CreateSecretParams();
        params.setName("token");
        params.setValue("value");

        service.create(params);

        ArgumentCaptor<CreateSecret> captor = ArgumentCaptor.forClass(CreateSecret.class);
        verify(secretApi).createSecret(captor.capture(), isNull());
        CreateSecret req = captor.getValue();
        assertThat(req.getDescription()).isNull();
        assertThat(req.getHosts()).isEmpty();
    }

    @Test
    void createReturnsEmptyModelWhenApiReturnsNull() {
        when(secretApi.createSecret(any(), isNull())).thenReturn(null);

        CreateSecretParams params = new CreateSecretParams();
        params.setName("token");
        params.setValue("value");

        Secret result = service.create(params);

        assertThat(result.getId()).isNull();
        assertThat(result.getName()).isNull();
        assertThat(result.getPlaceholder()).isNull();
    }

    @Test
    void listMapsAllItems() {
        when(secretApi.listSecrets(isNull())).thenReturn(Arrays.asList(
                secret("sec-1", "anthropic-prod", "dtn_secret_sec-1", Arrays.asList("api.anthropic.com")),
                secret("sec-2", "openai-prod", "dtn_secret_sec-2", Arrays.asList("api.openai.com"))
        ));

        assertThat(service.list())
                .extracting(Secret::getName)
                .containsExactly("anthropic-prod", "openai-prod");
    }

    @Test
    void listReturnsEmptyListWhenApiReturnsNull() {
        when(secretApi.listSecrets(isNull())).thenReturn(null);

        assertThat(service.list()).isEqualTo(Collections.<Secret>emptyList());
    }

    @Test
    void getMapsResponse() {
        when(secretApi.getSecret("sec-1", null)).thenReturn(
                secret("sec-1", "anthropic-prod", "dtn_secret_sec-1", Arrays.asList("api.anthropic.com")));

        Secret result = service.get("sec-1");

        assertThat(result.getId()).isEqualTo("sec-1");
        assertThat(result.getName()).isEqualTo("anthropic-prod");
        assertThat(result.getHosts()).containsExactly("api.anthropic.com");
    }

    @Test
    void getReturnsEmptyModelWhenApiReturnsNull() {
        when(secretApi.getSecret("sec-1", null)).thenReturn(null);

        Secret result = service.get("sec-1");

        assertThat(result.getId()).isNull();
        assertThat(result.getName()).isNull();
        assertThat(result.getPlaceholder()).isNull();
    }

    @Test
    void updateBuildsRequestAndMapsResponse() {
        when(secretApi.updateSecret(any(), any(), isNull())).thenReturn(
                secret("sec-1", "anthropic-prod", "dtn_secret_sec-1", Arrays.asList("api.anthropic.com", "*.anthropic.com")));

        UpdateSecretParams params = new UpdateSecretParams();
        params.setValue("sk-ant-new");
        params.setHosts(Arrays.asList("api.anthropic.com", "*.anthropic.com"));

        Secret result = service.update("sec-1", params);

        ArgumentCaptor<UpdateSecret> captor = ArgumentCaptor.forClass(UpdateSecret.class);
        verify(secretApi).updateSecret(org.mockito.ArgumentMatchers.eq("sec-1"), captor.capture(), isNull());
        UpdateSecret req = captor.getValue();
        assertThat(req.getValue()).isEqualTo("sk-ant-new");
        assertThat(req.getDescription()).isNull();
        assertThat(req.getHosts()).containsExactly("api.anthropic.com", "*.anthropic.com");
        assertThat(result.getHosts()).containsExactly("api.anthropic.com", "*.anthropic.com");
    }

    @Test
    void deleteDelegatesToApi() {
        service.delete("sec-1");

        verify(secretApi).deleteSecret("sec-1", null);
    }

    @Test
    void createMapsConflictError() {
        when(secretApi.createSecret(any(), isNull()))
                .thenThrow(new io.daytona.api.client.ApiException(409, "conflict", null, "{\"message\":\"name exists\"}"));

        CreateSecretParams params = new CreateSecretParams();
        params.setName("anthropic-prod");
        params.setValue("value");

        assertThatThrownBy(() -> service.create(params))
                .isInstanceOf(DaytonaConflictException.class)
                .hasMessage("name exists");
    }

    @ParameterizedTest
    @MethodSource("mappedMainApiExceptions")
    void getMapsApiErrors(int status, Class<? extends RuntimeException> type) {
        when(secretApi.getSecret("sec-1", null))
                .thenThrow(new io.daytona.api.client.ApiException(status, "boom", null, "{\"message\":\"mapped\"}"));

        assertThatThrownBy(() -> service.get("sec-1"))
                .isInstanceOf(type)
                .hasMessage("mapped");
    }

    private static Stream<Arguments> mappedMainApiExceptions() {
        return Stream.of(
                Arguments.of(404, DaytonaNotFoundException.class),
                Arguments.of(500, DaytonaServerException.class)
        );
    }

    private static io.daytona.api.client.model.Secret secret(String id, String name, String placeholder, java.util.List<String> hosts) {
        io.daytona.api.client.model.Secret secret = new io.daytona.api.client.model.Secret();
        secret.setId(id);
        secret.setName(name);
        secret.setPlaceholder(placeholder);
        if (hosts != null) {
            secret.setHosts(hosts);
        }
        secret.setCreatedAt(OffsetDateTime.parse("2026-06-25T00:00:00Z"));
        secret.setUpdatedAt(OffsetDateTime.parse("2026-06-25T00:00:00Z"));
        return secret;
    }
}
