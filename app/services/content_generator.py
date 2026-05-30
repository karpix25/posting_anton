import openai
from typing import Optional, List
from app.config import settings
from app.config import ClientConfig
from app.services.generation_prompt_rules import build_cta_case_instruction

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

        decoded_path = video_path # Decode if needed, Python usually handles strings unicode natively
        system_prompt = client_config.prompt
        system_prompt += build_cta_case_instruction(system_prompt, decoded_path)
        
        if author_name:
             hashtag_author = author_name.replace(" ", "")
             system_prompt += f"\n\nВ конце поста ОБЯЗАТЕЛЬНО добавь хештег: #by{hashtag_author} (для указания авторства)."

        user_prompt = f"Путь к файлу: {decoded_path}. Платформа: {platform}."
        
        if platform == 'youtube':
            user_prompt += "\n\nВАЖНО: Верни результат СТРОГО в формате ниже, без любого лишнего текста:\n"
            user_prompt += "[YT_TITLE]\n"
            user_prompt += "<короткий заголовок, 3-8 слов, без хештегов, максимум 85 символов>\n"
            user_prompt += "[/YT_TITLE]\n"
            user_prompt += "[YT_DESCRIPTION]\n"
            user_prompt += "<полное описание с хештегами, до 4250 символов>\n"
            user_prompt += "[/YT_DESCRIPTION]\n"
            user_prompt += "Нельзя добавлять комментарии, пояснения, markdown или дополнительные блоки.\n"
            user_prompt += "Если сомневаешься, используй этот же формат в точности.\n"
        else:
            user_prompt += "\n\nВАЖНО: Напиши ТОЛЬКО креативное описание (caption) для поста с хештегами.\n"
            user_prompt += "Этот текст будет отправлен в поле title платформы (instagram_title/tiktok_title), поэтому:\n"
            user_prompt += "- максимум 1870 символов;\n"
            user_prompt += "- без заголовков, без меток, без \"$\" и без служебных блоков;\n"
            user_prompt += "- только готовый финальный текст публикации.\n"
            
        user_prompt += "ЗАПРЕЩЕНО писать технические инструкции. Пиши ТОЛЬКО креативный текст."

        try:
            response = await self.client.chat.completions.create(
                model="openai/gpt-4o-mini", # User requested cheaper model
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            print(f"[Generator] Error: {e}")
            return None

content_generator = ContentGenerator()
