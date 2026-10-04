from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/build-light-macos-arm64.yml'

class WorkflowTests(unittest.TestCase):
    def test_workflow_can_only_be_started_manually(self):
        self.assertTrue(WORKFLOW.is_file(), 'manual build workflow is missing')
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['on']), {'workflow_dispatch'})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})

    def test_job_environment_does_not_use_unavailable_runner_context(self):
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        for value in workflow['jobs']['build'].get('env', {}).values():
            self.assertNotIn('runner.', str(value))

    def test_native_intermediates_never_enter_upload_paths(self):
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        ids = {s.get('id'): s for s in workflow['jobs']['build']['steps']}
        self.assertNotIn('--dir', ids['build']['run'], 'Explicit native DMG/ZIP targets are not overridden by --dir')
        self.assertIn('"$GITHUB_WORKSPACE/personal-signed-packages"', ids['build']['run'])
        self.assertIn('--verify', ids['verify']['run'])
        self.assertIn('"$GITHUB_WORKSPACE/personal-signed-packages"', ids['verify']['run'])
        self.assertNotIn('release.glob', ids['verify']['run'])
        self.assertEqual(ids['artifacts']['with']['path'].splitlines(),
                         ['artifacts/Hermes-Light-arm64.dmg', 'artifacts/Hermes-Light-arm64.zip',
                          'artifacts/SHA256SUMS.txt', 'artifacts/BUILD-INFO.json'])
        self.assertEqual(workflow['on']['workflow_dispatch']['inputs']['source_commit']['default'],
                         '93782e9cc3b6d40eb28b7545708a126538127a60')

    def test_light_build_produces_checked_downloads(self):
        import re
        import subprocess
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        job = workflow['jobs']['build']
        steps = job['steps']
        ids = {step.get('id'): step for step in steps}
        self.assertIn('build', ids, 'native Light build step is missing')
        self.assertEqual(job['runs-on'], 'macos-15')
        source = workflow['on']['workflow_dispatch']['inputs']['source_commit']['default']
        self.assertRegex(source, r'^[0-9a-f]{40}$')
        self.assertIn('--variant light', ids['build']['run'])
        self.assertIn('--commit "$SOURCE_COMMIT"', ids['build']['run'])
        self.assertNotIn('--dir', ids['build']['run'])
        self.assertNotIn('-c.mac.identity=', ids['build']['run'])
        self.assertIn('sign-and-package.mjs', ids['build']['run'])
        self.assertIn('json.load(open(sys.argv[1]))["node"]', ids['build']['run'])
        self.assertIn('"$node_bin" "$GITHUB_WORKSPACE/build-tools/scripts/sign-and-package.mjs"', ids['build']['run'])
        self.assertTrue((ROOT / 'scripts/sign-and-package.mjs').is_file())
        own_checkout = next(step for step in steps if step.get('with', {}).get('path') == 'build-tools')
        self.assertEqual(own_checkout['with']['persist-credentials'], 'false')
        self.assertEqual(own_checkout['with']['ref'], '${{ github.sha }}')
        self.assertIn('sign-and-package.mjs" --verify', ids['verify']['run'])
        verification = (ROOT / 'scripts/sign-and-package.mjs').read_text()
        self.assertIn('Signature=adhoc', verification)
        self.assertIn("assert.equal(stamp.payload, 'light'", verification)
        self.assertIn('agent-payload', verification)
        self.assertIn('codesign', verification)
        self.assertIn('lipo', verification)
        self.assertIn('SHA256SUMS.txt', verification)
        self.assertIn("['attach', '-readonly', '-nobrowse'", verification)
        self.assertEqual(ids['artifacts']['with']['if-no-files-found'], 'error')
        self.assertIn('artifacts/Hermes-Light-arm64.dmg', ids['artifacts']['with']['path'])
        self.assertIn('artifacts/Hermes-Light-arm64.zip', ids['artifacts']['with']['path'])
        for step in steps:
            if 'uses' in step:
                self.assertRegex(step['uses'], r'^[^@]+@[0-9a-f]{40}$')
            if 'run' in step:
                self.assertNotIn('${{', step['run'], 'inputs must be passed through env, not shell interpolation')
                proc = subprocess.run(['bash', '-n'], input=step['run'], text=True, capture_output=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                heredocs = re.findall(r"python(?:3)? - <<'PY'\n(.*?)\nPY", step['run'], re.S)
                for code in heredocs:
                    compile(code, '<workflow python>', 'exec')
        self.assertNotIn('secrets.', WORKFLOW.read_text())
        self.assertNotIn('write', str(workflow['permissions']))

if __name__ == '__main__':
    unittest.main()
