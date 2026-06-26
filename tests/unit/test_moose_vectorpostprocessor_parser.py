from simvue_moose.connector import MooseRun
import simvue
import threading
import time
import tempfile
from unittest.mock import patch
import uuid
import pathlib
import shutil
from simvue.api.objects import GridMetrics
import numpy
import pandas
import pytest


def mock_vector_postprocessor(self, *cmd_args, **__):
    """
    Mock process for creating VectorPostProcessor output CSV files.
    There is one of these CSV files written all at once for each timestep.
    """

    def write_to_vector_pp():

        if cmd_args[-1] == "--time-file":
            _timefile_lines = (
                pathlib.Path(__file__)
                .parent.joinpath("example_data", "moose_temps_time.csv")
                .open("r")
                .readlines()
            )
            _timefile = (
                pathlib.Path(self._output_dir_path)
                .joinpath("moose_temps_time.csv")
                .open("w", buffering=1)
            )
            _timefile.write(_timefile_lines[0])
        for num in range(0, 6, 1):
            if cmd_args[-1] == "--time-file":
                _timefile.write(_timefile_lines[num + 1])
            shutil.copy(
                pathlib.Path(__file__).parent.joinpath(
                    "example_data", f"moose_temps_000{num}.csv"
                ),
                pathlib.Path(self._output_dir_path).joinpath(
                    f"moose_temps_000{num}.csv"
                ),
            )
            time.sleep(0.5)
        if cmd_args[-1] == "--time-file":
            _timefile.close()
        self._trigger.set()
        return

    thread = threading.Thread(target=write_to_vector_pp)
    thread.start()


@pytest.mark.parametrize("track_vector_postprocessors", [True, False])
@pytest.mark.parametrize("load", [True, False])
@pytest.mark.parametrize("write_time_file", [True, False])
@patch.object(MooseRun, "_moose_input_parser", lambda *_, **__: {})
@patch.object(MooseRun, "_moose_input_callback", lambda *_, **__: None)
@patch.object(MooseRun, "add_process", mock_vector_postprocessor)
def test_moose_vectorpostprocessor_parser(
    folder_setup, write_time_file, load, track_vector_postprocessors
):
    """
    Test values of VectorPostProcessors at each timestep are uploaded as Metrics.
    """
    temp_dir = tempfile.TemporaryDirectory(prefix="moose_test")
    name = "test_moose_vectorpostprocessor_parser-%s" % str(uuid.uuid4())
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name=name, folder=folder_setup)
        run_id = run.id
        # Set these here instead of them being read from a MOOSE input file
        run._output_dir_path = pathlib.Path(temp_dir.name)
        run._results_prefix = "moose"
        run._dt = 2
        if load:
            for num in range(6):
                shutil.copy(
                    pathlib.Path(__file__).parent.joinpath(
                        "example_data", f"moose_temps_000{num}.csv"
                    ),
                    pathlib.Path(temp_dir.name).joinpath(f"moose_temps_000{num}.csv"),
                )
            if write_time_file:
                shutil.copy(
                    pathlib.Path(__file__).parent.joinpath(
                        "example_data", "moose_temps_time.csv"
                    ),
                    pathlib.Path(temp_dir.name).joinpath("moose_temps_time.csv"),
                )

            run.load(
                moose_file_path=pathlib.Path(__file__).parent.joinpath(
                    "example_data", "example_input_1.i"
                ),
                results_dir=pathlib.Path(temp_dir.name),
                track_vector_postprocessors=track_vector_postprocessors,
            )
        else:
            run.launch(
                moose_application_path=pathlib.Path(__file__),
                moose_file_path=pathlib.Path(__file__).parent.joinpath(
                    "example_data", "example_input_1.i"
                ),
                track_vector_postprocessors=track_vector_postprocessors,
                moose_env_vars={"time_file": write_time_file},
            )

    # Get time step 1, check values match those in files
    for num in range(1, 6, 1):
        if track_vector_postprocessors:
            df = pandas.read_csv(
                pathlib.Path(__file__).parent.joinpath(
                    "example_data", f"moose_temps_000{num}.csv"
                ),
            )
            metric = next(GridMetrics.get(runs=[run_id], metrics=["temps.T"], step=num))
            numpy.testing.assert_almost_equal(
                df["T"].values, numpy.array(metric["array"])
            )

            # Time should be 2 x step due to values in time file
            assert metric["time"] == 2 * num
        else:
            with pytest.raises(RuntimeError, match="No grid metrics at this step"):
                metric = next(
                    GridMetrics.get(runs=[run_id], metrics=["temps.T"], step=num)
                )
