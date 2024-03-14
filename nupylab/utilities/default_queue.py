from queue import SimpleQueue


class DefaultQueue(SimpleQueue):
    """Simple queue with default value field.

    Enables data recording to fall back on a default value, e.g. nan, rather than
    duplicate previously emitted result.

    Attributes:
        default: default value for data emission.
    """

    def __init__(self, default) -> None:
        """Create default queue.

        Args:
            default: default value for data emission.
        """
        super().__init__()
        self.default = default
