# Otázky a odpovědi k obhajobě

Tento dokument je napsaný jako tréninkový tahák. Odpovědi nejsou jen definice, ale formulace, které se dají říct nahlas u obhajoby.

## Jak odpovídat

- Nezačínej obhajobu detaily API. Nejdřív řekni pointu, potom důkaz.
- U čísel vždy řekni kontext: velikost domény, počet jader, bez I/O nebo s I/O.
- Když si nejsi jistý přesnou hodnotou, řekni relativní závěr a neopírej se o nepodložené číslo.
- RMA neprodávej jako obecně lepší než P2P. Prodávej ho jako dobře vysvětlenou optimalizaci.
- U I/O nemluv o konkrétní propustnosti, pokud ji nemáš přímo v grafu nebo reportu.

## Základ projektu

### Co projekt řeší?

Projekt řeší paralelní simulaci šíření tepla ve 2D doméně. Výpočet je stencil metoda, kde se v každé iteraci nová teplota bodu počítá z okolních bodů a materiálových parametrů.

Paralelizace je přes MPI a OpenMP. MPI rozděluje doménu mezi procesy, OpenMP paralelizuje výpočet uvnitř lokální dlaždice. Protože stencil potřebuje sousední hodnoty, procesy si v každé iteraci vyměňují halo zóny.

### Jaká je hlavní myšlenka tvého řešení?

Hlavní myšlenka je rozdělit doménu na dlaždice, výpočet uvnitř dlaždice dělat lokálně a mezi procesy posílat jen okrajové halo zóny. Implementace podporuje 1D a 2D dekompozici, P2P i RMA halo exchange a paralelní HDF5 výstup.

Výkonnostně je důležité, že komunikace halo zón se spouští před výpočtem vnitřku dlaždice. Část komunikace se tím překryje s výpočtem.

### Co je podle tebe největší přínos projektu?

Největší přínos není jen samotná paralelizace, ale rozklad výkonu na měřitelné příčiny. U škálování se vysvětlil rozdíl 1D a 2D dekompozice, u RMA se oddělil vliv `MPI_Get` proti `MPI_Put`, packingu a synchronizace, a profilování potvrdilo očekávaný komunikační vzor.

### Co bys zmínil jako hlavní výsledek jednou větou?

Nejlepší výkon vyšel z kombinace 2D dekompozice, překrytí halo výměny s výpočtem a odstranění hlavních režijních problémů v RMA komunikaci.

### Jaké technologie jsi použil?

Použil jsem MPI pro rozdělení domény a komunikaci mezi procesy, OpenMP pro výpočet uvnitř procesu, HDF5 pro výstup dat a Score-P, Vampir a Cube pro profilování.

## Numerická metoda a stencil

### Jak funguje výpočet jednoho bodu?

Nová teplota bodu se počítá ze středu a sousedů ve čtyřech směrech. V kódu `computePoint` používá sousedy vlevo, vpravo, nahoře a dole ve vzdálenosti 1 i 2. Hodnoty jsou vážené materiálovými parametry.

Pokud je bod vzduch, aplikuje se ještě ochlazení přes `airflowRate` a `coolerTemp`.

### Proč má halo šířku 2?

Protože stencil používá sousedy ve vzdálenosti 2. Aby proces mohl spočítat body u hranice lokální dlaždice, potřebuje od souseda dvě vrstvy buněk.

V kódu je to `haloZoneSize = 2`.

### Co je halo zóna?

Halo zóna je kopie okrajových dat sousední dlaždice. Proces ji lokálně nepočítá jako svoji vnitřní doménu, ale potřebuje ji, aby mohl spočítat stencil u hranice.

### Proč nestačí posílat jen jednu buňku okraje?

Nestačí to kvůli šířce stencil operátoru. Výpočet bodu se dívá až o dvě pozice nahoru, dolů, doleva a doprava. Jedna vrstva by pokryla jen stencil s dosahem 1.

### Jsou okrajové podmínky nějak speciálně řešené?

Okraje globální domény se nepočítají stejně jako vnitřek, protože stencil potřebuje okolí. Lokální výpočet proto rozlišuje, jestli proces má souseda v daném směru. Pokud soused neexistuje, jde o okraj globální domény a halo výměna se v tom směru nespouští.

