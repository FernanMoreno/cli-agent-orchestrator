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

/* ---- T082: vínculos dirigidos por antecesor común más bajo (LCA) ----
   Cada vínculo atómico une agentes concretos en hijos distintos del nodo que lo genera;
   los niveles superiores lo ven como haz agregado y los inferiores como puerto externo
   con el mismo id. Conteos exactos sobre ese conjunto modelado, nunca sobre la red total. */
const inR = (node, i) => node.lo <= i && i < node.hi;
for (const n of [100, 10_000, 1_000_000]) {
  const M = CaosScale.create(n);
  const walk = () => {
    const out = [];
    let node = M.root();
    for (;;) {
      out.push(node);
      if (M.isLeaf(node)) break;
      const kids = M.children(node);
      node = kids[Math.min(kids.length - 1, 2)];
    }
    return out;
  };
  check(
    `${n}: edgesOf acotado, extremos en hijos distintos e ids únicos`,
    () => {
      walk().forEach((node) => {
        const kids = M.kids(node),
          es = M.edgesOf(node);
        assert.ok(
          es.length <= Math.min(3 * kids.length, L.edgesPerGroup),
          `aristas=${es.length}`,
        );
        assert.ok(
          es.length > 0 || kids.length < 2,
          `${M.label(node)} sin vínculos`,
        );
        const ids = new Set(es.map((e) => e.id));
        assert.equal(ids.size, es.length, "ids repetidos");
        es.forEach((e) => {
          assert.ok(inR(node, e.a) && inR(node, e.b), `${e.id} fuera del nodo`);
          const ka = kids.findIndex((k) => inR(k, e.a)),
            kb = kids.findIndex((k) => inR(k, e.b));
          assert.ok(ka >= 0 && kb >= 0 && ka !== kb, `${e.id} no cruza hijos`);
          assert.ok(Object.hasOwn(CaosScale.RELS, e.ty), e.ty);
        });
      });
    },
  );
  check(
    `${n}: semántica: delega desde el responsable, espera solo a bloqueados/esperando`,
    () => {
      walk().forEach((node) =>
        M.edgesOf(node).forEach((e) => {
          if (e.ty === "delega")
            assert.equal(e.a, node.lo, `${e.id} no sale del responsable`);
          if (e.ty === "espera")
            assert.ok(
              ["wait", "block"].includes(M.agent(e.b).st),
              `${e.id} destino ${M.agent(e.b).st}`,
            );
          assert.notEqual(e.a, e.b);
        }),
      );
    },
  );
  check(`${n}: determinismo de ids y extremos entre instancias`, () => {
    const M2 = CaosScale.create(n);
    walk().forEach((node) =>
      assert.deepEqual(
        M2.edgesOf(node).map((e) => [e.id, e.ty, e.a, e.b]),
        M.edgesOf(node).map((e) => [e.id, e.ty, e.a, e.b]),
      ),
    );
  });
  check(`${n}: haces del nivel suman exactamente el conjunto modelado`, () => {
    walk().forEach((node) => {
      const r = M.relations({ kind: "range", node });
      const sum = [...r.internal, ...r.external].reduce(
        (a, b) => a + b.count,
        0,
      );
      assert.equal(sum + r.self, r.modeled);
      assert.equal(
        r.internal.reduce((a, b) => a + b.count, 0),
        M.edgesOf(node).length,
      );
      assert.equal(r.self, 0);
      r.internal.forEach((b) => {
        assert.notEqual(b.from.key, b.to.key);
        assert.equal(b.count, b.atoms.length);
        b.atoms.forEach((e) => assert.equal(e.ty, b.ty));
      });
    });
  });
  check(
    `${n}: al bajar, los vínculos del haz reaparecen como puertos externos con el mismo id`,
    () => {
      const path = walk();
      for (let d = 0; d + 1 < path.length; d++) {
        const child = path[d + 1],
          key = M.keyOf(child),
          up = M.relations({ kind: "range", node: path[d] }),
          down = M.relations({ kind: "range", node: child });
        const touching = up.internal
          .filter((b) => b.from.key === key || b.to.key === key)
          .flatMap((b) => b.atoms.map((e) => e.id));
        const ext = new Set(
          down.external.flatMap((b) => b.atoms.map((e) => e.id)),
        );
        touching.forEach((id) =>
          assert.ok(
            ext.has(id),
            `${id} se pierde al bajar a ${M.label(child)}`,
          ),
        );
        down.external.forEach((b) =>
          assert.ok(b.outside === "from" || b.outside === "to"),
        );
      }
    },
  );
  check(`${n}: proyectos agregan los vínculos raíz sin perder ninguno`, () => {
    const root = M.edgesOf(M.root()),
      r = M.relations({ kind: "projects" });
    assert.equal(r.modeled, root.length);
    assert.equal(
      r.internal.reduce((a, b) => a + b.count, 0) + r.self,
      root.length,
    );
    M.projects().forEach((p) => {
      const rp = M.relations({ kind: "project", p: p.p });
      const inP = (i) => M.projectOf({ lo: i }) === p.p;
      assert.equal(
        rp.internal.reduce((a, b) => a + b.count, 0),
        root.filter((e) => inP(e.a) && inP(e.b)).length,
      );
      assert.equal(
        rp.external.reduce((a, b) => a + b.count, 0),
        root.filter((e) => inP(e.a) !== inP(e.b)).length,
      );
    });
  });
  check(
    `${n}: agrupación por estado marca alcance parcial y es exacta sobre la raíz`,
    () => {
      const r = M.relations({ kind: "states", base: M.root() });
      assert.equal(r.partial, true);
      assert.equal(
        r.internal.reduce((a, b) => a + b.count, 0) + r.self,
        M.edgesOf(M.root()).length,
      );
      r.internal.forEach((b) =>
        b.atoms.forEach((e) => {
          assert.equal("s:" + M.agent(e.a).st, b.from.key);
          assert.equal("s:" + M.agent(e.b).st, b.to.key);
        }),
      );
    },
  );
  check(
    `${n}: página por estado tiene vínculos (internos o puertos) acotados`,
    () => {
      const st = M.counts(M.root()).block ? "block" : "wait";
      const pg = M.pageByState(M.root(), st, 0, L.leaf).rows.map((a) => a.idx);
      const r = M.relations({ kind: "page", agents: pg });
      assert.equal(r.partial, true);
      assert.ok(r.internal.length + r.external.length > 0, "página sin líneas");
      assert.ok(r.modeled <= pg.length * 8 * L.edgesPerGroup);
      r.external.forEach((b) =>
        b.atoms.forEach((e) =>
          assert.ok(pg.includes(e.a) !== pg.includes(e.b)),
        ),
      );
    },
  );
  check(
    `${n}: vecindad deriva sus vínculos de agente del mismo generador`,
    () => {
      for (const i of [
        0,
        n - 1,
        Math.floor(n / 3),
        M.nthWithState(M.root(), "block", 0),
        M.nthWithState(M.root(), "wait", 0),
      ].filter((x) => x >= 0)) {
        const atoms = new Map(M.agentAtoms(i).map((e) => [e.id, e])),
          d = M.neighbourhood(i, {}),
          byId = new Map(d.N.map((x) => [x.id, x]));
        const agentEdges = d.E.filter(
          (e) =>
            byId.get(e.a).kind === "agent" && byId.get(e.b).kind === "agent",
        );
        agentEdges.forEach((e) => {
          const at = atoms.get(e.id);
          assert.ok(at, `${e.id} no existe en el generador`);
          assert.equal(e.ty, at.ty);
          assert.equal(e.a, M.agent(at.a).id);
          assert.equal(e.b, M.agent(at.b).id);
        });
        assert.equal(d.INFO.total, atoms.size);
        assert.ok(d.INFO.shown <= d.INFO.total);
        if (atoms.size)
          assert.ok(agentEdges.length > 0, "vecindad sin vínculos de agente");
        const sup = [...atoms.values()].find(
          (e) => e.ty === "delega" && e.b === i,
        );
        if (sup)
          assert.ok(
            agentEdges.some((e) => e.id === sup.id),
            "falta la delegación del superior",
          );
        const focus = [...atoms.values()].at(-1);
        if (focus)
          assert.ok(
            M.neighbourhood(i, {}, focus.id).E.some((e) => e.id === focus.id),
            "vínculo pedido no visible",
          );
      }
    },
  );
}
check(
  "1M: relaciones de cualquier nivel sin recorrer el millón (< 60 ms)",
  () => {
    const M = CaosScale.create(1_000_000),
      t = performance.now();
    let node = M.root();
    M.relations({ kind: "projects" });
    M.relations({ kind: "states", base: node });
    while (!M.isLeaf(node)) {
      M.relations({ kind: "range", node });
      node = M.children(node)[5] || M.children(node)[0];
    }
    M.relations({ kind: "range", node });
    M.relations({
      kind: "page",
      agents: M.pageByState(M.root(), "wait", 777, L.leaf).rows.map(
        (a) => a.idx,
      ),
    });
    M.neighbourhood(654_321, {});
    assert.ok(
      performance.now() - t < 60,
      `${(performance.now() - t).toFixed(1)} ms`,
    );
  },
);
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

