"""Target-scoped configuration for the reusable equity research harness.

The first TSMC run exposed that identity, official filings, peer sets and
artifact names belong to a target profile, not to the shared financial-depth
kernel.  This module is the only place where the first three equity fixtures
declare those differences.
"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
from typing import Any

from finance_crawler_poc.source_registry import build_source_registry


_PROFILES: dict[str, dict[str, Any]] = {
    "tsmc": {
        "target_id": "tsmc",
        "target": {
            "kind": "equity",
            "symbol": "2330.TW",
            "name": "Taiwan Semiconductor Manufacturing Company Limited",
            "aliases": ["TSMC", "2330", "Taiwan Semiconductor", "台積電", "台灣積體電路製造"],
            "market": "TW",
            "sector": "Technology",
            "industry": "Semiconductors",
            "currency": "TWD",
            "peer_symbols": ["2303.TW", "GFS", "5347.TWO", "6770.TW"],
            "peer_selection_rule": "pure_play_foundry_peers_v4_currency_aligned",
        },
        "question": "台積電的標的研究",
        "benchmark": {"symbol": "^TWII", "name": "Taiwan Weighted Index", "provider": "yahoo_finance"},
        "official": {
            "kind": "sec_filing",
            "source_id": "sec_tsmc_20f",
            "cik": "0001046179",
            "accession": "0001628280-26-025362",
            "document": "tsm-20251231.htm",
            "filing_date": "2026-04-16",
            "fiscal_period_end": "2025-12-31",
            "form_label": "Form 20-F",
        },
        "forecast_guidance": {
            "revenue_growth": {
                "bear": [0.34, 0.16, 0.10],
                "base": [0.40, 0.22, 0.16],
                "bull": [0.44, 0.28, 0.22],
            },
            "operating_margin": {
                "bear": [0.52, 0.50, 0.49],
                "base": [0.57, 0.55, 0.54],
                "bull": [0.59, 0.58, 0.57],
            },
            "tax_rate": 0.175,
            "lineage": [
                {
                    "claim": "The SEC-filed 2Q26 release reports Q3 revenue guidance of US$44.6-45.8bn, gross margin 65-67%, and operating margin 56-58%.",
                    "url": "https://www.sec.gov/Archives/edgar/data/1046179/000104617926000451/a2q26e_withguidancexfinal.htm",
                    "publisher": "SEC EDGAR / TSMC 6-K Exhibit",
                },
                {
                    "claim": "The 2025 Form 20-F anchors the business model, historical investment, risk and capital structure used by the forecast.",
                    "url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm",
                    "publisher": "SEC EDGAR / TSMC Form 20-F",
                },
            ],
        },
        "research_context": {
            "catalysts": {
                "items": [
                    {"event": "2026 年第三季財報與第四季指引", "window": "2026 年第三季財報公告", "mechanism": "檢驗 44.6–45.8 億美元第三季營收指引、65–67% 毛利率與 56–58% 營業利益率是否兌現", "probability": "高；定期事件"},
                    {"event": "2 奈米量產爬坡", "window": "2026 年下半年至 2027 年", "mechanism": "先進節點出貨可推動收入與產品組合，但初期良率、折舊與海外廠成本可能壓縮毛利", "probability": "高；公司已揭露爬坡"},
                    {"event": "AI／HPC 需求與先進封裝擴產", "window": "未來 12 個月", "mechanism": "雲端客戶 AI GPU 與 ASIC 需求若延續，將支撐先進製程利用率、議價與現金流", "probability": "中高；需由客戶指引與月營收持續驗證"},
                ],
                "sources": [
                    {"publisher": "SEC EDGAR / TSMC 2Q26 6-K Exhibit", "group": "sec_edgar", "url": "https://www.sec.gov/Archives/edgar/data/1046179/000104617926000451/a2q26e_withguidancexfinal.htm"},
                    {"publisher": "SEC EDGAR / TSMC 2025 Form 20-F", "group": "sec_edgar", "url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm"},
                ],
            },
            "forecast": {
                "sources": [
                    {"publisher": "SEC EDGAR / TSMC 2Q26 6-K Exhibit", "group": "sec_edgar", "url": "https://www.sec.gov/Archives/edgar/data/1046179/000104617926000451/a2q26e_withguidancexfinal.htm"},
                    {"publisher": "SEC EDGAR / TSMC 2025 Form 20-F", "group": "sec_edgar", "url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm"},
                ]
            },
            "company": {
                "business_model": "台積電採純晶圓代工模式，不以自有品牌設計或銷售半導體產品，核心價值是替客戶提供製程、產能與先進封裝，避免與客戶競爭。",
                "scale": "2025 年生產 12,682 種產品、使用 305 種製程、服務 534 家客戶；年產能超過 1,700 萬片十二吋約當晶圓。",
                "platform_mix": {"HPC": "58%", "智慧型手機": "29%", "IoT": "5%", "車用": "5%", "DCE": "1%", "其他": "2%"},
                "geography_mix": {"北美": "75%", "亞太（不含中國與日本）": "9%", "中國": "9%", "日本": "4%", "歐洲／中東／非洲": "3%"},
                "moat": ["先進製程技術領先", "大規模製造與良率管理", "不與客戶競爭所建立的信任", "Open Innovation Platform 與供應鏈生態系"],
                "sources": [
                    {"publisher": "SEC EDGAR / TSMC 2025 Form 20-F", "group": "sec_edgar", "url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm"},
                    {"publisher": "TSMC Investor Relations", "group": "tsmc_ir", "url": "https://investor.tsmc.com/english", "required": False},
                ],
            },
            "industry": {
                "position": "Counterpoint 估計台積電 2026 年第一季純晶圓代工市占率為 73%；TrendForce 估計同季為 72%。兩者定義接近但仍須保留方法差異。",
                "cycle": "AI GPU、ASIC 與先進封裝需求推動先進節點；消費電子提前備貨支撐成熟製程，但智慧手機季節性與庫存循環仍可能造成季度波動。",
                "capacity": "WSTS Spring 2026 預測全球半導體市場 2026 年將成長至約 1.51 兆美元，主要增量來自 AI 相關的邏輯與記憶體；這支持先進製程需求，但也意味產能與設備投資將維持高檔。報告應以保存的 WSTS PDF 版本與頁碼重新核對此數字。",
                "sources": [
                    {"publisher": "Counterpoint Research", "group": "counterpoint", "url": "https://counterpointresearch.com/en/insights/global-semiconductor-foundry-market-share"},
                    {"publisher": "TrendForce", "group": "trendforce", "url": "https://www.trendforce.com/presscenter/news/20260612-13095.html"},
                    {"publisher": "WSTS", "group": "wsts", "url": "https://www.wsts.org/esraCMS/extension/esrapdf/generate/105", "locator": "WSTS Spring 2026 forecast, page 1"},
                ],
            },
            "governance": {
                "summary": "董事會共十席，透過審計暨風險、薪酬與人才發展、提名與公司治理暨永續等委員會監督。C.C. Wei 同時擔任董事長與執行長，提升決策一致性但也形成權力集中風險，需由獨立董事與委員會持續制衡。",
                "capital_allocation": "2025 年每股現金股利提高至新台幣 18 元；2025 年資本支出約 409 億美元。高資本強度支持先進製程與產能護城河，也提高折舊、海外廠稀釋與投資回收風險。",
                "ownership": "截至 2025 年 12 月 17 日，ADR 保管部位占普通股 20.49%，行政院國家發展基金占 6.38%，新加坡政府占 2.08%；持股結構分散，但政府與海外機構資金仍是重要持有人。",
                "sources": [
                    {"publisher": "SEC EDGAR / TSMC 2025 Form 20-F", "group": "sec_edgar", "url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm"},
                    {"publisher": "TSMC Board Committees", "group": "tsmc_governance", "url": "https://investor.tsmc.com/english/committees", "required": False},
                    {"publisher": "TSMC Board of Directors", "group": "tsmc_governance", "url": "https://investor.tsmc.com/english/board-of-directors", "required": False},
                    {"publisher": "TSMC Risk Management", "group": "tsmc_governance", "url": "https://investor.tsmc.com/english/risk-management", "required": False},
                ],
            },
            "esg": {
                "summary": "重大議題不是評分表，而是能源、水、碳成本與海外擴產能否影響產能、成本、客戶訂單與資本成本。公司目標 2040 年使用 100% 再生能源、2050 年淨零；其永續揭露也明列電力與供水中斷可能限制產能。",
                "sources": [
                    {"publisher": "SEC EDGAR / TSMC 2025 Form 20-F", "group": "sec_edgar", "url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm"},
                    {"publisher": "TSMC Sustainability", "group": "tsmc_esg", "url": "https://esg.tsmc.com/en-US", "required": False},
                    {"publisher": "TSMC Sustainability Reports", "group": "tsmc_esg", "url": "https://www.tsmc.com/english/aboutTSMC/dc_csr_report", "required": False},
                    {"publisher": "TSMC 2024 Sustainability Report", "group": "tsmc_esg", "url": "https://esg.tsmc.com/en-US/file/public/2024-TSMC-Sustainability-Report-e.pdf", "locator": "2024 Sustainability Report", "required": False},
                ],
            },
        },
    },
    "delta": {
        "target_id": "delta",
        "target": {
            "kind": "equity",
            "symbol": "2308.TW",
            "name": "Delta Electronics, Inc.",
            "aliases": ["Delta Electronics", "Delta Electronics, Inc.", "台達電", "台達電子", "2308"],
            "market": "TW",
            "sector": "Technology",
            "industry": "Electronic Components",
            "currency": "TWD",
            "peer_symbols": ["2301.TW", "2382.TW", "2395.TW", "1503.TW"],
            "peer_selection_rule": "curated_taiwan_electronics_peers_v1",
        },
        "question": "台達電的標的研究",
        "benchmark": {"symbol": "^TWII", "name": "Taiwan Weighted Index", "provider": "yahoo_finance"},
        "official": {
            "kind": "twse_financial_statement",
            "source_id": "twse_delta_financial_statement",
            "income_url": "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
            "balance_url": "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci",
        },
        "research_context": {
            "company": {
                "sources": [
                    {"publisher": "Delta Electronics official site", "group": "delta_official", "url": "https://www.deltaww.com/en-US"},
                    {"publisher": "Delta Electronics investor financials", "group": "delta_ir", "url": "https://www.deltaww.com/en-US/investors/financials"},
                    {"publisher": "Delta Electronics 2025 Annual Report", "group": "delta_ir", "url": "https://filecenter.deltaww.com/IR/download/annual_report/2025annual.pdf", "locator": "2025 Annual Report"},
                ]
            },
            "industry": {
                "sources": [
                    {"publisher": "Delta Electronics data-center solutions", "group": "delta_solutions", "url": "https://www.deltaww.com/en-US/solutions/data-centers"},
                    {"publisher": "Delta Electronics 2025 Annual Report", "group": "delta_ir", "url": "https://filecenter.deltaww.com/IR/download/annual_report/2025annual.pdf", "locator": "Business categories and market applications"},
                ]
            },
            "governance": {
                "sources": [
                    {"publisher": "Delta Electronics corporate governance", "group": "delta_governance", "url": "https://www.deltaww.com/en-US/investors/corporate-governance"},
                    {"publisher": "Delta Board introduction and operation", "group": "delta_governance", "url": "https://filecenter.deltaww.com/ir/download/govern/2025_eng_Introduction%20and%20Operation%20of%20Delta%20Board%20of%20Directors.pdf", "locator": "Board composition and operation"},
                    {"publisher": "Delta Audit and Risk Committee operation", "group": "delta_governance", "url": "https://filecenter.deltaww.com/ir/download/govern/2025_eng_Introduction%20and%20Operation%20of%20the%20Audit%20and%20Risk%20Committee.pdf", "locator": "Audit and risk oversight"},
                    {"publisher": "Delta Compensation Committee operation", "group": "delta_governance", "url": "https://filecenter.deltaww.com/ir/download/govern/2025_eng_Introduction%20and%20Operation%20of%20the%20Compensation%20Committee.pdf", "locator": "Compensation oversight"},
                ]
            },
            "esg": {
                "sources": [
                    {"publisher": "Delta Electronics sustainability", "group": "delta_esg", "url": "https://www.deltaww.com/en-US/sustainability"},
                    {"publisher": "Delta Electronics 2024 CSRD Sustainability Report", "group": "delta_esg", "url": "https://filecenter.deltaww.com/about/download/esg/2024%20Delta%20Electronics%20CSRD%20Sustainability%20Report.pdf", "locator": "CSRD Sustainability Report"},
                    {"publisher": "Delta sustainability roadmap", "group": "delta_esg", "url": "https://filecenter.deltaww.com/Products/download/08/Products-202507231755115390.pdf", "locator": "Sustainability commitment and targets"},
                ]
            },
        },
    },
    "tatung": {
        "target_id": "tatung",
        "target": {
            "kind": "equity",
            "symbol": "2371.TW",
            "name": "Tatung Company",
            "aliases": ["Tatung Company", "Tatung", "大同公司", "大同集團", "2371"],
            "ambiguous_aliases": ["Tatung"],
            "identity_context_terms": ["company", "electric", "energy", "power", "taiwan", "land", "system", "industrial", "集團", "公司", "電", "能源", "電力"],
            "identity_exclude_terms": [" fc", "taipower", "chinaware", "institute of technology", "coatings", "chef tatung", "tatung baby", "rebranding tatung"],
            "market": "TW",
            "sector": "Industrials",
            "industry": "Diversified Electrical Equipment",
            "currency": "TWD",
            "peer_symbols": ["2301.TW", "2308.TW", "1503.TW", "1605.TW"],
            "peer_selection_rule": "curated_taiwan_diversified_electrical_peers_v1",
        },
        "question": "大同集團的標的研究",
        "benchmark": {"symbol": "^TWII", "name": "Taiwan Weighted Index", "provider": "yahoo_finance"},
        "official": {
            "kind": "twse_financial_statement",
            "source_id": "twse_tatung_financial_statement",
            "income_url": "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
            "balance_url": "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci",
        },
        "research_context": {
            "company": {
                "sources": [
                    {"publisher": "Tatung official site", "group": "tatung_official", "url": "https://www.tatung.com/en/"},
                    {"publisher": "Tatung annual reports", "group": "tatung_ir", "url": "https://www.tatung.com/en/info/annual-report"},
                    {"publisher": "Tatung 2025 Annual Report", "group": "tatung_ir", "url": "https://www.tatung.com/content-en/download/investors/2025annualreport.pdf", "locator": "2025 Annual Report"},
                ]
            },
            "industry": {
                "sources": [
                    {"publisher": "Tatung annual reports", "group": "tatung_ir", "url": "https://www.tatung.com/en/info/annual-report"},
                    {"publisher": "Tatung 2025 Annual Report", "group": "tatung_ir", "url": "https://www.tatung.com/content-en/download/investors/2025annualreport.pdf", "locator": "Business segments and market applications"},
                ]
            },
            "governance": {
                "sources": [
                    {"publisher": "Tatung governance and responsible officer", "group": "tatung_governance", "url": "https://www.tatung.com/en/info/csr-gov-officer"},
                    {"publisher": "Tatung 2025 Board performance evaluation", "group": "tatung_governance", "url": "https://www.tatung.com/Content-EN/download/investors/2025%20Performance%20evaluation%20report.pdf", "locator": "Board and functional committee evaluation"},
                    {"publisher": "Tatung 2025 External Board Performance Evaluation", "group": "tatung_governance", "url": "https://www.tatung.com/content-en/download/investors/2025%20External%20Performance%20Evaluation.pdf", "locator": "Independent external board evaluation"},
                ]
            },
            "esg": {
                "sources": [
                    {"publisher": "Tatung climate information", "group": "tatung_esg_climate", "url": "https://www.tatung.com/en/info/csr-climate"},
                    {"publisher": "Tatung supply-chain responsibility", "group": "tatung_esg_supply_chain", "url": "https://www.tatung.com/en/info/csr-chain"},
                    {"publisher": "Tatung 2024 Sustainability Report", "group": "tatung_esg", "url": "https://tatung.com/Content-EN/download/csr/Tatung-CSR-2024.pdf", "locator": "2024 Sustainability Report"},
                    {"publisher": "Tatung 2025 Sustainable Development Implementation Status", "group": "tatung_esg", "url": "https://www.tatung.com/Content-EN/download/csr/CSR-%20Sustainable%20development%20Implementation%20Status-2025.pdf?lang=zhRead&preview=true", "locator": "2025 implementation status"},
                ]
            },
        },
    },
}


def _taiwan_equity_profile(
    *,
    target_id: str,
    symbol: str,
    legal_name: str,
    local_name: str,
    aliases: list[str],
    sector: str,
    industry: str,
    peer_symbols: list[str],
    official_root: str,
    investor_url: str,
    governance_url: str | None = None,
    esg_url: str | None = None,
    annual_report_url: str | None = None,
    brand_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Build a target profile for a Taiwan-listed issuer.

    The shared pipeline needs target-scoped identity, peers and source routes,
    but it must not copy company-specific facts from TSMC/Delta/Tatung.  The
    initial profile therefore keeps qualitative prose deliberately conservative
    and lets frozen official pages, TWSE APIs and the qualitative evidence gate
    supply the issuer-specific claims.
    """

    income_url = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"
    balance_url = "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci"
    profile_url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    official_sources = [
        {"publisher": f"{legal_name} official website", "group": f"{target_id}_official", "url": official_root, "required": False},
        {"publisher": f"{legal_name} investor relations", "group": f"{target_id}_ir", "url": investor_url, "required": False},
        {"publisher": "TWSE company profile API", "group": "twse_profile", "url": profile_url},
        {"publisher": "TWSE listed-company financial API", "group": "twse_financial", "url": income_url},
    ]
    if annual_report_url:
        official_sources.append({"publisher": f"{legal_name} annual report", "group": f"{target_id}_annual_report", "url": annual_report_url, "required": False, "locator": "latest available annual report", "discovery": "annual_report_pdf"})
    if governance_url:
        official_sources.append({"publisher": f"{legal_name} corporate governance", "group": f"{target_id}_governance", "url": governance_url, "required": False})
    if esg_url:
        official_sources.append({"publisher": f"{legal_name} sustainability / ESG", "group": f"{target_id}_esg", "url": esg_url, "required": False})

    def section_sources(*extra_groups: str) -> list[dict[str, Any]]:
        selected = [dict(item) for item in official_sources if str(item.get("group")) in {"twse_profile", "twse_financial", *extra_groups}]
        return selected

    family = {
        "Cement & Building Materials": "cement",
        "Steel": "steel",
        "Petrochemicals": "petrochemical",
        "Diversified Chemicals": "petrochemical",
        "Electronic Components": "electronic_components",
        "Computer Hardware": "pc_hardware",
        "Electronics Manufacturing Services": "pc_hardware",
    }.get(industry, "generic_equity")
    industry_routes: dict[str, list[dict[str, str]]] = {
        "cement": [
            {"publisher": "USGS Mineral Commodity Summaries", "group": "usgs_cement_statistics", "url": "https://pubs.usgs.gov/periodicals/mcs2025/mcs2025-cement.pdf", "role": "industry_statistic", "requirement_ids": ["industry.market_demand", "industry.price_capacity_cycle"], "geography_scope": ["US"]},
            {"publisher": "USGS National Minerals Information Center", "group": "usgs_cement_information", "url": "https://www.usgs.gov/centers/national-minerals-information-center/cement-statistics-and-information", "role": "independent_secondary", "requirement_ids": ["industry.market_demand"], "geography_scope": ["US"]},
        ],
        "steel": [
            {"publisher": "USGS Mineral Commodity Summaries", "group": "usgs_steel_statistics", "url": "https://pubs.usgs.gov/periodicals/mcs2025/mcs2025-iron-steel.pdf", "role": "industry_statistic", "requirement_ids": ["industry.market_demand", "industry.price_capacity_cycle"], "geography_scope": ["US"]},
            {"publisher": "World Steel Association", "group": "worldsteel_statistics", "url": "https://worldsteel.org/steel-topics/statistics/world-steel-in-figures-2025/", "role": "independent_secondary", "requirement_ids": ["industry.market_demand", "industry.price_capacity_cycle", "industry.competitive_position"], "geography_scope": ["global", "Asia"]},
        ],
        "petrochemical": [
            {"publisher": "U.S. Energy Information Administration", "group": "eia_petroleum_prices", "url": "https://www.eia.gov/dnav/pet/pet_pri_spt_s1_d.htm", "role": "price_index", "requirement_ids": ["industry.price_capacity_cycle"], "geography_scope": ["US"]},
            {"publisher": "U.S. Energy Information Administration", "group": "eia_steo_outlook", "url": "https://www.eia.gov/outlooks/steo/", "role": "industry_statistic", "requirement_ids": ["industry.market_demand"], "geography_scope": ["US", "global"]},
        ],
        "electronic_components": [
            {"publisher": "World Semiconductor Trade Statistics", "group": "wsts_statistics", "url": "https://www.wsts.org/", "role": "industry_statistic", "requirement_ids": ["industry.market_demand", "industry.price_capacity_cycle"], "geography_scope": ["global", "Asia"]},
            {"publisher": "OECD ICT statistics", "group": "oecd_ict_statistics", "url": "https://data.oecd.org/ict/ict-investment.htm", "role": "independent_secondary", "requirement_ids": ["industry.market_demand"], "geography_scope": ["global", "Asia"]},
        ],
        "pc_hardware": [
            {"publisher": "World Semiconductor Trade Statistics", "group": "wsts_statistics", "url": "https://www.wsts.org/", "role": "industry_statistic", "requirement_ids": ["industry.price_capacity_cycle"], "geography_scope": ["global", "Asia"]},
            {"publisher": "OECD ICT statistics", "group": "oecd_ict_statistics", "url": "https://data.oecd.org/ict/ict-investment.htm", "role": "independent_secondary", "requirement_ids": ["industry.market_demand"], "geography_scope": ["global", "Asia"]},
        ],
    }
    external_industry_sources = industry_routes.get(family, [])

    def tagged_sources(values: list[dict[str, Any]], *, requirement_ids: list[str], role_by_group: Mapping[str, str]) -> list[dict[str, Any]]:
        return [
            {
                **dict(source),
                "requirement_ids": list(requirement_ids),
                "evidence_role": role_by_group.get(str(source.get("group")), "company_disclosure"),
                "independence_group": str(source.get("group") or "unknown"),
            }
            for source in values
        ]

    company_sources = tagged_sources(
        section_sources(f"{target_id}_official", f"{target_id}_ir", f"{target_id}_annual_report"),
        requirement_ids=["company.business_model", "segment.disclosure"],
        role_by_group={f"{target_id}_annual_report": "annual_report", f"{target_id}_official": "official", f"{target_id}_ir": "official", "twse_profile": "official", "twse_financial": "financial_statement"},
    )
    for source in company_sources:
        group = str(source.get("group") or "")
        source["geography_scope"] = ["TW"]
        source["requirement_ids"] = ["company.business_model"]
        if group.endswith("_annual_report") or group == "twse_financial":
            source["requirement_ids"].append("segment.disclosure")
    industry_sources = tagged_sources(
        section_sources(f"{target_id}_official", f"{target_id}_ir", f"{target_id}_annual_report"),
        requirement_ids=["industry.market_demand", "industry.price_capacity_cycle", "industry.competitive_position"],
        role_by_group={f"{target_id}_annual_report": "company_disclosure", f"{target_id}_official": "company_disclosure", f"{target_id}_ir": "company_disclosure", "twse_profile": "company_disclosure", "twse_financial": "company_disclosure"},
    )
    for source in industry_sources:
        group = str(source.get("group") or "")
        source["geography_scope"] = ["TW"]
        if group.endswith(("_annual_report", "_ir")):
            source["requirement_ids"] = ["industry.market_demand", "industry.competitive_position"]
        elif group.endswith("_official"):
            source["requirement_ids"] = ["industry.competitive_position"]
        else:
            source["requirement_ids"] = []
    industry_sources.extend({
        **dict(source),
        "required": False,
        "requirement_ids": list(source.get("requirement_ids") or []),
        "evidence_role": str(source.get("role") or "industry_statistic"),
        "independence_group": str(source.get("group") or "industry_external"),
        "geography_scope": list(source.get("geography_scope") or []),
    } for source in external_industry_sources)
    # One verified WSTS route can support two distinct observable dimensions
    # (market demand and utilization/capacity).  Keep the same independence
    # group so this is not counted as another source, but expose both roles to
    # the requirement matcher.
    if family in {"electronic_components", "pc_hardware"}:
        wsts = next((source for source in external_industry_sources if source.get("group") == "wsts_statistics"), None)
        if wsts is not None:
            industry_sources.append({
                **dict(wsts),
                "required": False,
                "requirement_ids": ["industry.price_capacity_cycle"],
                "evidence_role": "capacity_or_utilization",
                "independence_group": "wsts_statistics",
            })
    governance_sources = tagged_sources(
        section_sources(f"{target_id}_ir", f"{target_id}_official", f"{target_id}_annual_report", f"{target_id}_governance"),
        requirement_ids=["governance.board_and_ownership", "governance.capital_allocation"],
        role_by_group={f"{target_id}_annual_report": "annual_report", f"{target_id}_governance": "governance_filing", f"{target_id}_official": "regulatory_ownership", f"{target_id}_ir": "annual_report", "twse_profile": "regulatory_ownership", "twse_financial": "financial_statement"},
    )
    esg_sources = tagged_sources(
        section_sources(f"{target_id}_esg", f"{target_id}_official", f"{target_id}_annual_report", f"{target_id}_governance"),
        requirement_ids=["esg.materiality_kpi"],
        role_by_group={f"{target_id}_esg": "sustainability_report", f"{target_id}_annual_report": "sustainability_report", f"{target_id}_governance": "regulatory_or_governance", f"{target_id}_official": "regulatory_or_governance", f"{target_id}_ir": "regulatory_or_governance", "twse_profile": "regulatory_or_governance", "twse_financial": "regulatory_or_governance"},
    )
    peer_sources: list[dict[str, Any]] = []
    for peer_symbol in peer_symbols[:4]:
        peer_code = str(peer_symbol).split(".", 1)[0].casefold()
        peer_sources.extend([
            {"publisher": "Yahoo Finance fundamentals time series", "group": "peer_financials", "independence_group": "peer_financials", "url": f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{peer_symbol}?symbol={peer_symbol}&type=annualDilutedEPS,annualTotalRevenue,annualTotalDebt,annualCashAndCashEquivalents", "required": False, "requirement_ids": ["peer.comparison"], "evidence_role": "peer_filing", "peer_symbol": peer_symbol},
            {"publisher": "Yahoo Finance market history", "group": "peer_market_data", "independence_group": "peer_market_data", "url": f"https://query1.finance.yahoo.com/v8/finance/chart/{peer_symbol}?range=1y&interval=1d&events=history", "required": False, "requirement_ids": ["peer.comparison"], "evidence_role": "market_data", "peer_symbol": peer_symbol},
        ])

    return {
        "target_id": target_id,
        "target": {
            "kind": "equity",
            "symbol": symbol,
            "name": legal_name,
            "aliases": list(dict.fromkeys([local_name, *aliases, symbol.split(".", 1)[0]])),
            "market": "TW",
            "domicile_country": "TW",
            "primary_market": "TWSE",
            "primary_region": "TW",
            "languages": ["zh-TW", "en"],
            "region_priority": ["TW", "Asia", "global"],
            "local_names": [local_name],
            "international_names": list(dict.fromkeys([legal_name, *[alias for alias in aliases if alias.isascii()]])),
            "brand_terms": list(dict.fromkeys(brand_terms or [])),
            "sector": sector,
            "industry": industry,
            "currency": "TWD",
            "peer_symbols": peer_symbols,
            "peer_selection_rule": "curated_taiwan_equity_peers_v1",
        },
        "question": f"{local_name}（{symbol}）的標的研究",
        "benchmark": {"symbol": "^TWII", "name": "Taiwan Weighted Index", "provider": "yahoo_finance"},
        "official": {
            "kind": "twse_financial_statement",
            "source_id": f"twse_{target_id}_financial_statement",
            "income_url": income_url,
            "balance_url": balance_url,
        },
        "research_context": {
            "company": {
                "business_model": f"{legal_name} 的產品、客戶、分部與地理布局，僅採保存的公司官方頁面、年報及 TWSE 原始資料核對；本次不以未保存的市場傳聞補值。",
                "scale": "營收規模、產能、員工與區域占比以最新年報／官方揭露為準；若來源未揭露，報告會列為待補證據。",
                "moat": ["產品與客戶關係", "製程／規模／成本能力", "供應鏈與營運執行"],
                "sources": company_sources,
            },
            "industry": {
                "position": f"{legal_name} 所屬的 {industry} 競爭位置，需以公司年報、TWSE 原始資料與產業直接來源交叉驗證；本輪不預設市占或領先結論。",
                "cycle": "需求循環、價格／產品組合、庫存與資本支出是本標的的主要產業變數，待保存來源逐項確認。",
                "capacity": "產能、利用率、供需與政策／能源成本只在有可追溯來源時納入；沒有來源的數字不得進入估值假設。",
                "sources": industry_sources,
            },
            "governance": {
                "summary": "董事會組成、獨立性、功能委員會與重大治理風險以公司治理頁、年報與 TWSE 原始資料核對；未保存的資訊標為 unresolved。",
                "capital_allocation": "股利、資本支出、併購、負債與現金配置只採最新官方揭露，並與財務歷史期間對齊。",
                "ownership": "主要股東、政府／法人持股與流通股結構待官方股權資料補齊；不能以新聞敘述代替持股證據。",
                "sources": governance_sources,
            },
            "esg": {
                "summary": "ESG 僅納入會影響現金流、資本成本、營運許可、供應鏈或護城河的議題；能源、碳、水、勞動與治理指標需由保存的官方揭露確認。",
                "sources": esg_sources,
            },
            "peer": {
                "sources": peer_sources,
            },
        },
    }


# The following profiles are intentionally data-driven and share one adapter;
# they are the full-scale stability cohort for the first investment-research
# harness.  「台朔」 is retained as a search alias for the user's input typo and
# maps to the listed issuer 台塑 (1301.TW).
_PROFILES.update({
    "tcc": _taiwan_equity_profile(
        target_id="tcc", symbol="1101.TW", legal_name="Taiwan Cement Corporation", local_name="台泥", aliases=["台泥", "TCC", "Taiwan Cement"], sector="Materials", industry="Cement & Building Materials", peer_symbols=["1102.TW", "1103.TW", "5522.TW", "2504.TW"], official_root="https://www.tccgroupholdings.com/", investor_url="https://www.tccgroupholdings.com/investors", governance_url="https://www.tccgroupholdings.com/en/sustainability", esg_url="https://www.tccgroupholdings.com/en/sustainability", annual_report_url="https://www.tccgroupholdings.com/investors",
    ),
    "csc": _taiwan_equity_profile(
        target_id="csc", symbol="2002.TW", legal_name="China Steel Corporation", local_name="中鋼", aliases=["中鋼", "CSC", "China Steel"], sector="Materials", industry="Steel", peer_symbols=["2014.TW", "2027.TW", "2031.TW", "2009.TW"], official_root="https://www.csc.com.tw/csc/", investor_url="https://www.csc.com.tw/csc/ss/bd/bd_index.html", governance_url="https://www.csc.com.tw/csc/esg/index.html", esg_url="https://www.csc.com.tw/csc/esg/index.html", annual_report_url="https://www.csc.com.tw/csc/ss/bd/bd_index.html",
    ),
    "formosa": _taiwan_equity_profile(
        target_id="formosa", symbol="1301.TW", legal_name="Formosa Plastics Corporation", local_name="台塑", aliases=["台塑", "台朔", "FPC", "Formosa Plastics"], sector="Materials", industry="Petrochemicals", peer_symbols=["1303.TW", "1326.TW", "6505.TW", "1310.TW"], official_root="https://www.fpc.com.tw/fpcw/", investor_url="https://www.fpc.com.tw/fpcw/index.php?c=55&id=13&op=res", governance_url="https://www.fpc.com.tw/fpcw/index.php?c=55&id=13&op=res", esg_url="https://www.fpc.com.tw/fpcwuploads/files/2024_ESG_TW.pdf", annual_report_url="https://www.fpc.com.tw/fpcwuploads/files/2024_annual%20report.pdf",
    ),
    "nanya": _taiwan_equity_profile(
        target_id="nanya", symbol="1303.TW", legal_name="Nan Ya Plastics Corporation", local_name="南亞", aliases=["南亞", "南亞塑膠", "Nan Ya Plastics"], sector="Materials", industry="Diversified Chemicals", peer_symbols=["1301.TW", "1326.TW", "6505.TW", "1310.TW"], official_root="https://www.npc.com.tw/", investor_url="https://www.npc.com.tw/j2npc/zhtw/investor/Annual%20Reports", governance_url="https://www.npc.com.tw/j2npc/zhtw/co_governance.jsp", esg_url="https://www.npc.com.tw/npcfile/public/download/csr/2024_Sustainability_tw.pdf", annual_report_url="https://www.npc.com.tw/j2npc/zhtw/investor/Annual%20Reports",
    ),
    "yageo": _taiwan_equity_profile(
        target_id="yageo", symbol="2327.TW", legal_name="YAGEO Corporation", local_name="國巨", aliases=["國巨", "YAGEO", "YAGEO Corporation"], sector="Technology", industry="Electronic Components", peer_symbols=["2308.TW", "2317.TW", "2382.TW", "3037.TW"], official_root="https://yageogroup.com/", investor_url="https://www.vitrohm.com/About/InvestorRelations", governance_url="https://yageogroup.com/content/Resource%20Library/Financial/yageo_2025-annual-report_25052810_107.pdf", esg_url="https://yageogroup.com/content/Resource%20Library/Compliance%20Report/YAGEO_ESG2024_en.pdf", annual_report_url="https://yageogroup.com/content/Resource%20Library/Financial/yageo_2025-annual-report_25052810_107.pdf",
    ),
    "asus": _taiwan_equity_profile(
        target_id="asus", symbol="2357.TW", legal_name="ASUSTeK Computer Inc.", local_name="華碩", aliases=["華碩", "ASUS", "ASUSTeK"], sector="Technology", industry="Computer Hardware", peer_symbols=["2353.TW", "2376.TW", "2382.TW", "2324.TW"], official_root="https://www.asus.com/event/Investor/c/index", investor_url="https://www.asus.com/EVENT/Investor/C/ir_report", governance_url="https://www.asus.com/event/Investor/c/index", esg_url="https://esg.asus.com/en/resource/reports", annual_report_url="https://www.asus.com/EVENT/Investor/C/ir_report", brand_terms=["ROG", "Republic of Gamers"],
    ),
    "wistron": _taiwan_equity_profile(
        target_id="wistron", symbol="3231.TW", legal_name="Wistron Corporation", local_name="緯創", aliases=["緯創", "Wistron"], sector="Technology", industry="Electronics Manufacturing Services", peer_symbols=["2317.TW", "2382.TW", "2356.TW", "2324.TW"], official_root="https://www.wistron.com/", investor_url="https://www.wistron.com/ch/Investors/AnnualReports", governance_url="https://esg.wistron.com/en/governance/GovernanceReport", esg_url="https://esg.wistron.com/en/report-download/esg/NoakpM47xGKX/2024SustainabilityReportEN.pdf", annual_report_url="https://doc.twse.com.tw/server-java/t57sb01?step=1&colorchg=1&co_id=3231&year=115&mtype=F&",
    ),
})

# 「南亞」在台灣市場也常被用於南亞科技（2408）。保留公司常用名，
# 但要求塑膠／電子材料或 1303 脈絡，並排除南亞科，避免本地新聞與社群
# 路由因名稱相近而灌入錯誤標的。
_PROFILES["nanya"]["target"].update({
    "ambiguous_aliases": ["南亞"],
    "identity_context_terms": ["1303", "南亞塑膠", "塑膠", "電子材料", "聚酯", "plastics"],
    "identity_exclude_terms": ["南亞科", "2408"],
})


def get_target_profile(target_id: str) -> dict[str, Any]:
    """Return an isolated profile copy so callers cannot mutate the registry."""

    normalized = str(target_id or "").strip().casefold()
    if normalized not in _PROFILES:
        raise ValueError(f"unsupported target_id: {target_id}")
    return deepcopy(_PROFILES[normalized])


def list_target_profiles() -> list[dict[str, Any]]:
    """Return all configured profiles in deterministic order."""

    return [get_target_profile(target_id) for target_id in sorted(_PROFILES)]


def target_source_registry(profile: dict[str, Any]) -> dict[str, Any]:
    """Build a registry whose source IDs and official route belong to one target."""

    target_id = str(profile.get("target_id") or "target").strip().casefold()
    official = profile.get("official") if isinstance(profile.get("official"), dict) else {}
    official_source_id = str(official.get("source_id") or f"official_{target_id}_profile")
    official_transport = "static_html" if official.get("kind") == "sec_filing" else "json_api"
    official_is_financial = official.get("kind") == "twse_financial_statement"
    official_url = (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(str(official.get('cik') or '0'))}/{str(official.get('accession') or '').replace('-', '')}/{official.get('document')}"
        if official.get("kind") == "sec_filing"
        else str(official.get("income_url") or "https://openapi.twse.com.tw/v1/opendata/t187ap03_L")
    )
    return build_source_registry([
        {
            "source_id": official_source_id,
            "publisher_id": "sec_edgar" if official.get("kind") == "sec_filing" else "twse_openapi",
            "source_tier": "regulatory" if official.get("kind") == "sec_filing" or official_is_financial else "official",
            "independence_group": "sec_edgar" if official.get("kind") == "sec_filing" else "twse_openapi",
            "transport": official_transport,
            "canonical_url": official_url,
        },
        {
            "source_id": f"yahoo_finance_{target_id}_rss",
            "publisher_id": "yahoo_finance",
            "source_tier": "direct_secondary",
            "independence_group": "yahoo_finance",
            "transport": "rss",
            "canonical_url": "https://feeds.finance.yahoo.com/rss/2.0/headline",
        },
        {
            "source_id": f"yahoo_finance_{target_id}_search",
            "publisher_id": "yahoo_finance",
            "source_tier": "direct_secondary",
            "independence_group": "yahoo_finance",
            "transport": "json_api",
            "canonical_url": "https://query1.finance.yahoo.com/v1/finance/search",
        },
        {
            "source_id": f"google_news_{target_id}_rss",
            "publisher_id": "google_news",
            "source_tier": "aggregator",
            "independence_group": "google_news",
            "transport": "rss",
            "canonical_url": "https://news.google.com/rss/search",
        },
        {
            "source_id": f"yahoo_finance_{target_id}_tw_rss",
            "publisher_id": "yahoo_finance",
            "source_tier": "aggregator",
            "independence_group": "yahoo_finance",
            "transport": "rss",
            "canonical_url": "https://feeds.finance.yahoo.com/rss/2.0/headline",
            "region": "TW", "language": "zh-TW", "priority": 1, "route_role": "local_news",
        },
        {
            "source_id": f"google_news_{target_id}_tw_rss",
            "publisher_id": "google_news",
            "source_tier": "aggregator",
            "independence_group": "google_news",
            "transport": "rss",
            "canonical_url": "https://news.google.com/rss/search",
            "region": "TW", "language": "zh-TW", "priority": 1, "route_role": "local_news",
        },
        {
            "source_id": f"google_news_{target_id}_asia_rss",
            "publisher_id": "google_news",
            "source_tier": "aggregator",
            "independence_group": "google_news",
            "transport": "rss",
            "canonical_url": "https://news.google.com/rss/search",
            "region": "Asia", "language": "en", "priority": 2, "route_role": "regional_news",
        },
        {
            "source_id": f"google_news_{target_id}_global_rss",
            "publisher_id": "google_news",
            "source_tier": "aggregator",
            "independence_group": "google_news",
            "transport": "rss",
            "canonical_url": "https://news.google.com/rss/search",
            "region": "global", "language": "en", "priority": 3, "route_role": "global_news",
        },
        {
            "source_id": f"ptt_stock_{target_id}_search",
            "publisher_id": "ptt_stock",
            "source_tier": "direct_secondary",
            "independence_group": "ptt_stock",
            "transport": "static_html",
            "canonical_url": "https://www.ptt.cc/bbs/Stock/search",
            "region": "TW", "language": "zh-TW", "priority": 1, "route_role": "local_community",
        },
        {
            "source_id": f"hacker_news_{target_id}_api",
            "publisher_id": "hacker_news",
            "source_tier": "direct_secondary",
            "independence_group": "hacker_news",
            "transport": "json_api",
            "canonical_url": "https://hn.algolia.com/api/v1/search_by_date",
        },
    ])
