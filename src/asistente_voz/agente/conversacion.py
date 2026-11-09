"""Motor conversacional local. Preparar, aclarar y confirmar son operaciones separadas."""
import functools
import json
import re
import time
from datetime import date
from pathlib import Path
from uuid import uuid4

from asistente_voz import fechas
from asistente_voz.aclaraciones import completar_aclaracion
from asistente_voz.agente import bucle
from asistente_voz.agente.herramientas import TOOLS, Entorno, sin_sustento, consulta_agenda
from asistente_voz.agente.historial import Historial, codificar
from asistente_voz.nlu import DEFAULT_MODEL
from asistente_voz.correos import direccion_valida
from asistente_voz import perfiles

# Continuidad acotada: no se reutilizan datos de tareas completadas para autorizar escrituras.
_REPREGUNTA = re.compile(r"^(?:y\b|el de\b|la de\b|en ese\b|en el mismo\b)")
_NUEVA_ACCION = re.compile(r"\b(?:agenda\w*|anota\w*|recorda\w*|busca\w*|consulta\w*|mostra\w*|decime|precio\w*|cuanto|que tengo|escrib\w*|redact\w*|correo|mail|borrador)\b")
MAX_ACLARACIONES = 3
MAX_PEDIDO = 12000
MAX_CONSULTA = 2000

_CORRECCION = re.compile(r"^(?:mejor|cambia\w*|corregi\w*)\b")


def _normal(texto):
    return fechas._normalizar(texto).strip(" .!¿?¡")


