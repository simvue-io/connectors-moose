import pytest
import subprocess
import pathlib
import tempfile
import simvue
import uuid
import shutil
from simvue_moose.connector import MooseRun
from simvue.sender import Sender
from simvue.api.objects import GridMetrics

# Set this variable to wherever your MOOSE app is located:
MOOSE_APP_PATH = "/opt/moose/bin/moose-opt"


def run_moose(
    moose_file_path: pathlib.Path,
    load_results_dir,
    workdir_path,
    offline,
    parallel,
    load,
    upload_misc_logs,
    moose_app_path,
) -> None:

    # Initialise the MooseRun class as a context manager
    with MooseRun(mode="offline" if offline else "online") as run:
        # Initialise the run, providing a name for the run, and optionally extra information such as a folder, description, tags etc
        run.init(
            name=f"fds-integration-{moose_file_path.stem}-{'parallel' if parallel else 'serial'}-{'offline' if offline else 'online'}-{'load' if load else 'launch'}-{str(uuid.uuid4())}",
            description="An example of using the MooseRun Connector to track a MOOSE simulation.",
            folder="/test-moose",
            tags=["moose", "thermal", "diffusion"],
            retention_period="1 hour",
        )

        # You can use any of the Simvue Run() methods to upload extra information before/after the simulation
        run.create_metric_threshold_alert(
            name="avg_temp_above_500",
            metric="average_temperature",
            rule="is above",
            threshold=500.0,
            frequency=1,
            window=1,
        )

        if load:
            run.load(
                moose_file_path=moose_file_path,
                results_dir=load_results_dir,
                # You can optionally choose to track VectorPostProcessor outputs:
                track_vector_postprocessors=True,
                upload_miscellaneous_logs=upload_misc_logs,
            )
        else:
            # Then call the .launch() method to start your MOOSE simulation
            run.launch(
                moose_application_path=moose_app_path,
                moose_file_path=moose_file_path,
                workdir_path=workdir_path,
                upload_miscellaneous_logs=upload_misc_logs,
                # You can optionally choose to track VectorPostProcessor outputs:
                track_vector_postprocessors=True,
                # And you can choose whether to run it in parallel
                run_in_parallel=parallel,
                num_processors=2,
                parallel_cli_options={"allow-run-as-root": True},
            )

        # Once the simulation is complete, you can upload any final items to the Simvue run before it closes
        run.log_event("Deleting local copies of results...")

        run_id = run.id

    if offline:
        sender = Sender(throw_exceptions=True)
        sender.upload()
        run_id = sender._id_mapping.get(run_id)

    return run_id


