"""Ticket 3a-H acceptance — the 50 seed-3003 graded edges as FROZEN fixtures.

Every passage below is verbatim from `reports/3b-precision-sample-3.md`; every
verdict is the reviewer's from `reports/3b-grading-3.md`. THE FIXTURES ARE
FROZEN (handoff-shot6.md hard rule 3): they may not be edited to make guards
pass. Acceptance bar, pre-registered: the full guard stack must remove all 18
INCORRECT edges from the named pool (kill or demote) and retain >= 30 of the
32 CORRECT edges; every collateral kill is named with the guard responsible.

Pre-registered expected collateral: #29 ODC/Walmart — the claimed 20% is the
FY2024 figure and FY2025 (19%) sits in the same sentence; the reviewer graded
it correct as immaterial, but a strict latest-year guard (G6) kills it. That
is a recall loss, accepted: precision is the 3b bar.
"""

from __future__ import annotations

from stockbot.bots.frontier.candidates import (
    CUSTOMER_CONCENTRATION,
    REGULATORY_PRECEDENT,
    SOLE_SUPPLIER,
)
from stockbot.bots.frontier.guards import (
    apply_edge_guard_stack,
    apply_round3,
    is_anonymous_descriptor,
    is_internal_entity,
    is_named_regulator,
    magnitude_denominator_scope,
    magnitude_stale_year,
    nonentity_counterparty,
    relationship_terminated,
    sole_supplier_violation,
)

CC = CUSTOMER_CONCENTRATION
SS = SOLE_SUPPLIER
RP = REGULATORY_PRECEDENT


def edge(num, symbol, edge_type, name, magnitude, verdict, passage):
    return {
        "num": num,
        "symbol": symbol,
        "edge_type": edge_type,
        "counterparty_name": name,
        "magnitude_pct": magnitude,
        "verdict": verdict,
        "passage": passage,
    }


# --- FROZEN fixture set (50 edges, seed 3003) --------------------------------

