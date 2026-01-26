"""
Voice Tool - транскрипция голосовых через Whisper
"""
import os
import tempfile
from openai import OpenAI


class VoiceTool:
    def __init__(self, api_key: str = None):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    def transcribe(self, audio_path: str) -> str:
        """Транскрибировать аудио файл в текст"""
        with open(audio_path, "rb") as f:
            response = self.client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ru"
            )
        return response.text

    async def transcribe_telegram_voice(self, bot, voice_file_id: str) -> str:
        """Скачать и транскрибировать голосовое из Telegram"""
        file = await bot.get_file(voice_file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await bot.download_file(file.file_path, tmp.name)
            text = self.transcribe(tmp.name)
            os.unlink(tmp.name)
            return text
