# Deploy

Songwars kører som sin egen container ved siden af EBVB, på samme server
og bag samme nginx — men helt adskilt: egen container, egen `data/`, egen
database, egen `.env`.

```
Bruger → Cloudflare → nginx-container (80/443)
                          ├─ ebvb.dk           → ebvb:8090
                          ├─ cv.ebvb.dk        → filer
                          └─ songwars.ebvb.dk  → songwars:8092   (over stack_web)
```

Kommandoerne er skrevet til at blive indsat i **PowerShell** på din
Windows-maskine, ligesom i EBVB's DEPLOY.md. Alt inde i anførselstegnene
efter `ssh` køres af bash på serveren. `dinserver` skal have dit rigtige
navn.

**Antagelser — ret dem hvis de ikke passer:**

- Subdomænet er `songwars.ebvb.dk`.
- Repoet er klonet i `/opt/ebvb-src` (som for EBVB).
- Wildcard-certifikatet for `*.ebvb.dk` er Cloudflare Origin-certet i
  `/etc/ssl/cloudflare`, med de samme filnavne som `ebvb.dk` bruger.
- nginx kører i containeren `nginx`, med `~/stack/nginx/conf.d/` mountet
  ind, og er på Docker-netværket `stack_web`.

---

## 1. DNS

I Cloudflare: en `CNAME` med navnet `songwars`, der peger på `ebvb.dk`,
med **proxy slået til** (orange sky). Så går trafikken samme vej som
resten, og certifikatet dækker den.

Brug **Full (strict)** som SSL-tilstand, ligesom for `ebvb.dk`. Dækker
Origin-certet ikke `*.ebvb.dk`, svarer Cloudflare med fejl 526 — så skal
der laves et nyt Origin-cert med begge navne.

## 2. Koden

```bash
ssh lucas@dinserver "cd /opt/ebvb-src && sudo git pull && sudo ln -sfn /opt/ebvb-src/songwars /opt/songwars && ls /opt/songwars/"
```

`/opt/songwars` er et link ind i repoet, præcis som `/opt/ebvb`. Et
`git pull` opdaterer begge apps; hver bliver først genstartet, når du
bygger den.

## 3. `.env`

Lav en hemmelig nøgle på serveren:

```bash
ssh lucas@dinserver "python3 -c 'import secrets; print(secrets.token_hex(32))'"
```

Opret `.env` og sæt den, så kun du kan læse den:

```bash
ssh -t lucas@dinserver "cd /opt/songwars && sudo cp -n .env.example .env && sudo chown lucas: .env && chmod 600 .env && nano .env"
```

Udfyld:

- `SONGWARS_SECRET_KEY` — nøglen fra før.
- `SONGWARS_ADMIN_PASSWORD` — mindst 12 tegn. Brug en password-manager.

Resten kan blive som det står. `.env` er i `.gitignore` og kommer ikke med
ind i imaget (`.dockerignore`) — compose giver variablerne til containeren
ved start.

## 4. Datamappen

**Det her er den fælde der bider.** Containeren kører som uid 10001, og
`./data` er en bind-mount fra værten. Så er det værtens mappe der
bestemmer, ikke imaget — ejes den af root, kan appen ikke skrive.

```bash
ssh lucas@dinserver "sudo mkdir -p /opt/songwars/data && sudo chown -R 10001:10001 /opt/songwars/data && ls -ln /opt/songwars | grep data"
```

Linjen skal vise `10001 10001`. Glemmer du det, starter containeren ikke,
og loggen siger præcis det her:

```
Songwars kan ikke skrive i /data (...).
Containeren koerer som uid 10001. Paa vaerten:
    sudo chown -R 10001:10001 data
```

## 5. Byg og start

```bash
ssh lucas@dinserver "cd /opt/songwars && docker compose up -d --build && sleep 3 && curl -s http://127.0.0.1:8092/healthz"
```

Den skal svare `ok`. Gør den ikke:

```bash
ssh lucas@dinserver "cd /opt/songwars && docker compose logs --tail 40"
```

Netværket `stack_web` skal findes i forvejen (det gør det, nginx og EBVB
er på det). Siger compose `network stack_web declared as external, but
could not be found`, er det fordi det hedder noget andet hos dig — se
`docker network ls`, og ret navnet nederst i `docker-compose.yml`.

## 6. nginx

Tjek først hvad certifikatfilerne hedder:

```bash
ssh lucas@dinserver "ls /etc/ssl/cloudflare/"
```

Kopiér server-blokken ind, ret filnavnene i den hvis de er anderledes, og
genindlæs:

```bash
ssh lucas@dinserver "cp /opt/songwars/nginx.songwars.conf.example ~/stack/nginx/conf.d/songwars.conf && docker exec nginx nginx -t && docker exec nginx nginx -s reload"
```

To ting i blokken der er værd at vide:

- **CSP'en har `style-src 'self'` og `font-src 'self'`.** Din politik står
  der ellers som den er. `font-src` skal med, fordi skrifterne er
  selvhostede i `static/fonts/` — uden den blokerer browseren dem, og siden
  falder tilbage til systemets skrift.
  Uden `style-src` falder stylesheets tilbage til `default-src 'none'` og
  bliver blokeret — siden ville stå helt uden styling. `form-action`,
  `base-uri` og `frame-ancestors` falder ikke tilbage til `default-src` og
  er derfor skrevet med.
