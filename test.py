import os
import requests
from dotenv import load_dotenv
from google import genai

load_dotenv()

# 1. Probar Gemini
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents="Di solo: 'Conexión con Gemini correcta'."
)
ai_text = response.text.strip()
print(f"Gemini respondió: {ai_text}")

# 2. Probar Telegram
token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")
url = f"https://api.telegram.org/bot{token}/sendMessage"

res = requests.post(url, json={
    "chat_id": chat_id,
    "text": f"🤖 *Prueba de bot exitosa*\n{ai_text}",
    "parse_mode": "Markdown"
})

if res.ok:
    print(" Mensaje recibido en Telegram.")
else:
    print(f" Error en Telegram: {res.text}")