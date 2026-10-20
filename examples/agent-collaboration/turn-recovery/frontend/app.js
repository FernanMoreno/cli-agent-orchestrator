// ---- Referencias al DOM ----
const formNota = document.getElementById('form-nota');
const inputNota = document.getElementById('input-nota');
const btnAgregar = document.getElementById('btn-agregar');
const listaNotas = document.getElementById('lista-notas');
const buscarNota = document.getElementById('buscar-nota');
const filtroNotas = document.getElementById('filtro-notas');
const statTotal = document.getElementById('stat-total');
const statPending = document.getElementById('stat-pending');
const statCompleted = document.getElementById('stat-completed');
const regionError = document.getElementById('error');
const btnLimpiarCompletadas = document.getElementById('btn-limpiar-completadas');
const anuncioLimpieza = document.getElementById('anuncio-limpieza');

// ---- Constantes y estado ----
const MAX_TEXTO = 2000; // mismo límite que valida el servidor tras recortar

let notas = [];
let primeraCarga = true;
let editandoId = null;          // id de la nota en edición (una sola a la vez), o null
let borradorEdicion = null;     // texto en curso de la edición (sobrevive a re-renderizados)
let focoEditarPendiente = null; // id cuyo botón Editar recibe el foco tras renderizar
let limpiandoCompletadas = false;

// ---- Región global de errores (role="alert") ----
function mostrarError(mensaje) {
  regionError.textContent = mensaje;
  regionError.hidden = false;
}

function limpiarError() {
  regionError.textContent = '';
  regionError.hidden = true;
}

// ---- Error de edición junto al campo (role="alert" local) ----
function mostrarErrorEdicion(region, campo, mensaje) {
  region.textContent = mensaje;
  region.hidden = false;
  campo.setAttribute('aria-invalid', 'true');
}

function limpiarErrorEdicion(region, campo) {
  region.textContent = '';
  region.hidden = true;
  campo.removeAttribute('aria-invalid');
}

// ---- Validación de cliente: texto recortado de 1 a 2000 caracteres ----
function validarTextoNota(texto) {
  const recortado = texto.trim();
  if (recortado.length < 1) {
    return { valida: false, mensaje: 'El texto de la nota no puede quedar vacío.' };
  }
  if (recortado.length > MAX_TEXTO) {
    return { valida: false, mensaje: `El texto de la nota no puede superar ${MAX_TEXTO} caracteres.` };
  }
  return { valida: true, texto: recortado };
}

function mostrarMensajeLista(texto) {
  listaNotas.innerHTML = '';
  const li = document.createElement('li');
  li.className = 'cargando';
  li.textContent = texto;
  listaNotas.appendChild(li);
}

async function cargarNotas() {
  if (primeraCarga) mostrarMensajeLista('Cargando notas...');
  try {
    const res = await fetch('api/notes');
    if (!res.ok) throw new Error('Error al cargar las notas');
    notas = await res.json();
    primeraCarga = false;
    limpiarError();
    renderizar();
    cargarStats(); // refresca Total/Pendientes/Completadas desde /api/stats (con fallback local)
  } catch (err) {
    mostrarError('No se pudieron cargar las notas.');
    if (primeraCarga) mostrarMensajeLista('No se pudieron cargar las notas.');
  }
}

function notasVisibles() {
  const termino = buscarNota.value.trim().toLowerCase();
  return notas.filter(nota => {
    if (filtroNotas.value === 'pendientes' && nota.done) return false;
    if (filtroNotas.value === 'completadas' && !nota.done) return false;
    if (termino && !nota.text.toLowerCase().includes(termino)) return false;
    return true;
  });
}

// ---- Contadores (Total / Pendientes / Completadas) ----
// Fuente principal: GET /api/stats -> {"total":N,"pending":N,"completed":N}.
// Si stats es null o tiene una forma inesperada, se recurre al cálculo local
// sobre `notas`: los contadores nunca rompen la lista aunque /api/stats falle.
function esConteoValido(valor) {
  return Number.isInteger(valor) && valor >= 0;
}

