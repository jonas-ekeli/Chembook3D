"""D148: the structures `build_structure` made, kept in memory by id while the app runs, so Claude
saves one (set_coordinates, create_node) or goes on from it without copying its coordinates.
Only the last MAX_BUILDS are kept; nothing is written to the investigation."""

import copy
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import HTTPException, Request

from chembook3d import xyz
from chembook3d.services import builder

MAX_BUILDS = 100


@dataclass
class Build:
    structure: builder.Structure
    charge: int | None
    multiplicity: int | None


class Builds:
    def __init__(self, keep: int = MAX_BUILDS) -> None:
        self.keep = keep
        self._found: OrderedDict[str, Build] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, build: Build) -> str:
        build_id = "build-" + secrets.token_hex(4)
        with self._lock:
            self._found[build_id] = copy.deepcopy(build)
            while len(self._found) > self.keep:
                self._found.popitem(last=False)
        return build_id

    def get(self, build_id: str) -> Build | None:
        with self._lock:
            found = self._found.get(build_id)
            if found is not None:
                self._found.move_to_end(build_id)
            return copy.deepcopy(found)


def get(request: Request, build_id: str) -> Build:
    found = request.app.state.builds.get(build_id)
    if found is None:
        raise HTTPException(
            404,
            f"No build {build_id}: builds are kept only while the app runs (the last "
            f"{MAX_BUILDS}); run build_structure again",
        )
    return found


def xyz_of(build: Build) -> str:
    atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in build.structure.rows]
    if not atoms:
        raise HTTPException(422, "That build has no atoms")
    return xyz.format_xyz(atoms, "Built with build_structure")