### Jak se ověřuje správnost výsledku?

Správnost se typicky kontroluje porovnáním výstupu paralelní varianty se sekvenční referencí a kontrolou finální průměrné teploty ve středním sloupci. V kódu se tato hodnota počítá paralelně přes procesy, které vlastní střední sloupec, a na konci také sekvenčně z výsledku.

## Dekompozice domény

### Jaký je rozdíl mezi 1D a 2D dekompozicí?

U 1D dekompozice se doména řeže jen v jednom směru, takže vzniknou dlouhé pruhy. Proces komunikuje typicky se dvěma sousedy, ale posílá dlouhé hrany.

U 2D dekompozice vzniknou dlaždice. Proces může komunikovat až se čtyřmi sousedy, ale každá hrana je kratší.

### Proč je 2D dekompozice výhodnější?

Protože zmenšuje objem halo dat na proces. U čtvercové domény a `P` procesů vychází poměr objemu 1D proti 2D přibližně:

```text
V_1D / V_2D = sqrt(P) / 2
```

Při `P = 16` posílá 1D asi `2×` více dat. Při `P = 256` už asi `8×` více dat.

### Proč má 2D více sousedů, ale pořád je lepší?

Počet sousedů sice vzroste ze dvou na čtyři, ale délka každé komunikované hrany klesne mnohem víc. U většího počtu procesů je rozhodující celkový objem dat, ne jen počet zpráv.

### Kdy by 1D dekompozice mohla být dobrá?

U menšího počtu procesů nebo u menších domén, kde komunikace ještě není dominantní. 1D má jednodušší komunikační vzor a méně sousedů. Jakmile ale roste počet procesů, delší halo hrany začnou být problém.

### Jak se vybírá 2D procesová mřížka?

Procesy jsou uspořádány do 2D kartézské topologie. V implementaci se používá rozdělení podle zadaných rozměrů `grid_tiles_x` a `grid_tiles_y`. Potom se vytvoří `MPI_Cart_create` a sousedé se získají přes `MPI_Cart_shift`.

### Co když velikost domény není dělitelná procesovou mřížkou?

Tahle implementace je stavěná pro projektové benchmarky, kde jsou velikosti domén a procesové mřížky zvolené tak, aby dělení vycházelo. Obecné nerovnoměrné dělení by šlo doplnit, ale nebylo hlavním cílem projektu.

### Proč je zátěž vyrovnaná?

Každý proces dostane stejně velkou dlaždici a stencil má konstantní práci na bod. Proto by výpočetní čas mezi procesy měl být podobný. To potvrzuje i Vampir Process Summary.

### Co by mohlo zátěž rozhodit?

Nerovnoměrné velikosti dlaždic, adaptivní výpočet nebo různá cena bodů podle materiálu. Tady se ale každý bod počítá stejnou stencil funkcí, takže materiál mění hodnoty, ne počet operací zásadním způsobem.

## Průběh iterace

### Jak přesně probíhá jedna iterace?

Iterace má čtyři hlavní části.

1. `computeHaloZones` spočítá okrajové části dlaždice, které budou potřeba sousedům.
2. `startHaloExchangeP2P` nebo `startHaloExchangeRMA` spustí výměnu halo zón.
3. `updateTile` spočítá vnitřek dlaždice, který už na halo nečeká.
4. `awaitHaloExchangeP2P` nebo `awaitHaloExchangeRMA` počká na dokončení komunikace.

### Proč nejdřív počítáš okraje a potom vnitřek?

Okraje jsou data, která musí odejít sousedům. Když je spočítám první, můžu komunikaci spustit co nejdřív. Během komunikace pak počítám vnitřek dlaždice, který nezávisí na nově příchozím halo.

### Co přesně znamená překrytí komunikace a výpočtu?

Znamená to, že proces nečeká nečinně na halo hned po spuštění přenosu. Pošle nebo zapíše halo data a mezitím počítá vnitřní část lokální dlaždice. Teprve potom čeká na dokončení komunikace.

### Je komunikace překrytá úplně?

Ne nutně. Profilování ukazuje, že na konci iterace pořád existuje čekání na halo. Překrytí snižuje viditelnou cenu komunikace, ale nezaručuje, že komunikace zmizí úplně.

### Kdy překrytí funguje nejlépe?