GRADED_EDGES = [
    edge(1, "AMBQ", SS, "TSMC", None, "CORRECT",
         "Any of these challenges or delays could damage our relationships with our end customers, adversely affect our reputation and adversely affect our business, financial condition, results of operations and our ability to grow our business. Our dependence on TSMC as our sole supplier of wafers exposes us to certain political, social and economic risks that may harm our business. Deterioration in the political, social, business or economic conditions in the jurisdictions in which TSMC or other suppliers operate could slow or halt product shipments or disrupt our ability to manufacture, package, test or post-process products."),
    edge(2, "AQST", SS, "Assertio", None, "INCORRECT",
         "Additionally, we subsequently received from Assertio a $6,000 milestone payment upon its receipt of a notice of allowance from the United States Patent and Trademark Office of its patent application U.S. Serial No. 16/561,573, and payment of the related allowance fee. Aquestive is the exclusive sole manufacturer and supplier of Sympazan for Assertio and will receive manufacturing fees from Assertio for the product through the expiration of such License Agreement. • KYNMOBI ® – a sublingual film formulation of apomorphine, which is a dopamine agonist, was developed to treat episodic off-periods in Parkinson’s disease."),
    edge(3, "ATAI", SS, "Nualtis", None, "CORRECT",
         "Under the APA, Nualtis sold licensing and intellectual property rights of Nualtis's oral thin film technology in exchange for an upfront payment of $0.2 million and an additional $0.5 million upon completion of certain manufacturing milestones. Under the Supply Agreement, subject to approval by the FDA, Nualtis will serve as the sole manufacturer of Rizafilm's products over a five year term with an automatic renewal option for an additional five years unless either party provides sufficient written notice. Additionally, the Supply Agreement requires Rizafilm to adhere to certain firm commitments. On March 11, 2021, we entered into a license and collaboration agreement (the “Otsuka Agreement”), with Otsuka Pharmaceutical Co., LTD (“Otsuka”)."),
    edge(4, "BCC", CC, "BMD segment", 71.0, "INCORRECT",
         "10 Table of Contents In 2025, EWP and plywood accounted for 60% and 31%, respectively, of our Wood Products sales. The majority of our wood products are sold to leading wholesalers (including our BMD segment), home improvement centers, dealers, and industrial converters in North America. Our BMD segment is our Wood Products segment's largest customer, representing approximately 71% of our Wood Products segment's overall sales in 2025. In 2025, approximately 75% and 51% of our Wood Products segment's EWP and plywood sales volumes, respectively, were to our BMD segment. Customers Our customer relationships range from locally owned single-location facilities to large national dealers and home improvement centers across the U.S."),
    edge(5, "BKE", SS, "Axis Denim", 20.6, "INCORRECT",
         "The Company requires its vendors to sign and adhere to its Buckle Responsible Sourcing Standards & Code of Conduct, which addresses adherence to legal requirements regarding employment practices and health, safety, and environmental regulations. In fiscal 2025, Axis Denim (which produces private label denim for the Company) accounted for 20.6% of net sales. No other vendor accounted for more than 10% of the Company’s net sales. Buckle stores generally carry the same merchandise, with quantity and seasonal variations based upon historical sales data, climate, and perceived local customer demand. The Company uses a centralized receiving and distribution center located in Kearney, Nebraska."),
    edge(6, "BKKT", CC, "Webull", 74.0, "INCORRECT",
         "Furthermore, our ability to retain existing, or obtain new, clients and customers may be impacted to the extent that clients choose not to partner with us, or customers choose not to transact or to engage in fewer transactions on our platform, in each case, because we do not currently offer or plan to cease offering certain digital assets. For example, Webull, which represented approximately 74% and 73% of our Crypto services revenue in the years ended December 31, 2024 and 2023, respectively, did not renew its agreement with us after the term of the agreement ended on June 14, 2025."),
    edge(7, "BTBT", CC, "Initial Customer", 70.7, "INCORRECT",
         "WhiteFiber currently provides cloud services at Blönduos Campus, Iceland, where it leases capacity to house its GPUs. During 2025, our HPC data center in Iceland had contracts with 21 customers. DNA Fund accounted for approximately 11.5% of our revenue during the 12 months ended December 31, 2025. We did not generate any revenue from DNA Fund in 2024. Our Initial Customer accounted for approximately 70.7% of its revenue during the 12 months ended December 31, 2025 and 96.6% of its revenues through December 31, 2024. As of the report date, WhiteFiber and the Initial Customer are engaged in ongoing discussions regarding a potential resolution of the existing service agreements following the agreed pause of services. No definitive agreement has been reached."),
    edge(8, "CENT", CC, "The Home Depot", 37.0, "INCORRECT",
         "Amdro expanded its visibility by sponsoring Brandon Jones in the NASCAR CRAFTSMAN® Truck Series™, following a 2024 brand modernization that introduced eye-catching packaging and simplified, consumer-relevant claims. Our customer base includes home improvement centers, food, drug and mass merchants, as well as professional end users. In fiscal 2025 and 2024, sales to The Home Depot accounted for approximately 37% and 38%, Walmart represented approximately 29% and 27%, and Lowe’s represented approximately 14% and 15% of our Garden segment’s net sales, respectively."),
    edge(9, "CERS", CC, "American Red Cross", 33.0, "CORRECT",
         "The Company had the following significant customers that accounted for more than 10% of the Company’s total product revenue, during the years ended December 31, 2025, 2024 and 2023 (in percentages): Year Ended December 31, 2025 2024 2023 American Red Cross 33 % 35 % 35 % Établissement Français du Sang 10 % 11 % 12 % 116 Revenues by geographical location were based on the location of the customer during the years ended December 31, 2025, 2024 and 2023, and was as follows (in thousands): Year Ended December 31,"),
    edge(10, "CERS", SS, "Fresenius", None, "CORRECT",
         "We rely on various contracts and our relationships with these suppliers to ensure that the sourced products are manufactured in sufficient quantities, timely, to our exact specifications and at prices we agree upon with the supplier. For example, Fresenius is our sole supplier for the manufacture of finished disposable kits for the platelet and plasma systems. We also rely on other third-party suppliers for other components and products that are currently our sole qualified suppliers for such components and products."),
    edge(11, "CERS", SS, "Porex", None, "CORRECT",
         "In December 2024, we and Porex have entered into a second amended and restated manufacturing and supply agreement that became effective January 1, 2025, or the 2025 Agreement, for the continued supply of the compound adsorption devices. Porex is currently our sole supplier for compound adsorption devices. Under the 2025 Agreement, we and Porex agreed to extend the term of the prior agreement until December 31, 2027. Under the terms of the 2025 Agreement, unit pricing for platelet wafers and plasma disks are set at certain amounts for the first twenty-four months, starting January 1, 2025 with 10 volume based pricing after the first twenty-four months."),
    edge(12, "CLPT", SS, "CLS", None, "CORRECT",
         "These manufacturers supply IRRA flow disposables under master service agreements that govern the terms of our supply relationships. While we have identified alternative sources for certain IRRA flow disposables, we do not currently maintain multiple qualified suppliers for all of these products. The ClearPoint Prism Neuro Laser Therapy System is supplied to us by CLS, and we rely on CLS as a single-source supplier for this product. CLS may itself rely on single source suppliers for certain components, subassemblies, or materials used in the manufacture of the ClearPoint Prism Neuro Laser Therapy System."),
    edge(13, "CRON", CC, "Ontario Cannabis Retail Corporation", 27.0, "CORRECT",
         "Major Customers Major customers are customers for which sales equaled or exceeded 10% of our consolidated net revenues for the year. In 2025, there were two major customers, Ontario Cannabis Retail Corporation and Alberta Gaming, Liquor and Cannabis Commission, which accounted for approximately 27% and 16%, respectively, of the Company’s consolidated net revenues, before excises taxes, for the year ended December 31, 2025. The Company verifies the customers’ liquidity prior to the authorization of material transactions for the purpose of mitigating credit risk."),
    edge(14, "CTS", RP, "EPA", None, "CORRECT",
         "CTS CORPORATION 56 Table of Contents The Company operates under and in accordance with a federal consent decree, dated March 7, 2017, with the EPA for the Asheville Site."),
    edge(15, "ECVT", RP, "Consent Decree", None, "INCORRECT",
         "federal rules for the pollutants sulfur dioxide and sulfuric acid mist. The Consent Decree required Solvay (the owner of such facilities at the time) to pay a $2 million penalty and spend approximately $34 million on air pollution controls at our facilities, the majority of which was received from customers in contractual arrangements. Work under the Consent Decree has proceeded since 2007, and all of the significant capital improvements related to the Consent Decree have been completed. Three of our operating locations have been released from the scope of the Consent Decree and we are seeking release of three other locations covered by the Consent Decree."),
    edge(16, "EVH", CC, "Molina Healthcare, Inc.", 25.7, "CORRECT",
         "However, significant sustained inflation driven by the macroeconomic environment or other factors could negatively impact our margins, profitability and results of operations in future periods. Customers The following table summarizes those partners who represented at least 10.0% of our consolidated revenue: For the Year Ended December 31, 2025 2024 2023 Molina Healthcare, Inc. 25.7% 13.7% 13.5% Cook County Health and Hospitals System 16.4% 11.5% 15.7% Florida Blue 14.2% 12.9% 10.4% Centene Corporation 12.2% * * Humana Insurance Company * 19.3% 12.0% ———————— * Represents less than 10.0% of the respective balance."),
    edge(17, "EVLV", SS, "Columbia Tech", None, "CORRECT",
         "and targeted countries. For example, our risk exposure may increase further if any countries levy additional retaliatory tariffs, taxes, or other trade restrictions or penalties against the U.S. or U.S. companies. Delays in production, increases in prices charged or the loss of a limited or sole source supplier could have an adverse effect on our business, financial condition and operating results. We depend on our primary third-party contract manufacturer for the production of our security screening systems. While there are several potential contract manufacturers for most of these products, all our systems are currently manufactured, assembled, tested, and packaged by Columbia Tech."),
    edge(18, "FRPT", CC, "Walmart", 25.0, "CORRECT",
         "We sell our products through the following classes of retail: grocery, mass, international, digital, pet specialty, and club. Our customers determine whether they wish to purchase our products directly from us or through a third-party distributor. In 2025, our largest distributor by net sales accounted for less than 10% of our net sales and our largest customers, Walmart and Costco, accounted for 25% and 10% of our net sales, respectively. The Freshpet Fridge We primarily sell our products through a growing network of company-owned branded refrigerators, the Freshpet Fridges. Our Freshpet Fridges are typically four feet wide by seven feet high and replace standard shelving in the pet aisle or an end-cap of a retail store."),
    edge(19, "GPOR", RP, "USEPA", None, "CORRECT",
         "During the process of terminating the Consent Decree, the Company was informed that there were untimely repairs on a number of locations subject to the Company's Consent Decree that failed to comply with Subpart OOOO or Permit to Install and Operate Applications. On July 31, 2024, the Company received a NOV/FOV from USEPA related to the alleged untimely repairs. On September 30, 2025, the Company entered into a Consent Agreement and Final Order with USEPA fully resolving the NOV/FOVs for $ 454,403 . In November 2020, Robert T. Stephenson and Sandra J."),
    edge(20, "GTN", RP, "Department of Justice", None, "CORRECT",
         "In 2018, several broadcasting companies, including Raycom Media (“Raycom”) (which the Company subsequently acquired in January 2019) and Meredith (which the Company acquired in December 2021) agreed to enter into substantially identical consent decrees with the Department of Justice (the “DOJ”). This consent decree provided for the settlement of a confidential investigation by the DOJ into the alleged exchange of certain competitively sensitive information relating to advertising sales among certain stations in some local markets. The consent decree and related settlement were finalized on May 22, 2019."),
    edge(21, "KDK", CC, "Atlas", 46.0, "CORRECT",
         "Until we have scaled our commercial operations in on-highway operations and other applications of the Kodiak Driver, we expect our contract with Atlas to account for a growing portion of our revenue as we continue to deploy additional trucks to Atlas. For example, for the year ended December 31, 2025 , we recognized revenue from Atlas of $1.8 million, which represented 46% of our total revenue. As we continue to scale our commercial business, the composition of our significant customers may vary. We believe our business, financial condition, and results of operations for the foreseeable future will continue to depend on the deployment of the Kodiak Driver by a relatively limited number of customers into their fleets."),
    edge(22, "KDK", CC, "U.S. Army", 26.0, "CORRECT",
         "A significant portion of our revenue has been generated from a limited number of customers, and we expect that to continue in the near term. For example, for the years ended December 31, 2025, 2024, and 2023, we recognized revenue from the U.S. Army of $1.0 million, $13.3 million, and $15.0 million , respectively, which represented 26%, 89%, and 89% , respectively, of our revenues in such periods. While our contract with the U.S. Army was amended in connection with our work with the U.S. Marines and is still effective and the contract may be used for other work through its expiration in October 2026, there are currently a limited number of contracted deliverables to be completed."),
    edge(23, "KRUS", SS, "Kura Japan", None, "CORRECT",
         "Our profitability depends in part on our ability to anticipate and react to changes in food and supply costs and/or the availability of products necessary to operate our business, including, but not limited to, rice vinegar from Kura Japan, which owns the recipe and is our sole supplier of such rice vinegar. Shortages or interruptions in the availability of certain supplies caused by unanticipated demand, problems in production or distribution, food contamination, inclement weather, natural disasters, or other conditions could adversely affect the productivity, availability, quality and cost of our ingredients, which could harm our operations."),
    edge(24, "MNKD", SS, "Amphastar France Pharmaceuticals S.A.S.", None, "CORRECT",
         "We believe that our Connecticut facility has enough capacity to satisfy the current demand for Afrezza and Tyvaso DPI, especially given the recent expansion of our production capacity in order to meet the demand for Tyvaso DPI projected by UT over the next several years. The costs of this expansion project were primarily borne by UT. Currently, the only source of insulin that we have qualified for Afrezza is manufactured by Amphastar France Pharmaceuticals S.A.S. (“Amphastar”). In April 2014, we entered into a supply agreement with Amphastar (as amended, the “Insulin Supply Agreement”) to purchase certain annual minimum quantities with an aggregate purchase commitment of €120.1 million over a term that currently extends through at least December 31, 2034."),
    edge(25, "MSEX", CC, "Middlesex System", 67.0, "INCORRECT",
         "The Middlesex System also provides water treatment and pumping services to the Township of East Brunswick under contract. The amount of water supply allocated to the Township of East Brunswick is granted directly to the Township by the New Jersey Water Supply Authority. The Middlesex System produced approximately 67% of our 2025 consolidated operating revenues. The Middlesex System’s retail customers are located in an area of approximately 55 square miles in Woodbridge Township, the City of South Amboy, the Boroughs of Metuchen and Carteret, portions of the Township of Edison and the Borough of South Plainfield, all in Middlesex County, and a portion of the Township of Clark in Union County."),
    edge(26, "MVST", CC, "Italy", 22.0, "INCORRECT",
         "This shift reflects our strategy to capture higher-margin opportunities in the European commercial vehicle sectors. The following table provides a breakdown of our revenue by major geographic regions, based on the locations of our customers, for the periods indicated (in thousands, except percentages): Year ended December 31, 2025 2024 Revenue % Revenue % China $ 138,945 33 % $ 127,138 33 % India 34,568 8 % 48,767 13 % Other Asia & Pacific countries 2,754 1 % 1,791 — % Asia & Pacific Region 176,267 42 % 177,696 46 % Italy 92,941 22 % 150,809 40 % France 78,192 18 % 12,764 3 % Other European countries 40,811 9 % 24,145 7 % Europe 211,944 49 % 187,718 50 % U.S."),
    edge(27, "NGVT", SS, "APT plant", None, "INCORRECT",
         "APT plant. While we have some redundancies within the plants that are the sole manufacturer of certain products, we have limited ability to make these products at other plants. Supply Chain Risks We purchase a variety of raw materials, which are subject to pricing pressures and limited availability; inability to procure these raw materials or to pass on price increases could negatively impact our operations or financial results."),
    edge(28, "NKTR", CC, "UCB", 39.0, "INCORRECT",
         "97 Table of Contents Our revenue has been derived primarily from customers in the pharmaceutical and biotechnology industries. Revenue from Baxalta (a subsidiary of Takeda Pharmaceutical Company Limited), AstraZeneca, and UCB represented 45 %, 37 %, and 14 % of our revenue, respectively, for the year ended December 31, 2025 . Revenue from UCB, Baxalta, AstraZeneca, Pfizer Inc., and Roche represented 39 %, 18 %, 17 %, 12 % and 10 % of our revenue, respectively, for the year ended December 31, 2024. Revenue by geographic area is based on the headquarters or shipping locations of our partners."),
    edge(29, "ODC", CC, "Walmart Inc.", 20.0, "CORRECT",
         "7 C USTOMERS Walmart Inc. (“Walmart”) is a customer in our Retail and Wholesale Products Group with whom we have a long-term relationship. Sales to Walmart and its affiliates accounted for approximately 19% and 20% of our total net sales for fiscal years 2025 and 2024, respectively. Sales to Walmart are comprised of a variety of products including but not limited to, multiple scoopable, coarse, and crystal cat litter items and accessories. There are no customers in the Business to Business Products Group with sales equal to or greater than 10% of our total sales."),
    edge(30, "OPK", RP, "U.S.", None, "INCORRECT",
         "espect to our products; • our ability to operate our business without infringing the intellectual property rights of others; • our ability to attract and retain key scientific and management personnel; • the risk that the carrying value of certain assets may exceed the fair value of the assets causing us to impair goodwill or other intangible assets; • our ability to comply with the terms of our 2022 Corporate Integrity Agreement with the U.S."),
    edge(31, "PBH", CC, "Walmart", 20.0, "CORRECT",
         "We depend on a limited number of customers with whom we have no long-term agreements for a large portion of our gross revenues, and the loss of one or more of these customers or changes in their strategies and policies could reduce our gross revenues and have a material adverse effect on our financial condition and results of operations. During 2026, Walmart and Amazon, which accounted for approximately 20% and 15%, respectively, of our gross revenues, were our only customers that accounted for more than 10% of our gross revenues. We expect that for future periods, our top ten customers, including Walmart and Amazon, in the aggregate, will continue to account for a large and potentially increasing portion of our sales."),
    edge(32, "PCRX", CC, "McKesson Drug Company", 31.0, "CORRECT",
         "LG Chem plans to file for marketing authorizations in South Korea and Thailand in 2026. Significant Customers We had three wholesalers each comprising 10 percent or more of our total revenue for the year ended December 31, 2025: McKesson Drug Company, Cardinal Health, Inc. and AmerisourceBergen Health Corporation, which accounted for 31%, 26% and 22% of our total revenues, respectively. These wholesalers process orders for EXPAREL under a drop-ship program. EXPAREL is delivered directly to end-users without the wholesalers ever taking physical possession of the product. There were no other customers of EXPAREL, ZILRETTA or iovera° that accounted for 10 percent or more of our total revenue for the year ended December 31, 2025."),
    edge(33, "PKE", CC, "affiliate and non-affiliate subtier suppliers of GE Aerospace", 39.3, "CORRECT",
         "The primary measure of segment profit is consolidated net income as reported on the Consolidated Statement of Operations. In addition, segment assets reviewed by the CODM are reported on the Company’s Consolidated Balance Sheet as total assets. Customers – Net sales to affiliate and non-affiliate subtier suppliers of GE Aerospace were 39.3 %, 39.8 %, and 37.7 % of the Company’s total worldwide sales in the 2026, 2025, and 2024 fiscal years, respectively."),
    edge(34, "PVLA", SS, "Nemera", None, "CORRECT",
         "Nemera or any other supplies due to pricing, timing, availability or other issues, if Nemera or any other sole-source supplier does not successfully carry out our contractual duties, meet expected deadlines or supply these pumps or other supplies in accordance with the terms of our agreement and with applicable federal, state, and foreign regulations, or if there are disagreements between us and Nemera or such other sole source supplier, clinical development, marketing approval or commercial manufacturing of our product candidates, if approved, could be delayed."),
    edge(35, "PVLA", SS, "Nemera Le Tréport SAS", None, "CORRECT",
         "As we advance QTORIN rapamycin through development, we will add backup suppliers for drug product manufacture and packaging to protect against any potential supply disruptions. Certain of our supplies, including our pumps, are obtained from sole source suppliers. For example, we have an agreement with Nemera Le Tréport SAS (“Nemera”) for the supply of pumps we use to deliver QTORIN rapamycin. Nemera is a sole source supplier of these pumps, and we are required under a supply agreement to purchase from Nemera. The use of CMOs and reliance on collaboration partners is cost-efficient and has eliminated the need for our direct investment in manufacturing facilities and additional staff early in development."),
    edge(36, "QNST", CC, "Financial services client vertical", 75.0, "INCORRECT",
         "Our business derives its net revenue primarily from fees earned through the delivery of qualified inquiries such as clicks, leads, calls, applications, or customers. Through a vertical focus, targeted media presence and our technology platform, we are able to deliver targeted, measurable marketing results to our clients. Our financial services client vertical represented 75% and 64% of net revenue in fiscal years 2025 and 2024. Our home services client vertical represented 24% and 35% of net revenue in fiscal years 2025 and 2024. Other revenue, which primarily includes our performance marketing agency and technology services, represented 1% of net revenue in fiscal years 2025 and 2024. We generated the majority of our revenue from sales to clients in the United States."),
    edge(37, "QUIK", CC, 'Customer "A"', 44.0, "INCORRECT",
         'Distributor amounts represent revenue from the Company\'s goods and services sold to a distributor. Customer amounts represent revenues from both distributor and from the Company to an end customer. As such, revenue to a distributor may also include information related to customers. Fiscal Years 2025 2024 Distributor "A" 15 % 12 % Customer "A" 44 % 56 % Customer "B" 11 % * * Represents less than 10% of revenue as of the date presented. 75 Table of Contents The following distributors and customers accounted for 10% or more of the Company\'s accounts receivable and contract asset balance as of the dates presented: December 28, December 29, 2025 2024 Distributor "A" * 10 % Dis'),
    edge(38, "RCUS", SS, "WuXi Biologics", None, "CORRECT",
         "Our reliance on limited manufacturing arrangements increases the risk that we will not have and may not be able to obtain sufficient quantities of our investigational products for use in our clinical trials and, if approved, commercial activities. For example, WuXi Biologics, located in China, is currently our sole manufacturer of zimberelimab. We regularly assess our supply needs against our manufactured quantities, however, if WuXi Biologics, or any other manufacturer that we rely on, is unable or unwilling to provide the quantity of material we require, there is no guarantee that any reserves we have of our investigational products will be sufficient for our future clinical development plans."),
    edge(39, "RIGL", CC, "McKesson Corporation", 42.0, "CORRECT",
         "ales, net $ 231,983 $ 144,902 $ 87,081 Contract revenues from collaborations 62,034 34,376 27,658 Government contracts 265 — 265 Total revenues $ 294,282 $ 179,278 $ 115,004 The following table summarizes revenues from each of our customers who individually accounted for 10% or more of the total net product sales and revenues from collaborations: Year Ended December 31, 2025 2024 McKesson Corporation 42% 45% Cencora Inc. 19% 20% Cardinal Health, Inc. * 13% Lilly 14% —% Kissei * 11% _____________________________ * Denotes less than 10% Revenue from product sales is related to our sale of our products in the US, net of chargebacks, discounts and fees, government and other rebates and returns."),
    edge(40, "SBET", CC, "WPT Global", 48.0, "INCORRECT",
         "The loss of any of our key operators, or a significant reduction in sales or contracted rates to any key operator, or unfavorable changes in contracts with operators due to geopolitical, regulatory or other factors, could significantly reduce our revenue, margins and earnings and adversely affect our business. WPT Global, an operator, is our largest customer, comprising approximately 48% of our Affiliate Marketing Services segment’s revenue for the year ended December 31, 2025. The contract with this customer requires a nominal notice period to terminate the contract. If notice were provided, our revenues would decline from then-current net gaming revenue rates to approximately one-third the then-current rate."),
    edge(41, "TECX", SS, "WuXi Biologics", None, "CORRECT",
         "We currently have a sole source relationship with WuXi Biologics for our supply of TX45. If there should be any disruption in such supply arrangement or the supply arrangement with our CDMO for TX2100, including any adverse events affecting our sole supplier for TX45, WuXi Biologics, it could have a negative effect on the clinical development of our product candidates and other operations while we work to identify and qualify an alternate supply source."),
    edge(42, "TNDM", SS, "Unomedical A/S", None, "CORRECT",
         "For example, we purchase all of our currently marketed infusion sets from a third-party supplier, Unomedical A/S, a subsidiary of the ConvaTec Group. Unomedical is responsible for all manufacturing, testing, sterilization and packaging of the infusion sets under our brands. Any sole and single source supplier is managed through our supplier management program that is focused on reducing supply chain risk. Certain suppliers are evaluated, approved and monitored periodically by our quality department to ensure conformity with the specifications, policies and procedures applicable to our devices."),
    edge(43, "TSSI", CC, "US-based IT OEM", 99.0, "INCORRECT",
         "The loss of or a significant reduction in orders from this customer or the failure to provide adequate products or services to it would significantly reduce our revenue. The following customer accounted for a significant percentage of our revenues for the periods shown: Year Ended December 31, 2025 2024 2023 US-based IT OEM 99 % 99 % 96 % No other customer represented more than 10 % of our revenues for any period presented. Our US-based IT OEM customer represented 98 % of our accounts receivable at December 31, 2025 and 98 % at December 31, 2024."),
    edge(44, "UPBD", RP, "FTC", None, "CORRECT",
         "• We may be unable to protect our intellectual property, or may be alleged to have infringed upon the intellectual property rights of others, which could result in a loss of our competitive advantage, a diversion of resources and a material adverse effect on our business and results of operations. • Brigit previously entered into a settlement with the FTC under which it continues to have ongoing compliance obligations, noncompliance with which could result in material regulatory enforcement. • Our Brigit segment is subject to extensive regulation and oversight in a variety of areas under federal, state and local laws."),
    edge(45, "VISN", CC, "Comcast Corporation", 35.0, "CORRECT",
         "The corporate and other costs related to the CCS segment will be reallocated to the Company’s remaining segments beginning in the first quarter of 2026. (3) Reflects ARRIS acquisition accounting adjustments related to reducing deferred revenue to its estimated fair value. Customer and Supplier Information Net sales to Comcast Corporation and affiliates (Comcast) accounted for approximately 35 %, 21 % and 20 % for the years ended December 31, 2025, 2024 and 2023, respectively. Net sales to Charter Communications, Inc. (Charter) accounted for approximately 13 % and 14 % for the years ended December 31, 2024 and 2023, respectively. Other than Comcast and Charter, no direct customer accounted for 10 % or more of the Company’s total net sales during the years ended December 31, 2025, 2024 or 2023 ."),
    edge(46, "VYX", RP, "federal and state governments", None, "INCORRECT",
         "The Company was identified as a PRP because of alleged PCB discharges from two carbonless copy paper manufacturing facilities it previously owned, which 98 Table of Contents were located along the Fox River, and carbonless copy paper “broke” the Company allegedly sold to other mills as raw material. In 2017, the Company entered into a Consent Decree with the federal and state governments for the clean-up of the Fox River, which was approved on August 22, 2017 by the federal district court in Wisconsin presiding over this matter. The Consent Decree resolved the Company’s disputes with the enforcement agencies as well as the other PRPs."),
    edge(47, "WASH", RP, "DOJ", None, "CORRECT",
         "As previously disclosed in 2023, the Bank entered into a settlement with the DOJ through an agreement to resolve allegations that it violated fair lending laws in the state of Rhode Island from 2016 to 2021. Under the settlement, the Bank agreed, over a five-year period, to (i) provide $7.0 million in loan subsidies and (ii) commit $2.0 million for focused community outreach and marketing efforts."),
    edge(48, "WLKP", CC, "Westlake", 88.6, "CORRECT",
         "68 Table of Contents WESTLAKE CHEMICAL PARTNERS LP NOTES TO CONSOLIDATED FINANCIAL STATEMENTS—(Continued) (in thousands of dollars, except unit amounts and per unit data) Major Customer and Concentration of Credit Risk During the years ended December 31, 2025, 2024 and 2023, Westlake accounted for approximately 88.6 %, 83.7 % and 86.2 %, respectively, of the Partnership's net sales. General During the years ended December 31, 2025, 2024 and 2023, the Partnership reimbursed $ 333 , $ 351 and $ 347 , respectively, to Westlake for certain state tax payments. Other See Note 10 above for an additional transaction through which a related party acquired common units in a private placement. 12."),
    edge(49, "WS", CC, "Detroit Three Automakers", 33.0, "CORRECT",
         "Total volume (tons) decreased 5% compared to the prior year, with our direct shipments down 4% and toll shipments down 7%. Toll volumes were down primarily with our steel mill customers as they required less outside processing to meet their production requirements. Direct shipments to the automotive market were down 3% compared to the prior year. Detroit Three Automakers represented 33% and 32% of our consolidated net sales during fiscal 2025 and consolidated and combined net sales during fiscal 2024, respectively."),
    edge(50, "ZURA", SS, "WuXi Biologics", None, "INCORRECT",
         "exports. We cannot predict what effects these tariffs and potential additional tariffs will have on our business, including in the context of escalating trade tensions. However, these tariffs and other trade restrictions could increase our operating costs, reduce our gross margins or otherwise negatively impact our financial results. WuXi Biologics has historically been our sole supplier of torudokimab. We have moved our existing product from WuXi to the U.K. When we require additional product, we may find a new manufacturing facility for torudokimab."),
]

