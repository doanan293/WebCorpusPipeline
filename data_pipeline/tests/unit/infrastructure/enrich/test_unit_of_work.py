from __future__ import annotations

import threading
from pathlib import Path

from data_pipeline.domain.enrich.models import SourceProgress
from data_pipeline.infrastructure.enrich.sqlite.unit_of_work import SqliteEnrichUnitOfWork


def test_enrich_repositories_share_one_connection_and_lock(tmp_path: Path) -> None:
    with SqliteEnrichUnitOfWork.open(tmp_path / "work", "c1") as uow:
        locks = {
            id(repository._write_lock)
            for repository in (uow.inventory, uow.downloads, uow.parses, uow.failures)
        }
        assert locks == {id(uow._write_lock)}
        uow.inventory.add_inventory_batch(
            ["https://a.test/f.pdf"], [], SourceProgress("s", 0, 0, 0, done=True)
        )
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                for n in range(50):
                    uow.failures.record_failure("https://a.test/f.pdf", "download", f"err {n}")
                    uow.parses.save_cloud_job(f"r{n}", "https://a.test/f.pdf", status="accepted")
            except BaseException as exc:  # noqa: BLE001 -- the test reports it
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert uow.summary().failed == 1
