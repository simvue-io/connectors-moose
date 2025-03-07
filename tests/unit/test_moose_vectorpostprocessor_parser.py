from simvue_moose.connector import MooseRun
import simvue
import threading
import time
import tempfile
from unittest.mock import patch
import uuid
import pathlib
import shutil

def mock_vector_postprocessor(self, *_, **__):
    """
    Mock process for creating VectorPostProcessor output CSV files.
    There is one of these CSV files written all at once for each timestep.
    """
    def write_to_vector_pp():
        _timefile_lines = pathlib.Path(__file__).parent.joinpath("example_data", "moose_temps_time.csv").open("r").readlines()
        _timefile = pathlib.Path(self._output_dir_path).joinpath("moose_temps_time.csv").open("w", buffering=1)
        _timefile.write(_timefile_lines[0])
        for num in range(0,6,1):
            _timefile.write(_timefile_lines[num+1])
            shutil.copy(pathlib.Path(__file__).parent.joinpath("example_data", f"moose_temps_000{num}.csv"), pathlib.Path(self._output_dir_path).joinpath(f"moose_temps_000{num}.csv"))
            time.sleep(0.5)
        _timefile.close()
        self._trigger.set()
        return
    thread = threading.Thread(target=write_to_vector_pp)
    thread.start()
    
@patch.object(MooseRun, '_moose_input_parser', lambda *_, **__: None) 
@patch.object(MooseRun, 'add_process', mock_vector_postprocessor)
def test_moose_vectorpostprocessor_parser_no_positions(folder_setup):
    """
    Test values of VectorPostProcessors at each timestep are uploaded as Metrics,
    when positions are turned off it should not track x, y, or z as metrics.
    """
    temp_dir = tempfile.TemporaryDirectory(prefix="moose_test")
    name = 'test_moose_vectorpostprocessor_parser-%s' % str(uuid.uuid4())
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        # Set these here instead of them being read from a MOOSE input file
        run._output_dir_path = temp_dir.name
        run._results_prefix = "moose"
        
        run.launch(
            moose_application_path=pathlib.Path(__file__),
            moose_file_path=pathlib.Path(__file__),
            track_vector_postprocessors=True,
            track_vector_positions=False
        )
           
    client = simvue.Client()
    
    # Check that 7 metrics have been created for positions along the bar 0-6
    metrics_names = client.get_metrics_names(run_id)
    assert sum(1 for i in metrics_names) == 7
    
    # Get metric with ID '1', check that time and step values are correct
    sample_metric_times = client.get_metric_values(metric_names=["temps.T.1"], xaxis="time", output_format="dataframe", run_ids=[run_id])
    assert list(sample_metric_times.index.levels[0]) == [2.0, 4.0, 6.0, 8.0, 10.0]
    sample_metric_steps = client.get_metric_values(metric_names=["temps.T.1"], xaxis="step", output_format="dataframe", run_ids=[run_id])
    assert list(sample_metric_steps.index.levels[0]) == [1.0, 2.0, 3.0, 4.0, 5.0]
    
    # Check that metric values match those given in files
    metric_values = sample_metric_times['temps.T.1'].tolist()
    metric_ints = [int(val) for val in metric_values]
    assert metric_ints == [366, 549, 641, 694, 729]        
        
@patch.object(MooseRun, '_moose_input_parser', lambda *_, **__: None) 
@patch.object(MooseRun, 'add_process', mock_vector_postprocessor)
def test_moose_vectorpostprocessor_parser_with_positions(folder_setup):    
    """
    Test values of VectorPostProcessors at each timestep are uploaded as Metrics,
    when positions are turned on it should track x, y, and z metrics.
    """
    name = 'test_moose_vectorpostprocessor_parser_positions-%s' % str(uuid.uuid4())
    temp_dir = tempfile.TemporaryDirectory(prefix="moose_test")
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        # Set these here instead of them being read from a MOOSE input file
        run._output_dir_path = temp_dir.name
        run._results_prefix = "moose"
        
        run.launch(
            moose_application_path=pathlib.Path(__file__),
            moose_file_path=pathlib.Path(__file__),
            track_vector_postprocessors=True,
            track_vector_positions=True
        )
           
    client = simvue.Client()
    
    # Check that 28 metrics have been created for positions along the bar 0-6
    # One for each of T, X, Y, Z
    metrics_names = client.get_metrics_names(run_id)
    assert sum(1 for i in metrics_names) == 28
    
    # Get metric with ID '1', check that time and step values are correct
    sample_metric_times = client.get_metric_values(metric_names=["temps.x.1"], xaxis="time", output_format="dataframe", run_ids=[run_id])
    assert list(sample_metric_times.index.levels[0]) == [2.0, 4.0, 6.0, 8.0, 10.0]
    sample_metric_steps = client.get_metric_values(metric_names=["temps.x.1"], xaxis="step", output_format="dataframe", run_ids=[run_id])
    assert list(sample_metric_steps.index.levels[0]) == [1.0, 2.0, 3.0, 4.0, 5.0]
    
    # Check that the x position is recorded, and is set to 1 for id=1 throughout
    metric_values = sample_metric_times['temps.x.1'].tolist()
    assert metric_values == [1.0,1.0,1.0,1.0,1.0]
    
@patch.object(MooseRun, '_moose_input_parser', lambda *_, **__: None)    
@patch.object(MooseRun, 'add_process', mock_vector_postprocessor)
def test_moose_vectorpostprocessor_disabled(folder_setup):    
    """
    Check that no information is uploaded from VectorPostProcessor files if disabled.
    """
    name = 'test_moose_vectorpostprocessor_parser-%s' % str(uuid.uuid4())
    temp_dir = tempfile.TemporaryDirectory(prefix="moose_test")
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        # Set these here instead of them being read from a MOOSE input file
        run._output_dir_path = temp_dir.name
        run._results_prefix = "moose"
        
        run.launch(
            moose_application_path=pathlib.Path(__file__),
            moose_file_path=pathlib.Path(__file__),
            track_vector_postprocessors=False,
        )
           
    client = simvue.Client()
    
    # Check that no metrics are recorded
    metrics_names = client.get_metrics_names(run_id)
    assert sum(1 for i in metrics_names) == 0