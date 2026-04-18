"""
Vercel serverless entry point.
Scraping uses httpx + Claude text API (no Playwright — not available in serverless).
Local dev uses backend/main.py which has full Playwright-based scraping.
"""
import json
import os
import re
from dataclasses import asdict
from typing import Any, Dict, Optional

import anthropic
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Inline calculator (mirrors backend/calculator.py) ────────────────────────

from dataclasses import dataclass


@dataclass
class PropertyData:
    purchase_price: float
    monthly_rent: float
    hausgeld: float = 0.0
    maintenance: float = 0.0
    property_size_m2: float = 0.0
    down_payment_pct: float = 0.20
    interest_rate: float = 0.04
    loan_term_years: int = 30
    grunderwerbsteuer_pct: float = 0.035
    notar_pct: float = 0.015
    makler_pct: float = 0.0357


@dataclass
class PurchaseCosts:
    grunderwerbsteuer: float
    notar: float
    makler: float
    total: float


@dataclass
class AnalysisResult:
    gross_yield: float
    net_yield: float
    monthly_mortgage: float
    monthly_maintenance: float
    monthly_cashflow: float
    annual_cashflow: float
    is_positive_cashflow: bool
    purchase_costs: PurchaseCosts
    total_investment: float
    loan_amount: float
    down_payment: float
    roi_years: Optional[float]
    break_even_rent: float


def _calc_mortgage(principal: float, annual_rate: float, years: int) -> float:
    r = annual_rate / 12
    n = years * 12
    if r == 0:
        return principal / n
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def analyze_property(d: PropertyData) -> AnalysisResult:
    annual_rent = d.monthly_rent * 12
    monthly_maint = d.maintenance
    annual_maint = monthly_maint * 12
    annual_hg = d.hausgeld * 12

    gross_yield = (annual_rent / d.purchase_price) * 100
    net_yield = ((annual_rent - annual_hg - annual_maint) / d.purchase_price) * 100

    down = d.purchase_price * d.down_payment_pct
    loan = d.purchase_price - down
    mortgage = _calc_mortgage(loan, d.interest_rate, d.loan_term_years)
    cashflow = d.monthly_rent - mortgage - d.hausgeld - monthly_maint
    annual_cf = cashflow * 12

    grt = d.purchase_price * d.grunderwerbsteuer_pct
    notar = d.purchase_price * d.notar_pct
    makler = d.purchase_price * d.makler_pct
    total_costs = grt + notar + makler
    total_inv = down + total_costs

    return AnalysisResult(
        gross_yield=round(gross_yield, 2),
        net_yield=round(net_yield, 2),
        monthly_mortgage=round(mortgage, 2),
        monthly_maintenance=round(monthly_maint, 2),
        monthly_cashflow=round(cashflow, 2),
        annual_cashflow=round(annual_cf, 2),
        is_positive_cashflow=cashflow > 0,
        purchase_costs=PurchaseCosts(
            grunderwerbsteuer=round(grt, 2),
            notar=round(notar, 2),
            makler=round(makler, 2),
            total=round(total_costs, 2),
        ),
        total_investment=round(total_inv, 2),
        loan_amount=round(loan, 2),
        down_payment=round(down, 2),
        roi_years=round(total_inv / annual_cf, 1) if annual_cf > 0 else None,
        break_even_rent=round(mortgage + d.hausgeld + monthly_maint, 2),
    )


# ── Scraper (httpx + Claude text — no Playwright) ────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}

_EXTRACT_PROMPT = """
Extract property data from this German real estate listing page text.
Return ONLY valid JSON — no explanation, no markdown fences:
{
  "purchase_price":   <number or null>,
  "monthly_rent":     <number or null>,
  "hausgeld":         <number or null>,
  "property_size_m2": <number or null>,
  "title":            <string or null>,
  "address":          <string or null>
}
Notes:
- purchase_price = Kaufpreis (buying price in €)
- monthly_rent = Kaltmiete (cold rent per month in €)
- hausgeld = monthly service/HOA charge (Hausgeld/Wohngeld)
- property_size_m2 = living area in m² (Wohnfläche)
- German number format: "450.000" = 450000, "1.250,50" = 1250.50 — return plain floats.
- If this looks like a CAPTCHA, login wall, or error page, return all nulls.

Listing text:
"""


