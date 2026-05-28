# Obhajoba - mluvený text

Cíl je držet se kolem 5:30 až 6:00. Úvod je krátký. Hlavní čas patří škálování, RMA optimalizaci a profilování.

## Slajd 1 - Titulka

**Čas:** 0:00-0:10

Dobrý den, budu prezentovat paralelní heat solver z PPP projektu. Zaměřím se hlavně na výsledky měření, na optimalizaci halo komunikace a na to, co potvrzují profilovací nástroje.

## Slajd 2 - Popis řešení

**Čas:** 0:10-0:32

Solver počítá šíření tepla ve 2D mřížce pomocí stencil výpočtu. Doména je rozdělená mezi MPI procesy jako 1D nebo 2D kartézská topologie.

Uvnitř každého procesu běží OpenMP výpočet lokální dlaždice. V každé iteraci se nejdřív spočítají okraje, spustí se výměna halo zón a během komunikace se počítá vnitřek dlaždice. Komunikace tedy není úplně mimo výpočet, část se překrývá.

## Slajd 3 - Silné škálování Hybrid 2D P2P

**Čas:** 0:32-1:00

Toto je nejsilnější naměřený výsledek. Hybridní 2D P2P varianta dosáhla pro doménu `4096^2` přibližně `666×` zrychlení na 256 jádrech.

Je to superlineární vůči 256 jádrům. Vysvětlení je cache efekt. Sekvenční běh pracuje s celou velkou doménou, zatímco paralelní běh má menší dlaždice a pracovní sada se lépe vejde do cache.

## Slajd 4 - Silné škálování MPI 2D P2P

**Čas:** 1:00-1:15

Čisté MPI 2D P2P škáluje stabilně a na `4096^2` dosahuje přibližně `354×` na 128 MPI rancích. Tenhle graf je dobrý jako kontrola, že 2D dekompozice funguje i bez OpenMP.

Hybridní varianta pak dokáže jít na vyšší počet jader, protože kombinuje menší počet MPI procesů s vlákny uvnitř procesu.

## Slajd 5 - Silné škálování Hybrid 1D P2P

**Čas:** 1:15-1:30

Hybridní 1D varianta na `4096^2` pořád škáluje a dosahuje přibližně `407×` na 256 jádrech, ale zaostává za 2D dekompozicí. V přímém srovnání vychází přibližně `1,6×` slabší než Hybrid 2D.

Důvod je objem halo dat. U 1D dekompozice jsou hrany delší, takže s rostoucím počtem procesů roste komunikační zátěž rychleji než u 2D.

## Slajd 6 - Slabé škálování

**Čas:** 1:30-1:55

Slabé škálování měří, co se děje, když s počtem procesů roste i velikost problému. Ideálně by čas jedné iterace zůstával konstantní.

MPI 2D drží efektivitu `82,2 %` na 256 jádrech. Hybridní 2D má `78,5 %` na 128 jádrech. Hybridní 1D klesá na `55,5 %`, což odpovídá tomu, že 1D posílá delší halo hranice.

## Slajd 7 - RMA optimalizace

**Čas:** 1:55-2:28

RMA optimalizace byla jedna z hlavních technických částí projektu. Původní varianta používala strided `MPI_Get`, derived datatypes a `MPI_Win_fence`.

Finální varianta používá `MPI_Put`, balí halo data do souvislého bufferu a synchronizuje jen sousedy pomocí PSCW. Důležité je, že to není jedna změna, ale tři oddělené příčiny výkonového problému.

## Slajd 8 - Izolované měření RMA

**Čas:** 2:28-3:05

Tabulka ukazuje rozklad RMA zrychlení. Největší faktor byl přechod z `MPI_Get` na `MPI_Put`, který dal `55-80×`.

Packing přidal `1,5-3×`, protože svislé halo hrany nejsou v paměti souvislé. Nahrazení globálního `MPI_Win_fence` za PSCW přidalo `1,15-1,74×`, protože synchronizace probíhá jen mezi sousedy.

