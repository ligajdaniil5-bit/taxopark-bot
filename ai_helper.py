# -*- coding: utf-8 -*-
"""
ИИ-слой бота: вопрос → SQL → ответ.

Главное изменение: данные для SQL берутся НЕ из старого файла базы, а собираются
в память прямо из Google Таблицы на момент вопроса. Поэтому ответы всегда по
актуальным данным — без синхронизаций и без риска «база отстала на месяц».

Служебные таблицы (qa_log, few_shot_pool) по-прежнему живут в файле базы.
"""
import os
import re
import sqlite3
import threading
import time

from dotenv import load_dotenv
load_dotenv()

from paths import DB_PATH
from sheets_helper import get_cars, get_earnings, get_cash, get_balances, get_repairs, get_shifts

HISTORY_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repairs_history.csv")

OR_KEY = os.getenv("OPENROUTER_KEY", "")
GQ_KEY = os.getenv("GROQ_KEY", "")

# сколько секунд держать собранную базу в памяти
DATA_TTL = int(os.getenv("DATA_TTL", "900"))

WRITE_ACTIONS = {
    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_PRAGMA,
}

SYSTEM_PROMPT = """You are an SQL generator EXCLUSIVELY for SQLite 3.

Schema:
cars(plate, plate_norm, owner, brand, model)
earnings(id, date, gos, driver, amount, owner)
cash(id, date, type, category, sum, account, who, comment)
balances(id, date, card, account, cash, total)
shifts(id, date, gos, owner, driver, status, revenue, rent, salary, accrued, driver_pay)
repairs(id, date, gos, work, firm, part_cost, work_cost, total, mileage, idle_cost)
repairs_history(id, plate, date, work, part_cost, work_cost, total, mileage)

Rules:
1. Generate ONLY one SELECT or WITH ... SELECT query.
2. NEVER use INSERT, UPDATE, DELETE, DROP.
3. NEVER use PostgreSQL/MySQL syntax: EXTRACT, DATE_FORMAT, TO_DATE, ILIKE, MONTH(), YEAR().
4. The date column is TEXT in format dd.MM.yyyy (example: 05.10.2026).
5. Filter by month and year using: date LIKE '%.10.2026'
6. Do NOT use strftime on dd.MM.yyyy dates.
7. Use SQL aggregates: SUM, AVG, COUNT.
8. Use only tables and columns from the schema.
9. Return ONLY SQL. No markdown, no comments, no explanation.
10. If impossible, return exactly: NO_SQL
11. Owner names in DB: 'Александр Борисович', 'Анна', 'Даниил'.
12. If user asks about 'Данила', 'Данил' or 'Дане', use 'Даниил' in the SQL query.
13. If user asks about 'Аня', 'Ани', 'Анне', 'Анну', use 'Анна'.
14. To count CARS for an owner, ALWAYS use: SELECT COUNT(*) FROM cars WHERE owner = 'Имя'
15. Never count cars using the earnings table.
16. In table cash the money column is called "sum", not amount.
17. gross income of drivers is in earnings.amount.

17a. Plates may be written in any case: use LOWER(plate) = LOWER('x') or rely on COLLATE NOCASE.
17b. NEVER change letters in a plate. If a plate hint is given, copy it exactly. If the hint lists two plates, ask which one is meant.
18. Repairs are in the repairs table: part_cost + work_cost = total.
19. To find repair costs for a car use gos (license plate), for example: SELECT SUM(total) FROM repairs WHERE gos = 'K904OX'
20. Repair dates use the same dd.MM.yyyy format: date LIKE '%.10.2026'

17c. shifts = смены: revenue — выручка машины за день, rent — аренда, salary — зарплата (% от выручки), accrued = rent + salary, driver_pay — остаток водителю, owner — собственник машины.
21. repairs = официальные ремонты из таблицы (с суммами, учитываются в деньгах).
22. repairs_history = подробная история работ из карточек машин (может не совпадать с repairs, суммы считать НЕ по ней).
23. На вопросы «что делали с машиной», «когда был ремонт», «какой пробег» используй repairs_history.
24. На вопросы про деньги на ремонты используй только repairs.

JOIN: cars.plate = earnings.gos AND cars.plate = repairs.gos AND cars.plate = repairs_history.plate
"""

