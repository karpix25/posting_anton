import { Pool, PoolClient } from 'pg';
import { ClientConfig, ScheduledPost } from './types';

type AIClientsStorage =
    | { kind: 'table'; schemaName: string; tableName: string }
    | { kind: 'posting_system_config' }
    | { kind: 'none' };

export class DatabaseService {
    private pool: Pool;
    private initialized: boolean = false;
    private aiClientsStorageCache: AIClientsStorage | null = null;

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

    public isReady(): boolean {
        return this.initialized;
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

            // Unified JSON config storage (source of truth candidate)
            await client.query(`
                CREATE TABLE IF NOT EXISTS posting_system_config (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            `);

            console.log('[DB] Schema initialized (posting_history, brand_stats, posting_system_config ready).');
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

    public async getAIClients(): Promise<ClientConfig[]> {
        if (!this.initialized) {
            console.warn('[DB] Skipping getAIClients because DB is not initialized.');
            return [];
        }

        try {
            const storage = await this.detectAIClientsStorage();

            if (storage.kind === 'posting_system_config') {
                const result = await this.pool.query(
                    'SELECT value FROM posting_system_config WHERE key = $1',
                    ['main_config']
                );
                const row = result.rows[0];
                const value = row?.value || {};
                const clients = Array.isArray(value.clients) ? value.clients : [];
                return this.sanitizeClients(clients);
            }

            if (storage.kind === 'table') {
                return await this.getAIClientsFromTable(storage.schemaName, storage.tableName);
            }

            return [];
        } catch (error) {
            console.error('[DB] Failed to get AI clients:', error);
            return [];
        }
    }

    public async replaceAIClients(clients: ClientConfig[]): Promise<void> {
        if (!this.initialized) {
            console.warn('[DB] Skipping replaceAIClients because DB is not initialized.');
            return;
        }

        const safeClients = this.sanitizeClients(clients);
        const storage = await this.detectAIClientsStorage();

        // Safety guard: never wipe existing clients with an accidental empty payload.
        if (safeClients.length === 0) {
            const existing = await this.getAIClients();
            if (existing.length > 0) {
                console.warn('[DB] Refusing to replace non-empty AI clients with empty payload.');
                return;
            }
        }

        if (storage.kind === 'none') {
            console.warn('[DB] No AI clients storage detected.');
            return;
        }

        const dbClient = await this.pool.connect();
        try {
            await dbClient.query('BEGIN');
            if (storage.kind === 'posting_system_config') {
                const existing = await dbClient.query(
                    'SELECT value FROM posting_system_config WHERE key = $1 FOR UPDATE',
                    ['main_config']
                );
                const row = existing.rows[0];
                const current = row?.value && typeof row.value === 'object' ? row.value : {};
                const nextValue = { ...current, clients: safeClients };

                if (row) {
                    await dbClient.query(
                        'UPDATE posting_system_config SET value = $1, updated_at = NOW() WHERE key = $2',
                        [nextValue, 'main_config']
                    );
                } else {
                    await dbClient.query(
                        'INSERT INTO posting_system_config (key, value, updated_at) VALUES ($1, $2, NOW())',
                        ['main_config', nextValue]
                    );
                }
            } else {
                await this.replaceAIClientsInTable(dbClient, storage.schemaName, storage.tableName, safeClients);
            }

            await dbClient.query('COMMIT');
            console.log(`[DB] Replaced AI clients: ${safeClients.length}`);
        } catch (error) {
            await dbClient.query('ROLLBACK');
            console.error('[DB] Failed to replace AI clients:', error);
            throw error;
        } finally {
            dbClient.release();
        }
    }

    public async getMainConfig(): Promise<any | null> {
        if (!this.initialized) {
            console.warn('[DB] Skipping getMainConfig because DB is not initialized.');
            return null;
        }

        try {
            const result = await this.pool.query(
                'SELECT value FROM posting_system_config WHERE key = $1',
                ['main_config']
            );
            const row = result.rows[0];
            if (!row || !row.value || typeof row.value !== 'object') return null;
            return row.value;
        } catch (error) {
            console.error('[DB] Failed to get main config:', error);
            return null;
        }
    }

    public async saveMainConfig(config: any): Promise<void> {
        if (!this.initialized) {
            console.warn('[DB] Skipping saveMainConfig because DB is not initialized.');
            return;
        }

        try {
            await this.pool.query(
                `
                INSERT INTO posting_system_config (key, value, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                `,
                ['main_config', config || {}]
            );
        } catch (error) {
            console.error('[DB] Failed to save main config:', error);
            throw error;
        }
    }

    private sanitizeClients(clients: any[]): ClientConfig[] {
        if (!Array.isArray(clients)) return [];
        const seen = new Set<string>();
        const sanitized: ClientConfig[] = [];

        for (const raw of clients) {
            if (!raw || typeof raw !== 'object') continue;
            const name = String(raw.name || '').trim();
            if (!name) continue;
            const dedupeKey = name.toLowerCase();
            if (seen.has(dedupeKey)) continue;
            seen.add(dedupeKey);

            const regex = String(raw.regex || '').trim();
            const prompt = String(raw.prompt || '');
            const quotaNumber = raw.quota === undefined || raw.quota === null || raw.quota === ''
                ? undefined
                : Number(raw.quota);

            sanitized.push({
                name,
                regex,
                prompt,
                quota: Number.isFinite(quotaNumber as number) ? quotaNumber : undefined
            });
        }

        return sanitized;
    }

    private quoteIdentifier(identifier: string): string {
        return `"${identifier.replace(/"/g, '""')}"`;
    }

    private async findTableByNames(names: string[]): Promise<{ schemaName: string; tableName: string } | null> {
        const normalized = names.map((n) => n.toLowerCase());
        const result = await this.pool.query(
            `
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND lower(table_name) = ANY($1)
            ORDER BY
              CASE WHEN table_schema = 'public' THEN 0 ELSE 1 END,
              table_schema,
              table_name
            LIMIT 1
            `,
            [normalized]
        );

        const row = result.rows[0];
        if (!row) return null;
        return { schemaName: row.table_schema, tableName: row.table_name };
    }

    private async detectAIClientsStorage(): Promise<AIClientsStorage> {
        if (this.aiClientsStorageCache) return this.aiClientsStorageCache;

        const postingConfigTable = await this.findTableByNames(['posting_system_config']);
        if (postingConfigTable) {
            this.aiClientsStorageCache = { kind: 'posting_system_config' };
            console.log('[DB] AI clients source: posting_system_config(main_config).');
            return this.aiClientsStorageCache;
        }

        const aiClientsDbTable = await this.findTableByNames(['ai_clients_db', 'Ai Clients Db', 'ai clients db']);
        if (aiClientsDbTable) {
            this.aiClientsStorageCache = {
                kind: 'table',
                schemaName: aiClientsDbTable.schemaName,
                tableName: aiClientsDbTable.tableName
            };
            console.log(`[DB] AI clients source: table ${aiClientsDbTable.schemaName}.${aiClientsDbTable.tableName}`);
            return this.aiClientsStorageCache;
        }

        const aiClientsTable = await this.findTableByNames(['ai_clients']);
        if (aiClientsTable) {
            this.aiClientsStorageCache = {
                kind: 'table',
                schemaName: aiClientsTable.schemaName,
                tableName: aiClientsTable.tableName
            };
            console.log(`[DB] AI clients source: fallback table ${aiClientsTable.schemaName}.${aiClientsTable.tableName}`);
            return this.aiClientsStorageCache;
        }

        this.aiClientsStorageCache = { kind: 'none' };
        console.warn('[DB] AI clients source not found.');
        return this.aiClientsStorageCache;
    }

    private async getTableColumns(schemaName: string, tableName: string): Promise<Map<string, string>> {
        const result = await this.pool.query(
            `SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2`,
            [schemaName, tableName]
        );
        const map = new Map<string, string>();
        for (const row of result.rows) {
            const col = String(row.column_name);
            map.set(col.toLowerCase(), col);
        }
        return map;
    }

    private async getAIClientsFromTable(schemaName: string, tableName: string): Promise<ClientConfig[]> {
        const columns = await this.getTableColumns(schemaName, tableName);
        const nameCol = columns.get('name');
        const regexCol = columns.get('regex');
        const promptCol = columns.get('prompt');
        if (!nameCol || !regexCol || !promptCol) return [];

        const quotaCol = columns.get('quota');
        const sortCol = columns.get('sort_order');
        const updatedAtCol = columns.get('updated_at');
        const tableRef = `${this.quoteIdentifier(schemaName)}.${this.quoteIdentifier(tableName)}`;

        const selectParts = [
            `${this.quoteIdentifier(nameCol)} AS name`,
            `${this.quoteIdentifier(regexCol)} AS regex`,
            `${this.quoteIdentifier(promptCol)} AS prompt`,
            quotaCol ? `${this.quoteIdentifier(quotaCol)} AS quota` : 'NULL::int AS quota'
        ];

        const orderParts: string[] = [];
        if (sortCol) orderParts.push(`${this.quoteIdentifier(sortCol)} ASC`);
        if (updatedAtCol) orderParts.push(`${this.quoteIdentifier(updatedAtCol)} DESC`);
        orderParts.push(`${this.quoteIdentifier(nameCol)} ASC`);

        const query = `SELECT ${selectParts.join(', ')} FROM ${tableRef} ORDER BY ${orderParts.join(', ')}`;
        const result = await this.pool.query(query);
        return this.sanitizeClients(result.rows);
    }

    private async replaceAIClientsInTable(
        dbClient: PoolClient,
        schemaName: string,
        tableName: string,
        clients: ClientConfig[]
    ): Promise<void> {
        const columns = await this.getTableColumns(schemaName, tableName);
        const nameCol = columns.get('name');
        const regexCol = columns.get('regex');
        const promptCol = columns.get('prompt');
        if (!nameCol || !regexCol || !promptCol) {
            throw new Error(`Table ${tableName} does not have required columns: name, regex, prompt`);
        }

        const quotaCol = columns.get('quota');
        const sortCol = columns.get('sort_order');
        const updatedAtCol = columns.get('updated_at');
        const tableRef = `${this.quoteIdentifier(schemaName)}.${this.quoteIdentifier(tableName)}`;

        await dbClient.query(`DELETE FROM ${tableRef}`);

        for (let i = 0; i < clients.length; i++) {
            const client = clients[i];
            const insertCols = [
                this.quoteIdentifier(nameCol),
                this.quoteIdentifier(regexCol),
                this.quoteIdentifier(promptCol)
            ];
            const values: any[] = [client.name, client.regex || '', client.prompt || ''];

            if (quotaCol) {
                insertCols.push(this.quoteIdentifier(quotaCol));
                values.push(client.quota === undefined ? null : client.quota);
            }
            if (sortCol) {
                insertCols.push(this.quoteIdentifier(sortCol));
                values.push(i);
            }
            if (updatedAtCol) {
                insertCols.push(this.quoteIdentifier(updatedAtCol));
                values.push(new Date());
            }

            const placeholders = values.map((_, idx) => `$${idx + 1}`).join(', ');
            const sql = `INSERT INTO ${tableRef} (${insertCols.join(', ')}) VALUES (${placeholders})`;
            await dbClient.query(sql, values);
        }
    }

    public async close(): Promise<void> {
        await this.pool.end();
    }
}
