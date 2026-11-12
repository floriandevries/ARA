# ARA

ARA, the role atlas for Azure. Who can do what, and where.

[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-3.0.0-green.svg)](CHANGELOG.md)

ARA scans a management group, a subscription or a whole tenant and writes an HTML report of who holds which Azure role, and where. It is one Python file with no Python packages to install. A scan only reads: it changes nothing in Azure.

## What you need

- Python 3.7 or later. No Python packages.
- A way to sign in. The Azure CLI is the easiest (`az login`). A token works too.
- Read-only permissions. The built-in Reader role at the scope you scan is enough for role assignments, management groups and PIM data (Privileged Identity Management, which holds eligible and time-bound assignments). Without permission to read PIM data, eligible assignments are missing and the report says so in a banner.
- Principal names come from PIM and from Microsoft Graph. A guest account, or an account without directory read access, may see some principals as `Unknown`.

## Quick start

```bash
git clone https://github.com/fjdev/ARA.git
cd ARA
chmod +x ara
./ara --version    # ARA 3.0.0
```

Run `az login` first, or pass a token with `--token`, or set `AZURE_ACCESS_TOKEN`. One `az login` covers both Azure and Microsoft Graph.

Then run your first scan:

```bash
./ara --scope <management-group-id>
```

Only direct assignments at or below the scanned scope are listed; assignments inherited from above it are not.

The console prints the tenant, a count per role, and one `Written:` line per file, with the HTML report last. The files are in `results/` inside the folder you ran ARA from. Open the report with `open results/<file>.html` on macOS, `xdg-open results/<file>.html` on Linux, `start results\<file>.html` on Windows, or double-click the file.

More examples:

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

A scan takes about 4 to 5 seconds per management group or subscription. On a real tenant, 13 scopes took about 1 minute and 66 scopes about 5 minutes. The calls run one at a time.

Run `./ara --help` for all options.

## Scope formats

`--scope` takes a management group, a subscription or the tenant id. Case does not matter and a trailing slash is ignored.

| Form | Meaning |
|---|---|
| `my-mg` | Management group |
| `managementGroups/my-mg` | Management group |
| `/providers/Microsoft.Management/managementGroups/my-mg` | Management group |
| `<subscription-guid>` | Subscription (tried first as a subscription, then as a management group) |
| `subscriptions/<subscription-guid>` | Subscription |
| `/subscriptions/<subscription-guid>` | Subscription |
| `<tenant-id>` | The whole tenant |

Giving the tenant id as `--scope` scans the whole tenant, because the tenant root management group has the tenant id as its id:

```bash
./ara --scope 00000000-0000-0000-0000-000000000000
```

Resource group and resource paths are not valid scopes. Use `--depth` to include them in the scan.

## The report

The report is one HTML file with no network requests. It opens in any browser. From top to bottom:

- A heading with the tenant name, the scope name, the tenant's default domain, the scan time, how many management groups and subscriptions were scanned, and whether the scan was complete. If the tenant name could not be read the heading shows the tenant id; if the tenant is unknown it shows nothing. The browser tab title is `ARA <tenant name> <scope name>`.
- A banner when the scan is incomplete, when PIM data is missing, or when some principal names could not be read. Each says what is missing.
- Four figures: direct assignments, high-privilege assignments, eligible assignments through PIM, and principals not found in the directory.
- A search field and three filter chips: High privilege, Eligible, and Users only. A count line shows how many rows match.
- A table with principal, role, scope, assignment type and end date.
- A side panel. Click a principal to see every assignment that principal has. Click a scope to see everyone assigned on it. Esc or the close button closes it. The panel shows the object ID and, for users, the user principal name. For a guest that is the ID in the scanned tenant, not in the guest's home tenant.
- Shareable views. The search text, the active chips and the open panel are kept in the address bar, so you can send the link to someone who has the same file.
- "Download CSV" saves the rows you currently see, with the same columns as the CSV file.
- Light and dark theme. It follows your system setting and has a toggle.
- A print layout that prints the table without the controls.

High privilege means the roles Owner, User Access Administrator and Role Based Access Control Administrator.

