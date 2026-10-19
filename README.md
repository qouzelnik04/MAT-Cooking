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

Tlačítko **📺 Na displej** v detailu receptu zapíše recept do entity
`sensor.mat_cooking_recept` (název, porce, přepočtené suroviny, postup)
a službou `cast.show_lovelace_view` promítne na zvolený `media_player`
dashboard `mat-cooking-displej`, který tu entitu ukazuje. Dashboard má tři pohledy (velikosti písma), na displeji se přepínají tlačítky A / A+ / A++. YAML dashboardu je
v `app/main.py` (`DISPLAY_DASHBOARD`) a appka ho ukazuje ke zkopírování.
Entita není trvalá -- po restartu HA zmizí do dalšího poslání receptu.

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
| 2c | Recept na displej (Google Nest Hub přes Cast), velikost písma A / A+ / A++ | hotovo |
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
    ha.py                  seznamy úkolů v HA (čtení, přidávání položek)
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
