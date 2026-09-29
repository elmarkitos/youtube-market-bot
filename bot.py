import calendar
import io
import logging
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import feedparser
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

# ---------------------------------------------------------------------------
# Configuración general
# ---------------------------------------------------------------------------
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Silencia el aviso informativo de "automatic function calling" del SDK
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

# Trabajar siempre desde la carpeta del script (importante para el Programador de tareas)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

HISTORY_FILE = "processed_videos.json"

# Modelos en orden de preferencia. Si uno agota su cuota o está saturado, pasa al siguiente.
# La cuota gratuita es POR MODELO, así que tener 2-3 modelos válidos multiplica el margen.
# Los modelos que devuelvan 404 (no existen o no están disponibles para tu cuenta) se descartan solos.
MODELS = [
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

MAX_VIDEO_AGE_HOURS = 36   # solo vídeos publicados en las últimas 36 h
LOW_RES = True             # resolución baja = menos tokens (~100 tokens/s de vídeo en vez de ~300)

client = genai.Client(api_key=GEMINI_KEY)

PORTFOLIO_FILE = os.path.join(BASE_DIR, "portfolio.json")

def load_portfolio_data():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("portfolio", []), data.get("watchlist", [])
        except Exception as e:
            print(f"Error leyendo portfolio.json: {e}")
    return [], []

PORTFOLIO, WATCHLIST = load_portfolio_data()

# ---------------------------------------------------------------------------
# Canales (RSS por channel_id). Añade o quita los que quieras.
# ---------------------------------------------------------------------------
def rss(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

CHANNELS_FILE = os.path.join(BASE_DIR, "channels.json")

def load_channels():
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [
                    {
                        "name": ch["name"],
                        "rss": f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['channel_id']}"
                    }
                    for ch in data
                ]
        except Exception as e:
            print(f"Error leyendo channels.json: {e}")
    return []

CHANNELS = load_channels()

# ---------------------------------------------------------------------------
# Historial (para no repetir vídeos)
# ---------------------------------------------------------------------------
def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):  # formato antiguo: lista de IDs
                return {vid: {} for vid in data}
            return data
        except Exception:
            return {}
    return {}


def save_history(history: dict):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def split_text(text: str, limit: int = 4000) -> list:
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current.strip():
        chunks.append(current)
    return chunks


def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    text = text.replace("**", "*")  # Telegram (Markdown clásico) usa un solo asterisco

    for chunk in split_text(text):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        res = requests.post(url, json=payload, timeout=30)
        if not res.ok:  # si falla el Markdown, reenviar como texto plano
            payload.pop("parse_mode")
            res = requests.post(url, json=payload, timeout=30)
        if not res.ok:
            print(f"Error enviando a Telegram: {res.text}")
            return False
        time.sleep(1)
    return True


# ---------------------------------------------------------------------------
# Gemini (reintentos, control de cuota y modelos de respaldo)
# ---------------------------------------------------------------------------
EXHAUSTED_MODELS = set()  # modelos sin cuota diaria durante esta ejecución
DEAD_MODELS = set()       # modelos que dan 404 (no disponibles para tu cuenta)


class SkipVideo(Exception):
    """Error permanente con este vídeo (403/400): no merece la pena reintentar."""


class QuotaExhausted(Exception):
    """Todos los modelos han agotado su cuota."""


def is_daily_quota(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in ("perday", "per day", "daily", "hours of youtube"))


def looks_like_quota(text: str) -> bool:
    t = text.lower()
    return is_daily_quota(text) or any(k in t for k in ("quota", "resource_exhausted", "rate limit"))


def retry_delay(text: str, default: float = 60, cap: float = 120) -> float:
    m = (re.search(r'retryDelay["\']?:\s*["\']?(\d+(?:\.\d+)?)s', text)
         or re.search(r"retry in (\d+(?:\.\d+)?)s", text, re.I))
    return min(float(m.group(1)) + 2, cap) if m else default


