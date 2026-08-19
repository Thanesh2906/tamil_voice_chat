import unittest

from jarvis.monitoring import MonitoringTools, validate_project, validate_window


class Backend:
    def __init__(self):
        self.calls = []

    def query(self, template, labels, window):
        self.calls.append((template, labels, window))
        return 1.0


class MonitoringTests(unittest.TestCase):
    def test_only_controlled_inputs_reach_backend(self):
        backend = Backend()
        result = MonitoringTools(backend).get_project_summary("clinic_api", "15m")
        self.assertEqual(result["container_cpu_percent"], 1.0)
        self.assertTrue(all(call[1] == {"project_id": "clinic_api"} for call in backend.calls))

    def test_promql_injection_inputs_rejected(self):
        with self.assertRaises(ValueError):
            validate_project('x"} or on() vector(1)')
        with self.assertRaises(ValueError):
            validate_window("1y")


if __name__ == "__main__":
    unittest.main()