# Pre-registered guard attribution for the 18 INCORRECT edges. Edges whose
# attribution is a demotion (named -> anonymous) are marked ("demote", guard).
EXPECTED_GUARDS = {
    2: ("kill", "g7_direction"),
    4: ("kill", "g1_internal_entity"),
    5: ("kill", "g7_sole_language"),
    6: ("kill", "g4_relationship_currency"),
    7: ("kill", "g5_denominator"),
    8: ("kill", "g5_denominator"),
    15: ("kill", "g2_instrument"),
    25: ("kill", "g1_internal_entity"),
    26: ("kill", "g2_geography"),
    27: ("kill", "g1_internal_entity"),
    28: ("kill", "g6_stale_year"),
    30: ("kill", "g2_vague_government"),
    36: ("kill", "g1_internal_entity"),
    37: ("demote", "g3_anonymous_descriptor"),
    40: ("kill", "g5_denominator"),
    43: ("demote", "g3_anonymous_descriptor"),
    46: ("kill", "g2_vague_government"),
    50: ("kill", "g4_relationship_currency"),
}

# Pre-registered collateral: strict latest-year selection kills #29 (claimed
# 20% = FY2024; FY2025 = 19% in the same sentence). Accepted recall loss.
EXPECTED_COLLATERAL = {29: "g6_stale_year"}


