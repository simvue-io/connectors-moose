""" Connector.
===============

This module provides functionality for using Simvue to track and monitor a simulation.
"""
import pydantic

import simvue
from simvue_connector.connector import WrappedRun

class YourRun(WrappedRun):
    
    def _pre_simulation(self):
        """Upload any preliminary metadata etc and start the simulation process."""
        super()._pre_simulation()
        # Your code here...
        
    def _during_simulation(self):
        """Describe which files should be monitored during the simulation by Multiparser."""
        # Your code here, using methods from `self.file_monitor` ...
        
    def _post_simulation(self):
        """Do any required post-processing, upload output files etc after the simulation has finished."""
        # Your code here...
        super()._post_simulation()

    @simvue.utilities.prettify_pydantic
    @pydantic.validate_call
    def launch(
        self,
        # Your input parameters here...
    ):
        """Command to launch the simulation and track it with Simvue.
        """
        # Your code here...
        super().launch()
