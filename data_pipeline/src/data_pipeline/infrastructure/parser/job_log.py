from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class LoggedCloudJobs:
    """The ``CloudJobLog`` port for the service: LlamaParse job ids go to the log."""

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
    ) -> None:
        del file_id, key_fingerprint, payload
        log.info("llamaparse %s %s job=%s key=%s: %s", request_id, url, job_id, key_label, status)
