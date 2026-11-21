from sqlalchemy import String,Float,Boolean,Integer,ForeignKey,JSON,Text,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from src.config.database import Base
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    email:Mapped[str]=mapped_column(String(120),unique=True)
    role:Mapped[str]=mapped_column(String(20))
    password_hash:Mapped[str]=mapped_column(String(200))
class Student(Base):
    __tablename__='students'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(50),unique=True)
    name:Mapped[str]=mapped_column(String(80))
    campus:Mapped[str]=mapped_column(String(50))
    group:Mapped[str]=mapped_column(String(20))
    score:Mapped[float|None]=mapped_column(Float,nullable=True)
    absent:Mapped[bool]=mapped_column(Boolean,default=False)
class Plan(Base):
    __tablename__='plans'
    id:Mapped[int]=mapped_column(primary_key=True)
    title:Mapped[str]=mapped_column(String(120),unique=True)
    campus:Mapped[str]=mapped_column(String(50),default='全部')
    exclude_absent:Mapped[bool]=mapped_column(Boolean,default=True)
    description:Mapped[str]=mapped_column(Text,default='')
class Report(Base):
    __tablename__='reports'
    id:Mapped[int]=mapped_column(primary_key=True)
    plan_id:Mapped[int]=mapped_column(ForeignKey('plans.id'))
    status:Mapped[str]=mapped_column(String(20),default='queued')
    result:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    error:Mapped[str]=mapped_column(Text,default='')
    conclusion:Mapped[str]=mapped_column(Text,default='')
    published:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[str]=mapped_column(String(40))
class Audit(Base):
    __tablename__='audit_logs'
    id:Mapped[int]=mapped_column(primary_key=True)
    actor:Mapped[str]=mapped_column(String(120))
    action:Mapped[str]=mapped_column(String(200))
    status:Mapped[int]=mapped_column(Integer)
    created_at:Mapped[str]=mapped_column(String(40))
class PrivacyBudget(Base):
    """每个辅导方案一份累计隐私预算；跨报告共用，成绩修订不重置。"""
    __tablename__='privacy_budgets'
    id:Mapped[int]=mapped_column(primary_key=True)
    plan_id:Mapped[int]=mapped_column(ForeignKey('plans.id'),unique=True)
    cap_epsilon:Mapped[float]=mapped_column(Float)
    spent_epsilon:Mapped[float]=mapped_column(Float,default=0.0)
    updated_at:Mapped[str]=mapped_column(String(40))
class ShareRequest(Base):
    """对外共享申请：从报告样本快照聚合，只产出加噪匿名均分。"""
    __tablename__='share_requests'
    id:Mapped[int]=mapped_column(primary_key=True)
    report_id:Mapped[int]=mapped_column(ForeignKey('reports.id'))
    plan_id:Mapped[int]=mapped_column(ForeignKey('plans.id'))
    idempotency_key:Mapped[str]=mapped_column(String(80),unique=True)
    epsilon:Mapped[float]=mapped_column(Float)
    status:Mapped[str]=mapped_column(String(20),default='pending')
    # 确定性噪声种子：同一申请重试返回完全相同的噪声结果
    noise_seed:Mapped[int]=mapped_column(Integer)
    # 公开下载令牌：不暴露自增 id，免登录获取对外内容
    download_token:Mapped[str|None]=mapped_column(String(64),unique=True,nullable=True)
    # 对外内容（仅加噪均分与元数据），生成成功后写入
    public_payload:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    # 申请时锁定的样本指纹；成绩修订后旧申请仍按旧快照口径
    sample_checksum:Mapped[str]=mapped_column(String(80),default='')
    fail_injected:Mapped[bool]=mapped_column(Boolean,default=False)
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    error:Mapped[str]=mapped_column(Text,default='')
    created_by:Mapped[str]=mapped_column(String(120))
    created_at:Mapped[str]=mapped_column(String(40))
    updated_at:Mapped[str]=mapped_column(String(40))
class PrivacyLedger(Base):
    """扣费流水：与共享结果在同一事务写入，只增不改。"""
    __tablename__='privacy_ledger'
    __table_args__=(UniqueConstraint('share_request_id',name='uq_ledger_share_once'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    plan_id:Mapped[int]=mapped_column(ForeignKey('plans.id'))
    share_request_id:Mapped[int]=mapped_column(ForeignKey('share_requests.id'))
    epsilon:Mapped[float]=mapped_column(Float)
    cap_epsilon:Mapped[float]=mapped_column(Float)
    spent_after:Mapped[float]=mapped_column(Float)
    created_at:Mapped[str]=mapped_column(String(40))
