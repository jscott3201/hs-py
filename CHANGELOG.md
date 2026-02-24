# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-02-24

### Changed

- Made `rdflib` a core dependency (no longer optional).
- Local `make` targets now run with `--all-extras` to match CI.

### Fixed

- Fixed SCRAM auth middleware tests (stale handshake purge and token expiry).
- Fixed RDF turtle export test assertion for prefixed namespace output.

### Added

- MIT license file.
- License metadata in `pyproject.toml`.

## [0.1.0] - 2026-02-24

Initial release.
