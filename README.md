# ARA

ARA, the role atlas for Azure. Who can do what, and where.

[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-3.0.0-green.svg)](CHANGELOG.md)

ARA scans a management group or a subscription and writes a report of who holds which Azure role, and where. Anyone with read access to the environment can run it. It is one Python file with no dependencies.

Only direct assignments at or below the scanned scope are listed; assignments inherited from above it are not.

## What a scan writes

Every scan writes two files into `results/` (or the directory you give with `--output-dir`):

- `role_assignments_<scope>.html`: the report. One file, no network requests, opens in any browser.
- `role_assignments_<scope>.json`: the same data, with scan metadata.

`--format csv` also writes `role_assignments_<scope>.csv`.

`<scope>` is the scope id. A scan of a subscription or of the tenant root group, whose ids are GUIDs, is named after its display name instead, for example `role_assignments_Sandbox.html` or `role_assignments_Tenant_Root_Group.html`.

### The report

The report has, from top to bottom:

- A heading with the scope, the scan time, how many management groups and subscriptions were scanned, and whether the scan was complete.
- A banner when the scan is incomplete, when PIM data is missing, or when some principal names could not be read. Each says what is missing.
- Four figures: direct assignments, high-privilege assignments, eligible assignments through PIM, and principals not found in the directory.
- A search field and three filter chips: High privilege, Eligible, and Users only. A count line shows how many rows match.
- A table with principal, role, scope, assignment type and end date.
- A side panel. Click a principal to see every assignment that principal has. Click a scope to see everyone assigned on it. Esc or the close button closes it.

Also in the report:

- Shareable views. The search text, the active chips and the open panel are kept in the address bar, so you can send the link to someone who has the same file.
- "Download CSV" saves the rows you currently see, with the same columns as the CSV file.
- Light and dark theme. It follows your system setting and has a toggle.
- A print layout that prints the table without the controls.

High privilege means the roles Owner, User Access Administrator and Role Based Access Control Administrator.

## Installation

You need Python 3.7 or later. There is nothing to install.

```bash
git clone https://github.com/fjdev/ARA.git
cd ARA
chmod +x ara
./ara --version    # ARA 3.0.0
```

Sign in with the Azure CLI (`az login`) or provide a token, see Authentication.

## Quick start

```bash
# A management group, down to management groups only (the default for a management group)
./ara --scope my-mg

# A subscription, down to resource groups (the default for a subscription)
./ara --scope xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Down to individual resources, only high-privilege roles, plus a CSV
./ara --scope my-mg --depth resources --role-filter "Owner,User Access Administrator" --format csv

# Without PIM, into another directory
./ara --scope my-mg --skip-pim --output-dir ./scans
```

Run `./ara --help` for all options.

## Scope formats

`--scope` takes a management group or a subscription. Case does not matter and a trailing slash is ignored.

| Form | Meaning |
|---|---|
| `my-mg` | Management group |
| `managementGroups/my-mg` | Management group |
| `/providers/Microsoft.Management/managementGroups/my-mg` | Management group |
| `<subscription-guid>` | Subscription (tried first as a subscription, then as a management group) |
| `subscriptions/<subscription-guid>` | Subscription |
| `/subscriptions/<subscription-guid>` | Subscription |

Resource group and resource paths are not valid scopes. Use `--depth` to include them in the scan.

## Depth

`--depth` sets the lowest level to include. A combination that does not apply to the scope is a usage error.

| Scope kind | Allowed depths | Default |
|---|---|---|
| Management group | `management-groups`, `subscriptions`, `resource-groups`, `resources` | `management-groups` |
| Subscription | `subscriptions` (the subscription only), `resource-groups`, `resources` | `resource-groups` |

`--resource-types Microsoft.Compute/virtualMachines ...` keeps only resources of those types. It needs `--depth resources`.

ARA reads one role assignment list per subscription, so a deeper depth does not make the scan slower in proportion to the number of resource groups or resources. If the API throttles you, raise `--api-delay` (seconds between calls, default 0.1).

## Authentication

ARA tries these in order:

1. `--token`
2. The environment variables `AZURE_ACCESS_TOKEN`, `AZURE_TOKEN`, `ARM_ACCESS_TOKEN`
3. The Azure CLI (`az account get-access-token`)
4. A prompt, only when running in a terminal

Principal names from Microsoft Graph use their own token: `AZURE_GRAPH_TOKEN` if set, otherwise the Azure CLI. Tokens are never printed or logged.

With the Azure CLI, an expired token is renewed when it expires during the scan. With a token you supplied, the scan stops when it expires.

## PIM and assignment types

Unless you pass `--skip-pim`, ARA also reads Privileged Identity Management. Every row has one of these assignment types:

| Assignment type | Meaning |
|---|---|
| `Active permanent` | Assigned now, no end date |
| `Active time-bound` | Assigned now, with an end date (includes an activated eligible assignment) |
| `Eligible permanent` | Can be activated, no end date |
| `Eligible time-bound` | Can be activated, with an end date |
| `Active` | Assigned now, duration unknown |