Když má dlaždice velký vnitřek vůči okraji. U velkých domén je dost práce, kterou lze dělat během komunikace. U malých dlaždic je vnitřek malý, takže není čím komunikaci schovat.

## MPI topologie a distribuce dat

### Proč používáš kartézskou topologii?

Protože problém je přirozeně mřížkový. Kartézská topologie umožňuje jednoduše mapovat procesy na dlaždice a získat sousedy přes `MPI_Cart_shift`.

### K čemu je `MPI_Cart_shift`?

Vrací rank souseda v daném směru. Používá se pro zjištění levého, pravého, horního a dolního souseda. Pokud soused neexistuje, dostane se `MPI_PROC_NULL`.

### Jak řešíš procesy na kraji domény?

Když `MPI_Cart_shift` vrátí `MPI_PROC_NULL`, v daném směru se komunikace nespouští. Proces tedy nečeká na neexistujícího souseda.

### Jak se rozesílají počáteční data?

Používá se `MPI_Scatterv` s MPI subarray datovými typy. Ty popisují, která část globální domény patří danému procesu a kam se má uložit v lokální dlaždici s halo okrajem.

### Jak se skládá výsledná doména?

Na konci se lokální dlaždice sbírají přes `MPI_Gatherv` zpět do globálního pole. Opět se používají subarray datové typy, aby se správně mapovaly lokální a globální indexy.

### Proč nepoužíváš ruční kopírování pro scatter a gather?

MPI datové typy umožňují popsat 2D výřezy bez ručního balení celé domény na master ranku. Je to přirozenější pro dlaždice a méně náchylné na chyby v indexování.

### Proč rank 0 vychází v některých profilech výrazně?

Protože rank 0 se účastní scatter a gather částí běhu. To není problém halo komunikace, ale důsledek vstupní a výstupní distribuce dat.

## P2P halo exchange

### Jak funguje P2P komunikace?

P2P varianta používá neblokující `MPI_Isend` a `MPI_Irecv` pro jednotlivé směry. Přenosy se spustí, potom běží výpočet vnitřku dlaždice a nakonec `MPI_Waitall` čeká na dokončení.

### Proč neblokující komunikace?

Kvůli překrytí komunikace s výpočtem. Blokující `MPI_Send` a `MPI_Recv` by vedly k tomu, že proces čeká dřív a má méně prostoru schovat komunikaci za výpočet.

### Proč používáš derived datatype pro halo?

Horizontální halo je v paměti souvislejší, svislé halo je strided. MPI derived datatype umí popsat svislou hranu bez ručního kopírování. U P2P je to čisté a přehledné řešení.

### Proč P2P vyšlo tak dobře?

P2P je přímočaré, neblokující a dobře odpovídá sousedské halo komunikaci. V nejlepší konfiguraci Hybrid 2D P2P dosáhla varianta přibližně `666×` na `4096^2`, 256 jader, bez I/O.

### Proč ne neighbor collectives?

Neighbor collectives by byly přirozená možnost pro kartézskou topologii. V projektu jsem použil explicitní P2P, protože dává přímou kontrolu nad směry, buffery a překrytím. Neighbor collectives bych uvedl jako možný směr další práce.

### Co jsou persistent requests a proč je zmiňuješ jako další práci?

Persistent requests umožňují předem vytvořit komunikační požadavky a v iteracích je jen opakovaně spouštět. U halo exchange se komunikační vzor nemění, takže by to mohlo snížit opakovanou režii vytváření požadavků.

## RMA halo exchange

### Co je MPI RMA?

RMA je jednostranná komunikace. Jeden proces může zapisovat nebo číst paměťový prostor jiného procesu přes MPI window. U halo exchange to znamená, že procesy mohou zapisovat svoje okraje přímo do přijímacího bufferu sousedů.

### Jaká byla původní RMA varianta?

Původní varianta používala strided `MPI_Get` a `MPI_Win_fence`. Proces si četl halo data ze souseda. V měření se ukázalo, že tahle kombinace byla velmi pomalá.

### Jaká je finální RMA varianta?

Finální varianta používá `MPI_Put`, packing do souvislého bufferu a PSCW synchronizaci. Proces tedy zapíše svoje halo data sousedovi, místo aby si je soused četl.

### Proč bylo `MPI_Get` horší než `MPI_Put`?

