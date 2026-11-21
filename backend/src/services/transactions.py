"""数据库写事务重试工具。

SQLite 单库文件在并发写入时可能抛 database is locked（busy_timeout 之后）；
PostgreSQL 对应 40001/40P01（序列化失败/死锁）。这类冲突都是安全可重试的
瞬态错误：重试时预算行的最新余额会被重新读取，保证并发申请绝不透支。
"""
import time

from sqlalchemy.exc import DBAPIError

_RETRYABLE_SQLITE = ('database is locked', 'database table is locked')


def is_retryable(exc):
    text = str(exc).lower()
    if any(marker in text for marker in _RETRYABLE_SQLITE):
        return True
    if isinstance(exc, DBAPIError):
        orig = getattr(exc, 'orig', None)
        code = getattr(orig, 'sqlstate', None) or getattr(orig, 'pgcode', None)
        if code in ('40001', '40P01'):
            return True
    return False


def with_write_retry(db, fn, attempts=25, base_delay=0.02):
    """在事务内执行 fn；遇到锁冲突回滚后指数退避重试。"""
    last_exc = None
    for i in range(attempts):
        try:
            result = fn()
            db.commit()
            return result
        except Exception as exc:
            db.rollback()
            if is_retryable(exc) and i < attempts - 1:
                last_exc = exc
                time.sleep(min(base_delay * (2 ** i), 0.5))
                continue
            raise
    raise last_exc
