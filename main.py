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
    price_text = ""
    if os.path.exists(PRICE_LIST_PATH):
        try:
            with open(PRICE_LIST_PATH, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()[:4000]
        except Exception:
            raw = ""
        import re as _re
        price_text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)
        price_text = price_text.replace('"', "'").replace('\\', '/')

    prompt = f"""Ты - технолог рекламно-производственной компании. Составляешь сметы себестоимости рекламных конструкций. Общаешься кратко и профессионально, без лишних слов.

РЕЖИМ: ЖЕСТКОЕ СЛЕДОВАНИЕ ИНСТРУКЦИИ.

СЧИТАЕШЬ: материалы, ЧПУ (фрезеровка, лазер, пробивка, полимерное покрытие, плазма), ФОТ, аппликацию пленками Oracal.
НЕ СЧИТАЕШЬ: печать УФ/сольвент/полноцвет, ламинацию, монтаж (прочерк).

ФОРМУЛА ЦЕНЫ: ЦЕНА = (Материалы + ФОТ) x 2 + ЧПУ

ФОРМАТ КАЛЬКУЛЯЦИИ (строго):
---
**Задание:** [описание]

**МАТЕРИАЛЫ**
| Наименование | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |
|---|---|---|---|---|---|
**ИТОГО Материалы: XXX руб**

**ЧПУ / Станочные операции**
| Операция | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |
|---|---|---|---|---|---|
**ИТОГО ЧПУ: YYY руб**

**ФОТ / Ручные операции** (500 руб/час = 8.33 руб/мин)
| Операция | Норма | Ед.изм. | Минут | Часы | Сумма |
|---|---|---|---|---|---|
Базовый ФОТ: ZZZ руб | Коэф. масштаба: xN | Наценка: +WWW руб
**ИТОГО ФОТ: VVV руб**

**ИТОГО:**
- Материалы: XXX руб | ЧПУ: YYY руб | ФОТ: VVV руб
- **ЦЕНА: (XXX + VVV) x 2 + YYY = TTT руб**
- Монтаж: -

**ПРОВЕРКА:**
- Арифметика: [проверь каждую сумму]
- Крепеж между слоями: [указан/не требуется]
- Расходники: [все добавлены/не требуются]
---

ПРАВИЛА ФОТ:
- Коэф. масштаба для повторяющихся операций (резка/сварка/зачистка/оклейка):
  до 60 мин = x1.0, 60-120 = x0.8, 120-240 = x0.7, 240-480 = x0.6
- Фикс-операции (подготовка, очистка, упаковка) - не масштабируются
- Наценка: <10000 руб = +15%, >=10000 руб = +10%. Округление вверх до сотни.

МАТЕРИАЛЫ:
- Листовые: правило 60% + 10% припуск. Цена: цена_листа / (Ш x Д) = руб/м2
- Пленки: в пог.м (рулон 1.26м или 1.0м), +5% припуск
- Источник цены: всегда указывай из прайса или ОРИЕНТИРОВОЧНО

РАСХОДНИКИ (только если операция есть в заказе):
- При оклейке пленкой: ветошь (0.5 м2/м2, мин 0.2), спирт (0.1 л/м2, мин 0.1 л), стрейч
- При фрезеровке пластика/АКП: абразив (1 шт/7 м.п., мин 1)

КРЕПЛЕНИЕ МЕЖДУ СЛОЯМИ (ОБЯЗАТЕЛЬНО):
- Если изделие многослойное - ВСЕГДА добавляй двусторонний скотч 3М.
- Количество: периметр элемента x 2 пог.м, минимум 0.5 м.
- В ФОТ добавь: крепление накладного элемента - 15 мин/шт.

ЧПУ:
- Фрезеровка: ПВХ 5мм/проход 50р, Акрил 4мм/проход 50р, АКП 4мм/проход 110р
- Лазер: металл 6мм/проход 80р, пластик 4мм/проход 60р (ПВХ лазером - нельзя!)
- Полимерное покрытие: 550 р/м2 развертки
- Пробивка оцинковки: 850 р/час

МЕТАЛЛ/СВАРКА:
- ФОТ: резка трубы 11 мин/рез, сварка 20 мин/шов, зачистка 10 мин/шов
- Труба: хлыст 6м, закуп = ceil(факт/6)x6
- Расходники: сварочные мин 300р, отрезной/зачистной круг по 1 шт/20 м.п. (мин по 1)

КРЕПЛЕНИЕ: уточни способ если не указан. Самовывоз - метизы монтажные не включаем.

РАБОТА С ПРАЙСОМ:
Прайс имеет формат: Наименование | Толщина | Цена | Ед.изм. | Обработка (станок)
- Колонка "Обработка" показывает на каком станке режется материал - СТРОГО следуй этому
- Ищи каждый материал в прайсе, используй логику (акрил = оргстекло, АКП = композит и т.д.)
- Если нашел - пиши точное название из прайса и используй указанный станок/обработку
- Если не нашел - СТОП, напиши: "В прайсе нет [название]. Возможно: [2-3 варианта из прайса]. Уточни."
- Только по запросу "считай ориентировочно" - ставь ОРИЕНТИРОВОЧНО

ПРОВЕРКА ПЕРЕД ВЫДАЧЕЙ:
1. Пересчитай все суммы
2. Проверь формулу ЦЕНЫ
3. Есть многослойность -> добавлен скотч?
4. Есть оклейка -> добавлены ветошь/спирт/стрейч?
5. Все цены из прайса или помечены ОРИЕНТИРОВОЧНО?

ПРАЙС:
{price_text if price_text else "Прайс не загружен. Цены - ОРИЕНТИРОВОЧНО."}
"""
    prompt = prompt.replace("\u2014", "-").replace("\u2013", "-").replace("\u00ab", "<<").replace("\u00bb", ">>")
    return prompt


# ── Routes ───────────────────────────────────────────────────────────────────

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

    system_prompt = get_system_prompt()
    api_messages = []
    for i, m in enumerate(messages):
        role = m["role"]
        content = m["content"]
        if i == 0 and role == "user":
            content = system_prompt + "\n\n" + content
        api_messages.append({"role": role, "text": content})

    token = AMVERA_TOKEN or ""
    assistant_message = ""

    for _attempt in range(2):
        try:
            body = {"model": MODEL, "messages": api_messages}
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
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
