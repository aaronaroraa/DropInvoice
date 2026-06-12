# DropInvoice Deployment Guide
# ============================
#
# Assumptions:
# - Deploying on Railway (primary) or Render (alternative).
# - Redis is provisioned as a platform add-on for Celery.
# - Tesseract OCR is available via an Apt buildpack or Docker layer.
# - Supabase is a managed external service (no self-hosted DB).
# - Twilio webhook URL switches from ngrok (local) to Railway's public URL.
#
# ===================================================================
# RAILWAY DEPLOYMENT
# ===================================================================
#
# 1. INSTALL RAILWAY CLI
#    npm install -g @railway/cli
#    railway login
#
# 2. CREATE PROJECT
#    cd dropinvoice/
#    railway init
#    railway link
#
# 3. ADD REDIS
#    railway add --plugin redis
#    → Railway auto-injects REDIS_URL into your service env.
#
# 4. CONFIGURE APT PACKAGES (for Tesseract + ffmpeg)
#    Create a file called Aptfile in the project root:
#
#    tesseract-ocr
#    tesseract-ocr-eng
#    libgl1-mesa-glx
#    ffmpeg
#
# 5. SET ENVIRONMENT VARIABLES
#    railway variables set APP_NAME=DropInvoice
#    railway variables set APP_ENV=production
#    railway variables set PUBLIC_BASE_URL=https://your-service.up.railway.app
#    railway variables set TWILIO_ACCOUNT_SID=ACxxxxxxxx
#    railway variables set TWILIO_AUTH_TOKEN=your_token
#    railway variables set TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
#    railway variables set GEMINI_API_KEY=your_key
#    railway variables set GEMINI_MODEL=gemini-2.5-flash
#    railway variables set SUPABASE_URL=https://your-project.supabase.co
#    railway variables set SUPABASE_SERVICE_ROLE_KEY=your_key
#    railway variables set GMAIL_USERNAME=your_email@gmail.com
#    railway variables set GMAIL_APP_PASSWORD=your_app_password
#    railway variables set TESSERACT_CMD=/usr/bin/tesseract
#    railway variables set WHISPER_MODEL=base
#
# 6. DEPLOY
#    railway up
#    → Railway reads Procfile and starts both `web` and `worker` processes.
#
# 7. UPDATE TWILIO WEBHOOK URL
#    Go to Twilio Console → Messaging → WhatsApp Sandbox Settings
#    Set webhook to: https://your-service.up.railway.app/webhook
#    Method: POST
#
# ===================================================================
# RENDER DEPLOYMENT (alternative)
# ===================================================================
#
# 1. Create a Web Service on render.com → connect your GitHub repo.
# 2. Set Build Command:  pip install -r requirements.txt
# 3. Set Start Command:  uvicorn main:app --host 0.0.0.0 --port $PORT
# 4. Add a Background Worker for Celery:
#    Start Command: celery -A tasks.celery_tasks worker --loglevel=info
# 5. Add Redis: Render Dashboard → New → Redis → copy Internal URL.
# 6. Set REDIS_URL env var to the Redis Internal URL.
# 7. Set all other env vars from the list above.
# 8. For Tesseract: use a Docker runtime with a custom Dockerfile:
#
#    FROM python:3.10-slim
#    RUN apt-get update && apt-get install -y \
#        tesseract-ocr tesseract-ocr-eng ffmpeg libgl1-mesa-glx \
#        && rm -rf /var/lib/apt/lists/*
#    WORKDIR /app
#    COPY requirements.txt .
#    RUN pip install --no-cache-dir -r requirements.txt
#    COPY . .
#    CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
#
# ===================================================================
# PRE-DEPLOYMENT CHECKLIST
# ===================================================================
#
# [ ] All environment variables set (check .env.example for full list)
# [ ] Supabase tables created (run SQL from database/supabase_client.py)
# [ ] Redis add-on provisioned and REDIS_URL injected
# [ ] Tesseract OCR installed on the deployment platform
# [ ] ffmpeg installed (required by pydub for audio conversion)
# [ ] Twilio WhatsApp Sandbox webhook updated to production URL
# [ ] Twilio webhook method set to POST
# [ ] Gmail App Password generated (not regular password)
# [ ] CORS origins updated for production (ALLOWED_ORIGINS)
# [ ] PUBLIC_BASE_URL matches the deployed service URL exactly
# [ ] Health check endpoint verified: GET /health returns 200
# [ ] Celery worker process is running (check logs for "celery@... ready")
# [ ] Test with a real WhatsApp message to the Twilio sandbox number
# [ ] Whisper model downloads on first run (~140MB for base) — allow time
# [ ] /tmp/invoices/ directory is writable (Railway uses ephemeral FS)
#
# ===================================================================
# MONITORING & LOGS
# ===================================================================
#
# Railway:  railway logs --follow
# Render:   Dashboard → Service → Logs tab
#
# Monitor Celery workers:
#   celery -A tasks.celery_tasks inspect active
#   celery -A tasks.celery_tasks inspect stats
