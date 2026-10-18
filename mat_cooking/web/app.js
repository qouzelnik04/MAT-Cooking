/*
  MAT-Cooking -- frontend. Vanilla JS, žádný build: má jít spustit i za roky.
  Adresy jsou relativní (api/..., ne /api/...), protože v HA appka běží pod
  prefixem Ingressu.
*/
'use strict';

const S = { meta: null, recipes: [], ingredients: [], filter: '', category: '' };
const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function toast(msg) {
  const t = el('div', {}, msg);
  $('#toast').append(t);
  setTimeout(() => t.remove(), 3500);
}

/* Množství: 1.5 -> "1,5"; dělení porcí zaokrouhlí rozumně podle velikosti. */
function fmtAmount(x) {
  if (!Number.isFinite(x) || x <= 0) return '';
  const r = x >= 100 ? Math.round(x) : x >= 10 ? Math.round(x * 10) / 10 : Math.round(x * 100) / 100;
  return r.toLocaleString('cs-CZ', { maximumFractionDigits: 2 });
}
const catName = (c) => (S.meta.categories || {})[c] || c;

const photoUrl = (r, small) => `api/recipes/${r.id}/photo?${small ? 'small=1&' : ''}v=${r.photo}`;
const minutesText = (m) => (!m ? '' : m < 60 ? `${m} min`
  : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ''}`);

/* Fotka se zmenší přímo v prohlížeči: na server jde jen JPEG ~1200 px
   (~200 kB) a náhled ~480 px. Fotka z telefonu (klidně 5 MB) tak na HA
   nezabere skoro nic. */
async function resizeJpeg(file, max) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, fail) => {
      const i = new Image();
      i.onload = () => ok(i);
      i.onerror = () => fail(new Error('Tenhle obrázek prohlížeč neumí otevřít (zkus JPEG nebo PNG).'));
      i.src = url;
    });
    const scale = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement('canvas');
    c.width = Math.round(img.naturalWidth * scale);
    c.height = Math.round(img.naturalHeight * scale);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL('image/jpeg', 0.82).split(',')[1];
  } finally { URL.revokeObjectURL(url); }
}

/* Tlačítko, které se napoprvé zeptá a smaže až napodruhé (confirm() v okně
   HA nemusí fungovat). */
function confirmButton(label, ask, action) {
  const b = el('button', { class: 'btn danger' }, label);
  let armed = false;
  b.addEventListener('click', async () => {
    if (!armed) {
      armed = true; b.textContent = ask;
      setTimeout(() => { armed = false; b.textContent = label; }, 4000);
      return;
    }
    await action();
  });
  return b;
}

/* ============================================================== ROUTER */
async function route() {
  const [name, arg, arg2] = (location.hash.replace('#', '') || 'plan').split('/');
  const tab = ['suroviny', 'plan'].includes(name) ? name : 'recepty';
  document.querySelectorAll('.tabs a').forEach((a) =>
    a.classList.toggle('on', a.dataset.tab === tab));
  const view = $('#view');
  try {
    if (name === 'recept') view.replaceChildren(await recipeView(+arg, +arg2 || 0));
    else if (name === 'upravit') view.replaceChildren(await editView(+arg));
    else if (name === 'novy') view.replaceChildren(await editView(null));
    else if (name === 'suroviny') view.replaceChildren(await ingredientsView());
    else if (name === 'plan') view.replaceChildren(await planView());
    else view.replaceChildren(await listView());
  } catch (e) {
    view.replaceChildren(el('p', { class: 'error' }, 'Chyba: ' + e.message));
  }
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', route);

/* ============================================================= RECEPTY */
async function listView() {
  S.recipes = await api('api/recipes');
  const box = el('div', { class: 'list' });
  const paint = () => {
    const q = S.filter.toLowerCase();
    const shown = S.recipes.filter((r) => (!S.category || r.category === S.category)
      && (!q || r.name.toLowerCase().includes(q)));
    box.replaceChildren(...(shown.length ? shown.map((r) =>
      el('a', { class: 'recipe', href: `#recept/${r.id}` },
        r.photo ? el('img', { class: 'thumb', src: photoUrl(r, true), alt: '', loading: 'lazy' }) : null,
        el('b', {}, r.name),
        el('span', { class: 'chip' }, catName(r.category)), ' ',
        el('span', { class: 'muted small' },
          `${r.servings} ${r.servings < 5 ? 'porce' : 'porcí'} · ${r.items} surovin`
          + (r.minutes ? ` · ⏱ ${minutesText(r.minutes)}` : ''))))
      : [el('p', { class: 'muted' }, S.recipes.length ? 'Nic nenalezeno.'
        : 'Zatím žádné recepty. Přidej první.')]));
  };
  const search = el('input', { type: 'search', placeholder: 'Hledat recept…', value: S.filter });
  search.addEventListener('input', () => { S.filter = search.value; paint(); });
  const chips = el('div', { class: 'chips' });
  const paintChips = () => chips.replaceChildren(
    ...[['', 'Vše'], ...Object.entries(S.meta.categories)].map(([k, v]) =>
      el('button', { class: S.category === k ? 'on' : '',
        onclick: () => { S.category = k; paintChips(); paint(); } }, v)));
  paintChips();
  paint();
  const importInput = el('input', { type: 'file', accept: '.json,application/json', hidden: true });
  importInput.addEventListener('change', async () => {
    const f = importInput.files[0];
    importInput.value = '';
    if (!f) return;
    try {
      const res = await api('api/import', JSON.parse(await f.text()));
      toast(`Importováno ${res.added.length}`
        + (res.skipped.length ? `, přeskočeno ${res.skipped.length} (už existují)` : '') + '.');
      route();
    } catch (e) { toast('Import se nepovedl: ' + e.message); }
  });
  return el('div', {},
    el('div', { class: 'row' }, el('h1', {}, 'Recepty'), el('div', { class: 'spacer' }),
      el('a', { class: 'btn primary', href: '#novy' }, '+ Nový recept')),
    search, chips, box,
    el('div', { class: 'row', style: 'margin-top:24px' },
      el('a', { class: 'btn small', href: 'api/export', download: '' }, '⇩ Export receptů'),
      el('button', { class: 'btn small', onclick: () => importInput.click() }, '⇧ Import'),
      importInput,
      el('span', { class: 'muted small' }, 'Soubor s recepty i fotkami — na jiném HA ho '
        + 'nahraješ přes Import. Recepty se stejným názvem se přeskočí.')));
}

