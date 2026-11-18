# -*- coding: utf-8 -*-
"""
Самообновление бота без панели BotHost.

Скачивает архив репозитория с GitHub, заменяет .py-файлы в /app
и перезапускает контейнер через внутренний API агента BotHost.
Нужен потому, что у BotHost нет API пересборки — только restart.
"""
import io
import os
import shutil
import tarfile
import urllib.request

REPO = "ligajdaniil5-bit/taxopark-bot"
ARCHIVE = "https://codeload.github.com/%s/tar.gz/refs/heads/main" % REPO
KEEP_EXT = ("py", "txt", "csv", "version", "json")


def _app_dir():
    return os.path.dirname(os.path.abspath(__file__))


def fetch_archive():
    req = urllib.request.Request(ARCHIVE, headers={"User-Agent": "dsh-self-update"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def apply_update():
    """Возвращает (сколько файлов обновлено, список имён)."""
    data = fetch_archive()
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    app = _app_dir()
    updated = []
    for m in tf.getmembers():
        if not m.isfile():
            continue
        parts = m.name.split("/", 1)
        if len(parts) != 2:
            continue
        rel = parts[1]
        if not rel or "." not in rel:
            continue
        if rel.split(".")[-1].lower() not in KEEP_EXT:
            continue
        dest = os.path.join(app, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest) or app, exist_ok=True)
        with tf.extractfile(m) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        updated.append(rel)
    return len(updated), updated


def restart_self():
    """Перезапускает контейнер через внутренний агент BotHost."""
    bot_id = os.getenv("BOT_ID", "").strip()
    base = os.getenv("BOTHOST_AGENT_URL", "http://agent:8000").rstrip("/")
    body = b"{}"
    req = urllib.request.Request(
        base + "/api/bots/self/restart",
        data=body,
        headers={"X-Bot-ID": bot_id, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")
