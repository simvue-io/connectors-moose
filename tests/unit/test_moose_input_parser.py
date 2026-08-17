from simvue_moose.connector import MooseRun
import uuid
import simvue
import pytest
import pathlib
from functools import reduce

testdata = [
    (
        "example_input_1",
        {
            "input_file.r": "${units 5 cm -> m}",
            "input_file.GlobalParams.initial_T": 310,
            "input_file.FluidProperties.fluid.type": "IdealGasFluidProperties",
            "input_file.Components.pipe.position": "'0 0 0'",
            "input_file.Postprocessors.T_inlet.boundary": "pipe:in",
        },
        {},
    ),
    (
        "example_input_2",
        {
            "input_file.Mesh.file": "mug.e",
            "input_file.Variables.diffused.order": "FIRST",
            "input_file.BCs.bottom.value": 1,
            "input_file.BCs.top.boundary": "'top'",
        },
        {
            "input_file.BCs.bottom.boundary": "'bottom' # This must match a named boundary in the mesh file",
            "input_file.BCs.bottom] # arbitrary user-chosen name.type": "Shouldn't exist",
            "input_file.Mesh.#file": "mug_2.e",
        },
    ),
    (
        "example_input_3",
        {
            "input_file.Mesh.file": "half-cone.e",
            "input_file.Variables.diffused.order": "FIRST",
            "input_file.Kernels.td.type": "TimeDerivative",
            "input_file.BCs.left.value": 2,
            "input_file.Outputs.exodus": "true",
        },
        {
            "input_file.Variables../diffused.order": "FIRST",
            "input_file.Executioner.#Preconditioned": "JFNK (default)",
        },
    ),
    (
        "example_input_4",
        {
            "input_file.Mesh.generated.type": "GeneratedMeshGenerator",
            "input_file.VectorPostprocessors.temps_line.points": "'0 0.5 0.5  1 0.5 0.5  2 0.5 0.5  3 0.5 0.5  4 0.5 0.5  5 0.5 0.5  6 0.5 0.5'",
            "input_file.Postprocessors": {
                "temp.avg": {
                    "type": "ElementAverageValue",
                    "block": 0.0,
                    "variable": "'T'",
                },
                "temp.max": {
                    "type": "ElementExtremeValue",
                    "block": 0.0,
                    "variable": "'T'",
                    "value_type": "max",
                },
                "temp.min": {
                    "type": "ElementExtremeValue",
                    "block": 0.0,
                    "variable": "'T'",
                    "value_type": "min",
                },
            },
        },
        {
            "input_file.Postprocessors.temp.avg.block": "Shouldn't exist",
        },
    ),
]


@pytest.mark.parametrize(
    "file_name,expected_metadata,not_expected_metadata", testdata, ids=(1, 2, 3, 4)
)
def test_moose_input_parser(
    folder_setup, file_name, expected_metadata, not_expected_metadata
):
    """
    Check information from MOOSE input file is correctly uploaded as metadata
    """
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(
            name="test_moose_input_parser-%s" % str(uuid.uuid4()), folder=folder_setup
        )
        run.workdir_path = pathlib.Path.cwd()
        run.moose_file_paths = [
            pathlib.Path(__file__).parent.joinpath("example_data", f"{file_name}.i")
        ]
        run_id = run.id
        input_metadata = run._moose_input_parser()

        run._moose_input_callback(input_metadata)
        run._output_dir_path, run._results_prefix = run._find_results_dir()

        client = simvue.Client()
        metadata = client.get_run(run_id).metadata
        # Check that keys and values parsed correctly
        for key, value in expected_metadata.items():
            # Have moved keys from being stored as dot notation to nested dict
            # So unpack the dot separated keys as a list of keys, then use reduce to obtain values from nested metadata
            assert (
                reduce(lambda d, k: d.get(k, None), key.split("."), metadata) == value
            )

        for key, value in not_expected_metadata.items():
            try:
                assert (
                    reduce(lambda d, k: d.get(k, None), key.split("."), metadata)
                    != value
                )
            except AttributeError:  # Will get this if the key doesnt exist since it tries to get() on a None, but we are expecting that...
                continue

        assert run._output_dir_path == pathlib.Path.cwd().joinpath("results")
        assert run._results_prefix == file_name

        if file_name in ("example_input_1", "example_input_3"):
            assert run._dt == 0.1
        else:
            assert run._dt == None


def test_multi_input_parser(folder_setup):
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(
            name="test_multi_input_parser-%s" % str(uuid.uuid4()), folder=folder_setup
        )
        run.moose_file_paths = [
            pathlib.Path(__file__).parent.joinpath(
                "example_data", "example_input_5a.i"
            ),
            pathlib.Path(__file__).parent.joinpath(
                "example_data", "example_input_5b.i"
            ),
            pathlib.Path(__file__).parent.joinpath(
                "example_data", "example_input_5c.i"
            ),
        ]
        input_metadata = run._moose_input_parser()

        # Check metadata from input file A is present
        assert input_metadata["Mesh"]["generated"]["dim"] == 3
        assert (
            input_metadata["VectorPostprocessors"]["temps_line"]["type"]
            == "PointValueSampler"
        )

        # Check Executioner block not fully overriden by file C
        assert input_metadata["Executioner"]["solve_type"] == "NEWTON"

        # Check metadata added from file B
        assert input_metadata["BCs"]["cold"]["variable"] == "T"

        # Check hot BC not fully overriden from file C
        assert input_metadata["BCs"]["hot"]["type"] == "DirichletBC"

        # Check metadata from file B overwrites file A
        assert input_metadata["Outputs"]["file_base"] == "results/example_input_5"

        # Check metadata added from file C, overwriting other files
        assert input_metadata["BCs"]["hot"]["value"] == 2000
        assert input_metadata["Executioner"]["end_time"] == 60

        # Check values duplicated across files are not an issue
        assert input_metadata["Executioner"]["type"] == "Transient"


def test_included_file_parser(folder_setup):
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(
            name="test_included_input_parser-%s" % str(uuid.uuid4()),
            folder=folder_setup,
        )
        run.moose_file_paths = [
            pathlib.Path(__file__).parent.joinpath(
                "example_data", "example_input_6a.i"
            ),
        ]
        input_metadata = run._moose_input_parser()

        # Check metadata from input file A is present
        assert input_metadata["Mesh"]["generated"]["dim"] == 3
        assert (
            input_metadata["VectorPostprocessors"]["temps_line"]["type"]
            == "PointValueSampler"
        )

        # Check metadata added from file B
        assert input_metadata["BCs"]["cold"]["variable"] == "T"
        assert input_metadata["BCs"]["hot"]["value"] == 1000