- **`client_max_body_size 100m`** — afleveringer er højst 50 MB og beats
  højst 95 MB. Cloudflare afviser alt over 100 MB, så højere giver ingen
  mening. Står den på nginx' standard (1 MB), afvises hver eneste
  aflevering med 413.

Blokken bruger `resolver 127.0.0.11` og en variabel i `proxy_pass`, så nginx
ikke holder fast i containerens gamle IP efter et deploy (samme fiks som
for EBVB).

## 7. Prøv den

1. Åbn <https://songwars.ebvb.dk/admin>, log ind, lav en genre og læg et
   beat op.
2. Opret en lobby på telefonen, join fra computeren med koden.
3. Med holdstørrelse 1 kan to spillere starte en runde.

Ser siden ud uden styling, mangler `style-src` i CSP'en (punkt 6). Står
den i en anden skrift end Inter, mangler `font-src`. Kan
man ikke logge ind og ryger tilbage til forsiden hver gang, kører siden
over http et sted — cookien er `Secure`.

## Opdatering: selvhostede fonte

Skrifterne — Poppins til titlerne og Inter til teksten — ligger i
`static/fonts/` og kræver `font-src 'self'` i CSP'en. Appen sender den
selv, og den står i `nginx.songwars.conf.example`. **Den kørende `songwars.conf` på serveren har den ikke**, hvis
den blev kopieret før — ret linjen så den svarer til
`nginx.songwars.conf.example`, og genindlæs:

```bash
ssh lucas@dinserver "grep -c font-src ~/stack/nginx/conf.d/songwars.conf"
```

Svarer den `0`, mangler den. Ret den i hånden, og:

```bash
ssh lucas@dinserver "docker exec nginx nginx -t && docker exec nginx nginx -s reload"
```

Siden virker uden — men titlerne mister undertekst-skriften og står i
systemets skrift i stedet for Poppins, og så forsvinder meget af
udseendet.

## Deploy af opdateringer

Tilføj en linje til `~/deploy.sh` på serveren, efter EBVB:

```bash
cd /opt/songwars && docker compose up -d --build
```

Eller kør den alene:

```bash
ssh lucas@dinserver "cd /opt/ebvb-src && sudo git pull && cd /opt/songwars && docker compose up -d --build"
```

Databasen og filerne ligger i `data/`, som hverken git eller imaget rører.

## Logs, oprydning og backup

```bash
ssh lucas@dinserver "cd /opt/songwars && docker compose logs --tail 80"
```

Oprydningen kører af sig selv (se README.md). Vil du køre den med det samme:

```bash
ssh lucas@dinserver "cd /opt/songwars && docker compose exec songwars python app.py ryd"
```

Alt der betyder noget ligger i `data/`. Afleveringerne er væk efter 7 dage
alligevel; det der er værd at tage backup af, er beats og databasen.

Databasen kører i WAL-tilstand, så de nyeste skrivninger kan ligge i
`songwars.db-wal` ved siden af. En `tar` af `songwars.db` alene kan derfor
mangle dem. Lav først en konsistent kopi inde i containeren, og pak så
kopien og beatsene:

```bash
ssh lucas@dinserver "cd /opt/songwars && docker compose exec songwars python -c \"import sqlite3; sqlite3.connect('/data/songwars.db').backup(sqlite3.connect('/data/backup.db'))\" && sudo tar czf ~/songwars-backup.tar.gz -C /opt/songwars data/backup.db data/beats && sudo rm data/backup.db"
```

## Fjern den igen

```bash
ssh lucas@dinserver "rm ~/stack/nginx/conf.d/songwars.conf && docker exec nginx nginx -s reload && cd /opt/songwars && docker compose down"
```

Det tager siden ned og stopper containeren. `data/` bliver liggende, til
du selv sletter den. EBVB mærker ingenting af nogen af delene.

## Beats fra YouTube

Imaget har yt-dlp, ffmpeg og deno med, så admin kan hente beats fra et
YouTube-link. Det gør imaget ca. 250 MB større. Der skal ikke sættes noget
op; deno og yt-dlp gemmer deres cache i `data/cache/`.

**YouTube ændrer sig hele tiden**, og en gammel yt-dlp holder op med at
virke — typisk med "HTTP Error 403". yt-dlp er derfor ikke låst fast i
`requirements.txt`, og et nyt build henter den nyeste:

```bash
ssh lucas@dinserver "cd /opt/songwars && docker compose build --pull --no-cache && docker compose up -d"
```

Det svarer til `Opdater-vaerktoejer.bat` i "YouTube → WAV"-mappen.

**"YouTube afviste serveren (bot-tjek)"** betyder at YouTube ikke vil
lade serverens IP hente — det sker oftere fra datacentre end hjemmefra.
Det kan et nyt build ikke løse. Hent beatet med dit eget værktøj og læg
filen op i stedet; det virker som før.

## Ressourcer

Containeren har `mem_limit: 512m`. To gunicorn-workers bruger i størrelsesordenen
100 MB i ro — tjek med `docker stats songwars` når den kører. Uploads
mellemlagres på disken i `data/tmp/`, ikke i hukommelsen. En upload binder
én tråd, ikke en hel worker, så polling fra de andre spillere bliver ved
med at svare, mens nogen sender 50 MB.
