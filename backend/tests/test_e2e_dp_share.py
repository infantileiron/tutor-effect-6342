"""端到端验证：FastAPI + SQLite 降级队列 + 后台 worker 线程。

覆盖需求点：
1. 纯差分隐私口径（替换一人敏感性、Laplace 确定性噪声、误差解释）
2. 累计预算跨报告共用、成绩修订不重置
3. 申请 pending -> ready 全流程，结果与扣费同事务生效
4. 同申请编号重试返回原噪声、不重复扣费；换参数 409 拒绝
5. 并发申请不透支
6. 生成中断（故障注入）后可恢复，未产生扣费
7. 对外内容不含明细/原均分/原检验结论
"""
import json
import os
import socket
import threading
import time

import pytest

# 环境变量必须在应用模块导入前设置
DB_PATH = os.path.join(os.path.dirname(__file__), 'e2e_test.db')
for suffix in ('', '-wal', '-shm'):
    p = DB_PATH + suffix
    if os.path.exists(p):
        os.remove(p)
os.environ['DATABASE_URL'] = f'sqlite:///{DB_PATH}'
os.environ['JWT_SECRET'] = 'e2e-test-secret'
os.environ['DP_BUDGET_CAP'] = '5.0'
os.environ['SEED_DEMO'] = 'false'
# 无 REDIS_URL，自动走 SQLite 队列降级

import httpx
import uvicorn
from sqlalchemy import select

from src.config.database import engine, SessionLocal
from src.models import entities
from src.models.entities import Plan, PrivacyBudget, PrivacyLedger, Report, ShareRequest, Student
from src.services.queue_bus import queue
from src.services.statistics import welch
from src.worker import sweep_stale, tick
from src.main import app
from src.utils.security import hash_password

Base = entities.Base


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope='session')
def setup_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(entities.User(email='analyst@demo.local', role='analyst',
                             password_hash=hash_password('Demo123!')))
        db.add(entities.User(email='viewer@demo.local', role='viewer',
                             password_hash=hash_password('Demo123!')))
        db.add(Plan(title='方案A', campus='全部', exclude_absent=True, description='e2e'))
        db.flush()
        # 每组 10 人，便于核对敏感性 100/10=10；组内保留微小差异以通过方差检查，
        # 均值取 69.55 / 80.55，避免与对外文件里可能出现的整数字面量混淆
        for g in ('control', 'intervention'):
            for i in range(10):
                base = 69.5 if g == 'control' else 80.5
                db.add(Student(code=f'{g}-{i}', name=f'{g}{i}', campus='城东',
                               group=g, score=base + (i - 5) * 0.1, absent=False))
        db.commit()
        plan = db.scalar(select(Plan))
        result = welch(db.scalars(select(Student).order_by(Student.code)).all(), plan)
        report = Report(plan_id=plan.id, status='completed', result=result,
                        created_at='2026-11-16T00:00:00+00:00',
                        conclusion='内部原始结论：干预组显著优于对照组', published=True)
        db.add(report)
        db.commit()
        yield {'plan_id': plan.id, 'report_id': report.id}


