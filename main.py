from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
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

AMVERA_API_URL = "https://llm.amvera.ru/api/v1/chat/completions"
AMVERA_TOKEN = os.environ.get("AMVERA_TOKEN", "")
MODEL = "deepseek-r1"

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
        with open(PRICE_LIST_PATH, "r", encoding="utf-8") as f:
            price_text = f.read()[:12000]

    return f"""Ты — Саша, технолог рекламно-производственной компании СБТ. Ты составляешь сметы себестоимости рекламных конструкций.

Ты профессионал, но свой в доску — иногда можешь пошутить про кофе, посочувствовать сложному заказу или сказать «ну и денёк» когда расчёт сложный. Общаешься на «ты», по-рабочему тепло.

РЕЖИМ РАБОТЫ: ЖЁСТКОЕ СЛЕДОВАНИЕ ИНСТРУКЦИИ. При расчётах запрещено отходить от формата, указанных формул и порядка действий.

ТВОЯ ЗОНА ОТВЕТСТВЕННОСТИ:
1. Считаешь ТОЛЬКО: материалы, операции ЧПУ (фрезеровка, лазерная резка, пробивка, полимерное покрытие, плазма), ФОТ (ручной труд)
2. НЕ считаешь: печать (УФ, сольвент, полноцвет), ламинацию, монтаж (ставишь прочерк)
3. Аппликацию плёнками Oracal — считаешь

ФОРМУЛА ЦЕНЫ: ЦЕНА = (Материалы + ФОТ) × 2 + ЧПУ

ФОРМАТ КАЛЬКУЛЯЦИИ (ОБЯЗАТЕЛЕН):

---
**Задание:** [как понял задачу]

**МАТЕРИАЛЫ**
| Наименование | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |
|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... |
**ИТОГО Материалы: XXX ₽**

**ЧПУ / Станочные операции**
| Операция | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |
|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... |
**ИТОГО ЧПУ: YYY ₽**

**ФОТ / Ручные операции**
| Операция | Норма | Ед.изм. | Минут | Часы | Сумма |
|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... |
Базовый ФОТ: ZZZ ₽
Коэффициент масштаба: ×X.XX
Наценка непредвиденные (15% или 10%): +WWW ₽
**ИТОГО ФОТ: VVV ₽**

**ИТОГОВАЯ СЕБЕСТОИМОСТЬ**
- Материалы: XXX ₽
- ЧПУ: YYY ₽
- ФОТ: VVV ₽
- **ВСЕГО: (XXX + VVV) × 2 + YYY = TTT ₽**
- Монтаж: —
---

ПРАВИЛА ФОТ:
- Ставка: 500 ₽/час (8,33 ₽/мин)
- Коэффициент масштаба применяется ТОЛЬКО к повторяющимся операциям (резка, сварка, зачистка, оклейка):
  до 60 мин → ×1.00 | 60–120 мин → ×0.80 | 120–240 мин → ×0.70 | 240–480 мин → ×0.60
- Фикс-операции (подготовка, финальная очистка, упаковка, подвес/снятие) — не масштабируются
- Наценка: ФОТ < 10 000 ₽ → +15%, ФОТ ≥ 10 000 ₽ → +10%
- Округление: всегда вверх до ближайшей сотни

ПРАВИЛА МАТЕРИАЛОВ:
- Листовые: применяй правило 60% + 10% припуск
- Плёнки Oracal: считать в пог.м (ширина рулона 1,26 м или 1,00 м), +5% припуск
- Цену ВСЕГДА брать из прайса или помечать «ориентировочно»
- При листовых материалах — пересчитывать цену из «за шт» в «за м²»

РАСХОДНИКИ (всегда добавлять):
- При оклейке плёнкой: ветошь (0,5 м²/м² оклейки, мин 0,2 м²), спирт (0,1 л/м², мин 0,1 л), стрейч-плёнка
- При фрезеровке пластика/АКП: абразив (1 шт/7 м.п., мин 1 шт)
- Стрейч: ≤1 м² → 3 м.п. | 1–3 м² → 5 м.п. | >3 м² → 8 м.п.

ЧПУ ТАРИФЫ:
- Фрезеровка: ПВХ 5мм/проход 50₽, Акрил 4мм/проход 50₽, АКП 4мм/проход 110₽
- Лазер: металл 6мм/проход 80₽, пластик 4мм/проход 60₽ (ПВХ лазером НЕ режем — токсично!)
- Полимерное покрытие: 550 ₽/м² развёртки металла
- Пробивка оцинковки: 850 ₽/час

МЕТАЛЛ/СВАРКА (триггер: слова «рама», «труба», «каркас», «сварка»):
- Обязательно: разметка+резка трубы (11 мин/рез), сварка (20 мин/шов), зачистка шва (10 мин/шов)
- Труба продаётся хлыстом 6 м, закуп = ceil(факт/6)×6
- Сварочные материалы: мин 300 ₽; отрезной круг: 1 шт/20 м.п. (мин 1); зачистной круг: 1 шт/20 м.п. (мин 1)

ПРАЙС (актуальный):
{price_text if price_text else "⚠️ Прайс не загружен. Используй ориентировочные цены с пометкой «ориентировочно»."}

Если чего-то не хватает для расчёта — спроси менеджера. Всегда уточняй способ крепления если не указан."""


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    user_id = body.get("user_id", "").strip().lower()
    if user_id not in VALID_USERS:
        raise HTTPException(status_code=401, detail="Неверный логин. Доступны sbt01–sbt05")
    return {"ok": True, "user_id": user_id}

