'use strict';

const porId = (id) => document.getElementById(id);
const ui = Object.fromEntries(['sesiones', 'nueva', 'recargar', 'titulo', 'conexion',
  'bienvenida', 'mensajes', 'pensando', 'propuesta', 'error', 'formulario', 'texto', 'enviar',
  'cancelar', 'contenido', 'microfono', 'estado-voz', 'descartar-voz', 'ver-agenda', 'ver-documentos', 'cerrar-recursos', 'recursos', 'perfil', 'perfil-activo', 'campo-correo', 'destinatario'].map((id) => [id, porId(id)]));
let actual = null;
let ocupada = false;
let sesiones = [];
const borradores = new Map();
let audioPendiente = null;
let grabadora = null;
let flujo = null;
let temporizador = null;
const tareas = new Map();

async function cerrarTarea(sesion) {
  const inicio = tareas.get(sesion);
  if (inicio === undefined) return;
  tareas.delete(sesion);
  const duracion_ms = Math.round((globalThis.performance?.now?.() ?? Date.now()) - inicio);
  try {
    const medicion = await api('/api/sesiones/' + sesion + '/mediciones', {duracion_ms});
    if (actual?.id === sesion) ui['estado-voz'].textContent = `Tarea resuelta en ${medicion.segundos.toFixed(2)} s. Tiempo guardado.`;
  } catch {
    if (actual?.id === sesion) ui['estado-voz'].textContent = `Tarea resuelta en ${(duracion_ms / 1000).toFixed(2)} s; no se pudo guardar el tiempo.`;
  }
}

function tareaContinua(respuesta, sesion) {
  return respuesta === 'propuesta_pendiente'
    || (respuesta === 'aclaracion_necesaria' && sesion?.requiere_destinatario);
}
function descartarVoz() {
  audioPendiente = null;
  ui['descartar-voz'].hidden = true;
  ui['estado-voz'].textContent = '';
}


async function api(ruta, datos) {
  let respuesta;
  try {
    respuesta = await fetch(ruta, datos === undefined ? {cache: 'no-store'} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(datos)
    });
  } catch {
    throw new Error('No se pudo conectar con el servidor local. Verificá que siga abierto y actualizá la conversación antes de reintentar.');
  }
  const contenido = await respuesta.json();
  if (!respuesta.ok) throw new Error(contenido.error || 'No se pudo completar la solicitud.');
  return contenido;
}

function nodo(tipo, clase, texto) {
  const elemento = document.createElement(tipo);
  if (clase) elemento.className = clase;
  if (texto !== undefined) elemento.textContent = texto;
  return elemento;
}

function controles(valor) {
  ocupada = valor;
  document.querySelectorAll('button, select').forEach((boton) => { boton.disabled = valor; });
  ui.texto.disabled = valor;
  ui.destinatario.disabled = valor || !!actual?.bloqueada;
  ui.cancelar.disabled = valor || !actual || actual.bloqueada;
  ui.enviar.disabled = valor || !!actual?.bloqueada;
  ui.microfono.disabled = valor || !!actual?.bloqueada;
  ui.pensando.hidden = !valor;
  ui.formulario.setAttribute('aria-busy', String(valor));
}

async function ejecutar(tarea) {
  if (ocupada) return;
  ui.error.hidden = true;
  controles(true);
  try {
    await tarea();
    ui.conexion.textContent = 'Servidor local conectado · Modelo configurado: ' + (actual?.modelo || 'qwen2.5:3b');
  } catch (error) {
    ui.conexion.textContent = "La operación no pudo completarse. Podés actualizar la conversación.";
    ui.error.textContent = error.message;
    ui.error.hidden = false;
  } finally {
    controles(false);
  }
}

function listar() {
  ui.sesiones.replaceChildren();
  if (!sesiones.length) {
    ui.sesiones.append(nodo('p', 'muted small', 'Tus conversaciones van a aparecer acá.'));
  }
  for (const sesion of sesiones) {
    const boton = nodo('button', 'session' + (sesion.id === actual?.id ? ' active' : ''));
    boton.type = 'button';
    if (sesion.id === actual?.id) boton.setAttribute('aria-current', 'page');
    boton.title = sesion.titulo;
    boton.append(nodo('span', '', sesion.titulo));
    const fecha = new Date(sesion.creada).toLocaleDateString('es-AR', {day: 'numeric', month: 'short'});
    boton.append(nodo('small', '', fecha));
    boton.addEventListener('click', () => ejecutar(() => abrir(sesion.id)));
    ui.sesiones.append(boton);
  }
}

