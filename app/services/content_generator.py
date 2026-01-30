import openai
from typing import Optional, List
from app.config import settings
from app.config import ClientConfig

import logging

logger = logging.getLogger(__name__)

class ContentGenerator:
    def __init__(self):
        self.client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://github.com/karpix25/posting_anton",
                "X-Title": "Automation Dashboard"
            }
        )

    async def generate_caption(self, video_path: str, platform: str, 
                               client_config: Optional[ClientConfig], 
                               author_name: Optional[str] = None) -> Optional[str]:
        if not client_config:
            print("[Generator] No client config provided.")
            return None

        system_prompt = client_config.prompt
        
        if author_name:
             hashtag_author = author_name.replace(" ", "")
             system_prompt += f"\n\nВ конце поста ОБЯЗАТЕЛЬНО добавь хештег: #by{hashtag_author} (для указания авторства)."

        decoded_path = video_path # Decode if needed, Python usually handles strings unicode natively
        
        user_prompt = f"Путь к файлу: {decoded_path}. Платформа: {platform}."
        
        if platform == 'youtube':
            user_prompt += "\n\nВАЖНО: Твой ответ должен состоять из двух частей, разделенных символами \"$$$\".\n"
            user_prompt += "Первая часть - это ЗАГОЛОВОК (1-5 слов, цепляющий).\n"
            user_prompt += "Вторая часть - это ОПИСАНИЕ (с хештегами).\n"
        else:
            user_prompt += "\n\nВАЖНО: Напиши ТОЛЬКО креативное описание (caption) для поста с хештегами.\n"
            user_prompt += "НИКАКИХ заголовков, никаких \"$$$\". Только сам текст поста.\n"
            
        user_prompt += "ЗАПРЕЩЕНО писать технические инструкции. Пиши ТОЛЬКО креативный текст."

        try:
            response = await self.client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                timeout=45.0  # Explicit timeout
            )
            content = response.choices[0].message.content or ""
            if not content:
                logger.warning("[Generator] Received empty response from LLM")
            return content
            
        except Exception as e:
            logger.error(f"[Generator] OpenRouter API Failed: {e}")
            raise e

content_generator = ContentGenerator()
