import { Pool, PoolClient } from 'pg';
import { ScheduledPost } from './types';

export class DatabaseService {
    private pool: Pool;
    private initialized: boolean = false;

    constructor(connectionString: string) {
        this.pool = new Pool({
            connectionString,
            ssl: connectionString.includes('sslmode=disable') ? false : { rejectUnauthorized: false }
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

            console.log('[DB] Schema initialized (posting_history table ready).');
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

    public async close(): Promise<void> {
        await this.pool.end();
    }
}
