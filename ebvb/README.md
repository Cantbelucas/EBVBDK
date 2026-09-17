# EBVB

Privat lytterum for gruppen. To faner — **Beats** og **Music** — hvor man
lægger et spor op med et cover og afspiller det i browseren. Ikke mapper,
ikke en filbrowser.

```
app.py                    hele backenden (Flask + SQLite)
templates/                base, login, forsiden, profil
  _bits.html              makroer: en raekke, et ansigt
  _plate.html             detaljepanelet
  _deck.html              afspilningsbaren
  mappe.html              læg en hel mappe op
  gennemse.html           ret titel, BPM og toneart på mange numre
static/styles.css         al styling
static/app.js             faner, afspiller, upload-ark
static/mappe.js           mappe-upload og gennemse-tabellen
static/theme.js           sætter lyst/mørkt før siden tegnes
data/                     ← databasen og filerne. Skal ikke i git.
  ebvb.db
  media/                  lydfilerne
  covers/                 artwork
  avatars/                profilbilleder
  tmp/                    halve filer fra en mappe-upload i gang
  secret_key              session-nøglen
```

## Design

Glas og runde kanter over en rød glød. Grundtonen er papir og blæk, og
accenten (`#c2413c` lys, `#e2554f` mørk) går igen ét sted ad gangen: på
det spor der spiller, på afspilningsbaren og på play-knappen. Mørk
tilstand er den samme opbygning i den mørke tone — knappen øverst til
højre hedder `Negativ` / `Positiv`.

Afspilningen bor i baren i bunden. Panelet i siden er kun detaljer om
sporet: cover, titel, BPM, toneart, note, hvem der lagde op, og længde.
Over 900px står listen til venstre og panelet til højre.

Skrifterne er Six Caps (mærket, fanerne, titler), skriveskriften
Mrs Saint Delafield til mottoet, og systemets mono til alt data.

### Bevægelse

Appen er udstyr, ikke en hjemmeside. Der er to hastigheder: `--t-press`
på 110ms så et tryk svarer med det samme, og `--t-settle` på 420ms med
kraftig decelleration når noget falder til ro. Ingen bounce — der er
ingenting i en afspiller der hopper.

Signaturen er at **coveret lander**: vælger du et spor, kommer flisen ind
lidt højere oppe, en anelse drejet, og sætter sig — som et omslag der
lægges på pladen. Det er det ene sted der bruges krudt. Resten er
tryk-feedback og en rød kant der vokser ud på det spillende spor.

Alt respekterer `prefers-reduced-motion`: hver animations sluttilstand er
neutral, så en varighed på nær nul lander det rigtige sted uden bevægelse.

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

## Profiler

Klik på et navn i en liste, eller på dit eget navn i baren øverst, og du
lander på `/profil/<navn>`: alt hvad den person har lagt op, på tværs af
begge sektioner, med tæller og samlet størrelse.

Din egen profil har en **Rediger profil**-knap. Der kan du lægge et
profilbillede op og skrive et par linjer om dig selv. Ingen andre kan
redigere din — heller ikke admin. Det er en gruppe, ikke en tjeneste med
moderation.

Profilbilledet gemmes i `data/avatars/` under et uuid-navn, som samtidig
er cache-buster i URL'en. Skifter du billede, bliver det gamle slettet.

**Databasen migrerer sig selv.** `users` fik to nye kolonner
(`avatar_file`, `bio`), og `CREATE TABLE IF NOT EXISTS` rører ikke en
tabel der findes i forvejen. `migrate()` tilføjer dem ved opstart hvis de
mangler, så et deploy oven på en kørende database ikke går i stykker.
Kommandoen kan køres igen uden at duplikere noget.

## Læg en mappe op

I "Læg op"-arket er der et link: **Læg mappen op**. Det er til når man
har en mappe med numre på computeren og ikke vil lægge dem op én ad
gangen. Kun desktop — det bruger browserens mappe-vælger.

1. **Vælg en mappe.** Alle `.mp3` og `.wav` i den findes, også i
   undermapper. Alt andet ignoreres, også macOS' `._`-filer.
2. **Listen.** Før noget sendes, ser du hvad der blev fundet, og hvad der
   blev læst ud af filnavnene. Fjern fluebenet ved det der ikke skal med.
