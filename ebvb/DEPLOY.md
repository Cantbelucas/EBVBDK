# Deploy

## Sådan ser det ud i drift

Omlægningen er gennemført. `ebvb.dk` kører appen, `cv.ebvb.dk` kører
portfolioen. Opsætningen på serveren ligger i `~/stack`:

```
Bruger → Cloudflare → nginx-container (80/443 på værten)
                          ├─ cv.ebvb.dk  → filer fra ~/stack/portfolio
                          └─ ebvb.dk     → ebvb-containeren på 8090
```

Begge containere ligger på Docker-netværket `stack_web`. **App-containeren
er ikke publiceret til værten** — den kan kun nås gennem nginx, hvilket er
med vilje. Nginx' konfiguration ligger i `~/stack/nginx/conf.d/`, og
certifikaterne er Cloudflare Origin CA fra `/etc/ssl/cloudflare`.

Et deploy er `~/deploy.sh` på serveren: den henter fra GitHub, bygger
portfolioen, genbygger app-imaget og starter containeren.

**To fælder der har bidt før:**

1. `--build` genskaber app-containeren og giver den en ny IP. Nginx slår
   kun upstream-navnet op ved indlæsning, så den holder fast i den gamle
   og svarer 502. `nginx.ebvb.conf.example` løser det permanent med
   `resolver 127.0.0.11` og en variabel i `proxy_pass`. Indtil de linjer
   står i din `musik.conf`, skal du køre `docker exec nginx nginx -s reload`
   efter hvert deploy.
2. Browseren og Cloudflare cacher `styles.css` og `app.js`. Appen sætter
   nu `?v=<hash>` bag dem, så en ny udgave får en ny URL. Ser du alligevel
   den gamle side, er det en cache fra før den rettelse — Ctrl+F5.

Resten af dokumentet er selve omlægningen fra Nextcloud, som er
historik nu, men står tilbage som opskrift hvis noget skal gøres om.

---

# Sæt EBVB på ebvb.dk i stedet for Nextcloud

Rækkefølgen her er valgt så siden aldrig er nede mere end den ene
`systemctl reload`, og så du kan rulle tilbage på ét minut hvis noget
ikke virker. **Nextcloud bliver stående og kørende hele vejen igennem** —
vi tager den bare ned fra `ebvb.dk`. Først når du har set at alt er med
over, beslutter du om den skal slukkes.

Jeg har ikke adgang til din server, så alt herunder kører du selv.
Kommandoer med `dinserver` og `nextcloud` skal have dine rigtige navne.

**Shell:** kommandoerne er skrevet til at blive indsat i PowerShell på din
Windows-maskine. Alt inde i anførselstegnene efter `ssh` bliver kørt af
bash på serveren — derfor er `&&` og pipes derinde helt i orden, selvom
PowerShell ikke selv forstår `&&`. Kører du en kommando *lokalt* med
`&&`, deler du den op i to linjer i stedet.

---

## 0. Før du går i gang

```bash
ssh lucas@dinserver "docker ps --format '{{.Names}}\t{{.Ports}}'; ls /etc/nginx/sites-enabled/"
```

Skriv ned hvad Nextcloud-containeren hedder, og hvad nginx-filen for
`ebvb.dk` hedder. Dem skal du bruge i trin 4 og 5.

Tag en kopi af den nginx-fil du er ved at erstatte:

```bash
ssh lucas@dinserver "sudo cp /etc/nginx/sites-available/nextcloud ~/nextcloud.nginx.bak"
```

---

## 1. Hent koden ned på serveren

Repoet er offentligt, så serveren kan klone det uden nøgler. Klon hele
repoet ét sted, og lav `/opt/ebvb` til et link ind i app-mappen — så
passer alle kommandoerne herunder, og en opdatering er ét `git pull`.

```bash
ssh lucas@dinserver "sudo git clone https://github.com/Cantbelucas/EBVBDK.git /opt/ebvb-src && sudo ln -s /opt/ebvb-src/ebvb /opt/ebvb && ls -l /opt/ebvb/"
```

Er branchen ikke merget til `main` endnu, så skift over til den:

```bash
ssh lucas@dinserver "cd /opt/ebvb-src && sudo git checkout ebvb-lytterum"
```

Opdatering fremover, når du har pushet noget nyt:

```bash
ssh lucas@dinserver "cd /opt/ebvb-src && sudo git pull && cd /opt/ebvb && docker compose up -d --build"
```

