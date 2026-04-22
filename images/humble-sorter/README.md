humble-sorter
=============

Classifies items in a Humble comic/book bundle into one of three lanes —
`comics`, `manga`, `ebook` — and writes a manifest file per lane.

Rules applied in precedence order:

1. `(Manga)` / `: Manga` / `(Manhwa)` / `(Manhua)` marker in item filename → manga.
2. Bundle title contains `Manga`/`Manhwa`/`Manhua` or a known manga publisher
   (Kodansha, VIZ, Yen Press, Seven Seas, Kana, Shonen Jump, Shogakukan) → manga.
3. Bundle title starts with `Humble Book Bundle` AND item PDF < 50 MiB → ebook.
   If item PDF >= 50 MiB, size-veto reclassifies to comics (mislabeled bundle).
4. Default → comics.

Usage
-----

    humble_sorter.py --bundle-dir /scratch/sources/humble-bundle/<name> \
                     --bundle-title "Humble Comics Bundle: ... by ..." \
                     --output-dir /scratch/classified

Writes `comics.txt`, `manga.txt`, `ebook.txt` to `--output-dir`, one PDF
relative path per line. All three files are always written, even if empty.

Tests
-----

    cd containers/images/humble-sorter
    python -m unittest test_humble_sorter -v