class MotorConversacion:
    def __init__(self, carpeta=Path("data/conversaciones"), documentos=Path("data/documentos"),
                 elegir=None, modelo=DEFAULT_MODEL, hoy=None, max_segundos=bucle.MAX_SEGUNDOS, datos=Path("data")):
        self.carpeta = Path(carpeta).resolve()
        self.documentos = Path(documentos).resolve()
        self.datos = Path(datos).resolve()
        self.historial = Historial(self.carpeta / "historial.sqlite3")
        if not 0 < max_segundos <= 300:
            raise ValueError("El tiempo máximo debe estar entre 0 y 300 segundos.")
        self.max_segundos = max_segundos
        self.elegir = elegir or functools.partial(bucle.elegir, modelo=modelo, cpu_only=True, timeout=max_segundos)
        self.modelo = modelo
        self.hoy = hoy  # reloj fijo solo para evaluación; por defecto se consulta en cada turno

    def crear(self, modo="aplicacion", perfil="pasteleria_casera"):
        if modo not in ("prueba", "aplicacion"):
            raise ValueError("Modo no válido.")
        perfil = perfiles.cargar(perfil)
        identificador = uuid4().hex
        datos = self.carpeta / "pruebas" / identificador if modo == "prueba" else self.datos
        configuracion = {"modo": modo, "modelo": self.modelo, "perfil": perfil, "cpu_only": True, "documentos": str(self.documentos),
                         "agenda": str(datos / "agenda.jsonl"), "correos": str(datos / "correos.jsonl")}
        return self.historial.crear(configuracion, identificador)

    def _entorno(self, configuracion, hoy=None):
        return Entorno(hoy or self.hoy or date.today(), Path(configuracion["agenda"]),
                       Path(configuracion["correos"]), Path(configuracion["documentos"]))

    def _respuesta(self, db, sesion_id, estado, resultado):
        if "mensaje" not in resultado:
            resultado["mensaje"] = bucle.mensaje(resultado)
        self.historial.anotar(db, sesion_id, "respuesta", resultado)
        self.historial.guardar(db, sesion_id, estado)
        return {"sesion_id": sesion_id, **resultado}

    def _invalidar(self, db, sesion_id, estado):
        propuesta = estado.pop("propuesta_id", None)
        if propuesta:
            db.execute("UPDATE propuestas SET estado='invalidada' WHERE id=? AND estado='pendiente'", (propuesta,))
            self.historial.anotar(db, sesion_id, "propuesta_invalidada", {"propuesta_id": propuesta})

    def mensaje(self, sesion_id, texto, origen="texto", metadatos=None, destinatario=None):
        if not isinstance(texto, str) or not texto.strip() or len(texto) > 8000:
            raise ValueError("Ingresá un mensaje de 1 a 8000 caracteres.")
        if origen not in ("texto", "voz"):
            raise ValueError("El origen debe ser texto o voz.")
        if destinatario is not None and (origen != "texto" or not isinstance(destinatario, str) or not direccion_valida(destinatario)):
            raise ValueError("Ingresá una dirección válida solo por teclado en el campo de correo.")
        with self.historial.conexion() as db:
            db.execute("BEGIN IMMEDIATE")
            sesion = self.historial.cargar(db, sesion_id)
            if sesion["cerrada"]:
                raise ValueError("La conversación está cerrada; creá una nueva.")
            if sesion["configuracion"]["modelo"] != self.modelo:
                raise ValueError("Reabrí la conversación con el modelo original o creá otra.")
            estado = sesion["estado"]
            perfil = perfiles.de_sesion(sesion["configuracion"])
            self.historial.anotar(db, sesion_id, "usuario", {"texto": texto, "origen": origen,
                                                         "metadatos": metadatos or {},
                                                         "destinatario_teclado": destinatario})
            if estado.get("ejecucion_incierta"):
                return self._respuesta(db, sesion_id, estado, {"resultado": "respondido",
                    "texto": "Hay una ejecución pendiente de revisión. Revisá el historial y los archivos antes de continuar."})
            estado.pop("turno_en_curso", None)
            if _normal(texto) in ("cancelar", "cancelar pedido", "reformular"):
                self._invalidar(db, sesion_id, estado)
                estado.clear()
                return self._respuesta(db, sesion_id, estado, {"resultado": "respondido",
                    "texto": "Pedido cancelado. Podés empezar otro en esta conversación."})
            if _normal(texto) in ("si", "confirmar", "guardar", "s"):
                return self._respuesta(db, sesion_id, estado, {"resultado": "respondido",
                    "texto": "Confirmá la propuesta vigente con su identificador; no se guardó ninguna acción."})
            activo = estado.get("pedido_activo")
            ultima = estado.get("ultima_busqueda")
            normal = _normal(texto)
            es_nueva = (bool(_NUEVA_ACCION.search(normal)) or consulta_agenda(texto)) and not _CORRECCION.search(normal)
            if _REPREGUNTA.search(normal) and not re.search(r"\b(mail|correo|borrador|agenda\w*|anota\w*)\b", normal):
                es_nueva = False
            self._invalidar(db, sesion_id, estado)
            contexto, permitidas, trabajo, evidencia = None, None, texto, None
            hoy = self.hoy or date.today()
            correo_teclado = destinatario
            if activo and activo.get("tool") == "redactar_correo" and not es_nueva:
                correo_teclado = destinatario or activo.get("destinatario_teclado")
            if destinatario and activo and activo.get("tool") == "redactar_correo" and not es_nueva:
                trabajo = activo["texto"]
                es_nueva = False
                permitidas = {"redactar_correo", "pedir_aclaracion", "responder"}
            elif activo and not es_nueva:
                if activo.get("rondas", 0) >= MAX_ACLARACIONES:
                    estado.pop("pedido_activo", None)
                    return self._respuesta(db, sesion_id, estado, {"resultado": "respondido",
                        "texto": "Se alcanzó el límite de aclaraciones. Repetí el pedido completo; no se guardó la acción pendiente."})
                if activo.get("tool") == "redactar_correo" and (
                        "destinatario" in activo.get("faltan", []) or _CORRECCION.search(normal)):
                    activo["faltan"] = ["destinatario"]
                    activo["rondas"] = activo.get("rondas", 0) + 1
                    return self._respuesta(db, sesion_id, estado, {"resultado": "aclaracion_necesaria",
                        "faltan": ["destinatario"], "pregunta": bucle.pregunta_aclaracion(["destinatario"])})
                if "consulta" in activo.get("faltan", []):
                    contexto = activo.get("contexto_busqueda") or {"pedido_pendiente": activo["texto"],
                                                                  "ultima_busqueda": ultima}
                    contexto = {**contexto, "pregunta_anterior": activo.get("pregunta", ""),
                                "regla": "Interpretá la respuesta sobre la consulta pendiente. Ambos pide todos los productos; solo uno nombra cuál conservar. No ejecutes otras acciones."}
                    evidencia = texto + "\n" + contexto["pedido_pendiente"]
                    if contexto.get("ultima_busqueda"):
                        evidencia += "\n" + contexto["ultima_busqueda"]["consulta"] + "\n" + contexto["ultima_busqueda"]["documento"]
                    trabajo = texto
                    permitidas = {"buscar_documento", "pedir_aclaracion", "responder"}
                else:
                    trabajo = self._continuar(activo, texto, hoy)
                if trabajo is None:
                    activo["rondas"] = activo.get("rondas", 0) + 1
                    return self._respuesta(db, sesion_id, estado, {"resultado": "aclaracion_necesaria",
                        "faltan": activo.get("faltan", []),
                        "pregunta": "No pude completar o corregir ese dato. Reformulá el pedido completo o cancelá."})
                if contexto is None:
                    permitidas = {activo["tool"], "pedir_aclaracion", "responder"} if activo.get("tool") else None
            elif _REPREGUNTA.search(normal) and not es_nueva:
                if not ultima:
                    return self._respuesta(db, sesion_id, estado, {"resultado": "aclaracion_necesaria", "faltan": [],
                        "pregunta": "¿Sobre qué documento o producto preguntás? Repetí la consulta completa."})
                # Solo metadatos: el contenido recuperado de documentos no vuelve al modelo.
                contexto = {"ultima_busqueda": ultima,
                            "regla": "La nueva pregunta continúa esa consulta. Conservá el documento si no se nombra otro; devolvé consulta completa reemplazando el producto o atributo que cambió. No conserves sabores anteriores si se pide otro."}
                evidencia = texto + "\n" + ultima["consulta"]
                if ultima["documento"]:
                    evidencia += "\n" + ultima["documento"]
                permitidas = {"buscar_documento", "pedir_aclaracion", "responder"}
            else:
                estado.pop("pedido_activo", None)
                estado.pop("ultima_busqueda", None)
            if len(trabajo) > MAX_PEDIDO:
                estado.pop("pedido_activo", None)
                return self._respuesta(db, sesion_id, estado, {"resultado": "respondido",
                    "texto": "El pedido acumulado es demasiado largo. Reformulalo de forma más breve."})
            entorno = self._entorno(sesion["configuracion"], hoy)
            turno = uuid4().hex
            vence = time.time() + self.max_segundos
            estado["turno_en_curso"] = {"id": turno, "vence": vence}
            self.historial.guardar(db, sesion_id, estado)
        # La inferencia no mantiene una transacción SQLite abierta.
        error_modelo = None
        try:
            entrada = bucle.procesar(trabajo, lambda a: False, elegir=self.elegir, hoy=hoy,
                agenda=entorno.agenda, correos=entorno.correos, documentos=entorno.documentos,
                registro=self.carpeta / sesion_id / "agente.jsonl", preparar=True,
                contexto=contexto, solo_tools=permitidas, texto_validacion=evidencia,
                destinatario_teclado=correo_teclado, perfil=perfil,
                max_segundos=max(0, vence - time.time()))
        except (Exception, KeyboardInterrupt) as error:
            error_modelo = {"tipo": type(error).__name__, "detalle": str(error)}
            entrada = {"resultado": "respondido", "texto":
                "Se interrumpió el procesamiento; no se guardó ninguna acción." if isinstance(error, KeyboardInterrupt) else
                "No pude procesar el mensaje. Revisá Ollama y la configuración; el error quedó registrado."}
        with self.historial.conexion() as db:
            db.execute("BEGIN IMMEDIATE")
            vigente = self.historial.cargar(db, sesion_id)["estado"]
            if vigente.get("turno_en_curso", {}).get("id") != turno:
                self.historial.anotar(db, sesion_id, "respuesta_descartada", {"turno": turno})
                return {"sesion_id": sesion_id, "resultado": "respondido",
                        "mensaje": "El pedido cambió o se canceló. Se descartó la respuesta anterior."}
            estado.pop("turno_en_curso", None)
            if time.time() > vence:
                entrada = {"resultado": "respondido", "forzada": "tiempo",
                           "texto": "Se agotó el tiempo del pedido. Podés volver a intentar; no se guardó ninguna acción."}
            if error_modelo:
                self.historial.anotar(db, sesion_id, "error", error_modelo)
            self.historial.anotar(db, sesion_id, "agente", entrada)
            if entrada["resultado"] == "propuesta_pendiente":
                propuesta_id = uuid4().hex
                if entrada["tool"] == "redactar_correo":
                    trabajo = entrada["texto"]
                datos = {"tool": entrada["tool"], "args": entrada["args"], "texto": trabajo, "hoy": hoy.isoformat(),
                         "destinatario_teclado": correo_teclado}
                db.execute("INSERT INTO propuestas(id, sesion_id, estado, datos) VALUES (?, ?, 'pendiente', ?)",
                           (propuesta_id, sesion_id, codificar(datos)))
                estado["propuesta_id"] = propuesta_id
                estado["pedido_activo"] = {**datos, "faltan": [], "rondas": 0}
                estado.pop("ultima_busqueda", None)
                entrada["propuesta_id"] = propuesta_id
            elif entrada["resultado"] == "aclaracion_necesaria":
                tool = activo.get("tool") if activo and not es_nueva else None
                # Las trazas conservan la acción que motivó la validación, si la hubo.
                for paso in entrada.get("traza", []):
                    if paso.get("tool") in ("crear_evento", "redactar_correo", "ver_eventos", "buscar_documento"):
                        tool = paso["tool"]
                if "consulta" in entrada.get("faltan", []):
                    tool = "buscar_documento"
                if "destinatario" in entrada.get("faltan", []):
                    tool = "redactar_correo"
                estado["pedido_activo"] = {"texto": trabajo, "tool": tool, "destinatario_teclado": correo_teclado, "faltan": entrada.get("faltan", []),
                    "rondas": activo.get("rondas", 0) + 1 if activo and not es_nueva else 0}
                if "consulta" in entrada.get("faltan", []):
                    estado["pedido_activo"].update(pregunta=entrada["pregunta"], contexto_busqueda={
                        "pedido_pendiente": (contexto or {}).get("pedido_pendiente", trabajo),
                        "ultima_busqueda": (contexto or {}).get("ultima_busqueda", ultima if not es_nueva else None)})
            else:
                estado.pop("pedido_activo", None)
                if entrada.get("tool") == "buscar_documento" and entrada["resultado"] != "documento_ambiguo":
                    archivos = {h["documento"] for h in entrada.get("hallazgos", [])}
                    documento = entrada["args"].get("documento", "")
                    if len(archivos) == 1:
                        documento = next(iter(archivos)).removesuffix(".txt").replace("_", " ")
                    if len(entrada["args"]["consulta"]) <= MAX_CONSULTA and len(documento) <= MAX_CONSULTA:
                        estado["ultima_busqueda"] = {"consulta": entrada["args"]["consulta"], "documento": documento}
                    else:
                        estado.pop("ultima_busqueda", None)
                        self.historial.anotar(db, sesion_id, "contexto_omitido", {"motivo": "consulta demasiado larga"})
                else:
                    estado.pop("ultima_busqueda", None)
            return self._respuesta(db, sesion_id, estado, entrada)

    def _continuar(self, activo, texto, hoy):
        if "destinatario" in activo.get("faltan", []):
            return None
        if activo.get("faltan"):
            return completar_aclaracion(activo["texto"], texto, activo["faltan"], hoy=hoy)
        if not _CORRECCION.search(_normal(texto)):
            return None
        args = activo.get("args", {})
        if activo.get("tool") == "crear_evento":
            # Reemplaza fecha/hora en vez de concatenar datos contradictorios.
            hora = fechas._HORA.search(fechas._normalizar(texto))
            nueva_fecha = fechas.resolver_fecha(texto, hoy)
            if hora or nueva_fecha:
                fecha = nueva_fecha or args["fecha"]
                horario = texto[hora.start():hora.end()] if hora else ""
                if not hora and args.get("hora"):
                    horario = "a las " + args["hora"] + (" de la mañana" if int(args["hora"][:2]) < 12 else "")
                return f"Agendá {args['titulo']} el {fecha} {horario}"
        return None

    def confirmar(self, sesion_id, propuesta_id, aceptar):
        if not isinstance(aceptar, bool):
            raise ValueError("La decisión debe ser un booleano explícito.")
        with self.historial.conexion() as db:
            db.execute("BEGIN IMMEDIATE")
            sesion = self.historial.cargar(db, sesion_id)
            estado = sesion["estado"]
            fila = db.execute("SELECT * FROM propuestas WHERE id=? AND sesion_id=?", (propuesta_id, sesion_id)).fetchone()
            if not fila or fila["estado"] != "pendiente" or estado.get("propuesta_id") != propuesta_id or sesion["cerrada"]:
                self.historial.anotar(db, sesion_id, "confirmacion_rechazada", {"propuesta_id": propuesta_id})
                return self._respuesta(db, sesion_id, estado, {"resultado": "respondido",
                    "texto": "La propuesta no está vigente. No se ejecutó ninguna acción."})
            self.historial.anotar(db, sesion_id, "confirmacion", {"propuesta_id": propuesta_id, "aceptar": aceptar})
            datos = json.loads(fila["datos"])
            entorno = self._entorno(sesion["configuracion"])
            if datos["hoy"] != entorno.hoy.isoformat():
                self._invalidar(db, sesion_id, estado)
                estado.pop("pedido_activo", None)
                return self._respuesta(db, sesion_id, estado, {"resultado": "respondido",
                    "texto": "Cambió el día desde la propuesta. Repetí el pedido para revisar la fecha antes de guardar."})
            if not aceptar:
                db.execute("UPDATE propuestas SET estado='rechazada' WHERE id=?", (propuesta_id,))
                estado.pop("pedido_activo", None)
                estado.pop("propuesta_id", None)
                return self._respuesta(db, sesion_id, estado, {"resultado": "rechazado"})
            tool = TOOLS[datos["tool"]]
            limpios, faltan = tool.validar(datos["args"], datos["texto"], entorno.hoy)
            if (datos["tool"] == "redactar_correo" and datos.get("destinatario_teclado") != limpios.get("destinatario")) or faltan or limpios != datos["args"] or sin_sustento(datos["tool"], limpios, datos["texto"]):
                self._invalidar(db, sesion_id, estado)
                return self._respuesta(db, sesion_id, estado, {"resultado": "respondido",
                    "texto": "La propuesta ya no pasa la validación. Repetí el pedido."})
            # Reservar antes de tocar JSONL: tras un corte no se reintenta automáticamente una escritura incierta.
            db.execute("UPDATE propuestas SET estado='ejecutando' WHERE id=?", (propuesta_id,))
            estado.pop("propuesta_id", None)
            estado.pop("pedido_activo", None)
            estado["ejecucion_incierta"] = propuesta_id
            self.historial.guardar(db, sesion_id, estado)
            db.commit()
            try:
                resultado = tool.ejecutar(limpios, entorno)
            except (Exception, KeyboardInterrupt) as error:
                self.historial.anotar(db, sesion_id, "error_ejecucion", {"propuesta_id": propuesta_id,
                    "tipo": type(error).__name__, "detalle": str(error)})
                return self._respuesta(db, sesion_id, estado, {"resultado": "respondido",
                    "texto": "No se pudo completar la escritura. Revisá el historial y los archivos; no se reintentará automáticamente."})
            db.execute("UPDATE propuestas SET estado='ejecutada', resultado=? WHERE id=?", (codificar(resultado), propuesta_id))
            estado.pop("ejecucion_incierta", None)
            if resultado["resultado"] == "borrador_guardado":
                resultado["mensaje"] = f"Borrador guardado en {entorno.correos}. No se envió nada."
            return self._respuesta(db, sesion_id, estado, {**resultado, "propuesta_id": propuesta_id})
