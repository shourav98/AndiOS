"""
Terms of Service Router for AndiOS
Provides an official HTML web page (matching the legal PDF document layout)
and structured JSON API endpoints.
Complies with Meta App Review, WhatsApp Business Platform, and UAE PDPL requirements.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from utils.response import api_success

router = APIRouter(tags=["Legal & Compliance"])

TERMS_OF_SERVICE_DATA = {
    "title": "AndiOS Terms of Service",
    "effective_date": "October 20, 2026",
    "last_updated": "October 28, 2026",
    "company": {
        "name": "Funky Di Andi for Online Selling",
        "legal_name": "Funky Di Andi for Online Selling (Sole Establishment)",
        "licence": "Dubai DET Licence No. 1582068",
        "email": "hello@funkydiandi.com",
        "phone": "+971 55 109 4841",
        "website": "https://andios.ae",
        "address": "Villa 103, New Dubai, P.O. Box 12345, Dubai, UAE",
        "jurisdiction": "Dubai, United Arab Emirates",
    },
    "sections": [
        {
            "id": 1,
            "title": "1. Introduction and Acceptance of Terms",
            "content": (
                "These Terms of Service (\"Terms\", \"Agreement\") constitute a legally binding agreement between "
                "Funky Di Andi for Online Selling, a sole establishment registered in Dubai, UAE, under Department of "
                "Economy and Tourism (DET) licence no. 1582068, with registered address at Villa 103, New Dubai, P.O. Box 12345, "
                "Dubai, UAE (\"AndiOS\", \"we\", \"us\", \"our\"), and the real estate brokerage, company, or individual "
                "accessing or using our software platform (\"Customer\", \"Brokerage\", \"you\", \"your\").\n\n"
                "By creating an account, connecting third-party communication channels (such as WhatsApp Business API or property portals), "
                "accessing our APIs, or utilizing the AndiOS platform, you acknowledge that you have read, understood, and agreed "
                "to be bound by these Terms and our Privacy Policy (available at https://api.andios.ae/privacy). If you are entering into "
                "this Agreement on behalf of a company or other legal entity, you represent and warrant that you have full legal authority "
                "to bind that entity."
            )
        },
        {
            "id": 2,
            "title": "2. Description of the AndiOS Platform",
            "content": (
                "AndiOS is an AI operating system specifically engineered for real estate brokerages and property professionals "
                "in the United Arab Emirates. The platform provides automated lead capture, qualification, intelligent WhatsApp messaging, "
                "AI voice assistant calling, calendar and viewing appointment scheduling, property listing synchronization, and CRM operations.\n\n"
                "We provide the platform as Software-as-a-Service (SaaS). We continuously update and enhance the platform features, and we reserve "
                "the right to modify or discontinue non-essential features with reasonable notice."
            )
        },
        {
            "id": 3,
            "title": "3. Eligibility, Brokerage Licensing and Account Security",
            "content": (
                "To use AndiOS, you must be a legally registered business or licensed real estate professional under the relevant UAE authorities "
                "(such as the Dubai Land Department / RERA, Abu Dhabi Department of Municipalities and Transport, or other UAE emirate licensing bodies). "
                "You agree to provide true, accurate, and up-to-date business information, including your legal company name, trade licence number, "
                "and RERA/BRN registration where applicable.\n\n"
                "Account Security: You are solely responsible for safeguarding the credentials of all administrative and agent accounts under your "
                "organization. You must notify us immediately at hello@funkydiandi.com if you suspect unauthorized access or compromise of your account."
            )
        },
        {
            "id": 4,
            "title": "4. Customer Data Ownership and Data Protection Roles",
            "content": (
                "Ownership: Our customers retain complete ownership of all data, leads, client lists, contacts, property specifications, "
                "and communication histories submitted to or processed by AndiOS (\"Customer Data\"). We do not sell, rent, or commercialize "
                "your Customer Data, and we never use one brokerage's client information to benefit another brokerage or third party.\n\n"
                "Data Controller and Processor Roles: Under the UAE Personal Data Protection Law (Federal Decree-Law No. 45 of 2021 - PDPL):\n"
                "• The Customer (Brokerage) acts as the Data Controller with respect to all leads, property owners, buyers, and tenants managed inside AndiOS.\n"
                "• AndiOS acts solely as the Data Processor, processing Customer Data exclusively on the documented instructions of the Brokerage and in accordance with our Data Processing terms.\n\n"
                "Data Subject Rights & Deletion: For detailed instructions regarding how data subjects can exercise their rights to access or delete "
                "their personal data, please consult our User Data Deletion Instructions at https://api.andios.ae/data-deletion."
            )
        },
        {
            "id": 5,
            "title": "5. Acceptable Use Policy and Communications Compliance",
            "content": (
                "When using AndiOS, including connected WhatsApp Business numbers and AI voice calling capabilities, you agree to comply with all "
                "applicable UAE federal and local laws, as well as third-party platform policies (including Meta Platform Terms and WhatsApp Business Messaging Policy).\n\n"
                "Specifically, you warrant and agree that:\n"
                "• You have obtained all necessary prior consents and opt-ins required by UAE law and Meta guidelines before initiating outbound messages or calls to leads.\n"
                "• You will immediately honor opt-out requests (such as 'STOP', 'UNSUBSCRIBE', or verbal requests to discontinue communications).\n"
                "• You will not transmit unsolicited commercial spam, fraudulent property advertisements, deceptive pricing, or content violating UAE National Media Council or RERA advertising guidelines.\n"
                "• Transparent AI Disclosure: When employing AI voice calling or automated messaging, you must clearly identify to recipients that they are communicating with an AI assistant operating on behalf of your brokerage.\n"
                "• You will not attempt to reverse engineer, decompile, compromise security, or conduct unauthorized vulnerability assessments against the AndiOS platform."
            )
        },
        {
            "id": 6,
            "title": "6. Artificial Intelligence and Human Oversight",
            "content": (
                "AI Technology: AndiOS utilizes advanced large language models (including models provided by OpenAI) to draft conversational replies, "
                "qualify leads, extract property criteria, and propose viewing schedules.\n\n"
                "No Model Training on Identifiable Client Data: Under our enterprise API agreements with AI providers, customer message contents and "
                "identifiable client records are never used to train public foundational AI models.\n\n"
                "Human Oversight: AI capabilities are assistive tools meant to augment licensed real estate agents, not replace professional human judgment. "
                "Brokerages maintain ultimate oversight and can configure autonomous reply thresholds. AndiOS does not make legally binding decisions—such as "
                "executing Tenancy Contracts (Ejari), approving mortgage applications, or finalizing sales agreements—without explicit human review and sign-off."
            )
        },
        {
            "id": 7,
            "title": "7. Third-Party Integrations and Services",
            "content": (
                "AndiOS interfaces with external service providers to deliver end-to-end functionality, including Meta Platforms (WhatsApp Business Cloud API), "
                "OpenAI, Google Calendar, and Supabase (UAE-region database storage). Your use of features integrated with third-party platforms is subject "
                "to the respective terms and service availability of those third parties. We are not liable for temporary service interruptions or changes in "
                "third-party API policies beyond our reasonable control."
            )
        },
        {
            "id": 8,
            "title": "8. Subscriptions, Fees and Invoicing",
            "content": (
                "Subscription Plans: Fees for AndiOS are billed on a recurring monthly or annual basis according to the tier and usage limits selected at subscription.\n\n"
                "Taxes: All fees are exclusive of value-added tax (VAT) unless explicitly stated otherwise. Where applicable under UAE tax regulations, UAE VAT at the prevailing rate (5%) will be added to your invoice.\n\n"
                "Payment Terms: Payments are processed via authorized payment gateway partners. Invoices and tax receipts are maintained in our accounting records for a minimum of five (5) years in compliance with UAE Federal Law No. 7 of 2017 on Tax Procedures."
            )
        },
        {
            "id": 9,
            "title": "9. Intellectual Property Rights",
            "content": (
                "All intellectual property rights in the AndiOS platform, software, underlying code, user interfaces, documentation, trademarks, and logos "
                "are and remain the exclusive property of Funky Di Andi for Online Selling. Subject to these Terms and timely payment of subscription fees, "
                "we grant you a limited, non-exclusive, non-transferable, revocable licence to access and use the platform solely for your internal brokerage operations."
            )
        },
        {
            "id": 10,
            "title": "10. Warranties and Disclaimers",
            "content": (
                "The AndiOS platform is provided on an 'AS IS' and 'AS AVAILABLE' basis. To the maximum extent permitted by UAE law, we disclaim all warranties, "
                "express or implied, including merchantability, fitness for a particular purpose, and non-infringement.\n\n"
                "While we strive for 99.9% platform availability, we do not guarantee uninterrupted, error-free operation. AndiOS does not guarantee any specific "
                "volume of real estate transactions, revenue generation, or commission outcomes resulting from the use of our software."
            )
        },
        {
            "id": 11,
            "title": "11. Limitation of Liability and Indemnification",
            "content": (
                "Limitation of Liability: To the maximum extent permitted under applicable UAE laws, neither Funky Di Andi for Online Selling nor its officers, "
                "directors, or employees shall be liable for indirect, incidental, special, punitive, or consequential damages, or loss of profits, data, or goodwill.\n\n"
                "Our aggregate cumulative liability arising out of or related to this Agreement shall be limited to the total amount paid by the Customer to AndiOS "
                "in the twelve (12) months immediately preceding the event giving rise to liability.\n\n"
                "Indemnification: The Customer agrees to indemnify, defend, and hold harmless AndiOS and its affiliates from and against any third-party claims, "
                "regulatory fines, damages, or legal expenses arising from Customer's violation of these Terms, failure to secure required messaging consent from "
                "leads, or breach of UAE real estate advertising regulations."
            )
        },
        {
            "id": 12,
            "title": "12. Term, Suspension and Termination",
            "content": (
                "Term: This Agreement begins upon your initial registration or platform access and continues until terminated by either party.\n\n"
                "Termination: Either party may terminate a subscription by providing written notice prior to the end of the current billing cycle. We reserve "
                "the right to suspend or terminate access immediately if you commit a material breach of these Terms, fail to pay fees, or engage in unlawful messaging.\n\n"
                "Post-Termination Data Export: Upon contract termination, the Customer may request an export of their client and lead data within thirty (30) "
                "days. After this transition period, Customer Data will be permanently and securely deleted in accordance with our data retention schedule."
            )
        },
        {
            "id": 13,
            "title": "13. Governing Law and Dispute Resolution",
            "content": (
                "These Terms, their interpretation, and any disputes arising out of or in connection with them shall be governed exclusively by the laws of "
                "the United Arab Emirates as applied in the Emirate of Dubai.\n\n"
                "The parties agree that any legal suit, action, or proceeding arising out of or relating to these Terms shall be instituted exclusively in "
                "the competent courts of Dubai, UAE."
            )
        },
        {
            "id": 14,
            "title": "14. Modifications to Terms",
            "content": (
                "We may revise these Terms from time to time to reflect regulatory changes or service enhancements. We will post the revised version on this page "
                "with an updated 'Last updated' date. For material changes, we will notify registered account holders via email or in-app notification at least "
                "fifteen (15) days before the changes take effect. Continued use of the platform after effective date constitutes acceptance."
            )
        },
        {
            "id": 15,
            "title": "15. Contact Us",
            "content": (
                "For legal enquiries, formal notices, or questions regarding these Terms of Service, please contact us:\n\n"
                "Funky Di Andi for Online Selling (Sole Establishment)\n"
                "Dubai DET Licence No. 1582068\n"
                "Villa 103, New Dubai, P.O. Box 12345, Dubai, UAE\n"
                "Email: hello@funkydiandi.com\n"
                "Phone / WhatsApp: +971 55 109 4841"
            )
        }
    ]
}


def _render_terms_html() -> str:
    """
    Renders an HTML webpage designed identically to the official PDF legal document.
    Clean black & white document aesthetic, precise typography, tables, and spacing.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AndiOS Terms of Service</title>
    <meta name="description" content="AndiOS Terms of Service - AI operating system for real estate brokerages in the UAE. Operated by Funky Di Andi for Online Selling.">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            background-color: #f3f4f6;
            color: #000000;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            font-size: 14.5px;
            line-height: 1.62;
            padding: 40px 16px;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        /* Top control bar for web visitors */
        .top-toolbar {
            max-width: 840px;
            margin: 0 auto 16px auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 13px;
            color: #4b5563;
            flex-wrap: wrap;
            gap: 10px;
        }

        .legal-nav {
            display: flex;
            gap: 8px;
            align-items: center;
        }

        .legal-nav a {
            color: #4b5563;
            text-decoration: none;
            font-weight: 500;
            padding: 4px 10px;
            border-radius: 4px;
            transition: all 0.15s ease;
        }

        .legal-nav a:hover {
            color: #000000;
            background: #e5e7eb;
        }

        .legal-nav a.active {
            color: #000000;
            font-weight: 700;
            background: #ffffff;
            border: 1px solid #d1d5db;
        }

        .top-toolbar .action-buttons {
            display: flex;
            gap: 8px;
        }

        .top-toolbar a.btn, .top-toolbar button {
            color: #1f2937;
            text-decoration: none;
            font-weight: 500;
            background: #ffffff;
            border: 1px solid #d1d5db;
            padding: 6px 14px;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
        }

        .top-toolbar a.btn:hover, .top-toolbar button:hover {
            background: #f9fafb;
            border-color: #9ca3af;
        }

        /* Document Paper Container */
        .pdf-page {
            max-width: 840px;
            margin: 0 auto;
            background-color: #ffffff;
            border: 1px solid #e5e7eb;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
            padding: 68px 72px;
            border-radius: 2px;
        }

        /* Document Header */
        h1.doc-title {
            font-size: 27px;
            font-weight: 800;
            color: #000000;
            letter-spacing: -0.4px;
            margin-bottom: 8px;
            line-height: 1.25;
        }

        .doc-meta {
            font-size: 13px;
            color: #4b5563;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid #e5e7eb;
        }

        /* Section Titles */
        h2.section-title {
            font-size: 17.5px;
            font-weight: 700;
            color: #000000;
            margin-top: 32px;
            margin-bottom: 14px;
            letter-spacing: -0.2px;
            line-height: 1.35;
        }

        /* Paragraphs */
        p {
            color: #000000;
            margin-bottom: 14px;
            font-size: 14.5px;
            line-height: 1.62;
        }

        p strong {
            font-weight: 700;
            color: #000000;
        }

        /* Bullet lists */
        ul.bullet-list {
            list-style-type: disc;
            padding-left: 26px;
            margin: 10px 0 16px 0;
        }

        ul.bullet-list li {
            margin-bottom: 5px;
            color: #000000;
            font-size: 14.5px;
            line-height: 1.55;
        }

        /* Tables matching PDF layout */
        .table-wrap {
            width: 100%;
            overflow-x: auto;
            margin: 18px 0 18px 0;
            -webkit-overflow-scrolling: touch;
        }

        table.policy-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
            line-height: 1.55;
            color: #000000;
        }

        table.policy-table th {
            text-align: left;
            font-weight: 700;
            color: #000000;
            padding: 12px 18px 12px 0;
            vertical-align: top;
            border-bottom: 1px solid #111827;
        }

        table.policy-table th:last-child {
            padding-right: 0;
        }

        table.policy-table td {
            text-align: left;
            padding: 14px 18px 14px 0;
            vertical-align: top;
            color: #000000;
            border-bottom: 1px solid #f1f5f9;
        }

        table.policy-table tr:last-child td {
            border-bottom: none;
        }

        table.policy-table td:last-child {
            padding-right: 0;
        }

        /* Links */
        a {
            color: #000000;
            text-decoration: underline;
            text-underline-offset: 2px;
        }

        a:hover {
            color: #2563eb;
        }

        /* Footer */
        .doc-footer {
            margin-top: 40px;
            padding-top: 24px;
            border-top: 1px solid #e5e7eb;
            font-size: 13px;
            color: #6b7280;
            display: flex;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
        }

        /* Mobile responsiveness */
        @media (max-width: 768px) {
            body {
                padding: 12px 8px;
            }

            .pdf-page {
                padding: 32px 20px;
                border-radius: 0;
                box-shadow: none;
                border: none;
            }

            h1.doc-title {
                font-size: 22px;
            }

            h2.section-title {
                font-size: 16px;
                margin-top: 24px;
            }

            .top-toolbar {
                flex-direction: column;
                align-items: flex-start;
            }
        }

        /* Print Mode: Exact paper output */
        @media print {
            body {
                background: #ffffff;
                padding: 0;
            }

            .top-toolbar {
                display: none;
            }

            .pdf-page {
                box-shadow: none;
                border: none;
                padding: 0;
                max-width: 100%;
            }

            h1.doc-title {
                margin-top: 0;
            }
        }
    </style>
