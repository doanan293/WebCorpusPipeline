from __future__ import annotations

from typing import Protocol


class CloudJobLog(Protocol):
    """Where a LlamaParse job is recorded while it runs, so a paid job can be found again."""

    def save_cloud_job(
        self,
        request_id: str,
        url: str,
        *,
        file_id: str | None = None,
        job_id: str | None = None,
        key_label: str | None = None,
        key_fingerprint: str | None = None,
        status: str = "accepted",
        payload: str | None = None,
    ) -> None: ...
