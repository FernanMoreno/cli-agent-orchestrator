// Controles puros (sin navegador ni dependencias) del modelo de escala sintético de
// mockup-studio-original.html. Extrae el bloque <script id="scale-model"> y lo ejecuta
// aislado del DOM.  Uso: node specs/019-frontend-studio/mockup-studio-scale-checks.mjs
// Prueba límites de la MAQUETA (ventanas, conteos coherentes, navegación); no mide ni
// promete capacidad de ningún backend.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const html = readFileSync(
  fileURLToPath(new URL("./mockup-studio-original.html", import.meta.url)),
  "utf8",
);
const m = html.match(/<script id="scale-model">([\s\S]*?)<\/script>/);
assert.ok(m, 'falta <script id="scale-model">');
const CaosScale = new Function(`"use strict";${m[1]};return CaosScale;`)();

const results = [];
const check = (name, fn) => {
  const t = performance.now();
  try {
    fn();
    results.push([name, "ok", (performance.now() - t).toFixed(1) + " ms"]);
  } catch (e) {
    results.push([name, "FALLO", e.message]);
  }
};
const sum = (c) => CaosScale.STATES.reduce((a, s) => a + c[s], 0);
const L = CaosScale.LIMITS;

for (const n of [100, 10_000, 1_000_000]) {
  const M = CaosScale.create(n);
  check(`${n}: raíz cuenta exactamente n agentes`, () => {
    const c = M.counts(M.root());
    assert.equal(c.total, n);
    assert.equal(sum(c), n);
  });
  check(`${n}: hijos acotados y suman al padre en cada nivel visitado`, () => {
    let node = M.root(),
      depth = 0;
    while (!M.isLeaf(node)) {
      const kids = M.children(node);
      assert.ok(
        kids.length >= 1 && kids.length <= L.cards,
        `hijos=${kids.length}`,
      );
      const pc = M.counts(node),
        acc = Object.fromEntries(CaosScale.STATES.map((s) => [s, 0]));
      kids.forEach((k) =>
        CaosScale.STATES.forEach((s) => (acc[s] += M.counts(k)[s])),
      );
      CaosScale.STATES.forEach((s) =>
        assert.equal(acc[s], pc[s], `estado ${s} nivel ${depth}`),
      );
      node = kids[kids.length - 1];
      depth++;
      assert.ok(depth < 8, "profundidad desbocada");
    }
    assert.ok(node.hi - node.lo <= L.leaf, "hoja mayor que el límite");
    assert.equal(M.leafAgents(node).length, node.hi - node.lo);
  });
  check(`${n}: pathOf llega a la hoja que contiene al agente`, () => {
    for (const i of [0, n - 1, Math.floor(n / 2), Math.floor(n * 0.7331)]) {
      const p = M.pathOf(i),
        leaf = p[p.length - 1];
      assert.ok(M.isLeaf(leaf) && leaf.lo <= i && i < leaf.hi);
      for (let d = 1; d < p.length; d++)
        assert.ok(
          M.children(p[d - 1]).some(
            (k) => k.lo === p[d].lo && k.hi === p[d].hi,
          ),
        );
    }
  });
  check(
    `${n}: conteo por estado coincide con fuerza bruta en rangos pequeños`,
    () => {
      const kids = M.children(M.root()),
        probe = M.isLeaf(kids[0]) ? kids[0] : M.children(kids[0])[0];
      const brute = Object.fromEntries(CaosScale.STATES.map((s) => [s, 0]));
      for (let i = probe.lo; i < Math.min(probe.hi, probe.lo + 5000); i++)
        brute[M.agent(i).st]++;
      if (probe.hi - probe.lo <= 5000)
        CaosScale.STATES.forEach((s) =>
          assert.equal(M.counts(probe)[s], brute[s]),
        );
    },
  );
  check(
    `${n}: nthWithState y páginas por estado son exactas y acotadas`,
    () => {
      const root = M.root();
      for (const st of CaosScale.STATES) {
        const total = M.counts(root)[st];
        const pg = M.pageByState(root, st, 0, L.rows);
        assert.equal(pg.total, total);
        assert.ok(pg.rows.length <= L.rows);
        pg.rows.forEach((a) => assert.equal(a.st, st));
        if (total) {
          const last = M.nthWithState(root, st, total - 1);
          assert.equal(M.agent(last).st, st);
          assert.equal(M.nthWithState(root, st, total), -1);
          const lastPage = M.pageByState(
            root,
            st,
            Math.floor((total - 1) / L.rows),
            L.rows,
          );
          assert.equal(lastPage.rows[lastPage.rows.length - 1].idx, last);
        }
      }
    },
  );
  check(`${n}: buscar por id o número abre al agente correcto`, () => {
    const i = Math.floor(n * 0.42);
    assert.equal(M.find(M.agent(i).id), i);
    assert.equal(M.find(String(i + 1)), i);
    assert.equal(M.find("AG-" + (n + 5)), -1);
  });
  check(
    `${n}: vecindad del agente es pequeña y referencialmente íntegra`,
    () => {
      for (const i of [0, n - 1, Math.floor(n / 3)]) {
        const d = M.neighbourhood(i, {});
        assert.ok(d.N.length <= L.neighbourhood, `nodos=${d.N.length}`);
        const ids = new Set(d.N.map((x) => x.id));
        assert.equal(ids.size, d.N.length, "ids repetidos");
        d.E.forEach((e) =>
          assert.ok(ids.has(e.a) && ids.has(e.b), `vínculo roto ${e.id}`),
        );
        d.N.forEach((x) => {
          if (x.owner) assert.ok(ids.has(x.owner));
          assert.ok(d.LANES.some((l) => l.id === x.lane));
        });
        d.ROUTE.forEach((r) => assert.ok(ids.has(r)));
        Object.keys(d.DEC).forEach((k) => assert.ok(ids.has(k)));
        assert.ok(
          d.N.some((x) => x.id === M.agent(i).id),
          "falta el agente central",
        );
        const st = M.agent(i).st;
        assert.equal(
          d.N.filter((x) => x.kind === "decision").length > 0,
          st === "wait" || st === "block",
        );
      }
    },
  );
  check(`${n}: determinismo entre instancias`, () => {
    const M2 = CaosScale.create(n),
      i = Math.floor(n * 0.9);
    assert.deepEqual(M2.agent(i), M.agent(i));
    assert.deepEqual(M2.counts(M2.root()), M.counts(M.root()));
    assert.deepEqual(
      M2.feedPage(M2.root(), 3, L.feed),
      M.feedPage(M.root(), 3, L.feed),
    );
  });
  check(`${n}: actividad paginada acotada y dentro del ámbito`, () => {
    const scope = M.children(M.root())[0];
    const pg = M.feedPage(scope, 2, L.feed);
    assert.ok(pg.length <= L.feed && pg.length > 0);
    pg.forEach((f) => assert.ok(f.idx >= scope.lo && f.idx < scope.hi));
  });
  check(`${n}: agrupación por proyecto conserva totales`, () => {
    const ps = M.projects();
    assert.ok(ps.length >= 1 && ps.length <= L.cards);
    assert.equal(
      ps.reduce((a, p) => a + M.counts(p).total, 0),
      n,
    );
    ps.forEach((p) =>
      M.children(p).forEach((t) => assert.equal(M.projectOf(t), p.p)),
    );
  });
}

