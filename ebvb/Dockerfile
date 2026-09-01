FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EBVB_DATA=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Appen skriver kun i /data. Alt andet er read-only for brugeren.
RUN useradd --uid 10001 --create-home ebvb \
 && mkdir -p /data \
 && chown -R ebvb:ebvb /data
USER ebvb

EXPOSE 8090

# 2 workers holder hukommelsen nede (~120 MB i alt). --timeout 300 fordi
# en 400 MB wav skal skrives til disk for svaret sendes.
CMD ["gunicorn", "--workers", "2", "--timeout", "300", \
     "--bind", "0.0.0.0:8090", "app:app"]
