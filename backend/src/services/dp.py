"""纯差分隐私：Laplace 机制下的匿名均分发布。

威胁模型与口径
--------------
相邻数据集定义为「替换一名学员的成绩」：两条数据集 x、x' 仅一名学员不同，
该学员成绩都在固定区间 [0, 100] 内，分组人数 n_g 固定不变（公开信息）。

对某一分组的均分查询 f_g(x) = (1/n_g) * Σ score_i：
    替换一人时，均值最大变化量（L1 敏感性）为
        Δ_g = |100 - 0| / n_g = 100 / n_g

单次共享发布两个分组的均分。两个分组是同一总体的不相交划分，对一个人而言
他只属于其中一个分组，因此两条相邻记录只会改变其中一个分组的均值。依据
并行组合（parallel composition），给两个分组各加 Lap(Δ_g / ε) 噪声，
整次发布满足 **ε-差分隐私**（不是 2ε）。费用按 ε 计一次。

Laplace 机制：M(x) = f(x) + Z，Z ~ Laplace(0, b)，b = Δ / ε。
    E|Z| = b（平均绝对误差 MAE），Var(Z) = 2b²（RMSE = √2·b），
    P(|Z| ≥ t) = exp(-t/b)，95% 误差边界 = b·ln20，99% = b·ln100。

把加噪结果截断回 [0, 100] 属于差分隐私的后处理（post-processing），
不会削弱隐私保证。所有随机数由申请级种子确定性派生，同一申请重试必然
得到相同噪声，从而做到「重试返回原噪声结果、不重复扣费」。
"""
import hashlib
import math
import random

from src.constants.enums import SCORE_MAX, SCORE_MIN, SCORE_RANGE

# Laplace 分布的分位点：P(|Z| >= b * k) = exp(-k)
TAIL_95=math.log(20)   # ≈ 2.996
TAIL_99=math.log(100)  # ≈ 4.605


def group_sensitivity(n):
    """替换一人时分组均分的 L1 敏感性：100 / n。"""
    if n <= 0:
        raise ValueError('分组人数必须为正')
    return SCORE_RANGE / n


def laplace_scale(n, epsilon):
    """该分组噪声尺度 b = Δ / ε。"""
    if epsilon <= 0:
        raise ValueError('隐私预算 epsilon 必须为正数')
    return group_sensitivity(n) / epsilon


def _derive_seed(idempotency_key, group):
    """同一申请、同一分组得到固定种子；不同申请/分组相互独立。"""
    digest=hashlib.sha256(f'dp-laplace-v1:{idempotency_key}:{group}'.encode()).digest()
    return int.from_bytes(digest[:8], 'big', signed=True)


def laplace_sample(rng):
    """逆变换采样 Laplace(0,1)：U ~ Uniform(-0.5,0.5)，X = -sgn(U) ln(1-2|U|)。"""
    u=rng.random()-0.5
    if u == 0:
        return 0.0
    return -math.copysign(math.log(1-2*abs(u)), u)


def noisy_group_mean(true_mean, n, epsilon, idempotency_key, group):
    """生成单个分组的加噪均分与误差解释，全部由 (key, group) 确定性决定。"""
    b=laplace_scale(n, epsilon)
    rng=random.Random(_derive_seed(idempotency_key, group))
    noise=b*laplace_sample(rng)
    raw=true_mean+noise
    clipped=min(SCORE_MAX, max(SCORE_MIN, raw))
    return {
        'group': group,
        'n': n,
        'true_mean': float(true_mean),                # 仅内部计算使用，不进入对外内容
        'noise': float(noise),                        # 仅内部排障使用，不进入对外内容
        'noisy_mean': float(clipped),
        'clipped': abs(clipped-raw) > 1e-12,
        'sensitivity': group_sensitivity(n),
        'scale_b': b,
        'mae': b,
        'rmse': math.sqrt(2)*b,
        'bound_95': b*TAIL_95,
        'bound_99': b*TAIL_99,
    }


def release(report_result, epsilon, idempotency_key):
    """从报告样本快照聚合两个分组的匿名均分。

    report_result 取自已完成报告的不可变 result（含 sample_snapshot），
    不读取学生明细表，保证成绩修订不改变既有共享口径。
    """
    snapshot=report_result['sample_snapshot']
    groups={}
    for row in snapshot:
        groups.setdefault(row['group'], []).append(float(row['score']))
    release_groups=[]
    for group in ('control', 'intervention'):
        scores=groups.get(group)
        if not scores:
            raise ValueError(f'分组 {group} 没有有效成绩，无法发布均分')
        n=len(scores)
        true_mean=sum(scores)/n
        # 人数必须与报告快照一致（公开固定人数）
        if n != (report_result['control_n'] if group == 'control' else report_result['intervention_n']):
            raise ValueError(f'分组 {group} 人数与报告结果不一致')
        item=noisy_group_mean(true_mean, n, epsilon, idempotency_key, group)
        release_groups.append(item)
    return release_groups


def public_view(groups, epsilon, request_id, token, report_id, created_at):
    """构造对外内容：只含加噪均分、公开人数与噪声误差解释。

    显式剔除：个人成绩明细、原均分、原检验结论、t/p/d 等任何原始统计量。
    """
    return {
        'schema': 'dp-share-v1',
        'share_request_id': request_id,
        'download_token': token,
        'report_id': report_id,
        'created_at': created_at,
        'mechanism': 'Laplace 机制（纯 ε-差分隐私），相邻数据集 = 替换一名学员成绩',
        'score_domain': {'min': SCORE_MIN, 'max': SCORE_MAX},
        'composition': '两组为不相交分组，按并行组合，整次发布满足 ε-DP，按 ε 扣费一次',
        'epsilon': epsilon,
        'groups': [
            {
                'group': g['group'],
                'n': g['n'],
                'private_mean': round(g['noisy_mean'], 4),
                'error_budget': {
                    'sensitivity_replace_one': round(g['sensitivity'], 6),
                    'laplace_scale_b': round(g['scale_b'], 6),
                    'expected_abs_error_mae': round(g['mae'], 4),
                    'rmse': round(g['rmse'], 4),
                    'prob_95pct_abs_error_within': round(g['bound_95'], 4),
                    'prob_99pct_abs_error_within': round(g['bound_99'], 4),
                    'clipped_to_domain': g['clipped'],
                },
            }
            for g in groups
        ],
        'notice': '本文件仅含差分隐私加噪均分与公开分组人数；不含个人成绩、原始均分或检验结论。',
    }