async function recipeView(id, planned) {
  const r = await api(`api/recipes/${id}`);
  let portions = planned || r.servings;
  const list = el('ul', { class: 'ingredients' });
  const count = el('b', {});
  const paint = () => {
    count.textContent = portions;
    const f = portions / r.servings;
    list.replaceChildren(...r.items.map((it) => el('li', {},
      el('span', { class: 'qty' }, it.amount ? `${fmtAmount(parseFloat(it.amount) * f)} ${it.unit}` : ''),
      el('span', {}, it.ingredient, it.note ? el('span', { class: 'muted' }, ` — ${it.note}`) : null))));
  };
  paint();
  const step = (d) => () => { portions = Math.max(1, portions + d); paint(); };
  const steps = r.steps.split('\n').map((s) => s.trim()).filter(Boolean);

  return el('div', {},
    planned ? el('a', { href: '#plan', class: 'muted small' }, '← Plán vaření')
      : el('a', { href: '#recepty', class: 'muted small' }, '← Recepty'),
    r.photo ? el('img', { class: 'hero', src: photoUrl(r, false), alt: r.name }) : null,
    el('h1', {}, r.name),
    el('div', { class: 'row' }, el('span', { class: 'chip' }, catName(r.category)),
      r.minutes ? el('span', { class: 'chip' }, '⏱ ' + minutesText(r.minutes)) : null,
      el('span', { class: 'muted small' }, `recept je na ${r.servings} `
        + (r.servings < 5 ? 'porce' : 'porcí')
        + (planned ? ` · v plánu ${planned}` : ''))),
    el('div', { class: 'card' },
      el('div', { class: 'row' }, el('h2', { style: 'margin:0' }, 'Suroviny'),
        el('div', { class: 'spacer' }),
        el('span', { class: 'muted small' }, 'porcí'),
        el('div', { class: 'stepper' },
          el('button', { onclick: step(-1), 'aria-label': 'méně porcí' }, '−'), count,
          el('button', { onclick: step(1), 'aria-label': 'více porcí' }, '+'))),
      r.items.length ? list : el('p', { class: 'muted' }, 'Bez surovin.')),
    steps.length ? el('div', { class: 'card' }, el('h2', { style: 'margin-top:0' }, 'Postup'),
      el('ol', { class: 'steps' }, ...steps.map((s) => el('li', {}, s)))) : null,
    r.notes ? el('div', { class: 'card' }, el('h2', { style: 'margin-top:0' }, 'Poznámky'),
      el('p', { style: 'white-space:pre-wrap;margin:0' }, r.notes)) : null,
    el('div', { class: 'row', style: 'margin-top:16px' },
      el('a', { class: 'btn', href: `#upravit/${r.id}` }, 'Upravit'),
      el('div', { class: 'spacer' }),
      confirmButton('Smazat', 'Opravdu smazat?', async () => {
        await api(`api/recipes/${r.id}/delete`, {});
        toast('Recept smazán.');
        location.hash = '#recepty';
      })));
}