`data/` er gitignoreret og ligger uden for det git rører ved, så et
`git pull` kan ikke overskrive databasen eller lydfilerne.

**Alternativ uden git på serveren** — hvis du hellere vil skubbe fra din
egen maskine, som du gør med portfolioen:

```bash
rsync -av --exclude data --exclude __pycache__ ebvb/ lucas@dinserver:/opt/ebvb/
```

`--exclude data` er ikke til forhandling. Uden den overskriver du
serverens database med din lokale, tomme. `deploy.sh` gør det samme og
har den indbygget — men både `rsync` og `deploy.sh` kræver Git Bash,
ikke PowerShell. Har du ikke det, er git-vejen ovenfor den lette.

---

## 2. Byg og start containeren

```bash
ssh lucas@dinserver "cd /opt/ebvb && docker compose up -d --build"
```

Den lytter nu på `127.0.0.1:8090` og kan ikke nås udefra endnu. Tjek at
den svarer:

```bash
ssh lucas@dinserver "curl -sI http://127.0.0.1:8090/ | head -1"
```

Du skal se `HTTP/1.1 302 FOUND` — den sender videre til `/login`. Får du
`connection refused`, så se hvorfor:

```bash
ssh lucas@dinserver "cd /opt/ebvb && docker compose logs --tail 40"
```

---

## 3. Opret brugerne

Din egen konto skal have `--admin`. Kun admin kan slette andres spor.

```bash
ssh -t lucas@dinserver "cd /opt/ebvb && docker compose exec ebvb python app.py adduser lucas --admin"
```

`-t` er nødvendig — uden en terminal kan `getpass` ikke spørge om
kodeordet. Så de to andre:

```bash
ssh -t lucas@dinserver "cd /opt/ebvb && docker compose exec ebvb python app.py adduser fjolli"
```

```bash
ssh -t lucas@dinserver "cd /opt/ebvb && docker compose exec ebvb python app.py adduser cc"
```

Kodeordet skal være mindst 10 tegn og bliver hashet med scrypt. Det står
aldrig i klartekst — heller ikke i databasen.

---

## 4. Hent musikken ud af Nextcloud

Det her er den del der faktisk kan tabe noget, så tag den før du rører
nginx. Filerne i delte mapper ligger fysisk hos ejeren, så én kopi fra
din egen konto får også det vennerne har lagt op.

Find datamappen:

```bash
ssh lucas@dinserver "docker exec nextcloud ls /var/www/html/data"
```

Kopiér de to mapper ud af containeren:

```bash
ssh lucas@dinserver "docker cp nextcloud:/var/www/html/data/lucas/files/Beats /tmp/nc-beats && docker cp nextcloud:/var/www/html/data/lucas/files/MUSIC /tmp/nc-music && du -sh /tmp/nc-*"
```

Flyt dem ind hvor containeren kan se dem, og kør importen tør først:

```bash
ssh lucas@dinserver "mv /tmp/nc-beats /tmp/nc-music /opt/ebvb/data/ && cd /opt/ebvb && docker compose exec ebvb python app.py import /data/nc-beats beats lucas --proev"
```

`--proev` skriver ikke noget. Den viser bare hvad der ville blive lagt
ind. Ser listen rigtig ud, så kør uden:

```bash
ssh lucas@dinserver "cd /opt/ebvb && docker compose exec ebvb python app.py import /data/nc-beats beats lucas && docker compose exec ebvb python app.py import /data/nc-music music lucas"
```

Hvad importen gør:

- Går rekursivt gennem mappen og tager alt med en lydendelse.
- Titlen kommer fra filnavnet, med `_` og `-` lavet om til mellemrum.
- Datoen kommer fra filens tidsstempel, så rækkefølgen i listen svarer
  til hvornår tingene faktisk blev lavet.
- Et cover bliver fundet hvis der ligger et billede med samme navn som
  lydfilen, eller en `cover.*` / `folder.*` i samme mappe.
- Kører du den to gange, springer den over det der allerede er inde. Den
  laver ikke dubletter.

Alt bliver lagt ind under din bruger, fordi filsystemet ikke ved hvem
der lagde hvad op i Nextcloud. Skal `fjolli` stå som ejer af sine egne,
så importér hans mappe for sig med `fjolli` som sidste argument.

Ryd op når du har set at det er med:

```bash
ssh lucas@dinserver "rm -rf /opt/ebvb/data/nc-beats /opt/ebvb/data/nc-music"
```

---

## 5. Byt nginx om

