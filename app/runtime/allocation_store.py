from redis import Redis
from datetime import datetime, timezone, timedelta
from enum import Enum


class AllocationResult(str, Enum):
    ALLOCATED = "ALLOCATED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    CAPACITY_MISSING = "CAPACITY_MISSING"
    CAPACITY_EXHAUSTED = "CAPACITY_EXHAUSTED"
    FREQUENCY_CAPPED = "FREQUENCY_CAPPED"
    
    
class AllocationStore:
    def __init__(self, client: Redis):
        self.client = client # This stores the Redis connection so that any future method added to AllocationStore can reuse self.client to communicate with Redis.
        
        
    # to name-format the Redis key that stores a campaign's remaining capacity
    def _remaining_capacity_key(self, campaign_id: int) -> str:
        return f"campaign:{campaign_id}:remaining_capacity"
    
    def initialize_new_campaign_capacity(self,campaign_id, capacity) -> bool:
        key=self._remaining_capacity_key(campaign_id)
        result = self.client.set(key,capacity, 
                        # if more than one process calls this function at the same time, then nx=true, will prevent duplicate initialization. 
                        nx=True)
        return bool(result)
    
    def get_remaining_capacity(self, campaign_id: int) -> int | None:
        key = self._remaining_capacity_key(campaign_id)

        value = self.client.get(key)

        if value is None:
            return None

        return int(value)
    
    def _frequency_key(self, campaign_id: int, viewer_id: str, decision_time: datetime) -> str:
        formatted_time = decision_time.strftime("%Y-%m-%d-%H")
        return f"freq::viewer:{viewer_id}::campaign:{campaign_id}::{formatted_time}"
    
    def get_frequency_count(self, campaign_id: int, viewer_id: str, decision_time: datetime) -> int:
        key=self._frequency_key(campaign_id, viewer_id, decision_time)
        value = self.client.get(key)

        if value is None:
            # viewer has not received this campaign in this hour yet
            return 0
        
        return int(value)
    
    def _frequency_ttl_seconds(self,decision_time: datetime) -> int:
        next_hour = decision_time.replace(minute=0,second=0,microsecond=0) + timedelta(hours=1)

        return int((next_hour - decision_time).total_seconds())
    
    def _decision_key(self, request_id: str) -> str:
        return f"decision:{request_id}"
    
    
    
    # We now need a function to check the following the things:
        # 1) Has the request been proessed before this?
        #   - don't consume again
        # 2) Is the caapacity missing?
        #   - Fail closed
        # 3) Is the capacity exhausted (=0)?
        #   - Reject
        # 4) Is the viewer frequency already at cap?
        #   - Reject
        # 
        # If none of the above, then:
            # → decrement capacity
            # → increment viewer frequency
            # → remember request_id
            # → success
    
    
     
        
        
        
        
        
    # #     prepare keys
    # #           ↓
    # # run one atomic Redis script
    # #           ↓
    # # check request duplicate
    # #           ↓
    # # check capacity exists
    # #           ↓
    # # check capacity > 0
    # #           ↓
    # # check viewer frequency
    # #           ↓
    # # decrement capacity
    # #           ↓
    # # increment frequency
    # #           ↓
    # # set TTL if needed
    # #           ↓
    # # remember request
    # #           ↓
    # # translate result code back into Python
    
    def try_allocate(self, campaign_id:int, request_id: str, viewer_id:str, 
                     frequency_cap_per_hour:int, decision_time:datetime )-> AllocationResult:
        frequency_count_key=self._frequency_key(campaign_id, viewer_id, decision_time)
        decision_key=self._decision_key(request_id)
        remaining_capacity_key = self._remaining_capacity_key(campaign_id)
        ttl = self._frequency_ttl_seconds(decision_time)
        
        
        # The following code will be atomic so that another Rush process CANNOT sneak in between those steps. We will therefore be writtinng it in Lua script
        
        # KEYS[1] → capacity key
        # KEYS[2] → frequency key
        # KEYS[3] → decision key
        # ARGV[1] → frequency_cap_per_hour
        # ARGV[2] → ttl
        # ARGV[3] → campaign_id           
        script = """
                -- Filter #1: Checking if the decision:<request_id> exists?
                    if redis.call('EXISTS', KEYS[3]) == 1 then
                        --request already processed
                        return 1
                    end
                    
                -- This is the campaigns remaining capacity
                    local capacity_raw = redis.call('GET', KEYS[1])
                    
                -- Filter #2: Checking if the campaign’s remaining-capacity key is missing?
                    if not capacity_raw then
                        return 2
                    end
                    
                -- Filter #3: Checking if the the remaining capacity already = 0?
                    local capacity = tonumber(capacity_raw)
                    if capacity <= 0 then
                        return 3
                    end
                    
                -- Filter #4: We compare the frequency count to the cap
                    -- to read the current frequency count  
                    local frequency_count_raw = redis.call('GET', KEYS[2])
                    local frequency_count = tonumber(frequency_count_raw or '0')
                    
                    -- to read the frequency cap 
                    local frequency_cap = tonumber(ARGV[1])

                    if frequency_count >= frequency_cap then
                        return 4
                    end
                    
                    -- Reducing capacity
                    redis.call('DECR', KEYS[1])
                    
                    -- Increment frequency
                    redis.call('INCR', KEYS[2])
                    
                    -- Setting the decision key for this campaign request ID
                    redis.call('SET', KEYS[3], ARGV[3])
                    
                    -- If this frequency counter was newly created
                    if not frequency_count_raw then
                        -- Then we add an expiry time (TTL)
                        redis.call('EXPIRE', KEYS[2], ARGV[2]) 
                    end
                    return 0
                """
                
        # Redis’s Python call:
        result = self.client.eval(
                    script,
                    3,
                    remaining_capacity_key,
                    frequency_count_key,
                    decision_key,
                    frequency_cap_per_hour,
                    ttl,
                    campaign_id          
                )
        # the request has already been processed as per the Lua script before this
        if result == 1:
            return AllocationResult.ALREADY_PROCESSED   
        if result == 2:
                return AllocationResult.CAPACITY_MISSING
        if result == 3:
            return AllocationResult.CAPACITY_EXHAUSTED
        if result == 4:
                return AllocationResult.FREQUENCY_CAPPED
        if result==0:
            return AllocationResult.ALLOCATED
    
    # this is a small helper function which will return the campaign ID incase of a repeated (duplicate) request
    def get_existing_decision(self, request_id: str) -> int | None:
        decision_key = self._decision_key(request_id)
        value = self.client.get(decision_key)

        if value is None:
            return None

        return int(value)
        
               
                
        
        
        
        
        
        






    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
   