def ask_gemini(contents, retries_per_model: int = 4) -> str:
    config = None
    if LOW_RES:
        config = types.GenerateContentConfig(
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW
        )

    for idx, model in enumerate(MODELS):
        if model in EXHAUSTED_MODELS or model in DEAD_MODELS:
            continue
        later = [m for m in MODELS[idx + 1:] if m not in EXHAUSTED_MODELS and m not in DEAD_MODELS]
        if model != MODELS[0]:
            print(f"   ➡ Probando con el modelo de respaldo: {model}")

        for attempt in range(retries_per_model):
            try:
                response = client.models.generate_content(
                    model=model, contents=contents, config=config
                )
                if response.text:
                    print(f"   ✓ Respondido por {model}")
                    return response.text
                print(f"   {model}: respuesta vacía")
                break
            except errors.ServerError as e:  # 5xx: saturación temporal
                if attempt >= 1 and later:  # ya lo he intentado 2 veces y hay respaldo
                    print(f"   ⏳ {model}: {e.code} saturado. Cambio de modelo.")
                    break
                wait = 20 * (attempt + 1)
                print(f"   ⏳ {model}: {e.code} saturado (intento {attempt + 1}), espero {wait}s...")
                time.sleep(wait)
            except errors.ClientError as e:  # 4xx
                text = str(e)
                quota_error = e.code == 429 or (e.code in (400, 403) and looks_like_quota(text))

                if quota_error and (is_daily_quota(text) or e.code != 429):
                    print(f"   ⛔ {model}: cuota DIARIA agotada ({e.code}). Paso al siguiente modelo.")
                    print(f"      Detalle: {text[:250]}")
                    EXHAUSTED_MODELS.add(model)
                    break
                elif quota_error:  # 429 por minuto: esperar lo que indique Google
                    wait = retry_delay(text)
                    print(f"   ⏳ {model}: límite por minuto (429), espero {wait:.0f}s "
                          f"(intento {attempt + 1}/{retries_per_model})...")
                    time.sleep(wait)
                elif e.code in (400, 403):  # vídeo inaccesible, privado, demasiado largo...
                    raise SkipVideo(f"{e.code}: {text[:200]}")
                elif e.code == 404:  # modelo no disponible para tu cuenta: no volver a intentarlo
                    print(f"   ✗ {model}: no disponible (404). Lo descarto para esta ejecución.")
                    DEAD_MODELS.add(model)
                    break
                else:
                    print(f"   {model}: error {e.code} ({text[:150]}), paso al siguiente modelo")
                    break

    usable = [m for m in MODELS if m not in DEAD_MODELS]
    if not usable:
        raise RuntimeError("Ningún modelo de MODELS está disponible para tu cuenta (404)")
    if all(m in EXHAUSTED_MODELS for m in usable):
        raise QuotaExhausted("Todos los modelos disponibles han agotado su cuota")
    raise RuntimeError("Ningún modelo de Gemini respondió (saturación temporal)")


def summarize_video(video_url: str, title: str, channel: str) -> str:
    prompt = f"""Eres un analista financiero. Analiza este vídeo del canal "{channel}" titulado "{title}".

Mi cartera: {', '.join(PORTFOLIO)}
Mi lista de seguimiento: {', '.join(WATCHLIST)}

Responde en español, conciso (máximo 1500 caracteres), con esta estructura exacta:

📌 *Resumen:* 3 viñetas máximo con las tesis principales del mercado.
🧭 *Sesgo general:* alcista / bajista / neutral / mixto, en una frase.
🚨 *Mi cartera y seguimiento:* SOLO los activos de mi lista que se mencionen, con la postura del analista (alcista/bajista/neutral) y el motivo o nivel clave si lo da. Si no menciona ninguno, escribe "Ninguno mencionado".
💡 *Otros activos mencionados:* tickers/activos que no están en mis listas y que recomienda o destaca, con una frase de motivo. Si no hay, escribe "Ninguno".

Si el vídeo no trata de mercados o inversión, dilo en una sola línea y no rellenes el resto.
Usa *un solo asterisco* para negrita. No inventes datos que no se digan en el vídeo."""

    contents = types.Content(
        parts=[
            types.Part(file_data=types.FileData(file_uri=video_url)),
            types.Part(text=prompt),
        ]
    )
    return ask_gemini(contents)


def global_analysis(items: list) -> str:
    bloques = "\n\n".join(
        f"=== {it['channel']} — {it['title']} ===\n{it['summary']}" for it in items
    )
    prompt = f"""Eres un analista financiero. Estos son los resúmenes de los últimos vídeos de varios canales de finanzas.

Mi cartera: {', '.join(PORTFOLIO)}
Mi lista de seguimiento: {', '.join(WATCHLIST)}

{bloques}

Genera un informe comparativo en español (máximo 2500 caracteres) con esta estructura:

🌍 *Panorama general:* 2-3 líneas sobre el sentimiento de mercado predominante hoy.
🤝 *Coincidencias:* temas o activos en los que varios canales opinan parecido (indica qué canales).
⚔️ *Discrepancias:* temas o activos donde los canales se contradicen (indica quién dice qué).
🚨 *Alertas para mi cartera/seguimiento:* activos míos mencionados por algún canal, con la postura de cada uno. Destaca primero los que tengan opiniones enfrentadas. Si no hay ninguno, dilo.
💡 *Ideas nuevas:* activos fuera de mis listas que aparezcan en más de un canal, o que destaquen.

Basa todo SOLO en los resúmenes anteriores. No añadas datos externos ni consejos de compra propios.
Usa *un solo asterisco* para negrita."""
    return ask_gemini(prompt)


