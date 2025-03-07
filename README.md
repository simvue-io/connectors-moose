# Simvue Connectors - MOOSE

<br/>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://github.com/simvue-io/.github/blob/5eb8cfd2edd3269259eccd508029f269d993282f/simvue-white.png" />
    <source media="(prefers-color-scheme: light)" srcset="https://github.com/simvue-io/.github/blob/5eb8cfd2edd3269259eccd508029f269d993282f/simvue-black.png" />
    <img alt="Simvue" src="https://github.com/simvue-io/.github/blob/5eb8cfd2edd3269259eccd508029f269d993282f/simvue-black.png" width="500">
  </picture>
</p>

<p align="center">
Allow easy connection between Simvue and MOOSE (Multiphysics Object Oriented Simulation Environment), allowing for easy tracking and monitoring of physics simulations in real time.
</p>

<div align="center">
<a href="https://github.com/simvue-io/client/blob/main/LICENSE" target="_blank"><img src="https://img.shields.io/github/license/simvue-io/client"/></a>
<img src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue">
</div>

<h3 align="center">
 <a href="https://simvue.io"><b>Website</b></a>
  •
  <a href="https://docs.simvue.io"><b>Documentation</b></a>
</h3>

## Implementation
A customised `MooseRun` class has been created which automatically does the following:

* Uploads the MOOSE input file and application Makefile as input artifacts
* Launches the MOOSE simulation as a process, triggering an alert if it encounters an error or exception
* Uploads information from the MOOSE input file as metadata
* Uploads information from the top of the console log (MOOSE version, parallelism information, mesh information etc) as metadata
* Uploads key information from the console log as events
* Creates an alert which notifies the user if a step fails to converge
* Uploads any variable values being written to CSV files as metrics
* Adds relevant metadata and tags to the run if the MOOSE Terminator stopped the run early
* Once complete, upload the any output files as artifacts

## Installation
To install and use this connector, first create a virtual environment:
```
python -m venv venv
```
Then activate it:
```
source venv/bin/activate
```
And then use pip to install this module:
```
pip install simvue-moose
```

## Configuration
The service URL and token can be defined as environment variables:
```sh
export SIMVUE_URL=...
export SIMVUE_TOKEN=...
```
or a file `simvue.toml` can be created containing:
```toml
[server]
url = "..."
token = "..."
```
The exact contents of both of the above options can be obtained directly by clicking the **Create new run** button on the web UI. Note that the environment variables have preference over the config file.

## Usage example
```python
from simvue_moose.connector import MooseRun

...

if __name__ == "__main__":

    ...

    # Using a context manager means that the status will be set to completed automatically,
    # and also means that if the code exits with an exception this will be reported to Simvue
    with MooseRun() as run:

        # Specify a run name, along with any other optional parameters:
        run.init(
          name = 'my-moose-simulation',                                 # Run name
          metadata = {'initial_temp': 30},                              # Metadata
          tags = ['moose', 'conduction'],                               # Tags
          description = 'MOOSE simulation of thermal conduction.',      # Description
          folder = '/moose/conduction/coffee_cup'                       # Folder path
        )

        # Set folder details if necessary
        run.set_folder_details(
          metadata = {'mesh': 'coffee_cup'},                            # Metadata
          tags = ['moose'],                                             # Tags
          description = 'MOOSE simulations of thermal conduction'       # Description
        )

        # Can use the base Simvue Run() methods to upload extra information, eg:
        run.save_file(os.path.abspath(__file__), "code")

        # Can add alerts specific to your simulation, eg:
        run.create_metric_threshold_alert(
          name="temperature_above_eighty",        # Name of Alert
          metric="temperature",                   # Metric to monitor
          frequency=1,                            # Frequency to evaluate rule at (mins)
          rule="is above",                        # Rule to alert on
          threshold=80,                           # Threshold to alert on
          notification='email',                   # Notification type
          trigger_abort=True                      # Abort simulation if triggered
        )

        # Launch the MOOSE simulation
        run.launch(
            moose_application_path='path/to/my/moose/app',    # Path to MOOSE application
            moose_file_path='path/to/my/input_file.i',        # Path to MOOSE input file
            track_vector_postprocessors=True,                 # Whether to track vector postprocessors
            track_vector_positions=False,                     # Whether to track positions of vectors
            run_in_parallel=True,                             # Whether to run in parallel using MPI
            num_processors=2                                  # Number of cores to use if in parallel

            )

```

## License

Released under the terms of the [Apache 2](https://github.com/simvue-io/client/blob/main/LICENSE) license.

## Citation

To reference Simvue, please use the information outlined in this [citation file](https://github.com/simvue-io/python-api/blob/dev/CITATION.cff).
