(() => {
  'use strict';

  // ---- Design (hier anpassen) ------------------------------------------------
  const RED_CENTER = '#e3152b';      // Hintergrund-Verlauf: Mitte
  const RED_EDGE = '#96000f';        // Hintergrund-Verlauf: Rand
  const EAGLE_ALPHA = 0.13;          // Stärke des Falken im Hintergrund
  const W = 1080;
  const FALLBACK_TEAMS = [
    'Eckernförder MTV', 'HSG Gettorf/Osdorf II', 'MTV Dänischenhagen III', 'SC Kieler Förde II',
    'SG Bordesholm/Brügge II', 'SG Handball Eidertal', 'SG Handball Neumünster III',
    'Suchsdorfer SV III', 'TSV Altenholz',
  ];
  const HEADLINES = {
    schluss: 'SCHLUSSSPIELSTAND',
    abpfiff: 'ABPFIFF',
    sieg: 'SIEG!',
    auto: null,
  };

  const $ = (id) => document.getElementById(id);
  const canvas = $('cv');
  const ctx = canvas.getContext('2d');
  const state = {
    teams: [], opp: 0, home: true, us: 0, them: 0,
    ht: false, htUs: 0, htThem: 0, head: 'schluss', fmt: '4:5',
  };
  const imgs = {};

  // ---- Speicher (nur Komfort, darf fehlen) -----------------------------------
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* egal */ } },
  };

  // ---- Laden -----------------------------------------------------------------
  function loadImg(src) {
    return new Promise((resolve) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => resolve(null);
      im.src = src;
    });
  }

  async function loadFonts() {
    const specs = ['ExtraBoldItalic', 'BoldItalic', 'MediumItalic'];
    await Promise.all(specs.map((s) => document.fonts.load(`60px BC${s}`).catch(() => null)));
  }

  async function init() {
    let teams = [];
    try {
      const r = await fetch('teams.json', { cache: 'no-cache' });
      teams = (await r.json()).teams || [];
    } catch (e) { /* Rückfall unten */ }
    if (!teams.length) teams = FALLBACK_TEAMS.map((name) => ({ name, crest: null }));
    state.teams = teams.slice().sort((a, b) => a.name.localeCompare(b.name, 'de'));

    const sel = $('opp');
    state.teams.forEach((t, i) => {
      const o = document.createElement('option');
      o.value = i;
      o.textContent = t.name;
      sel.appendChild(o);
    });

    const saved = store.get('adler.opp');
    const idx = state.teams.findIndex((t) => t.name === saved);
    state.opp = idx >= 0 ? idx : 0;
    sel.value = state.opp;
    if (store.get('adler.fmt') === '9:16') state.fmt = '9:16';
    if (store.get('adler.home') === '0') state.home = false;
    if (HEADLINES[store.get('adler.head')] !== undefined) state.head = store.get('adler.head');
    syncControls();

    await Promise.all([
      loadFonts(),
      loadImg('img/adler.png').then((im) => { imgs.adler = im; }),
      loadImg('img/eagle.png').then((im) => { imgs.eagle = im; }),
      ...state.teams.map((t) => (t.crest ? loadImg(t.crest).then((im) => { imgs[t.name] = im; }) : null)),
    ]);
    draw();
    $('loading').hidden = true;
  }

  // ---- Zeichnen --------------------------------------------------------------
  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // Text mittig; y = Oberkante der Zeile. Schrift wird verkleinert, bis sie in maxW passt.
  function text(str, cx, y, style, start, maxW, color) {
    let size = start;
    for (; size > 20; size -= 2) {
      ctx.font = `${size}px BC${style}`;
      if (ctx.measureText(str).width <= maxW) break;
    }
    ctx.font = `${start}px BC${style}`;                    // Grundlinie nach Startgröße -> Namen stehen auf einer Linie
    const asc = ctx.measureText(str).fontBoundingBoxAscent || start * 0.9;
    ctx.font = `${size}px BC${style}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = color || '#fff';
    ctx.fillText(str, cx, y + asc);
  }

  function crestCard(cx, y, size, im, name) {
    const x = cx - size / 2;
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,.38)';
    ctx.shadowBlur = 30;
    ctx.shadowOffsetY = 10;
    ctx.fillStyle = '#fff';
    roundRect(x, y, size, size, 36);
    ctx.fill();
    ctx.restore();
    const box = size - 68;
    if (im) {
      const k = Math.min(box / im.width, box / im.height);
      const w = im.width * k, h = im.height * k;
      ctx.drawImage(im, cx - w / 2, y + (size - h) / 2, w, h);
    } else {                                     // Monogramm, falls kein Wappen vorhanden ist
      ctx.fillStyle = '#0e0e0e';
      ctx.beginPath();
      ctx.arc(cx, y + size / 2, box / 2, 0, Math.PI * 2);
      ctx.fill();
      const ini = name.split(/\s+/).slice(0, 2).map((p) => p[0]).join('').toUpperCase();
      text(ini, cx, y + size / 2 - 70, 'ExtraBoldItalic', 190, box - 40);
    }
  }

  function headline() {
    if (state.head !== 'auto') return HEADLINES[state.head];
    if (state.us > state.them) return 'SIEG!';
    return state.us === state.them ? 'UNENTSCHIEDEN' : 'ABPFIFF';
  }

  function draw() {
    const tall = state.fmt === '9:16';
    const H = tall ? 1920 : 1350;
    const YF = tall ? { dy: 240, s: 1.12 } : { dy: 0, s: 1 };
    const Y = (y) => Math.round(YF.dy + 90 + (y - 90) * YF.s);
    canvas.width = W;
    canvas.height = H;

    // Hintergrund: roter Verlauf + Falke
    const g = ctx.createRadialGradient(W / 2, H * 0.42, 80, W / 2, H * 0.42, H * 0.85);
    g.addColorStop(0, RED_CENTER);
    g.addColorStop(1, RED_EDGE);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
    if (imgs.eagle) {
      const eh = tall ? 1750 : 1400;
      ctx.save();
      ctx.globalAlpha = EAGLE_ALPHA;
      ctx.drawImage(imgs.eagle, tall ? -40 : -10, tall ? 342 : 190, imgs.eagle.width * eh / imgs.eagle.height, eh);
      ctx.restore();
    }

    const opp = state.teams[state.opp] || { name: 'Gegner' };
    const usName = 'FT Adler Kiel';
    const left = state.home ? { name: usName, im: imgs.adler, goals: state.us, ht: state.htUs }
                            : { name: opp.name, im: imgs[opp.name], goals: state.them, ht: state.htThem };
    const right = state.home ? { name: opp.name, im: imgs[opp.name], goals: state.them, ht: state.htThem }
                             : { name: usName, im: imgs.adler, goals: state.us, ht: state.htUs };

    text(headline(), W / 2, Y(90), 'ExtraBoldItalic', 170, 940);

    const size = 360, cy = Y(330);
    crestCard(270, cy, size, left.im, left.name);
    crestCard(810, cy, size, right.im, right.name);
    text(left.name.toUpperCase(), 270, cy + size + 36, 'BoldItalic', 66, 470);
    text(right.name.toUpperCase(), 810, cy + size + 36, 'BoldItalic', 66, 470);

    text(`${left.goals} : ${right.goals}`, W / 2, Y(830), 'ExtraBoldItalic', 300, 900);
    if (state.ht) {
      text(`HALBZEIT  ${left.ht}:${right.ht}`, W / 2, Y(1200), 'MediumItalic', 48, 900);
    }
    $('oppName').textContent = opp.name;
    $('usName').textContent = usName;
  }

  // ---- Bedienung -------------------------------------------------------------
  function clampNum(v) {
    v = parseInt(v, 10);
    return Number.isFinite(v) ? Math.max(0, Math.min(99, v)) : 0;
  }

  function syncControls() {
    $('us').value = state.us;
    $('them').value = state.them;
    $('htUs').value = state.htUs;
    $('htThem').value = state.htThem;
    $('htOn').checked = state.ht;
    $('htBox').hidden = !state.ht;
    $('head').value = state.head;
    document.querySelectorAll('[data-home]').forEach((b) => {
      b.setAttribute('aria-pressed', String((b.dataset.home === '1') === state.home));
    });
    document.querySelectorAll('[data-fmt]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.fmt === state.fmt));
    });
    $('cv').style.aspectRatio = state.fmt === '9:16' ? '9 / 16' : '4 / 5';
  }

  function bind() {
    $('opp').addEventListener('change', (e) => {
      state.opp = Number(e.target.value);
      store.set('adler.opp', state.teams[state.opp].name);
      draw();
    });
    document.querySelectorAll('[data-home]').forEach((b) => b.addEventListener('click', () => {
      state.home = b.dataset.home === '1';
      store.set('adler.home', state.home ? '1' : '0');
      syncControls(); draw();
    }));
    document.querySelectorAll('[data-fmt]').forEach((b) => b.addEventListener('click', () => {
      state.fmt = b.dataset.fmt;
      store.set('adler.fmt', state.fmt);
      syncControls(); draw();
    }));
    [['us', 'us'], ['them', 'them'], ['htUs', 'htUs'], ['htThem', 'htThem']].forEach(([id, key]) => {
      $(id).addEventListener('input', (e) => { state[key] = clampNum(e.target.value); draw(); });
      $(id).addEventListener('blur', syncControls);
    });
    document.querySelectorAll('[data-step]').forEach((b) => b.addEventListener('click', () => {
      const key = b.dataset.target;
      state[key] = clampNum(state[key] + Number(b.dataset.step));
      syncControls(); draw();
    }));
    $('htOn').addEventListener('change', (e) => { state.ht = e.target.checked; syncControls(); draw(); });
    $('head').addEventListener('change', (e) => {
      state.head = e.target.value;
      store.set('adler.head', state.head);
      draw();
    });
    $('save').addEventListener('click', () => save(true));
    $('download').addEventListener('click', () => save(false));
    $('closeShot').addEventListener('click', () => { $('shot').hidden = true; });
  }

  function fileName() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    const slug = (s) => s.toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue')
      .replace(/ß/g, 'ss').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    const opp = state.teams[state.opp] ? state.teams[state.opp].name : 'gegner';
    const fmt = state.fmt === '9:16' ? 'story' : 'feed';
    return `ergebnis_${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${slug(opp)}_${state.us}-${state.them}_${fmt}.png`;
  }

  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.id);
    toast.id = setTimeout(() => { t.hidden = true; }, 3200);
  }

  function save(share) {
    canvas.toBlob(async (blob) => {
      if (!blob) { toast('Bild konnte nicht erstellt werden.'); return; }
      const name = fileName();
      const file = new File([blob], name, { type: 'image/png' });
      if (share && navigator.canShare && navigator.canShare({ files: [file] })) {
        try {
          await navigator.share({ files: [file] });
          toast('Fertig – Bild gesichert oder geteilt.');
          return;
        } catch (e) {
          if (e && e.name === 'AbortError') return;      // Abbruch durch Nutzerin
        }
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      $('shotImg').src = url;                           // Notlösung: Bild gedrückt halten -> sichern
      $('shot').hidden = false;
    }, 'image/png');
  }

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* Offline-Betrieb optional */ });
  }
  bind();
  init();
})();
