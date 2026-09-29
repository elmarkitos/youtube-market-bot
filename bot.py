import calendar
import io
import json
import os
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

# Trabajar siempre desde la carpeta del script (importante para el Programador de tareas)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

HISTORY_FILE = "processed_videos.json"

# Modelos en orden de preferencia (si uno está saturado, pasa al siguiente)
MODELS = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"]

MAX_VIDEO_AGE_HOURS = 36   # solo vídeos publicados en las últimas 36 h
LOW_RES = True             # resolución baja = menos tokens (vale para vídeos donde se habla; ponlo en False si se apoyan mucho en gráficos)

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
# Gemini (con reintentos y modelo de respaldo)
# ---------------------------------------------------------------------------
class SkipVideo(Exception):
    """Error permanente con este vídeo (403/400): no merece la pena reintentar."""


def resolve_models():
    """Descarta de MODELS los nombres que no existen para tu API key."""
    global MODELS
    try:
        available = {m.name.replace("models/", "") for m in client.models.list()}
    except Exception as e:
        print(f"No pude listar los modelos: {e}")
        return
    valid = [m for m in MODELS if m in available]
    if len(valid) < len(MODELS):
        print("Modelos descartados (no existen para tu clave):",
              [m for m in MODELS if m not in valid])
        print("Modelos 'flash' disponibles:", sorted(n for n in available if "flash" in n))
    if valid:
        MODELS = valid


def ask_gemini(contents, retries_per_model: int = 4) -> str:
    config = None
    if LOW_RES:
        config = types.GenerateContentConfig(
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW
        )

    for model in MODELS:
        for attempt in range(retries_per_model):
            try:
                response = client.models.generate_content(
                    model=model, contents=contents, config=config
                )
                if response.text:
                    return response.text
                print(f"{model}: respuesta vacía")
                break
            except errors.ServerError as e:  # 5xx: saturación
                wait = 20 * (attempt + 1)
                print(f"{model}: {e.code} (intento {attempt + 1}), espero {wait}s...")
                time.sleep(wait)
            except errors.ClientError as e:  # 4xx
                detail = str(getattr(e, "message", e))[:200]
                if e.code == 429:  # cuota/límite por minuto
                    print(f"{model}: 429, espero 60s...")
                    time.sleep(60)
                elif e.code in (400, 403):  # vídeo inaccesible, privado, demasiado largo...
                    raise SkipVideo(f"{e.code}: {detail}")
                else:
                    print(f"{model}: error {e.code} ({detail}), paso al siguiente modelo")
                    break
    raise RuntimeError("Ningún modelo de Gemini respondió")


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
    except SkipVideo as e:
        print(f"  ⚠ Vídeo omitido ({e}). ¿Es un directo, privado o solo para miembros?")
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
    print("  ✓ Enviado")
    return "ok"


def main():
    print(f"=== Ejecución {datetime.now():%Y-%m-%d %H:%M} ===")
    resolve_models()
    history = load_history()
    new_items = []
    pending = []

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

    for video in pending:
        print(f"✗ No se pudo procesar: {video['channel']} - {video['title']}")

    # Resumen global comparativo (solo si hay 2 o más vídeos nuevos)
    if len(new_items) >= 2:
        print("Generando análisis global...")
        try:
            report = global_analysis(new_items)
            send_telegram(f"📊 *ANÁLISIS GLOBAL DEL DÍA*\n\n{report}")
        except Exception as e:
            print(f"✗ Error en el análisis global: {e}")
    elif not new_items:
        print("Nada nuevo hoy.")


if __name__ == "__main__":
    main()