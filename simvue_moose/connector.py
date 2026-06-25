"""MOOSE Connector.

This module provides functionality for using Simvue to track and monitor a MOOSE simulation.
"""

import csv
import pathlib
import re
import time
import typing
from functools import reduce
from itertools import islice

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
    """

    _patterns: dict[str, typing.Pattern] = {
        "time_step": re.compile(r"Time Step.*"),
        "converged": re.compile(r"\s*Solve Converged!\s*"),
        "non_converged": re.compile(r"\s*Solve Did NOT Converge\s*"),
        "terminated": re.compile(
            r"Terminator '.+' is causing the execution to terminate."
        ),
        "nonlinear": re.compile(r" \d+ Nonlinear \|R\|"),
        "linear": re.compile(r"     \d+ Linear \|R\|"),
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
        self.workdir_path: pathlib.Path = None
        self.upload_files: list[str] | None = None
        self.track_vector_postprocessors: bool = None
        self.track_vector_positions: bool = None
        self.moose_env_vars: typing.Dict[str, typing.Any] = None
        self.run_in_parallel: bool = None
        self.num_processors: int = None
        self.mpiexec_env_vars: typing.Dict[str, typing.Any] = None

        self._output_dir_path: pathlib.Path = None
        self._results_prefix: str = None
        self._time = time.time()
        # This represents the step number and time of the step, ie when MOOSE says 'Time Step X, time = Y'
        self._step_num = 0
        self._step_time = 0
        # Initialize counters for keeping track of the number of linear and nonlinear steps involved in each solve
        self._nonlinear = 0
        self._linear = 0
        self._dt = None
        self._loading_historic_run = False

        super().__init__(
            mode=mode,
            abort_callback=abort_callback,
            server_token=server_token,
            server_url=server_url,
            debug=debug,
            server_profile=server_profile,
        )

    def _find_results_dir(
        self, file_base: str | None = None
    ) -> tuple[pathlib.Path, str]:
        """Find the directory and file prefix which results from the MOOSE file will be stored with.

        Should account for the following cases:
            - No `file_base` provided - output to working dir, file prefix is input file stem
            - Absolute `file_base` - set this as the output dir and prefix
            - Relative `file_base` - put files relative to working_dir

        Parameters
        ----------
        file_base: str | None
            The file_base defined in the MOOSE input file, if present

        Returns
        -------
        pathlib.Path
            The path to the output directory where results will be generated
        str
            The file prefix which results will be generated with

        """
        if not file_base:
            # Uses working dir, with moose file name stem as prefix
            return self.workdir_path, self.moose_file_path.stem

        # Try splitting on final slash
        split = file_base.rsplit("/", maxsplit=1)
        # If not split, no slashes, so only contains file prefix
        if len(split) < 2:
            return self.workdir_path, split[0]

        dir_path, results_prefix = split

        # Check if absolute path
        if dir_path.startswith("/"):
            return pathlib.Path(dir_path), results_prefix
        # Otherwise, relative path, should be relative to working dir
        return self.workdir_path.joinpath(dir_path).resolve(), results_prefix

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
        file_base = input_metadata.get("Outputs", {}).get("file_base", None)
        self._output_dir_path, self._results_prefix = self._find_results_dir(file_base)

        if not input_metadata.get("Executioner", None):
            print(
                "No Executioner block detected in your MOOSE input file - falling back to log times and steps."
            )

        elif _dt := input_metadata["Executioner"].get("dt", None):
            try:
                self._dt = float(_dt)
            except ValueError:
                print(
                    "WARNING: Could not interpret Executioner.dt as a number, falling back to log times and steps. To correct this, make sure 'dt' is a number in your MOOSE input file."
                )

        self.update_metadata({self.moose_file_path.stem: input_metadata})

    @mp_file_parser.file_parser
    def _moose_header_parser(
        self, input_file: str, **__
    ) -> tuple[dict[str, str], dict[str, str | float | int]]:
        """Parse the header of the MOOSE log file and return the data from it as a dictionary.

        Parameters
        ----------
        input_file : str
            The path to the file where the console log is stored.
        **__
            Additional unused keyword arguments

        Returns
        -------
        tuple[dict[str, str], dict[str, str | float | int]]
            An (empty) metadata dict,  and parsed data from the header of the MOOSE log file

        """
        # Open the log file, and read header lines (which contains information about the MOOSE version used etc)
        with open(input_file) as file:
            file_lines = file.readlines()
        file_lines = list(filter(None, file_lines))

        # Add the data from each line of the header into a dictionary as a key/value pair
        header_data = {"moose": {}}
        for line in file_lines:
            # Ignore blank lines and lines which don't contain a colon
            if not line.strip() or ":" not in line:
                continue
            # Break if execution has begun (outside of header)
            if "Currently Executing" in line:
                break
            key, value = line.split(":", 1)
            key = key.strip()
            key = key.replace(" ", "_").lower()
            # Replace any characters which will fail server side validation of key name with dashes
            key = re.sub(r"[^\w\-\s\.]+", "-", key)
            # Ignore lines which correspond to 'titles'
            if not value:
                continue
            value = value.strip()
            if not value:
                continue
            header_data["moose"][key] = value

        return {}, header_data

    @mp_file_parser.file_parser
    def _vector_postprocessor_parser(
        self,
        input_file: str,
        **__,
    ) -> tuple[dict[str, str], dict[str, float | int]]:
        """Parse data from VectorPostProcessor CSV files.

        Parameters
        ----------
        input_file : str
            Path to the VectorPostProcessor CSV file
        **__
            Additional unused keyword arguments

        Returns
        -------
        tuple[dict[str, str], dict[str, float | int]]
            A dictionary of metadata and data contained in the CSV file

        """
        metrics = {}
        current_time_data = None
        # Get name of vector which is being calculated by VectorPostProcessor from filename
        file_name = pathlib.Path(input_file).stem
        vector_name, serial_num = file_name.replace(
            f"{self._results_prefix}_", ""
        ).rsplit("_", 1)

        # If user has enabled time_data in their MOOSE file, get latest line from this file and save time
        time_file = f"{input_file.rsplit('_', 1)[0]}_time.csv"
        if pathlib.Path(time_file).exists():
            with open(time_file, newline="\n") as in_t:
                reader = csv.reader(in_t)
                current_time_data = next(
                    islice(reader, serial_num, serial_num + 1), None
                )
        if current_time_data:
            metrics["time"] = current_time_data[0]
            metrics["step"] = current_time_data[1]
        else:
            metrics["step"] = int(serial_num)
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

        _id = data.pop("id") if "id" in data.columns else None
        if len(varying_dims) < 1 and _id is not None and _id.nunique() > 0:
            varying_dims.append(_id)

        if len(varying_dims) > 1:
            print(varying_dims)
            print("Varying in too many dims!")
            return {}, {}
        if len(varying_dims) < 1:
            print("no dims found")
            return {}, {}

        for col in data.columns:
            # Assign to grid if required
            if col not in self._grids.keys():
                self.assign_metric_to_grid(
                    metric_name=f"{vector_name}.{col}",
                    axes_ticks=[varying_dims[0].values],
                    axes_labels=[varying_dims[0].name],
                )
            metrics[f"{vector_name}.{col}"] = data[col].values

        return {}, metrics

    def _per_event_callback(self, log_data: typing.Dict[str, str], _) -> bool:
        """Look out for certain phrases in the MOOSE log, and adds them to the Events log.

        Parameters
        ----------
        log_data : typing.Dict[str, str]
            Phrases of interest identified by the file monitor

        Returns
        -------
        bool
            Returns False if unable to upload events, to signal an error

        """
        # Look for relevant keys in the dictionary of data which we are passed in, and log the event with Simvue
        if any(
            key in ("time_step", "converged", "non_converged", "terminated")
            for key in log_data.keys()
        ):
            try:
                self.log_event(list(log_data.values())[0].rstrip())
            except RuntimeError as e:
                self._error(e)
                return False

        if "time_step" in log_data.keys():
            self._time = time.time()

            step_time = re.search(
                r"Time Step (\d+), time = (\d+), dt = .*", log_data["time_step"]
            )
            if step_time:
                self._step_num = int(step_time.group(1))
                self._step_time = float(step_time.group(2))

        elif "converged" in log_data.keys():
            if not self._loading_historic_run:
                self.log_event(
                    f" Step calculation time: {round((time.time() - self._time), 2)} seconds."
                )
            self.log_event(f" Total Nonlinear Iterations: {self._nonlinear}.")
            self.log_event(f" Total Linear Iterations: {self._linear}.")

            self.log_metrics(
                {
                    "total_linear_iterations": self._linear,
                    "total_nonlinear_iterations": self._nonlinear,
                },
                self._step_num,
                self._step_time,
            )

            self._linear = 0
            self._nonlinear = 0

        # Keep track of total number of linear and nonlinear iterations in the solve
        elif "nonlinear" in log_data.keys():
            self._nonlinear += 1
        elif "linear" in log_data.keys():
            self._linear += 1

        elif "terminated" in log_data.keys():
            self._terminated = True

            terminator = re.search(
                r"Terminator '(.+)' is causing the execution to terminate.",
                log_data["terminated"],
            ).group(1)

            self.update_metadata({terminator: True})
            self.update_tags(
                [
                    terminator,
                ]
            )

    def _per_metric_callback(
        self, csv_data: typing.Dict[str, float], sim_metadata: typing.Dict[str, str]
    ) -> None:
        """Monitor each line in the results CSV file, and add data from it to Simvue Metrics.

        Parameters
        ----------
        csv_data : typing.Dict[str, float]
            The data from the latest line in the CSV file
        sim_metadata : typing.Dict[str, str]
            The metadata about when this line was read by Multiparser

        """
        if not csv_data:
            return

        metric_time = csv_data.pop("time", None)
        metric_step = csv_data.pop("step", None)
        timestamp = sim_metadata.get("timestamp", "").replace(" ", "T")

        if self._dt and not metric_step:
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

        # Parse the MOOSE input file
        input_metadata = self._moose_input_parser(pathlib.Path(self.moose_file_path))

        # Extract useful information
        self._moose_input_callback(input_metadata)

        # Add alert for a non converging step
        self.create_event_alert(
            name="step_not_converged",
            frequency=1,
            pattern=" Solve Did NOT Converge!",
            notification="email",
        )

        # Save the MOOSE file for this run to the Simvue server
        if pathlib.Path(self.moose_file_path).exists() and (
            self.upload_files is None or self.moose_file_path in self.upload_files
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

        # Add the MOOSE simulation as a process, so that Simvue can abort it if alerts begin to fire
        command = []
        if self.run_in_parallel:
            command += ["mpiexec", "-n", str(self.num_processors)]
            command += format_command_env_vars(self.mpiexec_env_vars)
        command += [
            str(self.moose_application_path),
            "-i",
            str(self.moose_file_path),
            "--color",
            "off",
        ]
        command += format_command_env_vars(self.moose_env_vars)
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

        # Read the initial information within the log file when it is first created, to parse the header information
        self.file_monitor.track(
            path_glob_exprs=str(
                self._output_dir_path.joinpath(f"{self._results_prefix}.txt")
            ),
            callback=lambda header_data, metadata: self.update_metadata(
                {**header_data, **metadata}
            ),
            parser_func=self._moose_header_parser,
            static=True,
        )
        # Monitor each line added to the MOOSE log file as the simulation proceeds and look out for certain phrases to upload to Simvue
        self.file_monitor.tail(
            path_glob_exprs=str(
                self._output_dir_path.joinpath(f"{self._results_prefix}.txt")
            ),
            callback=self._per_event_callback,
            tracked_values=list(self._patterns.values()),
            labels=list(self._patterns.keys()),
        )
        # Monitor each line added to the MOOSE results file as the simulation proceeds, and upload results to Simvue
        self.file_monitor.tail(
            path_glob_exprs=str(
                self._output_dir_path.joinpath(f"{self._results_prefix}.csv")
            ),
            parser_func=mp_tail_parser.record_csv,
            callback=self._per_metric_callback,
        )
        self.file_monitor.exclude(
            str(self._output_dir_path.joinpath(f"{self._results_prefix}_*_time.csv"))
        )
        # Monitor each file created by a Vector PostProcessor, and upload results to Simvue if file matches an expected form.
        if self.track_vector_postprocessors:
            self.file_monitor.track(
                path_glob_exprs=str(
                    self._output_dir_path.joinpath(f"{self._results_prefix}_*.csv")
                ),
                parser_func=self._vector_postprocessor_parser,
                callback=self._per_metric_callback,
                static=True,
            )

    def _post_simulation(self):
        """Upload information to Simvue after the MOOSE simulation finishes."""
        if self.upload_files is None:
            files_to_upload = self._output_dir_path.glob(f"{self._results_prefix}*")
        else:
            files_to_upload = (
                self._output_dir_path.joinpath(file) for file in self.upload_files
            )

        for file in files_to_upload:
            if file.absolute() == pathlib.Path(self.moose_file_path).absolute():
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
        track_vector_postprocessors: bool = False,
        track_vector_positions: bool = False,
        moose_env_vars: typing.Optional[typing.Dict[str, typing.Any]] = None,
        run_in_parallel: bool = False,
        num_processors: int = 1,
        mpiexec_env_vars: typing.Optional[typing.Dict[str, typing.Any]] = None,
    ):
        """Command to launch the MOOSE simulation and track it with Simvue.

        Parameters
        ----------
        moose_application_path : pydantic.FilePath
            Path to the MOOSE application file
        moose_file_path : pydantic.FilePath
            Path to the MOOSE configuration file
        workdir_path : str | pathlib.Path | None, optional
            Path to a directory which you would like MOOSE to run in, by default None
            This is where FDS will generate the results from the simulation
            If a directory does not already exist at this path, it will be created
            Uses the current working directory by default.
        upload_files : list[str] | None, optional
            List of results file names to upload to the Simvue server for storage, by default None
            These should be supplied relative to the output directory provided in the MOOSE file.
            If not specified, will upload all files by default. If you want no results files to be uploaded, provide an empty list.
        track_vector_postprocessors : bool, optional
            Whether to track CSV outputs from Vector PostProcessors, by default False
        track_vector_positions: bool, optional
            Whether to create metrics for the positions given in Vector PostProcessor output at each time step (x, y, z, radius), by default False
        moose_env_vars : typing.Optional[typing.Dict[str, typing.Any]], optional
            Any environment variables to be passed to MOOSE on startup, by default None
        run_in_parallel: bool, optional
            Whether to run the MOOSE simulation in parallel, by default False
        num_processors : int, optional
            The number of processors to run a parallel MOOSE job across, by default 1
        mpiexec_env_vars : typing.Optional[typing.Dict[str, typing.Any]]
            Any environment variables to pass to mpiexec on startup if running in parallel, by default None

        Raises
        ------
        ValueError
            Raised if conflicting values of track_vector_positions and track_vector_postprocessors are found.

        """
        if track_vector_positions and not track_vector_postprocessors:
            raise ValueError(
                "Vector positions can only be tracked if vector postprocessor tracking is enabled."
            )

        self.moose_application_path = moose_application_path
        self.moose_file_path = moose_file_path
        self.workdir_path = (
            pathlib.Path(workdir_path) if workdir_path else pathlib.Path.cwd()
        )
        self.upload_files = upload_files
        self.track_vector_postprocessors = track_vector_postprocessors
        self.track_vector_positions = track_vector_positions
        self.moose_env_vars = moose_env_vars or {}
        self.run_in_parallel = run_in_parallel
        self.num_processors = num_processors
        self.mpiexec_env_vars = mpiexec_env_vars or {}

        super().launch()

    @simvue.utilities.prettify_pydantic
    @pydantic.validate_call
    def load(
        self,
        moose_file_path: pydantic.FilePath,
        results_dir: pydantic.DirectoryPath | None = None,
        upload_files: list[str] | None = None,
        track_vector_postprocessors: bool = False,
        track_vector_positions: bool = False,
    ):
        """Command to load a set of results from a MOOSE simulation into Simvue.

        Parameters
        ----------
        moose_file_path : pydantic.FilePath
            Path to the MOOSE configuration file
        results_dir : pydantic.DirectoryPath | None, optional
            A path to a directory containing MOOSE simulation results to upload
            By default, will use the location specified in the MOOSE file, or fallback to current working directory if file_base not specified.
        upload_files : list[str] | None, optional
            List of results file names to upload to the Simvue server for storage, by default None
            These should be supplied relative to the output directory provided in the MOOSE file.
            If not specified, will upload all files by default. If you want no results files to be uploaded, provide an empty list.
        track_vector_postprocessors : bool, optional
            Whether to track CSV outputs from Vector PostProcessors, by default False
        track_vector_positions: bool, optional
            Whether to create metrics for the positions given in Vector PostProcessor output at each time step (x, y, z, radius), by default False

        Raises
        ------
        ValueError
            Raised if conflicting values of track_vector_positions and track_vector_postprocessors are found.

        """
        if track_vector_positions and not track_vector_postprocessors:
            raise ValueError(
                "Vector positions can only be tracked if vector postprocessor tracking is enabled."
            )

        self.moose_file_path = moose_file_path
        self.workdir_path = pathlib.Path().cwd()
        self.upload_files = upload_files
        self.track_vector_postprocessors = track_vector_postprocessors
        self.track_vector_positions = track_vector_positions
        self._loading_historic_run = True

        # Save the MOOSE file for this run to the Simvue server
        if pathlib.Path(self.moose_file_path).exists() and (
            self.upload_files is None or self.moose_file_path in self.upload_files
        ):
            self.save_file(self.moose_file_path, category="input")

        # Parse the MOOSE input file
        input_metadata = self._moose_input_parser(pathlib.Path(self.moose_file_path))

        # Extract useful information
        self._moose_input_callback(input_metadata)

        if results_dir and results_dir.absolute() != self._output_dir_path.absolute():
            print(f"""
                  Warning: Output location specified in MOOSE file, '{self._output_dir_path}'
                  does not match results dir location given to load, '{results_dir}.'
                  Directory provided in MOOSE input file will be ignored.
                  """)
            self._output_dir_path = results_dir

        log_path = self._output_dir_path.joinpath(f"{self._results_prefix}.txt")
        if log_path.exists():
            # Pass into header callback to extract metadata
            _, metadata = self._moose_header_parser(input_file=str(log_path))
            self.update_metadata(metadata)

            # Parse line by line, matching regex patterns, upload as Events if found
            with open(log_path) as file:
                file_lines = file.readlines()
                file_lines = list(filter(None, file_lines))

                for line in file_lines:
                    for label, pattern in self._patterns.items():
                        match = pattern.search(line)
                        if not match:
                            continue
                        self._per_event_callback({label: match.group(0)}, {})
                        break

        # Extract metrics CSV file
        csv_path = self._output_dir_path.joinpath(f"{self._results_prefix}.csv")
        if csv_path.exists():
            with open(csv_path, "r") as _file:
                for _step, _metric in enumerate(csv.DictReader(_file)):
                    _data = {key: float(value) for key, value in _metric.items()}
                    _data["step"] = _step
                    self._per_metric_callback(_data, {})

        if self.track_vector_postprocessors:
            csv_paths = self._output_dir_path.glob(f"{self._results_prefix}_*.csv")
            for path in csv_paths:
                if path == self._output_dir_path.joinpath(
                    f"{self._results_prefix}_*_time.csv"
                ):
                    continue
                _, data = self._vector_postprocessor_parser(input_file=str(path))
                self._per_metric_callback(data, {})

        self._post_simulation()

        super().load()
