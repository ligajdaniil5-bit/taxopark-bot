# -*- coding: utf-8 -*-
"""
Отчёт картинкой: сколько за день заработала аренда и сколько зарплата,
с разбивкой по собственникам машин.

Данные — из листа «Оп_Смены» (колонки: выручка, аренда, зарплата, собственник).
"""
import os
import re
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

from sheets_helper import get_shifts

rcParams["font.family"] = "DejaVu Sans"

TRIGGERS_WHAT = ("аренд", "зп", "зарплат", "собственник", "владельц")
TRIGGERS_HOW = ("разбив", "распиши", "сколько", "за день", "покажи", "отчёт", "отчет")


def _fmt(n):
    return "{:,.0f}".format(n).replace(",", " ")


def wants_income_report(question):
    q = (question or "").lower()
    if not any(t in q for t in TRIGGERS_WHAT):
        return False
    return any(t in q for t in TRIGGERS_HOW) or bool(re.search(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}", q))


def _pick_date(question, shifts):
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", question or "")
    if m:
        return "%02d.%02d.%s" % (int(m.group(1)), int(m.group(2)), m.group(3))
    dates = []
    for s in shifts:
        mm = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", (s.get("date") or "").strip())
        if mm:
            dates.append((mm.group(3), mm.group(2), mm.group(1), s["date"]))
    return max(dates)[3] if dates else None


def build_income_image(question):
    """Возвращает (путь, подпись) или (None, текст ошибки)."""
    shifts = get_shifts()
    if not shifts:
        return None, "Нет данных по сменам"

    date = _pick_date(question, shifts)
    if not date:
        return None, "Не удалось определить дату"

    rows = [s for s in shifts if (s.get("date") or "").strip() == date]
    if not rows:
        return None, "За %s смен нет" % date

    rev = sum(s.get("revenue", 0) for s in rows)
    rent = sum(s.get("rent", 0) for s in rows)
    sal = sum(s.get("salary", 0) for s in rows)
    accrued = sum(s.get("accrued", 0) for s in rows) or (rent + sal)
    dp = sum(s.get("driver_pay", 0) for s in rows) or (rev - accrued)
    cars = len({s.get("gos") for s in rows if s.get("gos")})

    owners = {}
    for s in rows:
        o = (s.get("owner") or "").strip() or "(не указан)"
        if o not in owners:
            owners[o] = {"rev": 0.0, "rent": 0.0, "sal": 0.0, "cars": set()}
        owners[o]["rev"] += s.get("revenue", 0)
        owners[o]["rent"] += s.get("rent", 0)
        owners[o]["sal"] += s.get("salary", 0)
        if s.get("gos"):
            owners[o]["cars"].add(s["gos"])

    fig = plt.figure(figsize=(9.5, 7.2), dpi=130)
    fig.patch.set_facecolor("white")
    fig.text(0.5, 0.975, "АРЕНДА И ЗАРПЛАТА ЗА %s" % date, ha="center", va="top",
             fontsize=16, fontweight="bold", color="#111827")
    fig.text(0.5, 0.935, "машин в смене: %d · выручка %s ₽" % (cars, _fmt(rev)),
             ha="center", va="top", fontsize=10.5, color="#4b5563")

    # итоги
    ax1 = fig.add_axes([0.05, 0.56, 0.42, 0.30])
    ax1.axis("off")
    ax1.set_title("Итоги дня", fontsize=12.5, fontweight="bold", loc="left", color="#1f2937", pad=10)
    cell = [["Показатель", "Сумма, ₽"],
            ["Выручка машин", _fmt(rev)],
            ["Аренда", _fmt(rent)],
            ["Зарплата (% от выручки)", _fmt(sal)],
            ["Начислено по машинам", _fmt(accrued)],
            ["Осталось водителям", _fmt(dp)]]
    t1 = ax1.table(cellText=cell, loc="upper center", cellLoc="left", colWidths=[0.66, 0.34])
    t1.auto_set_font_size(False); t1.set_fontsize(10); t1.scale(1, 1.6)
    for (r, c), cel in t1.get_celld().items():
        cel.set_edgecolor("#e5e7eb")
        if r == 0:
            cel.set_facecolor("#065f46"); cel.set_text_props(color="white", fontweight="bold")
        elif r in (2, 3):
            cel.set_facecolor("#ecfdf5"); cel.set_text_props(fontweight="bold")

    # по собственникам
    ax2 = fig.add_axes([0.52, 0.56, 0.44, 0.30])
    ax2.axis("off")
    ax2.set_title("По собственникам", fontsize=12.5, fontweight="bold", loc="left", color="#1f2937", pad=10)
    cell2 = [["Собственник", "Выручка", "Аренда", "Зарплата"]]
    for o, v in sorted(owners.items(), key=lambda x: -x[1]["rev"]):
        cell2.append([o, _fmt(v["rev"]), _fmt(v["rent"]), _fmt(v["sal"])])
    cell2.append(["ИТОГО", _fmt(rev), _fmt(rent), _fmt(sal)])
    t2 = ax2.table(cellText=cell2, loc="upper center", cellLoc="left", colWidths=[0.40, 0.20, 0.20, 0.20])
    t2.auto_set_font_size(False); t2.set_fontsize(9); t2.scale(1, 1.6)
    for (r, c), cel in t2.get_celld().items():
        cel.set_edgecolor("#e5e7eb")
        if r == 0:
            cel.set_facecolor("#1f2937"); cel.set_text_props(color="white", fontweight="bold")
        elif r == len(cell2) - 1:
            cel.set_facecolor("#fef3c7"); cel.set_text_props(fontweight="bold")

    # строки по машинам (топ по выручке)
    ax3 = fig.add_axes([0.05, 0.03, 0.91, 0.47])
    ax3.axis("off")
    ax3.set_title("Машины за день", fontsize=12.5, fontweight="bold", loc="left", color="#1f2937", pad=8)
    rows_sorted = sorted(rows, key=lambda s: -s.get("revenue", 0))[:14]
    cell3 = [["Машина", "Собственник", "Выручка", "Аренда", "Зарплата", "Водителю"]]
    for s in rows_sorted:
        cell3.append([s.get("gos", ""), (s.get("owner") or "")[:20], _fmt(s.get("revenue", 0)),
                      _fmt(s.get("rent", 0)), _fmt(s.get("salary", 0)), _fmt(s.get("driver_pay", 0))])
    t3 = ax3.table(cellText=cell3, loc="upper center", cellLoc="left",
                   colWidths=[0.14, 0.26, 0.15, 0.15, 0.15, 0.15])
    t3.auto_set_font_size(False); t3.set_fontsize(9); t3.scale(1, 1.35)
    for (r, c), cel in t3.get_celld().items():
        cel.set_edgecolor("#e5e7eb")
        if r == 0:
            cel.set_facecolor("#0f172a"); cel.set_text_props(color="white", fontweight="bold")
        elif r % 2 == 0:
            cel.set_facecolor("#f9fafb")

    path = os.path.join(tempfile.gettempdir(), "income_%s.png" % date.replace(".", "_"))
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

    lines = ["Аренда и зарплата за %s" % date, ""]
    for o, v in sorted(owners.items(), key=lambda x: -x[1]["rev"]):
        lines.append("%s: аренда %s ₽ · зарплата %s ₽" % (o, _fmt(v["rent"]), _fmt(v["sal"])))
    lines.append("")
    lines.append("Всего: аренда %s ₽ + зарплата %s ₽ = %s ₽" % (_fmt(rent), _fmt(sal), _fmt(rent + sal)))
    return path, "\n".join(lines)
