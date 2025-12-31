import express from 'express';
import bodyParser from 'body-parser';
import cors from 'cors';
import fs from 'fs';
import path from 'path';
import { YandexDiskClient } from './automation/yandex'; // Import Yandex client

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

app.use(cors());
app.use(bodyParser.json());
app.use(express.static(path.join(__dirname, '../public'))); // Serve UI

// Helper to extract metadata (Theme and Author)
function extractMetadata(filePath: string, aliasesMap?: Record<string, string[]>) {
    const normalize = (str: string) => str.toLowerCase().replace(/ё/g, "е").replace(/[^a-zа-я0-9]/g, "");

    // Structural extraction: .../VIDEO/Name/Category/...
    const parts = filePath.split('/').filter(p => p.length > 0 && p !== 'disk:');
    let categoryCandidate = '';
    let authorCandidate = 'unknown';

    const videoIndex = parts.findIndex(p => {
        const lower = p.toLowerCase();
        return lower === 'video' || lower === 'видео';
    });

    if (videoIndex !== -1) {
        // Author is usually immediately after VIDEO
        if (videoIndex + 1 < parts.length) {
            authorCandidate = parts[videoIndex + 1];
        }
        // Category is after Author
        if (videoIndex + 2 < parts.length) {
            categoryCandidate = parts[videoIndex + 2];
        }
    } else if (parts.length >= 2) {
        // Fallback: Parent folder
        categoryCandidate = parts[parts.length - 2];
    }

    let theme = 'unknown';
    if (categoryCandidate) {
        const normCandidate = normalize(categoryCandidate);
        theme = normCandidate; // Default to raw name

        if (aliasesMap) {
            for (const [key, list] of Object.entries(aliasesMap)) {
                for (const alias of list) {
                    if (normCandidate.includes(normalize(alias))) {
                        theme = key;
                        break;
                    }
                }
            }
        }
    }

    return { theme, author: authorCandidate };
}

// --- API Endpoints ---

// Get Statistics
app.get('/api/stats', async (req, res) => {
    try {
        const stats = {
            totalVideos: 0,
            publishedCount: 0,
            byCategory: {} as Record<string, number>,
            byAuthor: {} as Record<string, number>
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

                // Yandex 'files' endpoint returns minimal info for ALL files flatly.
                // It ignores 'path' param. So we fetch ONCE globally.
                // Limit to 5000 to cover most cases.
                let allFiles: any[] = [];
                try {
                    console.log(`[Stats] fetching files...`);
                    // Request up to 100k files (will auto-retry with 50k/20k if timeout)
                    allFiles = await yandex.listFiles('/', 100000);
                    console.log(`[Stats DEBUG] Fetched ${allFiles.length} raw files from Yandex.`);
                    if (allFiles.length > 0) {
                        console.log(`[Stats DEBUG] First file: ${allFiles[0].path}`);
                        console.log(`[Stats DEBUG] Last file: ${allFiles[allFiles.length - 1].path}`);
                    }
                } catch (e) {
                    console.error('[Stats] Failed to list files', e);
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
                allFiles.forEach(f => {
                    const { theme, author } = extractMetadata(f.path, config.themeAliases);

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
        }

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
            console.log(`[Server] config.json missing at ${CONFIG_PATH}, copying from ${EXAMPLE_PATH}`);
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
                    config.themeAliases = exampleConfig.themeAliases;
                    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
                }

            } catch (e) {
                console.warn('[Config] Migration warning:', e);
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
        // Basic validation could go here
        fs.writeFileSync(CONFIG_PATH, JSON.stringify(newConfig, null, 2));
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
        console.log(`Automation process exited with code ${code}`);
    });

    // We can spawn a child process or import main() directly if it's async safe
    res.json({ success: true, message: 'Automation process started in background' });
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
            headers: { 'Authorization': `Apikey ${apiKey}` }
        });

        if (response.data.success) {
            console.log(`[API] Sync success. Found ${response.data.profiles?.length || 0} profiles.`);
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
