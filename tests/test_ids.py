from flash_device import ids
from flash_device.safety import guards
from flash_device.utils import usb


def test_ids_single_source():
    # guards/usb must re-export the same objects, never a copy.
    assert guards.EDL_PIDS is ids.EDL_PIDS
    assert usb.EDL_PIDS is ids.EDL_PIDS
    assert guards.classify_pid is ids.classify_pid
    assert usb.classify_pid is ids.classify_pid
    assert ids.classify_pid(0x9008) == "edl"
    assert 0x9008 in ids.EDL_PIDS
