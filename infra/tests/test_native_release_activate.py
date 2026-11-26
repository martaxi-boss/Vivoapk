"""Host adapter tests: no real systemctl, credentials or database writes."""
import os
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock, call
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import native_release_activate as a


class ActivationAdapterTests(unittest.TestCase):
    def test_release_pin(self):
        a.pin()
        with patch.object(a.stage,"SOURCE_SHA","0"*40):
            with self.assertRaisesRegex(a.Blocked,"activation_pin_mismatch"):
                a.pin()

    def test_stop_order_and_confirmation(self):
        with patch.object(a,"ctl") as ctl, patch.object(a,"wait_until") as wait:
            a.stop_pair()
            self.assertEqual(ctl.call_args_list,[call("stop","--no-block",a.pf.SERVER),
                call("stop","--no-block",a.pf.BACKEND)])
            self.assertEqual(wait.call_count,2)

    def test_start_order(self):
        with patch.object(a,"ctl") as ctl, patch.object(a,"wait_until") as wait:
            a.start_pair()
            self.assertEqual(ctl.call_args_list,[call("start","--no-block",a.pf.BACKEND),
                call("start","--no-block",a.pf.SERVER)])
            self.assertEqual(wait.call_count,2)
            self.assertIs(wait.call_args_list[-1].args[0], a.jellyfin_ready)

    def test_public_socket_alone_never_ready(self):
        with patch.object(a,"ready",return_value=True),patch.object(a,"jellyfin_startup_complete",return_value=False):
            self.assertFalse(a.jellyfin_ready())

    def test_current_invocation_completion_required(self):
        invocation = "a"*32
        fields={"MainPID":"1234","InvocationID":invocation}
        record={"_PID":"1234","_SYSTEMD_INVOCATION_ID":invocation,
                "MESSAGE":"[INF] Main: Startup complete 0:00:07.1"}
        result=Mock(returncode=0,stdout=json.dumps(record))
        with patch.object(a.pf,"prop",side_effect=lambda u,k:fields[k]),patch.object(a.subprocess,"run",return_value=result):
            self.assertTrue(a.jellyfin_startup_complete())
            record["_SYSTEMD_INVOCATION_ID"]="b"*32
            result.stdout=json.dumps(record)
            self.assertFalse(a.jellyfin_startup_complete())
            record["_SYSTEMD_INVOCATION_ID"]=invocation
            record["_PID"]="1222"
            result.stdout=json.dumps(record)
            self.assertFalse(a.jellyfin_startup_complete())
            record["_PID"]="1234"
            record["MESSAGE"]="Application startup complete"
            result.stdout=json.dumps(record)
            self.assertFalse(a.jellyfin_startup_complete())
            result.stdout=""
            self.assertFalse(a.jellyfin_startup_complete())

    def test_invocation_changes_after_completion_read(self):
        invocation="a"*32
        record={"_PID":"1234","_SYSTEMD_INVOCATION_ID":invocation,
                "MESSAGE":"Startup complete 0:00:07"}
        with patch.object(a.pf,"prop",side_effect=["1234",invocation,"1235"]), \
             patch.object(a.subprocess,"run",return_value=Mock(returncode=0,stdout=json.dumps(record))):
            self.assertFalse(a.jellyfin_startup_complete())

    def test_stopped_requires_no_control_or_main_process(self):
        fields={"ActiveState":"inactive","MainPID":"0","ControlPID":"0","ControlGroup":""}
        with patch.object(a.pf,"prop",side_effect=lambda u,k:fields[k]):
            self.assertTrue(a.stopped(a.pf.SERVER))
            fields["ControlPID"]="77"
            self.assertFalse(a.stopped(a.pf.SERVER))
            fields["ControlPID"]="0"
            fields["ControlGroup"]="/system.slice/"+a.pf.SERVER
            self.assertFalse(a.stopped(a.pf.SERVER))

    def test_nonroot_rejected_before_activation(self):
        with patch.object(a.os,"geteuid",return_value=1000),patch.object(a.host,"check_host") as check:
            with self.assertRaisesRegex(a.Blocked,"activation_root_required"):
                a.execute("activate")
            check.assert_not_called()

    def test_postflight_failure_not_reported_success(self):
        with patch.object(a.sys,"argv",["activate","activate"]),patch.object(a.signal,"signal"),              patch.object(a,"execute",return_value={"installed":False,"rollback_verified":True}):
            self.assertEqual(a.main(),1)

    def test_existing_dropin_directory_cannot_be_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"dropin"
            p.mkdir(mode=0o777)
            p.chmod(0o777)
            with self.assertRaisesRegex(a.Blocked,"activation_directory_not_trusted"):
                a.root_dir(p,0o755)


if __name__=="__main__":
    unittest.main()
