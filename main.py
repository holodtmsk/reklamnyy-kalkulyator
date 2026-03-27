from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import sqlite3, json, os, httpx, asyncio
from datetime import datetime
from typing import Optional
import io

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_PATH = "data/sbt.db"
PRICE_LIST_PATH = "data/pricelist.txt"
# DeepSeek direct API
AMVERA_API_URL = "https://kong-proxy.yc.amvera.ru/api/v1/models/deepseek"
AMVERA_TOKEN = os.getenv("AMVERA_TOKEN")
MODEL = "deepseek-V3"
VALID_USERS = [f"sbt0{i}" for i in range(1, 6)]

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS calculations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        messages TEXT NOT NULL,
        result TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()

init_db()

def get_system_prompt():
    import os as _os

    # Fallback prompt embedded in code (used if file not found)
    EMBEDDED_PROMPT = """Ты — технолог рекламно-производственной компании ПРОДВИЖЕНИЕ. Составляешь сметы себестоимости рекламных конструкций. Отвечаешь кратко и профессионально.

РЕЖИМ РАБОТЫ: ЖЁСТКОЕ СЛЕДОВАНИЕ ИНСТРУКЦИИ.
При расчётах запрещено отходить от формата, указанных формул и порядка действий.
Если невозможно выполнить пункт инструкции — выдавать ошибку и остановку расчёта.

Запрещено:
- вставлять ориентировочные цены без пометки «ориентировочно» и без причины.
- изменять формат таблиц и итогов.
- пропускать обязательные расходники и операции из чек-листов.
- выдавать варианты 1/2/3 вместо одной полной калькуляции.
- заменять таблицы списками или текстом.

═══════════════════════════════════════════
ФОРМАТ КАЛЬКУЛЯЦИИ — СТРОГО ОБЯЗАТЕЛЕН
═══════════════════════════════════════════

ФОРМА ТАБЛИЦЫ ОБЯЗАТЕЛЬНА, БЕЗ ВОЛЬНОГО ФОРМАТИРОВАНИЯ!
Всегда придерживайся такого формата. Всегда пиши если не удалось найти позицию в прайсе.

Задание: [как понял задачу, на основании чего считал]

**МАТЕРИАЛЫ**
| Наименование | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий (зачем и откуда цена) |
|---|---|---|---|---|---|
| ПВХ 5 мм | м² | 1,50 | 846,00 | 1 269,00 | На основание, из прайса: «ПВХ 5 мм» |
| Акрил 2 мм | м² | 1,20 | 1 470,00 | 1 764,00 | На карманы, из прайса: «Акрил / Оргстекло (прозрачное) 2 мм» |

**ИТОГО Материалы: ХХХ руб**

**ЧПУ / Станочные операции**
| Операция | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |
|---|---|---|---|---|---|
| Фрезеровка ПВХ, 1 проход | м.пог | 4,0 | 40 | 160 | Периметр 4,0 м, ПВХ 5мм ÷ 5мм/проход = 1 проход |
| Лазерная резка акрила 2мм | м.пог | 24,0 | 40 | 960 | Лазерная резка карманов |

**ИТОГО ЧПУ: ХХХ руб**

**ПЕЧАТЬ / Печатный цех** (раздел только если менеджер запросил, иначе — пропустить полностью)
| Операция | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |
|---|---|---|---|---|---|
| УФ печать на ПВХ (без белого) | м² | 1,0 | 1 125 | 1 125 | из прайса |

**ИТОГО ПЕЧАТЬ: ХХХ руб**

**ФОТ / Ручные операции** (500 руб/час = 8,33 руб/мин)
| № | Операция | Норма | Ед.изм. | Минут | Часы | Сумма |
|---|---|---|---|---|---|---|
| 1 | Подготовка рабочего места | фикс 15 мин | заказ | 15 | 0,25 | 125 руб |
| 2 | Оклейка плоской поверхности без подворота | 25 мин/м² | м² | 22 | 0,37 | 183 руб |
| 3 | Зачистка канта после фрезеровки | 4 мин/п.м. | п.м. | 16 | 0,27 | 133 руб |
| 4 | Финальная очистка и контроль | фикс 10 мин | заказ | 10 | 0,17 | 83 руб |
| 5 | Упаковка в стрейч | фикс 10 мин | заказ | 10 | 0,17 | 83 руб |

Базовый ФОТ: ХХХ руб | Коэф. масштаба (повт. мин): ×X,XX | Наценка +15%: +ХХХ руб
**ИТОГО ФОТ: ХХХ руб** (округлено вверх до сотни)

**ИТОГОВАЯ СЕБЕСТОИМОСТЬ**
Материалы: ХХХ руб
ЧПУ: ХХХ руб
ФОТ: ХХХ руб
Печать: ХХХ руб (или «—» если нет)
Монтаж: — (заполняет мастер)
**ЦЕНА = (Материалы + ФОТ) × 1,7 + ЧПУ + Печать = ХХХ руб**

═══════════════════════════════════════════
ШАГ 0 — ПЕРЕД РАСЧЁТОМ
═══════════════════════════════════════════

Если способ монтажа не указан — ОБЯЗАТЕЛЬНО спроси у менеджера:
- Самовывоз (клиент монтирует сам) → метизы для монтажа не включаем
- Наш монтаж → уточни способ: скотч к стене / саморезы / дюбели / дистанционные держатели / тросы / другое

═══════════════════════════════════════════
РАБОТА С ПРАЙСОМ
═══════════════════════════════════════════

ВСЕГДА брать цену из прайса. В прайсе каждая позиция записана через «/» с синонимами и характеристиками в скобках.
Пример: «Акрил / Оргстекло (прозрачное) 2 мм» — менеджер может написать «акрил прозрачный 2мм», «оргстекло 2мм» — это одна позиция.

Правила поиска:
1. Искать по синонимам (акрил = оргстекло, АКП = композит, профтруба = труба).
2. Учитывать толщину, цвет, прозрачность.
3. Если найдена — указать точное название из прайса в комментарии.
4. Если не найдена — СТОП: «В прайсе нет [название]. Уточни.»
5. Только по явному запросу — ставить ОРИЕНТИРОВОЧНО с причиной.

Пересчёт листовых материалов (цена «за шт» в прайсе):
- Цена/м² = цена_листа ÷ (ширина × длина из наименования)
- В комментарии обязательно: «Пересчёт из листа [размер], [цена] ÷ [площадь] = [цена/м²]»
- Пример: «ПВХ 3 мм (2,03×3,05), 3156,99 руб/шт» → 6,1815 м² → 510,64 руб/м²

Плёнки Oracal (цена в пог.м):
- Ширина рулона 1,26 м или 1,00 м
- Длина = (площадь × 1,05) ÷ ширину, округлить вверх до 0,1 м
- Цена = длина × цена_пог.м. НЕЛЬЗЯ считать по м² если цена в пог.м.

═══════════════════════════════════════════
РАСЧЁТ МАТЕРИАЛОВ
═══════════════════════════════════════════

Листовые материалы:
- +10% припуск к площади изделия
- Разные толщины и цвета НЕ суммировать
- Много мелких элементов (буквы, текст) → брать габарит надписи, припуск 10% НЕ ставить

Профильные трубы:
- L_факт = кол-во × 2 × (ширина + высота), мм → м, округлить вверх до 0,01 м
- Закуп: труба хлыст 6 м → L_закуп = ceil(L_факт / 6) × 6
- В Материалы — L_закуп; в ЧПУ и ФОТ — L_факт
- Рамка прямоугольная без перемычек: швов = 4 × кол-во; резов = 8 × кол-во

Скотч двусторонний:
- 3М узкие полоски — крепление накладных элементов к поверхности/стене
- 3М листовой — крепление элементов друг к другу (буквы на основу и т.д.)

═══════════════════════════════════════════
ЧПУ / СТАНОЧНЫЕ ОПЕРАЦИИ
═══════════════════════════════════════════

Считаем ТОЛЬКО: фрезеровка, лазерная резка, пробивка на КПП, полимерное покрытие, плазменная резка.
НЕ считаем: плоттерная резка, гибка труб, вальцовка, листогиб, резка труб.

Глубина за проход:
- Фрезеровка ПВХ: 5 мм/проход
- Фрезеровка акрил/АКП: 4 мм/проход
- Лазер металл: 6 мм/проход (металл >10 мм → плазма)
- Лазер пластик: 4 мм/проход (ПВХ лазером НЕЛЬЗЯ — токсично!)
- Полимерное покрытие: цена за м² площади развёртки (периметр сечения × длина)
- Пробивка оцинковки: цена за час

Количество проходов = ceil(толщина / глубина_за_проход)
Периметр реза = все линии реза (внешние + внутренние) + 2–3% страховка
Лазерная резка → чистый рез → зачистка и абразив НЕ добавляются
Полимерное покрытие → в Материалы НЕ дублировать; в ФОТ — только маскировка/подвес (25 мин) и снятие (20 мин)

═══════════════════════════════════════════
РАСХОДНИКИ
═══════════════════════════════════════════

При оклейке плёнкой или полноцвете (исключений нет):
- Ветошь: 0,5 м²/м² оклейки, минимум 0,2 м²
- Спирт: 0,1 л/м², минимум 0,1 л
- Стрейч-плёнка: ≤1 м² → 3 м.пог; 1–3 м² → 5 м.пог; >3 м² → 8 м.пог (2 руб/м.пог)

При фрезеровке пластика (ПВХ/акрил/ПЭТ) или АКП:
- Абразив: 1 шт / 7 м.п. зачистки, минимум 1 шт

При сварке металла (триггер: рама/труба/каркас/сварка):
- Сварочные материалы: 5 руб/м.п. шва, минимум 300 руб
- Отрезной круг: 1 шт / 20 м.п. трубы, минимум 1 шт
- Зачистной круг: 1 шт / 20 м.п. трубы, минимум 1 шт
- Грунт: 0,024 кг / м.п. металлоконструкции

При накладных элементах (буквы, карманы на основу):
- Двусторонний скотч 3М + ФОТ на крепление (15 мин/шт)

═══════════════════════════════════════════
ФОТ — ТАБЛИЦА ОПЕРАЦИЙ И НОРМЫ
═══════════════════════════════════════════

Ставка: 500 руб/час = 8,33 руб/мин

ОБЩИЕ ОПЕРАЦИИ (фикс, не масштабируются):
- Подготовка рабочего места: фикс 15 мин/заказ → 125 руб
- Финальная очистка и контроль: фикс 10 мин/заказ → 83 руб
- Упаковка в стрейч: фикс 10 мин/заказ → 83 руб
- Крепление накладного элемента: фикс 15 мин/шт → 125 руб/шт

РАБОТА С ПЛЁНКАМИ (масштабируются):
- Оклейка плоской поверхности с подворотом: 35 мин/м²
- Оклейка плоской поверхности без подворота (в край): 25 мин/м²
- Кантик плёнкой (торец 5–10 мм): 10 мин/п.м.
- Нанесение аппликации по макету: 15 мин/м²
- Изготовление и позиционирование трафарета: фикс 60 мин/компл → 500 руб
- Наклейка отдельных букв/элементов: 1,5 мин/шт
- Резка и подгонка плёнки для элемента: 12 мин/шт

АКРИЛ / ПВХ (масштабируются):
- Зачистка канта/торца после фрезеровки: 4 мин/п.м. (ТОЛЬКО при фрезеровке, НЕ при лазере)
- Обезжиривание поверхности: 5 мин/м²
- Сборка плоского кармана: 10 мин/шт
- Сборка объёмного кармана: 15 мин/шт
- Крепление кармана к подложке: 5 мин/шт
- Установка дистанционных держателей: 10 мин/шт

МЕТАЛЛОКОНСТРУКЦИИ (масштабируются):
- Разметка и резка профильной трубы: 11 мин/рез
- Сварка рамки/узлов: 20 мин/шов
- Зачистка сварного шва: 10 мин/шов
- Сверление/пробивка крепёжных отверстий: фикс 25 мин/заказ
- Маскировка и подвес под порошковое покрытие: фикс 25 мин/заказ
- Снятие после покраски, демонтаж маскировок: фикс 20 мин/заказ
- Подгонка и мелкая доводка после покраски: фикс 10 мин/заказ
- Контроль геометрии металлоконструкции: фикс 15 мин/заказ

ПОРЯДОК РАСЧЁТА ФОТ:
1. Все операции: кол-во × норма = минуты; минуты × 8,33 = сумма
2. Базовый ФОТ = сумма всех операций
3. Коэффициент масштаба (ТОЛЬКО повторяющиеся: резка/сварка/зачистка/оклейка):
   - до 60 мин → ×1,00
   - 60–120 мин → ×0,80
   - 120–240 мин → ×0,70
   - 240–480 мин → ×0,60
   Фикс-операции НЕ масштабируются
4. Наценка: ФОТ < 10 000 руб → +15%; ФОТ ≥ 10 000 руб → +10%
5. Округлить ВВЕРХ до ближайшей сотни

═══════════════════════════════════════════
ПЕЧАТЬ
═══════════════════════════════════════════

Добавлять ТОЛЬКО если менеджер явно запросил. Виды:
1. УФ печать — за м², цена зависит от материала. Уточнить: с белым или без белого.
2. Сольвентная печать на плёнке — за м². Уточнить: 720 dpi или 1440 dpi.
3. Ламинация — за м² печатного поля. Добавлять только если запросили, не предлагать.
При печати → ОБЯЗАТЕЛЬНО добавить ветошь, спирт, стрейч.

═══════════════════════════════════════════
ОСОБЕННОСТИ ПО ТИПАМ ИЗДЕЛИЙ
═══════════════════════════════════════════

ТАБЛИЧКИ:
- Дистанционные держатели + наш монтаж → «Бур» 550 руб, держателей 4 шт по углам
- Лазерная резка пластика → зачистку и абразив НЕ добавлять

ИНФОРМАЦИОННЫЕ СТЕНДЫ (с карманами):
- Карманы: акрил 2 мм или прозрачный ПЭТ
- Фрезеровка ПВХ → абразив (1 шт / 7 м.п.); лазерная резка → абразив не нужен
- Дистанционных держателей больше чем для таблички (из-за веса)

РАМЫ ДЛЯ БАННЕРОВ:
- Трубы: 20×20, 25×25, 20×40. Выбирать точную номенклатуру из прайса
- Открытые срезы → пластиковые заглушки
- Стяжки для баннера: через каждые 50 мм по периметру
- Расходники: отрезной круг 1 шт/20 м.п., зачистной 1 шт/20 м.п., грунт 0,024 кг/м.п., растворитель

МЕТАЛЛОКАРКАСЫ (триггер: рама/труба/сварка/каркас):
- Материалы: труба + сварочные ≥300 руб + отрезной и зачистной круги (мин. 1+1)
- ЧПУ: полимерное покрытие по площади развёртки (периметр сечения × длина)
- ФОТ: разметка и резка + сварка + зачистка швов + маскировка/подвес + снятие после покраски

═══════════════════════════════════════════
ЧЕКЛИСТ ПЕРЕД ВЫДАЧЕЙ (ОБЯЗАТЕЛЬНО)
═══════════════════════════════════════════

[ ] Все размеры в м/м²
[ ] Цены из прайса, у каждой позиции — источник в комментарии
[ ] Плёнка считается в пог.м, не в м²
[ ] ЧПУ: технология правильная, проходы рассчитаны
[ ] Есть оклейка/печать → ветошь + спирт + стрейч добавлены
[ ] Есть фрезеровка пластика/АКП → зачистка (ФОТ) + абразив добавлены
[ ] Есть металл/рама/сварка → резка+сварка+зачистка (ФОТ) + расходники
[ ] Количество резов и швов по факту, не по длине трубы
[ ] Сварочные материалы ≥300 руб, круги минимум по 1 шт
[ ] При порошковом покрытии → маскировка/подвес + снятие (ФОТ)
[ ] Накладные элементы → скотч + ФОТ на крепление
[ ] Способ монтажа согласован с менеджером
[ ] Антидубль: фрезеровка/лазер/сварка/покрытие — каждое только в своём разделе
[ ] ФОТ округлён вверх до сотни
[ ] Формула: (Материалы + ФОТ) × 1,7 + ЧПУ + Печать

═══════════════════════════════════════════
ПРАЙС
═══════════════════════════════════════════
"""

    # Try to load from file (allows updating without redeploy)
    base_prompt = ""
    paths = [
        "/app/data/system_prompt.txt",
        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "system_prompt.txt"),
    ]
    for path in paths:
        if _os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    txt = f.read().strip()
                if len(txt) > 500:  # valid prompt
                    base_prompt = txt
                    print(f"[PROMPT] Loaded from file: {path} ({len(txt)} chars)", flush=True)
                    break
            except Exception as e:
                print(f"[PROMPT] Error reading {path}: {e}", flush=True)

    if not base_prompt:
        base_prompt = EMBEDDED_PROMPT
        print(f"[PROMPT] Using embedded prompt ({len(base_prompt)} chars)", flush=True)

    # Append pricelist
    if _os.path.exists(PRICE_LIST_PATH):
        try:
            with open(PRICE_LIST_PATH, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            import json as _json, re as _re
            if raw.strip().startswith('{'):
                pdata = _json.loads(raw)
                lines = []
                for group, items in pdata.items():
                    lines.append(f"== {group} ==")
                    for item in items:
                        lines.append(f"{item['num']}. {item['name']} | {item['unit']} | {item['price']} руб.")
                base_prompt += "\n\n" + "\n".join(lines)
            else:
                base_prompt += "\n\n" + raw
        except Exception:
            pass

    return base_prompt



@app.get("/debug-prompt")
async def debug_prompt():
    import os as _os
    paths = [
        "/app/data/system_prompt.txt",
        "data/system_prompt.txt",
        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "system_prompt.txt"),
    ]
    result = {}
    for path in paths:
        result[path] = {
            "exists": _os.path.exists(path),
            "size": _os.path.getsize(path) if _os.path.exists(path) else 0,
        }
    # Also show cwd and listdir
    result["cwd"] = _os.getcwd()
    try:
        result["ls_data"] = _os.listdir("data")
    except Exception as e:
        result["ls_data"] = str(e)
    try:
        result["ls_app_data"] = _os.listdir("/app/data")
    except Exception as e:
        result["ls_app_data"] = str(e)
    
    prompt = get_system_prompt()
    result["prompt_loaded_chars"] = len(prompt)
    result["prompt_first_100"] = prompt[:100]
    return result

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


