import importlib.machinery, importlib.util, io, json, pathlib, socket, sys, unittest, urllib.error, urllib.parse
sys.dont_write_bytecode = True
_path = str(pathlib.Path(__file__).resolve().parent.parent / "ara")
_loader = importlib.machinery.SourceFileLoader("ara", _path)
_spec = importlib.util.spec_from_loader("ara", _loader)
ara = importlib.util.module_from_spec(_spec)
sys.modules["ara"] = ara
_loader.exec_module(ara)
ara.logger.disabled = True  # keep retry warnings out of test output

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

class FakeOpener:
    """Stands in for urlopen: returns or raises scripted outcomes and records each request."""
    def __init__(self, outcomes):
        self.outcomes = list(outcomes); self.requests = []; self.urls = []
    def __call__(self, request, timeout=None):
        self.requests.append(request); self.urls.append(request.full_url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())

def http_error(code, headers=None):
    return urllib.error.HTTPError("https://x", code, "error", headers or {}, io.BytesIO(b""))

class HttpClientTest(unittest.TestCase):
    def client(self, outcomes, **kw):
        self.opener = FakeOpener(outcomes); self.sleeps = []
        return ara.HttpClient("t", api_delay=0, opener=self.opener, sleep=self.sleeps.append, **kw)
    def test_get_all_follows_next_link(self):
        c = self.client([{"value": [1, 2], "nextLink": "https://x/page2"}, {"value": [3]}])
        self.assertEqual(c.get_all("https://x/page1"), [1, 2, 3])
        self.assertEqual(self.opener.urls, ["https://x/page1", "https://x/page2"])
    def test_retries_429_with_retry_after(self):
        c = self.client([http_error(429, {"Retry-After": "7"}), {"value": []}])
        c.get("https://x"); self.assertEqual(self.sleeps, [7])
    def test_retries_5xx_and_network_errors_with_backoff(self):
        c = self.client([http_error(503), urllib.error.URLError("down"), socket.timeout(), {"ok": 1}])
        self.assertEqual(c.get("https://x"), {"ok": 1}); self.assertEqual(self.sleeps, [2, 4, 8])
    def test_gives_up_after_four_attempts(self):
        c = self.client([http_error(500)] * 4)
        with self.assertRaises(ara.APIError) as e: c.get("https://x")
        self.assertEqual(e.exception.status, 500); self.assertEqual(len(self.opener.urls), 4)
    def test_404_is_not_retried_and_carries_status(self):
        c = self.client([http_error(404)])
        with self.assertRaises(ara.APIError) as e: c.get("https://x")
        self.assertEqual(e.exception.status, 404); self.assertEqual(len(self.opener.urls), 1)
    def test_401_refreshes_token_once(self):
        c = self.client([http_error(401), {"ok": 1}], refresh_token=lambda: "new")
        self.assertEqual(c.get("https://x"), {"ok": 1})
        self.assertEqual(self.opener.requests[1].get_header("Authorization"), "Bearer new")
    def test_401_without_refresh_raises_authentication_error(self):
        c = self.client([http_error(401)])
        with self.assertRaises(ara.AuthenticationError): c.get("https://x")
    def test_post_sends_json_body(self):
        c = self.client([{"value": []}]); c.request("POST", "https://x", body={"ids": ["a"]})
        self.assertEqual(json.loads(self.opener.requests[0].data), {"ids": ["a"]})

class FakeAPI:
    """Stands in for AzureAPIClient: maps a URL substring to rows (get_all) or a dict (get); records each call."""
    def __init__(self, routes, error=None):
        self.routes = routes; self.calls = []; self.error = error
    def url(self, path):
        return "https://m" + path
    def get(self, url, params=None):
        self.calls.append(url + "?" + urllib.parse.urlencode(params or {}))
        if self.error:
            raise self.error
        for key, value in self.routes.items():
            if key in url:
                return value
        raise ara.APIError("no route for " + url, 404)
    get_all = get

def row(scope, principal="p1", role_guid="acdd72a7-3385-48ef-bd42-f606fba81ae7", id_=None):
    """An ARM role assignment as the roleAssignments list returns it."""
    id_ = id_ or scope.rstrip("/") + "/providers/Microsoft.Authorization/roleAssignments/" + principal + "-" + role_guid
    return {"id": id_, "name": id_.rsplit("/", 1)[-1], "type": "Microsoft.Authorization/roleAssignments",
            "properties": {"scope": scope, "principalId": principal, "principalType": "User",
                           "roleDefinitionId": "/providers/Microsoft.Authorization/roleDefinitions/" + role_guid}}