OWNERS = {
    "александр": "Александр Борисович", "борис": "Александр Борисович",
    "анна": "Анна", "аня": "Анна", "ане": "Анна", "ани": "Анна", "анну": "Анна",
    "даниил": "Даниил", "данил": "Даниил", "дане": "Даниил", "данила": "Даниил",
}

_data = {"db": None, "built": 0.0}
_DB_LOCK = threading.Lock()


def _build_data_db():
    """Собирает свежую базу из Google Таблицы в память."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    c = conn.cursor()
    c.execute("CREATE TABLE cars (plate TEXT COLLATE NOCASE, plate_norm TEXT, owner TEXT, brand TEXT, model TEXT)")
    c.execute("CREATE TABLE earnings (id TEXT, date TEXT, gos TEXT COLLATE NOCASE, driver TEXT, amount REAL, owner TEXT)")
    c.execute("CREATE TABLE cash (id TEXT, date TEXT, type TEXT, category TEXT, sum REAL, account TEXT, who TEXT, comment TEXT)")
    c.execute("CREATE TABLE balances (id TEXT, date TEXT, card REAL, account REAL, cash REAL, total REAL)")

    cars = get_cars()
    for i, (plate, info) in enumerate(cars.items()):
        c.execute("INSERT INTO cars VALUES (?,?,?,?,?)",
                  (plate, plate, info.get("owner", ""), info.get("brand", ""), info.get("model", "")))

    for i, e in enumerate(get_earnings()):
        c.execute("INSERT INTO earnings VALUES (?,?,?,?,?,?)",
                  ("E%d" % i, e["date"], e["gos"], e.get("driver", ""), e["amount"], e.get("owner", "")))

    for i, x in enumerate(get_cash()):
        c.execute("INSERT INTO cash VALUES (?,?,?,?,?,?,?,?)",
                  ("C%d" % i, x["date"], x.get("type", ""), x.get("category", ""),
                   x["sum"], x.get("account", ""), x.get("who", ""), x.get("comment", "")))

    bal = get_balances()
    if bal:
        c.execute("INSERT INTO balances VALUES (?,?,?,?,?,?)",
                  ("B1", bal["date"], bal["card"], bal["account"], bal["cash"], bal["total"]))

    c.execute("CREATE TABLE repairs (id TEXT, date TEXT, gos TEXT COLLATE NOCASE, work TEXT, firm TEXT, part_cost REAL, work_cost REAL, total REAL, mileage REAL, idle_cost REAL)")
    for i, r in enumerate(get_repairs()):
        c.execute("INSERT INTO repairs VALUES (?,?,?,?,?,?,?,?,?,?)",
                  ("R%d" % i, r["date"], r["gos"], r.get("work", ""), r.get("firm", ""),
                   r.get("part_cost", 0), r.get("work_cost", 0), r.get("total", 0),
                   r.get("mileage", 0), r.get("idle_cost", 0)))

    # история ремонтов из файла карточек
    c.execute("CREATE TABLE shifts (id TEXT, date TEXT, gos TEXT COLLATE NOCASE, owner TEXT, driver TEXT, status TEXT, revenue REAL, rent REAL, salary REAL, accrued REAL, driver_pay REAL)")
    for i, sh_ in enumerate(get_shifts()):
        c.execute("INSERT INTO shifts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  ("SH%d" % i, sh_["date"], sh_["gos"], sh_.get("owner", ""), sh_.get("driver", ""),
                   sh_.get("status", ""), sh_.get("revenue", 0), sh_.get("rent", 0),
                   sh_.get("salary", 0), sh_.get("accrued", 0), sh_.get("driver_pay", 0)))

    c.execute("CREATE TABLE repairs_history (id TEXT, plate TEXT COLLATE NOCASE, date TEXT, work TEXT, part_cost REAL, work_cost REAL, total REAL, mileage REAL)")
    if os.path.exists(HISTORY_CSV):
        import csv as _csv
        try:
            with open(HISTORY_CSV, encoding="utf-8") as _f:
                for _i, _r in enumerate(_csv.DictReader(_f)):
                    c.execute("INSERT INTO repairs_history VALUES (?,?,?,?,?,?,?,?)",
                              ("H%d" % _i, _r.get("plate", ""), _r.get("date", ""), _r.get("work", ""),
                               float(_r.get("part_cost") or 0), float(_r.get("work_cost") or 0),
                               float(_r.get("total") or 0), float(_r.get("mileage") or 0)))
        except Exception as _e:
            print("история ремонтов не загружена: %s" % _e)

    conn.commit()
    return conn


def _persist_to_file(mem_conn):
    """Кладёт собранные данные в файловую базу — её читают графики и выгрузки."""
    tables = {
        "cars": "CREATE TABLE cars (plate TEXT COLLATE NOCASE, plate_norm TEXT, owner TEXT, brand TEXT, model TEXT)",
        "earnings": "CREATE TABLE earnings (id TEXT, date TEXT, gos TEXT COLLATE NOCASE, driver TEXT, amount REAL, owner TEXT)",
        "cash": "CREATE TABLE cash (id TEXT, date TEXT, type TEXT, category TEXT, sum REAL, account TEXT, who TEXT, comment TEXT)",
        "balances": "CREATE TABLE balances (id TEXT, date TEXT, card REAL, account REAL, cash REAL, total REAL)",
        "shifts": "CREATE TABLE shifts (id TEXT, date TEXT, gos TEXT COLLATE NOCASE, owner TEXT, driver TEXT, status TEXT, revenue REAL, rent REAL, salary REAL, accrued REAL, driver_pay REAL)",
        "repairs": "CREATE TABLE repairs (id TEXT, date TEXT, gos TEXT COLLATE NOCASE, work TEXT, firm TEXT, part_cost REAL, work_cost REAL, total REAL, mileage REAL, idle_cost REAL)",
        "repairs_history": "CREATE TABLE repairs_history (id TEXT, plate TEXT COLLATE NOCASE, date TEXT, work TEXT, part_cost REAL, work_cost REAL, total REAL, mileage REAL)",
    }
    try:
        with sqlite3.connect(DB_PATH, timeout=15) as f:
            f.execute("PRAGMA journal_mode=WAL")
            for t, ddl in tables.items():
                f.execute("DROP TABLE IF EXISTS " + t)
                f.execute(ddl)
                rows = mem_conn.execute("SELECT * FROM " + t).fetchall()
                if rows:
                    marks = ",".join("?" * len(rows[0]))
                    f.executemany("INSERT INTO " + t + " VALUES (" + marks + ")", rows)
            f.commit()
    except Exception as e:
        print("не удалось обновить файловую базу: %s" % e)


def _build_from_file():
    """Резервный вариант: берём последний сохранённый снимок из файловой базы."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    tables = ["cars", "earnings", "cash", "balances", "shifts", "repairs", "repairs_history"]
    try:
        src = sqlite3.connect(DB_PATH, timeout=10)
        for t in tables:
            try:
                ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
                if not ddl or not ddl[0]:
                    continue
                conn.execute(ddl[0])
                rows = src.execute("SELECT * FROM " + t).fetchall()
                if rows:
                    marks = ",".join("?" * len(rows[0]))
                    conn.executemany("INSERT INTO " + t + " VALUES (" + marks + ")", rows)
            except Exception:
                continue
        src.close()
        conn.commit()
    except Exception as e:
        print("снимок из файла не прочитан: %s" % e)
    return conn


