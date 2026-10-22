// Controles de interacción y límites de DOM de mockup-studio-original.html en jsdom
// (sin navegador, sin servidor, sin build). Usa el jsdom ya instalado en web/node_modules.
// Uso: node specs/019-frontend-studio/mockup-studio-dom-checks.mjs
// jsdom no maqueta: tamaños y texto se simulan; la apariencia se valida aparte en navegador.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const here = fileURLToPath(new URL(".", import.meta.url));
const { JSDOM } = createRequire(
  new URL("../../web/package.json", import.meta.url),
)("jsdom");
const html = readFileSync(here + "mockup-studio-original.html", "utf8");

function boot(
  width = 1440,
  url = "http://localhost/mockup-studio-original.html",
) {
  const errors = [];
  const dom = new JSDOM(html, {
    url,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(w) {
      w.addEventListener("error", (e) => errors.push(e.message));
      w.HTMLCanvasElement.prototype.getContext = () => ({
        font: "",
        measureText: (t) => ({ width: t.length * 6.6 }),
      });
      w.Element.prototype.scrollIntoView = () => {};
      w.Element.prototype.setPointerCapture = () => {};
      w.innerWidth = width;
      w.Element.prototype.getBoundingClientRect = function () {
        const vp = this.id === "graphView";
        return {
          left: 0,
          top: 0,
          x: 0,
          y: 0,
          width: vp ? Math.min(width - 380, 1060) : 0,
          height: vp ? 540 : 0,
          right: 0,
          bottom: 0,
        };
      };
    },
  });
  return { w: dom.window, d: dom.window.document, errors };
}

const results = [];
const check = (name, fn) => {
  try {
    fn();
    results.push([name, "ok", ""]);
  } catch (e) {
    results.push([name, "FALLO", e.message]);
  }
};
const $ = (d, s) => d.querySelector(s),
  $$ = (d, s) => [...d.querySelectorAll(s)];
const key = (el, k) =>
  el.dispatchEvent(
    new el.ownerDocument.defaultView.KeyboardEvent("keydown", {
      key: k,
      bubbles: true,
    }),
  );
const change = (el, v) => {
  el.value = v;
  el.dispatchEvent(
    new el.ownerDocument.defaultView.Event("change", { bubbles: true }),
  );
};
const nodes = (d) => $$(d, "#world .node");
const crumbs = (d) => $$(d, "#scope [data-crumb]").map((b) => b.textContent);
const h2 = (d) => $(d, "#inspector h2").textContent;
const sane = (d, ctx) => {
  const t =
    d.querySelector(".app").textContent + d.querySelector("#world").innerHTML;
  for (const bad of ["undefined", "NaN", "[object Object]", "Infinity"])
    assert.ok(!t.includes(bad), `${ctx}: texto contiene ${bad}`);
  const c = d.defaultView.__caosMockupChecks();
  assert.ok(c.ok, `${ctx}: límites ${JSON.stringify(c)}`);
  assert.ok(
    d.querySelectorAll("*").length < 2500,
    `${ctx}: DOM ${d.querySelectorAll("*").length} elementos`,
  );
};

const { w, d, errors } = boot();
check("demo: red original intacta (14 nodos, selección D-14)", () => {
  assert.equal(nodes(d).length, 14);
  assert.equal(h2(d), "¿Conservar ids de sesión?");
  assert.match(
    $(d, "#gstatus").textContent,
    /Mostrando 14 de 14 elementos y 16 de 16 vínculos/,
  );
  assert.match($(d, "#scope").textContent, /Red completa/);
  assert.equal($(d, "#groupWrap").hidden, true);
  sane(d, "demo");
});
let demoDom = d.querySelectorAll("*").length;