Nu er det ét greb. Læg den nye blok ind, tag den gamle ud, test, genlæs:

```bash
ssh lucas@dinserver "sudo cp /opt/ebvb/nginx.ebvb.conf.example /etc/nginx/sites-available/ebvb && sudo ln -sf /etc/nginx/sites-available/ebvb /etc/nginx/sites-enabled/ebvb && sudo rm -f /etc/nginx/sites-enabled/nextcloud && sudo nginx -t"
```

**Kun hvis `nginx -t` siger `syntax is ok` og `test is successful`:**

```bash
ssh lucas@dinserver "sudo systemctl reload nginx"
```

`reload` smider ikke igangværende forbindelser. Siger `nginx -t`
derimod noget andet, så rør ikke reload — så er den gamle opsætning
stadig den der kører, og du har intet ødelagt.

Certifikatet er det samme, `ebvb.dk` peger allerede på serveren, og
Cloudflare skal ikke ændres. Der er ingen DNS-ændring i det her trin.

---

## 6. Tjek

```bash
curl.exe -sI https://ebvb.dk/
```

`.exe` er ikke en tastefejl. Uden den rammer du PowerShells eget alias
for `Invoke-WebRequest`, som ikke forstår `-sI`. Den rigtige curl ligger
i `C:\Windows\System32` på Windows 11.

Så i en browser: log ind, spil et spor, spol i det, hent en fil, og læg
en ny op. Prøv den i inkognito for at bekræfte at login rent faktisk
lukker af.

---

## 7. Rollback

Virker noget ikke, er du tilbage på Nextcloud på under et minut:

```bash
ssh lucas@dinserver "sudo rm -f /etc/nginx/sites-enabled/ebvb && sudo ln -sf /etc/nginx/sites-available/nextcloud /etc/nginx/sites-enabled/nextcloud && sudo nginx -t && sudo systemctl reload nginx"
```

Nextcloud har kørt hele tiden. Der er intet at starte op igen, og intet
er slettet.

---

## Cloudflare og de 100 MB

Det her er den ene ting jeg forventer bider jer.

`client_max_body_size 512M` i nginx gælder kun fra nginx og indefter.
Cloudflare har sin egen grænse på request bodies, og på gratis- og
Pro-planer er den 100 MB. En 300 MB wav bliver afvist af Cloudflare med
en 413, før den overhovedet rammer din server — og fejlen ligner en fejl
i din opsætning, selvom din opsætning er rigtig.

Test det som det første efter skiftet: læg jeres største wav op. Går den
igennem, er der ikke mere at gøre. Bliver den afvist, har du tre veje:

1. **Slå proxy fra for `ebvb.dk`** (grå sky i Cloudflares DNS-panel i
   stedet for orange). Uploads bliver ubegrænsede, men serverens IP
   bliver synlig, og I mister Cloudflares filter foran siden.
2. **Lav et underdomæne til upload**, fx `up.ebvb.dk`, som er grå sky,
   og lad `ebvb.dk` blive bag proxyen. Mere fedtet, men I beholder
   beskyttelsen på selve siden.
3. **Hold jer under 100 MB.** En 24-bit/48 kHz wav på 100 MB er cirka
   6 minutter. Til beats rækker det langt.

Jeg vil anbefale at teste først og først vælge bagefter. Det kan være at
ingen af jeres filer er store nok til at det er et problem.

---

## Nextcloud bagefter

Lad den køre en uges tid, indtil I er sikre på at alt er med over. Så:

**Stop den, behold alt:**

```bash
ssh lucas@dinserver "cd /sti/til/nextcloud && docker compose stop"
```

Det frigiver de 1–2 GB RAM til Minecraft. Data og containeren bliver
liggende, og `docker compose start` henter den tilbage.

**Eller flyt den til `cloud.ebvb.dk`** i stedet for at slukke — den
blok ligger udkommenteret nederst i `nginx.ebvb.conf.example`. Kræver
en A-record, et certifikat, og `overwrite.cli.url` i Nextclouds
`config.php`.

Slet ikke Nextclouds data før du har taget en backup, og før EBVB har
kørt et stykke tid uden overraskelser.

---

## Backup fremover

Alt der betyder noget ligger i `/opt/ebvb/data`:

```bash
ssh lucas@dinserver 'sudo tar czf ~/ebvb-$(date +%F).tar.gz -C /opt/ebvb data'
```

Sæt den i cron ugentligt når du har fået den til at køre en gang i
hånden.
