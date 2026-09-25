import os
import json
import feedparser
import requests
from dotenv import load_dotenv
from google import genai
from youtube_transcript_api import YouTubeTranscriptApi

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

HISTORY_FILE = "processed_videos.json"

# Tus activos y lista de seguimiento
PORTFOLIO = [
    # Acciones y Criptomonedas
    "Nike (NKE)",
    "Take-Two Interactive (TTWO)",
    "Apple (AAPL)",
    "CorMedix (CRMD)",
    "Advanced Micro Devices (AMD)",
    "Ethereum (ETH)",
    "Amper (AMP)",
    "Bitcoin (BTC)",
    "Tesla (TSLA)",
    "Xiaomi (XIACF)",
    "Wizz Air (WIZZ)",
    "Novavax (NVAX)",
    "Oro (Gold)",
    "Plata (Silver)",
    "Uranio (Uranium)",
    # Fondos Indexados y Carteras
    "iShares MSCI World Small Cap UCITS ETF (IE000ZYRH0Q7)",
    "Pictet China Index (LU0625737910)",
    "iShares Developed World Index Fund (IE000QAZP7L2)",
    "Horos Value Internacional (ES0146309002)",
    "MyInvestor Value C (ES0165243025)",
]

WATCHLIST = [
    "Solana (SOL)",
    "Nextil (NXT)",
    "Mersen (MRN)",
    "International Airlines Group (IAG)",
]

# Canales a monitorizar (formato feed RSS por Channel ID)
# Encuentras el channel_id en el código fuente del canal (UC...)
CHANNELS = [
    {
        "name": "CryptoBruj", 
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=UChYI1ptK3fy06LzLnwsm8pA"
    },
    {
        "name": "La Pizarra de Andrés", 
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=UCDpE0dCtPTJZKAr8psiAHQw"
    },
    {
        "name": "Andrés Directos", 
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=ID_DE_ANDRES_DIRECTOS"
    },
    {
        "name": "Alex Morian", 
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=ID_DE_ALEX_MORIAN"
    }
]


def load_processed_ids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_processed_id(video_id):
    processed = load_processed_ids()
    if video_id not in processed:
        processed.append(video_id)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(processed, f, indent=2)

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    response = requests.post(url, json=payload)
    return response.ok

def get_video_transcript(video_id: str) -> str:
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'en'])
        return " ".join([t['text'] for t in transcript_list])
    except Exception as e:
        print(f"Subtítulos no disponibles para {video_id}: {e}")
        return ""

def analyze_with_gemini(transcript: str, video_title: str) -> str:
    client = genai.Client(api_key=GEMINI_KEY)

    prompt = f"""
    Eres un analista financiero asistente. Analiza la siguiente transcripción del vídeo: "{video_title}".

    Mis activos en cartera: {', '.join(PORTFOLIO)}
    Mi lista de seguimiento: {', '.join(WATCHLIST)}

    Por favor, genera un resumen en Markdown siguiendo esta estructura:
    1. 📌 **Resumen Ejecutivo:** (Máximo 3 viñetas con las tesis principales del mercado).
    2. 🚨 **Activos en mi Cartera/Seguimiento:** Si menciona alguno de mis activos, indica qué postura o datos da el analista (alcista/bajista/neutral). Si no menciona ninguno, pon "Ninguno mencionado".
    3. 💡 **Nuevas Oportunidades o Tickers:** Lista nuevos activos/acciones/fondos recomendados o mencionados con una frase breve del motivo.

    Transcripción:
    {transcript[:20000]}
    """

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt
    )
    return response.text

def main():
    processed_ids = load_processed_ids()

    for channel in CHANNELS:
        feed = feedparser.parse(channel["rss"])
        if not feed.entries:
            continue
        
        # Revisamos los últimos 3 vídeos de cada canal por si subieron varios
        for entry in feed.entries[:3]:
            video_id = entry.yt_videoid
            video_title = entry.title

            if video_id in processed_ids:
                continue

            print(f"Nuevo vídeo encontrado: {video_title} ({video_id})")
            transcript = get_video_transcript(video_id)
            
            if transcript:
                analysis = analyze_with_gemini(transcript, video_title)
                message = f"📹 *{video_title}*\nCanal: *{channel['name']}*\n🔗 https://youtu.be/{video_id}\n\n{analysis}"
                send_telegram_message(message)
                save_processed_id(video_id)
                print("Enviado a Telegram y registrado.")
            else:
                print("Sin transcripción aún. Se intentará en la próxima ejecución.")

if __name__ == "__main__":
    main()
