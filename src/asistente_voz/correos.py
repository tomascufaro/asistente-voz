"""Direcciones de correo: dictado acotado en español y validación local, sin consultar cuentas."""
import re

# Subconjunto práctico: direcciones ASCII sin comillas ni nombres de presentación.
_LOCAL = r"[A-Za-z0-9_%+.-]+"
_DOMINIO = r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
_DIRECCION = re.compile(rf"{_LOCAL}@{_DOMINIO}")
_OPERADORES = ((r"guion bajo|guión bajo", "_"), (r"arroba", "@"),
               (r"punto", "."), (r"guion|guión", "-"), (r"más|mas", "+"))


def normalizar_direccion(texto: str) -> str:
    """Convierte operadores explícitos; no une palabras ni corrige nombres o dominios."""
    valor = texto.strip().removesuffix(".")  # puntuación final de una frase ASR
    if direccion_valida(valor):
        return valor
    for patron, simbolo in _OPERADORES:
        valor = re.sub(rf"\b(?:{patron})\b", lambda m: simbolo, valor, flags=re.IGNORECASE)
    return re.sub(r"\s*([@._+%-])\s*", r"\1", valor)


def direccion_valida(valor: str) -> bool:
    """Solo sintaxis del subconjunto admitido; no verifica existencia ni titularidad."""
    if len(valor) > 254 or not _DIRECCION.fullmatch(valor):
        return False
    local, dominio = valor.split("@")
    return (len(local) <= 64 and not local.startswith(".") and not local.endswith(".")
            and ".." not in local and all(
                0 < len(etiqueta) <= 63 and not etiqueta.startswith("-") and not etiqueta.endswith("-")
                for etiqueta in dominio.split(".")))


def direcciones_en_texto(texto: str) -> list[str]:
    """Extrae tokens completos literales válidos, sin rescatar fragmentos de direcciones rotas."""
    valores = []
    for token in texto.split():
        candidato = token.strip('«»<>(),;:!?"').removesuffix(".").strip('«»<>(),;:!?"')
        if direccion_valida(candidato) and candidato not in valores:
            valores.append(candidato)
    return valores
