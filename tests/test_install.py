import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('installer', Path(__file__).parents[1] / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTest(unittest.TestCase):
    def test_systemd_path_escaping(self):
        self.assertEqual(installer.unit_quote('/home/a b/100%/$app.py'), '"/home/a b/100%%/$$app.py"')

    def test_desktop_path_escaping(self):
        self.assertEqual(installer.desktop_quote('/home/a b/$x'), '"/home/a b/\\$x"')
