# /Users/vish1504/projectRush/app/runtime/allocation_store.py

from redis import Redis
from datetime import datetime, timedelta
from enum import Enum


class AllocationResult(str, Enum):
    ALLOCATED = "ALLOCATED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    FREQUENCY_CAPPED = "FREQUENCY_CAPPED"
    STALE_INDEX = "STALE_INDEX"


DECISION_TTL_SECONDS = 300


class AllocationStore:

    def __init__(self, client: Redis):
        # Store the Redis connection so all methods can reuse it
        self.client = client


    # Creates the Redis key used to track how many times
    # this viewer has received this campaign in the current hour
    def _frequency_key(
        self,
        campaign_id: int,
        viewer_id: str,
        decision_time: datetime,
    ) -> str:

        formatted_time = decision_time.strftime("%Y-%m-%d-%H")

        return (
            f"freq::viewer:{viewer_id}"
            f"::campaign:{campaign_id}"
            f"::{formatted_time}"
        )


    # Returns the viewer's current frequency count for this campaign
    def get_frequency_count(
        self,
        campaign_id: int,
        viewer_id: str,
        decision_time: datetime,
    ) -> int:

        key = self._frequency_key(
            campaign_id,
            viewer_id,
            decision_time,
        )

        value = self.client.get(key)

        # Viewer has not received this campaign in this hour yet
        if value is None:
            return 0

        return int(value)


    # Calculates how many seconds remain until the current hour ends
    def _frequency_ttl_seconds(
        self,
        decision_time: datetime,
    ) -> int:

        next_hour = (
            decision_time.replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            + timedelta(hours=1)
        )

        return int(
            (next_hour - decision_time).total_seconds()
        )


    # Redis key that remembers which campaign was allocated
    # for this request ID
    def _decision_key(
        self,
        request_id: str,
    ) -> str:

        return f"decision:{request_id}"


    # Performs the shared Redis guards that must remain atomic:
    #
    # 1. Has this exact request already been processed?
    # 2. Is this worker using the current index version?
    # 3. Has this viewer already hit the campaign frequency cap?
    #
    # Campaign capacity is NOT handled here anymore.
    # Capacity is now handled by CampaignLeaseManager.
    def try_allocate(
        self,
        campaign_id: int,
        request_id: str,
        viewer_id: str,
        frequency_cap_per_hour: int,
        decision_time: datetime,
        index_version: int,
    ) -> AllocationResult:

        # KEYS[1] → frequency key
        frequency_count_key = self._frequency_key(
            campaign_id,
            viewer_id,
            decision_time,
        )

        # KEYS[2] → decision/idempotency key
        decision_key = self._decision_key(
            request_id
        )

        # Frequency counter expires when the current hour ends
        ttl = self._frequency_ttl_seconds(
            decision_time
        )

        # Redis Lua keeps all shared checks + mutations atomic
        #
        # KEYS[1] → frequency key
        # KEYS[2] → decision key
        # KEYS[3] → global index version key
        #
        # ARGV[1] → frequency_cap_per_hour
        # ARGV[2] → frequency TTL
        # ARGV[3] → campaign_id
        # ARGV[4] → decision TTL
        # ARGV[5] → index version used by this worker

        script = """
            -- Filter #1:
            -- Has this exact request already successfully allocated something?
            if redis.call('EXISTS', KEYS[2]) == 1 then
                return 1
            end


            -- Filter #2:
            -- Is this worker using the current inverted-index version?
            local current_version = redis.call('GET', KEYS[3])

            -- Missing global version means we cannot trust the index
            if not current_version then
                return 3
            end

            -- Worker's snapshot is stale
            if tonumber(current_version) ~= tonumber(ARGV[5]) then
                return 3
            end


            -- Filter #3:
            -- Has this viewer already hit the frequency cap?
            local frequency_count_raw = redis.call('GET', KEYS[1])
            local frequency_count = tonumber(
                frequency_count_raw or '0'
            )

            local frequency_cap = tonumber(ARGV[1])

            if frequency_count >= frequency_cap then
                return 2
            end


            -- All shared guards passed.
            -- Record the allocation atomically.

            redis.call('INCR', KEYS[1])

            redis.call(
                'SET',
                KEYS[2],
                ARGV[3],
                'EX',
                ARGV[4]
            )

            -- If this frequency counter was just created,
            -- make it expire at the end of the current hour
            if not frequency_count_raw then
                redis.call(
                    'EXPIRE',
                    KEYS[1],
                    ARGV[2]
                )
            end

            return 0
        """


        result = self.client.eval(
            script,
            3,                              # Number of Redis keys

            frequency_count_key,            # KEYS[1]
            decision_key,                   # KEYS[2]
            "rush:index:version",           # KEYS[3]

            frequency_cap_per_hour,         # ARGV[1]
            ttl,                            # ARGV[2]
            campaign_id,                    # ARGV[3]
            DECISION_TTL_SECONDS,           # ARGV[4]
            index_version,                  # ARGV[5]
        )


        # Request was already allocated previously
        if result == 1:
            return AllocationResult.ALREADY_PROCESSED

        # Viewer has already reached this campaign's frequency cap
        if result == 2:
            return AllocationResult.FREQUENCY_CAPPED

        # Worker used an outdated/missing index version
        if result == 3:
            return AllocationResult.STALE_INDEX

        # Allocation succeeded
        if result == 0:
            return AllocationResult.ALLOCATED

        # Lua should never return anything else
        raise RuntimeError(
            f"Unexpected Redis allocation result: {result}"
        )


    # Returns the campaign already allocated for a duplicate request
    def get_existing_decision(
        self,
        request_id: str,
    ) -> int | None:

        decision_key = self._decision_key(
            request_id
        )

        value = self.client.get(
            decision_key
        )

        if value is None:
            return None

        return int(value)