# Songwars

Spillere joiner en lobby, drejer genrehjulet på skift — alle får hver sin
genre og et beat i den — laver et nummer ovenpå det i deres eget program,
afleverer det, og alle stemmer 1–5 flammer.
Navnene kommer først frem på ranglisten.

Ingen konti. Man er et kaldenavn i en signeret cookie. Kun admin logger
ind, og kun for at styre genrer og beats.

```
app.py                      hele backenden (Flask + SQLite)
audio.py                    er filen faktisk mp3 eller wav? + længden
youtube.py                  hent et beat fra et YouTube-link (yt-dlp + ffmpeg + deno)
templates/
  base.html                 ramme, CSRF-token som <meta>
  home.html                 opret / join
  lobby.html                alle faser som skeletter - lobby.js viser den der er nu
  admin.html, admin_login.html
  error.html
static/
  styles.css                al styling - designsystemet staar som variabler oeverst
  common.js                 data-confirm, data-busy, kopiér-knap, baggrundsscenen
  lobby.js                  polling af /state, nedtælling, upload, afstemning
  admin.js                  status på hentninger fra YouTube
  flame.svg                 flammen - bruges som CSS-maske (wordmark, stemmer)
  fonts/                    Poppins (titler) og Inter (tekst) som woff2, selvhostet (OFL)
Dockerfile, docker-compose.yml, .env.example
nginx.songwars.conf.example
data/                       ← databasen og filerne. Ikke i git.
  songwars.db
  beats/                    admins beats
  submissions/              afleveringer (slettes efter 7 dage)
  tmp/                      uploads mens de bliver tjekket
```

Deploy: **[DEPLOY.md](DEPLOY.md)**.

## Kør lokalt

```bash
cd songwars
pip install -r requirements.txt
cp .env.example .env
```

Udfyld `SONGWARS_SECRET_KEY` (mindst 32 tegn) og `SONGWARS_ADMIN_PASSWORD`
(mindst 12 tegn) i `.env`. En nøgle laves med:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Start så udviklingsserveren:

```bash
python app.py
```

Den ligger på <http://localhost:8092>. Appen læser selv `.env` når den køres
sådan, og udviklingsserveren slår `Secure`-flaget på cookien fra, så man
kan være logget ind over `http`. Gå til `/admin`, lav en genre og læg et
beat op — ellers kan ingen runde starte.

For at prøve med flere spillere på én maskine: brug et privat vindue eller
en anden browser pr. spiller. Hver browser er én spiller.

## Sådan går en runde

| Fase | Hvad sker der | Hvem bestemmer |
|---|---|---|
| **Lobby** | Spillere joiner med koden. Alle ser listen live. Ejeren kan ændre tid og holdstørrelse, og fjerne spillere. | Ejeren starter |
| **Genrehjulet** | Holdene fordeles tilfældigt, og der trækkes en tilfældig rækkefølge. Holdene drejer hjulet på skift (solo: hver spiller). Serveren trækker en genre ingen andre har fået, og et tilfældigt beat i den — hjulet viser bare, hvor den lander, ens hos alle. Drejer et hold ikke inden for `SONGWARS_SPIN_SECONDS` (20 s), gør serveren det. | Holdene — eller tiden |
| **Reveal** | Hvert hold ser sin genre og sit beat. Varer `SONGWARS_REVEAL_SECONDS` (10 s). | Tiden |
| **Lav** | Nedtælling. Holdets eget beat kan afspilles og hentes. Ét nummer pr. hold; et nyt upload fra en på holdet erstatter det forrige. | Tiden — eller ejeren lukker før |
| **Afstemning** | Numrene i tilfældig rækkefølge som "Nummer 1, 2, 3 …". Alle giver 1–5 flammer til alle numre undtagen holdets eget. Stemmer kan ændres. | Slutter når alle har stemt — eller ejeren lukker |
| **Resultat** | Rangliste efter gennemsnit af flammer. Navnene og genrerne afsløres. Lige gennemsnit giver samme plads. | — |

**Tiden håndhæves på serveren.** Fasen gemmes som status plus
tidsstempler, og hver forespørgsel regner ud hvor vi er (`advance()` i
`app.py`). Nedtællingen i browseren er kun en visning — en upload efter
fristen afvises af serveren, uanset hvad uret i browseren viser.
Fristen tjekkes både når uploaden ankommer, og igen inde i den
transaktion der gemmer den.