#     def try_allocate(self, campaign_id: int, viewer_id: str, request_id: str, 
#                      frequency_cap_per_hour: int,decision_time: datetime )-> AllocationResult:
        
#         # Build the Redis keys needed for this allocation attempt.
#         capacity_key = self._remaining_capacity_key(campaign_id)
#         frequency_key = self._frequency_key(campaign_id, viewer_id, decision_time)
#         decision_key = self._decision_key(request_id)
        
#         # Frequency counters should expire when the current hour bucket ends.
#         ttl = self._frequency_ttl_seconds(decision_time)
        
        
#         # This Lua script performs the full allocation check + mutation atomically.
        
#         # Redis executes the whole script without another request interleaving
#         # between the checks and updates.
#         script = """
        
#                  -- Gate 1:
#                  -- If this request_id was already successfully processed,
#                  -- do not consume capacity or frequency again.
#                     if redis.call('EXISTS', KEYS[3]) == 1 then
#                         return 1
#                     end
                    
                    
#                 -- Gate 2:
#                 -- Read the campaign's remaining capacity.
#                     local capacity_raw = redis.call('GET', KEYS[1])
                    
#                 -- Missing capacity is unsafe.
#                 -- We do not recreate it from the configured campaign capacity,
#                 -- because previous allocations may already have happened.
#                     if not capacity_raw then
#                         return 2
#                     end
                    
                
#                     local capacity = tonumber(capacity_raw)


#                 -- Gate 3:
#                 -- Reject if the campaign has no remaining capacity.
#                     if capacity <= 0 then
#                         return 3
#                     end
                    
                    
                    
#                 -- Read this viewer's allocation count for this campaign
#                 -- during the current hourly bucket.
#                     local frequency_raw = redis.call('GET', KEYS[2])
                    
                    
#                 -- Missing frequency key means the viewer has not received
#                 -- this campaign yet during this hour.
#                     local frequency = tonumber(frequency_raw or '0')

#                 -- ARGV[1] contains the campaign's configured hourly frequency cap.
#                     local frequency_cap = tonumber(ARGV[1])


#                 -- Gate 4:
#                 -- Reject if this viewer has already reached the frequency cap.
#                     if frequency >= frequency_cap then
#                         return 4
#                     end


#                 -- All checks passed.
#                 -- Consume one unit of campaign capacity.
#                     redis.call('DECR', KEYS[1])
                    
#                 -- Count this allocation against the viewer's hourly frequency cap.
#                     redis.call('INCR', KEYS[2])
                    
                
#                 -- If this is the first frequency entry for this hour,
#                 -- attach a TTL so Redis removes it when the hour bucket ends.
#                 --
#                 -- We only set the TTL when the key is first created.
#                 -- Resetting it on every allocation would incorrectly extend the bucket.
#                     if not frequency_raw then
#                         redis.call('EXPIRE', KEYS[2], ARGV[2])
#                     end
                    
                    
#                 -- Remember that this request_id has already produced an allocation.
#                 -- ARGV[3] stores the campaign ID that won this request.
#                 --
#                 -- This prevents retries of the same logical request
#                 -- from consuming capacity twice.
#                     redis.call('SET', KEYS[3], ARGV[3])
                    
#                 -- Successful allocation.
#                     return 0
#                 """
#         # Lua receives Redis keys separately from ordinary values:
#         # KEYS[1] → capacity_key
#         # KEYS[2] → frequency_key
#         # KEYS[3] → decision_key

#         # ARGV[1] → frequency_cap_per_hour
#         # ARGV[2] -> frequency TTL in seconds
#         # ARGV[3] -> campaign_id
            
#         result = self.client.eval(
#                                     script,
#                                     3,
#                                     capacity_key,
#                                     frequency_key,
#                                     decision_key,
#                                     frequency_cap_per_hour,  # ARGV[1]
#                                     ttl,                     # ARGV[2]
#                                     campaign_id,             # ARGV[3]
#                                 )
        
        
#         # Translate the Lua return code into a meaningful Rush result.
#         if result == 1:
#             return AllocationResult.ALREADY_PROCESSED
        
#         if result == 2:
#             return AllocationResult.CAPACITY_MISSING
        
#         if result == 3:
#             return AllocationResult.CAPACITY_EXHAUSTED
        
#         if result == 4:
#             return AllocationResult.FREQUENCY_CAPPED
        
#         if result == 0:
#             return AllocationResult.ALLOCATED
        
        
                            
                                    
                            
            
            
        
        
        
        
        
        
    
    

        