U `MPI_Get` musí origin proces číst vzdálená, navíc strided data. To může mít větší režii na MPI implementaci a RDMA cestě. `MPI_Put` umožní každému procesu připravit vlastní data lokálně a zapsat je sousedovi.

Krátká odpověď u obhajoby: na měřené platformě byl dominantní problém strided `MPI_Get`; izolované měření ukázalo, že změna na `MPI_Put` dává `55-80×`.

### Znamená to, že `MPI_Put` je vždy lepší než `MPI_Get`?

Ne. Je to výsledek pro tuto implementaci, tento layout dat a měřenou platformu. Obecně záleží na MPI implementaci, síti, layoutu paměti a synchronizaci. Proto jsem to měřil izolovaně.

### Co přesně znamená packing?

Packing znamená, že halo data se před přenosem zkopírují do souvislého bufferu. Po přenosu se na druhé straně rozbalí zpět do halo pozic.

### Proč packing pomohl?

Protože svislé halo hrany jsou v paměti nesouvislé. Přenos přes derived datatype může mít vysokou režii. Souvislý buffer se přenáší jednodušeji.

### Nemůže packing uškodit?

Ano, může. Pokud je halo už souvislé, packing přidává zbytečnou kopii. Proto je dobrý směr další práce podmíněný packing, tedy balit jen nesouvislé směry.

### Co je `MPI_Win_fence`?

`MPI_Win_fence` je jednoduchá RMA synchronizace, ale synchronizuje všechny procesy v okně. Pro halo exchange je to zbytečně široké, protože proces komunikuje jen se sousedy.

### Co je PSCW?

PSCW znamená `post`, `start`, `complete`, `wait`. Je to aktivní cílová synchronizace v MPI RMA. V této implementaci se používá jen pro skupinu sousedů, takže odpovídá halo komunikaci.

### Proč PSCW pomohlo?

Protože synchronizuje jen relevantní sousedy místo celé skupiny procesů. Přínos byl menší na jednom uzlu a větší přes více uzlů, protože mezizlová synchronizace je dražší.

### Jaké byly faktory RMA zrychlení?

`MPI_Get` na `MPI_Put` dalo `55-80×`. Packing dal `1,5-3×`. Fence na PSCW dalo `1,15×` na jednom uzlu a `1,74×` na dvou uzlech. Kombinovaný efekt proti naivní variantě byl `133-268×`.

### Je finální RMA varianta neblokující?

RMA fáze je rozdělena na start a wait podobně jako P2P. Po zahájení RMA operací se počítá vnitřek dlaždice a potom se čeká přes `MPI_Win_complete` a `MPI_Win_wait`.

### Proč máš pro RMA samostatný receive buffer?

Protože `MPI_Put` zapisuje souvislá packed data do přijímacího RMA bufferu. Po dokončení synchronizace se data rozbalí do skutečných halo pozic v lokální dlaždici.

### Proč se nedá jednoduše zapisovat rovnou do halo pozic?

Dalo by se to popsat derived datatype, ale právě strided layout byl problém. Souvislý RMA buffer snižuje režii přenosu a layout se řeší lokálním unpackem.

### Proč RMA nakonec není hlavní vítěz proti P2P?

Protože optimalizovaná RMA varianta odstranila velkou režii původní RMA implementace, ale P2P je v produkčním strong scalingu pořád velmi silné. Nejlepší běh je Hybrid 2D P2P.

### Jak bys obhájil, že RMA část má smysl?

Má smysl, protože ukazuje metodickou optimalizaci. Neřekl jsem jen „RMA je pomalé“, ale oddělil jsem tři příčiny: směr operace, layout dat a synchronizaci. Každá měla samostatně měřitelný dopad.

## OpenMP

### Kde se používá OpenMP?

OpenMP se používá v `updateTile`, kde se paralelizuje výpočet řádků lokální dlaždice přes `#pragma omp parallel for`. Uvnitř smyčky je ještě SIMD pragma pro vektorizaci přes body v řádku.

### Proč kombinovat MPI a OpenMP?

Hybridní model snižuje počet MPI procesů proti čistému MPI a využívá vlákna uvnitř uzlu. Může tím snížit počet komunikujících ranků a zároveň využít všechna jádra.

