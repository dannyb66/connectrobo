import ast
import json
import unittest
from pathlib import Path
from safe_sample import inspect_html, validate_config

ROOT = Path(__file__).parent


class SafetyTests(unittest.TestCase):
    def test_fixture_and_legacy_selector_gap(self):
        report = inspect_html((ROOT / "fixture.html").read_text())
        self.assertEqual(report["buttons"], {"connect": 2, "follow": 1, "send": 1, "next": 1, "other": 0})
        self.assertEqual(report["legacy_exact_text_matches"]["connect"], 1)
        self.assertEqual(report["actions_executed"], 0)

    def test_fail_closed_configuration(self):
        good = json.loads((ROOT / "safe_config.json").read_text())
        validate_config(good)
        for key, value in {"mode": "live", "credentials": "secret", "allowed_mutations": ["connect"], "external_navigation": True, "max_pages": 2}.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_config(dict(good, **{key: value}))

    def test_profile_data_never_emitted(self):
        report = json.dumps(inspect_html('<button aria-label="Connect with PRIVATE_NAME">Connect</button><p>PRIVATE_EMAIL</p>'))
        self.assertNotIn("PRIVATE", report)

    def test_unknown_controls_not_executed(self):
        report = inspect_html('<button>Message</button><button>Email</button><button>  CONNECT  </button>')
        self.assertEqual(report["buttons"]["other"], 2)
        self.assertEqual(report["buttons"]["connect"], 1)
        self.assertEqual(report["external_requests"], 0)

    def test_no_network_or_browser_imports(self):
        tree = ast.parse((ROOT / "safe_sample.py").read_text())
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imports.update(alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names)
        self.assertEqual(imports, {"argparse", "json", "html.parser", "pathlib"})


if __name__ == "__main__":
    unittest.main()
