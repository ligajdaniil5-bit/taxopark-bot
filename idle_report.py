# -*- coding: utf-8 -*-
"""
Отчёт по простоям: сколько машин стояло и почему.

Причины Анна пишет в «Вводе данных» в колонке «Комментарий»
(Выходной, Не оплачен, Ремонт, Не работал, Запил и т.п.).
Отчёт строится за день или за месяц и приходит картинкой.
"""
import os
import re
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

from sheets_helper import get_statuses

rcParams["font.family"] = "DejaVu Sans"

TRIGGERS = ("простой", "простои", "простоя", "стоял", "стояли", "стояло",
            "запил", "причин", "не работал", "не работали", "не выш")

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

# причины, которые не считаем простоем (машина работала, но деньги не вписаны)
NOT_IDLE = ("оплачен",)


def _fmt(n):
    return "{:,.0f}".format(n).replace(",", " ")


def wants_idle_report(question):
    q = (question or "").lower()
    return any(t in q for t in TRIGGERS)


def _period(question, statuses):
    """Возвращает (подпись, отбор по дате)."""
    q = (question or "").lower()
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", q)
    if m:
        date = "%02d.%02d.%s" % (int(m.group(1)), int(m.group(2)), m.group(3))
        return date, (lambda d: d == date)

    m_year = re.search(r"20\d{2}", q)
    year = m_year.group(0) if m_year else None
    for stem, num in MONTHS.items():
        if stem in q:
            mm = "%02d" % num
            label = q[q.find(stem):q.find(stem) + 12].strip().split()[0] if q.find(stem) >= 0 else mm
            return label, (lambda d: d[3:5] == mm and (year is None or d[6:] == year))

    dates = sorted({x["date"] for x in statuses}, key=lambda d: (d[6:], d[3:5], d[:2]))
    if not dates:
        return None, None
    last = dates[-1]
    return last, (lambda d: d == last)


def build_idle_image(question):
    statuses = get_statuses()
    if not statuses:
        return None, "Нет данных"

    label, flt = _period(question, statuses)
    if not flt:
        return None, "Не удалось определить период"

    rows = [x for x in statuses if flt(x["date"])]
    if not rows:
        return None, "За %s записей нет" % label

    worked = [x for x in rows if x["amount"] > 0]
    idle = [x for x in rows if x["amount"] <= 0 and x["comment"]]
    no_reason = [x for x in rows if x["amount"] <= 0 and not x["comment"]]

    by_reason = {}
    days_by_reason = {}
    for x in idle:
        reason = x["comment"].strip()
        by_reason.setdefault(reason, set()).add(x["gos"])
        days_by_reason.setdefault(reason, set()).add((x["gos"], x["date"]))

    ordered = sorted(by_reason.items(), key=lambda kv: -len(kv[1]))
    idle_real = [(r, p) for r, p in ordered if r.lower() not in NOT_IDLE]
    other = [(r, p) for r, p in ordered if r.lower() in NOT_IDLE]
    idle_cars = set()
    for _, plates in idle_real:
        idle_cars |= set(plates)
    idle_count = len(idle_cars)

    rows_n = len(ordered) + len(other) + 6
    fig_h = 2.6 + 0.42 * rows_n
    fig = plt.figure(figsize=(9.5, fig_h), dpi=130)
    fig.patch.set_facecolor("white")

    fig.text(0.5, 0.985, "ПРОСТОИ ЗА %s" % label.upper(), ha="center", va="top",
             fontsize=16, fontweight="bold", color="#111827")
    fig.text(0.5, 0.952,
             "машин в списке %d · работали %d · простояли %d · без причины %d"
             % (len(rows), len(worked), idle_count, len(no_reason)),
             ha="center", va="top", fontsize=10.5, color="#4b5563")

    ax = fig.add_axes([0.04, 0.06, 0.92, 0.83])
    ax.axis("off")

    def wrap(plates, max_plates=5):
        """Одна строка: первые номера и остаток."""
        items = sorted(plates)
        shown = ", ".join(items[:max_plates])
        if len(items) > max_plates:
            shown += " +%d" % (len(items) - max_plates)
        return shown

    cell = [["Причина", "Машин", "Случаев", "Номера"]]
    for reason, plates in idle_real:
        cell.append([reason, str(len(plates)), str(len(days_by_reason.get(reason, ()))), wrap(plates)])
    if other:
        for reason, plates in other:
            cell.append([reason + " (не прост.)", str(len(plates)), str(len(days_by_reason.get(reason, ()))), wrap(plates)])
    cell.append(["ИТОГО ПРОСТОЕВ", str(idle_count), str(sum(len(days_by_reason.get(r, ())) for r, _ in idle_real)), ""])

    tbl = ax.table(cellText=cell, loc="upper center", cellLoc="left",
                   colWidths=[0.25, 0.08, 0.09, 0.58])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1, 1.7)
    for (r, c), cellobj in tbl.get_celld().items():
        cellobj.set_edgecolor("#e5e7eb")
        if r == 0:
            cellobj.set_facecolor("#7f1d1d")
            cellobj.set_text_props(color="white", fontweight="bold")
        elif r == len(cell) - 1:
            cellobj.set_facecolor("#fef2f2")
            cellobj.set_text_props(fontweight="bold")
        elif r % 2 == 0:
            cellobj.set_facecolor("#f9fafb")

    path = os.path.join(tempfile.gettempdir(), "idle_%s.png" % re.sub(r"\W+", "_", label))
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

    lines = ["Простои за %s" % label,
             "работали %d, простояли %d из %d" % (len(worked), idle_count, len(rows))]
    if idle_real:
        lines.append("")
        for reason, plates in idle_real[:6]:
            lines.append("%s: %d (%s)" % (reason, len(plates), ", ".join(sorted(plates)[:5])))
    return path, "\n".join(lines)
