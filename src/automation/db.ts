import { Pool, PoolClient } from 'pg';
import { ScheduledPost } from './types';

export class DatabaseService {
    private pool: Pool;
    private initialized: boolean = false;

    constructor(connectionString: string) {
        const sslEnabled = process.env.DB_SSL !== 'false' && process.env.NODE_ENV !== 'development';
        this.pool = new Pool({
            connectionString,
            ssl: sslEnabled ? { rejectUnauthorized: false } : false
        });

        // Error handling for idle clients
        this.pool.on('error', (err, client) => {
            console.error('[DB] Unexpected error on idle client', err);
            // Don't exit, just log
        });
    }

    public async init(): Promise<void> {
        if (this.initialized) return;

        let client: PoolClient | null = null;
        try {
            console.log('[DB] Connecting to database...');
            client = await this.pool.connect();
            console.log('[DB] Connected successfully.');

            // Create table for posting history
            // Columns: id, posted_at, profile, platform, video_path, video_name, author, status, meta
            await client.query(`
                CREATE TABLE IF NOT EXISTS posting_history (
                    id SERIAL PRIMARY KEY,
                    posted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    profile_username VARCHAR(255) NOT NULL,
                    platform VARCHAR(50) NOT NULL,
                    video_path TEXT,
                    video_name TEXT,
                    author VARCHAR(255),
                    status VARCHAR(50) DEFAULT 'success',
                    meta JSONB
                );
            `);

            // Create index on author for faster KPI queries
            await client.query(`CREATE INDEX IF NOT EXISTS idx_posting_history_author ON posting_history(author);`);
            // Create index on date
            await client.query(`CREATE INDEX IF NOT EXISTS idx_posting_history_date ON posting_history(posted_at);`);

            // Create table for brand quota tracking
            await client.query(`
                CREATE TABLE IF NOT EXISTS brand_stats (
                    id SERIAL PRIMARY KEY,
                    category VARCHAR(100) NOT NULL,
                    brand VARCHAR(100) NOT NULL,
                    month VARCHAR(7) NOT NULL,
                    published_count INT DEFAULT 0,
                    quota INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(category, brand, month)
                );
            `);

            // Create indexes for brand_stats
            await client.query(`CREATE INDEX IF NOT EXISTS idx_brand_stats_month ON brand_stats(month);`);
            await client.query(`CREATE INDEX IF NOT EXISTS idx_brand_stats_category_brand ON brand_stats(category, brand);`);
            await client.query(`CREATE INDEX IF NOT EXISTS idx_brand_stats_lookup ON brand_stats(category, brand, month);`);

            console.log('[DB] Schema initialized (posting_history and brand_stats tables ready).');
            this.initialized = true;
        } catch (error) {
            console.error('[DB] Failed to initialize database:', error);
            // We don't throw here to ensure the app can still run without DB if network fails,
            // but we log heavily.
        } finally {
            if (client) client.release();
        }
    }

    public async logPost(post: ScheduledPost, status: 'success' | 'failed' = 'success', errorMsg?: string): Promise<void> {
        if (!this.initialized) {
            console.warn('[DB] Skipping logPost because DB is not initialized.');
            return;
        }

        const author = this.extractAuthor(post.video.path);

        const query = `
            INSERT INTO posting_history 
            (profile_username, platform, video_path, video_name, author, status, meta)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        `;

        const meta = errorMsg ? { error: errorMsg } : {};

        try {
            await this.pool.query(query, [
                post.profile.username,
                post.platform,
                post.video.path,
                post.video.name,
                author,
                status,
                JSON.stringify(meta)
            ]);
            console.log(`[DB] Logged ${status} post for ${author} (Video: ${post.video.name})`);
        } catch (error) {
            console.error('[DB] Failed to log post:', error);
        }
    }

    private extractAuthor(path: string): string {
        // Same logic as in main.ts / scheduler.ts
        // /ВИДЕО/Author/Theme...
        const normalized = path.replace(/\\/g, '/');
        const parts = normalized.split('/');
        const idx = parts.findIndex(p => p.toLowerCase() === 'видео' || p.toLowerCase() === 'video');
        if (idx !== -1 && idx + 1 < parts.length) {
            return parts[idx + 1];
        }
        return 'unknown';
    }

    /**
     * Get brand statistics for a specific month
     * @param month Format: 'YYYY-MM' (e.g., '2026-01')
     * @returns Map of "category:brand" to { published_count, quota }
     */
    public async getBrandStats(month: string): Promise<Record<string, { published_count: number; quota: number }>> {
        if (!this.initialized) {
            console.warn('[DB] Skipping getBrandStats because DB is not initialized.');
            return {};
        }

        try {
            const result = await this.pool.query(
                'SELECT category, brand, published_count, quota FROM brand_stats WHERE month = $1',
                [month]
            );

            const stats: Record<string, { published_count: number; quota: number }> = {};
            for (const row of result.rows) {
                const key = `${row.category}:${row.brand}`;
                stats[key] = {
                    published_count: row.published_count,
                    quota: row.quota
                };
            }

            return stats;
        } catch (error) {
            console.error('[DB] Failed to get brand stats:', error);
            return {};
        }
    }

    /**
     * Increment published count for a brand
     * @param category Category name (e.g., 'smart')
     * @param brand Brand name (e.g., 'gqbox')
     * @param month Format: 'YYYY-MM'
     */
    public async incrementBrandCount(category: string, brand: string, month: string): Promise<void> {
        if (!this.initialized) {
            console.warn('[DB] Skipping incrementBrandCount because DB is not initialized.');
            return;
        }

        try {
            await this.pool.query(`
                INSERT INTO brand_stats (category, brand, month, published_count, quota)
                VALUES ($1, $2, $3, 1, 0)
                ON CONFLICT (category, brand, month)
                DO UPDATE SET 
                    published_count = brand_stats.published_count + 1,
                    updated_at = NOW()
            `, [category, brand, month]);

            console.log(`[DB] Incremented count for ${category}:${brand} in ${month}`);
        } catch (error) {
            console.error('[DB] Failed to increment brand count:', error);
        }
    }

    /**
     * Update quota for a brand
     * @param category Category name
     * @param brand Brand name
     * @param month Format: 'YYYY-MM'
     * @param quota New quota value
     */
    public async updateBrandQuota(category: string, brand: string, month: string, quota: number): Promise<void> {
        if (!this.initialized) {
            console.warn('[DB] Skipping updateBrandQuota because DB is not initialized.');
            return;
        }

        try {
            await this.pool.query(`
                INSERT INTO brand_stats (category, brand, month, quota, published_count)
                VALUES ($1, $2, $3, $4, 0)
                ON CONFLICT (category, brand, month)
                DO UPDATE SET 
                    quota = $4,
                    updated_at = NOW()
            `, [category, brand, month, quota]);

            console.log(`[DB] Updated quota for ${category}:${brand} to ${quota}`);
        } catch (error) {
            console.error('[DB] Failed to update brand quota:', error);
        }
    }

    public async close(): Promise<void> {
        await this.pool.end();
    }
}
