# /Users/vish1504/projectRush/app/runtime/allocation_store.py
import json
from redis import Redis
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass


class AllocationResult(str, Enum):
    ALLOCATED = "ALLOCATED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    FREQUENCY_CAPPED = "FREQUENCY_CAPPED"
    STALE_INDEX = "STALE_INDEX"

# Data Redis needs for one campaign while committing a pod.
@dataclass(frozen=True)
class PodCommitCandidate:
    campaign_id: int
    duration_seconds: int
    frequency_cap_per_hour: int
    
# Final Redis result for one campaign in the pod.
class PodSlotCommitStatus(str, Enum):
    ALLOCATED = "ALLOCATED"
    FREQUENCY_CAPPED = "FREQUENCY_CAPPED"


# Final Redis result for the complete pod.
class PodCommitStatus(str, Enum):
    COMMITTED = "COMMITTED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    STALE_INDEX = "STALE_INDEX"


# Stores Redis's final decision for one proposed campaign.
@dataclass(frozen=True)
class PodCommitSlot:
    campaign_id: int
    duration_seconds: int
    status: PodSlotCommitStatus


# Stores the result of the complete pod commit.
@dataclass(frozen=True)
class PodCommitResult:
    status: PodCommitStatus
    slots: tuple[PodCommitSlot, ...]

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
    
    # Redis key that is used to track how many allocations this campaign has received.
    def _pacing_key(self, campaign_id: int) -> str:
        return f"pacing:campaign:{campaign_id}:committed"
    
    # To get the committed allocation counts for all campaigns in one Redis call.
    def get_committed_allocation_counts(self,campaign_ids: list[int], ) -> dict[int, int]:
        if not campaign_ids:
            return {}

        keys = []
        
        # To add the pacing keys for each campaign
        for campaign_id in campaign_ids:
            key = self._pacing_key(campaign_id)
            keys.append(key)
            
        # Redis returns the values for all those keys in the same order in a list form
        values = self.client.mget(keys)

        allocation_counts = {}
        
        # Pair each campaign back with its Redis count.
        for campaign_id, value in zip(campaign_ids, values):
            if value is None:
                # None means that the key does not exist yet
                count = 0
            else:
                count = int(value)

            allocation_counts[campaign_id] = count

        return allocation_counts
    
    # Stores the final Redis result for the whole pod request to help avoid idempotency
    def _pod_decision_key(
        self,
        request_id: str,
    ) -> str:
        return f"decision:pod:{request_id}"
    
    # Returns the pod already committed for this request.
    def get_existing_pod_decision(
        self,
        request_id: str,
        ) -> tuple[PodCommitSlot, ...] | None:
        
        decision_key = self._pod_decision_key(
            request_id
        )
        payload = self.client.get(
            decision_key
        )
        if payload is None:
            return None
        return self._parse_pod_slots(
            payload
    )
    
    # Converts the stored Redis pod result (JSON) back into Python objects.
    def _parse_pod_slots(
        self,
        payload: str,
    ) -> tuple[PodCommitSlot, ...]:

        data = json.loads(payload)

        slots = []

        for item in data["slots"]:
            slot = PodCommitSlot(
                campaign_id=int(item["campaign_id"]),
                duration_seconds=int(item["duration_seconds"]),
                status=PodSlotCommitStatus(item["status"]),
            )

            slots.append(slot)

        return tuple(slots)
    
    # Atomically checks and commits every commercial campaign in the proposed pod.
    def commit_pod(
        self,
        candidates: list[PodCommitCandidate],
        request_id: str,
        viewer_id: str,
        decision_time: datetime,
        index_version: int,
    ) -> PodCommitResult:

        decision_key = self._pod_decision_key(
            request_id
        )

        frequency_ttl = self._frequency_ttl_seconds(
            decision_time
        )

        # These keys belong to the whole request.
        keys = [
            decision_key,
            "rush:index:version",
        ]

        # These arguments belong to the whole request.
        args = [
            index_version,
            DECISION_TTL_SECONDS,
            frequency_ttl,
            len(candidates),
        ]

        # Add the Redis keys and data needed for each campaign.
        for candidate in candidates:

            frequency_key = self._frequency_key(
                candidate.campaign_id,
                viewer_id,
                decision_time,
            )

            pacing_key = self._pacing_key(
                candidate.campaign_id
            )

            keys.append(frequency_key)
            keys.append(pacing_key)

            args.extend([
                candidate.campaign_id,
                candidate.duration_seconds,
                candidate.frequency_cap_per_hour,
            ])

        script = """
                    -- Return the stored result if this request was already committed.
                    local existing = redis.call('GET', KEYS[1])

                    if existing then
                        return {1, existing}
                    end


                    -- Reject the whole pod if this worker used a stale index.
                    local current_version = redis.call('GET', KEYS[2])

                    if not current_version then
                        return {2, ''}
                    end

                    if tonumber(current_version) ~= tonumber(ARGV[1]) then
                        return {2, ''}
                    end


                    local decision_ttl = tonumber(ARGV[2])
                    local frequency_ttl = tonumber(ARGV[3])
                    local slot_count = tonumber(ARGV[4])

                    local result_slots = {}


                    for i = 1, slot_count do

                        -- Each campaign has one frequency key and one pacing key.
                        local frequency_key_index = 3 + ((i - 1) * 2)
                        local pacing_key_index = frequency_key_index + 1

                        local frequency_key = KEYS[frequency_key_index]
                        local pacing_key = KEYS[pacing_key_index]


                        -- Each campaign has its ID, duration and frequency cap.
                        local argument_index = 5 + ((i - 1) * 3)

                        local campaign_id = tonumber(ARGV[argument_index])
                        local duration_seconds = tonumber(ARGV[argument_index + 1])
                        local frequency_cap = tonumber(ARGV[argument_index + 2])


                        local frequency_count_raw = redis.call(
                            'GET',
                            frequency_key
                        )

                        local frequency_count = tonumber(
                            frequency_count_raw or '0'
                        )


                        -- Reject only this campaign if the viewer has hit its cap.
                        if frequency_count >= frequency_cap then

                            table.insert(
                                result_slots,
                                {
                                    campaign_id = campaign_id,
                                    duration_seconds = duration_seconds,
                                    status = 'FREQUENCY_CAPPED'
                                }
                            )

                        else

                            -- This commercial allocation is now committed.
                            redis.call(
                                'INCR',
                                frequency_key
                            )

                            -- The frequency count only matters for the current hour.
                            if not frequency_count_raw then
                                redis.call(
                                    'EXPIRE',
                                    frequency_key,
                                    frequency_ttl
                                )
                            end

                            -- Track successful allocations for pacing.
                            redis.call(
                                'INCR',
                                pacing_key
                            )

                            table.insert(
                                result_slots,
                                {
                                    campaign_id = campaign_id,
                                    duration_seconds = duration_seconds,
                                    status = 'ALLOCATED'
                                }
                            )

                        end
                    end


                    local payload = cjson.encode({
                        slots = result_slots
                    })


                    -- Save the complete result before returning it.
                    redis.call(
                        'SET',
                        KEYS[1],
                        payload,
                        'EX',
                        decision_ttl
                    )


                    return {0, payload}
            """

        result = self.client.eval(
            script,
            len(keys),
            *keys,
            *args,
        )

        result_code = int(result[0])
        payload = result[1]

        # 2 means the worker's index was stale.
        if result_code == 2:
            return PodCommitResult(
                status=PodCommitStatus.STALE_INDEX,
                slots=(),
            )

        slots = self._parse_pod_slots(
            payload
        )

        # 1 means Redis had already committed this request.
        if result_code == 1:
            return PodCommitResult(
                status=PodCommitStatus.ALREADY_PROCESSED,
                slots=slots,
            )

        # 0 means this call successfully committed the pod.
        if result_code == 0:
            return PodCommitResult(
                status=PodCommitStatus.COMMITTED,
                slots=slots,
            )

        raise RuntimeError(
            f"Unexpected Redis pod commit result: {result}"
        )
                    

                        
        
