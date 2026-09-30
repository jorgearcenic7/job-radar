from typing import Protocol

from job_radar.domain import Job


class Connector(Protocol):
    company: str
    source: str

    def fetch(self) -> list[Job]:
        raise NotImplementedError

