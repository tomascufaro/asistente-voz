FROM python:3.12.16-slim-bookworm

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY src ./src
COPY casos ./casos
COPY data/documentos ./data/documentos

RUN python -m pip install --no-cache-dir uv==0.13.6 \
    && uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

EXPOSE 8765
CMD ["python", "-m", "asistente_voz.web", "--host", "0.0.0.0"]
