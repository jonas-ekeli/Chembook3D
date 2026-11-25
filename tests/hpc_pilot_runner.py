"""Local-only stand-ins for the two UiBKvant commands. No Gaussian or Slurm installation."""

import sys
from pathlib import Path


def main(args: list[str]) -> None:
    operation, mode, *fields = args
    if operation == "prepare":
        jd, geometry, flag = fields
        assert flag == "nosub" and Path(jd).is_absolute()
        if mode == "missing":
            return
        path = Path(geometry)
        atoms = path.read_text("ascii").splitlines()[2:]
        if mode == "wrong-geometry":
            atoms = ["He 0 0 0"]
        route = Path(jd).read_text("ascii").strip()
        path.with_suffix(".inp").write_text(
            f"%chk={path.stem}\n{route}\n\nLocal pilot\n\n0 1\n" + "\n".join(atoms) + "\n\n",
            encoding="ascii",
        )
        return
    inp, n, nodes, c, cores, t, hours = fields
    assert operation == "submit" and (n, nodes, c, t) == ("-n", "1", "-c", "-t")
    assert Path(inp).is_file()
    with Path("scheduler-calls.txt").open("a", encoding="ascii") as stream:
        stream.write(f"{inp} {nodes} {cores} {hours}\n")
    if mode == "reject":
        print(
            "sbatch: error: Batch job submission failed: invalid test allocation", file=sys.stderr
        )
        return  # matches the outer wrapper's zero exit
    job_id = int(Path.cwd().parent.name[:12], 16)
    name = Path(inp).stem
    Path(name + ".log").write_text(
        f"Starting job {job_id} on pilot-node on Saga at Tue Nov 24 09:00:00 CET 2026\n"
        f"JobName: {name}\n",
        encoding="ascii",
    )
    if mode != "lost-receipt":
        print(f"Submitted batch job {job_id}")


if __name__ == "__main__":
    main(sys.argv[1:])
