"""Interpretación de órdenes: texto -> intención y parámetros, con un modelo local (Ollama)."""
import json

import ollama

DEFAULT_MODEL = "qwen2.5:3b"

INTENTS = ["crear_evento", "ver_eventos", "redactar_correo", "buscar_documento", "ninguna"]

SCHEMA = {
    "type": "object",
    "properties": {"intencion": {"type": "string", "enum": INTENTS}},
    "required": ["intencion"],
}

SYSTEM = """Sos un asistente que clasifica órdenes en español rioplatense. Elegí UNA acción.

crear_evento: la persona quiere que algo quede anotado en la agenda para un día (con hora o sin hora). \
Incluye reuniones, turnos, recordatorios y cosas por hacer. \
Ejemplos: "Agendá reunión con el proveedor el jueves a las tres", "Recordame comprar tornillos mañana", \
"Anotá que el viernes tengo que pagar el alquiler".
ver_eventos: la persona pregunta qué tiene anotado en la agenda. \
Ejemplos: "¿Qué tengo mañana?", "Mostrame la agenda del viernes".
redactar_correo: la persona quiere un borrador de mail. NO es agenda. \
Ejemplos: "Escribile un mail al cliente avisando que el presupuesto está listo".
buscar_documento: la persona pregunta un dato que está en sus documentos (listas de precios, facturas, notas). \
Ejemplos: "¿Cuánto cuesta la pintura blanca en la lista de precios?".
ninguna: cualquier otra cosa, o una orden que no pide agendar, ver la agenda, un mail ni un dato de documentos. \
Ejemplos: "¿Qué hora es?", "Poné música", "Hola, ¿cómo estás?". Si dudás entre una acción y esta, elegí ninguna.

Devolvé solo JSON."""


def interpret(text: str, model: str = DEFAULT_MODEL, cpu_only: bool = True, system: str = SYSTEM) -> dict:
    """Devuelve {'intencion': ...} para una orden en texto; es una de INTENTS.

    cpu_only=True fuerza el modelo a CPU (en Mac, Ollama usa la GPU de Apple por defecto).
    """
    response = ollama.chat(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
        format=SCHEMA,
        options={"temperature": 0, **({"num_gpu": 0} if cpu_only else {})},
    )
    return json.loads(response.message.content)


SCHEMA_EVENTO = {
    "type": "object",
    "properties": {"titulo": {"type": "string"}},
    "required": ["titulo"],
}

SYSTEM_EVENTO = (
    "De una orden de agenda en español rioplatense, extraé solo el título del evento: de qué se "
    "trata, en pocas palabras, sin el día ni la hora. Ejemplo: «Agendá una reunión con el "
    "proveedor el jueves a las tres» -> «Reunión con el proveedor»."
)


def extract_event(text: str, model: str = DEFAULT_MODEL, cpu_only: bool = True) -> dict:
    """Devuelve {'titulo': ...}; la fecha y la hora las resuelve fechas.py, no el modelo."""
    response = ollama.chat(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_EVENTO}, {"role": "user", "content": text}],
        format=SCHEMA_EVENTO,
        options={"temperature": 0, **({"num_gpu": 0} if cpu_only else {})},
    )
    return json.loads(response.message.content)


SCHEMA_CORREO = {
    "type": "object",
    "properties": {
        "destinatario": {"type": "string"},
        "tema": {"type": "string"},
        "asunto": {"type": "string"},
        "cuerpo": {"type": "string"},
    },
    "required": ["destinatario", "tema", "asunto", "cuerpo"],
}

SYSTEM_CORREO = (
    "De una orden en español rioplatense para escribir un mail, devolvé: destinatario (a quién va, "
    "copiando las palabras de la orden, sin agregar ninguna), tema (de qué trata, en pocas palabras), asunto (corto) y "
    "cuerpo (borrador breve y cordial, con saludo y firma «Saludos», sin inventar datos como "
    "cantidades, precios ni fechas que la orden no diga). Si la orden no dice el destinatario o "
    "el tema, dejá ese campo vacío y no lo inventes. Ejemplo: «Escribile al proveedor pidiendo "
    "presupuesto de harina» -> destinatario «el proveedor», tema «presupuesto de harina»."
)


def draft_email(text: str, model: str = DEFAULT_MODEL, cpu_only: bool = True) -> dict:
    """Devuelve {'destinatario', 'tema', 'asunto', 'cuerpo'}; destinatario o tema vacíos si la orden no los dice."""
    response = ollama.chat(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_CORREO}, {"role": "user", "content": text}],
        format=SCHEMA_CORREO,
        options={"temperature": 0, **({"num_gpu": 0} if cpu_only else {})},
    )
    return json.loads(response.message.content)
