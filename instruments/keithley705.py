#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 19 09:57:49 2024.

@author: connor
"""

import logging

from pymeasure.instruments import Instrument
from pymeasure.instruments.validators import (truncated_range,
                                              strict_discrete_set)


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class Keithley705(Instrument):
    """Instrument class for the Keithley 705 scanner.

    .. code-block:: python

        scanner = Keithley705("GPIB::1")

    """
