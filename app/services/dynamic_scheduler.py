import asyncio
import logging
from datetime import datetime
from croniter import croniter
from app.config import settings
from app.worker import generate_daily_schedule

logger = logging.getLogger(__name__)

class DynamicScheduler:
    def __init__(self):
        self._task = None
        self._running = False
        self._last_run_minute = None

    def start(self):
        if self._running: return
        self._running = True
        self._task = asyncio.create_task(self.loop())
        logger.info("[DynamicScheduler] Started watching config.json for schedules.")

    async def loop(self):
        while self._running:
            try:
                now = datetime.now()
                # Check only once per minute
                current_minute = now.strftime("%Y-%m-%d %H:%M")
                
                if self._last_run_minute != current_minute:
                    await self.check_and_run(now)
                    self._last_run_minute = current_minute
                
                # Sleep a bit, but not too long to miss a minute if we sleep 60s
                # Align to next minute start? Or just sleep 15s.
                await asyncio.sleep(10) 

            except Exception as e:
                logger.error(f"[DynamicScheduler] Error in loop: {e}")
                await asyncio.sleep(60)

    async def check_and_run(self, now: datetime):
        # Reload config to get latest schedule
        # Note: settings.load_legacy_config() reads from disk.
        config = settings.load_legacy_config()
        
        cron_expression = config.cronSchedule
        
        if not cron_expression:
            # Default fallback if missing? Or just skip.
            # TS code had defaults. Let's assume user sets it if enabled.
            return

        try:
            # croniter match returns True if 'now' matches the cron pattern
            # Note: croniter match checks if the current time coincides with the schedule 
            # with minute precision.
            if croniter.match(cron_expression, now):
                logger.info(f"[DynamicScheduler] ⏰ Schedule '{cron_expression}' matched at {now}. Triggering automation!")
                
                # Trigger Celery Task
                generate_daily_schedule.delay()
                
        except Exception as e:
            logger.warning(f"[DynamicScheduler] Invalid cron schedule '{cron_expression}': {e}")

dynamic_scheduler = DynamicScheduler()
