"""describe.py against text captured from akaiutil 4.6.8 (real HD5.img and generated floppies)."""

from akaiutil_gui import describe

S3000_SAMPLE = """/disk0/A/VOLUME 001/SINE.S3
fnr:     2
start:   block 0x0006
size:    704 bytes
type:    S3000 sample
ramname: "SINE"
scount:  0x00000100 (      256)
srate:   44100Hz
sdur:    5.805ms
bandw:   20kHz
rkey:    60
ctune:   +0
stune:   +0
hltoff:  +0
locat:   0x00010100
start:   0x00000016
end:     0x000000ff
pmode:   LOOP
lnum:    1
lfirst:  1
loop 1:
  loopat:  0x000000c0
  length:  0x000000a8.0x8fdf
  ltime:   HOLD"""

S3000_PROGRAM = """/disk0/A/VOLUME 001/TEST PROGRAM.P3
fnr:     1
start:   block 0x0005
size:    384 bytes
type:    S3000 program
ramname: "TEST PROGRAM"
midich:    1
key lo-hi: 24-127
oct:       +0
auxch:     OFF
kgxf:      OFF
kgnum:     1
kg1a:      0x600c0
keygroup 1:
  key lo-hi: 24-127
  ctune:     +0
  stune:     +0
  filter:    99
  velxf:     ON
  velzone 1:
    vel lo-hi: 0-127
    ctune:     +0
    stune:     +0
    loud:      +0
    filter:    +0
    pan:       +0
    auxchoff:  0
    pitch:     TRACK
    pmode:     SAMPLE
    sname:     "SINE"
    shdra:     0x60180
  velzone 2:
    vel lo-hi: 0-0
    ctune:     +0
    stune:     +0
    loud:      +0
    filter:    +0
    pan:       +0
    auxchoff:  0
    pitch:     TRACK
    pmode:     SAMPLE
    sname:     ""
  velzone 3:
    vel lo-hi: 0-0
    ctune:     +0
    stune:     +0
    loud:      +0
    filter:    +0
    pan:       +0
    auxchoff:  0
    pitch:     CONST
    pmode:     SAMPLE
    sname:     ""
  kgnexta:   0x60180"""

S900_SAMPLE = """/disk2/A/VOLUME 001/KICK.S9
fnr:     1
start:   block 0x0004
size:    33135 bytes
type:    S900 sample
ramname: "KICK"
scount:  0x00005622 (    22050)
srate:   44100Hz
sdur:    500.000ms
npitch:  60.00
loud:    +0
dmadesa: 0x0000
locat:   0x00000000
start:   0x00000000
end:     0x00005622
llen:    0x00005622
pmode:   ONESHOT
dir:     NORM
type:    NORM
compr:   OFF"""

EFFECTS = """/disk0/A/VOLUME 001/EFFECTS FILE.X
fnr:     3
start:   block 0x0007
size:    7312 bytes
type:    S1100/S3000 effects
ramname: "EFFECTS FILE\""""

OS_FILE = """/disk0/A/VOLUME 001/MULTI FILE.C
fnr:     5
start:   block 0x0009
size:    262144 bytes
type:    S1000 operating system"""

CUE_LIST = """/disk0/A/VOLUME 001/SHOW.Q
fnr:     6
start:   block 0x000a
size:    1000 bytes
type:    S1100/S3000 cue-list
ramname: "SHOW"
cuenum:  1

cue  time           type  name          mnote mvel  lvl  pan
------------------------------------------------------------
  1  00:00:00:00.0  ON    KICK              60  100   99  +0
------------------------------------------------------------"""

DISK = """/disk0
disk:     0
fd:       3
start:    byte 0x0000000000
parts:    9
disktype: S1000/S3000 harddisk
blksize:  0x2000 bytes
total:    0xffff blocks (524280 KB)
free:     0x3fd0 blocks (130688 KB) =  24.9%
bad:      0x0000 blocks (     0 KB) =   0.0%"""

PARTITION = """/disk0/A
part:      A
parttype:  S1000/S3000 harddisk sampler partition
blksize:   0x2000 bytes
start:     block 0x0000
size:      0x1e00 blocks ( 61440 KB)
free:      0x083e blocks ( 16880 KB) =  27.5%
bad:       0x0000 blocks (     0 KB) =   0.0%
max. vols: 100"""

VOLUME = """/disk0/A/VOLUME 001
vnr:         1
vname:       "VOLUME 001"
load number: -  
voltype:     S3000
osver:       17.00
start:       block 0x0003
max. files:  510"""

FLOPPY_VOLUME = """/disk2/A/VOLUME 001
parttype:    low-density floppy
blksize:     0x0400 bytes
size:        0x0320 blocks (   800 KB)
free:        0x02fb blocks (   763 KB) =  95.4%
bad:         0x0000 blocks (     0 KB) =   0.0%
vnr:         1
vname:       "VOLUME 001"
voltype:     S900
start:       block 0x0000
max. files:  64"""


