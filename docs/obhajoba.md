# Obhajoba PPP projektu

Podklad pro priblizne sedmiminutovou obhajobu. Hlavni linka: nejde jen o to, ze solver bezi paralelne, ale ze jsme postupne zmerili a vysvetlili, kde vznikal vykonovy problem.

## Mluveny text

### 1. Uvod: co projekt resi

V projektu jsem implementoval paralelni solver sireni tepla ve 2D domene. Vypocet je zalozeny na stencil metode, kde se v kazde iteraci aktualizuje teplota bodu podle sousednich hodnot a materialovych parametru.

Paralelizace je postavena na kombinaci MPI a OpenMP. MPI rozdeluje domenu mezi procesy, OpenMP se pouziva uvnitr procesu pro vypocet lokalni casti. Protoze stencil potrebuje hodnoty ze sousednich bunek, kazdy proces si v kazde iteraci vymenuje halo zony se sousedy. Vystup simulace se uklada do HDF5 souboru, takze soucasti projektu bylo i paralelni I/O.

Hlavni otazky, ktere jsem resil, byly: jak domenu rozdelit, jak rychle vymenovat halo zony, jak prekryt komunikaci s vypoctem a jak se chova I/O pri vetsim poctu procesu.

### 2. Dekompozice domeny: 1D vs 2D

Prvni dulezite rozhodnuti je dekompozice domeny. Implementace podporuje 1D i 2D rozdeleni. U 1D dekompozice se domena deli jen v jednom smeru, takze procesy posilaji dlouhe hrany. U 2D dekompozice vznikaji obdelnikove nebo ctvercove dlazdice a proces komunikuje az se ctyrmi sousedy, ale kazda hrana je kratsi.

Teoreticky je objem komunikace u 1D dekompozice vyssi. Pro mrizku velikosti N krat N a P procesu vychazi pomer komunikace priblizne:

```text
V_1D / V_2D = sqrt(P) / 2
```

To znamena, ze napr. pri 16 procesech posila 1D dekompozice asi dvakrat vice halo dat, pri 256 procesech uz asi osmkrat vice. To se projevilo i v merenich: 2D dekompozice vychazela ve vetsine prime porovnatelnych konfiguraci lepe, hlavne pri vetsim poctu jader.

### 3. Prekryti komunikace a vypoctu

Kazda iterace je rozdelena tak, aby se komunikace dala castecne schovat za vypocet. Nejdriv se spocitaji okrajove casti dlazdice, ktere jsou potreba pro halo. Potom se spusti vymena halo zon. Behem toho se pocita vnitrni cast dlazdice, ktera na nove halo hodnoty jeste neceka. Az potom se ceka na dokonceni komunikace a iterace muze pokracovat.

Prinos tohoto pristupu zavisi na pomeru vnitru dlazdice k jejimu okraji. U vetsich dlazdic je vnitrni cast relativne velka, takze je dost prostoru, kam komunikaci schovat. Proto je to dulezite hlavne u vetsich domen a rozumne zvolene 2D dekompozice.

### 4. Halo exchange: P2P a RMA

Halo vymena je implementovana dvema zpusoby: klasicky pres point-to-point komunikaci a pres MPI RMA.

P2P varianta je primocara: procesy posilaji a prijimaji halo data pomoci neblokujicich operaci. RMA varianta je zajimavejsi, protoze se ukazalo, ze samotny vyber RMA API ma velky dopad na vykon.

Puvodni RMA pristup pouzival strided `MPI_Get`, tedy proces si cetl halo data z pameti souseda. Na Barbore, s Intel MPI a RDMA cestou, se to ukazalo jako velmi pomale. Proto jsem udelal izolovany mikrobenchmark, kde jsem porovnal jednotlive faktory samostatne.

Nejvetsi rozdil byl prechod z `MPI_Get` na `MPI_Put`. Misto aby proces cetl ze souseda, zapisuje vlastni halo data sousedovi do okna. Tento samotny krok dal zrychleni priblizne 55 az 80 krat podle konfigurace.

Druha optimalizace byl packing. Halo data, hlavne svisle hrany, nejsou v pameti souvisla. Misto prenosu pres strided MPI datove typy se data nejdriv zabali do souvisleho bufferu pomoci `MPI_Pack`, prenesou se pres `MPI_Put` a na druhe strane se rozbali. To pridalo zhruba 1.5 az 3 krat zrychleni pro konfigurace, kde byly halo zony opravdu strided.

Treti optimalizace byla synchronizace. `MPI_Win_fence` je jednoduche, ale synchronizuje zbytecne globalne. V halo vymene pritom kazdy proces komunikuje jen se svymi sousedy. Proto jsem pouzil PSCW synchronizaci, tedy `post/start/complete/wait`, ktera omezuje synchronizaci jen na sousedni procesy. Na jednom uzlu byl prinos mensi, asi 1.15 krat, ale pri prechodu na vice uzlu uz vyrostl az na 1.74 krat, protoze se setri mezizlova synchronizace.

