import os,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from src.config.database import Base,engine,SessionLocal
from src.models import entities
from src.routes import auth,students,plans,reports,shares
from src.middlewares.errors import ErrorMiddleware
from src.middlewares.audit import AuditMiddleware
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    if os.getenv('SEED_DEMO','true').lower()=='true':
        try:
            from scripts.seed_demo import seed
            seed()
        except Exception: logging.exception('seed failed; server remains available')
    yield
app=FastAPI(title='课程辅导效果检验',lifespan=lifespan)
app.add_middleware(AuditMiddleware)
app.add_middleware(ErrorMiddleware)
for router in [auth.router,students.router,plans.router,reports.router,shares.router]: app.include_router(router)
@app.get('/health')
def health():
    with SessionLocal() as db: db.execute(text('select 1'))
    from src.services.queue_bus import queue
    queue.ping()
    return {'status':'healthy'}
