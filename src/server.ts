import express, { Request, Response } from 'express';
import bodyParser from 'body-parser';
import cors from 'cors';
import { YandexDiskClient } from './automation/yandex'; // Import Yandex client
import { PlatformManager } from './automation/platforms';
import { StatsManager } from './automation/stats';
import { DatabaseService } from './automation/db';
import * as fs from 'fs';
import * as path from 'path';

const app = express();
const PORT = process.env.PORT || 3001;

// DATA_DIR for persistence (mounted volume)
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, '../data');

// Ensure data dir exists
if (!fs.existsSync(DATA_DIR)) {
    try {
        fs.mkdirSync(DATA_DIR, { recursive: true });
    } catch (e) {
        console.error('Failed to create DATA_DIR', e);
    }
}

const CONFIG_PATH = path.join(DATA_DIR, 'config.json');
const USED_HASHES_PATH = path.join(DATA_DIR, 'used_hashes.json');

// Initialize database service
const dbConnectionString = process.env.DATABASE_URL || '';
const db = new DatabaseService(dbConnectionString);
db.init().catch(err => console.error('[Server] Failed to initialize database:', err));

app.use(cors());
app.use(bodyParser.json());
app.use(express.static(path.join(__dirname, '../public'))); // Serve UI

// Helper to extract metadata (Theme, Brand, and Author)
function extractMetadata(filePath: string, aliasesMap?: Record<string, string[]>) {
    const normalize = (str: string) => str.toLowerCase().replace(/ё/g, "е").replace(/[^a-zа-я0-9]/g, "");

    // Structural extraction: /ВИДЕО/Автор/Категория/Бренд/файл.mp4
    const parts = filePath.split('/').filter(p => p.length > 0 && p !== 'disk:');
    let categoryCandidate = '';
    let brandCandidate = '';
    let authorCandidate = 'unknown';

    const videoIndex = parts.findIndex(p => {
        const lower = p.toLowerCase();
        return lower === 'video' || lower === 'видео';
    });

    if (videoIndex !== -1) {
        // Structure: /ВИДЕО/Автор/Категория/Бренд/файл.mp4
        //              0      1      2         3      4

        // Author is immediately after VIDEO
        if (videoIndex + 1 < parts.length) {
            authorCandidate = parts[videoIndex + 1];
        }

        // КАТЕГОРИЯ (theme) - 3rd level
        if (videoIndex + 2 < parts.length) {
            categoryCandidate = parts[videoIndex + 2];
        }

        // БРЕНД - 4th level
        if (videoIndex + 3 < parts.length) {
            brandCandidate = parts[videoIndex + 3];
        }
    } else if (parts.length >= 2) {
        // Fallback: Parent folder
        categoryCandidate = parts[parts.length - 2];
    }

    // DEBUG: Log first 5 paths to see what's being parsed
    if (Math.random() < 0.001) { // Log ~0.1% of paths
        console.log('[extractMetadata] DEBUG:');
        console.log('  filePath:', filePath);
        console.log('  parts:', parts);
        console.log('  videoIndex:', videoIndex);
        console.log('  categoryCandidate:', categoryCandidate);
    }

    // Use the RAW category name, not alias matching
    // This shows actual folder names like "Beauty не трогать" instead of "toplash"
    let theme = 'unknown';
    if (categoryCandidate) {
        const rawTheme = categoryCandidate.toLowerCase().trim();
        theme = rawTheme; // Default to raw name

        // Check aliases if provided
        if (aliasesMap) {
            for (const [groupKey, aliases] of Object.entries(aliasesMap)) {
                // Check if rawTheme is in aliases array
                // normalize aliases too just in case
                if (aliases.some(a => a.toLowerCase().trim() === rawTheme)) {
                    theme = groupKey;
                    break;
                }
                // Also check if matches the group key itself? Usually redundant if key is listed in aliases or expected behavior
                if (groupKey.toLowerCase().trim() === rawTheme) {
                    theme = groupKey;
                    break;
                }
            }
        }
    }

    // Normalize brand name
    const brand = brandCandidate ? normalize(brandCandidate) : 'unknown';

    return { theme, brand, author: authorCandidate };
}

// --- API Endpoints ---

// Cache for stats (avoid re-scanning Yandex on every page load)
let statsCache: any = null;
let statsCacheTime: number = 0;
const STATS_CACHE_TTL = 10 * 60 * 1000; // 10 minutes

