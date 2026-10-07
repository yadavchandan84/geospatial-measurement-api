"""Upload validation and safe zip extraction."""

import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.config import Settings

ALLOWED_EXTENSIONS = {".kml": "kml", ".zip": "shapefile"}
REQUIRED_SHAPEFILE_PARTS = (".shp", ".shx", ".dbf")


class UploadError(Exception):
    """Raised for client-side problems with an upload. Mapped to an HTTP error."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ShapefileMembers:
    shp: str
    shx: str
    dbf: str
    prj: str | None


def detect_file_type(filename: str | None) -> str:
    if not filename:
        raise UploadError("A file name is required.")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UploadError(
            f"Unsupported file extension '{ext or '(none)'}'. Allowed: {allowed}.",
            status_code=415,
        )
    return ALLOWED_EXTENSIONS[ext]


def validate_kml_bytes(content: bytes) -> None:
    """Cheap sniff test so obviously wrong files fail fast with a clear message."""
    if b"<kml" not in content[:4096].lower():
        raise UploadError("File does not look like a KML document.", status_code=422)


def _is_unsafe_member(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return path.is_absolute() or ".." in path.parts or (len(name) > 1 and name[1] == ":")


def inspect_shapefile_zip(zip_path: Path, settings: Settings) -> ShapefileMembers:
    """Check that the archive is a safe zip holding exactly one complete shapefile."""
    if not zipfile.is_zipfile(zip_path):
        raise UploadError("Uploaded .zip file is not a valid zip archive.")

    with zipfile.ZipFile(zip_path) as zf:
        infos = [
            i
            for i in zf.infolist()
            if not i.is_dir() and not i.filename.startswith("__MACOSX/")
        ]
        if len(infos) > settings.max_zip_entries:
            raise UploadError(f"Zip archive has too many entries (> {settings.max_zip_entries}).")
        if sum(i.file_size for i in infos) > settings.max_uncompressed_bytes:
            raise UploadError(
                "Zip archive is too large when uncompressed.", status_code=413
            )
        for info in infos:
            if _is_unsafe_member(info.filename):
                raise UploadError(f"Zip archive contains an unsafe path: {info.filename!r}.")

        # Group members by base path (without extension), case-insensitively.
        groups: dict[str, dict[str, str]] = {}
        for info in infos:
            p = PurePosixPath(info.filename)
            stem = str(p.with_suffix("")).lower()
            groups.setdefault(stem, {})[p.suffix.lower()] = info.filename

    shp_groups = {stem: parts for stem, parts in groups.items() if ".shp" in parts}
    if not shp_groups:
        raise UploadError("Zip archive does not contain a .shp file.")
    if len(shp_groups) > 1:
        raise UploadError(
            "Zip archive contains more than one shapefile; upload one shapefile per zip."
        )

    parts = next(iter(shp_groups.values()))
    missing = [ext for ext in REQUIRED_SHAPEFILE_PARTS if ext not in parts]
    if missing:
        raise UploadError(
            "Shapefile is incomplete; missing required component(s): " + ", ".join(missing) + "."
        )

    return ShapefileMembers(
        shp=parts[".shp"], shx=parts[".shx"], dbf=parts[".dbf"], prj=parts.get(".prj")
    )


def extract_shapefile(zip_path: Path, members: ShapefileMembers, dest: Path) -> Path:
    """Extract only the shapefile components into a flat directory. Returns the .shp path."""
    dest.mkdir(parents=True, exist_ok=True)
    base = "layer"
    wanted = {members.shp: ".shp", members.shx: ".shx", members.dbf: ".dbf"}
    if members.prj:
        wanted[members.prj] = ".prj"

    with zipfile.ZipFile(zip_path) as zf:
        # Also bring along an optional .cpg (encoding) file if present.
        for info in zf.infolist():
            stem = str(PurePosixPath(info.filename).with_suffix("")).lower()
            if (
                PurePosixPath(info.filename).suffix.lower() == ".cpg"
                and stem == str(PurePosixPath(members.shp).with_suffix("")).lower()
            ):
                wanted[info.filename] = ".cpg"

        for member, ext in wanted.items():
            # Write to a name we control instead of trusting archive paths.
            target = dest / f"{base}{ext}"
            with zf.open(member) as src, open(target, "wb") as out:
                out.write(src.read())

    return dest / f"{base}.shp"