async function editView(id) {
  const r = id ? await api(`api/recipes/${id}`)
    : { name: '', category: 'hlavni', servings: 4, minutes: null, photo: 0, steps: '',
      notes: '', items: [] };
  await loadIngredientNames();
  const name = el('input', { value: r.name, placeholder: 'např. Kuře s rýží' });
  const category = el('select', {}, ...Object.entries(S.meta.categories).map(([k, v]) =>
    el('option', { value: k, selected: k === r.category }, v)));
  const servings = el('input', { type: 'number', min: 1, step: 1, value: r.servings });
  const minutes = el('input', { type: 'number', min: 1, step: 5, value: r.minutes || '',
    placeholder: 'např. 45' });
  // Fotka: nová se nahraje až po uložení receptu (nový recept ještě nemá id)
  let newPhoto = null;
  let dropPhoto = false;
  const preview = el('div', { class: 'photo-edit' });
  const photoInput = el('input', { type: 'file', accept: 'image/*', hidden: true });
  const paintPhoto = () => {
    const src = newPhoto ? URL.createObjectURL(newPhoto)
      : (r.photo && !dropPhoto ? photoUrl(r, true) : null);
    preview.replaceChildren(
      src ? el('img', { src, alt: '' }) : el('span', { class: 'muted small' }, 'Bez fotky'),
      el('button', { class: 'btn small', type: 'button', onclick: () => photoInput.click() },
        src ? 'Změnit fotku' : '+ Fotka'),
      src ? el('button', { class: 'btn small danger', type: 'button', onclick: () => {
        newPhoto = null; dropPhoto = true; paintPhoto();
      } }, 'Odebrat') : null);
  };
  photoInput.addEventListener('change', () => {
    if (photoInput.files[0]) { newPhoto = photoInput.files[0]; dropPhoto = false; paintPhoto(); }
  });
  paintPhoto();
  const steps = el('textarea', { placeholder: 'Každý krok na nový řádek' }, r.steps);
  const notes = el('textarea', { style: 'min-height:80px' }, r.notes);
  const rows = el('div', {});

  const addRow = (it = {}) => {
    const amount = el('input', { value: it.amount || '', placeholder: 'kolik', inputmode: 'decimal' });
    const unit = el('select', {}, el('option', { value: '' }, '—'),
      ...S.meta.units.map((u) => el('option', { value: u, selected: u === it.unit }, u)));
    const ing = el('input', { value: it.ingredient || '', placeholder: 'surovina',
      list: 'ingredient-names' });
    const note = el('input', { class: 'note', value: it.note || '',
      placeholder: 'poznámka (nepovinné, např. nakrájená)' });
    const row = el('div', { class: 'item-row' }, amount, unit, ing,
      el('div', { class: 'tools' },
        el('button', { class: 'btn small', type: 'button', title: 'nahoru',
          onclick: () => row.previousElementSibling && rows.insertBefore(row, row.previousElementSibling) }, '↑'),
        el('button', { class: 'btn small danger', type: 'button', title: 'odebrat',
          onclick: () => row.remove() }, '✕')),
      note);
    row.read = () => ({ amount: amount.value, unit: unit.value, ingredient: ing.value, note: note.value });
    rows.append(row);
    return ing;
  };
  (r.items.length ? r.items : [{}, {}, {}]).forEach(addRow);

  const err = el('p', { class: 'error' });
  const save = el('button', { class: 'btn primary' }, 'Uložit recept');
  save.addEventListener('click', async () => {
    save.disabled = true; err.textContent = '';
    try {
      const res = await api('api/recipes', {
        id: id || null, name: name.value, category: category.value,
        servings: servings.value, minutes: minutes.value, steps: steps.value, notes: notes.value,
        items: [...rows.children].map((x) => x.read()),
      });
      if (newPhoto) {
        save.textContent = 'Nahrávám fotku…';
        await api(`api/recipes/${res.id}/photo`, {
          full: await resizeJpeg(newPhoto, 1200), small: await resizeJpeg(newPhoto, 480) });
      } else if (dropPhoto && r.photo) {
        await api(`api/recipes/${res.id}/photo/delete`, {});
      }
      toast('Recept uložen.');
      location.hash = `#recept/${res.id}`;
    } catch (e) {
      err.textContent = e.message;
    } finally { save.disabled = false; save.textContent = 'Uložit recept'; }
  });

  return el('div', {},
    el('a', { href: id ? `#recept/${id}` : '#recepty', class: 'muted small' }, '← Zpět'),
    el('h1', {}, id ? 'Upravit recept' : 'Nový recept'),
    el('div', { class: 'card' },
      el('label', { class: 'field' }, el('span', {}, 'Název'), name),
      el('div', { class: 'row' },
        el('label', { class: 'field', style: 'flex:2' }, el('span', {}, 'Kategorie'), category),
        el('label', { class: 'field', style: 'flex:1' }, el('span', {}, 'Recept je na porcí'), servings),
        el('label', { class: 'field', style: 'flex:1' }, el('span', {}, 'Doba přípravy (min)'), minutes)),
      el('div', { class: 'field' }, el('span', { class: 'muted small' }, 'Fotka jídla'),
        preview, photoInput)),
    el('div', { class: 'card' },
      el('h2', { style: 'margin-top:0' }, 'Suroviny'),
      el('p', { class: 'muted small' }, 'Množství jako 200, 1,5 nebo 1/2. Názvy surovin '
        + 'se nabízejí z těch, které už máš — piš je stejně, ať se dají sečíst.'),
      rows,
      el('button', { class: 'btn', type: 'button', onclick: () => addRow().focus() }, '+ Surovina')),
    el('div', { class: 'card' },
      el('label', { class: 'field' }, el('span', {}, 'Postup'), steps),
      el('label', { class: 'field' }, el('span', {}, 'Poznámky'), notes)),
    err, save);
}