def _rebuild_locked():
    """Пересобирает базу. Вызывать только под _DB_LOCK."""
    t0 = time.time()
    try:
        if _data["db"] is not None:
            _data["db"].close()
    except Exception:
        pass
    _data["db"] = _build_data_db()
    _data["built"] = time.time()
    print("[data] пересборка за %.1f с" % (time.time() - t0))
    try:
        total = _data["db"].execute(
            "SELECT (SELECT COUNT(*) FROM cars) + (SELECT COUNT(*) FROM earnings)"
        ).fetchone()[0]
    except Exception:
        total = 0
    if total > 0:
        # данные пришли — обновляем файловый снимок
        _persist_to_file(_data["db"])
    else:
        # таблица недоступна — работаем по последнему снимку
        fallback = _build_from_file()
        try:
            fb_total = fallback.execute(
                "SELECT (SELECT COUNT(*) FROM cars) + (SELECT COUNT(*) FROM earnings)"
            ).fetchone()[0]
        except Exception:
            fb_total = 0
        if fb_total > 0:
            _data["db"].close()
            _data["db"] = fallback
            print("таблица недоступна — использую последний снимок (%d записей)" % fb_total)
        else:
            fallback.close()




def _start_bg_rebuild():
    """Запускает пересборку в фоне (одна за раз)."""
    if _data.get("bg_running"):
        return
    _data["bg_running"] = True
    _data["bg_started"] = time.time()

    def _worker():
        try:
            with _DB_LOCK:
                _rebuild_locked()
        except Exception as e:  # noqa: BLE001
            print("[data] фоновая пересборка не удалась: %s" % e)
        finally:
            _data["bg_running"] = False

    import threading
    threading.Thread(target=_worker, daemon=True).start()