@app.get("/api/calculations/{user_id}")
async def get_calculations(user_id: str):
    if user_id not in VALID_USERS:
        raise HTTPException(status_code=401)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, created_at, updated_at FROM calculations WHERE user_id=? ORDER BY updated_at DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]} for r in rows]

@app.get("/api/calculation/{calc_id}")
async def get_calculation(calc_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM calculations WHERE id=?", (calc_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404)
    return {"id": row[0], "user_id": row[1], "title": row[2],
            "messages": json.loads(row[3]), "result": row[4],
            "created_at": row[5], "updated_at": row[6]}

@app.post("/api/calculation/new")
async def new_calculation(request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    if user_id not in VALID_USERS:
        raise HTTPException(status_code=401)
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO calculations (user_id, title, messages, result, created_at, updated_at) VALUES (?,?,?,?,?,?)",
              (user_id, "Новый расчёт", "[]", None, now, now))
    calc_id = c.lastrowid
    conn.commit()
    conn.close()
    return {"id": calc_id}

@app.post("/api/chat/{calc_id}")
async def chat(calc_id: int, request: Request):
    body = await request.json()
    user_message = body.get("message", "")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT messages, title FROM calculations WHERE id=?", (calc_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404)

    messages = json.loads(row[0])
    title = row[1]

    messages.append({"role": "user", "content": user_message})

    # Update title from first message
    if len(messages) == 1:
        title = user_message[:60] + ("..." if len(user_message) > 60 else "")

    api_messages = [{"role": "system", "content": get_system_prompt()}] + messages

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                AMVERA_API_URL,
                headers={
                    "X-Auth-Token": f"Bearer {AMVERA_TOKEN}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": MODEL,
                    "messages": api_messages,
                    "max_tokens": 4000,
                    "temperature": 0.3
                }
            )
            resp.raise_for_status()
            data = resp.json()
            assistant_message = data["choices"][0]["message"]["content"]
    except Exception as e:
        assistant_message = f"⚠️ Ошибка подключения к ИИ: {str(e)}\n\nПроверь токен Amvera в переменных окружения."

    messages.append({"role": "assistant", "content": assistant_message})

    now = datetime.now().isoformat()
    c.execute("UPDATE calculations SET messages=?, title=?, updated_at=? WHERE id=?",
              (json.dumps(messages, ensure_ascii=False), title, now, calc_id))
    conn.commit()
    conn.close()

    return {"reply": assistant_message, "title": title}

