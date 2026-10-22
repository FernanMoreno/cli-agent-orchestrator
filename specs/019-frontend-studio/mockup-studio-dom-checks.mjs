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
  assert.ok($$(d, "#listView tbody tr").length <= 40);
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
