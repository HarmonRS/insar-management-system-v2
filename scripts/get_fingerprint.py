import hashlib
import subprocess
import uuid


def _run_wmic(args: str) -> str:
    try:
        output = subprocess.check_output(
            ["wmic"] + args.split(),
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            shell=False,
        )
        return output.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""

def _run_powershell(cmd: str) -> str:
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", cmd],
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            shell=False,
        )
        return output.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _pick_value(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return ""
    values = [v for v in lines[1:] if v and v.lower() not in {"serialnumber", "uuid"}]
    return values[0] if values else ""


def get_fingerprint() -> str:
    uuid_text = _run_wmic("csproduct get uuid")
    if not uuid_text:
        uuid_text = _run_powershell("(Get-CimInstance Win32_ComputerSystemProduct).UUID")

    disk_text = _run_wmic("diskdrive get serialnumber")
    if not disk_text:
        disk_text = _run_powershell("(Get-CimInstance Win32_DiskDrive | Select-Object -First 1 -ExpandProperty SerialNumber)")

    uuid_val = _pick_value(uuid_text)
    disk_val = _pick_value(disk_text)
    mac_val = f"{uuid.getnode():012x}"
    if not mac_val or mac_val == "000000000000":
        mac_val = _run_powershell("(Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Select-Object -First 1 -ExpandProperty MacAddress)") or ""

    raw = "|".join([uuid_val, disk_val, mac_val])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    print(get_fingerprint())
