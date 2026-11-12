# Changelog

All notable changes to ARA (which began as the Azure Role Assignment Exporter) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-11-12

### Breaking
- Removed `--max-resources` and `--exclude-system`. `--exclude-managed-identities` replaces the latter and filters on the `ManagedIdentity` principal type.
- `--format` now only accepts `csv`, which writes a CSV in addition to the always-written HTML and JSON files. It no longer chooses between JSON and CSV.
- Output goes to `./results/` or `--output-dir`. Incomplete scans write `*.partial.*` files and never replace a complete result.
- JSON metadata changed: added `schema_version`, `complete`, `failed_scopes`, `pim`, `pim_unavailable_scopes`, `names`, `management_groups_scanned`, `subscriptions_scanned`, `scopes_with_assignments`; removed `total_scopes_scanned` and the top-level `scopes_scanned`. Timestamps are UTC. `principal_name` may be `null`.
- CSV has fixed columns and escapes cells that start with `=`, `+`, `-` or `@`.
- New assignment type `Active`, for active assignments whose duration is unknown (`--skip-pim`, PIM refused, or no PIM record).
- Exit codes: 0 complete, 1 fatal, 2 usage error, 3 incomplete with files written, 130 interrupted.
- Keychain login removed. The interactive token prompt only appears in a terminal.
- `Unknown` as a principal name now means not found in the directory. Names that could not be looked up are `null`.
- A depth that does not fit the scope kind is a usage error. Default depth: `management-groups` for a management group, `resource-groups` for a subscription.

### Added
- Self-contained HTML report for every scan, with search, filters, side panels, shareable views, CSV download, themes and print layout.
- Pagination on every list call.
- Tracking of failed scopes, with retries on 429, 5xx and connection errors.
- `--output-dir`.
- `AZURE_GRAPH_TOKEN` for Microsoft Graph name lookups.
- Principal names from PIM first, then Microsoft Graph in batches.
- `ManagedIdentity` principal type.
- Unit test suite (`tests/test_ara.py`), no network needed.

### Fixed
- Missing pagination silently dropped role assignments in large scopes.
- API errors were swallowed and a failed scan still reported success.
- PIM deduplication hid active access: a principal who was eligible and activated now shows both rows.

---

## [2.1.0] - 2026-02-05

### Removed

#### Zero Dependencies
- **Removed TQDM dependency**: Now uses built-in progress tracking only
- **Removed Excel/openpyxl dependency**: Removed Excel (.xlsx) output format
- **Removed test suite**: Cleaned up testing infrastructure for simpler deployment

#### Simplified Output Formats
- **Excel format removed**: Now supports only JSON and CSV output formats
- Updated `--format` argument to accept only `json` or `csv`

### Changed

#### Documentation Updates
- **Updated README**: Removed references to Excel output and tqdm
- **Updated progress tracking documentation**: Reflects built-in progress bar only
- **Updated requirements**: Now lists zero external dependencies
- **Updated version badge**: Changed from 2.0.0 to 2.1.0

