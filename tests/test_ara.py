import contextlib, csv, http.client, importlib.machinery, importlib.util, io, os, tempfile, json, pathlib, re, socket, sys, unittest, urllib.error, urllib.parse
sys.dont_write_bytecode = True
_path = str(pathlib.Path(__file__).resolve().parent.parent / "ara")
_loader = importlib.machinery.SourceFileLoader("ara", _path)
_spec = importlib.util.spec_from_loader("ara", _loader)
ara = importlib.util.module_from_spec(_spec)
sys.modules["ara"] = ara
_loader.exec_module(ara)
ara.logger.disabled = True  # keep retry warnings out of test output
from unittest import mock

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
        if hasattr(outcome, "read"):
            return outcome
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
    def test_truncated_body_is_retried(self):
        class Truncated(io.BytesIO):
            def read(self, *a): raise http.client.IncompleteRead(b"{")
        c = self.client([Truncated(), {"ok": 1}])
        self.assertEqual(c.get("https://x"), {"ok": 1}); self.assertEqual(self.sleeps, [2])
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

class TenantTest(unittest.TestCase):
    TENANTS = {"/tenants": [{"tenantId": "AAA", "displayName": "Other", "defaultDomain": "other.onmicrosoft.com"},
                            {"tenantId": "bbb-1", "displayName": "Contoso", "defaultDomain": "contoso.onmicrosoft.com"}]}
    def test_tenant_matches_id_ignoring_case(self):
        api = FakeAPI(self.TENANTS); h = ara.HierarchyClient(api); h.tenant_id = "BBB-1"
        self.assertEqual(h.tenant(), {"id": "bbb-1", "name": "Contoso", "domain": "contoso.onmicrosoft.com"})
        self.assertIn("api-version=2022-12-01", api.calls[0])
    def test_tenant_failure_keeps_id(self):
        h = ara.HierarchyClient(FakeAPI({}, error=ara.APIError("no", 403))); h.tenant_id = "t"
        self.assertEqual(h.tenant(), {"id": "t", "name": None, "domain": None})
    def test_tenant_not_in_list_keeps_id(self):
        h = ara.HierarchyClient(FakeAPI(self.TENANTS)); h.tenant_id = "zzz"
        self.assertEqual(h.tenant(), {"id": "zzz", "name": None, "domain": None})
    def test_tenant_unknown_without_root(self):
        self.assertIsNone(ara.HierarchyClient(FakeAPI(self.TENANTS)).tenant())
    def test_roots_set_tenant_id(self):
        h = ara.HierarchyClient(FakeAPI({"managementGroups/root": {"properties": {"tenantId": "t1"}}})); h.management_group_tree("root")
        self.assertEqual(h.tenant_id, "t1")
        h = ara.HierarchyClient(FakeAPI({"/subscriptions/": {"tenantId": "t2"}})); h.subscription(G)
        self.assertEqual(h.tenant_id, "t2")
    def test_scan_records_tenant(self):
        class H(FakeHierarchy):
            def tenant(self): return {"id": "t", "name": "Contoso", "domain": None}
        r = ara.Scanner(H(mgs=[ara.Scope.from_path(MG)]), FakeCollector({}), None, show_progress=False).scan(
            ara.ScopeRef(MG, "management-group", "my-mg"), "management-groups")
        self.assertEqual(r.tenant["name"], "Contoso")
    def test_metadata_and_file_names(self):
        t = {"id": "t", "name": "Contoso", "domain": "contoso.onmicrosoft.com"}
        r = result([], tenant=t); h = ara.JSONOutputHandler("x")
        self.assertEqual(r.to_dict()["metadata"]["tenant"]["name"], "Contoso")
        self.assertEqual(h.file_name(r), "role_assignments_contoso_my-mg.json")
        self.assertEqual(h.file_name(result([], tenant=dict(t, domain=None))), "role_assignments_Contoso_my-mg.json")
        self.assertEqual(h.file_name(result([], tenant=None)), "role_assignments_my-mg.json")
        self.assertIsNone(result([]).to_dict()["metadata"]["tenant"])
    def test_console_first_line(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ara.ConsoleSummary().print(result([], tenant={"id": "t", "name": "Contoso", "domain": "contoso.onmicrosoft.com"}), [])
            ara.ConsoleSummary().print(result([], tenant={"id": "t", "name": None, "domain": None}), [])
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines[0], "Tenant: Contoso (contoso.onmicrosoft.com)")
        self.assertIn("Tenant: t", lines)
    def test_report_embeds_tenant_and_hostile_name_stays_inert(self):
        html = ara.HTMLOutputHandler("x").render(result([], tenant={"id": "t", "name": "</script><b>x", "domain": "d"}))
        self.assertEqual(html.count("</script>"), 2)
        self.assertNotIn("<b>x", html)
        m = re.search(r'<script type="application/json" id="ara-data">(.*?)</script>', html, re.S)
        self.assertEqual(json.loads(m.group(1))["metadata"]["tenant"]["name"], "</script><b>x")

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
    def test_management_group_rows_get_the_display_name(self):
        root = "/providers/Microsoft.Management/managementGroups/" + G
        b = ara.AssignmentBuilder({}, {}, {G: "Tenant Root Group"})
        self.assertEqual([(a.scope_name, a.scope) for a in b.build([row(root, id_="a"), row(MG, id_="b")], None, None)],
                         [("Tenant Root Group", root), ("my-mg", MG)])
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
    def test_management_group_keeps_exact_scope_rows_only(self):
        api = FakeAPI({"roleEligibilityScheduleInstances": [instance(MG), instance(MG.upper()), instance(MG + "-parent"),
                                                            instance("/subscriptions/" + G)]})
        self.assertEqual(len(ara.PIMCollector(api).eligible(ara.Scope.from_path(MG))), 2)
        self.assertIn("atScope", api.calls[0])

