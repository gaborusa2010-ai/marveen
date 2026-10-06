#!/usr/bin/env python3
"""Hermetic tests for the evening open-card completion reporter."""
import importlib.util
import json
import os
import tempfile
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "kanban-kesz-nyitott.py")


def load():
    spec = importlib.util.spec_from_file_location("kanban_kesz_nyitott", SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TRUE_FIXTURES = [
    (428, "#428 KESZ, elesben (build + dashboard restart 19:28, allando engedely)"),
    (427, "[Cody] #427 kesz, NINCS commit (gazda: COMMIT NO), build/restart nem kell"),
    (421, "[x] VAR: a gazda dontese. Anorak munkaja kesz (komment 1111/1112 + kiegeszites 1140/1141)"),
    (50, "KESZ. A kartya lezarhato, minden teszt zold."),
    (51, "Kész, élesben van."),
    (52, "[LEZART] #52 done, a gazda megkapta."),
    # The marker quoted in the BODY must not silence the report (#434, 2026-10-05).
    # (A "folyamatban" word inside the first 160 characters still vetoes; that is deliberate.)
    (434, "[LEZART] #434 KESZ, elesben. Commit NEM.\n\nMi keszult:\n- scripts/kanban-kesz-nyitott.py: a nyitott"
          " kartyak UTOLSO kommentjet nezi, es Marveennek kuld egy uzenetet.\n"
          "- Hamis pozitiv ellen: \"[FOLYAMATBAN]\", reszlepes nem talal."),
]
FALSE_FIXTURES = [
    (31, "STEP 3 DONE 10:37. 16 Codex sheets in work/shorts/forgatas/ra"),
    (31, "Roxy 2026-10-04 délelőtt: 4. lépés v2 KÉSZ, mozgó próba-Short (a gazda \"ez meg nem mozgo kep\")"),
    (31, "Masodik AI-video proba KESZ (gazda igen: msg 245). video/p2-motion2-v1.mp4"),
    (31, "AI-video minta KESZ (gazda igen: Roxy-bot msg 234). work/shorts/proba"),
    (31, "Samples done 10:25-10:31: raw/lime-turnaround.png (v2, cap-bac"),
    (31, "Roxy 2026-10-03 este: KONTEXTUS-CSOMAG KESZ, generalas nelkul: agents/roxy/work/shorts/csomag"),
    (286, "2026-10-01 [FOLYAMATBAN] Prototipus kesz: agents/prospero/rule1/ (rule1.py motor, METHOD.md)"),
    (286, "Teljes egyezes a szamolassal egyezik. Jegyzet e6f8bf ezzel lezarhato."),
    (285, "Prospero elso lepese kesz (msg 7291, riport agents/prospero/riportok/2026-10-01)"),
    (196, "WAITING: a sajat lepes (386 SA103F-vaz) kesz; a tobbi a gazda anyagara var (384, 385, 387)."),
    (209, "## 2026-09-24 09:2x -- A KESZLET UJRAEPITVE AZ UJ, TAGABB MERCEVEL (sajat lekeres)"),
    (209, "Keszlet feltoltve (nem darab-kikuldes). A kimeno darab holnap."),
    (66, "[CODY, 2026-09-28, 1. RETEG KESZ: tema-rendszer] Ag feat/theme-system (worktree pr"),
    (170, "2026-09-18 19:1x -- AZ APP-OLDALI SZERZODES KESZ, a kartya masik fele NEM az enyem."),
    (352, "[LEZART] 2. lepes (#401), Claude Marketplace: kesz Xero/HMRC/UK banki plugin a hivatalos katalogusban"),
    (60, "Nem kész még, holnap folytatom."),
    (61, "Majdnem kész, a teszt hiányzik."),
    (62, "Kész a teszt, de a build még hátra van."),
    (63, "#401 kesz, ez a masik kartyan van."),
]


class ClassifierTests(unittest.TestCase):
    def test_real_positive_fixtures(self):
        module = load()
        for seq, text in TRUE_FIXTURES:
            with self.subTest(text=text):
                self.assertTrue(module.is_done_report(text, seq))

    def test_real_negative_fixtures(self):
        module = load()
        for seq, text in FALSE_FIXTURES:
            with self.subTest(text=text):
                self.assertFalse(module.is_done_report(text, seq))


class MainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.module = load()
        self.module.STATE = os.path.join(self.temp.name, "state.json")
        self.cards = [{"id": 7, "seq": 77, "title": "Finished feature",
                       "status": "waiting", "assignee": "cody",
                       "archived_at": None, "wait_kind": "owner", "wait_note": "x"}]
        self.comments = {7: [{"id": 9, "card_id": 7, "author": "cody",
                              "content": "#77 kesz", "created_at": 1000}]}
        self.sent = []
        self.module.api_get = self.api_get
        self.module.send = lambda text: self.sent.append(text) or True

    def tearDown(self):
        self.temp.cleanup()

    def api_get(self, path):
        if path == "/api/kanban":
            return {"cards": self.cards}
        card_id = int(path.split("/")[3])
        return {"comments": self.comments[card_id]}

    def test_latest_comment_only(self):
        self.comments[7].append({"id": 10, "author": "cody", "content": "still working",
                                 "created_at": 1001})
        self.assertEqual(self.module.main([]), 0)
        self.assertEqual(self.sent, [])

    def test_done_and_archived_cards_are_skipped(self):
        self.cards.extend([
            {"id": 8, "seq": 78, "status": "done", "archived_at": None},
            {"id": 9, "seq": 79, "status": "open", "archived_at": 123},
        ])
        self.assertEqual(self.module.main([]), 0)
        self.assertEqual(len(self.sent), 1)

    def test_state_deduplicates_second_run_and_message_has_fields(self):
        self.assertEqual(self.module.main([]), 0)
        self.assertIn("#77", self.sent[0])
        self.assertIn("waiting (owner)", self.sent[0])
        self.assertEqual(self.module.main([]), 0)
        self.assertEqual(len(self.sent), 1)

    def test_send_failure_does_not_write_state(self):
        self.module.send = lambda text: False
        self.assertEqual(self.module.main([]), 2)
        self.assertFalse(os.path.exists(self.module.STATE))

    def test_dry_run_does_not_send_or_write_state(self):
        self.assertEqual(self.module.main(["--dry-run"]), 0)
        self.assertEqual(self.sent, [])
        self.assertFalse(os.path.exists(self.module.STATE))

    def test_api_error_does_not_send(self):
        self.module.api_get = lambda path: (_ for _ in ()).throw(OSError("down"))
        self.assertEqual(self.module.main([]), 1)
        self.assertEqual(self.sent, [])
        self.assertFalse(os.path.exists(self.module.STATE))


if __name__ == "__main__":
    unittest.main(verbosity=2)
