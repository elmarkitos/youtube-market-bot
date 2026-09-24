# YouTube Market Bot 📈🤖

Bot automatizado en Python diseñado para ejecutarse 24/7 (en Raspberry Pi, Mac o PC). Monitoriza canales de YouTube de análisis de mercados e inversiones mediante feeds RSS, procesa las transcripciones con la IA de Google Gemini y envía alertas a Telegram destacando activos en cartera y nuevas oportunidades.

---

## 🚀 Características

- **Monitorización pasiva:** Consulta canales de YouTube vía RSS sin agotar cuotas de API de Google.
- **Detección de cartera y seguimiento:** Compara el contenido del vídeo contra tu lista personalizada de acciones, fondos y criptomonedas.
- **Análisis con IA:** Utiliza Google Gemini para extraer resúmenes ejecutivos, posturas del analista y nuevas tesis de inversión.
- **Historial antifiltro:** Registro en JSON para evitar notificaciones duplicadas de vídeos ya analizados.
- **Alertas en Telegram:** Notificaciones directas a tu móvil con formato Markdown.

---

## 🛠️ Requisitos Previos

- Python 3.10 o superior
- Un bot de Telegram (token obtenido de [@BotFather](https://t.me/BotFather))
- Tu ID personal de Telegram (obtenido mediante bots como `@userinfobot`)
- Una clave de API de [Google AI Studio](https://aistudio.google.com/)

---
