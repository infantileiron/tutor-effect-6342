ROLES=('admin','analyst','viewer')
REPORT_STATUSES=('queued','running','completed','failed')
GROUPS=('control','intervention')
# 差分隐私共享申请的生命周期：
# pending 待生成（可恢复重试）-> processing 生成中（worker 领取）-> ready 可下载 / failed 终态失败
SHARE_STATUSES=('pending','processing','ready','failed')
# 成绩固定取值范围；均分敏感性推导依赖该区间
SCORE_MIN=0.0
SCORE_MAX=100.0
SCORE_RANGE=SCORE_MAX-SCORE_MIN
# 单次申请允许选择的隐私预算区间（纯 DP，epsilon > 0）
EPSILON_MIN=0.05
EPSILON_MAX=10.0
# 每个方案的累计预算上限（演示默认值，可由环境变量 DP_BUDGET_CAP 覆盖）
DEFAULT_BUDGET_CAP=3.0
