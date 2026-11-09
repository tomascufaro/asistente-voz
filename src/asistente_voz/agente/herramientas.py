"""Registro central de tools: descripción, esquema de argumentos, validador, función y bandera de efecto.

Agregar una acción es agregar una entrada a TOOLS. Ninguna tool envía nada: el correo es solo un borrador.
"""
import json
import re
import threading
from dataclasses import dataclass
from datetime import date, timedelta
import calendar
from pathlib import Path
from typing import Callable

from asistente_voz import fechas
from asistente_voz.aclaraciones import ACLARACION_HORA
from asistente_voz.correos import direcciones_en_texto, direccion_valida

AGENDA = Path("data/agenda.jsonl")
CORREOS = Path("data/correos.jsonl")
DOCUMENTOS = Path("data/documentos")
_ESCRITURA_AGENDA = threading.Lock()

CAMPOS = ["titulo", "fecha", "hora", "destinatario", "consulta"]  # lo que puede faltar en una orden


@dataclass(frozen=True)
class Entorno:
    """Lo que las tools tocan fuera del modelo: el día de hoy y dónde se guarda."""
    hoy: date
    agenda: Path = AGENDA
    correos: Path = CORREOS
    documentos: Path = DOCUMENTOS


def _append(path: Path, entrada: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")


def _eventos_del_dia(agenda: Path, fecha: str) -> list[dict]:
    if not agenda.exists():
        return []
    eventos = [json.loads(linea) for linea in agenda.read_text(encoding="utf-8").splitlines()]
    return sorted((e for e in eventos if e["fecha"] == fecha), key=lambda e: e["hora"])


# Palabras de una consulta que piden un dato pero no lo identifican («cuánto cuesta», «en la lista»).
_GENERICAS = {"cuanto", "cuesta", "cuestan", "sale", "cobrar", "cobrarle", "precio", "precios", "costo", "costos",
              "lista", "receta", "recetas", "planilla", "catalogo", "documento", "documentos", "buscar", "busca",
              "buscame", "fijate", "dato", "datos", "ingredientes", "lleva", "tengo", "dice", "segun", "para",
              "como", "esta", "este", "sobre", "donde", "cual", "cuales", "entre", "tenga"}


# Indicios léxicos de que la orden pide esa acción (verbos y objetos propios de la tool). El 1.5B elige una tool
# de acción ante órdenes de otra cosa («Cancelá la reunión», «Mandale un WhatsApp»): sin indicios, el harness
# contesta en vez de ejecutar o preguntar por datos de una acción que nadie pidió.
_INDICIOS = {
    "crear_evento": re.compile(r"\b(?:(?<!la )(?<!mi )(?:agend|ajend)|anot|apunt|record|reserv|carg|pon[eé]|ponel|avis|olvid|program|tengo que|hay que)"),
    "redactar_correo": re.compile(r"\b((?:e-?)?mail|correo|borrador|redact|escrib|carta)"),
}
_TOOL_DEL_CAMPO = {"titulo": ["crear_evento"], "hora": ["crear_evento"], "fecha": ["crear_evento", "ver_eventos"],
                   "destinatario": ["redactar_correo"], "consulta": ["buscar_documento"]}
_INYECCION = re.compile(r"\b(ignor|olvid)\w* (?:\w+ ){0,3}(instruccion|indicacion|regla|anterior)")


def sin_sustento(nombre: str, args: dict, texto: str) -> bool:
    """True si la tool elegida (o la acción por la que pregunta pedir_aclaracion) no tiene indicios en la orden."""
    t = fechas._normalizar(texto)
    if _INYECCION.search(t):
        return True
    if nombre == "pedir_aclaracion":
        faltan = args.get("faltan") or []
        campos = [c for c in faltan if c != "fecha"] or faltan  # «fecha» no distingue entre crear y ver
        herramientas = [h for campo in campos for h in _TOOL_DEL_CAMPO.get(campo, [])]
        return bool(herramientas) and not any(_con_indicios(h, t) for h in herramientas)
    return not _con_indicios(nombre, t)


def _con_indicios(nombre: str, t: str) -> bool:
    patron = _INDICIOS.get(nombre)
    return patron is None or bool(patron.search(t))  # ver_eventos y buscar_documento se validan por su resultado


def _raiz(palabra: str) -> str:
    return palabra[:max(4, len(palabra) - 2)]  # «alfajores» y «alfajor» comparten raíz


def buscar_en_documentos(consulta: str, documento: str, carpeta: Path) -> dict:
    """Extrae de los documentos las líneas que contienen TODAS las palabras distintivas de la consulta.

    No genera texto: lo que devuelve está escrito en el documento. Si algo de la consulta no aparece,
    es «no_encontrado» (mejor eso que contestar con un dato de otra cosa). La línea se busca junto con el
    título del documento (primera línea), que da contexto («Receta: alfajores…»). Las palabras genéricas
    («costo», «precio») no se exigen, pero ordenan: primero las líneas que también las tienen.
    """
    palabras = [w for w in fechas._normalizar(consulta).replace("?", " ").split() if len(w) >= 4]
    raices = [_raiz(w) for w in palabras if w not in _GENERICAS]
    genericas = [_raiz(w) for w in palabras if w in _GENERICAS]
    archivos = {fechas._normalizar(a.stem.replace("_", " ")): a for a in sorted(carpeta.glob("*.txt"))
                if a.is_file() and not a.is_symlink()}
    if documento:
        palabras_doc = [_raiz(w) for w in fechas._normalizar(documento).replace(".txt", "").replace("_", " ").split()
                        if len(w) >= 4]
        candidatos = {n: a for n, a in archivos.items() if palabras_doc and all(p in n for p in palabras_doc)}
        if not candidatos:
            return {"resultado": "no_encontrado", "archivos_consultados": [], "documento_solicitado": documento}
        if len(candidatos) > 1:
            return {"resultado": "documento_ambiguo", "opciones": [a.name for a in candidatos.values()]}
        archivos = candidatos
    hallazgos = []
    for archivo in archivos.values():
        lineas = [linea for linea in archivo.read_text(encoding="utf-8").splitlines() if linea.strip()]
        titulo = fechas._normalizar(lineas[0]) if lineas else ""
        for linea in lineas[1:]:
            t = fechas._normalizar(linea)
            if raices and all(r in t + " " + titulo for r in raices):
                hallazgos.append((-sum(g in t for g in genericas), archivo.name, linea))
    if not hallazgos:
        return {"resultado": "no_encontrado", "archivos_consultados": [a.name for a in archivos.values()]}
    hallazgos.sort(key=lambda h: h[0])
    return {"resultado": "respuesta_documento", "ambiguo": len(hallazgos) > 1,
            "archivos_consultados": [a.name for a in archivos.values()],
            "total_coincidencias": len(hallazgos), "hallazgos": [{"documento": nombre, "linea": linea} for _, nombre, linea in hallazgos[:3]]}


def anclado(valor: str, texto: str) -> bool:
    """El modelo de 1.5B inventa datos («Proveedor de Harina»): un texto libre solo vale si está en la orden."""
    palabras = fechas._normalizar(valor).split()
    if palabras and palabras[0] in ("el", "la", "los", "las"):
        palabras = palabras[1:]
    return bool(palabras) and " ".join(palabras) in fechas._normalizar(texto)


def anclado_parcial(valor: str, texto: str) -> bool:
    """Título/tema: todas las palabras distintivas y cifras deben tener sustento en el pedido."""
    orden = fechas._normalizar(texto)
    if not set(re.findall(r"\d+", valor)) <= set(re.findall(r"\d+", texto)):
        return False
    genericas = {"reunion", "evento", "recordatorio", "presupuesto", "solicitud", "correo", "mail", "pedido"}
    palabras = [p for p in fechas._normalizar(valor).split() if len(p) >= 4 and p not in genericas]
    return all(p[:5] in orden for p in palabras) if palabras else anclado(valor, texto)


def _sin_preposicion(destinatario: str) -> str:
    """«al proveedor» -> «el proveedor», «a Lucía» -> «Lucía»: el modelo copia la preposición de la orden."""
    palabras = destinatario.split()
    if palabras and fechas._normalizar(palabras[0]) == "al":
        palabras[0] = "el"
    elif palabras and fechas._normalizar(palabras[0]) in ("a", "para"):
        palabras = palabras[1:]
    return " ".join(palabras)


def _literal(args: dict, campo: str) -> str:
    valor = args.get(campo)
    return valor.strip() if isinstance(valor, str) else ""


def _fecha(args: dict, texto: str, hoy: date) -> str:
    """Fecha de la orden: el modelo copia el texto («el jueves») y fechas.py lo resuelve. "" si no sirve."""
    literal = _literal(args, "fecha")
    texto = texto.split(ACLARACION_HORA, 1)[0]
    return ((fechas.resolver_fecha(literal, hoy) or "") if anclado(literal, texto) else "") or fechas.resolver_fecha(texto, hoy) or ""


def _hora(args: dict, texto: str) -> str:
    """Hora opcional. Levanta ValueError si es ambigua o inválida; si el modelo no la copió, se mira la orden."""
    literal = _literal(args, "hora")
    if ACLARACION_HORA in texto:
        original, respuesta = texto.split(ACLARACION_HORA, 1)
        try:
            explicita = fechas.resolver_hora(respuesta)
        except ValueError:
            explicita = None
        if explicita:
            return explicita
        hora = literal if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", literal) else fechas.resolver_hora(literal)
        anterior = fechas._HORA.search(fechas._normalizar(original))
        if hora and anterior:
            base = fechas.resolver_hora(anterior[0] + " de la mañana")
            if base and (int(hora[:2]) % 12, hora[3:]) == (int(base[:2]) % 12, base[3:]):
                return hora
        raise ValueError("No pude resolver la hora con la aclaración.")
    hora = fechas.resolver_hora(literal) if anclado(literal, texto) else None
    return hora or fechas.resolver_hora(texto) or ""


def _validar_evento(args: dict, texto: str, hoy: date) -> tuple[dict, list[str]]:
    faltan = []
    titulo = _literal(args, "titulo")
    if not titulo or not anclado_parcial(titulo, texto):
        titulo = ""
        faltan.append("titulo")
    fecha = _fecha(args, texto, hoy)
    if not fecha:
        faltan.append("fecha")
    try:
        hora = _hora(args, texto)
    except ValueError:
        hora = ""
        faltan.append("hora")
    return {"titulo": titulo, "fecha": fecha, "hora": hora}, faltan


def consulta_agenda(texto: str) -> bool:
    """Reconoce consultas explícitas simples; no toma pedidos de escritura o compuestos."""
    t = fechas._normalizar(texto)
    return (bool(re.search(r"\b(agenda|reuniones|eventos|pendientes)\b", t))
            and bool(re.search(r"\b(ver|mostra\w*|consulta\w*|proxim\w*|que tengo)\b", t))
            and not re.search(r"\b(agendar|agenda una|anota\w*|crea\w*|escrib\w*|redact\w*|borra\w*|elimina\w*)\b", t))


def _rango_agenda(texto: str, hoy: date):
    t = fechas._normalizar(texto)
    if "entre " in t:
        extremos = re.split(r"\s+y\s+", t.split("entre ", 1)[1], maxsplit=1)
        if len(extremos) != 2:
            raise ValueError("Indicá las dos fechas del intervalo.")
        dias = [fechas.resolver_fecha(e, hoy) for e in extremos]
        if not all(dias) or dias[0] > dias[1]:
            raise ValueError("Indicá un intervalo válido, por ejemplo entre 2026-11-10 y 2026-11-15.")
        return tuple(dias)
    if "semana" in t and not any(re.search(rf"\b{d}\b", t) for d in fechas.DIAS):
        lunes = hoy - timedelta(days=hoy.weekday())
        if re.search(r"semana (?:que viene|proxima)|proxima semana", t):
            lunes += timedelta(days=7)
        elif re.search(r"semana (?:pasada|anterior)", t):
            lunes -= timedelta(days=7)
        elif not re.search(r"esta semana|semana actual", t):
            raise ValueError("¿Esta semana, la próxima o la pasada?")
        return lunes.isoformat(), (lunes + timedelta(days=6)).isoformat()
    if "mes" in t and not re.search(r"\b\d{1,2} de\b", t):
        inicio = hoy.replace(day=1)
        if re.search(r"mes (?:que viene|proximo)|proximo mes", t):
            inicio = fechas._sumar_meses(inicio, 1)
        elif re.search(r"mes (?:pasado|anterior)", t):
            inicio = fechas._sumar_meses(inicio, -1)
        elif not re.search(r"este mes|mes actual", t):
            raise ValueError("¿Este mes, el próximo o el pasado?")
        return inicio.isoformat(), inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1]).isoformat()
    return None


