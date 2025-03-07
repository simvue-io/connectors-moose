"""MOOSE Connector.

This module provides functionality for using Simvue to track and monitor a MOOSE simulation.
"""

import csv
import pathlib
import re
import time
import typing
from functools import reduce

import multiparser.parsing.file as mp_file_parser
import multiparser.parsing.tail as mp_tail_parser
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

    moose_application_path: pydantic.FilePath = None
    moose_file_path: pydantic.FilePath = None
    track_vector_postprocessors: bool = None
    track_vector_positions: bool = None
    moose_env_vars: typing.Dict[str, typing.Any] = None
    run_in_parallel: bool = None
    num_processors: int = None
    mpiexec_env_vars: typing.Dict[str, typing.Any] = None

    _output_dir_path: typing.Union[str, pydantic.DirectoryPath] = None
    _results_prefix: str = None
    _time = time.time()
    # This represents the step number and time of the step, ie when MOOSE says 'Time Step X, time = Y'
    _step_num = 0
    _step_time = 0
    # Initialize counters for keeping track of the number of linear and nonlinear steps involved in each solve
    _nonlinear = 0
    _linear = 0
    _dt = None

    def _moose_input_parser(self, input_file: pathlib.Path):
        """Parse MOOSE input file, and create a dictionary of metadata with dot notation representing indentation of keys.

        Parameters
        ----------
        input_file: pathlib.Path
            The path to the MOOSE input file

        Raises
        ------
        KeyError
            Raised if there is no 'file_base' parameter in the MOOSE file Output section, since the connector requires this to find output files.

        """
        input_metadata = {}
        prefix = input_file.name.split(".")[0]
        # Will make a list of keys for each value to create a nested dict
        keys = [prefix]

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

        self.update_metadata(input_metadata)

        # Try to retrieve some useful things
        try:
            file_base = input_metadata[prefix]["Outputs"]["file_base"]
            self._output_dir_path, self._results_prefix = file_base.rsplit("/", 1)
        except KeyError:
            raise KeyError(
                "Could not find file_base in your MOOSE file.\n"
                "Please add 'file_base' to your Outputs section, in the form <results directory path>/<results file prefix>."
            )

        if not input_metadata[prefix].get("Executioner", None):
            print(
                "No Executioner block detected in your MOOSE input file - falling back to log times and steps."
            )

        elif _dt := input_metadata[prefix]["Executioner"].get("dt", None):
            try:
                self._dt = float(_dt)
            except ValueError:
                print(
                    "WARNING: Could not interpret Executioner.dt as a number, falling back to log times and steps. To correct this, make sure 'dt' is a number in your MOOSE input file."
                )

    @mp_file_parser.file_parser
    def _moose_header_parser(self, input_file: str, **__) -> typing.Dict[str, str]:
        """Parse the header of the MOOSE log file and return the data from it as a dictionary.

        Parameters
        ----------
        input_file : str
            The path to the file where the console log is stored.
        **__
            Additional unused keyword arguments

        Returns
        -------
        typing.Dict[str, str]
            The parsed data from the header of the MOOSE log file

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
    ) -> typing.Dict[str, str]:
        """Parse data from VectorPostProcessor CSV files.

        Parameters
        ----------
        input_file : str
            Path to the VectorPostProcessor CSV file
        **__
            Additional unused keyword arguments

        Returns
        -------
        typing.Dict[str, str]
            A dictionary of metadata and data contained in the CSV file

        """
        metrics = {}
        # Get name of vector which is being calculated by VectorPostProcessor from filename
        file_name = pathlib.Path(input_file).name
        vector_name, serial_num = file_name.replace(
            f"{self._results_prefix}_", ""
        ).rsplit("_", 1)

        # If user has enabled time_data in their MOOSE file, get latest line from this file and save time
        time_file = f"{input_file.rsplit('_', 1)[0]}_time.csv"
        if pathlib.Path(time_file).exists():
            with open(time_file, newline="\n") as in_t:
                final_line = [in_t.readlines()[-1]]
                current_time_data = next(csv.reader(final_line))
                metrics["time"] = current_time_data[0]
                metrics["step"] = current_time_data[1]
        else:
            metrics["step"] = int(serial_num.split(".")[0])
            if self._dt:
                metrics["time"] = metrics["step"] * self._dt

        with open(input_file, newline="") as in_f:
            read_csv = csv.DictReader(in_f)

            for csv_data in read_csv:
                if not self.track_vector_positions:
                    csv_data.pop("x", None)
                    csv_data.pop("y", None)
                    csv_data.pop("z", None)
                    csv_data.pop("radius", None)

                if _id := csv_data.pop("id", None):
                    metrics.update(
                        {
                            f"{vector_name}.{key}.{_id}": value
                            for (key, value) in csv_data.items()
                        }
                    )

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
                self.log_event(list(log_data.values())[0])
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
    ):
        """Monitor each line in the results CSV file, and add data from it to Simvue Metrics.

        Parameters
        ----------
        csv_data : typing.Dict[str, float]
            The data from the latest line in the CSV file
        sim_metadata : typing.Dict[str, str]
            The metadata about when this line was read by Multiparser

        """
        metric_time = csv_data.pop("time", None)
        metric_step = csv_data.pop("step", None)

        if self._dt and not metric_step:
            # Has come from a scalar PostProcessor, can assume step = time / dt
            metric_step = int(metric_time / self._dt)

        # Log all results for this timestep as Metrics
        self.log_metrics(
            csv_data,
            step=metric_step if metric_step is not None else self._step_num,
            time=metric_time if metric_time is not None else self._step_time,
            timestamp=sim_metadata["timestamp"].replace(" ", "T"),
        )

    def _pre_simulation(self):
        """Upload information to Simvue before the MOOSE simulation begins."""
        super()._pre_simulation()

        # Add alert for a non converging step
        self.create_event_alert(
            name="step_not_converged",
            frequency=1,
            pattern=" Solve Did NOT Converge!",
            notification="email",
        )

        # Save the MOOSE file for this run to the Simvue server
        if pathlib.Path(self.moose_file_path).exists:
            self.save_file(self.moose_file_path, "input")

        # Parse the MOOSE input file
        self._moose_input_parser(pathlib.Path(self.moose_file_path))

        # Save the MOOSE Makefile
        if (
            pathlib.Path(self.moose_application_path)
            .parent.joinpath("Makefile")
            .exists()
        ):
            self.save_file(
                pathlib.Path(self.moose_application_path).parent.joinpath("Makefile"),
                "input",
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
        )

    def _during_simulation(self):
        """Describe which files should be monitored during the simulation by Multiparser."""
        self.log_event("Beginning MOOSE simulation...")

        # Record time here, for that for static problems the overall time for execution will be returned
        self._time = time.time()

        # Read the initial information within the log file when it is first created, to parse the header information
        self.file_monitor.track(
            path_glob_exprs=str(
                pathlib.Path(self._output_dir_path).joinpath(
                    f"{self._results_prefix}.txt"
                )
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
                pathlib.Path(self._output_dir_path).joinpath(
                    f"{self._results_prefix}.txt"
                )
            ),
            callback=self._per_event_callback,
            tracked_values=[
                re.compile(r"Time Step.*"),
                " Solve Converged!",
                " Solve Did NOT Converge!",
                re.compile(r"Terminator '.+' is causing the execution to terminate."),
                re.compile(r" \d+ Nonlinear \|R\|"),
                re.compile(r"     \d+ Linear \|R\|"),
            ],
            labels=[
                "time_step",
                "converged",
                "non_converged",
                "terminated",
                "nonlinear",
                "linear",
            ],
        )
        # Monitor each line added to the MOOSE results file as the simulation proceeds, and upload results to Simvue
        self.file_monitor.tail(
            path_glob_exprs=str(
                pathlib.Path(self._output_dir_path).joinpath(
                    f"{self._results_prefix}.csv"
                )
            ),
            parser_func=mp_tail_parser.record_csv,
            callback=self._per_metric_callback,
        )
        self.file_monitor.exclude(
            str(
                pathlib.Path(self._output_dir_path).joinpath(
                    f"{self._results_prefix}_*_time.csv"
                )
            )
        )
        # Monitor each file created by a Vector PostProcessor, and upload results to Simvue if file matches an expected form.
        if self.track_vector_postprocessors:
            self.file_monitor.track(
                path_glob_exprs=str(
                    pathlib.Path(self._output_dir_path).joinpath(
                        f"{self._results_prefix}_*.csv"
                    )
                ),
                parser_func=self._vector_postprocessor_parser,
                callback=self._per_metric_callback,
                static=True,
            )

    def _post_simulation(self):
        """Upload informatino to Simvue after the MOOSE simulation finishes."""
        for file in pathlib.Path(self._output_dir_path).glob(
            f"{self._results_prefix}*"
        ):
            if (
                pathlib.Path(file).absolute()
                == pathlib.Path(self.moose_file_path).absolute()
            ):
                continue
            self.save_file(file, "output")

        super()._post_simulation()

    @simvue.utilities.prettify_pydantic
    @pydantic.validate_call
    def launch(
        self,
        moose_application_path: pydantic.FilePath,
        moose_file_path: pydantic.FilePath,
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
        self.track_vector_postprocessors = track_vector_postprocessors
        self.track_vector_positions = track_vector_positions
        self.moose_env_vars = moose_env_vars or {}
        self.run_in_parallel = run_in_parallel
        self.num_processors = num_processors
        self.mpiexec_env_vars = mpiexec_env_vars or {}

        super().launch()
