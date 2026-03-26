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
        try:
            with open(PRICE_LIST_PATH, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()[:4000]
        except Exception:
            raw = ""
        import re as _re
        # Убираем управляющие символы
        price_text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)
        # Убираем лишние кавычки которые ломают JSON
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
- Если изделие многослойное (буквы на основе, карман на стенде, элемент на элементе) - ВСЕГДА добавляй двусторонний скотч 3М для крепления слоев между собой.
- Количество: периметр элемента x 2 пог.м (полосы по краям), минимум 0.5 м.
- Найди в прайсе: "скотч 2-х стор.3М" - цена в м.
- В ФОТ добавь операцию "Крепление накладного элемента" - 15 мин/шт.

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
- Ищи каждый материал в прайсе, используй логику (акрил = оргстекло, АКП = композит, профтруба = труба)
- Если нашел - пиши точное название из прайса
- Если не нашел - СТОП, напиши: "В прайсе нет [название]. Возможно: [2-3 варианта из прайса с ценами]. Уточни."
- Только по запросу менеджера "считай ориентировочно" - ставь ОРИЕНТИРОВОЧНО

ПРОВЕРКА ПЕРЕД ВЫДАЧЕЙ:
1. Пересчитай все суммы в таблицах
2. Проверь итоговую формулу ЦЕНЫ
3. Проверь: есть ли многослойность -> добавлен ли скотч?
4. Проверь: есть ли оклейка -> добавлены ли ветошь/спирт/стрейч?
5. Проверь: все цены из прайса или помечены ОРИЕНТИРОВОЧНО?

ПРАЙС:
{price_text if price_text else "Прайс не загружен. Цены - ОРИЕНТИРОВОЧНО."}
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