// Files cache (raw Yandex data, separate from stats)
let filesCache: any[] = [];
let filesCacheTime: number = 0;
const FILES_CACHE_TTL = 30 * 60 * 1000; // 30 minutes - files change rarely

// Get Statistics
app.get('/api/stats', async (req, res) => {
    try {
        const forceRefresh = req.query.refresh === 'true';
        const now = Date.now();

        // Return cache if valid and not forcing refresh
        if (!forceRefresh && statsCache && (now - statsCacheTime) < STATS_CACHE_TTL) {
            console.log('[Stats] Returning cached stats (age: ' + Math.round((now - statsCacheTime) / 1000) + 's)');
            return res.json(statsCache);
        }

        console.log('[Stats] Fetching fresh stats from Yandex Disk...');
        const stats = {
            totalVideos: 0,
            publishedCount: 0,
            byCategory: {} as Record<string, number>,
            byAuthor: {} as Record<string, number>,
            profilesByCategory: {} as Record<string, string[]> // NEW: profiles per category
        };

        let usedSet = new Set<string>();

        // 1. Get History Count
        if (fs.existsSync(USED_HASHES_PATH)) {
            const used = JSON.parse(fs.readFileSync(USED_HASHES_PATH, 'utf-8'));
            if (Array.isArray(used)) {
                stats.publishedCount = used.length;
                usedSet = new Set(used);
            }
        }

        // 2. Get Yandex Stats (Live)
        if (fs.existsSync(CONFIG_PATH)) {
            const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
            const folders: string[] = config.yandexFolders || [];

            if (process.env.YANDEX_TOKEN) {
                const yandex = new YandexDiskClient(process.env.YANDEX_TOKEN);

                let allFiles: any[] = [];
                const now = Date.now();

                // Check if we can use cached files
                const filesExpired = (now - filesCacheTime) > FILES_CACHE_TTL;

                if (!forceRefresh && filesCache.length > 0 && !filesExpired) {
                    // Use cached files
                    console.log(`[Stats] Using cached files (${filesCache.length} files, age: ${Math.round((now - filesCacheTime) / 1000)}s)`);
                    allFiles = filesCache;
                } else {
                    // Fetch fresh files from Yandex
                    try {
                        const reason = forceRefresh ? 'force refresh' : filesExpired ? 'cache expired' : 'no cache';
                        console.log(`[Stats] Fetching files from Yandex Disk (${reason})...`);
                        allFiles = await yandex.listFiles('/', 100000); // 100k limit
                        console.log(`[Stats] ✅ Fetched ${allFiles.length} files from Yandex.`);

                        // Update files cache
                        filesCache = allFiles;
                        filesCacheTime = now;

                        // Debug first few files
                        if (allFiles.length > 0) {
                            console.log(`[Stats DEBUG] First file: ${allFiles[0].path}`);
                            console.log(`[Stats DEBUG] Last file: ${allFiles[allFiles.length - 1].path}`);
                        }
                    } catch (e: any) {
                        console.error('[Stats] Failed to list files', e);
                        // If cache exists (even expired), use it as fallback?
                        // For now, simple fail logic, or empty array
                    }
                }

                // Filter 1: Must be video (yandex.ts handles this via media_type param)
                // Filter 2: Must be within one of the config folders (if folders valid)
                // Filter 3: Must NOT be in usedSet

                console.log('[Stats DEBUG] Configured folders:', folders);
                console.log(`[Stats DEBUG] Filtering ${allFiles.length} files against folders...`);

                const availableFiles = allFiles.filter(f => {
                    // Check if file is inside one of the target folders
                    // f.path looks like "disk:/Folder/File.mp4"

                    let inFolder = false;
                    if (folders.length === 0) inFolder = true;
                    else {
                        // Normalize paths to avoid slash/prefix issues
                        // remove "disk:" and leading/trailing slashes
                        const normPath = f.path.replace(/^disk:\/?/, '').replace(/^\//, '').toLowerCase();

                        inFolder = folders.some(folder => {
                            const normFolder = folder.replace(/^disk:\/?/, '').replace(/^\//, '').toLowerCase();
                            // Use includes instead of startsWith to allow partial matches (e.g. subfolders)
                            return normPath.includes(normFolder);
                        });
                    }

                    // Log exclusion reason for first few failures for debug
                    if (!inFolder && Math.random() < 0.001) {
                        // console.log(`[Stats TRACE] Excluded folder: ${f.path}`);
                    }

                    if (!inFolder) return false;

                    // Check usage
                    if (usedSet.has(f.md5) || usedSet.has(f.path)) {
                        return false;
                    }

                    return true;
                });
                console.log(`[Stats DEBUG] availableFiles after filtering: ${availableFiles.length}`);

                stats.totalVideos = availableFiles.length;

                // Group by Category & Author

                // 1. Pre-fill categories from aliases
                if (config.themeAliases) {
                    for (const key of Object.keys(config.themeAliases)) {
                        stats.byCategory[key] = 0;
                    }
                }

                // 2. Scan ALL files to detect existence of categories/authors
                console.log('[Stats DEBUG] Sample file paths (first 5):');
                allFiles.slice(0, 5).forEach((f, idx) => {
                    console.log(`  ${idx + 1}. ${f.path}`);
                });

                allFiles.forEach(f => {
                    const { theme, brand, author } = extractMetadata(f.path, config.themeAliases);

                    // Debug first few extractions
                    if (allFiles.indexOf(f) < 3) {
                        console.log(`[Stats DEBUG] File: ${f.path}`);
                        console.log(`[Stats DEBUG]   → Author: "${author}", Category: "${theme}", Brand: "${brand}"`);
                    }

                    // Init Category
                    if (theme !== 'unknown') {
                        if (stats.byCategory[theme] === undefined) stats.byCategory[theme] = 0;
                    }
                    // Init Author
                    if (author !== 'unknown') {
                        if (stats.byAuthor[author] === undefined) stats.byAuthor[author] = 0;
                    }
                });

                // 3. Count AVAILABLE videos
                availableFiles.forEach(f => {
                    const { theme, author } = extractMetadata(f.path, config.themeAliases);

                    if (theme !== 'unknown') {
                        stats.byCategory[theme] = (stats.byCategory[theme] || 0) + 1;
                    }
                    if (author !== 'unknown') {
                        stats.byAuthor[author] = (stats.byAuthor[author] || 0) + 1;
                    }
                });
            }
            // Map profiles to categories based on theme_key
            if (config && config.profiles) {
                config.profiles.forEach((profile: any) => {
                    const themeKey = profile.theme_key?.toLowerCase().trim();
                    if (themeKey && themeKey !== 'unknown') {
                        if (!stats.profilesByCategory[themeKey]) {
                            stats.profilesByCategory[themeKey] = [];
                        }
                        // Only add if enabled
                        if (profile.enabled !== false) {
                            stats.profilesByCategory[themeKey].push(profile.username);
                        }
                    } else {
                        // Debug unmapped profiles
                        // console.log(`[Stats DEBUG] Profile ${profile.username} has no valid theme_key: "${profile.theme_key}"`);
                    }
                });
            }
        }

        // Update cache
        statsCache = stats;
        statsCacheTime = Date.now();
        console.log('[Stats] Cache updated');

        res.json(stats);
    } catch (e) {
        console.error('[Stats] Error generating stats:', e);
        res.status(500).json({ error: 'Failed to fetch stats' });
    }
});

// Get Config
app.get('/api/config', (req, res) => {
    // If config.json doesn't exist, try to initialize it
    if (!fs.existsSync(CONFIG_PATH)) {
        // Look for example config in the APP root (not data dir)
        const EXAMPLE_PATH = path.join(__dirname, '../config.example.json');

        if (fs.existsSync(EXAMPLE_PATH)) {
            console.log(`[Server] config.json missing at ${CONFIG_PATH}, copying from ${EXAMPLE_PATH} `);
            fs.copyFileSync(EXAMPLE_PATH, CONFIG_PATH);
        } else {
            console.log('[Server] config.json missing, creating default');
            const defaultConfig = {
                profiles: [],
                limits: { instagram: 10, tiktok: 10, youtube: 2 },
                daysToGenerate: 7,
                clients: []
            };
            fs.writeFileSync(CONFIG_PATH, JSON.stringify(defaultConfig, null, 2));
        }
    }

    // Now read it
    if (fs.existsSync(CONFIG_PATH)) {
        try {
            const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));

            // Ensure essential structure
            if (!config.profiles) config.profiles = [];
            if (!config.clients) config.clients = [];
            if (!config.limits) config.limits = { instagram: 10, tiktok: 10, youtube: 2 };

            // Migration logic: Sync clients from example if they are missing
            try {
                // Try multiple possible paths for example config
                const possiblePaths = [
                    path.join(__dirname, '../config.example.json'),
                    path.join(process.cwd(), 'config.example.json'),
                    '/app/config.example.json'
                ];

                let exampleConfig: any = null;
                for (const p of possiblePaths) {
                    if (fs.existsSync(p)) {
                        try {
                            exampleConfig = JSON.parse(fs.readFileSync(p, 'utf-8'));
                            break;
                        } catch (e) { }
                    }
                }

                if (exampleConfig && exampleConfig.clients && Array.isArray(exampleConfig.clients)) {
                    let changed = false;
                    if (!config.clients) config.clients = [];

                    for (const exampleClient of exampleConfig.clients) {
                        const exists = config.clients.find((c: any) => c.name === exampleClient.name);
                        if (!exists) {
                            console.log(`[Config] Adding missing client '${exampleClient.name}' from template.`);
                            config.clients.push(exampleClient);
                            changed = true;
                        } else {
                            // Optional: Update prompt text if it looks like a default placeholder? 
                            // Better not overwrite user changes.
                        }
                    }

                    if (changed) {
                        console.log('[Config] Saving updated clients list.');
                        fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
                    }
                }

                // Merge theme mappings if missing
                if (exampleConfig && exampleConfig.themeAliases && !config.themeAliases) {
                    console.log('[Config] Merging default theme aliases.');
                    console.log('[Server] Merging themeAliases from example config');
                    config.themeAliases = exampleConfig.themeAliases;
                }
            } catch (e) {
                console.error('[Server] Error during theme alias migration:', e);
            }

            // MIGRATION: Convert old profile format (platform field) to new format (platforms array)
            if (config.profiles && config.profiles.length > 0) {
                const firstProfile = config.profiles[0];

                // Detect old format by presence of 'platform' field
                if (firstProfile && 'platform' in firstProfile && !('platforms' in firstProfile)) {
                    console.log('[Server] 🔄 Migrating old profile format to new multi-platform format...');

                    // Group by username
                    const grouped: Record<string, any> = {};

                    config.profiles.forEach((profile: any) => {
                        if (!grouped[profile.username]) {
                            grouped[profile.username] = {
                                username: profile.username,
                                theme_key: profile.theme_key || '',
                                platforms: [],
                                last_posted: profile.last_posted || {}
                            };
                        }

                        // Add platform to array if not already there
                        if (profile.platform && !grouped[profile.username].platforms.includes(profile.platform)) {
                            grouped[profile.username].platforms.push(profile.platform);
                        }
                    });

                    // Replace with grouped profiles
                    config.profiles = Object.values(grouped);

                    // Save migrated config
                    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
                    console.log(`[Server] ✅ Migration complete: ${config.profiles.length} profiles converted`);
                }
            }

            // MIGRATION: Add enabled field to profiles (default true)
            if (config.profiles && config.profiles.length > 0) {
                let addedEnabled = false;
                config.profiles.forEach((profile: any) => {
                    if (profile.enabled === undefined) {
                        profile.enabled = true;
                        addedEnabled = true;
                    }
                });

                if (addedEnabled) {
                    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
                    console.log('[Server] ✅ Added enabled field to profiles');
                }
            }

            res.json(config);
        } catch (e) {
            console.error('[Server] Failed to parse config.json', e);
            res.status(500).json({ error: 'Invalid config file' });
        }
    } else {
        res.status(500).json({ error: 'Failed to create config' });
    }
});

