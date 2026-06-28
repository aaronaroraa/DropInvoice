FROM python:3.10-slim

WORKDIR /app

# Slim install — image processing uses Gemini Vision API, no system ML deps needed
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY . .

RUN mkdir -p /tmp/invoices

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
