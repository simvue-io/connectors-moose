from examples.example import example
import pytest
import simvue
from simvue.sender import sender

@pytest.mark.parametrize("offline", (True, False), ids=("offline", "online"))
def test_fds_connector(folder_setup, offline):
    
    run_id = example(folder_setup, offline)

    if offline:
        _id_mapping = sender()
        run_id = _id_mapping.get(run_id)
    
    # Use the client to check stuff got uploaded correctly, eg
    client = simvue.Client()
    events = [event["message"] for event in client.get_events(run_id)]
    assert "Simulation complete!" in events
    