@pytest.mark.parametrize("offline", (True, False), ids=("offline", "online"))
@pytest.mark.parametrize("parallel", (True, False), ids=("parallel", "serial"))
@pytest.mark.parametrize("load", (True, False), ids=("load", "launch"))
def test_moose_connector(offline, parallel, load, offline_cache_setup):
    try:
        subprocess.run(MOOSE_APP_PATH)
    except FileNotFoundError:
        pytest.skip(
            "You are attempting to run MOOSE Integration Tests without having MOOSE installed."
        )
    if load:
        if parallel:
            pytest.skip("Parallel has no effect when loading from historic runs")
        # Create a temp dir to contain results
    with tempfile.TemporaryDirectory() as tempd:
        run_id = run_moose(
            moose_file_path=pathlib.Path(__file__).parent.joinpath(
                "example_data", "thermal_bar.i"
            ),
            load_results_dir=pathlib.Path(__file__).parent.joinpath(
                "example_data", "thermal_bar_results"
            ),
            workdir_path=pathlib.Path(tempd),
            offline=offline,
            parallel=parallel,
            load=load,
            upload_misc_logs=False,
            moose_app_path=MOOSE_APP_PATH,
        )

    client = simvue.Client()
    run_data = client.get_run(run_id)
    events = [event["message"] for event in client.get_events(run_id)]

    # Check run description and tags from init have been added
    assert (
        run_data.description
        == "An example of using the MooseRun Connector to track a MOOSE simulation."
    )
    assert run_data.tags == ["moose", "thermal", "diffusion"]

    # Check alert has been added
    assert "avg_temp_above_500" in [
        alert["name"] for alert in run_data.get_alert_details()
    ]

    # Check metadata from MOOSE log header has been uploaded
    assert (
        run_data.metadata["moose"]["execution_information"]["executioner"]
        == "Transient"
    )

    if parallel:
        assert run_data.metadata["moose"]["parallelism"]["num_processors"] == "2"
    else:
        assert run_data.metadata["moose"]["parallelism"]["num_processors"] == "1"

    # Check metadata from MOOSE input file has been uploaded
    assert (
        run_data.metadata["thermal_bar"]["Postprocessors"]["average_temperature"][
            "type"
        ]
        == "ElementAverageValue"
    )
    assert run_data.metadata["thermal_bar"]["BCs"]["hot"]["value"] == 1000

    # Check events uploaded from log
    assert "Time Step 1, time = 2, dt = 2" in events
    assert "Time Step 15, time = 30, dt = 2" in events

    # Check misc logs not uploaded
    assert not any("Finished Executing" in msg for msg in events)

    # Check PostProcessor and residuals not uploaded as events
    assert "Postprocessor Values:" not in events
    assert not any("+--" in msg for msg in events)
    assert not any("Linear |R|" in msg for msg in events)
    assert not any("Nonlinear |R|" in msg for msg in events)

    # Check metrics uploaded from PostProcessor CSV
    metrics = dict(run_data.metrics)
    assert metrics["average_temperature"]["max"] > 498

    # Check time and step data is correct
    sample_metric = client.get_metric_values(
        metric_names=["average_temperature"],
        xaxis="time",
        output_format="dataframe",
        run_ids=[run_id],
    )
    assert list(sample_metric.index.levels[0]) == list(range(0, 32, 2))
    sample_metric = client.get_metric_values(
        metric_names=["average_temperature"],
        xaxis="step",
        output_format="dataframe",
        run_ids=[run_id],
    )
    assert list(sample_metric.index.levels[0]) == list(range(0, 16, 1))

    # Check metrics uploaded from VectorPostProcessor CSV
    metric = next(
        GridMetrics.get(runs=[run_id], metrics=["temperature_along_bar.T"], step=15)
    )
    assert len(metric["array"]) == 3
    assert metric["array"][1] > 498

    # Check time and step data is correct - time is 2x step
    assert metric["time"] == 30

    with tempfile.TemporaryDirectory() as temp_dir:
        # Check input file uploaded as input
        client.get_artifacts_as_files(run_id, "input", temp_dir)
        assert pathlib.Path(temp_dir).joinpath("thermal_bar.i").exists()

        # Check results uploaded as output
        client.get_artifacts_as_files(run_id, "output", temp_dir)
        assert pathlib.Path(temp_dir).joinpath("simvue_thermal.e").exists()


