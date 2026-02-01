ebook-metadata
==============

Fetch metadata from the Kobo store or Goodreads and enrich epub ebooks.

For each epub in the input directory the tool:

1. Reads existing OPF metadata (title, author, ISBN)
2. Searches the configured provider for a match
3. Enriches the epub with metadata from the provider (series, tags, cover, synopsis, publisher, etc.)
4. Replaces the cover only if the fetched cover is larger than the existing one
5. Moves the file to `output-dir/Author/Series/Title.epub` (or `Author/Title.epub` if no series)
6. Creates a marker file for idempotency

## Providers

| Provider | Flag | Notes |
|---|---|---|
| Kobo | `--provider kobo` | Uses cloudscraper. Requires `--country` and `--language`. |
| Goodreads | `--provider goodreads` | Uses requests. ISBN fast-path via autocomplete API. |

## Usage

```
ebook_metadata.py --input-dir /books/sources/kobo \
                  --output-dir /books/library/ebooks \
                  --marker-dir /books/markers/kobo \
                  --provider kobo \
                  --country de \
                  --language en \
                  --skip-large 50
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--input-dir` | Yes | | Directory containing epub files |
| `--output-dir` | Yes | | Output directory for organized ebooks |
| `--marker-dir` | Yes | | Directory to store processing markers |
| `--provider` | No | `kobo` | Metadata provider (`kobo` or `goodreads`) |
| `--country` | No | `us` | Kobo store country code |
| `--language` | No | `en` | Kobo store language code |
| `--skip-large` | No | `0` | Skip files larger than N megabytes (0 = no limit) |
| `--timeout` | No | `30` | HTTP request timeout in seconds |
| `-v` | No | | Enable verbose (debug) logging |

## Output structure

```
output-dir/
  Author Name/
    Series Name/
      Book Title.epub       # book belongs to a series
    Standalone Book.epub    # book has no series
```

## Attribution

- Kobo scraper adapted from [Kobo-Metadata](https://github.com/NotSimone/Kobo-Metadata) (GPL v3)
- Goodreads scraper adapted from [calibre_plugins/goodreads](https://github.com/kiwidude68/calibre_plugins) by Grant Drake (GPL v3)
