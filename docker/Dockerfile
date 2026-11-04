FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && useradd --system --uid 10001 --create-home appuser

COPY src/config.py src/database.py src/main.py src/schemas.py ./src/
COPY src/scrapers/ ./src/scrapers/
COPY src/services/ ./src/services/
COPY sql/ ./sql/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER appuser

CMD ["python", "src/main.py"]
