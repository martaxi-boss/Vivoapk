import json,os,stat,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitor_transaction import configure
class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.env=self.root/'native.env';self.key=self.root/'monitor.key';self.back=self.root/'backup';self.back.mkdir(mode=0o700);self.old=b'PRIVATE_MEDIA_KEY=synthetic-existing\n';self.new=self.old+b'ENTITLEMENT_MONITOR_KEY=synthetic-independent\n';self.env.write_bytes(self.old);self.env.chmod(0o600);self.events=[]
    def tearDown(self):self.tmp.cleanup()
    def apply(self,verify):return configure(self.env,self.key,self.back,self.old,self.new,(0o600,os.getuid(),os.getgid()),b'synthetic-independent',os.getuid(),os.getgid(),lambda:self.events.append('restart'),verify)
    def test_commits_only_after_postflight_and_retains_private_backup(self):
        def verify():
            self.assertEqual(self.env.read_bytes(),self.new);self.assertEqual(self.key.read_bytes(),b'synthetic-independent');self.assertEqual(stat.S_IMODE(self.key.stat().st_mode),0o400);self.events.append('verify')
        self.assertEqual(self.apply(verify)['transaction_result'],'committed');self.assertEqual(self.events,['restart','verify']);self.assertEqual((self.back/'native-media.env.before').read_bytes(),self.old)
        self.assertNotIn('synthetic', (self.back/'journal.json').read_text())
    def test_postflight_failure_restores_exact_env_and_removes_only_created_key(self):
        def fail():raise RuntimeError('synthetic-check-failure')
        result=self.apply(fail);self.assertTrue(result['automatic_rollback']);self.assertEqual(self.env.read_bytes(),self.old);self.assertFalse(self.key.exists());self.assertEqual(self.events,['restart','restart']);self.assertEqual(json.loads((self.back/'journal.json').read_text())['phase'],'rolled_back')
    def test_existing_key_blocks_without_changes(self):
        self.key.write_bytes(b'existing')
        with self.assertRaises(RuntimeError):self.apply(lambda:None)
        self.assertEqual(self.key.read_bytes(),b'existing');self.assertEqual(self.events,[])
    def test_unrelated_env_edit_is_preserved_and_recovery_requires_review(self):
        def fail():self.env.write_bytes(b'unrelated');raise RuntimeError('failure')
        with self.assertRaisesRegex(RuntimeError,'unrelated_env_change'):self.apply(fail)
        self.assertEqual(self.env.read_bytes(),b'unrelated')
    def test_unrelated_key_edit_is_preserved(self):
        def fail():self.key.chmod(0o600);self.key.write_bytes(b'unrelated');raise RuntimeError('failure')
        with self.assertRaisesRegex(RuntimeError,'unrelated_key_change'):self.apply(fail)
        self.assertEqual(self.key.read_bytes(),b'unrelated')
if __name__=='__main__':unittest.main()
