import * as cron from 'node-cron';
import { execSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

export class AutomationScheduler {
    private task: cron.ScheduledTask | null = null;
    private configPath: string;
    private currentCronExpression: string | null = null;

    constructor(configPath: string) {
        this.configPath = configPath;
    }

    start() {
        console.log('[AutoScheduler] 🕐 Initializing node-cron based scheduler...');

        // Check config every minute and restart cron if settings changed
        this.updateSchedule();
        setInterval(() => this.updateSchedule(), 60000);
    }

    stop() {
        if (this.task) {
            this.task.stop();
            this.task = null;
            console.log('[AutoScheduler] ⏹️ Stopped');
        }
    }

    private updateSchedule() {
        try {
            const config = this.loadConfig();
            const schedule = config.schedule || {};

            if (!schedule.enabled) {
                if (this.task) {
                    this.task.stop();
                    this.task = null;
                    this.currentCronExpression = null;
                }
                return;
            }

            const timezone = schedule.timezone || 'Europe/Moscow';
            const targetTime = schedule.dailyRunTime || '00:01';
            const [hour, minute] = targetTime.split(':');

            // Cron format: minute hour * * *
            const cronExpression = `${minute} ${hour} * * *`;

            // Only recreate if schedule changed
            if (this.currentCronExpression !== cronExpression) {
                // Stop existing task
                if (this.task) {
                    this.task.stop();
                }

                console.log(`[AutoScheduler] ✅ Scheduling automation: ${cronExpression} (${timezone})`);
                console.log(`[AutoScheduler] ℹ️  This means: every day at ${targetTime} in ${timezone}`);

                this.task = cron.schedule(cronExpression, () => {
                    this.runAutomation();
                }, {
                    timezone: timezone
                });

                this.currentCronExpression = cronExpression;
            }
        } catch (error: any) {
            console.error('[AutoScheduler] ❌ Error updating schedule:', error.message);
        }
    }

    private loadConfig() {
        if (!fs.existsSync(this.configPath)) {
            throw new Error('config.json not found');
        }
        return JSON.parse(fs.readFileSync(this.configPath, 'utf-8'));
    }

    private runAutomation() {
        console.log('[AutoScheduler] 🚀 Cron triggered! Starting automation...');
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