def data_db():
    """Соединение с собранной базой; пересобирается раз в DATA_TTL секунд.

    Соединение создаётся с check_same_thread=False и используется под замком:
    бот вызывает ask_ai в разных потоках пула, поэтому без этого падает с
    «SQLite objects created in a thread can only be used in that same thread».
    """
    if _data["db"] is not None and time.time() - _data["built"] <= DATA_TTL:
        return _data["db"]
    # Никогда не блокируем ответ ожиданием сети: если данных ещё нет —
    # берём последний снимок с диска и запускаем обновление в фоне.
    if _data["db"] is None:
        try:
            _data["db"] = _build_from_file()
            _data["built"] = time.time()
            print("[data] старт из файлового снимка")
        except Exception as e:
            print("[data] снимок не прочитан: %s" % e)
    if time.time() - max(_data["built"], _data["bg_started"]) > DATA_TTL:
        _start_bg_rebuild()
    return _data["db"]


def schema():
    """Описание таблиц и количества строк — для промпта."""
    try:
        with _DB_LOCK:
            c = data_db().cursor()
            out = []
            for t in ("cars", "earnings", "cash", "balances", "shifts", "repairs", "repairs_history"):
                n = c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                cols = [r[1] for r in c.execute("PRAGMA table_info(" + t + ")").fetchall()]
                out.append("%s (%d): %s" % (t, n, ", ".join(cols)))
        return "\n".join(out) + "\n"
    except Exception as e:
        return "schema error: %s\n" % e


def find_owner(q):
    ql = (q or "").lower()
    for k, v in OWNERS.items():
        if k in ql:
            return v
    return None


def extract_sql(response):
    if not response:
        return None
    text = response.strip()
    text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    if not re.match(r"^(SELECT|WITH)\b", text, flags=re.I):
        return None
    if ";" in text.rstrip(";"):
        return None
    up = text.upper()
    for bad in ["EXTRACT", "DATE_FORMAT", "DATEDIFF", "NO_SQL", "SAFETY"]:
        if bad in up:
            return None
    return text.rstrip(";")


def read_sql(sql):
    q = (sql or "").strip()
    if not re.match(r"^(SELECT|WITH)\b", q, re.I):
        return None, "Only SELECT allowed"
    if ";" in q.rstrip(";"):
        return None, "Multiple statements not allowed"
    try:
        conn = data_db()
        with _DB_LOCK:
            conn.set_authorizer(lambda a, b, c, d, e: sqlite3.SQLITE_DENY if a in WRITE_ACTIONS else sqlite3.SQLITE_OK)
            cur = conn.execute(q)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(201)[:200]
        return {"columns": cols, "rows": rows}, None
    except Exception as e:
        return None, str(e)


def load_fewshot():
    try:
        with sqlite3.connect(DB_PATH, timeout=5) as cn:
            return cn.execute("SELECT question, sql_text FROM few_shot_pool ORDER BY slot LIMIT 5").fetchall()
    except Exception:
        return []


def _llm(msgs, kind):
    if kind == "groq" and GQ_KEY:
        try:
            from groq import Groq
            cl = Groq(api_key=GQ_KEY)
            for m in ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]:
                try:
                    r = cl.chat.completions.create(model=m, messages=msgs, timeout=60)
                    return r.choices[0].message.content
                except Exception:
                    pass
        except Exception:
            pass
    if kind == "openrouter" and OR_KEY:
        try:
            from openai import OpenAI
            cl = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OR_KEY)
            for m in ["openrouter/free", "qwen/qwen3.8-27b:free", "meta-llama/llama-3.3-70b-instruct:free"]:
                try:
                    r = cl.chat.completions.create(model=m, messages=msgs, timeout=90)
                    return r.choices[0].message.content
                except Exception:
                    pass
        except Exception:
            pass
    return None



