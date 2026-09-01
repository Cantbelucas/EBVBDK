# EBVB

Privat lytterum for gruppen. To faner — **Beats** og **Music** — hvor man
lægger et spor op med et cover og afspiller det i browseren. Ikke mapper,
ikke en filbrowser.

```
app.py                    hele backenden (Flask + SQLite)
templates/                base, login, forsiden
static/styles.css         al styling
static/app.js             faner, afspiller, upload-ark
static/theme.js           sætter lyst/mørkt før siden tegnes
data/                     ← databasen og filerne. Skal ikke i git.
  ebvb.db
  media/                  lydfilerne
  covers/                 artwork
  secret_key              session-nøglen
```

## Design

Ingen accentfarve. Papir og blæk, intet andet — det eneste farvede på
siden er det artwork I selv lægger op. Mørk tilstand er ikke en anden
palet, det er **negativet**: de samme to toner byttet om. Knappen øverst
til højre hedder derfor `Negativ` / `Positiv`.

Det spor der spiller, står **på hovedet** — cover-pladen roterer 180°.
Det er tilstandsmarkeringen, og det er et citat af jeres eget cover.
Slår man `prefers-reduced-motion` til, markeres det med en tyk ramme i
stedet, så tilstanden aldrig kun ligger i en bevægelse.

Skrifterne er Six Caps (mærket, fanerne, titler), skriveskriften
Mrs Saint Delafield til mottoet, og systemets mono til alt data.

## Kør lokalt

```bash
pip install -r requirements.txt
python app.py init
python app.py adduser lucas --admin
python app.py
```

Så ligger den på <http://localhost:8090>. Udviklingsserveren slår
`Secure`-flaget på session-cookien fra, ellers kan man ikke logge ind
over `http`. I drift bliver det stående.

## Brugere

Konti oprettes fra kommandolinjen. Der er ingen selvbetjening, og der er
ingen vej ind i appen uden en konto.

```bash
python app.py adduser fjolli      # spørger om kodeord, ekkoer det ikke
python app.py adduser cc
python app.py users               # hvem findes
python app.py passwd fjolli       # skift kodeord
```

I Docker:

```bash
docker compose exec ebvb python app.py adduser fjolli
```

Kodeordet skal være mindst 10 tegn og bliver hashet med werkzeugs
`generate_password_hash` (scrypt). Det står aldrig i klartekst nogen
steder — heller ikke i databasen.

**Admin** (`--admin`) kan slette alles spor. Alle andre kan kun slette
deres egne. Alle kan se og hente alt — det er en gruppe, ikke en
tjeneste.

## Hent en hel mappe ind

Til at flytte en eksisterende samling ind i appen — fx det der ligger i
Nextclouds mapper i dag.

```bash
python app.py import ~/gamle-beats beats lucas --proev
```

`--proev` skriver ikke noget, den viser bare hvad der ville ske. Uden
den bliver filerne kopieret ind (`--flyt` flytter dem i stedet).

Titlen kommer fra filnavnet, datoen fra filens tidsstempel, og et cover
bliver fundet hvis der ligger et billede med samme navn som lydfilen
eller en `cover.*` i samme mappe. Kører du den to gange, springer den
over det der allerede er inde.

## Deploy

Første gang — omlægningen fra Nextcloud til `ebvb.dk`, inklusive import
af det der ligger i Nextcloud i dag, og hvordan du ruller tilbage:
**[DEPLOY.md](DEPLOY.md)**.

Derefter er et deploy én kommando:

```bash
./deploy.sh lucas@dinserver
```

Containeren lytter kun på `127.0.0.1:8090`. Server-blokken ligger i
[`nginx.ebvb.conf.example`](nginx.ebvb.conf.example) og sætter
`client_max_body_size 512M` — samme grænse som `MAX_BYTES` i `app.py`.
**De to skal følges ad.** Er nginx' grænse lavere, afviser den filen med
en 413 før Flask overhovedet ser den. Og Cloudflare har sin egen grænse
oveni; se afsnittet om det i DEPLOY.md.

## Ressourcer

To gunicorn-workers bruger omkring 120 MB. Til sammenligning tager
Nextcloud 1–2 GB, og Minecraft skal senere have sine ~5 GB. Appen er
altså ikke det der presser de 8 GB.

Databasen er SQLite ligesom Nextclouds. Fint til en håndfuld brugere.
Den ville ikke holde til mange samtidige skrivninger, men det er ikke
det den skal.

## Backup

Alt der betyder noget ligger i `data/`:

```bash
tar czf ebvb-$(date +%F).tar.gz -C /opt/ebvb data
```

Sletter du `data/secret_key`, bliver alle logget ud. Sletter du
`ebvb.db`, mister du brugere og metadata — lydfilerne ligger stadig i
`data/media/`, bare uden titler.

## Fonte

Siden henter Six Caps og Mrs Saint Delafield fra Google Fonts. Det er de
eneste to kald ud af huset. Vil du have den helt lukket, som portfolioen:

1. Hent de to `.woff2`-filer fra
   <https://fonts.google.com> og læg dem i `static/fonts/`.
2. Erstat `<link>`-linjen i `templates/base.html` med `@font-face`-regler
   i toppen af `styles.css`.
3. Skift CSP'en i nginx-blokken til den strammere variant der står som
   kommentar i filen.

## Bemærkninger

- Lyd hentes med `Range`-forespørgsler, så man kan spole i en 40 MB wav
  uden at hente hele filen først.
- Siden virker uden JavaScript, den bliver bare kedeligere: begge lister
  vises på én gang, og sporene hentes i stedet for at blive afspillet.
- Mellemrumstasten er play/pause, når markøren ikke står i et felt.
- Formater der kan lægges op: wav, mp3, m4a, aac, flac, ogg, opus, aiff.
  Om de kan afspilles i browseren afhænger af browseren — wav og mp3 kan
  alle. Kan den ikke afspille filen, siger pladen det og tilbyder
  download i stedet.
