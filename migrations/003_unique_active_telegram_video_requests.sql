-- Prevent the Telegram bot from giving the same source video to multiple users.
--
-- Cancelled and failed requests are intentionally excluded so an operator can
-- release a video back into rotation if it was not actually used.

CREATE UNIQUE INDEX IF NOT EXISTS uq_telegram_video_requests_active_video_path
ON telegram_video_requests(video_path)
WHERE status IN ('sent', 'reported', 'archived');
