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
            "example_input_2.Mesh.#file": "mug_2.e"
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
            "example_input_4.Postprocessors": {'temp.avg': {'type': 'ElementAverageValue', 'block': 0.0, 'variable': "'T'"}, 'temp.max': {'type': 'ElementExtremeValue', 'block': 0.0, 'variable': "'T'", 'value_type': 'max'}, 'temp.min': {'type': 'ElementExtremeValue', 'block': 0.0, 'variable': "'T'", 'value_type': 'min'}},
        },
        {
            "example_input_4.Postprocessors.temp.avg.block": "Shouldn't exist",
        },
    ),
]


@pytest.mark.parametrize("file_name,expected_metadata,not_expected_metadata", testdata, ids=(1, 2, 3, 4))
def test_moose_input_parser(folder_setup, file_name, expected_metadata, not_expected_metadata):   
    """
    Check information from MOOSE input file is correctly uploaded as metadata
    """ 
    with MooseRun() as run:
        run.config(disable_resources_metrics=True)
        run.init(name='test_moose_input_parser-%s' % str(uuid.uuid4()), folder=folder_setup)
        run_id = run.id
        run._moose_input_parser(pathlib.Path(__file__).parent.joinpath("example_data", f"{file_name}.i"))

        
        client = simvue.Client()
        metadata = client.get_run(run_id).metadata
        # Check that keys and values parsed correctly
        for key, value in expected_metadata.items():
            # Have moved keys from being stored as dot notation to nested dict
            # So unpack the dot separated keys as a list of keys, then use reduce to obtain values from nested metadata
            assert reduce(lambda d, k: d.get(k, None), key.split("."), metadata) == value
    
        for key, value in not_expected_metadata.items():
            try:
                assert reduce(lambda d, k: d.get(k, None), key.split("."), metadata) != value
            except AttributeError: # Will get this if the key doesnt exist since it tries to get() on a None, but we are expecting that...
                continue
            
        assert run._output_dir_path == "results"
        assert run._results_prefix == file_name
        
        if file_name in ("example_input_1", "example_input_3"):
            assert run._dt == 0.1
        else:
            assert run._dt == None
        
        