def instance(scope, origin=None, end=None, principal="p1", name="Ann", kind="Assigned"):
    """A PIM schedule instance as roleAssignmentScheduleInstances or roleEligibilityScheduleInstances returns it."""
    props = {"scope": scope, "principalId": principal, "principalType": "User", "assignmentType": kind,
             "roleDefinitionId": "/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7",
             "startDateTime": "2026-11-12T10:00:00Z",
             "expandedProperties": {"principal": {"displayName": name}, "roleDefinition": {"displayName": "Reader"}}}
    if origin:
        props["originRoleAssignmentId"] = origin
    if end:
        props["endDateTime"] = end
    return {"id": scope + "/providers/Microsoft.Authorization/scheduleInstances/x", "properties": props}

class AssignmentCollectorTest(unittest.TestCase):
    S = "/subscriptions/" + G
    def test_subscription_keeps_inside_rows_only_and_ignores_case(self):
        rows = [row(self.S), row(self.S + "/resourceGroups/rg-a"),
                row(self.S.upper() + "/RESOURCEGROUPS/rg-a/providers/Microsoft.Network/virtualNetworks/v/subnets/s"),
                row(MG), row("/"), row("/subscriptions/" + G + "0")]
        api = FakeAPI({"roleAssignments": rows})
        kept = ara.AssignmentCollector(api).for_subscription(ara.Scope.from_path(self.S))
        self.assertEqual(len(kept), 3)
        self.assertNotIn("atScope", api.calls[0])
    def test_management_group_keeps_own_rows_and_filters_at_scope(self):
        api = FakeAPI({"roleAssignments": [row(MG), row(MG.lower()), row("/"), row(MG + "-parent")]})
        kept = ara.AssignmentCollector(api).for_management_group(ara.Scope.from_path(MG))
        self.assertEqual(len(kept), 2); self.assertIn("atScope", api.calls[0])
    def test_role_names_keyed_by_lowercased_guid(self):
        api = FakeAPI({"roleDefinitions": [{"id": self.S + "/providers/Microsoft.Authorization/roleDefinitions/ACDD72A7-3385-48EF-BD42-F606FBA81AE7",
                                            "properties": {"roleName": "Reader"}}]})
        self.assertEqual(ara.AssignmentCollector(api).role_names(self.S),
                         {"acdd72a7-3385-48ef-bd42-f606fba81ae7": "Reader"})

class HierarchyClientTest(unittest.TestCase):
    def test_tree_flattens_groups_and_subscriptions(self):
        tree = {"name": "root", "properties": {"displayName": "Root", "children": [
            {"type": "/subscriptions", "name": G, "displayName": "Prod"},
            {"type": "Microsoft.Management/managementGroups", "name": "child", "displayName": "Child",
             "children": [{"type": "/subscriptions", "name": G[:-1] + "6", "displayName": "Dev"}]}]}}
        mgs, subs = ara.HierarchyClient(FakeAPI({"managementGroups/root": tree})).management_group_tree("root")
        self.assertEqual([m.name for m in mgs], ["Root", "Child"])
        self.assertEqual([(s.name, s.subscription_id) for s in subs], [("Prod", G), ("Dev", G[:-1] + "6")])
    def test_tree_without_children(self):
        mgs, subs = ara.HierarchyClient(FakeAPI({"managementGroups/root": {"name": "root", "properties": {"displayName": "Root", "children": None}}})).management_group_tree("root")
        self.assertEqual((len(mgs), subs), (1, []))

class DepthFilterTest(unittest.TestCase):
    def test_depths(self):
        S = "/subscriptions/" + G
        paths = {"mg": MG, "sub": S, "rg": S + "/resourceGroups/rg", "res": S + "/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm"}
        expect = {"management-groups": {"mg"}, "subscriptions": {"mg", "sub"},
                  "resource-groups": {"mg", "sub", "rg"}, "resources": {"mg", "sub", "rg", "res"}}
        for depth, kept in expect.items():
            got = {k for k, p in paths.items() if ara.DepthFilter.keep(ara.Scope.from_path(p), depth, None)}
            self.assertEqual(got, kept, depth)
    def test_resource_types_filter_resources_only_and_ignore_case(self):
        S = "/subscriptions/" + G + "/resourceGroups/rg"
        vm = ara.Scope.from_path(S + "/providers/Microsoft.Compute/virtualMachines/vm")
        sa = ara.Scope.from_path(S + "/providers/Microsoft.Storage/storageAccounts/sa")
        types = ["microsoft.compute/virtualmachines"]
        self.assertTrue(ara.DepthFilter.keep(vm, "resources", types))
        self.assertFalse(ara.DepthFilter.keep(sa, "resources", types))
        self.assertTrue(ara.DepthFilter.keep(ara.Scope.from_path(S), "resources", types))