Der er en lille **graceperiode** (`SONGWARS_GRACE_SECONDS`, 10 s): en fil på
50 MB tager tid at sende, og en upload der lander lige efter nul skal ikke
gå tabt. Sæt den til `0` for at være helt stram.

**Hold:** antallet af hold er `spillere ÷ holdstørrelse` rundet ned, og de
der er tilovers fordeles ud. Intet hold bliver mindre end den valgte
størrelse, og ingen to hold er mere end én fra hinanden (7 spillere i
hold på 2 → 3, 2, 2). Der skal være mindst to hold, ellers er der ingen at
stemme på. Og der skal være mindst lige så mange genrer med beats, som der
bliver hold — alle skal have hver sin.

**Ejeren** er den der oprettede lobbyen. Forlader ejeren lobbyen før
start, går ejerskabet videre til den der har været længst med. Under en
runde kan man ikke forlade den — holdet og stemmerne skal stå ved magt.

## Konfiguration

Alt kommer fra miljøet (`.env`). Ingen hemmeligheder i koden.

| Variabel | Standard | |
|---|---|---|
| `SONGWARS_SECRET_KEY` | — | **Påkrævet.** Signerer cookien. Mindst 32 tegn. |
| `SONGWARS_ADMIN_PASSWORD` | — | **Påkrævet.** Mindst 12 tegn. |
| `SONGWARS_COOKIE_SECURE` | `1` | Cookien sendes kun over https. `0` kun uden https. |
| `SONGWARS_IP_HEADER` | `CF-Connecting-IP` | Hvor brugerens IP står, til rate limiting. |
| `SONGWARS_MAX_UPLOAD_MB` | `50` | Pr. aflevering. |
| `SONGWARS_MAX_BEAT_MB` | `95` | Pr. beat (kun admin). Under Cloudflares 100 MB. |
| `SONGWARS_LOBBY_HOURS` | `24` | Derefter lukker lobbyen. |
| `SONGWARS_KEEP_DAYS` | `7` | Derefter slettes alt om lobbyen. |
| `SONGWARS_YT_MAX_MINUTES` | `15` | Beats fra YouTube må højst være så lange. |
| `SONGWARS_SPIN_SECONDS` | `20` | Hvor længe et hold har til at dreje hjulet, før serveren gør det. |
| `SONGWARS_REVEAL_SECONDS` | `10` | Hvor længe genren vises før tiden starter. |
| `SONGWARS_GRACE_SECONDS` | `10` | Se ovenfor. |

Mangler en af de to påkrævede, eller er de for korte, nægter appen at
starte og siger hvorfor.

## Beats fra YouTube

På `/admin` kan man sætte et link ind, skrive en titel og vælge genre, så
henter serveren selv lyden — samme opskrift som "YouTube → WAV"-scriptet:
yt-dlp henter kun den ene video (`--no-playlist`), ffmpeg laver lyden om,
og deno er den JavaScript-motor, yt-dlp skal bruge til YouTube.

- **mp3, ikke wav.** Lyden på YouTube er allerede komprimeret, så en wav
  bliver ikke bedre — bare fire gange større at hente for spillerne.
  Den gemmes som mp3 i bedste kvalitet (VBR 0).
- **Tom titel** = navnet fra videoens titel, uden "(FREE)", "[free for
  profit]", "Type Beat", årstal og lignende. Står navnet i anførselstegn
  (`"Global"`), bruges det.
- **I baggrunden.** En hentning tager typisk 10–30 sekunder, og nginx og
  Cloudflare afbryder lange forespørgsler. Derfor starter formularen bare
  en hentning (tabellen `imports`) og kommer tilbage med det samme; siden
  viser status og genindlæser sig selv, når beatet er klar. Højst to ad
  gangen. En hentning der har "kørt" i et kvarter, er død (serveren blev
  genstartet) og vises som afbrudt.
- **Kun YouTube.** Linket laves om til `youtube.com/watch?v=<id>`, og
  yt-dlp må kun bruge sin YouTube-del (`allowed_extractors`), så feltet kan
  ikke bruges til at få serveren til at hente fra andre adresser.
  Playlister afvises.
- **Samme video to gange** afvises: beatet husker sin kilde
  (`beats.source = youtube:<id>`).
