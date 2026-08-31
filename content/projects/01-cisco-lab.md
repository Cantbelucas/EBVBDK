---
title: Cisco-lab
status: Løbende
tags: [Cisco IOS, VLAN, EtherChannel, STP, HSRP, AAA]
summary: Hands-on netværkslab hvor jeg konfigurerer, verificerer og bevidst bryder et switchet og routet netværk.
---

Labbet er bygget op omkring et par switche og en router, og bliver revet ned og sat op igen hver gang der er en mekanisme jeg ikke forstår godt nok.

- **Segmentering:** VLAN, trunking og inter-VLAN routing, med kontrol af hvilke VLAN'er der reelt kommer igennem en trunk
- **Redundans og båndbredde:** EtherChannel med LACP, og Spanning Tree hvor root bridge er valgt bevidst i stedet for tilfældigt
- **Failover:** HSRP på gateway-niveau, verificeret ved at trække forbindelsen og måle hvor længe trafikken er nede
- **Sti-overvågning:** IP SLA til at måle om en rute faktisk svarer, og til at skifte statisk routing automatisk
- **Adgangskontrol:** Port Security på access-porte og centraliseret AAA i stedet for lokale brugere på hver enhed

Hver øvelse afsluttes med verifikation via `show`-kommandoer og en kort note om hvad der gik galt undervejs. Noterne er ærligt talt den mest værdifulde del — det er dem jeg slår op i, næste gang det samme sker.
