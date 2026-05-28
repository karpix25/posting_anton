-- Telegram approval workflow: user requests -> admin approval -> delivery -> report

ALTER TABLE telegram_video_requests
  ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP NULL;

ALTER TABLE telegram_video_requests
  ADD COLUMN IF NOT EXISTS approved_by BIGINT NULL;

ALTER TABLE telegram_video_requests
  ADD COLUMN IF NOT EXISTS week_key VARCHAR(16) NULL;

ALTER TABLE telegram_video_requests
  ADD COLUMN IF NOT EXISTS assigned_folder_prefix TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_telegram_video_requests_week_key
ON telegram_video_requests(week_key);

CREATE TABLE IF NOT EXISTS telegram_distribution_rules (
  id SERIAL PRIMARY KEY,
  telegram_user_id BIGINT NOT NULL,
  telegram_username VARCHAR(255),
  telegram_full_name VARCHAR(255),
  folder_prefix TEXT NOT NULL,
  weekly_limit INTEGER NOT NULL DEFAULT 1,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_telegram_distribution_rules_user
ON telegram_distribution_rules(telegram_user_id);

DROP INDEX IF EXISTS uq_telegram_video_requests_active_video_path;
CREATE UNIQUE INDEX IF NOT EXISTS uq_telegram_video_requests_active_video_path
ON telegram_video_requests(video_path)
WHERE status IN ('approved', 'sent', 'reported', 'archived')
  AND video_path NOT LIKE 'pending://%';
