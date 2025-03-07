from simvue_moose.connector import MooseRun
from simvue.api.objects.run import Run
import pathlib
from unittest.mock import patch, PropertyMock
import tempfile
import uuid
import simvue
import filecmp
import time
import threading

def mock_moose_process(self, *_, **__):
    # No need to do anything this time, just set termination trigger
    self._trigger.set()
    return True

def mock_input_parser(self, *_, **__):
    self._output_dir_path = str(pathlib.Path(__file__).parent.joinpath("example_data", "moose_outputs"))
    self._results_prefix = "moose_test"

@patch.object(MooseRun, '_moose_input_parser', mock_input_parser)
@patch.object(MooseRun, 'add_process', mock_moose_process)
def test_moose_file_upload(folder_setup):
    """
    Check that Exodus file is correctly uploaded as an artifact once simulation is complete.
    """    
    name = 'test_moose_file_upload-%s' % str(uuid.uuid4())
    temp_dir = tempfile.TemporaryDirectory(prefix="moose_test")
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        run.launch(
            moose_application_path=pathlib.Path(__file__),
            moose_file_path=pathlib.Path(__file__),
        )
        
        client = simvue.Client()
        
        # Retrieve Exodus and CSV file from server and compare with local copies
        client.get_artifacts_as_files(run_id, "output", temp_dir.name)
        comparison = filecmp.dircmp(pathlib.Path(__file__).parent.joinpath("example_data", "moose_outputs"), temp_dir.name)
        assert not (comparison.diff_files or comparison.left_only or comparison.right_only)
        
def mock_aborted_moose_process(self, *_, **__):
    """
    Mock a long running MOOSE process which is aborted by the server
    """ 
    def aborted_process():
        """
        Long running process which should be interrupted at the next heartbeat
        """
        self._heartbeat_interval = 2
        time_elapsed = 0
        while time_elapsed < 30:
            if self._alert_raised_trigger.is_set():
                break
            time.sleep(1)
            time_elapsed += 1
        self._trigger.set()
        
    thread = threading.Thread(target=aborted_process)
    thread.start()

def abort(self):
    """
    Instead of making an API call to the server, just sleep for 1s and return True to indicate an abort has been triggered
    """
    time.sleep(2)
    return True   

@patch.object(MooseRun, '_moose_input_parser', mock_input_parser)
@patch.object(MooseRun, 'add_process', mock_aborted_moose_process)
@patch.object(Run, 'abort_trigger', abort)    
def test_moose_file_upload_after_abort(folder_setup):
    """
    Check that outputs are uploaded if the simulation is aborted early by Simvue
    """
    name = 'test_moose_file_upload_after_abort-%s' % str(uuid.uuid4())
    temp_dir = tempfile.TemporaryDirectory(prefix="moose_test")
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        run.launch(
            moose_application_path=pathlib.Path(__file__),
            moose_file_path=pathlib.Path(__file__),
        )
    
    client = simvue.Client()
    # Check that run was aborted correctly, and did not exist for longer than 10s
    runtime = client.get_run(run_id).runtime
    assert runtime.tm_sec < 30
    
    # Check files correctly uploaded after an abort
    client.get_artifacts_as_files(run_id, "output", temp_dir.name)
    comparison = filecmp.dircmp(pathlib.Path(__file__).parent.joinpath("example_data", "moose_outputs"), temp_dir.name)
    assert not (comparison.diff_files or comparison.left_only or comparison.right_only)
    