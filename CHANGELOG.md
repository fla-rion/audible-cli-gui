# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2026-08-24

### Added
- Windows and Linux builds, published automatically via GitHub Actions on version tags
- Linux bundle uses PySide6/QtWebEngine so it ships fully self-contained (no system WebKit2GTK required)
- CI smoke-tests each build by launching the packaged binary and checking the app responds

### Fixed
- App crashed on first launch (all platforms) if `~/.audible_tool/` didn't exist yet, because logging was set up before the config directory was created

## [1.0.0] - 2026-05-09

### Added
- Initial release
- Audible library browser (German marketplace)
- AAX/AAXC download support
- MP3 conversion with chapter metadata and cover art
- Local web UI (Flask + Alpine.js) wrapped in a native macOS window (pywebview)
- First-run authentication flow for Audible accounts
- macOS `.app` bundle and `.dmg` installer
