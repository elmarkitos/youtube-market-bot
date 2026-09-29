# YouTube Market Bot 📈🤖

Bot automatizado en Python diseñado para ejecutarse 24/7 (en Raspberry Pi, Mac o PC). Monitoriza canales de YouTube de análisis de mercados e inversiones mediante feeds RSS, procesa directamente el contenido audiovisual con los modelos multimodales de Google Gemini y envía alertas detalladas a Telegram contrastando la información con tu cartera personal y lista de seguimiento.

---

## 🚀 Características

- **Monitorización pasiva vía RSS:** Detecta publicaciones recientes sin consumir cuota de la YouTube Data API.
- **Análisis audiovisual nativo:** Envía la URL directa a Google Gemini (`types.FileData`) sin depender de transcripciones locales, extensiones de navegador ni herramientas de scraping propensas a bloqueos (código 429).
- **Gestión de cartera desacoplada:** Lectura de activos desde `portfolio.json`, manteniendo tus inversiones fuera del repositorio público de Git.
- **Sistema de fallback y reintentos:** Rotación automática entre modelos (`gemini-3.6-flash`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`) y tiempos de espera exponenciales ante picos de demanda (errores 5xx) o límites por minuto (429).
- **Consumo optimizado de tokens:** Opción `LOW_RES` (`MEDIA_RESOLUTION_LOW`) para procesar el material hablado minimizando el coste de tokens.
- **Filtro inteligente de contenido:** Omite Shorts (`/shorts/`) y vídeos con antigüedad superior a 36 horas.
- **Historial persistente:** Control en `processed_videos.json` para evitar análisis duplicados.
- **Informe global diario:** Genera una síntesis comparativa cruzada cuando se procesan 2 o más vídeos en una misma tanda (consensos, discrepancias y alertas agregadas).
- **Alertas en Telegram:** Envío con formato limpio y mecanismo de respaldo a texto plano si falla la sintaxis de Markdown.

---

## 🛠️ Requisitos Previos

- Python 3.10 o superior
- Bot de Telegram y token (obtenido mediante [@BotFather](https://t.me/BotFather))
- Tu ID personal de Telegram (obtenido con [@userinfobot](https://t.me/userinfobot))
- Clave de API de [Google AI Studio](https://aistudio.google.com/)

---

## 📂 Estructura del Proyecto

```text
youtube-market-bot/
├── bot.py
├── requirements.txt
├── .env                       # Variables de entorno (ignorado por Git)
├── portfolio.json             # Tus activos privados (ignorado por Git)
├── portfolio.example.json     # Plantilla pública de ejemplo
├── processed_videos.json      # Registro de vídeos procesados (ignorado por Git)
└── README.md