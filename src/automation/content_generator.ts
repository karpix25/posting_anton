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

    async generateCaption(videoPath: string, platform: string, authorName?: string): Promise<string> {
        const client = this.findClientConfig(videoPath);
        let systemPrompt = client ? client.prompt : "Ты — эксперт по SMM."; // Default fallback

        if (authorName) {
            systemPrompt += `\n\nЕсли автор найден (${authorName}), добавь #by${authorName} в конец.`;
        }

        let userPrompt = `Путь к файлу: ${videoPath}. Платформа: ${platform}.`;
        userPrompt += `\n\nВАЖНО: Твой ответ должен состоять из двух частей, разделенных символами "$$$".\n`;
        userPrompt += `Первая часть - это ЗАГОЛОВОК (короткий, цепляющий).\n`;
        userPrompt += `Вторая часть - это ОПИСАНИЕ (с хештегами).\n`;
        userPrompt += `Пример ответа: Крутая новинка! $$$ Смотрите, какая удобная штука. #хештег\n\n`;
        userPrompt += `ЗАПРЕЩЕНО писать технические инструкции (типа "Нажмите кнопку", "Опубликуйте"). Пиши ТОЛЬКО креативный текст для самого поста от имени автора.`;

        const response = await this.openai.chat.completions.create({
            model: 'gpt-4o', // Use gpt-4o or gpt-4-turbo for better instruction following if available, else gpt-4
            messages: [
                { role: 'system', content: systemPrompt },
                { role: 'user', content: userPrompt }
            ]
        });

        return response.choices[0].message.content || '';
    }

    private findClientConfig(path: string) {
        if (!this.config.clients) return null;
        return this.config.clients.find(c => {
            try {
                return new RegExp(c.regex, 'i').test(path);
            } catch (e) {
                console.warn(`Invalid regex for client ${c.name}: ${c.regex}`);
                return false;
            }
        });
    }
}
