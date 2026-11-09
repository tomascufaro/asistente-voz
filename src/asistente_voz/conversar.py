"""Terminal de texto para sesiones del agente: cada respuesta mantiene abierta la conversación."""
import argparse
import json
from pathlib import Path

from asistente_voz.agente.conversacion import MotorConversacion


def mostrar(entrada):
    if entrada.get("resultado") == "propuesta_pendiente":
        args = entrada["args"]
        if "asunto" in args:
            print(f"Para: {args['destinatario']}\nAsunto: {args['asunto']}\n\n{args['cuerpo']}")
        else:
            print(f"Propuesta: {args['titulo']} — {args['fecha']} {args['hora'] or '(sin hora)'}")
        print(f"Propuesta {entrada['propuesta_id']}. /confirmar guarda; /rechazar descarta. Podés corregir antes.")
    else:
        print(entrada["mensaje"])


def main(voz=False):
    ap = argparse.ArgumentParser()
    ap.add_argument("--modo", choices=["prueba", "aplicacion"], help="aplicacion por defecto; prueba solo para verificación aislada")
    ap.add_argument("--carpeta", type=Path, default=Path("data/conversaciones"))
    ap.add_argument("--sesion", help="reabrir explícitamente esta conversación")
    ap.add_argument("--listar", action="store_true", help="listar sesiones guardadas y salir")
    ap.add_argument("--perfil", choices=["pasteleria_casera", "generico"], help="pasteleria_casera por defecto en sesiones nuevas")
    if voz:
        from asistente_voz.asr import MODELO_PREDETERMINADO
        ap.add_argument("--captura", choices=["manual", "automatica"], default="manual")
        ap.add_argument("--whisper", choices=["tiny", "base", "small", MODELO_PREDETERMINADO], default=MODELO_PREDETERMINADO)
        ap.add_argument("--audio", type=Path, help="transcribir un archivo en el primer turno")
    args = ap.parse_args()
    motor = MotorConversacion(carpeta=args.carpeta)
    if args.listar:
        print(json.dumps(motor.historial.listar(), ensure_ascii=False, indent=2))
        return
    sesion = args.sesion or motor.crear(args.modo or "aplicacion", args.perfil or "pasteleria_casera")
    # Una sesión reabierta conserva su modo y rutas originales.
    detalle = motor.historial.sesion(sesion)
    configuracion = detalle["configuracion"]
    if args.sesion and args.modo and args.modo != configuracion["modo"]:
        ap.error("La sesión conserva su modo original. No se puede cambiar al reabrirla.")
    from asistente_voz.perfiles import de_sesion
    perfil = de_sesion(configuracion)
    if args.sesion and args.perfil and args.perfil != perfil["id"]:
        ap.error("La sesión conserva su perfil original; creá otra para cambiarlo.")
    estado = detalle["estado"]
    print(f"Modo: {configuracion['modo']}. Agenda: {configuracion['agenda']}. Borradores: {configuracion['correos']}")
    print(f"Perfil: {perfil['nombre']} · {perfil['sha256'][:12]} · CPU")
    print(f"Conversación: {sesion}. Historial: {motor.historial.ruta}")
    print("Escribí un pedido. /confirmar | /rechazar | /cancelar | /nueva | /historial | /salir")
    if estado.get("propuesta_id"):
        print("Hay una propuesta pendiente. Consultá /historial para revisarla antes de confirmar.")
    revisada = None  # reabrir no confirma ni da por revisada una propuesta anterior
    while True:
        try:
            estado = motor.historial.estado(sesion)
            destinatario = None
            origen, metadatos = "texto", None
            if "destinatario" in estado.get("pedido_activo", {}).get("faltan", []):
                texto = input("Correo (solo teclado; /cancelar para cancelar) > ").strip()
                if texto and not texto.startswith("/"):
                    destinatario = texto
                    texto = "Dirección ingresada por teclado."
            else:
                texto = "" if voz and args.audio else input("Vos (Enter: voz) > " if voz else "Vos > ").strip()
                if voz and not texto:
                    from asistente_voz.escuchar import capturar_turno
                    capturado = capturar_turno(motor, sesion, args)
                    if not capturado:
                        print("Sin texto aceptado; podés grabar otra vez o escribir.")
                        continue
                    texto, metadatos = capturado
                    origen = "voz"
        except (EOFError, KeyboardInterrupt):
            print("\nConversación guardada.")
            return
        if not texto:
            continue
        if texto == "/salir":
            print("Conversación guardada.")
            return
        if texto == "/nueva":
            motor.mensaje(sesion, "cancelar")
            sesion = motor.crear(configuracion["modo"], perfil["id"])
            revisada = None
            print(f"Nueva conversación: {sesion}")
            continue
        if texto == "/historial":
            for evento in motor.historial.eventos(sesion):
                if evento["tipo"] == "usuario":
                    print("Vos:", evento["datos"]["texto"])
                elif evento["tipo"] == "respuesta":
                    if evento["datos"].get("resultado") == "propuesta_pendiente":
                        print("Propuesta histórica:", json.dumps(evento["datos"]["args"], ensure_ascii=False))
                    else:
                        mostrar(evento["datos"])
            # Mostrar explícitamente solo la propuesta vigente, nunca una invalidada del historial.
            estado = motor.historial.estado(sesion)
            propuesta = estado.get("propuesta_id")
            if propuesta:
                print("Propuesta vigente:")
                mostrar({"resultado": "propuesta_pendiente", "args": estado["pedido_activo"]["args"],
                         "propuesta_id": propuesta})
            revisada = propuesta
            continue
        if texto in ("/confirmar", "/rechazar"):
            propuesta = motor.historial.estado(sesion).get("propuesta_id")
            if not propuesta or propuesta != revisada:
                print("No hay una propuesta revisada vigente. Usá /historial o repetí el pedido.")
                continue
            mostrar(motor.confirmar(sesion, propuesta, texto == "/confirmar"))
            revisada = None
            continue
        if texto.startswith("/correo "):
            destinatario = texto.removeprefix("/correo ").strip()
            texto = "Dirección ingresada por teclado."
        try:
            entrada = motor.mensaje(sesion, "cancelar" if texto == "/cancelar" else texto,
                                     origen=origen, metadatos=metadatos, destinatario=destinatario)
        except ValueError as error:
            print(str(error))
            continue
        mostrar(entrada)
        revisada = entrada.get("propuesta_id")


if __name__ == "__main__":
    main()
