"""Shot 7 Agent A acceptance — round-3 + round-4 graded edges as FROZEN fixtures.

Round 3 (seed 3003): 18/18 INCORRECT must stay killed; >= 30/32 CORRECT retained
(31/32 with pre-registered ODC collateral). Round 4 (seed 3004): 6/6 new failures
killed by pre-registered guard; >= 44/44 CORRECT retained (zero collateral budget
unless declared below). Fixtures are frozen — do not edit to make guards pass.
"""

from __future__ import annotations

from stockbot.bots.frontier.candidates import (
    CUSTOMER_CONCENTRATION,
    REGULATORY_PRECEDENT,
    SOLE_SUPPLIER,
)
from stockbot.bots.frontier.guards import apply_edge_guard_stack

from tests.test_shot6_3aH_guards import (
    EXPECTED_COLLATERAL as EXPECTED_COLLATERAL_R3,
    EXPECTED_GUARDS as EXPECTED_GUARDS_R3,
    GRADED_EDGES as GRADED_EDGES_R3,
)

CC = CUSTOMER_CONCENTRATION
SS = SOLE_SUPPLIER
RP = REGULATORY_PRECEDENT

R4_RETAINED_FLOOR = 44


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


GRADED_EDGES_R4 = [
    edge(1, "AMBQ", CC, "Huawei", 41, "INCORRECT",
         "For example, one of our largest end customers historically was Huawei, which is designated on the Entity List administered by the U.S. Department of Commerce Bureau of Industry and Security. Huawei accounted for approximately 41% of our net sales during 2024 and 0.1% of our net sales during 2025. Our sales to Huawei are subject to a license requirement from the U.S. Government for all items subject to the licensing jurisdiction of the EAR. To date, our sales to Huawei have been authorized pursuant to licenses granted by the U.S. Government, which require among other things that our products not be used in 5G-compatible devices."),
    edge(2, "AQST", CC, "Indivior", 73, "CORRECT",
         "If revenues from such key customer were to decline significantly, it would materially adversely affect our business, financial condition and results of operations. Indivior accounted for approximately 73% and 62% of our revenues for 2025 and 2024, respectively, and we believe in the future will continue to account for a substantial part of our revenues. Further, the Indivior License Agreement under which we manufacture and supply Suboxone to Indivior on an exclusive basis, may be terminated should certain causes or events occur."),
    edge(3, "AQST", SS, "Pharmanovia", None, "INCORRECT",
         "Under the Pharmanovia Agreement, Pharmanovia will lead the regulatory and commercialization activities for Libervant in the Territory and Aquestive will serve as the exclusive sole manufacturer and supplier of Libervant in the Territory. We 9 received $3,500 upon agreement execution. Effective March 27, 2023, we amended the Pharmanovia Agreement to expand the scope of the licensed territory for Libervant to cover the rest of the world, excluding the U.S., Canada and China. Pharmanovia will be responsible for seeking appropriate regulatory approval in the expanded territories."),
    edge(4, "ARCB", RP, "EPA", None, "CORRECT",
         "Management believes we are in substantial compliance with all such regulations. The underground storage tanks are required to have leak detection systems, and we are not aware of any leaks from such tanks that could reasonably be expected to have a material adverse effect on our operating results. Notwithstanding current compliance, including under a consent decree with the EPA, as discussed below, we are subject to on-going environmental remediation obligations concerning historical underground storage tank releases, for which the resolutions are not expected to have a material adverse effect on our financial condition, results of operations, or cash flows. ​ Certain of our Asset-Based service center facilities operate with no-exposure certifications or stormwater permits u"),
    edge(5, "ATAI", SS, "Nualtis", None, "CORRECT",
         "Under the APA, Nualtis sold licensing and intellectual property rights of Nualtis's oral thin film technology in exchange for an upfront payment of $0.2 million and an additional $0.5 million upon completion of certain manufacturing milestones. Under the Supply Agreement, subject to approval by the FDA, Nualtis will serve as the sole manufacturer of Rizafilm's products over a five year term with an automatic renewal option for an additional five years unless either party provides sufficient written notice. Additionally, the Supply Agreement requires Rizafilm to adhere to certain firm commitments. On March 11, 2021, we entered into a license and collaboration agreement (the “Otsuka Agreement”), with Otsuka Pharmaceutical Co., LTD (“Otsuka”)."),
    edge(6, "ATEC", SS, "Invibio", None, "CORRECT",
         "We depend on third-party suppliers, and in one case a single supplier, for key raw materials and the loss of any of these third-party suppliers, or their inability to supply us with adequate raw materials, could harm our business. We rely on a number of suppliers and in one case on a single source vendor, Invibio, to provide the raw materials used in the production of our products. We have a supply agreement with Invibio, pursuant to which it supplies us with PEEK, a biocompatible plastic that we use in some of our spacers. Invibio is one of a limited number of companies approved to distribute PEEK in the U.S. for use in implantable devices."),
    edge(7, "AUPH", SS, "LUPKYNIS", None, "INCORRECT",
         "If we need to obtain additional financing in the future, such financing could result in dilution to your investment, adversely affect the trading price of our common shares and/or create future operating and financial restrictions. As of December 31, 2025, we had cash, cash equivalents, restricted cash and investments of $398.0 million. LUPKYNIS is our only approved product and our only source of net product sales. Prior to the year ended December 31, 2024, we had negative cash flows from operating activities for multiple years. The amount and timing of future funding requirements, if any, will depend on many factors, including the success of our commercialization efforts for LUPKYNIS and our ability to control expenses and our decisions on how to deploy capital."),
    edge(8, "AUPH", SS, "Lonza", None, "CORRECT",
         "The long-term commercial success of LUPKYNIS will depend in part on the ability of our contract manufacturers to supply cGMP-compliant drug substance and drug product without interruption. Manufacturing of Drug Substance Voclosporin requires a specialized drug substance manufacturing process and is manufactured by Lonza, our sole supplier for drug substance. Pricing for supply is determined through supply agreements between us and Lonza and is based on the volume produced and the cost of the raw materials used in the drug substance manufacturing process. As of the date of this Annual Report, we have not experienced any difficulty in obtaining the raw materials required with respect to the manufacturing of voclosporin."),
    edge(9, "BGS", CC, "Walmart", 31, "CORRECT",
         "Freight rates remained elevated during fiscal 2023, 2024 and 2025, and we expect freight rates to remain elevated during fiscal 2026. To the extent that we are unable to offset present and future cost increases through pricing and cost savings initiatives, our operating results will be negatively impacted. Customers Our top ten customers accounted for approximately 63.6% of our net sales and approximately 68.0% of our end of the year receivables for fiscal 2025. Other than Walmart, which accounted for approximately 31.0% of our fiscal 2025 net sales, no single customer accounted for 10.0% or more of our fiscal 2025 net sales."),
    edge(10, "CDNA", CC, "Medicare", 34, "CORRECT",
         "The Company has not experienced any significant credit losses and does not require collateral on receivables. For the years ended December 31, 2025, 2024 and 2023, approximately 34 %, 38 % and 40 %, respectively, of total revenue was billed to Medicare. No other payers represented more than 10% of total revenue for the years ended December 31, 2025, 2024 and 2023. As of December 31, 2025 and 2024, approximately 21 % and 27 %, respectively, of accounts receivable was due from Medicare. No other payer or customer represented more than 10% of accounts receivable at either December 31, 2025 or 2024. 81 Table of Contents Cash and Cash Equivalents Cash equivalents consist of short-term, highly liquid investments with original maturities of three months or less from the date of purchase."),
    edge(11, "CERS", CC, "American Red Cross", 33, "CORRECT",
         "The Company had the following significant customers that accounted for more than 10% of the Company’s total product revenue, during the years ended December 31, 2025, 2024 and 2023 (in percentages): Year Ended December 31, 2025 2024 2023 American Red Cross 33 % 35 % 35 % Établissement Français du Sang 10 % 11 % 12 % 116 Revenues by geographical location were based on the location of the customer during the years ended December 31, 2025, 2024 and 2023, and was as follows (in thousands): Year Ended December 31,"),
    edge(12, "CERS", SS, "Fresenius", None, "CORRECT",
         "We rely on various contracts and our relationships with these suppliers to ensure that the sourced products are manufactured in sufficient quantities, timely, to our exact specifications and at prices we agree upon with the supplier. For example, Fresenius is our sole supplier for the manufacture of finished disposable kits for the platelet and plasma systems. We also rely on other third-party suppliers for other components and products that are currently our sole qualified suppliers for such components and products."),
    edge(13, "DBRG", RP, "SEC", None, "CORRECT",
         "In September 2024, Colony Capital Investment Advisors, LLC (“CCIA”), the investment adviser to certain legacy funds and vehicles holding legacy assets, entered into a settlement agreement with the SEC that resolved the previously disclosed SEC examination into certain alleged deficiencies identified in the examination relating to CCIA’s compliance with its fiduciary duty, duty of care and disclosure of affiliate transactions involving certain legacy businesses and operations."),
    edge(14, "EOLS", SS, "Daewoong", None, "CORRECT",
         "The Company also has the option to negotiate first with Daewoong to secure a distribution license for any product that Daewoong directly or indirectly develops or commercializes that is classified as an injectable botulinum toxin (other than Jeuveau ® ) in a territory covered by the Daewoong Agreement. The Company relies on Daewoong, its exclusive and sole supplier, to manufacture Jeuveau ® . Any termination or loss of significant rights, including exclusivity, under the Daewoong Agreement would materially and adversely affect the Company’s commercialization of Jeuveau ® . See Note 9. Commitments and Contingencies and Note 11. Medytox Settlement Agreements for additional information."),
    edge(15, "ESTA", SS, "Avantor, Inc.", None, "CORRECT",
         "Suppliers We source manufacturing inputs from a number of outside suppliers. In particular, we obtain NuSil brand medical-grade silicone from Avantor, Inc. (formerly NuSil Technology LLC), or Avantor, which is a sole-source supplier of such products to the majority of the silicone breast implant industry. In May 2022, we entered into a master supply agreement with Avantor, which provides for specified prices per unit of each relevant component, has an initial term ending on December 31, 2026 and automatically renews for successive terms of one year each for up to five successive renewal terms."),
    edge(16, "ETON", CC, "AnovoRx product", 84.6, "CORRECT",
         "For the years ended December 31, 2025, 2024 and 2023 , AnovoRx product sales represented 84.6 %, 93.6 % and 78.2 % of net revenues, respectively. As of December 31, 2025 and 2024 , AnovoRx product sales represented 88.4 % and 96.2 % of net accounts receivable. For the year ended December 31, 2025, the Company's revenues from external customers were derived from U.S operations and included product sales to U.S. and foreign countries."),
    edge(17, "EVH", CC, "Molina Healthcare, Inc.", 25.7, "CORRECT",
         "However, significant sustained inflation driven by the macroeconomic environment or other factors could negatively impact our margins, profitability and results of operations in future periods. Customers The following table summarizes those partners who represented at least 10.0% of our consolidated revenue: For the Year Ended December 31, 2025 2024 2023 Molina Healthcare, Inc. 25.7% 13.7% 13.5% Cook County Health and Hospitals System 16.4% 11.5% 15.7% Florida Blue 14.2% 12.9% 10.4% Centene Corporation 12.2% * * Humana Insurance Company * 19.3% 12.0% ———————— * Represents less than 10.0% of the respective balance."),
    edge(18, "EVTC", CC, "Popular", 29, "CORRECT",
         "In addition, we generally enter into multi-year contracts with our customers. We believe our business model should enable us to continue to grow our business organically in the primary markets we serve without significant incremental capital expenditures. For the year ended December 31, 2025, approximately 29% of our revenue was generated from our relationship with Popular. The revenue concentration with Popular makes our MSA with them our most significant client contract. See “Part II, Item 7."),
    edge(19, "EVTC", CC, "Banco Popular", 29, "CORRECT",
         "For the year ended December 31, 2025, approximately 29% of our revenue was attributable to Banco Popular, a wholly-owned subsidiary of Popular. The A&R MSA by and among Popular, Banco Popular de Puerto Rico and EVERTEC Group, is our most significant client contract, and was amended and restated to include a term ending in 2028."),
    edge(20, "EXPO", CC, "consumer products industry", 21, "INCORRECT",
         "The scope of our services in failure prevention and technology evaluation has grown as the technological complexity of products has increased over the years. During 2025, we provided services representing approximately 21%, 20%, 16% and 11% of revenues to clients in the consumer products industry, energy and utilities industries, transportation industry and chemical industry, respectively. Pricing and Terms of Engagements We provide our services on either a fixed-price basis or on a time and material basis, charging in the latter case hourly rates for each staff member involved in a project, based on their skills and experience."),
    edge(21, "FMC", RP, "EPA", None, "CORRECT",
         "In September 2012, the EPA issued an Interim Record of Decision (\"IROD\") that is environmentally protective and that is also protective of the health and safety of both workers and the general public. Since the plant's closure, we have successfully decommissioned our Pocatello plant, completed closure of the RCRA ponds and formally requested that the EPA acknowledge completion of work under a June 1999 RCRA Consent Decree. Future remediation costs include completion of the IROD that addresses groundwater contamination and existing waste disposal areas on the Pocatello plant portion of the Eastern Michaud Flats Superfund Site. In June 2013, the EPA issued a Unilateral Administrative Order to us under which we are continuing to implement the IROD remedy."),
    edge(22, "FORM", CC, "SK hynix Inc.", 24.5, "CORRECT",
         "The following customers represented 10% or more of our quarterly revenues for the quarters indicated: Fiscal Quarters Ended Dec 27, 2025 Sep 27, 2025 Jun 28, 2025 Mar 29, 2025 Dec 28, 2024 Sep 28, 2024 Jun 29, 2024 Mar 30, 2024 SK hynix Inc. 19.2 % 24.5 % 25.0 % 23.3 % 22.0 % 18.1 % 19.5 % 15.5 % Intel Corporation * * 12.4 % 12.0 % * 17.1 % 16.7 % 15.7 % Taiwan Semiconductor Manufacturing Company Ltd. * * 10.4 % * * * * * Samsung Electronics Co., Ltd. * * * * * * * 12.4 % 19.2 % 24.5 % 47.8 % 35.3 % 22.0 % 35.2 % 36.2 % 43.6 % * Less than 10% of revenues."),
    edge(23, "FRPH", CC, "Vulcan Materials Company", 26, "CORRECT",
         "Price, location, rental space availability, flexibility of design and property management services are the major factors that affect competition. Customers. In the Mining Royalty Lands Segment, we have a total of five tenants currently leasing our mining locations, one of which, Vulcan Materials Company (“Vulcan” or “Vulcan Materials”), accounted for 26% of the Company’s consolidated revenues in 2025. An event affecting Vulcan’s ability to perform under its lease agreements could materially impact the Company’s results. 5 T able of c ontents Sales and Marketing. We use national brokerage firms to assist us in marketing our vacant properties. Our hands-on in-house management team focuses on tenant satisfaction during the life of the lease."),
    edge(24, "GHM", CC, "U.S. Navy", 60, "CORRECT",
         "If one or more of these risks actually occurs, our business, results of operations and/or financial condition could suffer, and the price of our stock could be negatively affected. Risks Related to our Business We may experience customer concentration risk related to strategic growth for U.S. Navy projects. During fiscal 2026, sales to the Defense industry continued to grow and represented 60% of our business compared with 58% and 54% of sales to the Defense industry in fiscal 2025 and 2024, respectively. While these projects are spread across multiple contractors and programs, the end customer for these projects, primarily the U.S. Navy, is the same."),
    edge(25, "GLIBA", RP, "FCC", None, "CORRECT",
         "GCI Holdings responded to that inquiry on October 25, 2024. The Bureau transmitted a set of supplemental questions to which GCI Holdings responded on December 23, 2024. On August 8, 2025, GCI entered into a $10,000 settlement and a three-year consent decree, fully resolving this matter. Schools and Libraries Program. In 2014, the FCC adopted orders modernizing the USF Schools and Libraries Program (“E-Rate”), which aids schools and libraries in obtaining affordable broadband."),
    edge(26, "GLIBK", RP, "FCC", None, "CORRECT",
         "GCI Holdings responded to that inquiry on October 25, 2024. The Bureau transmitted a set of supplemental questions to which GCI Holdings responded on December 23, 2024. On August 8, 2025, GCI entered into a $10,000 settlement and a three-year consent decree, fully resolving this matter. Schools and Libraries Program. In 2014, the FCC adopted orders modernizing the USF Schools and Libraries Program (“E-Rate”), which aids schools and libraries in obtaining affordable broadband."),
    edge(27, "GNE", CC, "GRE", 95.3, "INCORRECT",
         "Newly acquired customers typically have higher rates of churn than longer-tenured customers, and thus churn rates tend to be higher during and shortly after periods of significant expansion of our customer base. Expiration of municipal aggregation deals also impacts churn when the customers are moved en masse to the new supplier. GRE’s revenue represented approximately 95.3%, 94.9% and 95.6% of our total consolidated revenue in 2025, 2024 and 2023, respectively."),
    edge(28, "GPOR", RP, "Department of Justice and USEPA", None, "CORRECT",
         "On January 22, 2020, the Company entered a Consent Decree with the Department of Justice and USEPA addressing Gulfport's failure to capture and control air emissions from storage vessels and to comply with associated inspection, recordkeeping, and reporting requirements. During the process of terminating the Consent Decree, the Company was informed that there were untimely repairs on a number of locations subject to the Company's Consent Decree that failed to comply with Subpart OOOO or Permit to Install and Operate Applications. On July 31, 2024, the Company received a NOV/FOV from USEPA related to the alleged untimely repairs."),
    edge(29, "HUMA", SS, "SeraCare", None, "CORRECT",
         "Once the package is delivered to the operating room, the ATEV is removed from the bioreactor bag by the surgical staff. Suppliers We source critical components and necessary raw materials from vendors that have been approved and qualified through our vendor management program. SeraCare, which was subsequently acquired by LGC Clinical Diagnostics, Inc. (“SeraCare”), is the current single source supplier of human plasma used in our manufacturing process and Confluent Medical Technologies, Inc. (“Confluent”) is the current single source supplier of the polymer mesh we use. We source custom, Humacyte-designed, pre-sterilized (gamma irradiated) assemblies and single-use tubing sets through multiple approved vendors."),
    edge(30, "KRUS", SS, "Kura Japan", None, "CORRECT",
         "Our profitability depends in part on our ability to anticipate and react to changes in food and supply costs and/or the availability of products necessary to operate our business, including, but not limited to, rice vinegar from Kura Japan, which owns the recipe and is our sole supplier of such rice vinegar. Shortages or interruptions in the availability of certain supplies caused by unanticipated demand, problems in production or distribution, food contamination, inclement weather, natural disasters, or other conditions could adversely affect the productivity, availability, quality and cost of our ingredients, which could harm our operations."),
    edge(31, "MNKD", SS, "Amphastar", None, "CORRECT",
         "For the commercial manufacture of inhaled drug products, we need access to sufficient, reliable and affordable supplies of raw materials for formulating powders, such as FDKP, as well as other components, such as the inhaler and the related cartridges. For Afrezza, we also require a supply of insulin. Currently, the only source of insulin that we have qualified for Afrezza is manufactured by Amphastar. We must rely on all of our suppliers to comply with relevant regulatory and other legal requirements, including the production of insulin and FDKP in accordance with cGMP for drug products, and the molding of the inhaler and cartridges components in accordance with QMSRs."),
    edge(32, "MRVI", CC, "Cygnus", 36, "CORRECT",
         "GMP mRNA synthesis. We provide GMP mRNA manufacturing services for customers in clinical trials through commercial manufacturing. This includes process development and scale-up, regulatory submission support, and in-house analytical services for mRNA analysis and characterization. Cygnus (36% of Revenue for the Year Ended December 31, 2025) We provide products and services that enable the detection of impurities present in bioproduction. Our products are used during development and scale-up, during the regulatory approval process and throughout commercialization."),
    edge(33, "NAVI", RP, "CFPB", None, "CORRECT",
         "Any action by the CFPB or one or more State Attorneys General could have a material adverse effect on us or our business. In September 2024, we entered into a settlement agreement with the CFPB to resolve all matters in dispute with respect to a civil action filed by the CFPB in January 2017. A description of the CFPB settlement is included in \"Note 12 – Commitments, Contingencies and Guarantees.\" Our FFELP Loans are subject to the HEA and related laws, rules, regulations and policies."),
    edge(34, "PARR", RP, "EPA", None, "CORRECT",
         "Compliance with this rule has not had a material impact on our financial condition, results of operations, or cash flows to date. However, new operating and other regulatory standards could involve additional costs, and failure to comply with such standards could involve penalties, each of which could be material. Hawaii Consent Decree On July 18, 2016, PHR and subsidiaries of Tesoro Corporation (“Tesoro”) entered into a consent decree with the EPA, the U.S."),
    edge(35, "PAYO", CC, "Amazon marketplaces", 21, "CORRECT",
         "The loss of business associated or connected with a large marketplace or ecosystem could materially harm our business, results of operations and financial condition. For example, the payments our customers received from Amazon marketplaces around the world generated 21% of our revenues during the year ended December 31, 2025, and accordingly, should Amazon change eligibility or other requirements for approved payment service providers operating on its platforms such that we are negatively impacted, our financial condition and results of operations may be materially adversely impacted."),
    edge(36, "PLUS", CC, "Verizon Communications Inc.", 24, "CORRECT",
         "Our Customers We serve 4,200 customers that are primarily middle market to large enterprises and state and local government institutions, including state and local education (“SLED”) institutions, across diverse customer end markets. For the year ended March 31, 2026, the percentage of revenue by customer end market within our business includes 30% for telecommunications, media and entertainment, 13% for SLED, 13% for healthcare, 12% for technology, 10% for financial services, and 6% for retail. Sales to Verizon Communications Inc. represented 24%, 17%, and 19% of our net sales and 17%, 12%, and 13% of gross billings for the years ended March 31, 2026, 2025, and 2024, respectively."),
    edge(37, "PVLA", SS, "Nemera", None, "CORRECT",
         "Nemera or any other supplies due to pricing, timing, availability or other issues, if Nemera or any other sole-source supplier does not successfully carry out our contractual duties, meet expected deadlines or supply these pumps or other supplies in accordance with the terms of our agreement and with applicable federal, state, and foreign regulations, or if there are disagreements between us and Nemera or such other sole source supplier, clinical development, marketing approval or commercial manufacturing of our product candidates, if approved, could be delayed."),
    edge(38, "SBGI", RP, "FCC", None, "CORRECT",
         "As part of the consent decree, the Company also agreed to implement a two-year compliance plan relating to the FCC’s limits on commercial matter in children’s programming and closed captioning rules, and the FCC agreed to grant the license renewal applications of all Company stations involved in the matters resolved by the consent decree. The consent decree states the forfeiture order will be resolved by separate action with respect to the non-Company licensees named in the forfeiture order, and to the Company’s knowledge all but one of such non-Company licensees have entered into non-monetary consent decrees with the FCC and agreed to similar compliance plans with respect to the FCC’s limits on commercial matter in children’s programming."),
    edge(39, "SDRL", CC, "Petrobras", 36, "CORRECT",
         "In addition, we provide management services to certain affiliated entities. The following customers had total revenues greater than 10% of our total operating revenues in any of the periods presented: Year ended December 31, 2025 Year ended December 31, 2024 Year ended December 31, 2023 Petrobras 36 % 18 % 16 % Sonadrill 22 % 22 % 17 % Talos 11 % 2 % 7 % LLOG 10 % 9 % 9 % Others 21 % 49 % 51 % 100 % 100 % 100 % Competition The offshore drilling industry is highly competitive, with market participants ranging from large multinational companies to small locally-owned companies. The demand for offshore drilling services is driven by oil and gas companies’ exploration and development drilling programs."),
    edge(40, "SENS", CC, "Ascensia", 53, "CORRECT",
         "The Company expects to invoice and collect all unbilled accounts receivable within twelve months. ​ Concentration of Revenues and Customers ​ A significant portion of the Company’s revenue is derived from one customer, Ascensia. For the year ended December 31, 2025 and 2024, sales to Ascensia accounted for 53 % and 82 % of total revenue, respectively. ​ A portion of the Company’s revenue is earned via consignment arrangements with healthcare providers. For the years ended December 31, 2025 and 2024, sales under consignment arrangements accounted for 41 % and 15 % of total revenue, respectively."),
    edge(41, "SKYT", CC, "Infineon", 43, "CORRECT",
         "Experience supporting diverse quantum architectures also informs the broader value of SkyWater’s foundry platform in highly strategic and emerging technology domains. 13 Our Customers We serve a diverse array of customers ranging from designers producing near-commodity volume chips to those requiring highly specialized next-generation technology solutions. Infineon accounted for 43% and 7% of our revenue for fiscal years ended December 28, 2025 and December 29, 2024, respectively. Two customers, other than Infineon, represented 21% and 10% of our revenue for the fiscal year ended December 28, 2025. Two customers, other than Infineon, represented 40%, and 20% of our revenue for the fiscal year ended December 29, 2024."),
    edge(42, "SMPL", CC, "Walmart Inc.", 31, "CORRECT",
         "9 Retailers We have a wide variety of customers across the mass merchandise, food, club, drug, small format, and e-commerce channels. A substantial majority of our sales are generated from a limited number of retailers. Sales to our largest retailer, Walmart Inc., represented approximately 31% of consolidated sales in fiscal year 2025, with approximately 24% through their mass retail channel and approximately 7% through their Sam’s Club and e-commerce channels. Sales to our next largest retailer, Amazon, represented approximately 18% of consolidated sales in fiscal year 2025. No other customer represents more than 10% of consolidated sales."),
    edge(43, "TALO", CC, "Shell Trading (US) Company", 35, "CORRECT",
         "Our customers consist primarily of major oil and gas companies, well-established oil and pipeline companies and independent oil and natural gas producers and suppliers. We perform ongoing credit evaluations of our customers and provide allowances for expected credit losses when necessary. For the year ended December 31, 2025, 35%, 23% and 12% of our oil, natural gas and NGL revenues were attributable to Shell Trading (US) Company, Exxon Mobil Corporation and Chevron Corporation, respectively, which are the customers that individually represented 10% or more of our oil, natural gas and NGL revenues."),
    edge(44, "TECX", SS, "WuXi Biologics", None, "CORRECT",
         "We currently have a sole source relationship with WuXi Biologics for our supply of TX45. If there should be any disruption in such supply arrangement or the supply arrangement with our CDMO for TX2100, including any adverse events affecting our sole supplier for TX45, WuXi Biologics, it could have a negative effect on the clinical development of our product candidates and other operations while we work to identify and qualify an alternate supply source."),
    edge(45, "TLS", CC, "U.S. federal government", 91, "CORRECT",
         "Our Customers Our primary customers include the U.S. federal government, large commercial businesses, state and local governments, and international customers. Our consolidated revenues are largely attributable to prime contracts or subcontracts with our contractors engaged in work for the U.S. federal government. In fiscal year 2025, we generated approximately 91.0% of our revenues from the U.S. federal government, with the remaining attributable to state and local governments, and commercial markets. Our U.S. federal government customers include the DoW, the intelligence community (\"IC\"), and multiple civilian and public sector agencies, including the U.S. Department of Homeland Security (\"DHS\"), the U.S. Department of Justice, the U.S. Department of Energy, the U.S."),
    edge(46, "ULH", CC, "General Motors", 25, "CORRECT",
         "Customers We serve customers throughout the United States and in Mexico and Canada. Revenues are concentrated in automotive, retail and consumer goods, metals, energy, and manufacturing industries. For the year ended December 31, 2025: • Automotive customers represented approximately 45% of total revenues; • Our top customer, General Motors, represented approximately 25% of revenues; and • Our top ten customers represented approximately 59% of revenues. Human Capital Resources As of December 31, 2025, Universal employed approximately 10,525 employees, supported by the full-time equivalency of approximately 46 individuals on a contract basis."),
    edge(47, "VISN", CC, "Comcast Corporation and affiliates", 35, "CORRECT",
         "(3) Reflects ARRIS acquisition accounting adjustments related to reducing deferred revenue to its estimated fair value. Customer and Supplier Information Net sales to Comcast Corporation and affiliates (Comcast) accounted for approximately 35 %, 21 % and 20 % for the years ended December 31, 2025, 2024 and 2023, respectively. Net sales to Charter Communications, Inc. (Charter) accounted for approximately 13 % and 14 % for the years ended December 31, 2024 and 2023, respectively. Other than Comcast and Charter, no direct customer accounted for 10 % or more of the Company’s total net sales during the years ended December 31, 2025, 2024 or 2023 ."),
    edge(48, "WLKP", CC, "Westlake", 88.6, "CORRECT",
         "68 Table of Contents WESTLAKE CHEMICAL PARTNERS LP NOTES TO CONSOLIDATED FINANCIAL STATEMENTS—(Continued) (in thousands of dollars, except unit amounts and per unit data) Major Customer and Concentration of Credit Risk During the years ended December 31, 2025, 2024 and 2023, Westlake accounted for approximately 88.6 %, 83.7 % and 86.2 %, respectively, of the Partnership's net sales. General During the years ended December 31, 2025, 2024 and 2023, the Partnership reimbursed $ 333 , $ 351 and $ 347 , respectively, to Westlake for certain state tax payments. Other See Note 10 above for an additional transaction through which a related party acquired common units in a private placement. 12."),
    edge(49, "WTI", CC, "BP Products North America", 33, "CORRECT",
         "The prices received for oil, NGL and natural gas sales are generally tied to monthly or daily indices as quoted in industry publications. We are not dependent upon, or contractually limited to, any one customer or small group of customers. In 2025, BP Products North America and Shell Trading (US) Company accounted for 33% and 17%, respectively, of our revenues from sales of oil, NGLs and natural gas."),
    edge(50, "ZURA", RP, "FDA", None, "INCORRECT",
         "These restrictions may include suspension of product manufacturing until the FDA is assured that quality standards can be met, continuing oversight of manufacturing by the FDA under a ‘‘consent decree,’’ which frequently includes the imposition of costs and continuing inspections over a period of many years, as well as possible withdrawal of the product from the market."),
]

