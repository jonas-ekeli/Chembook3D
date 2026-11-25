import pytest

from chembook3d.parsers.saga_job import parse

HEADER = "Starting job 123 on test-node on Saga at Wed Nov 25 21:44:19 CET 2026\r\n"


@pytest.mark.parametrize(
    ("footer", "state"),
    [
        ("JobState=RUNNING\nWarning: no checkpoint", "started"),
        (
            "*** JOB 123 ON test-node CANCELLED AT 12:01 DUE TO TIME LIMIT ***\n"
            "Job was cancelled.\nJob 123 completed at noon",
            "timed_out",
        ),
        ("*** JOB 456 ON test-node CANCELLED AT noon ***", "started"),
        ("Job 123 completed at noon", "ended"),
        ("Job exited normally.\nJob 123 completed at noon", "completed"),
    ],
)
def test_status_uses_the_matching_attempt_and_preserves_specific_outcomes(footer, state):
    assert parse(HEADER + footer, "123", "/work/model.log").state == state


def test_copy_failure_or_unrelated_scratch_is_not_accepted():
    log = HEADER + (
        "JobName: model\nWork space: /work/456-model\n"
        "Copying './model.out' to submission folder...\n"
        " File './model.out' could not be copied back home!\nJob exited normally.\n"
    )
    report = parse(log, "123", "/work/model.log")
    assert report.run_directory is None and not report.copy_announced
    assert parse(log + HEADER, "123", "/work/model.log").state == "unknown"
