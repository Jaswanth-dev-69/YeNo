# YeNo volume bot. Stdlib only: nothing is installed. Base pinned by tag and digest.
FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YENO_BIND_HOST=0.0.0.0 \
    YENO_PORT=8080

WORKDIR /app
COPY yeno_bot.py /app/yeno_bot.py
USER nobody
EXPOSE 8080
CMD ["python", "/app/yeno_bot.py"]