class FakeHierarchy:
    def __init__(self, mgs=(), subs=(), error=None, subscription_error=None):
        self.mgs, self.subs, self.error, self.subscription_error = list(mgs), list(subs), error, subscription_error
    def management_group_tree(self, mg_id):
        if self.error:
            raise self.error
        return self.mgs, self.subs
    def subscription(self, subscription_id):
        if self.subscription_error:
            raise self.subscription_error
        return ara.Scope.from_path("/subscriptions/" + subscription_id)

class FakeCollector:
    """Returns rows per scope path; fail maps a scope path to the exception its listing raises."""
    def __init__(self, rows_by_scope, fail=None):
        self.rows, self.fail = rows_by_scope, fail or {}
    def _rows(self, scope):
        if scope.scope_path in self.fail:
            raise self.fail[scope.scope_path]
        return self.rows.get(scope.scope_path, [])
    for_management_group = for_subscription = _rows
    def role_names(self, scope_path):
        return {}

class FakePIM:
    """Eligible rows per scope path, no active instances; unavailable refuses both lists, active_refused only the active list."""
    def __init__(self, unavailable=(), eligible_rows=None, active_refused=()):
        self.unavailable, self.eligible_rows, self.active_refused = unavailable, eligible_rows or {}, active_refused
    def eligible(self, scope):
        if scope.scope_path in self.unavailable:
            raise ara.PIMUnavailable("forbidden")
        return self.eligible_rows.get(scope.scope_path, [])
    def active(self, scope):
        if scope.scope_path in self.unavailable or scope.scope_path in self.active_refused:
            raise ara.PIMUnavailable("forbidden")
        return []