/* ================================================================ PLÁN */
/* Nedělní vaření: kolik porcí čeho -> sečtené suroviny -> co mám doma ->
   zbytek do nákupního seznamu v HA. Plán je společný pro celou domácnost. */
async function planView() {
  const [recipes, plan] = await Promise.all([api('api/recipes'), api('api/plan')]);
  let state = plan;
  const portionsOf = () => Object.fromEntries(state.recipes.map((r) => [r.id, r.portions]));
  const recipeBox = el('div', {});
  const shopBox = el('div', {});
  const filter = el('input', { type: 'search', placeholder: 'Hledat recept…' });
  // Pořadí se určí jen při otevření (vybrané nahoře) a pak se nemění --
  // jinak by řádek po kliknutí na + uskočil a další klik trefil jiný recept.
  const initial = portionsOf();
  const order = recipes.slice().sort((a, b) => (initial[b.id] ? 1 : 0) - (initial[a.id] ? 1 : 0));

  const setPortions = async (rid, n) => {
    state = await api('api/plan/portions', { recipe_id: rid, portions: n });
    paintRecipes(); paintShop();
  };

  const paintRecipes = () => {
    const p = portionsOf();
    const q = filter.value.toLowerCase();
    const list = order.filter((r) => !q || r.name.toLowerCase().includes(q));
    recipeBox.replaceChildren(...(list.length ? list.map((r) => {
      const n = p[r.id] || 0;
      return el('div', { class: 'plan-row' + (n ? ' on' : '') },
        el('div', { class: 'spacer' },
          n ? el('a', { href: `#recept/${r.id}/${n}`, class: 'plan-link' }, el('b', {}, r.name))
            : el('b', {}, r.name), ' ',
          el('span', { class: 'chip' }, catName(r.category))),
        el('div', { class: 'stepper' },
          el('button', { onclick: () => setPortions(r.id, Math.max(0, n - 1)),
            disabled: !n, 'aria-label': 'méně porcí' }, '−'),
          el('b', {}, n),
          el('button', { onclick: () => setPortions(r.id, n + 1), 'aria-label': 'více porcí' }, '+')));
    }) : [el('p', { class: 'muted' }, recipes.length ? 'Nic nenalezeno.'
      : 'Nejdřív přidej recepty.')]));
  };

  const qtyText = (r) => (r.amount ? `${fmtAmount(parseFloat(r.amount))} ${r.unit}` : '');

  const sendBox = (missing, missingAll) => {
    if (!missingAll) return el('p', {}, '✓ Všechno máš doma.');
    if (!missing.length) return el('p', {}, '✓ Všechno chybějící už je v nákupním seznamu.');
    if (!state.ha) {
      const text = missing.map((r) => `${r.ingredient} ${qtyText(r)}`.trim()).join('\n');
      return el('div', { style: 'margin-top:12px' },
        el('p', { class: 'muted small' }, 'Appka neběží v Home Assistantu, takže seznam '
          + 'neodešle. Můžeš ho zkopírovat:'),
        el('textarea', { readonly: true, style: 'min-height:100px' }, text),
        el('button', { class: 'btn', onclick: async () => {
          await navigator.clipboard.writeText(text); toast('Zkopírováno.');
        } }, 'Zkopírovat'));
    }
    const select = el('select', {}, el('option', { value: '' }, 'Načítám seznamy…'));
    const send = el('button', { class: 'btn primary', disabled: true },
      `Poslat ${missing.length} do seznamu`);
    api('api/lists').then((res) => {
      let last = '';
      try { last = localStorage.getItem('mat-cooking-list') || ''; } catch (e) { /* nevadí */ }
      select.replaceChildren(...(res.lists.length ? res.lists.map((l) =>
        el('option', { value: l.entity_id, selected: l.entity_id === last }, l.name))
        : [el('option', { value: '' }, 'V HA nejsou žádné seznamy úkolů')]));
      send.disabled = !res.lists.length;
    }).catch((e) => toast(e.message));
    send.addEventListener('click', async () => {
      send.disabled = true;
      try {
        try { localStorage.setItem('mat-cooking-list', select.value); } catch (e) { /* nevadí */ }
        const res = await api('api/plan/send', { entity_id: select.value });
        const name = select.options[select.selectedIndex].text;
        toast(`${name}: přidáno ${res.added.length}`
          + (res.updated.length ? `, navýšeno ${res.updated.length}` : '') + '.');
        state = await api('api/plan');
        paintShop();
      } catch (e) { toast(e.message); send.disabled = false; }
    });
    return el('div', { class: 'row', style: 'margin-top:12px' }, select, send);
  };

  const paintShop = () => {
    const rows = state.shopping;
    if (!rows.length) {
      shopBox.replaceChildren(el('p', { class: 'muted' },
        'Vyber nahoře, co budeš vařit a kolik porcí — suroviny se tu sečtou.'));
      return;
    }
    const missing = rows.filter((r) => !r.have);
    const pending = missing.filter((r) => r.to_send);
    shopBox.replaceChildren(
      el('p', { class: 'muted small' }, `Chybí ${missing.length} z ${rows.length}`
        + (missing.length > pending.length
          ? `, ${missing.length - pending.length} už je v nákupním seznamu` : '')
        + '. Zaškrtni, co máš doma.'),
      el('ul', { class: 'shop' }, ...rows.map((r) => {
        const cb = el('input', { type: 'checkbox' });
        cb.checked = r.have;
        cb.addEventListener('change', async () => {
          r.have = cb.checked;
          await api('api/plan/have', { key: r.key, have: r.have });
          paintShop();
        });
        return el('li', { class: r.have ? 'have' : '' },
          el('label', {}, cb,
            el('span', { class: 'qty' }, qtyText(r)),
            el('span', {}, r.ingredient,
              el('span', { class: 'muted small' }, ' · ' + r.recipes.join(', ')),
              !r.have && !r.to_send ? el('span', { class: 'chip sent' }, '✓ v seznamu') : null)));
      })),
      sendBox(pending, missing.length));
  };

  filter.addEventListener('input', paintRecipes);
  paintRecipes(); paintShop();
  return el('div', {},
    el('div', { class: 'row' }, el('h1', {}, 'Plán vaření'), el('div', { class: 'spacer' }),
      confirmButton('Vyčistit plán', 'Opravdu vyčistit?', async () => {
        state = await api('api/plan/clear', {});
        paintRecipes(); paintShop(); toast('Plán vyčištěn.');
      })),
    el('div', { class: 'card' }, el('h2', { style: 'margin-top:0' }, 'Co budu vařit'),
      filter, recipeBox),
    el('div', { class: 'card' }, el('h2', { style: 'margin-top:0' }, 'Nákup'), shopBox));
}

