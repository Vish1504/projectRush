# /Users/vish1504/projectRush/app/runtime/redis_client.py

import redis

# Redis server is running on this computer, on port 6379
REDIS_URL = "redis://localhost:6379/0"

# we can now create a python object that Rush can use to talk to Redis
redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True, # this will make returned text as regular Python strings
)

# actual connection
redis_client.ping()


