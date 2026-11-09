"""Completar campos explícitos del pedido activo, independiente de la interfaz."""
from asistente_voz import fechas
from asistente_voz.correos import normalizar_direccion, direccion_valida, direcciones_en_texto

ACLARACION_HORA = "\nAclaración de hora: "

def completar_aclaracion(original: str, respuesta: str, faltan: list[str], hoy=None) -> str | None:
    """Completa fecha/hora o un correo explícito sin perder el pedido original."""
    if not original.strip():
        return respuesta
    if "destinatario" in faltan:
        correo = normalizar_direccion(respuesta)
        if direccion_valida(correo):
            # Una aclaración explícita reemplaza cualquier dirección anterior; la evidencia conserva el original.
            for anterior in direcciones_en_texto(original):
                original = original.replace(anterior, "[dirección reemplazada]")
            return original + ". El destinatario del correo es " + correo
        return None
    if faltan == ["hora"]:
        return original.split(ACLARACION_HORA, 1)[0] + ACLARACION_HORA + respuesta
    if faltan == ["fecha"]:
        from datetime import date
        if fechas.resolver_fecha(respuesta, (hoy or date.today())) and not fechas.resolver_fecha(original, (hoy or date.today())):
            return original + ". " + respuesta
    return None
