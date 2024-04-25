#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Apr 10 09:07:54 2024

@author: connor
"""

import os
from typing import Optional

import clr


class AutolabProcedure:
    def __init__(self, procedure: str, sdk_path: Optional[str] = None) -> None:
        if sdk_path is None:
            sdk_path = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1"
        sdk = os.path.join(sdk_path, "EcoChemie.Autolab.Sdk")
        if clr.FindAssembly(sdk):
            clr.AddReference(sdk)
            from EcoChemie.Autolab.Sdk import Instrument
            self._instrument = Instrument()
            self.procedure = self._instrument.LoadProcedure(procedure)
        else:
            message = f"Cannot find {sdk}.dll"
            raise AutolabException(message, -1001)



# Exceptions
class AutolabException(Exception):
    """Base exception for all Autolab SDK exceptions."""

    def __init__(self, message, error_code) -> None:
        """Initialize base exception."""
        super().__init__(message)
        self.error_code = error_code
        self.message = message

    def __str__(self):
        """__str__ representation of the AutolabException."""
        string = (
            f"{self.__class__.__name__} code: {self.error_code}. Message "
            f"\'{self.message.decode('utf-8')}\'"
        )
        return string

    def __repr__(self):
        """__repr__ representation of the AutolabException."""
        return self.__str__()
