import importlib.machinery, importlib.util, io, json, pathlib, socket, sys, unittest, urllib.error
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

if __name__ == "__main__":
    unittest.main()
