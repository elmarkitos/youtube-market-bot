import os
import feedparser
import requests
from dotenv import load_dotenv
from google import genai
from youtube_transcript_api import YouTubeTranscriptApi

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# Tus activos y lista de seguimiento
PORTFOLIO = [
    # Acciones y Criptomonedas
    "Nike (NKE)",
    "Take-Two Interactive (TTWO)",
    "Apple (AAPL)",
    "CorMedix (CRMD)",
    "Advanced Micro Devices (AMD)",
    "Ethereum (ETH)",
    "Amp (AMP)",
    "Bitcoin (BTC)",
    "Tesla (TSLA)",
    "Xiaomi (XIACF)",
    "Wizz Air (WIZZ)",
    "Novavax (NVAX)",
    # Fondos Indexados y Carteras
    "iShares MSCI World Small Cap UCITS ETF (IE000ZYRH0Q7)",
    "Nordea 1 - Global Stable Equity Fund (LU0625737910)",
    "iShares Developed World Index Fund (IE000QAZP7L2)",
    "CaixaBank Monetario Rendimiento (ES0146309002)",
    "MyInvestor Cartera Indexada / Baelo (ES0165243025)",
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
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=ID_DE_CRYPTOBRUJ"
    },
    {
        "name": "La Pizarra de Andrés", 
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=ID_DE_ANDRES"
    },
    {
        "name": "Andrés Directos", 
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=ID_DE_ANDRES_DIRECTOS"
    },
    {
        "name": "DoctorCrypto", 
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbVIsFH23kSc_K4qRN3NIsw"
    },
    {
        "name": "Alex Morian", 
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=ID_DE_ALEX_MORIAN"
    }
]

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    return response.ok

def get_video_transcript(video_id: str) -> str:
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'en'])
        return " ".join([t['text'] for t in transcript_list])
    except Exception as e:
        print(f"Error obteniendo subtítulos de {video_id}: {e}")
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
    3. 💡 **Nuevas Oportunidades o Tickers:** Lista nuevos activos/acciones/fondos mencionados que no estén en mi lista, con una frase breve del motivo por el que se mencionan.

    Transcripción:
    {transcript[:15000]} # Limitamos caracteres si es muy largo
    """

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    return response.text

def main():
    # Prueba con un vídeo puntual o recorre el feed
    for channel in CHANNELS:
        feed = feedparser.parse(channel["rss"])
        if not feed.entries:
            continue
        
        latest_video = feed.entries[0]
        video_id = latest_video.yt_videoid
        video_title = latest_video.title

        print(f"Procesando: {video_title} ({video_id})")
        transcript = get_video_transcript(video_id)
        
        if transcript:
            analysis = analyze_with_gemini(transcript, video_title)
            message = f"📹 *{video_title}*\nCanal: {channel['name']}\n\n{analysis}"
            send_telegram_message(message)
            print("Mensaje enviado a Telegram.")
        else:
            print("No se encontraron subtítulos disponibles para este vídeo.")

if __name__ == "__main__":
    main()
