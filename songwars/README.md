# Songwars

Spillere joiner en lobby, får en genre og et beat, laver et nummer ovenpå
det i deres eget program, afleverer det, og alle stemmer 1–5 flammer.
Navnene kommer først frem på ranglisten.

Ingen konti. Man er et kaldenavn i en signeret cookie. Kun admin logger
ind, og kun for at styre genrer og beats.

```
app.py                      hele backenden (Flask + SQLite)
audio.py                    er filen faktisk mp3 eller wav? + længden
templates/
  base.html                 ramme, CSRF-token som <meta>
  home.html                 opret / join
  lobby.html                alle faser som skeletter - lobby.js viser den der er nu
  admin.html, admin_login.html
  error.html
static/
  styles.css                al styling
  common.js                 data-confirm, data-busy, kopiér-knap
  lobby.js                  polling af /state, nedtælling, upload, afstemning
  flame.svg                 flammen - bruges som CSS-maske
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
| **Lobby** | Spillere joiner med koden. Alle ser listen live. Ejeren kan ændre tid, holdstørrelse og genre, og fjerne spillere. | Ejeren starter |
| **Reveal** | Serveren trækker genren (eller bruger den valgte), et tilfældigt beat i den, og fordeler holdene tilfældigt. Varer `SONGWARS_REVEAL_SECONDS` (10 s). | Tiden |
| **Lav** | Nedtælling. Beatet kan afspilles og hentes. Ét nummer pr. hold; et nyt upload fra en på holdet erstatter det forrige. | Tiden — eller ejeren lukker før |
| **Afstemning** | Numrene i tilfældig rækkefølge som "Nummer 1, 2, 3 …". Alle giver 1–5 flammer til alle numre undtagen holdets eget. Stemmer kan ændres. | Slutter når alle har stemt — eller ejeren lukker |
| **Resultat** | Rangliste efter gennemsnit af flammer. Navnene afsløres. Lige gennemsnit giver samme plads. | — |

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
stemme på.

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
| `SONGWARS_REVEAL_SECONDS` | `10` | Hvor længe genren vises før tiden starter. |
| `SONGWARS_GRACE_SECONDS` | `10` | Se ovenfor. |

Mangler en af de to påkrævede, eller er de for korte, nægter appen at
starte og siger hvorfor.

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
  inline-scripts, ingen `style=""`, ingen `on…=`, intet udefra — heller
  ikke fonte. JS sætter kun styling via CSSOM (`el.style.setProperty`),
  hvilket CSP tillader.
- Al tekst fra spillere sættes med `textContent` — aldrig som HTML.
- Lobbykoden er 5 tegn fra 31 tegn uden 0/O og 1/I/L: 28 millioner koder.
- Containeren kører som uid 10001 med skrivebeskyttet filsystem, uden
  capabilities og med `no-new-privileges`. Kun `/data` er skrivbar.

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
