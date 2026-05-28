# Příprava na obhajobu

## Hlavní linka

Projekt není jen paralelní přepis solveru. Hlavní přínos je v tom, že výkon je vysvětlený měřením.

Řekni to takto:

1. Doména je rozdělena mezi MPI procesy jako 1D nebo 2D kartézská topologie.
2. OpenMP počítá lokální dlaždici, MPI řeší halo zóny.
3. Komunikace se částečně překrývá s výpočtem vnitřku dlaždice.
4. 2D dekompozice snižuje objem halo dat.
5. RMA problém byl rozložen na směr operace, layout dat a synchronizaci.
6. Profilování potvrzuje vyváženou zátěž a očekávaný komunikační vzor.

## Čísla, která si pohlídat

- Hybrid 2D P2P dosahuje přibližně `666×` na `4096^2`, 256 jader, bez I/O.
- MPI 2D P2P dosahuje přibližně `354×` na `4096^2`, 128 MPI ranků.
- Hybrid 1D je v reportu přibližně `1,6×` slabší než Hybrid 2D.
- Slabé škálování drží MPI 2D na `82,2 %`, Hybrid 2D na `78,5 %`, Hybrid 1D na `55,5 %`.
- `MPI_Get` na `MPI_Put` dalo `55-80×`.
- Packing dal `1,5-3×`.
- `MPI_Win_fence` na PSCW dalo `1,15×` na jednom uzlu a `1,74×` na dvou uzlech.
- Celkový RMA zisk proti naivní variantě je `133-268×`.
- Komunikační matice má pro 1D maximum `840 KiB`, pro 2D `210 KiB`.
- U I/O říkat opatrně: chunkování a Lustre striping výrazně pomohly, ale malé snapshoty `4-64 MB` pořád naráží na režii filesystemu.

## Pravděpodobné otázky

### Proč má halo šířku 2?

Stencil v `computePoint` používá sousedy ve vzdálenosti `1` i `2` v každém směru. Proto má `ParallelHeatSolver` `haloZoneSize = 2`.

### Jak přesně probíhá jedna iterace?

Nejdřív se spočítají okrajové buňky, které půjdou do halo. Pak se spustí P2P nebo RMA výměna. Během ní se počítá vnitřek dlaždice. Nakonec se čeká na dokončení halo exchange.

### Proč je 2D dekompozice lepší než 1D?

U 1D se posílají dlouhé hrany. U 2D má proces více sousedů, ale kratší hrany. Pro čtvercovou doménu platí

```text
V_1D / V_2D = sqrt(P) / 2
```

Při větším počtu procesů tedy 1D komunikuje výrazně víc dat.

### Proč vyšlo superlineární zrychlení?

Kvůli cache efektu. Sekvenční `4096^2` doména je velká pracovní sada. Po rozdělení na dlaždice má každý proces menší část dat a dvě teplotní pole se lépe vejdou do L3 cache.

### Je superlineární speedup chyba měření?

Ne nutně. U stencil výpočtů je to běžné, pokud sekvenční běh naráží na paměťovou propustnost a paralelní dlaždice se vejdou do cache. Důležité je říct, že jde o bez I/O měření.

### Proč bylo `MPI_Get` tak pomalé?

V původní variantě šlo o strided čtení přes derived datatype. Na měřené kombinaci Cascade Lake, Intel MPI a RDMA cesty to mělo velkou režii. `MPI_Put` obrátil směr operace, proces zapisuje svoje lokální halo data sousedovi.

### Proč pomohl packing?

Svislé halo hrany nejsou v paměti souvislé. Derived datatype sice umí popsat strided layout, ale MPI pak musí pracovat s nesouvislým přístupem. Packing vytvoří souvislý buffer, který se přenáší levněji.

### Proč je PSCW lepší než `MPI_Win_fence`?

`MPI_Win_fence` synchronizuje celé okno v komunikátoru. Halo exchange potřebuje jen přímé sousedy. PSCW používá sousedskou groupu, takže synchronizace odpovídá skutečnému komunikačnímu vzoru.

### Je RMA po optimalizaci lepší než P2P?

Neprodávej to jako obecné vítězství RMA. V produkčním škálování je nejlepší Hybrid 2D P2P. RMA část je důležitá hlavně proto, že ukazuje, jak velký rozdíl dělá `Put`, packing a PSCW proti naivní RMA variantě.

