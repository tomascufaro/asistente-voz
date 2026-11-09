"""Perfiles locales permitidos; la sesión conserva su propia copia verificable."""
import hashlib
import re
from pathlib import Path

MAX_PERFIL = 12000
CASOS = Path(__file__).with_name("casos")
if not CASOS.is_dir():
    CASOS = Path(__file__).resolve().parents[2] / "casos"
PERFILES = {"pasteleria_casera": "Pastelería casera", "generico": "Sin especialización"}


def validar(perfil):
    if not isinstance(perfil, dict) or perfil.get("id") not in PERFILES:
        raise ValueError("El perfil no es válido.")
    texto = perfil.get("texto")
    if not isinstance(texto, str) or len(texto.encode("utf-8")) > MAX_PERFIL:
        raise ValueError("El perfil supera el tamaño permitido.")
    if perfil["id"] != "generico" and not all(c in texto for c in ("## Caso", "## Herramientas", "## Límites")):
        raise ValueError("El perfil debe describir caso, herramientas y límites.")
    if hashlib.sha256(texto.encode("utf-8")).hexdigest() != perfil.get("sha256"):
        raise ValueError("La copia del perfil no coincide con su huella registrada.")
    return perfil


def cargar(identificador="pasteleria_casera"):
    if not isinstance(identificador, str) or identificador not in PERFILES:
        raise ValueError("Elegí un perfil permitido.")
    texto = ""
    if identificador != "generico":
        archivo = CASOS / identificador / "AGENTS.md"
        if archivo.parent.is_symlink() or archivo.is_symlink() or not archivo.is_file():
            raise ValueError("No se encuentra el perfil solicitado.")
        if archivo.stat().st_size > MAX_PERFIL:
            raise ValueError("El perfil supera el tamaño permitido.")
        texto = archivo.read_text(encoding="utf-8")
    version = re.search(r"Versión: ([^\s]+)", texto)
    return validar({"id": identificador, "nombre": PERFILES[identificador], "texto": texto,
                    "version": version[1] if version else "sin-perfil",
                    "sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest()})


def de_sesion(configuracion):
    # Las sesiones anteriores a los perfiles mantienen su configuración histórica.
    return validar(configuracion["perfil"]) if "perfil" in configuracion else cargar("generico")
