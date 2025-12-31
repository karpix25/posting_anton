import express from 'express';
import bodyParser from 'body-parser';
import cors from 'cors';
import fs from 'fs';
import path from 'path';
import { YandexDiskClient } from './automation/yandex'; // Import Yandex client

const app = express();
const PORT = process.env.PORT || 3001;
const CONFIG_PATH = path.join(__dirname, '../config.json');
const USED_HASHES_PATH = path.join(__dirname, 'automation/used_hashes.json'); // Adjust path based on build structure
// In prod, main.ts is in dist/automation, server is in dist/. 
// used_hashes is likely alongside main.ts or in app root? 
// main.ts: path.join(__dirname, 'used_hashes.json'); -> dist/automation/used_hashes.json
// server.ts: path.join(__dirname, 'automation/used_hashes.json'); -> dist/automation/used_hashes.json
// Seems correct relative to server.ts in dist/

app.use(cors());
app.use(bodyParser.json());
app.use(express.static(path.join(__dirname, '../public'))); // Serve UI

// Helper to extract theme (duplicated from scheduler to avoid complex deps if lazy)
function extractTheme(filePath: string): string {
    const parts = filePath.split('/');
    if (parts.length >= 4) {
        return parts[3].toLowerCase().replace(/ё/g, "е").replace(/[^a-zа-я0-9]/g, "");
    }
    return 'unknown';
}

// --- API Endpoints ---

// Get Statistics
app.get('/api/stats', async (req, res) => {
    try {
        const stats = {
            totalVideos: 0,
            publishedCount: 0, // Effectively "Published & Deleted" or just "Processed"
            byCategory: {} as Record<string, number>
        };

        // 1. Get History Count
        if (fs.existsSync(USED_HASHES_PATH)) {
            const used = JSON.parse(fs.readFileSync(USED_HASHES_PATH, 'utf-8'));
            stats.publishedCount = Array.isArray(used) ? used.length : 0;
        }

        // 2. Get Yandex Stats (Live)
        // We need config to know folders
        if (fs.existsSync(CONFIG_PATH)) {
            const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
            const folders = config.yandexFolders || [];

            if (folders.length > 0 && process.env.YANDEX_TOKEN) {
                const yandex = new YandexDiskClient(process.env.YANDEX_TOKEN);

                let allFiles: any[] = [];
                // We'll limit this to avoid timeouts on huge disks, or assume reasonable size
                for (const folder of folders) {
                    try {
                        const files = await yandex.listFiles(folder, 2000); // Higher limit?
                        allFiles = allFiles.concat(files);
                    } catch (e) {
                        console.error(`[Stats] Failed to list ${folder}`, e);
                    }
                }

                stats.totalVideos = allFiles.length;

                // Group by Category
                allFiles.forEach(f => {
                    const theme = extractTheme(f.path);
                    stats.byCategory[theme] = (stats.byCategory[theme] || 0) + 1;
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
        const EXAMPLE_PATH = path.join(__dirname, '../config.example.json');
        if (fs.existsSync(EXAMPLE_PATH)) {
            console.log('[Server] config.json missing, copying from config.example.json');
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

            // Migration logic: If clients are empty, try to populate from example
            if (config.clients.length === 0) {
                const EXAMPLE_PATH = path.join(__dirname, '../config.example.json');
                if (fs.existsSync(EXAMPLE_PATH)) {
                    try {
                        const exampleConfig = JSON.parse(fs.readFileSync(EXAMPLE_PATH, 'utf-8'));
                        if (exampleConfig.clients && exampleConfig.clients.length > 0) {
                            console.log('[Server] Migrating clients from config.example.json');
                            config.clients = exampleConfig.clients;
                            // Save back to make it permanent
                            fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
                        }
                    } catch (e) {
                        console.warn('[Server] Failed to read example config for migration', e);
                    }
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
    const { spawn } = require('child_process');

    const scriptPath = path.join(__dirname, 'automation/main.js'); // Assuming built structure
    // If running in dev with ts-node, this might need adjustment, but for Docker it's fine.

    const child = spawn('node', [scriptPath], {
        stdio: 'inherit',
        env: { ...process.env }
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