- **Filen tjekkes** af `audio.py` som enhver anden upload, før den lægges
  på plads.
- **Hvis YouTube siger nej** ("bot-tjek", 403): se DEPLOY.md. Upload med
  fil virker altid som før.

## Sikkerhed

- **Filerne tjekkes på indholdet**, ikke endelsen (`audio.py`). En wav skal
  have en rigtig RIFF/WAVE-header med PCM- eller float-lyd; en mp3 skal have
  tre rigtige MPEG-rammer i træk. En m4a, ogg, flac eller et billede
  omdøbt til `.mp3` afvises, og det samme gør en fil der er afbrudt
  halvvejs (headeren lover mere lyd end der er). Lyd under 1 sekund eller
  over en time afvises.
- **Brugerens filnavn bruges aldrig** — hverken på disken eller i
  databasen. Filerne hedder `<32 tilfældige hex-tegn>.mp3|wav`. Under
  afstemningen er der intet i svaret der afslører hvem der lavede et
  nummer: hverken filnavn, rækkefølge eller id.
- **Uploads streames til `data/tmp/`**, tjekkes, og flyttes først derefter
  på plads. En afvist fil efterlader intet.
- **Rate limiting** i SQLite, så begge gunicorn-workers tæller sammen:
  join 12 pr. 5 min. pr. IP (forsøg der fejler, tæller med — ellers kan
  man gætte koder), oprettelse 6 pr. time, upload 12 pr. 10 min. pr.
  spiller og 30 pr. IP, admin-login 6 pr. kvarter.
- **CSRF-token** på alle POST: et skjult felt i formularerne, en
  `X-CSRF-Token`-header fra JS. Cookien er desuden `SameSite=Lax`.
- **CSP**: appen sender selv samme stramme politik som nginx. Ingen
  inline-scripts, ingen `style=""`, ingen `on…=`, intet udefra. Fontene
  ligger i `static/fonts/`, så politikken har `font-src 'self'`. JS sætter
  kun styling via CSSOM (`el.style.setProperty`), hvilket CSP tillader.
- Al tekst fra spillere sættes med `textContent` — aldrig som HTML.
- Lobbykoden er 5 tegn fra 31 tegn uden 0/O og 1/I/L: 28 millioner koder.
- Containeren kører som uid 10001 med skrivebeskyttet filsystem, uden
  capabilities og med `no-new-privileges`. Kun `/data` er skrivbar.

## Design

En spilmenu fra omkring 2002 lagt over en tåget by en efterårsmorgen.
Variablerne står øverst i `static/styles.css`.

- **Wordmarket** "songwars" og **underteksterne** er de eneste højlydte
  ting: **Poppins** i halvfed, gul (`--accent`), små bogstaver, en tynd
  mørk kant (`--sub-shadow`) og en svag skråning (`--slant`). Genren i
  reveal har samme stil, bare i orange.
- **Alt andet er roligt og tyndt**: overskrifter i **Inter** med en tynd
  linje under, labels i små store bogstaver med luft imellem, og uret i
  tynd Inter.
- **Panelerne** er halvgennemsigtige med en svag lodret gradient og en
  dobbelt kant (tynd lys udenom, mørk indenfor), så byen anes bagved.
  Hvert panel har en tynd farvet linje øverst. Felter, lobbykoden og
  numrene ligger nedsænket i panelerne.
- **Knapperne** er glansede, med den skarpe lyskant midt på, som dengang.
  De lyser svagt op ved hover og trykkes en pixel ned. Deaktiverede knapper
  er flade og matte, og årsagen står lige ved siden af.
- **Bevægelsen er stille**: ting kommer frem af tågen (de toner frem fra
  uskarpe til skarpe) i stedet for at hoppe. Kun wordmarket på forsiden
  popper og bølger. Genren dukker langsomt op af tågen, uret gløder og
  blinker hurtigere til sidst, flammerne tænder og gløder, og vinderen får
  en gul kant med en svag glorie.
