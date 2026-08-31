---
title: NetOps-dashboard
status: Under udvikling
# TODO (Lucas): ret tags til den stak du faktisk bruger
tags: [Python, SNMP, SQLite, REST API]
summary: Datadrevet overvågning der samler netværks- og sikkerhedsstatus ét sted.
---

Idéen kom af irritation: for at finde ud af om noget var galt, skulle jeg logge på hver enkelt enhed og kigge manuelt. Det skalerer ikke, og det bliver ikke gjort.

- Indsamler tilgængelighed, interface-status og log-hændelser fra enhederne i labbet
- Gemmer historik, så man kan se **hvornår** noget ændrede sig — ikke bare at det er galt lige nu. Det er forskellen på en alarm og en forklaring
- Prioriterer få læsbare signaler frem for en væg af grafer som ingen kigger på
- Bygget som et værktøj jeg selv vil bruge dagligt, ikke som en demo

Status: dataindsamlingen kører, visningslaget er under opbygning.
