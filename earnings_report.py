# -*- coding: utf-8 -*-
"""
Картинка-отчёт по заработку за конкретную дату.

Показывает ВСЕ машины парка (в том числе без заработка — выходной, не оплачен)
и разбивку по владельцам. Бот присылает её вместо текстового списка.
"""
import os
import re
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

from sheets_helper import get_earnings, get_cars

rcParams["font.family"] = "DejaVu Sans"

TRIGGERS = (
    "кто сколько", "распиши", "расписать", "по водителям", "по машинам",
    "скрин", "скриншот", "таблиц", "список", "разбив", "попап", "отчет", "отчёт", "сводка",
)


def _num(v):
    s = str(v or "").replace("\xa0", "").replace(" ", "").replace("₽", "").replace(",", ".")
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except Exception:
        return 0.0


def _fmt(n):
    return "{:,.0f}".format(n).replace(",", " ")


def wants_image_report(question):
    q = (question or "").lower()
    if not any(t in q for t in TRIGGERS):
        return False
    return ("заработ" in q or "заработал" in q or "скрин" in q
            or "водител" in q or "попап" in q or "машин" in q or "отчёт" in q or "отчет" in q)


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
    return max(dates)[3] if dates else None


def build_earnings_image(question):
    """Возвращает (путь к png, подпись) или (None, текст)."""
    earnings = get_earnings()
    cars = get_cars()
    if not earnings and not cars:
        return None, "Нет данных"

    date = _pick_date(question, earnings)
    if not date:
        return None, "Не удалось определить дату"

    # сколько заработала каждая машина за день
    per_car = {}
    for e in earnings:
        if (e.get("date") or "").strip() != date:
            continue
        gos = (e.get("gos") or "").strip().upper()
        if not gos:
            continue
        per_car[gos] = per_car.get(gos, 0.0) + _num(e.get("amount"))

    # все машины парка + те, что встречались в заработке
    all_plates = set(cars.keys()) | set(per_car.keys())
    rows = []
    for plate in all_plates:
        info = cars.get(plate, {})
        rows.append({
            "plate": plate,
            "amount": per_car.get(plate, 0.0),
            "owner": (info.get("owner") or "").strip() or "?",
        })
    rows.sort(key=lambda r: (-r["amount"], r["plate"]))

    by_owner = {}
    for r in rows:
        by_owner[r["owner"]] = by_owner.get(r["owner"], 0.0) + r["amount"]
    owners = sorted(by_owner.items(), key=lambda x: -x[1])

    total = sum(r["amount"] for r in rows)
    earned_count = sum(1 for r in rows if r["amount"] > 0)

    row_in = 0.26 if len(rows) > 30 else 0.30
    table_in = (len(rows) + 2) * row_in
    fig_h = table_in + 2.2
    fig = plt.figure(figsize=(9.5, fig_h), dpi=130)
    ax_h = table_in / fig_h
    ax_bottom = 1 - 0.09 - ax_h
    fig.patch.set_facecolor("white")

    fig.text(0.5, 0.985, "ЗАРАБОТОК ЗА %s" % date, ha="center", va="top",
             fontsize=16, fontweight="bold", color="#111827")
    fig.text(0.5, 0.958, "всего %s ₽ · машин %d, с заработком %d" % (_fmt(total), len(rows), earned_count),
             ha="center", va="top", fontsize=10.5, color="#4b5563")

    ax1 = fig.add_axes([0.04, ax_bottom, 0.60, ax_h])
    ax1.axis("off")
    ax1.set_title("По машинам", fontsize=12.5, fontweight="bold", loc="left", pad=6, color="#1f2937")

    cell = [["№", "Машина", "Сумма, ₽"]]
    for i, r in enumerate(rows, start=1):
        cell.append([str(i), r["plate"], _fmt(r["amount"]) if r["amount"] else "—"])
    cell.append(["", "ИТОГО", _fmt(total)])

    tbl = ax1.table(cellText=cell, loc="upper center", cellLoc="left", colWidths=[0.10, 0.55, 0.35])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8.5 if len(rows) > 30 else 9.5)
    tbl.scale(1, 1.15)
    for (r, c), cellobj in tbl.get_celld().items():
        cellobj.set_edgecolor("#e5e7eb")
        if r == 0:
            cellobj.set_facecolor("#1f2937")
            cellobj.set_text_props(color="white", fontweight="bold")
        elif r == len(cell) - 1:
            cellobj.set_facecolor("#ecfdf5")
            cellobj.set_text_props(fontweight="bold")
        elif r % 2 == 0:
            cellobj.set_facecolor("#f9fafb")

    # разбивка по владельцам — справа сверху
    box_h = min(ax_h, 0.30)
    ax2 = fig.add_axes([0.68, 1 - 0.09 - box_h, 0.30, box_h])
    ax2.axis("off")
    ax2.set_title("По владельцам", fontsize=12.5, fontweight="bold", loc="left", pad=6, color="#1f2937")
    cell2 = [["Владелец", "Сумма, ₽"]]
    for name, amount in owners:
        cell2.append([name, _fmt(amount)])
    cell2.append(["ИТОГО", _fmt(total)])
    tbl2 = ax2.table(cellText=cell2, loc="upper center", cellLoc="left", colWidths=[0.70, 0.30])
    tbl2.auto_set_font_size(False)
    tbl2.set_fontsize(9.5)
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

    try:
        from PIL import Image, ImageChops
        img = Image.open(path).convert("RGB")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        box = ImageChops.difference(img, bg).getbbox()
        if box:
            pad = 14
            box = (max(0, box[0] - pad), max(0, box[1] - pad),
                   min(img.width, box[2] + pad), min(img.height, box[3] + pad))
            img.crop(box).save(path)
    except Exception as e:
        print("обрезать не удалось: %s" % e)

    lines = ["Заработок за %s — %s ₽" % (date, _fmt(total)),
             "машин: %d, с заработком: %d" % (len(rows), earned_count), ""]
    for name, amount in owners:
        share = (amount / total * 100) if total else 0
        lines.append("%s: %s ₽ (%.0f%%)" % (name, _fmt(amount), share))
    return path, "\n".join(lines)