def find_plate(q):
    """Ищет номер машины в вопросе по трём цифрам и возвращает точное значение."""
    digits = re.findall(r"\d{3}", q or "")
    if not digits:
        return None
    try:
        plates = [r[0] for r in data_db().execute("SELECT plate FROM cars").fetchall()]
    except Exception:
        return None
    for d in digits:
        cands = [p for p in plates if d in p]
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            return " или ".join(cands)   # подскажем варианты
    return None

def ask_ai(q):
    sc = schema()
    owner = find_owner(q)
    hint = "Owner mentioned: " + owner + "\n" if owner else ""
    plate = find_plate(q)
    if plate:
        hint += "Plate mentioned: " + plate + " (use EXACTLY this value, do not change letters)\n"
    joins = "JOIN: cars.plate = earnings.gos AND cars.plate = repairs.gos AND cars.plate = repairs_history.plate\n"
    user_msg = "Schema:\n" + sc + "\n" + joins + hint + "Question: " + q

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for ex_q, ex_sql in load_fewshot():
        msgs.append({"role": "user", "content": ex_q})
        msgs.append({"role": "assistant", "content": ex_sql})
    msgs.append({"role": "user", "content": user_msg})

    sql = None
    for provider in ["groq", "openrouter"]:
        raw = _llm(msgs, provider)
        sql = extract_sql(raw)
        if sql:
            break

    if not sql:
        return "Не удалось сформировать корректный SQL. Попробуйте переформулировать.", None, []

    res, err = read_sql(sql)
    if err:
        return "Ошибка SQL: " + err + "\n\nЗапрос: " + sql, sql, []
    if not res or not res["rows"]:
        return "Данных за указанный период не найдено.", sql, []

    sql_rows = res["rows"]
    p2 = (
        "Ты аналитик таксопарка. Отвечай на русском, кратко и точно.\n"
        "Вопрос: " + q + "\n"
        "SQL: " + sql + "\n"
        "Результат SQL: " + str(res)
    )
    ans = _llm([{"role": "user", "content": p2}], "groq")
    if not ans:
        ans = _llm([{"role": "user", "content": p2}], "openrouter")
    return ans or str(res), sql, sql_rows


# прогрев при старте: собираем данные и обновляем файловую базу
try:
    data_db()
except Exception:
    pass


# warm-up: собираем базу в фоне сразу после старта, чтобы первый вопрос не ждал
def _warm_up():
    try:
        data_db()
        print("[data] прогрев завершён")
    except Exception as e:
        print("[data] прогрев не удался: %s" % e)


try:
    import threading as _threading
    _threading.Thread(target=_warm_up, daemon=True).start()
except Exception:
    pass

# ─────────────────────────── живой разговор ───────────────────────────
# Обычный человеческий чат про машины и день (не SQL). Работает по тем же
# живым данным из Google Sheets, что и ask_ai (data_db + _DB_LOCK).

CREDIT_AB_DAY = 10500     # кредит Александра Борисовича в день
CREDIT_DAN_DAY = 3500     # кредит Даниила в день (с 20.11.2026)
ANNA_FEE = 666            # ЗП Ани с каждой машины в день


def _q(sql, args=()):
    """SELECT по живой базе. Возвращает список словарей (пусто при ошибке)."""
    try:
        conn = data_db()
        if conn is None:
            return []
        with _DB_LOCK:
            cur = conn.execute(sql, args)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchall()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        print("[chat] sql:", e)
        return []


def _money(v):
    try:
        return ("{:,.0f}".format(float(v or 0))).replace(",", " ")
    except Exception:
        return "0"


def last_dates(limit=2):
    rows = _q("SELECT DISTINCT date FROM earnings WHERE date IS NOT NULL AND date<>'' "
              "ORDER BY substr(date,7,4)||substr(date,4,2)||substr(date,1,2) DESC LIMIT ?", (limit,))
    return [r["date"] for r in rows]


