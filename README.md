# Asistente local de pastelería

Asistente en español rioplatense para consultar agenda y documentos, crear eventos y preparar borradores de correo. La app corre localmente; no envía correos y pide confirmación antes de guardar cambios.

## Video demostrativo

[Ver video demostrativo](demo/video-demostrativo.mov)

## Iniciar con Docker

Requisitos: Docker Desktop o Docker Engine con Compose, conexión a internet para descargar las imágenes y modelos.

```sh
docker compose up --build
```

Abrí <http://127.0.0.1:8765>. Si el puerto está ocupado, elegí uno libre:

```sh
APP_PORT=8767 docker compose up --build
```

En ese caso, abrí <http://127.0.0.1:8767>. El primer inicio descarga Ollama y Qwen 2.5 3B; el primer uso de voz descarga Whisper. Esas cachés se conservan entre reinicios. La agenda, los borradores y el historial quedan en `data/`.

Para detener, presioná `Ctrl+C`. Si lo iniciaste en segundo plano, ejecutá `docker compose down`. No uses `docker compose down -v` salvo que quieras borrar también las cachés de modelos.

## Usar la app

- Escribí un pedido o grabá voz. Revisá la transcripción antes de enviarla.
- **Ver agenda** muestra los eventos desde hoy. La agenda empieza vacía.
- **Ver documentos** muestra los archivos de texto locales. No se incluyen documentos de negocio en este repositorio. Para usarlos, copiá tus `.txt` a `data/documentos/`; quedan en tu equipo y no los agregues a Git.
- Para redactar un correo, escribí la dirección en el campo de teclado. La app solo guarda borradores; no envía mensajes.
- Revisá cada propuesta y confirmala o rechazala.

Los documentos y datos de negocio son locales. La web se publica solo en `127.0.0.1`; Ollama queda dentro de la red Docker.

## Ejecución nativa y pruebas

Con Python 3.12, `uv`, Ollama y `make` instalados:

```sh
uv sync --no-dev
ollama serve
ollama pull qwen2.5:3b
uv run python -m asistente_voz.web
```

Pruebas sin modelos y del frontend:

```sh
make rapido
make test-front
```

La grabación de voz en Docker no se ha validado en todos los navegadores y equipos. Para usar el micrófono, abrí la app desde `localhost` y concedé el permiso del navegador.

Este repositorio no especifica una licencia de uso.