async function actualizarLista() {
  sesiones = (await api('/api/sesiones')).sesiones;
  listar();
}

function detalle(titulo, valor) {
  const fila = nodo('div', 'detail');
  fila.append(nodo('strong', '', titulo), nodo('span', '', valor));
  return fila;
}

function mostrarPropuesta() {
  ui.propuesta.replaceChildren();
  ui.propuesta.hidden = !actual?.propuesta;
  if (!actual?.propuesta) return;
  const propuesta = actual.propuesta;
  const args = propuesta.args;
  const correo = propuesta.tool === 'redactar_correo';
  ui.propuesta.append(nodo('p', 'eyebrow', 'PENDIENTE DE CONFIRMACIÓN'),
    nodo('h2', '', correo ? 'Revisá el borrador' : 'Revisá el evento'));
  if (correo) {
    ui.propuesta.append(detalle('Para', args.destinatario), detalle('Asunto', args.asunto), nodo('pre', '', args.cuerpo));
  } else {
    ui.propuesta.append(detalle('Evento', args.titulo), detalle('Fecha', args.fecha), detalle('Hora', args.hora || 'Sin hora'));
  }
  const acciones = nodo('div', 'proposal-actions');
  const aceptar = nodo('button', 'button primary', correo ? 'Guardar borrador' : 'Guardar evento');
  const rechazar = nodo('button', 'button', 'Rechazar');
  aceptar.type = rechazar.type = 'button';
  // El cierre conserva el ID que se mostró, aunque otra pestaña cambie la propuesta.
  aceptar.addEventListener('click', () => decidir(propuesta.id, true));
  rechazar.addEventListener('click', () => decidir(propuesta.id, false));
  acciones.append(aceptar, rechazar);
  ui.propuesta.append(acciones, nodo('p', 'proposal-note', correo
    ? 'Se guarda localmente. No se envía ningún correo.'
    : 'Podés escribir una corrección antes de confirmar.'));
}

function mostrar(sesion) {
  ui.recursos.hidden = true;
  ui['cerrar-recursos'].hidden = true;
  actual = sesion;
  ui['perfil-activo'].textContent = 'Perfil activo: ' + (sesion.perfil?.nombre || 'Sin especialización (sesión anterior)');
  ui['estado-voz'].textContent = sesion.tiempo_tarea_s == null ? '' : `Tarea resuelta en ${sesion.tiempo_tarea_s.toFixed(2)} s. Tiempo guardado.`;
  ui['campo-correo'].hidden = !sesion.requiere_destinatario && sesion.propuesta?.tool !== 'redactar_correo';
  const primera = sesion.mensajes.find((mensaje) => mensaje.rol === 'usuario');
  ui.titulo.textContent = primera ? primera.texto : 'Nueva conversación';
  ui.bienvenida.hidden = sesion.mensajes.length > 0;
  ui.mensajes.replaceChildren();
  for (const mensaje of sesion.mensajes) {
    const elemento = nodo('article', 'message ' + (mensaje.rol === 'usuario' ? 'user' : 'assistant'));
    elemento.append(nodo('div', 'message-label', mensaje.rol === 'usuario' ? 'Vos' : 'Asistente local'),
      nodo('p', '', mensaje.texto));
    ui.mensajes.append(elemento);
  }
  mostrarPropuesta();
  if (sesion.bloqueada) {
    ui.error.textContent = 'Esta conversación necesita revisión por una escritura incierta. Revisá el historial y los datos antes de continuar.';
    ui.error.hidden = false;
  }
  listar();
  requestAnimationFrame(() => { ui.contenido.scrollTop = ui.contenido.scrollHeight; });
}

async function abrir(id) {
  descartarVoz();
  ui.destinatario.value = '';
  if (actual) borradores.set(actual.id, ui.texto.value);
  const sesion = await api('/api/sesiones/' + id);
  mostrar(sesion);
  ui.texto.value = borradores.get(id) || '';
  history.replaceState(null, '', '#' + id);
}