def _decide(e):
    return apply_edge_guard_stack(
        edge_type=e["edge_type"],
        counterparty_name=e["counterparty_name"],
        named=True,
        magnitude_pct=e["magnitude_pct"],
        quoted_passage=e["passage"],
    )


def test_fixture_set_shape():
    assert len(GRADED_EDGES) == 50
    incorrect = [e for e in GRADED_EDGES if e["verdict"] == "INCORRECT"]
    correct = [e for e in GRADED_EDGES if e["verdict"] == "CORRECT"]
    assert len(incorrect) == 18
    assert len(correct) == 32
    assert set(EXPECTED_GUARDS) == {e["num"] for e in incorrect}


def test_all_18_incorrect_edges_leave_named_pool():
    """Acceptance part 1: every INCORRECT edge is killed or demoted, by the
    pre-registered guard."""
    for e in GRADED_EDGES:
        if e["verdict"] != "INCORRECT":
            continue
        decision = _decide(e)
        expected_action, expected_guard = EXPECTED_GUARDS[e["num"]]
        assert decision.action == expected_action, (
            f"#{e['num']} {e['symbol']} -> {e['counterparty_name']}: "
            f"expected {expected_action}, got {decision.action} "
            f"(guard={decision.guard}, demoted_by={decision.demoted_by})"
        )
        got_guard = decision.guard if decision.action == "kill" else decision.demoted_by
        assert got_guard == expected_guard, (
            f"#{e['num']} {e['symbol']}: expected guard {expected_guard}, got {got_guard}"
        )