/* ============================================================ SUROVINY */
async function loadIngredientNames() {
  S.ingredients = await api('api/ingredients');
  $('#ingredient-names').replaceChildren(...S.ingredients.map((i) => el('option', { value: i.name })));
}

async function ingredientsView() {
  await loadIngredientNames();
  const rows = S.ingredients.map((i) => {
    const input = el('input', { value: i.name });
    const save = el('button', { class: 'btn small' }, 'Přejmenovat');
    save.addEventListener('click', async () => {
      try {
        const res = await api(`api/ingredients/${i.id}/rename`, { name: input.value });
        toast(res.result === 'merged' ? `Sloučeno do „${input.value}“.` : 'Přejmenováno.');
        route();
      } catch (e) { toast(e.message); }
    });
    return el('tr', {},
      el('td', {}, input),
      el('td', { class: 'num muted small' }, i.used ? `${i.used}×` : 'nepoužitá'),
      el('td', {}, el('div', { class: 'tools' }, save,
        i.used ? null : confirmButton('✕', 'Smazat?', async () => {
          await api(`api/ingredients/${i.id}/delete`, {});
          route();
        }))));
  });
  return el('div', {},
    el('h1', {}, 'Suroviny'),
    el('p', { class: 'muted' }, 'Všechny suroviny ze všech receptů. Když stejnou věc '
      + 'píšeš dvakrát jinak (cibule / cibulka), přejmenuj jednu na tu druhou — '
      + 'sloučí se a v nákupu se sečtou.'),
    S.ingredients.length
      ? el('div', { class: 'card' }, el('table', {}, el('tbody', {}, ...rows)))
      : el('p', { class: 'muted' }, 'Zatím žádné. Přibudou s recepty.'));
}

/* =============================================================== START */
(async function boot() {
  try {
    S.meta = await api('api/meta');
    $('#ver').textContent = 'v' + S.meta.version;
    route();
  } catch (e) {
    $('#view').replaceChildren(el('p', { class: 'error' }, 'Nepodařilo se spustit: ' + e.message));
  }
}());
