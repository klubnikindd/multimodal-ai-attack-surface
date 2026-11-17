# Research/dev only. Не канал доставки (ADR-0001).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPYCACHEPREFIX=/tmp/pycache \
    PYTEST_ADDOPTS="-p no:cacheprovider" \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    KADR_HOME=/data/kadr

RUN useradd --create-home --uid 10001 kadr
# Отдельный venv в writable-слое образа: /app монтируется read-only в compose,
# и рантайм не должен зависеть от записи в системный site-packages.
RUN python -m venv "$VIRTUAL_ENV"
WORKDIR /app
COPY pyproject.toml README.md ./
COPY kadr ./kadr
COPY tests/kadr ./tests/kadr
RUN pip install --no-cache-dir --no-compile ".[dev,api]" \
    && chmod -R a+rX /app "$VIRTUAL_ENV"

USER kadr
CMD ["pytest", "-q", "tests/kadr"]
