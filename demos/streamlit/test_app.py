"""Self-contained tests; never contacts the PoG API, chain or payment services."""

import importlib.util
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree

from streamlit.testing.v1 import AppTest

APP = Path(__file__).with_name("app.py")
spec = importlib.util.spec_from_file_location("pog_showcase", APP)
demo = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = demo
spec.loader.exec_module(demo)


class SnapshotTests(unittest.TestCase):
    def test_review_cannot_release_or_partially_pay(self):
        state = demo.snapshot("review")
        self.assertEqual(state.received, 8)
        self.assertFalse(state.receipt_confirmed)
        self.assertFalse(state.human_approved)
        self.assertFalse(state.can_release)
        self.assertEqual((state.released, state.supplier_paid), (0, 0))
        self.assertEqual(state.reserved, demo.INVOICE)

    def test_ready_requires_receipt_and_human_and_moves_nothing(self):
        state = demo.snapshot("ready")
        self.assertEqual(state.received, 10)
        self.assertTrue(state.receipt_confirmed and state.human_approved)
        self.assertTrue(state.can_release)
        self.assertEqual(state.released, 0)

    def test_release_and_supplier_payment_are_distinct(self):
        state = demo.snapshot("released")
        self.assertEqual(state.released, demo.INVOICE)
        self.assertEqual(state.supplier_paid, 0)
        self.assertEqual(state.locked, 2500)
        self.assertFalse(state.can_release)

    def test_funds_conservation_all_snapshots(self):
        for scene in demo.SCENES:
            with self.subTest(scene=scene):
                state = demo.snapshot(scene)
                self.assertEqual(state.locked + state.released, demo.FUNDING)
                self.assertLessEqual(state.reserved, state.locked)
                self.assertLessEqual(state.supplier_paid, state.released)
                self.assertIn(state.released, (0, demo.INVOICE))
        self.assertEqual(demo.snapshot("settled").supplier_paid, demo.INVOICE)

    def test_unknown_scene_is_rejected(self):
        with self.assertRaises(ValueError):
            demo.snapshot("real_payment")

    def test_fan_image_is_standalone_svg(self):
        self.assertEqual(ElementTree.fromstring(demo.FAN_SVG).tag, "{http://www.w3.org/2000/svg}svg")

    def test_only_fictional_evidence_and_no_fake_transaction_hash(self):
        before = demo.evidence_items(demo.snapshot("review"))
        after = demo.evidence_items(demo.snapshot("ready"))
        self.assertEqual(before[:2], after[:2])
        self.assertNotEqual(before[2], after[2])
        for _, record_id, text in before + after:
            self.assertTrue(record_id.startswith("DEMO-"))
            self.assertIn("DEMO ONLY", text)
            self.assertEqual(len(demo.sha256(text.encode()).hexdigest()), 64)


class RenderingTests(unittest.TestCase):
    def test_all_views_and_scenarios_render(self):
        app = AppTest.from_file(str(APP)).run()
        self.assertEqual(len(app.exception), 0)
        for view in demo.VIEWS:
            for scene in demo.SCENES:
                with self.subTest(view=view, scene=scene):
                    app.radio(key="view").set_value(view)
                    app.selectbox(key="scene").set_value(scene)
                    app.run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(app.session_state["scene"], scene)
                    html = " ".join(node.value for node in app.get("html"))
                    self.assertIn("DEMO DATA ONLY", html)
                    self.assertIn("No AI inference", html)
                    if view == "donor" and scene == "review":
                        self.assertIn("Review required", html)
                        self.assertIn("No money is released", html)
                    if view == "approval":
                        self.assertIn("Scripted AI finding", html)
                    if view == "how":
                        self.assertIn("Files off-chain", html)

    def test_unknown_query_values_fall_back(self):
        app = AppTest.from_file(str(APP))
        app.query_params["scene"] = "invalid"
        app.query_params["view"] = "invalid"
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state["scene"], "review")
        self.assertEqual(app.session_state["view"], "donor")


if __name__ == "__main__":
    unittest.main()
