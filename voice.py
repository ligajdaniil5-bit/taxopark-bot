# -*- coding: utf-8 -*-
"""Распознавание голосовых сообщений через Groq Whisper."""
import os

import requests

API = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELS = ["whisper-large-v3", "whisper-large-v3-turbo"]


def transcribe(data, filename="voice.ogg", lang="ru"):
    """Принимает байты аудио, возвращает текст или ''."""
    key = os.getenv("GROQ_KEY", "").strip()
    if not key:
        raise RuntimeError("не задан GROQ_KEY")

    last = ""
    for model in MODELS:
        try:
            r = requests.post(
                API,
                headers={"Authorization": "Bearer " + key},
                files={"file": (filename, data, "audio/ogg")},
                data={"model": model, "language": lang, "response_format": "json"},
                timeout=90,
            )
            if r.status_code != 200:
                last = "%s: HTTP %s %s" % (model, r.status_code, r.text[:120])
                continue
            return (r.json().get("text") or "").strip()
        except Exception as e:
            last = "%s: %s" % (model, e)
            continue
    raise RuntimeError(last or "не удалось распознать")
