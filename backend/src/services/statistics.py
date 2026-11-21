import math,hashlib,json
try:
    from scipy import stats as _scipy_stats
except Exception:  # 本地最小环境（无 scipy）的标准库回退
    _scipy_stats=None


def _betacf(a,b,x,max_iter=200,eps=3e-14):
    """连分式计算不完全贝塔函数（Lentz 方法），用于 t 分布 p 值回退。"""
    qab,qap,qam=a+b,a+1.0,a-1.0
    c=1.0;d=1.0-qab*x/qab
    if abs(d)<1e-30:d=1e-30
    d=1.0/d;h=d
    for m in range(1,max_iter+1):
        m2=2*m
        aa=m*(b-m)*x/((qam+m2)*(a+m2))
        d=1.0+aa*d
        if abs(d)<1e-30:d=1e-30
        c=1.0+aa/c
        if abs(c)<1e-30:c=1e-30
        d=1.0/d;h*=d*c
        aa=-(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        d=1.0+aa*d
        if abs(d)<1e-30:d=1e-30
        c=1.0+aa/c
        if abs(c)<1e-30:c=1e-30
        d=1.0/d;de=d*c;h*=de
        if abs(de-1.0)<eps:break
    return h


def _betainc(a,b,x):
    if x<=0:return 0.0
    if x>=1:return 1.0
    lbeta=math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log(1-x)
    bt=math.exp(lbeta)
    if x < (a+1)/(a+b+2):
        return bt*_betacf(a,b,x)/a
    return 1-bt*_betacf(b,a,1-x)/b


def t_sf_two_sided(t_abs,df):
    """双侧 p 值 = I_{df/(df+t²)}(df/2, 1/2)。"""
    if _scipy_stats is not None:
        return float(2*_scipy_stats.t.sf(t_abs,df))
    x=df/(df+t_abs*t_abs)
    return max(0.0,min(1.0,_betainc(df/2.0,0.5,x)))


def t_ppf_975(df):
    """t 分布 0.975 分位数（二分求逆），用于 95% 置信区间回退。"""
    if _scipy_stats is not None:
        return float(_scipy_stats.t.ppf(.975,df))
    lo,hi=0.0,100.0
    target=0.975
    for _ in range(80):
        mid=(lo+hi)/2
        # 单侧 CDF：1 - 0.5*I_{df/(df+x²)}(df/2,1/2)
        x=df/(df+mid*mid)
        cdf=1-0.5*_betainc(df/2.0,0.5,x)
        if cdf<target:lo=mid
        else:hi=mid
    return (lo+hi)/2


def welch(students,plan):
    selected=[s for s in students if plan.campus=='全部' or s.campus==plan.campus]
    kept=[s for s in selected if s.score is not None and (not plan.exclude_absent or not s.absent)]
    control=[s.score for s in kept if s.group=='control']
    treatment=[s.score for s in kept if s.group=='intervention']
    n0,n1=len(control),len(treatment)
    if min(n0,n1)<2: raise ValueError('每组至少需要 2 个有效成绩；请调整教学点或补录样本')
    m0,m1=sum(control)/n0,sum(treatment)/n1
    v0=sum((x-m0)**2 for x in control)/(n0-1)
    v1=sum((x-m1)**2 for x in treatment)/(n1-1)
    se2=v0/n0+v1/n1
    if se2<=0: raise ValueError('两组方差均为零，无法进行 Welch 检验')
    df=se2**2/((v0/n0)**2/(n0-1)+(v1/n1)**2/(n1-1))
    se=math.sqrt(se2);delta=m1-m0;t=delta/se
    p=t_sf_two_sided(abs(t),df)
    margin=t_ppf_975(df)*se
    pooled=math.sqrt(((n0-1)*v0+(n1-1)*v1)/(n0+n1-2))
    digest=hashlib.sha256(json.dumps([(s.code,s.score,s.group,s.absent) for s in kept],ensure_ascii=False).encode()).hexdigest()
    return {'control_n':n0,'intervention_n':n1,'excluded':len(selected)-len(kept),'total_selected':len(selected),'control_mean':m0,'intervention_mean':m1,'difference':delta,'t':t,'df':df,'p_value':p,'ci_low':delta-margin,'ci_high':delta+margin,'cohen_d':delta/pooled if pooled else None,'significant':p<.05,'checksum':digest,'sample_snapshot':[{'code':s.code,'group':s.group,'score':s.score,'campus':s.campus} for s in kept],'method':'Welch 独立样本 t 检验；双侧 α=0.05；干预组减对照组；95% CI'}
