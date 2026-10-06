FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROJECT_ROOT=/home/app/project

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && install -d -o app -g app /home/app/project

WORKDIR /home/app/project

COPY --chown=app:app devserver/ ./devserver/
COPY --chown=app:app tests/ ./tests/
COPY --chown=app:app evaluate.py ./evaluate.py
COPY --chown=app:app README.md ARCHITECTURE.md SECURITY.md EVALUATION.md ./

# Keep code read-only while allowing the unittest fixtures to create temporary files under PROJECT_ROOT.
RUN find /home/app/project -type d -exec chmod 0555 {} + \
    && find /home/app/project -type f -exec chmod 0444 {} + \
    && chmod 0755 /home/app/project

USER 10001:10001

ENTRYPOINT ["python", "-m", "devserver"]
