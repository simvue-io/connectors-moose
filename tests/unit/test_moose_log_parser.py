from simvue_moose.connector import MooseRun
import simvue
import threading
import time
import tempfile
from unittest.mock import patch
import uuid
import pathlib

def mock_moose_process(self, *_, **__):
    """
    Mock process which writes each entry in the example log file, line by line
    """
    temp_logfile = tempfile.NamedTemporaryFile(mode="w",prefix="moose_test_", suffix=".txt", buffering=1)
    self._results_prefix = pathlib.Path(temp_logfile.name).name.split(".")[0]
    self._output_dir_path = pathlib.Path(temp_logfile.name).parent

    def write_to_log():
        log_file = pathlib.Path(__file__).parent.joinpath("example_data", "moose_log.txt").open("r")
        for line in log_file:
            temp_logfile.write(line)
            time.sleep(0.01)
        temp_logfile.flush()
        time.sleep(1)
        temp_logfile.close()
        self._trigger.set()
        return
    thread = threading.Thread(target=write_to_log)
    thread.start()
    
@patch.object(MooseRun, '_moose_input_parser', lambda *_, **__: None)   
@patch.object(MooseRun, 'add_process', mock_moose_process)
def test_moose_log_parser(folder_setup):    
    """
    Check that Events and Metrics are correctly parsed from the MOOSE log file and uploaded.
    """
    name = 'test_moose_log_parser-%s' % str(uuid.uuid4())
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        run.launch(
            moose_application_path=pathlib.Path(__file__),
            moose_file_path=pathlib.Path(__file__),
        )
    client = simvue.Client()
    # Check messages correctly extracted from log and added as events
    events = client.get_events(run_id)
    assert events[2]["message"] == "Time Step 1, time = 1, dt = 1"
    assert events[3]["message"] == " Solve Converged!"
    assert events[5]["message"] == " Total Nonlinear Iterations: 3."
    assert events[6]["message"] == " Total Linear Iterations: 112."
    assert events[-2]["message"] == "Terminator 'handle-too-hot' is causing the execution to terminate."
    
    # Check that total linear and nonlinear events from each step uploaded as metrics
    # Correct answers calculated manually from log file
    metrics = client.get_metric_values(metric_names=["total_linear_iterations", "total_nonlinear_iterations"], run_ids=[run_id,], output_format="dict", xaxis="step")
    assert list(metrics['total_linear_iterations'].values()) == [112.0, 107.0]
    assert list(metrics['total_nonlinear_iterations'].values()) == [3.0, 3.0]
    
    # Check that reason for termination is added as metadata and tag, and run is set to terminated state
    run_data = client.get_run(run_id)
    assert run_data.metadata["handle-too-hot"] == True
    assert "handle-too-hot" in run_data.tags
    assert run_data.status == "terminated"
    
        
        
        