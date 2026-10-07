from app.runtime.redis_client import redis_client
from app.runtime.allocation_store import AllocationStore, AllocationResult
from datetime import datetime, timezone, timedelta

from concurrent.futures import ThreadPoolExecutor



def test_remaining_capacity_key():
    store = AllocationStore(redis_client)

    key = store._remaining_capacity_key(42)

    assert key == "campaign:42:remaining_capacity"
    # redis_client.delete(key)
    redis_client.delete(key)
    
def test_initialize_new_campaign_capacity():
    store = AllocationStore(redis_client)

    campaign_id = 999999
    key = store._remaining_capacity_key(campaign_id)
    redis_client.delete(key)
    result=store.initialize_new_campaign_capacity(campaign_id,5)
    value=redis_client.get(key)
    assert result is True
    assert value=="5"
    
    second_result = store.initialize_new_campaign_capacity( campaign_id,100,)
    
    second_value = redis_client.get(key)
    # assert second_value=="100"
    assert second_result is False
    assert second_value == "5"
    redis_client.delete(key)
    
def test_get_remaining_capacity_returns_integer():
    store = AllocationStore(redis_client)

    campaign_id = 999998
    key = store._remaining_capacity_key(campaign_id)

    redis_client.delete(key)
    redis_client.set(key, 7)
    remaining = store.get_remaining_capacity(campaign_id)
    assert remaining == 7
    redis_client.delete(key)
    
def test_get_remaining_capacity_returns_none_when_missing():
    store = AllocationStore(redis_client)

    campaign_id = 999997
    key = store._remaining_capacity_key(campaign_id)

    redis_client.delete(key)

    remaining = store.get_remaining_capacity(campaign_id)

    assert remaining is None

def test_frequency_key_uses_hour_bucket():
    store = AllocationStore(redis_client)

    decision_time = datetime(
        2026, 10, 5, 20, 37, 12,
        tzinfo=timezone.utc,
    )

    key = store._frequency_key(
        campaign_id=42,
        viewer_id="123",
        decision_time=decision_time,
    )

    assert key == "freq::viewer:123::campaign:42::2026-10-05-20"
    
from datetime import datetime, timezone


def test_get_frequency_count_returns_zero_when_missing():
    store = AllocationStore(redis_client)

    decision_time = datetime(
        2026, 10, 5, 20, 37,
        tzinfo=timezone.utc,
    )

    key = store._frequency_key(
        campaign_id=42,
        viewer_id="viewer-123",
        decision_time=decision_time,
    )

    redis_client.delete(key)

    count = store.get_frequency_count(
        campaign_id=42,
        viewer_id="viewer-123",
        decision_time=decision_time,
    )

    assert count == 0


def test_get_frequency_count_returns_existing_count():
    store = AllocationStore(redis_client)

    decision_time = datetime(
        2026, 10, 5, 20, 37,
        tzinfo=timezone.utc,
    )

    key = store._frequency_key(
        campaign_id=42,
        viewer_id="viewer-123",
        decision_time=decision_time,
    )

    redis_client.delete(key)
    redis_client.set(key, 2)

    count = store.get_frequency_count(
        campaign_id=42,
        viewer_id="viewer-123",
        decision_time=decision_time,
    )

    assert count == 2

    redis_client.delete(key)
    


def test_frequency_ttl_seconds():
    store = AllocationStore(redis_client)

    decision_time = datetime(
        2026, 10, 5, 20, 37, 12,
        tzinfo=timezone.utc,
    )

    ttl = store._frequency_ttl_seconds(decision_time)

    assert ttl == 1368
    
def test_successful_allocation():
    store = AllocationStore(redis_client)

    campaign_id = 100001
    viewer_id = "viewer-success"
    request_id = "request-success"

    decision_time = datetime(
        2026, 10, 5, 21, 30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )
    decision_key = store._decision_key(request_id)

    # Start this test from a completely clean Redis state.
    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

    # Campaign begins with 5 units available.
    store.initialize_new_campaign_capacity(
        campaign_id,
        5,
    )
    
# idempotency test: a retry of the same logical request must not double-charge Rush's capacity or frequency counters.
def test_successful_allocation():
    store = AllocationStore(redis_client)

    campaign_id = 100001
    viewer_id = "viewer-success"
    request_id = "request-success"

    decision_time = datetime(
        2026,
        10,
        5,
        21,
        30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )
    decision_key = store._decision_key(request_id)

    # Start with clean Redis state.
    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

    # Campaign starts with 5 remaining allocations.
    store.initialize_new_campaign_capacity(
        campaign_id,
        5,
    )

    result = store.try_allocate(
        campaign_id=campaign_id,
        viewer_id=viewer_id,
        request_id=request_id,
        frequency_cap_per_hour=3,
        decision_time=decision_time,
    )

    # The allocation should succeed.
    assert result == AllocationResult.ALLOCATED

    # One unit of capacity should have been consumed.
    assert redis_client.get(capacity_key) == "4"

    # Viewer should now have seen this campaign once this hour.
    assert redis_client.get(frequency_key) == "1"

    # The request should remember which campaign was allocated.
    assert redis_client.get(decision_key) == str(campaign_id)

    # Cleanup.
    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )
    