function actualizarContadores(stats) {
  let total;
  let pendientes;
  let completadas;
  if (stats && esConteoValido(stats.total) && esConteoValido(stats.pending) && esConteoValido(stats.completed)) {
    total = stats.total;
    pendientes = stats.pending;
    completadas = stats.completed;
  } else {
    pendientes = notas.filter(nota => !nota.done).length;
    completadas = notas.length - pendientes;
    total = notas.length;
  }
  statTotal.textContent = String(total);
  statPending.textContent = String(pendientes);
  statCompleted.textContent = String(completadas);
  btnLimpiarCompletadas.disabled = limpiandoCompletadas || completadas === 0;
  btnLimpiarCompletadas.setAttribute('aria-label', completadas === 0
    ? 'Borrar notas completadas (no hay ninguna)'
    : `Borrar ${completadas} ${completadas === 1 ? 'nota completada' : 'notas completadas'}`);
}

// Pide las estadísticas al servidor y actualiza los tres contadores.
// Cualquier error (red, HTTP, JSON inválido) deja los contadores con el
// cálculo local; nunca interfiere con la carga ni el renderizado de notas.
async function cargarStats() {
  try {
    const res = await fetch('api/stats');
    if (!res.ok) throw new Error('Error al cargar las estadísticas');
    const stats = await res.json();
    actualizarContadores(stats);
  } catch (err) {
    actualizarContadores();
  }
}

function buscarItemNota(id) {
  for (const li of listaNotas.children) {
    if (li.dataset && li.dataset.id === id) return li;
  }
  return null;
}

// Devuelve el foco al botón Editar de la nota indicada (tras Guardar/Cancelar).
function restaurarFocoEditar() {
  if (focoEditarPendiente === null) return;
  const li = buscarItemNota(focoEditarPendiente);
  focoEditarPendiente = null;
  if (li) {
    const boton = li.querySelector('.editar');
    if (boton) boton.focus();
  }
}

function renderizar() {
  actualizarContadores();
  listaNotas.innerHTML = '';
  const visibles = notasVisibles();
  // Si la nota en edición desapareció (borrada o filtrada), se descarta el borrador.
  if (editandoId !== null && !visibles.some(nota => nota.id === editandoId)) {
    editandoId = null;
    borradorEdicion = null;
  }
  if (visibles.length === 0) {
    mostrarMensajeLista(notas.length === 0
      ? 'No hay notas todavía.'
      : 'Ninguna nota coincide con la búsqueda o el filtro.');
    restaurarFocoEditar();
    return;
  }
  visibles.forEach(nota => {
    const li = document.createElement('li');
    li.className = nota.done ? 'completada' : '';
    li.dataset.id = nota.id;
    if (editandoId === nota.id) {
      renderizarEdicion(li, nota);
    } else {
      renderizarVista(li, nota);
    }
    listaNotas.appendChild(li);
  });
  restaurarFocoEditar();
}

// ---- Vista normal de una nota: checkbox, texto, Editar, Eliminar ----
function renderizarVista(li, nota) {
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = nota.done;
  checkbox.setAttribute('aria-label',
    `Marcar "${nota.text}" como ${nota.done ? 'pendiente' : 'completada'}`);
  checkbox.addEventListener('change', () => alternarNota(nota.id, checkbox.checked, checkbox));

  const span = document.createElement('span');
  span.className = 'texto-nota';
  span.textContent = nota.text;

  const botonEditar = document.createElement('button');
  botonEditar.type = 'button';
  botonEditar.className = 'editar';
  botonEditar.textContent = 'Editar';
  botonEditar.setAttribute('aria-label', `Editar nota "${nota.text}"`);
  botonEditar.addEventListener('click', () => entrarEdicion(nota.id));

  const botonEliminar = document.createElement('button');
  botonEliminar.type = 'button';
  botonEliminar.className = 'eliminar';
  botonEliminar.textContent = '×';
  botonEliminar.setAttribute('aria-label', `Eliminar nota "${nota.text}"`);
  botonEliminar.addEventListener('click', () => eliminarNota(nota.id, botonEliminar));

  li.appendChild(checkbox);
  li.appendChild(span);
  li.appendChild(botonEditar);
  li.appendChild(botonEliminar);
}

