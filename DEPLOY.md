# Deploying DropInvoice to Render

This runs DropInvoice 24/7 on a permanent URL — no laptop, no tunnel, no
re-pointing the Twilio webhook ever again.

## 1. Create the service
1. Go to https://render.com and sign up with **"Sign in with GitHub"**.
2. **New → Blueprint**.
3. Connect GitHub and select the **`aaronaroraa/DropInvoice`** repo.
4. Render reads `render.yaml` and creates the `dropinvoice` web service.

## 2. Add the secret env vars (Render prompts for `sync: false` keys)
Copy these values from your local `.env` into Render's dashboard fields:

| Key | Value |
| --- | --- |
| `TWILIO_ACCOUNT_SID` | from .env |
| `TWILIO_AUTH_TOKEN` | from .env |
| `TWILIO_WHATSAPP_FROM` | `whatsapp:+14155238886` |
| `GEMINI_API_KEY` | from .env |
| `SUPABASE_URL` | from .env |
| `SUPABASE_SERVICE_ROLE_KEY` | from .env |
| `GMAIL_USERNAME` | from .env |
| `GMAIL_APP_PASSWORD` | from .env |
| `PUBLIC_BASE_URL` | leave blank for now (set in step 4) |

Never commit secrets — enter them in the Render dashboard only.

## 3. First deploy → get the URL
- Render builds (`pip install -r requirements-deploy.txt`) and starts
  (`uvicorn main:app --host 0.0.0.0 --port $PORT`).
- You get a URL like `https://dropinvoice.onrender.com`.
- Health check: open `https://<your-url>/health` → should return
  `{"status":"ok",...}`.

## 4. Finalize the public URL + Twilio webhook (one time)
1. In Render env vars, set `PUBLIC_BASE_URL` = your exact Render URL, then
   **Manual Deploy → Deploy latest commit** so it takes effect.
2. In Twilio (WhatsApp Sandbox settings, or your WhatsApp Sender), set the
   inbound webhook to `https://<your-url>/webhook` (HTTP POST). This never
   changes again.

## Notes
- **Free tier sleeps** after ~15 min idle; the first request wakes it (~30–60s).
  Upgrade to the **Starter** plan (~$7/mo) to keep it always awake for a client.
- **Gemini billing**: enable pay-as-you-go on the Gemini API key so AI parsing
  isn't capped by the free 20-requests/day limit (cost is a fraction of a cent
  per bill).
- The heavy local ML stack (Whisper/OpenCV/Tesseract) is excluded from the cloud
  build — images are read via Gemini Vision, so it isn't needed on the server.
