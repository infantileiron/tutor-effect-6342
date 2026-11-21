-- 差分隐私共享：累计预算、共享申请、扣费流水。
-- 启动使用 SQLAlchemy metadata.create_all 幂等建表；本文件为 canonical schema 留档。
CREATE TABLE IF NOT EXISTS privacy_budgets (
  id SERIAL PRIMARY KEY,
  plan_id INTEGER NOT NULL UNIQUE REFERENCES plans(id),
  cap_epsilon DOUBLE PRECISION NOT NULL,
  spent_epsilon DOUBLE PRECISION NOT NULL DEFAULT 0,
  updated_at VARCHAR(40) NOT NULL
);
CREATE TABLE IF NOT EXISTS share_requests (
  id SERIAL PRIMARY KEY,
  report_id INTEGER NOT NULL REFERENCES reports(id),
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  idempotency_key VARCHAR(80) UNIQUE NOT NULL,
  epsilon DOUBLE PRECISION NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  noise_seed INTEGER NOT NULL,
  download_token VARCHAR(64) UNIQUE,
  public_payload JSON,
  sample_checksum VARCHAR(80) NOT NULL DEFAULT '',
  fail_injected BOOLEAN NOT NULL DEFAULT FALSE,
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  created_by VARCHAR(120) NOT NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL
);
CREATE TABLE IF NOT EXISTS privacy_ledger (
  id SERIAL PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  share_request_id INTEGER NOT NULL UNIQUE REFERENCES share_requests(id),
  epsilon DOUBLE PRECISION NOT NULL,
  cap_epsilon DOUBLE PRECISION NOT NULL,
  spent_after DOUBLE PRECISION NOT NULL,
  created_at VARCHAR(40) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_share_requests_plan ON share_requests(plan_id);
CREATE INDEX IF NOT EXISTS ix_share_requests_status ON share_requests(status);
CREATE INDEX IF NOT EXISTS ix_privacy_ledger_plan ON privacy_ledger(plan_id);