async function crear() {
  descartarVoz();
  ui.destinatario.value = '';
  if (actual) borradores.set(actual.id, ui.texto.value);
  const sesion = await api('/api/sesiones', {perfil: ui.perfil.value || 'pasteleria_casera'});
  mostrar(sesion);
  ui.texto.value = '';
  history.replaceState(null, '', '#' + sesion.id);
  await actualizarLista();
}

async function decidir(id, aceptar) {
  await ejecutar(async () => {
    const resultado = await api('/api/sesiones/' + actual.id + '/confirmaciones', {propuesta_id: id, aceptar});
    mostrar(resultado.sesion);
    if (!tareaContinua(resultado.respuesta, resultado.sesion)) await cerrarTarea(actual.id);
  });
}

ui.nueva.addEventListener('click', () => ejecutar(crear));
ui.recargar.addEventListener('click', () => ejecutar(async () => {
  if (actual) await abrir(actual.id);
  await actualizarLista();
}));
ui.cancelar.addEventListener('click', () => ejecutar(async () => {
  const resultado = await api('/api/sesiones/' + actual.id + '/cancelar', {});
  mostrar(resultado.sesion);
  await cerrarTarea(actual.id);
}));
ui.formulario.addEventListener('submit', (evento) => {
  evento.preventDefault();
  const destinatario = ui['campo-correo'].hidden ? '' : ui.destinatario.value.trim();
  const texto = ui.texto.value.trim() || (destinatario ? 'Dirección ingresada por teclado.' : '');
  if (destinatario && audioPendiente) {
    ui.error.textContent = 'Enviá primero el pedido por voz. Después ingresá el correo en el campo de teclado.';
    ui.error.hidden = false;
    return;
  }
  if (!texto || ocupada || actual?.bloqueada) return;
  ejecutar(async () => {
    let enviado = false;
    try {
      if (!actual) await crear();
      const datos = {texto};
      if (destinatario) datos.destinatario = destinatario;
      if (audioPendiente?.sesion === actual.id) datos.audio_id = audioPendiente.id;
      const resultado = await api('/api/sesiones/' + actual.id + '/mensajes', datos);
      descartarVoz();
      ui.destinatario.value = '';
      enviado = true;
      borradores.delete(actual.id);
      ui.texto.value = '';
      mostrar(resultado.sesion);
      if (!tareaContinua(resultado.respuesta, resultado.sesion)) await cerrarTarea(actual.id);
      // Una falla al actualizar la lista no vuelve a enviar el mensaje ya procesado.
      await actualizarLista();
    } catch (error) {
      if (!enviado && ui.texto.value === '') ui.texto.value = texto;
      throw error;
    }
  });
});
ui.texto.addEventListener('keydown', (evento) => {
  if (evento.key === 'Enter' && !evento.shiftKey && !evento.isComposing) {
    evento.preventDefault();
    ui.formulario.requestSubmit();
  }
});
document.querySelectorAll('[data-ejemplo]').forEach((boton) => {
  boton.addEventListener('click', () => {
    ui.texto.value = boton.dataset.ejemplo;
    ui.texto.focus();
  });
});

ejecutar(async () => {
  await actualizarLista();
  const id = location.hash.slice(1);
  if (/^[a-f0-9]{32}$/.test(id)) await abrir(id);
});

