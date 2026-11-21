"""差分隐私共享申请的业务事务。

关键不变量
==========
1. 累计预算按方案（plan_id）记账，跨报告共用；成绩修订不重置，没有任何重置入口。
2. 「生成加噪结果 + 写扣费流水 + 扣减预算 + 申请置 ready」在同一个数据库事务内，
   同时成功或同时回滚——结果和扣费同时生效，中断后不留半笔账。
3. 同一 idempotency_key 重试：参数一致则返回原申请，ready 后返回原噪声结果，
   绝不二次扣费；参数变化返回 409 拒绝。
4. 并发申请通过预算行锁 + 条件更新串行化，余额不足整体回滚，绝不透支。
"""
import hashlib
import math
import os
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from src.config.database import IS_SQLITE, lock_for_update
from src.constants.enums import DEFAULT_BUDGET_CAP, EPSILON_MAX, EPSILON_MIN
from src.models.entities import PrivacyBudget, PrivacyLedger, Report, ShareRequest
from src.services import dp
from src.services.transactions import with_write_retry


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cap():
    return float(os.environ.get('DP_BUDGET_CAP', DEFAULT_BUDGET_CAP))


def _seed_for(idempotency_key):
    # 取 4 字节有符号整数，兼容 PostgreSQL INTEGER 与 SQLite INTEGER
    digest=hashlib.sha256(f'seed:{idempotency_key}'.encode()).digest()
    return int.from_bytes(digest[:4], 'big', signed=True)


# ---------- 预算 ----------

def get_or_create_budget(db, plan_id):
    budget=db.scalar(select(PrivacyBudget).where(PrivacyBudget.plan_id == plan_id))
    if budget is None:
        budget=PrivacyBudget(plan_id=plan_id, cap_epsilon=_cap(), spent_epsilon=0.0, updated_at=_now())
        db.add(budget)
        db.flush()
    return budget


def budget_view(db, plan_id):
    budget=get_or_create_budget(db, plan_id)
    return {
        'plan_id': plan_id,
        'cap_epsilon': budget.cap_epsilon,
        'spent_epsilon': round(budget.spent_epsilon, 10),
        'remaining_epsilon': round(budget.cap_epsilon-budget.spent_epsilon, 10),
    }


def ledger_view(db, plan_id=None):
    stmt=select(PrivacyLedger).order_by(PrivacyLedger.id.desc())
    if plan_id is not None:
        stmt=stmt.where(PrivacyLedger.plan_id == plan_id)
    rows=db.scalars(stmt.limit(200)).all()
    return [
        {
            'id': r.id,
            'plan_id': r.plan_id,
            'share_request_id': r.share_request_id,
            'epsilon': r.epsilon,
            'cap_epsilon': r.cap_epsilon,
            'spent_after': round(r.spent_after, 10),
            'created_at': r.created_at,
        }
        for r in rows
    ]


# ---------- 申请 ----------

def _validate_report(db, report_id):
    report=db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, '报告不存在')
    if report.status != 'completed' or not report.result or 'sample_snapshot' not in report.result:
        raise HTTPException(409, '报告尚未完成计算，无法从样本快照聚合均分')
    return report


def _validate_epsilon(epsilon):
    if not (EPSILON_MIN <= epsilon <= EPSILON_MAX) or not math.isfinite(epsilon):
        raise HTTPException(422, f'隐私预算 epsilon 必须在 {EPSILON_MIN} 到 {EPSILON_MAX} 之间')