// Update Config
app.post('/api/config', (req, res) => {
    try {
        const newConfig = req.body;
        // Basic validation
        fs.writeFileSync(CONFIG_PATH, JSON.stringify(newConfig, null, 2));

        // Clear stats cache so it recalculates with new config
        statsCache = null;
        console.log('[Config] Saved. Stats cache cleared (Files cache preserved).');

        res.json({ success: true, message: 'Config saved' });
    } catch (error) {
        res.status(500).json({ error: 'Failed to save config' });
    }
});

// Manual Run Trigger (Placeholder for now)
// Manual Run Trigger
app.post('/api/run', (req, res) => {
    console.log('Starting automation process...');

    // In Docker (prod), we run the compiled JS. In dev, ts-node.
    // For simplicity in this structure, we assume we are running 'node dist/automation/main.js' or similar.
    const { testMode } = req.body;
    const { spawn } = require('child_process');
    const scriptPath = path.join(__dirname, 'automation/main.js');

    const env = { ...process.env };

    if (testMode) {
        console.log('[Server] Running in TEST MODE (Limits = 1)');
        env.FORCE_LIMITS = '1';
    }

    const child = spawn('node', [scriptPath], {
        stdio: 'inherit',
        env
    });

    child.on('error', (err: any) => {
        console.error('Failed to start subprocess:', err);
    });

    child.on('close', (code: number) => {
        console.log(`Automation process exited with code ${code} `);
    });

    // We can spawn a child process or import main() directly if it's async safe
    res.json({ success: true, message: 'Automation process started in background' });
});

