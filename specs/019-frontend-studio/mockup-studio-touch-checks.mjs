// Real touch-gesture checks (Chromium CDP Input.dispatchTouchEvent) for the graph canvas at 375 px.
// Env: CAOS_MOCKUP_URL (served folder), CAOS_PLAYWRIGHT_REQUIRE (package.json resolving @playwright/test),
// CAOS_CHROMIUM (executable), CAOS_MOCKUP_OUTPUT (evidence directory).
import {createRequire} from 'node:module';
import {writeFileSync} from 'node:fs';
const baseUrl=process.env.CAOS_MOCKUP_URL||'http://localhost:8767';
const out=process.env.CAOS_MOCKUP_OUTPUT||'.';
const {chromium}=createRequire(process.env.CAOS_PLAYWRIGHT_REQUIRE)('@playwright/test');
const browser=await chromium.launch({executablePath:process.env.CAOS_CHROMIUM,headless:true});
const checks={};let failed=0;
function assert(v,m){if(!v)throw Error(m)}
async function check(name,fn){try{checks[name]={pass:true,info:await fn()}}catch(e){failed++;checks[name]={pass:false,info:e.message}}}
try{
  const context=await browser.newContext({viewport:{width:375,height:812},deviceScaleFactor:1,hasTouch:true,isMobile:true});
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const cdp=await context.newCDPSession(page);
  const touch=async(type,pts)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:pts.map(([x,y])=>({x,y}))});
  // One-finger swipe in small steps, like a real drag.
  async function swipe(x0,y0,x1,y1,steps=12){await touch('touchStart',[[x0,y0]]);for(let i=1;i<=steps;i++)await touch('touchMove',[[x0+(x1-x0)*i/steps,y0+(y1-y0)*i/steps]]);await touch('touchEnd',[]);await page.waitForTimeout(350)}
  const cam=()=>page.evaluate(()=>document.querySelector('#cam').getAttribute('transform'));
  const vpBox=async()=>page.locator('#graphView').boundingBox();
  await page.goto(baseUrl+'/mockup-studio-original.html');await page.evaluate(()=>document.fonts.ready);
  await page.selectOption('#scaleSel','1m');await page.selectOption('#groupSel','team');
  await check('canvas-touch-action-none',async()=>{const t=await page.evaluate(()=>getComputedStyle(document.querySelector('#graphView')).touchAction);assert(t==='none',t);return t});
  await check('touch-vertical-pan-moves-camera-not-page',async()=>{await page.locator('#graphView').scrollIntoViewIfNeeded();const b=await vpBox(),y0=await page.evaluate(()=>scrollY),c0=await cam();
    await swipe(b.x+b.width/2,b.y+b.height-30,b.x+b.width/2,b.y+40);const c1=await cam(),y1=await page.evaluate(()=>scrollY);
    assert(c0!==c1,'camera unchanged');assert(y0===y1,`page scrolled ${y0}->${y1}`);return {c0,c1,scrollY:y1}});
  await check('touch-pan-reaches-and-selects-last-card',async()=>{const ids=await page.evaluate(()=>[...document.querySelectorAll('#world .node')].map(g=>g.dataset.id));const last=ids[ids.length-1];
    const loc=page.locator(`#world .node[data-id="${last}"]`);let swipes=0;
    for(;;){const b=await vpBox(),n=await loc.boundingBox();const cx=n.x+n.width/2,cy=n.y+n.height/2;
      if(cy>b.y+20&&cy<b.y+b.height-60&&cx>b.x+10&&cx<b.x+b.width-10)break;
      assert(++swipes<=60,'card never reached by touch pan');
      const dx=Math.max(-250,Math.min(250,(b.x+b.width/2)-cx)),dy=Math.max(-300,Math.min(300,(b.y+b.height/2)-cy));
      await swipe(b.x+b.width/2-dx/2,b.y+b.height/2-dy/2,b.x+b.width/2+dx/2,b.y+b.height/2+dy/2)}
    await page.waitForTimeout(600);/* let the swipe's gesture settle, as a user lifting and re-tapping would */const n=await loc.boundingBox();await page.evaluate(()=>{window.__ev=[];for(const t of ['pointerdown','pointerup','pointercancel','click','touchstart','touchend'])document.addEventListener(t,e=>__ev.push(t+':'+(e.target.getAttribute&&e.target.getAttribute('class'))),true)});await page.touchscreen.tap(n.x+n.width/2,n.y+n.height/2);await page.waitForTimeout(700);const ev=await page.evaluate(()=>__ev);
    await page.waitForTimeout(50);const sel=await page.evaluate(()=>document.querySelector('#detail')?.innerText||document.body.innerText);
    const zoom=await page.locator('#zoomPct').innerText();assert(Number(zoom.replace('%',''))>=90,'zoom below 90%: '+zoom);
    const selected=await loc.evaluate(g=>g.classList.contains('sel')||g.getAttribute('aria-pressed')==='true'||g.getAttribute('aria-selected')==='true'||g.getAttribute('aria-current')!==null);
    const pressed=await page.evaluate(()=>[...document.querySelectorAll('#world .node[aria-pressed=true]')].map(g=>g.dataset.id));const hit=await page.evaluate(([x,y])=>{const e=document.elementFromPoint(x,y);return e?e.outerHTML.slice(0,120):null},[n.x+n.width/2,n.y+n.height/2]);assert(selected,'tapped card not selected: '+JSON.stringify({last,swipes,n,vp:await vpBox(),inner:await page.evaluate(()=>[innerWidth,innerHeight,scrollY,visualViewport.scale,visualViewport.offsetTop]),pressed,hit,ev}));await page.screenshot({path:out+'/mockup-studio-touch-1m-last-375.png',fullPage:false});return {last,swipes,zoom}});
  await check('touch-tap-activates-exactly-once',async()=>{await page.waitForTimeout(900);const b=await vpBox();const id=await page.evaluate(()=>[...document.querySelectorAll('#world .node')].find(g=>g.getAttribute('aria-pressed')!=='true'&&(r=>r.top>document.querySelector('#graphView').getBoundingClientRect().top+10&&r.bottom<document.querySelector('#graphView').getBoundingClientRect().bottom-60&&r.left>0&&r.right<innerWidth)(g.getBoundingClientRect()))?.dataset.id);assert(id,'no other card fully visible');const n=await page.locator(`#world .node[data-id="${id}"]`).boundingBox();
    await page.evaluate(()=>{window.__clicks=0;document.addEventListener('click',()=>__clicks++)});await page.touchscreen.tap(n.x+n.width/2,n.y+n.height/2);await page.waitForTimeout(900);
    const c=await page.evaluate(()=>__clicks),p=await page.locator('#world .node[aria-pressed=true]').count();assert(c===1,'click handled '+c+' times');assert(p===1,'pressed '+p);return {id,clicks:c,pressed:p}});
  // Tap-dedup cases (review 2026-10-23): the compat-click suppression must belong only to its own gesture/target.
  const visibleCard=async()=>{const id=await page.evaluate(()=>{const v=document.querySelector('#graphView').getBoundingClientRect();return [...document.querySelectorAll('#world .node')].find(g=>g.getAttribute('aria-pressed')!=='true'&&(r=>r.top>v.top+10&&r.bottom<v.bottom-60&&r.left>0&&r.right<innerWidth)(g.getBoundingClientRect()))?.dataset.id});assert(id,'no unselected card fully visible');const n=await page.locator(`#world .node[data-id="${id}"]`).boundingBox();return {id,x:n.x+n.width/2,y:n.y+n.height/2}};
  const pressedIds=()=>page.evaluate(()=>[...document.querySelectorAll('#world .node[aria-pressed=true]')].map(g=>g.dataset.id));
  const zoomPct=async()=>Number((await page.locator('#zoomPct').innerText()).replace('%',''));
  // Emulate "first tap receives no compat click": a one-shot window capture listener swallows the browser's compat click of
  // the next tap before the page sees it (touchend.preventDefault does not stop it in headless Chromium 1243).
  const dropNextCompatClick=()=>page.evaluate(()=>{window.__dropped=0;const f=e=>{if(e.isTrusted){__dropped++;e.stopImmediatePropagation();e.preventDefault();removeEventListener('click',f,true)}};addEventListener('click',f,true)});
  await check('tap-without-compat-click-then-trusted-click-other-card-under-800ms',async()=>{await page.waitForTimeout(900);const c=await visibleCard();await dropNextCompatClick();
    await page.touchscreen.tap(c.x,c.y);await page.waitForTimeout(150);assert(await page.evaluate(()=>__dropped)===1,'compat click not dropped');const p0=await pressedIds();assert(p0.includes(c.id),'first tap did not select '+c.id);
    const d=await visibleCard();const t0=Date.now();/* trusted CDP pointer click on another logical target inside the canvas (headless Chromium 1243 emits no compat click for a quick second touch tap here, old and new HTML alike) */await page.mouse.click(d.x,d.y);const dt=Date.now()-t0+150;await page.waitForTimeout(400);
    const p1=await pressedIds();assert(p1.length===1&&p1[0]===d.id,`second target eaten ${dt} ms after first tap: `+JSON.stringify({first:c.id,second:d.id,pressed:p1}));return {first:c.id,second:d.id,secondClickAfterMs:dt,droppedCompatClicks:await page.evaluate(()=>__dropped)}});
  await check('tap-without-compat-click-then-keyboard-control-under-800ms',async()=>{await page.waitForTimeout(900);const c=await visibleCard();await dropNextCompatClick();
    await page.touchscreen.tap(c.x,c.y);await page.waitForTimeout(100);assert(await page.evaluate(()=>__dropped)===1,'compat click not dropped');const z0=await zoomPct();await page.locator('.zoombar [data-zoom="in"]').focus();await page.keyboard.press('Enter');await page.waitForTimeout(400);
    const z1=await zoomPct();assert(z1>z0,`keyboard activation eaten (${z0}->${z1})`);await page.keyboard.press('Enter');await page.locator('.zoombar [data-zoom="out"]').focus();await page.keyboard.press('Enter');await page.keyboard.press('Enter');await page.waitForTimeout(300);return {card:c.id,z0,z1}});
  await check('double-tap-same-card-activates-each-tap-once',async()=>{await page.waitForTimeout(900);const c=await visibleCard();
    await page.evaluate(()=>{window.__bub=0;document.addEventListener('click',e=>{if(e.target.closest&&e.target.closest('.node'))__bub++})});
    await page.touchscreen.tap(c.x,c.y);await page.waitForTimeout(120);const c2=await page.locator(`#world .node[data-id="${c.id}"]`).boundingBox();await page.touchscreen.tap(c2.x+c2.width/2,c2.y+c2.height/2);await page.waitForTimeout(900);
    const n=await page.evaluate(()=>__bub);assert(n===2,'card activations for 2 taps: '+n);return {card:c.id,activations:n,pressed:await pressedIds()}});
  await check('pointercancel-clears-pending-tap-next-tap-selects-once',async()=>{await page.waitForTimeout(900);const a=await visibleCard();
    await touch('touchStart',[[a.x,a.y]]);await touch('touchCancel',[]);await page.waitForTimeout(100);
    const b=await visibleCard();await page.evaluate(()=>{window.__bub2=0;document.addEventListener('click',e=>{if(e.target.closest&&e.target.closest('.node'))__bub2++})});
    await page.touchscreen.tap(b.x,b.y);await page.waitForTimeout(900);const n=await page.evaluate(()=>__bub2),p=await pressedIds();
    assert(n===1,'activations '+n);assert(p.length===1&&p[0]===b.id,'pressed '+JSON.stringify(p));return {cancelled:a.id,tapped:b.id,activations:n}});
  await check('touch-outside-canvas-scrolls-page',async()=>{await page.evaluate(()=>scrollTo(0,0));const y0=await page.evaluate(()=>scrollY);
    const b=await vpBox();const yStart=Math.min(800,b.y+b.height+60);await swipe(187,yStart,187,Math.max(10,yStart-400),16);await page.waitForTimeout(300);
    const y1=await page.evaluate(()=>scrollY);assert(y1>y0,`page did not scroll from outside canvas (${y0}->${y1}, start ${yStart})`);return {y0,y1,yStart}});
  await check('no-page-errors',async()=>{assert(!errors.length,errors.join('|'));return 0});
  await context.close();
}finally{await browser.close()}
writeFileSync(out+'/mockup-studio-touch-checks.json',JSON.stringify({checks,failed},null,1));
console.log(JSON.stringify({checks,failed},null,1));process.exit(failed?1:0);
