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

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

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
        with open(PRICE_LIST_PATH, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()[:3000]
            # Убираем символы которые ломают JSON
            import re as _re
            price_text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)

    prompt = f"""Ты - Саша, технолог рекламно-производственной компании СБТ. Составляешь сметы себестоимости рекламных конструкций. Общаешься на ты, по-рабочему тепло, иногда шутишь про кофе.

РЕЖИМ: ЖЕСТКОЕ СЛЕДОВАНИЕ ИНСТРУКЦИИ. Запрещено отходить от формата таблиц и формул.

СЧИТАЕШЬ ТОЛЬКО: материалы, ЧПУ (фрезеровка, лазер, пробивка, полимерное покрытие, плазма), ФОТ (ручной труд), аппликацию пленками Oracal.
НЕ СЧИТАЕШЬ: печать УФ/сольвент/полноцвет, ламинацию, монтаж (ставь прочерк).

ФОРМУЛА ЦЕНЫ: ЦЕНА = (Материалы + ФОТ) x 2 + ЧПУ

ОБЯЗАТЕЛЬНЫЙ ФОРМАТ КАЛЬКУЛЯЦИИ:
---
Задание: [как понял задачу]

МАТЕРИАЛЫ
| Наименование | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий (зачем и откуда цена) |
|---|---|---|---|---|---|
ИТОГО Материалы: XXX руб

ЧПУ / Станочные операции
| Операция | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |
|---|---|---|---|---|---|
ИТОГО ЧПУ: YYY руб

ФОТ / Ручные операции (ставка 500 руб/час = 8.33 руб/мин)
| Операция | Норма | Ед.изм. | Минут | Часы | Сумма |
|---|---|---|---|---|---|
Базовый ФОТ: ZZZ руб
Коэффициент масштаба: xN.NN
Наценка непредвиденные (+15% если <10000, +10% если >=10000): +WWW руб
ИТОГО ФОТ (округлено до сотни): VVV руб

ИТОГОВАЯ СЕБЕСТОИМОСТЬ
- Материалы: XXX руб
- ЧПУ: YYY руб
- ФОТ: VVV руб
- ВСЕГО: (XXX + VVV) x 2 + YYY = TTT руб
- Монтаж: -
---

ПРАВИЛА ФОТ:
- Коэффициент масштаба ТОЛЬКО для повторяющихся операций (резка, сварка, зачистка, оклейка):
  до 60 мин = x1.00, 60-120 мин = x0.80, 120-240 мин = x0.70, 240-480 мин = x0.60
- Фикс-операции (подготовка, финальная очистка, упаковка) не масштабируются
- Наценка: ФОТ < 10000 руб = +15%, >= 10000 руб = +10%
- Округление всегда вверх до ближайшей сотни

ПРАВИЛА МАТЕРИАЛОВ:
- Листовые: правило 60% + 10% припуск. Цену пересчитывай из цены за лист в руб/м2
- Пленки Oracal: считать в пог.м (ширина рулона 1.26м или 1.0м), +5% припуск
- Всегда указывай откуда взята цена: из прайса или ориентировочно

РАСХОДНИКИ (добавлять ТОЛЬКО если соответствующая операция есть в заказе):
- ТОЛЬКО при оклейке пленкой (если пленка явно указана в задании!): ветошь (0.5 м2/м2 оклейки, мин 0.2 м2), спирт (0.1 л/м2, мин 0.1 л), стрейч (до 1м2=3 пог.м, 1-3м2=5 пог.м, >3м2=8 пог.м)
- ТОЛЬКО при фрезеровке пластика/АКП: абразив (1 шт/7 м.п., мин 1 шт)
НЕ добавляй расходники если операция не указана в задании!

ЧПУ ТАРИФЫ:
- Фрезеровка: ПВХ 5мм/проход 50 руб, Акрил 4мм/проход 50 руб, АКП 4мм/проход 110 руб
- Лазер: металл 6мм/проход 80 руб, пластик 4мм/проход 60 руб (ПВХ лазером НЕ режем - токсично!)
- Полимерное покрытие: 550 руб/м2 площади развертки металла
- Пробивка оцинковки: 850 руб/час

МЕТАЛЛ/СВАРКА (триггер: слова рама/труба/каркас/сварка):
- Обязательно в ФОТ: разметка+резка трубы (11 мин/рез), сварка (20 мин/шов), зачистка шва (10 мин/шов)
- Труба продается хлыстом 6м, закуп = ceil(факт/6)x6
- Расходники: сварочные материалы мин 300 руб, отрезной круг 1 шт/20 м.п. (мин 1), зачистной круг 1 шт/20 м.п. (мин 1)

КРЕПЛЕНИЕ: всегда уточняй способ крепления если не указан.
- Самовывоз/силами заказчика - метизы для монтажа не включаем
- Наш монтаж - уточнить способ

РАБОТА С ПРАЙСОМ:
Для каждого материала ищи его в прайсе. Прайс может содержать другие названия для одного материала.
Используй логику и здравый смысл при поиске.

Если нашел - указывай точное название позиции из прайса в комментарии и пересчитывай цену в нужные единицы.
Для листовых материалов пересчитывай: цена_листа / (ширина_м x длина_м) = руб/м2.

ВАЖНО: Если не можешь найти материал в прайсе - НЕ бери цену из головы.
Вместо этого ОСТАНОВИ расчет и напиши менеджеру:
"В прайсе не нашел позицию [название]. Возможно ты имел в виду: [перечисли 2-3 самых похожих позиции из прайса с ценами].
Уточни пожалуйста что именно использовать, тогда посчитаю точно."

Только если менеджер явно говорит "считай ориентировочно" - тогда ставь свою оценку с пометкой ОРИЕНТИРОВОЧНО.

ПРАЙС (актуальный, использовать для цен):
{price_text if price_text else "Прайс не загружен. Используй ориентировочные цены с пометкой ОРИЕНТИРОВОЧНО."}
"""

    # Очищаем от символов которые ломают JSON
    prompt = prompt.replace("\u2014", "-").replace("\u2013", "-").replace("\u00ab", "<<").replace("\u00bb", ">>")
    return prompt





