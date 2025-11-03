class EventSourceResponse:
    def __init__(self, generator):
        self._generator = generator

    def stream(self):
        if callable(self._generator):
            return self._generator()
        return iter(self._generator)
