# -*- coding: utf-8 -*-
"""
Картинка-отчёт по заработку: кто сколько заработал + разбивка по владельцам.

Бот присылает её вместо текстового списка, когда просят «распиши», «скрин»,
«кто сколько заработал» и т.п.
"""
import os
import re
import tempfile
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

from sheets_helper import get_earnings, get_cars

rcParams["font.family"] = "DejaVu Sans"

TRIGGERS = (
    "кто сколько", "распиши", "расписать", "по водителям", "по машинам",
    "скрин", "скриншот", "таблиц", "список", "разбив",
)


def _norm(v):
    import re as _re
    s = str(v or "").replace("\xa0", "").replace(" ", "").replace("₽", "").replace(",", ".")
    s = _re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except Exception:
        return 0.0


def wants_image_report(question):
    q = (question or "").lower()
    if not any(t in q for t in TRIGGERS):
        return False
    return ("заработ" in q) or ("заработал" in q) or ("скрин" in q) or ("водител" in q)


def _pick_date(question, earnings):
    """Дата из вопроса (dd.mm.yyyy) или последний день с данными."""
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", question or "")
    if m:
        return "%02d.%02d.%s" % (int(m.group(1)), int(m.group(2)), m.group(3))
    dates = []
    for e in earnings:
        d = (e.get("date") or "").strip()
        mm = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", d)
        if mm:
            dates.append((mm.group(3), mm.group(2), mm.group(1), d))
    if not dates:
        return None
    return max(dates)[3]


def _fmt(n):
    return "{:,.0f}".format(n).replace(",", " ")


def build_earnings_image(question):
    """Возвращает (путь к png, подпись) или (None, текст ошибки)."""
    earnings = get_earnings()
    if not earnings:
        return None, "Нет данных по заработку"

    date = _pick_date(question, earnings)
    if not date:
        return None, "Не удалось определить дату"

    rows = [e for e in earnings if (e.get("date") or "").strip() == date]
    if not rows:
        return None, "За %s записей нет" % date

    by_driver = {}
    by_owner = {}
    for e in rows:
        driver = (e.get("driver") or "").strip() or "(без имени)"
        by_driver[driver] = by_driver.get(driver, 0.0) + _norm(e.get("amount"))
        owner = (e.get("owner") or "?").strip() or "?"
        by_owner[owner] = by_owner.get(owner, 0.0) + _norm(e.get("amount"))

    drivers = sorted(by_driver.items(), key=lambda x: -x[1])
    owners = sorted(by_owner.items(), key=lambda x: -x[1])
    total = sum(by_driver.values())

    # обрезаем длинный список, чтобы влезло на картинку
    shown = drivers[:22]
    rest = drivers[22:]

    row_in = 0.32
    table_in = max(len(shown) + 2, len(owners) + 3) * row_in
    fig_h = table_in + 2.3
    fig = plt.figure(figsize=(9, fig_h), dpi=130)
    ax_h = table_in / fig_h
    ax_bottom = 1 - 0.10 - ax_h
    fig.patch.set_facecolor("white")

    fig.text(0.5, 0.975, "ЗАРАБОТОК ЗА %s" % date, ha="center", va="top",
             fontsize=17, fontweight="bold", color="#111827")
    fig.text(0.5, 0.945, "всего %s ₽ · машин с заработком: %d" % (_fmt(total), len(rows)),
             ha="center", va="top", fontsize=11, color="#4b5563")

    # таблица водителей
    ax1 = fig.add_axes([0.04, ax_bottom, 0.58, ax_h])
    ax1.axis("off")
    ax1.set_title("По водителям", fontsize=13, fontweight="bold", loc="left", pad=8, color="#1f2937")

    cell = [["№", "Водитель", "Сумма, ₽"]]
    for i, (name, amount) in enumerate(shown, start=1):
        cell.append([str(i), name[:24], _fmt(amount)])
    if rest:
        cell.append(["…", "остальные (%d)" % len(rest), _fmt(sum(a for _, a in rest))])

    tbl = ax1.table(cellText=cell, loc="upper center", cellLoc="left", colWidths=[0.09, 0.56, 0.35])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1, 1.35)
    for (r, c), cellobj in tbl.get_celld().items():
        cellobj.set_edgecolor("#e5e7eb")
        if r == 0:
            cellobj.set_facecolor("#1f2937")
            cellobj.set_text_props(color="white", fontweight="bold")
        elif r % 2 == 0:
            cellobj.set_facecolor("#f9fafb")

    # разбивка по владельцам
    ax2 = fig.add_axes([0.64, ax_bottom, 0.34, ax_h])
    ax2.axis("off")
    ax2.set_title("По владельцам", fontsize=13, fontweight="bold", loc="left", pad=8, color="#1f2937")
    cell2 = [["Владелец", "Сумма, ₽"]]
    for name, amount in owners:
        cell2.append([name, _fmt(amount)])
    cell2.append(["ИТОГО", _fmt(total)])
    tbl2 = ax2.table(cellText=cell2, loc="upper center", cellLoc="left", colWidths=[0.70, 0.30])
    tbl2.auto_set_font_size(False)
    tbl2.set_fontsize(9)
    tbl2.scale(1, 1.5)
    for (r, c), cellobj in tbl2.get_celld().items():
        cellobj.set_edgecolor("#e5e7eb")
        if r == 0:
            cellobj.set_facecolor("#065f46")
            cellobj.set_text_props(color="white", fontweight="bold")
        elif r == len(cell2) - 1:
            cellobj.set_facecolor("#ecfdf5")
            cellobj.set_text_props(fontweight="bold")

    path = os.path.join(tempfile.gettempdir(), "earnings_%s.png" % date.replace(".", "_"))
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # обрезаем белые поля, чтобы в телеграме картинка была плотной
    try:
        from PIL import Image, ImageChops
        img = Image.open(path).convert("RGB")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        box = ImageChops.difference(img, bg).getbbox()
        if box:
            pad = 12
            box = (max(0, box[0] - pad), max(0, box[1] - pad),
                   min(img.width, box[2] + pad), min(img.height, box[3] + pad))
            img.crop(box).save(path)
    except Exception as _e:
        print("обрезать не удалось: %s" % _e)

    lines = ["Заработок за %s — %s ₽" % (date, _fmt(total)), ""]
    for name, amount in owners:
        share = (amount / total * 100) if total else 0
        lines.append("%s: %s ₽ (%.0f%%)" % (name, _fmt(amount), share))
    caption = "\n".join(lines)
    return path, caption
