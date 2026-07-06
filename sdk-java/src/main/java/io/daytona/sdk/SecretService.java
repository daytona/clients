// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.api.SecretApi;
import io.daytona.api.client.model.CreateSecret;
import io.daytona.api.client.model.UpdateSecret;
import io.daytona.sdk.model.CreateSecretParams;
import io.daytona.sdk.model.ListSecretsQuery;
import io.daytona.sdk.model.ListSecretsResponse;
import io.daytona.sdk.model.Secret;
import io.daytona.sdk.model.UpdateSecretParams;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;

/**
 * Service for managing organization-scoped Daytona Secrets.
 *
 * <p>Secrets can be created, listed, retrieved, updated, and deleted, and referenced when creating a
 * Sandbox via the {@code secrets} field on the create-sandbox parameters. The plaintext {@code value}
 * is write-only and is never returned by the API; the Sandbox only ever sees the Secret's opaque
 * placeholder, and the real value is substituted at the network egress layer for the Secret's allowed
 * hosts.
 */
public class SecretService {
    private final SecretApi secretApi;

    SecretService(SecretApi secretApi) {
        this.secretApi = secretApi;
    }

    /**
     * Creates a new Secret.
     *
     * @param params creation parameters; {@code name} must match {@code ^[a-zA-Z_][a-zA-Z0-9_-]*$} and
     *               be unique within the organization
     * @return created {@link Secret} (without the plaintext {@code value})
     * @throws io.daytona.sdk.exception.DaytonaConflictException if a Secret with the same name already exists
     * @throws io.daytona.sdk.exception.DaytonaException if creation fails
     */
    public Secret create(CreateSecretParams params) {
        CreateSecret req = new CreateSecret().name(params.getName()).value(params.getValue());
        if (params.getDescription() != null) {
            req.setDescription(params.getDescription());
        }
        if (params.getHosts() != null) {
            req.setHosts(params.getHosts());
        }
        io.daytona.api.client.model.Secret secret = ExceptionMapper.callMain(
                () -> secretApi.createSecret(req, null)
        );
        return toSecret(secret);
    }

    /**
     * Lists Secrets in the organization one page at a time, using default query parameters.
     *
     * @return page of Secrets; {@code nextCursor} is {@code null} when there are no more pages
     * @throws io.daytona.sdk.exception.DaytonaException if the API request fails
     */
    public ListSecretsResponse list() {
        return list(null);
    }

    /**
     * Lists Secrets in the organization one page at a time. Pass the {@code nextCursor} from a
     * previous response as the query {@code cursor} to fetch the next page.
     *
     * @param query optional filter, sort, and pagination parameters; may be {@code null}
     * @return page of Secrets; {@code nextCursor} is {@code null} when there are no more pages
     * @throws io.daytona.sdk.exception.DaytonaException if the API request fails
     */
    public ListSecretsResponse list(ListSecretsQuery query) {
        String cursor = query == null ? null : query.getCursor();
        BigDecimal limit = query == null || query.getLimit() == null ? null : BigDecimal.valueOf(query.getLimit());
        String name = query == null ? null : query.getName();
        String sort = query == null ? null : query.getSort();
        String order = query == null ? null : query.getOrder();
        io.daytona.api.client.model.ListSecretsResponse result = ExceptionMapper.callMain(
                () -> secretApi.listSecretsPaginated(null, cursor, limit, name, sort, order)
        );

        ListSecretsResponse output = new ListSecretsResponse();
        List<Secret> items = new ArrayList<Secret>();
        if (result != null && result.getItems() != null) {
            for (io.daytona.api.client.model.Secret secret : result.getItems()) {
                items.add(toSecret(secret));
            }
        }
        output.setItems(items);
        output.setTotal(result != null && result.getTotal() != null ? result.getTotal().intValue() : 0);
        output.setNextCursor(result == null ? null : result.getNextCursor());
        return output;
    }

    /**
     * Retrieves a Secret by ID.
     *
     * @param secretId Secret identifier
     * @return matching {@link Secret}
     * @throws io.daytona.sdk.exception.DaytonaNotFoundException if no Secret with the given ID exists
     * @throws io.daytona.sdk.exception.DaytonaException if the request fails
     */
    public Secret get(String secretId) {
        io.daytona.api.client.model.Secret secret = ExceptionMapper.callMain(() -> secretApi.getSecret(secretId, null));
        return toSecret(secret);
    }

    /**
     * Updates an existing Secret. Omitted ({@code null}) fields are left unchanged.
     *
     * @param secretId Secret identifier
     * @param params fields to update
     * @return updated {@link Secret}
     * @throws io.daytona.sdk.exception.DaytonaNotFoundException if no Secret with the given ID exists
     * @throws io.daytona.sdk.exception.DaytonaException if the request fails
     */
    public Secret update(String secretId, UpdateSecretParams params) {
        UpdateSecret req = new UpdateSecret();
        if (params.getValue() != null) {
            req.setValue(params.getValue());
        }
        if (params.getDescription() != null) {
            req.setDescription(params.getDescription());
        }
        if (params.getHosts() != null) {
            req.setHosts(params.getHosts());
        }
        io.daytona.api.client.model.Secret secret = ExceptionMapper.callMain(
                () -> secretApi.updateSecret(secretId, req, null)
        );
        return toSecret(secret);
    }

    /**
     * Deletes a Secret by ID.
     *
     * @param secretId Secret identifier
     * @throws io.daytona.sdk.exception.DaytonaNotFoundException if no Secret with the given ID exists
     * @throws io.daytona.sdk.exception.DaytonaException if deletion fails
     */
    public void delete(String secretId) {
        ExceptionMapper.runMain(() -> secretApi.deleteSecret(secretId, null));
    }

    private Secret toSecret(io.daytona.api.client.model.Secret source) {
        Secret secret = new Secret();
        if (source != null) {
            secret.setId(source.getId());
            secret.setName(source.getName());
            secret.setDescription(source.getDescription());
            secret.setPlaceholder(source.getPlaceholder());
            secret.setHosts(source.getHosts());
            secret.setCreatedAt(source.getCreatedAt() == null ? null : source.getCreatedAt().toString());
            secret.setUpdatedAt(source.getUpdatedAt() == null ? null : source.getUpdatedAt().toString());
        }
        return secret;
    }
}
