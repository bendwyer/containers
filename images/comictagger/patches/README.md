comictagger image patches
=========================

Patches applied to installed site-packages after `pip install`. Each `.patch`
file is a unified diff applied relative to the Python site-packages root
(i.e. `mangabaka_talker/mangabaka.py`, `comictagger/...`, etc).

Applied by the Dockerfile's builder stage via:

    patch -p1 --directory=/install/lib/python3.12/site-packages < <file>

When upstream ships a fix, bump the corresponding package version in
`../requirements.txt` and delete the patch file. `patch` will loud-fail on
mismatched context if the patched wheel has already been updated, forcing
visibility rather than silent drift.

Current patches
---------------

- `mangabaka_talker-none-guard.patch` — guards None for `secondary_titles` in
  `_format_secondary_titles` (mangabaka_talker 0.0.9). No upstream issue
  open at time of patch.