</head>
<body>

    <div class="top-toolbar">
        <nav class="legal-nav">
            <a href="/privacy">Privacy Policy</a>
            <a href="/terms" class="active">Terms of Service</a>
            <a href="/data-deletion">Data Deletion</a>
        </nav>
        <div class="action-buttons">
            <button onclick="window.print()" title="Print or Save as PDF">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V2h12v7"></path><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
                Print / PDF
            </button>
            <a href="/api/terms" class="btn" target="_blank" title="View JSON format">JSON API</a>
        </div>
    </div>

    <main class="pdf-page">
        <!-- Document Title -->
        <h1 class="doc-title">AndiOS Terms of Service</h1>
        <div class="doc-meta">
            <span>Effective Date: October 20, 2026</span> &bull;
            <span>Last Updated: October 28, 2026</span> &bull;
            <span>Governing Jurisdiction: Dubai, UAE</span>
        </div>

        <!-- Section 1 -->
        <h2 class="section-title">1. Introduction and Acceptance of Terms</h2>
        <p>
            These Terms of Service (&ldquo;Terms&rdquo;, &ldquo;Agreement&rdquo;) constitute a legally binding agreement between
            <strong>Funky Di Andi for Online Selling</strong>, a sole establishment registered in Dubai, UAE, under Department of
            Economy and Tourism (DET) licence no. 1582068, with registered address at Villa 103, New Dubai, P.O. Box 12345,
            Dubai, UAE (&ldquo;AndiOS&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;, &ldquo;our&rdquo;), and the real estate brokerage, company, or individual
            accessing or using our software platform (&ldquo;Customer&rdquo;, &ldquo;Brokerage&rdquo;, &ldquo;you&rdquo;, &ldquo;your&rdquo;).
        </p>
        <p>
            By creating an account, connecting third-party communication channels (such as WhatsApp Business API or property portals),
            accessing our APIs, or utilizing the AndiOS platform, you acknowledge that you have read, understood, and agreed
            to be bound by these Terms and our <a href="/privacy">Privacy Policy</a>. If you are entering into
            this Agreement on behalf of a company or other legal entity, you represent and warrant that you have full legal authority
            to bind that entity.
        </p>

        <!-- Section 2 -->
        <h2 class="section-title">2. Description of the AndiOS Platform</h2>
        <p>
            AndiOS is an AI operating system specifically engineered for real estate brokerages and property professionals
            in the United Arab Emirates. The platform provides automated lead capture, qualification, intelligent WhatsApp messaging,
            AI voice assistant calling, calendar and viewing appointment scheduling, property listing synchronization, and CRM operations.
        </p>
        <p>
            We provide the platform as Software-as-a-Service (SaaS). We continuously update and enhance the platform features, and we reserve
            the right to modify or discontinue non-essential features with reasonable notice.
        </p>

        <!-- Section 3 -->
        <h2 class="section-title">3. Eligibility, Brokerage Licensing and Account Security</h2>
        <p>
            To use AndiOS, you must be a legally registered business or licensed real estate professional under the relevant UAE authorities
            (such as the Dubai Land Department / RERA, Abu Dhabi Department of Municipalities and Transport, or other UAE emirate licensing bodies).
            You agree to provide true, accurate, and up-to-date business information, including your legal company name, trade licence number,
            and RERA/BRN registration where applicable.
        </p>
        <p>
            <strong>Account Security:</strong> You are solely responsible for safeguarding the credentials of all administrative and agent accounts under your
            organization. You must notify us immediately at <a href="mailto:hello@funkydiandi.com">hello@funkydiandi.com</a> if you suspect unauthorized access or compromise of your account.
        </p>

        <!-- Section 4 -->
        <h2 class="section-title">4. Customer Data Ownership and Data Protection Roles</h2>
        <p>
            <strong>Ownership:</strong> Our customers retain complete ownership of all data, leads, client lists, contacts, property specifications,
            and communication histories submitted to or processed by AndiOS (&ldquo;Customer Data&rdquo;). We do not sell, rent, or commercialize
            your Customer Data, and we never use one brokerage's client information to benefit another brokerage or third party.
        </p>
        <p>
            <strong>Data Controller and Processor Roles:</strong> Under the UAE Personal Data Protection Law (Federal Decree-Law No. 45 of 2021 &ndash; PDPL):
        </p>
        <ul class="bullet-list">
            <li>The Customer (Brokerage) acts as the <strong>Data Controller</strong> with respect to all leads, property owners, buyers, and tenants managed inside AndiOS.</li>
            <li>AndiOS acts solely as the <strong>Data Processor</strong>, processing Customer Data exclusively on the documented instructions of the Brokerage and in accordance with our Data Processing terms.</li>
        </ul>
        <p>
            <strong>Data Subject Rights &amp; Deletion:</strong> For detailed instructions regarding how data subjects can exercise their rights to access or delete
            their personal data, please consult our <a href="/data-deletion">User Data Deletion Instructions</a>.
        </p>

        <!-- Section 5 -->
        <h2 class="section-title">5. Acceptable Use Policy and Communications Compliance</h2>
        <p>
            When using AndiOS, including connected WhatsApp Business numbers and AI voice calling capabilities, you agree to comply with all
            applicable UAE federal and local laws, as well as third-party platform policies (including Meta Platform Terms and WhatsApp Business Messaging Policy).
        </p>
        <p>Specifically, you warrant and agree that:</p>
        <ul class="bullet-list">
            <li>You have obtained all necessary prior consents and opt-ins required by UAE law and Meta guidelines before initiating outbound messages or calls to leads.</li>
            <li>You will immediately honor opt-out requests (such as replying &ldquo;STOP&rdquo;, &ldquo;UNSUBSCRIBE&rdquo;, or verbal requests to discontinue communications).</li>
            <li>You will not transmit unsolicited commercial spam, fraudulent property advertisements, deceptive pricing, or content violating UAE National Media Council or RERA advertising guidelines.</li>
            <li><strong>Transparent AI Disclosure:</strong> When employing AI voice calling or automated messaging, you must clearly identify to recipients that they are communicating with an AI assistant operating on behalf of your brokerage.</li>
            <li>You will not attempt to reverse engineer, decompile, compromise security, or conduct unauthorized vulnerability assessments against the AndiOS platform.</li>
        </ul>

        <!-- Section 6 -->
        <h2 class="section-title">6. Artificial Intelligence and Human Oversight</h2>
        <p>
            <strong>AI Technology:</strong> AndiOS utilizes advanced large language models (including models provided by OpenAI) to draft conversational replies,
            qualify leads, extract property criteria, and propose viewing schedules.
        </p>
        <p>
            <strong>No Model Training on Identifiable Client Data:</strong> Under our enterprise API agreements with AI providers, customer message contents and
            identifiable client records are never used to train public foundational AI models.
        </p>
        <p>
            <strong>Human Oversight:</strong> AI capabilities are assistive tools meant to augment licensed real estate agents, not replace professional human judgment.
            Brokerages maintain ultimate oversight and can configure autonomous reply thresholds. AndiOS does not make legally binding decisions&mdash;such as
            executing Tenancy Contracts (Ejari), approving mortgage applications, or finalizing sales agreements&mdash;without explicit human review and sign-off.
        </p>

        <!-- Section 7 -->
        <h2 class="section-title">7. Third-Party Integrations and Services</h2>
        <p>
            AndiOS interfaces with external service providers to deliver end-to-end functionality, including Meta Platforms (WhatsApp Business Cloud API),
            OpenAI, Google Calendar, and Supabase (UAE-region database storage). Your use of features integrated with third-party platforms is subject
            to the respective terms and service availability of those third parties. We are not liable for temporary service interruptions or changes in
            third-party API policies beyond our reasonable control.
        </p>

        <!-- Section 8 -->
        <h2 class="section-title">8. Subscriptions, Fees and Invoicing</h2>
        <p>
            <strong>Subscription Plans:</strong> Fees for AndiOS are billed on a recurring monthly or annual basis according to the tier and usage limits selected at subscription.
        </p>
        <p>
            <strong>Taxes:</strong> All fees are exclusive of value-added tax (VAT) unless explicitly stated otherwise. Where applicable under UAE tax regulations, UAE VAT at the prevailing rate (5%) will be added to your invoice.
        </p>
        <p>
            <strong>Payment Terms:</strong> Payments are processed via authorized payment gateway partners. Invoices and tax receipts are maintained in our accounting records for a minimum of five (5) years in compliance with UAE Federal Law No. 7 of 2017 on Tax Procedures.
        </p>

        <!-- Section 9 -->
        <h2 class="section-title">9. Intellectual Property Rights</h2>
        <p>
            All intellectual property rights in the AndiOS platform, software, underlying code, user interfaces, documentation, trademarks, and logos
            are and remain the exclusive property of Funky Di Andi for Online Selling. Subject to these Terms and timely payment of subscription fees,
            we grant you a limited, non-exclusive, non-transferable, revocable licence to access and use the platform solely for your internal brokerage operations.
        </p>

        <!-- Section 10 -->
        <h2 class="section-title">10. Warranties and Disclaimers</h2>
        <p>
            The AndiOS platform is provided on an &ldquo;AS IS&rdquo; and &ldquo;AS AVAILABLE&rdquo; basis. To the maximum extent permitted by UAE law, we disclaim all warranties,
            express or implied, including merchantability, fitness for a particular purpose, and non-infringement.
        </p>
        <p>
            While we strive for 99.9% platform availability, we do not guarantee uninterrupted, error-free operation. AndiOS does not guarantee any specific
            volume of real estate transactions, revenue generation, or commission outcomes resulting from the use of our software.
        </p>

        <!-- Section 11 -->
        <h2 class="section-title">11. Limitation of Liability and Indemnification</h2>
        <p>
            <strong>Limitation of Liability:</strong> To the maximum extent permitted under applicable UAE laws, neither Funky Di Andi for Online Selling nor its officers,
            directors, or employees shall be liable for indirect, incidental, special, punitive, or consequential damages, or loss of profits, data, or goodwill.
        </p>
        <p>
            Our aggregate cumulative liability arising out of or related to this Agreement shall be limited to the total amount paid by the Customer to AndiOS
            in the twelve (12) months immediately preceding the event giving rise to liability.
        </p>
        <p>
            <strong>Indemnification:</strong> The Customer agrees to indemnify, defend, and hold harmless AndiOS and its affiliates from and against any third-party claims,
            regulatory fines, damages, or legal expenses arising from Customer's violation of these Terms, failure to secure required messaging consent from
            leads, or breach of UAE real estate advertising regulations.
        </p>

        <!-- Section 12 -->
        <h2 class="section-title">12. Term, Suspension and Termination</h2>
        <p>
            <strong>Term:</strong> This Agreement begins upon your initial registration or platform access and continues until terminated by either party.
        </p>
        <p>
            <strong>Termination:</strong> Either party may terminate a subscription by providing written notice prior to the end of the current billing cycle. We reserve
            the right to suspend or terminate access immediately if you commit a material breach of these Terms, fail to pay fees, or engage in unlawful messaging.
        </p>
        <p>
            <strong>Post-Termination Data Export:</strong> Upon contract termination, the Customer may request an export of their client and lead data within thirty (30)
            days. After this transition period, Customer Data will be permanently and securely deleted in accordance with our data retention schedule.
        </p>

        <!-- Section 13 -->
        <h2 class="section-title">13. Governing Law and Dispute Resolution</h2>
        <p>
            These Terms, their interpretation, and any disputes arising out of or in connection with them shall be governed exclusively by the laws of
            the United Arab Emirates as applied in the Emirate of Dubai.
        </p>
        <p>
            The parties agree that any legal suit, action, or proceeding arising out of or relating to these Terms shall be instituted exclusively in
            the competent courts of Dubai, UAE.
        </p>

        <!-- Section 14 -->
        <h2 class="section-title">14. Modifications to Terms</h2>
        <p>
            We may revise these Terms from time to time to reflect regulatory changes or service enhancements. We will post the revised version on this page
            with an updated &ldquo;Last updated&rdquo; date. For material changes, we will notify registered account holders via email or in-app notification at least
            fifteen (15) days before the changes take effect. Continued use of the platform after effective date constitutes acceptance.
        </p>

        <!-- Section 15 -->
        <h2 class="section-title">15. Contact Us</h2>
        <p>
            Funky Di Andi for Online Selling (Sole Establishment)<br>
            Dubai DET Licence No. 1582068<br>
            Villa 103, New Dubai, P.O. Box 12345, Dubai, UAE<br>
            Email: <a href="mailto:hello@funkydiandi.com">hello@funkydiandi.com</a><br>
            Phone / WhatsApp: <a href="tel:+971551094841">+971 55 109 4841</a>
        </p>

        <div class="doc-footer">
            <span>&copy; 2026 Funky Di Andi for Online Selling. All rights reserved.</span>
            <span>
                <a href="/privacy">Privacy Policy</a> &bull;
                <a href="/terms">Terms of Service</a> &bull;
                <a href="/data-deletion">Data Deletion</a>
            </span>
        </div>
    </main>

</body>
</html>
"""


@router.get("/terms", response_class=HTMLResponse)
@router.get("/terms-of-service", response_class=HTMLResponse)
@router.get("/tos", response_class=HTMLResponse)
async def get_terms_of_service_page(request: Request):
    """
    Renders the public Terms of Service webpage matching the official PDF document layout.
    Complies with Meta App Review & Developer Dashboard verification requirements.
    """
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse(content=api_success(data=TERMS_OF_SERVICE_DATA))

    return HTMLResponse(content=_render_terms_html(), status_code=200)


@router.get("/api/terms", response_class=JSONResponse)
@router.get("/api/terms-of-service", response_class=JSONResponse)
async def get_terms_of_service_json():
    """
    Returns structured Terms of Service data in JSON format for API consumers and frontend apps.
    """
    return JSONResponse(
        status_code=200,
        content=api_success(
            data=TERMS_OF_SERVICE_DATA,
            message="Terms of Service retrieved successfully"
        )
    )