// ---- Vista de edición: textarea, error accesible, Guardar y Cancelar ----
function renderizarEdicion(li, nota) {
  li.classList.add('editando');

  const textarea = document.createElement('textarea');
  textarea.className = 'editor-nota';
  textarea.maxLength = MAX_TEXTO;
  textarea.rows = 3;
  textarea.value = borradorEdicion !== null ? borradorEdicion : nota.text;
  textarea.setAttribute('aria-label', `Editar texto de la nota "${nota.text}"`);

  const errorEdicion = document.createElement('p');
  errorEdicion.className = 'error-edicion';
  errorEdicion.id = `error-edit-${nota.id}`;
  errorEdicion.setAttribute('role', 'alert');
  errorEdicion.hidden = true;
  textarea.setAttribute('aria-describedby', errorEdicion.id);

  const botonGuardar = document.createElement('button');
  botonGuardar.type = 'button';
  botonGuardar.className = 'guardar';
  botonGuardar.textContent = 'Guardar';
  botonGuardar.setAttribute('aria-label', `Guardar cambios en la nota "${nota.text}"`);

  const botonCancelar = document.createElement('button');
  botonCancelar.type = 'button';
  botonCancelar.className = 'cancelar';
  botonCancelar.textContent = 'Cancelar';
  botonCancelar.setAttribute('aria-label', `Cancelar la edición de la nota "${nota.text}"`);

  botonGuardar.addEventListener('click', () =>
    guardarEdicion(nota, textarea, botonGuardar, botonCancelar, errorEdicion));
  botonCancelar.addEventListener('click', () => cancelarEdicion(nota.id));

  textarea.addEventListener('input', () => {
    borradorEdicion = textarea.value;
    limpiarErrorEdicion(errorEdicion, textarea);
  });
  textarea.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      guardarEdicion(nota, textarea, botonGuardar, botonCancelar, errorEdicion);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelarEdicion(nota.id);
    }
  });

  const acciones = document.createElement('div');
  acciones.className = 'acciones-edicion';
  acciones.appendChild(botonGuardar);
  acciones.appendChild(botonCancelar);

  li.appendChild(textarea);
  li.appendChild(errorEdicion);
  li.appendChild(acciones);
}

// ---- Entrar en edición: el foco va al textarea (cursor al final) ----
function entrarEdicion(id) {
  if (editandoId === id) return;
  editandoId = id;
  borradorEdicion = null;
  renderizar();
  const li = buscarItemNota(id);
  const textarea = li ? li.querySelector('.editor-nota') : null;
  if (textarea) {
    textarea.focus();
    const largo = textarea.value.length;
    textarea.setSelectionRange(largo, largo);
  }
}

// ---- Cancelar: vuelve a la vista y devuelve el foco al botón Editar ----
function cancelarEdicion(id) {
  editandoId = null;
  borradorEdicion = null;
  focoEditarPendiente = id;
  renderizar();
}

