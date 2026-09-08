from flash_device.backend.progress import ProgressState, count_program_entries, parse_line


def test_progress_bar_parses_pct():
    s = ProgressState()
    parse_line("Progress: |█████-----|  50.0% Write (Sector 0x10 of 0x20) 1.00 MB/s", s)
    assert s.pct_in_file == 50.0
    assert s.op == "Write"


def test_qfil_programming_sets_file():
    s = ProgressState(total_files=3)
    parse_line("[qfil] programming boot_a.img to partition(4) ...", s)
    assert s.current_file == "boot_a.img"
    assert s.file_index == 1
    parse_line("Progress: |█████-----|  50.0% Write foo", s)
    # overall = file0 done 0/3 + half of 1/3
    assert 10 < s.overall_pct < 25


def test_count_program_entries():
    xml = (
        '<data><program filename="a.img"/><program filename="b.img"/><program filename=""/></data>'
    )
    assert count_program_entries(xml) == 2
    assert count_program_entries("not xml") == 0