async def scrape_property(url: str) -> Dict[str, Any]:
    url = url.strip()
    supported = (
        "immoscout24" in url
        or "immobilienscout24" in url
        or "is24.de" in url
        or "immowelt" in url
    )
    if not supported:
        return {"error": "Unsupported site. Paste a link from immoscout24.de or immowelt.de."}

    try:
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20) as client:
            resp = await client.get(url)
        html = resp.text
    except Exception as e:
        return {"error": f"Could not fetch page: {e}"}

    # Detect hard blocks
    lower = html.lower()
    if resp.status_code in (401, 403) or "ich bin kein roboter" in lower:
        return {
            "error": (
                "ImmobilienScout24 blocked the request. "
                "Run the app locally — the local version uses a real browser to bypass this."
            )
        }

    # Try fast regex/BeautifulSoup extraction first
    result = _parse_html(html)

    # If key fields are missing, ask Claude to read the page text
    if not result.get("purchase_price"):
        result = _extract_via_claude_text(html)

    return result


def _parse_html(html: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    soup = BeautifulSoup(html, "html.parser")

    # JSON-LD
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, dict):
                if "price" in data and not result.get("purchase_price"):
                    v = _de_num(str(data["price"]))
                    if v and v > 10_000:
                        result["purchase_price"] = v
                if "name" in data:
                    result.setdefault("title", data["name"])
                if "address" in data:
                    a = data["address"]
                    result.setdefault(
                        "address",
                        f"{a.get('streetAddress','')} {a.get('postalCode','')} {a.get('addressLocality','')}".strip(),
                    )
        except Exception:
            pass

    # Inline JS state
    for tag in soup.find_all("script"):
        text = tag.string or ""
        for js_key, field in [
            ("kaufpreis", "purchase_price"),
            ("buyingPrice", "purchase_price"),
            ("purchasePrice", "purchase_price"),
            ("kaltmiete", "monthly_rent"),
            ("baseRent", "monthly_rent"),
            ("coldRent", "monthly_rent"),
            ("hausgeld", "hausgeld"),
            ("serviceCharge", "hausgeld"),
        ]:
            if field not in result:
                m = re.search(rf'"{js_key}"[:\s]*(\d[\d.,]*)', text, re.IGNORECASE)
                if m:
                    v = _de_num(m.group(1))
                    if v and v > 0:
                        result[field] = v

        if "property_size_m2" not in result:
            for js_key in ("wohnflaeche", "livingSpace", "livingArea", "areaTotal"):
                m = re.search(rf'"{js_key}"[:\s]*(\d[\d.,]*)', text, re.IGNORECASE)
                if m:
                    v = _de_num(m.group(1))
                    if v:
                        result["property_size_m2"] = v
                        break

    return result


def _extract_via_claude_text(html: str) -> Dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not set — cannot extract data from this page."}

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)[:8000]

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT + text}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
    except Exception as e:
        return {"error": f"Extraction failed: {e}"}

    result: Dict[str, Any] = {}
    for key in ("purchase_price", "monthly_rent", "hausgeld", "property_size_m2"):
        v = data.get(key)
        if v is not None:
            try:
                result[key] = float(v)
            except (TypeError, ValueError):
                pass
    for key in ("title", "address"):
        if data.get(key):
            result[key] = str(data[key])

    if not result:
        return {"error": "No property data found — the page may require a browser. Use the local version."}

    return result


def _de_num(text: str) -> Optional[float]:
    text = re.sub(r"[€$\s\u00a0]", "", str(text))
    if re.search(r"\d\.\d{3}", text):
        text = text.replace(".", "")
    text = text.replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", text)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    return None


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Renditerechner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScrapeRequest(BaseModel):
    url: str


class AnalyzeRequest(BaseModel):
    purchase_price: float
    monthly_rent: float
    hausgeld: float = 0.0
    maintenance: float = 0.0
    property_size_m2: float = 0.0
    down_payment_pct: float = 0.20
    interest_rate: float = 0.04
    loan_term_years: int = 30
    grunderwerbsteuer_pct: float = 0.035
    notar_pct: float = 0.015
    makler_pct: float = 0.0357


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/scrape")
async def scrape(req: ScrapeRequest):
    return await scrape_property(req.url)


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    data = PropertyData(**req.model_dump())
    result = analyze_property(data)
    d = asdict(result)
    d["purchase_costs"] = asdict(result.purchase_costs)
    return d