EXPECTED_GUARDS_R4 = {
    1: ("kill", "g6_stale_year"),
    3: ("kill", "g7_direction"),
    7: ("kill", "g8_product"),
    20: ("kill", "g2_industry"),
    27: ("kill", "g1_internal_entity"),
    50: ("kill", "g9_hypothetical_regulatory"),
}

# Pre-registered round-4 collateral (empty unless a guard kills a CORRECT edge).
EXPECTED_COLLATERAL_R4: dict[int, str] = {}


def _decide(e):
    return apply_edge_guard_stack(
        edge_type=e["edge_type"],
        counterparty_name=e["counterparty_name"],
        named=True,
        magnitude_pct=e["magnitude_pct"],
        quoted_passage=e["passage"],
    )


def _assert_incorrect_killed(edges, expected_guards, label):
    for e in edges:
        if e["verdict"] != "INCORRECT":
            continue
        decision = _decide(e)
        expected_action, expected_guard = expected_guards[e["num"]]
        assert decision.action == expected_action, (
            f"{label} #{e['num']} {e['symbol']} -> {e['counterparty_name']}: "
            f"expected {expected_action}, got {decision.action} "
            f"(guard={decision.guard}, demoted_by={decision.demoted_by})"
        )
        got_guard = decision.guard if decision.action == "kill" else decision.demoted_by
        assert got_guard == expected_guard, (
            f"{label} #{e['num']} {e['symbol']}: expected guard {expected_guard}, got {got_guard}"
        )


