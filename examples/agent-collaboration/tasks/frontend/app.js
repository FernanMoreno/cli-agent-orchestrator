// Configuración de la API
const API_BASE_URL = '';

// Elementos del DOM
const formulario = document.getElementById('formulario-tarea');
const inputTitulo = document.getElementById('input-titulo');
const listaTareas = document.getElementById('lista-tareas');
const estadoCarga = document.getElementById('estado-carga');
const listaVacia = document.getElementById('lista-vacia');
const estadoApi = document.getElementById('estado-api');

// Verificar conexión con la API
async function verificarApi() {
    try {
        const respuesta = await fetch(`${API_BASE_URL}/api/health`);
        if (respuesta.ok) {
            estadoApi.textContent = 'Conectado ✓';
            estadoApi.className = 'conectado';
            return true;
        }
    } catch (error) {
        console.error('Error al conectar con la API:', error);
    }
    estadoApi.textContent = 'Error de conexión';
    estadoApi.className = 'error';
    return false;
}

// Obtener todas las tareas
async function obtenerTareas() {
    try {
        const respuesta = await fetch(`${API_BASE_URL}/api/tasks`);
        if (!respuesta.ok) throw new Error('Error al obtener tareas');
        const datos = await respuesta.json();
        if (!Array.isArray(datos?.tasks)) {
            throw new Error('Respuesta de tareas inválida');
        }
        return datos.tasks;
    } catch (error) {
        console.error('Error al obtener tareas:', error);
        mostrarError('No se pudieron cargar las tareas');
        return null;
    }
}

// Crear nueva tarea
async function leerTarea(respuesta, mensajeError) {
    let datos;
    try {
        datos = await respuesta.json();
    } catch (_error) {
        throw new Error(respuesta.ok ? 'Respuesta de tarea inválida' : mensajeError);
    }
    if (!respuesta.ok) {
        throw new Error(typeof datos?.error === 'string' && datos.error ? datos.error : mensajeError);
    }
    const tarea = datos?.task;
    if (!tarea || !Number.isSafeInteger(tarea.id) || tarea.id <= 0 ||
        typeof tarea.title !== 'string' || !tarea.title.trim() ||
        typeof tarea.completed !== 'boolean') {
        throw new Error('Respuesta de tarea inválida');
    }
    return tarea;
}

async function crearTarea(titulo) {
    try {
        const respuesta = await fetch(`${API_BASE_URL}/api/tasks`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ title: titulo }),
        });
        
        return await leerTarea(respuesta, 'Error al crear tarea');
    } catch (error) {
        console.error('Error al crear tarea:', error);
        mostrarError(error.message);
        return null;
    }
}

// Completar tarea
async function completarTarea(id) {
    try {
        const respuesta = await fetch(`${API_BASE_URL}/api/tasks/${id}`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ completed: true }),
        });
        
        return await leerTarea(respuesta, 'Error al completar tarea');
    } catch (error) {
        console.error('Error al completar tarea:', error);
        mostrarError(error.message);
        return null;
    }
}

// Mostrar error
function mostrarError(mensaje) {
    const divError = document.createElement('div');
    divError.className = 'error-mensaje';
    divError.textContent = mensaje;
    formulario.parentNode.insertBefore(divError, formulario);
    setTimeout(() => divError.remove(), 5000);
}

// Renderizar lista de tareas
function renderizarTareas(tareas) {
    listaTareas.innerHTML = '';
    
    if (tareas.length === 0) {
        listaVacia.style.display = 'block';
        estadoCarga.style.display = 'none';
        return;
    }
    
    listaVacia.style.display = 'none';
    estadoCarga.style.display = 'none';
    
    tareas.forEach(tarea => {
        const li = document.createElement('li');
        li.className = `tarea ${tarea.completed ? 'completada' : ''}`;
        li.dataset.id = tarea.id;
        
        li.innerHTML = `
            <input 
                type="checkbox" 
                class="checkbox-tarea" 
                ${tarea.completed ? 'checked disabled' : ''}
                title="${tarea.completed ? 'Tarea completada' : 'Marcar como completada'}"
            >
            <span class="titulo-tarea">${escapeHtml(tarea.title)}</span>
        `;
        
        if (!tarea.completed) {
            const checkbox = li.querySelector('.checkbox-tarea');
            checkbox.addEventListener('change', async () => {
                checkbox.disabled = true;
                const exito = await completarTarea(tarea.id);
                if (exito) {
                    li.classList.add('completada');
                    checkbox.checked = true;
                    li.querySelector('.titulo-tarea').style.textDecoration = 'line-through';
                } else {
                    checkbox.disabled = false;
                    checkbox.checked = false;
                }
            });
        }
        
        listaTareas.appendChild(li);
    });
}

// Escapar HTML para prevenir XSS
function escapeHtml(texto) {
    const div = document.createElement('div');
    div.textContent = texto;
    return div.innerHTML;
}

// Cargar tareas al iniciar
async function cargarTareas() {
    estadoCarga.style.display = 'block';
    const tareas = await obtenerTareas();
    if (tareas === null) {
        estadoCarga.textContent =
            'No se pudieron cargar las tareas. Comprueba la conexión y recarga la página.';
        listaVacia.style.display = 'none';
        return;
    }
    renderizarTareas(tareas);
}

// Evento del formulario
formulario.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const titulo = inputTitulo.value.trim();
    if (!titulo) return;
    
    const boton = formulario.querySelector('button');
    boton.disabled = true;
    boton.textContent = 'Creando...';
    
    const tarea = await crearTarea(titulo);
    
    if (tarea) {
        inputTitulo.value = '';
        await cargarTareas();
    }
    
    boton.disabled = false;
    boton.innerHTML = '➕ Crear Tarea';
});

// Inicializar
async function inicializar() {
    const apiConectada = await verificarApi();
    if (apiConectada) {
        await cargarTareas();
    } else {
        estadoCarga.textContent = 'No se pudo conectar con la API';
    }
}

inicializar();
