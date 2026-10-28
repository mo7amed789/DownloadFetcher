# DownloadFetcher

DownloadFetcher is a Windows desktop search and download assistant. It searches the web, ranks results, finds relevant direct download links, previews source pages, and saves downloads to the user's Downloads folder.

## Run

Double-click `run.bat`. The launcher checks GitHub for updates before starting the app.

## Automatic updates

The app reads `version.json` from this repository. When a newer version is published, it downloads the verified release ZIP, checks its SHA-256 hash, and updates the application files while preserving the user's virtual environment and Downloads folder.