### Proč Hybrid 2D vyšel nejlépe?

Protože kombinuje výhodnou 2D dekompozici s využitím více jader přes OpenMP. U velké domény zároveň těží z cache efektu a menších lokálních dlaždic.

### Jaká je možná režie OpenMP?

Pokud se v každé iteraci opakovaně vytváří paralelní region, může vznikat fork/join režie. Proto je v návrzích další práce ověření OpenMP režie.

### Proč nepoužít čisté OpenMP?

Čisté OpenMP by fungovalo jen v rámci jednoho uzlu sdílené paměti. Projekt cílí na škálování přes více uzlů, takže je potřeba MPI.

### Proč nepoužít čisté MPI všude?

Čisté MPI také funguje a má dobré výsledky. Hybridní varianta ale umožňuje využít více jader s menším počtem MPI ranků, což může snížit komunikační tlak a lépe sedět na uzlovou architekturu.

## Silné škálování

### Co je silné škálování?

Silné škálování měří, jak se zrychluje výpočet pevné velikosti problému při rostoucím počtu jader. Ideální je, když čas klesá úměrně počtu jader.

### Jaký byl nejlepší strong scaling výsledek?

Hybrid 2D P2P na doméně `4096^2`, 256 jader, bez I/O, přibližně `666×`.

### Proč je `666×` více než ideálních `256×`?

Protože reference je sekvenční běh na stejné velké doméně. Sekvenční pracovní sada je velká a naráží na paměť. Paralelní dlaždice jsou menší a lépe se vejdou do cache, takže výpočet na bod může být rychlejší než v sekvenčním běhu.

### Není superlineární speedup podezřelý?

Je potřeba ho vysvětlit, ale není automaticky špatně. U paměťově náročných stencil výpočtů je cache efekt realistické vysvětlení. V prezentaci je dobré zdůraznit, že jde o měření bez I/O.

### Proč u menších domén škálování horší?

Menší doména má méně práce na proces. Režie komunikace, synchronizace, OpenMP a MPI startů potom tvoří větší část celkového času. U velké domény je víc výpočtu, který režii překryje.

### Proč čisté MPI 2D končí na 128 MPI rancích?

V měřených konfiguracích čisté MPI 2D jde do 128 MPI ranků. Hybridní 2D jde na 256 jader přes 32 MPI procesů a 8 OpenMP vláken. Proto je dobré ty grafy číst podle konfigurace, ne jen podle počtu křivek.

### Proč je Hybrid 1D slabší?

Protože má větší halo objem. Report to shrnuje tak, že Hybrid 1D je přibližně `1,6×` slabší než Hybrid 2D. Pointa není přesné jedno číslo, ale komunikační rozdíl 1D proti 2D.

### Co když se někdo zeptá na rozdíl `365×` a `407×` u Hybrid 1D?

Řekni, že ve slidu nechceš stavět závěr na jednom sporném čísle, protože hlavní závěr je relativní: 1D zaostává za 2D kvůli většímu halo objemu. Ve finálním reportu je pro přímé srovnání uvedeno přibližně `1,6×` horší chování Hybrid 1D proti Hybrid 2D.

## Slabé škálování

### Co je slabé škálování?

Slabé škálování měří, co se děje, když spolu s počtem procesů roste i velikost problému. Každý proces má zhruba stejnou práci. Ideálně by čas iterace zůstal stejný.

### Jaké byly výsledky slabého škálování?

MPI 2D drží `82,2 %` na 256 jádrech. Hybrid 2D má `78,5 %` na 128 jádrech. Hybrid 1D klesá na `55,5 %` na 256 jádrech.

### Proč 1D slabé škálování klesá víc?

Protože při rostoucím počtu procesů zůstávají 1D pruhy komunikačně nevýhodné. Halo hrany jsou dlouhé a komunikace roste hůř než u 2D dlaždic.

### Proč slabé škálování není stoprocentní?

Kvůli komunikaci, synchronizaci, nerovnoměrnostem runtime prostředí a režii MPI/OpenMP. I když práce na proces zůstává podobná, komunikace a synchronizace nerostou zadarmo.

## Profilování

### Proč jsi používal Score-P?

Score-P slouží k instrumentaci běhu a sběru profilovacích dat. Z něj lze potom data analyzovat ve Vampiru a Cube. V prezentaci ho zmiňuji jako zdroj profilovacích výstupů.

