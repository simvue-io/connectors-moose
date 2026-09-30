from simvue_moose.connector import MooseRun
import pathlib
import pytest

WORKDIR = pathlib.Path(__file__).parent.joinpath("example_data")


@pytest.mark.parametrize(
    ("working_dir", "file_base", "expected_output_dir", "expected_prefix"),
    [
        # No file_base - results in workdir, with prefix matching input file
        [
            None,
            None,
            pathlib.Path(__file__).joinpath("example_data"),
            "example_input_1",
        ],
        [
            WORKDIR,
            None,
            pathlib.Path(__file__).joinpath("example_data"),
            "example_input_1",
        ],
        # file_base absolute path with no prefix, results stored in that dir (with no prefix)
        [None, "/tmp/", pathlib.Path("/tmp"), ""],
        [WORKDIR, "/tmp/", pathlib.Path("/tmp"), ""],
        # file_base absolute path with prefix, results stored in that dir (with prefix)
        [None, "/tmp/prefix", pathlib.Path("/tmp"), "prefix"],
        [WORKDIR, "/tmp/prefix", pathlib.Path("/tmp"), "prefix"],
        # file_base relative path with no prefix, results stored relative to workdir (no prefix)
        [None, "results/", pathlib.Path.cwd().joinpath("results"), ""],
        [WORKDIR, "results/", WORKDIR.joinpath("results"), ""],
        # file_base relative path with prefix, results stored relative to workdir (with prefix)
        [None, "results/prefix", pathlib.Path.cwd().joinpath("results"), "prefix"],
        [WORKDIR, "results/prefix", WORKDIR.joinpath("results"), "prefix"],
        # file_base relative path with ./ and prefix, results stored relative to workdir (with prefix)
        [None, "./results/prefix", pathlib.Path.cwd().joinpath("results"), "prefix"],
        [WORKDIR, "./results/prefix", WORKDIR.joinpath("results"), "prefix"],
        # file_base relative path with ../ and prefix, results stored one dir above workdir (with prefix)
        [
            None,
            "../results/prefix",
            pathlib.Path.cwd().parent.joinpath("results"),
            "prefix",
        ],
        [WORKDIR, "../results/prefix", WORKDIR.parent.joinpath("results"), "prefix"],
        # file_base prefix only, store in working dir with prefix
        [None, "prefix", pathlib.Path.cwd(), "prefix"],
        [WORKDIR, "prefix", WORKDIR, "prefix"],
    ],
)
def test_find_results_dir(
    folder_setup, working_dir, file_base, expected_output_dir, expected_prefix
):
    """
    Check that results dir is correctly resolved.
    """
    with MooseRun() as run:
        run.workdir_path = (
            pathlib.Path(working_dir) if working_dir else pathlib.Path.cwd()
        )
        run.moose_file_paths = [
            pathlib.Path(__file__).joinpath("example_data", "example_input_1.i"),
        ]
        run._file_base = file_base
        output_dir, prefix = run._find_results_dir()

        assert output_dir == expected_output_dir
        assert prefix == expected_prefix