@pytest.mark.parametrize("offline", (True, False), ids=("offline", "online"))
@pytest.mark.parametrize("parallel", (True, False), ids=("parallel", "serial"))
@pytest.mark.parametrize("load", (True, False), ids=("load", "launch"))
def test_moose_steady(offline, parallel, load, offline_cache_setup):
    try:
        subprocess.run(MOOSE_APP_PATH)
    except FileNotFoundError:
        pytest.skip(
            "You are attempting to run MOOSE Integration Tests without having MOOSE installed."
        )
    if load:
        if parallel:
            pytest.skip("Parallel has no effect when loading from historic runs")

        # Create a temp dir to contain results
    with tempfile.TemporaryDirectory() as tempd:
        # Need to make a copy of the input file into the workdir
        # Because file_base not specified, so will make results relative to input file
        shutil.copy(
            pathlib.Path(__file__).parent.joinpath("example_data", "diffusion.i"),
            pathlib.Path(tempd).joinpath("diffusion.i"),
        )
        run_id = run_moose(
            moose_file_path=pathlib.Path(tempd).joinpath("diffusion.i"),
            load_results_dir=pathlib.Path(__file__).parent.joinpath(
                "example_data", "diffusion_results"
            ),
            workdir_path=None,
            offline=offline,
            parallel=parallel,
            load=load,
            upload_misc_logs=True,
            moose_app_path=MOOSE_APP_PATH,
        )

    client = simvue.Client()
    run_data = client.get_run(run_id)
    events = [event["message"] for event in client.get_events(run_id)]

    # Check run description and tags from init have been added
    assert (
        run_data.description
        == "An example of using the MooseRun Connector to track a MOOSE simulation."
    )
    assert run_data.tags == ["moose", "thermal", "diffusion"]

    # Check alert has been added
    assert "avg_temp_above_500" in [
        alert["name"] for alert in run_data.get_alert_details()
    ]

    # Check metadata from MOOSE log header has been uploaded
    assert (
        run_data.metadata["moose"]["execution_information"]["executioner"] == "Steady"
    )

    if parallel:
        assert run_data.metadata["moose"]["parallelism"]["num_processors"] == "2"
    else:
        assert run_data.metadata["moose"]["parallelism"]["num_processors"] == "1"

    # Check metadata from MOOSE input file has been uploaded
    assert (
        run_data.metadata["diffusion"]["Postprocessors"]["left"]["type"]
        == "SideAverageValue"
    )
    assert run_data.metadata["diffusion"]["GlobalParams"]["diffusivity"] == 1

    # Check events uploaded from log
    assert "Beginning Nonlinear Iteration 1" in events
    assert " Solve Converged!" in events
    assert " Total Nonlinear Iterations: 3." in events

    # Check misc logs uploaded
    assert "Outlier Variable Residual Norms:" in events

    # Check PostProcessor and residuals not uploaded as events
    assert "Postprocessor Values:" not in events
    assert not any("+--" in msg for msg in events)
    assert not any("Linear |R|" in msg for msg in events)
    assert not any("Nonlinear |R|" in msg for msg in events)

    metrics = dict(run_data.metrics)
    assert metrics["bottom"]["max"] > 0.49

    # Check residuals metrics uploaded from log file
    assert metrics.get("linear_iteration_residuals")
    assert metrics.get("nonlinear_iteration_residuals")

    assert metrics["linear_iteration_residuals"]["count"] > 200
    assert metrics["nonlinear_iteration_residuals"]["count"] > 2

    with tempfile.TemporaryDirectory() as temp_dir:
        # Check input file uploaded as input
        client.get_artifacts_as_files(run_id, "input", temp_dir)
        assert pathlib.Path(temp_dir).joinpath("diffusion.i").exists()

        # Check results uploaded as output
        client.get_artifacts_as_files(run_id, "output", temp_dir)
        assert pathlib.Path(temp_dir).joinpath("diffusion_csv.csv").exists()