class ScannerTest(unittest.TestCase):
    S1, S2 = "/subscriptions/" + G, "/subscriptions/" + G[:-1] + "6"
    def scanner(self, fail=None, pim="default", subs=None):
        mg = ara.Scope.from_path(MG); subs = subs if subs is not None else [ara.Scope.from_path(self.S1), ara.Scope.from_path(self.S2)]
        rows = {MG: [row(MG, id_="m")], self.S1: [row(self.S1, id_="a"), row(self.S1 + "/resourceGroups/rg", id_="b")], self.S2: [row(self.S2, id_="c")]}
        return ara.Scanner(FakeHierarchy([mg], subs), FakeCollector(rows, fail or {}),
                           FakePIM() if pim == "default" else pim, show_progress=False)
    def ref(self): return ara.ScopeParser.parse(MG)
    def test_depth_management_groups_skips_subscriptions(self):
        r = self.scanner().scan(self.ref(), "management-groups")
        self.assertEqual((len(r.assignments), r.subscriptions_scanned, r.management_groups_scanned), (1, 0, 1))
    def test_depth_resource_groups(self):
        r = self.scanner().scan(self.ref(), "resource-groups")
        self.assertEqual((len(r.assignments), r.subscriptions_scanned, r.complete, r.scopes_with_assignments), (4, 2, True, 4))
    def test_failed_scope_is_recorded_and_scan_continues(self):
        r = self.scanner(fail={self.S1: ara.APIError("boom", status=500)}).scan(self.ref(), "subscriptions")
        self.assertEqual([f["scope"] for f in r.failed_scopes], [self.S1])
        self.assertEqual((len(r.assignments), r.complete), (2, False))
    def test_authentication_error_stops_scan(self):
        r = self.scanner(fail={self.S1: ara.AuthenticationError("expired")}).scan(self.ref(), "subscriptions")
        self.assertTrue(r.stopped); self.assertFalse(r.complete); self.assertEqual(len(r.assignments), 1)
    def test_keyboard_interrupt_returns_partial(self):
        r = self.scanner(fail={self.S2: KeyboardInterrupt()}).scan(self.ref(), "subscriptions")
        self.assertTrue(r.interrupted); self.assertEqual(len(r.assignments), 2)
    def test_pim_states(self):
        self.assertEqual(self.scanner().scan(self.ref(), "subscriptions").pim_status, "included")
        self.assertEqual(self.scanner(pim=None).scan(self.ref(), "subscriptions").pim_status, "skipped")
        r = self.scanner(pim=FakePIM(unavailable=(self.S1,))).scan(self.ref(), "subscriptions")
        self.assertEqual((r.pim_status, r.pim_unavailable_scopes, r.complete), ("partial", [self.S1], True))
        r = self.scanner(pim=FakePIM(unavailable=(MG, self.S1, self.S2))).scan(self.ref(), "subscriptions")
        self.assertEqual(r.pim_status, "unavailable")
    def test_active_refused_keeps_eligible_rows_and_flags_scope(self):
        pim = FakePIM(eligible_rows={self.S1: [instance(self.S1)]}, active_refused=(self.S1,))
        r = self.scanner(pim=pim).scan(self.ref(), "subscriptions")
        self.assertIn("Eligible permanent", [a.assignment_type for a in r.assignments if a.scope == self.S1])
        self.assertEqual(r.pim_unavailable_scopes, [self.S1])
    def test_pim_unavailable_warns_once(self):
        ara.logger.disabled = False
        try:
            with self.assertLogs(ara.logger, level="WARNING") as logs:
                self.scanner(pim=FakePIM(unavailable=(self.S1, self.S2))).scan(self.ref(), "subscriptions")
        finally:
            ara.logger.disabled = True
        self.assertEqual(len(logs.records), 1)
    def test_root_failure_is_raised(self):
        s = ara.Scanner(FakeHierarchy(error=ara.APIError("nope", status=403)), FakeCollector({}, {}), None, show_progress=False)
        with self.assertRaises(ara.APIError): s.scan(self.ref(), "subscriptions")
    def test_bare_guid_falls_back_to_management_group(self):
        s = ara.Scanner(FakeHierarchy([], [], subscription_error=ara.APIError("nf", status=404)), FakeCollector({}, {}), None, show_progress=False)
        self.assertEqual(s.resolve(ara.ScopeParser.parse(G)).kind, "management-group")
    def test_to_dict_metadata(self):
        m = self.scanner().scan(self.ref(), "subscriptions").to_dict()["metadata"]
        self.assertEqual((m["tool"], m["version"], m["schema_version"], m["complete"], m["pim"], m["depth"]),
                         ("ARA", "3.0.0", 3, True, "included", "subscriptions"))
        self.assertRegex(m["scan_timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        for gone in ("total_scopes_scanned",): self.assertNotIn(gone, m)
    def test_subscription_scan_is_named_after_its_display_name(self):
        h = FakeHierarchy()
        h.subscription = lambda sid: ara.Scope("/subscriptions/" + sid, "Sandbox", "Subscription", subscription_id=sid)
        ref = ara.ScopeParser.parse("/subscriptions/" + G)
        r = ara.Scanner(h, FakeCollector({}), None, show_progress=False).scan(ref, "subscriptions")
        self.assertEqual((r.scope_name, r.to_dict()["metadata"]["scope"]), ("Sandbox", ref.path))
    def test_guid_management_group_is_named_after_its_display_name(self):
        ref = ara.ScopeParser.parse("managementGroups/" + G)
        root = ara.Scope(ref.path, "Tenant Root Group", "Management Group")
        r = ara.Scanner(FakeHierarchy([root]), FakeCollector({}), None, show_progress=False).scan(ref, "management-groups")
        self.assertEqual((r.scope_name, r.scope), ("Tenant Root Group", ref.path))
    def test_readable_management_group_id_is_kept(self):
        root = ara.Scope(MG, "My MG", "Management Group")
        r = ara.Scanner(FakeHierarchy([root]), FakeCollector({}), None, show_progress=False).scan(self.ref(), "management-groups")
        self.assertEqual(r.scope_name, "my-mg")

def ra(name="Ann", pid="p1", ptype="User", role="Reader", atype="Active permanent"):
    return ara.RoleAssignment(principal_id=pid, principal_name=name, principal_type=ptype, role_name=role,
                              role_id="r", scope=MG, scope_name="my-mg", scope_type="Management Group",
                              assignment_type=atype)

class FakeGraph:
    """Stands in for GraphAPIClient: returns the known objects among the requested ids, or raises error."""
    def __init__(self, objects, error=None):
        self.objects, self.error = objects, error
    def get_by_ids(self, ids):
        if self.error:
            raise self.error
        return {i: self.objects[i] for i in ids if i in self.objects}

def args(**kw):
    values = dict(role_filter=None, principal_type_filter=None, assignment_type_filter=None,
                  principal_name_filter=None, exclude_managed_identities=False)
    values.update(kw)
    return ara.argparse.Namespace(**values)

class PrincipalResolverTest(unittest.TestCase):
    def test_graph_fills_names_marks_unknown_and_managed_identities(self):
        rows = [ra(name=None, pid="a"), ra(name=None, pid="b"), ra(name="sp", pid="c", ptype="ServicePrincipal"), ra(name="Kept", pid="d")]
        graph = FakeGraph({"a": {"displayName": "Ann"}, "c": {"displayName": "id-aks", "servicePrincipalType": "ManagedIdentity"}})
        out, status = ara.PrincipalResolver(graph).resolve(rows)
        self.assertEqual([(a.principal_name, a.principal_type) for a in out],
                         [("Ann", "User"), ("Unknown", "User"), ("id-aks", "ManagedIdentity"), ("Kept", "User")])
        self.assertEqual(status, "resolved")
    def test_empty_display_name_keeps_none(self):
        out, _ = ara.PrincipalResolver(FakeGraph({"a": {"displayName": None}})).resolve([ra(name=None, pid="a")])
        self.assertIsNone(out[0].principal_name)
    def test_without_graph_status_reflects_pim_names(self):
        r = ara.PrincipalResolver(None)
        self.assertEqual(r.resolve([ra(), ra(pid="b")])[1], "resolved")
        self.assertEqual(r.resolve([ra(), ra(name=None, pid="b")])[1], "partial")
        self.assertEqual(r.resolve([ra(name=None)])[1], "unavailable")
    def test_graph_failure_never_writes_unknown(self):
        out, status = ara.PrincipalResolver(FakeGraph({}, error=ara.APIError("x", status=500))).resolve([ra(name=None)])
        self.assertEqual((out[0].principal_name, status), (None, "unavailable"))
    def test_graph_sets_user_principal_name(self):
        out, _ = ara.PrincipalResolver(FakeGraph({"a": {"displayName": "Ann", "userPrincipalName": "ann@contoso.com"}})).resolve([ra(pid="a")])
        self.assertEqual(out[0].principal_upn, "ann@contoso.com")
    def test_without_graph_user_principal_name_is_none(self):
        out, _ = ara.PrincipalResolver(None).resolve([ra()])
        self.assertIsNone(out[0].principal_upn)
    def test_user_principal_name_is_in_json_rows(self):
        self.assertIn("principal_upn", result([ra()]).to_dict()["role_assignments"][0])
    def test_get_by_ids_chunks_by_1000(self):
        opener = FakeOpener([{"value": []}, {"value": []}, {"value": []}])
        g = ara.GraphAPIClient("t", api_delay=0, opener=opener, sleep=lambda s: None)
        g.get_by_ids([str(i) for i in range(2001)])
        self.assertEqual([len(json.loads(r.data)["ids"]) for r in opener.requests], [1000, 1000, 1])

class AssignmentFilterTest(unittest.TestCase):
    def test_name_filter_skips_missing_names_without_crashing(self):
        f = ara.AssignmentFilter(args(principal_name_filter="^sp-"))
        self.assertEqual([a.principal_name for a in f.apply([ra(name=None), ra(name="sp-x"), ra(name="Ann")])], ["sp-x"])
    def test_exclude_managed_identities(self):
        f = ara.AssignmentFilter(args(exclude_managed_identities=True))
        self.assertEqual(len(f.apply([ra(ptype="ManagedIdentity"), ra(ptype="ServicePrincipal")])), 1)
        self.assertTrue(f.needs_graph())
    def test_filters_are_case_insensitive_and_combine(self):
        f = ara.AssignmentFilter(args(role_filter="owner,reader", assignment_type_filter="active permanent"))
        self.assertEqual(len(f.apply([ra(), ra(role="Contributor"), ra(atype="Eligible permanent")])), 1)
        self.assertFalse(f.needs_graph())
    def test_unknown_values_are_rejected(self):
        for kw in ({"assignment_type_filter": "eligible"}, {"principal_type_filter": "Robot"}):
            with self.assertRaises(ara.ValidationError): ara.AssignmentFilter(args(**kw))
    def test_active_is_a_valid_assignment_type(self):
        ara.AssignmentFilter(args(assignment_type_filter="Active"))

def result(assignments, **kw):
    return ara.ScanResult(MG, "my-mg", "management-groups", "2026-01-01T00:00:00Z", assignments, **kw)

class OutputHandlerTest(unittest.TestCase):
    def test_json_file_name_and_content(self):
        with tempfile.TemporaryDirectory() as d:
            path = ara.JSONOutputHandler(d).generate(result([ra()]))
            self.assertEqual(os.path.basename(path), "role_assignments_my-mg.json")
            data = json.load(open(path)); self.assertEqual(data["metadata"]["complete"], True)
            self.assertEqual(data["role_assignments"][0]["principal_name"], "Ann")
            self.assertEqual(os.listdir(d), ["role_assignments_my-mg.json"])
    def test_incomplete_scan_writes_partial_and_keeps_complete_file(self):
        with tempfile.TemporaryDirectory() as d:
            h = ara.JSONOutputHandler(d); good = h.generate(result([ra()]))
            partial = h.generate(result([], failed_scopes=[{"scope": "/x", "error": "boom"}]))
            self.assertEqual(os.path.basename(partial), "role_assignments_my-mg.partial.json")
            self.assertEqual(len(json.load(open(good))["role_assignments"]), 1)
    def test_scope_name_is_sanitised(self):
        r = result([]); r.scope_name = "../a b/c"
        self.assertEqual(ara.JSONOutputHandler("x").file_name(r), "role_assignments____a_b_c.json")
    def test_csv_columns_are_fixed_even_without_resource_rows(self):
        text = ara.CSVOutputHandler("x").render(result([ra()]))
        self.assertEqual(text.splitlines()[0], "Name,Principal ID,Type,Role,Scope,Scope Path,Scope Type,Assignment Type,Resource Type,Start Date,End Date")
    def test_csv_escapes_formulas_and_missing_names(self):
        rows = list(csv.reader(io.StringIO(ara.CSVOutputHandler("x").render(result([ra(name="=HYPERLINK(1)"), ra(name=None)])))))
        self.assertEqual((rows[1][0], rows[2][0]), ("'=HYPERLINK(1)", ""))
    def test_unwritable_directory_raises_export_error(self):
        with tempfile.NamedTemporaryFile() as f:
            with self.assertRaises(ara.ExportError): ara.JSONOutputHandler(os.path.join(f.name, "sub")).generate(result([]))
    def test_zero_rows_is_a_valid_complete_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(ara.JSONOutputHandler(d).generate(result([])).endswith("role_assignments_my-mg.json"))

class HTMLReportTest(unittest.TestCase):
    def html(self, rows, **kw): return ara.HTMLOutputHandler("x").render(result(rows, **kw))
    def data(self, html):
        m = re.search(r'<script type="application/json" id="ara-data">(.*?)</script>', html, re.S)
        return json.loads(m.group(1))
    def test_hostile_names_cannot_break_out(self):
        html = self.html([ra(name='</script><script>alert(1)</script>'), ra(name='<img src=x onerror=alert(1)>')])
        self.assertEqual(html.count("</script>"), 2)   # data block and the report script
        self.assertNotIn("<img src=x", html)
        self.assertEqual(self.data(html)["role_assignments"][0]["principal_name"], '</script><script>alert(1)</script>')
    def test_line_separators_are_escaped(self):
        html = self.html([ra(name="a\u2028b\u2029c")])
        self.assertNotIn("\u2028", html); self.assertNotIn("\u2029", html)
    def test_report_script_never_builds_html_from_data(self):
        for banned in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write"):
            self.assertNotIn(banned, ara.REPORT_TEMPLATE)
    def test_no_external_requests(self):
        self.assertIn("default-src 'none'", ara.REPORT_TEMPLATE)
        self.assertIsNone(re.search(r"https?://", ara.REPORT_TEMPLATE))
    def test_missing_name_and_report_block_are_embedded(self):
        d = self.data(self.html([ra(name=None)]))
        self.assertIsNone(d["role_assignments"][0]["principal_name"])
        self.assertEqual(d["report"]["high_privilege_roles"], ["Owner", "User Access Administrator", "Role Based Access Control Administrator"])
    def test_report_block_carries_stopped(self):
        for kw, stopped in (({"stopped": True}, True), ({"interrupted": True}, True), ({}, False)):
            self.assertEqual(self.data(self.html([ra()], **kw))["report"]["stopped"], stopped, kw)
    def test_incomplete_scan_gets_partial_file_name(self):
        r = result([], failed_scopes=[{"scope": "/x", "error": "e"}])
        self.assertEqual(ara.HTMLOutputHandler("x").file_name(r), "role_assignments_my-mg.partial.html")
    def test_application_writes_report_first(self):
        _, files = run(app(result([ra()])), "--scope", "my-mg")
        self.assertIn("role_assignments_my-mg.html", files)
    def test_html_handler_comes_first(self):
        handlers = app().handlers(ara.parse_arguments(["--scope", "my-mg", "--format", "csv"]))
        self.assertIsInstance(handlers[0], ara.HTMLOutputHandler)

class HTMLReportExtrasTest(unittest.TestCase):
    T = property(lambda self: ara.REPORT_TEMPLATE)
    def test_export_uses_the_csv_columns(self):
        for col in ara.CSVOutputHandler.COLUMNS: self.assertIn(col, self.T)
        self.assertIn("_filtered.csv", self.T)
        self.assertIn(json.dumps(list(ara.CSVOutputHandler.COLUMNS)), self.T)
    def test_theme_and_print_hooks_exist(self):
        for needle in ("prefers-color-scheme", "data-theme", "ara-theme", "@media print", "prefers-reduced-motion"):
            self.assertIn(needle, self.T)
    def test_state_is_kept_in_the_fragment(self):
        for needle in ("replaceState", "hashchange"): self.assertIn(needle, self.T)
    def test_still_no_html_injection_or_external_urls(self):
        for banned in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write"): self.assertNotIn(banned, self.T)
        self.assertIsNone(re.search(r"https?://", self.T))
    def test_no_hallmark_stamp(self):
        self.assertNotIn("hallmark", self.T.lower())
    def test_panel_labels_the_object_id(self):
        self.assertIn("Object ID", self.T)
    def test_heading_uses_the_scan_scope_name(self):
        r = result([]); r.scope_name = "Sandbox"
        self.assertEqual(HTMLReportTest().data(ara.HTMLOutputHandler("x").render(r))["report"]["scope_name"], "Sandbox")
        self.assertIn("report.scope_name", self.T)
    def test_twenty_thousand_rows_stay_under_fifteen_megabytes(self):
        html = ara.HTMLOutputHandler("x").render(result([ra(pid="p%d" % i) for i in range(20000)]))
        self.assertLess(len(html.encode("utf-8")), 15 * 1024 * 1024)

class ConsoleSummaryTest(unittest.TestCase):
    def test_states_pim_failed_scopes_and_paths(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ara.ConsoleSummary().print(result([ra(), ra(role="Owner"), ra(role="Owner")], pim_skipped=True,
                                              failed_scopes=[{"scope": "/subscriptions/x", "error": "boom"}]), ["/tmp/r.html"])
        out = buf.getvalue()
        self.assertLess(out.index("Owner"), out.index("Reader"))
        for needle in ("PIM", "/subscriptions/x", "/tmp/r.html"): self.assertIn(needle, out)
    def test_incomplete_scan_is_stated_and_report_path_is_last(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ara.ConsoleSummary().print(result([ra()], failed_scopes=[{"scope": "/x", "error": "e"}]), ["/tmp/r.html", "/tmp/r.json"])
        out = buf.getvalue()
        self.assertIn("Scan incomplete.", out); self.assertTrue(out.rstrip().endswith("/tmp/r.html"))

class FakeScanner:
    """Stands in for Scanner: resolve returns the ref unchanged; scan returns the result or raises root_error."""
    def __init__(self, scan_result, root_error):
        self.scan_result, self.root_error = scan_result, root_error
    def resolve(self, ref): return ref
    def scan(self, ref, depth, resource_types=None):
        if self.root_error:
            raise self.root_error
        return self.scan_result

def app(scan_result=None, root_error=None, graph=None):
    return ara.ARAApplication(scanner_factory=lambda a: FakeScanner(scan_result, root_error), graph_factory=lambda: graph)

def run(app, *argv):
    with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
        code = app.run(ara.parse_arguments(list(argv) + ["--token", "t", "--no-progress", "--output-dir", d]))
        return code, sorted(os.listdir(d))

class ApplicationTest(unittest.TestCase):
    def test_complete_scan_exits_0_and_writes_json(self):
        code, files = run(app(result([ra()])), "--scope", "my-mg")
        self.assertEqual(code, 0); self.assertIn("role_assignments_my-mg.json", files)
    def test_zero_assignments_is_success(self):
        code, files = run(app(result([])), "--scope", "my-mg")
        self.assertEqual(code, 0); self.assertIn("role_assignments_my-mg.json", files)
    def test_failed_scope_exits_3_with_partial_file(self):
        code, files = run(app(result([ra()], failed_scopes=[{"scope": "/x", "error": "e"}])), "--scope", "my-mg")
        self.assertEqual(code, 3); self.assertIn("role_assignments_my-mg.partial.json", files)
    def test_interrupt_exits_130_with_partial_file(self):
        code, files = run(app(result([ra()], interrupted=True)), "--scope", "my-mg")
        self.assertEqual(code, 130); self.assertTrue(any(".partial." in f for f in files))
    def test_root_failure_exits_1_and_writes_nothing(self):
        code, files = run(app(root_error=ara.APIError("not found", status=404)), "--scope", "my-mg")
        self.assertEqual((code, files), (1, []))
    def test_usage_errors_exit_2(self):
        for argv in (("--scope", "a/b/c"), ("--scope", "my-mg", "--assignment-type-filter", "eligible"),
                     ("--scope", "my-mg", "--resource-types", "Microsoft.Compute/virtualMachines"),
                     ("--scope", "subscriptions/" + G, "--depth", "management-groups")):
            self.assertEqual(run(app(result([])), *argv)[0], 2, argv)
    def test_name_filter_without_graph_exits_1_before_scanning(self):
        code, files = run(app(result([ra()]), graph=None), "--scope", "my-mg", "--principal-name-filter", "^a")
        self.assertEqual((code, files), (1, []))
    def test_graph_failure_with_named_rows_does_not_apply_graph_filters(self):
        class LookupFails(FakeGraph):
            def get_by_ids(self, ids):
                if ids == ["00000000-0000-0000-0000-000000000000"]: return {}
                raise ara.APIError("down", status=503)
        code, files = run(app(result([ra()]), graph=LookupFails({})), "--scope", "my-mg", "--exclude-managed-identities")
        self.assertEqual(code, 3); self.assertIn("role_assignments_my-mg.partial.json", files)
    def test_graph_precheck_failure_exits_1_and_writes_nothing(self):
        graph = FakeGraph({}, error=ara.APIError("token rejected", status=403))
        code, files = run(app(result([ra()]), graph=graph), "--scope", "my-mg", "--exclude-managed-identities")
        self.assertEqual((code, files), (1, []))
    def test_resource_types_are_recorded_as_filter(self):
        a = app(result([ra()]))
        run(a, "--scope", "my-mg", "--depth", "resources", "--resource-types", "Microsoft.Compute/virtualMachines")
        self.assertIn("resource_types", a.last_result.filters_applied)
    def test_csv_is_written_in_addition(self):
        _, files = run(app(result([ra()])), "--scope", "my-mg", "--format", "csv")
        self.assertIn("role_assignments_my-mg.csv", files); self.assertIn("role_assignments_my-mg.json", files)
    def test_filter_is_applied_and_counted(self):
        a = app(result([ra(), ra(role="Owner")])); code, _ = run(a, "--scope", "my-mg", "--role-filter", "Owner")
        self.assertEqual((len(a.last_result.assignments), a.last_result.assignments_before_filters), (1, 2))
    def test_unwritable_output_dir_exits_1(self):
        with tempfile.NamedTemporaryFile() as f:
            code = app(result([ra()])).run(ara.parse_arguments(["--scope", "my-mg", "--token", "t", "--no-progress", "--output-dir", os.path.join(f.name, "x")]))
        self.assertEqual(code, 1)
    def test_failed_write_removes_tmp_file(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(ara.os, "replace", side_effect=OSError("disk full")), self.assertRaises(ara.ExportError):
                ara.JSONOutputHandler(d).generate(result([]))
            self.assertEqual(os.listdir(d), [])

class ArgumentsTest(unittest.TestCase):
    def test_removed_flags_are_rejected(self):
        for flag in (["--max-resources", "5"], ["--exclude-system"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()): ara.parse_arguments(["--scope", "x"] + flag)
    def test_depth_default_is_none(self):
        self.assertIsNone(ara.parse_arguments(["--scope", "x"]).depth)

class AuthTest(unittest.TestCase):
    def test_no_prompt_without_terminal(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False), mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ara.AuthenticationError): ara.AzureAuthenticationManager().get_token()
    def test_keychain_provider_is_gone(self):
        self.assertFalse(hasattr(ara, "KeychainAuthProvider"))

class DocsTest(unittest.TestCase):
    ROOT = pathlib.Path(__file__).resolve().parent.parent
    def test_readme_matches_the_tool(self):
        text = (self.ROOT / "README.md").read_text()
        for flag in ("--output-dir", "--exclude-managed-identities", "--skip-pim", "--format csv"): self.assertIn(flag, text)
        for gone in ("--max-resources", "--exclude-system", "Keychain", "PERFORMANCE.md", "2.0.0", "2.1.0", "LRU", "code%20style-black"):
            self.assertNotIn(gone, text)
        self.assertIn("3.0.0", text)
        self.assertEqual(set(re.findall(r"([\w-]+)\.onmicrosoft\.com", text.lower())), {"contoso"})
        for phrase in ("assignments_before_filters", "no filters are applied", "the Graph lookup failed or left principals unnamed",
                       "assignments inherited from above it are not", "renewed when it expires"): self.assertIn(phrase, text)
    def test_changelog_has_3_0_0(self):
        self.assertIn("## [3.0.0]", (self.ROOT / "CHANGELOG.md").read_text())

if __name__ == "__main__":
    unittest.main()