check("demo: cabeceras de grupo pintadas sobre los vínculos y con halo", () => {
  const all = $$(d, "#cam > *"),
    lastEdge = Math.max(
      ...all.map((e, i) => (e.matches(".edge,.edge-hit") ? i : -1)),
    );
  const heads = all.filter((e) => e.matches(".lane-label,.lane-sub,.cont"));
  assert.ok(heads.length >= 4, "faltan cabeceras");
  heads.forEach((h) => {
    assert.ok(
      all.indexOf(h) > lastEdge,
      `«${h.textContent}» queda bajo los vínculos`,
    );
    assert.ok(h.classList.contains("halo"), `«${h.textContent}» sin halo`);
  });
});
check("1M: raíz pinta ≤40 tarjetas y conteos exactos con formato", () => {
  change($(d, "#scaleSel"), "1m");
  assert.ok(
    nodes(d).length > 0 && nodes(d).length <= 40,
    `tarjetas=${nodes(d).length}`,
  );
  assert.match($(d, "#gstatus").textContent, /1\.000\.000/);
  const s = ["sRun", "sWait", "sBlock", "sDone"].map((i) =>
    Number($(d, "#" + i).textContent.replace(/\./g, "")),
  );
  assert.ok(s.every((x) => x > 0) && s.reduce((a, b) => a + b) < 1_000_000);
  assert.deepEqual(crumbs(d), ["Red completa"]);
  assert.ok(
    d.querySelectorAll("*").length < demoDom * 2,
    "DOM a 1M no debe crecer con n",
  );
  sane(d, "1m raíz");
});
let teamKey;
check("1M: clic selecciona (inspector coherente) y Enter abre el grupo", () => {
  const card = nodes(d)[3];
  teamKey = card.dataset.id;
  card.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.equal(h2(d), "Equipo 04");
  assert.equal(
    $(d, `#world .node[data-id="${teamKey}"]`).getAttribute("aria-pressed"),
    "true",
  );
  key($(d, `#world .node[data-id="${teamKey}"]`), "Enter");
  assert.deepEqual(crumbs(d), ["Red completa", "Equipo 04"]);
  assert.ok(nodes(d).length <= 40);
  sane(d, "1m equipo");
});
let agentId;
check("1M: bajar hasta la hoja y abrir la vecindad de un agente", () => {
  for (
    let guard = 0;
    guard < 6 && !nodes(d)[0].dataset.id.startsWith("a:");
    guard++
  ) {
    const c = nodes(d)[1] || nodes(d)[0];
    key(c, "Enter");
    key(d.querySelector(`#world .node[data-id="${c.dataset.id}"]`), "Enter");
  }
  assert.ok(nodes(d)[0].dataset.id.startsWith("a:"), "no se llegó a agentes");
  assert.ok(nodes(d).length <= 40);
  const a = nodes(d)[2];
  key(a, "Enter");
  key(d.querySelector(`#world .node[data-id="${a.dataset.id}"]`), "Enter");
  assert.ok(
    nodes(d).length >= 3 && nodes(d).length <= 12,
    `vecindad=${nodes(d).length}`,
  );
  agentId = crumbs(d).at(-1);
  assert.match(agentId, /^AG-\d{7}$/);
  assert.equal($(d, `#world .node.sel`).dataset.id, agentId);
  assert.ok(
    $$(d, "#world .edge").length > 0,
    "la vecindad debe mostrar vínculos",
  );
  sane(d, "1m vecindad");
});
check(
  "1M: migas vuelven a la raíz y re-seleccionan el equipo de origen",
  () => {
    $(d, '#scope [data-crumb="0"]').dispatchEvent(
      new w.MouseEvent("click", { bubbles: true }),
    );
    assert.deepEqual(crumbs(d), ["Red completa"]);
    assert.equal(h2(d), "Equipo 04");
    assert.equal(
      $(d, `#world .node[data-id="${teamKey}"]`).getAttribute("aria-pressed"),
      "true",
    );
    sane(d, "1m vuelta");
  },
);
check("1M: Retroceso sube un nivel desde el grafo", () => {
  key($(d, `#world .node[data-id="${teamKey}"]`), "Enter");
  assert.equal(crumbs(d).length, 2);
  key(nodes(d)[0], "Backspace");
  assert.equal(crumbs(d).length, 1);
});
check(
  "1M: buscar AG-0500000 + Enter abre su vecindad con la ruta completa",
  () => {
    const q = $(d, "#q");
    q.value = "AG-0500000";
    key(q, "Enter");
    assert.equal(crumbs(d).at(-1), "AG-0500000");
    assert.equal(crumbs(d).length, 5, crumbs(d).join(" / "));
    assert.equal($(d, "#world .node.sel").dataset.id, "AG-0500000");
    sane(d, "1m búsqueda");
  },
);
check(
  "1M: filtro «requiere atención» solo deja grupos con bloqueos o esperas",
  () => {
    $(d, '#scope [data-crumb="0"]').dispatchEvent(
      new w.MouseEvent("click", { bubbles: true }),
    );
    change($(d, "#stateFilter"), "attn");
    nodes(d).forEach((n) =>
      assert.doesNotMatch(
        n.getAttribute("aria-label"),
        /, 0 bloqueados, 0 esperan/,
      ),
    );
    change($(d, "#stateFilter"), "all");
  },
);
check(
  "1M: agrupar por estado → página de 40 agentes bloqueados y paginación",
  () => {
    change($(d, "#groupSel"), "state");
    assert.ok(nodes(d).length <= 5);
    const blk = $(d, '#world .node[data-id="s:block"]');
    key(blk, "Enter");
    key($(d, '#world .node[data-id="s:block"]'), "Enter");
    assert.equal(nodes(d).length, 40);
    nodes(d).forEach((n) =>
      assert.match(n.getAttribute("aria-label"), /Bloqueada/),
    );
    $(d, '#gstatus [data-page="1"]').dispatchEvent(
      new w.MouseEvent("click", { bubbles: true }),
    );
    assert.match($(d, "#gstatus").textContent, /41–80 de/);
    assert.match(crumbs(d).at(-1), /pág\. 2/);
    sane(d, "1m estado");
  },
);
check("1M: agrupar por proyecto conserva el total", () => {
  change($(d, "#groupSel"), "project");
  const tot = nodes(d)
    .map((n) =>
      Number(
        n
          .getAttribute("aria-label")
          .match(/: ([\d.]+) agentes/)[1]
          .replace(/\./g, ""),
      ),
    )
    .reduce((a, b) => a + b);
  assert.equal(tot, 1_000_000);
  change($(d, "#groupSel"), "team");
});
check("1M: vista Lista acotada y decisiones/actividad paginadas", () => {
  $(d, "#tabList").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  /* dos tablas desde T082: ventana de elementos (≤40) y ventana de Vínculos (≤10) */
  assert.ok($$(d, "#listView tbody tr[data-item]").length <= 40);
  assert.ok($$(d, "#listView tbody tr[data-bundle]").length <= 10);
  assert.ok($$(d, "#listView tbody tr").length <= 51);
  assert.ok($$(d, "#decisions article").length <= 3);
  assert.ok($$(d, "#feed li").length <= 9);
  const first = $(d, "#feed time").textContent;
  $(d, '#feed [data-fpage="1"]').dispatchEvent(
    new w.MouseEvent("click", { bubbles: true }),
  );
  assert.notEqual($(d, "#feed time").textContent, first);
  $(d, "#tabGraph").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  sane(d, "1m lista");
});
check("1M: responder una decisión es local y queda registrada", () => {
  const art = $(d, "#decisions article.q:not(.answered)");
  const id = art.querySelector("[data-answer]").dataset.answer;
  art.querySelector("input").checked = true;
  art
    .querySelector("[data-answer]")
    .dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.match(
    $(d, "#decisions").textContent,
    new RegExp(id + "[\\s\\S]*Respuesta registrada"),
  );
});
check("volver a demostración restaura la red original", () => {
  change($(d, "#scaleSel"), "demo");
  assert.equal(nodes(d).length, 14);
  assert.equal(h2(d), "¿Conservar ids de sesión?");
  sane(d, "demo de vuelta");
});
for (const [v, n] of [
  ["100", 100],
  ["10k", 10000],
]) {
  check(`${v}: raíz → agente en pocos pasos, todo acotado`, () => {
    const b = boot(
      1440,
      `http://localhost/mockup-studio-original.html?escala=${v}`,
    );
    assert.match(
      b.d.querySelector("#gstatus").textContent,
      new RegExp(n.toLocaleString("es-ES").replace(".", "\\.")),
    );
    for (let g = 0; g < 6 && !nodes(b.d)[0].dataset.id.startsWith("a:"); g++) {
      const c = nodes(b.d)[0];
      key(c, "Enter");
      key(
        b.d.querySelector(`#world .node[data-id="${c.dataset.id}"]`),
        "Enter",
      );
    }
    const a = nodes(b.d)[0];
    key(a, "Enter");
    key(b.d.querySelector(`#world .node[data-id="${a.dataset.id}"]`), "Enter");
    assert.match(crumbs(b.d).at(-1), /^AG-/);
    sane(b.d, v);
    assert.deepEqual(b.errors, []);
  });
}
check("móvil (390 px): 1M arranca acotado y sin errores", () => {
  const b = boot(390, "http://localhost/mockup-studio-original.html?escala=1m");
  assert.ok(nodes(b.d).length <= 40);
  sane(b.d, "móvil");
  assert.deepEqual(b.errors, []);
});
check("escala desconocida o heredada conserva la demostración", () => {
  for (const value of ["toString", "constructor", "desconocida"]) {
    const b = boot(
      1440,
      `http://localhost/mockup-studio-original.html?escala=${value}`,
    );
    assert.equal(nodes(b.d).length, 14, value);
    assert.equal(b.d.querySelector("#scaleSel").value, "demo", value);
    assert.deepEqual(b.errors, [], value);
    sane(b.d, value);
    b.w.close();
  }
});
/* ---- T082: haces dirigidos en todos los niveles ---- */
const click = (el) =>
  el.dispatchEvent(
    new el.ownerDocument.defaultView.MouseEvent("click", { bubbles: true }),
  );
