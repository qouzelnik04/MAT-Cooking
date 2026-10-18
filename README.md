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

## Vývoj na notebooku

Dvojklik na `START.bat` spustí appku lokálně na `http://127.0.0.1:8720` nad
testovací databází v `devdata\` (není v gitu). Potřeba je jen Python 3.11+,
nic se neinstaluje.

## Plán

| Krok | Obsah | Stav |
|---|---|---|
| 1 | Recepty, katalog surovin, přepočet porcí | hotovo |
| 2 | Plán vaření: výběr receptů a porcí, sečtení surovin, „mám doma“, odeslání do nákupního seznamu HA | |
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
  web/                     UI (vanilla JS, mobil na prvním místě)
START.bat                  lokální spuštění
```

## Zásady

- Jen standardní knihovna Pythonu (SQLite je její součást) — za pět let to
  musí nastartovat bez `pip install`.
- Množství jako Decimal, ne float. Převádí se jen g/kg a ml/dl/l; lžíce,
  hrnky a kusy ne — odhad v nákupním seznamu je horší než poctivé „2 lžíce“.
- Schéma databáze se mění jen novou migrací v `db.py` (`MIGRATIONS`), aby
  stará data přežila aktualizaci.
