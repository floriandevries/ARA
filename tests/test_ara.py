import importlib.machinery, importlib.util, pathlib, sys, unittest
sys.dont_write_bytecode = True
_path = str(pathlib.Path(__file__).resolve().parent.parent / "ara")
_loader = importlib.machinery.SourceFileLoader("ara", _path)
_spec = importlib.util.spec_from_loader("ara", _loader)
ara = importlib.util.module_from_spec(_spec)
sys.modules["ara"] = ara
_loader.exec_module(ara)

G = "11111111-2222-3333-4444-555555555555"
MG = "/providers/Microsoft.Management/managementGroups/my-mg"

class ScopeParserTest(unittest.TestCase):
    def test_management_group_forms(self):
        for text in ("my-mg", "managementGroups/my-mg", MG, MG + "/", "ManagementGroups/my-mg"):
            ref = ara.ScopeParser.parse(text)
            self.assertEqual((ref.path, ref.kind, ref.name), (MG, "management-group", "my-mg"))
    def test_subscription_forms(self):
        for text in ("subscriptions/" + G, "/subscriptions/" + G, "/Subscriptions/" + G + "/"):
            ref = ara.ScopeParser.parse(text)
            self.assertEqual((ref.path, ref.kind, ref.name), ("/subscriptions/" + G, "subscription", G))
    def test_bare_guid_is_undecided(self):
        ref = ara.ScopeParser.parse(G.upper())
        self.assertEqual((ref.kind, ref.name), ("guid", G))
    def test_rejects_other_paths(self):
        for text in ("", "/", "/subscriptions/" + G + "/resourceGroups/rg", "subscriptions/not-a-guid", "a/b/c"):
            with self.assertRaises(ara.ValidationError):
                ara.ScopeParser.parse(text)
    def test_depth_defaults(self):
        self.assertEqual(ara.ScopeParser.resolve_depth("management-group", None), "management-groups")
        self.assertEqual(ara.ScopeParser.resolve_depth("subscription", None), "resource-groups")
        self.assertEqual(ara.ScopeParser.resolve_depth("subscription", "subscriptions"), "subscriptions")
    def test_depth_invalid_for_subscription(self):
        with self.assertRaises(ara.ValidationError):
            ara.ScopeParser.resolve_depth("subscription", "management-groups")

class ScopeFromPathTest(unittest.TestCase):
    def test_levels(self):
        S = "/subscriptions/" + G
        cases = [
            (MG, "Management Group", "my-mg", None, None),
            (S, "Subscription", G, None, G),
            (S + "/resourceGroups/rg-a", "Resource Group", "rg-a", None, G),
            (S + "/resourceGroups/rg-a/providers/Microsoft.Compute/virtualMachines/vm1",
             "Resource", "vm1", "Microsoft.Compute/virtualMachines", G),
            (S + "/resourcegroups/rg-a/providers/Microsoft.Network/virtualNetworks/v/subnets/s1",
             "Resource", "s1", "Microsoft.Network/virtualNetworks/subnets", G),
            (S + "/providers/Microsoft.Security/pricings/p1", "Resource", "p1", "Microsoft.Security/pricings", G),
        ]
        for path, kind, name, rtype, sub in cases:
            s = ara.Scope.from_path(path)
            self.assertEqual((s.scope_type, s.name, s.resource_type, s.subscription_id), (kind, name, rtype, sub))
    def test_root_is_rejected(self):
        with self.assertRaises(ara.ValidationError):
            ara.Scope.from_path("/")

class RoleAssignmentModelTest(unittest.TestCase):
    def test_active_and_missing_name_allowed(self):
        a = ara.RoleAssignment(principal_id="p", principal_name=None, principal_type="User", role_name="Reader",
                               role_id="r", scope=MG, scope_name="my-mg", scope_type="Management Group",
                               assignment_type="Active")
        self.assertIsNone(a.principal_name)

if __name__ == "__main__":
    unittest.main()
