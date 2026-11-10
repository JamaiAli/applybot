"""Concurrency tests for database write lock and retry resilience.

Verifies that concurrent multi-threaded writes (such as background auto-cycle
and foreground Telegram command handlers like /track) do not encounter
'database is locked' errors.
"""

import concurrent.futures
import random
import tempfile
import unittest
from pathlib import Path

from applypilot.database import (
    close_connection,
    create_stub_job,
    get_connection,
    init_db,
    store_tracking_email,
    store_tracking_person,
    update_job_tracking_fields,
    update_tracking_status,
)


class TestDatabaseConcurrency(unittest.TestCase):
    """Test concurrent SQLite writes across multiple threads."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_concurrent.db"
        self.conn = init_db(self.db_path)

        # Populate initial test jobs
        for i in range(20):
            url = f"https://example.com/job/{i}"
            self.conn.execute(
                "INSERT INTO jobs (url, title, company, site, applied_at, apply_status, state) "
                "VALUES (?, ?, ?, 'manual', '2026-11-09T10:00:00Z', 'applied', 'applied')",
                (url, f"Job {i}", f"Company {i}"),
            )
        self.conn.commit()

    def tearDown(self):
        close_connection(self.db_path)
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_concurrent_multi_thread_writes_no_lock_error(self):
        """10 threads concurrently updating tracking fields, inserting emails,

        updating status, and creating stub jobs must succeed without any
        'database is locked' OperationalError.
        """
        errors = []

        def worker(thread_id: int):
            try:
                conn = get_connection(self.db_path)
                for step in range(15):
                    job_idx = random.randint(0, 19)
                    url = f"https://example.com/job/{job_idx}"
                    action = step % 5

                    if action == 0:
                        update_job_tracking_fields(
                            url,
                            {"tracking_doc_path": f"/tmp/doc_{thread_id}_{step}.md"},
                            conn,
                        )
                    elif action == 1:
                        update_tracking_status(
                            url,
                            random.choice(["confirmation", "interview", "rejection"]),
                            conn,
                        )
                    elif action == 2:
                        store_tracking_email(
                            {
                                "email_id": f"msg_{thread_id}_{step}",
                                "thread_id": f"th_{thread_id}",
                                "job_url": url,
                                "sender": f"hr{thread_id}@test.com",
                                "subject": f"Update on application {step}",
                                "received_at": "2026-11-10T12:00:00Z",
                                "classification": "confirmation",
                            },
                            conn,
                        )
                    elif action == 3:
                        store_tracking_person(
                            {
                                "job_url": url,
                                "name": f"Recruiter {thread_id}",
                                "email": f"recruiter{thread_id}@test.com",
                                "source_email_id": f"msg_{thread_id}_{step}",
                            },
                            conn,
                        )
                    elif action == 4:
                        create_stub_job(
                            {
                                "subject": f"Thank you for applying to Startup {thread_id}_{step}",
                                "company": f"Startup {thread_id}_{step}",
                            },
                            classification="confirmation",
                            conn=conn,
                        )
            except Exception as exc:
                errors.append((thread_id, type(exc).__name__, str(exc)))
            finally:
                close_connection(self.db_path)

        num_threads = 10
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            concurrent.futures.wait(futures)

        self.assertEqual(
            errors,
            [],
            f"Encountered {len(errors)} errors during concurrent writes: {errors[:5]}",
        )

        # Verify data consistency in the database
        main_conn = get_connection(self.db_path)
        email_count = main_conn.execute("SELECT COUNT(*) FROM tracking_emails").fetchone()[0]
        self.assertGreater(email_count, 0)

        people_count = main_conn.execute("SELECT COUNT(*) FROM tracking_people").fetchone()[0]
        self.assertGreater(people_count, 0)


if __name__ == "__main__":
    unittest.main()
