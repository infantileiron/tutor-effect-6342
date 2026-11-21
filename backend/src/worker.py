import time,traceback
from pathlib import Path
from sqlalchemy import select
from src.config.database import SessionLocal, lock_for_update
from src.models.entities import Report,Plan,Student,ShareRequest
from src.services.queue_bus import queue
from src.services.statistics import welch
from src.services import sharing
print('tutor effect worker started',flush=True)


def process_report(db, task_id):
    task=db.scalar(lock_for_update(select(Report).where(Report.id==task_id, Report.status.in_(['queued','running'])), skip_locked=True))
    if not task:
        return
    task.status='running';db.commit()
    try:
        plan=db.get(Plan,task.plan_id)
        task.result=welch(db.scalars(select(Student).order_by(Student.code)).all(),plan)
        task.status='completed'
    except Exception as e:
        task.error=str(e);task.status='failed'
    db.commit()
    print(f'report={task.id} status={task.status}',flush=True)


def process_share(db, share_id):
    try:
        item, charged = sharing.process_share(db, share_id)
        if item is not None:
            print(f'share={share_id} status={item.status} charged={charged}',flush=True)
    except Exception:
        # 故障注入或数据库错误：申请保持 pending/processing，等待重试恢复
        traceback.print_exc()


def sweep_stale():
    """启动回收：worker 崩溃后遗留 running/processing 的任务重新可领取。
    重启本身即是显式恢复动作，一次性故障注入标记随之解除。"""
    with SessionLocal() as db:
        for r in db.scalars(select(Report).where(Report.status=='running')).all():
            r.status='queued'
        for s in db.scalars(select(ShareRequest).where(ShareRequest.status=='processing')).all():
            s.status='pending'
            s.fail_injected=False
            s.error=''
        db.commit()


def handle_message(message):
    if not message:
        return
    if message.get('kind')=='report':
        with SessionLocal() as db: process_report(db, message['id'])
    elif message.get('kind')=='share':
        with SessionLocal() as db: process_share(db, message['id'])


def tick():
    # 通知队列只是唤醒信号；权威状态在数据库，扫描兜底保证不丢任务
    handle_message(queue.pop('tutor:reports',timeout=1))
    handle_message(queue.pop('tutor:shares',timeout=1))
    with SessionLocal() as db:
        task=db.scalar(lock_for_update(select(Report).where(Report.status=='queued').order_by(Report.id), skip_locked=True))
    if task:
        with SessionLocal() as db: process_report(db, task.id)
    with SessionLocal() as db:
        share=db.scalar(lock_for_update(select(ShareRequest).where(ShareRequest.status=='pending').order_by(ShareRequest.id), skip_locked=True))
    if share:
        with SessionLocal() as db: process_share(db, share.id)


def main():
    sweep_stale()
    while True:
        Path('/tmp/worker-heartbeat').touch()
        try:
            tick()
        except Exception:
            traceback.print_exc();time.sleep(2)


if __name__=='__main__':
    main()