@app.get("/api/test-ai")
async def test_ai():
    import os
    token = os.environ.get("AMVERA_TOKEN", "")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            body = {
                "model": "deepseek-R1",
                "messages": [
                    {"role": "user", "text": "Привет!"}
                ]
            }
            r = await client.post(
                "https://kong-proxy.yc.amvera.ru/api/v1/models/deepseek",
                headers={
                    "X-Auth-Token": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json=body
            )
            return {"status": r.status_code, "response": r.text[:500]}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/debug-request")
async def debug_request():
    try:
        with open("/app/data/last_request.txt", "r") as f:
            return {"content": f.read()}
    except:
        return {"content": "No request logged yet"}


@app.get("/api/debug-price")
async def debug_price():
    if os.path.exists(PRICE_LIST_PATH):
        with open(PRICE_LIST_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        # Find ПВХ in the text
        lines = text.split("\n")
        pvh_lines = [l for l in lines if "ПВХ" in l or "пвх" in l.lower()]
        return {
            "total_chars": len(text),
            "total_lines": len(lines),
            "pvh_lines_count": len(pvh_lines),
            "pvh_sample": pvh_lines[:5],
            "first_500": text[:500],
        }
    return {"error": "no pricelist"}

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

    # Собираем сообщения с системным промптом в первом user-сообщении
    sys_prompt = get_system_prompt()
    # Очищаем промпт от символов которые могут сломать JSON
    sys_prompt = sys_prompt.replace(chr(0), "").replace("\x00", "")
    api_messages = []
    for i, m in enumerate(messages):
        text = m["content"]
        if i == 0:
            text = sys_prompt + "\n\n---\n\nЗапрос: " + text
        api_messages.append({"role": m["role"], "text": text})
    
    # Логируем размер для отладки
    import logging
    total_size = sum(len(m["text"]) for m in api_messages)
    logging.info(f"API request size: {total_size} chars")

    # Auto-retry up to 2 times
    for _attempt in range(2):
      try:
        import json as json_lib
        body_dict = {
            "model": MODEL,
            "messages": api_messages
        }
        body_str = json_lib.dumps(body_dict, ensure_ascii=False)
        # Сохраняем последний запрос для отладки
        with open("/app/data/last_request.txt", "w", encoding="utf-8") as dbg:
            dbg.write(f"SIZE: {len(body_str)}\n")
            dbg.write(f"FIRST 2000 CHARS:\n{body_str[:2000]}\n")
            dbg.write(f"LAST 500 CHARS:\n{body_str[-500:]}")
        body_bytes = body_str.encode("utf-8")

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=300.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        ) as client:
            resp = await client.post(
                AMVERA_API_URL,
                headers={
                    "X-Auth-Token": f"Bearer {AMVERA_TOKEN}",
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                },
                content=body_bytes
            )
            body = await resp.aread()
            if resp.status_code != 200:
                assistant_message = f"⚠️ Ошибка API {resp.status_code}: {body.decode('utf-8', errors='ignore')}"
            else:
                import json as json_lib2
                data = json_lib2.loads(body.decode('utf-8'))
                msg = data["choices"][0]["message"]
                raw = msg.get("text") or msg.get("content") or str(msg)
                import re as re_mod
                assistant_message = re_mod.sub(r"<think>.*?</think>", "", raw, flags=re_mod.DOTALL).strip()
      except Exception as e:
        if _attempt == 0:
            import asyncio
            await asyncio.sleep(2)
            continue
        assistant_message = f"⚠️ Ошибка: {str(e)}"
      else:
        break

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
    if file.filename.endswith(".pdf") and False:  # PDF disabled, use CSV/TXT
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    t = page.extract_text(x_tolerance=3, y_tolerance=3)
                    if t:
                        # Fix encoding if needed
                        try:
                            t.encode('utf-8')
                        except Exception:
                            t = t.encode('latin-1').decode('utf-8', errors='ignore')
                        text += t + "\n"
        except Exception as e:
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(io.BytesIO(content))
                for page in reader.pages:
                    t = page.extract_text() or ""
                    text += t + "\n"
            except Exception as e2:
                text = content.decode("utf-8", errors="ignore")
    else:
        # Try UTF-8 first, then cp1251
        try:
            decoded = content.decode("utf-8")
            # Check if it looks like garbled text (contains replacement chars)
            if decoded.count('�') > 10:
                raise UnicodeDecodeError('utf-8', b'', 0, 1, 'too many errors')
            text = decoded
        except Exception:
            try:
                text = content.decode("cp1251")
            except Exception:
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
