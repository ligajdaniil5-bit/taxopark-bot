# -*- coding: utf-8 -*-
"""
Чтение данных из Google Таблицы для бота (актуальная таблица).

Что изменилось по сравнению со старой версией:
  * SPREADSHEET_ID указывает на рабочую таблицу «Аня, тебе сюда»;
  * колонки ищутся по нескольким вариантам названий — переименование
    в таблице больше не даёт тихих нулей;
  * даты приводятся к виду dd.MM.yyyy (его ждут база и ai_helper);
  * читается по имени листа через gviz, как раньше — таблица должна быть
    открыта по ссылке (просмотр для всех, у кого есть ссылка).

API модуля не менялся: get_cars / get_earnings / get_cash / get_balances.
"""
import csv
import io
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime

# «Аня, тебе сюда» — рабочая таблица
SPREADSHEET_ID = os.getenv("SHEET_ID", "1qxUa-C-eEKxUUhFjO4boPBnsPAqEYYBHpTUWDWFkme8")

CACHE_SECONDS = 300
_cache = {}


def _num(v):
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("\xa0", "").replace(" ", "").replace("₽", "").replace("р.", "")
    s = re.sub(r"[^\d.\-]", "", s.replace(",", "."))
    try:
        return float(s)
    except Exception:
        return 0.0


def _col(row, *names):
    """Первое непустое значение по списку возможных названий колонки."""
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return ""


def _date(v):
    """01.10.2026 | 2026-10-01 | serial → dd.MM.yyyy"""
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d.%m.%Y")
    s = str(v).strip()
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        return "%02d.%02d.%s" % (int(m.group(1)), int(m.group(2)), m.group(3))
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return "%s.%s.%s" % (m.group(3), m.group(2), m.group(1))
    if re.match(r"^\d{5}(\.\d+)?$", s):
        from datetime import timedelta
        d = datetime(1899, 12, 30) + timedelta(days=float(s))
        return d.strftime("%d.%m.%Y")
    return s


def _plate(s):
    s = str(s or "").strip().upper()
    return "".join(ch for ch in s if not ch.isspace() and ch != "-")