#### .gitignore Updates
- **Added executable build artifacts**: Added entries for .spec, .exe, .dmg, .app, .deb, .rpm
- **Removed testing artifacts**: Removed .coverage, htmlcov/, .tox/, .pytest_cache/
- **Removed Excel results**: Removed results/*.xlsx pattern

### Technical Details
- **Version bumped to 2.1.0**
- **Zero external dependencies**: Tool now runs with Python standard library only
- **Smaller codebase**: Removed ~300 lines of Excel-related code
- **Preparation for executables**: Updated for PyInstaller/similar packaging tools

---

## [2.0.0] - 2026-01-29

### ⚠️ BREAKING CHANGES

**All output formats (JSON, CSV) have been updated with new fields. Users parsing output programmatically must update their code.**

#### New Fields in All Output Formats:
- **`assignment_type`** (string): Assignment type with four possible values:
  - `"Active permanent"` - Standard RBAC assignment with no expiration
  - `"Active time-bound"` - PIM-activated assignment with an end date
  - `"Eligible permanent"` - PIM-eligible assignment (can be activated anytime)
  - `"Eligible time-bound"` - PIM-eligible assignment with an expiration date
  
- **`start_date_time`** (nullable string): ISO 8601 timestamp for time-bound assignments (null for permanent assignments)

- **`end_date_time`** (nullable string): ISO 8601 timestamp for time-bound assignments (null for permanent assignments)

#### Migration Guide:

**For JSON consumers:**
```diff
{
  "principal_id": "...",
  "principal_name": "...",
  "role_name": "...",
+ "assignment_type": "Active permanent",
+ "start_date_time": null,
+ "end_date_time": null
}
```

**For CSV consumers:**
- Old columns: `Name, Principal ID, Type, Role, Scope, Scope Type, Resource Type`
- New columns: `Name, Principal ID, Type, Role, Scope, Scope Type, Assignment Type, Resource Type, Start Date, End Date`

**For Excel consumers:**
- Role Assignments sheet has 3 additional columns: `Assignment Type`, `Start Date`, `End Date`
- Summary sheet has new section: "Assignments by Type (PIM)"

### Added

#### PIM (Privileged Identity Management) Integration 🎉
- **Full PIM support** distinguishing between four assignment types (active permanent, active time-bound, eligible permanent, eligible time-bound)
- **Automatic PIM detection** - enabled by default for management groups, subscriptions, and resource groups
- **Graceful fallback** - continues with standard RBAC if PIM permissions unavailable
- **PIM API integration** using Microsoft's official endpoints:
  - `roleEligibilityScheduleInstances` for eligible assignments
  - `roleAssignmentScheduleInstances` for time-bound active assignments
- **Deduplication logic** to prevent counting the same assignment twice
- **Separate exception handling** for better error visibility

#### New CLI Options
- `--skip-pim` flag to explicitly disable PIM API calls (improves performance when PIM not needed)
- `--assignment-type-filter` to filter by assignment type (e.g., `--assignment-type-filter "Eligible permanent,Active time-bound"`)

#### Documentation
- **Comprehensive PIM documentation** in README with examples and troubleshooting
- **Updated requirements** with optional PIM permissions
- **PIM filtering examples** for common security audit scenarios
- **Migration guide** for users upgrading from v1.x

#### Output Enhancements
- **Console logging** shows breakdown by assignment type (e.g., "2 active permanent, 1 eligible permanent")

### Changed
- **Version bumped to 2.0.0** (breaking changes)
- **Module docstring updated** to reflect PIM features and v2.0.0
- **README examples updated** with new PIM output fields
- **AssignmentFilter** now supports filtering by assignment_type

### Fixed
- **Duplicate fields bug** in RoleAssignment dataclass (removed duplicate scope, scope_name, scope_type fields)

### Technical Details
- **Performance**: PIM adds 2 API calls per scope (eligible + time-bound active)
- **Scope limitations**: PIM only works for management groups, subscriptions, and resource groups (not individual resources, per Microsoft documentation)
- **Backward compatibility**: Use `--skip-pim` to maintain v1.x behavior (all assignments marked as "Active permanent")

---

## [1.0.0] - 2025-11-29

### Initial Release

#### Features
- Multiple authentication methods (environment variables, Azure CLI, macOS Keychain, interactive prompt)
- Multi-level scanning (management groups, subscriptions, resource groups, individual resources)
- Flexible depth control with configurable depth levels
- Direct assignment filtering (excludes inherited assignments)
- Principal name resolution via Microsoft Graph API with "Unknown" fallback
- Resource type filtering for focused scans
- Performance safeguards (rate limiting, max resource limits, API retry logic)
- Multiple output formats (JSON, CSV, Excel/xlsx)
- Enhanced filtering (role, principal type, principal name with regex, exclude system identities)
- Progress tracking with visual progress bar and ETA
- Comprehensive console summaries with statistics
- Robust error handling and graceful API error handling
- Production-ready with proper logging, validation, and OOP design
- Minimal dependencies (core uses Python standard library)
- Comprehensive test suite (88 tests: 79 unit + 9 performance)

#### Output Formats
- **JSON**: Structured data with metadata and role assignments
- **CSV**: Simple tabular format for spreadsheet import
- **Excel**: Multi-sheet workbook with Role Assignments, Summary, and Metadata sheets

#### Performance
- Memory efficient: ~0.49 KB per role assignment
- Fast processing: 50,000 assignments in 0.42 seconds
- Optimized caching with LRU cache
- Smart rate limiting with exponential backoff

[2.0.0]: https://github.com/fjdev/ARA/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/fjdev/ARA/releases/tag/v1.0.0
