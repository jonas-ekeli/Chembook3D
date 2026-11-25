"""Read the Saga UiBKvant wrapper log for one Slurm attempt (D266)."""

import posixpath
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

START = re.compile(r"^Starting job ([0-9]+) on (\S+) on Saga at (.+)$", re.MULTILINE)
CANCELLATION = re.compile(r"\*\*\* JOB ([0-9]+) ON \S+ CANCELLED AT ")


@dataclass
class JobReport:
    job_id: str
    state: str
    evidence: str
    log_path: str | None = None
    job_name: str | None = None
    node: str | None = None
    started_at: str | None = None
    run_directory: str | None = None
    wrapper_termination: str | None = None
    copy_announced: bool = False
    output_path: str | None = None
    output_note: str | None = None
    steps: list[dict] | None = None

    def start_timestamp(self) -> float | None:
        """Saga's English start line includes the local CET/CEST offset."""
        if not self.started_at:
            return None
        parts = self.started_at.split()
        if len(parts) != 6 or parts[4] not in {"CET", "CEST"}:
            return None
        _, month, day, clock, zone, year = parts
        if not re.fullmatch(r"[0-9]{4}", year):
            return None
        offset = "+0100" if zone == "CET" else "+0200"
        try:
            return parsedate_to_datetime(f"{day} {month} {year} {clock} {offset}").timestamp()
        except (ValueError, OverflowError, OSError):
            return None


def parse(text: str, job_id: str, log_path: str) -> JobReport:
    text = text.replace("\r\n", "\n")
    starts = list(START.finditer(text))
    if len(starts) != 1 or starts[0][1] != job_id:
        return JobReport(job_id, "unknown", "Log does not uniquely identify this attempt.")

    start = starts[0]
    report = JobReport(job_id, "started", start[0], log_path, node=start[2], started_at=start[3])
    lines = text[start.end() :].splitlines()
    stem = posixpath.basename(log_path).removesuffix(".log")
    name = next(
        (line.removeprefix("JobName: ") for line in lines if line.startswith("JobName: ")), ""
    )
    valid_name = name and name not in {".", ".."} and name == posixpath.basename(name)
    if valid_name:
        report.job_name = name
    output = f"{name}.out"
    for index, line in enumerate(lines):
        if line.startswith("Work space: ") and valid_name:
            path = line.removeprefix("Work space: ")
            if (
                path.startswith("/")
                and posixpath.normpath(path) == path
                and posixpath.basename(path) == f"{job_id}-{name}"
            ):
                report.run_directory = path
        if line == "Checking normal termination:":
            following = [s.strip() for s in lines[index + 1 : index + 3]]
            if following == [f"-> {output}", "normally terminated"]:
                report.wrapper_termination = "normal"
        if line.startswith(
            f"WARNING: File {output} does not end with the 'Normal Termination' line."
        ):
            report.wrapper_termination = "abnormal"
        if line == f"Copying './{output}' to submission folder..." and name == stem:
            report.copy_announced = True
        if line.strip() == f"File './{output}' could not be copied back home!":
            report.copy_announced = False
        marker = CANCELLATION.search(line)
        cancelled = marker is not None and marker[1] == job_id
        if cancelled and "DUE TO TIME LIMIT" in line.upper():
            report.state, report.evidence = "timed_out", line
        elif (cancelled or line == "Job was cancelled.") and report.state != "timed_out":
            report.state, report.evidence = "cancelled", line
        elif line == "Job exited normally." and report.state == "started":
            report.state, report.evidence = "completed", line
        elif line.startswith(f"Job {job_id} completed at ") and report.state == "started":
            report.state, report.evidence = "ended", line
    return report
