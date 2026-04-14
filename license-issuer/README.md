# InSAR License Issuer

This folder contains the offline license issuing tool for the LIC2 scheme used by the backend.

## Files

```text
license-issuer/
├── issue_license.py        # CLI entry and reusable signing logic
├── license_issuer_gui.pyw  # Desktop GUI
├── start_gui.bat           # Windows launcher for the GUI
├── private_key.b64         # Private key, keep offline and do not distribute
├── public_key.b64          # Public key, can be synced to backend
└── README.md
```

## Requirements

```bash
pip install cryptography
```

The GUI uses the Python standard library `tkinter`, so no extra GUI dependency is required.

## GUI Usage

Windows:

```bat
start_gui.bat
```

Or directly open:

```text
license_issuer_gui.pyw
```

The GUI provides these flows:

- Read the current machine fingerprint
- Issue a `.lic` file
- Verify an existing `.lic` file
- Rotate key pairs
- Sync `public_key.b64` into `backend/app/license_service.py`

The backend sync target is configurable. If the issuer tool is copied to another machine or another folder layout, choose the target `license_service.py` manually in the GUI, or use `--target` in CLI.

## CLI Usage

Show help:

```bash
python issue_license.py --help
```

Get local fingerprint:

```bash
python issue_license.py fingerprint
```

Issue a license:

```bash
python issue_license.py issue ^
  --to "XX省自然资源厅" ^
  --fingerprint <fingerprint> ^
  --days 365 ^
  --output license_xx.lic
```

Verify a license:

```bash
python issue_license.py verify license_xx.lic
```

Generate a new key pair:

```bash
python issue_license.py rotate-key
```

Force rotate an existing key pair:

```bash
python issue_license.py rotate-key --force
```

Rotate and immediately sync the new public key to backend:

```bash
python issue_license.py rotate-key --force --sync-backend
```

Sync the current `public_key.b64` to backend without rotating:

```bash
python issue_license.py sync-public-key
```

## Standard Flow

1. Run `fingerprint` on the target machine and collect the value.
2. Run `issue` on the issuer machine and generate the `.lic` file.
3. Upload the `.lic` file through the admin page, or replace `backend/license/license.lic`.
4. If keys are rotated, sync the new public key to `backend/app/license_service.py` and redeploy the backend.

## Notes

- The private key must stay offline and should not be committed or distributed.
- Rotating the private key invalidates old licenses. All customer licenses must then be reissued.
- The fingerprint algorithm is intentionally kept consistent with `backend/app/license_service.py`.
