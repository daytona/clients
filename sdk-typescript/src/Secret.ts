/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { SecretApi } from '@daytona/api-client'
import type {
  Secret as SecretModel,
  ListSecretsResponse as ListSecretsResponseDto,
  ListSecretsPaginatedSortEnum,
  ListSecretsPaginatedOrderEnum,
} from '@daytona/api-client'
import { WithInstrumentation } from './utils/otel.decorator'

/**
 * Represents an organization-scoped Secret.
 *
 * The plaintext `value` is write-only and is never returned by the API. When a Secret is
 * referenced from a Sandbox, the injected environment variable holds the opaque
 * {@link Secret.placeholder} token, not the real value. The real value is substituted
 * transparently on outbound requests to the Secret's allowed {@link Secret.hosts}.
 *
 * @property {string} id - Unique identifier for the Secret
 * @property {string} name - Name of the Secret (unique within the organization)
 * @property {string} [description] - Optional description of the Secret
 * @property {string} placeholder - Opaque token (`dtn_secret_<id>`) injected as the env var value
 * @property {string[]} hosts - Hosts the Secret value may be sent to (may be empty)
 * @property {string} createdAt - Date and time when the Secret was created
 * @property {string} updatedAt - Date and time when the Secret was last updated
 */
export type Secret = SecretModel & { __brand: 'Secret' }

/**
 * Parameters for creating a new Secret.
 *
 * @interface
 * @property {string} name - Name of the Secret. Must match `^[a-zA-Z_][a-zA-Z0-9_-]*$` and be
 * unique within the organization.
 * @property {string} value - The plaintext Secret value. Stored encrypted and never returned by the API.
 * @property {string} [description] - Optional description of the Secret
 * @property {string[]} [hosts] - Hosts the Secret value may be sent to. Each entry is a hostname
 * (`api.example.com`) or a `*.` wildcard (`*.example.com`); ports are not supported. Omit to leave
 * the Secret unrestricted.
 */
export interface CreateSecretParams {
  name: string
  value: string
  description?: string
  hosts?: string[]
}

/**
 * Parameters for updating an existing Secret. Omitted fields are left unchanged.
 *
 * @interface
 * @property {string} [value] - Replaces the stored Secret value when present
 * @property {string} [description] - Optional description of the Secret
 * @property {string[]} [hosts] - Hosts the Secret value may be sent to. Same constraints as
 * {@link CreateSecretParams.hosts}.
 */
export interface UpdateSecretParams {
  value?: string
  description?: string
  hosts?: string[]
}

/**
 * Query parameters for listing Secrets with pagination.
 *
 * @interface
 * @property {string} [cursor] - Pagination cursor from a previous response. Omit to fetch the first page.
 * @property {number} [limit] - Number of results per page (1-200). Defaults to 100.
 * @property {string} [name] - Filters the results to Secrets whose name partially matches the value
 * @property {ListSecretsPaginatedSortEnum} [sort] - Field to sort by. Defaults to `createdAt`.
 * @property {ListSecretsPaginatedOrderEnum} [order] - Direction to sort by. Defaults to `desc`.
 */
export interface ListSecretsQuery {
  cursor?: string
  limit?: number
  name?: string
  sort?: ListSecretsPaginatedSortEnum
  order?: ListSecretsPaginatedOrderEnum
}

/**
 * Represents a paginated list of Daytona Secrets.
 *
 * @property {Secret[]} items - List of Secrets in the current page.
 * @property {number} total - Total number of Secrets matching the filters.
 * @property {string | null} nextCursor - Cursor for the next page of results. `null` when there are no more pages.
 */
export interface ListSecretsResponse extends Omit<ListSecretsResponseDto, 'items'> {
  items: Secret[]
}

/**
 * Service for managing organization-scoped Daytona Secrets.
 *
 * This service provides methods to create, list, get, update, and delete Secrets. Secrets can be
 * mounted into Sandboxes as environment variables by referencing them via the `secrets` field on
 * the create-sandbox parameters. The Sandbox only ever sees the Secret's opaque placeholder; the
 * real value is substituted at the network egress layer for the Secret's allowed hosts.
 *
 * @class
 */
