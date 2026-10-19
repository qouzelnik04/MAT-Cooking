# MAT-Cooking

Doplněk (add-on) pro Home Assistant: recepty a plán vaření pro celou
domácnost. Chybějící suroviny půjdou rovnou do nákupních seznamů v HA.

**Píšu to pro budoucího tebe** — za rok si nebudeš pamatovat, jak to funguje.

## Instalace do Home Assistantu

1. **Nastavení → Doplňky → Obchod s doplňky → ⋮ → Repozitáře**, přidej
   `https://github.com/qouzelnik04/MAT-Cooking`.
2. V obchodě se objeví **MAT-Cooking** → Instalovat → zapnout
   **Zobrazit v postranním panelu** → Spustit.
3. Otevři **Vaření** v postranním panelu.

**Aktualizace:** po zvýšení verze v `mat_cooking/config.yaml` a pushi na
GitHub nabídne HA aktualizaci sám (Obchod s doplňky → ⋮ → Zkontrolovat
aktualizace).

## Nákupní seznamy

Appka posílá chybějící suroviny do **seznamů úkolů v HA** (integrace Local
To-do, jeden záznam = jeden obchod: Albert, Billa, Kaufland…). Add-on k tomu
má `homeassistant_api: true` a token mu HA dá sám. Co už v seznamu
nevyřízené je, se nepřidá podruhé. U položky je v popisu, pro které recepty
je.

## Recept na displeji

Tlačítko **📺 Na displej** v detailu receptu promítne recept službou
`cast.show_lovelace_view` na zvolený `media_player` (Google Nest Hub).

Appka si v HA přes websocket API sama udržuje (`app/display.py`):

- dashboard `mat-cooking-displej` -- vytvoří ho a přepíše při každé změně
  receptů, fotek nebo plánu (mřížka receptů s fotkami, naplánované první),
- pomocníky `input_select.mat_cooking_recept` (klepnutí na dlaždici v mřížce),
  `input_number.mat_cooking_porce` a `input_select.mat_cooking_pismo`,
- entity `sensor.mat_cooking_recept` (hotové HTML karet receptu) a
  `sensor.mat_cooking_recept_foto` (náhled fotky jako data: URL).

Dashboard má dva pohledy, `recept` a `recepty` (mřížka). Podmíněné karty
(conditional) promítání na Nest Hub neukáže -- zůstane bílá obrazovka --,
proto se na mřížku jde navigací a zpátky na recept appka přepne sama
(`cast.show_lovelace_view` na displej, kam se naposledy posílalo). Klepnutí
na displeji mění pomocníky, appka změny sleduje a recept hned překreslí; po
výběru receptu nastaví porce z plánu, jinak výchozí. Který recept je na
displeji, si appka pamatuje v `displej.json` vedle databáze. Karty jsou
jen vestavěné (markdown, picture-glance, tile, button), nic z HACS. Po
restartu HA se appka znovu připojí a vše obnoví.

## Vývoj na notebooku

Dvojklik na `START.bat` spustí appku lokálně na `http://127.0.0.1:8720` nad
testovací databází v `devdata\` (není v gitu). Potřeba je jen Python 3.11+,
nic se neinstaluje.

## Plán

| Krok | Obsah | Stav |
|---|---|---|
| 1 | Recepty, katalog surovin, přepočet porcí | hotovo |
| 2 | Plán vaření: výběr receptů a porcí, sečtení surovin, „mám doma“, odeslání do nákupního seznamu HA | hotovo |
| 2b | Odkaz z plánu na recept s naplánovanými porcemi, doba přípravy, fotky, export/import receptů | hotovo |
| 2c | Recept na displej (Google Nest Hub přes Cast), mřížka receptů, porce a písmo přímo na displeji, dashboard spravuje appka | hotovo |
| 3 | Lednice a mrazák, upozornění | |

## Kde co je

```
repository.yaml            HA podle něj pozná repozitář doplňků
mat_cooking/
  config.yaml              verze, Ingress, přístup k API HA -- VERZE JE JEN TADY
  Dockerfile
  app/
    main.py                HTTP server (Ingress v HA, 127.0.0.1 lokálně)
    db.py                  SQLite: recepty, suroviny, migrace schématu
    units.py               jednotky, převody g/kg a ml/l, parsování množství
    plan.py                plán -> sečtený nákupní seznam (kusy nahoru)
    ha.py                  REST API HA (seznamy úkolů, stavy entit, služby)
    display.py             recept na displej (dashboard, pomocníci, ovládání z displeje)
    ws.py                  websocket klient pro HA (jen standardní knihovna)
  web/                     UI (vanilla JS, mobil na prvním místě)
START.bat                  lokální spuštění
```

## Zásady

- Jen standardní knihovna Pythonu (SQLite je její součást) — za pět let to
  musí nastartovat bez `pip install`.
- Množství jako Decimal, ne float. Převádí se jen g/kg a ml/dl/l; lžíce,
  hrnky a kusy ne — odhad v nákupním seznamu je horší než poctivé „2 lžíce“.
- Fotky zmenšuje prohlížeč (JPEG 1200 px + náhled 480 px) a na server jdou
  jako soubory do `photos/` v datech add-onu, ne do databáze.
- Schéma databáze se mění jen novou migrací v `db.py` (`MIGRATIONS`), aby
  stará data přežila aktualizaci.
