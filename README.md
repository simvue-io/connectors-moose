# Simvue Connectors - Template

<br/>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://github.com/simvue-io/.github/blob/5eb8cfd2edd3269259eccd508029f269d993282f/simvue-white.png" />
    <source media="(prefers-color-scheme: light)" srcset="https://github.com/simvue-io/.github/blob/5eb8cfd2edd3269259eccd508029f269d993282f/simvue-black.png" />
    <img alt="Simvue" src="https://github.com/simvue-io/.github/blob/5eb8cfd2edd3269259eccd508029f269d993282f/simvue-black.png" width="500">
  </picture>
</p>

<p align="center">
This is a template repository which allows you to quickly create new Connectors which provide Simvue tracking and monitoring functionality to Non-Python simulations.
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

## How to use this template

### Naming your connector
First, make a name for your new connector. Typically, the module name is of the form `simvue-{software_name}`, and the connector class itself is of the form `{SoftwareName}Run`. Update the `pyproject.toml` file with the name of your module, and also update the directory currently called `simvue_template` with your module name.

### Creating the code
Your connector class should be made in the `connector.py` file inside your module, with any extra functionality which it needs to work (but you don't want inside the class itself) put in files inside the `extras` directory. The connector should inherit from the `WrappedRun` class provided by the `simvue-connector` module, and should use `multiparser` to track and parse output files as they are being written. See an example in the [connectors-generic repository](https://github.com/simvue-io/connectors-generic), or check out any of our premade connectors for ideas:

* [FDS](https://github.com/simvue-io/connectors-fds)

Also look at the `CONTRIBUTING.md` file for expected coding standards.


### Writing examples
In the `examples` directory, please provide at least one example of your connector being used to track your simulation software. Create this example inside a function so that it can be used in the integration tests. If your software is difficult to install, you may want to provide setup instructions for using a Docker container or similar, as well as instructions assuming that the software is already installed on the user's system.

### Writing tests
You should create two types of tests:

* Unit tests: Check each element of your connector independently, such as file parsers and callbacks, each method etc. You should use `pytest`, and use Mockers to mock out any functionality which your simulation software would typically provide so that the simulation software itself is **not** required to run these tests.
* Integration tests: These check the end-to-end functionality of your connector when used with the actual simulation software. You should parametrize the test to include offline mode, as well as online. You can use the example(s) which you created earlier as the basis for these tests.

### CI Workflows
Inside the `.github` directory, there are a number of workflows already created. You should adit these to work for your connector. They include:

* `test_macos`, `test_ubuntu`, `test_windows`: These run the unit tests, should not need to be altered
* `test_integration`: These run the integration tests, you will need to provide a docker container to use and whatever installation steps are required for your case
* `deploy`: Automates deployment to test-PyPI and PyPI for tagged releases (see below). You need to update the module names in this file - see the curly brackets.

### Deployment
When you are happy with your connector and are ready to deploy it to PyPI for the first time, you need to do the following:

* Install `poetry` and `twine` if you haven't already: `pip install poetry twine`
* Check your `pyproject.toml` file is valid by running `poetry check`
* Install your module: `poetry install`
* Build the distribution: `poetry build`
* Go to `test.pypi.org`, create an account, and get a token
* Upload your package with Twine: `twine upload -r testpypi dist/*`
* Enter the token when prompted
* Go to `https://test.pypi.org/project/{your-package-name}`, check it has been published
* Click 'Manage Project'
* If you wish to enable automatic deployments, click 'Publishing' -> 'Add a new publisher' and fill in the details for your repository, setting Workflow name to `deploy.yaml` and Environment name to `test_pypi`

If this was all successful, repeat with the real PyPI instance at `pypi.org`, using `twine upload dist/*`, and setting the Environment name in the publisher settings to `pypi`.

From now on, you can do deployments automatically. Simply:

* Update the `pyproject.toml` with a new version number, eg `v1.0.1`
* Update the CHANGELOG to reflect your newest changes
* Tag a branch with a semantic version number, eg `git tag v1.0.1`
* Push the tag: `git push origin v1.0.1`

This should automatically start the deployment workflow - check that it completes successfully on the Github UI.

### Updating the README
When finished, delete all of the information above under the 'How to use this template' heading. Then update the information below to be relevant for your connector:

## Implementation
{List here how your Connector works, and the things about the simulation it tracks by default.}

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
pip install {your_module_name_here}
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
{Replace the example below with a similar example exhibiting how to use your Connector. The example for the FDS (Fire Dynamics Simulator) connector is given below:}

```python
from simvue_fds.connector import FDSRun

...

if __name__ == "__main__":

    ...

    # Using a context manager means that the status will be set to completed automatically,
    # and also means that if the code exits with an exception this will be reported to Simvue
    with FDSRun() as run:

        # Specify a run name, along with any other optional parameters:
        run.init(
          name = 'my-fds-simulation',                                   # Run name
          metadata = {'number_fires': 3},                               # Metadata
          tags = ['fds', 'multiple-fires'],                             # Tags
          description = 'FDS simulation of fires in a parking garage.', # Description
          folder = '/fds/parking-garage/trial_1'                        # Folder path
        )

        # Set folder details if necessary
        run.set_folder_details(
          metadata = {'simulation_type': 'parking_garage'},             # Metadata
          tags = ['fds'],                                               # Tags
          description = 'FDS simulations with fires in different areas' # Description
        )

        # Can use the base Simvue Run() methods to upload extra information, eg:
        run.save_file(os.path.abspath(__file__), "code")

        # Can add alerts specific to your simulation, eg:
        run.create_metric_threshold_alert(
          name="visibility_below_five_metres",    # Name of Alert
          metric="eye_level_visibility",          # Metric to monitor
          frequency=1,                            # Frequency to evaluate rule at (mins)
          rule="is below",                        # Rule to alert on
          threshold=5,                            # Threshold to alert on
          notification='email',                   # Notification type
          trigger_abort=True                      # Abort simulation if triggered
        )

        # Launch the FDS simulation
        run.launch(
            fds_input_path='path/to/my/input_file.i',   # Path to your FDS input file
            workdir_path='path/to/my/results_dir',      # Path where results should be created
            run_in_parallel=True,                       # Whether to run in parallel using MPI
            num_processors=2                            # Number of cores to use if in parallel

            )

```

## License

Released under the terms of the [Apache 2](https://github.com/simvue-io/client/blob/main/LICENSE) license.
