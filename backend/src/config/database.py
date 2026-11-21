import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL=os.environ.get('DATABASE_URL','sqlite:///./tutor_effect.db')
IS_SQLITE=DATABASE_URL.startswith('sqlite')

# 生产使用 PostgreSQL（docker-compose 注入 postgresql+psycopg://...）；
# 本地无数据库时回退 SQLite，仅用于开发与自动化验证。
connect_args={'check_same_thread':False} if IS_SQLITE else {}
engine=create_engine(DATABASE_URL,pool_pre_ping=True,connect_args=connect_args)

if IS_SQLITE:
    # WAL + 外键约束，让多线程 worker/API 并发访问更接近 Postgres 语义
    @event.listens_for(engine,'connect')
    def _sqlite_pragmas(dbapi_conn,_):
        cur=dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA synchronous=NORMAL')
        cur.execute('PRAGMA busy_timeout=15000')
        cur.execute('PRAGMA foreign_keys=ON')
        cur.close()

SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)

class Base(DeclarativeBase): pass

def lock_for_update(stmt, skip_locked=False):
    """行锁子句：PostgreSQL 使用 FOR UPDATE [SKIP LOCKED]；
    SQLite 依赖单写者锁 + busy_timeout 串行化，忽略该子句。"""
    if IS_SQLITE:
        return stmt
    return stmt.with_for_update(skip_locked=skip_locked)

def get_db():
    with SessionLocal() as db: yield db
