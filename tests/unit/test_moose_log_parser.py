from simvue_moose.connector import MooseRun
import simvue
import uuid
import pathlib
import pytest


@pytest.mark.parametrize(
    "upload_misc_logs", (True, False), ids=("all_events", "filtered_events")
)
def test_moose_log_parser(folder_setup, upload_misc_logs):
    """
    Check that Events and Metrics are correctly parsed from the MOOSE log file and uploaded.
    """
    name = "test_moose_log_parser-%s" % str(uuid.uuid4())
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        run.upload_miscellaneous_logs = upload_misc_logs

        for line in (
            pathlib.Path(__file__)
            .parent.joinpath("example_data", "moose_log.txt")
            .open("r")
        ):
            run._log_parser(line)

    client = simvue.Client()
    # Check messages correctly extracted from log and added as events
    events = client.get_events(run_id)
    event_messages = [event["message"] for event in events]

    assert "Time Step 1, time = 1, dt = 1" in event_messages
    assert " Solve Converged!" in event_messages
    assert " Total Nonlinear Iterations: 3." in event_messages
    assert " Total Linear Iterations: 112." in event_messages
    assert (
        "Terminator 'handle-too-hot' is causing the execution to terminate."
        in event_messages
    )

    # Residal lines themselves not uploaded
    assert not any("Linear |R|" in msg for msg in event_messages)
    assert not any("Nonlinear |R|" in msg for msg in event_messages)

    # Postprocessor lines not uploaded
    assert not any("Postprocessor Values:" in msg for msg in event_messages)
    assert not any("+--" in msg for msg in event_messages)
    assert not any("| time" in msg for msg in event_messages)

    # Should upload everything not handled elsewhere if upload_misc_logs
    results = (
        "*** Warning ***" in event_messages,
        "/home/workspace/copper_mug.i:131.5:" in event_messages,
        "The following warning occurred in the UserObject 'handle-too-hot' of type Terminator."
        in event_messages,
    )
    assert all(results) if upload_misc_logs else not any(results)

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


@pytest.mark.parametrize(
    "upload_misc_logs", (True, False), ids=("all_events", "filtered_events")
)
def test_moose_log_parser_steady(folder_setup, upload_misc_logs):
    """
    Check that Events and Metrics are correctly parsed from log file of a steady MOOSE simulation.
    """
    name = "test_moose_steady_log_parser-%s" % str(uuid.uuid4())
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        run.upload_miscellaneous_logs = upload_misc_logs
        for line in (
            pathlib.Path(__file__)
            .parent.joinpath("example_data", "moose_log_steady.txt")
            .open("r")
        ):
            run._log_parser(line)

    client = simvue.Client()
    # Check messages correctly extracted from log and added as events
    events = client.get_events(run_id)
    event_messages = [event["message"] for event in events]

    assert "Beginning Nonlinear Iteration 0" in event_messages
    assert (
        "    Linear Solve Did Not Converge Due To Diverged_Its Iterations 50"
        in event_messages
    )
    assert " Total Nonlinear Iterations: 1." in event_messages
    assert " Total Linear Iterations: 51." in event_messages
    assert "Beginning Nonlinear Iteration 1" in event_messages
    assert " Total Nonlinear Iterations: 2." in event_messages
    assert " Total Linear Iterations: 102." in event_messages

    # Residal lines themselves not uploaded
    assert not any("Linear |R|" in msg for msg in event_messages)
    assert not any("Nonlinear |R|" in msg for msg in event_messages)

    # Should upload everything not handled elsewhere if upload_misc_logs
    results = (
        "    Preparing Mesh" in event_messages,
        "Performing automatic scaling calculation" in event_messages,
        "PETSc Version:           3.25.2"
        in event_messages,  # not ideal, but can't reliably distinguish between header and misc events
    )
    assert all(results) if upload_misc_logs else not any(results)

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