@app.delete("/api/calculation/{calc_id}")
async def delete_calculation(calc_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM calculations WHERE id=?", (calc_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/api/upload-pricelist")
async def upload_pricelist(file: UploadFile = File(...)):
    content = await file.read()
    # Try to extract text from PDF using pdfplumber if available
    text = ""
    if file.filename.endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        text += t + "\n"
        except Exception:
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(io.BytesIO(content))
                for page in reader.pages:
                    text += page.extract_text() + "\n"
            except Exception as e:
                text = content.decode("utf-8", errors="ignore")
    else:
        text = content.decode("utf-8", errors="ignore")

    os.makedirs("data", exist_ok=True)
    with open(PRICE_LIST_PATH, "w", encoding="utf-8") as f:
        f.write(text)

    return {"ok": True, "chars": len(text), "preview": text[:200]}

@app.get("/api/pricelist-status")
async def pricelist_status():
    if os.path.exists(PRICE_LIST_PATH):
        size = os.path.getsize(PRICE_LIST_PATH)
        return {"loaded": True, "size": size}
    return {"loaded": False}

# Export endpoints
@app.get("/api/export/txt/{calc_id}")
async def export_txt(calc_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT title, messages FROM calculations WHERE id=?", (calc_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404)

    title, messages_json = row
    messages = json.loads(messages_json)

    text = f"КАЛЬКУЛЯЦИЯ: {title}\n"
    text += f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
    text += "=" * 60 + "\n\n"

    for msg in messages:
        role = "Менеджер" if msg["role"] == "user" else "Технолог (ИИ)"
        text += f"[{role}]:\n{msg['content']}\n\n"

    from fastapi.responses import Response
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=calculation_{calc_id}.txt"}
    )

@app.get("/api/export/excel/{calc_id}")
async def export_excel(calc_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT title, messages FROM calculations WHERE id=?", (calc_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404)

    title, messages_json = row
    messages = json.loads(messages_json)

    # Find last assistant message with the calculation
    last_calc = ""
    for msg in reversed(messages):
        if msg["role"] == "assistant" and "ИТОГО" in msg["content"]:
            last_calc = msg["content"]
            break

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Калькуляция"

        # Header
        ws.merge_cells("A1:F1")
        ws["A1"] = f"КАЛЬКУЛЯЦИЯ: {title}"
        ws["A1"].font = Font(bold=True, size=14)
        ws["A1"].alignment = Alignment(horizontal="center")

        ws.merge_cells("A2:F2")
        ws["A2"] = f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        ws["A2"].alignment = Alignment(horizontal="center")

        ws.append([])

        # Conversation
        row_num = 4
        for msg in messages:
            role = "Менеджер" if msg["role"] == "user" else "Технолог (ИИ-Саша)"
            ws.merge_cells(f"A{row_num}:F{row_num}")
            ws[f"A{row_num}"] = f"[{role}]"
            ws[f"A{row_num}"].font = Font(bold=True, color="FF6B35" if msg["role"] == "assistant" else "2C3E50")
            row_num += 1

            ws.merge_cells(f"A{row_num}:F{row_num + 5}")
            cell = ws[f"A{row_num}"]
            cell.value = msg["content"]
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row_num].height = 200
            row_num += 7

        ws.column_dimensions["A"].width = 30
        for col in ["B", "C", "D", "E", "F"]:
            ws.column_dimensions[col].width = 15

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        from fastapi.responses import Response
        return Response(
            content=output.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=calculation_{calc_id}.xlsx"}
        )
    except ImportError:
        # Fallback to CSV
        return await export_txt(calc_id)