Dohromady se RMA halo exchange zrychlil oproti naivni variante zhruba 133 az 268 krat. Dulezite je, ze to neni jedno magicke cislo, ale rozklad na tri vysvetlene faktory: `Get` proti `Put`, strided prenos proti packingu a globalni fence proti sousedskemu PSCW.

### 5. Vysledky skalovani

Nejlepsi namereny vysledek byl u hybridni 2D varianty s P2P komunikaci na domene 4096 krat 4096, kde solver dosahl zrychleni zhruba 666 krat na 256 jadrech.

Toto zrychleni je superlinearni vuci idealnimu zrychleni 256 krat. Hlavni duvod je cache efekt. Sekvencni varianta pracuje s celou velkou domenou, ktera se nevejde do L3 cache. Pri paralelnim rozdeleni ma ale kazdy proces mensi dlazdici, ktera se do cache vejde mnohem lepe. Vypocet je potom mene omezeny propustnosti pameti.

Pri porovnani 1D a 2D dekompozice se ukazalo, ze 2D varianta vyhrava hlavne tam, kde uz komunikace zacina byt dulezita. To odpovida teoretickemu odhadu objemu halo dat. Hybridni 1D varianta umi byt dobra diky OpenMP a mensimu poctu MPI procesu, ale pri vetsim skalovani ji omezuje delsi halo hranice.

### 6. Paralelni I/O

U I/O byla dulezita hlavne oprava HDF5 chunkovani. Puvodni nastaveni vytvarelo mnoho malych lokálních chunku, coz vedlo k velmi spatnemu chovani pri paralelnim zapisu. Po nastaveni chunku podle globalni domeny a po pouziti Lustre stripingu se paralelni zapis radove zlepsil.

Zaroven ale vyslo, ze pro merene velikosti vystupu nebylo paralelni I/O vzdy rychlejsi nez sekvencni zapis. Duvodem je rezije koordinace na Lustre a relativne male objemy dat na snapshot. Takze zaver neni, ze paralelni I/O je automaticky lepsi, ale ze musi mit spravne chunkovani a dostatecne velky zapis, aby se jeho rezije vyplatila.

### 7. Profilovani a zaver

Profilovani pres Score-P, Vampir a Cube slouzilo hlavne jako kontrola, jestli vykonove vysledky odpovidaji tomu, co implementace dela.

Vampir ukazal pravidelnou strukturu iteraci: lokalni vypocet, MPI komunikace a cekani na halo. Komunikacni matice potvrdila rozdil mezi 1D a 2D dekompozici: 1D ma uzky komunikacni vzor, ale vetsi objem na hranu, zatimco 2D komunikuje s vice sousedy, ale kratsimi hranami. Cube potvrdil, ze zatez je mezi procesy rovnomerne rozdelena.

Celkovy zaver je, ze vykon solveru stoji na trech vecech: vhodne 2D dekompozici, efektivni halo vymene a spravnem nastaveni I/O. Nejvetsi technicky prinos projektu je podle me v tom, ze se podarilo rozlozit problem RMA vykonu na konkretni meritelne priciny a kazdou z nich samostatne optimalizovat.

## Kratka verze na nauceni

Projekt implementuje paralelni 2D heat solver pomoci MPI a OpenMP. Domena se deli mezi procesy a v kazde iteraci se meni halo zony. Porovnaval jsem 1D a 2D dekompozici, P2P a RMA komunikaci a ruzne rezimy I/O.

2D dekompozice je vyhodnejsi, protoze zkracuje halo hranice. Teoreticky pomer komunikace 1D proti 2D je `sqrt(P) / 2`, coz se potvrdilo i v merenich.

Nejzajimavejsi cast byla optimalizace RMA. Izolovanym mikrobenchmarkem jsem zjistil, ze nejvetsi problem byl strided `MPI_Get`. Prechod na `MPI_Put` dal 55 az 80 krat zrychleni. Packing pridal dalsich 1.5 az 3 krat a nahrazeni globalniho `MPI_Win_fence` sousedskou PSCW synchronizaci pridalo az 1.74 krat pri vice uzlech. Celkove se RMA halo exchange zrychlil asi 133 az 268 krat.

Nejlepsi skalovani mela hybridni 2D P2P varianta: asi 666 krat na 256 jadrech pro domenu 4096 krat 4096. Superlinearni zrychleni vysvetluju cache efektem, protoze rozdelene dlazdice se vejdou do cache lepe nez cela sekvencni domena.

U HDF5 pomohla oprava chunkovani a Lustre striping, ale pro merene velikosti nebylo paralelni I/O vzdy rychlejsi nez sekvencni zapis kvuli reziji filesystemu.

Profilovani potvrdilo pravidelnou komunikaci, vyrovnanou zatez a rozdil mezi 1D a 2D komunikacnim vzorem. Hlavni zaver je, ze vykon neni dany jednou optimalizaci, ale kombinaci dekompozice, halo komunikace, synchronizace a I/O.

## Navrh slajdu

Na sedm minut bych delal 6 az 7 slajdu. Prezentace by mela byt hodne vizualni: malo textu, jeden hlavni obrazek nebo graf na slajd.

### Slajd 1: Problem a cil