def day_snapshot(limit=2):
    """Компактная сводка по последним дням: выручка машин, водители, касса, ремонты."""
    parts = []
    for d in last_dates(limit):
        e = _q("SELECT gos, driver, amount, owner FROM earnings WHERE date=?", (d,))
        total = sum(float(r["amount"] or 0) for r in e)
        by_owner = {}
        for r in e:
            key = r["owner"] or "не указан"
            by_owner[key] = by_owner.get(key, 0) + float(r["amount"] or 0)
        top = sorted(e, key=lambda r: -float(r["amount"] or 0))[:6]
        idle = _q("SELECT plate FROM cars WHERE plate NOT IN (SELECT gos FROM earnings WHERE date=?)", (d,))
        cash = _q("SELECT type, sum FROM cash WHERE date=?", (d,))
        cin = sum(float(r["sum"] or 0) for r in cash if "приход" in str(r["type"]).lower())
        cout = sum(float(r["sum"] or 0) for r in cash if "расход" in str(r["type"]).lower())
        rep = _q("SELECT gos, work, total FROM repairs WHERE date=?", (d,))
        rep_sum = sum(float(r["total"] or 0) for r in rep)

        L = ["ДЕНЬ " + d + ": заработали " + _money(total) + " ₽, строк " + str(len(e))]
        if by_owner:
            L.append("  по владельцам: " + "; ".join(k + " " + _money(v) + " ₽" for k, v in by_owner.items()))
        if top:
            L.append("  топ машин: " + "; ".join(
                str(r["gos"]) + " " + _money(r["amount"]) + " ₽" +
                ((" (" + str(r["driver"]) + ")") if r.get("driver") else "") for r in top))
        if idle:
            names = ", ".join(str(r["plate"]) for r in idle[:14])
            L.append("  без заработка (" + str(len(idle)) + "): " + names + (" …" if len(idle) > 14 else ""))
        L.append("  касса: приход " + _money(cin) + " ₽, расход " + _money(cout) + " ₽")
        if rep:
            L.append("  ремонты: " + str(len(rep)) + " на " + _money(rep_sum) + " ₽ — " +
                     "; ".join(str(r["gos"]) + " " + str(r["work"]) for r in rep[:4]))
        parts.append("\n".join(L))
    return "\n".join(parts)


CHAT_PROMPT = (
    "Ты — «Главный таксист», помощник таксопарка Данила в Telegram. С тобой переписывается {name}.\n"
    "Общайся как живой человек: тепло, по-свойски, коротко — 2–5 предложений, без канцелярита, "
    "можно с эмодзи и с встречным вопросом. Не здоровайся заново в каждом сообщении.\n"
    "Про машины говори конкретно: какая сколько принесла, кто не выходил, у кого ремонт, "
    "кого стоит проверить. Числа бери только из данных ниже, не выдумывай. "
    "Если данных за нужный период нет — так и скажи.\n"
    "Правила денег: кредит АБ {ab} ₽/день, кредит Даниила {dan} ₽/день, ЗП Ани {fee} ₽/день.\n"
    "Если просят сложную аналитику за период (месяц, сравнение, средние) — предложи спросить точнее, "
    "а сам не выдумывай.\n\n"
    "ДАННЫЕ (из таблицы, последние дни):\n{data}"
)


def chat_reply(text, name=None, history=None):
    """Ответ в режиме живого разговора, с памятью последних сообщений."""
    try:
        data = day_snapshot(2) or "данных по дням пока нет"
    except Exception as e:
        print("[chat] snapshot:", e)
        data = "данных по дням пока нет"

    sys = CHAT_PROMPT.format(
        name=name or "сотрудник", ab=_money(CREDIT_AB_DAY),
        dan=_money(CREDIT_DAN_DAY), fee=_money(ANNA_FEE), data=data
    )
    msgs = [{"role": "system", "content": sys}]
    for u, a in (history or [])[-6:]:
        msgs.append({"role": "user", "content": str(u)})
        msgs.append({"role": "assistant", "content": str(a)})
    msgs.append({"role": "user", "content": text})

    ans = _llm(msgs, "groq") or _llm(msgs, "openrouter")
    if ans:
        return ans.strip()
    return "Сейчас не смог подумать 🤷\nВот что по последним дням:\n\n" + data