### Co ukazuje Vampir timeline?

Ukazuje časový průběh procesů. U měření je vidět pravidelný vzor iterací: výpočet, MPI komunikace a čekání. Slouží jako vizuální kontrola, že aplikace má očekávanou strukturu.

### Co z timeline vyvozuješ?

Výpočet tvoří dominantní část času, ale komunikace a čekání na halo jsou viditelné. Překrytí tedy pomáhá, ale komunikaci úplně neeliminuje.

### Co ukazuje Function Summary?

Rozdělení času podle funkcí. Potvrzuje, že největší část času je ve výpočetním jádře, zejména v aktualizaci bodů. MPI části odpovídají halo komunikaci a čekání.

### Co ukazuje Process Summary?

Ukazuje rozdělení času mezi procesy. Podobné hodnoty mezi procesy potvrzují vyváženou zátěž.

### Co ukazuje komunikační matice?

Ukazuje, které procesy mezi sebou komunikují a v jakém objemu. U 2D dekompozice je vidět komunikace se sousedy v kartézské topologii. Oproti 1D je více sousedů, ale menší objem na hranu.

### Proč uvádíš `840 KiB` a `210 KiB`?

Je to interpretace měřítka ve Vampir komunikační matici. 1D má maximum `840 KiB`, 2D `210 KiB`. Podporuje to teoretický závěr, že 2D má kratší hrany a menší objem komunikace na jednu hranu.

### Co ukazuje Cube?

Cube používám jako další pohled na profilovací data, zejména na rozložení komunikace a procesů. V prezentaci ukazuje, že halo komunikace je mezi procesy vyrovnaná.

### Proč v Cube vyniká rank 0?

Protože profil zahrnuje i scatter a gather části běhu. Rank 0 má v těchto fázích speciální roli. Samotná halo komunikace mezi ostatními ranky je rovnoměrná.

### Máš I/O Summary?

Nemám ho jako samostatný slajd. I/O závěr proto neopírám o detailní I/O Summary, ale o měřené časy, implementaci chunkování a opatrnou interpretaci. Netvrdím konkrétní propustnosti.

### Splňuje prezentace požadavek na profilovací výstupy?

Ano. Obsahuje Vampir timeline, Function Summary, Process Summary, komunikaci a Cube topologii. Pokrývá poměr výpočtu a komunikace, vyvážení zátěže a komunikační vzor.

## HDF5 a I/O

### Jak je implementované paralelní I/O?

Používá se paralelní HDF5 přes MPI-IO. Každý proces vybere hyperslab odpovídající své lokální dlaždici a zapisuje kolektivně do společného datasetu.

### Co je hyperslab?

Hyperslab je výběr části vícerozměrného datasetu v HDF5. Tady odpovídá dlaždici procesu v globální 2D doméně.

### Co byl problém s chunkováním?

Špatné chunkování vytvářelo mnoho malých lokálních chunků. To vedlo k vysoké režii a špatnému chování paralelního zápisu.

### Jak jsi chunkování opravil?

Nastavil jsem `H5Pset_chunk` na velikost celé globální domény. Tím vznikne jeden společný chunk pro dataset a kolektivní zápis se chová výrazně lépe než při lokálních chunkech.

### Proč je jeden globální chunk lepší?

Pro měřený způsob zápisu umožnil lépe agregovat kolektivní zápis a odstranit režii mnoha malých chunků. Není to univerzální pravidlo pro všechny aplikace, ale pro tento benchmark to výrazně pomohlo.

### Co je Lustre striping?

Lustre striping určuje, jak se soubor rozkládá přes OST úložiště. V benchmark skriptech se používá stripe size `1M` a stripe count `16`, aby se soubor lépe rozložil přes storage.

### Proč paralelní I/O nebylo vždy rychlejší než sekvenční?

Protože snapshoty jsou relativně malé, přibližně `4-64 MB`. Paralelní HDF5 a Lustre mají koordinační režii. U malých zápisů tato režie může převážit nad přínosem paralelismu.

### Co bys optimalizoval u I/O dál?

Méně časté a větší kolektivní zápisy. Tedy batching snapshotů nebo úprava write intensity, aby se režie filesystemu amortizovala přes větší objem dat.

