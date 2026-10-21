import json
import unittest
from unittest import mock

from globos import HEADER, fill, address_queries, distance, extract, main_price, parse_row, postcode_lookup, row_link, severe_prices, summary, head, website


class GlobosTest(unittest.TestCase):
    def test_severe_prices_keep_section(self):
        cell = ["Stacionare:", "1413,00 Eur/mėn. senyvo amžiaus ir asmenims su negalia;", "1492,00 Eur/mėn. asmenims su sunkia negalia.",
                "Grupinio gyvenimo namuose:", "1523,00 Eur/mėn. senyvo amžiaus ir asmenims su negalia;", "1612,00 Eur/mėn. asmenims su sunkia negalia."]
        self.assertEqual(severe_prices(cell), [{"eur": 1492.0, "unit": "mėn", "note": "Stacionare", "label": "Stacionare: asmenims su sunkia negalia"},
                                               {"eur": 1612.0, "unit": "mėn", "note": "Grupinio gyvenimo namuose", "label": "Grupinio gyvenimo namuose: asmenims su sunkia negalia"}])
        self.assertEqual(severe_prices(["2172,67 Eur/mėn. asmenims su sunkia negalia."])[0]["label"], "asmenims su sunkia negalia")

    def test_only_severe_disability_prices(self):
        self.assertEqual([p["eur"] for p in severe_prices(["1610,00 Eur/mėn. asmenims be sunkios negalios;", "1780,00 Eur/mėn. asmenims su sunkia negalia."])], [1780.0])
        self.assertEqual(severe_prices(["2000,00 Eur/mėn. asmeniui."]), [])
        self.assertEqual(severe_prices(["2050,00 Eur/mėn. senyvo amžiaus asmenims ir suaugusiems asmenims su negalia."]), [])
        self.assertEqual([p["eur"] for p in severe_prices(["1450,00 Eur/mėn. senyvo amžiaus ir asmenims su sunkia negalia;", "1650,00 Eur/mėn. asmenims su sunkia negalia."])], [1650.0])

    def test_main_price_prefers_specialised_nursing_then_stacionare(self):
        cell = ["Stacionare:", "1537,00 Eur/mėn. asmenims su sunkia negalia.", "8–10 asmenų grupinio gyvenimo namuose:", "1650,00 Eur/mėn. asmenims su sunkia negalia.",
                "1736,00 Eur/mėn. ilgalaikė specializuota slauga ir globa suaugusiam asmeniui su sunkia negalia."]
        self.assertEqual(main_price(severe_prices(cell))["eur"], 1736.0)
        self.assertEqual(main_price(severe_prices(cell[:4]))["eur"], 1537.0)

    def test_distance(self):
        self.assertEqual(distance(["Prienų r. sav.", "96,9 km.", "64 vietos"]), 96.9)
        self.assertEqual(distance(["Vilnius", "90 vietų"]), 0.0)
        self.assertIsNone(distance(["Klaipėda", "79 vietos"]))

    def test_row_with_several_units_is_one_row_for_the_main_price(self):
        row = [["7."], ["BĮ Nijolės Genytės socialinės globos namai"],
               ["Švenčionių r.", "50 km.", "151 vieta,", "iš kurių stac. 103 vietos", "senyvo 38 vietos,", "grup. 10 vietų"],
               ["-", "-", "-"], ["5", "7", "-"], ["Stacionare:", "1620,00 Eur/mėn. asmenims su sunkia negalia.",
               "Grupinio gyvenimo namuose (8-10 vietų):", "1730,00 Eur/mėn. asmenims su sunkia negalia."], ["Klevų g. 27, Pabradė", "Tel. +370 1"]]
        [r] = parse_row(row)
        self.assertEqual((r["name"], r["spots"], r["queue"], [p["eur"] for p in r["price"]], r["km"], r["address"]),
                         ("BĮ Nijolės Genytės socialinės globos namai", 141, "12", [1620.0], 50.0, "Klevų g. 27, Pabradė"))

    def test_total_with_one_sub_unit_and_nursing_price(self):
        row = [["36."], ["BĮ Strėvininkų socialinės globos namai"], ["Kaišiadorių r.", "71 km.", "180 vietų,", "iš kurių spec. slauga", "40 vietų"],
               ["-", "-"], ["7", "2"], ["Stacionare:", "1580,00 Eur/mėn. asmenims su sunkia negalia.",
               "Specializuotuose slaugos ir socialinės globos namuose – 1748,00 Eur/mėn. asmenims su sunkia negalia."], ["Mokyklos g. 46", "Tel. 1"]]
        [r] = parse_row(row)
        self.assertEqual((r["name"].split(" — ")[1], r["spots"], r["queue"], r["price"][0]["eur"]), ("spec. slauga", 40, "2", 1748.0))

    def test_same_price_at_two_addresses_is_one_row(self):
        row = [["1."], ["VšĮ „Viltis“"], ["Naujasodžio g. 64, Vilnius 10 vietų", "Smilgų g. 2, Lentvaris 10 vietų"], ["1", "-"], ["-", "-"],
               ["2172,67 Eur/mėn. asmenims su sunkia negalia."], ["Naujasodžio g. 64", "Tel. 1"]]
        self.assertEqual([(r["spots"], r["free"]) for r in parse_row(row)], [(20, "1")])

    def test_address_queries_drop_postcodes_then_street(self):
        r = {"address": "Dvaro g. 1, 20355, Jasiuliškio k., Ukmergės r.", "place": "Ukmergės r."}
        self.assertEqual(address_queries(r), ["Dvaro g. 1, Jasiuliškio k., Ukmergės r.", "Jasiuliškio k., Ukmergės r.", "Ukmergės r."])
        self.assertEqual(address_queries({"address": "J. Kubiliaus g. 2, Vilnius 08234", "place": "Vilnius"}),
                         ["J. Kubiliaus g. 2, Vilnius", "Vilnius"])


    def test_website_is_name_link_not_queue_or_directory(self):
        links = [("Pasitikrinti eilę", "https://docs.google.com/x"), ("https://anta.lrv.lt/lt/x", "https://anta.lrv.lt/lt/x"),
                 ("BĮ Aknystos socialinės globos namai", "https://aknystossgn.lrv.lt/lt/")]
        self.assertEqual(website(links), "https://aknystossgn.lrv.lt/lt/")
        self.assertIsNone(website([("Evil", "javascript:alert(1)")]))

    def test_referral_text_is_moved_out_of_queue(self):
        tds = lambda *c: "<tr>" + "".join(f"<td>{x}</td>" for x in c) + "</tr>"
        page = (f"<h3>{HEADER}</h3><table>" + tds("Nr", "Pavadinimas", "Vietos", "Laisvos vietos", "Eilė", "Kaina", "Kontaktai")
                + tds("1.", "A", "Trakų r.<br>50 km.<br>25 vietos", "-", "Siuntimus išduoda Kauno m. sav.", "1500,00 Eur/mėn. asmenims su sunkia negalia.", "Adr. 1")
                + tds("2.", "B", "Vilnius<br>9 vietos", "-", "12", "1500,00 Eur/mėn. asmenims su sunkia negalia.", "Adr. 2") + "</table>")
        a, b = extract(page)
        self.assertEqual((a["queue"], a["queue_n"], a["referral"]), ("", None, "Kauno m. sav."))
        self.assertEqual((b["queue"], b["queue_n"], b.get("referral")), ("12", 12, None))

    def test_row_link_uses_first_text_unique_on_page(self):
        cells = [["5."], ["BĮ Prienų globos namai", "Pasitikrinti eilę"], [], [], [], [], ["Adr.", "El. p. info@pgn.lt", "Tel. +370 (3) 196 0401"]]
        self.assertTrue(row_link(cells, "x bį prienų globos namai info@pgn.lt").endswith("#:~:text=B%C4%AE%20Prien%C5%B3%20globos%20namai"))
        self.assertTrue(row_link(cells, "bį prienų globos namai bį prienų globos namai info@pgn.lt").endswith("#:~:text=info%40pgn.lt"))
        self.assertTrue(row_link([["1."], ["A-B namai"], [], [], [], [], []], "a-b namai").endswith("text=A%2DB%20namai"))

    def test_postcode_lookup_by_code_then_village_in_same_municipality(self):
        places = [("59015", "Putriškių k.", "Prienų r. sav.", 54.53, 23.88), ("33016", "Putriškių k.", "Molėtų r. sav.", 55.06, 25.65),
                  ("69030", "Kalvarijos", "Kalvarijos sav.", 54.41, 23.22), ("69037", "Mikališkių k.", "Kalvarijos sav.", 54.4, 23.23)]
        self.assertEqual(postcode_lookup("Lankų g. 32, Putriškių k., Balbieriškio sen., LT-59252 Prienų r. sav.", places), (54.53, 23.88))
        self.assertEqual(postcode_lookup("Mikališkių k. 4, Kalvarijos sen., Kalvarijos sav.", places), (54.4, 23.23))
        self.assertEqual(postcode_lookup("Kažkur 1, LT-33016 Niekur", places), (55.06, 25.65))
        self.assertIsNone(postcode_lookup("Putriškių k., Vilniaus m. sav.", places))
        self.assertIsNone(postcode_lookup("Mikališkių k. 4, Kalvarijos sav.", places + [("69099", "Mikališkių k.", "Kalvarijos sav.", 54.5, 23.3)]))

    def test_summary_and_head_escape_and_count(self):
        rows = [{"price": [{"eur": e, "unit": u}], "free_n": f} for e, u, f in [(1500, "mėn", 1), (2000, "mėn", 0), (1700, "mėn", None), (90, "dieną", 0)]]
        self.assertIn("nuo 1500 iki 2000 € (mediana 1700 €); laisvų vietų nurodyta 1 įstaigose", summary(rows, "2026-10-21"))
        with mock.patch("globos.SITE_URL", None):
            h = head('A "B" <c>', "d", "index.html", "2026-10-21", dataset=True)
        self.assertIn("<title>A &quot;B&quot; &lt;c&gt;</title>", h)
        self.assertNotIn("canonical", h)  # local-only use
        with mock.patch("globos.SITE_URL", "https://x.github.io/g/"):
            self.assertIn('<link rel="canonical" href="https://x.github.io/g/">', head("t", "d", "index.html", "2026-10-21"))
        self.assertEqual(json.loads(h.split('ld+json">')[1].split("</script>")[0])["@type"], "Dataset")

    def test_fill_does_not_rescan_inserted_text(self):
        self.assertEqual(fill("<td>__ROWS__</td><script>__DATA__</script>", ROWS="evil __DATA__", DATA="[]"),
                         "<td>evil __DATA__</td><script>[]</script>")


if __name__ == "__main__":
    unittest.main()
