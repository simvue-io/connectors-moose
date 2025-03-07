"""
MOOSE Connector Example
========================
This is an example of the MOOSERun Connector class.

The MOOSE simulation used here simulated the diffusion of heat through
a rectangular bar, where one end is held at 0K, and the other end at 1000K.

To run this example with Docker:
    - Pull the base MOOSE image: docker run -it idaholab/moose:latest
    - Clone this repository: git clone https://github.com/simvue-io/integrations.git
    - Move into MOOSE examples directory: cd integrations/examples/moose
    - Create a simvue.toml file, copying in your information from the Simvue server: vi simvue.toml
    - Install Poetry: pip install poetry
    - Install required modules: poetry install
    - Run the example script: poetry run python moose_example.py

To run this example on your own system with MOOSE installed:
    - Ensure that you have a MOOSE app installed with the Heat Transfer module enabled
    - Move into MOOSE examples directory: cd integrations/examples/moose
    - Create a simvue.toml file, copying in your information from the Simvue server: vi simvue.toml
    - Update the 'MOOSE_APP_PATH' at the top of the script to point to your MOOSE app
    - Install Poetry: pip install poetry
    - Install required modules: poetry install
    - Run the example script: poetry run python moose_example.py
    
For a more in depth example, see: https://docs.simvue.io/examples/moose/

"""
import pathlib
import uuid
import shutil
from simvue_moose.connector import MooseRun

MOOSE_APP_PATH = "/opt/moose/bin/moose-opt"

def moose_example(moose_app_path, offline = False, parallel = False) -> None:
    
    # Delete old copies of results, if they exist:
    if pathlib.Path("/tmp/simvue").exists():
        shutil.rmtree(pathlib.Path("/tmp/simvue"))
        
    # Initialise the MooseRun class as a context manager
    with MooseRun(mode="offline" if offline else "online") as run:
        
        # Initialise the run, providing a name for the run, and optionally extra information such as a folder, description, tags etc
        run.init(
            name="moose_simulation_thermal-%s" % str(uuid.uuid4()),
            description="An example of using the MooseRun Connector to track a MOOSE simulation.",
            folder="/test-moose",
            tags=["moose", "thermal", "diffusion"],
            retention_period="1 hour",
        )

        # You can use any of the Simvue Run() methods to upload extra information before/after the simulation
        run.create_metric_threshold_alert(
            name='avg_temp_above_500',
            metric='average_temerature',
            rule='is above',
            threshold=500.0,
            frequency=1,
            window=1,
            )
        
        # Then call the .launch() method to start your MOOSE simulation
        run.launch(
            moose_application_path=moose_app_path,
            moose_file_path=pathlib.Path(__file__).parent.joinpath("thermal_bar.i"),
            # You can optionally choose to track VectorPostProcessor outputs too:
            track_vector_postprocessors = True,
            track_vector_positions = False,
            # And you can choose whether to run it in parallel
            run_in_parallel = parallel,
            num_processors = 2,
            mpiexec_env_vars = {"allow-run-as-root": True}
        )

        # Once the simulation is complete, you can upload any final items to the Simvue run before it closes
        run.log_event("Deleting local copies of results...")
        
        return run.id
    
if __name__ == "__main__":
    moose_example(moose_app_path=MOOSE_APP_PATH)