import importlib.util
import io
from pathlib import Path
import tarfile
import unittest

spec = importlib.util.spec_from_file_location('builder', Path(__file__).parents[1] / 'scripts/build_release.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PackageTest(unittest.TestCase):
    def test_debian_archive_declares_parents_before_files(self):
        data = builder.archive([('usr/lib/systemd/user/hyperx-rgb.service', b'test', 0o644)])
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            members = tar.getmembers()
        self.assertEqual([m.name for m in members], ['usr', 'usr/lib', 'usr/lib/systemd', 'usr/lib/systemd/user', 'usr/lib/systemd/user/hyperx-rgb.service'])
        self.assertTrue(all(m.isdir() for m in members[:-1]))
        self.assertTrue(all(m.uid == 0 and m.gid == 0 for m in members))

    def test_archive_is_reproducible(self):
        inputs = [('app.py', b'example', 0o644)]
        self.assertEqual(builder.archive(inputs), builder.archive(inputs))