### Proč neříkat konkrétní propustnost I/O?

Protože v prezentaci nemáš samostatný I/O Summary graf s propustností. Je lepší držet se doloženého závěru: chunkování a striping pomohly, ale malé snapshoty pořád narážely na režii.

## Výsledky a interpretace

### Jaké jsou tři hlavní důvody dosažených výsledků?

První je 2D dekompozice, která snižuje objem halo dat. Druhý je cache efekt u velkých domén, který vysvětluje superlineární speedup. Třetí je odstranění režie v RMA komunikaci přes `MPI_Put`, packing a PSCW.

### Proč nejlepší výsledek není RMA, když RMA mělo obrovské zrychlení?

Protože obrovské RMA zrychlení je proti naivní RMA variantě. P2P už od začátku nebylo tak špatné a v produkčním strong scalingu vyšlo velmi dobře. Proto je nejlepší celkový běh Hybrid 2D P2P.

### Jak obhájíš, že RMA zrychlení není zavádějící?

Řekl bych jasně, že jde o zrychlení RMA halo exchange proti naivní RMA variantě, ne o zrychlení celé aplikace proti P2P. Proto je v prezentaci uvedeno jako izolované měření RMA.

### Co je nejdůležitější graf ve slajdech?

Strong scaling Hybrid 2D P2P ukazuje nejlepší výsledek. RMA tabulka ukazuje hlavní technickou optimalizaci. Weak scaling ukazuje, že 2D dekompozice drží efektivitu lépe než 1D.

### Co když se zeptají, proč tam nejsou všechny grafy?

Prezentace má jen 5 až 7 minut, takže jsem vybral grafy, které pokrývají požadavky zadání: silné škálování, slabé škálování, profilování a zdůvodnění výsledků. Kompletní sada grafů je v reportu.

### Co když budou chtít přesnou metodiku měření?

Měřil jsem iterační čas a zrychlení vůči sekvenční implementaci na stejné velikosti domény. U strong scalingu je velikost problému pevná a roste počet jader. U weak scalingu roste velikost domény s počtem procesů.

### Proč používáš čas na iteraci?

Protože počet iterací se mezi konfiguracemi může lišit podle velikosti domény nebo běhu. Čas na iteraci je čistší metrika pro porovnání výkonu stencil výpočtu.

## Možné slabiny a férové odpovědi

### Co je největší slabina implementace?

Nejobecnější slabina je, že implementace je optimalizovaná pro pravidelnou čtvercovou doménu a benchmarkové konfigurace. Neřeší obecné nerovnoměrné dělení nebo adaptivní load balancing.

### Co bys udělal jinak, kdybys měl víc času?

Zaměřil bych se na podmíněný packing, persistent P2P komunikaci, měření OpenMP režie a větší I/O batching. Tyto změny přímo navazují na zjištěné režie.

### Co bys udělal pro lepší RMA?

Otestoval bych podmíněný packing podle směru halo, případně přímé souvislé `MPI_Put` pro horizontální hrany. Také bych porovnal různé MPI implementace, protože RMA výkon je na nich citlivý.

### Co bys udělal pro lepší P2P?

Vyzkoušel bych persistent requests a případně neighbor collectives. Komunikační vzor se v iteracích nemění, takže by šlo snížit opakovanou režii.

### Co bys udělal pro lepší OpenMP?

Prověřil bych, jestli se nevyplatí jeden větší paralelní region přes více částí iterace, aby se snížila fork/join režie. Také by dávalo smysl měřit vliv affinity a rozložení vláken.

### Co bys udělal pro lepší I/O?

Méně časté větší zápisy, případně agregace více snapshotů. Cílem je, aby velikost zápisu byla dostatečná a režie Lustre a HDF5 se rozložila přes víc dat.

### Co bys z prezentace radši neříkal, pokud se nezeptají?

Nešel bych hluboko do pořadí `MPI_Win_post/start/complete/wait`. Nečetl bych všechny body v grafech. Netvrdil bych konkrétní I/O propustnost. Neříkal bych, že RMA je obecně lepší než P2P.

## Rýpavé otázky

### Nemůže být superlineární speedup jen špatná reference?

