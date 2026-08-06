from daytona import Daytona


def main():
    daytona = Daytona()

    # Create a warm pool that keeps ready-to-use sandboxes for an existing snapshot.
    # `target` is optional and defaults to the organization default region.
    pool = daytona.warm_pool.create("my-snapshot", pool=3)
    print(f"Created warm pool {pool.id} for snapshot '{pool.snapshot}' in {pool.target}")

    # List warm pools. current_size vs pool is the status check: current_size is the
    # number of ready sandboxes; error_reason is set when the pool cannot be filled.
    for p in daytona.warm_pool.list():
        status = f" (error: {p.error_reason})" if p.error_reason else ""
        print(f"{p.snapshot} ({p.target}): {p.current_size}/{p.pool} ready{status}")

    # Grow the pool. Setting pool to 0 drains it without deleting the pool.
    updated = daytona.warm_pool.update(pool.id, pool=5)
    print(f"Updated desired size to {updated.pool}")

    # Cleanup
    daytona.warm_pool.delete(pool.id)
    print("Deleted warm pool")


if __name__ == "__main__":
    main()
