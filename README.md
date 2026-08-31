# Portfolio

Statisk portfolio-side. Ingen server-side rendering, ingen Node-runtime,
ingen database. `dist/` er rene filer som nginx kan servere direkte.

```
build.py                 generator (kun Python-standardbibliotek)
content/
  site.md                navn, rolle, tagline, mail + "Om mig"-teksten
  kompetencer.md         kompetencegrupper
  projects/*.md          ET projekt pr. fil
src/
  template.html          sidens skelet
  styles.css             al styling
  main.js                filtrering + aktivt menupunkt (valgfri)
  favicon.svg
dist/                    ← byg-output. Det er kun denne mappe der skal på serveren.
```

## Byg

```bash
python build.py
```

Lokal preview på <http://localhost:8000>:

```bash
python build.py --serve
```

Der er ingen `npm install`, ingen `node_modules`, ingen afhængigheder at
holde opdateret. Alt hvad der skal bruges er Python 3, og det ligger
allerede på Ubuntu.

## Tilføj et projekt

Lav én ny fil i `content/projects/` og byg. Ikke andet — nummerering,
filterknapper og projekttælleren i hero'en bliver genereret.

```markdown
---
title: Navn på projektet
status: I drift
tags: [Python, nginx, Docker]
summary: Én linje der forklarer hvad det er. Vises fremhævet øverst i kortet.
---

Et afsnit der uddyber hvad projektet gør, og hvorfor du byggede det.

- Punkter til det konkrete du har lavet
- **Fed skrift** til at fremhæve et begreb
- `kommandoer og filnavne` i kode-format

Et afsnit til sidst om hvad du lærte, eller hvad næste skridt er.
```

Filerne sorteres efter filnavn, så præfikset (`01-`, `02-`, …) styrer
rækkefølgen. Skal et projekt flyttes uden at omdøbe filen, kan du sætte
`order: 3` i frontmatter — lavest tal først.

**Gyldige `status`-værdier** (styrer farven på badgen):

| Værdi | Farve |
|---|---|
| `I drift`, `Kører`, `Aktiv` | grøn |
| `Løbende`, `Igangværende` | blå |
| `Under udvikling`, `I gang`, `Planlagt` | gul |
| `Afsluttet`, `Færdig`, `Arkiveret` | grå |

Andre værdier virker fint, de får bare den grå farve.

## Ret navn, mail og "Om mig"

Alt personligt står i `content/site.md`. Kompetencerne står i
`content/kompetencer.md` — en `## Gruppe` efterfulgt af `- punkter`.
Tilføjer du en femte gruppe, tilpasser layoutet sig selv.

## Deploy

Byg lokalt og kopiér `dist/` op:

```bash
python build.py
rsync -av --delete dist/ lucas@dinserver:/var/www/portfolio/
```

`--delete` rydder gamle filer væk. Lad være med at lægge filer direkte i
`dist/` på serveren — de bliver slettet ved næste deploy. Skal der ligge
en PDF eller et billede med, så læg det i `src/` og tilføj filnavnet til
`ASSET_FILES` i `build.py`.

Server-blokken ligger i [`nginx.conf.example`](nginx.conf.example).

## Bemærkninger

- Siden virker fuldt ud uden JavaScript. `main.js` tilføjer kun
  projektfiltrering og markering af aktivt menupunkt — alt indhold står i
  HTML'en.
- Ingen fonte, scripts eller billeder hentes fra tredjepart. Ingen
  cookies, intet at samtykke til.
- CSS og JS får en `?v=<hash>` bag URL'en ved hvert byg, så browsere ikke
  serverer en gammel version efter en ændring.
- `Ctrl+P` giver en brugbar udskrift — der er et print-stylesheet.
