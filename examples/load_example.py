"""
MOOSE Connector Example
========================
This is an example of using the MOOSERun Connector to load historic simulation results.

The MOOSE simulation used here simulated the diffusion of heat through
a rectangular bar, where one end is held at 0K, and the other end at 1000K.

To run this example with Docker:
    - Pull the base MOOSE image: docker run -it idaholab/moose:latest
    - Clone this repository: git clone https://github.com/simvue-io/connectors-moose.git
    - Move into MOOSE examples directory: cd connectors-moose/examples
    - Create a simvue.toml file, copying in your information from the Simvue server: vi simvue.toml
    - Install Poetry: pip install poetry
    - Install required modules: poetry install
    - Run the example script: poetry run python load_example.py

To run this example on your own system with MOOSE installed:
    - Ensure that you have a MOOSE app installed with the Heat Transfer module enabled
    - Move into MOOSE examples directory: cd connectors-moose/examples
    - Create a simvue.toml file, copying in your information from the Simvue server: vi simvue.toml
    - Update the 'MOOSE_APP_PATH' at the top of the script to point to your MOOSE app
    - Install Poetry: pip install poetry
    - Install required modules: poetry install
    - Run the example script: poetry run python load_example.py

For a more in depth example, see: https://docs.simvue.io/examples/moose/

"""

import pathlib
import uuid
import shutil
from simvue_moose.connector import MooseRun

# Initialise the MooseRun class as a context manager
with MooseRun() as run:
    # Initialise the run, providing a name for the run, and optionally extra information such as a folder, description, tags etc
    run.init(
        name="load_moose_simulation_thermal-%s" % str(uuid.uuid4()),
        description="An example of using the MooseRun Connector to load a MOOSE simulation.",
        folder="/test-moose",
        tags=["moose", "thermal", "diffusion"],
        retention_period="1 hour",
    )

    # Call the .load() method to parse your MOOSE simulation results
    run.load(
        # Provide path to input file, making sure it has a file base pointing to your results directory
        moose_file_path=pathlib.Path(__file__).parent.joinpath("thermal_bar.i"),
        results_dir=pathlib.Path(__file__).parent.joinpath("results"),
        # You can optionally choose to track VectorPostProcessor outputs too:
        track_vector_postprocessors=True,
        # Optionally choose which files to upload, by default uploads all results
        # upload_files = ["simvue_thermal.e"]
    )

    # Once the simulation is complete, you can upload any final items to the Simvue run before it closes
    run.log_event("Deleting local copies of results...")