class LoginRequest(BaseModel):
    user_id: str

@app.post("/api/login")
async def login(req: LoginRequest):
    if req.user_id not in VALID_USERS:
        raise HTTPException(status_code=401, detail="Неверный логин")
    return {"ok": True, "user_id": req.user_id}


@app.get("/api/calculations/{user_id}")
async def get_calculations(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, updated_at FROM calculations WHERE user_id=? ORDER BY updated_at DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "updated_at": r[2]} for r in rows]


@app.get("/api/calculation/{calc_id}")
async def get_calculation(calc_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, messages, user_id FROM calculations WHERE id=?", (calc_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    msgs = json.loads(row[2]) if row[2] else []
    return {"id": row[0], "title": row[1], "messages": msgs, "user_id": row[3]}


class NewCalcRequest(BaseModel):
    user_id: str

@app.post("/api/calculation/new")
async def new_calculation(req: NewCalcRequest):
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO calculations (user_id, title, messages, created_at, updated_at) VALUES (?,?,?,?,?)",
              (req.user_id, "Новый расчёт", "[]", now, now))
    calc_id = c.lastrowid
    conn.commit()
    conn.close()
    return {"id": calc_id}


@app.delete("/api/calculation/{calc_id}")
async def delete_calculation(calc_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM calculations WHERE id=?", (calc_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


class ChatMessage(BaseModel):
    message: str

@app.post("/api/chat/{calc_id}")
async def chat(calc_id: int, msg: ChatMessage):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT messages, user_id, title FROM calculations WHERE id=?", (calc_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Calculation not found")

    messages = json.loads(row[0]) if row[0] else []
    messages.append({"role": "user", "content": msg.message})

    # messages will be passed to DeepSeek via openai_messages in the API call below
    pass

    token = AMVERA_TOKEN or ""
    assistant_message = ""

    for _attempt in range(2):
        try:
            system_prompt = get_system_prompt()
            # Use system role - Amvera proxy supports it and doesn't count against body size limit
            api_messages = [{"role": "system", "text": system_prompt}]
            for m in messages:
                api_messages.append({"role": m["role"], "text": m["content"]})

            body = {"model": MODEL, "messages": api_messages}
            # Clean payload - remove control chars that break Amvera proxy JSON parsing
            payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    AMVERA_API_URL,
                    headers={
                        "X-Auth-Token": f"Bearer {token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    content=payload
                )
                if resp.status_code != 200:
                    raise Exception(f"API {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
                raw = data["choices"][0]["message"].get("text") or data["choices"][0]["message"].get("content") or ""
                import re as re_mod
                assistant_message = re_mod.sub(r"<think>.*?</think>", "", raw, flags=re_mod.DOTALL).strip()
                ticks = chr(96) * 3
                assistant_message = re_mod.sub(r"^" + ticks + r"[a-z]*", "", assistant_message.lstrip()).strip()
                assistant_message = re_mod.sub(ticks + r"$", "", assistant_message.rstrip()).strip()
                break
        except Exception as e:
            if _attempt == 0:
                await asyncio.sleep(2)
                continue
            assistant_message = f"Ошибка API: {str(e)}"

    messages.append({"role": "assistant", "content": assistant_message})

    # Update title from first user message
    title = row[2]
    user_msgs = [m for m in messages if m["role"] == "user"]
    if len(user_msgs) == 1:
        title = user_msgs[0]["content"][:60].replace("\n", " ")

    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE calculations SET messages=?, title=?, updated_at=? WHERE id=?",
              (json.dumps(messages, ensure_ascii=False), title, now, calc_id))
    conn.commit()
    conn.close()

    return {"reply": assistant_message, "title": title}


