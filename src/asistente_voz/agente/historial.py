"""Historial local y estado de conversaciones, sin dependencias de interfaz ni modelos."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def ahora():
    return datetime.now().astimezone().isoformat()


def codificar(valor):
    return json.dumps(valor, ensure_ascii=False)


class Historial:
    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.conexion() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sesiones (
                    id TEXT PRIMARY KEY, creada TEXT NOT NULL, configuracion TEXT NOT NULL,
                    estado TEXT NOT NULL, cerrada INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS eventos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sesion_id TEXT NOT NULL REFERENCES sesiones(id),
                    momento TEXT NOT NULL, tipo TEXT NOT NULL, datos TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS propuestas (
                    id TEXT PRIMARY KEY, sesion_id TEXT NOT NULL REFERENCES sesiones(id),
                    estado TEXT NOT NULL, datos TEXT NOT NULL, resultado TEXT);
                CREATE INDEX IF NOT EXISTS eventos_sesion ON eventos(sesion_id, id);
            ''')

    @contextmanager
    def conexion(self):
        db = sqlite3.connect(self.ruta, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def crear(self, configuracion, sesion_id=None):
        identificador = sesion_id or uuid4().hex
        with self.conexion() as db:
            db.execute("INSERT INTO sesiones(id, creada, configuracion, estado) VALUES (?, ?, ?, ?)",
                       (identificador, ahora(), codificar(configuracion), "{}"))
            self.anotar(db, identificador, "sesion_creada", configuracion)
        return identificador

    @staticmethod
    def cargar(db, sesion_id):
        fila = db.execute("SELECT * FROM sesiones WHERE id=?", (sesion_id,)).fetchone()
        if fila is None:
            raise ValueError("La conversación no existe.")
        return {**dict(fila), "configuracion": json.loads(fila["configuracion"]),
                "estado": json.loads(fila["estado"])}

    @staticmethod
    def anotar(db, sesion_id, tipo, datos):
        db.execute("INSERT INTO eventos(sesion_id, momento, tipo, datos) VALUES (?, ?, ?, ?)",
                   (sesion_id, ahora(), tipo, codificar(datos)))

    @staticmethod
    def guardar(db, sesion_id, estado):
        db.execute("UPDATE sesiones SET estado=? WHERE id=?", (codificar(estado), sesion_id))

    def listar(self):
        with self.conexion() as db:
            return [dict(f) for f in db.execute("SELECT id, creada, cerrada FROM sesiones ORDER BY creada DESC")]

    def resumir(self):
        with self.conexion() as db:
            filas = db.execute("""
                SELECT s.id, s.creada, s.configuracion,
                    (SELECT e.datos FROM eventos e WHERE e.sesion_id=s.id
                     AND e.tipo='usuario' ORDER BY e.id LIMIT 1) AS primero
                FROM sesiones s ORDER BY s.creada DESC
            """)
            return [{"id": f["id"], "creada": f["creada"],
                     "modo": json.loads(f["configuracion"])["modo"],
                     "titulo": json.loads(f["primero"])["texto"][:70] if f["primero"] else "Nueva conversación"}
                    for f in filas]

    def eventos(self, sesion_id):
        with self.conexion() as db:
            self.cargar(db, sesion_id)
            return [{**dict(f), "datos": json.loads(f["datos"])} for f in db.execute(
                "SELECT * FROM eventos WHERE sesion_id=? ORDER BY id", (sesion_id,))]

    def sesion(self, sesion_id):
        with self.conexion() as db:
            return self.cargar(db, sesion_id)

    def estado(self, sesion_id):
        return self.sesion(sesion_id)["estado"]