`Unknown` means the principal was not found in the directory: deleted, or not visible to the account that ran the scan.

`Active` means assigned now, with a duration ARA could not read.

Only direct assignments at or below the scanned scope are listed; assignments inherited from above it are not. To see what applies to a subscription from above, scan the management group above it or the whole tenant.

The report contains access data. Treat it as confidential and share it only with people who may see who has access to what.

The console summary starts with `Tenant: <name> (<domain>)`.

## Depth

`--depth` sets the lowest level to include. A combination that does not apply to the scope is a usage error.

| Scope kind | Allowed depths | Default |
|---|---|---|
| Management group | `management-groups`, `subscriptions`, `resource-groups`, `resources` | `management-groups` |
| Subscription | `subscriptions` (the subscription only), `resource-groups`, `resources` | `resource-groups` |

`--resource-types Microsoft.Compute/virtualMachines ...` keeps only resources of those types. It needs `--depth resources`.

ARA reads one role assignment list per subscription, so a deeper depth does not make the scan slower in proportion to the number of resource groups or resources. If the API throttles you, raise `--api-delay` (seconds between calls, default 0.1).

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

- `Unknown` is explained under The report. A guest or a principal from another tenant may be invisible to the person scanning.
- If names could not be looked up at all, the report shows the principal ID instead and the metadata field `names` is `partial` or `unavailable`.

## Output files

Every scan writes two files into `results/` (or the directory you give with `--output-dir`):

- `role_assignments_<scope>.html`: the report.
- `role_assignments_<scope>.json`: the same data, with scan metadata.

`--format csv` also writes `role_assignments_<scope>.csv`.

When the tenant is known, the files are named `role_assignments_<tenant>_<scope>.<ext>`, where `<tenant>` is the part of the tenant's default domain before the first dot (`contoso` for `contoso.onmicrosoft.com`), or the tenant name when the domain is missing. When it is unknown the `<tenant>_` part is left out.

`<scope>` is the scope id. A scan of a subscription or of the tenant root group, whose ids are GUIDs, is named after its display name instead, for example `role_assignments_contoso_Sandbox.html` or `role_assignments_contoso_Tenant_Root_Group.html`.

### JSON

```json
{
  "metadata": {
    "tool": "ARA",
    "version": "3.0.0",
    "schema_version": 3,
    "scan_timestamp": "2026-11-12T08:14:00Z",
    "scope": "/providers/Microsoft.Management/managementGroups/my-mg",
    "tenant": {"id": "00000000-0000-0000-0000-000000000000", "name": "Contoso", "domain": "contoso.onmicrosoft.com"},
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

The metadata field `tenant` is `{"id", "name", "domain"}`, or `null` when the tenant is unknown. `name` and `domain` are `null` when the tenant list could not be read (one extra call to `/tenants`) or does not contain the tenant. That never makes a scan incomplete.

### CSV

With `--format csv`. Columns, in this order: Name, Principal ID, Type, Role, Scope, Scope Path, Scope Type, Assignment Type, Resource Type, Start Date, End Date. A cell that starts with `=`, `+`, `-` or `@` gets a leading single quote so spreadsheets do not run it as a formula.

### Incomplete scans

If a scope could not be read, or the scan was stopped, ARA still writes what it has, but to `role_assignments_<tenant>_<scope>.partial.html` and `.partial.json` (and `.partial.csv`). A partial file never replaces a complete result. Complete results are written to a temporary file and renamed. A complete scan with zero assignments is a valid result.

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
- **PIM banner**: see PIM and assignment types. Use `--skip-pim` to skip PIM on purpose.
- **Rows named `Unknown` or showing IDs**: see Principal names. For names beyond PIM you need a Graph token (`az login`, or `AZURE_GRAPH_TOKEN`).
- **Throttling (429)**: ARA tries each request up to four times. Raise `--api-delay` if it keeps happening.
- **`--principal-name-filter` stops immediately**: it needs a Graph token, see Filters.
- **Debugging**: add `--debug`. Use `--no-progress` for logs.

## History

ARA began as the Azure Role Assignment Exporter. Version 3.0 added the report and made the scan check that it has read everything.

## Licence

See [LICENSE](LICENSE).
