"""Parsers against text captured from akaiutil 4.6.8."""

from akaiutil_gui.engine import parse

DISKS = """
/
disk  type parts  blksize tot/blks  tot/MB  free/blks free/MB free/%
--------------------------------------------------------------------
   0    HD    18   0x2000   0x4e20   156.2     0x2cca    89.6   57.3
   1   FLH     1   0x0400   0x0640     1.6     0x063b     1.6   99.7
--------------------------------------------------------------------
total:   2 disk(s)

"""

PARTS = """
/disk0
part  type startblk size/blks  size/MB  free/blks free/MB free/%
----------------------------------------------------------------
   A    HD   0x0000    0x0280      5.0     0x0263     4.8   95.5
   B    HD   0x0280    0x0280      5.0     0x027d     5.0   99.5
  DD    DD   0x2b00    0x0100      2.0     0x0100     2.0  100.0
----------------------------------------------------------------
total:   3 partition(s)

"""

VOLS = """
/disk0/A
vnr   vname         lnum  startblk  osver  type
---------------------------------------------------
  1   DRUMS          -      0x0003  17.00  S3000
  2   MY SYNTH       007    0x0005  17.00  S3000
---------------------------------------------------
total:   2 volume(s) (max. 100)

"""

S3000_FILES = """
/disk0/A/DRUMS
fnr  fname               size/B  startblk  osver  tags
--------------------------------------------------------------
  1  SINE440.S3           88392    0x0007  17.00              
  2  SNARE 2.S3           88392    0x0012  17.00  01 05       
--------------------------------------------------------------
total:   2 file(s) (max. 510),    176784 bytes

"""

S900_FILES = """
/disk2/A/VOLUME 001
fnr  fname               size/B  startblk  uncompr
--------------------------------------------------
  1  KICK.S9              33135    0x0004  
--------------------------------------------------
total:   1 file(s) (max.  64),     33135 bytes

"""

EMPTY = """
/disk1/A/NOT NAMED
fnr  fname               size/B  startblk  osver  tags
--------------------------------------------------------------
--------------------------------------------------------------
total:   0 file(s) (max.  64),         0 bytes
"""


def test_disks():
    disks = parse.parse_disks(DISKS)
    assert [(d.index, d.type, d.partitions, d.block_size) for d in disks] == [
        (0, "HD", 18, 0x2000),
        (1, "FLH", 1, 0x400),
    ]
    assert disks[0].total_mb == 156.2 and disks[0].path == "/disk0"
    assert (disks[0].total_blocks, disks[0].free_blocks) == (0x4E20, 0x2CCA)
    assert disks[0].total_bytes == 0x4E20 * 0x2000


def test_partitions_including_dd():
    parts = parse.parse_partitions(PARTS, 0, 0x2000)
    assert [p.name for p in parts] == ["A", "B", "DD"]
    assert (parts[0].size_blocks, parts[0].free_blocks) == (0x280, 0x263)
    assert parts[0].size_bytes == 0x280 * 0x2000 and parts[0].used_bytes == (0x280 - 0x263) * 0x2000
    assert parts[2].is_dd and not parts[0].is_dd
    assert parts[0].path == "/disk0/A"


def test_volumes_keep_spaces_in_names():
    vols = parse.parse_volumes(VOLS, 0, "A")
    assert [(v.index, v.name, v.load_number, v.os_version, v.type) for v in vols] == [
        (1, "DRUMS", "-", "17.00", "S3000"),
        (2, "MY SYNTH", "007", "17.00", "S3000"),
    ]
    assert vols[1].path == "/disk0/A/MY SYNTH"


def test_files_s3000():
    files = parse.parse_files(S3000_FILES)
    assert [(f.index, f.name, f.size, f.start_block) for f in files] == [
        (1, "SINE440.S3", 88392, "0x0007"),
        (2, "SNARE 2.S3", 88392, "0x0012"),
    ]
    assert files[1].tags == "01 05"
    assert files[0].is_sample and files[0].kind == "Sample"


def test_files_s900_has_no_osver():
    (f,) = parse.parse_files(S900_FILES)
    assert (f.name, f.size, f.os_version) == ("KICK.S9", 33135, "")


def test_empty_volume_and_garbage():
    assert parse.parse_files(EMPTY) == []
    assert parse.parse_files("directory not found\n") == []
    assert parse.parse_disks("") == []


DIRREC = """
/disk0
part  type startblk size/blks  size/MB  free/blks free/MB free/%
----------------------------------------------------------------
   A    HD   0x0000    0x0280      5.0     0x025f     4.7   94.8
   B    HD   0x0280    0x0280      5.0     0x027d     5.0   99.5
  DD    DD   0x0500    0x0100      2.0     0x0100     2.0  100.0
----------------------------------------------------------------
total:   3 partition(s)

/disk0/A
vnr   vname         lnum  startblk  osver  type
---------------------------------------------------
  1   DRUMS          -      0x0003  17.00  S3000
  2   GONE           -      0x0005  17.00  INACT
  3   MY SYNTH       -      0x0007  17.00  S3000
---------------------------------------------------
total:   3 volume(s) (max. 100)

/disk0/A/DRUMS
fnr  fname               size/B  startblk  osver  tags
--------------------------------------------------------------
  1  KICK.S3              44292    0x0009  17.00              
  2  SNARE.S3             44292    0x000f  17.00              
--------------------------------------------------------------
total:   2 file(s) (max. 510),     88584 bytes

/disk0/A/MY SYNTH
fnr  fname               size/B  startblk  osver  tags
--------------------------------------------------------------
  1  LEAD.S3                  1    0x001b  17.00              
--------------------------------------------------------------
total:   1 file(s) (max. 510),         1 bytes

/disk0/B
vnr   vname         lnum  startblk  osver  type
---------------------------------------------------
---------------------------------------------------
total:   0 volume(s) (max. 100)

/disk0/DD
tnr  tname         cstarts cstarte csizes  csizee     scount  srate  stype
---------------------------------------------------------------------------
---------------------------------------------------------------------------
total:   0 take(s)

"""


def test_dirrec_gives_volume_sizes_in_whole_blocks():
    parts, usages = parse.parse_dirrec(DIRREC, 0, 0x2000)
    assert [p.name for p in parts] == ["A", "B", "DD"]
    # inactive volumes are skipped, and the "tnr" section of a DD partition is ignored
    assert [(u.partition, u.index, u.name, u.files) for u in usages] == [("A", 1, "DRUMS", 2), ("A", 3, "MY SYNTH", 1)]
    assert usages[0].used_bytes == 2 * 6 * 0x2000  # 44292 B -> 6 blocks of 8 KB, two files
    assert usages[0].data_bytes == 2 * 44292  # ...while the files themselves add up to less
    assert usages[1].data_bytes == 1
    assert usages[1].used_bytes == 0x2000  # a 1-byte file still takes a whole block