@pytest.fixture(scope='session')
def runtime(setup_db):
    # 后台 worker 线程（消费 SQLite 降级队列 + 兜底扫描）
    stop = threading.Event()

    def run_worker():
        sweep_stale()
        while not stop.is_set():
            try:
                tick()
            except Exception:
                pass
            time.sleep(0.05)

    worker = threading.Thread(target=run_worker, daemon=True)
    worker.start()

    # 真实 uvicorn 服务线程，经 HTTP 完整走一遍中间件/路由/事务
    port = _free_port()
    config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    base_url = f'http://127.0.0.1:{port}'
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(base_url + '/health', timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError('测试服务未在超时内启动')

    yield base_url

    stop.set()
    worker.join(timeout=3)
    server.should_exit = True
    server_thread.join(timeout=5)


@pytest.fixture
def client(runtime):
    timeout_client = httpx.Client(base_url=runtime, timeout=10)
    r = timeout_client.post('/auth/login', json={'email': 'analyst@demo.local',
                                                 'password': 'Demo123!'})
    assert r.status_code == 200
    timeout_client.headers['Authorization'] = f"Bearer {r.json()['access_token']}"
    yield timeout_client
    timeout_client.close()


def wait_status(client, share_id, target, timeout_s=8):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = client.get(f'/shares/{share_id}')
        last = r.json()
        if last['status'] == target:
            return last
        time.sleep(0.1)
    raise AssertionError(f'share {share_id} 未达到 {target}，最后状态 {last}')


def test_01_dp_math_sensitivity_and_determinism(setup_db):
    from src.services import dp
    # 替换一人：均分敏感性 = 100 / n
    assert dp.group_sensitivity(10) == pytest.approx(10.0)
    assert dp.laplace_scale(10, 0.5) == pytest.approx(20.0)
    with SessionLocal() as db:
        report = db.get(Report, setup_db['report_id'])
        g1 = dp.release(report.result, 0.5, 'math-key-1')
        g2 = dp.release(report.result, 0.5, 'math-key-1')
        g3 = dp.release(report.result, 0.5, 'math-key-2')
    assert [x['noisy_mean'] for x in g1] == [x['noisy_mean'] for x in g2]
    assert [x['noisy_mean'] for x in g1] != [x['noisy_mean'] for x in g3]
    for g in g1:
        assert 0 <= g['noisy_mean'] <= 100
        assert g['bound_95'] < g['bound_99']
        assert g['mae'] == pytest.approx(g['scale_b'])


def test_02_full_flow_budget_and_ledger(client, setup_db):
    r = client.post('/shares', json={'report_id': setup_db['report_id'],
                                    'epsilon': 0.3, 'idempotency_key': 'flow-0001'})
    assert r.status_code == 202, r.text
    share_id = r.json()['id']
    assert r.json()['status'] in ('pending', 'processing')
    ready = wait_status(client, share_id, 'ready')
    payload = ready['public_payload']

    # 对外白名单：不得包含任何敏感字段（error_budget 是合法的误差解释，
    # 但绝不能出现具体噪声值、原始均值、样本明细或检验结论）
    raw = json.dumps(payload, ensure_ascii=False)
    for forbidden in ('sample_snapshot', 'true_mean', '"noise"', 'conclusion', 'p_value',
                      '"t"', 'cohen_d', '69.55', '80.55', '内部原始结论'):
        assert forbidden not in raw, f'对外内容泄漏敏感字段: {forbidden}'
    assert payload['epsilon'] == 0.3
    assert len(payload['groups']) == 2
    for g in payload['groups']:
        assert set(g.keys()) == {'group', 'n', 'private_mean', 'error_budget'}
        assert g['n'] == 10
        assert 0 <= g['private_mean'] <= 100
        assert g['error_budget']['sensitivity_replace_one'] == pytest.approx(10.0)

    # 预算与流水
    r = client.get(f'/shares/budget?plan_id={setup_db["plan_id"]}')
    body = r.json()
    assert body['budget']['cap_epsilon'] == 5.0
    assert body['budget']['spent_epsilon'] == pytest.approx(0.3)
    assert body['budget']['remaining_epsilon'] == pytest.approx(5.0 - 0.3)
    assert len(body['ledger']) == 1
    assert body['ledger'][0]['epsilon'] == 0.3
    assert body['ledger'][0]['spent_after'] == pytest.approx(0.3)

    # 免登录公开下载
    token = ready['download_token']
    anon = httpx.Client(base_url=client.base_url, timeout=10)
    r = anon.get(f'/public/shares/{token}')
    assert r.status_code == 200
    assert 'attachment' in r.headers['content-disposition']
    assert json.loads(r.content)['download_token'] == token
    anon.close()

    # 登录态下载同样可用
    r = client.get(f'/shares/{share_id}/download')
    assert r.status_code == 200


def test_03_idempotent_retry_same_noise_no_double_charge(client, setup_db):
    payload = {'report_id': setup_db['report_id'], 'epsilon': 0.2,
               'idempotency_key': 'idem-0001'}
    r1 = client.post('/shares', json=payload)
    assert r1.status_code == 202
    sid = r1.json()['id']
    wait_status(client, sid, 'ready')

    # 同 key 同参数重复请求：返回原申请，不新建
    r2 = client.post('/shares', json=payload)
    assert r2.status_code == 202
    assert r2.json()['id'] == sid

    # 显式重试接口
    r3 = client.post(f'/shares/{sid}/retry')
    assert r3.status_code == 202
    assert r3.json()['id'] == sid

    # 噪声结果完全一致
    detail = client.get(f'/shares/{sid}').json()
    assert detail['status'] == 'ready'
    means_a = [g['private_mean'] for g in detail['public_payload']['groups']]
    detail2 = client.get(f'/shares/{sid}').json()
    means_b = [g['private_mean'] for g in detail2['public_payload']['groups']]
    assert means_a == means_b

    with SessionLocal() as db:
        ledgers = db.scalars(select(PrivacyLedger).where(PrivacyLedger.share_request_id == sid)).all()
        assert len(ledgers) == 1
        spent = db.scalar(select(PrivacyBudget.spent_epsilon))
        # 0.3（test02）+ 0.2（本测试）
        assert spent == pytest.approx(0.5)


def test_04_changed_params_rejected(client, setup_db):
    # 同一申请编号换预算 -> 409 拒绝
    r = client.post('/shares', json={'report_id': setup_db['report_id'], 'epsilon': 0.1,
                                     'idempotency_key': 'idem-0001'})
    assert r.status_code == 409
    # 同一申请编号换报告 -> 409 拒绝（参数冲突优先于报告存在性校验）
    r = client.post('/shares', json={'report_id': setup_db['report_id'] + 1, 'epsilon': 0.2,
                                     'idempotency_key': 'idem-0001'})
    assert r.status_code == 409


def test_05_budget_insufficient_rejected(client, setup_db):
    drain_shares(client)
    # 动态构造超额申请：当前余额 + 0.1 必然无法通过预检（402，且不留申请）
    before = client.get(
        f'/shares/budget?plan_id={setup_db["plan_id"]}').json()['budget']
    r = client.post('/shares', json={'report_id': setup_db['report_id'],
                                     'epsilon': round(before['remaining_epsilon'] + 0.1, 6),
                                     'idempotency_key': 'over-cap-0001'})
    assert r.status_code == 402
    keys = {x['idempotency_key'] for x in client.get('/shares').json()}
    assert 'over-cap-0001' not in keys
    after = client.get(
        f'/shares/budget?plan_id={setup_db["plan_id"]}').json()['budget']
    assert after['spent_epsilon'] == pytest.approx(before['spent_epsilon'])


def test_06_concurrent_requests_no_overdraft(client, setup_db):
    # 两笔申请各占当前余额的 75%：单笔可创建，但合计 150% 必然超额。
    # 创建阶段可能两笔都受理（202），硬性扣费在 worker 处理时串行执行：
    # 一笔 ready，另一笔 failed（余额不足），全程绝不透支。
    budget = client.get(f'/shares/budget?plan_id={setup_db["plan_id"]}').json()['budget']
    remaining = round(budget['remaining_epsilon'], 10)
    epsilon = round(remaining * 0.75, 6)
    assert epsilon <= 10.0
    spent_before = budget['spent_epsilon']
    results = []
    errors = []
    barrier = threading.Barrier(2)

    def fire(key):
        try:
            barrier.wait()  # 尽量让两个请求同时进入
            r = client.post('/shares', json={'report_id': setup_db['report_id'],
                                             'epsilon': epsilon, 'idempotency_key': key})
            results.append((key, r.status_code, r.json()))
        except Exception as exc:
            errors.append((key, str(exc)))

    threads = [threading.Thread(target=fire, args=(f'concurrency-{i}-xxxxxxxx',)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f'并发请求不应出现网络异常: {errors}'

    # 等待两个申请到达终态
    created = [x for x in results if x[1] == 202]
    rejected_at_once = [x for x in results if x[1] == 402]
    assert len(created) + len(rejected_at_once) == 2
    finals = []
    for _, _, body in created:
        finals.append(wait_terminal(client, body['id']))

    ready = [f for f in finals if f['status'] == 'ready']
    failed = [f for f in finals if f['status'] == 'failed']
    assert len(ready) == 1, f'并发下只允许一笔扣费成功: {finals}'
    if not rejected_at_once:
        assert len(failed) == 1
        assert '余额不足' in failed[0]['error']
    sid = ready[0]['id']

    with SessionLocal() as db:
        row = db.scalar(select(PrivacyBudget).where(PrivacyBudget.plan_id == setup_db['plan_id']))
        # 只增加了一笔 epsilon，且绝不超过 cap
        assert row.spent_epsilon == pytest.approx(spent_before + epsilon)
        assert row.spent_epsilon <= row.cap_epsilon + 1e-9
        pair_ids = [body['id'] for _, _, body in created]
        ledgers = db.scalars(select(PrivacyLedger).where(
            PrivacyLedger.share_request_id.in_(pair_ids))).all()
        assert len(ledgers) == 1
        assert ledgers[0].share_request_id == sid


def wait_terminal(client, share_id, timeout_s=8):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = client.get(f'/shares/{share_id}').json()
        if last['status'] in ('ready', 'failed'):
            return last
        time.sleep(0.1)
    raise AssertionError(f'share {share_id} 未到达终态，最后状态 {last}')


def drain_shares(client, timeout_s=15):
    """等待所有申请到达终态，避免前一用例的延迟处理影响后续预算断言。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        items = client.get('/shares').json()
        if all(x['status'] in ('ready', 'failed') for x in items):
            return items
        time.sleep(0.1)
    raise AssertionError('队列未在超时内排空')


def test_07_failure_injection_then_recover(client, setup_db):
    drain_shares(client)
    r = client.post('/shares', json={'report_id': setup_db['report_id'], 'epsilon': 0.1,
                                     'idempotency_key': 'fail-0001', 'fail_injected': True})
    assert r.status_code == 202
    sid = r.json()['id']
    # 首次 worker 处理在提交前中断：申请停在 pending/processing（无结果、无扣费），
    # 且不会被兜底扫描悄悄做成 ready
    deadline = time.time() + 5
    detail = None
    while time.time() < deadline:
        detail = client.get(f'/shares/{sid}').json()
        if detail['status'] in ('pending', 'processing'):
            break
        time.sleep(0.05)
    assert detail['status'] in ('pending', 'processing'), detail
    time.sleep(0.8)
    detail = client.get(f'/shares/{sid}').json()
    assert detail['status'] in ('pending', 'processing')
    assert detail['public_payload'] is None
    with SessionLocal() as db:
        assert db.scalar(select(PrivacyLedger).where(PrivacyLedger.share_request_id == sid)) is None
        budget_before = db.scalar(select(PrivacyBudget.spent_epsilon))

    # 显式重试恢复：同申请、同参数、同确定性噪声，仅扣一次费
    r = client.post(f'/shares/{sid}/retry')
    assert r.status_code == 202
    ready = wait_status(client, sid, 'ready')
    assert ready['public_payload']['epsilon'] == 0.1
    with SessionLocal() as db:
        ledgers = db.scalars(select(PrivacyLedger).where(PrivacyLedger.share_request_id == sid)).all()
        assert len(ledgers) == 1
        budget = db.scalar(select(PrivacyBudget).where(PrivacyBudget.plan_id == setup_db['plan_id']))
        assert budget.spent_epsilon == pytest.approx(budget_before + 0.1)

    # worker 重启回收路径：sweep_stale 会把残留 processing 置回 pending 并解除注入
    with SessionLocal() as db:
        db.add(ShareRequest(report_id=setup_db['report_id'], plan_id=setup_db['plan_id'],
                            idempotency_key='fail-0002', epsilon=0.05, status='processing',
                            noise_seed=1, fail_injected=True, attempts=0,
                            created_by='test', created_at='2026-11-16T02:00:00+00:00',
                            updated_at='2026-11-16T02:00:00+00:00'))
        db.commit()
    sweep_stale()
    recovered = client.get(
        '/shares', params={'plan_id': setup_db['plan_id']}
    ).json()
    target = next(x for x in recovered if x['idempotency_key'] == 'fail-0002')
    assert target['status'] == 'pending'
    assert target['fail_injected'] is False


def test_08_budget_cross_report_and_score_revision_no_reset(client, setup_db):
    drain_shares(client)
    # 新建第二份报告（同方案 -> 共享同一累计预算）
    with SessionLocal() as db:
        old = db.get(Report, setup_db['report_id'])
        report2 = Report(plan_id=setup_db['plan_id'], status='completed', result=old.result,
                         created_at='2026-11-16T01:00:00+00:00', conclusion='第二份内部报告',
                         published=False)
        db.add(report2)
        db.commit()
        rid2 = report2.id

    # 用掉当前全部余额（基于第二份报告，但与第一份共用同一预算账本）；
    # 单次 epsilon 上限为 10，余额更高时按上限申请，再用第二笔把余额花光
    budget_before = client.get(
        f'/shares/budget?plan_id={setup_db["plan_id"]}').json()['budget']
    remaining = round(budget_before['remaining_epsilon'], 10)
    assert remaining > 0
    first_epsilon = min(remaining, 10.0)
    r = client.post('/shares', json={'report_id': rid2, 'epsilon': first_epsilon,
                                     'idempotency_key': 'cross-rpt-1'})
    assert r.status_code == 202
    sid = r.json()['id']
    ready = wait_status(client, sid, 'ready')
    spent_after_first = budget_before['spent_epsilon'] + first_epsilon
    # 若还有零头（仅在剩余 > 10 的边界下），补第二笔花光
    rest = round(budget_before['cap_epsilon'] - spent_after_first, 10)
    if rest > 0:
        r2 = client.post('/shares', json={'report_id': rid2, 'epsilon': rest,
                                          'idempotency_key': 'cross-rpt-1b'})
        assert r2.status_code == 202
        wait_status(client, r2.json()['id'], 'ready')
    with SessionLocal() as db:
        budget = db.scalar(select(PrivacyBudget).where(PrivacyBudget.plan_id == setup_db['plan_id']))
        assert budget.spent_epsilon == pytest.approx(budget_before['cap_epsilon'])
        assert budget.spent_epsilon <= budget.cap_epsilon + 1e-9

    # 预算耗尽：新申请被拒（402，且不产生任何申请/流水）
    r = client.post('/shares', json={'report_id': rid2, 'epsilon': 0.05,
                                     'idempotency_key': 'cross-rpt-2'})
    assert r.status_code == 402

    # 修订成绩：预算不重置，旧共享结果不变
    old_payload = client.get(f'/shares/{ready["id"]}').json()['public_payload']
    old_means = [g['private_mean'] for g in old_payload['groups']]
    with SessionLocal() as db:
        s = db.scalar(select(Student).where(Student.code == 'control-0'))
        s.score = 5.0
        db.commit()
    with SessionLocal() as db:
        budget = db.scalar(select(PrivacyBudget).where(PrivacyBudget.plan_id == setup_db['plan_id']))
        # 修订成绩不重置累计花费，仍等于 cap
        assert budget.spent_epsilon == pytest.approx(budget.cap_epsilon)
    new_payload = client.get(f'/shares/{ready["id"]}').json()['public_payload']
    new_means = [g['private_mean'] for g in new_payload['groups']]
    assert old_means == new_means


def test_09_viewer_cannot_create_share(setup_db, runtime):
    c = httpx.Client(base_url=runtime, timeout=10)
    r = c.post('/auth/login', json={'email': 'viewer@demo.local', 'password': 'Demo123!'})
    c.headers['Authorization'] = f"Bearer {r.json()['access_token']}"
    r = c.post('/shares', json={'report_id': setup_db['report_id'], 'epsilon': 0.1,
                                'idempotency_key': 'viewer-deny-1'})
    assert r.status_code == 403
    # 只读账号可以查看余额
    assert c.get('/shares/budget').status_code == 200
    c.close()


def test_10_epsilon_validation(client, setup_db):
    r = client.post('/shares', json={'report_id': setup_db['report_id'], 'epsilon': 0,
                                     'idempotency_key': 'bad-eps-1'})
    assert r.status_code == 422
    r = client.post('/shares', json={'report_id': setup_db['report_id'], 'epsilon': 99,
                                     'idempotency_key': 'bad-eps-2'})
    assert r.status_code == 422


def test_11_queue_fallback_and_pending_visible(client, setup_db):
    # 队列降级实现可 ping；申请列表接口可见此前流程产生的全部记录
    queue.ping()
    r = client.get('/shares')
    assert r.status_code == 200
    keys = {item['idempotency_key'] for item in r.json()}
    assert {'flow-0001', 'idem-0001', 'fail-0001'} <= keys
    # 被预算拒绝的申请不会留下记录（扣费与申请创建都不发生）
    assert 'cross-rpt-2' not in keys


def test_12_laplace_error_claims_monte_carlo(setup_db):
    """用大样本蒙特卡洛验证对外误差解释：MAE≈b、95%/99% 边界覆盖率符合理论。"""
    from src.services import dp
    n, epsilon = 25, 0.8
    b = dp.laplace_scale(n, epsilon)          # 100/25/0.8 = 5.0
    assert b == pytest.approx(5.0)
    errors = []
    for i in range(20000):
        item = dp.noisy_group_mean(50.0, n, epsilon, f'mc-{i:06d}', 'control')
        errors.append(item['noisy_mean'] - 50.0)
    mae = sum(abs(e) for e in errors) / len(errors)
    within_95 = sum(1 for e in errors if abs(e) <= b * dp.TAIL_95) / len(errors)
    within_99 = sum(1 for e in errors if abs(e) <= b * dp.TAIL_99) / len(errors)
    assert mae == pytest.approx(b, rel=0.05)
    assert 0.94 <= within_95 <= 0.96
    assert 0.985 <= within_99 <= 0.995
