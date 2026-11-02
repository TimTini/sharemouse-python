# Offline runtime packages

`packages/` contains unmodified upstream runtime wheels from PyPI. Only standard
Windows x64 CPython 3.11–3.14 is supported. Four cffi wheels cover those interpreter
ABIs; cryptography uses the CPython 3.11 stable ABI. No source distributions,
platform variants for other operating systems, or development tooling are bundled.

`packages.json` records the original download URL and SHA256 of every wheel.
`requirements.txt` pins versions and accepted wheel hashes. Installation uses
`pip --no-index --require-hashes --only-binary=:all:` with this local directory.
No repository code fetches these URLs at setup or runtime.

| Package | Purpose | Upstream | License |
| --- | --- | --- | --- |
| spake2 0.9 | SPAKE2 PIN authentication | https://github.com/warner/python-spake2 | MIT |
| cryptography 50.0.2 | HKDF-SHA256 | https://github.com/pyca/cryptography | Apache-2.0 OR BSD-3-Clause; bundled components carry additional notices |
| cffi 2.1.1 | cryptography runtime dependency | https://cffi.readthedocs.io/ | MIT-0; bundled libffi has its own notice |
| pycparser 3.12 | cffi dependency | https://github.com/eliben/pycparser | BSD-3-Clause |

Author/license notices remain inside each wheel's `*.dist-info/licenses/` or
license metadata. These third-party archives are retained intact, including
upstream package metadata and any files upstream includes in its runtime wheel;
application code does not vendor or reimplement PAKE math.

The upstream Python SPAKE2 code is not constant-time. Reusing this implementation
is not an independent cryptographic audit or certification of this application.
