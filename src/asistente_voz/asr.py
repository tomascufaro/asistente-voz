"""Reconocimiento de voz local con faster-whisper (CPU, sin GPU)."""
from pathlib import Path

from faster_whisper import WhisperModel
from faster_whisper.utils import download_model

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"

# Vocabulario del caso; sin frases esperadas, precios, contactos ni datos inventados.
VOCABULARIO = "Pastelería casera. Catálogo, budín, limón, chocolate, naranja, alfajores, harina, precio, agenda."

MODELO_LATAM = "nekusu/faster-whisper-large-v3-turbo-latam-int8-ct2"
REVISION_LATAM = "cdc3c64a38e8278e84efe9ea9342a9e15466837c"
MODELO_PREDETERMINADO = MODELO_LATAM

_cache: dict[str, WhisperModel] = {}


def load_model(size: str = MODELO_PREDETERMINADO) -> WhisperModel:
    """Carga (y descarga la primera vez) un modelo Whisper en models/."""
    if size not in _cache:
        modelo = (download_model(size, cache_dir=str(MODELS_DIR), revision=REVISION_LATAM)
                  if size == MODELO_LATAM else size)
        _cache[size] = WhisperModel(
            modelo, device="cpu", compute_type="int8", download_root=str(MODELS_DIR)
        )
    return _cache[size]


def transcribe(audio_path: str | Path, size: str = MODELO_PREDETERMINADO) -> str:
    """Transcribe un archivo de audio en español y devuelve el texto."""
    segments, _info = load_model(size).transcribe(
        str(audio_path), language="es", beam_size=5, vad_filter=True,
        initial_prompt=None if size == MODELO_LATAM else VOCABULARIO
    )
    return " ".join(segment.text.strip() for segment in segments).strip()
