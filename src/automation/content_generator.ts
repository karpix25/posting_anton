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

    async generateCaption(videoPath: string, platform: string, authorName?: string, brandName?: string): Promise<string | null> {
        const client = this.findClientConfig(brandName);

        // If no AI client found for this brand, skip the video
        if (!client) {
            console.log(`⚠️  [Generator] No AI client found for brand "${brandName}". Skipping video.`);
            return null;
        }

        let systemPrompt = client.prompt;

        if (authorName) {
            const hashtagAuthor = authorName.replace(/\s+/g, '');
            systemPrompt += `\n\nВ конце поста ОБЯЗАТЕЛЬНО добавь хештег: #by${hashtagAuthor} (для указания авторства).`;
        }


        // Decode path to ensure LLM gets human-readable text (e.g., "Юлия" instead of "%D0%AE%D0%BB%D0%B8%D1%8F")
        // Also remove 'copy_' artifacts to avoid AI mentioning them
        const decodedPath = decodeURIComponent(videoPath);
        const sanitizedPath = decodedPath.replace(/copy_/gi, '');
        let userPrompt = `Путь к файлу: ${sanitizedPath}. Платформа: ${platform}.`;

        if (platform === 'youtube') {
            userPrompt += `\n\nВАЖНО: Верни результат СТРОГО в формате ниже, без лишнего текста:\n`;
            userPrompt += `[YT_TITLE]\n`;
            userPrompt += `<короткий заголовок, 3-8 слов, без хештегов, максимум 85 символов>\n`;
            userPrompt += `[/YT_TITLE]\n`;
            userPrompt += `[YT_DESCRIPTION]\n`;
            userPrompt += `<полное описание с хештегами, до 4250 символов>\n`;
            userPrompt += `[/YT_DESCRIPTION]\n`;
            userPrompt += `Нельзя добавлять комментарии, markdown, пояснения или дополнительные блоки.\n\n`;
        } else {
            // Instagram / TikTok
            userPrompt += `\n\nВАЖНО: Напиши ТОЛЬКО креативное описание (caption) для поста с хештегами.\n`;
            userPrompt += `Этот текст пойдёт в поле title платформы (instagram_title/tiktok_title), поэтому:\n`;
            userPrompt += `- максимум 1870 символов;\n`;
            userPrompt += `- никаких заголовков, никаких "$$$", никаких служебных меток;\n`;
            userPrompt += `- только финальный текст публикации.\n`;
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

    private findClientConfig(brandName?: string) {
        if (!this.config.clients || !brandName) return null;

        // Match client by brand name (case-insensitive)
        const normalizedBrand = brandName.toLowerCase().trim();
        return this.config.clients.find(c => {
            // Match by client name
            const clientName = (c.name || '').toLowerCase().trim();
            if (clientName === normalizedBrand) return true;

            // Fallback to regex if provided (for backwards compatibility)
            if (c.regex) {
                try {
                    return new RegExp(c.regex, 'i').test(brandName);
                } catch (e) {
                    console.warn(`Invalid regex for client ${c.name}: ${c.regex}`);
                    return false;
                }
            }

            return false;
        });
    }
}
