"""任务通知队列。

生产环境使用 Redis（REDIS_URL 注入）；本地无 Redis 时降级到 SQLite 队列表，
worker 仍由数据库扫描兜底，保证「通知丢失也能恢复」。
队列只负责唤醒；任务的权威状态始终在业务表中。
"""
import json
import os
import time

from sqlalchemy import String, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base, IS_SQLITE, SessionLocal

REDIS_URL=os.environ.get('REDIS_URL')


class TaskQueueRow(Base):
    __tablename__='task_queue'
    id:Mapped[int]=mapped_column(Integer, primary_key=True)
    kind:Mapped[str]=mapped_column(String(20))
    payload:Mapped[str]=mapped_column(Text)
    created_at:Mapped[float]=mapped_column(default=time.time)


class _RedisQueue:
    def __init__(self, url):
        import redis
        self._redis=redis.Redis.from_url(url)

    def push(self, key, message):
        self._redis.lpush(key, json.dumps(message))

    def pop(self, key, timeout=2):
        item=self._redis.brpop(key, timeout=timeout)
        return json.loads(item[1]) if item else None

    def ping(self):
        self._redis.ping()


class _SqliteQueue:
    """基于数据库表的最小通知队列；brpop 语义退化为轮询。"""

    def push(self, key, message):
        kind=key.split(':')[-1]
        with SessionLocal() as db:
            db.add(TaskQueueRow(kind=kind, payload=json.dumps(message)))
            db.commit()

    def pop(self, key, timeout=2):
        kind=key.split(':')[-1]
        deadline=time.time()+timeout
        while time.time() < deadline:
            with SessionLocal() as db:
                row=db.query(TaskQueueRow).filter_by(kind=kind).order_by(TaskQueueRow.id).first()
                if row:
                    payload=json.loads(row.payload)
                    db.delete(row)
                    db.commit()
                    return payload
            time.sleep(0.25)
        return None

    def ping(self):
        with SessionLocal() as db:
            db.connection()


if REDIS_URL:
    try:
        import redis as _redis
        _redis.Redis.from_url(REDIS_URL).ping()
        queue=_RedisQueue(REDIS_URL)
    except Exception:
        queue=_SqliteQueue()
else:
    queue=_SqliteQueue()

# 队列表只在 SQLite 降级模式下需要；create_all 对其幂等
if IS_SQLITE:
    from src.config.database import engine
    Base.metadata.create_all(engine)