ui['descartar-voz'].addEventListener('click', () => {
  descartarVoz();
  ui.texto.value = '';
});
ui.microfono.addEventListener('click', async () => {
  if (grabadora?.state === 'recording') {
    grabadora.stop();
    return;
  }
  if (ocupada || actual?.bloqueada) return;
  await ejecutar(async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      throw new Error('Este navegador no permite grabar. Abrí la aplicación en localhost con un navegador actualizado.');
    }
    if (ui.texto.value.trim()) throw new Error('Enviá o borrá el texto actual antes de grabar.');
    if (!actual) await crear();
    descartarVoz();
    const sesion = actual.id;
    const tipo = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus']
      .find((valor) => MediaRecorder.isTypeSupported(valor));
    if (!tipo) throw new Error('El navegador no admite un formato de grabación compatible.');
    try {
      flujo = await navigator.mediaDevices.getUserMedia({audio: true});
      grabadora = new MediaRecorder(flujo, {mimeType: tipo});
      const partes = [];
      let fallo = false;
      const liberar = () => {
        clearTimeout(temporizador);
        flujo?.getTracks().forEach((pista) => pista.stop());
        flujo = null;
        ui.microfono.textContent = 'Grabar voz';
      };
      grabadora.ondataavailable = (evento) => { if (evento.data.size) partes.push(evento.data); };
      grabadora.onerror = () => {
        fallo = true;
        liberar();
        controles(false);
        ui.error.textContent = 'Falló la grabación. Intentá de nuevo.';
        ui.error.hidden = false;
      };
      grabadora.onstop = async () => {
        liberar();
        controles(false);
        if (fallo) return;
        await ejecutar(async () => {
          ui['estado-voz'].textContent = 'Transcribiendo en tu equipo…';
          const blob = new Blob(partes, {type: tipo});
          if (!blob.size || blob.size > 4000000) throw new Error('Audio vacío o demasiado grande. Grabá una frase más corta.');
          const audio = await new Promise((resolve, reject) => {
            const lector = new FileReader();
            lector.onerror = reject;
            lector.onload = () => resolve(lector.result.split(',')[1]);
            lector.readAsDataURL(blob);
          });
          const resultado = await api('/api/sesiones/' + sesion + '/audio', {audio, tipo});
          if (!resultado.texto.trim()) throw new Error('No se reconoció voz. Volvé a grabar o escribí el pedido.');
          audioPendiente = {id: resultado.audio_id, sesion};
          ui.texto.value = resultado.texto;
          ui['descartar-voz'].hidden = false;
          ui['estado-voz'].textContent = 'Revisá y corregí la transcripción; después presioná Enviar. El audio se conserva localmente.';
          ui.texto.focus();
        });
      };
      grabadora.start();
      if (!tareas.has(sesion)) tareas.set(sesion, globalThis.performance?.now?.() ?? Date.now());
      temporizador = setTimeout(() => { if (grabadora.state === 'recording') grabadora.stop(); }, 60000);
    } catch (error) {
      flujo?.getTracks().forEach((pista) => pista.stop());
      throw new Error('No se pudo iniciar el micrófono. Revisá los permisos del navegador. ' + error.message);
    }
  });
  if (grabadora?.state === 'recording') {
    controles(true);
    ui.pensando.hidden = true;
    ui.microfono.disabled = false;
    ui.microfono.textContent = 'Detener grabación';
    ui['estado-voz'].textContent = 'Grabando… Presioná Detener cuando termines (máximo 60 segundos).';
  }
});
window.addEventListener('pagehide', () => {
  clearTimeout(temporizador);
  flujo?.getTracks().forEach((pista) => pista.stop());
});

async function verRecursos(tipo) {
  await ejecutar(async () => {
    if (!actual) await crear();
    const datos = await api('/api/sesiones/' + actual.id + '/' + tipo);
    ui.recursos.replaceChildren(nodo('h2', '', tipo === 'agenda' ? 'Agenda desde hoy' : 'Documentos disponibles'));
    if (tipo === 'agenda') {
      if (!datos.eventos.length) ui.recursos.append(nodo('p', '', 'No hay eventos guardados.'));
      for (const evento of datos.eventos) ui.recursos.append(nodo('p', '', (evento.fecha || 'Sin fecha · pendiente') + ' · ' + (evento.hora || 'Sin hora') + ' · ' + evento.titulo));
    } else {
      if (!datos.documentos.length) ui.recursos.append(nodo('p', '', 'No hay documentos disponibles.'));
      for (const documento of datos.documentos) {
        const entrada = nodo('details');
        entrada.append(nodo('summary', '', documento.nombre), nodo('pre', '', documento.texto));
        ui.recursos.append(entrada);
      }
    }
    ui.recursos.hidden = false;
    ui['cerrar-recursos'].hidden = false;
  });
}
ui['ver-agenda'].addEventListener('click', () => verRecursos('agenda'));
ui['ver-documentos'].addEventListener('click', () => verRecursos('documentos'));
ui['cerrar-recursos'].addEventListener('click', () => {
  ui.recursos.hidden = true;
  ui['cerrar-recursos'].hidden = true;
});
