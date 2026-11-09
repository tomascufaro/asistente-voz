"""Fecha y hora de una orden, resueltas en código: el modelo de 1.5B las inventa o las reescribe."""
import calendar
import re
import unicodedata
from datetime import date, timedelta

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
NUMEROS = {"una": 1, "uno": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
           "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12}
_NUMERO = rf"\d{{1,2}}|{'|'.join(NUMEROS)}"
_HORA = re.compile(
    rf"\ba (?:la|las) ({_NUMERO})(?::(\d{{2}}))?(?: (y media|y cuarto|menos cuarto|en punto))?"
    r"(?: (?:de la|por la|a la) (manana|tarde|noche))?\b"
)
_RELATIVA = re.compile(rf"\b(?:en|dentro de) ({_NUMERO}) (dias?|semanas?|meses|mes)\b")
_PERIODO_MANANA = re.compile(r"\b(?:de la|por la|a la|en la) manana\b")


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def _sumar_meses(dia: date, meses: int) -> date:
    indice = dia.year * 12 + dia.month - 1 + meses
    anio, mes = divmod(indice, 12)
    return date(anio, mes + 1, min(dia.day, calendar.monthrange(anio, mes + 1)[1]))


def _fecha_relativa(t: str, hoy: date) -> date | None:
    """«en dos semanas», «a fin de mes», «el mes que viene», «la semana que viene», «el 25»."""
    relativa = _RELATIVA.search(t)
    if relativa:
        n = int(relativa[1]) if relativa[1].isdigit() else NUMEROS[relativa[1]]
        unidad = relativa[2]
        if unidad.startswith("dia"):
            return hoy + timedelta(days=n)
        return hoy + timedelta(weeks=n) if unidad.startswith("semana") else _sumar_meses(hoy, n)
    if re.search(r"\bfin de mes\b", t):
        return hoy.replace(day=calendar.monthrange(hoy.year, hoy.month)[1])
    if re.search(r"\b(?:proximo mes|mes que viene|mes proximo)\b", t):
        return _sumar_meses(hoy, 1).replace(day=1)  # sin día en la orden: el primero (se confirma igual)
    if re.search(r"\b(?:semana que viene|semana proxima|proxima semana)\b", t):
        lunes = hoy + timedelta(days=7 - hoy.weekday())
        for numero, dia in enumerate(DIAS):
            if re.search(rf"\b{dia}\b", t):
                return lunes + timedelta(days=numero)
        return lunes
    return None


def resolver_fecha(texto: str, hoy: date) -> str | None:
    """Busca el día en la orden («mañana», «el jueves», «el 15 de noviembre», AAAA-MM-DD).

    Un día de la semana es siempre la próxima ocurrencia posterior a hoy; «el mes que viene» sin día es el primero. Devuelve None si no
    hay día o no se puede resolver: se pide aclaración en vez de adivinar.
    """
    t = _PERIODO_MANANA.sub("", _normalizar(texto))  # «por la mañana» es hora, no el día siguiente
    iso = re.search(r"\b\d{4}-\d{2}-\d{2}\b", t)
    if iso:
        try:
            return date.fromisoformat(iso[0]).isoformat()
        except ValueError:
            return None
    numerica = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", t)
    if numerica:
        dia, mes = map(int, numerica.group(1, 2))
        anios = [int(numerica[3])] if numerica[3] else [hoy.year, hoy.year + 1]
        for anio in anios:
            try:
                candidata = date(anio, mes, dia)
            except ValueError:
                return None
            if numerica[3] or candidata >= hoy:
                return candidata.isoformat()
    explicita = re.search(rf"\b(\d{{1,2}}) de ({'|'.join(MESES)})(?: de(?:l)? (\d{{4}}))?\b", t)
    if explicita:
        dia, mes = int(explicita[1]), MESES.index(explicita[2]) + 1
        for anio in ([int(explicita[3])] if explicita[3] else [hoy.year, hoy.year + 1]):
            try:
                candidata = date(anio, mes, dia)
            except ValueError:
                return None
            if explicita[3] or candidata >= hoy:
                return candidata.isoformat()
    relativa = _fecha_relativa(t, hoy)
    if relativa:
        return relativa.isoformat()
    if re.search(r"\bpasado manana\b", t):
        return (hoy + timedelta(days=2)).isoformat()
    if re.search(r"\bmanana\b", t):
        return (hoy + timedelta(days=1)).isoformat()
    if re.search(r"\bhoy\b", t):
        return hoy.isoformat()
    for numero, dia in enumerate(DIAS):
        if re.search(rf"\b{dia}\b", t):
            return (hoy + timedelta(days=(numero - hoy.weekday() - 1) % 7 + 1)).isoformat()
    dia_suelto = re.search(r"\bel (?:dia )?(\d{1,2})\b(?! de)", t)  # «el 25»: el próximo 25 que venga
    if dia_suelto:
        for meses in (0, 1, 2):
            mes = _sumar_meses(hoy.replace(day=1), meses)
            try:
                candidata = mes.replace(day=int(dia_suelto[1]))
            except ValueError:
                continue
            if candidata > hoy:
                return candidata.isoformat()
    return None


def resolver_hora(texto: str) -> str | None:
    """Busca la hora en la orden y devuelve HH:MM, o None si la orden no dice ninguna.

    Levanta ValueError si dice una hora pero no se puede resolver: «a las cuatro» sin
    tarde/mañana (1 a 6) es ambigua, y «a las 25» es inválida. Se pide aclaración.
    """
    t = _normalizar(texto)
    if re.search(r"\bmediodia\b", t):
        return "12:00"
    coincidencia = _HORA.search(t)
    if not coincidencia:
        return None
    numero, minutos, ajuste, periodo = coincidencia.groups()
    hora = int(numero) if numero.isdigit() else NUMEROS[numero]
    minuto = int(minutos) if minutos else 0
    if ajuste == "y media":
        minuto = 30
    elif ajuste == "y cuarto":
        minuto = 15
    elif ajuste == "menos cuarto":
        hora, minuto = hora - 1, 45
    if periodo in ("tarde", "noche") and hora < 12:
        hora += 12
    elif periodo == "noche" and hora == 12:
        hora = 0
    elif periodo is None and 1 <= hora <= 6:
        raise ValueError(f"hora ambigua: {coincidencia[0]}")
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        raise ValueError(f"hora inválida: {coincidencia[0]}")
    return f"{hora:02d}:{minuto:02d}"


def resolver_evento(titulo: str, texto: str, hoy: date) -> tuple[dict, list[str]]:
    """Arma ({'titulo', 'fecha', 'hora'}, faltan) desde la orden.

    Obligatorios: título y fecha. La hora es opcional ("" si no se dijo), pero si se dijo
    y es ambigua o inválida figura en `faltan`.
    """
    faltan = []
    fecha = resolver_fecha(texto, hoy) or ""
    try:
        hora = resolver_hora(texto) or ""
    except ValueError:
        hora = ""
        faltan.append("hora")
    if not titulo.strip():
        faltan.insert(0, "titulo")
    if not fecha:
        faltan.insert(1 if "titulo" in faltan else 0, "fecha")
    return {"titulo": titulo.strip(), "fecha": fecha, "hora": hora}, faltan