3. **Dubletter.** Findes et nummer med samme filnavn allerede i
   sektionen, bliver du spurgt om de skal overskrives eller springes
   over, og "Læg op" er låst til du har svaret. Valget kan ændres pr.
   nummer bagefter. Et nummer en anden har lagt op kan ikke overskrives,
   kun springes over eller lægges op ved siden af. Admin kan overskrive.
4. **Upload.** Filerne sendes én ad gangen med fremdrift pr. fil og
   samlet. Luk ikke fanen. **Stop** afbryder, og **Prøv igen** fortsætter
   hvor den slap — det der allerede er nået frem, sendes ikke igen.
5. **Gennemse.** Bagefter kommer du til en tabel over alt fra uploaden,
   hvor titel, BPM og toneart kan rettes direkte og gemmes på én gang.

Uploaderens brugernavn er artist på alle numrene — det er det navn der
står som "Lagt op af". Der er ikke et separat artist-felt.

**Overskriv** skifter lyden ud og beholder sporets cover, note, dato og
plads i listen. Titel, BPM og toneart tages fra det nye filnavn, men kan
det ikke læse fx tonearten, beholdes den gamle — så en toneart du har
rettet i hånden, ryger ikke ved en ny version.

### Hvad der læses ud af filnavnet

```
Midnight Drive 95BPM Fm.wav              Midnight Drive · 95 · Fm
midnight_drive_95_F#m.wav                midnight drive · 95 · F#m
Midnight Drive - 95 bpm - F minor.mp3    Midnight Drive · 95 · Fm
Deep House 124 A minor (Final).wav       Deep House (Final) · 124 · Am
BPM 140 - Keys - Cm.wav                  Keys · 140 · Cm
lucas - Night Call 86 Ebmin.wav          Night Call · 86 · Ebm
```

- **BPM** med eller uden `BPM` efter (eller før): `95`, `95BPM`,
  `95 bpm`, `bpm 95`, `95.5bpm`. Et tal uden `BPM` skal ligge mellem 50
  og 220 for at tælle.
- **Toneart**: `Fm`, `F minor`, `F Minor`, `Fmin`, `F#m`, `Bbm`,
  `Eb maj`, og også `F mol`, `F moll` og `F dur`. Det gemmes kort:
  `Fm`, `F#m`, `Bb`.
- **Titlen** er det der er tilbage. Står uploaderens navn forrest
  (`lucas - …`), fjernes det.

Parseren gætter hellere for lidt end forkert. En toneart der også kan
være et ord — `Am` i "I Am Legend", et enkelt `A` — tæller kun hvis den
står lige ved siden af BPM'en eller sidst i navnet.

**Filer der ikke kan læses, bliver ikke afvist.** De lægges op med
filnavnet som titel og tomme felter og bliver markeret. Delvist læste
(fx BPM men ingen toneart) markeres også. Du finder dem igen på din
profil — **N numre mangler at blive gennemset** fører til `/gennemse` —
og de forsvinder derfra, når du har gemt tabellen.

Vil du se hvorfor et filnavn blev læst som det blev:

```bash
python app.py filnavn "Midnight Drive 95BPM Fm.wav" "I Am Legend.wav"
```

### Store filer

Hver fil deles i bidder på 32 MB (`CHUNK_BYTES`). Det gør at en 300 MB
wav kommer igennem Cloudflares grænse på 100 MB pr. forespørgsel, og at
`client_max_body_size` i nginx **ikke** skal hæves. Grænsen pr. fil er
stadig `MAX_BYTES` (512 MB).

Bidderne skrives til `data/tmp/` og flyttes ind i `data/media/` når den
sidste er nået frem. En upload der aldrig bliver gjort færdig, ryddes
efter et døgn.

**CSP'en skal have `connect-src 'self'`**, ellers blokerer browseren
uploaden. Se punkt 3 øverst i [DEPLOY.md](DEPLOY.md).

### Databasen

`tracks` har fået to kolonner, som `migrate()` tilføjer ved opstart:

- `batch_id` — hvilken mappe-upload sporet kom med. Tom for enkeltfiler.
- `parsed` — hvad filnavnet gav: `fuld`, `delvis`, `ingen`, eller
  `rettet` når et delvist eller ulæst nummer er gennemset. Tom for
  enkeltfiler.

## Hent en hel mappe ind fra serveren

Til at flytte en eksisterende samling ind i appen — fx det der ligger i
Nextclouds mapper i dag. Filerne skal ligge på serveren; ligger de på din
egen computer, så brug "Læg en mappe op" ovenfor.

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