export class SecretService {
  constructor(private secretApi: SecretApi) {}

  /**
   * Lists Secrets in the organization with cursor-based pagination.
   *
   * @param {ListSecretsQuery} [query] - Optional filters, sorting, pagination cursor, and per-page size
   * @returns {Promise<ListSecretsResponse>} A page of Secrets together with the total count and the
   * cursor for the next page
   *
   * @example
   * const daytona = new Daytona();
   * let cursor: string | undefined = undefined;
   * do {
   *   const page = await daytona.secret.list({ cursor, limit: 50 });
   *   console.log(`Fetched ${page.items.length} of ${page.total} secrets`);
   *   page.items.forEach(secret => console.log(`${secret.name} (${secret.id})`));
   *   cursor = page.nextCursor ?? undefined;
   * } while (cursor);
   */
  @WithInstrumentation()
  async list(query?: ListSecretsQuery): Promise<ListSecretsResponse> {
    const response = await this.secretApi.listSecretsPaginated(
      undefined,
      query?.cursor,
      query?.limit,
      query?.name,
      query?.sort,
      query?.order,
    )
    return {
      items: response.data.items.map((secret) => secret as Secret),
      total: response.data.total,
      nextCursor: response.data.nextCursor,
    }
  }

  /**
   * Gets a Secret by its ID.
   *
   * @param {string} secretId - ID of the Secret to retrieve
   * @returns {Promise<Secret>} The requested Secret
   * @throws {DaytonaNotFoundError} If the Secret does not exist
   *
   * @example
   * const daytona = new Daytona();
   * const secret = await daytona.secret.get("secret-id");
   * console.log(`Secret ${secret.name} can be used on ${secret.hosts.join(', ')}`);
   */
  @WithInstrumentation()
  async get(secretId: string): Promise<Secret> {
    const response = await this.secretApi.getSecret(secretId)
    return response.data as Secret
  }

  /**
   * Creates a new Secret.
   *
   * @param {CreateSecretParams} params - Parameters for the new Secret
   * @returns {Promise<Secret>} The newly created Secret (without the plaintext `value`)
   * @throws {DaytonaConflictError} If a Secret with the same name already exists in the organization
   *
   * @example
   * const daytona = new Daytona();
   * const secret = await daytona.secret.create({
   *   name: "anthropic-prod",
   *   value: "sk-ant-...",
   *   hosts: ["api.anthropic.com"],
   * });
   * console.log(`Created secret ${secret.name} with placeholder ${secret.placeholder}`);
   */
  @WithInstrumentation()
  async create(params: CreateSecretParams): Promise<Secret> {
    const response = await this.secretApi.createSecret({
      name: params.name,
      value: params.value,
      description: params.description,
      hosts: params.hosts,
    })
    return response.data as Secret
  }

  /**
   * Updates an existing Secret. Omitted fields are left unchanged.
   *
   * @param {string} secretId - ID of the Secret to update
   * @param {UpdateSecretParams} params - Fields to update
   * @returns {Promise<Secret>} The updated Secret
   * @throws {DaytonaNotFoundError} If the Secret does not exist
   *
   * @example
   * const daytona = new Daytona();
   * const secret = await daytona.secret.update("secret-id", {
   *   value: "sk-ant-new-value",
   *   hosts: ["api.anthropic.com", "*.anthropic.com"],
   * });
   */
  @WithInstrumentation()
  async update(secretId: string, params: UpdateSecretParams): Promise<Secret> {
    const response = await this.secretApi.updateSecret(secretId, {
      value: params.value,
      description: params.description,
      hosts: params.hosts,
    })
    return response.data as Secret
  }

  /**
   * Deletes a Secret.
   *
   * @param {string} secretId - ID of the Secret to delete
   * @returns {Promise<void>}
   * @throws {DaytonaNotFoundError} If the Secret does not exist
   *
   * @example
   * const daytona = new Daytona();
   * await daytona.secret.delete("secret-id");
   * console.log("Secret deleted successfully");
   */
  @WithInstrumentation()
  async delete(secretId: string): Promise<void> {
    await this.secretApi.deleteSecret(secretId)
  }
}