Celkově se RMA halo exchange oproti naivní variantě zrychlil `133-268×`.

## Slajd 9 - Vampir Timeline

**Čas:** 3:05-3:22

Timeline ukazuje pravidelný vzor iterací. Vidíme lokální výpočet, MPI komunikaci a čekání na halo.

Výpočet dominuje, ale MPI části nejsou nulové. To odpovídá implementaci, kde se část komunikace překrývá s výpočtem vnitřku dlaždice, ale na konci iterace se pořád čeká na dokončení halo výměny.

## Slajd 10 - Vampir Function Summary

**Čas:** 3:22-3:38

Function Summary potvrzuje, že hlavní část času je ve výpočetním jádře, tedy v aktualizaci bodů.

MPI části jsou hlavně halo synchronizace a čekání. Je to dobrá kontrola, že aplikace netráví většinu času v režijním kódu.

## Slajd 11 - Vampir Process Summary

**Čas:** 3:38-3:54

Process Summary ukazuje vyvážení zátěže. Procesy mají podobný čas ve výpočtu i podobný charakter MPI částí.

To odpovídá implementaci. Dlaždice mají stejnou velikost a stencil má pevnou cenu na bod.

## Slajd 12 - Vampir komunikace

**Čas:** 3:54-4:12

Komunikační matice ukazuje hlavně komunikaci se sousedy v kartézské topologii.

Pro 2D výstup je maximum škály `210 KiB`. U 1D bylo odpovídající maximum `840 KiB`. To podporuje závěr, že 2D komunikuje s více sousedy, ale posílá kratší hrany.

## Slajd 13 - Cube topologie

**Čas:** 4:12-4:28

Cube pohled potvrzuje rovnoměrný objem halo komunikace. Rank 0 vyniká kvůli scatter a gather částem běhu.

Ostatní ranky mají téměř shodný objem halo komunikace, takže rozdíly nejsou způsobené špatným rozdělením domény.

## Slajd 14 - Zdůvodnění výsledků

**Čas:** 4:28-5:05

Výsledky stojí hlavně na třech faktorech.

První je dekompozice. Poměr objemu halo dat pro 1D vůči 2D je `sqrt(P) / 2`, takže při větším počtu procesů 2D varianta komunikuje výhodněji.

Druhý je cache efekt. U největší domény se paralelní dlaždice vejdou do cache lépe než sekvenční pracovní sada, což vysvětluje superlineární zrychlení.

Třetí je I/O. Oprava HDF5 chunkování pomohla, ale pro malé snapshoty `4-64 MB` může pořád dominovat režie Lustre koordinace.

## Slajd 15 - Možné směry další práce

**Čas:** 5:05-5:25

Další práci bych směřoval hlavně do dvou věcí, které přímo vycházejí z měření.

První je podmíněný packing, aby se nebalilo halo, které je už souvislé v paměti. Druhá je I/O batching, tedy méně časté nebo větší kolektivní zápisy, aby se lépe amortizovala režie HDF5 a Lustre.

Závěr je, že implementace škáluje, profilování odpovídá očekávání a největší technický přínos je v rozkladu RMA problému na měřitelné faktory.

## Zkrácení pod 5 minut

- Slajdy 4 a 5 říct jen jednou větou.
- Profilovací slajdy 9 až 13 brát jako důkazní materiál, ne jako samostatný výklad.
- U slajdu 15 říct jen jednu větu ke každému zlepšení.

## Co neříkat bez dotazu

- Nezacházet do detailního pořadí `MPI_Win_post`, `MPI_Win_start`, `MPI_Win_complete`, `MPI_Win_wait`.
- Nerozebírat všechny křivky ve scaling grafech.
- Netvrdit konkrétní dosažené propustnosti, pokud nejsou na slidu nebo v reportu.
- Neprodávat paralelní I/O jako vždy rychlejší.
