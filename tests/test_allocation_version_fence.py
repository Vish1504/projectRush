from datetime import datetime, timezone
from uuid import uuid4

from redis import Redis

from app.runtime.allocation_store import AllocationResult, AllocationStore


def test_allocation_version_fence():
    client = Redis(host="localhost", port=6379, decode_responses=True)
    store = AllocationStore(client)

    test_id = uuid4().hex
    campaign_id = 999999
    request_id = f"version-fence-test-{test_id}"
    viewer_id = f"version-fence-viewer-{test_id}"
    decision_time = datetime.now(timezone.utc)

    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )
    decision_key = store._decision_key(request_id)
    version_key = "rush:index:version"

    original_version = client.get(version_key)
    client.set(version_key, 10)

    try:
        client.set(frequency_key, 1, ex=3600)
        client.delete(decision_key)

        result = store.try_allocate(
            campaign_id=campaign_id,
            request_id=request_id,
            viewer_id=viewer_id,
            frequency_cap_per_hour=3,
            decision_time=decision_time,
            index_version=9,
        )

        assert result == AllocationResult.STALE_INDEX

        # A stale snapshot must not mutate any shared allocation state.
        assert store.get_frequency_count(
            campaign_id,
            viewer_id,
            decision_time,
        ) == 1
        assert client.get(decision_key) is None

    finally:
        client.delete(frequency_key, decision_key)

        if original_version is None:
            client.delete(version_key)
        else:
            client.set(version_key, original_version)




# from datetime import datetime, timezone
# from uuid import uuid4

# from redis import Redis

# from app.runtime.allocation_store import AllocationStore, AllocationResult


# def test_allocation_version_fence():
#     # Use our local Redis instance
#     client = Redis(host="localhost", port=6379, decode_responses=True)
#     store = AllocationStore(client)

#     # Use unique identifiers to avoid existing test data
#     test_id = uuid4().hex
#     campaign_id = 999999
#     request_id = f"version-fence-test-{test_id}"
#     viewer_id = f"version-fence-viewer-{test_id}"

#     decision_time = datetime.now(timezone.utc)

#     capacity_key = store._remaining_capacity_key(campaign_id)
#     frequency_key = store._frequency_key(
#         campaign_id, viewer_id, decision_time
#     )
#     decision_key = store._decision_key(request_id)
#     version_key = "rush:index:version"

#     # Save the existing global version; don't change it
#     current_version = client.get(version_key)
#     assert current_version is not None, "Global index version is missing"

#     current_version = int(current_version)

#     try:
#         # Prepare a campaign with 100 units remaining
#         client.set(capacity_key, 100)

#         # Viewer has already seen this campaign once
#         client.set(frequency_key, 1, ex=3600)

#         # Make sure this request has not been processed
#         client.delete(decision_key)

#         # Simulate a worker using an outdated index version
#         stale_version = current_version - 1

#         result = store.try_allocate(
#             campaign_id=campaign_id,
#             request_id=request_id,
#             viewer_id=viewer_id,
#             frequency_cap_per_hour=3,
#             decision_time=decision_time,
#             index_version=stale_version,
#         )

#         # Redis must reject the outdated snapshot
#         assert result == AllocationResult.STALE_INDEX

#         # No capacity should have been consumed
#         assert store.get_remaining_capacity(campaign_id) == 100

#         # Frequency count must remain unchanged
#         assert store.get_frequency_count(
#             campaign_id, viewer_id, decision_time
#         ) == 1

#         # No idempotency key should have been created
#         assert client.get(decision_key) is None

#         print("Lua version fence test passed!")

#     finally:
#         # Remove only the test's dedicated keys
#         client.delete(capacity_key, frequency_key, decision_key)


# if __name__ == "__main__":
#     test_allocation_version_fence()