def test_duplicate_request_does_not_allocate_twice():
    store = AllocationStore(redis_client)

    campaign_id = 100002
    viewer_id = "viewer-duplicate"
    request_id = "request-duplicate"

    decision_time = datetime(
        2026,
        10,
        5,
        21,
        30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )
    decision_key = store._decision_key(request_id)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

    store.initialize_new_campaign_capacity(
        campaign_id,
        5,
    )

    first_result = store.try_allocate(
        campaign_id=campaign_id,
        viewer_id=viewer_id,
        request_id=request_id,
        frequency_cap_per_hour=3,
        decision_time=decision_time,
    )

    second_result = store.try_allocate(
        campaign_id=campaign_id,
        viewer_id=viewer_id,
        request_id=request_id,
        frequency_cap_per_hour=3,
        decision_time=decision_time,
    )

    assert first_result == AllocationResult.ALLOCATED
    assert second_result == AllocationResult.ALREADY_PROCESSED

    # Only the first request should consume state.
    assert redis_client.get(capacity_key) == "4"
    assert redis_client.get(frequency_key) == "1"
    assert redis_client.get(decision_key) == str(campaign_id)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

    
def test_missing_capacity_fails_closed():
    store = AllocationStore(redis_client)

    campaign_id = 100003
    viewer_id = "viewer-missing-capacity"
    request_id = "request-missing-capacity"

    decision_time = datetime(
        2026,
        10,
        5,
        21,
        30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )
    decision_key = store._decision_key(request_id)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

    result = store.try_allocate(
        campaign_id=campaign_id,
        viewer_id=viewer_id,
        request_id=request_id,
        frequency_cap_per_hour=3,
        decision_time=decision_time,
    )

    assert result == AllocationResult.CAPACITY_MISSING

    assert redis_client.get(capacity_key) is None
    assert redis_client.get(frequency_key) is None
    assert redis_client.get(decision_key) is None    
    
def test_exhausted_capacity_rejects_allocation():
    store = AllocationStore(redis_client)

    campaign_id = 100004
    viewer_id = "viewer-exhausted"
    request_id = "request-exhausted"

    decision_time = datetime(
        2026,
        10,
        5,
        21,
        30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )
    decision_key = store._decision_key(request_id)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

    # Campaign has no capacity left.
    store.initialize_new_campaign_capacity(
        campaign_id,
        0,
    )

    result = store.try_allocate(
        campaign_id=campaign_id,
        viewer_id=viewer_id,
        request_id=request_id,
        frequency_cap_per_hour=3,
        decision_time=decision_time,
    )

    assert result == AllocationResult.CAPACITY_EXHAUSTED

    # Capacity stays at 0.
    assert redis_client.get(capacity_key) == "0"

    # No frequency usage or decision should be recorded.
    assert redis_client.get(frequency_key) is None
    assert redis_client.get(decision_key) is None

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )
    
    
# atomicity property: failing the frequency check doesn't accidentally consume capacity.
def test_frequency_cap_rejects_allocation():
    store = AllocationStore(redis_client)

    campaign_id = 100005
    viewer_id = "viewer-frequency-capped"
    request_id = "request-frequency-capped"

    decision_time = datetime(
        2026,
        10,
        5,
        21,
        30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )
    decision_key = store._decision_key(request_id)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

    store.initialize_new_campaign_capacity(
        campaign_id,
        5,
    )

    # Viewer has already hit the hourly cap.
    redis_client.set(
        frequency_key,
        3,
    )

    result = store.try_allocate(
        campaign_id=campaign_id,
        viewer_id=viewer_id,
        request_id=request_id,
        frequency_cap_per_hour=3,
        decision_time=decision_time,
    )

    assert result == AllocationResult.FREQUENCY_CAPPED

    # Rejected allocation must not consume campaign capacity.
    assert redis_client.get(capacity_key) == "5"

    # Frequency remains unchanged.
    assert redis_client.get(frequency_key) == "3"

    # The request was not successfully allocated.
    assert redis_client.get(decision_key) is None

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )
    
    
def test_frequency_key_gets_ttl_on_first_allocation():
    store = AllocationStore(redis_client)

    campaign_id = 100006
    viewer_id = "viewer-ttl"
    request_id = "request-ttl"

    decision_time = datetime(
        2026,
        10,
        5,
        21,
        30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )
    decision_key = store._decision_key(request_id)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

    store.initialize_new_campaign_capacity(
        campaign_id,
        5,
    )

    result = store.try_allocate(
        campaign_id=campaign_id,
        viewer_id=viewer_id,
        request_id=request_id,
        frequency_cap_per_hour=3,
        decision_time=decision_time,
    )

    assert result == AllocationResult.ALLOCATED

    ttl = redis_client.ttl(frequency_key)

    assert ttl > 0
    assert ttl <= store._frequency_ttl_seconds(decision_time)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

