"""Base procedure module for NUPyLab GUIs."""

from __future__ import annotations

import logging
from datetime import datetime
from math import nan
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic, sleep
from typing import Callable, Dict, List, Optional, Sequence, TYPE_CHECKING, Union

from nupylab.utilities import DataTuple, NupylabError
from pymeasure.experiment import FloatParameter, IntegerParameter, Procedure

if TYPE_CHECKING:
    from nupylab.utilities.nupylab_instrument import NupylabInstrument


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class NupylabProcedure(Procedure):
    """Base Procedure for NUPyLab GUI procedures to subclass.

    Subclass procedures must define `DATA_COLUMNS` and `TABLE_PARAMETERS` attributes, as
    well as a `set_instruments` method. Attributes `X_AXIS`, `Y_AXIS`, and `INPUTS` are
    expected but not strictly required.

    Running this procedure or its subclasses calls startup, execute, and shutdown
    methods sequentially.

    Attrs:
        previous_procedure: Nupylab Procedure class from previous step. Maintains
            previous instrument connections.
    """

    # Parameters common to all NUPyLab procedures
    record_time = FloatParameter("Record Time", units="s", default=2.0)
    num_steps = IntegerParameter("Number of Measurement Steps")
    current_step = IntegerParameter("Current Step")

    def __init__(self) -> None:
        """Initialize default data and instrument list."""
        self._data: dict = {"System Time": None, "Time (s)": 0.0}
        # Initialize values with NaN to avoid complaints
        self._data_defaults: dict = {
            k: nan for k in (k for k in self.DATA_COLUMNS if k not in self._data)
        }
        self._data.update(self._data_defaults)
        self._counter: int = 1
        self._start_time: float = 0
        self._multivalue_results: List[DataTuple] = []
        self.previous_procedure: Optional[NupylabProcedure] = None
        self.instruments: Sequence[NupylabInstrument] = ()
        self.active_instruments: Sequence[NupylabInstrument] = ()

        super().__init__()

    TABLE_PARAMETERS: Dict[str, str] = {}

    def _check_errors(self) -> None:
        if not self.DATA_COLUMNS:
            raise NotImplementedError(
                "Attribute `DATA_COLUMNS` must be overridden by child class "
                f"`{self.__class__.__name__}`."
            )
        if not self.TABLE_PARAMETERS:
            raise NotImplementedError(
                "Attribute `TABLE_PARAMETERS` must be overridden by child class "
                f"`{self.__class__.__name__}`."
            )
        if hasattr(self, "X_AXIS"):
            for x in self.X_AXIS:
                if x not in self.DATA_COLUMNS:
                    raise AttributeError(f"`X_AXIS` entry `{x}` missing from "
                                         f"`DATA_COLUMNS`: {self.DATA_COLUMNS}")

        if hasattr(self, "Y_AXIS"):
            for y in self.Y_AXIS:
                if y not in self.DATA_COLUMNS:
                    raise AttributeError(f"`Y_AXIS` entry `{y}` missing from "
                                         f"`DATA_COLUMNS`: {self.DATA_COLUMNS}")

        if hasattr(self, "INPUTS"):
            for input_ in self.INPUTS:
                if not hasattr(self, input_):
                    raise AttributeError(f"`INPUTS` entry `{input_}` does not "
                                         "correspond to any defined parameters.")
                if input_ in self.TABLE_PARAMETERS.values():
                    raise NupylabError(f"Parameter {input_} cannot be listed in both "
                                       "`INPUTS` and `TABLE_PARAMETERS.")

    def set_instruments(self) -> None:
        """Set instrument connections."""
        raise NotImplementedError(
            "Method `set_instruments` missing from instrument class "
            f"`{self.__class__.__name__}`."
        )

    def startup(self) -> None:
        """Connect and initialize instruments."""
        if self.previous_procedure is None:
            self._check_errors()
        self.set_instruments()
        if not self.instruments or not self.active_instruments:
            raise NupylabError("Method `set_instruments` must create non-empty "
                               "`instruments` and `active_instruments` attributes.")
        for instrument in self.active_instruments:
            if not instrument.connected:
                instrument.connect()
                log.info("Connection to %s successful.", instrument.name)
            instrument.start()
        self.previous_procedure = None  # Prevent procedure-chaining in memory
        sleep(1)  # give instruments time to start their respective programs

    def execute(self) -> None:
        """Loop through thread for each instrument and emit results."""
        log.info("Running step %d / %d.", self.current_step, self.num_steps)
        queues = []
        threads = []
        for instrument in self.active_instruments:
            queue = SimpleQueue()
            queues.append(queue)
            thread = Thread(target=self._sub_loop, args=(instrument.get_data, queue))
            threads.append(thread)

        self._start_time = monotonic()
        for thread in threads:
            thread.start()

        while True:
            sleep_time: float = self.record_time * self._counter - (
                monotonic() - self._start_time
            )
            sleep(max(0, sleep_time))
            self._emit_results(queues)  # Emit after other threads have run
            self._counter += 1

            if self.should_stop():
                log.warning("Catch stop command in procedure")
                break
            if self.finished:
                for thread in threads:
                    thread.join()
                while self._emit_results(queues) != 0:  # Flush queues
                    continue
                log.info("Step %d / %d complete.", self.current_step, self.num_steps)
                break
        for thread in threads:
            thread.join()
        for instrument in self.active_instruments:
            instrument.stop_measurement()

    def shutdown(self) -> None:
        """Shut down instruments if all steps have run or there was an error."""
        if (self.should_stop() or self.status == Procedure.FAILED or self.num_steps ==
                self.current_step):
            for instrument in self.instruments:
                try:
                    if instrument.connected:
                        instrument.shutdown()
                except Exception as e:
                    log.warning("Error shutting down %s: %s", instrument.name, e)
            log.info("Shutdown complete.")

    @property
    def progress(self) -> float:
        """Get procedure step progress, from 0-100. Overwrite in subclass."""
        return 0

    @property
    def finished(self):
        """Get whether all active instruments are finished measuring."""
        for instrument in self.active_instruments:
            if not instrument.finished:
                return False
        return True

    def _sub_loop(
        self, process: Callable[..., None], queue: SimpleQueue, *args
    ) -> None:
        """Implement generic sub-loop for concurrent instrument communication.

        All sub-loops are synchronized with the main loop.

        Args:
            process: function to loop, typically an instrument read.
            queue: queue to place data in.
            *args: additional args to pass to `process`
        """
        counter: int = 0
        while not self.should_stop() and not self.finished:
            if counter != self._counter:
                queue.put(process(*args))
                counter = self._counter
                sleep_time: float = self.record_time * self._counter - (
                    monotonic() - self._start_time
                )
            else:
                sleep_time = 0.1  # Wait for counter to iterate
            sleep(max(0, sleep_time))

    def _parse_results(self, result: Union[list, tuple]) -> None:
        """Write value to class data if single-valued, otherwise postpone extraction."""
        # Recursively unpack if necessary
        if not isinstance(result, DataTuple):
            for r in result:
                self._parse_results(r)
            return
        if not hasattr(result.value, "__len__"):
            self._data[result.label] = result.value
        elif len(result.value) == 0:  # do not include empty results
            return
        elif len(result.value) == 1:
            self._data[result.label] = result.value[0]
        else:
            self._multivalue_results.append(result)

    def _emit_results(self, queues: List[SimpleQueue]) -> int:
        """Emit next set of results from all queues.

        Args:
            queues: list of queues.

        Returns:
            the number of queues that contained results.
        """
        self._data["Time (s)"] = self.record_time * (self._counter - 1)
        self._data["System Time"] = str(datetime.now())
        self._multivalue_results = []
        filled_queues: int = 0
        results: tuple
        for q in queues:
            try:
                results = q.get_nowait()
                filled_queues += 1
            except Empty:
                continue
            self._parse_results(results)

        if filled_queues == 0:
            return filled_queues

        if len(self._multivalue_results) == 0:
            self.emit("results", self._data)
            self.emit("progress", self.progress)
            self._data.update(self._data_defaults)  # reset data to defaults
            return filled_queues

        index: int = max(len(result.value) for result in self._multivalue_results)
        for i in range(index):
            for result in self._multivalue_results:
                if len(result.value) < i + 1:
                    continue
                self._data[result.label] = result.value[i]
            self.emit("results", self._data)
            self._data.update(self._data_defaults)  # reset data to defaults
        self.emit("progress", self.progress)
        return filled_queues