def create_share(db, report_id, epsilon, idempotency_key, actor, fail_injected=False):
    """登记申请（待生成）。幂等：同 key 命中原申请，参数不同则 409。

    并发提交时写锁冲突自动重试；重试期间重新读取预算余额，因此不会透支。
    """
    _validate_epsilon(epsilon)

    def _once():
        # 幂等冲突优先判定：同一申请编号换参数（含换报告）一律 409
        existing=db.scalar(select(ShareRequest).where(ShareRequest.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.report_id != report_id or not math.isclose(existing.epsilon, epsilon, abs_tol=1e-12):
                raise HTTPException(409, '同一申请编号已绑定不同的报告或隐私预算，参数变更被拒绝；请使用新的申请编号')
            return existing, False

        report=_validate_report(db, report_id)
        budget=get_or_create_budget(db, report.plan_id)
        if budget.cap_epsilon-budget.spent_epsilon+1e-9 < epsilon:
            raise HTTPException(402, f'累计隐私预算余额不足：剩余 {budget.cap_epsilon-budget.spent_epsilon:.4f}，本次需要 {epsilon:.4f}')

        item=ShareRequest(
            report_id=report_id,
            plan_id=report.plan_id,
            idempotency_key=idempotency_key,
            epsilon=epsilon,
            status='pending',
            noise_seed=_seed_for(idempotency_key),
            sample_checksum=report.result.get('checksum', ''),
            fail_injected=bool(fail_injected),
            attempts=0,
            created_by=actor,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(item)
        db.flush()
        return item, True

    try:
        item, created = with_write_retry(db, _once)
    except HTTPException:
        raise
    except Exception:
        # 并发下唯一键冲突等：退化为幂等查询
        existing=db.scalar(select(ShareRequest).where(ShareRequest.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.report_id != report_id or not math.isclose(existing.epsilon, epsilon, abs_tol=1e-12):
                raise HTTPException(409, '同一申请编号已绑定不同的报告或隐私预算，参数变更被拒绝；请使用新的申请编号')
            return existing, False
        raise
    if created:
        db.refresh(item)
    return item, created


def list_shares(db, plan_id=None):
    stmt=select(ShareRequest).order_by(ShareRequest.id.desc())
    if plan_id is not None:
        stmt=stmt.where(ShareRequest.plan_id == plan_id)
    return db.scalars(stmt).all()


def get_share(db, share_id):
    item=db.get(ShareRequest, share_id)
    if item is None:
        raise HTTPException(404, '共享申请不存在')
    return item


def get_share_by_token(db, token):
    item=db.scalar(select(ShareRequest).where(ShareRequest.download_token == token))
    if item is None:
        raise HTTPException(404, '共享链接不存在或已失效')
    if item.status != 'ready':
        raise HTTPException(409, '共享内容尚未生成，请稍后重试')
    return item


def retry_share(db, share_id, actor):
    """恢复 / 重试：只允许把 pending、failed、processing(中断遗留) 的申请重新入队，
    绝不修改参数；一次性故障注入标记在恢复时解除。"""
    item=get_share(db, share_id)
    if item.status == 'ready':
        return item
    item.status='pending'
    item.fail_injected=False
    item.error=''
    item.updated_at=_now()
    db.commit()
    db.refresh(item)
    from src.services.queue_bus import queue
    queue.push('tutor:shares', {'kind': 'share', 'id': item.id})
    return item


def process_share(db, share_id):
    """worker 调用：领取申请并在单事务内完成「加噪 + 扣费 + 可下载」。

    返回 (item, charged)：charged=False 表示本次未发生扣费（已完成/被他人接管）。
    """
    # 1) 原子领取：pending/processing -> processing，防止两个 worker 同时处理
    if IS_SQLITE:
        row=db.execute(
            ShareRequest.__table__.update().where(
                ShareRequest.id == share_id,
                ShareRequest.status.in_(('pending', 'processing')),
            ).values(status='processing', updated_at=_now())
        )
        db.flush()
        if row.rowcount == 0:
            db.rollback()
            return db.get(ShareRequest, share_id), False
        item=db.get(ShareRequest, share_id)
    else:
        item=db.scalar(
            lock_for_update(select(ShareRequest).where(ShareRequest.id == share_id), skip_locked=True)
        )
        if item is None:
            db.rollback()
            return None, False
        if item.status == 'ready':
            db.rollback()
            return item, False
        if item.status not in ('pending', 'processing'):
            db.rollback()
            return item, False
        item.status='processing'
        item.updated_at=_now()
        db.flush()

    # 2) 故障注入：模拟 worker 进程在提交前被 kill——回滚全部修改，
    # 申请停留在 processing，无结果、无扣费流水、预算不变；
    # 由显式重试接口（或 worker 重启 sweep_stale）回收。
    if item.fail_injected:
        db.rollback()
        raise RuntimeError(f'模拟生成中断（share_request={share_id}）：事务已回滚，未产生扣费，等待重试或 worker 重启回收')

    item.attempts += 1

    report=db.get(Report, item.report_id)
    if report is None or report.status != 'completed':
        item.status='failed'
        item.error='关联报告缺失或未完成'
        item.updated_at=_now()
        db.commit()
        return item, False

    # 3) 锁预算行并做余额条件检查（并发申请在此串行，杜绝透支）
    budget=db.scalar(
        lock_for_update(select(PrivacyBudget).where(PrivacyBudget.plan_id == item.plan_id))
    ) if not IS_SQLITE else get_or_create_budget(db, item.plan_id)
    if budget is None:
        budget=get_or_create_budget(db, item.plan_id)

    already=db.scalar(select(PrivacyLedger).where(PrivacyLedger.share_request_id == item.id))
    if already is not None:
        # 极端恢复：流水已在但申请未置 ready，沿用原结果不重复扣费
        item.status='ready'
        item.updated_at=_now()
        db.commit()
        return item, False

    if budget.cap_epsilon-budget.spent_epsilon+1e-9 < item.epsilon:
        item.status='failed'
        item.error=f'累计隐私预算余额不足：剩余 {budget.cap_epsilon-budget.spent_epsilon:.4f}'
        item.updated_at=_now()
        db.commit()
        return item, False

    # 4) 确定性加噪：种子派生自申请编号，重试得到完全相同的噪声
    groups=dp.release(report.result, item.epsilon, item.idempotency_key)
    token=secrets.token_urlsafe(24)
    payload=dp.public_view(groups, item.epsilon, item.id, token, report.id, _now())

    # 5) 同一事务：结果落库 + 扣费流水 + 预算扣减 + 置为可下载
    item.public_payload=payload
    item.download_token=token
    item.status='ready'
    item.error=''
    item.updated_at=_now()

    budget.spent_epsilon += item.epsilon
    budget.updated_at=_now()
    db.add(PrivacyLedger(
        plan_id=item.plan_id,
        share_request_id=item.id,
        epsilon=item.epsilon,
        cap_epsilon=budget.cap_epsilon,
        spent_after=budget.spent_epsilon,
        created_at=_now(),
    ))
    db.commit()
    db.refresh(item)
    return item, True
