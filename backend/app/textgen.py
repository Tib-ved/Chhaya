"""The one place the text model is called. Everything it writes is checked by its caller."""
from typing import Callable

TEXT_TIMEOUT_MS = 8000  # below the after-call step's own limit, so a hung call ends by itself.
# Measured on 2026-10-12: with its default thinking the model took 7 to 10 seconds for one
# paragraph and was cut off by this limit; with thinking set to "low" it took about 2.

TextModel = Callable[[str, str], str]  # (instruction, prompt) -> what the model wrote


def make_text_model(settings) -> TextModel | None:
    """A function that asks the text model, or None when no text model is configured."""
    if not settings.text_model.strip() or not settings.gcp_project.strip():
        return None
    clients: list = []  # made on first use, so that start-up never needs credentials

    def text_model(instruction: str, prompt: str) -> str:
        from google import genai

        if not clients:
            clients.append(genai.Client(
                vertexai=True, project=settings.gcp_project, location=settings.text_location,
                http_options={"timeout": TEXT_TIMEOUT_MS},
            ))
        config: dict = {"system_instruction": instruction, "temperature": 0.2}
        if settings.text_thinking.strip():
            config["thinking_config"] = {"thinking_level": settings.text_thinking.strip()}
        for attempt in (1, 2):
            try:
                response = clients[0].models.generate_content(
                    model=settings.text_model, contents=prompt, config=config
                )
                return response.text or ""
            except Exception:
                # The service now and then answers with a passing server error (seen on
                # 2026-10-12, about one call in ten). Once more, then the caller gives up.
                if attempt == 2:
                    raise
        return ""

    return text_model
