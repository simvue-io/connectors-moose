from examples.moose_example import moose_example
import pytest
import subprocess
import pathlib
import tempfile
import simvue
from simvue.sender import sender

@pytest.mark.parametrize("offline", (True, False), ids=("offline", "online"))
@pytest.mark.parametrize("parallel", (True, False), ids=("parallel", "serial"))
def test_moose_connector(offline, parallel):
    try:
        subprocess.run("/opt/moose/bin/moose-opt")
    except FileNotFoundError:
        pytest.skip("You are attempting to run MOOSE Integration Tests without having MOOSE installed.")
            
    run_id = moose_example("/opt/moose/bin/moose-opt", offline=offline, parallel=parallel)

    if offline:
        _id_mapping = sender()
        run_id = _id_mapping.get(run_id)
    
    client = simvue.Client()
    run_data = client.get_run(run_id)
    events = [event["message"] for event in client.get_events(run_id)]
    
    # Check run description and tags from init have been added
    assert run_data.description == "An example of using the MooseRun Connector to track a MOOSE simulation."
    assert run_data.tags == ['moose', 'thermal', 'diffusion']
    
    # Check alert has been added
    assert "avg_temp_above_500" in [alert["name"] for alert in run_data.get_alert_details()]
    
    # Check metadata from MOOSE log header has been uploaded
    assert run_data.metadata["moose"]["executioner"] == "Transient"
    
    if parallel:
        assert run_data.metadata["moose"]["num_processors"] == '2'
    else:
        assert run_data.metadata["moose"]["num_processors"] == '1'
    
    # Check metadata from MOOSE input file has been uploaded
    assert run_data.metadata["thermal_bar"]["Postprocessors"]["average_temperature"]["type"] == "ElementAverageValue"
    assert run_data.metadata["thermal_bar"]["BCs"]["hot"]["value"] == 1000
    
    # Check events uploaded from log
    assert "Time Step 1, time = 2, dt = 2" in events
    assert "Time Step 15, time = 30, dt = 2" in events
    
    # Check metrics uploaded from PostProcessor CSV
    metrics = dict(run_data.metrics)
    assert metrics["average_temperature"]["max"] > 498
    
    # Check time and step data is correct
    sample_metric = client.get_metric_values(metric_names=["average_temperature"], xaxis="time", output_format="dataframe", run_ids=[run_id])
    assert list(sample_metric.index.levels[0]) == list(range(0, 32, 2))
    sample_metric = client.get_metric_values(metric_names=["average_temperature"], xaxis="step", output_format="dataframe", run_ids=[run_id])
    assert list(sample_metric.index.levels[0]) == list(range(0, 16, 1))
    
    # Check metrics uploaded from VectorPostProcessor CSV
    assert metrics["temperature_along_bar.T.1"]["max"] > 498
    
    # Check time and step data is correct - starts from first step, since file PostProcessor file 0000 is blank
    sample_metric = client.get_metric_values(metric_names=["temperature_along_bar.T.0"], xaxis="time", output_format="dataframe", run_ids=[run_id])
    assert list(sample_metric.index.levels[0]) == list(range(2, 32, 2))
    sample_metric = client.get_metric_values(metric_names=["temperature_along_bar.T.2"], xaxis="step", output_format="dataframe", run_ids=[run_id])
    assert list(sample_metric.index.levels[0]) == list(range(1, 16, 1))
    
    temp_dir = tempfile.TemporaryDirectory()
    
    # Check input file uploaded as input
    client.get_artifacts_as_files(run_id, "input", temp_dir.name)
    assert pathlib.Path(temp_dir.name).joinpath("thermal_bar.i").exists()
    
    # Check results uploaded as output
    client.get_artifacts_as_files(run_id, "output", temp_dir.name)
    assert pathlib.Path(temp_dir.name).joinpath("simvue_thermal.e").exists()
    
     
    
    
    