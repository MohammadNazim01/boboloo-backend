import logging
from openai import AsyncOpenAI
from app.core.config import settings

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


class AIService:

    @staticmethod
    async def generate_child_reply(
        question: str,
        child_age: int,
        interests: list[str] | None = None,
        settings: dict | None = None,
        history: list | None = None,
        conversation_id: str | None = None
    ) -> str:

        logging.info(f"Conversation: {conversation_id}")
        logging.info(f"User question: {question}")

        interests_text = ", ".join(interests) if interests else ""

        complexity = 3
        speech_speed = 2

        if settings:
            complexity = settings.get("word_complexity", 3)
            speech_speed = settings.get("speech_speed", 2)

        system_prompt = f"""
You are Boboloo, a friendly AI toy for children.
Child age: {child_age}
Child interests: {interests_text}
Speech speed level: {speech_speed}
Word complexity level: {complexity}
Rules:
- Use very simple language
- Maximum 2 sentences
- Be playful and encouraging
- Speak appropriately for the child's age
"""

        messages = [{"role": "system", "content": system_prompt}]

        if history:
            messages.extend(history)

        messages.append({"role": "user", "content": question})
        logging.info(f"user message: {messages}")

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.7,
            max_tokens=60,
        )

        reply = response.choices[0].message.content

        logging.info(f"AI reply: {reply}")

        return reply