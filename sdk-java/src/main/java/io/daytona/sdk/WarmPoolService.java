package io.daytona.sdk;

import io.daytona.api.client.api.WarmPoolsApi;
import io.daytona.api.client.model.CreateWarmPool;
import io.daytona.api.client.model.UpdateWarmPool;
import io.daytona.sdk.model.WarmPool;

import java.math.BigDecimal;
import java.util.List;
import java.util.ArrayList;

/**
 * Service for managing Daytona Warm Pools.
 *
 * <p>Warm pools keep ready-to-use Sandboxes for a snapshot. A pool's {@code currentSize} versus
 * {@code pool} is its status: {@code currentSize} is the number of ready warm sandboxes,
 * {@code pool} is the desired number, and {@code errorReason} is set when the pool cannot be
 * filled.
 */
public class WarmPoolService {
    private final WarmPoolsApi warmPoolsApi;

    WarmPoolService(WarmPoolsApi warmPoolsApi) {
        this.warmPoolsApi = warmPoolsApi;
    }

    /**
     * Lists all warm pools in the organization.
     *
     * @return list of warm pools
     * @throws io.daytona.sdk.exception.DaytonaException if the API request fails
     */
    public List<WarmPool> list() {
        List<io.daytona.api.client.model.WarmPool> warmPools = ExceptionMapper.callMain(() -> warmPoolsApi.listWarmPools(null));
        List<WarmPool> result = new ArrayList<WarmPool>();
        if (warmPools != null) {
            for (io.daytona.api.client.model.WarmPool warmPool : warmPools) {
                result.add(toWarmPool(warmPool));
            }
        }
        return result;
    }

    /**
     * Creates a new warm pool.
     *
     * @param snapshot snapshot (ID or name) to keep warm sandboxes for
     * @param pool number of warm sandboxes to keep ready
     * @param target target region, or {@code null} for the organization default region
     * @return created {@link WarmPool}
     * @throws io.daytona.sdk.exception.DaytonaException if a pool for the same snapshot and
     *     region already exists or creation fails
     */
    public WarmPool create(String snapshot, int pool, String target) {
        CreateWarmPool request = new CreateWarmPool().snapshot(snapshot).pool(BigDecimal.valueOf(pool));
        if (target != null) {
            request.target(target);
        }
        io.daytona.api.client.model.WarmPool warmPoolDto = ExceptionMapper.callMain(
                () -> warmPoolsApi.createWarmPool(request, null)
        );
        return toWarmPool(warmPoolDto);
    }

    /**
     * Updates the desired size of a warm pool.
     *
     * @param id warm pool identifier
     * @param pool new desired number of warm sandboxes (0 drains the pool)
     * @return updated {@link WarmPool}
     * @throws io.daytona.sdk.exception.DaytonaException if no warm pool is found or request fails
     */
    public WarmPool update(String id, int pool) {
        io.daytona.api.client.model.WarmPool warmPoolDto = ExceptionMapper.callMain(
                () -> warmPoolsApi.updateWarmPool(id, new UpdateWarmPool().pool(BigDecimal.valueOf(pool)), null)
        );
        return toWarmPool(warmPoolDto);
    }

    /**
     * Deletes a warm pool by ID.
     *
     * @param id warm pool identifier
     * @throws io.daytona.sdk.exception.DaytonaException if deletion fails
     */
    public void delete(String id) {
        ExceptionMapper.runMain(() -> warmPoolsApi.deleteWarmPool(id, null));
    }

    private WarmPool toWarmPool(io.daytona.api.client.model.WarmPool source) {
        WarmPool warmPool = new WarmPool();
        if (source != null) {
            warmPool.setId(source.getId());
            warmPool.setSnapshot(source.getSnapshot());
            warmPool.setTarget(source.getTarget());
            warmPool.setPool(source.getPool() == null ? 0 : source.getPool().intValue());
            warmPool.setCurrentSize(source.getCurrentSize() == null ? 0 : source.getCurrentSize().intValue());
            warmPool.setErrorReason(source.getErrorReason());
        }
        return warmPool;
    }
}
