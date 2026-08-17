from simvue_moose.connector import MooseRun
import uuid
import simvue
import pytest
import pathlib
from functools import reduce


def _parse_moose_input(
    tmp_path: pathlib.Path, contents: str
) -> dict[str, object]:
    input_path = tmp_path / "input.i"
    _ = input_path.write_text(contents)

    run = MooseRun.__new__(MooseRun)
    return run._moose_input_parser(input_path)


testdata = [
    (
        "example_input_1",
        {
            "example_input_1.r": "${units 5 cm -> m}",
            "example_input_1.GlobalParams.initial_T": 310,
            "example_input_1.FluidProperties.fluid.type": "IdealGasFluidProperties",
            "example_input_1.Components.pipe.position": "'0 0 0'",
            "example_input_1.Postprocessors.T_inlet.boundary": "pipe:in",
        },
        {},
    ),
    (
        "example_input_2",
        {
            "example_input_2.Mesh.file": "mug.e",
            "example_input_2.Variables.diffused.order": "FIRST",
            "example_input_2.BCs.bottom.value": 1,
            "example_input_2.BCs.top.boundary": "'top'",
        },
        {
            "example_input_2.BCs.bottom.boundary": "'bottom' # This must match a named boundary in the mesh file",
            "example_input_2.BCs.bottom] # arbitrary user-chosen name.type": "Shouldn't exist",
            "example_input_2.Mesh.#file": "mug_2.e",
        },
    ),
    (
        "example_input_3",
        {
            "example_input_3.Mesh.file": "half-cone.e",
            "example_input_3.Variables.diffused.order": "FIRST",
            "example_input_3.Kernels.td.type": "TimeDerivative",
            "example_input_3.BCs.left.value": 2,
            "example_input_3.Outputs.exodus": "true",
        },
        {
            "example_input_3.Variables../diffused.order": "FIRST",
            "example_input_3.Executioner.#Preconditioned": "JFNK (default)",
        },
    ),
    (
        "example_input_4",
        {
            "example_input_4.Mesh.generated.type": "GeneratedMeshGenerator",
            "example_input_4.VectorPostprocessors.temps_line.points": "'0 0.5 0.5  1 0.5 0.5  2 0.5 0.5  3 0.5 0.5  4 0.5 0.5  5 0.5 0.5  6 0.5 0.5'",
            "example_input_4.Postprocessors": {
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
            "example_input_4.Postprocessors.temp.avg.block": "Shouldn't exist",
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
        run.moose_file_path = pathlib.Path(__file__).parent.joinpath(
            "example_data", f"{file_name}.i"
        )
        run_id = run.id
        input_metadata = run._moose_input_parser(
            pathlib.Path(__file__).parent.joinpath("example_data", f"{file_name}.i")
        )
        run._moose_input_callback(input_metadata)

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


@pytest.mark.parametrize(
    "block_body",
    (
        pytest.param(
            "  # ignored = 2\n  value = 1\n  after = 2",
            id="commented-assignment",
        ),
        pytest.param(
            "  # [Ignored]\n  value = 1\n  after = 2",
            id="commented-block-opener",
        ),
        pytest.param(
            "  value = 1 # [Ignored]\n  after = 2",
            id="inline-commented-block-opener",
        ),
        pytest.param(
            "  value = 1\n  # []\n  after = 2",
            id="commented-block-closer",
        ),
        pytest.param(
            "  value = 1 # []\n  after = 2",
            id="inline-commented-block-closer",
        ),
    ),
)
def test_moose_input_parser_ignores_syntax_in_comments(tmp_path, block_body):
    metadata = _parse_moose_input(
        tmp_path,
        f"[Outer]\n{block_body}\n[]\n",
    )

    assert metadata == {"Outer": {"value": 1.0, "after": 2.0}}


@pytest.mark.parametrize(
    "value",
    (
        pytest.param("'material#1'", id="single-quoted-hash"),
        pytest.param('"material#1"', id="double-quoted-hash"),
        pytest.param(r"'material\'#1'", id="escaped-quote"),
        pytest.param("O'Reilly", id="apostrophe-in-unquoted-value"),
    ),
)
def test_moose_input_parser_preserves_values_when_stripping_comments(
    tmp_path, value
):
    metadata = _parse_moose_input(
        tmp_path,
        f"[Outer]\n  label = {value} # actual comment\n[]\n",
    )

    assert metadata == {"Outer": {"label": value}}


@pytest.mark.parametrize(
    "value",
    (
        pytest.param("'[Ignored]'", id="block-opener"),
        pytest.param("'[]'", id="block-closer"),
    ),
)
def test_moose_input_parser_preserves_brackets_in_quoted_values(tmp_path, value):
    metadata = _parse_moose_input(
        tmp_path,
        f"[Outer]\n  label = {value}\n[]\n",
    )

    assert metadata == {"Outer": {"label": value}}
