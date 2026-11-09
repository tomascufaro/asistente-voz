"""Interfaz y API del agente en localhost. Arranque: python -m asistente_voz.web."""
import argparse
import base64
import binascii
import threading
import time
import hashlib
from uuid import uuid4
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from asistente_voz.agente.conversacion import MotorConversacion
from asistente_voz import perfiles

ESTATICOS = Path(__file__).with_name("interfaz")
RUTA_SESION = re.compile(r"/api/sesiones/([a-f0-9]{32})(?:/(mensajes|confirmaciones|cancelar|audio|agenda|documentos|mediciones))?")
ARCHIVOS = {"/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_CUERPO = 40000


def campos(datos, permitidos):
    if not isinstance(datos, dict) or set(datos) - set(permitidos):
        raise ValueError("Los campos de la solicitud no son válidos.")


def descripcion(args):
    if "asunto" in args:
        return f"Para: {args['destinatario']}\nAsunto: {args['asunto']}\n\n{args['cuerpo']}"
    return f"{args['titulo']}\nFecha: {args['fecha']}\nHora: {args['hora'] or 'sin hora'}"


class AplicacionWeb:
    def __init__(self, motor, transcribir=None):
        self.motor = motor
        self.transcribir = transcribir
        self.cerrojo_asr = threading.Lock()

    def audio(self, identificador, datos):
        campos(datos, {"audio", "tipo"})
        tipos = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4"}
        tipo = datos.get("tipo", "")
        if not isinstance(tipo, str):
            raise ValueError("El formato de audio no es válido.")
        tipo = tipo.split(";")[0]
        if tipo not in tipos or not isinstance(datos.get("audio"), str):
            raise ValueError("Formato de audio no admitido.")
        try:
            contenido = base64.b64decode(datos["audio"], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("El audio no es válido.") from error
        if not 0 < len(contenido) <= 4_000_000:
            raise ValueError("El audio está vacío o es demasiado grande.")
        intento = uuid4().hex
        carpeta = self.motor.carpeta / identificador / "voz" / intento
        carpeta.mkdir(parents=True)
        archivo = carpeta / ("audio." + tipos[tipo])
        archivo.write_bytes(contenido)
        from asistente_voz.asr import transcribe, load_model, VOCABULARIO, MODELO_PREDETERMINADO, MODELO_LATAM, REVISION_LATAM
        evidencia = {"modelo_asr": MODELO_PREDETERMINADO, "vocabulario_asr": None if MODELO_PREDETERMINADO == MODELO_LATAM else VOCABULARIO,
                     "revision_asr": REVISION_LATAM if MODELO_PREDETERMINADO == MODELO_LATAM else None,
                     "idioma_asr": "es", "beam_size": 5, "vad_filter": True, "origen": "voz", "audio": archivo.name,
                     "audio_sha256": hashlib.sha256(contenido).hexdigest(), "estado": "audio_guardado"}
        registro = carpeta / "intento.json"
        registro.write_text(json.dumps(evidencia, ensure_ascii=False), encoding="utf-8")
        espera = time.perf_counter()
        try:
            # Una sola inferencia ASR a la vez en el equipo de 8 GB.
            with self.cerrojo_asr:
                inicio = time.perf_counter()
                evidencia["cola_asr_s"] = inicio - espera
                if self.transcribir is None:
                    load_model(MODELO_PREDETERMINADO)
                    evidencia["carga_asr_s"] = time.perf_counter() - inicio
                    inicio = time.perf_counter()
                texto = (self.transcribir or transcribe)(archivo, size=MODELO_PREDETERMINADO)
                evidencia.update(asr_s=time.perf_counter()-inicio, transcripcion_original=texto, estado="transcripto")
        except Exception as error:
            evidencia.update(estado="error_asr", error_tipo=type(error).__name__, error=str(error))
            raise
        finally:
            registro.write_text(json.dumps(evidencia, ensure_ascii=False), encoding="utf-8")
        return {"audio_id": intento, "texto": texto}


    def sesion(self, identificador):
        try:
            sesion = self.motor.historial.sesion(identificador)
        except ValueError as error:
            raise KeyError("La conversación no existe.") from error
        mensajes = []
        tiempo_tarea_s = None
        for evento in self.motor.historial.eventos(identificador):
            datos = evento["datos"]
            if evento["tipo"] == "medicion_tarea":
                tiempo_tarea_s = datos["segundos"]
            elif evento["tipo"] == "usuario":
                mensajes.append({"id": evento["id"], "rol": "usuario", "texto": datos["texto"], "momento": evento["momento"]})
            elif evento["tipo"] == "respuesta":
                texto = descripcion(datos["args"]) if datos["resultado"] == "propuesta_pendiente" else datos["mensaje"]
                mensajes.append({"id": evento["id"], "rol": "asistente", "texto": texto,
                                 "tipo": datos["resultado"], "momento": evento["momento"]})
        estado = sesion["estado"]
        propuesta = None
        if estado.get("propuesta_id"):
            propuesta = {"id": estado["propuesta_id"], "tool": estado["pedido_activo"]["tool"],
                         "args": estado["pedido_activo"]["args"]}
        return {"id": identificador, "modo": sesion["configuracion"]["modo"],
                "modelo": sesion["configuracion"]["modelo"],
                "mensajes": mensajes, "propuesta": propuesta,
                "tiempo_tarea_s": tiempo_tarea_s,
                "perfil": {k: perfiles.de_sesion(sesion["configuracion"])[k] for k in ("id", "nombre", "version", "sha256")},
                "requiere_destinatario": "destinatario" in estado.get("pedido_activo", {}).get("faltan", []),
                "bloqueada": bool(estado.get("ejecucion_incierta"))}

    def despachar(self, metodo, ruta, datos=None):
        if ruta == "/api/perfiles" and metodo == "GET":
            return {"perfiles": [{"id": k, "nombre": v} for k, v in perfiles.PERFILES.items()]}
        if ruta == "/api/sesiones":
            if metodo == "GET":
                return {"sesiones": self.motor.historial.resumir()}
            if metodo != "POST":
                raise KeyError("La ruta no existe.")
            campos(datos, {"modo", "perfil"})
            identificador = self.motor.crear(datos.get("modo", "aplicacion"), datos.get("perfil", "pasteleria_casera"))
            return self.sesion(identificador)
        coincidencia = RUTA_SESION.fullmatch(ruta)
        if not coincidencia:
            raise KeyError("La ruta no existe.")
        identificador, accion = coincidencia.groups()
        self.sesion(identificador)
        if metodo == "GET" and accion in ("agenda", "documentos"):
            configuracion = self.motor.historial.sesion(identificador)["configuracion"]
            if accion == "agenda":
                from asistente_voz.agente.herramientas import TOOLS
                return TOOLS["ver_eventos"].ejecutar(
                    {"fecha": "", "alcance": "proximos"}, self.motor._entorno(configuracion))
            carpeta = Path(configuracion["documentos"])
            return {"documentos": [{"nombre": archivo.name, "texto": archivo.read_text(encoding="utf-8")}
                                   for archivo in sorted(carpeta.glob("*.txt")) if archivo.is_file() and not archivo.is_symlink()]}
        if metodo == "GET" and accion is None:
            return self.sesion(identificador)
        if metodo != "POST":
            raise KeyError("La ruta no existe.")
        if accion == "mensajes":
            campos(datos, {"texto", "audio_id", "destinatario"})
            metadatos = None
            if "audio_id" in datos:
                audio_id = datos["audio_id"]
                if not isinstance(audio_id, str) or not re.fullmatch(r"[a-f0-9]{32}", audio_id):
                    raise ValueError("El identificador de audio no es válido.")
                archivo = self.motor.carpeta / identificador / "voz" / audio_id / "intento.json"
                if not archivo.is_file():
                    raise ValueError("El audio no pertenece a esta conversación.")
                metadatos = json.loads(archivo.read_text(encoding="utf-8"))
                metadatos.update(audio_id=audio_id, transcripcion_revisada=datos.get("texto"))
                archivo.write_text(json.dumps(metadatos, ensure_ascii=False), encoding="utf-8")
            respuesta = self.motor.mensaje(identificador, datos.get("texto"),
                                           origen="voz" if metadatos else "texto", metadatos=metadatos,
                                           destinatario=datos.get("destinatario"))
        elif accion == "mediciones":
            campos(datos, {"duracion_ms"})
            duracion = datos.get("duracion_ms")
            if isinstance(duracion, bool) or not isinstance(duracion, (int, float)) or not 0 <= duracion <= 86_400_000:
                raise ValueError("La duración de la tarea no es válida.")
            with self.motor.historial.conexion() as db:
                db.execute("BEGIN IMMEDIATE")
                self.motor.historial.cargar(db, identificador)
                segundos = round(duracion / 1000, 2)
                self.motor.historial.anotar(db, identificador, "medicion_tarea",
                                            {"duracion_ms": round(duracion), "segundos": segundos})
            respuesta = {"resultado": "medicion_guardada"}
        elif accion == "audio":
            return self.audio(identificador, datos)
        elif accion == "confirmaciones":
            campos(datos, {"propuesta_id", "aceptar"})
            if not isinstance(datos.get("propuesta_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", datos["propuesta_id"]):
                raise ValueError("La propuesta no es válida.")
            respuesta = self.motor.confirmar(identificador, datos["propuesta_id"], datos.get("aceptar"))
        elif accion == "cancelar":
            campos(datos, set())
            respuesta = self.motor.mensaje(identificador, "cancelar")
        else:
            raise KeyError("La ruta no existe.")
        return {"respuesta": respuesta["resultado"], "sesion": self.sesion(identificador)}


class Manejador(BaseHTTPRequestHandler):
    def responder(self, estado, cuerpo, tipo="application/json; charset=utf-8"):
        if isinstance(cuerpo, dict):
            cuerpo = json.dumps(cuerpo, ensure_ascii=False).encode()
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(cuerpo)

    def local(self):
        puerto = self.server.server_address[1]
        hosts = {f"127.0.0.1:{puerto}", f"localhost:{puerto}"}
        origen = self.headers.get("Origin")
        navegacion = (self.command == "GET" and self.path == "/"
                      and self.headers.get("Sec-Fetch-Mode") == "navigate")
        return (self.headers.get("Host", "").lower() in hosts
                and (not origen or origen.lower() in {"http://" + h for h in hosts})
                and (self.headers.get("Sec-Fetch-Site") != "cross-site" or navegacion))

    def ejecutar(self, metodo):
        if not self.local():
            self.responder(403, {"error": "Solo se permiten solicitudes desde la interfaz local."})
            return
        ruta = urlsplit(self.path).path
        try:
            if metodo == "GET" and ruta in ARCHIVOS:
                nombre, tipo = ARCHIVOS[ruta]
                self.responder(200, (ESTATICOS / nombre).read_bytes(), tipo)
                return
            datos = None
            if metodo == "POST":
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    self.responder(415, {"error": "Se requiere JSON."})
                    return
                longitud = int(self.headers.get("Content-Length", "0"))
                limite = 5_400_000 if ruta.endswith("/audio") else MAX_CUERPO
                if not 0 < longitud <= limite:
                    self.responder(413, {"error": "La solicitud está vacía o excede el tamaño permitido."})
                    return
                cuerpo = self.rfile.read(longitud)
                if len(cuerpo) != longitud:
                    raise ValueError("La solicitud llegó incompleta.")
                datos = json.loads(cuerpo.decode("utf-8"))
            self.responder(200, self.server.aplicacion.despachar(metodo, ruta, datos))
        except KeyError as error:
            self.responder(404, {"error": str(error.args[0])})
        except (ValueError, TypeError, UnicodeError) as error:
            self.responder(400, {"error": str(error) if not isinstance(error, json.JSONDecodeError) else "El JSON no es válido."})
        except Exception:
            self.responder(500, {"error": "No se pudo completar la solicitud. Recargá la conversación antes de reintentar."})

    def do_GET(self):
        self.ejecutar("GET")

    def do_POST(self):
        self.ejecutar("POST")

    def do_OPTIONS(self):
        self.responder(403, {"error": "No se habilita acceso desde otros sitios."})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--carpeta", type=Path, default=Path("data/conversaciones"))
    args = ap.parse_args()
    if not 1024 <= args.puerto <= 65535:
        ap.error("El puerto debe estar entre 1024 y 65535.")
    aplicacion = AplicacionWeb(MotorConversacion(carpeta=args.carpeta))
    try:
        servidor = ThreadingHTTPServer((args.host, args.puerto), Manejador)
    except OSError as error:
        ap.exit(1, f"No se pudo abrir el servidor local: {error}\n")
    servidor.aplicacion = aplicacion
    print(f"Asistente local: http://127.0.0.1:{args.puerto}\nAgenda local compartida entre conversaciones. Ctrl+C para terminar.", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
