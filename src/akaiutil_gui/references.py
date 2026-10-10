"""Which samples do a volume's programs use? (Qt-free)

A program names the samples it plays (`sname` in each velocity zone, or in each layer of an S900 keygroup), and only
samples in the same volume count: the sampler looks them up there by name. So a volume's programs say which of its
samples are in use, which programs a sample belongs to, and which names point at nothing. Multis name programs, not
samples, so they don't change any of this.
"""

from dataclasses import dataclass

from .describe import Node, parse_info
from .engine.models import FileEntry


def sample_names(info_text: str) -> list[str]:
    """The sample names a program's `infoi` text refers to, each once, in order of first use."""
    _, root, _ = parse_info(info_text)
    found: list[str] = []

    def walk(node: Node) -> None:
        for child in node.children:
            if child.key == "sname":
                name = child.value.strip()
                if name and name not in found:
                    found.append(name)
            walk(child)

    walk(root)
    return found


def _key(name: str) -> str:
    return name.strip().casefold()


def _stem(file_name: str) -> str:
    """"KICK.S3" -> "KICK": what a program calls the sample."""
    return file_name.rsplit(".", 1)[0] if "." in file_name else file_name


@dataclass(frozen=True)
class VolumeReferences:
    """One volume's files, and the sample names each of its programs uses (by program file number)."""

    files: tuple[FileEntry, ...]
    programs: dict[int, tuple[str, ...]]

    @property
    def has_programs(self) -> bool:
        return any(f.is_program for f in self.files)

    def _samples_named(self, name: str) -> list[FileEntry]:
        return [f for f in self.files if f.is_sample and _key(_stem(f.name)) == _key(name)]

    def programs_using(self, sample: FileEntry) -> list[FileEntry]:
        """The programs that play this sample."""
        stem = _key(_stem(sample.name))
        return [p for p in self.files if p.is_program and any(_key(n) == stem for n in self.programs.get(p.index, ()))]

    def samples_of(self, program: FileEntry) -> tuple[list[FileEntry], list[str]]:
        """(the samples this program plays that are in the volume, the names it uses that aren't)."""
        found: list[FileEntry] = []
        missing: list[str] = []
        for name in self.programs.get(program.index, ()):
            matches = self._samples_named(name)
            if not matches:
                missing.append(name)
            found += [m for m in matches if m not in found]
        return found, missing

    def unused_samples(self) -> list[FileEntry]:
        """The samples no program plays. Empty when there are no programs at all (nothing could use them)."""
        if not self.has_programs:
            return []
        used = {_key(n) for names in self.programs.values() for n in names}
        return [f for f in self.files if f.is_sample and _key(_stem(f.name)) not in used]
