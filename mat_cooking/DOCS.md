# MAT-Cooking

Recepty a plán vaření pro celou domácnost.

## Použití

Otevři **MAT-Cooking** v postranním panelu HA.

- **Plán** — u receptů nastavíš, kolik porcí uvaříš. Suroviny se sečtou,
  zaškrtneš, co máš doma, a zbytek pošleš do nákupního seznamu (seznamy
  úkolů HA, např. Albert, Billa). Plán je společný pro celou domácnost.
- **Recepty** — přidávání receptů se surovinami (množství + jednotka +
  surovina), postupem a poznámkami. Pořadí surovin změníš přetažením za
  úchyt ⠿ (myší i prstem). V detailu receptu jde přepočítat počet
  porcí tlačítky −/+.
- **Fotky a doba přípravy** — u receptu v editoru. Fotku prohlížeč před
  nahráním zmenší (asi 200 kB), takže místo na HA nezabere.
- **Export / Import** — dole v záložce Recepty. Soubor se všemi recepty
  i fotkami; na jiném HA ho nahraješ přes Import (recepty se stejným názvem
  se přeskočí).
- **Na displej** — v detailu receptu pošleš recept (s nastavenými porcemi)
  na displej s Google Cast, např. Google Nest Hub. Na displeji jde vybírat
  recepty z mřížky s fotkami (filtr podle kategorie a počátečního písmene),
  měnit porce a velikost písma. Dashboard
  *MAT-Cooking displej* a pomocníky *MAT-Cooking recept / porce / písmo*
  si appka v HA vytvoří a udržuje sama — nemaž je ani je ručně neupravuj.
  HA musí mít nastavenou internetovou adresu.
- **Suroviny** — katalog všech surovin. Když stejnou věc máš pod dvěma názvy,
  přejmenuj jednu na druhou a sloučí se.

## Data

Databáze a fotky (zmenšené, ~200 kB na recept) jsou v datech add-onu a je součástí zálohy HA. Při záloze se add-on
na chvíli zastaví, aby se nezálohovala rozepsaná databáze.

## Přístup

Jen přes Home Assistant (Ingress), add-on nevystavuje žádný port. Panel
vidí i uživatelé HA bez admin práv — recepty jsou společné.