class AssignmentBuilderTest(unittest.TestCase):
    S = "/subscriptions/" + G
    READER = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
    def builder(self): return ara.AssignmentBuilder({self.READER: "Reader"}, {G: "Prod"})
    def test_eligible_and_activated_give_two_rows(self):
        r = row(self.S, id_="ra1")
        out = self.builder().build([r], [instance(self.S, origin="ra1", end="2026-11-12T18:00:00Z", kind="Activated")],
                                   [instance(self.S)])
        self.assertEqual(sorted(a.assignment_type for a in out), ["Active time-bound", "Eligible permanent"])
    def test_permanent_active(self):
        out = self.builder().build([row(self.S, id_="ra1")], [instance(self.S, origin="ra1")], [])
        self.assertEqual((out[0].assignment_type, out[0].end_date_time, out[0].principal_name), ("Active permanent", None, "Ann"))
    def test_unmatched_and_pim_off_are_active(self):
        b = self.builder()
        self.assertEqual(b.build([row(self.S, id_="ra1")], [], [])[0].assignment_type, "Active")
        out = b.build([row(self.S, id_="ra1")], None, None)
        self.assertEqual((out[0].assignment_type, out[0].principal_name), ("Active", None))
    def test_two_eligibilities_for_same_principal_and_role_are_both_kept(self):
        out = self.builder().build([], [], [instance(self.S), instance(self.S, end="2026-12-01T00:00:00Z")])
        self.assertEqual(sorted(a.assignment_type for a in out), ["Eligible permanent", "Eligible time-bound"])
    def test_unknown_role_definition_falls_back_to_guid(self):
        out = self.builder().build([row(self.S, role_guid="00000000-0000-0000-0000-00000000dead", id_="ra1")], None, None)
        self.assertEqual(out[0].role_name, "00000000-0000-0000-0000-00000000dead")
    def test_scope_fields(self):
        out = self.builder().build([row(self.S, id_="a"), row(self.S + "/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/s", id_="b")], None, None)
        self.assertEqual((out[0].scope_name, out[0].scope_type), ("Prod", "Subscription"))
        self.assertEqual((out[1].scope_name, out[1].resource_type), ("s", "Microsoft.Network/virtualNetworks/subnets"))
    def test_bad_row_is_skipped_with_warning(self):
        ara.logger.disabled = False
        try:
            with self.assertLogs(ara.logger, level="WARNING") as logs:
                out = self.builder().build([row(self.S, id_="a"), row("/", id_="bad-row"), row(self.S, id_="b")], None, None)
        finally:
            ara.logger.disabled = True
        self.assertEqual(len(out), 2)
        self.assertEqual(len(logs.records), 1); self.assertIn("bad-row", logs.output[0])
    def test_origin_id_matches_ignoring_case(self):
        out = self.builder().build([row(self.S, id_="/X/RA1")], [instance(self.S, origin="/x/ra1")], None)
        self.assertEqual(out[0].assignment_type, "Active permanent")

class PIMCollectorTest(unittest.TestCase):
    def test_403_becomes_pim_unavailable(self):
        api = FakeAPI({}, error=ara.APIError("forbidden", status=403))
        with self.assertRaises(ara.PIMUnavailable): ara.PIMCollector(api).eligible(ara.Scope.from_path("/subscriptions/" + G))
    def test_other_errors_pass_through(self):
        api = FakeAPI({}, error=ara.APIError("boom", status=500))
        with self.assertRaises(ara.APIError): ara.PIMCollector(api).active(ara.Scope.from_path(MG))
    def test_subscription_keeps_inside_rows(self):
        S = "/subscriptions/" + G
        api = FakeAPI({"roleEligibilityScheduleInstances": [instance(S + "/resourceGroups/rg"), instance(MG)]})
        self.assertEqual(len(ara.PIMCollector(api).eligible(ara.Scope.from_path(S))), 1)

if __name__ == "__main__":
    unittest.main()
