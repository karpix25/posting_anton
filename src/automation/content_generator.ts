import OpenAI from 'openai';
import { AutomationConfig } from './types';

export class ContentGenerator {
    private openai: OpenAI;
    private config: AutomationConfig;

    constructor(apiKey: string, config: AutomationConfig) {
        this.openai = new OpenAI({
            apiKey,
            baseURL: 'https://openrouter.ai/api/v1',
            defaultHeaders: {
                'HTTP-Referer': 'https://github.com/karpix25/posting_anton', // Optional, good practice for OpenRouter
                'X-Title': 'Automation Dashboard',
            }
        });
        this.config = config;
    }

    async generateCaption(videoPath: string, platform: string, authorName?: string, profileTheme?: string): Promise<string> {
        const client = this.findClientConfig(profileTheme);
        let systemPrompt = client ? client.prompt : "Ты — эксперт по SMM."; // Default fallback

        if (authorName) {
            const hashtagAuthor = authorName.replace(/\s+/g, '');
            systemPrompt += `\n\nВ конце поста ОБЯЗАТЕЛЬНО добавь хештег: #by${hashtagAuthor} (для указания авторства).`;
        }


        // Decode path to ensure LLM gets human-readable text (e.g., "Юлия" instead of "%D0%AE%D0%BB%D0%B8%D1%8F")
        const decodedPath = decodeURIComponent(videoPath);
        let userPrompt = `Путь к файлу: ${decodedPath}. Платформа: ${platform}.`;

        if (platform === 'youtube') {
            userPrompt += `\n\nВАЖНО: Твой ответ должен состоять из двух частей, разделенных символами "$$$".\n`;
            userPrompt += `Первая часть - это ЗАГОЛОВОК (1-5 слов, цепляющий).\n`;
            userPrompt += `Вторая часть - это ОПИСАНИЕ (с хештегами).\n`;
            userPrompt += `Пример ответа: Крутая новинка! $$$ Смотрите, какая удобная штука. #хештег\n\n`;
        } else {
            // Instagram / TikTok
            userPrompt += `\n\nВАЖНО: Напиши ТОЛЬКО креативное описание (caption) для поста с хештегами.\n`;
            userPrompt += `НИКАКИХ заголовков, никаких "$$$". Только сам текст поста.\n`;
        }

        userPrompt += `ЗАПРЕЩЕНО писать технические инструкции (типа "Нажмите кнопку", "Опубликуйте", "Вот шаги"). Пиши ТОЛЬКО креативный текст для самого поста от имени автора.`;

        // Debug
        // Debug - Show full prompts
        console.log('[Generator] ========== FULL PROMPT ==========');
        console.log('[Generator] System Prompt:', systemPrompt);
        console.log('[Generator] User Prompt:', userPrompt);
        console.log('[Generator] =====================================');

        const response = await this.openai.chat.completions.create({
            model: 'gpt-4o',
            messages: [
                { role: 'system', content: systemPrompt },
                { role: 'user', content: userPrompt }
            ]
        });

        return response.choices[0].message.content || '';
    }

    private findClientConfig(profileTheme?: string) {
        if (!this.config.clients || !profileTheme) return null;

        // Match client by theme (case-insensitive)
        const normalizedTheme = profileTheme.toLowerCase().trim();
        return this.config.clients.find(c => {
            // Match by client name or regex (for backwards compatibility)
            const clientName = (c.name || '').toLowerCase().trim();
            if (clientName === normalizedTheme) return true;

            // Fallback to regex if provided
            if (c.regex) {
                try {
                    return new RegExp(c.regex, 'i').test(profileTheme);
                } catch (e) {
                    console.warn(`Invalid regex for client ${c.name}: ${c.regex}`);
                    return false;
                }
            }

            return false;
        });
    }
}