def _assert_correct_retention(edges, expected_collateral, floor, label):
    collateral = {}
    retained = 0
    for e in edges:
        if e["verdict"] != "CORRECT":
            continue
        decision = _decide(e)
        if decision.action == "keep":
            retained += 1
        else:
            collateral[e["num"]] = decision.guard or decision.demoted_by
    assert collateral == expected_collateral, f"{label} collateral changed: {collateral}"
    assert retained >= floor, f"{label}: only {retained} correct edges retained: {collateral}"


def test_round3_fixture_shape():
    assert len(GRADED_EDGES_R3) == 50
    assert len([e for e in GRADED_EDGES_R3 if e["verdict"] == "INCORRECT"]) == 18
    assert len(EXPECTED_GUARDS_R3) == 18


def test_round4_fixture_shape():
    assert len(GRADED_EDGES_R4) == 50
    incorrect = [e for e in GRADED_EDGES_R4 if e["verdict"] == "INCORRECT"]
    assert len(incorrect) == 6
    assert set(EXPECTED_GUARDS_R4) == {e["num"] for e in incorrect}


def test_round3_incorrect_still_killed():
    _assert_incorrect_killed(GRADED_EDGES_R3, EXPECTED_GUARDS_R3, "R3")


def test_round4_incorrect_killed_by_expected_guard():
    _assert_incorrect_killed(GRADED_EDGES_R4, EXPECTED_GUARDS_R4, "R4")


def test_round3_correct_retention():
    _assert_correct_retention(
        GRADED_EDGES_R3, EXPECTED_COLLATERAL_R3, floor=30, label="R3"
    )


def test_round4_correct_retention():
    _assert_correct_retention(
        GRADED_EDGES_R4, EXPECTED_COLLATERAL_R4, floor=R4_RETAINED_FLOOR, label="R4"
    )