// Manual Cleanup Trigger
app.post('/api/cleanup', (req, res) => {
    console.log('Starting cleanup process...');

    // Run compiled JS
    const scriptPath = path.join(__dirname, 'scripts/cleanup.js');
    const { spawn } = require('child_process');

    const child = spawn('node', [scriptPath], {
        stdio: 'inherit',
        env: process.env
    });

    child.on('error', (err: any) => {
        console.error('Failed to start cleanup subprocess:', err);
    });

    child.on('close', (code: number) => {
        console.log(`Cleanup process exited with code ${code}`);
    });

    res.json({ success: true, message: 'Cleanup process started in background' });
});

// Get brand statistics for current month
app.get('/api/brands/stats', async (req, res) => {
    try {
        const month = (req.query.month as string) || new Date().toISOString().substring(0, 7);
        const stats = await db.getBrandStats(month);
        res.json({ success: true, stats, month });
    } catch (error: any) {
        console.error('[API] Failed to get brand stats:', error);
        res.status(500).json({ success: false, error: error.message });
    }
});

// Update brand quota
app.post('/api/brands/quotas', async (req, res) => {
    try {
        const { category, brand, quota } = req.body;

        if (!category || !brand || quota === undefined) {
            return res.status(400).json({
                success: false,
                error: 'Missing required fields: category, brand, quota'
            });
        }

        const month = new Date().toISOString().substring(0, 7);
        await db.updateBrandQuota(category, brand, month, quota);

        // Also update config.json
        const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
        if (!config.brandQuotas) config.brandQuotas = {};
        if (!config.brandQuotas[category]) config.brandQuotas[category] = {};
        config.brandQuotas[category][brand] = quota;

        fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));

        res.json({ success: true, message: `Updated quota for ${category}:${brand} to ${quota}` });
    } catch (error: any) {
        console.error('[API] Failed to update brand quota:', error);
        res.status(500).json({ success: false, error: error.message });
    }
});

