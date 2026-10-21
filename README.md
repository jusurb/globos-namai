# Globos namai asmenims su sunkia negalia

Ilgalaikės / trumpalaikės socialinės globos įstaigų sąrašas iš [vilnius.lt](https://vilnius.lt/savivaldybe/sveikata-ir-socialine-parama/socialines-paslaugos/istaigos-teikiancios-socialines-globos-paslaugas-senyvo-amziaus-asmenims-ir-asmenims-su-negalia#:~:text=ILGALAIK%C4%96S,S%C4%84RA%C5%A0AS): kaina asmeniui su sunkia negalia, vietos, eilės, kontaktai ir atstumas nuo Vilniaus – lentelėje ir žemėlapyje.

## Nuorodos

| Puslapis | Adresas |
|---|---|
| Lentelė | https://jusurb.github.io/globos-namai/ |
| Žemėlapis | https://jusurb.github.io/globos-namai/zemelapis.html |
| Svetainės schema (paieškos sistemoms) | https://jusurb.github.io/globos-namai/sitemap.xml |

Nuorodą į žemėlapį galima tiesiog nusiųsti (el. paštu, Messenger, WhatsApp) – ji atsidaro bet kurioje naršyklėje, ir telefone, ir visada rodo naujausius duomenis. Puslapius viršuje galima perjungti mygtukais **Lentelė** / **Žemėlapis**.

## Kaip naudotis lentele

- **Filtrai** viršuje: paieška pagal pavadinimą, adresą ar kontaktus, didžiausias atstumas, kaina, eilė, mažiausias vietų skaičius ir „tik su laisvomis vietomis“. Įstaigos, kurių reikšmė nežinoma (?), filtruojant paliekamos.
- **Rikiavimas**: spustelėkite stulpelio antraštę; dar kartą – atvirkštine tvarka. Rodyklė ▲/▼ rodo, pagal ką surikiuota.
- **Eil. Nr.** – nuoroda į tą pačią eilutę vilnius.lt puslapyje (patikrinti originalą).
- **Įstaigos pavadinimas** – nuoroda į įstaigos svetainę.
- **Kontaktai**: el. pašto adresas atidaro laišką, telefono numeris telefone iškart skambina.
- **Kaina** – vieno mėnesio kaina asmeniui su sunkia negalia; po ja pilka spalva – originalus aprašymas iš vilnius.lt.
- **Atstumas, km** – iš vilnius.lt; su ženklu „≈“ – apskaičiuotas pagal vietą žemėlapyje (tiesi linija × 1,12).

## Kaip naudotis žemėlapiu

- Ant kiekvienos įstaigos – kainos etiketė. Spalva: **žalia** – iki 1600 €, **geltona** – 1600–1999 €, **raudona** – nuo 2000 €.
- Spustelėjus spalvą legendoje rodomos tik tos spalvos įstaigos; dar kartą spustelėjus – vėl visos.
- Spustelėjus etiketę atsidaro kortelė: kaina, vietos, laisvos vietos, eilė, adresas, kontaktai, nuorodos į svetainę ir į vilnius.lt eilutę.
- Etiketė su **punktyriniu rėmeliu** – vieta apytikslė (gyvenvietė ar pašto rajonas, ne tikslus adresas). Kelios įstaigos toje pačioje vietoje rodomos viena po kita.

## Kaip atnaujinti duomenis

Reikia Python 3.11 ar naujesnio (papildomų paketų nereikia) ir interneto.

```sh
python3 globos.py              # atsisiunčia vilnius.lt sąrašą ir sukuria index.html, zemelapis.html, sitemap.xml
python3 -m unittest test_globos  # testai
git add -A && git commit -m "Atnaujinti duomenys" && git push
```

Po `git push` GitHub Pages puslapius atnaujina per ~1 minutę (naršyklėje gali tekti perkrauti su Cmd+Shift+R).

- Adresų koordinatės saugomos `geocode_cache.json`, todėl kartojant paleidimą adresai iš naujo neieškomi. Nauji adresai ieškomi per OpenStreetMap Nominatim (1 užklausa per sekundę) ir GeoNames.
- Žemėlapio biblioteka Leaflet įdedama tiesiai į `zemelapis.html` (patikrinus jos kontrolinę sumą), todėl žemėlapį galima siųsti ir kaip vieną failą.
- Viešas adresas nustatytas `globos.py` kintamajame `SITE_URL`; jei jis `None`, puslapiai kuriami tik vietiniam naudojimui (be canonical nuorodų ir sitemap.xml).

## Kaip skaičiuojama

- Rodoma **tik kaina „asmenims su sunkia negalia“**. Jei įstaiga turi kelias tokias kainas, pasirenkama: specializuota slauga → stacionaras → grupinio gyvenimo namai. Vietos ir eilė rodomos būtent tos dalies, kuriai taikoma kaina.
- Įstaigos be kainos asmenims su sunkia negalia į sąrašą neįtraukiamos.
- Jei eilės stulpelyje parašyta „Siuntimus išduoda … sav.“, eilė rodoma kaip „?“, o pastaba – po pavadinimu.

## Svarbu

Duomenys – vilnius.lt sąrašo kopija nurodytą dieną. Kainos, laisvos vietos ir eilės keičiasi, todėl prieš sprendžiant pasitikslinkite tiesiogiai įstaigoje arba savivaldybėje. Žemėlapio fonas © Esri, vietos © OpenStreetMap ir GeoNames.
