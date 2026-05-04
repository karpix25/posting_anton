#!/usr/bin/env node

/**
 * Automation Scheduler
 * Runs automation based on schedule configuration in config.json
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const CONFIG_PATH = path.join(__dirname, '../config.json');

function log(message) {
    console.log(`[Scheduler] ${new Date().toISOString()} - ${message}`);
}

function loadConfig() {
    if (!fs.existsSync(CONFIG_PATH)) {
        log('❌ config.json not found!');
        process.exit(1);
    }
    return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
}

function shouldRun(config) {
    const schedule = config.schedule || {};

    // Check if scheduling is enabled
    if (!schedule.enabled) {
        log('⏸️  Scheduling is disabled in config');
        return false;
    }

    const timezone = schedule.timezone || 'Europe/Moscow';
    const targetTime = schedule.dailyRunTime || '00:01';

    // Get current time in specified timezone
    const now = new Date();
    const currentTimeStr = now.toLocaleString('en-US', {
        timeZone: timezone,
        hour12: false,
        hour: '2-digit',
        minute: '2-digit'
    });

    // Extract only HH:MM from the full datetime string
    // Format is "M/D/YYYY, HH:MM" or "MM/DD/YYYY, HH:MM"
    const timePart = currentTimeStr.split(', ')[1]; // Get "HH:MM"

    log(`Current time (${timezone}): ${currentTimeStr} (extracted: ${timePart})`);
    log(`Target time: ${targetTime}`);

    return timePart === targetTime;
}

function runAutomation() {
    log('🚀 Starting automation...');
    try {
        execSync('npm run automation', {
            stdio: 'inherit',
            cwd: path.join(__dirname, '..')
        });
        log('✅ Automation completed successfully');
    } catch (error) {
        log(`❌ Automation failed: ${error.message}`);
        process.exit(1);
    }
}

// Main execution
const config = loadConfig();

if (shouldRun(config)) {
    runAutomation();
} else {
    log('⏭️  Not time to run yet');
}