check("1M: navegar raíz→hoja y contar no recorre el millón (< 50 ms)", () => {
  const M = CaosScale.create(1_000_000),
    t = performance.now();
  let node = M.root();
  while (!M.isLeaf(node)) {
    M.children(node).forEach((k) => M.counts(k));
    node = M.children(node)[7] || M.children(node)[0];
  }
  M.pageByState(M.root(), "block", 1234, L.rows);
  assert.ok(
    performance.now() - t < 50,
    `${(performance.now() - t).toFixed(1)} ms`,
  );
});
check("respuesta local persiste al regenerar la vecindad", () => {
  const M = CaosScale.create(10_000);
  const i = M.nthWithState(M.root(), "block", 0),
    d = "D-" + (i + 1);
  const nb = M.neighbourhood(i, { [d]: "Aprobar" });
  assert.equal(nb.N.find((x) => x.id === d).st, "done");
  assert.equal(nb.N.find((x) => x.id === "T-" + (i + 1) + ".1").st, "idle");
});
check(
  "tamaños arbitrarios (1, 41, 1601, 2 500 001) sin límite de diseño fijo",
  () => {
    for (const n of [1, 41, 1601, 2_500_001]) {
      const M = CaosScale.create(n);
      assert.equal(M.counts(M.root()).total, n);
      const p = M.pathOf(n - 1);
      assert.ok(M.isLeaf(p[p.length - 1]));
    }
  },
);

const w = Math.max(...results.map((r) => r[0].length));
results.forEach(([n, s, i]) =>
  console.log(`${s === "ok" ? "  ok  " : "FALLO "} ${n.padEnd(w)}  ${i}`),
);
const bad = results.filter((r) => r[1] !== "ok").length;
console.log(`\n${results.length - bad}/${results.length} controles superados`);
process.exit(bad ? 1 : 0);
