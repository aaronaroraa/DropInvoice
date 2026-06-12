web: uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
worker: celery -A tasks.celery_tasks worker --loglevel=info --concurrency=2