def test_correct_retention_bar():
    """Acceptance part 2: >= 30 of the 32 CORRECT edges survive as named.
    Collateral kills must match the pre-registered list exactly."""
    collateral = {}
    retained = 0
    for e in GRADED_EDGES:
        if e["verdict"] != "CORRECT":
            continue
        decision = _decide(e)
        if decision.action == "keep":
            retained += 1
        else:
            collateral[e["num"]] = decision.guard or decision.demoted_by
    assert collateral == EXPECTED_COLLATERAL, f"collateral kills changed: {collateral}"
    assert retained >= 30, f"only {retained}/32 correct edges retained: {collateral}"
    assert retained == 31


# --- direct unit coverage of the round-3 guard functions ---------------------


def test_g1_internal_entity():
    assert is_internal_entity("BMD segment", "including our BMD segment")
    assert is_internal_entity("APT plant", "APT plant. While we have some redundancies")
    assert is_internal_entity(
        "Middlesex System", "The Middlesex System produced approximately 67% of our revenues"
    )
    assert not is_internal_entity("Cisco Systems", "products from Cisco Systems represented 29%")
    assert not is_internal_entity("Atlas", "our contract with Atlas")


def test_g2_nonentity():
    assert nonentity_counterparty("Italy", CC) == "g2_geography"
    assert nonentity_counterparty("Consent Decree", RP) == "g2_instrument"
    assert nonentity_counterparty("U.S.", RP) == "g2_vague_government"
    assert nonentity_counterparty("federal and state governments", RP) == "g2_vague_government"
    assert nonentity_counterparty("U.S. Army", CC) is None
    assert nonentity_counterparty("American Red Cross", CC) is None
    assert is_named_regulator("USEPA")
    assert is_named_regulator("Department of Justice")
    assert not is_named_regulator("Acme Holdings")


