import express from 'express';
import bodyParser from 'body-parser';
import cors from 'cors';
import fs from 'fs';
import path from 'path';

const app = express();
const PORT = 3000;
const CONFIG_PATH = path.join(__dirname, '../config.json');

app.use(cors());
app.use(bodyParser.json());
app.use(express.static(path.join(__dirname, '../public'))); // Serve UI

// --- API Endpoints ---

// Get Config
app.get('/api/config', (req, res) => {
    if (fs.existsSync(CONFIG_PATH)) {
        const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
        res.json(config);
    } else {
        res.status(404).json({ error: 'Config not found' });
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
    const apiKey = process.env.UPLOAD_POST_API_KEY;
    if (!apiKey) {
        return res.status(500).json({ error: 'UPLOAD_POST_API_KEY not configured on server' });
    }

    try {
        // We import the client dynamically or duplicates logic, 
        // but since we are in a simple setup, we'll make a direct axios call here 
        // OR better, import the class if we can. 
        // Given compilation complexity with allowJs/ts-node, let's just use axios directly here 
        // to avoid importing from 'automation/platforms' which might bring other deps.
        // ACTUALLY, we can just copy the simple fetch logic to avoid coupling issues in this simple script.

        const axios = require('axios');
        const USER_PROFILES_API_URL = 'https://api.upload-post.com/api/uploadposts/users';

        const response = await axios.get(USER_PROFILES_API_URL, {
            headers: { 'Authorization': `Apikey ${apiKey}` }
        });

        if (response.data.success) {
            res.json({ success: true, profiles: response.data.profiles });
        } else {
            res.status(400).json({ error: response.data.message || 'Failed to fetch profiles' });
        }
    } catch (error: any) {
        console.error('Profile sync error:', error.message);
        res.status(500).json({ error: 'Failed to sync profiles: ' + error.message });
    }
});

// Start Server
app.listen(PORT, () => {
    console.log(`Dashboard running at http://localhost:${PORT}`);
});
