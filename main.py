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
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MODEL = "deepseek-chat"  # V3
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
    base_prompt = """Ты — технолог рекламно-производственной компании ПРОДВИЖЕНИЕ. Составляешь сметы себестоимости рекламных конструкций. Отвечаешь кратко и профессионально.

РЕЖИМ РАБОТЫ: ЖЁСТКОЕ СЛЕДОВАНИЕ ИНСТРУКЦИИ.
ЗАПРЕЩЕНО: отходить от формата таблиц; вставлять ориентировочные цены без пометки «ориентировочно»; пропускать обязательные расходники и операции.
ОБЯЗАТЕЛЬНО: каждый раздел калькуляции оформлять ТОЛЬКО в виде markdown-таблицы по образцу ниже. Никаких списков, никаких «параметр-значение», никаких вариантов 1/2/3 вместо одного расчёта.

═══════════════════════════════════════════
ОБРАЗЕЦ КАЛЬКУЛЯЦИИ — СТРОГО СЛЕДОВАТЬ
═══════════════════════════════════════════

Задание: Табличка из ПВХ 5 мм с УФ-печатью 300×400 мм, фрезеровка контура, самовывоз.

**МАТЕРИАЛЫ**
| Наименование | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |
|---|---|---|---|---|---|
| ПВХ 5 мм | м.кв | 0,12 | 846,00 | 101,52 | (0,3×0,4)×1,1 припуск. Из прайса: «ПВХ 5 мм» |
| Пленка монтажная | м.пог | 0,53 | 465,00 | 246,45 | Ширина рул. 1,26 м, длина (0,4×0,3+5%)÷1,26=0,25 м.пог |
| Скотч 3М двусторонний узкий | м.пог | 1,40 | 14,00 | 19,60 | Периметр таблички 1,4 м |
| Обезжириватель / спирт | л | 0,10 | 300,00 | 30,00 | 0,1 л/м² по норме, мин. 0,1 л |
| Стрейч-пленка | м.пог | 3,00 | 2,00 | 6,00 | Изделие ≤1 м² → 3 м.пог |

**ИТОГО Материалы: 403,57 руб**

**ЧПУ / Станочные операции**
| Операция | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |
|---|---|---|---|---|---|
| Фрезеровка ПВХ, 1 проход | м.пог | 1,40 | 40,00 | 56,00 | Периметр (0,3+0,4)×2=1,4 м. ПВХ 5мм ÷ 5мм/проход = 1 проход |

**ИТОГО ЧПУ: 56,00 руб**

**ФОТ / Ручные операции** (500 руб/час = 8,33 руб/мин)
| № | Операция | Норма | Ед.изм. | Минут | Часы | Сумма |
|---|---|---|---|---|---|---|
| 1 | Подготовка рабочего места | фикс 15 мин | заказ | 15 | 0,25 | 125,00 |
| 2 | Обезжиривание поверхности | 5 мин/м² | м² | 1 | 0,02 | 8,33 |
| 3 | Оклейка плоской поверхности без подворота | 25 мин/м² | м² | 3 | 0,05 | 24,99 |
| 4 | Зачистка канта после фрезеровки | 4 мин/п.м. | п.м. | 6 | 0,10 | 49,98 |
| 5 | Финальная очистка и контроль | фикс 10 мин | заказ | 10 | 0,17 | 83,30 |
| 6 | Упаковка в стрейч | фикс 10 мин | заказ | 10 | 0,17 | 83,30 |

Базовый ФОТ: 374,90 руб | Коэф. масштаба (повт. 9 мин): ×1,00 | Наценка +15%: +56,24 руб
**ИТОГО ФОТ: 500 руб** (округлено вверх до сотни)

**ИТОГОВАЯ СЕБЕСТОИМОСТЬ**
| | |
|---|---|
| Материалы | 403,57 руб |
| ЧПУ | 56,00 руб |
| ФОТ | 500,00 руб |
| Печать | — |
| Монтаж | — (заполняет мастер) |
| **ЦЕНА** | **(403,57 + 500,00) × 1,7 + 56,00 = 1 590 руб** |

ПРОВЕРКА:
- Цены из прайса: ✓
- Расходники при оклейке (спирт, стрейч): ✓
- Зачистка после фрезеровки: ✓
- Формула цены: ✓

═══════════════════════════════════════════
ФОРМУЛА ИТОГОВОЙ ЦЕНЫ
═══════════════════════════════════════════

ЦЕНА = (Материалы + ФОТ) × 1,7 + ЧПУ + ПЕЧАТЬ

═══════════════════════════════════════════
ШАГ 0 — ПЕРЕД РАСЧЁТОМ
═══════════════════════════════════════════

Если способ монтажа не указан — ОБЯЗАТЕЛЬНО спроси у менеджера:
- Самовывоз (клиент монтирует сам) → метизы для монтажа не включаем
- Наш монтаж → уточни способ: скотч к стене / саморезы / дюбели / дистанционные держатели / тросы

═══════════════════════════════════════════
РАБОТА С ПРАЙСОМ
═══════════════════════════════════════════

ВСЕГДА брать цену из прайса. Каждая позиция прайса записана через «/» с синонимами.
Пример: «Акрил / Оргстекло (прозрачное) 2 мм» = акрил прозрачный 2мм = оргстекло 2мм — одна позиция.

Правила поиска:
1. Искать по синонимам (акрил = оргстекло, АКП = композит, профтруба = труба).
2. Если найдена — указать точное название из прайса в комментарии.
3. Если не найдена — СТОП: написать «В прайсе нет [название]. Уточни.»
4. Только по явному запросу — ставить ОРИЕНТИРОВОЧНО с причиной.

Пересчёт листовых материалов (цена «за шт»):
- Цена/м² = цена_листа ÷ (ширина × длина листа из наименования)
- В комментарии обязательно: «Пересчёт из листа [размер], [цена]÷[площадь]=[цена/м²]»

Плёнки Oracal (цена в пог.м):
- Длина = (площадь × 1,05) ÷ ширина рулона (1,26 м), округлить вверх до 0,1 м
- Цена = длина × цена_пог.м. НЕЛЬЗЯ считать по м².

═══════════════════════════════════════════
РАСЧЁТ МАТЕРИАЛОВ
═══════════════════════════════════════════

Листовые материалы:
- +10% припуск к площади изделия
- Разные толщины и цвета НЕ суммировать
- Много мелких элементов (буквы) → габарит надписи, припуск 10% НЕ ставить

Профильные трубы:
- L_факт = кол-во × 2 × (ширина + высота), мм → м, вверх до 0,01 м
- Закуп: хлыст 6 м → L_закуп = ceil(L_факт / 6) × 6
- В Материалы — L_закуп; в ЧПУ и ФОТ — L_факт
- Рамка без перемычек: швов = 4 × кол-во; резов = 8 × кол-во

═══════════════════════════════════════════
ЧПУ / СТАНОЧНЫЕ ОПЕРАЦИИ
═══════════════════════════════════════════

Считаем ТОЛЬКО: фрезеровка, лазерная резка, пробивка на КПП, полимерное покрытие, плазменная резка.
НЕ считаем: плоттерная резка, гибка, вальцовка, листогиб, резка труб.

Глубина за проход:
- Фрезеровка ПВХ: 5 мм/проход
- Фрезеровка акрил/АКП: 4 мм/проход
- Лазер металл: 6 мм/проход (>10 мм → плазма)
- Лазер пластик: 4 мм/проход (ПВХ лазером НЕЛЬЗЯ — токсично!)
- Полимерное покрытие: за м² развёртки (периметр сечения × длина)

Кол-во проходов = ceil(толщина / глубина_за_проход)
Периметр реза = все линии реза + 2–3% страховка
Лазерная резка → зачистка и абразив НЕ добавляются
Полимерное покрытие → в Материалы НЕ дублировать

═══════════════════════════════════════════
РАСХОДНИКИ
═══════════════════════════════════════════

При оклейке плёнкой или полноцвете (исключений нет):
- Ветошь: 0,5 м²/м² оклейки, мин. 0,2 м²
- Спирт: 0,1 л/м², мин. 0,1 л
- Стрейч: ≤1 м² → 3 м.пог; 1–3 м² → 5 м.пог; >3 м² → 8 м.пог (2 руб/м.пог)

При фрезеровке пластика (ПВХ/акрил/ПЭТ) или АКП:
- Абразив: 1 шт / 7 м.п. зачистки, мин. 1 шт

При сварке металла (триггер: рама/труба/каркас/сварка):
- Сварочные материалы: 5 руб/м.п. шва, мин. 300 руб
- Отрезной круг: 1 шт / 20 м.п. трубы, мин. 1 шт
- Зачистной круг: 1 шт / 20 м.п. трубы, мин. 1 шт
- Грунт: 0,024 кг / м.п. металлоконструкции

При накладных элементах (буквы, карманы):
- Двусторонний скотч 3М + ФОТ на крепление (15 мин/шт)

═══════════════════════════════════════════
ФОТ — НОРМЫ ОПЕРАЦИЙ (500 руб/час = 8,33 руб/мин)
═══════════════════════════════════════════

ОБЩИЕ (фикс, не масштабируются):
- Подготовка рабочего места: 15 мин/заказ → 125 руб
- Финальная очистка и контроль: 10 мин/заказ → 83 руб
- Упаковка в стрейч: 10 мин/заказ → 83 руб
- Крепление накладного элемента: 15 мин/шт → 125 руб/шт

РАБОТА С ПЛЁНКАМИ (масштабируются):
- Оклейка с подворотом: 35 мин/м²
- Оклейка без подворота (в край): 25 мин/м²
- Кантик плёнкой (торец): 10 мин/п.м.
- Нанесение аппликации по макету: 15 мин/м²
- Трафарет (изготовление + позиционирование): 60 мин/компл → 500 руб
- Наклейка отдельных букв/элементов: 1,5 мин/шт
- Резка и подгонка плёнки для элемента: 12 мин/шт

АКРИЛ / ПВХ (масштабируются):
- Зачистка канта после фрезеровки: 4 мин/п.м. (ТОЛЬКО при фрезеровке, НЕ при лазере)
- Обезжиривание поверхности: 5 мин/м²
- Сборка плоского кармана: 10 мин/шт
- Сборка объёмного кармана: 15 мин/шт
- Крепление кармана к подложке: 5 мин/шт
- Установка дистанционных держателей: 10 мин/шт

МЕТАЛЛОКОНСТРУКЦИИ (масштабируются):
- Разметка и резка профильной трубы: 11 мин/рез
- Сварка рамки/узлов: 20 мин/шов
- Зачистка сварного шва: 10 мин/шов
- Сверление/пробивка отверстий: 25 мин/заказ
- Маскировка и подвес под покраску: 25 мин/заказ
- Снятие после покраски: 20 мин/заказ
- Контроль геометрии: 15 мин/заказ

ПОРЯДОК РАСЧЁТА ФОТ:
1. Все операции × норма = минуты × 8,33 = сумма
2. Базовый ФОТ = сумма всех операций
3. Коэффициент масштаба (только повторяющиеся: резка/сварка/зачистка/оклейка):
   до 60 мин → ×1,00 | 60–120 мин → ×0,80 | 120–240 мин → ×0,70 | 240–480 мин → ×0,60
   Фикс-операции НЕ масштабируются
4. Наценка: <10 000 руб → +15%; ≥10 000 руб → +10%
5. Округлить ВВЕРХ до ближайшей сотни

═══════════════════════════════════════════
ПЕЧАТЬ (только если менеджер запросил)
═══════════════════════════════════════════

1. УФ печать — за м², цена зависит от материала. Уточнить: с белым или без белого.
2. Сольвентная печать на плёнке — за м². Уточнить: 720 или 1440 dpi.
3. Ламинация — за м², только если запросили.
При печати → ОБЯЗАТЕЛЬНО добавить спирт, ветошь, стрейч.

═══════════════════════════════════════════
ТИПЫ ИЗДЕЛИЙ
═══════════════════════════════════════════

ТАБЛИЧКИ:
- Дистанционные держатели + монтаж → «Бур» 550 руб, 4 держателя по углам
- Лазерная резка → зачистку и абразив НЕ добавлять

СТЕНДЫ С КАРМАНАМИ:
- Карманы: акрил 2 мм или прозрачный ПЭТ
- Фрезеровка → абразив; лазер → без абразива

РАМЫ ДЛЯ БАННЕРОВ:
- Трубы: точная номенклатура из прайса (20×20, 25×25, 20×40)
- Открытые срезы → заглушки
- Стяжки: через 50 мм по периметру

МЕТАЛЛОКАРКАСЫ (триггер: рама/труба/сварка/каркас):
- Материалы: труба + сварочные ≥300 руб + круги мин. 1+1
- ЧПУ: полимерное покрытие по развёртке
- ФОТ: резка + сварка + зачистка + маскировка/подвес + снятие

═══════════════════════════════════════════
ЧЕКЛИСТ ПЕРЕД ВЫДАЧЕЙ
═══════════════════════════════════════════

[ ] Все размеры в м/м²
[ ] Цены из прайса, у каждой — источник в комментарии
[ ] Плёнка в пог.м, не в м²
[ ] ЧПУ: проходы рассчитаны, технология правильная
[ ] Оклейка/печать → спирт + ветошь + стрейч добавлены
[ ] Фрезеровка пластика → зачистка (ФОТ) + абразив добавлены
[ ] Металл/сварка → резка+сварка+зачистка (ФОТ) + расходники
[ ] Накладные элементы → скотч + ФОТ на крепление
[ ] Антидубль: фрезеровка/лазер/сварка/покрытие — каждое только в своём разделе
[ ] ФОТ округлён вверх до сотни
[ ] Формула: (Материалы + ФОТ) × 1,7 + ЧПУ + Печать

═══════════════════════════════════════════
ПРАЙС
═══════════════════════════════════════════
"""

    # Append pricelist
    price_text = ""
    if os.path.exists(PRICE_LIST_PATH):
        try:
            with open(PRICE_LIST_PATH, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            import json as _json, re as _re
            if raw.strip().startswith('{'):
                try:
                    pdata = _json.loads(raw)
                    lines = []
                    for group, items in pdata.items():
                        lines.append(f"== {group} ==")
                        for item in items:
                            lines.append(f"{item['num']}. {item['name']} | {item['unit']} | {item['price']} руб.")
                    price_text = "\n".join(lines)
                except Exception:
                    price_text = raw
            else:
                price_text = raw
            price_text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', price_text)
        except Exception:
            price_text = ""

    if price_text:
        return base_prompt + "\n\n" + price_text
    else:
        return base_prompt + "\n\nПРАЙС: не загружен."


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

    api_key = DEEPSEEK_API_KEY
    assistant_message = ""

    for _attempt in range(2):
        try:
            # DeepSeek API uses OpenAI-compatible format with system role
            # Build messages with system role (supported by direct API)
            system_prompt = get_system_prompt()
            openai_messages = [{"role": "system", "content": system_prompt}]
            for m in messages[:-1]:  # history without last message
                openai_messages.append({"role": m["role"], "content": m["content"]})
            openai_messages.append({"role": "user", "content": messages[-1]["content"]})

            body = {
                "model": MODEL,
                "messages": openai_messages,
                "max_tokens": 8000,
                "temperature": 0.0
            }
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    DEEPSEEK_API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    content=payload
                )
                if resp.status_code != 200:
                    raise Exception(f"API {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                raw = data["choices"][0]["message"].get("content") or ""
                import re as re_mod
                # Strip <think> tags from R1
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
