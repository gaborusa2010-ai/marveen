#!/usr/bin/env python3
"""idle-agent-guard: tud-e MINDKET valaszt adni, es tud-e NEMET mondani a mero.

A flotta szabalya szerint egy ellenorzot nem az igazol, hogy zoldet mutat, hanem
hogy pirosat IS tud mutatni. Ezert minden agra van eset, es a mai eles adatokra
egy visszameres is.
"""
import importlib.util
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "idle-agent-guard.py")


def load():
    spec = importlib.util.spec_from_file_location("idle_agent_guard", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


G = load()


class TestShouldAlert(unittest.TestCase):
    """A dontesfuggveny, mind a negy ag."""

    def test_riaszt_ha_fut_tetlen_es_van_munkaja(self):
        # A 2026-09-17-i eset, visszameresbol: cody 05:23 ota futott, 0 token,
        # 7 nyitott kartya. Ha ez az orszem akkor letezik, szol.
        self.assertTrue(G.should_alert(True, 0, 7, 1_000_000))
        self.assertTrue(G.should_alert(True, 0, 3, 1_000_000))   # roxy, ugyanaznap

    def test_NEM_riaszt_ha_dolgozik(self):
        # Pozitiv kontroll a masik iranyba: Forgacs ugyanaznap 55,5M tokenen.
        self.assertFalse(G.should_alert(True, 55_473_523, 3, 1_000_000))
        # Es a hataron: pont a kuszob felett.
        self.assertFalse(G.should_alert(True, 1_000_001, 5, 1_000_000))

    def test_NEM_riaszt_ha_nem_fut(self):
        # A gazda megkotese: pihenonapos agens nem lelet. Margo 2026-09-17-en.
        self.assertFalse(G.should_alert(False, 0, 2, 1_000_000))

    def test_NEM_riaszt_ha_nincs_munkaja(self):
        # Ugyanaz a megkotes masik felol: fut, tetlen, de nincs mit tennie.
        self.assertFalse(G.should_alert(True, 0, 0, 1_000_000))

    def test_a_kuszob_pontosan_inkluziv(self):
        self.assertTrue(G.should_alert(True, 1_000_000, 1, 1_000_000))


class TestApiGetTudNemetMondani(unittest.TestCase):
    """A mero nem defaultolhat uresre egy hibavalaszon."""

    def test_nem_lista_valasz_HIBAT_dob_nem_uresset_ad(self):
        with mock.patch.object(G, "api_get", return_value={"error": "bad", "known": []}):
            with self.assertRaises(RuntimeError):
                G.running_agents()
            with self.assertRaises(RuntimeError):
                G.open_work()

    def test_hianyzo_name_mezo_HIBAT_dob(self):
        # Ha valaki `id`-re ir at a mezot, ne csendben ejtsen ki mindenkit.
        with mock.patch.object(G, "api_get", return_value=[{"id": "cody", "running": True}]):
            with self.assertRaises(RuntimeError) as cm:
                G.running_agents()
            self.assertIn("name", str(cm.exception))

    def test_running_agents_helyesen_olvas(self):
        rows = [{"name": "cody", "running": True}, {"name": "margo", "running": False}]
        with mock.patch.object(G, "api_get", return_value=rows):
            self.assertEqual(G.running_agents(), {"cody": True, "margo": False})

    def test_open_work_kihagyja_a_done_es_archivalt_kartyat(self):
        rows = [
            {"assignee": "cody", "status": "planned"},
            {"assignee": "Cody", "status": "waiting"},          # kis-nagybetu
            {"assignee": "cody", "status": "done"},             # nem szamol
            {"assignee": "cody", "status": "planned", "archived_at": 123},  # nem szamol
            {"assignee": None, "status": "planned"},            # delegalatlan
            {"assignee": "roxy", "status": "in_progress"},
        ]
        with mock.patch.object(G, "api_get", return_value=rows):
            w = G.open_work()
        self.assertEqual(w["cody"], 2)
        self.assertEqual(w["roxy"], 1)
        self.assertEqual(w["margo"], 0)


class TestNotifyBukasaNemJelolodikElkuldottnek(unittest.TestCase):
    """Ha a kuldes elbukik, a csillapitas NE iktassa be -- kulonben soha nem szol ujra."""

    def _run(self, notify_ok, state_path):
        fake_guard = mock.Mock()
        fake_guard.window_tokens.return_value = (0, 1, 0.0, False)
        send = mock.Mock(return_value=notify_ok)
        with mock.patch.object(G, "_load_guard", return_value=fake_guard), \
             mock.patch.object(G, "scheduled_today", return_value={}), \
             mock.patch.object(G, "send_to_main", send), \
             mock.patch.object(G, "running_agents", return_value={n: (n == "cody") for n in G.SUBS}), \
             mock.patch.object(G, "open_work", return_value={n: (7 if n == "cody" else 0) for n in G.SUBS}), \
             mock.patch.object(G, "STATE", state_path), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = G.main(["--notify"])
        return rc, out.getvalue(), send

    def test_sikeres_kuldes_utan_csillapit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            rc, out, fg = self._run(True, p)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["alert_count"], 1)
            fg.assert_called_once()
            with open(p, encoding="utf-8") as fh:
                self.assertIn("cody", json.dumps(json.load(fh)))

    def test_elbukott_kuldes_NEM_ir_allapotot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            rc, out, fg = self._run(False, p)
            self.assertEqual(rc, 0)
            fg.assert_called_once()
            self.assertFalse(os.path.exists(p),
                             "elbukott kuldes utan nem szabad csillapitani")

    def test_notify_nelkul_NEM_kuld(self):
        fake_guard = mock.Mock()
        fake_guard.window_tokens.return_value = (0, 1, 0.0, False)
        with mock.patch.object(G, "_load_guard", return_value=fake_guard), \
             mock.patch.object(G, "scheduled_today", return_value={}), \
             mock.patch.object(G, "running_agents", return_value={n: True for n in G.SUBS}), \
             mock.patch.object(G, "open_work", return_value={n: 5 for n in G.SUBS}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            G.main([])
        self.assertEqual(json.loads(out.getvalue())["alert_count"], 5)
        fake_guard.notify.assert_not_called()


class TestNemMerhetoNemCsendesAgens(unittest.TestCase):
    def test_a_hianyzo_transzkript_nem_nullakent_jon_be(self):
        fake_guard = mock.Mock()
        fake_guard.window_tokens.return_value = (0, 0, 0.0, True)   # missing=True
        with mock.patch.object(G, "_load_guard", return_value=fake_guard), \
             mock.patch.object(G, "scheduled_today", return_value={}), \
             mock.patch.object(G, "running_agents", return_value={n: True for n in G.SUBS}), \
             mock.patch.object(G, "open_work", return_value={n: 5 for n in G.SUBS}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            G.main([])
        d = json.loads(out.getvalue())
        self.assertEqual(d["rows"], [])
        self.assertEqual(sorted(d["unmeasurable"]), sorted(G.SUBS))
        self.assertEqual(d["alert_count"], 0)
        self.assertIn("NEM MERHETO", err.getvalue())


# 2026-10-07 was a Wednesday; the guard's 09:00 run raised the margo alert.
WED_0900 = __import__("datetime").datetime(2026, 10, 7, 9, 0, 8)


class TestFirstFireToday(unittest.TestCase):
    """A cron-olvaso: a mai hatralevo elso idopont, vagy None."""

    def test_napi_esti_feladat_reggel_meg_hatravan(self):
        self.assertEqual(G.first_fire_today("0 19 * * *", WED_0900), "19:00")

    def test_mar_lefutott_feladat_NEM_szamit(self):
        self.assertIsNone(G.first_fire_today("0 7 * * 1-5", WED_0900))

    def test_az_aktualis_perc_meg_szamit(self):
        # A 09:00-s feladat promptja epp most megy ki; az agens meg nem kolthetett.
        self.assertEqual(G.first_fire_today("0 9 * * *", WED_0900), "09:00")

    def test_mas_napra_szolo_NEM_szamit(self):
        self.assertIsNone(G.first_fire_today("0 19 * * 0", WED_0900))   # vasarnap
        self.assertIsNone(G.first_fire_today("0 19 * * 7", WED_0900))   # 7 = vasarnap is
        self.assertEqual(G.first_fire_today("0 19 * * 3", WED_0900), "19:00")

    def test_lepes_tartomany_lista(self):
        self.assertEqual(G.first_fire_today("*/30 6-22 * * *", WED_0900), "09:00")
        self.assertEqual(G.first_fire_today("5,30 16,18 * * 1-6", WED_0900), "16:05")
        self.assertEqual(G.first_fire_today("15/20 10 * * *", WED_0900), "10:15")

    def test_honap_es_nap_szures(self):
        self.assertIsNone(G.first_fire_today("0 9 27,30 11 *", WED_0900))
        self.assertEqual(G.first_fire_today("0 12 7 10 *", WED_0900), "12:00")

    def test_nap_es_hetnap_VAGY_kapcsolat(self):
        # Mindketto korlatozott: eleg, ha az egyik illik (cron-parser szabaly).
        self.assertEqual(G.first_fire_today("0 12 1 * 3", WED_0900), "12:00")
        self.assertEqual(G.first_fire_today("0 12 7 * 1", WED_0900), "12:00")
        self.assertIsNone(G.first_fire_today("0 12 1 * 1", WED_0900))

    def test_hibas_kifejezes_HIBAT_dob(self):
        for bad in ("0 19 * *", "61 * * * *", "0 25 * * *", "x * * * *", "*/0 * * * *", ""):
            with self.assertRaises(ValueError, msg=bad):
                G.first_fire_today(bad, WED_0900)


class TestScheduledToday(unittest.TestCase):
    def _dir(self, d, tasks):
        for name, cfg in tasks.items():
            os.makedirs(os.path.join(d, name))
            with open(os.path.join(d, name, "task-config.json"), "w", encoding="utf-8") as fh:
                fh.write(cfg if isinstance(cfg, str) else json.dumps(cfg))

    def test_csak_az_engedelyezett_mai_sub_agens_feladat(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._dir(d, {
                "esti": {"schedule": "0 19 * * *", "agent": "margo", "enabled": True},
                "korabbi": {"schedule": "0 12 * * *", "agent": "Margo", "enabled": True},
                "kikapcsolt": {"schedule": "0 10 * * *", "agent": "cody", "enabled": False},
                "lefutott": {"schedule": "0 7 * * 1-5", "agent": "forgacs", "enabled": True},
                "fo": {"schedule": "0 21 * * *", "agent": "marveen", "enabled": True},
            })
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                got = G.scheduled_today(WED_0900, d)
        self.assertEqual(got, {"margo": "12:00"})

    def test_olvashatatlan_config_kihagyva_es_SZOL(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._dir(d, {
                "rossz-json": "{nem json",
                "rossz-cron": {"schedule": "0 19 * *", "agent": "roxy", "enabled": True},
            })
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                got = G.scheduled_today(WED_0900, d)
        self.assertEqual(got, {})   # nem mentesit: inkabb riaszt
        self.assertEqual(err.getvalue().count("nem olvashato"), 2)


class TestUtemezettMunkaNemRiaszt(unittest.TestCase):
    """A 2026-10-07-i margo-eset, mindket iranyban."""

    def test_should_alert_utemezett_munkaval_NEM_riaszt(self):
        self.assertFalse(G.should_alert(True, 0, 5, 1_000_000, True))
        self.assertTrue(G.should_alert(True, 0, 5, 1_000_000, False))

    def _main(self, sched):
        fake_guard = mock.Mock()
        fake_guard.window_tokens.return_value = (0, 1, 0.0, False)
        with mock.patch.object(G, "_load_guard", return_value=fake_guard), \
             mock.patch.object(G, "scheduled_today", return_value=sched), \
             mock.patch.object(G, "running_agents", return_value={n: (n == "margo") for n in G.SUBS}), \
             mock.patch.object(G, "open_work", return_value={n: (5 if n == "margo" else 0) for n in G.SUBS}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            G.main([])
        return {r["agent"]: r for r in json.loads(out.getvalue())["rows"]}

    def test_margo_19_orai_feladattal_csendes_de_latszik(self):
        rows = self._main({"margo": "19:00"})
        self.assertFalse(rows["margo"]["alert"])
        self.assertEqual(rows["margo"]["scheduled_today"], "19:00")

    def test_margo_utemezes_nelkul_riaszt(self):
        rows = self._main({})
        self.assertTrue(rows["margo"]["alert"])
        self.assertIsNone(rows["margo"]["scheduled_today"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
