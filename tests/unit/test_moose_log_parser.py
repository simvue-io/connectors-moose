from simvue_moose.connector import MooseRun
import simvue
import uuid
import pathlib


def test_moose_log_parser(folder_setup):
    """
    Check that Events and Metrics are correctly parsed from the MOOSE log file and uploaded.
    """
    name = "test_moose_log_parser-%s" % str(uuid.uuid4())
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id

        run.log_event("Beginning MOOSE simulation...")
        for line in (
            pathlib.Path(__file__)
            .parent.joinpath("example_data", "moose_log.txt")
            .open("r")
        ):
            run._log_parser(line)
        run.log_event("Simulation complete!")

    client = simvue.Client()
    # Check messages correctly extracted from log and added as events
    events = client.get_events(run_id)

    assert events[1]["message"] == "Time Step 1, time = 1, dt = 1"
    assert events[2]["message"] == " Solve Converged!"
    assert events[4]["message"] == " Total Nonlinear Iterations: 3."
    assert events[5]["message"] == " Total Linear Iterations: 112."
    assert (
        events[-2]["message"]
        == "Terminator 'handle-too-hot' is causing the execution to terminate."
    )

    # Check that total linear and nonlinear events from each step uploaded as metrics
    # Correct answers calculated manually from log file
    metrics = client.get_metric_values(
        metric_names=["total_linear_iterations", "total_nonlinear_iterations"],
        run_ids=[
            run_id,
        ],
        output_format="dict",
        xaxis="step",
    )
    assert list(metrics["total_linear_iterations"].values()) == [112.0, 107.0]
    assert list(metrics["total_nonlinear_iterations"].values()) == [3.0, 3.0]

    # Check that reason for termination is added as metadata and tag, and run is set to terminated state
    run_data = client.get_run(run_id)
    assert run_data.metadata["handle-too-hot"] == True
    assert "handle-too-hot" in run_data.tags
    assert run_data.status == "terminated"


def test_moose_log_parser_steady(folder_setup):
    """
    Check that Events and Metrics are correctly parsed from log file of a steady MOOSE simulation.
    """
    name = "test_moose_steady_log_parser-%s" % str(uuid.uuid4())
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id

        run.log_event("Beginning MOOSE simulation...")
        for line in (
            pathlib.Path(__file__)
            .parent.joinpath("example_data", "moose_log_steady.txt")
            .open("r")
        ):
            run._log_parser(line)
        run.log_event("Simulation complete!")

    client = simvue.Client()
    # Check messages correctly extracted from log and added as events
    events = client.get_events(run_id)

    assert events[1]["message"] == "Beginning Nonlinear Iteration 0"
    assert (
        events[2]["message"]
        == "    Linear Solve Did Not Converge Due To Diverged_Its Iterations 50"
    )
    assert events[3]["message"] == " Total Nonlinear Iterations: 1."
    assert events[4]["message"] == " Total Linear Iterations: 51."
    assert events[5]["message"] == "Beginning Nonlinear Iteration 1"
    assert events[7]["message"] == " Total Nonlinear Iterations: 2."
    assert events[8]["message"] == " Total Linear Iterations: 102."

    # Check that linear and nonlinear residuals uploaded as metrics
    linear_iteration_residuals = client.get_metric_values(
        metric_names=["linear_iteration_residuals"],
        run_ids=[
            run_id,
        ],
        output_format="dict",
        xaxis="step",
    )
    nonlinear_iteration_residuals = client.get_metric_values(
        metric_names=["nonlinear_iteration_residuals"],
        run_ids=[
            run_id,
        ],
        output_format="dict",
        xaxis="step",
    )
    linear_iteration_residuals = list(
        linear_iteration_residuals["linear_iteration_residuals"].values()
    )
    nonlinear_iteration_residuals = list(
        nonlinear_iteration_residuals["nonlinear_iteration_residuals"].values()
    )
    assert len(linear_iteration_residuals) == 255
    assert len(nonlinear_iteration_residuals) == 5
