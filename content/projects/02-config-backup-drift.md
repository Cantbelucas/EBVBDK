---
title: Config backup + drift detection
status: I drift
tags: [Python, Git, SSH, Netværksautomatisering]
summary: Script der henter running-config fra netværksenheder, versionsstyrer den i Git og alarmerer ved ændringer.
---

Scriptet logger på enhederne med faste intervaller og committer kun når noget reelt er anderledes. Formålet er at kunne svare præcist på **hvem ændrede hvad, hvornår — og hvad stod der før?** uden at gætte.

- Én fil pr. enhed, så `git log` bliver en tidslinje over hele netværkets konfiguration
- Flygtige linjer som timestamps, uptime og tællere filtreres fra, så der kun opstår en commit ved en **reel** konfigurationsændring — ellers drukner de rigtige ændringer i støj
- Uventet drift udløser en notifikation med selve diff'en vedhæftet, så man kan se ændringen uden at skulle logge på noget
- Credentials ligger uden for repoet i miljøvariabler, aldrig i koden
- Kører som planlagt job, og fejler højlydt hvis en enhed ikke svarer — et backup-script der stille holder op med at virke er værre end ingen backup

Det er også min rollback-plan: en kendt god konfiguration ligger altid ét `git checkout` væk.