### Proč používáš P2P přes `MPI_Isend` a `MPI_Irecv`?

Kvůli neblokující halo výměně a překrytí s výpočtem. Po spuštění komunikace se počítá vnitřek dlaždice a až potom se volá `MPI_Waitall`.

### Jak řešíš procesy na okraji domény?

Sousedé se zjišťují přes `MPI_Cart_shift`. Když soused neexistuje, rank je `MPI_PROC_NULL`. V P2P se pro takový směr nespouští přenos, v RMA se také kontroluje existence souseda.

### Jak víš, že je zátěž vyrovnaná?

Doména je rozdělena na stejně velké dlaždice a stencil má konstantní práci na bod. Vampir Process Summary ukazuje podobné časy procesů. Cube ukazuje, že kromě ranku 0 jsou halo objemy téměř stejné.

### Proč v Cube vystupuje rank 0?

Protože profil zahrnuje i scatter a gather části běhu. Samotná halo komunikace je mezi ostatními ranky rovnoměrná.

### Co přesně ukazuje Vampir timeline?

Pravidelný vzor iterací: lokální výpočet, MPI komunikaci a čekání. Slouží jako důkaz, že výpočet dominuje, ale na konci iterace pořád existuje čekání na halo.

### Co ukazuje Function Summary?

Že hlavní část času je ve výpočetním jádře, tedy v aktualizaci bodů. MPI části jsou hlavně komunikace a čekání.

### Co ukazuje komunikační matice?

Procesy komunikují hlavně se sousedy v kartézské topologii. 2D má širší vzor, protože má až čtyři sousedy, ale menší objem na hranu.

### Jak funguje distribuce dat?

Používají se MPI subarray datové typy pro globální i lokální dlaždice. Na začátku se data rozesílají přes `MPI_Scatterv`, na konci se sbírají přes `MPI_Gatherv`.

### Co dělá OpenMP?

`updateTile` používá `#pragma omp parallel for` přes řádky a uvnitř `#pragma omp simd`. Výpočet bodu je čistá lokální stencil operace, takže se dobře paralelizuje.

### Co je slabina implementace?

Kód předpokládá rozdělení domény tak, aby velikost šla rozumně dělit procesovou mřížkou. Neřeší obecný load balancing pro nerovnoměrné nebo nepravidelné domény. Pro projektové benchmarky to stačí.

### Proč paralelní I/O nebylo vždy rychlejší?

Protože snapshoty jsou malé. Paralelní HDF5 a Lustre mají koordinační režii. Chunkování a striping opravily špatnou původní variantu, ale u malých zápisů se režie nemusí vyplatit.

### Co bys optimalizoval dál?

Nejbezpečnější odpověď:

- podmíněný packing, aby se nebalilo souvislé halo,
- měření OpenMP fork/join režie,
- persistent requests pro P2P,
- méně časté a větší kolektivní zápisy pro I/O.

## Otázky, na které si dát pozor

### Proč je v některých materiálech Hybrid 1D kolem `407×`?

Říkej raději relativní závěr: Hybrid 1D je slabší než Hybrid 2D, v reportu přibližně `1,6×`. Pokud se někdo doptá na přesné číslo, odkaž se na tabulku ve finálním reportu a zdůrazni, že pointa slidu je rozdíl 1D proti 2D kvůli halo objemu.

### Máš I/O Summary?

Nemáš samostatný slajd s I/O Summary. Řekni, že I/O závěr vychází z měřených časů a implementace HDF5 chunkování, ne z detailní propustnostní interpretace. Netvrdit konkrétní dosažené propustnosti.

### Proč ne neighbor collectives?

Explicitní P2P bylo jednodušší pro překrytí a kontrolu bufferů. Neighbor collectives jsou rozumný směr další práce, ale nebyly hlavním cílem.

### Proč ne vždy RMA?

RMA je citlivé na směr operace, layout dat a synchronizaci. P2P je jednodušší a v konečném měření vychází velmi dobře. Proto prezentace neříká, že RMA je obecně lepší.

## Krátká odpověď na závěr

Kdybych měl projekt shrnout jednou větou:

> Nejlepší výkon vyšel z kombinace 2D dekompozice, překrytí halo výměny s výpočtem a odstranění hlavních režijních problémů v RMA komunikaci.
