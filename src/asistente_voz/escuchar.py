"""Voz -> texto -> agente: captura manual por defecto, con evidencia persistente.

Uso: uv run python -m asistente_voz.escuchar [--modo prueba] [--captura automatica]
El texto se revisa antes de enviarlo al motor compartido; las confirmaciones usan ID de propuesta.
"""
import json
import shutil
import select
import sys
from uuid import uuid4
import time
import wave
from pathlib import Path

import numpy as np

from asistente_voz import asr

FRECUENCIA = 16000  # Hz, la que usa Whisper
BLOQUE = 0.03  # segundos por bloque de audio
UMBRAL = 0.01  # energía (RMS, escala 0 a 1) por encima de la cual se considera voz
SILENCIO_FINAL = 1.0  # segundos de silencio, ya habiendo hablado, que cierran la frase
ESPERA_VOZ = 8.0  # segundos esperando que empiece a hablar
MAX_FRASE = 15.0


def grabar(destino: Path, umbral: float = UMBRAL) -> bool:
    """Graba mono a `destino` (wav) desde que se supera el umbral hasta SILENCIO_FINAL de silencio.

    Devuelve False si nadie habló. La detección es solo por energía: el ruido de fondo fuerte puede
    impedir el corte, y por eso hay un tope de MAX_FRASE segundos.
    """
    import sounddevice as sd  # acá y no arriba: importar PortAudio no debería hacer falta para leer un archivo

    bloques, hablo, callado, inicio = [], False, 0.0, time.monotonic()
    with sd.InputStream(samplerate=FRECUENCIA, channels=1, dtype="float32", blocksize=int(FRECUENCIA * BLOQUE)) as flujo:
        while True:
            datos, _ = flujo.read(int(FRECUENCIA * BLOQUE))
            voz = float(np.sqrt(np.mean(datos**2))) > umbral
            if voz:
                hablo, callado = True, 0.0
            elif hablo:
                callado += BLOQUE
            if hablo:
                bloques.append(datos)
            if hablo and (callado >= SILENCIO_FINAL or len(bloques) * BLOQUE >= MAX_FRASE):
                break
            if not hablo and time.monotonic() - inicio > ESPERA_VOZ:
                return False
    audio = (np.concatenate(bloques)[:, 0] * 32767).astype(np.int16)
    with wave.open(str(destino), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(FRECUENCIA)
        f.writeframes(audio.tobytes())
    return True


def _esperar_fin(segundos: float) -> bool:
    """Espera Enter en la terminal; False indica que venció el máximo."""
    disponible, _, _ = select.select([sys.stdin], [], [], segundos)
    if not disponible:
        return False
    if sys.stdin.readline() == "":
        raise EOFError("Se cerró la entrada de la terminal.")
    return True


def grabar_manual(destino: Path, max_segundos: float = 60.0, esperar_fin=_esperar_fin) -> str:
    """Conserva todo el intervalo abierto, sin disparo por energía ni corte por pausas."""
    import sounddevice as sd

    input("Presioná Enter para preparar el micrófono. ")
    bloques = []

    def recibir(datos, frames, tiempo, estado):
        bloques.append(datos.copy())
        if estado:
            print(f"Aviso de captura: {estado}", file=sys.stderr, flush=True)

    try:
        with sd.InputStream(samplerate=FRECUENCIA, channels=1, dtype="float32", callback=recibir):
            print("Grabando. Hablá ahora; presioná Enter para terminar (máximo 60 s).", flush=True)
            terminado = esperar_fin(max_segundos)
    finally:
        # También conserva la parte capturada si se interrumpe con Ctrl+C.
        if bloques:
            audio = (np.clip(np.concatenate(bloques)[:, 0], -1, 1) * 32767).astype(np.int16)
            with wave.open(str(destino), "wb") as archivo:
                archivo.setnchannels(1)
                archivo.setsampwidth(2)
                archivo.setframerate(FRECUENCIA)
                archivo.writeframes(audio.tobytes())
    return "sin_audio" if not bloques else "manual" if terminado else "limite"


def _actualizar(destino: Path, **campos) -> None:
    """Conserva el estado del intento también si falla después de transcribir."""
    datos = json.loads(destino.read_text()) if destino.exists() else {}
    datos.update(campos)
    temporal = destino.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n")
    temporal.replace(destino)


def revisar_transcripcion(texto: str) -> str | None:
    """Revisión diagnóstica; la corrección se conserva separada del ASR original."""
    while True:
        opcion = input("Transcripción: Enter aceptar | e editar | c cancelar > ").strip().lower()
        if not opcion:
            return texto
        if opcion in ("c", "cancelar"):
            return None
        if opcion in ("e", "editar"):
            return input("Escribí lo que quisiste decir: ").strip()
        print("Para corregir el texto, escribí e y luego ingresá la transcripción corregida. Enter acepta; c cancela.")


def capturar_turno(motor, sesion, args):
    """Guarda audio y revisión; no interpreta ni ejecuta acciones."""
    carpeta = motor.carpeta / sesion / "voz" / uuid4().hex
    carpeta.mkdir(parents=True)
    audio = carpeta / ("audio" + args.audio.suffix if args.audio else "audio.wav")
    evidencia = carpeta / "intento.json"
    _actualizar(evidencia, origen="voz", estado="iniciado", audio=audio.name, modelo_asr=args.whisper,
                vocabulario_asr=None if args.whisper == asr.MODELO_LATAM else asr.VOCABULARIO,
                revision_asr=asr.REVISION_LATAM if args.whisper == asr.MODELO_LATAM else None, idioma_asr="es", beam_size=5, vad_filter=True)
    try:
        if args.audio:
            shutil.copyfile(args.audio, audio)
            args.audio = None
        elif args.captura == "automatica":
            if not grabar(audio):
                _actualizar(evidencia, estado="sin_voz")
                return None
        elif grabar_manual(audio) != "manual":
            _actualizar(evidencia, estado="captura_incompleta")
            return None
        original = asr.transcribe(audio, args.whisper)
        _actualizar(evidencia, transcripcion_original=original, estado="transcripto")
        print(f"Entendí: «{original}»")
        texto = revisar_transcripcion(original) if original.strip() else None
        _actualizar(evidencia, transcripcion_revisada=texto, estado="revisado" if texto else "cancelado")
        if texto:
            return texto, {"audio": str(audio), "audio_id": carpeta.name,
                           "transcripcion_original": original, "transcripcion_revisada": texto,
                           "modelo_asr": args.whisper}
    except (Exception, KeyboardInterrupt) as error:
        _actualizar(evidencia, estado="error", error_tipo=type(error).__name__)
        raise
    return None


def main():
    from asistente_voz.conversar import main as conversar
    conversar(voz=True)


if __name__ == "__main__":
    main()
