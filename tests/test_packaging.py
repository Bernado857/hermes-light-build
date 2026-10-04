from pathlib import Path
import json
import os
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/sign-and-package.mjs'


class PackagingTests(unittest.TestCase):
    def test_signing_policy_uses_adhoc_and_upstream_entitlements(self):
        self.assertTrue(SCRIPT.is_file(), 'separate ad-hoc packaging script is missing')
        code = '''
        import assert from 'node:assert/strict';
        import { signingOptions } from %s;
        const opts = signingOptions('/official/source', '/build/Test.app');
        assert.equal(opts.identity, '-');
        assert.equal(opts.identityValidation, false);
        assert.equal(opts.batchCodesignCalls, false);
        const main = opts.optionsForFile('/build/Test.app');
        const helper = opts.optionsForFile('/build/Test.app/Contents/Frameworks/Helper.app');
        assert.equal(main.hardenedRuntime, true);
        assert.equal(main.entitlements, '/official/source/apps/desktop/electron/entitlements.mac.plist');
        assert.equal(helper.entitlements, '/official/source/apps/desktop/electron/entitlements.mac.inherit.plist');
        ''' % json.dumps(SCRIPT.as_uri())
        proc = subprocess.run(['node', '--input-type=module', '-e', code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_packaging_requires_a_real_light_app_before_signing(self):
        self.assertTrue(SCRIPT.is_file(), 'separate ad-hoc packaging script is missing')
        proc = subprocess.run(['node', str(SCRIPT), str(ROOT), str(ROOT.parent / 'unused-packages')],
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('Expected one ARM64 app', proc.stderr)


FIXTURE = os.environ.get('HERMES_PACKAGING_FIXTURE')


@unittest.skipUnless(sys.platform == 'darwin' and FIXTURE, 'requires explicit synthetic macOS fixture')
class NativePackagingTests(unittest.TestCase):
    def setUp(self):
        assert FIXTURE is not None
        self.temp = tempfile.TemporaryDirectory(prefix='light-packaging-test-', dir=ROOT.parent)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.source = self.work / 'source'
        subprocess.run(['ditto', FIXTURE, str(self.source)], check=True, capture_output=True)
        modules = self.source / 'apps/desktop/node_modules'
        modules.symlink_to(Path(FIXTURE).parent / 'node_modules', target_is_directory=True)
        self.output = self.work / 'personal-signed-packages'
        self.commit = 'fixture-not-a-Hermes-installer'

    def run_node(self, *args):
        env = dict(os.environ, TMPDIR=str(self.work))
        env.pop('GITHUB_ENV', None)
        return subprocess.run(['node', str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=env)

    def test_only_isolated_postsign_packages_are_selected(self):
        release = self.source / 'apps/desktop/release'
        stale = release / 'stale-unsigned.zip'
        stale.write_bytes(b'NOT A SIGNED INSTALLER')
        intermediates = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in release.iterdir() if p.is_file()}
        proc = self.run_node(self.source, self.output)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(set(p.name for p in self.output.iterdir()),
                         {'Hermes-Light-arm64.dmg', 'Hermes-Light-arm64.zip', 'packages.json'})
        manifest = json.loads((self.output / 'packages.json').read_text())
        self.assertEqual(manifest['packages'], ['Hermes-Light-arm64.dmg', 'Hermes-Light-arm64.zip'])
        self.assertEqual(intermediates, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in release.iterdir() if p.is_file()})
        artifacts = self.work / 'artifacts'
        proc = self.run_node('--verify', self.source, self.output, self.commit, artifacts)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(set(p.name for p in artifacts.iterdir()),
                         {'Hermes-Light-arm64.dmg', 'Hermes-Light-arm64.zip',
                          'SHA256SUMS.txt', 'BUILD-INFO.json'})
        self.assertEqual(intermediates, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in release.iterdir() if p.is_file()})

    def test_distributed_copies_are_checked_before_metadata_and_publication(self):
        proc = self.run_node(self.source, self.output)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        artifacts = self.work / 'artifacts'
        proc = self.run_node('--verify', self.source, self.output, self.commit, artifacts)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(set(p.name for p in artifacts.iterdir()),
                         {'Hermes-Light-arm64.dmg', 'Hermes-Light-arm64.zip',
                          'SHA256SUMS.txt', 'BUILD-INFO.json'})
        report = json.loads((artifacts / 'BUILD-INFO.json').read_text())
        self.assertTrue(report['codesign_verified'])
        self.assertTrue(report['distributed_copies_verified'])
        self.assertEqual(set(report['verified_packages']),
                         {'Hermes-Light-arm64.dmg', 'Hermes-Light-arm64.zip'})
        self.assertFalse(report['apple_notarized'])
        self.assertFalse(report['installation_on_M5_verified'])
        self.assertEqual(report['commit'], self.commit)
        self.assertIn('Signature=adhoc', proc.stdout)
        self.assertIn('arm64', proc.stdout)
        for name in ('Hermes-Light-arm64.dmg', 'Hermes-Light-arm64.zip'):
            self.assertEqual((artifacts / name).read_bytes(), (self.output / name).read_bytes())


    def repack(self, app, suffix):
        target = self.output / ('Hermes-Light-arm64.' + suffix)
        target.unlink()
        if suffix == 'zip':
            subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(target)],
                           check=True, capture_output=True)
        else:
            staging = app.parent
            (staging / 'Applications').symlink_to('/Applications')
            subprocess.run(['hdiutil', 'create', '-format', 'UDZO', '-srcfolder', str(staging), str(target)],
                           check=True, capture_output=True)

    def test_rejects_resigned_distributed_identity_or_executable_changes(self):
        proc = self.run_node(self.source, self.output)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        originals = {suffix: (self.output / ('Hermes-Light-arm64.' + suffix)).read_bytes()
                     for suffix in ('zip', 'dmg')}
        for kind in ('stamp', 'executable'):
            for suffix in ('zip', 'dmg'):
                with self.subTest(kind=kind, package=suffix):
                    for ext, data in originals.items():
                        (self.output / ('Hermes-Light-arm64.' + ext)).write_bytes(data)
                    app = self.work / (kind + '-' + suffix) / 'PackagingProbe.app'
                    app.parent.mkdir()
                    subprocess.run(['ditto', str(self.source / 'apps/desktop/release/mac-arm64/PackagingProbe.app'),
                                    str(app)], check=True, capture_output=True)
                    if kind == 'stamp':
                        stamp = app / 'Contents/Resources/install-stamp.json'
                        changed = json.loads(stamp.read_text())
                        changed['identity'] = 'different-distributed-copy'
                        stamp.write_text(json.dumps(changed))
                    else:
                        code = self.work / (kind + '-' + suffix + '.c')
                        code.write_text('int main(void) { return 7; }\n')
                        subprocess.run(['clang', '-arch', 'arm64', str(code), '-o',
                                        str(app / 'Contents/MacOS/PackagingProbe')], check=True, capture_output=True)
                    subprocess.run(['codesign', '--force', '--sign', '-', '--deep', str(app)],
                                   check=True, capture_output=True)
                    self.repack(app, suffix)
                    artifacts = self.work / ('artifacts-' + kind + '-' + suffix)
                    proc = self.run_node('--verify', self.source, self.output, self.commit, artifacts)
                    self.assertNotEqual(proc.returncode, 0, 'Changed distributed identity/executable was published')
                    self.assertIn('Distributed ' + ('stamp' if kind == 'stamp' else 'executable') + ' mismatch', proc.stderr)
                    self.assertFalse(artifacts.exists(), 'Failed verification left publication files')


    def test_rejects_invalid_final_package_sets_and_paths(self):
        proc = self.run_node(self.source, self.output)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for kind in ('extra', 'traversal', 'symlink', 'missing', 'empty'):
            with self.subTest(kind=kind):
                invalid = self.work / ('invalid-' + kind)
                shutil.copytree(self.output, invalid)
                zipfile = invalid / 'Hermes-Light-arm64.zip'
                if kind == 'extra':
                    (invalid / 'native-intermediate.zip').write_bytes(b'stale')
                elif kind == 'traversal':
                    (invalid / 'packages.json').write_text(json.dumps(
                        {'packages': ['../native-intermediate.dmg', 'Hermes-Light-arm64.zip']}))
                elif kind == 'symlink':
                    zipfile.unlink()
                    zipfile.symlink_to(self.output / zipfile.name)
                elif kind == 'missing':
                    zipfile.unlink()
                else:
                    zipfile.write_bytes(b'')
                artifacts = self.work / ('artifacts-' + kind)
                proc = self.run_node('--verify', self.source, invalid, self.commit, artifacts)
                self.assertNotEqual(proc.returncode, 0, 'Invalid package set/path was published')
                self.assertFalse(artifacts.exists())
        proc = self.run_node(self.source, self.source / 'apps/desktop/release/final')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('outside official source', proc.stderr)
        proc = self.run_node(self.source, self.output)
        self.assertNotEqual(proc.returncode, 0, 'Stale output directory was reused')

    def test_each_package_rejects_bad_signature_and_light_identity(self):
        proc = self.run_node(self.source, self.output)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        originals = {suffix: (self.output / ('Hermes-Light-arm64.' + suffix)).read_bytes()
                     for suffix in ('zip', 'dmg')}
        cases = {'commit': 'Source identity mismatch', 'payload': 'Not a Light build',
                 'updateMechanism': 'Personal builds must use external updates',
                 'backend': 'Unexpected local backend payload', 'signature': 'not signed at all',
                 'architecture': 'Unexpected architectures'}
        for kind, message in cases.items():
            for suffix in ('zip', 'dmg'):
                with self.subTest(kind=kind, package=suffix):
                    for ext, data in originals.items():
                        (self.output / ('Hermes-Light-arm64.' + ext)).write_bytes(data)
                    app = self.work / (kind + '-' + suffix) / 'PackagingProbe.app'
                    app.parent.mkdir()
                    subprocess.run(['ditto', str(self.source / 'apps/desktop/release/mac-arm64/PackagingProbe.app'),
                                    str(app)], check=True, capture_output=True)
                    resources = app / 'Contents/Resources'
                    if kind in ('commit', 'payload', 'updateMechanism'):
                        stamp = resources / 'install-stamp.json'
                        changed = json.loads(stamp.read_text())
                        changed[kind] = 'WRONG'
                        stamp.write_text(json.dumps(changed))
                    elif kind == 'backend':
                        (resources / 'agent-payload').mkdir()
                    elif kind == 'architecture':
                        code = self.work / (kind + '-' + suffix + '.c')
                        code.write_text('int main(void) { return 0; }\n')
                        subprocess.run(['clang', '-arch', 'x86_64', str(code), '-o',
                                        str(app / 'Contents/MacOS/PackagingProbe')], check=True, capture_output=True)
                    if kind == 'signature':
                        subprocess.run(['codesign', '--remove-signature', str(app)], check=True, capture_output=True)
                    else:
                        subprocess.run(['codesign', '--force', '--sign', '-', '--deep', str(app)],
                                       check=True, capture_output=True)
                    self.repack(app, suffix)
                    artifacts = self.work / ('artifacts-' + kind + '-' + suffix)
                    proc = self.run_node('--verify', self.source, self.output, self.commit, artifacts)
                    self.assertNotEqual(proc.returncode, 0, 'Invalid distributed app was published')
                    self.assertIn(message, proc.stderr)
                    self.assertFalse(artifacts.exists())


    def test_rejects_unexpected_files_inside_each_final_installer(self):
        proc = self.run_node(self.source, self.output)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        originals = {suffix: (self.output / ('Hermes-Light-arm64.' + suffix)).read_bytes()
                     for suffix in ('zip', 'dmg')}
        for suffix in ('zip', 'dmg'):
            with self.subTest(package=suffix):
                for ext, data in originals.items():
                    (self.output / ('Hermes-Light-arm64.' + ext)).write_bytes(data)
                app = self.work / ('extra-' + suffix) / 'PackagingProbe.app'
                app.parent.mkdir()
                subprocess.run(['ditto', str(self.source / 'apps/desktop/release/mac-arm64/PackagingProbe.app'),
                                str(app)], check=True, capture_output=True)
                (app.parent / 'unsigned-intermediate.zip').write_bytes(b'unchecked')
                if suffix == 'zip':
                    target = self.output / 'Hermes-Light-arm64.zip'
                    target.unlink()
                    subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', str(app.parent), str(target)],
                                   check=True, capture_output=True)
                else:
                    self.repack(app, suffix)
                artifacts = self.work / ('artifacts-extra-' + suffix)
                proc = self.run_node('--verify', self.source, self.output, self.commit, artifacts)
                self.assertNotEqual(proc.returncode, 0, 'Unexpected installer payload was published')
                self.assertIn('Unexpected installer contents', proc.stderr)
                self.assertFalse(artifacts.exists())


if __name__ == '__main__':
    unittest.main()