`Active` appears when ARA has no PIM record for an assignment: with `--skip-pim`, when PIM refused access, and for a few assignments PIM does not report. It does not mean permanent. With `--skip-pim`, eligible assignments are not listed. Where PIM data could not be read in full, eligible assignments may be missing and active assignments may have the type `Active`.

Eligible rows are separate rows. Someone who is eligible and has activated the role appears twice: once as eligible, once as active.

The scan metadata field `pim` is `included`, `partial`, `skipped` or `unavailable`. `partial` means PIM was refused on some scopes; `pim_unavailable_scopes` lists them. A refused PIM call is a missing permission, not a failed scan: the exit code stays 0 and the report shows a banner.

## Principal names

Names come from PIM first, then from Microsoft Graph for the rest. Principals of type `ManagedIdentity` are told apart from other service principals through Graph.

- `Unknown` means the principal was not found in the directory. It may be deleted, or not visible to the person scanning (a guest, or a principal from another tenant).
- If names could not be looked up at all, the report shows the principal ID instead and the metadata field `names` is `partial` or `unavailable`.

## Filters

Filters apply to the output. Values are comma-separated; unknown values are a usage error.

| Flag | Filters on |
|---|---|
| `--role-filter "Owner,Contributor"` | Role name |
| `--principal-type-filter "User,Group"` | `User`, `Group`, `ServicePrincipal`, `ManagedIdentity`, `ForeignGroup`, `Device` |
| `--assignment-type-filter "Eligible permanent"` | The assignment types above |
| `--principal-name-filter "^Admin"` | Principal name, regex, case-insensitive |
| `--exclude-managed-identities` | Leaves out managed identities |

`--principal-name-filter` and `--exclude-managed-identities` need a Microsoft Graph token. ARA checks this before scanning and stops with exit code 1 if it has none. If the scan runs but the Graph lookup failed or left principals unnamed, no filters are applied: the report shows every assignment and says so in a banner. The scan then counts as incomplete (exit code 3) and the output goes to the `.partial` files.

## Output

### JSON

```json
{
  "metadata": {
    "tool": "ARA",
    "version": "3.0.0",
    "schema_version": 3,
    "scan_timestamp": "2026-11-12T08:14:00Z",
    "scope": "/providers/Microsoft.Management/managementGroups/my-mg",
    "depth": "resource-groups",
    "complete": true,
    "failed_scopes": [],
    "pim": "included",
    "pim_unavailable_scopes": [],
    "names": "resolved",
    "filters_applied": {},
    "management_groups_scanned": 2,
    "subscriptions_scanned": 5,
    "scopes_with_assignments": 15,
    "total_assignments_found": 42,
    "assignments_before_filters": 42
  },
  "role_assignments": []
}
```

`failed_scopes` is a list of `{"scope": "...", "error": "..."}`. `principal_name` is `null` when no name was found. Each row has `principal_upn`, the user principal name from Microsoft Graph; it is `null` for groups and service principals, and when Graph was not used. The timestamp is UTC.

### CSV

With `--format csv`. Columns, in this order: Name, Principal ID, Type, Role, Scope, Scope Path, Scope Type, Assignment Type, Resource Type, Start Date, End Date. A cell that starts with `=`, `+`, `-` or `@` gets a leading single quote so spreadsheets do not run it as a formula.

### Incomplete scans

If a scope could not be read, or the scan was stopped, ARA still writes what it has, but to `role_assignments_<scope>.partial.html` and `.partial.json` (and `.partial.csv`). A partial file never replaces a complete result. Complete results are written to a temporary file and renamed. A complete scan with zero assignments is a valid result.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Complete |
| 1 | Fatal error, nothing written |
| 2 | Usage error |
| 3 | Incomplete: files written, at least one scope failed or the scan was stopped |
| 130 | Interrupted (Ctrl-C), partial files written |

## Troubleshooting

- **No token found**: run `az login`, or pass `--token`, or set `AZURE_ACCESS_TOKEN`.
- **Exit code 3 or a banner about failed scopes**: the failed scopes and their errors are in the JSON under `failed_scopes`. Check that you have read access there, then run again.
- **PIM banner**: your account may lack permission to read PIM schedules. Eligible assignments may then be missing and active assignments may have the type `Active`. Use `--skip-pim` to skip PIM on purpose.
- **Rows named `Unknown` or showing IDs**: see Principal names. For names beyond PIM you need a Graph token (`az login`, or `AZURE_GRAPH_TOKEN`).
- **Throttling (429)**: ARA tries each request up to four times. Raise `--api-delay` if it keeps happening.
- **`--principal-name-filter` stops immediately**: it needs a Graph token, see Filters.
- **Debugging**: add `--debug`. Use `--no-progress` for logs.

## History

ARA began as the Azure Role Assignment Exporter. Version 3.0 added the report and made the scan check that it has read everything.

## Licence

See [LICENSE](LICENSE).
