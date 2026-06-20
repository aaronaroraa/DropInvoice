# DropInvoice — 5-Minute Interview Demo Script
# =============================================
#
# Use this script to walk through DropInvoice during a technical interview
# or capstone presentation. Assumes the system is running locally with ngrok.
#
# -------------------------------------------------------------------
# MINUTE 0:00–0:30 — THE HOOK
# -------------------------------------------------------------------
#
# "63 million small businesses in India still write invoices by hand.
#  Most don't have computers. But every single one of them has WhatsApp.
#
#  DropInvoice turns WhatsApp into a GST invoicing system.
#  Send a bill photo → get a professional invoice PDF back.
#  No app. No login. No dashboard. Just WhatsApp."
#
# → Show the README hero section on GitHub (badges, tagline).
#
# -------------------------------------------------------------------
# MINUTE 0:30–1:30 — LIVE DEMO
# -------------------------------------------------------------------
#
# 1. Open WhatsApp on your phone.
# 2. Send a pre-prepared photo of a handwritten bill to the Twilio
#    sandbox number. (Have this ready in your camera roll.)
#
#    Example bill to photograph:
#    ┌────────────────────────────┐
#    │ Sharma General Store       │
#    │ GSTIN: 07AAACR5055K1Z5    │
#    │                            │
#    │ Rice   5 kg × ₹60 = ₹300  │
#    │ Dal    2 kg × ₹120 = ₹240 │
#    │ Oil    1 ltr × ₹180 = ₹180│
#    │                            │
#    │ Total: ₹720                │
#    └────────────────────────────┘
#
# 3. Show the WhatsApp reply:
#    "⏳ Processing your invoice..."
#
# 4. Wait ~15 seconds. Show the PDF arriving on WhatsApp.
#
# 5. Open the PDF and highlight:
#    - Professional DropInvoice branding header
#    - Seller/Buyer details with GSTIN
#    - HSN code column
#    - CGST 9% + SGST 9% tax breakdown
#    - Grand total with tax
#    - "GSTIN NOT PROVIDED" watermark (if applicable)
#
# "That entire flow — image capture, OCR, AI parsing, tax calculation,
#  PDF generation, delivery — happened in under 20 seconds."
#
# -------------------------------------------------------------------
# MINUTE 1:30–2:30 — VOICE NOTE DEMO (optional but impressive)
# -------------------------------------------------------------------
#
# 1. Record a voice note on WhatsApp:
#    "I sold 5 kg rice at 60 rupees, 2 kg dal at 120 rupees,
#     and 1 litre oil at 180 rupees."
#
# 2. Send it to the Twilio number.
#
# 3. Show the PDF result — same quality output from speech.
#
# "For kirana owners who can't write English or Hindi clearly,
#  voice notes are a game-changer. Whisper handles the transcription,
#  Gemini structures the data."
#
# -------------------------------------------------------------------
# MINUTE 2:30–3:30 — ARCHITECTURE WALKTHROUGH
# -------------------------------------------------------------------
#
# Pull up the architecture diagram from the README (terminal or slide):
#
# "Here's how the system works end-to-end:"
#
# 1. WEBHOOK LAYER
#    "Twilio forwards every WhatsApp message to our FastAPI endpoint.
#     We validate the Twilio signature for security, then immediately
#     reply '⏳ Processing...' to avoid webhook timeouts."
#
# 2. PROCESSING PIPELINE
#    "For images: OpenCV preprocesses (grayscale, denoise, deskew),
#     then Tesseract extracts raw text.
#     For voice: pydub converts to WAV, Whisper runs locally."
#
# 3. AI EXTRACTION
#    "The bill image goes to Gemini Vision with a carefully engineered
#     system prompt. Gemini returns structured JSON — line items, GSTINs,
#     prices. If Gemini fails, a regex fallback handles it."
#
# 4. GST CALCULATION
#    "The calculator checks seller and buyer GSTIN state codes.
#     Same state → CGST + SGST at 9% each.
#     Different states → IGST at 18%.
#     This follows actual Indian GST rules."
#
# 5. PDF GENERATION
#    "ReportLab creates a professional invoice with a branded header,
#     HSN code table, and tax breakdown. Invoice numbers auto-increment
#     in DROPINV-YYYYMM-XXXX format."
#
# 6. DELIVERY
#    "The PDF goes back via WhatsApp. If the user has a registered
#     email in Supabase, it's also emailed with an HTML template."
#
# -------------------------------------------------------------------
# MINUTE 3:30–4:15 — TECHNICAL DEPTH
# -------------------------------------------------------------------
#
# Pick 2-3 of these talking points based on interviewer interest:
#
# A) ASYNC ARCHITECTURE
#    "Processing takes 10-20 seconds, but Twilio expects a response
#     in 15. So we use Celery with Redis — the webhook acks instantly,
#     and the heavy pipeline runs asynchronously. Failed tasks retry
#     twice with exponential backoff."
#
# B) ERROR RESILIENCE
#    "If the image is unreadable, we tell the user to retake it.
#     If Gemini fails, we fall back to regex extraction.
#     Every failure is logged to Supabase for debugging.
#     The user always gets a response — never silence."
#
# C) GSTIN VALIDATION
#    "I implemented the actual Indian government modulo-36 checksum
#     algorithm for GSTIN validation. It's not just regex — it
#     verifies the check digit mathematically."
#
# D) OCR PREPROCESSING
#    "Raw phone photos are messy — skewed, noisy, inconsistent
#     lighting. The OpenCV pipeline does grayscale → denoise →
#     adaptive threshold → deskew → padding before Tesseract runs.
#     This dramatically improves OCR accuracy."
#
# E) SUPABASE INTEGRATION
#    "User profiles are auto-created on first message. If a user
#     sends invoices regularly, their GSTIN and business name are
#     remembered and auto-filled. Invoice history is queryable."
#
# -------------------------------------------------------------------
# MINUTE 4:15–5:00 — IMPACT & FUTURE
# -------------------------------------------------------------------
#
# "DropInvoice proves that meaningful software doesn't need a fancy UI.
#  The best interface for India's 63 million MSMEs is the one they
#  already use every day — WhatsApp.
#
#  Next steps:
#  - Hindi and regional language OCR support
#  - GSTR-1 filing integration for direct GST returns
#  - WhatsApp Business API for production scale
#  - Multi-page bill stitching for larger invoices
#
#  This project taught me how to build a production pipeline that
#  handles real-world messiness — blurry photos, accented speech,
#  OCR errors — and still delivers a reliable result every time."
#
# -------------------------------------------------------------------
# BACKUP: If the live demo fails
# -------------------------------------------------------------------
#
# Have these ready:
# 1. A pre-recorded screen capture of the WhatsApp flow (30 seconds)
# 2. A sample generated PDF saved locally
# 3. The test suite output (pytest -v) as proof the code works
#
# Run the tests live:
#   pytest tests/test_pipeline.py -v --tb=short
#
# Show the health check:
#   curl http://localhost:8000/health
#
# -------------------------------------------------------------------
# COMMON INTERVIEW QUESTIONS & ANSWERS
# -------------------------------------------------------------------
#
# Q: "Why not use Google Cloud Vision instead of Tesseract?"
# A: "Tesseract is free and runs locally — no per-image API cost.
#     For a product targeting ₹50/day kirana owners, cost matters.
#     Cloud Vision is a great upgrade path for accuracy."
#
# Q: "Why Gemini and not GPT-4?"
# A: "Gemini's multimodal reliability is excellent — it reads the bill
#     image directly and returns structured JSON. The system prompt +
#     schema approach produces consistent, parseable results. GPT-4
#     would work too."
#
# Q: "How do you handle scale?"
# A: "Celery workers scale horizontally. Each worker handles one
#     invoice at a time (OCR/Whisper are CPU-bound). Redis queue
#     ensures nothing is lost. Supabase handles DB scale."
#
# Q: "What's the biggest engineering challenge?"
# A: "Handling the messiness of real-world input. Handwritten bills
#     have spelling errors, inconsistent formatting, and blurry photos.
#     The Gemini prompt engineering + regex fallback combo handles
#     ~90% of cases. The remaining 10% is logged for improvement."
