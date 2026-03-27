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

DB_PATH = "data/db/sbt.db"
PRICE_LIST_PATH = "data/db/pricelist.txt"
# DeepSeek direct API
AMVERA_API_URL = "https://kong-proxy.yc.amvera.ru/api/v1/models/deepseek"
AMVERA_TOKEN = os.getenv("AMVERA_TOKEN")
MODEL = "deepseek-V3"
VALID_USERS = [f"sbt0{i}" for i in range(1, 6)]

def init_db():
    os.makedirs("data/db", exist_ok=True)
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

    # Try file first (persistent volume)
    for path in ["/app/data/system_prompt.txt",
                 _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "system_prompt.txt")]:
        if _os.path.exists(path):
            try:
                for enc in ["utf-8", "cp1251", "latin-1"]:
                    try:
                        txt = open(path, encoding=enc).read().strip()
                        if len(txt) > 500 and "\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433" in txt:
                            print(f"[PROMPT] file {len(txt)} chars enc={enc}", flush=True)
                            base = txt
                            break
                    except Exception:
                        continue
                else:
                    continue
                break
            except Exception as e:
                print(f"[PROMPT] err {e}", flush=True)
    else:
        print("[PROMPT] using embedded", flush=True)
        base = (
            "Ты — технолог ПРОДВИЖЕНИЕ. Составляешь сметы себестоимости. Отвечай ТОЛЬКО в формате таблиц ниже.\n\n"
            "ЗАПРЕЩЕНО: списки вместо таблиц, варианты 1/2/3, расчёт только одного параметра без полной сметы.\n\n"
            "ФОРМАТ ОТВЕТА — СТРОГО:\n\n"
            "Задание: [описание]\n\n"
            "**МАТЕРИАЛЫ**\n"
            "| Наименование | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |\n"
            "|---|---|---|---|---|---|\n"
            "| ПВХ 5 мм | м.кв | 0,12 | 846 | 101,52 | (0,3x0,4)x1,1. Из прайса |\n\n"
            "**ИТОГО Материалы: ХХХ руб**\n\n"
            "**ЧПУ / Станочные операции**\n"
            "| Операция | Ед.изм. | Кол-во | Цена, руб | Сумма, руб | Комментарий |\n"
            "|---|---|---|---|---|---|\n"
            "| Фрезеровка ПВХ 1 проход | м.пог | 1,4 | 40 | 56 | Периметр 1,4м |\n\n"
            "**ИТОГО ЧПУ: ХХХ руб**\n\n"
            "**ФОТ / Ручные операции** (500 руб/час = 8,33 руб/мин)\n"
            "| № | Операция | Норма | Ед.изм. | Минут | Часы | Сумма |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Подготовка рабочего места | фикс 15 мин | заказ | 15 | 0,25 | 125 |\n\n"
            "Базовый ФОТ: ХХХ | Коэф.масштаба: x1,0 | Наценка +15%: +ХХХ\n"
            "**ИТОГО ФОТ: ХХХ руб** (округлить вверх до сотни)\n\n"
            "**ИТОГОВАЯ СЕБЕСТОИМОСТЬ**\n"
            "Материалы: ХХХ | ЧПУ: ХХХ | ФОТ: ХХХ | Монтаж: —\n"
            "**ЦЕНА = (Материалы + ФОТ) x 1,7 + ЧПУ + Печать = ХХХ руб**\n\n"
            "---\n"
            "ПРАВИЛА:\n\n"
            "Если монтаж не указан — спроси: самовывоз или наш монтаж.\n"
            "Цены ВСЕГДА из прайса. Синонимы: акрил=оргстекло, АКП=композит, профтруба=труба.\n"
            "Если позиции нет в прайсе — написать нет в прайсе, уточни.\n\n"
            "МАТЕРИАЛЫ: листовые +10% припуск. Буквы/текст — габарит надписи без припуска.\n"
            "Плёнки Oracal: длина = (площадь x 1,05) / 1,26, округлить вверх до 0,1м. Считать в пог.м.\n"
            "Трубы: L_факт = кол-во x 2 x (W+H). Закуп хлыстами 6м. В материалы — L_закуп.\n\n"
            "ЧПУ только: фрезеровка, лазер, пробивка, полимерное покрытие, плазма.\n"
            "Проходы: ПВХ 5мм/пр, акрил/АКП 4мм/пр, лазер металл 6мм/пр, лазер пластик 4мм/пр.\n"
            "ПВХ лазером нельзя (токсично). Лазер = чистый рез, зачистка и абразив не нужны.\n"
            "Полимерное покрытие — в материалы не дублировать.\n\n"
            "РАСХОДНИКИ при оклейке/печати: ветошь 0,5м²/м², спирт 0,1л/м², стрейч (до1м²=3м.пог, 1-3м²=5, >3м²=8).\n"
            "При фрезеровке пластика/АКП: абразив 1шт/7м.п., мин.1шт.\n"
            "При сварке: сварочные мин.300руб, отрезной+зачистной круги мин.1+1шт, грунт 0,024кг/м.п.\n"
            "Накладные элементы (буквы, карманы): скотч 3М + ФОТ 15мин/шт на крепление.\n\n"
            "ФОТ НОРМЫ (8,33 руб/мин):\n"
            "Подготовка рабочего места: фикс 15мин=125руб\n"
            "Финальная очистка: фикс 10мин=83руб\n"
            "Упаковка в стрейч: фикс 10мин=83руб\n"
            "Оклейка без подворота: 25мин/м²\n"
            "Оклейка с подворотом: 35мин/м²\n"
            "Кантик плёнкой: 10мин/п.м.\n"
            "Зачистка канта после фрезеровки: 4мин/п.м. (не при лазере!)\n"
            "Обезжиривание: 5мин/м²\n"
            "Наклейка букв: 1,5мин/шт\n"
            "Сборка плоского кармана: 10мин/шт\n"
            "Сборка объёмного кармана: 15мин/шт\n"
            "Крепление кармана: 5мин/шт\n"
            "Дистанционные держатели: 10мин/шт\n"
            "Резка трубы: 11мин/рез\n"
            "Сварка: 20мин/шов\n"
            "Зачистка шва: 10мин/шов\n"
            "Маскировка под покраску: 25мин/заказ\n"
            "Снятие после покраски: 20мин/заказ\n\n"
            "Коэф.масштаба (только повторяющиеся операции):\n"
            "<60мин=x1,0; 60-120=x0,8; 120-240=x0,7; 240-480=x0,6\n"
            "Наценка: ФОТ<10000=+15%, >=10000=+10%. Округлить ВВЕРХ до сотни.\n\n"
            "ПЕЧАТЬ (только если запросили): УФ за м² (с белым/без белого), сольвент за м² (720/1440 dpi), ламинация за м².\n"
            "При печати — добавить спирт, ветошь, стрейч.\n\n"
            "ТАБЛИЧКИ: дистанц.держатели+монтаж — Бур 550руб, 4 держателя по углам.\n"
            "СТЕНДЫ: карманы из акрила 2мм или ПЭТ. Фрезеровка — абразив; лазер — без.\n"
            "РАМЫ: точная номенклатура труб из прайса. Открытые срезы — заглушки.\n"
            "МЕТАЛЛОКАРКАС (рама/труба/сварка/каркас): трубы+сварочные+круги в материалы; полимерное покрытие в ЧПУ; резка+сварка+зачистка+маскировка в ФОТ."
        )

    # Append pricelist
    if _os.path.exists(PRICE_LIST_PATH):
        try:
            import json as _json, re as _re
            raw = open(PRICE_LIST_PATH, encoding="utf-8", errors="ignore").read()
            if raw.strip().startswith("{"):
                pdata = _json.loads(raw)
                lines = []
                for group, items in pdata.items():
                    lines.append(f"== {group} ==")
                    for item in items:
                        lines.append(f"{item['num']}. {item['name']} | {item['unit']} | {item['price']} руб.")
                base += "\n\n" + "\n".join(lines)
        except Exception:
            pass

    return base


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
            api_messages = [{"role": "system", "content": system_prompt}]
            for m in messages:
                api_messages.append({"role": m["role"], "content": m["content"]})

            body = {"model": MODEL, "messages": api_messages}
            # Clean payload - remove control chars that break Amvera proxy JSON parsing
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



@app.get("/fix-prompt-encoding")
async def fix_prompt_encoding():
    import os as _os
    path = "/app/data/system_prompt.txt"
    if not _os.path.exists(path):
        return {"error": "file not found"}
    # Read with auto-detection
    raw = open(path, "rb").read()
    for enc in ["utf-8", "cp1251", "latin-1"]:
        try:
            txt = raw.decode(enc)
            if "технолог" in txt and len(txt) > 500:
                # Rewrite as proper UTF-8
                open(path, "w", encoding="utf-8").write(txt)
                return {"ok": True, "encoding_was": enc, "chars": len(txt), "first_100": txt[:100]}
        except Exception:
            continue
    return {"error": "could not detect encoding"}

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