// ---- Guardar: valida en cliente y hace PATCH solo con {"text"} ----
async function guardarEdicion(nota, textarea, botonGuardar, botonCancelar, errorEdicion) {
  if (botonGuardar.disabled) return;
  const resultado = validarTextoNota(textarea.value);
  if (!resultado.valida) {
    mostrarErrorEdicion(errorEdicion, textarea, resultado.mensaje);
    textarea.focus();
    return; // texto inválido: no se envía nada al servidor
  }
  if (resultado.texto === nota.text) {
    cancelarEdicion(nota.id); // sin cambios: equivale a cancelar
    return;
  }
  botonGuardar.disabled = true;
  botonCancelar.disabled = true;
  textarea.readOnly = true;
  try {
    const res = await fetch(`api/notes/${nota.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: resultado.texto }) // solo text: id y done se conservan
    });
    if (!res.ok) {
      const fallo = new Error('Error al guardar la nota');
      fallo.status = res.status;
      throw fallo;
    }
    const actualizada = await res.json(); // {id, text, done} según el contrato
    const indice = notas.findIndex(n => n.id === nota.id);
    if (indice !== -1) notas[indice] = actualizada;
    editandoId = null;
    borradorEdicion = null;
    focoEditarPendiente = nota.id; // el foco vuelve al botón Editar
    limpiarError();
    renderizar();
    cargarStats(); // refresca los contadores tras editar (aunque solo cambie el texto)
  } catch (err) {
    // 400 / 404 / error de red: se muestra el mensaje sin perder lo escrito
    botonGuardar.disabled = false;
    botonCancelar.disabled = false;
    textarea.readOnly = false;
    let mensaje;
    if (err.status === 400) {
      mensaje = 'El servidor rechazó el texto: debe tener entre 1 y 2000 caracteres. Corrígelo e inténtalo de nuevo.';
    } else if (err.status === 404) {
      mensaje = 'No se pudo guardar: la nota ya no existe en el servidor.';
    } else {
      mensaje = 'No se pudo guardar la nota. Revisa la conexión e inténtalo de nuevo.';
    }
    mostrarErrorEdicion(errorEdicion, textarea, mensaje);
    textarea.focus();
  }
}

async function agregarNota(texto) {
  btnAgregar.disabled = true;
  try {
    const res = await fetch('api/notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: texto })
    });
    if (!res.ok) throw new Error('Error al añadir la nota');
    inputNota.value = '';
    limpiarError();
    await cargarNotas();
  } catch (err) {
    mostrarError('No se pudo añadir la nota.');
  } finally {
    btnAgregar.disabled = false;
    inputNota.focus();
  }
}

async function alternarNota(id, done, checkbox) {
  checkbox.disabled = true;
  try {
    const res = await fetch(`api/notes/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ done })
    });
    if (!res.ok) throw new Error('Error al actualizar la nota');
    limpiarError();
    await cargarNotas();
  } catch (err) {
    checkbox.checked = !done;
    checkbox.disabled = false;
    mostrarError('No se pudo actualizar la nota.');
  }
}

async function eliminarNota(id, boton) {
  boton.disabled = true;
  try {
    const res = await fetch(`api/notes/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Error al eliminar la nota');
    limpiarError();
    await cargarNotas();
  } catch (err) {
    boton.disabled = false;
    mostrarError('No se pudo eliminar la nota.');
  }
}

// ---- Borrar todas las completadas: DELETE /api/notes/completed ----
async function borrarCompletadas() {
  if (limpiandoCompletadas || !notas.some(nota => nota.done)) return;
  if (!window.confirm('¿Limpiar todas las notas completadas?')) return;
  limpiandoCompletadas = true;
  btnLimpiarCompletadas.disabled = true;
  try {
    const res = await fetch('api/notes/completed', { method: 'DELETE' });
    if (!res.ok) throw new Error('Error al borrar las notas completadas');
    limpiarError();
    await cargarNotas();
  } catch (err) {
    mostrarError('No se pudieron borrar las notas completadas.');
  } finally {
    limpiandoCompletadas = false;
    actualizarContadores();
  }
}

formNota.addEventListener('submit', e => {
  e.preventDefault();
  if (btnAgregar.disabled) return;
  const texto = inputNota.value.trim();
  if (!texto) return;
  agregarNota(texto);
});

buscarNota.addEventListener('input', renderizar);
filtroNotas.addEventListener('change', renderizar);
btnLimpiarCompletadas.addEventListener('click', borrarCompletadas);

cargarNotas();