def rows(block):
    return {r.label: r.value for r in block.rows}


def block(details, title):
    return next(b for b in details.blocks if b.title == title)


def test_parse_nests_by_indentation():
    path, root, other = describe.parse_info(S3000_SAMPLE)
    assert path == "/disk0/A/VOLUME 001/SINE.S3" and other == []
    assert root.get("ramname") == "SINE"  # quotes stripped
    assert [n.value for n in root.all("start")] == ["block 0x0006", "0x00000016"]  # file location, then sample marker
    (loop,) = root.sections("loop ")
    assert loop.get("loopat") == "0x000000c0" and loop.get("ltime") == "HOLD"


def test_s3000_sample_in_plain_words():
    d = describe.describe_file(S3000_SAMPLE)
    assert (d.title, d.raw) == ("SINE", S3000_SAMPLE)
    assert d.subtitle == "S3000 sample  ·  /disk0/A/VOLUME 001/SINE.S3"
    assert rows(d.blocks[0]) == {"Type": "S3000 sample", "Size": "704 B"}
    s = rows(block(d, "Sample"))
    assert s["Length"] == "5.8 ms  (256 samples)"
    assert s["Sample rate"] == "44.1 kHz" and s["Bandwidth"] == "20 kHz"
    assert s["Original key"] == "C3  (MIDI 60)"  # middle C is C3 on Akai panels
    assert (s["Coarse tune"], s["Fine tune"]) == ("+0 semitones", "+0 cents")
    p = rows(block(d, "Playback"))
    assert p["Mode"] == "Normal looping"
    assert (p["Start marker"], p["End marker"]) == ("sample 22", "sample 255")  # hex 0x16 / 0xff, not the file's block
    lp = rows(block(d, "Loop 1"))
    assert lp == {"Loop at": "sample 192", "Length": "168.56 samples", "Time": "Hold (until release)"}
    tech = block(d, "Technical")
    assert tech.muted and rows(tech)["Starts at"] == "block 6 (0x0006)" and rows(tech)["File number"] == "2"
    # nothing akaiutil-internal (addresses, pointers) leaks into the readable text
    text = " ".join(r.value for b in d.blocks for r in b.rows)
    assert "0x00010100" not in text and "srate" not in text


def test_tune_labels_follow_akaiutils_struct_not_the_initials():
    """akaiutil's `stune` is SEMITONES and `ctune` is CENTS (akaiutil_file.h), the reverse of what c/s suggest."""
    d = describe.describe_file(S3000_SAMPLE.replace("ctune:   +0", "ctune:   +7").replace("stune:   +0", "stune:   -2"))
    s = rows(block(d, "Sample"))
    assert s["Coarse tune"] == "-2 semitones" and s["Fine tune"] == "+7 cents"


def test_s3000_program_with_velocity_zones():
    d = describe.describe_file(S3000_PROGRAM)
    assert d.title == "TEST PROGRAM"
    prog = rows(block(d, "Program"))
    assert prog["MIDI channel"] == "Channel 1" and prog["Aux output"] == "Off" and prog["Keygroup crossfade"] == "Off"
    assert prog["Key range"] == "C0 – G8  (MIDI 24–127)" and prog["Keygroups"] == "1"
    kg = block(d, "Keygroup 1  ·  C0 – G8")
    assert rows(kg)["Velocity crossfade"] == "On" and rows(kg)["Filter cutoff"] == "99"
    assert kg.headers[:3] == ["Zone", "Velocity", "Sample"]
    assert len(kg.table) == 1  # zones 2 and 3 have no sample, so they're left out
    zone = dict(zip(kg.headers, kg.table[0]))
    assert zone["Velocity"] == "0 – 127" and zone["Sample"] == "SINE"
    assert zone["Pitch"] == "Key track" and zone["Play mode"] == "As sample"


def test_s900_sample():
    d = describe.describe_file(S900_SAMPLE)
    s = rows(block(d, "Sample"))
    assert s["Length"] == "500 ms  (22,050 samples)" and s["Sample rate"] == "44.1 kHz"
    assert s["Original pitch"] == "C3  (60.00)" and s["Sample type"] == "Normal" and s["Compressed"] == "Off"
    p = rows(block(d, "Playback"))
    assert p["Mode"] == "One-shot" and p["Direction"] == "Forward" and p["Loop length"] == "22,050 samples"
    assert p["End marker"] == "sample 22,050"


