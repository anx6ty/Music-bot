FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg libopus0 libopus-dev curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno — JS runtime yt-dlp needs (since Oct 2025) to solve YouTube's signature
# challenge. Without this, many videos fail to extract a playable audio URL.
RUN curl -fsSL https://deno.land/install.sh | sh \
    && mv /root/.deno/bin/deno /usr/local/bin/deno
ENV DENO_DIR=/tmp/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Always pull the newest yt-dlp at build time, even if requirements.txt didn't
# change — YouTube extraction breaks and gets patched on almost a weekly basis.
RUN pip install --upgrade --no-cache-dir yt-dlp

COPY . .

CMD ["python", "bot.py"]
