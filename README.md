<div align="center">

# 📱 DropInvoice

**WhatsApp-native GST invoicing for India's informal economy**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Twilio](https://img.shields.io/badge/Twilio-WhatsApp-F22F46?logo=twilio&logoColor=white)](https://www.twilio.com/whatsapp)
[![Gemini](https://img.shields.io/badge/Gemini-2.5_Flash-4285F4?logo=google&logoColor=white)](https://ai.google.dev)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?logo=supabase&logoColor=white)](https://supabase.com)
[![ReportLab](https://img.shields.io/badge/ReportLab-PDF-red)](https://www.reportlab.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

*Send a photo of a handwritten bill or a voice note on WhatsApp — get a GST-compliant invoice PDF back in seconds.*

</div>

---

## 🎯 Problem Statement

Over **63 million** MSMEs in India still create invoices by hand. Most don't have access to invoicing software, and many can't read English UIs. DropInvoice meets them where they already are — **WhatsApp** — and handles the entire invoicing workflow with zero app installs, zero logins, and zero dashboards.

## 🎬 Demo

<!-- Replace with actual demo GIF/video after recording -->
![DropInvoice Demo](https://via.placeholder.com/800x400?text=Demo+GIF+Coming+Soon)

> **Try it:** Send a photo of any handwritten bill to the Twilio Sandbox WhatsApp number.

---

## 🏗 Architecture

```
[WhatsApp User]
      │
      ▼
[Twilio Webhook] ──► [FastAPI /webhook endpoint]
                              │
                    ┌─────────┴──────────┐
                    │                    │
              [Image Input]        [Voice Input]
                    │                    │
              [OpenCV preprocess]  [Whisper ASR]
                    │                    │
              [Tesseract OCR]      [Raw transcript]
                    │                    │
                    └─────────┬──────────┘
                              │
                    [Gemini API — extract structured
                     JSON: items, prices, GSTIN, name]
                              │
                    [Supabase — fetch/store user profile]
                              │
                    [ReportLab — generate GST PDF]
                              │
                    ┌─────────┴──────────┐
                    │                    │
             [Email PDF via         [WhatsApp reply
              Gmail SMTP]            via Twilio]
```

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| **API** | FastAPI + Uvicorn |
| **WhatsApp** | Twilio WhatsApp Business API |
| **OCR** | OpenCV + Tesseract (pytesseract) |
| **ASR** | OpenAI Whisper (local, base model) |
| **AI Parsing** | Google Gemini API (gemini-2.5-flash) |
| **PDF** | ReportLab |
| **Email** | Gmail SMTP (smtplib) |
| **Database** | Supabase (PostgreSQL) |
| **Queue** | Celery + Redis |
| **Deployment** | Railway / Render / Docker |

---

## 📂 Project Structure

```
dropinvoice/
├── main.py                    # FastAPI entry point + Twilio signature middleware
├── webhook/
│   └── handler.py             # Twilio webhook receiver + media router
├── processing/
│   ├── image_processor.py     # OpenCV preprocessing + Tesseract OCR
│   ├── audio_processor.py     # pydub WAV conversion + Whisper transcription
│   └── parser.py              # Gemini structured extraction + regex fallback
├── invoice/
│   ├── gst_calculator.py      # CGST/SGST/IGST logic + state code inference
│   └── generator.py           # ReportLab GST-compliant PDF generator
├── delivery/
│   ├── whatsapp.py            # Twilio WhatsApp PDF + summary sender
│   └── email_sender.py        # Gmail SMTP with HTML template
├── database/
│   └── supabase_client.py     # User profiles + invoice records + failure logging
├── tasks/
│   └── celery_tasks.py        # Async pipeline orchestration with retry logic
├── utils/
│   └── validators.py          # GSTIN checksum validator + phone normalizer
├── tests/
│   └── test_pipeline.py       # 40+ unit tests covering all modules
├── Procfile                   # Railway/Render process definitions
├── Dockerfile                 # Production Docker image
├── Aptfile                    # System deps for Railway buildpack
├── runtime.txt                # Python version pin
├── requirements.txt           # Pinned Python dependencies
├── DEPLOYMENT.md              # Full deployment guide + checklist
├── .env.example               # Environment variable template
└── README.md                  # This file
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Tesseract OCR (`brew install tesseract` on macOS)
- ffmpeg (`brew install ffmpeg`)
- Redis (`brew install redis && redis-server`)
- ngrok account (for local Twilio webhook testing)

### 1. Clone & Install

```bash
git clone https://github.com/your-username/dropinvoice.git
cd dropinvoice
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your actual API keys:
# - Twilio credentials (Account SID, Auth Token, WhatsApp number)
# - Gemini API key
# - Supabase URL + service role key
# - Gmail app password
```

### 3. Set Up Supabase Tables

Copy the SQL from the top of `database/supabase_client.py` and run it in your Supabase SQL editor.

### 4. Start Services

```bash
# Terminal 1: Redis
redis-server

# Terminal 2: Celery worker
celery -A tasks.celery_tasks worker --loglevel=info

# Terminal 3: FastAPI
uvicorn main:app --reload --port 8000

# Terminal 4: ngrok tunnel
ngrok http 8000
```

### 5. Configure Twilio Webhook

1. Go to [Twilio Console](https://console.twilio.com) → Messaging → WhatsApp Sandbox
2. Set the webhook URL to: `https://your-ngrok-url.ngrok.io/webhook`
3. Method: **POST**

### 6. Test It!

Send a photo of a handwritten bill to the Twilio WhatsApp Sandbox number.

---

## 📋 GST Compliance

DropInvoice follows Indian GST invoicing rules:

| Rule | Implementation |
|------|---------------|
| **Intra-state** | CGST (9%) + SGST (9%) = 18% total |
| **Inter-state** | IGST (18%) |
| **HSN Code** | User-provided or default `9999` |
| **Invoice Number** | `DROPINV-YYYYMM-XXXX` (auto-incremented) |
| **Required Fields** | Invoice No, Date, Seller/Buyer GSTIN, Items, Tax Breakdown, Grand Total |
| **GSTIN Validation** | 15-char format + modulo-36 checksum verification |
| **Missing GSTIN** | Watermark: "GSTIN NOT PROVIDED" |

---

## 🔄 How It Works

```
1. User sends a bill photo/voice note on WhatsApp
2. Twilio forwards the message to our FastAPI webhook
3. Webhook immediately replies: "⏳ Processing your invoice..."
4. Celery worker picks up the async task:
   a. Image → OpenCV preprocessing → Tesseract OCR → raw text
      Voice → pydub WAV conversion → Whisper ASR → transcript
   b. Gemini API extracts structured JSON (items, prices, GSTIN)
   c. GST calculator determines CGST/SGST or IGST
   d. ReportLab generates a professional PDF invoice
   e. PDF sent back via WhatsApp + emailed if address is on file
5. Invoice record saved to Supabase for history
```

---

## 🧪 Running Tests

```bash
# Run all tests
pytest tests/test_pipeline.py -v

# Run with coverage
pytest tests/test_pipeline.py -v --cov=. --cov-report=term-missing

# Run a specific test class
pytest tests/test_pipeline.py::TestGSTCalculator -v
```

---

## 🚢 Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for complete Railway and Render deployment instructions, including:
- Redis add-on setup
- Tesseract/ffmpeg system dependency installation
- Environment variable configuration
- Twilio webhook URL migration from ngrok
- Pre-deployment checklist

---

## 🛡 Error Handling

| Scenario | User Sees |
|----------|-----------|
| Unreadable image | 📸 *Couldn't read your bill clearly. Please retake in better lighting.* |
| Voice note < 3 sec | 🎤 *Voice note too short. Please describe your items clearly.* |
| Missing GSTIN | Invoice generated with "GSTIN NOT PROVIDED" watermark |
| Gemini API failure | Regex fallback parser extracts what it can |
| Twilio webhook timeout | Immediate ack + async Celery processing |

---

## 🗺 Roadmap

- [ ] Hindi/regional language OCR support
- [ ] Multi-page bill stitching
- [ ] Recurring invoice templates
- [ ] GST return filing integration (GSTR-1)
- [ ] WhatsApp Business API migration (non-sandbox)
- [ ] Invoice history via WhatsApp command ("show my invoices")

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">
<strong>Built with ❤️ for India's 63M small businesses</strong>
</div>
