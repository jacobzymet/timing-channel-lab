FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN groupadd --gid 10001 channel \
    && useradd --uid 10001 --gid channel --no-create-home --shell /usr/sbin/nologin channel \
    && mkdir -p /run/channel \
    && chown channel:channel /run/channel

COPY --chown=channel:channel src/ /app/src/
COPY --chown=channel:channel LICENSE /app/LICENSE

USER channel

ENTRYPOINT ["python", "-m"]
