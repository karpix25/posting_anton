export const PROMPTS = {
    INSTAGRAM: {
        PAY_WORLD_TG: `Ты — эксперт по нативному женскому сторителлингу... (rest of the prompt for Telegram bot)...`,
        PAY_WORLD_YANDEX: `Ты — эксперт... (rest of the prompt for Yandex service)...`,
        SYNERGETIC: `Ты — эксперт... (prompt for Synergetic products)...`,
    },
    TIKTOK: {
        // ... similar structure
    },
    COMMON: {
        // Shared instructions
    }
};

export const PROMPT_MATCHERS = [
    {
        regex: /tg|telegram|bot|telebot/i,
        key: 'PAY_WORLD_TG'
    },
    {
        regex: /ya|yandex|ynd|market/i,
        key: 'PAY_WORLD_YANDEX'
    },
    {
        regex: /SYNERGETIC/i,
        key: 'SYNERGETIC'
    }
];
