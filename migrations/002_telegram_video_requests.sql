-- Telegram bot manual video distribution and reporting.

CREATE TABLE IF NOT EXISTS telegram_video_requests (
  id SERIAL PRIMARY KEY,
  telegram_user_id BIGINT NOT NULL,
  telegram_username VARCHAR(255),
  telegram_full_name VARCHAR(255),
  brand VARCHAR(255) NOT NULL,
  video_path TEXT NOT NULL,
  video_name TEXT NOT NULL,
  youtube_title TEXT,
  youtube_description TEXT,
  status VARCHAR(32) NOT NULL DEFAULT 'sent',
  published_url TEXT,
  archive_path TEXT,
  error_message TEXT,
  requested_at TIMESTAMP DEFAULT NOW(),
  reported_at TIMESTAMP,
  archived_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_telegram_video_requests_user ON telegram_video_requests(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_telegram_video_requests_username ON telegram_video_requests(telegram_username);
CREATE INDEX IF NOT EXISTS idx_telegram_video_requests_brand ON telegram_video_requests(brand);
CREATE INDEX IF NOT EXISTS idx_telegram_video_requests_video_path ON telegram_video_requests(video_path);
CREATE INDEX IF NOT EXISTS idx_telegram_video_requests_status ON telegram_video_requests(status);
CREATE INDEX IF NOT EXISTS idx_telegram_video_requests_requested_at ON telegram_video_requests(requested_at);