def test_files_with_nothing_beyond_the_header():
    for text, name, kind in [(EFFECTS, "EFFECTS FILE", "S1100/S3000 effects"), (OS_FILE, "MULTI FILE.C", "S1000 operating system")]:
        d = describe.describe_file(text)
        assert d.title == name and rows(d.blocks[0])["Type"] == kind
        assert [b.title for b in d.blocks] == ["", "Technical"]  # no stray "as reported" dump


def test_types_it_doesnt_present_are_shown_as_akaiutil_printed_them():
    d = describe.describe_file(CUE_LIST)
    assert d.title == "SHOW"
    dump = block(d, "As reported by akaiutil")
    assert "cuenum:  1" in dump.text and "00:00:00:00.0  ON    KICK" in dump.text
    assert "fnr:" not in dump.text and "/disk0" not in dump.text  # the header's already shown above


def test_garbage_and_errors_are_not_swallowed():
    d = describe.describe_file("must be inside a volume")
    assert "must be inside a volume" in block(d, "As reported by akaiutil").text


def test_disk_partition_and_volume():
    disk = describe.describe_disk(DISK)
    assert disk.title == "Disk 0" and disk.subtitle == "S1000/S3000 hard disk"
    r = rows(disk.blocks[0])
    assert r["Size"] == "511.99 MB" and r["Free (inside partitions)"] == "127.62 MB  (24.9%)"
    assert r["Bad blocks"] == "None" and r["Partitions"] == "9" and r["Block size"] == "8.0 KB"
    assert rows(block(disk, "Technical")) == {"Start offset in the image": "0 bytes"}
    assert "fd" not in " ".join(x.label for b in disk.blocks for x in b.rows).lower()  # file descriptor: meaningless to people

    part = describe.describe_partition(PARTITION)
    assert part.title == "Partition A"
    r = rows(part.blocks[0])
    assert r["Type"] == "S1000/S3000 sampler partition" and r["Size"] == "60.00 MB"
    assert r["Free"] == "16.48 MB  (27.5%)" and r["Room for"] == "100 volumes"

    vol = describe.describe_volume(VOLUME)
    assert vol.title == "VOLUME 001" and vol.subtitle == "S3000 volume"
    r = rows(vol.blocks[0])
    assert r["Load number"] == "None" and r["OS version"] == "17.00" and r["Room for"] == "510 files"
    assert [b.title for b in vol.blocks] == ["", "Technical"]


def test_floppy_volume_also_shows_the_disk_space():
    vol = describe.describe_volume(FLOPPY_VOLUME)
    r = rows(block(vol, "Disk space"))
    assert r["Size"] == "800.0 KB" and r["Free"] == "763.0 KB  (95.4%)"
    assert "OS version" not in rows(vol.blocks[0])  # S900 volumes have none


def test_a_multi_selection_summarises_itself():
    from akaiutil_gui.engine.models import FileEntry

    files = [
        FileEntry(1, "A.S3", 2048, "0x0001", "17.00"),
        FileEntry(2, "B.S3", 1024, "0x0002", "17.00"),
        FileEntry(3, "C.P3", 100, "0x0003", "17.00"),
    ]
    d = describe.describe_selection(files)
    assert d.title == "3 files selected"
    assert rows(d.blocks[0]) == {"Total size": "3.1 KB  (3,172 bytes)", "Sample": "2", "Program": "1"}
    assert describe.describe_selection(files[:1]).title == "1 file selected"


def test_volume_shows_file_count_and_used_space():
    from akaiutil_gui.engine.models import Disk, DiskUsage, Partition, VolumeUsage

    bs = 8192
    disk = Disk(0, "HD", 1, bs, 1000, 600, 7.8, 4.7, 60.0)
    part = Partition(0, "A", "HD", bs, 400, 100, 3.1, 0.8, 25.0)
    usage = DiskUsage(disk, [part], [VolumeUsage(0, "A", 1, "VOLUME 001", 100 * bs, 425, 700_000), VolumeUsage(0, "A", 2, "OTHER", 5 * bs, 1, 100)])

    d = describe.describe_volume(VOLUME, usage)
    assert [b.title for b in d.blocks] == ["", "Contents", "Technical"]
    assert rows(block(d, "Contents")) == {
        "Files": "425 files",
        "File data": "683.6 KB",  # the files' own sizes...
        "Space used on disk": "800.0 KB  (25% of partition A)",  # ...vs. what they take in whole blocks
    }
    assert describe.describe_volume(VOLUME.replace("vnr:         1", "vnr:         2"), usage).blocks[1].rows[0].value == "1 file"

    # without usage (or for a volume it doesn't know) the view is just the plain one
    assert [b.title for b in describe.describe_volume(VOLUME).blocks] == ["", "Technical"]
    assert [b.title for b in describe.describe_volume(VOLUME.replace("vnr:         1", "vnr:         9"), usage).blocks] == ["", "Technical"]
