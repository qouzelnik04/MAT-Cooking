# MAT-Cooking

Recepty a plán vaření pro celou domácnost.

## Použití

Otevři **Vaření** v postranním panelu HA.

- **Recepty** — přidávání receptů se surovinami (množství + jednotka +
  surovina), postupem a poznámkami. V detailu receptu jde přepočítat počet
  porcí tlačítky −/+.
- **Suroviny** — katalog všech surovin. Když stejnou věc máš pod dvěma názvy,
  přejmenuj jednu na druhou a sloučí se.

## Data

Databáze je v datech add-onu a je součástí zálohy HA. Při záloze se add-on
na chvíli zastaví, aby se nezálohovala rozepsaná databáze.

## Přístup

Jen přes Home Assistant (Ingress), add-on nevystavuje žádný port. Panel
vidí i uživatelé HA bez admin práv — recepty jsou společné.
