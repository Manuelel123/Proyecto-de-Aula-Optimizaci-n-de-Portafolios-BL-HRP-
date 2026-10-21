"""Golden tests: calculation outputs must match ``tests/golden/values.json``.

See ``golden_cases.py`` for the cases and how to regenerate the file after an
intended numerical change.
"""

import json
import math
import unittest

from golden_cases import CASES, GOLDEN_FILE

REL_TOLERANCE = 1e-9
ABS_TOLERANCE = 1e-12


def differences(expected, actual, path="") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        found = []
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                found.append(f"{path}/{key}: missing")
            elif key not in expected:
                found.append(f"{path}/{key}: unexpected")
            else:
                found.extend(differences(expected[key], actual[key], f"{path}/{key}"))
        return found
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{path}: length {len(actual)} != {len(expected)}"]
        return [d for i, (e, a) in enumerate(zip(expected, actual)) for d in differences(e, a, f"{path}[{i}]")]
    if isinstance(expected, float) and isinstance(actual, (int, float)) and not isinstance(actual, bool):
        if math.isclose(expected, actual, rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE):
            return []
        return [f"{path}: {actual!r} != {expected!r}"]
    if expected != actual:
        return [f"{path}: {actual!r} != {expected!r}"]
    return []


class GoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))

    def test_every_case_has_golden_values(self):
        self.assertEqual(sorted(CASES), sorted(self.golden))

    def test_cases_match_golden_values(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                actual = json.loads(json.dumps(case()))
                found = differences(self.golden[name], actual)
                self.assertEqual(found, [], f"{name}: " + "; ".join(found[:10]))


if __name__ == "__main__":
    unittest.main()
