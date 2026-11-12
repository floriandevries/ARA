"""Write a sample HTML report with N generated rows to a temporary directory and print its path.

Usage: python3 tests/sample_report.py N [--incomplete]
"""
import importlib.machinery, importlib.util, pathlib, sys, tempfile

sys.dont_write_bytecode = True
_loader = importlib.machinery.SourceFileLoader("ara", str(pathlib.Path(__file__).resolve().parent.parent / "ara"))
ara = importlib.util.module_from_spec(importlib.util.spec_from_loader("ara", _loader))
_loader.exec_module(ara)

MG = "/providers/Microsoft.Management/managementGroups/"
SUB = "/subscriptions/0000000{}-1111-2222-3333-444444444444"
SCOPES = [
    (MG + "mg-platform", "mg-platform", "Management Group", None),
    (MG + "mg-landing-zones", "mg-landing-zones", "Management Group", None),
    (SUB.format(1), "sub-prod", "Subscription", None),
    (SUB.format(2), "sub-dev", "Subscription", None),
    (SUB.format(1) + "/resourceGroups/rg-app-prod", "rg-app-prod", "Resource Group", None),
    (SUB.format(2) + "/resourceGroups/rg-network", "rg-network", "Resource Group", None),
    (SUB.format(1) + "/resourceGroups/rg-app-prod/providers/Microsoft.KeyVault/vaults/kv-app", "kv-app",
     "Resource", "Microsoft.KeyVault/vaults"),
    (SUB.format(2) + "/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-hub", "vnet-hub",
     "Resource", "Microsoft.Network/virtualNetworks"),
]
ROLES = ["Reader", "Contributor", "Owner", "User Access Administrator", "Role Based Access Control Administrator",
         "Key Vault Secrets User", "Network Contributor"]


def name(k: int, ptype: str):
    if k == 1:
        return "<b>bold</b>"
    if k % 11 == 0:
        return None
    if k % 13 == 0:
        return "Unknown"
    return "{}-{:03d}".format(ptype.lower(), k)


def rows(n: int):
    principals = max(1, n // 3)
    for i in range(n):
        k = i % principals
        ptype = ara.ARAConfig.PRINCIPAL_TYPES[k % len(ara.ARAConfig.PRINCIPAL_TYPES)]
        atype = ara.ARAConfig.ASSIGNMENT_TYPES[i % len(ara.ARAConfig.ASSIGNMENT_TYPES)]
        path, scope_name, scope_type, rtype = SCOPES[i % len(SCOPES)]
        bound = atype.endswith("time-bound")
        yield ara.RoleAssignment(
            principal_id="{:08x}-0000-4000-8000-{:012x}".format(k, k), principal_name=name(k, ptype),
            principal_type=ptype, role_name=ROLES[i % len(ROLES)], role_id="r{}".format(i % len(ROLES)),
            scope=path, scope_name=scope_name, scope_type=scope_type, resource_type=rtype, assignment_type=atype,
            start_date_time="2026-01-01T00:00:00Z" if bound else None,
            end_date_time="2026-12-{:02d}T17:00:00Z".format(1 + i % 28) if bound else None)


def main() -> None:
    n = int(sys.argv[1])
    incomplete = "--incomplete" in sys.argv[2:]
    result = ara.ScanResult(MG + "mg-platform", "mg-platform", "resources", "2026-11-12T09:14:00Z", list(rows(n)),
                            management_groups_scanned=2, subscriptions_scanned=2, names="resolved")
    if incomplete:
        result.failed_scopes = [{"scope": SUB.format(3), "error": "403 Forbidden"},
                                {"scope": SUB.format(4), "error": "Timed out"}]
        result.pim_unavailable_scopes = [SUB.format(2)]
    print(ara.HTMLOutputHandler(tempfile.mkdtemp(prefix="ara-sample-")).generate(result))


if __name__ == "__main__":
    main()
