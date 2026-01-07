import { execSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

export class AutomationScheduler {
    private intervalId: NodeJS.Timeout | null = null;
    private configPath: string;
    private lastRunMinute: string | null = null; // Prevent multiple runs in same minute

    constructor(configPath: string) {
        this.configPath = configPath;
    }

    start() {
        console.log('[AutoScheduler] 🕐 Starting built-in scheduler (checks every 10 seconds)...');

        // Check every 10 seconds to not miss the minute window
        this.intervalId = setInterval(() => {
            this.checkAndRun();
        }, 10 * 1000);

        // Also check immediately on start (after 5s delay for server to fully start)
        setTimeout(() => this.checkAndRun(), 5000);
    }

    stop() {
        if (this.intervalId) {
            clearInterval(this.intervalId);
            this.intervalId = null;
            console.log('[AutoScheduler] ⏹️  Stopped');
        }
    }

    private checkAndRun() {
        try {
            const config = this.loadConfig();
            if (this.shouldRun(config)) {
                this.runAutomation();
            }
        } catch (error: any) {
            console.error('[AutoScheduler] ❌ Error:', error.message);
        }
    }

    private loadConfig() {
        if (!fs.existsSync(this.configPath)) {
            throw new Error('config.json not found');
        }
        return JSON.parse(fs.readFileSync(this.configPath, 'utf-8'));
    }

    private shouldRun(config: any): boolean {
        const schedule = config.schedule || {};

        if (!schedule.enabled) {
            // Only log once per minute
            const now = new Date();
            const currentMinute = now.toISOString().substring(0, 16); // YYYY-MM-DDTHH:MM
            if (this.lastRunMinute !== currentMinute) {
                // Log strictly every 5-10 mins to reduce spam or just silence it
                // console.log('[AutoScheduler] ⏸️  Scheduling is disabled'); 
                this.lastRunMinute = currentMinute;
            }
            return false;
        }

        const timezone = schedule.timezone || 'Europe/Moscow';
        const targetTime = schedule.dailyRunTime || '00:01';

        const now = new Date();
        // Use reliable Intl formatter to get time in target timezone
        const formatter = new Intl.DateTimeFormat('en-US', {
            timeZone: timezone,
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        });

        const parts = formatter.formatToParts(now);
        const hour = parts.find(p => p.type === 'hour')?.value || '00';
        const minute = parts.find(p => p.type === 'minute')?.value || '00';

        // Construct HH:MM format
        const currentTimeStr = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;

        // Create unique key for this minute to prevent duplicate runs
        // We use the *server* UTC minute for deduplication key, which is stable/unique per minute globally
        const currentMinute = now.toISOString().substring(0, 16);

        // Debug logging (optional, maybe too spammy if every 10s? logic below filters spam)
        // console.log(`[AutoScheduler] Checking: ${currentTimeStr} (Moscow) vs ${targetTime} (Target)`);

        const matches = currentTimeStr === targetTime;

        if (matches) {
            // Check if we already ran this minute
            if (this.lastRunMinute === currentMinute) {
                return false;
            }

            console.log(`[AutoScheduler] ✅ Time matched! ${currentTimeStr} === ${targetTime} (${timezone})`);
            this.lastRunMinute = currentMinute;
            return true;
        }

        return false;
    }

    private runAutomation() {
        console.log('[AutoScheduler] 🚀 Starting automation...');
        try {
            execSync('npm run automation', {
                stdio: 'inherit',
                cwd: path.join(__dirname, '../..')
            });
            console.log('[AutoScheduler] ✅ Automation completed successfully');
        } catch (error: any) {
            console.error('[AutoScheduler] ❌ Automation failed:', error.message);
        }
    }
}