const hits = (d) => $$(d, "#world .edge-hit[data-bundle]");
const shown = (d, ctx) => {
  const h = hits(d);
  assert.ok(h.length > 0, `${ctx}: sin líneas`);
  assert.ok(h.length <= 24, `${ctx}: ${h.length} haces`);
  assert.ok(
    $$(d, "#world path.edge").length >= h.length,
    `${ctx}: haz sin trazo`,
  );
  assert.match($(d, "#gstatus").textContent, /Mostrando \d+ de \d+ haces/, ctx);
  h.forEach((p) => {
    assert.match(p.getAttribute("aria-label"), /→/, `${ctx}: sin dirección`);
    assert.equal(p.getAttribute("tabindex"), "0", `${ctx}: haz sin teclado`);
  });
  sane(d, ctx);
};
const enterTwice = (d, k) => {
  key($(d, `#world .node[data-id="${k}"]`), "Enter");
  key($(d, `#world .node[data-id="${k}"]`), "Enter");
};
for (const width of [1440, 390]) {
  check(
    `T082 ${width}px 1M: líneas en raíz, equipo, subequipo, célula y hoja; vecindad del generador`,
    () => {
      const b = boot(
        width,
        "http://localhost/mockup-studio-original.html?escala=1m",
      );
      shown(b.d, "raíz");
      const levels = [];
      while (!nodes(b.d)[0].dataset.id.startsWith("a:")) {
        enterTwice(b.d, nodes(b.d)[1].dataset.id);
        levels.push(crumbs(b.d).at(-1));
        shown(b.d, crumbs(b.d).join("/"));
        assert.ok(levels.length < 6);
      }
      assert.ok(levels.length >= 3, levels.join(","));
      assert.ok(
        $$(b.d, "#world .port").length > 0,
        "la hoja debe mostrar puertos externos",
      );
      enterTwice(b.d, nodes(b.d)[3].dataset.id);
      const atomEdges = $$(b.d, "#world .edge-hit").filter((p) =>
        /^R[\d.]+-/.test(p.dataset.edge),
      );
      assert.ok(atomEdges.length > 0, "vecindad sin vínculos del generador");
      assert.match(
        $(b.d, "#gstatus").textContent,
        /Relaciones del agente: \d+ de \d+/,
      );
      sane(b.d, "vecindad");
      assert.deepEqual(b.errors, []);
    },
  );
}
check(
  "T082 1M: proyectos y estado (con página) también muestran haces; estado avisa alcance parcial",
  () => {
    const b = boot(
      1440,
      "http://localhost/mockup-studio-original.html?escala=1m",
    );
    change($(b.d, "#groupSel"), "project");
    shown(b.d, "proyectos");
    enterTwice(b.d, nodes(b.d)[0].dataset.id);
    shown(b.d, "proyecto");
    change($(b.d, "#groupSel"), "state");
    shown(b.d, "estados");
    assert.match($(b.d, "#gstatus").textContent, /alcance parcial/);
    enterTwice(b.d, "s:block");
    shown(b.d, "página de bloqueados");
    assert.match($(b.d, "#gstatus").textContent, /alcance parcial/);
    assert.deepEqual(b.errors, []);
  },
);
check(
  "T082 1M: haz seleccionable con origen, destino, tipo, conteo exacto y pares paginados",
  () => {
    const b = boot(
      1440,
      "http://localhost/mockup-studio-original.html?escala=1m",
    );
    const h = hits(b.d).find((p) => +p.dataset.count > 0) || hits(b.d)[0];
    click(h);
    const t = $(b.d, "#inspector").textContent;
    assert.match(t, /Haz de vínculos/);
    for (const w of ["Origen", "Destino", "Tipo", "Significado", "modelados"])
      assert.ok(t.includes(w), w);
    assert.ok(
      $$(b.d, "#inspector [data-agent][data-atom]").length > 0,
      "pares sin acceso a vecindad",
    );
    assert.ok($$(b.d, "#inspector .atoms li").length <= 6);
    assert.ok($(b.d, `#world .edge.esel`), "trazo seleccionado");
    /* teclado sobre otro haz */
    const other = hits(b.d).find((p) => p.dataset.bundle !== h.dataset.bundle);
    key(other, "Enter");
    assert.ok($(b.d, "#inspector").textContent.includes(other.dataset.bundle));
    sane(b.d, "haz");
    assert.deepEqual(b.errors, []);
  },
);
check(
  "T082 1M: pares atómicos paginan y bajar conserva el id del vínculo como puerto",
  () => {
    const b = boot(
      1440,
      "http://localhost/mockup-studio-original.html?escala=1m",
    );
    change($(b.d, "#groupSel"), "project");
    const big = hits(b.d).sort((x, y) => y.dataset.count - x.dataset.count)[0];
    click(big);
    if (+big.dataset.count > 6) {
      click($(b.d, '#inspector [data-apage="1"]'));
      assert.match($(b.d, "#inspector").textContent, /7–/);
    }
    const atom = $(b.d, "#inspector [data-atom]").dataset.atom;
    click($(b.d, "#inspector [data-down]"));
    assert.equal(crumbs(b.d).length, 2);
    assert.ok(
      $(b.d, "#inspector").textContent.includes(atom),
      `${atom} no aparece al bajar`,
    );
    /* subir conserva la selección del grupo del que se sale */
    click($(b.d, '#scope [data-crumb="0"]'));
    assert.equal(crumbs(b.d).length, 1);
    assert.equal($$(b.d, "#world .node.sel").length, 1);
    assert.deepEqual(b.errors, []);
  },
);
check(
  "T082 1M: par atómico abre la vecindad con ese mismo vínculo seleccionado",
  () => {
    const b = boot(
      1440,
      "http://localhost/mockup-studio-original.html?escala=1m",
    );
    click(hits(b.d)[0]);
    const a = $(b.d, "#inspector [data-atom]"),
      atom = a.dataset.atom;
    click(a);
    assert.match(crumbs(b.d).at(-1), /^AG-/);
    assert.ok(
      $(b.d, `#world .edge-hit[data-edge="${atom}"]`),
      `${atom} no dibujado`,
    );
    assert.match(h2(b.d), new RegExp(crumbs(b.d).at(-1)));
    assert.deepEqual(b.errors, []);
  },
);
check(
  "T082 1M: seleccionar un grupo focaliza sus vínculos y el inspector los separa",
  () => {
    const b = boot(
      1440,
      "http://localhost/mockup-studio-original.html?escala=1m",
    );
    const k = nodes(b.d)[2].dataset.id;
    click(nodes(b.d)[2]);
    const mine = hits(b.d).filter(
      (p) => p.dataset.from === k || p.dataset.to === k,
    );
    assert.ok(mine.length > 0, "grupo sin haces visibles");
    hits(b.d).forEach((p) => {
      const edge = p.previousElementSibling;
      assert.equal(
        edge.classList.contains("dim"),
        !(p.dataset.from === k || p.dataset.to === k),
      );
    });
    const t = $(b.d, "#inspector").textContent;
    assert.match(t, /Salientes · \d+/);
    assert.match(t, /Entrantes · \d+/);
    assert.match(t, /Externos · \d+/);
    assert.ok(
      $(b.d, "#inspector button[data-bundle]"),
      "inspector sin acceso a haces",
    );
  },
);
check("T082 1M: filtro de tipo separado de los tipos de elemento", () => {
  const b = boot(
    1440,
    "http://localhost/mockup-studio-original.html?escala=1m",
  );
  assert.ok(hits(b.d).some((p) => p.dataset.ty === "delega"));
  click($(b.d, '#gtools [data-ety="delega"]'));
  assert.equal(
    $(b.d, '#gtools [data-ety="delega"]').getAttribute("aria-pressed"),
    "false",
  );
  assert.ok(!hits(b.d).some((p) => p.dataset.ty === "delega"));
  assert.ok(hits(b.d).length > 0);
  assert.ok($$(b.d, ".chip[data-kind]").every((c) => c.disabled));
  click($(b.d, "#gstatus [data-reset]"));
  assert.ok(hits(b.d).some((p) => p.dataset.ty === "delega"));
});
check("T082 1M: tabla Vínculos accesible, paginada y equivalente", () => {
  const b = boot(
    1440,
    "http://localhost/mockup-studio-original.html?escala=1m",
  );
  const total = Number(
    $(b.d, "#gstatus").textContent.match(/de (\d+) haces/)[1],
  );
  click($(b.d, "#tabList"));
  const rows = $$(b.d, "#listView tr[data-bundle]");
  assert.ok(rows.length > 0 && rows.length <= 10);
  assert.match($(b.d, "#listView").textContent, new RegExp(`de ${total}`));
  if (total > 10) {
    click($(b.d, '#listView [data-lpage="1"]'));
    assert.notEqual(
      $(b.d, "#listView tr[data-bundle]").dataset.bundle,
      rows[0].dataset.bundle,
    );
  }
  const r = $(b.d, "#listView tr[data-bundle]");
  key(r, "Enter");
  assert.ok($(b.d, "#inspector").textContent.includes(r.dataset.bundle));
  assert.equal(b.d.activeElement.dataset.bundle, r.dataset.bundle);
  sane(b.d, "lista vínculos");
});
check(
  "T083: seleccionar desde otra página prioriza el haz al volver al grafo",
  () => {
    const b = boot(
      1440,
      "http://localhost/mockup-studio-original.html?escala=1m",
    );
    click($(b.d, "#tabList"));
    click($(b.d, '#listView [data-lpage="1"]'));
    const row = $$(b.d, "#listView tr[data-bundle]").at(-1);
    key(row, "Enter");
    click($(b.d, "#tabGraph"));
    assert.ok(
      hits(b.d).some((p) => p.dataset.bundle === row.dataset.bundle),
      "haz seleccionado fuera de la ventana dibujada",
    );
    b.w.close();
  },
);
check("T083: los extremos filtrados no se describen como externos", () => {
  const b = boot(
    1440,
    "http://localhost/mockup-studio-original.html?escala=1m",
  );
  click(hits(b.d)[0]);
  const q = $(b.d, "#q");
  q.value = "__no_matching_group__";
  q.dispatchEvent(new b.w.Event("input", { bubbles: true }));
  assert.match($(b.d, "#inspector").textContent, /oculto por los filtros/);
  assert.doesNotMatch($(b.d, "#inspector").textContent, /fuera de este nivel/);
  b.w.close();
});
check(
  "T083: el icono del tipo conserva tamaño propio fuera de la leyenda",
  () => {
    const b = boot(
      1440,
      "http://localhost/mockup-studio-original.html?escala=1m",
    );
    click(hits(b.d)[0]);
    const icon = $(b.d, "#inspector dl svg");
    assert.equal(icon.getAttribute("width"), "24");
    assert.equal(icon.getAttribute("height"), "10");
    b.w.close();
  },
);
check(
  "T083: bajar por estado lleva a la página del extremo seleccionado",
  () => {
    const b = boot(
      1440,
      "http://localhost/mockup-studio-original.html?escala=1m",
    );
    change($(b.d, "#groupSel"), "state");
    let chosen;
    for (const hit of hits(b.d)) {
      click(hit);
      const pair = $$(b.d, "#inspector .atoms li button[data-agent]").slice(
        0,
        2,
      );
      for (const [j, side] of [hit.dataset.from, hit.dataset.to].entries()) {
        const index = Number(pair[j].dataset.agent);
        const firstPage = b.w.eval(
          `M.pageByState(M.root(),${JSON.stringify(side.slice(2))},0,40).rows.map(a=>a.idx)`,
        );
        if (!firstPage.includes(index)) {
          chosen = { side, index, atom: pair[j].dataset.atom };
          break;
        }
      }
      if (chosen) break;
    }
    assert.ok(chosen, "falta caso con extremo fuera de la primera página");
    const down = $$(b.d, "#inspector [data-down]").find(
      (x) => x.dataset.down === chosen.side,
    );
    click(down);
    assert.ok(
      nodes(b.d).some((x) => x.dataset.id === "a:" + chosen.index),
      "el extremo quedó fuera de página",
    );
    assert.ok(
      $(b.d, "#inspector").textContent.includes(chosen.atom),
      "se perdió el vínculo atómico al bajar",
    );
    b.w.close();
  },
);
check(
  "T083: bajar y Vecindad usan el primer par de la página atómica actual",
  () => {
    const b = boot(
      1440,
      "http://localhost/mockup-studio-original.html?escala=1m",
    );
    change($(b.d, "#groupSel"), "state");
    const hit = hits(b.d).find((h) => Number(h.dataset.count) > 6);
    assert.ok(hit, "falta haz con más de una página de pares");
    click(hit);
    click($(b.d, '#inspector [data-apage="1"]'));
    const first = $(b.d, "#inspector .atoms li button[data-atom]").dataset.atom;
    for (const x of $$(b.d, "#inspector .actions [data-atom]"))
      assert.equal(x.dataset.atom, first);
    assert.match($(b.d, "#inspector").textContent, /Significado/);
    b.w.close();
  },
);
check("sin errores de script en toda la sesión", () =>
  assert.deepEqual(errors, []),
);

const wd = Math.max(...results.map((r) => r[0].length));
results.forEach(([n, s, i]) =>
  console.log(`${s === "ok" ? "  ok  " : "FALLO "} ${n.padEnd(wd)}  ${i}`),
);
const bad = results.filter((r) => r[1] !== "ok").length;
console.log(`\n${results.length - bad}/${results.length} controles superados`);
process.exit(bad ? 1 : 0);
