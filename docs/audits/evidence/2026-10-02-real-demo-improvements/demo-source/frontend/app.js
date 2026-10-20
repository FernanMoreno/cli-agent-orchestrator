const formNota = document.getElementById('form-nota');
const inputNota = document.getElementById('input-nota');
const btnAgregar = document.getElementById('btn-agregar');
const listaNotas = document.getElementById('lista-notas');
const buscarNota = document.getElementById('buscar-nota');
const filtroNotas = document.getElementById('filtro-notas');
const contadores = document.getElementById('contadores');
const regionError = document.getElementById('error');

let notas = [];
let primeraCarga = true;

function mostrarError(mensaje) {
  regionError.textContent = mensaje;
  regionError.hidden = false;
}

function limpiarError() {
  regionError.textContent = '';
  regionError.hidden = true;
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

function actualizarContadores() {
  const pendientes = notas.filter(nota => !nota.done).length;
  contadores.textContent = `Total: ${notas.length} · Pendientes: ${pendientes}`;
}

function renderizar() {
  actualizarContadores();
  listaNotas.innerHTML = '';
  const visibles = notasVisibles();
  if (visibles.length === 0) {
    mostrarMensajeLista(notas.length === 0
      ? 'No hay notas todavía.'
      : 'Ninguna nota coincide con la búsqueda o el filtro.');
    return;
  }
  visibles.forEach(nota => {
    const li = document.createElement('li');
    li.className = nota.done ? 'completada' : '';
    li.dataset.id = nota.id;

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = nota.done;
    checkbox.setAttribute('aria-label',
      `Marcar "${nota.text}" como ${nota.done ? 'pendiente' : 'completada'}`);
    checkbox.addEventListener('change', () => alternarNota(nota.id, checkbox.checked, checkbox));

    const span = document.createElement('span');
    span.className = 'texto-nota';
    span.textContent = nota.text;

    const botonEliminar = document.createElement('button');
    botonEliminar.type = 'button';
    botonEliminar.className = 'eliminar';
    botonEliminar.textContent = '×';
    botonEliminar.setAttribute('aria-label', `Eliminar nota "${nota.text}"`);
    botonEliminar.addEventListener('click', () => eliminarNota(nota.id, botonEliminar));

    li.appendChild(checkbox);
    li.appendChild(span);
    li.appendChild(botonEliminar);
    listaNotas.appendChild(li);
  });
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

formNota.addEventListener('submit', e => {
  e.preventDefault();
  if (btnAgregar.disabled) return;
  const texto = inputNota.value.trim();
  if (!texto) return;
  agregarNota(texto);
});

buscarNota.addEventListener('input', renderizar);
filtroNotas.addEventListener('change', renderizar);

cargarNotas();
