import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.controllers.business import serialize
from src.models.entities import Report
from src.schemas.business import ShareCreateInput
from src.services import dp, sharing
from src.services.queue_bus import queue
from src.utils.security import current_user, editor

router=APIRouter()


def _share_view(item):
    """列表/详情视图：不含噪声真值明细，仅暴露状态与对外内容。"""
    data=serialize(item)
    # 内部噪声与原始均值绝不下发到前端管理接口以外的任何地方；
    # public_payload 本身已经过白名单构造，只含加噪均分。
    return data


@router.get('/shares/budget')
def budget(plan_id: int | None = None, db: Session = Depends(get_db), user=Depends(current_user)):
    if plan_id is None:
        # 未指定方案时返回全部方案预算汇总
        from sqlalchemy import select
        from src.models.entities import PrivacyBudget, Plan
        rows=db.scalars(select(PrivacyBudget).order_by(PrivacyBudget.plan_id)).all()
        plans={p.id: p.title for p in db.scalars(select(Plan)).all()}
        return {
            'budgets': [
                {
                    'plan_id': b.plan_id,
                    'plan_title': plans.get(b.plan_id, ''),
                    'cap_epsilon': b.cap_epsilon,
                    'spent_epsilon': round(b.spent_epsilon, 10),
                    'remaining_epsilon': round(b.cap_epsilon-b.spent_epsilon, 10),
                }
                for b in rows
            ],
            'ledger': sharing.ledger_view(db),
        }
    return {'budget': sharing.budget_view(db, plan_id), 'ledger': sharing.ledger_view(db, plan_id)}


@router.get('/shares')
def list_shares(plan_id: int | None = None, db: Session = Depends(get_db), user=Depends(current_user)):
    return [_share_view(i) for i in sharing.list_shares(db, plan_id)]


@router.post('/shares', status_code=202)
def create_share(data: ShareCreateInput, request: Request, db: Session = Depends(get_db), user=Depends(editor)):
    item, created = sharing.create_share(
        db, data.report_id, float(data.epsilon), data.idempotency_key,
        getattr(request.state, 'actor', user.email), fail_injected=data.fail_injected,
    )
    if created:
        queue.push('tutor:shares', {'kind': 'share', 'id': item.id})
    return _share_view(item)


@router.get('/shares/{share_id}')
def share_detail(share_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    return _share_view(sharing.get_share(db, share_id))


@router.post('/shares/{share_id}/retry', status_code=202)
def retry_share(share_id: int, db: Session = Depends(get_db), user=Depends(editor)):
    item=sharing.retry_share(db, share_id, user.email)
    return _share_view(item)


@router.get('/shares/{share_id}/download')
def authenticated_download(share_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    item=sharing.get_share(db, share_id)
    if item.status != 'ready':
        raise HTTPException(409, '共享内容尚未生成，请稍后再试')
    return _download_response(item)


@router.get('/public/shares/{token}')
def public_download(token: str, db: Session = Depends(get_db)):
    """对外免登录入口：只凭随机令牌取白名单内容，无法遍历自增 id。"""
    item=sharing.get_share_by_token(db, token)
    return _download_response(item)


def _download_response(item):
    # public_payload 在生成时已做白名单处理；这里再防御性剔除任何敏感键
    payload=dict(item.public_payload)
    for forbidden in ('true_mean', 'noise', 'sample_snapshot', 'conclusion', 'p_value', 't', 'cohen_d'):
        payload.pop(forbidden, None)
    body=json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(
        body,
        media_type='application/json; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="dp-share-{item.id}.json"'},
    )


@router.get('/reports/{report_id}/dp-preview')
def dp_preview(report_id: int, epsilon: float, db: Session = Depends(get_db), user=Depends(current_user)):
    """不扣费的误差试算：只返回敏感性与噪声尺度，便于页面解释噪声误差。"""
    if not (0.05 <= epsilon <= 10.0):
        raise HTTPException(422, '隐私预算 epsilon 必须在 0.05 到 10 之间')
    report=db.get(Report, report_id)
    if report is None or report.status != 'completed':
        raise HTTPException(409, '报告尚未完成计算')
    result=report.result
    preview=[]
    for group, n in (('control', result['control_n']), ('intervention', result['intervention_n'])):
        b=dp.laplace_scale(n, epsilon)
        preview.append({
            'group': group,
            'n': n,
            'sensitivity': dp.group_sensitivity(n),
            'scale_b': b,
            'mae': b,
            'rmse': (2*b*b) ** 0.5,
            'bound_95': b*dp.TAIL_95,
            'bound_99': b*dp.TAIL_99,
        })
    return {'epsilon': epsilon, 'groups': preview}