@pytest.mark.parametrize(
    "file_base",
    (None, "relative", "absolute"),
)
@pytest.mark.parametrize(
    "workdir_path",
    (True, False),
    ids=("workdir_path", "default_workdir"),
)
def test_file_base(file_base, workdir_path, offline_cache_setup, monkeypatch):
    try:
        subprocess.run(MOOSE_APP_PATH)
    except FileNotFoundError:
        pytest.skip(
            "You are attempting to run MOOSE Integration Tests without having MOOSE installed."
        )

    original_cwd = pathlib.Path.cwd()

    with tempfile.TemporaryDirectory() as tempd:
        # Replace file base
        text = (
            pathlib.Path(__file__)
            .parent.joinpath("example_data", "thermal_bar.i")
            .read_text()
        )
        if file_base == "absolute":
            text = text.replace(
                "  file_base = test_results/simvue_thermal",
                f"  file_base = {pathlib.Path(tempd).joinpath('absolute', 'simvue_thermal').absolute()}",
            )
        elif not file_base:
            text = text.replace(
                "  file_base = test_results/simvue_thermal",
                "  ",
            )

        # Create new copy of input file
        moose_file_path = pathlib.Path(tempd).joinpath("thermal_bar.i")
        moose_file_path.write_text(text)

        # Initialise the MooseRun class as a context manager
        with MooseRun() as run:
            # Initialise the run, providing a name for the run, and optionally extra information such as a folder, description, tags etc
            run.init(
                name=f"fds-integration-file_base-{file_base}-working_dir-{str(workdir_path)}-{str(uuid.uuid4())}",
                description="An example of using the MooseRun Connector to track a MOOSE simulation.",
                folder="/test-moose",
                tags=["moose", "thermal", "diffusion"],
                retention_period="1 hour",
            )

            run_id = run.id

            # Change current working directory
            monkeypatch.chdir(tempd)

            run.launch(
                moose_application_path=MOOSE_APP_PATH,
                moose_file_path=moose_file_path,
                workdir_path=pathlib.Path(tempd).joinpath("my_results")
                if workdir_path
                else None,
                # You can optionally choose to track VectorPostProcessor outputs:
                track_vector_postprocessors=True,
            )

        # Check results files exist in expected location
        if file_base == "absolute":
            # Should be in tempd with prefix simvue_absolute
            assert (
                pathlib.Path(tempd).joinpath("absolute", "simvue_thermal.csv").exists()
            )
        elif file_base == "relative":
            # Should be in folder test_results, relative to working dir if specified, with simvue_thermal prefix
            if workdir_path:
                assert (
                    pathlib.Path(tempd)
                    .joinpath("my_results", "test_results", "simvue_thermal.csv")
                    .exists()
                )
            else:
                assert (
                    pathlib.Path(tempd)
                    .joinpath("test_results", "simvue_thermal.csv")
                    .exists()
                )
        else:
            # Files given default names, starting with name of input file
            # Always puts in parent dir of input file, regardless of working dir
            assert pathlib.Path(tempd).joinpath("thermal_bar_out.csv").exists()

    # Change current working directory back to normal
    monkeypatch.chdir(original_cwd)

    # Check data collected
    client = simvue.Client()
    run_data = client.get_run(run_id)
    events = [event["message"] for event in client.get_events(run_id)]

    # Check run description and tags from init have been added
    assert (
        run_data.description
        == "An example of using the MooseRun Connector to track a MOOSE simulation."
    )
    assert run_data.tags == ["moose", "thermal", "diffusion"]

    # Check metadata from MOOSE log header has been uploaded
    assert (
        run_data.metadata["moose"]["execution_information"]["executioner"]
        == "Transient"
    )

    assert run_data.metadata["moose"]["parallelism"]["num_processors"] == "1"

    # Check metadata from MOOSE input file has been uploaded
    assert (
        run_data.metadata["thermal_bar"]["Postprocessors"]["average_temperature"][
            "type"
        ]
        == "ElementAverageValue"
    )
    assert run_data.metadata["thermal_bar"]["BCs"]["hot"]["value"] == 1000

    # Check events uploaded from log
    assert "Time Step 1, time = 2, dt = 2" in events
    assert "Time Step 15, time = 30, dt = 2" in events

    # Check metrics uploaded from PostProcessor CSV
    metrics = dict(run_data.metrics)
    assert metrics["average_temperature"]["max"] > 498

    # Check time and step data is correct
    sample_metric = client.get_metric_values(
        metric_names=["average_temperature"],
        xaxis="time",
        output_format="dataframe",
        run_ids=[run_id],
    )
    assert list(sample_metric.index.levels[0]) == list(range(0, 32, 2))
    sample_metric = client.get_metric_values(
        metric_names=["average_temperature"],
        xaxis="step",
        output_format="dataframe",
        run_ids=[run_id],
    )
    assert list(sample_metric.index.levels[0]) == list(range(0, 16, 1))

    # Check metrics uploaded from VectorPostProcessor CSV
    metric = next(
        GridMetrics.get(runs=[run_id], metrics=["temperature_along_bar.T"], step=15)
    )
    assert len(metric["array"]) == 3
    assert metric["array"][1] > 498

    # Check time and step data is correct - time is 2x step
    assert metric["time"] == 30

    with tempfile.TemporaryDirectory() as temp_dir:
        # Check input file uploaded as input
        client.get_artifacts_as_files(run_id, "input", temp_dir)
        assert pathlib.Path(temp_dir).joinpath("thermal_bar.i").exists()

        # Check results uploaded as output
        client.get_artifacts_as_files(run_id, "output", temp_dir)
        if file_base:
            assert pathlib.Path(temp_dir).joinpath("simvue_thermal.e").exists()
        else:
            assert pathlib.Path(temp_dir).joinpath("thermal_bar_exodus.e").exists()