# ---------------------------------------------------------------------------
# RSS: último vídeo válido de cada canal
# ---------------------------------------------------------------------------
def latest_video(channel: dict):
    feed = feedparser.parse(channel["rss"])
    if not feed.entries:
        print(f"[{channel['name']}] feed vacío o inaccesible")
        return None

    now = time.time()
    for entry in feed.entries[:5]:
        link = entry.get("link", "")
        if "/shorts/" in link:
            continue
        published = calendar.timegm(entry.published_parsed)
        age_hours = (now - published) / 3600
        if age_hours > MAX_VIDEO_AGE_HOURS:
            return None  # los siguientes son aún más antiguos
        return {
            "id": entry.yt_videoid,
            "title": entry.title,
            "url": f"https://www.youtube.com/watch?v={entry.yt_videoid}",
            "channel": channel["name"],
        }
    return None


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------
def process_video(video: dict, history: dict, new_items: list) -> str:
    """Devuelve 'ok', 'retry' (fallo temporal) o 'skip' (fallo permanente)."""
    try:
        summary = summarize_video(video["url"], video["title"], video["channel"])
    except QuotaExhausted:
        raise  # se gestiona en main()
    except SkipVideo as e:
        print(f"  ⚠ Vídeo omitido ({e}). ¿Es un directo, privado, no listado o solo para miembros?")
        return "skip"
    except Exception as e:
        print(f"  ✗ Error con Gemini: {e}")
        return "retry"

    message = (
        f"📹 *{video['title']}*\n"
        f"Canal: *{video['channel']}*\n"
        f"🔗 {video['url']}\n\n{summary}"
    )
    if not send_telegram(message):
        return "retry"

    video["summary"] = summary
    new_items.append(video)
    history[video["id"]] = {
        "channel": video["channel"],
        "title": video["title"],
        "date": datetime.now(timezone.utc).isoformat(),
    }
    save_history(history)
    print("  ✓ Enviado a Telegram")
    return "ok"


def main():
    print(f"=== Ejecución {datetime.now():%Y-%m-%d %H:%M} ===")
    print("Modelos (por orden de preferencia):", MODELS)
    history = load_history()
    new_items = []
    pending = []
    quota_out = False

    for channel in CHANNELS:
        video = latest_video(channel)
        if not video:
            print(f"[{channel['name']}] sin vídeo nuevo reciente")
            continue
        if video["id"] in history:
            print(f"[{channel['name']}] ya procesado: {video['title']}")
            continue

        pending.append(video)

    # Dos pasadas: si algo falla por saturación de Gemini, se reintenta a los 5 minutos
    try:
        for round_number in (1, 2):
            failed = []
            for video in pending:
                print(f"[{video['channel']}] procesando: {video['title']}")
                if process_video(video, history, new_items) == "retry":
                    failed.append(video)
                time.sleep(3)
            pending = failed
            if not pending:
                break
            if round_number == 1:
                print(f"{len(pending)} vídeo(s) fallidos. Reintento en 5 minutos...")
                time.sleep(300)
    except QuotaExhausted:
        quota_out = True
        print("⛔ Todos los modelos han agotado su cuota. Se detiene la ejecución.")

    remaining = [v for v in pending if v["id"] not in history]
    for video in remaining:
        print(f"✗ Sin procesar: {video['channel']} - {video['title']}")

    if quota_out and remaining:
        lista = "\n".join(f"• {v['channel']}: {v['title']}" for v in remaining)
        send_telegram(f"⚠️ Cuota de Gemini agotada. Vídeos sin resumir:\n{lista}")

    # Resumen global comparativo (solo si hay 2 o más vídeos nuevos)
    if len(new_items) >= 2 and not quota_out:
        print("Generando análisis global...")
        try:
            report = global_analysis(new_items)
            send_telegram(f"📊 *ANÁLISIS GLOBAL DEL DÍA*\n\n{report}")
        except Exception as e:
            print(f"✗ Error en el análisis global: {e}")
    elif not new_items and not remaining:
        print("Nada nuevo hoy.")

    if EXHAUSTED_MODELS:
        print("Modelos sin cuota en esta ejecución:", sorted(EXHAUSTED_MODELS))


if __name__ == "__main__":
    main()