def test_existing_frequency_key_does_not_reset_ttl():
    store = AllocationStore(redis_client)

    campaign_id = 100007
    viewer_id = "viewer-existing-ttl"
    request_id = "request-existing-ttl"

    decision_time = datetime(
        2026,
        10,
        5,
        21,
        30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )
    decision_key = store._decision_key(request_id)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )

    store.initialize_new_campaign_capacity(
        campaign_id,
        5,
    )

    # Viewer has already received this campaign once.
    # Give the existing frequency bucket a short TTL.
    redis_client.set(
        frequency_key,
        1,
        ex=60,
    )

    result = store.try_allocate(
        campaign_id=campaign_id,
        viewer_id=viewer_id,
        request_id=request_id,
        frequency_cap_per_hour=3,
        decision_time=decision_time,
    )

    assert result == AllocationResult.ALLOCATED

    # Frequency should increase normally.
    assert redis_client.get(frequency_key) == "2"

    # Existing TTL should remain roughly 60 seconds,
    # rather than being reset to the full hourly TTL.
    ttl = redis_client.ttl(frequency_key)

    assert ttl > 0
    assert ttl <= 60

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key,
    )
    
    
def test_concurrent_allocations_do_not_overallocate():
    store = AllocationStore(redis_client)

    campaign_id = 100008
    viewer_id = "viewer-concurrency"

    decision_time = datetime(
        2026,
        10,
        5,
        21,
        30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )

    request_1 = "request-concurrent-1"
    request_2 = "request-concurrent-2"

    decision_key_1 = store._decision_key(request_1)
    decision_key_2 = store._decision_key(request_2)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key_1,
        decision_key_2,
    )

    # Only ONE allocation is available.
    store.initialize_new_campaign_capacity(
        campaign_id,
        1,
    )

    def allocate(request_id: str):
        return store.try_allocate(
            campaign_id=campaign_id,
            viewer_id=viewer_id,
            request_id=request_id,
            frequency_cap_per_hour=10,
            decision_time=decision_time,
        )

    # Fire both allocation attempts concurrently.
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                allocate,
                [request_1, request_2],
            )
        )

    # Exactly one request must win.
    assert results.count(AllocationResult.ALLOCATED) == 1
    assert results.count(AllocationResult.CAPACITY_EXHAUSTED) == 1

    # Capacity must stop at zero — never negative.
    assert redis_client.get(capacity_key) == "0"

    # Only the winning allocation should increment frequency.
    assert redis_client.get(frequency_key) == "1"

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key_1,
        decision_key_2,
    )
    
    
def test_concurrent_allocations_do_not_exceed_frequency_cap():
    store = AllocationStore(redis_client)

    campaign_id = 100009
    viewer_id = "viewer-frequency-concurrency"

    decision_time = datetime(
        2026,
        10,
        5,
        21,
        30,
        tzinfo=timezone.utc,
    )

    capacity_key = store._remaining_capacity_key(campaign_id)
    frequency_key = store._frequency_key(
        campaign_id,
        viewer_id,
        decision_time,
    )

    request_1 = "request-frequency-concurrent-1"
    request_2 = "request-frequency-concurrent-2"

    decision_key_1 = store._decision_key(request_1)
    decision_key_2 = store._decision_key(request_2)

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key_1,
        decision_key_2,
    )

    store.initialize_new_campaign_capacity(
        campaign_id,
        10,
    )

    def allocate(request_id: str):
        return store.try_allocate(
            campaign_id=campaign_id,
            viewer_id=viewer_id,
            request_id=request_id,
            frequency_cap_per_hour=1,
            decision_time=decision_time,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                allocate,
                [request_1, request_2],
            )
        )

    assert results.count(AllocationResult.ALLOCATED) == 1
    assert results.count(AllocationResult.FREQUENCY_CAPPED) == 1

    # Only one request consumed capacity.
    assert redis_client.get(capacity_key) == "9"

    # Frequency cap of 1 was never exceeded.
    assert redis_client.get(frequency_key) == "1"

    redis_client.delete(
        capacity_key,
        frequency_key,
        decision_key_1,
        decision_key_2,
    )
    
def test_successful_decision_key_has_ttl():
    campaign_id = 99901
    request_id = "decision-ttl-test"
    viewer_id = "viewer-ttl-test"
    decision_time = datetime.now(timezone.utc)

    store = AllocationStore(redis_client)

    capacity_key = store._remaining_capacity_key(campaign_id)
    decision_key = store._decision_key(request_id)

    redis_client.delete(capacity_key, decision_key)

    store.initialize_new_campaign_capacity(
        campaign_id=campaign_id,
        capacity=10,
    )

    result = store.try_allocate(
        campaign_id=campaign_id,
        request_id=request_id,
        viewer_id=viewer_id,
        frequency_cap_per_hour=3,
        decision_time=decision_time,
    )

    assert result == AllocationResult.ALLOCATED

    ttl = redis_client.ttl(decision_key)

    assert 0 < ttl <= 300

