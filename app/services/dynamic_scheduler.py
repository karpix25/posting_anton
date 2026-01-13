import asyncio
import logging
from datetime import datetime
import pytz
from croniter import croniter
from app.config import settings
from app.worker import generate_daily_schedule

from app.database import async_session_maker
from app.services.config_db import get_db_config

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
        logger.info("[DynamicScheduler] Started watching DB config for schedules.")

    async def loop(self):
        while self._running:
            try:
                # Use Timezone-aware check
                # Default to Moscow for this project as requested
                tz = pytz.timezone('Europe/Moscow')
                now = datetime.now(tz)
                
                # Check only once per minute
                current_minute = now.strftime("%Y-%m-%d %H:%M")
                
                if self._last_run_minute != current_minute:
                    await self.check_and_run(now)
                    self._last_run_minute = current_minute
                
                # Sleep a bit
                await asyncio.sleep(10) 

            except Exception as e:
                logger.error(f"[DynamicScheduler] Error in loop: {e}")
                await asyncio.sleep(60)

    async def check_and_run(self, now: datetime):
        # Reload config from DB
        try:
            async with async_session_maker() as session:
                config = await get_db_config(session)
                
            cron_expression = config.cronSchedule
            
            if not cron_expression:
                return

            # croniter match returns True if 'now' matches the cron pattern
            if croniter.match(cron_expression, now):
                logger.info(f"[DynamicScheduler] ⏰ Schedule '{cron_expression}' matched at {now}. Triggering automation!")
                
                # Trigger Celery Task
                generate_daily_schedule.delay()
        except Exception as e:
            logger.error(f"[DynamicScheduler] Check failed: {e}")

dynamic_scheduler = DynamicScheduler()