// Sync Profiles from Upload Post API
app.get('/api/profiles/sync', async (req, res) => {
    console.log('[API] /api/profiles/sync requested');
    const apiKey = process.env.UPLOAD_POST_API_KEY;
    if (!apiKey) {
        console.error('[API] Error: UPLOAD_POST_API_KEY is missing in env');
        return res.status(500).json({ error: 'UPLOAD_POST_API_KEY not configured on server' });
    }

    try {
        console.log('[API] Fetching profiles using Axios...');
        const axios = require('axios');
        const USER_PROFILES_API_URL = 'https://api.upload-post.com/api/uploadposts/users';

        const response = await axios.get(USER_PROFILES_API_URL, {
            headers: { 'Authorization': `Apikey ${apiKey} ` }
        });

        if (response.data.success) {
            console.log(`[API] Sync success.Found ${response.data.profiles?.length || 0} profiles.`);
            res.json({ success: true, profiles: response.data.profiles });
        } else {
            console.error('[API] Sync failed:', response.data.message);
            res.status(400).json({ error: response.data.message || 'Failed to fetch profiles' });
        }
    } catch (error: any) {
        console.error('[API] Profile sync error:', error.message, error.response?.data);
        res.status(500).json({ error: 'Failed to sync profiles: ' + error.message });
    }
});

// Health Check
app.get('/health', (req, res) => {
    res.status(200).send('OK');
});

// Start Server
const server = app.listen(PORT, () => {
    console.log(`Dashboard running at http://localhost:${PORT}`);
});

// Graceful Shutdown
process.on('SIGTERM', () => {
    console.log('SIGTERM received. Closing HTTP server...');
    server.close(() => {
        console.log('HTTP server closed.');
        process.exit(0);
    });
});

process.on('SIGINT', () => {
    console.log('SIGINT received. Shutting down...');
    server.close(() => {
        console.log('HTTP server closed.');
        process.exit(0);
    });
});
