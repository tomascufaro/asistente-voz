.PHONY: setup modelo web

setup: ## Instala dependencias fijadas en uv.lock
	uv sync --no-dev

modelo: ## Descarga el modelo de lenguaje local
	ollama pull qwen2.5:3b

web: ## Inicia la interfaz local en 127.0.0.1:8765
	uv run python -m asistente_voz.web