Reference je sekvenční implementace na stejné velikosti domény. Superlinearita se dá vysvětlit cache efektem, protože paralelní běh změní velikost pracovní sady na proces. Zároveň bych netvrdil, že jde o čistou algoritmickou škálovatelnost. Je to kombinace paralelizace a lepší práce s cache.

### Proč používáš aproximace jako `~666×`?

Protože grafy a měření mají variabilitu a jde o prezentaci hlavního trendu. Přesná čísla jsou v reportu a CSV. Pro obhajobu je důležitější interpretace, proč je Hybrid 2D nejlepší a proč 1D zaostává.

### Není packing jen přesunutí práce z MPI do CPU?

Ano, packing přidá lokální kopírování. V tomto případě se ale vyplatilo, protože odstranilo dražší režii strided RMA přenosu. Kdyby halo bylo souvislé, packing by se vyplatit nemusel.

### Není jeden globální HDF5 chunk špatný obecně?

Může být. Je to optimalizace pro tento konkrétní zápis a měřený pattern. Obecně chunkování záleží na přístupovém vzoru. Tady šlo o kolektivní zápis celé globální domény po snapshotech, takže globální chunk odstranil problém mnoha malých chunků.

### Proč jsi neporovnal víc MPI implementací?

Projekt byl měřený na dostupném systému a MPI stacku. RMA výsledky mohou být implementačně citlivé, takže porovnání MPI implementací by bylo zajímavé, ale mimo hlavní rozsah projektu.

### Jak víš, že profilování není zkreslené instrumentací?

Profilování používám hlavně kvalitativně: vzor iterací, vyvážení zátěže a komunikační struktura. Hlavní výkonová čísla vychází ze samostatných benchmarků, ne jen z instrumentovaného běhu.

### Proč nejsou všechny procesy úplně stejné?

Rank 0 má speciální roli ve scatter/gather a některé procesy mohou být na okraji domény s méně sousedy. Důležité je, že hlavní halo komunikace a výpočet jsou v profilech přibližně vyrovnané.

### Co když se zeptají na průměrnou velikost zprávy?

Odpověz přes halo geometrii. Velikost zprávy odpovídá hraně dlaždice krát šířka halo a velikost `float`. U 2D jsou zprávy kratší než u 1D, protože hrany dlaždic jsou kratší. Pokud chtějí přesnou hodnotu z nástroje, drž se toho, co je na komunikační matici, tedy 1D maximum `840 KiB` a 2D `210 KiB`.

### Co když se zeptají na dosažené propustnosti?

Řekni, že propustnost není hlavní metrika ve slajdech. Prezentuješ speedup, weak scaling a profilovací výstupy. Bez konkrétního I/O Summary nebo throughput grafu bys propustnost nechtěl interpretovat nepřesně.

### Co když se zeptají, proč máš tolik profiling slajdů?

Zadání přímo chce výstupy ze Score-P, Vampir a Cube. Slajdy jsou důkazní materiál: timeline pro poměr výpočtu a komunikace, Process Summary pro load balance, komunikační matice pro komunikaci a Cube pro topologický pohled.

### Co když řeknou, že prezentace moc řeší RMA?

RMA je nejzajímavější optimalizační část, protože má jasný rozklad příčin. Ale prezentace zároveň obsahuje strong scaling, weak scaling, profilování i I/O. RMA není náhrada celkových výsledků, je to detailní vysvětlení jedné výkonové části.

## Krátké odpovědi na zapamatování

### Proč 2D?

Protože snižuje objem halo dat. Má více sousedů, ale kratší hrany.

### Proč superlinearita?

Protože paralelní dlaždice mají lepší cache locality než sekvenční celá doména.

### Proč `MPI_Put`?

Protože na měřené platformě byl strided `MPI_Get` dominantní problém a `MPI_Put` ho odstranil.

### Proč packing?

Protože souvislý buffer byl levnější než strided RMA přenos.

### Proč PSCW?

Protože halo potřebuje synchronizovat jen sousedy, ne všechny procesy.

### Proč P2P pořád vyhrává?

Protože P2P je jednoduché, neblokující a pro tuto halo výměnu velmi efektivní.

### Proč paralelní I/O ne vždy vyhrává?

Protože malé snapshoty naráží na koordinační režii HDF5 a Lustre.

### Co dál?

Podmíněný packing, persistent P2P requests, měření OpenMP režie a větší I/O batching.
