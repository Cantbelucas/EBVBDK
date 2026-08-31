---
title: Selvhostet stack
status: I drift
tags: [Ubuntu, Docker, nginx, Cloudflare, UFW, SSH]
summary: Hærdet cloud-VM som jeg selv drifter — og som denne side kører på lige nu.
---

VM'en er sat op fra en tom Ubuntu-installation, og hvert lag er lagt på bevidst ét ad gangen. **Siden du læser lige nu bliver serveret herfra** som statiske filer gennem nginx.

- **SSH:** kun nøglebaseret login. Password-autentificering og direkte root-login er slået fra
- **Firewall:** `ufw` med default deny indgående — kun de porte der faktisk skal bruges er åbne
- **Isolation:** tjenester kører i Docker-containere, så de ikke deler filsystem og afhængigheder med hinanden eller med værten
- **Indgang:** nginx som reverse proxy foran containerne, med HTTPS og automatisk fornyede certifikater
- **Foran det hele:** Cloudflare til DNS, TLS og som filter mod den baggrundsstøj enhver offentlig IP får
- **På vej:** privat fildeling og en gameserver på samme host, adskilt fra det der allerede kører

Pointen er ikke at det er avanceret — pointen er at det har oppetid, at jeg selv har sat hvert lag op, og at jeg ved hvad der sker når noget går ned.
