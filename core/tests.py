import json
import re
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Invoice

LNBITS_BASE = "https://lnbits.test/api/v1/payments"
LNBITS_KEY = "test-api-key-not-for-browser"
PAYMENT_HASH = "a" * 64
BOLT11 = "lnbc1500n1" + ("q" * 180)


def lnbits_response(status_code=200, payload=None):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {}
    return response


def created_payload(amount_sats=1500):
    return {
        "payment_hash": PAYMENT_HASH,
        "payment_request": BOLT11,
        "amount": amount_sats * 1000,
        "expiry": (timezone.now() + timedelta(minutes=15)).isoformat(),
    }


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    LNBITS_URL=LNBITS_BASE,
    LNBITS_API_KEY=LNBITS_KEY,
    LNBITS_TIMEOUT=3,
    POS_MIN_SATS=1,
    POS_MAX_SATS=1_000_000,
    POS_INVOICE_TTL_SECONDS=600,
)
class InvoiceApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.create_url = reverse("invoice_create")

    def create_invoice(self, amount_sats=1500, request_id=None):
        payload = {
            "amount_sats": amount_sats,
            "request_id": request_id or str(uuid.uuid4()),
        }
        with patch("core.services.lnbits.requests.post") as post:
            post.return_value = lnbits_response(201, created_payload(amount_sats))
            response = self.client.post(
                self.create_url,
                data=json.dumps(payload),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def status_url(self, invoice_id):
        return reverse("invoice_status", args=[invoice_id])

    # ------------------------------------------------------------------
    # Interface / remoção da loja
    # ------------------------------------------------------------------
    def test_home_is_pos_and_has_no_store_ui(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("browser-POS", content)
        self.assertIn("Teclado numérico", content)
        self.assertIn("Gerar fatura Lightning", content)
        self.assertNotIn("carrinho", content.lower())
        self.assertNotIn("produto", content.lower())
        self.assertNotIn("checkout", content.lower())
        self.assertNotIn(LNBITS_KEY, content)
        self.assertNotIn(LNBITS_BASE, content)

    def test_home_sends_security_headers(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_legacy_routes_are_gone(self):
        for path in (
            "/cart/checkout/",
            "/cart/status/",
            "/cart/add/",
            "/produto/exemplo/",
            "/categoria/exemplo/",
            "/admin/",
            "/accounts/login/",
            "/servico/interesse/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    # ------------------------------------------------------------------
    # Criação da invoice
    # ------------------------------------------------------------------
    @patch("core.services.lnbits.requests.post")
    def test_create_invoice_calls_lnbits_and_hides_hash(self, post):
        post.return_value = lnbits_response(201, created_payload(1500))
        request_id = str(uuid.uuid4())

        response = self.client.post(
            self.create_url,
            data=json.dumps({"amount_sats": 1500, "request_id": request_id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["amount_sats"], 1500)
        self.assertEqual(body["payment_request"], BOLT11)
        self.assertEqual(body["status"], "pending")
        self.assertFalse(body["paid"])
        self.assertNotIn("payment_hash", body)
        self.assertNotIn(PAYMENT_HASH, response.content.decode())
        self.assertNotIn(LNBITS_KEY, response.content.decode())

        invoice = Invoice.objects.get()
        self.assertEqual(str(invoice.id), body["invoice_id"])
        self.assertEqual(invoice.amount_sats, 1500)
        self.assertEqual(invoice.payment_hash, PAYMENT_HASH)
        self.assertEqual(invoice.request_id, uuid.UUID(request_id))
        self.assertEqual(invoice.status, Invoice.Status.PENDING)
        self.assertIsNotNone(invoice.expires_at)

        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["amount"], 1500)
        self.assertEqual(kwargs["json"]["unit"], "sat")
        self.assertIs(kwargs["json"]["out"], False)
        self.assertIn("browser-POS", kwargs["json"]["memo"])
        self.assertEqual(kwargs["headers"]["X-Api-Key"], LNBITS_KEY)

    @patch("core.services.lnbits.requests.post")
    def test_repeated_request_id_is_idempotent(self, post):
        post.return_value = lnbits_response(201, created_payload(1500))
        request_id = str(uuid.uuid4())
        payload = {"amount_sats": 1500, "request_id": request_id}

        first = self.client.post(
            self.create_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        second = self.client.post(
            self.create_url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["invoice_id"], second.json()["invoice_id"])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(Invoice.objects.count(), 1)

    @patch("core.services.lnbits.requests.post")
    def test_same_request_id_with_different_amount_conflicts(self, post):
        post.return_value = lnbits_response(201, created_payload(1500))
        request_id = str(uuid.uuid4())

        self.client.post(
            self.create_url,
            data=json.dumps({"amount_sats": 1500, "request_id": request_id}),
            content_type="application/json",
        )
        response = self.client.post(
            self.create_url,
            data=json.dumps({"amount_sats": 2000, "request_id": request_id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_invalid_amounts_are_rejected_before_lnbits(self):
        invalid_payloads = [
            {},
            {"amount_sats": 0},
            {"amount_sats": -1},
            {"amount_sats": 1.5},
            {"amount_sats": "100"},
            {"amount_sats": True},
            {"amount_sats": 1_000_001},
        ]

        with patch("core.services.lnbits.requests.post") as post:
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    response = self.client.post(
                        self.create_url,
                        data=json.dumps(payload),
                        content_type="application/json",
                    )
                    self.assertEqual(response.status_code, 400)
            post.assert_not_called()
        self.assertEqual(Invoice.objects.count(), 0)

    def test_non_json_body_is_rejected(self):
        with patch("core.services.lnbits.requests.post") as post:
            response = self.client.post(
                self.create_url,
                data="amount_sats=1500",
                content_type="application/x-www-form-urlencoded",
            )
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    def test_malformed_json_is_rejected(self):
        with patch("core.services.lnbits.requests.post") as post:
            response = self.client.post(
                self.create_url,
                data="{not-json",
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    def test_create_requires_post(self):
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 405)

    def test_create_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            self.create_url,
            data=json.dumps({"amount_sats": 1500, "request_id": str(uuid.uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    @patch("core.services.lnbits.requests.post")
    def test_csrf_token_from_page_works_with_httponly_cookie(self, post):
        post.return_value = lnbits_response(201, created_payload(1500))
        csrf_client = Client(enforce_csrf_checks=True)
        home = csrf_client.get(reverse("home"))
        match = re.search(
            r'<meta name="csrf-token" content="([^"]+)">',
            home.content.decode(),
        )
        self.assertIsNotNone(match)
        self.assertIn("csrftoken", home.cookies)

        response = csrf_client.post(
            self.create_url,
            data=json.dumps({"amount_sats": 1500, "request_id": str(uuid.uuid4())}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=match.group(1),
        )
        self.assertEqual(response.status_code, 201)

    @patch("core.services.lnbits.requests.post")
    def test_lnbits_rejection_returns_safe_error(self, post):
        post.return_value = lnbits_response(401, {"detail": "bad key"})
        response = self.client.post(
            self.create_url,
            data=json.dumps({"amount_sats": 1500, "request_id": str(uuid.uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(LNBITS_KEY, response.content.decode())
        self.assertEqual(Invoice.objects.count(), 0)

    @patch("core.services.lnbits.requests.post")
    def test_lnbits_timeout_returns_safe_error(self, post):
        post.side_effect = requests.Timeout("internal timeout detail")
        response = self.client.post(
            self.create_url,
            data=json.dumps({"amount_sats": 1500, "request_id": str(uuid.uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("internal timeout detail", response.content.decode())
        self.assertEqual(Invoice.objects.count(), 0)

    @patch("core.services.lnbits.requests.post")
    def test_invalid_upstream_invoice_is_rejected(self, post):
        post.return_value = lnbits_response(
            201,
            {"payment_hash": "not-a-hash", "payment_request": "https://evil.test"},
        )
        response = self.client.post(
            self.create_url,
            data=json.dumps({"amount_sats": 1500, "request_id": str(uuid.uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(Invoice.objects.count(), 0)

    # ------------------------------------------------------------------
    # Consulta de pagamento
    # ------------------------------------------------------------------
    def test_status_pending_then_paid_only_from_lnbits(self):
        invoice = self.create_invoice(1500)

        with patch("core.services.lnbits.requests.get") as get:
            get.return_value = lnbits_response(
                200, {"paid": False, "status": "pending"}
            )
            pending = self.client.get(self.status_url(invoice["invoice_id"]))
            self.assertEqual(pending.status_code, 200)
            self.assertFalse(pending.json()["paid"])
            self.assertEqual(pending.json()["status"], "pending")
            self.assertNotIn("payment_hash", pending.json())
            self.assertNotIn(PAYMENT_HASH, pending.content.decode())
            self.assertNotIn(LNBITS_KEY, pending.content.decode())

            get.return_value = lnbits_response(
                200, {"paid": True, "status": "success"}
            )
            paid = self.client.get(self.status_url(invoice["invoice_id"]))
            self.assertEqual(paid.status_code, 200)
            self.assertTrue(paid.json()["paid"])
            self.assertEqual(paid.json()["status"], "paid")

            stored = Invoice.objects.get(pk=invoice["invoice_id"])
            self.assertEqual(stored.status, Invoice.Status.PAID)
            self.assertIsNotNone(stored.paid_at)

            # Pago é estado terminal: não consulta o LNbits novamente.
            calls_before = get.call_count
            again = self.client.get(self.status_url(invoice["invoice_id"]))
            self.assertTrue(again.json()["paid"])
            self.assertEqual(get.call_count, calls_before)

        _, kwargs = get.call_args_list[0]
        self.assertEqual(
            get.call_args_list[0].args[0],
            f"{LNBITS_BASE}/{PAYMENT_HASH}",
        )
        self.assertEqual(kwargs["headers"]["X-Api-Key"], LNBITS_KEY)

    @patch("core.services.lnbits.requests.get")
    def test_status_is_bound_to_session(self, get):
        invoice = self.create_invoice(1500)
        other_client = Client()

        response = other_client.get(self.status_url(invoice["invoice_id"]))

        self.assertEqual(response.status_code, 404)
        get.assert_not_called()

    def test_status_rejects_unknown_or_malformed_ids(self):
        self.assertEqual(
            self.client.get(f"/api/invoices/{uuid.uuid4()}/status/").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/invoices/{PAYMENT_HASH}/status/").status_code,
            404,
        )
        self.assertEqual(self.client.get("/api/invoices/not-a-uuid/status/").status_code, 404)

    @patch("core.services.lnbits.requests.get")
    def test_status_network_error_is_safe(self, get):
        invoice = self.create_invoice(1500)
        get.side_effect = requests.ConnectionError(
            f"failed to connect to {LNBITS_BASE} with {LNBITS_KEY}"
        )

        response = self.client.get(self.status_url(invoice["invoice_id"]))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn(LNBITS_KEY, response.content.decode())
        self.assertNotIn("failed to connect", response.content.decode())
        self.assertEqual(
            Invoice.objects.get(pk=invoice["invoice_id"]).status,
            Invoice.Status.PENDING,
        )

    @patch("core.services.lnbits.requests.get")
    def test_local_expiry_is_reported_without_false_payment(self, get):
        invoice = self.create_invoice(1500)
        stored = Invoice.objects.get(pk=invoice["invoice_id"])
        stored.expires_at = timezone.now() - timedelta(seconds=1)
        stored.save(update_fields=["expires_at"])

        get.return_value = lnbits_response(200, {"paid": False, "status": "pending"})
        response = self.client.get(self.status_url(invoice["invoice_id"]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["paid"])
        self.assertEqual(response.json()["status"], "expired")
        self.assertEqual(
            Invoice.objects.get(pk=invoice["invoice_id"]).status,
            Invoice.Status.EXPIRED,
        )

    @patch("core.services.lnbits.requests.get")
    def test_upstream_failed_status_marks_expired(self, get):
        invoice = self.create_invoice(1500)
        get.return_value = lnbits_response(200, {"paid": False, "status": "failed"})

        response = self.client.get(self.status_url(invoice["invoice_id"]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "expired")

    @patch("core.services.lnbits.requests.get")
    def test_lnbits_expiry_overrides_stale_local_deadline(self, get):
        invoice = self.create_invoice(1500)
        stored = Invoice.objects.get(pk=invoice["invoice_id"])
        stored.expires_at = timezone.now() - timedelta(seconds=1)
        stored.save(update_fields=["expires_at"])

        actual_expiry = timezone.now() + timedelta(minutes=30)
        get.return_value = lnbits_response(
            200,
            {
                "paid": False,
                "status": "pending",
                "details": {"expiry": actual_expiry.isoformat()},
            },
        )

        response = self.client.get(self.status_url(invoice["invoice_id"]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")
        stored.refresh_from_db()
        self.assertEqual(stored.status, Invoice.Status.PENDING)
        self.assertGreaterEqual(
            stored.expires_at.timestamp(),
            actual_expiry.timestamp() - 1,
        )

    def test_missing_lnbits_configuration_returns_safe_error(self):
        with override_settings(LNBITS_URL="", LNBITS_API_KEY=""):
            response = self.client.post(
                self.create_url,
                data=json.dumps(
                    {"amount_sats": 1500, "request_id": str(uuid.uuid4())}
                ),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("traceback", response.content.decode().lower())
