# /Users/vish1504/projectRush/app/runtime/redis_client.py

import redis
import os

# This makes the redis connnections configurable
REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)

# we can now create a python object that Rush can use to talk to Redis
redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True, # this will make returned text as regular Python strings
)

# actual connection
redis_client.ping()


