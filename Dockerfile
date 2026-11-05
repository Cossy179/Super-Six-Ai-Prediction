FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SUPER6_HOME=/app
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

COPY alembic.ini ./
COPY configs ./configs
COPY migrations ./migrations
COPY rounds ./rounds

ENTRYPOINT ["super6"]
CMD ["--help"]