check("T083: proyectos declaran el subconjunto entre equipos", () => {
  const M = CaosScale.create(1_000_000);
  assert.equal(M.relations({ kind: "projects" }).partial, true);
  for (const p of M.projects())
    assert.equal(M.relations({ kind: "project", p: p.p }).partial, true);
});
check(
  "T083: la autoridad ficticia de todo emisor de delegación es coherente",
  () => {
    const M = CaosScale.create(1_000_000);
    for (const index of [0, 500_000, 999_999]) {
      for (const node of M.pathOf(index)) {
        for (const e of M.edgesOf(node).filter((e) => e.ty === "delega")) {
          assert.match(M.agent(e.a).auth, /delegar/);
        }
      }
    }
  },
);
check(
  "T083: solo los responsables estructurales delegan y la espera apunta a agentes retenidos",
  () => {
    const M = CaosScale.create(1_000_000);
    for (const index of [1, 2, 777, 500_001]) {
      const leads = M.pathOf(index).some((nd) => nd.lo === index);
      if (!leads) assert.doesNotMatch(M.agent(index).auth, /delegar/);
    }
    assert.equal(M.agent(0).name, "Coordinador");
    for (const node of M.pathOf(500_000))
      for (const e of M.edgesOf(node).filter((e) => e.ty === "espera")) {
        assert.match(M.agent(e.a).auth, /coordina decisiones/);
        assert.ok(["wait", "block"].includes(M.agent(e.b).st));
      }
    assert.match(CaosScale.RELS.espera.note, /coordinador/);
    assert.doesNotMatch(CaosScale.RELS.espera.desc, /una persona/);
  },
);
check(
  "T083: toda espera de hoja o grupo nace en un coordinador de otro hijo (barrido de hojas)",
  () => {
    let leaves = 0,
      waits = 0;
    for (const size of [100, 10_000, 1_000_000]) {
      const M = CaosScale.create(size);
      const step = Math.max(1, Math.floor(size / 397));
      for (let index = 0; index < size; index += step) {
        for (const node of M.pathOf(index)) {
          if (M.isLeaf(node)) leaves++;
          const kids = M.kids(node);
          for (const e of M.edgesOf(node).filter((e) => e.ty === "espera")) {
            waits++;
            assert.match(M.agent(e.a).auth, /coordina decisiones/, e.id);
            assert.equal(M.agent(e.a).name, "Coordinador", e.id);
            assert.ok(["wait", "block"].includes(M.agent(e.b).st), e.id);
            const ka = kids.findIndex((c) => c.lo <= e.a && e.a < c.hi);
            const kb = kids.findIndex((c) => c.lo <= e.b && e.b < c.hi);
            assert.notEqual(ka, kb, e.id);
          }
        }
      }
    }
    assert.ok(leaves > 300 && waits > 50, `hojas ${leaves}, esperas ${waits}`);
  },
);
const w = Math.max(...results.map((r) => r[0].length));
results.forEach(([n, s, i]) =>
  console.log(`${s === "ok" ? "  ok  " : "FALLO "} ${n.padEnd(w)}  ${i}`),
);
const bad = results.filter((r) => r[1] !== "ok").length;
console.log(`\n${results.length - bad}/${results.length} controles superados`);
process.exit(bad ? 1 : 0);