def _validar_ver(args: dict, texto: str, hoy: date) -> tuple[dict, list[str]]:
    t = fechas._normalizar(texto)
    numeros = {"una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
               "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}
    cantidad = re.search(r"\bproxim[oa]s?\s+(\d+|" + "|".join(numeros) + r")\b", t)
    limite = (numeros.get(cantidad[1]) or int(cantidad[1])) if cantidad else None
    if limite is not None and not 1 <= limite <= 100:
        raise ValueError("Pedí entre 1 y 100 eventos próximos.")
    reuniones = bool(re.search(r"\breuniones?\b", t))
    rango = _rango_agenda(texto, hoy)
    fecha = "" if rango else _fecha(args, texto, hoy)
    if not rango and re.search(r"\b(?:pasado|anterior)\b", t):
        for numero, dia in enumerate(fechas.DIAS):
            if re.search(rf"\b{dia}\b", t):
                fecha = (hoy - timedelta(days=(hoy.weekday() - numero - 1) % 7 + 1)).isoformat()
    if not rango and re.search(r"\bayer\b", t):
        fecha = (hoy - timedelta(days=1)).isoformat()
    if not rango and not fecha and any(re.search(rf"\b{mes}\b", t) for mes in fechas.MESES):
        return {"fecha": ""}, ["fecha"]
    if not rango and not fecha and re.search(r"\b(dia|lunes|martes|miercoles|jueves|viernes|sabado|domingo|semana|mes|manana|hoy)\b|\d{4}-", t):
        return {"fecha": ""}, ["fecha"]
    if fecha and not reuniones and limite is None:
        return {"fecha": fecha}, []
    pasados = bool(re.search(r"\b(pasad[oa]s?|anteriores|historico|historial)\b", t))
    incluir = bool(re.search(r"\b(inclu\w*|tambien|historial|historico)\b", t))
    alcance = "todos" if pasados and incluir else "pasados" if pasados else "proximos"
    limpios = {"fecha": fecha, "alcance": alcance, "limite": limite, "solo_reuniones": reuniones}
    if rango:
        limpios.update(desde=rango[0], hasta=rango[1])
    return limpios, []


def _validar_correo(args: dict, texto: str, hoy: date) -> tuple[dict, list[str]]:
    borrador = {campo: _literal(args, campo) for campo in ("destinatario", "asunto", "cuerpo")}
    borrador["destinatario"] = _sin_preposicion(borrador["destinatario"])
    direcciones = direcciones_en_texto(texto)
    if (not direccion_valida(borrador["destinatario"])
            or direcciones != [borrador["destinatario"]]):
        borrador["destinatario"] = ""
    contenido = borrador["asunto"] + " " + borrador["cuerpo"]
    numeros = set(re.findall(r"\d+(?:[.,:]\d+)*", contenido))
    referencias = set(re.findall(r"\d+(?:[.,:]\d+)*", texto))
    if (not numeros <= referencias or any(d != borrador["destinatario"] for d in direcciones_en_texto(contenido))
            or len(borrador["cuerpo"]) > 4000 or len(borrador["asunto"]) > 200
            or re.search(r"\[(?:tu nombre|nombre|firma)\]", contenido, re.IGNORECASE)):
        borrador["cuerpo"] = ""
    nombre = _literal(args, "nombre_destinatario")
    borrador["nombre_destinatario"] = nombre if nombre and anclado(nombre, texto) else ""
    faltan = [campo for campo in ("destinatario",) if not borrador[campo]]
    return borrador, faltan or [c for c in ("asunto", "cuerpo") if not borrador[c]]


def _validar_documento(args: dict, texto: str, hoy: date) -> tuple[dict, list[str]]:
    documento = _literal(args, "documento")
    consulta = _literal(args, "consulta")
    if "consultas" in args:
        consultas = args["consultas"]
        if (not isinstance(consultas, list) or not 1 <= len(consultas) <= 8
                or any(not isinstance(c, str) or not c.strip() or len(c) > 2000 for c in consultas)):
            return {"consulta": "", "documento": documento}, ["consulta"]
        if consulta and consulta not in consultas:
            consultas = [consulta, *consultas]
        if len(consultas) > 8:
            return {"consulta": "", "documento": documento}, ["consulta"]
        consultas = list(dict.fromkeys(c.strip() for c in consultas))
        return {"consulta": "; ".join(consultas), "consultas": consultas, "documento": documento}, []
    return {"consulta": consulta, "documento": documento}, ([] if consulta else ["consulta"])


def _validar_aclaracion(args: dict, texto: str, hoy: date) -> tuple[dict, list[str]]:
    faltan = [c for c in args.get("faltan") or [] if c in CAMPOS]
    pregunta = _literal(args, "pregunta") or f"Me falta: {', '.join(faltan)}. ¿Me lo decís?"
    return {"faltan": faltan, "pregunta": pregunta}, ([] if faltan else ["faltan"])


def _validar_respuesta(args: dict, texto: str, hoy: date) -> tuple[dict, list[str]]:
    respuesta = _literal(args, "texto")
    return {"texto": respuesta}, ([] if respuesta else ["texto"])


def _crear_evento(args: dict, entorno: Entorno) -> dict:
    with _ESCRITURA_AGENDA:
        eventos = [json.loads(linea) for linea in entorno.agenda.read_text(encoding="utf-8").splitlines()
                   if linea.strip()] if entorno.agenda.exists() else []
        if any(clave_evento(e) == clave_evento(args) for e in eventos):
            return {"resultado": "evento_duplicado"}
        _append(entorno.agenda, args)
    return {"resultado": "agendado"}


def clave_evento(evento: dict) -> tuple:
    return (" ".join(fechas._normalizar(evento["titulo"]).split()), evento["fecha"], evento.get("hora") or "")


def _ver_eventos(args: dict, entorno: Entorno) -> dict:
    eventos = [json.loads(linea) for linea in entorno.agenda.read_text(encoding="utf-8").splitlines()
               if linea.strip()] if entorno.agenda.exists() else []
    if args.get("fecha"):
        eventos = [e for e in eventos if e["fecha"] == args["fecha"]]
    elif args.get("desde"):
        eventos = [e for e in eventos if args["desde"] <= e["fecha"] <= args["hasta"]]
    elif args.get("alcance") == "pasados":
        eventos = [e for e in eventos if e["fecha"] and e["fecha"] < entorno.hoy.isoformat()]
    elif args.get("alcance") != "todos":
        eventos = [e for e in eventos if not e["fecha"] or e["fecha"] >= entorno.hoy.isoformat()]
    if args.get("solo_reuniones"):
        eventos = [e for e in eventos if "reunion" in fechas._normalizar(e["titulo"])]
    eventos.sort(key=lambda e: (not bool(e["fecha"]), e["fecha"], e.get("hora") or "00:00", e["titulo"]))
    if args.get("limite"):
        eventos = [e for e in eventos if e["fecha"]][:args["limite"]]
    return {"resultado": "listado", **args, "eventos": eventos}


def _redactar_correo(args: dict, entorno: Entorno) -> dict:
    _append(entorno.correos, {campo: args[campo] for campo in ("destinatario", "nombre_destinatario", "asunto", "cuerpo")})
    return {"resultado": "borrador_guardado"}


def _buscar_documento(args: dict, entorno: Entorno) -> dict:
    # Solo lectura. El texto del documento es dato no confiable: va al usuario, nunca de vuelta al modelo.
    if args.get("consultas"):
        resultados = [{"consulta": consulta, **buscar_en_documentos(consulta, args["documento"], entorno.documentos)}
                      for consulta in args["consultas"]]
        return {"resultado": "respuesta_documento", "resultados_consultas": resultados,
                "hallazgos": [h for r in resultados for h in r.get("hallazgos", [])],
                "archivos_consultados": sorted({a for r in resultados for a in r.get("archivos_consultados", [])})}
    return buscar_en_documentos(args["consulta"], args["documento"], entorno.documentos)


def _pedir_aclaracion(args: dict, entorno: Entorno) -> dict:
    return {"resultado": "aclaracion_necesaria", "faltan": args["faltan"], "pregunta": args["pregunta"]}


def _responder(args: dict, entorno: Entorno) -> dict:
    return {"resultado": "respondido", "texto": args["texto"]}


def _esquema(propiedades: dict, requeridos: list[str]) -> dict:
    return {"type": "object", "properties": propiedades, "required": requeridos}


_TEXTO = {"type": "string"}


@dataclass(frozen=True)
class Tool:
    descripcion: str  # va al prompt del modelo
    esquema: dict  # argumentos que el modelo puede devolver (JSON Schema)
    validar: Callable[[dict, str, date], tuple[dict, list[str]]]  # (args, orden, hoy) -> (args limpios, faltan)
    ejecutar: Callable[[dict, Entorno], dict]
    efecto: bool  # escribe algo: el harness exige confirmación antes de ejecutar


TOOLS: dict[str, Tool] = {
    "crear_evento": Tool(
        "anotar en la agenda algo para un día (reunión, turno, recordatorio, tarea). Args: titulo (de qué se trata, "
        "sin día ni hora); fecha (el día, copiado textual de la orden: «el jueves», «mañana», «el 3 de noviembre»); "
        "hora (opcional: «a las tres de la tarde»; combiná pedido y aclaración para devolver una hora completa; vacía si no se indica).",
        _esquema({"titulo": _TEXTO, "fecha": _TEXTO, "hora": _TEXTO}, ["titulo", "fecha"]),
        _validar_evento, _crear_evento, efecto=True),
    "ver_eventos": Tool(
        "consultar la agenda, entregas y pedidos pendientes. Sin fecha: próximos eventos desde hoy; incluir pasados requiere pedirlos explícitamente. "
        "También admite próximas cinco reuniones. Args: fecha opcional, copiada textual; vacía si no se indica.",
        _esquema({"fecha": _TEXTO}, []),
        _validar_ver, _ver_eventos, efecto=False),
    "redactar_correo": Tool(
        "preparar un borrador de mail (no se envía). Args: destinatario (dirección de correo obligatoria, "
        "ingresada por teclado, nunca inventada); nombre_destinatario (persona o empresa, opcional); asunto y cuerpo derivados del pedido y su contexto, sin pedir un tema separado ni inventar datos.",
        _esquema({"destinatario": _TEXTO, "nombre_destinatario": _TEXTO, "asunto": _TEXTO, "cuerpo": _TEXTO},
                 ["destinatario", "asunto", "cuerpo"]),
        _validar_correo, _redactar_correo, efecto=True),
    "buscar_documento": Tool(
        "buscar datos factuales en catálogos, listas de precios, costos y notas de proveedores. "
        "Args: consultas, lista con una consulta completa por producto (1 a 8). Incluso para un producto usá una lista. "
        "Cada consulta incluye el tipo de producto y atributo buscado, sin palabras de selección como solo o ambos. "
        "Pedir dos precios es una sola acción: buscá ambos, no pidas elegir. documento opcional: archivo donde buscar, "
        "sin inventar nombres ni sustituir un archivo indicado por el usuario.",
        _esquema({"consultas": {"type": "array", "items": _TEXTO, "minItems": 1, "maxItems": 8},
                  "documento": _TEXTO}, ["consultas"]),
        _validar_documento, _buscar_documento, efecto=False),
    "pedir_aclaracion": Tool(
        "preguntar cuando falta un dato obligatorio o es ambiguo: nunca adivines. Args: faltan (qué datos faltan); "
        "pregunta (lo que le preguntás a la persona).",
        _esquema({"faltan": {"type": "array", "items": {"type": "string", "enum": CAMPOS}}, "pregunta": _TEXTO},
                 ["faltan", "pregunta"]),
        _validar_aclaracion, _pedir_aclaracion, efecto=False),
    "responder": Tool(
        "contestar cuando la orden no es agendar, ver la agenda, un mail ni buscar un documento (charla, pedidos "
        "que no podés hacer). Args: texto (respuesta breve).",
        _esquema({"texto": _TEXTO}, ["texto"]),
        _validar_respuesta, _responder, efecto=False),
}
