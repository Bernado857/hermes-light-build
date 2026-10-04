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
        self.assertIn('-c.mac.identity=-', ids['build']['run'])
        self.assertIn('stamp["payload"] == "light"', ids['verify']['run'])
        self.assertIn('agent-payload', ids['verify']['run'])
        self.assertIn('codesign', ids['verify']['run'])
        self.assertIn('lipo', ids['verify']['run'])
        self.assertIn('SHA256SUMS.txt', ids['verify']['run'])
        self.assertEqual(ids['artifacts']['with']['if-no-files-found'], 'error')
        self.assertIn('*.dmg', ids['artifacts']['with']['path'])
        self.assertIn('*.zip', ids['artifacts']['with']['path'])
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
