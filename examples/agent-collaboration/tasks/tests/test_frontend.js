const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

test('a malformed task response stays in the error state instead of showing empty', async () => {
    const elements = new Map();
    for (const id of [
        'formulario-tarea',
        'input-titulo',
        'lista-tareas',
        'estado-carga',
        'lista-vacia',
        'estado-api',
    ]) {
        elements.set(id, {
            style: { display: '' },
            addEventListener() {},
            appendChild() {},
            parentNode: { insertBefore() {} },
        });
    }

    const context = {
        document: {
            getElementById: id => elements.get(id),
            createElement: () => ({ style: {}, remove() {} }),
        },
        fetch: async url => url.endsWith('/api/health')
            ? { ok: true }
            : { ok: true, json: async () => ({ tasks: null }) },
        console: { error() {} },
        setTimeout: () => 0,
    };
    const source = fs.readFileSync(
        path.join(__dirname, '..', 'frontend', 'app.js'),
        'utf8',
    );
    vm.runInNewContext(source, context);
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(elements.get('estado-carga').style.display, 'block');
    assert.match(elements.get('estado-carga').textContent, /No se pudieron cargar/);
    assert.equal(elements.get('lista-vacia').style.display, 'none');
});

function responseContext(response) {
    const errors = [];
    const element = {
        style: {}, addEventListener() {}, appendChild() {},
        parentNode: { insertBefore(error) { errors.push(error.textContent); } },
    };
    const context = {
        document: { getElementById: () => element, createElement: () => ({ remove() {} }) },
        fetch: async url => url.endsWith('/api/health') ? { ok: false } : response,
        console: { error() {} }, setTimeout() {},
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'frontend', 'app.js'), 'utf8'), context);
    return { context, errors };
}

for (const action of ['crearTarea', 'completarTarea']) {
    for (const payload of [{}, { task: null }, { task: { id: '1', title: 'ok', completed: false } }, { task: { id: 1, title: 'ok', completed: 'false' } }]) {
        test(`${action} rechaza sobre de éxito inválido ${JSON.stringify(payload)}`, async () => {
            const { context, errors } = responseContext({ ok: true, json: async () => payload });
            assert.equal(await context[action]('1'), null);
            assert.match(errors[0], /Respuesta de tarea inválida/);
        });
    }
    test(`${action} muestra error legible cuando el servidor no devuelve JSON`, async () => {
        const { context, errors } = responseContext({ ok: false, json: async () => { throw new SyntaxError('unexpected <'); } });
        assert.equal(await context[action]('1'), null);
        assert.match(errors[0], /Error al (crear|completar) tarea/);
        assert.doesNotMatch(errors[0], /unexpected/);
    });
    test(`${action} conserva respuesta válida`, async () => {
        const task = { id: 1, title: 'ok', completed: true };
        const { context, errors } = responseContext({ ok: true, json: async () => ({ task }) });
        assert.equal(await context[action]('1'), task);
        assert.equal(errors.length, 0);
    });
}