def test_g3_anonymous_descriptor():
    assert is_anonymous_descriptor("Initial Customer")
    assert is_anonymous_descriptor('Customer "A"')
    assert is_anonymous_descriptor("US-based IT OEM")
    assert not is_anonymous_descriptor("Kura Japan")
    assert not is_anonymous_descriptor("U.S. Army")
    assert not is_anonymous_descriptor("Columbia Tech")


def test_g4_relationship_currency():
    assert relationship_terminated(
        "Webull, which represented 74% of our revenue, did not renew its agreement.", "Webull"
    )
    assert relationship_terminated(
        "WuXi Biologics has historically been our sole supplier of torudokimab.",
        "WuXi Biologics",
    )
    # "year ended December 31" must never fire the agreement-ended pattern.
    assert not relationship_terminated(
        "Atlas accounted for 46% of revenue for the year ended December 31, 2025.", "Atlas"
    )
    # "amended" must not match \bended\b.
    assert not relationship_terminated(
        "Our contract with the U.S. Army was amended and is still effective.", "U.S. Army"
    )


def test_g5_denominator_scope():
    assert (
        magnitude_denominator_scope(
            "sales to The Home Depot accounted for approximately 37% of our Garden segment’s "
            "net sales, respectively.",
            37.0,
        )
        == "segment"
    )
    assert (
        magnitude_denominator_scope(
            "Our Initial Customer accounted for approximately 70.7% of its revenue.", 70.7
        )
        == "segment"
    )
    assert (
        magnitude_denominator_scope(
            "Walmart accounted for 25% of our net sales in 2025.", 25.0
        )
        == "company"
    )