def read_sheet(sheet_name):
    """Читает лист по его названию через gviz (нужен доступ по ссылке)."""
    import time

    cached = _cache.get(sheet_name)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]

    url = (
        "https://docs.google.com/spreadsheets/d/"
        + SPREADSHEET_ID
        + "/gviz/tq?tqx=out:csv&sheet="
        + urllib.parse.quote(sheet_name)
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "taxopark-bot/2"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8-sig")
        head = data[:200].lower()
        if "<html" in head or "<!doctype" in head:
            print("Ошибка доступа к листу «%s»: таблица не открыта по ссылке" % sheet_name)
            return []
        rows = list(csv.DictReader(io.StringIO(data)))
        _cache[sheet_name] = (time.time(), rows)
        return rows
    except Exception as e:
        print("Ошибка чтения листа %s: %s" % (sheet_name, e))
        return []


def get_cars():
    cars = {}
    for r in read_sheet("Автомобили"):
        gos = str(_col(r, "Госномер")).strip().upper()
        if not gos:
            continue
        brand = str(_col(r, "Марка", "Модель")).strip()
        model = str(_col(r, "Модель")).strip()
        owner = str(_col(r, "Владелец")).strip() or "Александр Борисович"
        cars[gos] = {"gos": gos, "owner": owner, "brand": brand, "model": model}
    return cars


def get_earnings():
    cars = get_cars()
    out = []
    for r in read_sheet("Ввод данных"):
        date = _date(_col(r, "Дата"))
        gos = str(_col(r, "Госномер")).strip().upper()
        amount = _num(_col(r, "Заработок"))
        if not date or not gos or amount <= 0:
            continue
        owner = str(_col(r, "Владелец")).strip()
        if not owner:
            owner = cars.get(_plate(gos), {}).get("owner") or cars.get(gos, {}).get("owner") or "?"
        out.append({
            "date": date,
            "gos": gos,
            "driver": str(_col(r, "Водитель")).strip(),
            "amount": amount,
            "owner": owner,
        })
    return out


def get_cash():
    out = []
    for r in read_sheet("Касса"):
        date = _date(_col(r, "Дата"))
        amount = _num(_col(r, "Сумма"))
        if not date or amount <= 0:
            continue
        out.append({
            "date": date,
            "type": str(_col(r, "Тип операции", "Тип")).strip(),
            "category": str(_col(r, "Категория")).strip(),
            "sum": amount,
            "account": str(_col(r, "Счёт")).strip(),
            "who": str(_col(r, "Получил из кассы", "Кому/Кто", "Куда")).strip(),
            "comment": str(_col(r, "Комментарий")).strip(),
        })
    return out


def _last_by_date(rows):
    """Последняя строка по дате (не по порядку в листе)."""
    best, best_key = None, None
    for r in rows:
        d = _date(_col(r, "Дата"))
        m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", d)
        if not m:
            continue
        key = (m.group(3), m.group(2), m.group(1))
        if best_key is None or key > best_key:
            best, best_key = r, key
    return best


def get_balances():
    rows = read_sheet("Балансы")
    if not rows:
        return None
    last = _last_by_date(rows) or rows[-1]
    card = _num(_col(last, "Карта (₽)", "Карта (Альфа)", "Карта"))
    account = _num(_col(last, "Счёт (₽)", "Счёт (Переводы)", "Счёт"))
    cash = _num(_col(last, "Нал (₽)", "Наличные (₽)", "Наличные"))
    total = _num(_col(last, "Всего (₽)", "Всего")) or (card + account + cash)
    return {
        "date": _date(_col(last, "Дата")),
        "card": card,
        "account": account,
        "cash": cash,
        "total": total,
    }


def get_repairs():
    """Ремонты: дата, машина, работы, запчасти, работа, итого, пробег, простой."""
    out = []
    for r in read_sheet("Ремонты"):
        date = _date(_col(r, "Дата"))
        gos = str(_col(r, "Госномер")).strip().upper()
        if not date or not gos:
            continue
        part_cost = _num(_col(r, "Стоимость запчасти", "Стоимость запчастей"))
        work_cost = _num(_col(r, "Стоимость работы"))
        total = _num(_col(r, "Итого")) or (part_cost + work_cost)
        out.append({
            "date": date,
            "gos": gos,
            "work": str(_col(r, "Наименование работ", "Работа")).strip(),
            "firm": str(_col(r, "Фирма запчасти", "Фирма")).strip(),
            "part_cost": part_cost,
            "work_cost": work_cost,
            "total": total,
            "mileage": _num(_col(r, "Пробег (км)", "Пробег")),
            "idle_cost": _num(_col(r, "Простой (₽)", "Простой")),
        })
    return out



def get_shifts():
    """Смены из листа «Оп_Смены»: выручка, аренда, зарплата, начислено, водителю."""
    out = []
    for r in read_sheet("Оп_Смены"):
        date = _date(_col(r, "Дата"))
        gos = str(_col(r, "Госномер")).strip().upper()
        if not date or not gos:
            continue
        out.append({
            "id": str(_col(r, "ID смены")).strip(),
            "date": date,
            "gos": gos,
            "owner": str(_col(r, "Владелец")).strip(),
            "driver": str(_col(r, "Водитель")).strip(),
            "status": str(_col(r, "Статус дня")).strip(),
            "revenue": _num(_col(r, "Выручка (внёсла Анна), ₽", "Выручка водителя, ₽", "Выручка водителя", "Выручка")),
            "rent": _num(_col(r, "Аренда (парку), ₽", "Аренда, ₽", "Аренда")),
            "salary": _num(_col(r, "Зарплата (заработок), ₽", "Зарплата, ₽", "Зарплата")),
            "accrued": _num(_col(r, "Итого парку, ₽", "Начислено по машине, ₽", "Начислено по машине")),
            "driver_pay": _num(_col(r, "Остаток водителю, ₽", "Водителю, ₽", "Водителю")),
        })
    return out


def get_statuses():
    """Все строки «Ввода данных», включая простои: дата, номер, водитель, сумма, причина."""
    out = []
    for r in read_sheet("Ввод данных"):
        date = _date(_col(r, "Дата"))
        gos = str(_col(r, "Госномер")).strip().upper()
        if not date or not gos:
            continue
        out.append({
            "date": date,
            "gos": gos,
            "driver": str(_col(r, "Водитель")).strip(),
            "amount": _num(_col(r, "Заработок")),
            "comment": str(_col(r, "Комментарий")).strip(),
        })
    return out

if __name__ == "__main__":
    cars = get_cars()
    earnings = get_earnings()
    cash = get_cash()
    bal = get_balances()
    rep = get_repairs()
    print("машин:", len(cars))
    print("заработков:", len(earnings), "| сумма:", int(sum(e["amount"] for e in earnings)))
    print("касса:", len(cash), "| приход:", int(sum(c["sum"] for c in cash if c["type"].lower().startswith("приход"))))
    print("ремонтов:", len(rep), "| на сумму:", int(sum(x["total"] for x in rep)))
    sh = get_shifts()
    print("смен:", len(sh), "| аренда:", int(sum(x["rent"] for x in sh)), "| зарплата:", int(sum(x["salary"] for x in sh)))
    print("баланс:", bal)