@app.post("/api/upload-pricelist")
async def upload_pricelist(file: UploadFile = File(...)):
    content = await file.read()
    text = ""
    fname = (file.filename or "").lower()
    if fname.endswith(".xlsx") or fname.endswith(".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    parts = [str(c).strip() if c is not None else "" for c in row]
                    # Skip completely empty rows
                    if any(p for p in parts):
                        lines.append(", ".join(p for p in parts if p))
            text = "\n".join(lines)
        except Exception as e:
            text = f"Ошибка чтения Excel: {e}"
    elif fname.endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    t = page.extract_text(x_tolerance=3, y_tolerance=3)
                    if t:
                        text += t + "\n"
        except Exception:
            text = content.decode("utf-8", errors="ignore")
    else:
        try:
            text = content.decode("utf-8")
        except Exception:
            try:
                text = content.decode("cp1251")
            except Exception:
                text = content.decode("utf-8", errors="ignore")

    os.makedirs("data", exist_ok=True)
    with open(PRICE_LIST_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    return {"ok": True, "chars": len(text)}


@app.get("/api/pricelist-status")
async def pricelist_status():
    if os.path.exists(PRICE_LIST_PATH):
        size = os.path.getsize(PRICE_LIST_PATH)
        return {"loaded": True, "size": size}
    return {"loaded": False, "size": 0}


@app.get("/api/pricelist-content")
async def pricelist_content():
    if not os.path.exists(PRICE_LIST_PATH):
        return {"content": "", "lines": []}
    with open(PRICE_LIST_PATH, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    lines = [l for l in text.split("\n") if l.strip()]
    return {"content": text, "lines": lines}


@app.post("/api/pricelist-save")
async def pricelist_save(request: Request):
    body = await request.json()
    content = body.get("content", "")
    os.makedirs("data", exist_ok=True)
    with open(PRICE_LIST_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "chars": len(content)}
