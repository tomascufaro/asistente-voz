# Guía rápida de instalación

**Versión del proyecto:** commit 36f1146  
**Ejecución:** Docker Compose

## Requisitos

- Docker Desktop o Docker Engine con Docker Compose v2.
- Acceso al repositorio.
- Internet para descargar las imágenes y modelos en el primer inicio.

El Compose usa Python 3.12.16, Ollama 0.40.13 y Qwen 2.5 3B. Whisper LATAM Turbo se descarga al usar voz por primera vez.

## Instalar e iniciar

Clonar el repositorio, entrar en la carpeta y seleccionar la versión de entrega:

    git clone https://github.com/tomascufaro/asistente-voz.git
    cd asistente-voz
    git checkout 36f1146

Iniciar la aplicación:

    docker compose up --build

Esperar a que Ollama descargue Qwen y abrir http://127.0.0.1:8765. Para usar voz, permitir el acceso al micrófono en el navegador.

Si el puerto 8765 está ocupado:

    APP_PORT=8767 docker compose up --build

En ese caso, abrir http://127.0.0.1:8767.

Para probar la consulta documental, copiar los documentos ficticios entregados por separado en data/documentos/.

## Detener y conservar los datos

Presionar Ctrl+C o ejecutar:

    docker compose down

La agenda, los borradores, el historial y las cachés de modelos se conservan. No ejecutar docker compose down -v, porque elimina también los volúmenes de modelos.
