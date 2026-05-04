import * as fs from 'fs';
import * as path from 'path';

export interface Statistics {
    published_total: number;
    deleted_total: number;
    last_updated: string;
    by_platform: {
        instagram: number;
        tiktok: number;
        youtube: number;
    };
}

export class StatsManager {
    private statsPath: string;
    private stats: Statistics;

    constructor(dataDir: string) {
        this.statsPath = path.join(dataDir, 'stats.json');
        this.stats = this.load();
    }

    private load(): Statistics {
        if (fs.existsSync(this.statsPath)) {
            try {
                const data = fs.readFileSync(this.statsPath, 'utf-8');
                return JSON.parse(data);
            } catch (error) {
                console.warn('[Stats] Failed to load stats.json, creating new:', error);
            }
        }

        // Default stats
        return {
            published_total: 0,
            deleted_total: 0,
            last_updated: new Date().toISOString(),
            by_platform: {
                instagram: 0,
                tiktok: 0,
                youtube: 0
            }
        };
    }

    private save(): void {
        try {
            this.stats.last_updated = new Date().toISOString();
            fs.writeFileSync(this.statsPath, JSON.stringify(this.stats, null, 2));
        } catch (error) {
            console.error('[Stats] Failed to save stats.json:', error);
        }
    }

    incrementPublished(platform: 'instagram' | 'tiktok' | 'youtube'): void {
        this.stats.published_total++;
        this.stats.by_platform[platform]++;
        this.save();
        console.log(`[Stats] Published: ${this.stats.published_total} total, ${platform}: ${this.stats.by_platform[platform]}`);
    }

    incrementDeleted(): void {
        this.stats.deleted_total++;
        this.save();
        console.log(`[Stats] Deleted: ${this.stats.deleted_total} total`);
    }

    getStats(): Statistics {
        return { ...this.stats };
    }

    reset(): void {
        this.stats = {
            published_total: 0,
            deleted_total: 0,
            last_updated: new Date().toISOString(),
            by_platform: {
                instagram: 0,
                tiktok: 0,
                youtube: 0
            }
        };
        this.save();
        console.log('[Stats] Statistics reset');
    }
}