- **Genrehjulet** er skærmens fokus i den fase: spillerlisten træder til
  side, byen bliver mørkere, og hjulet står stort under et spotlys.
  Skiverne er næsten sorte med en tynd gul kontur og en svag glød mod
  kanten, og kun skiven under viseren lyser gult — gult er en accent, ikke
  et mønster. Navnene står ens: samme størrelse, kursiv, med luft imellem,
  og lange navne forkortes ("JERSEY C."). Uden om sidder en tynd mørk ring
  med streger som på et speedometer; de fyldes op fra toppen med farten,
  mens hjulet drejer, og blinker, når det lander. I midten et sekskantet
  glasnav med en tynd ring, der pulserer, og en glasrefleks over den ene
  halvdel af hjulet. En gul flap i toppen slår mod hver skillelinje. Det trækkes lidt tilbage, slippes,
  ruller en smule forbi og rykker tilbage på plads. Navnene bliver uskarpe
  (motion blur), når det drejer hurtigt. Ved landingen: en hvid blitz fra
  viseren, et lille ryk (ikke ved reduceret bevægelse), skiven der vinder,
  lyser op, og resten dæmpes et par sekunder. Svage scanlines over hjulet
  og en tung vignette i hjørnerne. Taget genrer bliver matte.
- **Lyd** laves med WebAudio, uden lydfiler: et kort klik for hver pind,
  to blip når hjulet slippes, en dyb tone med en lys glimmer når det
  lander, og to blip når det bliver ens tur. Browsere tillader først lyd
  efter et klik på siden. "Lyd: til/fra" ved knappen, og valget huskes i
  browseren.
- **Hjulets vinkel er en funktion af serverens ur** (`frame()` i
  `lobby.js`): hvert spin starter, hvor det forrige landede, og slutvinklen
  regnes ud fra genren og tidspunktet. Så står hjulet samme sted hos alle —
  også hos en, der åbner siden midt i et spin. Det drejes med
  `requestAnimationFrame` og drejer også ved reduceret bevægelse, fordi det
  er selve spillet; pynten omkring respekterer det stadig.
- **Baggrunden** er den tågede by: tågen driver, lagene flytter sig lidt
  med musen (parallax, kun med mus), støv svæver op, og blade og krager
  kommer og går. Et mørkt slør med svage scanlines ligger over det hele.
- **Den gule tekst skrives ud** tegn for tegn som på en skrivemaskine:
  underteksterne nederst ("din genre er … trap.", "ét minut tilbage."),
  "Din genre er" og genren i reveal, "Du fik …" under hjulet og vinderens
  navn i resultatet. Det kører også ved reduceret bevægelse — intet
  flytter sig, bogstaverne kommer bare frem.
- **Reduceret bevægelse**: siden står stille, men stilen er den samme.
  Støv og parallax laves slet ikke. Undtagelserne er genrehjulet (selve
  spillet) og bladene og kragerne i baggrunden, der hører til stemningen —
  de driver roligt og flytter ikke noget, man skal læse eller trykke på.
- **Fonte** er selvhostede i `static/fonts/` (Poppins og Inter, begge
  SIL OFL 1.1 — licenserne ligger ved siden af).

## Oprydning

Ingen cron nødvendig. Højst hvert tiende minut rydder den første
forespørgsel der når til det, op:

- **Efter 24 timer** lukker en lobby. Var afstemningen i gang, bliver
  resultatet regnet ud med de stemmer der er; ellers lukker den bare. Et
  resultat kan stadig ses af dem der var med.
- **Efter 7 dage** slettes lobbyen helt: spillere, stemmer og
  afleveringernes filer. Beats bliver liggende.
- Filer i `data/submissions/` og `data/tmp/` som ingen række peger på
  (en upload der døde halvvejs), slettes efter et døgn.

Vil du køre den med det samme:

```bash
docker compose exec songwars python app.py ryd
```

## Bemærkninger

- Klienten poller `/state` hvert 2,5 sekund, hvert sekund når et ur er ved
  at ramme nul, og hvert 15. sekund når fanen ikke er i forgrunden. Ingen
  WebSockets.
- Afspillerne i afstemningen bygges én gang. En poll tegner kun stemmerne
  om, så et nummer der spiller, bliver ved med at spille.
- Starter man et nummer, stopper det der spillede.
- Et beat der bruges i en runde lige nu, kan ikke slettes. En genre med
  beats kan ikke slettes.
- En lobby kan ikke startes, hvis den udløber før runden kan nå at blive
  færdig — så hellere en ny lobby.
- Én runde pr. lobby. Vil man spille igen, laver man en ny og sender
  koden rundt.