**Obsah:**
- nazev projektu
- 2D heat equation / stencil solver
- MPI + OpenMP + HDF5

**Vizual:**
- jednoduchy Excalidraw obrazek 2D mrizky
- uprostred bod a jeho sousedi
- sipka "iterace -> nova teplota"

**Mluvena pointa:**
Toto je stencil vypocet, kde paralelizace znamena rozdelit mrizku a efektivne vymenovat okraje.

### Slajd 2: Dekompozice domeny

**Obsah:**
- 1D: dlouhe halo hrany
- 2D: kratsi halo hrany
- `V_1D / V_2D = sqrt(P) / 2`

**Vizual:**
- vlevo 1D rozdeleni na pruhy
- vpravo 2D rozdeleni na dlazdice
- barevne zvyraznit halo hranice

**Mluvena pointa:**
2D ma sice vice sousedu, ale kratsi hranice. Pri vetsim P to vyhrava.

### Slajd 3: Iterace solveru a prekryti

**Obsah:**
- compute halo zones
- start exchange
- compute inner tile
- wait for halo

**Vizual:**
- timeline jedne iterace
- komunikace jako modry pruh
- vypocet vnitru jako zeleny pruh pres cast komunikace

**Mluvena pointa:**
Komunikace se nespousti az po celem vypoctu, ale cast se skryje za vypocet vnitrku dlazdice.

### Slajd 4: RMA optimalizace

**Obsah:**
- `MPI_Get -> MPI_Put`: 55-80x
- strided -> packed: 1.5-3x
- fence -> PSCW: 1.15-1.74x
- total: 133-268x

**Vizual:**
- stacked bar nebo tri kroky pipeline:
  1. direction
  2. memory layout
  3. synchronization
- pripadne mala tabulka s cisly

**Mluvena pointa:**
Toto je nejsilnejsi technicky vysledek: RMA nebylo jen "optimalizovane", ale rozlozene na meritelne priciny.

### Slajd 5: Skalovani

**Obsah:**
- best: Hybrid 2D P2P
- 4096^2
- 256 jader
- cca 666x
- superlinear kvuli cache

**Vizual:**
- jeden graf speedupu, idealne jen nejdulezitejsi krivky
- nebo velke cislo `~666x` a vedle maly cache diagram

**Mluvena pointa:**
Superlinearni vysledek neni chyba mereni, ale dusledek toho, ze mensi dlazdice se vejdou do cache.

### Slajd 6: I/O a profilovani

**Obsah:**
- HDF5 chunk fix
- Lustre striping
- par I/O neni automaticky rychlejsi
- Score-P/Vampir/Cube validace

**Vizual:**
- vlevo maly diagram spatne vs dobre chunkovani
- vpravo screenshot nebo zjednoduseny obrazek komunikacni matice 1D vs 2D

**Mluvena pointa:**
I/O bylo potreba opravit, ale mereni ukazuje, ze filesystem rezije muze dominovat. Profilovani potvrdilo komunikacni vzor a vyrovnanou zatez.

### Slajd 7: Zaver

**Obsah:**
- 2D dekompozice snizuje komunikaci
- RMA: Put + packing + PSCW
- Hybrid 2D skaluje nejlepe
- I/O vyzaduje spravne chunkovani

**Vizual:**
- ctyri ikony/bloky:
  - decomposition
  - halo exchange
  - scaling
  - I/O

**Mluvena pointa:**
Vykon solveru vznikl kombinaci vice rozhodnuti. Nejdulezitejsi bylo umet kazde rozhodnuti zmerit a vysvetlit.

## Co pripravit v Excalidraw

1. **Stencil diagram**
   - 2D grid
   - jeden stredovy bod
   - sousedi nahore/dole/vlevo/vpravo

2. **1D vs 2D dekompozice**
   - stejna domena dvakrat
   - vlevo pruhy
   - vpravo dlazdice
   - halo hrany barevne

3. **Timeline iterace**
   - osa casu
   - compute halo
   - start exchange
   - compute inner
   - wait

4. **RMA pipeline**
   - naive: `Get + strided + fence`
   - optimized: `Put + packed + PSCW`
   - mezi nimi sipky s faktory zrychleni

5. **Cache efekt**
   - velka sekvencni domena mimo L3
   - male paralelni dlazdice v L3

6. **HDF5 chunkovani**
   - spatne: mnoho malych chunku
   - dobre: globalni chunk / kolektivni zapis

## Poznamky pred finalni prezentaci

- Sjednotit cislo pro pocet vyher 2D dekompozice. V dokumentu je `63/90`, ve starsich poznamkach bylo `56/90`.
- V prezentaci radeji nepouzivat moc API detailu. `MPI_Win_post/start/complete/wait` staci vyslovit jednou a hned vysvetlit jako "synchronizace jen se sousedy".
- U RMA zduraznit, ze cisla jsou z izolovaneho mikrobenchmarku, ne jen z cele aplikace.
- U paralelniho I/O rict poctive, ze oprava chunkovani pomohla, ale paralelni I/O nebylo pro vsechny merene pripady nejrychlejsi.
