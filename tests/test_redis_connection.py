from app.runtime.redis_client import redis_client


def test_redis_connection():
    assert redis_client.ping() is True