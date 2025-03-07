from simvue_template.connector import YourRun
import simvue
import threading
import time
import tempfile
from unittest.mock import patch
import uuid
import pathlib
import pytest

def mock_process(self, *_, **__):
    """
    Mock process for creating log file
    """
    temp_logfile = tempfile.TemporaryFile()
    
    def write_to_log():
        log_file = pathlib.Path(__file__).parent.joinpath("example_data", "example_log.txt").open("r")
        for line in log_file:
            temp_logfile.write(line)
            temp_logfile.flush()
            time.sleep(0.01)
        time.sleep(1)
        temp_logfile.close()
        self._trigger.set()
        return
    
    thread = threading.Thread(target=write_to_log)
    thread.start()
    
@patch.object(YourRun, 'add_process', mock_process)
def test_fds_log_parser(folder_setup):
    """
    Check that values from log file are uploaded correctly.
    """ 
    name = 'test_fds_log_parser-%s' % str(uuid.uuid4())
    with YourRun() as run:
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        run.launch()
        
    client = simvue.Client()
    
    # Check stuff has been uploaded correctly, eg
    metrics_names = client.get_metrics_names(run_id)
    assert sum(1 for name in metrics_names) == 1