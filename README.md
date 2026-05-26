# MercadoPago OpenAPI Specification

[![Spec](https://img.shields.io/badge/OpenAPI-3.1-blue)](spec3.yaml)
[![Endpoints](https://img.shields.io/badge/endpoints-132-green)]()
[![Sites](https://img.shields.io/badge/countries-7-orange)]()

Machine-readable, standards-compliant OpenAPI 3.1 specification for all MercadoPago API endpoints
across 7 countries in Latin America: Argentina, Brazil, Mexico, Chile, Colombia, Peru, and Uruguay.

---

## Files

| File | Description |
|---|---|
| `spec3.yaml` | Public spec — 132 endpoints, 31 tags, 65 schemas, fully self-contained |
| `spec3.json` | JSON twin of `spec3.yaml` (generated, never edited manually) |
| `spec3.sdk.yaml` | SDK variant — same as `spec3.yaml` plus `x-mp-sdk-coverage` per operation |
| `spec3.sdk.json` | JSON twin of `spec3.sdk.yaml` |
| `fixtures3.yaml` | Sample response objects per resource, used by mock servers |
| `fixtures3.json` | JSON twin of `fixtures3.yaml` |

`schemas/` contains human-editable source fragments merged into `spec3.yaml` by the build pipeline.
Never `$ref` them from `spec3.yaml` directly — the spec must remain self-contained.

---

## Coverage

### Endpoints (132 total)

| Section | Tag | Endpoints |
|---|---|---|
| Authentication | OAuth | 1 |
| Online Payments | Preferences, Payments, Orders, Merchant Orders | 22 |
| Customers & Cards | Customers, Cards, Addresses, Card Tokens | 19 |
| Payment Methods | Payment Methods, Identification Types | 3 |
| Cancellations & Refunds | Cancellations & Refunds, Chargebacks | 7 |
| Subscriptions | Subscriptions, Plans, Invoices | 11 |
| Wallet Connect | Wallet Connect | 4 |
| In-Person — Point | Stores, POS, Terminals, Point Orders, Point Deprecated | 20 |
| In-Person — QR | QR Orders, QR Integrator, QR Deprecated | 13 |
| Post-Sale | Claims, Claim Messages, Claim Resolutions, Claim Shipping | 14 |
| Reports | Releases Report, Settlements Report | 20 |
| Payouts | Payouts | 5 |

7 endpoints are marked `deprecated: true` with `x-mp-migration-guide` pointers to modern equivalents.

### SDK Coverage (`spec3.sdk.yaml`)

Source: official SDKs — `php`, `nodejs`, `java`, `python`, `ruby`, `dotnet`, `go`

| Coverage | Count |
|---|---|
| All 7 SDKs | 38 |
| Partial SDK support | 21 |
| Spec-only (no SDK yet) | 73 |

### Countries (`by-site/`)

Pre-merged per-site specs filtered to only the endpoints applicable to each country:

| Site | Country | Currency | Endpoints |
|---|---|---|---|
| MLA | Argentina | ARS | 130 |
| MLB | Brazil | BRL | 129 |
| MLM | Mexico | MXN | 129 |
| MLC | Chile | CLP | 113 |
| MCO | Colombia | COP | 110 |
| MPE | Peru | PEN | 110 |
| MLU | Uruguay | UYU | 110 |

---

## Quick Start

### Import into Postman
1. Open Postman → Import → Link
2. Paste the raw URL of `spec3.yaml`
3. Postman generates a full collection with all 132 endpoints and pre-filled examples

### Run a local mock server (Prism)
```bash
docker run --rm -p 4010:4010 \
  -v $(pwd):/tmp/spec \
  stoplight/prism mock /tmp/spec/spec3.yaml
```
Then call any endpoint against `http://localhost:4010` — Prism serves responses from `fixtures3.yaml`.

### Interactive docs (Redoc)
```bash
npx @redocly/cli preview-docs spec3.yaml
```

---

## Authentication

MercadoPago uses three authentication patterns:

| Pattern | OpenAPI scheme | Used for |
|---|---|---|
| **Bearer Access Token** | `bearerAuth` | All server-side endpoints |
| **Public Key** | `publicKey` | Client-side card tokenization via MercadoPago.js only |
| **OAuth 2.0** | `mercadopagoOAuth` | Marketplace on-behalf-of, Wallet Connect |

**Critical rules:**
- Tokens go in `Authorization: Bearer <token>` header **only** — never as `?access_token=` URL parameter
- `APP_USR-xxx` for production
- Public key is not an access token — using it server-side is a security misconfiguration

---

## Amounts

> ⚠️ MercadoPago uses **decimal amounts**, not integer cents.

| Currency | Decimals | Correct | Wrong |
|---|---|---|---|
| BRL, ARS, MXN, COP, PEN, UYU | 2 | `100.50` | `10050` |
| CLP | 0 | `10000` | `10000.00` |

---

## PCI DSS Scope

MercadoPago Secure Fields and MercadoPago.js capture raw card data (PAN, CVV) directly in MP's
infrastructure — your server only receives a single-use token. Integrations using this approach
are out of PCI DSS scope for card data storage.

Endpoints with `x-mp-pci-scope: true` handle card tokenization.

---

## Countries & site_id

| Country | site_id | Currency | Key local payment methods |
|---|---|---|---|
| Argentina | MLA | ARS | Rapipago, Pago Fácil, Amex, Naranja |
| Brazil | MLB | BRL | Pix, Boleto Bancário, Elo, Hipercard |
| Mexico | MLM | MXN | OXXO, SPEI, Citibanamex |
| Chile | MLC | CLP | Khipu, RedCompra |
| Colombia | MCO | COP | PSE, Efecty, Baloto |
| Peru | MPE | PEN | PagoEfectivo |
| Uruguay | MLU | UYU | OCA, Redpagos, Abitab |

The `x-mp-sites` extension on each endpoint and tag lists the applicable `site_id` codes.
Country-specific examples and local payment method details are in `overlays/<site_id>.yaml`.

---

## Vendor Extensions

| Extension | Where | Description |
|---|---|---|
| `x-mp-sdk-coverage` | endpoint (sdk spec only) | Which SDKs implement this endpoint |

---

## Repository Structure

```
openapi/
├── spec3.yaml          # self-contained public spec (132 endpoints, 31 tags, 65 schemas)
├── spec3.json          # JSON twin (generated)
├── spec3.sdk.yaml      # SDK variant with x-mp-sdk-coverage per operation
├── spec3.sdk.json      # JSON twin (generated)
├── fixtures3.yaml      # sample response objects (12 resources)
├── fixtures3.json      # JSON twin (generated)
├── schemas/            # human-editable source fragments (10 files)
├── overlays/           # OAL 1.0 country-specific additions (7 countries)
├── by-site/            # pre-merged per-site specs (MLA, MLB, MLM, MLC, MCO, MPE, MLU)
└── preview/            # pre-GA endpoints
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report missing or incorrect endpoints,
run validation locally, and submit a pull request.

---

MercadoPago Developer Experience · Apache 2.0
