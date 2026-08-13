"""MOOSE Connector.

This module provides functionality for using Simvue to track and monitor a MOOSE simulation.
"""

import csv
import os
import pathlib
import re
import shutil
import time
import typing
from collections import defaultdict
from functools import reduce
from itertools import islice

import numpy

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

import multiparser.parsing.file as mp_file_parser
import multiparser.parsing.tail as mp_tail_parser
import pandas
import pydantic
import simvue
from simvue_connector.connector import WrappedRun
from simvue_connector.extras.create_command import format_command_env_vars


class MooseRun(WrappedRun):
    """Class for setting up Simvue to track and monitor of a MOOSE simulation.

    Use this class as a context manager, in the same way you use default Simvue runs, and call run.launch(). Eg:

    with MooseRun() as run:
        run.init(
            name="moose_simulation",
        )
        run.launch(...)

    NOTE: The connector currently does not support running MOOSE on Windows.
    """

    _patterns: dict[str, typing.Pattern] = {
        "time_step": re.compile(
            r"Time Step ([\deE\.\+\-]+), time = ([\deE\.\+\-]+), dt = .*"
        ),
        "converged": re.compile(r"\s*Solve Converged!\s*"),
        "non_converged": re.compile(r"\s*Solve Did NOT Converge\s*", re.IGNORECASE),
        "terminated": re.compile(
            r"Terminator '(.+)' is causing the execution to terminate."
        ),
        "nonlinear": re.compile(r"\s*(\d+) Nonlinear \|R\| = ([\deE\.\+\-]+)"),
        "linear": re.compile(r"\s*(\d+) Linear \|R\| = ([\deE\.\+\-]+)"),
    }

    def __init__(
        self,
        mode: typing.Literal["online", "offline", "disabled"] = "online",
        abort_callback: typing.Callable[[Self], None] | None = None,
        server_token: str | None = None,
        server_url: str | None = None,
        debug: bool = False,
        server_profile: str | None = None,
    ):
        """Initialize the MooseRun instance.

        If `abort_callback` is provided the first argument must be this Run instance.

        Parameters
        ----------
        mode : typing.Literal['online', 'offline', 'disabled'], optional
            mode of running, by default 'online':
                online - objects sent directly to Simvue server
                offline - everything is written to disk for later dispatch
                disabled - disable monitoring completelyby default "online"
        abort_callback : typing.Callable[[Self], None] | None, optional
            callback executed when the run is aborted, by default None
        server_token : str | None, optional
            overwrite value for server token, by default None
        server_url : str | None, optional
            overwrite value for server URL, by default None
        debug : bool, optional
            run in debug mode, by default False
        server_profile : str | None, optional
            specify alternative profile to use for server, this assumes
            additional profiles have been specified in the configuration.
            Default is to use the main server.

        """
        self.moose_application_path: pydantic.FilePath = None
        self.moose_file_path: pydantic.FilePath = None
        self.workdir_path: pathlib.Path | None = None
        self.upload_files: list[str] | None = None
        self.upload_miscellaneous_logs: bool = False
        self.track_vector_postprocessors: bool = None
        self.moose_cli_options: typing.Dict[str, typing.Any] = None
        self.run_in_parallel: bool = None
        self.num_processors: int = None
        self.parallel_cli_options: typing.Dict[str, typing.Any] = None

        self._output_dir_path: pathlib.Path = None
        self._file_base: str | None = None
        self._results_prefix: str = None
        self._time = time.time()
        # This represents the step number and time of the step, ie when MOOSE says 'Time Step X, time = Y'
        self._step_num = 0
        self._step_time = 0
        # Initialize counters for keeping track of the number of linear and nonlinear steps involved in each solve
        self._nonlinear = 0
        self._linear = 0
        self._dt = None
        self._steady = False
        self._unsupported_vectors: list[str] = []
        self._loading_historic_run = False
        self._log_header_key: str | None = None
        self._postprocessor_block = False
        self._header_metadata = defaultdict(dict)

        super().__init__(
            mode=mode,
            abort_callback=abort_callback,
            server_token=server_token,
            server_url=server_url,
            debug=debug,
            server_profile=server_profile,
        )

    def _find_results_dir(
        self,
    ) -> tuple[pathlib.Path, str]:
        """Find the directory and file prefix which results from the MOOSE file will be stored with.

        Should account for the following cases:
            - No `file_base` provided - output to input file parent, file prefix is input file stem
            - Absolute `file_base` - set this as the output dir and prefix
            - Relative `file_base` - put files relative to working_dir

        Returns
        -------
        pathlib.Path
            The path to the output directory where results will be generated
        str
            The file prefix which results will be generated with

        """
        workdir = self.workdir_path if self.workdir_path else pathlib.Path.cwd()

        if not self._file_base:
            if self.workdir_path:
                print(
                    "Warning: No file_base specified in MOOSE file - results will be generated in parent directory of input file."
                )
            # Uses directory containing input file, with moose file name stem as prefix
            return self.moose_file_path.parent, self.moose_file_path.stem

        # Try splitting on final slash
        split = self._file_base.rsplit("/", maxsplit=1)
        # If not split, no slashes, so only contains file prefix
        # It then puts things in the *working directory*, not input file parent
        if len(split) < 2:
            return (
                workdir,
                split[0],
            )

        dir_path, results_prefix = split

        # Check if absolute path
        if dir_path.startswith("/"):
            if self.workdir_path:
                print(
                    "Warning: Absolute file path detected in MOOSE input file - outputs will be generated here and not in the workdir_path provided."
                )
            return pathlib.Path(dir_path), results_prefix
        # Otherwise, relative path, should be relative to working dir
        return workdir.joinpath(dir_path).resolve(), results_prefix

    def _moose_input_parser(self, input_file: pathlib.Path) -> dict[str, typing.Any]:
        """Parse MOOSE input file, and create a dictionary of metadata with dot notation representing indentation of keys.

        Parameters
        ----------
        input_file: pathlib.Path
            The path to the MOOSE input file

        Returns
        -------
        dict[str, typing.Any]
            The MOOSE input file as a dictionary of metadata

        """
        input_metadata = {}
        # Will make a list of keys for each value to create a nested dict
        keys = []

        with open(input_file, "r") as file:
            for line in file:
                line = line.strip()
                # Find lines which represent ends of blocks
                # Could be similar to [] or [../] - so check for square brackets with any number of non alphanumeric chars between
                if re.search(r"\[[^\w]*\]", line):
                    # Remove that block from the key - split at the last dot in the key and remove what comes after
                    keys.pop()
                # Find lines which represent starts of new blocks
                # Eg [Mesh] - so look for square brackets with any characters between (already screened out end blocks above)
                elif new_key := re.search(r"\[.+\]", line):
                    # Add the title of the new block to the key, dot separated notation
                    # Remove './' from before the titles of blocks if present
                    # Replace a '.' with '_' to prevent issues with dot notation of keys, but still allow users to use dots in block names
                    keys.append(f"{new_key.group().strip('[]/').replace('./', '')}")
                # Find lines which represent a key value pair, <key> = <value>
                # Make sure to remove in line comments from the value
                elif match := re.search(r"(\w*)\s*=\s*([^#]+)(#+.*)?", line):
                    # If the value ends with a ;, it means it is a multi line array input
                    # Not interested in uploading long inputs like these as metadata, so ignore for now
                    if ";" in match.group(2):
                        continue
                    try:
                        val = float(match.group(2).strip())
                    except ValueError:
                        val = match.group(2).strip()

                    # Create nested dict by reducing list of keys and creating a nested dict if not already existing,
                    # then set the final key: value pair as normal
                    reduce(lambda d, key: d.setdefault(key, {}), keys, input_metadata)[
                        match.group(1)
                    ] = val
        return input_metadata

    def _moose_input_callback(self, input_metadata: dict[str, typing.Any]) -> None:
        """Extract useful information from the MOOSE input file metadata.

        Parameters
        ----------
        input_metadata: dict[str, typing.Any]
            The metadata from the MOOSE input file

        """
        # Find the location to output files
        self._file_base = input_metadata.get("Outputs", {}).get("file_base", None)
        self._output_dir_path, self._results_prefix = self._find_results_dir()

        if _executioner_metadata := input_metadata.get("Executioner", None):
            if _dt := _executioner_metadata.get("dt", None):
                try:
                    self._dt = float(_dt)
                except ValueError:
                    print(
                        "WARNING: Could not interpret Executioner.dt as a number, falling back to log times and steps. To correct this, make sure 'dt' is a number in your MOOSE input file."
                    )

            if _executioner_metadata.get("type", "").lower() == "steady":
                self._steady = True
        else:
            print(
                "No Executioner block detected in your MOOSE input file - falling back to log times and steps."
            )

        self.update_metadata({self.moose_file_path.stem: input_metadata})

    def _log_header_parser(self, line: str) -> None:
        """Parse a line from the header of the MOOSE log file and add the data to the header metadata dictionary.

        Parameters
        ----------
        line : str
            The line in the console log to parse.

        """
        # Add the data from the line of the header into the dictionary as a key/value pair
        # Ignore blank lines and lines which don't contain a colon
        if not line.strip() or ":" not in line:
            return

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        # Replace any characters which will fail server side validation of key name with dashes
        key = key.replace(" ", "_").lower()
        key = re.sub(r"[^\w\-\s\.]+", "-", key)

        # Corresponds to a title - set log header key
        if not value:
            self._log_header_key = key
            return

        self._header_metadata[self._log_header_key][key] = value
        return

    def _log_parser(
        self, file_content: str, **__
    ) -> tuple[dict[str, typing.Any], dict[str, list[str]]]:
        """Parse each line from the log file.

        Parameters
        ----------
        file_content : str
            A collection of lines from the log file
        **__
            Additional unused keyword arguments

        Returns
        -------
        dict[str, typing.Any]
            Unused dictionary of metadata
        dict[str, list[str]]
           Dictionary containing a list of lines to process

        """
        return {}, {"lines": file_content.splitlines()}

    def _log_callback(self, data: dict[str, list[str]], _: dict[str, str]) -> None:
        """Extract relevant information from each line in log file and upload.

        Parameters
        ----------
        data: dict[str, list[str]]
            Lines from the log file since last read

        """
        for line in data["lines"]:
            # First, check if we are inside the framework information header
            if "Framework Information:" in line or self._log_header_key:
                self._log_header_parser(line)

            if "Postprocessor Values:" in line:
                self._postprocessor_block = True
                continue

            if self._postprocessor_block:
                if not line.strip():
                    # Empty line at end of postprocessor table
                    self._postprocessor_block = False
                continue

            # Look for relevant keys in the dictionary of data which we are passed in, and log the event with Simvue
            for name, pattern in self._patterns.items():
                match = pattern.search(line)
                if not match:
                    continue

                # There is no way to reliably define when we are at the end of the header to extract metadata from
                # So we will check against our other log patterns each time, and if one matches, we must be at the end
                # So disable framework header parsing, update metadata, check for static solve
                if self._log_header_key:
                    self._log_header_key = None
                    self.update_metadata({"moose": self._header_metadata})

                    # Check if static solve, in case not found in input file
                    if (
                        self._header_metadata.get("execution_information", {})
                        .get("executioner", "")
                        .lower()
                        == "steady"
                    ):
                        self._steady = True

                if name == "time_step":
                    self.log_event(line.rstrip())
                    self._time = time.time()
                    self._step_num = int(match.group(1))
                    self._step_time = float(match.group(2))

                    # Reset counters
                    self._linear = 0
                    self._nonlinear = 0

                elif name in ("converged", "non_converged"):
                    self.log_event(line.rstrip().title())
                    if not self._loading_historic_run and not self._steady:
                        self.log_event(
                            f" Step calculation time: {round((time.time() - self._time), 2)} seconds."
                        )
                    self.log_event(f" Total Nonlinear Iterations: {self._nonlinear}.")
                    self.log_event(f" Total Linear Iterations: {self._linear}.")

                    if not self._steady:
                        self.log_metrics(
                            {
                                "total_linear_iterations": self._linear,
                                "total_nonlinear_iterations": self._nonlinear,
                            },
                            self._step_num,
                            self._step_time,
                        )

                # Keep track of total number of linear and nonlinear iterations in the solve
                elif name == "nonlinear":
                    self._nonlinear += 1
                    if self._steady:
                        self.log_event(
                            f"Beginning Nonlinear Iteration {match.group(1)}"
                        )
                        self.log_metrics(
                            {"nonlinear_iteration_residuals": float(match.group(2))},
                            step=self._nonlinear,
                        )
                elif name == "linear":
                    self._linear += 1
                    if self._steady:
                        self.log_metrics(
                            {"linear_iteration_residuals": float(match.group(2))},
                            step=self._linear,
                        )

                elif name == "terminated":
                    self.log_event(line.rstrip())
                    self._terminated = True

                    terminator = match.group(1)

                    self.update_metadata({terminator: True})
                    self.update_tags(
                        [
                            terminator,
                        ]
                    )
                break
            else:
                # No pattern matched, and not in PostProcessor block, upload line as event
                # Note there is no reliable way to tell when a header block has ended and a misc log has begun
                # Since header info is only printed once, will also upload this as Events
                # So that misc log lines between header and solve beginning are not missed
                if self.upload_miscellaneous_logs and line.strip():
                    self.log_event(line)

        return {}, {}

    @mp_file_parser.file_parser
    def _vector_postprocessor_parser(
        self,
        input_file: str,
        **__,
    ) -> tuple[dict[str, str], dict[str, numpy.ndarray]]:
        """Parse data from VectorPostProcessor CSV files.

        Parameters
        ----------
        input_file : str
            Path to the VectorPostProcessor CSV file
        **__
            Additional unused keyword arguments

        Returns
        -------
        tuple[dict[str, str], dict[str, numpy.ndarray]]
            A dictionary of metadata and data contained in the CSV file

        """
        metrics = {}
        current_time_data = None
        # Get name of vector which is being calculated by VectorPostProcessor from filename
        file_name = pathlib.Path(input_file).stem
        vector_name, serial_num = file_name.replace(
            f"{self._results_prefix}_",
            "",
            1,
        ).rsplit("_", 1)
        serial_num = int(serial_num)

        # If no file_base provided, and not using a custom CSV output section, MOOSE will prepend '_out' to name
        # So remove this, if present. If using custom CSV block,
        # it'll prepend the name of that block, but we have no good way of knowing what that is to remove it
        if not self._file_base and vector_name.startswith("out_"):
            vector_name = vector_name.replace("out_", "", 1)

        # If user has enabled time_data in their MOOSE file, get latest line from this file and save time
        time_file = f"{input_file.rsplit('_', 1)[0]}_time.csv"
        if pathlib.Path(time_file).exists():
            with open(time_file, newline="\n") as in_t:
                reader = csv.reader(in_t)
                # Read line in time file corresponding to this step
                # + 1 to skip header
                current_time_data = next(
                    islice(reader, serial_num + 1, serial_num + 2), None
                )
        if current_time_data:
            metrics["time"] = current_time_data[0]
            metrics["step"] = current_time_data[1]
        else:
            metrics["step"] = serial_num
            if self._dt:
                metrics["time"] = metrics["step"] * self._dt

        data = pandas.read_csv(input_file)

        varying_dims = []
        for coord in ("x", "y", "z", "radius"):
            if coord not in data.columns:
                continue

            dimension = data.pop(coord)

            if dimension.nunique() == 0:
                continue
            elif dimension.nunique() != 1:
                varying_dims.append(dimension)

        if "id" in data.columns:
            _id = data.pop("id")
            if len(varying_dims) < 1 and _id.nunique() > 0:
                # Fallback to using ID as axis if no coord found
                varying_dims = [_id]

        if len(varying_dims) > 1:
            print(
                f"Warning: VectorPostProcessor {vector_name} varies in more than one dimension, which is unsupported. Ignoring..."
            )
            self._unsupported_vectors.append(vector_name)
            if not self._loading_historic_run:
                self.file_monitor.exclude(
                    f"{self._output_dir_path / self._results_prefix}*_{vector_name}_[0-9][0-9][0-9][0-9].csv"
                )
            return {}, {}
        if len(varying_dims) < 1:
            # Likely an empty file representing initial conditions, ignore...
            return {}, {}

        for col in data.columns:
            metric_name = f"{vector_name}.{col}"
            # Assign to grid if required
            if metric_name not in self._grids.keys():
                self.assign_metric_to_grid(
                    metric_name=metric_name,
                    axes_ticks=[varying_dims[0].values],
                    axes_labels=[varying_dims[0].name],
                )
            metrics[metric_name] = data[col].values

        return {}, metrics

    def _per_metric_callback(
        self,
        csv_data: typing.Dict[str, float | numpy.ndarray],
        sim_metadata: typing.Dict[str, str],
    ) -> None:
        """Monitor each line in the results CSV file, and add data from it to Simvue Metrics.

        Parameters
        ----------
        csv_data : typing.Dict[str, float | numpy.ndarray]
            The data from the latest line in the CSV file
        sim_metadata : typing.Dict[str, str]
            The metadata about when this line was read by Multiparser

        """
        if not csv_data:
            return

        metric_time = csv_data.pop("time", None)
        metric_step = csv_data.pop("step", None)
        timestamp = sim_metadata.get("timestamp", "").replace(" ", "T")

        if self._dt and metric_step is None:
            # Has come from a scalar PostProcessor, can assume step = time / dt
            metric_step = int(metric_time / self._dt)

        # Log all results for this timestep as Metrics
        self.log_metrics(
            csv_data,
            step=metric_step if metric_step is not None else self._step_num,
            time=metric_time if metric_time is not None else self._step_time,
            timestamp=timestamp if timestamp else None,
        )

    def _pre_simulation(self):
        """Upload information to Simvue before the MOOSE simulation begins."""
        super()._pre_simulation()

        # Add alert for a non converging step
        self.create_event_alert(
            name="step_not_converged",
            frequency=1,
            pattern="Solve Did Not Converge",
            notification="email",
        )
        if self.workdir_path:
            # Ensure workdir path exists
            self.workdir_path.mkdir(parents=True, exist_ok=True)

        # Save the MOOSE file for this run to the Simvue server
        if pathlib.Path(self.moose_file_path).exists() and (
            self.upload_files is None or self.moose_file_path.name in self.upload_files
        ):
            self.save_file(self.moose_file_path, category="input")

        # Save the MOOSE Makefile
        if (
            pathlib.Path(self.moose_application_path)
            .parent.joinpath("Makefile")
            .exists()
        ):
            self.save_file(
                pathlib.Path(self.moose_application_path).parent.joinpath("Makefile"),
                category="input",
            )

        # Parse the MOOSE input file
        input_metadata = self._moose_input_parser(pathlib.Path(self.moose_file_path))

        # Extract useful information
        self._moose_input_callback(input_metadata)

        # Add the MOOSE simulation as a process, so that Simvue can abort it if alerts begin to fire
        command = []
        if self.run_in_parallel:
            launcher = None
            if os.getenv("SLURM_JOB_ID"):
                launcher = shutil.which("srun")

            launcher = launcher or shutil.which("mpiexec") or shutil.which("mpirun")
            if not launcher:
                raise RuntimeError(
                    "Run in parallel requested, but could not find srun, mpiexec, or mpirun executables. Make sure one of these is available in your environment."
                )
            command.append(launcher)
            command += ["-n", str(self.num_processors)]
            command += format_command_env_vars(self.parallel_cli_options)
        command += [
            str(self.moose_application_path.absolute()),
            "-i",
            str(self.moose_file_path.absolute()),
            "--color",
            "off",
        ]
        command += format_command_env_vars(self.moose_cli_options)

        # Delete .out file, if it exists, so we don't upload old events
        pathlib.Path(f"{self.name}_moose_simulation.out").unlink(missing_ok=True)

        self.add_process(
            "moose_simulation",
            *command,
            completion_trigger=self._trigger,
            cwd=self.workdir_path,
        )

    def _during_simulation(self):
        """Describe which files should be monitored during the simulation by Multiparser."""
        self.log_event("Beginning MOOSE simulation...")

        # Record time here, for that for static problems the overall time for execution will be returned
        self._time = time.time()

        self.file_monitor.exclude(
            str(self._output_dir_path.joinpath(f"{self._results_prefix}_*_time.csv"))
        )

        # Monitor each line added to the MOOSE log file as the simulation proceeds and look out for certain phrases to upload to Simvue
        self.file_monitor.tail(
            path_glob_exprs=f"{self.name}_moose_simulation.out",
            parser_func=mp_tail_parser.log_parser(self._log_parser),
            callback=self._log_callback,
        )
        # Monitor each line added to the MOOSE results file as the simulation proceeds, and upload results to Simvue
        self.file_monitor.tail(
            path_glob_exprs=[
                f"{self._output_dir_path / self._results_prefix}.csv",
                f"{self._output_dir_path / self._results_prefix}_*[!0-9].csv",
            ],
            parser_func=mp_tail_parser.record_csv,
            callback=self._per_metric_callback,
        )
        # Monitor each file created by a Vector PostProcessor, and upload results to Simvue if file matches an expected form.
        if self.track_vector_postprocessors:
            self.file_monitor.track(
                path_glob_exprs=f"{self._output_dir_path / self._results_prefix}*_[0-9][0-9][0-9][0-9].csv",
                parser_func=self._vector_postprocessor_parser,
                callback=self._per_metric_callback,
                static=True,
            )

    def _post_simulation(self):
        """Upload information to Simvue after the MOOSE simulation finishes."""
        # If only header information logged, and then MOOSE stops, still want to upload this info
        if self._log_header_key and self._header_metadata:
            self.update_metadata({"moose": self._header_metadata})

        if self.upload_files is None:
            files_to_upload = self._output_dir_path.glob(f"{self._results_prefix}*")
        else:
            files_to_upload = (
                self._output_dir_path.joinpath(file)
                for file in self.upload_files
                if file != self.moose_file_path.name
            )

        for file in files_to_upload:
            if (
                file.is_dir()
                or file.absolute() == pathlib.Path(self.moose_file_path).absolute()
            ):
                continue
            self.save_file(file, category="output")

        super()._post_simulation()

    @simvue.utilities.prettify_pydantic
    @pydantic.validate_call
    def launch(
        self,
        moose_application_path: pydantic.FilePath,
        moose_file_path: pydantic.FilePath,
        workdir_path: str | pathlib.Path | None = None,
        upload_files: list[str] | None = None,
        upload_miscellaneous_logs: bool = False,
        track_vector_postprocessors: bool = False,
        moose_cli_options: typing.Optional[typing.Dict[str, typing.Any]] = None,
        run_in_parallel: bool = False,
        num_processors: int = 1,
        parallel_cli_options: typing.Optional[typing.Dict[str, typing.Any]] = None,
    ):
        """Command to launch the MOOSE simulation and track it with Simvue.

        Note the following current limitations:
            * Only 1D Vector PostProcessors, varying along one cartesian direction, are supported
            * If outputting scalar postprocessors with a custom `CSV` block in your input file, the name of this
              block should not end in a number (or alternatively a `file_base` should be specified).

        Parameters
        ----------
        moose_application_path : pydantic.FilePath
            Path to the MOOSE application file
        moose_file_path : pydantic.FilePath
            Path to the MOOSE configuration file
        workdir_path : str | pathlib.Path | None, optional
            Path to a directory which you would like MOOSE to run in, by default None
            If this directory does not exist, it will be created.
            Note that relative paths in your MOOSE input file will be resolved relative to this directory.
            If `file_base` is not specified, MOOSE will output results to the parent directory of the input file regardless of this setting.
            Uses the current working directory by default.
        upload_files : list[str] | None, optional
            List of results file names to upload to the Simvue server for storage, by default None
            Results should be supplied relative to the output directory provided in the MOOSE file,
            and/or specify the name of the input file.
            If not specified, will upload all files by default. If you want no results files to be uploaded, provide an empty list.
        upload_miscellaneous_logs : bool, optional
            Whether to upload any lines from the console log which have not been otherwise handled as Events, by default False
            Note - this may cause a large volume of events to be uploaded!
        track_vector_postprocessors : bool, optional
            Whether to track CSV outputs from Vector PostProcessors, by default False
        moose_cli_options : typing.Optional[typing.Dict[str, typing.Any]], optional
            Any options to be passed to MOOSE on startup, by default None
        run_in_parallel: bool, optional
            Whether to run the MOOSE simulation in parallel, by default False
        num_processors : int, optional
            The number of processors to run a parallel MOOSE job across, by default 1
        parallel_cli_options : typing.Optional[typing.Dict[str, typing.Any]]
            Any options to pass to the parallel job launcher on startup if running in parallel, by default None
            These will be applied to whichever parallel job launcher is found first.
            Looks for `srun` if running on a SLURM system, then `mpiexec`, then `mpirun`.

        """
        self.moose_application_path = moose_application_path
        self.moose_file_path = moose_file_path
        self.workdir_path = pathlib.Path(workdir_path) if workdir_path else None
        self.upload_files = upload_files
        self.upload_miscellaneous_logs = upload_miscellaneous_logs
        self.track_vector_postprocessors = track_vector_postprocessors
        self.moose_cli_options = moose_cli_options or {}
        self.run_in_parallel = run_in_parallel
        self.num_processors = num_processors
        self.parallel_cli_options = parallel_cli_options or {}

        super().launch()

    @simvue.utilities.prettify_pydantic
    @pydantic.validate_call
    def load(
        self,
        moose_file_path: pydantic.FilePath,
        results_dir: pydantic.DirectoryPath | None = None,
        log_path: pydantic.FilePath | None = None,
        upload_files: list[str] | None = None,
        upload_miscellaneous_logs: bool = False,
        track_vector_postprocessors: bool = False,
    ):
        """Command to load a set of results from a MOOSE simulation into Simvue.

        Parameters
        ----------
        moose_file_path : pydantic.FilePath
            Path to the MOOSE configuration file
        results_dir : pydantic.DirectoryPath | None, optional
            A path to a directory containing MOOSE simulation results to upload
            By default, will use the location specified in the MOOSE file, or fallback to current working directory if file_base not specified.
        log_path : pydantic.FilePath | None, optional
            The path to a file containing MOOSE console output to parse for this run
            By default, will try to find a `.out` or `.txt` file in the results directory.
            Will raise an exception if multiple files are found and a log_path is not provided.
        upload_files : list[str] | None, optional
            List of results file names to upload to the Simvue server for storage, by default None
            Results should be supplied relative to the output directory provided in the MOOSE file,
            and/or specify the name of the input file.
            If not specified, will upload all files by default. If you want no results files to be uploaded, provide an empty list.
        upload_miscellaneous_logs : bool, optional
            Whether to upload any lines from the console log which have not been otherwise handled as Events, by default False
            Note - this may cause a large volume of events to be uploaded!
        track_vector_postprocessors : bool, optional
            Whether to track CSV outputs from Vector PostProcessors, by default False

        Raises
        ------
        FileNotFoundError
            Raised if no results directory is found at expected location
        RuntimeError
            Found if more than one potential console log file is found, and the user did not specify the one to use.

        """
        self.moose_file_path = moose_file_path
        self.upload_files = upload_files
        self.upload_miscellaneous_logs = upload_miscellaneous_logs
        self.track_vector_postprocessors = track_vector_postprocessors
        self._loading_historic_run = True

        # Save the MOOSE file for this run to the Simvue server
        if self.upload_files is None or self.moose_file_path.name in self.upload_files:
            self.save_file(self.moose_file_path, category="input")

        # Parse the MOOSE input file
        input_metadata = self._moose_input_parser(pathlib.Path(self.moose_file_path))

        # Extract useful information
        self._moose_input_callback(input_metadata)

        if results_dir:
            if (
                self._file_base
                and results_dir.resolve() != self._output_dir_path.resolve()
            ):
                print(
                    f"Warning: File base specified in input file, '{self._file_base}', conflicts with user provided results_dir path. The path in the MOOSE input file will be ignored."
                )
            self._output_dir_path = results_dir

        if not self._output_dir_path.exists():
            raise FileNotFoundError(
                f"No results directory found at '{self._output_dir_path}'!"
            )

        if not log_path:
            candidate_paths = list(
                self._output_dir_path.glob(f"{self._results_prefix}*.txt")
            ) + list(self._output_dir_path.glob("*_moose_simulation.out"))
            if len(candidate_paths) > 1:
                raise RuntimeError(
                    "More than one potential console log file found in results directory. Please specify the one to use as the `log_path` parameter to the connector."
                )
            log_path = candidate_paths[0] if candidate_paths else None

        if log_path and log_path.exists():
            # Parse line by line, matching regex patterns, upload as Events if found
            meta, data = self._log_parser(log_path.read_text())
            self._log_callback(data, meta)

        scalar_csv_paths = list(
            self._output_dir_path.glob(f"{self._results_prefix}.csv")
        ) + list(self._output_dir_path.glob(f"{self._results_prefix}_*[!0-9].csv"))

        for path in scalar_csv_paths:
            # Ignore Vector PostProcessor time files
            if path.match(f"{self._results_prefix}_*_time.csv"):
                continue
            with open(path, "r") as _file:
                for _step, _metric in enumerate(csv.DictReader(_file)):
                    _data = {key: float(value) for key, value in _metric.items()}
                    _data["step"] = _step
                    self._per_metric_callback(_data, {})

        if self.track_vector_postprocessors:
            vector_csv_paths = self._output_dir_path.glob(
                f"{self._results_prefix}*_[0-9][0-9][0-9][0-9].csv"
            )
            for path in vector_csv_paths:
                if any(
                    path.match(
                        f"{self._results_prefix}_*{vector_name}_[0-9][0-9][0-9][0-9].csv"
                    )
                    for vector_name in self._unsupported_vectors
                ):
                    continue
                _, data = self._vector_postprocessor_parser(input_file=str(path))
                self._per_metric_callback(data, {})

        self._post_simulation()

        super().load()