def test_g6_stale_year():
    nktr = GRADED_EDGES[27]["passage"]
    assert GRADED_EDGES[27]["num"] == 28
    assert magnitude_stale_year(nktr, 39.0, "UCB")
    # The latest-year figure in the same passage is NOT stale.
    assert not magnitude_stale_year(nktr, 14.0, "UCB")
    pke = GRADED_EDGES[32]["passage"]
    assert GRADED_EDGES[32]["num"] == 33
    assert not magnitude_stale_year(pke, 39.3, "affiliate and non-affiliate subtier suppliers of GE Aerospace")


def test_g7_sole_supplier():
    assert (
        sole_supplier_violation(
            "Aquestive is the exclusive sole manufacturer and supplier of Sympazan for "
            "Assertio and will receive manufacturing fees.",
            "Assertio",
        )
        == "g7_direction"
    )
    assert (
        sole_supplier_violation(
            "Axis Denim (which produces private label denim for the Company) accounted for "
            "20.6% of net sales. No other vendor accounted for more than 10%.",
            "Axis Denim",
        )
        == "g7_sole_language"
    )
    assert (
        sole_supplier_violation(
            "Fresenius is our sole supplier for the manufacture of finished disposable kits.",
            "Fresenius",
        )
        is None
    )
    assert (
        sole_supplier_violation(
            "all our systems are currently manufactured, assembled, tested, and packaged by "
            "Columbia Tech.",
            "Columbia Tech",
        )
        is None
    )


def test_round3_g5_nulls_magnitude_for_sole_supplier():
    decision = apply_round3(
        edge_type=SS,
        counterparty_name="Acme Pumps",
        named=True,
        magnitude_pct=30.0,
        quoted_passage=(
            "Acme Pumps is our sole supplier of pumps, representing 30% of our Industrial "
            "segment’s net sales."
        ),
    )
    assert decision.action == "keep"
    assert decision.null_magnitude
    assert decision.denominator_scope == "segment"
