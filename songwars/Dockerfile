# deno er den JavaScript-motor yt-dlp skal bruge for at hente fra YouTube.
FROM denoland/deno:bin-2.1.4 AS deno

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SONGWARS_DATA=/data \
    TMPDIR=/data/tmp \
    DENO_DIR=/data/cache/deno \
    XDG_CACHE_HOME=/data/cache

WORKDIR /app

# ffmpeg laver lyden fra YouTube om til mp3.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py audio.py youtube.py ./
COPY templates/ templates/
COPY static/ static/

# Appen koerer som uid 10001 og skriver kun i /data. Ejerskabet her gaelder
# et navngivet volume; ved en bind-mount (./data) er det vaertens mappe der
# taeller - se DEPLOY.md. Appen siger klart fra ved opstart hvis den ikke
# kan skrive.
RUN groupadd --gid 10001 songwars \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin songwars \
 && mkdir -p /data/tmp \
 && chown -R 10001:10001 /data
USER 10001:10001

EXPOSE 8092

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8092/healthz', timeout=3).status == 200 else 1)"

# gthread: en upload paa 50 MB binder en traad, ikke en hel worker, saa
# polling fra de andre spillere bliver ved med at svare. --preload laver
# databasen en gang foer der forkes.
CMD ["gunicorn", "--preload", \
     "--workers", "2", "--threads", "8", "--worker-class", "gthread", \
     "--timeout", "120", "--worker-tmp-dir", "/dev/shm", \
     "--bind", "0.0.0.0:8092", \
     "--access-logfile", "-", "app:app"]
